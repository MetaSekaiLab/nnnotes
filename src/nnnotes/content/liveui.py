"""Live start-canvas paths and localized strings from explicit master records."""

from __future__ import annotations

from ..source import languages

SCENE_KEY = "EmbScene/Live"
CANVAS = "Live/UILiveStartCanvas"
CONTENT = f"{CANVAS}/root/UISafeArea/ContentArea"
# Parts drawn in LightWeight mode: root/base (a _simpleModeOnlyObjects entry), center_bottom and
# simple_music_jacket_panel (left_top is _normalModeOnlyObjects, right_top/battle_panel is _battleLiveGameObjects).
DRAWN = (f"{CANVAS}/root/base", f"{CONTENT}/center_bottom", f"{CONTENT}/simple_music_jacket_panel")
# App.LiveBase.LiveDifficulty
LIVE_DIFFICULTY = {"easy": 0, "normal": 1, "hard": 2, "expert": 3, "master": 4}
JAPANESE_COLUMN = languages.column("ja")


def _drawn(path: str) -> bool:
    return any(path == d or path.startswith(d + "/") for d in DRAWN)


def record_strings(tables: dict, music_id: int, difficulty: str, column: str) -> tuple[dict, dict]:
    """The live start strings and their sources from explicit master-table rows."""
    music = next(r for r in tables["MasterLiveMusic"] if r["_id"] == music_id)
    text = {r["_id"]: r for r in tables["MasterText"]}

    def localized(tid: str) -> str:
        # MasterLoader.GetLocalizedText in the current LanguageMode: the column as it is, "" included (no
        # fallback language); LocalizedTextTokenResolver.Resolve returns an empty text unchanged
        return text[tid][column]

    def word(tid: str) -> str:
        # MasterLiveMusic.get_LyricistText / get_ComposerText / get_ArrangerText
        # -> LocalizeManager.GetLocalizedText: an empty id is returned as it is, else the loader's text
        return localized(tid) if tid else tid

    def credit(label_id: str, word_id: str) -> str:
        # LocalizeText.RegisterReplacementWord("{0}", word) stores the word and re-runs SetMasterTextId: an empty
        # label text goes to the text as it is, otherwise String.Replace(key, word) for each registered word
        # (ordinal, every occurrence; an empty word leaves "編曲:"). LocalizedTextTokenResolver.Resolve knows no
        # "{0}" token. Initialize and FitUI test no text for emptiness.
        label = localized(label_id)
        return label.replace("{0}", word(word_id)) if label else label

    # SongTitleDisplaySetting._useTranslation false (option 999 unset, GetBool default false):
    # MasterLiveMusic.TitleText = loader.Get(_titleTextID, Japanese)
    title = text[music["_titleTextID"]][JAPANESE_COLUMN]
    # GetBandNameTextId: _bandNameTextID, else MasterBand(_bandIDs[0])._nameTextID
    band_tid = music["_bandNameTextID"]
    if not band_tid:
        band = next(r for r in tables["MasterBand"] if r["_id"] == music["_bandIDs"][0])
        band_tid = band["_nameTextID"]
    score_id = music[f"_{difficulty}ID"]
    score = next(r for r in tables["MasterLiveMusicScore"] if r["_id"] == score_id)
    strings = {
        "musicTitle": title,
        "singerName": localized(band_tid),
        "lyricsWriter": credit("ui_credit_lyrics", music["_lyricistTextID"]),
        "composer": credit("ui_credit_composer", music["_composerTextID"]),
        "arranger": credit("ui_credit_arranger", music["_arrangerTextID"]),
        # MasterLiveMusicScore.get_MusicScoreLevel (_musicScoreLevel), UIText.SetText(int)
        "musicLevel": str(int(score["_musicScoreLevel"])),
        # Player.GetLiveMusicResult: a fresh profile has no entry -> new LiveMusicResult(id), get_HighScore = 0.
        # The IFix patch checks inside these getters (patch ids 0x13e0, 0x1b5b) depend on patches loaded at run
        # time; the code assumes none is loaded
        "highScore": "0",
    }
    sources = {
        "musicTitle": {
            "textId": music["_titleTextID"],
            "column": JAPANESE_COLUMN,
            "why": "SongTitleDisplaySetting._useTranslation false (option 999 default)",
        },
        "singerName": {"textId": band_tid, "column": column},
        "lyricsWriter": {"label": "ui_credit_lyrics", "word": music["_lyricistTextID"], "column": column},
        "composer": {"label": "ui_credit_composer", "word": music["_composerTextID"], "column": column},
        "arranger": {"label": "ui_credit_arranger", "word": music["_arrangerTextID"], "column": column},
        "musicLevel": {"MasterLiveMusicScore": score_id, "field": "_musicScoreLevel"},
        "highScore": {"why": "fresh profile: no LiveMusicResult entry, HighScore 0"},
    }
    for key in ("lyricsWriter", "composer", "arranger"):
        if not word(sources[key]["word"]):
            sources[key]["emptyWord"] = True  # the label is shown without the word
    return strings, sources


# UILiveStartCanvas field -> strings key (UIText fields and arrays set by Initialize)
TEXT_FIELDS = {
    "_musicTitleText": "musicTitle",
    "_singerText": "singerName",
    "_lyricistText": "lyricsWriter",
    "_compositterText": "composer",
    "_compositterText2": "composer",
    "_arrangerText": "arranger",
    "_musicLevelTexts": "musicLevel",
    "_highScoreTexts": "highScore",
}
