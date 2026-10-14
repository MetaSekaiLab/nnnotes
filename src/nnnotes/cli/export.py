"""`nnnotes export`: the selected bundles of the catalog into the store and its layouts (`assets`), and single
subjects into a directory or file: an ADV episode, a story, a live, a Live2D model, a spot, a room, shaders, a cue
sheet, the APK's UI library."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..core.jsonio import write_json
from ..pipeline.store import write_file
from ..source.session import Session
from . import assets
from .common import (
    DIFFICULTY_CHOICES,
    audio_args,
    band_args,
    command,
    flac_options,
    fonts_arg,
    fonts_extra,
    group,
    known_row,
    live_option_arg,
    live_options,
    out_arg,
    report,
    usage,
)


def cmd_episode(args, cfg):
    from ..pipeline import contract
    from ..pipeline.advstages import EPISODE, episode_keys
    from ..source import master

    md = Session(cfg).master_dir()
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    (row,) = [r for r in master.table(md, "MasterAdv") if r["_id"] == args.adv_id]
    run = assets.SubjectRun(args, cfg)
    subject = str(args.adv_id)
    run.run([EPISODE], keys=episode_keys(row["_advEpisodeAsset"]), requests={EPISODE: [subject]})
    doc = run.result(contract.task_id(EPISODE, subject))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    (art,) = doc["artifacts"]
    write_file(out, run.store.read(art["content"]["sha256"]))
    facts = art["semantics"]["facts"]
    r = {"out": str(out), "commands": facts["commands"], "resources": facts["resources"]}
    report(args, r, lambda r: [f"{r['out']}  ({r['commands']} commands, {r['resources']} resources)"])


def cmd_story(args, cfg):
    from ..content import story
    from ..site import webmodel
    from ..source import languages
    from ..unity.player import PlayerData

    fonts_extra(args.fonts)
    flac = flac_options(args)
    out = Path(os.path.abspath(args.out))
    models = Path(os.path.abspath(args.models or Path(args.out) / os.pardir / "live2d"))
    try:
        os.path.relpath(models, out)
    except ValueError:
        usage(args, f"--models {models}: not on the drive of {out} (story.json names it relative to the story)")
    language = languages.check(cfg.require("catalog", "language"))  # the story UI's language
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    cat = Session(cfg).catalog()
    player = PlayerData(Session(cfg).apk())
    source = webmodel.ModelDir(cat, player, models, force=args.force)
    r = story.build(
        cat,
        md,
        player,
        args.adv_id,
        out,
        audio_format=args.format,
        audio=not args.no_audio,
        fonts=args.fonts,
        models=source,
        language=language,
        audio_options=flac,
    )
    report(args, {**r, "modelsBuilt": source.built, "modelsSkipped": source.skipped})


def cmd_live(args, cfg):
    from ..content import live, liveoptions
    from ..site.web import all_pairs
    from ..source import languages
    from ..unity.player import PlayerData

    fonts_extra(args.fonts)
    request = live_options(args)
    flac = flac_options(args)
    language = languages.check(cfg.require("catalog", "language"))
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterLiveMusic", args.music_id, "music")
    if (args.music_id, args.difficulty) not in all_pairs(md):
        usage(args, f"music {args.music_id}: no {args.difficulty} chart (no MasterLiveMusicScore row)")
    try:
        options = liveoptions.resolve(request, md)
    except liveoptions.OptionSpecError as e:
        usage(args, str(e))
    cat = Session(cfg).catalog()
    try:  # an unknown --leader-card, a --band without its scene keys
        live.resolve_band(cat, md, args.music_id, band=args.band, leader_card=args.leader_card)
    except (KeyError, ValueError) as e:
        usage(args, e.args[0] if e.args else type(e).__name__)
    r = live.build(
        cat,
        md,
        PlayerData(Session(cfg).apk()),
        args.music_id,
        args.difficulty,
        Path(args.out),
        audio_format=args.format,
        band=args.band,
        leader_card=args.leader_card,
        language=language,
        fonts=args.fonts,
        options=options,
        **flac,
    )
    report(args, r)


def cmd_live2d(args, cfg):
    from ..pipeline import contract
    from ..pipeline.modelstages import LIVE2D
    from ..site import webmodel

    cfg.require_path("paths", "apk")  # the Cubism component classes are read from the APK
    run = assets.SubjectRun(args, cfg)
    prefix = webmodel.LIVE2D_PREFIX
    keys = {loc["primaryKey"] for loc in run.index()["locations"] if loc["primaryKey"].startswith(prefix)}
    try:  # a model id or a model key
        (key,) = webmodel.select([args.key], webmodel.model_keys(sorted(keys))).values()
    except ValueError as e:
        usage(args, str(e))
    run.run([LIVE2D], keys=[key])
    doc = run.result(contract.task_id(LIVE2D, key))
    out = Path(args.out)
    assets.place(run.store, doc, out)
    (summary,) = [a["semantics"]["facts"] for a in doc["artifacts"] if a["semantics"]["kind"] == "live2d.model3"]
    report(args, summary)


def _facts(doc: dict, kind: str) -> dict:
    """The facts of the one artifact of `kind` of a result."""
    (facts,) = [a["semantics"]["facts"] for a in doc["artifacts"] if a["semantics"]["kind"] == kind]
    return facts


def _known_keys(args, index: dict, keys) -> None:
    """A usage error (exit 2) naming the keys the catalog index does not have."""
    known = {loc["primaryKey"] for loc in index["locations"]}
    unknown = [k for k in keys if k not in known]
    if unknown:
        usage(args, f"not a key of the catalog: {', '.join(unknown)}")


def cmd_spot(args, cfg):
    from ..pipeline import contract
    from ..pipeline.modelstages import ROOM, SPOT, Closures
    from ..pipeline.objexport import EXPORT
    from ..source import master

    cfg.require_path("paths", "apk")  # the Spot / Spine component classes are read from the APK
    md = Session(cfg).master_dir()
    known_row(args, md, "MasterHomeSpot", args.spot_id, "spot")
    (row,) = [r for r in master.table(md, "MasterHomeSpot") if r["_id"] == args.spot_id]
    bg, situation = row["_backgroundAssetPath"], row["_situationAssetPath"]
    run = assets.SubjectRun(args, cfg)
    index = run.index()
    _known_keys(args, index, [bg, situation])
    closures = Closures(index)
    order = closures.closure_bundles(bg) + closures.closure_bundles(situation)
    spot_id = str(args.spot_id)
    run.run(
        [SPOT, ROOM, EXPORT],
        {EXPORT: {"classes": ["Shader"]}},
        keys=[bg, situation],
        requests={SPOT: [spot_id], ROOM: [bg]},
    )
    out = Path(args.out)
    doc = run.result(contract.task_id(SPOT, spot_id))
    room_doc = run.result(contract.task_id(ROOM, bg))
    assets.place(run.store, doc, out)
    assets.place(run.store, room_doc, out)
    s = _write_shaders(run, order, out / "shaders")
    rm = _facts(room_doc, "room.glb")
    r = {
        "out": str(out),
        "spot": _facts(doc, "spot.json"),
        "room": {"meshes": rm["meshCount"], "inactiveMeshes": rm["inactiveMeshes"]},
        "shaders": {"shaders": s["count"], "variants": s["variants"]},
    }
    report(
        args,
        r,
        lambda r: [
            f"{r['out']}/spot.json  ({r['spot']['tapTargets']} tap targets, {r['spot']['skeletons']} skeletons)",
            f"{r['out']}/room.glb  ({r['room']['meshes']} meshes, {r['room']['inactiveMeshes']} inactive)",
            f"{r['out']}/shaders/  ({r['shaders']['shaders']} shaders, {r['shaders']['variants']} variants)",
        ],
    )


def cmd_room(args, cfg):
    from ..pipeline import contract
    from ..pipeline.modelstages import ROOM

    run = assets.SubjectRun(args, cfg)
    _known_keys(args, run.index(), [args.key])
    run.run([ROOM], keys=[args.key], requests={ROOM: [args.key]})
    doc = run.result(contract.task_id(ROOM, args.key))
    arts = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_file(out, run.store.read(arts["room.glb"]["content"]["sha256"]))
    r = json.loads(run.store.read(arts["room.json"]["content"]["sha256"]))
    r["glb"] = out.name  # the report names the scene's file
    write_json(out.with_suffix(".json"), r)
    report(args, r, lambda r: [f"{r['glb']}  ({r['meshCount']} meshes, {r['inactiveMeshes']} inactive)"])


def _write_shaders(run, order: list[str], out: Path) -> dict:
    """The shaders of the bundles `order` (stable names) from the results of their unity.export tasks (the class
    Shader) into `out`: the first shader of each name, in bundle and object order, and shaders.json
    (shader.write_index)."""
    from ..formats import shader
    from ..pipeline import contract
    from ..pipeline.objexport import EXPORT

    names = {b["stable"]: b["name"] for b in run.index()["bundles"]}
    records: list = []
    for stable in order:
        doc = run.result(contract.task_id(EXPORT, stable))
        arts = {a["id"]: a for a in doc["artifacts"]}
        shaders = [it for it in doc["items"] if it.get("class") == "Shader"]
        for it in sorted(shaders, key=lambda it: contract.parse_object_id(it["object"])):
            if it["status"] != "exported":
                sys.exit(f"nnnotes: shader {it['object']} of {names[stable]}: {it.get('why', {}).get('message')}")
            obj = it["object"]
            index_doc = contract.loads(run.store.read(arts[contract.artifact_id(obj, "index")]["content"]["sha256"]))
            name = index_doc["name"]
            if any(r["name"] == name for r in records):
                continue
            safe = shader.safe_name(name)
            parsed = run.store.read(arts[contract.artifact_id(obj, "json")]["content"]["sha256"])
            out.mkdir(parents=True, exist_ok=True)
            write_file(out / f"{safe}.json", parsed)
            variants = []
            for v in index_doc["variants"]:
                code = run.store.read(arts[contract.artifact_id(obj, v["role"])]["content"]["sha256"])
                write_file(out.joinpath(*v["file"].split("/")), code)
                variants.append({k: x for k, x in v.items() if k != "role"})
            records.append({"name": name, "source": names[stable], "parsed": f"{safe}.json", "variants": variants})
    return shader.write_index(records, out)


def cmd_shader(args, cfg):
    from ..pipeline.modelstages import Closures
    from ..pipeline.objexport import EXPORT

    if not args.key:
        cfg.require_path("paths", "apk")
    run = assets.SubjectRun(args, cfg)
    index = run.index()
    if args.key:
        _known_keys(args, index, [args.key])
        order = Closures(index).closure_bundles(args.key)  # the bundles of the key's closure, as the catalog loads them
    else:
        order = []
        for s in args.apk_bundle:
            names = [b["stable"] for b in index["bundles"] if not b["remote"] and s in b["name"]]
            if len(names) != 1:
                usage(args, f"--apk-bundle {s!r}: {len(names)} APK bundles match")
            order += names
    run.run([EXPORT], {EXPORT: {"classes": ["Shader"]}}, keys=[args.key] if args.key else (), files=order)
    r = _write_shaders(run, order, Path(args.out))
    report(args, r, lambda r: [f"{r['count']} shaders, {r['variants']} variants", *(f"  {n}" for n in r["names"])])


def cmd_audio(args, cfg):
    from ..pipeline import contract
    from ..pipeline.cristages import AUDIO

    flac = flac_options(args)
    cfg.require_path("paths", "apk")  # the HCA keycode is read from the APK's boot data
    run = assets.SubjectRun(args, cfg)
    key = f"Cri/Sound/{args.cue_sheet}"
    if not any(loc["primaryKey"] == key for loc in run.index()["locations"]):
        usage(args, f"cue sheet {args.cue_sheet}: no key {key} in the catalog")
    params = {"format": args.format}
    if "flac_level" in flac:
        params["flac"] = {"level": flac["flac_level"]}
    if args.format == "flac":
        del params["format"]
    run.run([AUDIO], {AUDIO: params}, keys=[key])
    subjects = [s for s, names in run.names(AUDIO).items() if key in names]
    if len(subjects) != 1:
        sys.exit(f"nnnotes: cue sheet {args.cue_sheet}: {len(subjects)} ACBs have the key {key}")
    doc = run.result(contract.task_id(AUDIO, subjects[0]))
    (item,) = doc["items"]
    if item["status"] != "exported":
        sys.exit(f"nnnotes: cue sheet {args.cue_sheet}: {item['why']['message']}")
    out = Path(args.out)
    assets.place(run.store, doc, out)
    (cues,) = [a["semantics"]["facts"]["cues"] for a in doc["artifacts"] if a["semantics"]["kind"] == "cri.cues"]
    report(args, {"out": str(out), "cues": cues}, lambda r: [f"{r['out']}  ({r['cues']} cues)"])


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


def register(sub) -> None:
    es = group(sub, "export", "game data -> common formats: the store's asset export and single subjects")

    c = command(
        es,
        "assets",
        assets.cmd_export,
        "every object of the selected bundles -> common formats (the store and its layouts)",
    )
    out_arg(c, "output directory")
    assets.run_args(c)
    c.add_argument(
        "--link",
        choices=("auto", "clone", "hard", "copy"),
        help="how the layout files are made from the store: auto (default; [export] link): a clone where "
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
        "an ADV episode -> story directory (episode, audio, scene, UI, media, videos) + its Live2D models",
    )
    c.add_argument("adv_id", type=int, help="MasterAdv id of the episode")
    out_arg(c, "output directory")
    c.add_argument(
        "--models", metavar="DIR", help="directory of the Live2D models, one <model id>/ each (default: OUT/../live2d)"
    )
    c.add_argument("--force", action="store_true", help="export the models again whose directory exists")
    audio_args(c)
    c.add_argument("--no-audio", action="store_true", help="do not decode the cue sheets")
    fonts_arg(
        c,
        "open (default): text layout and style only, no font data; game: also the game's TMP fonts "
        "(needs the 'fonts' extra)",
    )

    c = command(es, "live", cmd_live, "a live (music + difficulty) -> self-contained live directory")
    c.add_argument("music_id", type=int, help="MasterLiveMusic id")
    c.add_argument(
        "--difficulty", default="expert", choices=DIFFICULTY_CHOICES, help="chart difficulty (default expert)"
    )
    audio_args(c)
    fonts_arg(
        c,
        "start canvas text: open (default): layout and style only, no font data (your fonts are not used); "
        "game: also the game's TMP fonts (needs the 'fonts' extra)",
    )
    band_args(c)
    live_option_arg(c)
    out_arg(c, "output directory")

    c = command(es, "live2d", cmd_live2d, "a Live2D model -> runtime model directory")
    c.add_argument("key", help="model key Character/Live2D/<group>/<name>/model/<name>, or its model id <name>")
    out_arg(c, "output directory")

    c = command(es, "spot", cmd_spot, "a spot -> spot.json + Spine + room.glb + shaders")
    c.add_argument("spot_id", type=int, help="MasterHomeSpot id")
    out_arg(c, "output directory")

    c = command(es, "room", cmd_room, "a background prefab -> glb")
    c.add_argument("key", help="addressable key of the prefab")
    out_arg(c, "output .glb file")

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
