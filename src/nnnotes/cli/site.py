"""Build a named static site from depot products and publish its complete layout."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ..content import sitefacts
from ..formats.compression import DEFAULT_ENCODING, ENCODINGS
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO
from ..pipeline import contract
from ..pipeline.depot import open_depot
from ..source.config import ConfigError, use
from ..source.languages import check
from ..source.session import Session
from . import assets, export
from .common import (
    band_args,
    command,
    fonts_extra,
    group,
    live_option_arg,
    live_options,
    nonnegative,
    parse_font,
    parse_languages,
    parse_pair,
    positive,
    report,
    usage,
)


def product_entries(args, cfg, ws, expand=None):
    run, _, aborted = assets.run_request(args, cfg, ws, expand=expand)
    saved = run.write()
    if aborted or run.exit_code:
        report(args, {"run": saved["id"], "tasks": saved["summary"], "failures": run.failures(), "aborted": aborted})
        raise SystemExit(assets.EXIT_ABORTED if aborted else assets.EXIT_FAILED)
    done = {tid: t["key"] for tid, t in run.tasks.items() if t["status"] != "failed"}
    ws.done(done)
    output = {tid for tid in done if ws._output(contract.parse_task_id(tid)[0])}
    placed = ws.layout_input({tid: done[tid] for tid in output})
    for tid, doc in run.results():
        if tid in output:
            placed.add(tid, doc)
    entries, _ = ws.layout_entries("product", placed)
    return entries, saved["summary"]


def cmd_build(args, cfg):
    from ..content import live2d, liveoptions
    from ..pipeline import live as lp
    from ..pipeline import story as sp
    from ..site import catalog, pages
    from ..site.build import Builder, document, documents, model_stats, size
    from ..source.master import table

    charts = bool(args.pair or args.all)
    models = bool(args.live2d or args.all_live2d)
    stories = bool(args.story or args.all_stories)
    if not (charts or models or stories or args.player_only):
        usage(args, "one of --pair --all --live2d --all-live2d --story --all-stories --player-only is required")
    if args.player_only and (models or stories):
        usage(args, "--player-only cannot be combined with a product selection")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.name):
        usage(args, "--name must contain letters, digits, underscores or hyphens")
    requested_options = live_options(args)
    if requested_options and not charts:
        usage(args, "--live-option requires --pair or --all")
    player = pages.check_player(cfg.path("paths", "player"))
    depot = open_depot(cfg)
    if args.player_only:
        saved, _ = catalog.load(depot, args.name)
        if saved is None:
            usage(args, f"site {args.name}: no saved catalog; select products for the first build")
        builder = Builder(depot, args.name, saved["language"], saved["regions"], player)
        report(args, builder.finish(Path(args.out)))
        return
    regions = pages.site_regions(cfg, args.site_regions, args.all_regions)
    if (stories or models) and len({cfg.provider(r) for r in regions}) > 1:
        raise ConfigError("build JP stories/models in a separate site from international releases")
    language = check(cfg.get("catalog", "language"))
    builder = Builder(depot, args.name, language, pages.region_meta(cfg, regions), player, encoding=args.compress)
    for region in regions:
        cfg.for_region(region).require_path("paths", "apk")
    if charts:
        fonts_extra(args.fonts)
    if stories:
        from ..unity.tmpfont import require_extra

        require_extra("--story / --all-stories")
    masters = (
        pages.region_masters(cfg, regions)
        if charts or stories
        else {r: Session(cfg).master_dir(r) if cfg.path(*cfg.master(r)) is not None else None for r in regions}
    )
    pairs = {r: set(sitefacts.all_pairs(md)) for r, md in masters.items()} if charts else {}
    episodes = (
        {r: {row["_id"] for row in table(md, "MasterAdv")} for r, md in masters.items() if md is not None}
        if stories
        else {}
    )
    if args.pair and (missing := set(args.pair) - set.union(*pairs.values())):
        usage(args, "charts not in the site's master data: " + ", ".join(f"{m}:{d}" for m, d in sorted(missing)))
    if args.story and (missing := set(args.story) - set.union(*episodes.values())):
        usage(args, "stories not in the site's master data: " + ", ".join(map(str, sorted(missing))))
    summaries = []
    try:
        for region in regions:
            regional = use(cfg.for_region(region))
            md = masters[region]
            lang = check(regional.get("catalog", "language") or language)
            wanted = args.story_languages or [lang]
            base = {
                **vars(args),
                "audio_format": args.format,
                "movie_format": "webm",
                "views": "none",
                "live_languages": [lang],
                "story_languages": wanted,
            }
            if charts:
                options = liveoptions.resolve(requested_options, md)
                selected = pairs[region] if args.all else set(args.pair) & pairs[region]
                if args.all:
                    builder.replace.add((region, "live"))
                for music, difficulty in sorted(selected):
                    request = argparse.Namespace(**{**base, "music_id": music, "difficulty": difficulty})
                    ws = export.prepare_live(request, regional)
                    entries, summary = product_entries(request, regional, ws)
                    summaries.append(summary)
                    assert ws.products is not None
                    subject = ws.products[lp.LIVE][0]
                    source = lp.LiveStage().manifest(subject)
                    manifests = documents(builder.depot, entries)
                    live = document(builder.depot, manifests, source, "live.json")
                    music_doc = document(builder.depot, manifests, live["parts"]["music"], "music.json")
                    facts = {
                        **sitefacts.chart(live, music_doc, lang),
                        "audio": not args.no_audio,
                        "audioFormat": args.format,
                        "flows": ["normal"],
                    }
                    snapshot = builder.add(
                        entries,
                        source,
                        "live",
                        f"{music}_{difficulty}",
                        region,
                        facts,
                        languages=[lang],
                        options=options,
                        audio=not args.no_audio,
                    )
                    facts["bytes"] = size(snapshot, source)
            if models:
                ws = assets.Workspace(argparse.Namespace(**base), regional, layouts=["product"])
                ws.run_index()
                keys = live2d.model_keys(loc["primaryKey"] for loc in ws.index()["locations"])
                try:
                    selected = live2d.select_models(args.live2d, keys)
                except ValueError as error:
                    usage(args, str(error))
                names = sitefacts.optional_model_names(md, lang)
                if args.all_live2d:
                    builder.replace.add((region, "model"))
                for mid, key in sorted(selected.items()):
                    request = argparse.Namespace(**{**base, "key": key, "player": True})
                    model_ws = export.prepare_live2d(request, regional)
                    entries, summary = product_entries(request, regional, model_ws)
                    summaries.append(summary)
                    source = f"models/{mid}.json"
                    facts = {"key": key, **names.get(key, {})}
                    snapshot = builder.add(entries, source, "model", mid, region, facts)
                    facts.update(model_stats(snapshot, source))
            if stories:
                assert md is not None
                groups = sitefacts.story_groups(md)
                selected = episodes[region] if args.all_stories else set(args.story) & episodes[region]
                if args.all_stories:
                    builder.replace.add((region, "story"))
                for adv_id in sorted(selected):
                    request = argparse.Namespace(**{**base, "adv_id": adv_id})
                    ws, expand = export.prepare_story(request, regional)
                    entries, summary = product_entries(request, regional, ws, expand)
                    summaries.append(summary)
                    source = sp.StoryStage().manifest(str(adv_id))
                    manifests = documents(builder.depot, entries)
                    episode = document(builder.depot, manifests, source, "episode.json")
                    facts = {
                        **sitefacts.story(episode, groups.get(adv_id, []), wanted, lang),
                        "audio": not args.no_audio,
                        "audioFormat": args.format,
                        "fonts": args.fonts,
                    }
                    snapshot = builder.add(
                        entries, source, "story", adv_id, region, facts, languages=wanted, audio=not args.no_audio
                    )
                    facts["size"] = {
                        "common": 0,
                        "models": 0,
                        "languages": {code: size(snapshot, source, code) for code in wanted},
                    }
                    names = sitefacts.optional_model_names(md, lang)
                    for path, model in manifests.items():
                        if model["product"] != "live2d.model" or path not in snapshot.paths:
                            continue
                        key = model["subject"]
                        builder.topics.append(
                            {
                                "kind": "model",
                                "id": live2d.model_id(key),
                                "region": region,
                                "manifest": snapshot.paths[path],
                                "entry": {"key": key, **names.get(key, {}), **model_stats(snapshot, path)},
                            }
                        )
        result = builder.finish(Path(args.out))
        result["products"] = {key: sum(s[key] for s in summaries) for key in ["tasks", "hit", "ran", "failed"]}
        report(args, result)
    finally:
        use(cfg)


def register(sub) -> None:
    ss = group(sub, "site", "the static site of ournotes-player")
    c = command(
        ss,
        "build",
        cmd_build,
        "live charts, Live2D models and stories -> static site for ournotes-player (shared player + per-chart / "
        "per-model / per-story data)",
    )
    c.add_argument("-o", "--out", required=True, metavar="SITE", help="site directory")
    c.add_argument("--name", default="default", help="persistent site name in the depot (default: default)")
    c.add_argument("--player", help="ournotes-player checkout (built) or installed package ([paths] player)")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--pair", type=parse_pair, action="append", help="<musicId>:<difficulty> (repeatable)")
    g.add_argument("--all", action="store_true", help="every MasterLiveMusic x difficulty")
    g.add_argument("--player-only", action="store_true", help="publish the saved catalog with current player pages")
    m = c.add_mutually_exclusive_group()
    m.add_argument(
        "--live2d",
        action="append",
        metavar="MODEL",
        help="Live2D model id or key Character/Live2D/<group>/<name>/model/<name> (repeatable)",
    )
    m.add_argument("--all-live2d", action="store_true", help="every Live2D model of the catalog")
    c.add_argument(
        "--format",
        default=DEFAULT_AUDIO_FORMAT,
        choices=tuple(WEB_AUDIO),
        help=f"audio format of the charts and stories (default {DEFAULT_AUDIO_FORMAT})",
    )
    c.add_argument("--no-audio", action="store_true", help="export no audio files")
    c.add_argument(
        "--compress",
        default=DEFAULT_ENCODING,
        choices=ENCODINGS,
        help="encoding of the compressible assets (JSON, shaders, moc3, ...) where it makes them "
        "smaller: gzip (default), br (brotli) or none",
    )
    c.add_argument("--workers", type=nonnegative, metavar="N", help="product task worker processes; 0: this process")
    c.add_argument("--memory", type=assets._gib, metavar="GiB", help="product task memory budget")
    c.add_argument("--fetch-workers", type=positive, metavar="N", help="parallel source downloads")
    c.add_argument("--stored-catalog", metavar="LABEL|SHA", help="a stored catalog version of the depot")
    t = c.add_mutually_exclusive_group()
    t.add_argument(
        "--story", type=int, action="append", metavar="ADV_ID", help="story episode: its MasterAdv id (repeatable)"
    )
    t.add_argument("--all-stories", action="store_true", help="every story episode (MasterAdv) of the master data")
    c.add_argument(
        "--story-languages",
        type=parse_languages,
        metavar="LANGS",
        help="languages of the stories' text, comma-separated (default: the catalog language)",
    )
    c.add_argument(
        "--font",
        type=parse_font,
        action="append",
        metavar="LANG=PATH",
        help="font file the story text of a language is drawn with (repeatable; [paths] fonts.<lang>); "
        "emoji=PATH: the colour emoji font the emoji sprites are drawn from ([paths] fonts.emoji)",
    )
    r = c.add_mutually_exclusive_group()
    r.add_argument(
        "--site-region",
        dest="site_regions",
        action="append",
        metavar="REGION",
        help="a region the site serves: a [servers.<region>] table (repeatable; the first is the base; "
        "default: [catalog] region)",
    )
    r.add_argument("--all-regions", action="store_true", help="every configured region")
    c.add_argument(
        "--fonts",
        default="open",
        choices=("open", "game"),
        help="text of the start canvas and stories: open (default): glyphs from your font files (--font); "
        "game: the game's TMP fonts (needs the 'fonts' extra)",
    )
    band_args(c)
    live_option_arg(c)
