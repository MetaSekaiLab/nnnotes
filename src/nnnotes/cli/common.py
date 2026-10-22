"""What the commands share: the global options and the settings they set, the command parsers, usage errors and the
output (a short summary, or with --json the command's report as JSON)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NoReturn

from ..core.jsonio import dumps
from ..source.catalog import Catalog
from ..source.config import Config, ConfigError, use

DIFFICULTY_CHOICES = ("easy", "normal", "hard", "expert")
AUDIO_CHOICES = ("flac", "ogg", "wav")
FLAC_LEVELS = range(13)
EMOJI_FONT = "emoji"  # --font emoji=<file>
# command-line flags of settings: (section, key) -> (argparse dest, flag)
FLAG_SETTINGS = {
    ("catalog", "region"): ("region", "--region"),
    ("catalog", "language"): ("language", "--language"),
    ("catalog", "version"): ("catalog_version", "--catalog-version"),
    ("paths", "catalog"): ("catalog", "--catalog"),
    ("paths", "master"): ("master", "--master"),
    ("paths", "apk"): ("apk", "--apk"),
    ("paths", "ffmpeg"): ("ffmpeg", "--ffmpeg"),
    ("paths", "ffprobe"): ("ffprobe", "--ffprobe"),
    ("paths", "vgaudio"): ("vgaudio", "--vgaudio"),
    ("paths", "vgmstream"): ("vgmstream", "--vgmstream"),
    ("paths", "node"): ("node", "--node"),
    ("paths", "depot"): ("depot", "--depot"),
    ("paths", "player"): ("player", "--player"),
}
# the global options: dest -> (flag, metavar, help); --player is an option of `site build` only
GLOBAL_OPTIONS = {
    "config": ("--config", "FILE", "TOML config file (else NNNOTES_CONFIG, else ./nnnotes.toml)"),
    "region": ("--region", "REGION", "region: a [servers.<region>] table ([catalog] region)"),
    "language": (
        "--language",
        "LANG",
        "catalog and client language: ja, en, zh-Hant, zh-Hans or ko ([catalog] language)",
    ),
    "catalog": ("--catalog", "FILE", "catalog .bin file ([paths] catalog; else downloaded into the depot)"),
    "catalog_version": (
        "--catalog-version",
        "VERSION",
        "the resource version of an international catalog ([catalog] version; else the version the game API "
        "serves when an API root is configured, otherwise main)",
    ),
    "depot": ("--depot", "DIR", "depot directory ([paths] depot)"),
    "master": ("--master", "DIR", "decoded master data directory ([paths] master)"),
    "apk": ("--apk", "FILE", "base.apk ([paths] apk)"),
    "ffmpeg": ("--ffmpeg", "EXE", "ffmpeg executable ([paths] ffmpeg; else on PATH)"),
    "ffprobe": ("--ffprobe", "EXE", "ffprobe executable ([paths] ffprobe; else on PATH)"),
    "vgaudio": ("--vgaudio", "FILE", "VGAudioCli executable or .NET DLL ([paths] vgaudio; else on PATH)"),
    "vgmstream": ("--vgmstream", "EXE", "vgmstream-cli executable ([paths] vgmstream; else on PATH)"),
    "node": ("--node", "EXE", "Node.js executable ([paths] node; else on PATH)"),
}
JSON_HELP = "print the command's report as JSON on standard output (default: a short summary)"


class Formatter(argparse.HelpFormatter):
    """The help of the command line: a usage line without the global options (the help lists them)."""

    def add_usage(self, usage, actions, groups, prefix=None):
        hidden = {*GLOBAL_OPTIONS, "json"}
        super().add_usage(usage, [a for a in actions if a.dest not in hidden], groups, prefix)


def global_options() -> argparse.ArgumentParser:
    """The parent parser of the options every command takes, before or after the command's name. Their defaults are
    left out of the namespace (an option given before the command name stays when the command's parser does not see
    it again) and filled in by `fill_globals`."""
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("global options")
    for dest, (flag, metavar, text) in GLOBAL_OPTIONS.items():
        g.add_argument(flag, dest=dest, metavar=metavar, default=argparse.SUPPRESS, help=text)
    g.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=JSON_HELP)
    return p


GLOBALS = global_options()


def fill_globals(args) -> None:
    for dest in (*GLOBAL_OPTIONS, "player"):
        if not hasattr(args, dest):
            setattr(args, dest, None)
    if not hasattr(args, "json"):
        args.json = False


def load_config(args) -> Config:
    """The settings of the config file, the environment and the global options (config.Config.load), made the
    process's settings (config.use)."""
    overrides = {k: getattr(args, dest, None) for k, (dest, _) in FLAG_SETTINGS.items()}
    flags = {k: flag for k, (_, flag) in FLAG_SETTINGS.items()}
    cfg = Config.load(getattr(args, "config", None), overrides=overrides, flags=flags)
    if getattr(args, "raw_config", False):
        return use(cfg)  # diagnose the settings as given, before the runtime defaults
    if cfg.provider() == "jp" and not cfg.has("catalog", "language"):
        cfg = cfg.for_region(cfg.region())
    return use(cfg)


