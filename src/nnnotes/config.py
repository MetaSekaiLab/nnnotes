"""Settings from a TOML file, environment variables and command-line flags.

Sources, lowest to highest precedence:

1. the TOML file: `--config <file>`, else the file named by `NNNOTES_CONFIG`, else `./nnnotes.toml` when present,
   else the per-user file (`user_file`) when present;
2. environment variables `NNNOTES_<SECTION>_<KEY>`: the setting's dotted name upper-cased, dots and dashes as
   underscores (`servers.tw.cdn` -> `NNNOTES_SERVERS_TW_CDN`, `bundle.nonce_seed` -> `NNNOTES_BUNDLE_NONCE_SEED`);
3. command-line flags.

No setting has a default and an empty value counts as unset. A command that needs a setting nobody gave stops with
a ConfigError naming the TOML key and the environment variable. Values are never printed, logged or put into an
error message. Relative paths in the TOML file are relative to the file's directory; relative paths from the
environment or the command line are relative to the working directory.
"""
from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

ENV_PREFIX = "NNNOTES_"
ENV_CONFIG = "NNNOTES_CONFIG"
DEFAULT_FILE = "nnnotes.toml"
TEMPLATE = "nnnotes.example.toml"         # package data: every setting with an empty value


class ConfigError(Exception):
    """A setting is missing or malformed (the message names the setting, never its value)."""


def check_catalog_version(value: str) -> str:
    """One safe international catalog filename component, including the legacy main selector."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) or ".." in value:
        raise ValueError("must be a single catalog version without path separators")
    return value


def user_file(environ: dict[str, str] | None = None) -> Path | None:
    """The per-user config file: `%APPDATA%\\nnnotes\\nnnotes.toml` on Windows, else
    `$XDG_CONFIG_HOME/nnnotes/nnnotes.toml` (`~/.config` when XDG_CONFIG_HOME is unset); None when the environment
    names no such directory."""
    env = os.environ if environ is None else environ
    if os.name == "nt":
        base = env.get("APPDATA")
    else:
        base = env.get("XDG_CONFIG_HOME") or (str(Path(env["HOME"]) / ".config") if env.get("HOME") else None)
    return Path(base) / "nnnotes" / DEFAULT_FILE if base else None


def config_files(path: str | Path | None = None,
                 environ: dict[str, str] | None = None) -> list[tuple[str, Path | None]]:
    """The config file candidates in lookup order, (how it is named, path or None): `--config`, NNNOTES_CONFIG, the
    working directory's nnnotes.toml, the per-user file. The first two are used when they are given (and must
    exist), the last two when they exist."""
    env = os.environ if environ is None else environ
    return [("--config", Path(path) if path else None),
            (ENV_CONFIG, Path(env[ENV_CONFIG]) if env.get(ENV_CONFIG) else None),
            ("working directory", Path(DEFAULT_FILE)),
            ("user", user_file(env))]


def find_file(path: str | Path | None = None, environ: dict[str, str] | None = None) -> Path | None:
    """The config file that is read (config_files), or None when there is none."""
    for name, file in config_files(path, environ):
        if file is None:
            continue
        if name in ("--config", ENV_CONFIG):
            if not file.is_file():
                raise ConfigError(f"config file {file} ({name}) not found")
            return file
        if file.is_file():
            return file
    return None


def template() -> bytes:
    """The config template shipped with the package (`nnnotes config init` writes it)."""
    from importlib import resources
    return resources.files(__package__).joinpath(TEMPLATE).read_bytes()


def env_name(section: str, key: str) -> str:
    return ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", f"{section}.{key}").upper()


def apk_missing(what: str) -> "ConfigError":
    """The error for data that is only in the game's APK when `[paths] apk` is not set."""
    return ConfigError(f"{what} needs the game's APK: give base.apk as {describe('paths', 'apk', '--apk')}")


def describe(section: str, key: str, flag: str | None = None) -> str:
    """Where a setting can be given: `[section] key` in the TOML file, the environment variable, the flag."""
    where = f"`{key}` in the [{section}] table of the config file, the environment variable {env_name(section, key)}"
    return where + (f" or {flag}" if flag else "")


