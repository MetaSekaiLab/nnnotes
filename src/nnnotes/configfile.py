"""The config file: the settings it holds, writing and editing it, and checking the settings.

The settings are those of the template (config.TEMPLATE, every value empty), plus one `[servers.<region>]` table per
region and the `[paths.fonts]` table. `write_new` renders the template with values, `set_value` edits one value of
an existing file in place (comments and the other lines stay), `check` reports each setting's origin and status.
Values are never printed: the prompts for the keys do not echo, and the reports hold states, not values.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import languages
from .config import Config, ConfigError, env_name, template

SECRETS = {("bundle", "key"), ("bundle", "nonce_seed"), ("master", "key"), ("master", "iv")}
HEX_BYTES = {("bundle", "key"): 16, ("bundle", "nonce_seed"): None, ("master", "key"): 32, ("master", "iv"): 32}
REGION = "servers.<region>"             # the section of the per-region settings
TEMPLATE_REGION = "tw"                  # the example region table of the template
FONTS = "paths.fonts"
FONT_KEYS = (*languages.LANGUAGES, "emoji")
LINKS = ("auto", "clone", "hard", "copy")
FILES = {("paths", "catalog"), ("paths", "apk"), ("paths", "ffmpeg"), ("paths", "vgmstream"), ("paths", "node")}
DIRS = {("paths", "master"), ("paths", "player"), (REGION, "master")}
REGION_PATHS = {"apk", "catalog"}
CHECK = "nnnotes.config-check/1"
PATHS = "nnnotes.config-path/1"
PROBLEMS = ("invalid", "not found", "unknown")

_HEADER = re.compile(r"^\s*\[(?!\[)\s*([A-Za-z0-9_.-]+)\s*\]\s*(?:#.*)?$")
_KEYVAL = re.compile(r"^(\s*)([A-Za-z0-9_-]+)\s*=\s*(.*)$")
_REGION_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Setting:
    section: str                        # "bundle", REGION, FONTS, ...
    key: str
    kind: str                           # "string", "list" or "path"
    secret: bool
    description: str


def _template_settings() -> list[Setting]:
    out, section, comment = [], None, []
    for line in template().decode("utf-8").splitlines():
        s = line.strip()
        if not s:
            comment = []
        elif s.startswith("#"):
            comment.append(s.lstrip("#").strip())
        elif m := _HEADER.match(line):
            section, comment = m.group(1), []
        elif (m := _KEYVAL.match(line)) and section:
            sec = REGION if section == f"servers.{TEMPLATE_REGION}" else section
            key, value = m.group(2), m.group(3).strip()
            kind = "list" if value.startswith("[") else "path" if (sec == "paths" or (sec, key) in DIRS
                    or sec == REGION and key in REGION_PATHS) else "string"
            out.append(Setting(sec, key, kind, (sec, key) in SECRETS, " ".join(comment)))
            comment = []
    for k in FONT_KEYS:
        what = "colour emoji font (CBDT or sbix) of the stories' emoji sprites" if k == "emoji" else \
            f"font file (OpenType or TrueType) the story text of {k} is drawn with (--fonts open)"
        out.append(Setting(FONTS, k, "path", False, what))
    return out


SETTINGS = _template_settings()


def resolve(name: str) -> tuple[Setting, str]:
    """(setting, concrete section) of a dotted name: `bundle.key`, `servers.tw.cdn`, `paths.fonts.zh-Hant`."""
    section, _, key = name.rpartition(".")
    for s in SETTINGS:
        if s.section == section and s.key == key and s.section != REGION:
            return s, section
    head, _, region = section.rpartition(".")
    if head == "servers" and _REGION_NAME.fullmatch(region):
        for s in SETTINGS:
            if s.section == REGION and s.key == key:
                return s, section
    raise ConfigError(f"unknown setting {name}: `nnnotes config check` lists the settings")


def parse_value(setting: Setting, text: str):
    """The value of a setting from its text form (a list: comma-separated; a path: made absolute), checked."""
    text = text.strip()
    if setting.kind == "list":
        value = [s.strip() for s in text.split(",") if s.strip()]
    elif setting.kind == "path" and text:
        value = str(Path(text).expanduser().absolute())
    else:
        value = text
    if value:
        validate(setting, value)
    return value


def validate(setting: Setting, value) -> None:
    """Raise ValueError (a message without the value) when `value` is not a valid value of `setting`."""
    k = (setting.section, setting.key)
    if k in HEX_BYTES:
        s = value[2:] if value[:2].lower() == "0x" else value
        try:
            b = bytes.fromhex(s)
        except ValueError:
            raise ValueError("must be a hex string") from None
        size = HEX_BYTES[k]
        if size is not None and len(b) != size:
            raise ValueError(f"must be {size} bytes ({2 * size} hex digits)")
    elif k == ("catalog", "language") or k == (REGION, "languages"):
        for v in value if isinstance(value, list) else [value]:
            if v not in languages.LANGUAGES:
                raise ValueError(f"not one of the game's languages ({', '.join(languages.LANGUAGES)})")
    elif k == (REGION, "cdn"):
        u = urlsplit(value)
        if u.scheme not in ("https", "http") or not u.netloc:
            raise ValueError("must be an http(s):// URL")
    elif k == (REGION, "provider") and value not in ("jp", "international"):
        raise ValueError("must be jp or international")
    elif k in ((REGION, "api"), ("bootstrap", "api")):
        from .gameapi import channel_target
        channel_target(value)
    elif k == ("export", "link") and value not in LINKS:
        raise ValueError(f"must be one of {', '.join(LINKS)}")


# ---------------------------------------------------------------- TOML text
def _toml(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if "'" not in value and not re.search(r"[\x00-\x1f\x7f]", value):
        return f"'{value}'"                             # a literal string: paths keep their backslashes
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _headers(lines: list[str]) -> list[tuple[int, str]]:
    return [(i, m.group(1)) for i, line in enumerate(lines) if (m := _HEADER.match(line))]


def _region_block(region: str) -> list[str]:
    """The template's example region table (comment lines, header, keys) for `region`."""
    lines = template().decode("utf-8").splitlines(keepends=True)
    heads = _headers(lines)
    for n, (i, name) in enumerate(heads):
        if name == f"servers.{TEMPLATE_REGION}":
            end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
            block = lines[i:end]
            while block and not block[-1].strip():
                block.pop()
            return [f"[servers.{region}]\n"] + block[1:]
    return [f"[servers.{region}]\n"]


