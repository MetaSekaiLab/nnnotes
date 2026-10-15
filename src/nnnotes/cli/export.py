"""`nnnotes export`: the selected bundles of the catalog into the depot and its layouts (`assets`), and single
subjects into a directory or file: an ADV episode, a story, a live, a Live2D model, a spot, a room, shaders, a cue
sheet, the APK's UI library."""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.jsonio import write_json
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO
from ..source.config import use
from ..source.session import Session
from . import assets
from .common import (
    DIFFICULTY_CHOICES,
    audio_args,
    band_args,
    command,
    flac_level_arg,
    flac_options,
    group,
    known_keys,
    known_row,
    live_option_arg,
    live_options,
    nonnegative,
    out_arg,
    report,
    usage,
)


def cmd_episode(args, cfg):
    from ..content import adv

    md = Session(cfg).master_dir()
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    cat = Session(cfg).catalog()
    doc = adv.to_json(adv.extract(cat, md, args.adv_id))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, doc)
    r = {"out": str(out), "commands": doc["commandCount"], "resources": len(doc["resources"])}
    report(args, r, lambda r: [f"{r['out']}  ({r['commands']} commands, {r['resources']} resources)"])


def cmd_story(args, cfg):
    from ..pipeline import contract
    from ..pipeline import story as sp
    from ..pipeline.stages import Env

    cfg.require_path("paths", "apk")  # the ADV settings, the built-in graphics settings and CRI Lips are in the APK
    use(cfg)  # the tools the atoms name are part of the keys
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    ws = assets.Workspace(args, cfg, layouts=["product"])
    env = Env(ws.depot, {"master": md})
    subject = str(args.adv_id)
    classes = {sp.STORY: sp.StoryStage, **sp.PART_STAGES}
    ws.run_index()
    held = {loc["primaryKey"] for loc in ws.index()["locations"]}

    def request(products: dict) -> tuple[dict, list[str]]:
        keys = {k for n, subjects in products.items() for s in subjects for k in classes[n]().keys(s, env)}
        return products, sorted(f"key:{k}" for k in keys if k in held)

    def parts(run):  # the products story.json names, with the story
        tid = contract.task_id(sp.STORY, subject)
        task = run.tasks.get(tid)
        if task is None or task["status"] == "failed":
            return None
        doc = ws.depot.committed(task["key"], tid)
        art = next((a for a in doc["artifacts"] if a["id"] == contract.artifact_id(tid, "story.json")), None)
        if art is None:  # the story is unsupported: the run reports it
            return None
        products = {sp.STORY: [subject], **sp.part_products(contract.loads(ws.depot.read(art["content"]["sha256"])))}
        missing = sorted(k for n, subjects in products.items() if n != sp.STORY for s in subjects
                         for k in classes[n]().keys(s, env) if k not in held)  # fmt: skip
        if missing:
            usage(args, f"episode {args.adv_id}: {', '.join(missing)} not in the catalog")
        return request(products)

    ws.request(*request({sp.STORY: [subject]}))
    assets.export(args, cfg, ws, expand=parts)


def cmd_live(args, cfg):
    from ..content import live, liveaudio, livenotes, liveoptions
    from ..pipeline import live as lp
    from ..pipeline.stages import Env
    from ..source.master import table

    cfg.require_path("paths", "apk")  # the built-in graphics and sound settings and the CRI ACF are in the APK
    use(cfg)  # the tools the atoms name are part of the keys
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterLiveMusic", args.music_id, "music")
    try:
        options = liveoptions.resolve(live_options(args), md)
    except liveoptions.OptionSpecError as e:
        usage(args, str(e))
    band = args.band
    if args.leader_card is not None:
        tables = {n: table(md, n) for n in ("MasterMemberCard", "MasterCharacter", "MasterLiveMusic")}
        try:
            band = live.band_choice(tables, args.music_id, leader_card=args.leader_card)["band"]
        except KeyError as e:
            usage(args, e.args[0])
    ws = assets.Workspace(args, cfg, layouts=["product"])
    env = Env(ws.depot, {"master": md})
    stage, subject = lp.LiveStage(), live.live_subject(args.music_id, args.difficulty, band)
    try:  # no chart of the difficulty, a music without a vocal character, a master table missing
        b = stage.live_record(subject, env)["band"]["band"]
        products = {lp.LIVE: [subject], **stage.parts(subject, env)}
    except (KeyError, ValueError) as e:
        usage(args, e.args[0] if e.args else type(e).__name__)
    ws.request(products, [])
    ws.params(ws.pipeline())  # the flags the stages take, checked before the index
    ws.run_index()
    held = {loc["primaryKey"] for loc in ws.index()["locations"]}
    missing = [k.format(band=b) for k in live.BAND_KEYS if k.format(band=b) not in held]
    if missing:
        usage(args, f"band {b}: {', '.join(missing)} not in the catalog")
    skins = {r["_id"]: r["_assetName"] for r in table(md, "MasterLiveNoteSkin")}
    effects = {r["_id"]: r["_assetName"] for r in table(md, "MasterLiveNoteEffectSkin")}
    products[lp.NOTESKIN] += [skins[i] for i in options.designs if skins[i] not in products[lp.NOTESKIN]]
    products[lp.NOTEEFFECT] += [  # a Light variant where the catalog has one, as the game
        n for n, light in options.effect_sets(effects) if not light or livenotes.effect_settings_key(n) in held
    ]
    if options.se_patterns:
        products[lp.SOUNDS].append(liveaudio.sounds_subject(options.se_patterns))
    classes = {lp.LIVE: lp.LiveStage, **lp.PART_STAGES}
    stages = {n: classes[n]() for n in products}
    keys = {k for n, subjects in products.items() for s in subjects for k in stages[n].keys(s, env)}
    ws.request(products, sorted(f"key:{k}" for k in keys))
    assets.export(args, cfg, ws)


