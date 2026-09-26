"""nnnotes command line.

Every setting (keys, the CDN base of each region, the paths of your own data and tools) comes from the config file,
the environment or the flags below; see config.py and nnnotes.example.toml. JSON summaries are printed as UTF-8
whatever the console encoding.

    nnnotes catalog --prefix Spot/ --limit 40
    nnnotes browse [--port 8000]
    nnnotes pull <key> [<key> ...]
    nnnotes servers [--show-hosts]
    nnnotes master version
    nnnotes master decode <dir or .bin files> -o <out dir>
    nnnotes master download --version <master version> | --latest -o <dir>
    nnnotes adv 10462 -o out/adv_10462.json
    nnnotes story 10462 -o out/story_10462
    nnnotes live2d <Character/Live2D/.../model/... | model id> -o out/live2d/x
    nnnotes spot 10001 -o out/spot_10001
    nnnotes room <Spot/.../Background/...> -o out/room.glb
    nnnotes shader --key <key> | --apk-bundle <substring> -o out/shaders
    nnnotes audio <cueSheet> -o out/audio
    nnnotes crikey [--write <dir>]
    nnnotes player -o out/player.json
    nnnotes live 100001 --difficulty expert [--band 1 | --leader-card <MasterMemberCard id>] [--fonts game]
                 -o out/live_100001
    nnnotes web out/site --player <ournotes-player> [--pair 100001:expert [--pair ...] | --all] [--format aac]
                         [--live2d <model id | key> [--live2d ...] | --all-live2d]
                         [--region <region> [--region ...] | --all-regions]
    nnnotes export -o out/assets [--select group:<group> | key:<prefix> | bundle:<glob> ...] [--layout original,cas]
    nnnotes plan [--select ...] [--json] [--check] [--emit-tasks <dir>]
    nnnotes run-stage <task.json> [...]
    nnnotes catalogs list | import <catalog.bin> | fetch | diff <old> <new>
    nnnotes store verify
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# numpy's OpenBLAS starts one busy thread per CPU in every process that imports it, and no export uses its
# parallelism. Set when the command line loads, before a command imports numpy; the worker processes of a build
# inherit it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from . import __version__, cli_assets
from .addressables import BundleKey
from .catalog import Catalog
from .config import Config, ConfigError, use
from .jsonio import dumps, write_json

DIFFICULTY_CHOICES = ("easy", "normal", "hard", "expert")
AUDIO_CHOICES = ("flac", "ogg", "wav")
# command-line flags of settings: (section, key) -> (argparse dest, flag)
FLAG_SETTINGS = {
    ("catalog", "region"): ("region", "--region"),
    ("catalog", "language"): ("language", "--language"),
    ("paths", "catalog"): ("catalog", "--catalog"),
    ("paths", "cache"): ("cache", "--cache"),
    ("paths", "master"): ("master", "--master"),
    ("paths", "apk"): ("apk", "--apk"),
    ("paths", "ffmpeg"): ("ffmpeg", "--ffmpeg"),
    ("paths", "vgmstream"): ("vgmstream", "--vgmstream"),
    ("paths", "node"): ("node", "--node"),
    ("paths", "player"): ("player", "--player"),
    ("paths", "store"): ("store", "--store"),
}


# ---------------------------------------------------------------- settings -> data
def load_config(args) -> Config:
    overrides = {k: getattr(args, dest, None) for k, (dest, _) in FLAG_SETTINGS.items()}
    return use(Config.load(getattr(args, "config", None), overrides=overrides,
                           flags={k: flag for k, (_, flag) in FLAG_SETTINGS.items()}))


def bundle_key(cfg: Config) -> BundleKey:
    return BundleKey(cfg.hex("bundle", "key", 16), cfg.hex("bundle", "nonce_seed"))


def _existing(cfg: Config, section: str, key: str, kind: str = "file") -> Path | None:
    p = cfg.path(section, key)
    if p is not None and not (p.is_file() if kind == "file" else p.is_dir()):
        raise ConfigError(f"setting {section}.{key}: {kind} {p} not found")
    return p


def open_catalog(cfg: Config, bundles: bool = True, region: str | None = None) -> Catalog:
    """The catalog of [catalog] language (merged with the APK's when [paths] apk is set), fetching from the CDN of
    `region` (default: [catalog] region); the regions serve the same catalog for a language, so one cached file
    serves them all. `bundles`: bundles will be fetched; else only the catalog is read. The region, its CDN base
    and the bundle key are read from the settings only when something must be downloaded (every file in the cache:
    none of them is needed), then a missing one is a ConfigError naming the setting."""
    cache = cfg.require_path("paths", "cache")
    catbin = _existing(cfg, "paths", "catalog")
    apk = _existing(cfg, "paths", "apk")
    language = cfg.require("catalog", "language") if catbin is None else None
    cdn = (lambda: cfg.cdn(region or cfg.region())) if bundles or catbin is None else None
    key = (lambda: bundle_key(cfg)) if bundles else None
    if catbin is not None:
        return Catalog(catbin.read_bytes(), cache, cdn=cdn, bundle_key=key, apk=apk)
    return Catalog.load(language, cache, cdn=cdn, bundle_key=key, apk=apk)


def master_dir(cfg: Config, region: str | None = None) -> Path:
    """The decoded master data of `region` (default: [catalog] region when it is set): the --master flag, else
    [servers.<region>] master, else [paths] master."""
    section, key = cfg.master(region or cfg.get("catalog", "region"))
    cfg.require_path(section, key)
    return _existing(cfg, section, key, "directory")


def player_data(cfg: Config):
    from .player import PlayerData
    cfg.require_path("paths", "apk")
    return PlayerData(_existing(cfg, "paths", "apk"))


def master_key(cfg: Config):
    from .master import MasterKey
    return MasterKey(cfg.hex("master", "key", 32), cfg.hex("master", "iv", 32))


def known_keys(args, cat: Catalog, keys) -> None:
    """A usage error (exit 2) naming the keys the catalog does not have."""
    unknown = [k for k in keys if not cat.has(k)]
    if unknown:
        args.usage(f"not a key of the catalog: {', '.join(unknown)}")


def known_row(args, md: Path, table: str, row_id: int, what: str) -> None:
    """A usage error (exit 2) naming `row_id` when the master table `table` has no row with that `_id`."""
    from .master import has_row
    try:
        found = has_row(md, table, row_id)
    except FileNotFoundError:
        raise ConfigError(f"master data {md}: no {table}.json (a directory written by `nnnotes master decode`)") \
            from None
    if not found:
        args.usage(f"{what} {row_id}: no {table} row with this id")


def _print_json(r) -> None:
    """Print a JSON summary as UTF-8 bytes (a GBK / cp932 console cannot encode every string)."""
    sys.stdout.flush()
    sys.stdout.buffer.write(dumps(r, ensure_ascii=False, indent=1).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


# ---------------------------------------------------------------- commands
def cmd_catalog(args, cfg):
    cat = open_catalog(cfg, bundles=False)
    ks = cat.keys(args.prefix)
    for k in ks[: args.limit]:
        print(k)
    print(f"# {len(ks)} keys", file=sys.stderr)


def cmd_browse(args, cfg):
    from .addressables import Region, serve
    names = cfg.regions()
    if not names:
        raise ConfigError("no region configured: add a [servers.<region>] table with `cdn` to the config file "
                          "(or set NNNOTES_SERVERS_<REGION>_CDN)")
    regions = []
    for r in names:
        langs = cfg.get_list(f"servers.{r}", "languages")
        if not langs:
            raise cfg.missing(f"servers.{r}", "languages")
        regions.append(Region(r, cfg.get(f"servers.{r}", "name") or r, cfg.cdn(r), langs))
    serve(regions, bundle_key(cfg), cfg.require_path("paths", "cache"), args.port, args.host)


def cmd_pull(args, cfg):
    cat = open_catalog(cfg)
    known_keys(args, cat, args.keys)
    for key in args.keys:
        for p in cat.fetch_key(key):
            print(p)


def cmd_master_decode(args, cfg):
    from . import master
    key = master_key(cfg)
    files = master.input_files(args.inputs)
    if not files:
        raise SystemExit("nnnotes: no master data files (*.bin) among the inputs")
    r = master.decode_files(files, Path(args.out), key, workers=args.workers)
    _print_json(r)
    if r["failed"]:
        sys.exit(1)


def cmd_servers(args, cfg):
    from . import gameapi
    try:
        servers = gameapi.server_list(cfg)
    except gameapi.GameApiError as e:
        sys.exit(f"nnnotes: {e}")
    configured = gameapi.configured_roots(cfg)
    _print_json({"servers": [gameapi.server_summary(s, configured, args.show_hosts) for s in servers]})


def cmd_master_version(args, cfg):
    from . import gameapi
    region = cfg.region()
    try:
        v = gameapi.master_version(cfg, region)
    except gameapi.GameApiError as e:
        sys.exit(f"nnnotes: {e}")
    _print_json({"region": region, "masterVersion": v.version, "resourceVersion": v.resource_version})


def cmd_master_download(args, cfg):
    from . import gameapi, master
    region = cfg.region()
    cdn = cfg.cdn(region)
    try:
        version = gameapi.master_version(cfg, region).version if args.latest else args.version
        r = master.download(cdn, version, Path(args.out), workers=args.workers)
    except (gameapi.GameApiError, master.DownloadError) as e:
        sys.exit(f"nnnotes: {e}")
    _print_json(r)
    if r["failed"]:
        sys.exit(1)


def cmd_adv(args, cfg):
    from . import adv
    md = master_dir(cfg)
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    cat = open_catalog(cfg)
    doc = adv.to_json(adv.extract(cat, md, args.adv_id))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, doc)
    print(f"{out}  ({doc['commandCount']} commands, {len(doc['resources'])} resources)")


def cmd_live2d(args, cfg):
    from . import live2d, webmodel
    cfg.require_path("paths", "apk")                 # the Cubism component classes are read from the APK
    cat = open_catalog(cfg)
    try:                                             # a model id or a model key
        (key,) = webmodel.catalog_models(cat, [args.key]).values()
    except ValueError as e:
        args.usage(str(e))
    r = live2d.extract_model(cat, key, Path(args.out))
    print(dumps(r, ensure_ascii=False))


def cmd_spot(args, cfg):
    from . import spot, room, shader
    cfg.require_path("paths", "apk")                 # the Spot / Spine component classes are read from the APK
    md = master_dir(cfg)
    known_row(args, md, "MasterHomeSpot", args.spot_id, "spot")
    cat = open_catalog(cfg)
    out = Path(args.out)
    doc = spot.extract(cat, md, args.spot_id, out)
    print(f"{out}/spot.json  ({len(doc['characters'])} tap targets, {len(doc['skeletons'])} skeletons)")
    bg = doc["master"]["_backgroundAssetPath"]
    r = room.extract_room(cat, bg, out / "room.glb")
    print(f"{out}/room.glb  ({r['meshCount']} meshes, {r['inactiveMeshes']} inactive)")
    bundles = cat.fetch_key(bg) + cat.fetch_key(doc["master"]["_situationAssetPath"])
    s = shader.dump(bundles, out / "shaders")
    print(f"{out}/shaders/  ({s['count']} shaders, {s['variants']} variants)")


def cmd_room(args, cfg):
    from . import room
    cat = open_catalog(cfg)
    known_keys(args, cat, [args.key])
    r = room.extract_room(cat, args.key, Path(args.out))
    print(f"{r['glb']}  ({r['meshCount']} meshes, {r['inactiveMeshes']} inactive)")


def cmd_shader(args, cfg):
    from . import shader
    cat = open_catalog(cfg)
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
            except KeyError as e:                    # none or several bundles match
                args.usage(f"--apk-bundle {e.args[0]}")
    r = shader.dump(bundles, Path(args.out))
    print(f"{r['count']} shaders, {r['variants']} variants")
    for n in r["names"]:
        print(f"  {n}")


def cmd_audio(args, cfg):
    from . import cri
    cfg.require_path("paths", "apk")                 # the HCA keycode is read from the APK
    cat = open_catalog(cfg)
    if not cat.has(f"Cri/Sound/{args.cue_sheet}"):
        args.usage(f"cue sheet {args.cue_sheet}: no key Cri/Sound/{args.cue_sheet} in the catalog")
    out = Path(args.out)
    r = cri.decode(cat, args.cue_sheet, out, fmt=args.format)
    print(f"{out}  ({len(r)} cues)")


def cmd_crikey(args, cfg):
    from . import crikey
    cfg.require_path("paths", "apk")
    k = crikey.find_key(_existing(cfg, "paths", "apk"))
    print(f"HCA keycode found ({len(str(k))} digits)" if k else "no HCA keycode (decryption disabled)")
    if args.write and k:
        d = Path(args.write)
        d.mkdir(parents=True, exist_ok=True)
        print(crikey.write_hcakey(k, d))


def cmd_player(args, cfg):
    g = player_data(cfg).graphics()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, g)
    print(f"{out}  ({g['colorSpace']}, {len(g['qualityLevels'])} quality levels, "
          f"{len(g['renderers'])} renderers)")


def cmd_story(args, cfg):
    from . import languages, story
    if args.fonts == "game":
        from .tmpfont import require_extra
        require_extra()
    languages.check(cfg.require("catalog", "language"))   # the story UI's language (advui reads the setting)
    md = master_dir(cfg)
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    cat = open_catalog(cfg)
    r = story.build(cat, md, player_data(cfg), args.adv_id, Path(args.out),
                    audio_format=args.format, audio=not args.no_audio, fonts=args.fonts)
    _print_json(r)


def _fonts_extra(fonts: str) -> None:
    """`--fonts game` needs the optional `fonts` dependencies."""
    if fonts == "game":
        from .tmpfont import require_extra
        require_extra()


def _live_options(args) -> dict:
    """The --live-option specs as a request (liveoptions.parse_specs); a malformed spec is a usage error."""
    from .liveoptions import OptionSpecError, parse_specs
    try:
        return parse_specs(args.live_option)
    except OptionSpecError as e:
        args.usage(str(e))


def cmd_live(args, cfg):
    from . import languages, live, liveoptions
    from .web import all_pairs
    _fonts_extra(args.fonts)
    request = _live_options(args)
    language = languages.check(cfg.require("catalog", "language"))
    md = master_dir(cfg)
    known_row(args, md, "MasterLiveMusic", args.music_id, "music")
    if (args.music_id, args.difficulty) not in all_pairs(md):
        args.usage(f"music {args.music_id}: no {args.difficulty} chart (no MasterLiveMusicScore row)")
    try:
        options = liveoptions.resolve(request, md)
    except liveoptions.OptionSpecError as e:
        args.usage(str(e))
    cat = open_catalog(cfg)
    try:                                             # an unknown --leader-card, a --band without its scene keys
        live.resolve_band(cat, md, args.music_id, band=args.band, leader_card=args.leader_card)
    except (KeyError, ValueError) as e:
        args.usage(e.args[0] if e.args else type(e).__name__)
    r = live.build(cat, md, player_data(cfg), args.music_id, args.difficulty, Path(args.out),
                   audio_format=args.format, band=args.band, leader_card=args.leader_card, language=language,
                   fonts=args.fonts, options=options)
    _print_json(r)


def cmd_web(args, cfg):
    from . import liveoptions, web, webmodel
    live_options = _live_options(args)
    charts, models = bool(args.pair or args.all), bool(args.live2d or args.all_live2d)
    if not (charts or models or args.player_only or args.reingest_json):
        args.usage("one of the arguments --pair --all --live2d --all-live2d --player-only --reingest-json is required")
    if models and (args.player_only or args.reingest_json):
        args.usage("--player-only and --reingest-json build nothing: leave out --live2d / --all-live2d")
    if live_options and not charts:
        args.usage("--live-option applies to charts: give --pair or --all")
    player = cfg.path("paths", "player")
    out = Path(args.out)
    if args.player_only or args.reingest_json:
        r = {**(web.reingest_json(out) if args.reingest_json else {}),
             **web.write_player(out, web.check_player(player)), **web.write_index(out)}
    else:
        web.check_player(player)
        regions = (web.site_regions(cfg, args.web_regions, args.all_regions)
                   if args.web_regions or args.all_regions else None)   # None: the one [catalog] region
        base = {"region": regions[0]} if regions else {}
        unknown = web.unknown_pairs(cfg, args.pair, regions) if args.pair else []
        if unknown:
            args.usage(f"chart{'s' if len(unknown) > 1 else ''} {', '.join(f'{m}:{d}' for m, d in unknown)}: no "
                       f"MasterLiveMusicScore row in the master data of the site's region(s)")
        if live_options:                             # a value a region's master data does not have
            try:
                for md in web.region_masters(cfg, web.site_regions(cfg, regions)).values():
                    liveoptions.resolve(live_options, md)
            except liveoptions.OptionSpecError as e:
                args.usage(str(e))
        r = {}
        if models:
            cfg.require_path("paths", "apk")         # the Cubism component classes and mask materials: the APK
            try:
                selected = webmodel.catalog_models(open_catalog(cfg, bundles=False, **base), args.live2d)
            except ValueError as e:
                args.usage(str(e))
            r.update(webmodel.build(out, selected, cfg, player, force=args.force, tmp_dir=args.tmp,
                                    workers=args.workers, **base))
        if charts:
            _fonts_extra(args.fonts)
            r.update(web.build(out, None if args.all else args.pair, cfg, player, args.format,
                               audio=not args.no_audio, force=args.force, tmp_dir=args.tmp, workers=args.workers,
                               band=args.band, leader_card=args.leader_card, regions=regions, fonts=args.fonts,
                               read_workers=args.read_workers, live_options=live_options))
    _print_json(r)
    if r.get("failed") or r.get("modelsFailed"):
        sys.exit(1)


# ---------------------------------------------------------------- parser
def parse_pair(s: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d+)[:_](easy|normal|hard|expert)", s)
    if not m:
        raise argparse.ArgumentTypeError(f"{s}: expected <musicId>:<difficulty>")
    return int(m.group(1)), m.group(2)


def _live_option_arg(c) -> None:
    c.add_argument("--live-option", action="append", metavar="OPTION[=VALUES]",
                   help="also export the files of a Live option's variants (repeatable): MirrorChart, "
                        "MeasureLineDisplay, NoteDesignId, NoteEffectId, LiveQuality, NoteSePatternId (every value "
                        "of the master data, or =v,v...), defaults (the option defaults and ranges only) or all")


def _band_args(c) -> None:
    g = c.add_mutually_exclusive_group()
    g.add_argument("--band", type=int, help="band of the LightWeight background and start timeline "
                                            "(default: band of the music's first vocal character)")
    g.add_argument("--leader-card", type=int, help="deck centre MasterMemberCard id (its character's band)")


def _fonts_arg(c) -> None:
    c.add_argument("--fonts", default="open", choices=("open", "game"),
                   help="start canvas text: open: layout and style only, no font data (default); game: also the "
                        "game's TMP fonts (needs the 'fonts' extra)")


def _out(c, what: str, required: bool = True) -> None:
    c.add_argument("-o", "--out", required=required, help=what)


def build_parser() -> argparse.ArgumentParser:
    from .web import WEB_AUDIO, DEFAULT_AUDIO_FORMAT
    p = argparse.ArgumentParser(prog="nnnotes", description="BanG Dream! Our Notes data toolkit")
    p.add_argument("--version", action="version", version=f"nnnotes {__version__}")
    p.add_argument("--config", help="TOML config file (else NNNOTES_CONFIG, else ./nnnotes.toml)")
    p.add_argument("--region", help="region: a [servers.<region>] table ([catalog] region)")
    p.add_argument("--language", help="catalog and client language: ja, en, zh-Hant, zh-Hans or ko "
                                      "([catalog] language)")
    p.add_argument("--catalog", help="catalog .bin file ([paths] catalog; else downloaded into the cache)")
    p.add_argument("--cache", help="cache directory ([paths] cache)")
    p.add_argument("--master", help="decoded master data directory ([paths] master)")
    p.add_argument("--apk", help="base.apk ([paths] apk)")
    p.add_argument("--ffmpeg", help="ffmpeg executable ([paths] ffmpeg; else on PATH)")
    p.add_argument("--vgmstream", help="vgmstream-cli executable ([paths] vgmstream; else on PATH)")
    p.add_argument("--node", help="Node.js executable ([paths] node; else on PATH)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    c = sub.add_parser("catalog", help="list addressable keys")
    c.add_argument("--prefix", default="")
    c.add_argument("--limit", type=int, default=200)
    c.set_defaults(func=cmd_catalog)

    c = sub.add_parser("browse", help="browse the configured regions' catalogs and bundles in a local web page")
    c.add_argument("--port", type=int, default=8000)
    c.add_argument("--host", default="127.0.0.1")
    c.set_defaults(func=cmd_browse)

    c = sub.add_parser("pull", help="fetch the bundle closure of keys into the cache")
    c.add_argument("keys", nargs="+")
    c.set_defaults(func=cmd_pull, usage=c.error)

    c = sub.add_parser("servers", help="the server list of the bootstrap API root: regions, their CDN and API roots")
    c.add_argument("--show-hosts", action="store_true", help="print the CDN and API roots, not only their counts")
    c.set_defaults(func=cmd_servers)

    c = sub.add_parser("master", help="master data files")
    msub = c.add_subparsers(dest="master_cmd", required=True, metavar="<master command>")
    m = msub.add_parser("version", help="the master data and resource version the region serves now (game API)")
    m.set_defaults(func=cmd_master_version)
    m = msub.add_parser("decode", help="master data .bin files -> <Table>.json")
    m.add_argument("inputs", nargs="+", type=Path, help="directories (their *.bin files) or files")
    _out(m, "output directory")
    m.add_argument("--workers", type=int, default=8, help="parallel decodes")
    m.set_defaults(func=cmd_master_decode)
    m = msub.add_parser("download", help="a master data version from the region's CDN (SHA-256 checked)")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--version", help="master data version")
    g.add_argument("--latest", action="store_true", help="the version the region serves now (`master version`)")
    _out(m, "directory for MasterManifest.json and the .bin files")
    m.add_argument("--workers", type=int, default=16, help="parallel downloads")
    m.set_defaults(func=cmd_master_download)

    c = sub.add_parser("adv", help="ADV episode -> JSON")
    c.add_argument("adv_id", type=int)
    _out(c, "output .json file")
    c.set_defaults(func=cmd_adv, usage=c.error)

    c = sub.add_parser("story", help="ADV episode -> story dir (episode, models, audio, scene, UI, media, videos)")
    c.add_argument("adv_id", type=int)
    _out(c, "output directory")
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES)
    c.add_argument("--no-audio", action="store_true", help="do not decode the cue sheets")
    c.add_argument("--fonts", default="open", choices=("open", "game"),
                   help="open: text layout and style only, no font data (default); game: also the game's TMP fonts "
                        "(needs the 'fonts' extra)")
    c.set_defaults(func=cmd_story, usage=c.error)

    c = sub.add_parser("live2d", help="Live2D model -> runtime model dir")
    c.add_argument("key", help="model key Character/Live2D/<group>/<name>/model/<name>, or its model id <name>")
    _out(c, "output directory")
    c.set_defaults(func=cmd_live2d, usage=c.error)

    c = sub.add_parser("spot", help="spot -> spot.json + Spine + room.glb + shaders")
    c.add_argument("spot_id", type=int)
    _out(c, "output directory")
    c.set_defaults(func=cmd_spot, usage=c.error)

    c = sub.add_parser("room", help="background prefab -> glb")
    c.add_argument("key")
    _out(c, "output .glb file")
    c.set_defaults(func=cmd_room, usage=c.error)

    c = sub.add_parser("shader", help="dump shaders of a key's closure or of APK bundles")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--key")
    g.add_argument("--apk-bundle", nargs="+", help="substring(s) of APK bundle file names")
    _out(c, "output directory")
    c.set_defaults(func=cmd_shader, usage=c.error)

    c = sub.add_parser("audio", help="decode a CRI cue sheet")
    c.add_argument("cue_sheet")
    _out(c, "output directory")
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES)
    c.set_defaults(func=cmd_audio, usage=c.error)

    c = sub.add_parser("crikey", help="find the HCA keycode in base.apk")
    c.add_argument("--write", help="directory to write .hcakey into")
    c.set_defaults(func=cmd_crikey)

    c = sub.add_parser("player", help="player graphics settings -> JSON")
    _out(c, "output .json file")
    c.set_defaults(func=cmd_player)

    c = sub.add_parser("live", help="live (music + difficulty) -> self-contained live dir")
    c.add_argument("music_id", type=int)
    c.add_argument("--difficulty", default="expert", choices=DIFFICULTY_CHOICES)
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES)
    _fonts_arg(c)
    _band_args(c)
    _live_option_arg(c)
    _out(c, "output directory")
    c.set_defaults(func=cmd_live, usage=c.error)

    c = sub.add_parser("web", help="live charts and Live2D models -> static site for ournotes-player "
                                   "(shared player + per-chart / per-model data)")
    c.add_argument("out", help="site directory")
    c.add_argument("--player", help="ournotes-player checkout (built) or installed package ([paths] player)")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--pair", type=parse_pair, action="append", help="<musicId>:<difficulty> (repeatable)")
    g.add_argument("--all", action="store_true", help="every MasterLiveMusic x difficulty")
    g.add_argument("--player-only", action="store_true",
                   help="rewrite the player files, charts.json and models.json only")
    g.add_argument("--reingest-json", action="store_true", help="store every chart's and model's JSON files again")
    m = c.add_mutually_exclusive_group()
    m.add_argument("--live2d", action="append", metavar="MODEL",
                   help="Live2D model id or key Character/Live2D/<group>/<name>/model/<name> (repeatable)")
    m.add_argument("--all-live2d", action="store_true", help="every Live2D model of the catalog")
    c.add_argument("--format", default=DEFAULT_AUDIO_FORMAT, choices=tuple(WEB_AUDIO), help="BGM format")
    c.add_argument("--no-audio", action="store_true", help="export no audio files")
    c.add_argument("--force", action="store_true", help="rebuild charts and models whose manifest exists")
    c.add_argument("--tmp", help="directory for the temporary live and model builds (default <site>.tmp)")
    c.add_argument("--workers", type=int,
                   help="parallel music / model processes (default a quarter of the CPUs, up to 8 / up to 4; "
                        "1 = this process)")
    c.add_argument("--read-workers", type=int,
                   help="chart read sets run at a time (default half the CPUs, up to 16)")
    r = c.add_mutually_exclusive_group()
    r.add_argument("--region", dest="web_regions", action="append", metavar="REGION",
                   help="a region the site serves: a [servers.<region>] table (repeatable; the first is the base; "
                        "default: [catalog] region)")
    r.add_argument("--all-regions", action="store_true", help="every configured region")
    _fonts_arg(c)
    _band_args(c)
    _live_option_arg(c)
    c.set_defaults(func=cmd_web, usage=c.error)

    cli_assets.register(sub, argparse.Namespace(open_catalog=open_catalog, print_json=_print_json))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args)
        args.func(args, cfg)
    except ConfigError as e:
        print(f"nnnotes: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