def group(sub, name: str, text: str):
    """A group of commands `nnnotes NAME <command>`: its subparsers."""
    p = sub.add_parser(name, help=text, description=text, parents=[GLOBALS], formatter_class=Formatter)
    return p.add_subparsers(dest=f"{name.replace('-', '_')}_command", required=True, metavar="<command>")


def command(sub, name: str, func, text: str, *, reads_config: bool = True) -> argparse.ArgumentParser:
    """The parser of a command that runs `func(args, cfg)` (cfg: the settings, None when `reads_config` is false);
    `usage(args, message)` exits with a usage error (status 2) of this command."""
    p = sub.add_parser(name, help=text, description=text, parents=[GLOBALS], formatter_class=Formatter)
    p.set_defaults(func=func, parser=p, reads_config=reads_config)
    return p


# ---------------------------------------------------------------- output
def write(text: str) -> None:
    """Text on standard output as UTF-8 bytes (a GBK / cp932 console cannot encode every string)."""
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def print_json(doc) -> None:
    write(dumps(doc, ensure_ascii=False, indent=1) + "\n")


def note(text: str) -> None:
    """A line for the reader on standard error (a count, a hint): never part of a report."""
    print(text, file=sys.stderr)


def _scalar(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, str):
        return v
    return dumps(v)


def summarize(doc) -> list[str]:
    """The summary lines of a report: per field `name: value`, a list by its length, an object by its number of
    entries (a small object of plain values inline)."""
    if not isinstance(doc, dict):
        return [f"{len(doc)} items"] if isinstance(doc, list) else [_scalar(doc)]
    lines = []
    for k, v in doc.items():
        if isinstance(v, dict):
            plain = all(not isinstance(x, (dict, list)) for x in v.values())
            if v and plain and len(v) <= 8:
                lines.append(f"{k}: " + ", ".join(f"{a} {_scalar(b)}" for a, b in v.items()))
            else:
                lines.append(f"{k}: {len(v)} entries")
        elif isinstance(v, list):
            lines.append(f"{k}: {len(v)} items")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return lines


def report(args, doc, lines=None) -> None:
    """The command's report: as JSON with --json, else the lines `lines(doc)` (default: summarize)."""
    if args.json:
        print_json(doc)
        return
    out = (lines or summarize)(doc)
    if out:
        write("".join(f"{line}\n" for line in out))


# ---------------------------------------------------------------- usage errors
def usage(args, message: str) -> NoReturn:
    """Exit with a usage error (status 2) of the command `args` were parsed for."""
    argparse.ArgumentParser.error(args.parser, message)


def known_keys(args, cat: Catalog, keys) -> None:
    """A usage error (exit 2) naming the keys the catalog does not have."""
    unknown = [k for k in keys if not cat.has(k)]
    if unknown:
        usage(args, f"not a key of the catalog: {', '.join(unknown)}")


def known_row(args, md: Path, table: str, row_id: int, what: str) -> None:
    """A usage error (exit 2) naming `row_id` when the master table `table` has no row with that `_id`."""
    from ..source.master import has_row

    try:
        found = has_row(md, table, row_id)
    except FileNotFoundError:
        raise ConfigError(
            f"master data {md}: no {table}.json (a directory written by `nnnotes master decode`)"
        ) from None
    if not found:
        usage(args, f"{what} {row_id}: no {table} row with this id")