def _set_line(text: str, section: str, key: str, value) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    heads = _headers(lines)
    for n, (i, name) in enumerate(heads):
        if name != section:
            continue
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        last = i
        for j in range(i + 1, end):
            m = _KEYVAL.match(lines[j])
            if not m:
                continue
            last = j
            if m.group(2) == key:
                v = m.group(3).strip()
                if v.startswith(('"""', "'''")) or (v.startswith("[") and v.count("[") > v.count("]")):
                    raise ConfigError(f"setting {section}.{key}: its value spans several lines of the file; edit "
                                      f"the file itself")
                lines[j] = f"{m.group(1)}{key} = {_toml(value)}\n"
                return "".join(lines)
        lines.insert(last + 1, f"{key} = {_toml(value)}\n")
        return "".join(lines)
    block = _region_block(section.split(".", 1)[1]) if section.startswith("servers.") else [f"[{section}]\n"]
    text = "".join(lines) + ("\n" if "".join(lines).strip() else "") + "".join(block)
    return _set_line(text, section, key, value)


def _lookup(data: dict, section: str, key: str):
    t = data
    for part in section.split("."):
        t = t.get(part) if isinstance(t, dict) else None
    return t.get(key) if isinstance(t, dict) else None


def edit(text: str, values: list[tuple[str, str, object]], where: str) -> str:
    """`text` with each (section, key, value) set in place; the result is checked to read back as those values."""
    for section, key, value in values:
        text = _set_line(text, section, key, value)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ConfigError(f"{where}: the edited file is not valid TOML (a table in a form this command does not "
                          f"edit); edit the file itself") from None
    for section, key, value in values:
        if _lookup(data, section, key) != value:
            raise ConfigError(f"setting {section}.{key}: {where} defines it in a form this command does not edit; "
                              f"edit the file itself")
    return text


def render(values: list[tuple[str, str, object]], regions: list[str]) -> str:
    """The template with one region table per region (the example table renamed, repeated) and the values set."""
    text = template().decode("utf-8")
    if regions:
        lines = text.splitlines(keepends=True)
        heads = _headers(lines)
        for n, (i, name) in enumerate(heads):
            if name == f"servers.{TEMPLATE_REGION}":
                end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
                blocks = ["".join(_region_block(r)) for r in regions]
                text = "".join(lines[:i]) + "\n".join(blocks) + "\n" + "".join(lines[end:])
                break
    return edit(text, values, "the template")


