"""Regions and languages: settings, region groups, localized listing texts, the multi-region chart index."""
import json
import re
from pathlib import Path

import pytest

from nnnotes import cli, languages, liveui, score, web
from nnnotes.config import Config, ConfigError

LANGS = list(languages.LANGUAGES)


def text_row(tid, base):
    return {"_id": tid, **{col: f"{base}-{code}" for code, (_, col) in languages.LANGUAGES.items()}}


def write_master(d: Path, *, title="Song", extra_music=None, shop=0):
    """A decoded master dir with one music (id 1, expert and easy scores) and the tables the chart build reads."""
    d.mkdir(parents=True, exist_ok=True)
    music = {"_id": 1, "_titleTextID": "t1", "_bandIDs": [1], "_bandNameTextID": "", "_easyID": 11, "_normalID": 0,
             "_hardID": 0, "_expertID": 14, "_musicSoundID": 5, "_jingleSoundID": 0, "_lyricistTextID": "w1",
             "_composerTextID": "", "_arrangerTextID": "w2", "_sortOrder": 3, **(extra_music or {})}
    tables = {
        "MasterLiveMusic": [music],
        "MasterLiveMusicScore": [{"_id": 11, "_musicScoreLevel": 5}, {"_id": 14, "_musicScoreLevel": 25}],
        "MasterText": [text_row("t1", title), text_row("b1", "Band"), text_row("w1", "Lyricist"),
                       text_row("w2", "Arranger"),
                       {"_id": "ui_credit_lyrics", **{c: "L:{0}" for _, c in languages.LANGUAGES.values()}},
                       {"_id": "ui_credit_composer", **{c: "C:{0}" for _, c in languages.LANGUAGES.values()}},
                       {"_id": "ui_credit_arranger", **{c: "" for _, c in languages.LANGUAGES.values()}}],
        "MasterBand": [{"_id": 1, "_nameTextID": "b1"}],
        "MasterSound": [{"_id": 5, "_soundCueSheetID": 7, "_cueName": "bgm"}],
        "MasterSoundCueSheet": [{"_id": 7, "_cueSheetName": "sheet"}],
        "MasterShop": [{"_id": i} for i in range(shop)],
    }
    for name, rows in tables.items():
        (d / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return d


def fake_player(root: Path) -> Path:
    for rel, text in {web.READ_SET_SCRIPT: "// read set", web.PLAYER_BUNDLES[0]: "/* bundle */",
                      f"{web.PLAYER_PAGE_DIR}/index.html": "<!-- @PLAYER_VERSION@ -->"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def config(tmp_path, servers: dict, paths=None, catalog=None, overrides=None) -> Config:
    data = {"servers": servers, "paths": paths or {}, "catalog": catalog or {}}
    return Config(data, base=tmp_path, environ={}, overrides=overrides)


# ---------------------------------------------------------------- languages
def test_language_table():
    assert LANGS == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    assert [languages.mode(c) for c in LANGS] == [0, 1, 2, 3, 4]
    assert languages.column("zh-Hans") == "_simplifiedChinese"
    assert languages.texts({"_japanese": "a", "_korean": "b"}) == {"ja": "a", "en": None, "zh-Hant": None,
                                                                   "zh-Hans": None, "ko": "b"}
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
    assert strings["musicTitle"] == "Song-ja"                   # the game shows the Japanese title by default
    assert strings["singerName"] == f"Band-{code}"
    assert strings["lyricsWriter"] == f"L:Lyricist-{code}"
    assert strings["composer"] == "C:"                          # empty word: the label alone
    assert strings["arranger"] == ""                            # empty label: as it is
    assert strings["musicLevel"] == "25"
    assert sources["singerName"]["column"] == languages.column(code)


# ---------------------------------------------------------------- settings
def test_master_setting_per_region(tmp_path):
    for n in ("p", "a", "b", "flag"):
        (tmp_path / n).mkdir()
    servers = {"a": {"cdn": "x", "master": "a"}, "b": {"cdn": "y"}}
    cfg = config(tmp_path, servers, paths={"master": "p"}, catalog={"region": "a"})
    assert cfg.master("a") == ("servers.a", "master") and cfg.master("b") == ("paths", "master")
    assert cli.master_dir(cfg) == tmp_path / "a"                # [catalog] region
    assert cli.master_dir(cfg, "b") == tmp_path / "p"
    flagged = config(tmp_path, servers, paths={"master": "p"}, overrides={("paths", "master"): str(tmp_path / "flag")})
    assert flagged.origin("paths", "master") == "flag"
    assert cli.master_dir(flagged, "a") == tmp_path / "flag"   # the flag names the directory of this run
    none = config(tmp_path, {"a": {"cdn": "x"}})
    with pytest.raises(ConfigError, match="paths.master"):
        cli.master_dir(none, "a")
    env = Config({"servers": {"a": {"cdn": "x"}}}, environ={"NNNOTES_SERVERS_A_MASTER": str(tmp_path / "a")})
    assert cli.master_dir(env, "a") == tmp_path / "a"


def test_site_regions(tmp_path):
    cfg = config(tmp_path, {"tw": {"cdn": "x"}, "en": {"cdn": "y"}, "kr": {"cdn": "z"}}, catalog={"region": "en"})
    assert web.site_regions(cfg) == ["en"]
    assert web.site_regions(cfg, ["kr", "tw", "kr"]) == ["kr", "tw"]
    assert web.site_regions(cfg, None, all_regions=True) == ["tw", "en", "kr"]
    with pytest.raises(ConfigError, match="servers.jp.cdn"):
        web.site_regions(cfg, ["jp"])
    with pytest.raises(ConfigError, match="catalog.region"):
        web.site_regions(config(tmp_path, {"tw": {"cdn": "x"}}))


def test_region_masters(tmp_path):
    for n in ("p", "a", "b"):
        (tmp_path / n).mkdir()
    servers = {"a": {"cdn": "x", "master": "a"}, "b": {"cdn": "y"}, "c": {"cdn": "z"}}
    cfg = config(tmp_path, servers, paths={"master": "p"})
    assert web.region_masters(cfg, ["a", "b"]) == {"a": tmp_path / "a", "b": tmp_path / "p"}
    with pytest.raises(ConfigError, match="servers.c.master") as e:
        web.region_masters(cfg, ["b", "c"])                     # two regions on [paths] master
    assert "NNNOTES_SERVERS_C_MASTER" in str(e.value)
    flagged = config(tmp_path, servers, overrides={("paths", "master"): str(tmp_path / "p")})
    assert web.region_masters(flagged, ["a"]) == {"a": tmp_path / "p"}
    with pytest.raises(ConfigError, match="--master names one directory"):
        web.region_masters(flagged, ["a", "b"])


# ---------------------------------------------------------------- region groups
def test_region_groups_follow_the_chart_tables(tmp_path):
    a = write_master(tmp_path / "a")
    b = write_master(tmp_path / "b", shop=3)                   # a table the chart build does not read
    c = write_master(tmp_path / "c", title="Other")            # a text the chart build reads
    assert web.chart_inputs(a) == web.chart_inputs(b) != web.chart_inputs(c)
    assert web.region_groups({"kr": c, "tw": a, "en": b, "x": c}) == [["kr", "x"], ["tw", "en"]]


def test_live_tables_cover_the_chart_build():
    src = Path(web.__file__).parent
    named = set()
    for mod in ("live", "liveaudio", "livescene", "livenotes", "liveui", "score"):
        named |= set(re.findall(r'"(Master[A-Za-z]+)"', (src / f"{mod}.py").read_text(encoding="utf-8")))
    assert named <= set(web.LIVE_TABLES), sorted(named - set(web.LIVE_TABLES))


def test_group_pairs(tmp_path):
    m = write_master(tmp_path / "m")
    assert web._group_pairs(None, m) == [(1, "easy"), (1, "expert")]
    assert web._group_pairs([(1, "expert"), (2, "easy"), (1, "hard")], m) == [(1, "expert")]


# ---------------------------------------------------------------- listing texts and index
def test_localized_texts():
    master = {"title": {"id": "t", "ja": "曲", "en": "Song", "zh-Hant": None, "ko": 3},
              "bands": [{"name": {"ja": "A", "en": "A"}}, {"name": {"ja": "B", "en": None}}]}
    assert web.localized_texts(master) == ({"ja": "曲", "en": "Song"}, {"ja": ["A", "B"]})
    assert web.localized_texts({}) == ({}, {c: [] for c in LANGS})


def chart_manifest(site: Path, music_id, difficulty, files, *, prefix="", regions=None, titles=None, language="en"):
    store = web.Store(site)
    doc = {"format": web.SITE_FORMAT, "musicId": music_id, "difficulty": difficulty, "audio": True,
           "audioFormat": "aac", "flows": ["direct"], "files": {p: store.put(p, b) for p, b in files.items()},
           "chart": {"title": (titles or {}).get(language, "t"), "language": language, "titles": titles or {},
                     "bandNames": {}, "level": 1}}
    if regions:
        doc["regions"] = regions
    path = web.manifest_file(site, f"{music_id}_{difficulty}", prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(web._dump(doc))
    return path


def test_index_with_regions_and_languages(tmp_path):
    chart_manifest(tmp_path, 1, "expert", {"live.json": b"{}"}, regions=["tw", "en"],
                   titles={"ko": "노래", "ja": "曲", "en": "Song"})
    chart_manifest(tmp_path, 1, "expert", {"live.json": b"[1]"}, prefix="kr/", regions=["kr"],
                   titles={"ja": "曲", "xx": "?"})
    chart_manifest(tmp_path, 1, "easy", {"live.json": b"{}"}, regions=["tw"])
    meta = [{"id": "tw", "name": "Region A", "languages": ["zh-Hant", "en"]}, {"id": "en", "name": "en"}]
    r = web.write_index(tmp_path, "zh-Hant", meta)
    assert (r["charts"], r["assets"]) == (3, 2)
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert index["language"] == "zh-Hant"
    assert index["languages"] == ["ja", "en", "ko", "xx"]
    assert index["regions"] == meta
    assert [(c["id"], c["manifest"], c["regions"]) for c in index["charts"]] == [
        ("1_easy", "charts/1_easy.json", ["tw"]), ("1_expert", "charts/1_expert.json", ["tw", "en"]),
        ("1_expert", "charts/kr/1_expert.json", ["kr"])]
    assert index["charts"][1]["titles"]["ko"] == "노래"
    # a later build merges its regions by id; a rewrite without metadata keeps them
    web.write_index(tmp_path, None, [{"id": "kr", "name": "Region C"}, {"id": "tw", "name": "Region A2"}])
    web.write_index(tmp_path)
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert [(g["id"], g["name"]) for g in index["regions"]] == [("tw", "Region A2"), ("en", "en"), ("kr", "Region C")]
    assert index["language"] == "zh-Hant"


def test_index_of_a_site_without_regions_is_unchanged(tmp_path):
    chart_manifest(tmp_path, 2, "hard", {"live.json": b"{}"})
    web.write_index(tmp_path)
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert sorted(index) == ["charts", "format"] and "regions" not in index["charts"][0]


def test_region_manifest_folds_into_the_shared_one(tmp_path):
    chart_manifest(tmp_path, 1, "expert", {"live.json": b"{}"}, regions=["tw"])
    chart_manifest(tmp_path, 1, "expert", {"live.json": b"{}"}, prefix="en/", regions=["en", "kr"])
    chart_manifest(tmp_path, 1, "easy", {"live.json": b"{}"}, regions=["tw"])
    chart_manifest(tmp_path, 1, "easy", {"live.json": b"[2]"}, prefix="en/", regions=["en"])
    assert web.fold_variant(tmp_path, "1_expert", "en/") is True
    assert web.fold_variant(tmp_path, "1_easy", "en/") is False           # other files: stays a region manifest
    assert not web.manifest_file(tmp_path, "1_expert", "en/").exists()
    shared = json.loads(web.manifest_file(tmp_path, "1_expert").read_text(encoding="utf-8"))
    assert shared["regions"] == ["tw", "en", "kr"]
    assert web.add_regions(web.manifest_file(tmp_path, "1_easy"), ["tw"]) is False
    assert web.merge_regions(None, ["a", "b"]) == ["a", "b"] and web.merge_regions(["b"], ["a", "b"]) == ["b", "a"]
    assert [p.relative_to(tmp_path).as_posix() for p in web.chart_manifests(tmp_path)] == [
        "charts/1_easy.json", "charts/1_expert.json", "charts/en/1_easy.json"]


def test_build_serves_regions_from_existing_manifests(tmp_path, monkeypatch):
    """Two regions with the same chart tables share charts/<id>.json; a region with other tables gets its own
    manifest unless the files are the same; a chart no region has fails."""
    masters = {"tw": write_master(tmp_path / "m" / "tw"), "en": write_master(tmp_path / "m" / "en", shop=2),
               "kr": write_master(tmp_path / "m" / "kr", title="Other")}
    servers = {r: {"cdn": r, "master": str(d), "name": r.upper()} for r, d in masters.items()}
    cfg = config(tmp_path, servers, catalog={"region": "tw", "language": "en"})
    site = tmp_path / "site"
    chart_manifest(site, 1, "expert", {"live.json": b"{}"})
    chart_manifest(site, 1, "expert", {"live.json": b"{}"}, prefix="kr/")
    chart_manifest(site, 1, "easy", {"live.json": b"{}"})
    chart_manifest(site, 1, "easy", {"live.json": b"[3]"}, prefix="kr/")
    monkeypatch.setattr(web, "open_data", lambda *a: pytest.fail("nothing to build"))
    r = web.build(site, [(1, "expert"), (1, "easy"), (9, "hard")], cfg, fake_player(tmp_path / "p"),
                  regions=["tw", "en", "kr"])
    assert r["regionGroups"] == [["tw", "en"], ["kr"]]
    assert r["skipped"] == ["1_expert", "1_easy", "kr/1_expert", "kr/1_easy"]
    assert r["foldedRegionManifests"] == ["kr/1_expert"]
    assert [f["id"] for f in r["failed"]] == ["9_hard"] and r["failed"][0]["stage"] == "master"
    index = json.loads((site / "charts.json").read_text(encoding="utf-8"))
    assert [(c["manifest"], c["regions"]) for c in index["charts"]] == [
        ("charts/1_easy.json", ["tw", "en"]), ("charts/kr/1_easy.json", ["kr"]),
        ("charts/1_expert.json", ["tw", "en", "kr"])]
    assert [g["name"] for g in index["regions"]] == ["TW", "EN", "KR"] and index["language"] == "en"
