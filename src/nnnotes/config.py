"""Settings from a TOML file, environment variables and command-line flags.

Sources, lowest to highest precedence:

1. the TOML file: `--config <file>`, else the file named by `NNNOTES_CONFIG`, else `./nnnotes.toml` when present;
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


class ConfigError(Exception):
    """A setting is missing or malformed (the message names the setting, never its value)."""


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
        if path:
            file = Path(path)
            if not file.is_file():
                raise ConfigError(f"config file {file} (--config) not found")
        elif env.get(ENV_CONFIG):
            file = Path(env[ENV_CONFIG])
            if not file.is_file():
                raise ConfigError(f"config file {file} ({ENV_CONFIG}) not found")
        else:
            file = Path(DEFAULT_FILE)
            if not file.is_file():
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

    def missing(self, section: str, key: str) -> ConfigError:
        return ConfigError(f"setting {section}.{key} is not set: give it as "
                           f"{describe(section, key, self._flags.get((section, key)))}")

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

    def cdn(self, region: str) -> str:
        """CDN base of a region, without a trailing slash."""
        return self.require(f"servers.{region}", "cdn").rstrip("/")

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


def tool(name: str, exe: str) -> str:
    """An external program: `[paths] <name>` of the active settings, else `exe` on PATH."""
    p = _active.path("paths", name) if _active is not None else None
    if p is not None:
        return str(p)
    found = shutil.which(exe)
    if not found:
        raise ConfigError(f"{exe} not found on PATH: give its path as {describe('paths', name, '--' + name)}")
    return found
