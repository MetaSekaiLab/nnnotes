"""The game's text languages: catalog language codes, Fwk.Localization.LanguageMode values and MasterText columns.

A catalog is `catalog_main_<code>.bin`; the client's LanguageMode picks the column of the text tables
(MasterLoader.GetLocalizedText) and the fonts of LocalizeManager. Every region serves every language.
"""
from __future__ import annotations

from .config import Config, ConfigError, active

# code -> (Fwk.Localization.LanguageMode, text table column)
LANGUAGES = {
    "ja": (0, "_japanese"),
    "en": (1, "_english"),
    "zh-Hant": (2, "_traditionalChinese"),
    "zh-Hans": (3, "_simplifiedChinese"),
    "ko": (4, "_korean"),
}


def check(code: str) -> str:
    """`code` when it is one of LANGUAGES, else a ConfigError naming the setting (not the value)."""
    if code not in LANGUAGES:
        raise ConfigError(f"setting catalog.language: not one of the game's languages ({', '.join(LANGUAGES)})")
    return code


def configured() -> str:
    """`[catalog] language` of the process's settings (config.use; without them, of the environment), checked."""
    return check((active() or Config()).require("catalog", "language"))


def mode(code: str) -> int:
    """The LanguageMode of a language code."""
    return LANGUAGES[check(code)][0]


def column(code: str) -> str:
    """The text table column of a language code (`_traditionalChinese` for zh-Hant)."""
    return LANGUAGES[check(code)][1]


def texts(row: dict | None) -> dict:
    """{code: text} of a text table row in every language (None for a column the row lacks)."""
    return {code: (row or {}).get(col) for code, (_, col) in LANGUAGES.items()}
