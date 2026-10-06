"""nnnotes command line.

Every setting (keys, the CDN base of each region, the paths of your own data and tools) comes from the config file,
the environment or the flags below; see config.py and nnnotes.example.toml (`nnnotes config init` writes it). JSON
summaries are printed as UTF-8 whatever the console encoding.

    nnnotes config init [--user | --file F] [--set SECTION.KEY=VALUE ...]
    nnnotes config set SECTION.KEY VALUE
    nnnotes config check [--json]
    nnnotes config path [--json]
    nnnotes catalog --prefix Spot/ --limit 40
    nnnotes browse [--port 8000]
    nnnotes pull <key> [<key> ...]
    nnnotes servers [--show-hosts]
    nnnotes master version
    nnnotes master decode <dir or .bin files> -o <out dir>
    nnnotes master download --version <master version> | --latest -o <dir>
    nnnotes adv 10462 -o out/adv_10462.json
    nnnotes story 10462 -o out/story_10462 [--models out/live2d] [--force]
    nnnotes live2d <Character/Live2D/.../model/... | model id> -o out/live2d/x
    nnnotes spot 10001 -o out/spot_10001
    nnnotes room <Spot/.../Background/...> -o out/room.glb
    nnnotes shader --key <key> | --apk-bundle <substring> -o out/shaders
    nnnotes audio <cueSheet> -o out/audio
    nnnotes voices list [--from out/assets] [--character <id | name>] [--category <category>] [--source <source>]
                        [--episode <adv id | asset>]
    nnnotes voices search <text> [--from out/assets] [--language ja] [--character ...] [--category ...]
    nnnotes voices summary [--from out/assets]
    nnnotes voices get <row id | sound id> -o out/voice [--from out/assets] [--format flac]
    nnnotes crikey [--write <dir>]
    nnnotes player -o out/player.json
    nnnotes live 100001 --difficulty expert [--band 1 | --leader-card <MasterMemberCard id>] [--fonts game]
                 -o out/live_100001
    nnnotes web out/site --player <ournotes-player> [--pair 100001:expert [--pair ...] | --all] [--format aac]
                         [--live2d <model id | key> [--live2d ...] | --all-live2d]
                         [--story 10462 [--story ...] | --all-stories] [--story-languages en,ja] [--font en=<file>]
                         [--font emoji=<file>]
                         [--region <region> [--region ...] | --all-regions]
    nnnotes music-data --master-files <master download dir> | --apk-master | --decoded-master [--full] [--no-deck]
                       [--no-gekisou-aptitude] [--stats-cache DIR] [--no-bgm] [--jackets DIR]
                       -o out/music-data.json[.gz]
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

from . import __version__, cli_assets, voices
from .addressables import BundleKey
from .catalog import Catalog
from .compress import DEFAULT_ENCODING, ENCODINGS
from .config import DEFAULT_FILE, ENV_CONFIG, Config, ConfigError, config_files, find_file, use, user_file
from .gameapi import GameApiError
from .jsonio import dumps, write_json
from .webaudio import DEFAULT_AUDIO_FORMAT, WEB_AUDIO

DIFFICULTY_CHOICES = ("easy", "normal", "hard", "expert")
AUDIO_CHOICES = ("flac", "ogg", "wav")
# command-line flags of settings: (section, key) -> (argparse dest, flag)
FLAG_SETTINGS = {
    ("catalog", "region"): ("region", "--region"),
    ("catalog", "language"): ("language", "--language"),
    ("catalog", "version"): ("catalog_release", "--catalog-release"),
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
    cfg = Config.load(getattr(args, "config", None), overrides=overrides,
                      flags={k: flag for k, (_, flag) in FLAG_SETTINGS.items()})
    if getattr(args, "func", None) is cmd_config_check:
        return use(cfg)                         # diagnose raw settings before applying runtime defaults
    if cfg.provider() == "jp" and not cfg.has("catalog", "language"):
        cfg = cfg.for_region(cfg.region())
    return use(cfg)


def bundle_key(cfg: Config) -> BundleKey:
    return BundleKey(cfg.hex("bundle", "key", 16), cfg.hex("bundle", "nonce_seed"))


def _existing(cfg: Config, section: str, key: str, kind: str = "file") -> Path | None:
    p = cfg.path(section, key)
    if key == "apk" and p is not None and p.is_dir():
        return p
    if p is not None and not (p.is_file() if kind == "file" else p.is_dir()):
        raise ConfigError(f"setting {section}.{key}: {kind} {p} not found")
    return p


def open_catalog(cfg: Config, bundles: bool = True, region: str | None = None) -> Catalog:
    """The catalog of [catalog] language (merged with the APK's when [paths] apk is set), fetching from the CDN of
    `region` (default: [catalog] region). International versioned catalogs are isolated by CDN root and resource
    version. `bundles`: bundles will be fetched; else only the catalog is read. Explicit files bypass discovery.
    Version pins bypass the API but need the CDN root to identify the cache; legacy main keeps lazy CDN lookup.
    The bundle key is only read for a missing encrypted bundle."""
    if region:
        cfg = cfg.for_region(region)
    cache = cfg.require_path("paths", "cache")
    catbin = _existing(cfg, "paths", "catalog")
    apk = _existing(cfg, "paths", "apk")
    if cfg.provider() == "jp":
        from .jp import open_catalog as jp_catalog
        return jp_catalog(cfg, cfg.region(), catalog_file=catbin,
                          bundle_key=(lambda: bundle_key(cfg)) if bundles else None, apk=apk)
    language = cfg.require("catalog", "language") if catbin is None else None
    cdn = (lambda: cfg.cdn(region or cfg.region())) if bundles or catbin is None else None
    key = (lambda: bundle_key(cfg)) if bundles else None
    if catbin is not None:
        return Catalog(catbin.read_bytes(), cache, cdn=cdn, bundle_key=key, apk=apk)
    return Catalog.load(language, cache, cdn=cdn, bundle_key=key, apk=apk, version=cfg.catalog_version())


def master_dir(cfg: Config, region: str | None = None) -> Path:
    """The decoded master data of `region` (default: [catalog] region when it is set): the --master flag, else
    [servers.<region>] master, else [paths] master."""
    section, key = cfg.master(region or cfg.get("catalog", "region"))
    cfg.require_path(section, key)
    return _existing(cfg, section, key, "directory")


def player_data(cfg: Config, region: str | None = None):
    from .player import PlayerData
    if region:
        cfg = cfg.for_region(region)
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
        regions.append(Region(r, cfg.get(f"servers.{r}", "name") or r, cfg.cdn(r), langs, cfg))
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


def _tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _config_target(args, default: Path | None) -> Path:
    """The file a config command writes: --file, --user, else `default` (None: the file the commands read)."""
    if args.file:
        return Path(args.file)
    if args.user:
        target = user_file()
        if target is None:
            raise ConfigError("no per-user config directory: the environment sets neither APPDATA (Windows) nor "
                              "XDG_CONFIG_HOME / HOME")
        return target
    if default is not None:
        return default
    target = find_file(args.config)
    if target is None:
        raise ConfigError("no config file is read (`nnnotes config path`): name one with --file or --user, or "
                          "write one with `nnnotes config init`")
    return target


def _config_value(args, name: str, text: str, stdin_used: list):
    """(section, key, value) of `name` = `text` (`-`: read from standard input, once), checked."""
    from . import configfile
    setting, section = configfile.resolve(name)
    if text == "-":
        if stdin_used:
            args.usage("only one value can be read from standard input")
        stdin_used.append(name)
        if setting.secret and _tty():
            import getpass
            text = getpass.getpass(f"[{section}] {setting.key} (hidden): ")
        else:
            text = sys.stdin.readline().rstrip("\r\n")
    try:
        return section, setting.key, configfile.parse_value(setting, text)
    except ValueError as e:
        raise ConfigError(f"setting {section}.{setting.key}: {e}") from None


def _config_note(target: Path) -> None:
    read = find_file()
    if read is None or read.absolute() != target.absolute():
        print(f"# the commands read {read.absolute() if read else 'no config file'} first (`nnnotes config path`)",
              file=sys.stderr)


def cmd_config_init(args, cfg):
    from . import configfile
    named = next((f for n, f in config_files(args.config)[:2] if f is not None), None)
    target = _config_target(args, named or Path(DEFAULT_FILE))
    if target.exists() and not args.force:
        raise ConfigError(f"config file {target} exists (--force overwrites it; `nnnotes config set` edits one "
                          f"value)")
    values, regions, stdin_used = [], [], []
    for item in args.set or []:
        name, sep, text = item.partition("=")
        if not sep:
            args.usage(f"--set {name}: give it as SECTION.KEY=VALUE")
        section, key, value = _config_value(args, name.strip(), text, stdin_used)
        values.append((section, key, value))
        region = section.split(".", 1)[1] if section.startswith("servers.") else None
        if region and region not in regions:
            regions.append(region)
    if not args.set and not args.no_input and _tty():
        try:
            values, regions = configfile.prompt()
        except (KeyboardInterrupt, EOFError):
            print("\nnnnotes: config init stopped, nothing written", file=sys.stderr)
            sys.exit(1)
    configfile.write(target, configfile.render(values, regions))
    print(target.absolute())
    print(f"# {len(values)} settings written, the others empty; `nnnotes config check` shows the status of each",
          file=sys.stderr)
    _config_note(target)


def _config_edit(args, value_text: str | None) -> None:
    from . import configfile
    target = _config_target(args, None)
    text = target.read_text(encoding="utf-8") if target.exists() else configfile.template().decode("utf-8")
    if value_text is None:                      # unset: an empty value
        setting, section = configfile.resolve(args.name)
        section, key, value = section, setting.key, [] if setting.kind == "list" else ""
    else:
        section, key, value = _config_value(args, args.name, value_text, [])
    configfile.write(target, configfile.edit(text, [(section, key, value)], str(target)))
    print(f"{target.absolute()}: {section}.{key} {'set' if value else 'emptied'}")
    _config_note(target)


def cmd_config_set(args, cfg):
    _config_edit(args, args.value)


def cmd_config_unset(args, cfg):
    _config_edit(args, None)


def cmd_config_check(args, cfg):
    from . import configfile
    r = configfile.check(cfg)
    if args.json:
        _print_json(r)
    else:
        print(f"# file: {r['file']}" if r["file"] else "# no config file is read (`nnnotes config path`)")
        for s in r["settings"]:
            status = s["status"] + (f": {s['reason']}" if s.get("reason") else "")
            print(f"{s['name']}\t{s.get('origin') or '-'}\t{status}")
        sys.stdout.flush()
        print(f"# {r['problems']} problems", file=sys.stderr)
    if r["problems"]:
        sys.exit(1)


def cmd_config_path(args, cfg):
    from . import configfile
    files, read = [], None
    for name, file in config_files(args.config):
        if file is None:
            state = "not given"
        elif not file.is_file():
            state = "not found"
        elif read is None:
            read, state = file, "read"
        else:
            state = "found, not read"
        files.append({"name": name, "path": str(file.absolute()) if file is not None else None, "state": state})
        if read is None and file is not None and name in ("--config", ENV_CONFIG):
            break                               # a named file that is missing stops every command
    if args.json:
        _print_json({"schema": configfile.PATHS, "files": files, "read": str(read.absolute()) if read else None})
        return
    for f in files:
        print(f"{f['name']}\t{f['path'] or '-'}\t{f['state']}")
    if read is None:
        print("# no config file is read: the settings come from the environment and the flags only", file=sys.stderr)


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
    result = {"region": region, "masterVersion": v.version, "resourceVersion": v.resource_version}
    if v.resource_hash is not None:
        result["resourceHash"] = v.resource_hash
    _print_json(result)


def cmd_master_download(args, cfg):
    from . import gameapi, master
    region = cfg.region()
    cdn = cfg.cdn(region)
    try:
        if cfg.provider(region) == "jp":
            from .jp import Session, master_version
            session = Session(cfg, region)
            observation = session.observe()
            version = observation.version.version if args.latest else master_version(args.version)
            r = master.download(observation.cdn, version, Path(args.out), workers=args.workers,
                                get=lambda url: session.get(url, master=version), strict=True)
        else:
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
    flac = _flac_options(args)
    cfg.require_path("paths", "apk")                 # the HCA keycode is read from the APK
    cat = open_catalog(cfg)
    if not cat.has(f"Cri/Sound/{args.cue_sheet}"):
        args.usage(f"cue sheet {args.cue_sheet}: no key Cri/Sound/{args.cue_sheet} in the catalog")
    out = Path(args.out)
    r = cri.decode(cat, args.cue_sheet, out, fmt=args.format, **flac)
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
    from . import languages, story, webmodel
    if args.fonts == "game":
        from .tmpfont import require_extra
        require_extra("--fonts game")
    flac = _flac_options(args)
    out = Path(os.path.abspath(args.out))
    models = Path(os.path.abspath(args.models or Path(args.out) / os.pardir / "live2d"))
    try:
        os.path.relpath(models, out)
    except ValueError:
        args.usage(f"--models {models}: not on the drive of {out} (story.json names it relative to the story)")
    languages.check(cfg.require("catalog", "language"))   # the story UI's language (advui reads the setting)
    md = master_dir(cfg)
    known_row(args, md, "MasterAdv", args.adv_id, "episode")
    cat = open_catalog(cfg)
    player = player_data(cfg)
    source = webmodel.ModelDir(cat, player, models, force=args.force)
    r = story.build(cat, md, player, args.adv_id, out, audio_format=args.format, audio=not args.no_audio,
                    fonts=args.fonts, models=source, audio_options=flac)
    _print_json({**r, "modelsBuilt": source.built, "modelsSkipped": source.skipped})


def _flac_options(args) -> dict:
    """The cri.decode options of --flac-level ({} without it); a level out of range, or the flag where no FLAC is
    written, is a usage error."""
    level = args.flac_level
    if level is None:
        return {}
    if not 0 <= level <= 12:
        args.usage(f"--flac-level {level}: expected 0 to 12")
    if args.format != "flac":
        args.usage(f"--flac-level: --format {args.format} writes no FLAC")
    if getattr(args, "no_audio", False):
        args.usage("--flac-level: --no-audio decodes no audio")
    return {"flac_level": level}


def _fonts_extra(fonts: str) -> None:
    """`--fonts game` needs the optional `fonts` dependencies."""
    if fonts == "game":
        from .tmpfont import require_extra
        require_extra("--fonts game")


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
    flac = _flac_options(args)
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
                   fonts=args.fonts, options=options, **flac)
    _print_json(r)


def cmd_web(args, cfg):
    from . import liveoptions, storysite, web, webmodel
    live_options = _live_options(args)
    charts, models = bool(args.pair or args.all), bool(args.live2d or args.all_live2d)
    stories = bool(args.story or args.all_stories)
    if not (charts or models or stories or args.player_only or args.reingest_json):
        args.usage("one of the arguments --pair --all --live2d --all-live2d --story --all-stories --player-only "
                   "--reingest-json is required")
    if models and (args.player_only or args.reingest_json):
        args.usage("--player-only and --reingest-json build nothing: leave out --live2d / --all-live2d")
    if stories and (args.player_only or args.reingest_json):
        args.usage("--player-only and --reingest-json build nothing: leave out --story / --all-stories")
    if live_options and not charts:
        args.usage("--live-option applies to charts: give --pair or --all")
    player = cfg.path("paths", "player")
    out = Path(args.out)
    if args.player_only or args.reingest_json:
        r = {**(web.reingest_json(out, args.compress) if args.reingest_json else {}),
             **web.write_player(out, web.check_player(player)), **web.write_index(out)}
    else:
        web.check_player(player)
        regions = (web.site_regions(cfg, args.web_regions, args.all_regions)
                   if args.web_regions or args.all_regions else None)   # None: the one [catalog] region
        if (stories or models) and regions and len({cfg.provider(r) for r in regions}) > 1:
            raise ConfigError("build JP stories/models in a separate site directory from international releases")
        if (stories or models) and regions:
            cfg = use(cfg.for_region(regions[0]))
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
        encoding = args.compress
        if models:
            cfg.require_path("paths", "apk")         # the Cubism component classes and mask materials: the APK
            try:
                selected = webmodel.catalog_models(open_catalog(cfg, bundles=False, **base), args.live2d)
            except ValueError as e:
                args.usage(str(e))
            r.update(webmodel.build(out, selected, cfg, player, force=args.force, tmp_dir=args.tmp,
                                    workers=args.workers, encoding=encoding, **base))
        if charts:
            _fonts_extra(args.fonts)
            r.update(web.build(out, None if args.all else args.pair, cfg, player, args.format,
                               audio=not args.no_audio, force=args.force, tmp_dir=args.tmp, workers=args.workers,
                               band=args.band, leader_card=args.leader_card, regions=regions, fonts=args.fonts,
                               read_workers=args.read_workers, live_options=live_options,
                               encoding=args.compress))
        if stories:
            from .tmpfont import require_extra
            require_extra("--story / --all-stories")  # the font assets of the story text (open and game)
            cfg.require_path("paths", "apk")         # the ADV settings and UI are embedded content of the APK
            unknown = storysite.unknown_stories(cfg, args.story, regions) if args.story else []
            if unknown:
                noun = "stories" if len(unknown) > 1 else "story"
                args.usage(f"{noun} {', '.join(map(str, unknown))}: no MasterAdv row in the master data of the "
                           f"site's region(s)")
            r.update(storysite.build(out, args.story, cfg, player, args.format, audio=not args.no_audio,
                                     force=args.force, tmp_dir=args.tmp, workers=args.workers, regions=regions,
                                     story_languages=args.story_languages, fonts=args.fonts,
                                     fonts_flags=dict(args.font or []), encoding=encoding))
    _print_json(r)
    if (r.get("failed") or r.get("modelsFailed") or r.get("storiesFailed")
            or (r.get("storyModels") or {}).get("modelsFailed")):
        sys.exit(1)


def cmd_music_data(args, cfg):
    from . import deckdata, musicdata
    if args.apk_master:
        cfg.require_path("paths", "apk")             # the master data files ship in the APK
    apk = _existing(cfg, "paths", "apk")
    try:
        deck = None if args.no_deck else musicdata.Deck(
            seeds=args.seeds, workers=args.workers, aptitude=not args.no_gekisou_aptitude,
            aptitude_max_seeds=args.aptitude_max_seeds, aptitude_cross_seeds=args.aptitude_cross_seeds,
            require_convergence=not args.allow_unconverged_aptitude, cache=args.stats_cache)
        if args.apk_master:
            src, region = deckdata.apk_master(apk), deckdata.EMBEDDED
        elif args.decoded_master:                    # decoded elsewhere: no master key
            src, region = deckdata.decoded_master(master_dir(cfg)), cfg.region()
        else:
            src, region = deckdata.master_files(Path(args.master_files)), cfg.region()
        key = None if src.decoded else master_key(cfg)
        cat = open_catalog(cfg)
        r = musicdata.export(Path(args.out), src, key, deckdata.catalog_fetch(cat),
                             None if args.no_bgm else musicdata.catalog_bgm(cat), region=region,
                             client=deckdata.apk_client(apk) if apk is not None else {},
                             catalog=deckdata.catalog_info(cat, cli_assets.store_root(args, cfg)),
                             deck=deck, full=args.full,
                             jacket=musicdata.catalog_jacket(cat) if args.jackets else None,
                             jackets_dir=args.jackets, replay_dir=args.replay_dir, replay_engine=args.replay_engine,
                             recommend_engine=args.recommend_engine)
    except (deckdata.DeckDataError, musicdata.MusicDataError) as e:
        sys.exit(f"nnnotes: {e}")
    _print_json(r)


# ---------------------------------------------------------------- parser
def parse_pair(s: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d+)[:_](easy|normal|hard|expert)", s)
    if not m:
        raise argparse.ArgumentTypeError(f"{s}: expected <musicId>:<difficulty>")
    return int(m.group(1)), m.group(2)


def parse_languages(s: str) -> list[str]:
    from .languages import LANGUAGES
    codes = [c.strip() for c in s.split(",") if c.strip()]
    if not codes or any(c not in LANGUAGES for c in codes):
        raise argparse.ArgumentTypeError(f"{s}: expected languages from {', '.join(LANGUAGES)}, comma-separated")
    return codes


EMOJI_FONT = "emoji"                        # --font emoji=<file>, as storysite.EMOJI_FONT


def parse_font(s: str) -> tuple[str, str]:
    from .languages import LANGUAGES
    lang, sep, path = s.partition("=")
    if not sep or (lang not in LANGUAGES and lang != EMOJI_FONT) or not path:
        raise argparse.ArgumentTypeError(f"{s}: expected <language>=<font file> or {EMOJI_FONT}=<emoji font file>, "
                                         f"language one of {', '.join(LANGUAGES)}")
    return lang, path


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


def _audio_args(c) -> None:
    """--format and --flac-level of a command that decodes cue sheets."""
    c.add_argument("--format", default="flac", choices=AUDIO_CHOICES, help="audio file format (default flac)")
    c.add_argument("--flac-level", type=int, metavar="N",
                   help="FLAC compression level 0-12 of --format flac (default 8; every level decodes to the same "
                        "samples)")


def _out(c, what: str, required: bool = True) -> None:
    c.add_argument("-o", "--out", required=required, help=what)


def cmd_ui(args, cfg):
    from . import ui
    ui.command(args, cfg)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nnnotes", description="BanG Dream! Our Notes data toolkit")
    p.add_argument("--version", action="version", version=f"nnnotes {__version__}")
    p.add_argument("--config", help="TOML config file (else NNNOTES_CONFIG, else ./nnnotes.toml)")
    p.add_argument("--region", help="region: a [servers.<region>] table ([catalog] region)")
    p.add_argument("--language", help="catalog and client language: ja, en, zh-Hant, zh-Hans or ko "
                                      "([catalog] language)")
    p.add_argument("--catalog", help="catalog .bin file ([paths] catalog; else downloaded into the cache)")
    p.add_argument("--catalog-release", help="pin an international resource version ([catalog] version; else API discovery when configured, otherwise main)")
    p.add_argument("--cache", help="cache directory ([paths] cache)")
    p.add_argument("--master", help="decoded master data directory ([paths] master)")
    p.add_argument("--apk", help="base.apk ([paths] apk)")
    p.add_argument("--ffmpeg", help="ffmpeg executable ([paths] ffmpeg; else on PATH)")
    p.add_argument("--vgmstream", help="vgmstream-cli executable ([paths] vgmstream; else on PATH)")
    p.add_argument("--node", help="Node.js executable ([paths] node; else on PATH)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    c = sub.add_parser("config", help="the config file: write it, set one value, check the settings, show where it is")
    csub = c.add_subparsers(dest="config_cmd", required=True, metavar="<config command>")

    def target(m, what):
        g = m.add_mutually_exclusive_group()
        g.add_argument("--file", help=f"the config file to {what}")
        g.add_argument("--user", action="store_true",
                       help=f"{what} the per-user config file, read from any directory (`config path` shows it)")

    m = csub.add_parser("init", help="write a config file from the template: the values are asked in a terminal, "
                                     "else taken from --set (the other settings empty)")
    target(m, f"write (default: the file --config or NNNOTES_CONFIG names, else ./{DEFAULT_FILE})")
    m.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE",
                   help="a value to write (repeatable; no questions then): VALUE - reads it from standard input; a "
                        "list comma-separated; a path is written absolute")
    m.add_argument("--no-input", action="store_true", help="ask nothing, also in a terminal")
    m.add_argument("--force", action="store_true", help="overwrite an existing file")
    m.set_defaults(func=cmd_config_init, reads_config=False, usage=m.error)
    m = csub.add_parser("set", help="set one value of the config file the commands read (or of --file / --user; "
                                    "a missing one is made from the template); comments and other lines stay")
    m.add_argument("name", metavar="SECTION.KEY", help="the setting: bundle.key, servers.tw.cdn, paths.fonts.ja, ...")
    m.add_argument("value", help="the value (a list comma-separated); - reads it from standard input, without echo "
                                 "for the keys in a terminal")
    target(m, "edit")
    m.set_defaults(func=cmd_config_set, reads_config=False, usage=m.error)
    m = csub.add_parser("unset", help="empty one value of the config file (an empty value counts as unset)")
    m.add_argument("name", metavar="SECTION.KEY", help="the setting")
    target(m, "edit")
    m.set_defaults(func=cmd_config_unset, reads_config=False, usage=m.error)
    m = csub.add_parser("check", help="every setting: where it comes from and whether it is valid, never its value; "
                                      "exit status 1 when one is invalid, not found or unknown")
    m.add_argument("--json", action="store_true", help="the report as JSON (nnnotes.config-check/1)")
    m.set_defaults(func=cmd_config_check)
    m = csub.add_parser("path", help="the config files looked up, in order, and the one the commands read")
    m.add_argument("--json", action="store_true", help="the list as JSON (nnnotes.config-path/1)")
    m.set_defaults(func=cmd_config_path, reads_config=False)

    c = sub.add_parser("catalog", help="list addressable keys")
    c.add_argument("--prefix", default="", help="only the keys that start with this prefix")
    c.add_argument("--limit", type=int, default=200, help="print at most N keys (default 200; the total goes "
                                                          "to stderr)")
    c.set_defaults(func=cmd_catalog)

    c = sub.add_parser("browse", help="browse the configured regions' catalogs and bundles in a local web page")
    c.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    c.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    c.set_defaults(func=cmd_browse)

    c = sub.add_parser("pull", help="fetch the bundle closure of keys into the cache")
    c.add_argument("keys", nargs="+", metavar="KEY", help="addressable keys (`catalog` lists them)")
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
    c.add_argument("adv_id", type=int, help="MasterAdv id of the episode")
    _out(c, "output .json file")
    c.set_defaults(func=cmd_adv, usage=c.error)

    c = sub.add_parser("story", help="ADV episode -> story dir (episode, audio, scene, UI, media, videos) + its Live2D "
                                     "models")
    c.add_argument("adv_id", type=int, help="MasterAdv id of the episode")
    _out(c, "output directory")
    c.add_argument("--models", metavar="DIR",
                   help="directory of the Live2D models, one <model id>/ each (default: OUT/../live2d)")
    c.add_argument("--force", action="store_true", help="export the models again whose directory exists")
    _audio_args(c)
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
    c.add_argument("spot_id", type=int, help="MasterSpot id")
    _out(c, "output directory")
    c.set_defaults(func=cmd_spot, usage=c.error)

    c = sub.add_parser("room", help="background prefab -> glb")
    c.add_argument("key", help="addressable key of the prefab")
    _out(c, "output .glb file")
    c.set_defaults(func=cmd_room, usage=c.error)

    c = sub.add_parser("shader", help="dump shaders of a key's closure or of APK bundles")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="addressable key: the shaders of its bundle closure")
    g.add_argument("--apk-bundle", nargs="+", help="substring(s) of APK bundle file names")
    _out(c, "output directory")
    c.set_defaults(func=cmd_shader, usage=c.error)

    c = sub.add_parser("audio", help="decode a CRI cue sheet")
    c.add_argument("cue_sheet", help="cue sheet name: the key Cri/Sound/<cue_sheet> of the catalog")
    _out(c, "output directory")
    _audio_args(c)
    c.set_defaults(func=cmd_audio, usage=c.error)

    c = sub.add_parser("crikey", help="find the HCA keycode in base.apk")
    c.add_argument("--write", help="directory to write .hcakey into")
    c.set_defaults(func=cmd_crikey)

    c = sub.add_parser("player", help="player graphics settings -> JSON")
    _out(c, "output .json file")
    c.set_defaults(func=cmd_player)

    c = sub.add_parser("live", help="live (music + difficulty) -> self-contained live dir")
    c.add_argument("music_id", type=int, help="MasterLiveMusic id")
    c.add_argument("--difficulty", default="expert", choices=DIFFICULTY_CHOICES,
                   help="chart difficulty (default expert)")
    _audio_args(c)
    _fonts_arg(c)
    _band_args(c)
    _live_option_arg(c)
    _out(c, "output directory")
    c.set_defaults(func=cmd_live, usage=c.error)

    c = sub.add_parser("web", help="live charts, Live2D models and stories -> static site for ournotes-player "
                                   "(shared player + per-chart / per-model / per-story data)")
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
    c.add_argument("--compress", default=DEFAULT_ENCODING, choices=ENCODINGS,
                   help="encoding of the compressible assets (JSON, shaders, moc3, ...) where it makes them "
                        "smaller: gzip (default), br (brotli) or none")
    c.add_argument("--force", action="store_true",
                   help="rebuild charts, models and stories whose manifest exists (stories: also their models)")
    c.add_argument("--tmp", help="directory for the temporary live and model builds (default <site>.tmp)")
    c.add_argument("--workers", type=int,
                   help="parallel music / model processes (default a quarter of the CPUs, up to 8 / up to 4; "
                        "1 = this process)")
    c.add_argument("--read-workers", type=int,
                   help="chart read sets run at a time (default half the CPUs, up to 16)")
    t = c.add_mutually_exclusive_group()
    t.add_argument("--story", type=int, action="append", metavar="ADV_ID",
                   help="story episode: its MasterAdv id (repeatable)")
    t.add_argument("--all-stories", action="store_true", help="every story episode (MasterAdv) of the master data")
    c.add_argument("--story-languages", type=parse_languages, metavar="LANGS",
                   help="languages of the stories' text, comma-separated (default: ja,en,zh-Hant,zh-Hans,ko)")
    c.add_argument("--font", type=parse_font, action="append", metavar="LANG=PATH",
                   help="font file the story text of a language is drawn with (repeatable; [paths] fonts.<lang>); "
                        "emoji=PATH: the colour emoji font the emoji sprites are drawn from ([paths] fonts.emoji)")
    r = c.add_mutually_exclusive_group()
    r.add_argument("--region", dest="web_regions", action="append", metavar="REGION",
                   help="a region the site serves: a [servers.<region>] table (repeatable; the first is the base; "
                        "default: [catalog] region)")
    r.add_argument("--all-regions", action="store_true", help="every configured region")
    c.add_argument("--fonts", default="open", choices=("open", "game"),
                   help="text of the start canvas and the stories: open (default): the start canvas has layout and "
                        "style only, the stories glyphs from your font files (--font); game: the game's TMP fonts "
                        "(needs the 'fonts' extra)")
    _band_args(c)
    _live_option_arg(c)
    c.set_defaults(func=cmd_web, usage=c.error)

    c = sub.add_parser("music-data", help="every live song and chart: metadata in every language, chart facts and "
                                          "the deck model's chart statistics -> one JSON file")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--master-files", metavar="DIR",
                   help="master data files as served: MasterManifest.json and the .bin files it lists "
                        "(`master download`)")
    g.add_argument("--apk-master", action="store_true", help="the master data files of base.apk ([paths] apk)")
    g.add_argument("--decoded-master", action="store_true",
                   help="decoded master data ([paths] master or --master) with the MasterManifest.json of the files "
                        "it was decoded from; no master key")
    c.add_argument("--full", action="store_true",
                   help="also write the deck model's input: every chart's runtime notes and the master data tables "
                        "about cards, skills, bonuses, scores and events")
    c.add_argument("--no-deck", action="store_true",
                   help="do not run the deck model (every chart's deck is null)")
    c.add_argument("--seeds", type=int, default=8, metavar="N",
                   help="seeds measured on a chart with a luck range (default 8)")
    c.add_argument("--workers", type=int, metavar="N",
                   help="threads measuring charts (default: every processor)")
    c.add_argument("--no-gekisou-aptitude", action="store_true",
                   help="leave out the charts' Gekisou aptitude (every gekisouAptitude is null)")
    c.add_argument("--aptitude-max-seeds", type=int, metavar="N",
                   help="seeds of a Gekisou aptitude variant at most (default: the deck model's, 65536); "
                        "sampling stops earlier when both score targets converge")
    c.add_argument("--allow-unconverged-aptitude", action="store_true",
                   help="diagnostic export only: retain unmet SE flags at the sample cap; final exports reject them")
    c.add_argument("--aptitude-cross-seeds", type=int, metavar="N",
                   help="seeds of a Gekisou aptitude variant's cross terms (default: the deck model's, 64)")
    c.add_argument("--stats-cache", type=Path, metavar="DIR",
                   help="keep the charts' deck statistics in DIR: a chart whose model sources, options, master tables "
                        "and chart are unchanged is not measured again; DIR then holds this export's charts only")
    c.add_argument("--no-bgm", action="store_true",
                   help="do not read the BGM cue sheets (every song's bgm.length is null)")
    c.add_argument("--replay-dir", metavar="DIR",
                   help="write canonical runtime DeckData, per-chart inputs and replay manifest under the output directory")
    c.add_argument("--replay-engine", metavar="PKG",
                   help="the deck model's replay WASM release package (ournotes-replay-wasm-vVERSION.tar.gz or its "
                        "directory); its web JS/WASM and build-info.json are copied into --replay-dir")
    c.add_argument("--recommend-engine", metavar="PKG",
                   help="the deck model's recommendation WASM release package (ournotes-recommend-wasm-vVERSION.tar.gz "
                        "or its directory); its web JS/WASM and build-info.json are copied into --replay-dir")
    c.add_argument("--jackets", metavar="DIR",
                   help="also write every song's jacket as DIR/<jacket>.webp (at most 320 px on the longer side)")
    _out(c, "output file (.json, or .json.gz for gzip)")
    c.set_defaults(func=cmd_music_data, usage=c.error)

    cli_assets.register(sub, argparse.Namespace(open_catalog=open_catalog, print_json=_print_json))
    voices.register(sub, argparse.Namespace(open_catalog=open_catalog, master_dir=master_dir))
    c = sub.add_parser("ui", help="export an offline UI prefab library for ournotes-player/ui")
    _out(c, "UI data directory outside the source repositories")
    c.add_argument("--key", action="append", help="exact APK catalog key (repeatable)")
    c.add_argument("--prefix", default="EmbUI/", help="APK UI key prefix (default EmbUI/)")
    c.add_argument("--limit", type=int, help="maximum number of keys")
    c.add_argument("--no-dependencies", action="store_true", help="omit additional prefab roots and controllers")
    c.add_argument("--force", action="store_true", help="re-export the selected keys")
    c.set_defaults(func=cmd_ui, usage=c.error)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args) if getattr(args, "reads_config", True) else None
        args.func(args, cfg)
    except ConfigError as e:
        print(f"nnnotes: {e}", file=sys.stderr)
        sys.exit(2)
    except GameApiError as e:
        sys.exit(f"nnnotes: {e}")


if __name__ == "__main__":
    main()
