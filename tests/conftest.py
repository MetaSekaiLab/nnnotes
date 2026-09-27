import os

import pytest

from nnnotes import config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test runs in its own directory (no ./nnnotes.toml), without NNNOTES_* variables of the caller, with an
    empty per-user config directory and with no active settings left over."""
    for k in list(os.environ):
        if k.startswith("NNNOTES_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("APPDATA", str(tmp_path / "user-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_active", None)
    yield tmp_path