def cmd_live2d(args, cfg):
    from ..content import live2d
    from ..pipeline.live2d import CUBISM, MODEL

    cfg.require_path("paths", "apk")  # the Cubism component classes and the mask materials are in the APK
    use(cfg)  # the tools the atoms name are part of the keys
    ws = assets.Workspace(args, cfg, layouts=["product" if args.player else "files"])
    ws.run_index()
    keys = (loc["primaryKey"] for loc in ws.index()["locations"])
    try:  # a model id or a model key
        (key,) = live2d.select_models([args.key], live2d.model_keys(keys)).values()
    except ValueError as e:
        usage(args, str(e))
    ws.request({MODEL if args.player else CUBISM: [key]}, [f"key:{key}"])
    assets.export(args, cfg, ws)


def cmd_spot(args, cfg):
    from ..pipeline.spot import ROOM, SPOT
    from ..source.master import table

    cfg.require_path("paths", "apk")  # the Spot / Spine component classes are read from the APK
    use(cfg)  # the tools the atoms name are part of the keys
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterHomeSpot", args.spot_id, "spot")
    (row,) = [r for r in table(md, "MasterHomeSpot") if r.get("_id") == args.spot_id]
    situation, background = row["_situationAssetPath"], row["_backgroundAssetPath"]
    ws = assets.Workspace(args, cfg, layouts=["files"])
    ws.request({SPOT: [situation], ROOM: [background]}, [f"key:{situation}", f"key:{background}"])
    assets.export(args, cfg, ws)


def cmd_room(args, cfg):
    from ..content import room
    from ..pipeline.spot import ROOM

    try:
        room.room_name(args.key)
    except ValueError as e:
        usage(args, str(e))
    use(cfg)
    ws = assets.Workspace(args, cfg, layouts=["files"])
    ws.run_index()
    if not any(loc["primaryKey"] == args.key for loc in ws.index()["locations"]):
        usage(args, f"{args.key}: not a key of the catalog")
    ws.request({ROOM: [args.key]}, [f"key:{args.key}"])
    assets.export(args, cfg, ws)


def cmd_shader(args, cfg):
    from ..formats import shader

    cat = Session(cfg).catalog()
    if args.key:
        known_keys(args, cat, [args.key])
        bundles = cat.fetch_key(args.key)
    else:
        if cat.apk is None:
            cfg.require_path("paths", "apk")
        bundles = []
        for s in args.apk_bundle:
            try:
                bundles.append(cat.apk_bundle(s))
            except KeyError as e:  # none or several bundles match
                usage(args, f"--apk-bundle {e.args[0]}")
    r = shader.dump(bundles, Path(args.out))
    report(args, r, lambda r: [f"{r['count']} shaders, {r['variants']} variants", *(f"  {n}" for n in r["names"])])


def cmd_audio(args, cfg):
    from ..formats import cri

    flac = flac_options(args)
    cfg.require_path("paths", "apk")  # the HCA keycode is read from the APK
    cat = Session(cfg).catalog()
    if not cat.has(f"Cri/Sound/{args.cue_sheet}"):
        usage(args, f"cue sheet {args.cue_sheet}: no key Cri/Sound/{args.cue_sheet} in the catalog")
    out = Path(args.out)
    cues = cri.decode(cat, args.cue_sheet, out, fmt=args.format, **flac)
    report(args, {"out": str(out), "cues": len(cues)}, lambda r: [f"{r['out']}  ({r['cues']} cues)"])


def cmd_ui(args, cfg):
    from ..content import ui

    if args.limit is not None and args.limit < 1:
        usage(args, "--limit must be positive")
    index = ui.export_apk_library(
        cfg,
        args.out,
        keys=args.key,
        prefix=args.prefix,
        limit=args.limit,
        dependencies=not args.no_dependencies,
        force=args.force,
    )
    report(
        args,
        {
            "index": str(Path(args.out).resolve() / "index.json"),
            "counts": index["counts"],
            "embedded": len(index["embedded"]),
            "controllers": len(index["controllers"]),
        },
    )
    if index["counts"].get("failed"):
        sys.exit(1)