def flac_options(args) -> dict:
    """The cri.decode options of --flac-level ({} without it); a level out of range, or the flag where no FLAC is
    written, is a usage error."""
    level = args.flac_level
    if level is None:
        return {}
    if level not in FLAC_LEVELS:
        usage(args, f"--flac-level {level}: expected 0 to 12")
    if args.format != "flac":
        usage(args, f"--flac-level: --format {args.format} writes no FLAC")
    if getattr(args, "no_audio", False):
        usage(args, "--flac-level: --no-audio decodes no audio")
    return {"flac_level": level}


def fonts_extra(fonts: str) -> None:
    """`--fonts game` needs the optional `fonts` dependencies."""
    if fonts == "game":
        from ..unity.tmpfont import require_extra

        require_extra("--fonts game")


def live_options(args) -> dict:
    """The --live-option specs as a request (liveoptions.parse_specs); a malformed spec is a usage error."""
    from ..content.liveoptions import OptionSpecError, parse_specs

    try:
        return parse_specs(args.live_option)
    except OptionSpecError as e:
        usage(args, str(e))


# ---------------------------------------------------------------- argument types and options
def nonnegative(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s}: expected a whole number") from None
    if v < 0:
        raise argparse.ArgumentTypeError(f"{s}: expected 0 or more")
    return v


def positive(s: str) -> int:
    v = nonnegative(s)
    if v == 0:
        raise argparse.ArgumentTypeError(f"{s}: expected 1 or more")
    return v


def parse_pair(s: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d+)[:_](easy|normal|hard|expert)", s)
    if not m:
        raise argparse.ArgumentTypeError(f"{s}: expected <musicId>:<difficulty>")
    return int(m.group(1)), m.group(2)


def parse_languages(s: str) -> list[str]:
    from ..source.languages import LANGUAGES

    codes = [c.strip() for c in s.split(",") if c.strip()]
    if not codes or any(c not in LANGUAGES for c in codes):
        raise argparse.ArgumentTypeError(f"{s}: expected languages from {', '.join(LANGUAGES)}, comma-separated")
    return codes


def parse_font(s: str) -> tuple[str, str]:
    from ..source.languages import LANGUAGES

    lang, sep, path = s.partition("=")
    if not sep or (lang not in LANGUAGES and lang != EMOJI_FONT) or not path:
        raise argparse.ArgumentTypeError(
            f"{s}: expected <language>=<font file> or {EMOJI_FONT}=<emoji font file>, "
            f"language one of {', '.join(LANGUAGES)}"
        )
    return lang, path


def out_arg(c, what: str, required: bool = True) -> None:
    c.add_argument("-o", "--out", required=required, help=what)


def workers_arg(c, what: str, default: str, zero: str = "this process") -> None:
    c.add_argument("--workers", type=nonnegative, metavar="N", help=f"{what} (default {default}; 0: {zero})")


def audio_args(c, default: str = "flac") -> None:
    """--format and --flac-level of a command that decodes cue sheets."""
    c.add_argument("--format", default=default, choices=AUDIO_CHOICES, help=f"audio file format (default {default})")
    flac_level_arg(c)


def flac_level_arg(c, what: str = "of --format flac") -> None:
    c.add_argument(
        "--flac-level",
        type=int,
        metavar="N",
        help=f"FLAC compression level 0-12 {what} (default 8; every level decodes to the same samples)",
    )


def fonts_arg(c, text: str) -> None:
    c.add_argument("--fonts", default="open", choices=("open", "game"), help=text)


def band_args(c) -> None:
    g = c.add_mutually_exclusive_group()
    g.add_argument(
        "--band",
        type=int,
        help="band of the LightWeight background and start timeline "
        "(default: band of the music's first vocal character)",
    )
    g.add_argument("--leader-card", type=int, help="deck centre MasterMemberCard id (its character's band)")


def live_option_arg(c) -> None:
    c.add_argument(
        "--live-option",
        action="append",
        metavar="OPTION[=VALUES]",
        help="also export the files of a Live option's variants (repeatable): MirrorChart, "
        "MeasureLineDisplay, NoteDesignId, NoteEffectId, LiveQuality, NoteSePatternId (every value "
        "of the master data, or =v,v...), defaults (the option defaults and ranges only) or all",
    )
