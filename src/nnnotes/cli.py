"""nnnotes command line.

Every setting (keys, the CDN base of each region, the paths of your own data and tools) comes from the config file,
the environment or the flags below; see config.py and nnnotes.example.toml. JSON summaries are printed as UTF-8
whatever the console encoding.

    nnnotes catalog --prefix Spot/ --limit 40
    nnnotes browse [--port 8000]
    nnnotes pull <key> [<key> ...]
    nnnotes master decode <dir or .bin files> -o <out dir>
    nnnotes master download --version <master version> -o <dir>
    nnnotes adv 10462 -o out/adv_10462.json
    nnnotes story 10462 -o out/story_10462
    nnnotes live2d <Character/Live2D/.../model/...> -o out/live2d/x
    nnnotes spot 10001 -o out/spot_10001
    nnnotes room <Spot/.../Background/...> -o out/room.glb
    nnnotes shader --key <key> | --apk-bundle <substring> -o out/shaders
    nnnotes audio <cueSheet> -o out/audio
    nnnotes crikey [--write <dir>]
    nnnotes player -o out/player.json
    nnnotes live 100001 --difficulty expert [--band 1 | --leader-card <MasterMemberCard id>] -o out/live_100001
    nnnotes web out/site --player <ournotes-player> --pair 100001:expert [--pair ...] | --all [--format aac]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
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
    ("paths", "dummy_dll"): ("dummy_dll", "--dummy-dll"),
    ("paths", "ffmpeg"): ("ffmpeg", "--ffmpeg"),
    ("paths", "vgmstream"): ("vgmstream", "--vgmstream"),
    ("paths", "node"): ("node", "--node"),
    ("paths", "player"): ("player", "--player"),
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


def open_catalog(cfg: Config, bundles: bool = True) -> Catalog:
    """The region's catalog (merged with the APK's when [paths] apk is set). `bundles`: bundles will be fetched,
    so the CDN base and the bundle key are required; else only what reading the catalog needs."""
    cache = cfg.require_path("paths", "cache")
    catbin = _existing(cfg, "paths", "catalog")
    apk = _existing(cfg, "paths", "apk")
    language = cfg.require("catalog", "language") if catbin is None else None
    download = catbin is None and not Catalog.cache_file(language, cache).is_file()
    cdn = cfg.cdn(cfg.region()) if bundles or download else None
    key = bundle_key(cfg) if bundles else None
    if catbin is not None:
        return Catalog(catbin.read_bytes(), cache, cdn=cdn, bundle_key=key, apk=apk)
    return Catalog.load(language, cache, cdn=cdn, bundle_key=key, apk=apk)


def master_dir(cfg: Config) -> Path:
    cfg.require_path("paths", "master")
    return _existing(cfg, "paths", "master", "directory")


def player_data(cfg: Config):
    from .player import PlayerData
    cfg.require_path("paths", "apk")
    cfg.require_path("paths", "dummy_dll")
    return PlayerData(_existing(cfg, "paths", "apk"), _existing(cfg, "paths", "dummy_dll", "directory"))


def master_key(cfg: Config):
    from .master import MasterKey
    return MasterKey(cfg.hex("master", "key", 32), cfg.hex("master", "iv", 32))


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


def cmd_master_download(args, cfg):
    from . import master
    try:
        r = master.download(cfg.cdn(cfg.region()), args.version, Path(args.out), workers=args.workers)
    except master.DownloadError as e:
        sys.exit(f"nnnotes: {e}")
    _print_json(r)
    if r["failed"]:
        sys.exit(1)


def cmd_adv(args, cfg):
    from . import adv
    cat = open_catalog(cfg)
    doc = adv.to_json(adv.extract(cat, master_dir(cfg), args.adv_id))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, doc)
    print(f"{out}  ({doc['commandCount']} commands, {len(doc['resources'])} resources)")


def cmd_live2d(args, cfg):
    from . import live2d
    cfg.require_path("paths", "apk")                 # the Cubism component classes are read from the APK
    cat = open_catalog(cfg)
    r = live2d.extract_model(cat, args.key, Path(args.out))
    print(dumps(r, ensure_ascii=False))


def cmd_spot(args, cfg):
    from . import spot, room, shader
    cfg.require_path("paths", "apk")                 # the Spot / Spine component classes are read from the APK
    cat = open_catalog(cfg)
    out = Path(args.out)
    doc = spot.extract(cat, master_dir(cfg), args.spot_id, out)
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
    r = room.extract_room(cat, args.key, Path(args.out))
    print(f"{r['glb']}  ({r['meshCount']} meshes, {r['inactiveMeshes']} inactive)")


def cmd_shader(args, cfg):
    from . import shader
    cat = open_catalog(cfg)
    if args.key:
        bundles = cat.fetch_key(args.key)
    else:
        if cat.apk is None:
            cfg.require_path("paths", "apk")
        bundles = [cat.apk_bundle(s) for s in args.apk_bundle]
    r = shader.dump(bundles, Path(args.out))
    print(f"{r['count']} shaders, {r['variants']} variants")
    for n in r["names"]:
        print(f"  {n}")


def cmd_audio(args, cfg):
    from . import cri
    cfg.require_path("paths", "apk")                 # the HCA keycode is read from the APK
    cat = open_catalog(cfg)
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
    from . import story
    cat = open_catalog(cfg)
    r = story.build(cat, master_dir(cfg), player_data(cfg), args.adv_id, Path(args.out),
                    audio_format=args.format)
    _print_json(r)


def cmd_live(args, cfg):
    from . import live
    cat = open_catalog(cfg)
    r = live.build(cat, master_dir(cfg), player_data(cfg), args.music_id, args.difficulty, Path(args.out),
                   audio_format=args.format, band=args.band, leader_card=args.leader_card)
    _print_json(r)


def cmd_web(args, cfg):
    from . import web
    player = cfg.path("paths", "player")
    out = Path(args.out)
    if args.player_only or args.reingest_json:
        r = {**(web.reingest_json(out) if args.reingest_json else {}),
             **web.write_player(out, web.check_player(player)), **web.write_index(out)}
    else:
        web.check_player(player)
        pairs = web.all_pairs(master_dir(cfg)) if args.all else args.pair
        r = web.build(out, pairs, cfg, player, args.format, audio=not args.no_audio, force=args.force,
                       tmp_dir=args.tmp, workers=args.workers, band=args.band, leader_card=args.leader_card)
    _print_json(r)
    if r.get("failed"):
        sys.exit(1)


# ---------------------------------------------------------------- parser
def parse_pair(s: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d+)[:_](easy|normal|hard|expert)", s)
    if not m:
        raise argparse.ArgumentTypeError(f"{s}: expected <musicId>:<difficulty>")
    return int(m.group(1)), m.group(2)


def _band_args(c) -> None:
    g = c.add_mutually_exclusive_group()
    g.add_argument("--band", type=int, help="band of the LightWeight background and start timeline "
                                            "(default: band of the music's first vocal character)")
    g.add_argument("--leader-card", type=int, help="deck centre MasterMemberCard id (its character's band)")


def _out(c, what: str, required: bool = True) -> None:
    c.add_argument("-o", "--out", required=required, help=what)


def build_parser() -> argparse.ArgumentParser:
    from .web import WEB_AUDIO, DEFAULT_AUDIO_FORMAT
    p = argparse.ArgumentParser(prog="nnnotes", description="BanG Dream! Our Notes data toolkit")
    p.add_argument("--version", action="version", version=f"nnnotes {__version__}")
    p.add_argument("--config", help="TOML config file (else NNNOTES_CONFIG, else ./nnnotes.toml)")
    p.add_argument("--region", help="region: a [servers.<region>] table ([catalog] region)")
    p.add_argument("--language", help="catalog language, e.g. zh-Hant ([catalog] language)")
    p.add_argument("--catalog", help="catalog .bin file ([paths] catalog; else downloaded into the cache)")
    p.add_argument("--cache", help="cache directory ([paths] cache)")
    p.add_argument("--master", help="decoded master data directory ([paths] master)")
    p.add_argument("--apk", help="base.apk ([paths] apk)")
    p.add_argument("--dummy-dll", help="Il2CppDumper DummyDll directory of the same APK ([paths] dummy_dll)")
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
    c.set_defaults(func=cmd_pull)

    c = sub.add_parser("master", help="master data files")
    msub = c.add_subparsers(dest="master_cmd", required=True, metavar="<master command>")
    m = msub.add_parser("decode", help="master data .bin files -> <Table>.json")
    m.add_argument("inputs", nargs="+", type=Path, help="directories (their *.bin files) or files")
    _out(m, "output directory")
    m.add_argument("--workers", type=int, default=8, help="parallel decodes")
    m.set_defaults(func=cmd_master_decode)
    m = msub.add_parser("download", help="a master data version from the region's CDN (SHA-256 checked)")
    m.add_argument("--version", required=True, help="master data version")
    _out(m, "directory for MasterManifest.json and the .bin files")
    m.add_argument("--workers", type=int, default=16, help="parallel downloads")
    m.set_defaults(func=cmd_master_download)

    c = sub.add_parser("adv", help="ADV episode -> JSON")
    c.add_argument("adv_id", type=int)
    _out(c, "output .json file")
    c.set_defaults(func=cmd_adv)

    c = sub.add_parser("story", help="ADV episode -> story dir (episode, models, audio, scene, UI)")
    c.add_argument("adv_id", type=int)
    _out(c, "output directory")
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES)
    c.set_defaults(func=cmd_story)

    c = sub.add_parser("live2d", help="Live2D model -> runtime model dir")
    c.add_argument("key")
    _out(c, "output directory")
    c.set_defaults(func=cmd_live2d)

    c = sub.add_parser("spot", help="spot -> spot.json + Spine + room.glb + shaders")
    c.add_argument("spot_id", type=int)
    _out(c, "output directory")
    c.set_defaults(func=cmd_spot)

    c = sub.add_parser("room", help="background prefab -> glb")
    c.add_argument("key")
    _out(c, "output .glb file")
    c.set_defaults(func=cmd_room)

    c = sub.add_parser("shader", help="dump shaders of a key's closure or of APK bundles")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--key")
    g.add_argument("--apk-bundle", nargs="+", help="substring(s) of APK bundle file names")
    _out(c, "output directory")
    c.set_defaults(func=cmd_shader)

    c = sub.add_parser("audio", help="decode a CRI cue sheet")
    c.add_argument("cue_sheet")
    _out(c, "output directory")
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES)
    c.set_defaults(func=cmd_audio)

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
    _band_args(c)
    _out(c, "output directory")
    c.set_defaults(func=cmd_live)

    c = sub.add_parser("web", help="live charts -> static site for ournotes-player (shared player + per-chart data)")
    c.add_argument("out", help="site directory")
    c.add_argument("--player", help="ournotes-player checkout (built) or installed package ([paths] player)")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--pair", type=parse_pair, action="append", help="<musicId>:<difficulty> (repeatable)")
    g.add_argument("--all", action="store_true", help="every MasterLiveMusic x difficulty")
    g.add_argument("--player-only", action="store_true", help="rewrite the player files and charts.json only")
    g.add_argument("--reingest-json", action="store_true", help="store every chart's JSON files again")
    c.add_argument("--format", default=DEFAULT_AUDIO_FORMAT, choices=tuple(WEB_AUDIO), help="BGM format")
    c.add_argument("--no-audio", action="store_true", help="export no audio files")
    c.add_argument("--force", action="store_true", help="rebuild charts whose manifest exists")
    c.add_argument("--tmp", help="directory for the temporary live builds (default <site>.tmp)")
    c.add_argument("--workers", type=int, help="parallel music processes (default up to 5, 1 = this process)")
    _band_args(c)
    c.set_defaults(func=cmd_web)
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