def product_args(c) -> None:
    """The options of a command that exports products through the depot (as export assets)."""
    c.add_argument(
        "--stored-catalog",
        metavar="LABEL|SHA",
        help="a catalog version of the depot (`catalog versions`; default: the current catalog)",
    )
    c.add_argument("--workers", type=nonnegative, metavar="N", help="worker processes (default: the usable CPUs)")
    c.add_argument("--link", choices=("auto", "clone", "hard", "copy"), help="as export assets --link")
    c.add_argument("--explain", action="store_true", help="print every setting of the run first")
    c.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    c.set_defaults(views="none", strict=False)


def register(sub) -> None:
    es = group(sub, "export", "game data -> common formats: the depot's asset export and single subjects")

    c = command(
        es,
        "assets",
        assets.cmd_export,
        "every object of the selected bundles -> common formats (the depot and its layouts)",
    )
    out_arg(c, "output directory")
    assets.run_args(c)
    c.add_argument(
        "--link",
        choices=("auto", "clone", "hard", "copy"),
        help="how the layout files are made from the depot: auto (default; [export] link): a clone where "
        "the file system makes them, else a hard link (read-only), else a copy; or that method only",
    )
    c.add_argument("--strict", action="store_true", help="exit 1 when a view has gaps")
    c.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")

    c = command(es, "episode", cmd_episode, "an ADV episode -> JSON")
    c.add_argument("adv_id", type=int, help="MasterAdv id of the episode")
    out_arg(c, "output .json file")

    c = command(
        es,
        "story",
        cmd_story,
        "an ADV episode -> the story product and the products it plays with (scene, assets, Live2D models, "
        "cue sheets, videos, chat, CRI Lips)",
    )
    c.add_argument("adv_id", type=int, help="MasterAdv id of the episode")
    c.add_argument(
        "--format",
        dest="audio_format",
        metavar="FORMAT",
        default=DEFAULT_AUDIO_FORMAT,
        choices=sorted(WEB_AUDIO),
        help=f"web audio format of the cue sheets (default {DEFAULT_AUDIO_FORMAT})",
    )
    flac_level_arg(c, "of the decoded sounds")
    out_arg(c, "output directory")
    product_args(c)
    c.set_defaults(movie_format="webm")  # the story videos: cri.movie's movie.webm

    c = command(
        es,
        "live",
        cmd_live,
        "a live (music + difficulty) -> the live product and the products it plays with (scene, note highway, "
        "sounds, music)",
    )
    c.add_argument("music_id", type=int, help="MasterLiveMusic id")
    c.add_argument(
        "--difficulty", default="expert", choices=DIFFICULTY_CHOICES, help="chart difficulty (default expert)"
    )
    c.add_argument(
        "--format",
        dest="audio_format",
        metavar="FORMAT",
        default=DEFAULT_AUDIO_FORMAT,
        choices=sorted(WEB_AUDIO),
        help=f"web audio format of the BGM (default {DEFAULT_AUDIO_FORMAT})",
    )
    flac_level_arg(c, "of the decoded sounds")
    band_args(c)
    live_option_arg(c)
    out_arg(c, "output directory")
    product_args(c)

    c = command(es, "live2d", cmd_live2d, "a Live2D model -> its Cubism SDK files, or the player's model")
    c.add_argument("key", help="model key Character/Live2D/<group>/<name>/model/<name>, or its model id <name>")
    out_arg(c, "output directory")
    c.add_argument(
        "--player",
        action="store_true",
        help="write the player's model (models/<id>.json and assets/) instead of the Cubism SDK files",
    )
    product_args(c)

    c = command(es, "spot", cmd_spot, "a spot -> spot.json + Spine files + room.glb + room.json")
    c.add_argument("spot_id", type=int, help="MasterHomeSpot id")
    out_arg(c, "output directory")
    product_args(c)

    c = command(es, "room", cmd_room, "a spot background -> room.glb + room.json")
    c.add_argument("key", help="background key Spot/<place>/Background/<place>")
    out_arg(c, "output directory")
    product_args(c)

    c = command(es, "shader", cmd_shader, "the shaders of a key's bundle closure or of APK bundles")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="addressable key: the shaders of its bundle closure")
    g.add_argument("--apk-bundle", nargs="+", help="substring(s) of APK bundle file names")
    out_arg(c, "output directory")

    c = command(es, "audio", cmd_audio, "a CRI cue sheet -> audio files")
    c.add_argument("cue_sheet", help="cue sheet name: the key Cri/Sound/<cue_sheet> of the catalog")
    out_arg(c, "output directory")
    audio_args(c)

    c = command(es, "ui", cmd_ui, "the APK's UI prefabs -> an offline UI prefab library for ournotes-player/ui")
    out_arg(c, "UI data directory outside the source repositories")
    c.add_argument("--key", action="append", help="exact APK catalog key (repeatable)")
    c.add_argument("--prefix", default="EmbUI/", help="APK UI key prefix (default EmbUI/)")
    c.add_argument("--limit", type=int, help="maximum number of keys")
    c.add_argument("--no-dependencies", action="store_true", help="omit additional prefab roots and controllers")
    c.add_argument("--force", action="store_true", help="re-export the selected keys")