class Config:
    """Merged settings. `overrides`: {(section, key): value} from command-line flags (None = not given)."""

    def __init__(self, data: dict | None = None, base: Path | None = None, source: str | None = None,
                 environ: dict[str, str] | None = None, overrides: dict | None = None, flags: dict | None = None):
        self._data = data or {}
        self._base = base
        self.source = source                    # the TOML file read, if any (a path, shown in messages)
        env = os.environ if environ is None else environ
        self._env = {k: v for k, v in env.items() if k.startswith(ENV_PREFIX) and k != ENV_CONFIG}
        self._over = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
        self._flags = dict(flags or {})         # {(section, key): "--flag"} for messages

    def __repr__(self) -> str:                  # never the values
        return f"Config(source={self.source!r})"

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path | None = None, *, environ: dict[str, str] | None = None,
             overrides: dict | None = None, flags: dict | None = None) -> "Config":
        env = os.environ if environ is None else environ
        file = find_file(path, env)
        if file is None:
            return cls(environ=env, overrides=overrides, flags=flags)
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"config file {file}: invalid TOML ({e})") from None
        return cls(data, base=file.resolve().parent, source=str(file), environ=env, overrides=overrides,
                   flags=flags)

    # ---------------------------------------------------------------- lookup
    def _table(self, section: str) -> dict:
        t = self._data
        for part in section.split("."):
            t = t.get(part) if isinstance(t, dict) else None
        return t if isinstance(t, dict) else {}

    def _raw(self, section: str, key: str):
        """(value, origin) with origin 'flag', 'env' or 'file'; (None, None) when unset."""
        if (section, key) in self._over:
            return self._over[(section, key)], "flag"
        v = self._env.get(env_name(section, key))
        if v:
            return v, "env"
        v = self._table(section).get(key)
        if v in (None, "", []):
            return None, None
        return v, "file"

    def has(self, section: str, key: str) -> bool:
        return self._raw(section, key)[0] is not None

    def origin(self, section: str, key: str) -> str | None:
        """Where a setting comes from: 'flag', 'env', 'file', or None when it is unset."""
        return self._raw(section, key)[1]

    def flag(self, section: str, key: str) -> str | None:
        """The command-line flag of a setting, when it has one."""
        return self._flags.get((section, key))

    def file_keys(self) -> list[tuple[str, str]]:
        """(section, key) of every value of the TOML file, a nested table as a dotted section ('' at the top)."""
        out = []

        def walk(table: dict, section: str) -> None:
            for k, v in table.items():
                if isinstance(v, dict):
                    walk(v, f"{section}.{k}" if section else k)
                else:
                    out.append((section, k))
        walk(self._data, "")
        return out

    def env_names(self) -> list[str]:
        """The names of the NNNOTES_ environment variables that set something (never their values)."""
        return sorted(k for k, v in self._env.items() if v)

    def missing(self, section: str, key: str) -> ConfigError:
        hint = "" if self.source else " (no config file was found: `nnnotes config init` writes one to fill in)"
        return ConfigError(f"setting {section}.{key} is not set: give it as "
                           f"{describe(section, key, self._flags.get((section, key)))}{hint}")

    def _bad(self, section: str, key: str, what: str) -> ConfigError:
        return ConfigError(f"setting {section}.{key}: {what}")

    def get(self, section: str, key: str) -> str | None:
        v, _ = self._raw(section, key)
        if v is None:
            return None
        if not isinstance(v, str):
            raise self._bad(section, key, "must be a string")
        return v

    def require(self, section: str, key: str) -> str:
        v = self.get(section, key)
        if v is None:
            raise self.missing(section, key)
        return v

    def get_list(self, section: str, key: str) -> list[str]:
        """A list of strings (TOML array; comma-separated in the environment or on the command line)."""
        v, origin = self._raw(section, key)
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
            raise self._bad(section, key, "must be a list of strings")
        return list(v)

    def path(self, section: str, key: str) -> Path | None:
        if section == "paths" and key in ("apk", "catalog") and self.origin(section, key) != "flag":
            region = self.get("catalog", "region")
            if region and self.has(f"servers.{region}", key):
                section = f"servers.{region}"
        v, origin = self._raw(section, key)
        if v is None:
            return None
        if not isinstance(v, (str, os.PathLike)):
            raise self._bad(section, key, "must be a path string")
        p = Path(v).expanduser()
        if origin == "file" and not p.is_absolute() and self._base is not None:
            p = self._base / p
        return p

    def require_path(self, section: str, key: str) -> Path:
        p = self.path(section, key)
        if p is None:
            raise self.missing(section, key)
        return p

    def hex(self, section: str, key: str, size: int | None = None) -> bytes:
        """A required hex string as bytes (`size`: the exact byte length)."""
        v = self.require(section, key)
        s = v.strip()
        if s[:2].lower() == "0x":
            s = s[2:]
        try:
            b = bytes.fromhex(s)
        except ValueError:
            raise self._bad(section, key, "must be a hex string") from None
        if size is not None and len(b) != size:
            raise self._bad(section, key, f"must be {size} bytes ({2 * size} hex digits)")
        return b

    # ---------------------------------------------------------------- servers
    def regions(self) -> list[str]:
        """Region names: the [servers.<region>] tables and the regions given by NNNOTES_SERVERS_<REGION>_CDN."""
        names = [k for k, v in self._table("servers").items() if isinstance(v, dict)]
        for k in self._env:
            m = re.fullmatch(ENV_PREFIX + r"SERVERS_([A-Z0-9_]+)_CDN", k)
            if m and m.group(1).lower() not in names:
                names.append(m.group(1).lower())
        return names

    def region(self) -> str:
        return self.require("catalog", "region")

    def provider(self, region: str | None = None) -> str:
        region = region or self.get("catalog", "region")
        value = self.get(f"servers.{region}", "provider") or ("jp" if region == "jp" else "international")
        if value not in ("jp", "international"):
            raise ConfigError(f"setting servers.{region}.provider: must be jp or international")
        return value

    def for_region(self, region: str) -> "Config":
        overrides = {**self._over, ("catalog", "region"): region}
        if self.provider(region) == "jp":
            overrides[("catalog", "language")] = "ja"
        return Config(self._data, self._base, self.source, self._env, overrides, self._flags)

    def cdn(self, region: str) -> str:
        """CDN base of a region, without a trailing slash."""
        return self.require(f"servers.{region}", "cdn").rstrip("/")

    def catalog_version(self, region: str | None = None) -> str:
        """International selector: flag, regional/global pin, API resource_version, else legacy main."""
        region = region or self.get("catalog", "region")
        section, key = "catalog", "version"
        if region and self.origin(section, key) != "flag" and self.has(f"servers.{region}", "catalog_version"):
            section, key = f"servers.{region}", "catalog_version"
        value = self.get(section, key)
        if value is None and region and self.has(f"servers.{region}", "api"):
            from .gameapi import master_version
            value = master_version(self, region).resource_version
            section, key = f"servers.{region}", "api"
        elif value is None:
            value = "main"
        try:
            return check_catalog_version(value)
        except ValueError as error:
            raise ConfigError(f"setting {section}.{key}: {error}") from None

    def master(self, region: str | None) -> tuple[str, str]:
        """The setting that names the decoded master data of `region`: the --master flag, else
        `[servers.<region>] master`, else `[paths] master` -> (section, key)."""
        if region and self.origin("paths", "master") != "flag" and self.has(f"servers.{region}", "master"):
            return f"servers.{region}", "master"
        return "paths", "master"


# ---------------------------------------------------------------- the process's settings
_active: Config | None = None


def use(cfg: Config) -> Config:
    """Make `cfg` the settings of this process (external tools; worker processes call it with the parent's)."""
    global _active
    _active = cfg
    return cfg


def active() -> Config | None:
    return _active


def usable_cpus() -> int:
    """The CPUs this process may run on, which sizes the default worker counts: its affinity mask (taskset, a
    container's cpuset), not the machine's CPU count. os.process_cpu_count (Python 3.13), else the affinity mask where
    the platform reports one, else os.cpu_count; at least 1."""
    count = getattr(os, "process_cpu_count", None)
    if count is not None:
        n = count()
    elif hasattr(os, "sched_getaffinity"):
        n = len(os.sched_getaffinity(0))
    else:
        n = os.cpu_count()
    return max(1, n or 1)


def tool(name: str, exe: str) -> str:
    """An external program: `[paths] <name>` of the active settings, else `exe` on PATH."""
    p = _active.path("paths", name) if _active is not None else None
    if p is not None:
        return str(p)
    found = shutil.which(exe)
    if not found:
        raise ConfigError(f"{exe} not found on PATH: give its path as {describe('paths', name, '--' + name)}")
    return found
