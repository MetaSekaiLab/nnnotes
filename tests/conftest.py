import os

import pytest

from nnnotes import cache, config

CACHE_DEFAULTS = cache.settings()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test runs in its own directory (no ./nnnotes.toml), without NNNOTES_* variables of the caller, with an
    empty per-user config directory, with no active settings left over and with the caches at their defaults (empty,
    no disk layer)."""
    for k in list(os.environ):
        if k.startswith("NNNOTES_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("APPDATA", str(tmp_path / "user-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_active", None)
    monkeypatch.setattr(cache, "_settings", dict(CACHE_DEFAULTS))
    cache.configure()
    yield tmp_path
