"""Regional master settings and localized listing sources."""

import json
from pathlib import Path

import pytest

from nnnotes.content import liveui, score
from nnnotes.site import pages
from nnnotes.source import languages
from nnnotes.source.config import Config, ConfigError
from nnnotes.source.session import Session

LANGS = list(languages.LANGUAGES)


def text_row(tid, base):
    return {"_id": tid, **{col: f"{base}-{code}" for code, (_, col) in languages.LANGUAGES.items()}}


def write_master(d: Path, *, title="Song", extra_music=None, shop=0):
    """A decoded master dir with one music (id 1, expert and easy scores) and the tables the chart build reads."""
    d.mkdir(parents=True, exist_ok=True)
    music = {
        "_id": 1,
        "_titleTextID": "t1",
        "_bandIDs": [1],
        "_bandNameTextID": "",
        "_easyID": 11,
        "_normalID": 0,
        "_hardID": 0,
        "_expertID": 14,
        "_musicSoundID": 5,
        "_jingleSoundID": 0,
        "_lyricistTextID": "w1",
        "_composerTextID": "",
        "_arrangerTextID": "w2",
        "_sortOrder": 3,
        **(extra_music or {}),
    }
    tables = {
        "MasterLiveMusic": [music],
        "MasterLiveMusicScore": [{"_id": 11, "_musicScoreLevel": 5}, {"_id": 14, "_musicScoreLevel": 25}],
        "MasterText": [
            text_row("t1", title),
            text_row("b1", "Band"),
            text_row("w1", "Lyricist"),
            text_row("w2", "Arranger"),
            {"_id": "ui_credit_lyrics", **{c: "L:{0}" for _, c in languages.LANGUAGES.values()}},
            {"_id": "ui_credit_composer", **{c: "C:{0}" for _, c in languages.LANGUAGES.values()}},
            {"_id": "ui_credit_arranger", **{c: "" for _, c in languages.LANGUAGES.values()}},
        ],
        "MasterBand": [{"_id": 1, "_nameTextID": "b1"}],
        "MasterSound": [{"_id": 5, "_soundCueSheetID": 7, "_cueName": "bgm"}],
        "MasterSoundCueSheet": [{"_id": 7, "_cueSheetName": "sheet"}],
        "MasterShop": [{"_id": i} for i in range(shop)],
    }
    for name, rows in tables.items():
        (d / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return d


def config(tmp_path, servers: dict, paths=None, catalog=None, overrides=None) -> Config:
    data = {"servers": servers, "paths": paths or {}, "catalog": catalog or {}}
    return Config(data, base=tmp_path, environ={}, overrides=overrides)


def test_language_table():
    assert LANGS == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    assert [languages.mode(c) for c in LANGS] == [0, 1, 2, 3, 4]
    assert languages.column("zh-Hans") == "_simplifiedChinese"
    assert languages.texts({"_japanese": "a", "_korean": "b"}) == {
        "ja": "a",
        "en": None,
        "zh-Hant": None,
        "zh-Hans": None,
        "ko": "b",
    }
    with pytest.raises(ConfigError) as e:
        languages.check("xx-Secret")
    assert "catalog.language" in str(e.value) and "xx-Secret" not in str(e.value)


def test_music_rows_have_every_language(tmp_path):
    rows = score.music_rows(write_master(tmp_path / "m"), 1)
    assert rows["title"] == {"id": "t1", **{c: f"Song-{c}" for c in LANGS}}
    assert rows["bands"][0]["name"] == {"id": "b1", **{c: f"Band-{c}" for c in LANGS}}


@pytest.mark.parametrize("code", LANGS)
def test_start_canvas_strings_follow_the_language(tmp_path, code):
    strings, sources = liveui.resolve_strings(write_master(tmp_path / "m"), 1, "expert", languages.column(code))
    assert strings["musicTitle"] == "Song-ja"  # the game shows the Japanese title by default
    assert strings["singerName"] == f"Band-{code}"
    assert strings["lyricsWriter"] == f"L:Lyricist-{code}"
    assert strings["composer"] == "C:"  # empty word: the label alone
    assert strings["arranger"] == ""  # empty label: as it is
    assert strings["musicLevel"] == "25"
    assert sources["singerName"]["column"] == languages.column(code)


def test_master_setting_per_region(tmp_path):
    for n in ("p", "a", "b", "flag"):
        (tmp_path / n).mkdir()
    servers = {"a": {"cdn": "x", "master": "a"}, "b": {"cdn": "y"}}
    cfg = config(tmp_path, servers, paths={"master": "p"}, catalog={"region": "a"})
    assert cfg.master("a") == ("servers.a", "master") and cfg.master("b") == ("paths", "master")
    assert Session(cfg).master_dir() == tmp_path / "a"  # [catalog] region
    assert Session(cfg).master_dir("b") == tmp_path / "p"
    flagged = config(tmp_path, servers, paths={"master": "p"}, overrides={("paths", "master"): str(tmp_path / "flag")})
    assert flagged.origin("paths", "master") == "flag"
    assert Session(flagged).master_dir("a") == tmp_path / "flag"  # the flag names the directory of this run
    none = config(tmp_path, {"a": {"cdn": "x"}})
    with pytest.raises(ConfigError, match="paths.master"):
        Session(none).master_dir("a")
    env = Config({"servers": {"a": {"cdn": "x"}}}, environ={"NNNOTES_SERVERS_A_MASTER": str(tmp_path / "a")})
    assert Session(env).master_dir("a") == tmp_path / "a"


def test_site_regions(tmp_path):
    cfg = config(tmp_path, {"tw": {"cdn": "x"}, "en": {"cdn": "y"}, "kr": {"cdn": "z"}}, catalog={"region": "en"})
    assert pages.site_regions(cfg) == ["en"]
    assert pages.site_regions(cfg, ["kr", "tw", "kr"]) == ["kr", "tw"]
    assert pages.site_regions(cfg, None, all_regions=True) == ["tw", "en", "kr"]
    with pytest.raises(ConfigError, match="servers.jp.cdn"):
        pages.site_regions(cfg, ["jp"])
    with pytest.raises(ConfigError, match="catalog.region"):
        pages.site_regions(config(tmp_path, {"tw": {"cdn": "x"}}))


def test_region_masters(tmp_path):
    for n in ("p", "a", "b"):
        (tmp_path / n).mkdir()
    servers = {"a": {"cdn": "x", "master": "a"}, "b": {"cdn": "y"}, "c": {"cdn": "z"}}
    cfg = config(tmp_path, servers, paths={"master": "p"})
    assert pages.region_masters(cfg, ["a", "b"]) == {"a": tmp_path / "a", "b": tmp_path / "p"}
    with pytest.raises(ConfigError, match="servers.c.master") as e:
        pages.region_masters(cfg, ["b", "c"])  # two regions on [paths] master
    assert "NNNOTES_SERVERS_C_MASTER" in str(e.value)
    flagged = config(tmp_path, servers, overrides={("paths", "master"): str(tmp_path / "p")})
    assert pages.region_masters(flagged, ["a"]) == {"a": tmp_path / "p"}
    with pytest.raises(ConfigError, match="--master names one directory"):
        pages.region_masters(flagged, ["a", "b"])