def write(path: Path, text: str) -> None:
    """Write the config file atomically; readable by its owner only (it holds the keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------- prompts
def prompt(regions_hint: str = TEMPLATE_REGION, ask=input, ask_secret=getpass.getpass, say=print) \
        -> tuple[list[tuple[str, str, object]], list[str]]:
    """Ask for every setting in the terminal (Enter leaves one empty; the keys are read without echo) -> (values,
    regions)."""
    values: list[tuple[str, str, object]] = []

    def one(s: Setting, section: str, default: str = ""):
        name = f"[{section}] {s.key}"
        hint = " (hidden)" if s.secret else " (comma-separated)" if s.kind == "list" else ""
        say(f"# {s.description}" if s.description else "#")
        while True:
            text = (ask_secret if s.secret else ask)(f"{name}{hint}{f' [{default}]' if default else ''}: ")
            try:
                v = parse_value(s, text or default)
            except (ValueError, ConfigError) as e:
                say(f"  {e}; again, or Enter to leave it empty")
                continue
            if v:
                values.append((section, s.key, v))
            return

    fixed = [s for s in SETTINGS if s.section not in (REGION, FONTS)]
    for s in fixed:
        if s.section in ("bundle", "master"):
            one(s, s.section)
    say("# regions to configure: names of your choice, one [servers.<region>] table each")
    regions = []
    while not regions:
        text = ask(f"regions (comma-separated) [{regions_hint}]: ") or regions_hint
        regions = [r.strip() for r in text.split(",") if r.strip()]
        if not all(_REGION_NAME.fullmatch(r) for r in regions):
            say("  a region name holds letters, digits, '-' and '_' only")
            regions = []
    for s in fixed:
        if s.section == "catalog":
            one(s, s.section, regions[0] if s.key == "region" else "")
    for r in regions:
        for s in SETTINGS:
            if s.section == REGION:
                one(s, f"servers.{r}")
    for s in fixed:
        if s.section not in ("bundle", "master", "catalog"):
            one(s, s.section)
    return values, regions


# ---------------------------------------------------------------- check
def _status(cfg: Config, s: Setting, section: str) -> tuple[str, str | None]:
    if cfg.origin(section, s.key) is None:
        return "unset", None
    try:
        if s.kind == "list":
            validate(s, cfg.get_list(section, s.key))
        elif s.kind == "path":
            p = cfg.path(section, s.key)
            if s.key == "apk":
                if not p.exists():
                    return "not found", "no such APK or directory"
            elif (s.section, s.key) in FILES or s.section == FONTS or (s.section, s.key) == (REGION, "catalog"):
                if not p.is_file():
                    return "not found", "no such file"
            elif (s.section, s.key) in DIRS and not p.is_dir():
                return "not found", "no such directory"
        else:
            v = cfg.get(section, s.key)
            validate(s, v)
            if (section, s.key) == ("catalog", "region") and v not in cfg.regions():
                return "invalid", f"names no [servers.{v}] table and no {env_name('servers.' + v, 'cdn')}"
    except ConfigError as e:
        return "invalid", str(e).split(": ", 1)[-1]
    except ValueError as e:
        return "invalid", str(e)
    return "ok", None


def check(cfg: Config) -> dict:
    """Every setting with its origin (flag, env, file or None) and status (ok, unset, invalid, not found), plus the
    keys of the file and the NNNOTES_ environment variables that name no setting (unknown). No values."""
    out = []
    known_env = set()

    def record(s: Setting, section: str, status: str, reason: str | None, origin: str | None):
        env = env_name(section, s.key)
        known_env.add(env)
        out.append({"name": f"{section}.{s.key}", "env": env, "flag": cfg.flag(section, s.key), "kind": s.kind,
                    "secret": s.secret, "description": s.description, "origin": origin, "status": status,
                    **({"reason": reason} if reason else {})})

    region_settings = [s for s in SETTINGS if s.section == REGION]
    for s in SETTINGS:
        if s.section == REGION:
            if s is region_settings[0]:                 # every region's table where the template has its example
                for r in cfg.regions():
                    for rs in region_settings:
                        status, reason = _status(cfg, rs, f"servers.{r}")
                        record(rs, f"servers.{r}", status, reason, cfg.origin(f"servers.{r}", rs.key))
            continue
        if s.section == FONTS and cfg.origin(s.section, s.key) is None:
            known_env.add(env_name(s.section, s.key))
            continue
        status, reason = _status(cfg, s, s.section)
        record(s, s.section, status, reason, cfg.origin(s.section, s.key))
    for section, key in cfg.file_keys():
        try:
            resolve(f"{section}.{key}")
        except ConfigError:
            out.append({"name": f"{section}.{key}", "origin": "file", "status": "unknown",
                        "reason": "not a setting of nnnotes"})
    for env in cfg.env_names():
        if env not in known_env:
            out.append({"name": env, "origin": "env", "status": "unknown", "reason": "names no setting of nnnotes"})
    return {"schema": CHECK, "file": cfg.source and str(Path(cfg.source).absolute()), "settings": out,
            "problems": sum(r["status"] in PROBLEMS for r in out)}
