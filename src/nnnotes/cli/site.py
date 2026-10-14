"""`nnnotes site build`: live charts, Live2D models and stories -> a static site for ournotes-player."""

from __future__ import annotations

import sys
from pathlib import Path

from ..site.compress import DEFAULT_ENCODING, ENCODINGS
from ..site.webaudio import DEFAULT_AUDIO_FORMAT, WEB_AUDIO
from ..source.config import ConfigError, use
from ..source.session import Session
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
)


def cmd_build(args, cfg):
    from ..content import liveoptions
    from ..site import sitefiles, siteindex, storysite, web, webmodel

    request = live_options(args)
    charts, models = bool(args.pair or args.all), bool(args.live2d or args.all_live2d)
    stories = bool(args.story or args.all_stories)
    if not (charts or models or stories or args.player_only or args.reingest_json):
        args.usage(
            "one of the arguments --pair --all --live2d --all-live2d --story --all-stories --player-only "
            "--reingest-json is required"
        )
    if models and (args.player_only or args.reingest_json):
        args.usage("--player-only and --reingest-json build nothing: leave out --live2d / --all-live2d")
    if stories and (args.player_only or args.reingest_json):
        args.usage("--player-only and --reingest-json build nothing: leave out --story / --all-stories")
    if request and not charts:
        args.usage("--live-option applies to charts: give --pair or --all")
    player = cfg.path("paths", "player")
    out = Path(args.out)
    if args.player_only or args.reingest_json:
        r = {
            **(sitefiles.reingest_json(out, args.compress) if args.reingest_json else {}),
            **sitefiles.write_player(out, sitefiles.check_player(player)),
            **siteindex.write_index(out),
        }
    else:
        sitefiles.check_player(player)
        regions = None  # the one [catalog] region
        if args.site_regions or args.all_regions:
            regions = sitefiles.site_regions(cfg, args.site_regions, args.all_regions)
        if (stories or models) and regions and len({cfg.provider(r) for r in regions}) > 1:
            raise ConfigError("build JP stories/models in a separate site directory from international releases")
        if (stories or models) and regions:
            cfg = use(cfg.for_region(regions[0]))
        base = {"region": regions[0]} if regions else {}
        unknown = web.unknown_pairs(cfg, args.pair, regions) if args.pair else []
        if unknown:
            args.usage(
                f"chart{'s' if len(unknown) > 1 else ''} {', '.join(f'{m}:{d}' for m, d in unknown)}: no "
                f"MasterLiveMusicScore row in the master data of the site's region(s)"
            )
        if request:  # a value a region's master data does not have
            try:
                for md in sitefiles.region_masters(cfg, sitefiles.site_regions(cfg, regions)).values():
                    liveoptions.resolve(request, md)
            except liveoptions.OptionSpecError as e:
                args.usage(str(e))
        r = {}
        encoding = args.compress
        if models:
            cfg.require_path("paths", "apk")  # the Cubism component classes and mask materials: the APK
            try:
                selected = webmodel.catalog_models(Session(cfg).catalog(bundles=False, **base), args.live2d)
            except ValueError as e:
                args.usage(str(e))
            r.update(
                webmodel.build(
                    out,
                    selected,
                    cfg,
                    player,
                    force=args.force,
                    tmp_dir=args.tmp,
                    workers=args.workers,
                    encoding=encoding,
                    **base,
                )
            )
        if charts:
            fonts_extra(args.fonts)
            r.update(
                web.build(
                    out,
                    None if args.all else args.pair,
                    cfg,
                    player,
                    args.format,
                    audio=not args.no_audio,
                    force=args.force,
                    tmp_dir=args.tmp,
                    workers=args.workers,
                    band=args.band,
                    leader_card=args.leader_card,
                    regions=regions,
                    fonts=args.fonts,
                    read_workers=args.read_workers,
                    live_options=request,
                    encoding=args.compress,
                )
            )
        if stories:
            from ..unity.tmpfont import require_extra

            require_extra("--story / --all-stories")  # the font assets of the story text (open and game)
            cfg.require_path("paths", "apk")  # the ADV settings and UI are embedded content of the APK
            unknown = storysite.unknown_stories(cfg, args.story, regions) if args.story else []
            if unknown:
                noun = "stories" if len(unknown) > 1 else "story"
                args.usage(
                    f"{noun} {', '.join(map(str, unknown))}: no MasterAdv row in the master data of the "
                    f"site's region(s)"
                )
            r.update(
                storysite.build(
                    out,
                    args.story,
                    cfg,
                    player,
                    args.format,
                    audio=not args.no_audio,
                    force=args.force,
                    tmp_dir=args.tmp,
                    workers=args.workers,
                    regions=regions,
                    story_languages=args.story_languages,
                    fonts=args.fonts,
                    fonts_flags=dict(args.font or []),
                    encoding=encoding,
                )
            )
    report(args, r)
    if (
        r.get("failed")
        or r.get("modelsFailed")
        or r.get("storiesFailed")
        or (r.get("storyModels") or {}).get("modelsFailed")
    ):
        sys.exit(1)


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
    c.add_argument("--player", help="ournotes-player checkout (built) or installed package ([paths] player)")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--pair", type=parse_pair, action="append", help="<musicId>:<difficulty> (repeatable)")
    g.add_argument("--all", action="store_true", help="every MasterLiveMusic x difficulty")
    g.add_argument(
        "--player-only", action="store_true", help="rewrite the player files, charts.json and models.json only"
    )
    g.add_argument("--reingest-json", action="store_true", help="store every chart's and model's JSON files again")
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
    c.add_argument(
        "--force",
        action="store_true",
        help="rebuild charts, models and stories whose manifest exists (stories: also their models)",
    )
    c.add_argument("--tmp", help="directory for the temporary live and model builds (default <site>.tmp)")
    c.add_argument(
        "--workers",
        type=nonnegative,
        metavar="N",
        help="parallel music / story / model processes (default a quarter of the CPUs, up to 8; models up to 4; "
        "0: this process)",
    )
    c.add_argument(
        "--read-workers",
        type=positive,
        metavar="N",
        help="chart read sets run at a time (default half the CPUs, up to 16)",
    )
    t = c.add_mutually_exclusive_group()
    t.add_argument(
        "--story", type=int, action="append", metavar="ADV_ID", help="story episode: its MasterAdv id (repeatable)"
    )
    t.add_argument("--all-stories", action="store_true", help="every story episode (MasterAdv) of the master data")
    c.add_argument(
        "--story-languages",
        type=parse_languages,
        metavar="LANGS",
        help="languages of the stories' text, comma-separated (default: ja,en,zh-Hant,zh-Hans,ko)",
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
        help="text of the start canvas and the stories: open (default): the start canvas has layout and "
        "style only, the stories glyphs from your font files (--font); game: the game's TMP fonts "
        "(needs the 'fonts' extra)",
    )
    band_args(c)
    live_option_arg(c)
