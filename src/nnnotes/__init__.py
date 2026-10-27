"""nnnotes: offline data toolkit for BanG Dream! Our Notes game files.

Reads Addressables catalogs, fetches and decrypts asset bundles, decodes master data and exports stories, Live2D
models, spots, shaders, CRI audio and live charts as JSON and common file formats. Game files, keys and server
addresses are supplied by the user through the configuration; nothing of the game is included.

The names below are the entry points of the Python API, imported on first use:

    from nnnotes import Config, Session

    session = Session(Config.load())
    catalog = session.catalog()

The subpackages and the names in their `__all__` are the documented API; other names are internal.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.3.2"

_LAZY = {
    "Catalog": "nnnotes.source.catalog",
    "Config": "nnnotes.source.config",
    "ConfigError": "nnnotes.source.config",
    "GameApiError": "nnnotes.source.gameapi",
    "MusicDataError": "nnnotes.musicdata",
    "Session": "nnnotes.source.session",
}

__all__ = ["Catalog", "Config", "ConfigError", "GameApiError", "MusicDataError", "Session", "__version__"]

if TYPE_CHECKING:
    from .musicdata import MusicDataError
    from .source.catalog import Catalog
    from .source.config import Config, ConfigError
    from .source.gameapi import GameApiError
    from .source.session import Session


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'nnnotes' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
