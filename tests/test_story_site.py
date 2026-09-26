"""The story part of a site (storysite.py and its hooks in web.py / cli.py) on synthetic story directories and master
data; nothing here comes from game data."""
import hashlib
import json
import struct

import pytest

from nnnotes import storysite, web
from nnnotes.config import Config, ConfigError


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(delay: int) -> bytes:
    """An MP4 file whose audio track has an edit list starting at media time `delay` (web.mp4_priming)."""
    elst = box(b"elst", b"\0\0\0\0" + struct.pack(">IIiI", 1, 48000, delay, 0x10000))
    return box(b"ftyp", b"M4A \0\0\0\0") + box(b"moov", box(b"trak", box(b"edts", elst)))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else
                     (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)).encode("utf-8"))


SHADERS = [{"name": "S", "parsed": "S.json", "variants": [
    {"file": "S/gles3/a.glsl", "platform": "gles3", "type": "GLES3", "subShader": 0, "pass": 0, "stage": "vertex",
     "keywords": []},
    {"file": "S/gles3/b.glsl", "platform": "gles3", "type": "GLES31", "subShader": 0, "pass": 0, "stage": "vertex",
     "keywords": []},
    {"file": "S/vulkan/c.vkprog", "platform": "vulkan", "type": "SPIRV", "subShader": 0, "pass": 0,
     "stage": "vertex", "keywords": []}]}]
GLSL = "#ifdef VERTEX\n#version 300 es\nvoid main() {}\n#endif\n#ifdef FRAGMENT\n#version 300 es\nvoid main() {}\n#endif\n"


def shader_dir(d):
    write(d / "shaders.json", SHADERS)
    write(d / "S.json", {"properties": []})
    write(d / "S/gles3/a.glsl", GLSL)
    write(d / "S/gles3/b.glsl", GLSL.replace("300 es", "310 es"))
    write(d / "S/vulkan/c.vkprog", b"spirv")


def episode(adv_id=5, mode=1):
    return {"advId": adv_id, "asset": f"adv_{adv_id}", "commandCount": 3,
            "commands": [{"i": 0, "cmd": "Talk", "raw": 2, "TargetName": "x", "VoiceIDs": [1]},
                         {"i": 1, "cmd": "Still", "raw": 30, "IgnoreData": 1},
                         {"i": 2, "cmd": "Bgm", "raw": 15}],
            "text": {}, "sounds": {}, "cuesheets": {}, "videos": {}, "resources": [],
            "master": {"_id": adv_id, "_sheetName": f"sheet_{adv_id}", "_advEpisodeAsset": f"adv_{adv_id}",
                       "_titleTextId": "t", "_rubyTitleTextId": "", "_playbackMode": mode},
            "title": {"japanese": "タイトル", "english": "Title", "traditionalChinese": "", "simplifiedChinese": None,
                      "korean": "제목"}}


def scene():
    return {"player": {"q": 1}, "settings": {"playerSettings": {
        "_initializeEpisodes": [{"Index": 0, "Command": 15}], "_finalizeEpisodes": [{"Index": 0, "Command": 5}]},
        "masterIdSettings": {}}, "stages": {}}


def story_dirs(root, adv_id=5, mode=1, ui_variant=""):
    """A story directory and two language UI directories as storysite.build_dirs leaves them."""
    s = root / "story"
    ep, sc = episode(adv_id, mode), scene()
    write(s / "story.json", {"advId": adv_id, "episode": "episode.json", "scene": "scene.json", "ui": "ui/ui.json",
                             "models": {}, "audio": {"sheet": "audio/sheet"}, "frames": None, "effects": None,
                             "postEffects": None, "stills": None, "talkWindows": None, "chat": None,
                             "videos": "videos/videos.json"})
    write(s / "episode.json", ep)
    write(s / "scene.json", sc)
    write(s / "textures/t.png", b"\x89PNG-t")
    write(s / "live2d/m/m.moc3", b"MOC3")
    write(s / "live2d/m/m.prefab.json", {"nodes": []})
    shader_dir(s / "shaders")
    write(s / "audio/sheet/cues.json", {"cue": {"file": "cue.flac", "sampleRate": 48000, "channels": 1,
                                                "samples": 100}})
    write(s / "audio/sheet/cue.flac", b"fLaC-cue")
    write(s / "audio/sheet/cue.m4a", mp4(1024))
    write(s / "audio/sheet/streams.json", [])
    write(s / "videos/videos.json", {"videos": {}})
    write(s / "videos/v.webm", b"webm")
    langs = {}
    for lang, font in (("ja", "A"), ("en", "B")):
        u = root / "lang" / lang / "ui"
        write(u / "ui.json", {"nodes": [{"path": "p", "textStyle": {"lang": lang + ui_variant}}],
                              "sprites": {"s": 1}, "language": {"mode": lang}})
        write(u / "textures/sprites.png", b"\x89PNG-sprites")
        shader_dir(u / "shaders")
        write(u / "fonts.json", {"format": "ournotes.story-fonts/1", "language": lang})
        write(u / "languages.json", {"format": "ournotes.story-language/1", "language": lang})
        write(u / f"fonts/font_{font}_0.png", b"\x89PNG-" + font.encode())
        langs[lang] = root / "lang" / lang
    return {"story": s, "languages": langs, "episode": ep, "scene": sc}


JOB = {"audio": True, "audioFormat": "aac", "languages": ["ja", "en"], "language": "en", "fonts": "open",
       "prefix": "", "regions": ["r1"]}


def test_collect_story(tmp_path):
    common, per = storysite.collect_story(story_dirs(tmp_path), True, "aac")
    assert "audio/sheet/cue.m4a" in common and "audio/sheet/cue.flac" not in common
    assert "audio/sheet/streams.json" not in common
    cues = json.loads(common["audio/sheet/cues.json"])
    assert cues == {"cue": {"file": "cue.m4a", "sampleRate": 48000, "channels": 1, "samples": 100,
                            "encoderDelay": 1024}}
    assert "shaders/S/gles3/a.glsl" in common
    assert not any(p.endswith((".vkprog", "b.glsl")) for p in [*common, *per["ja"]])
    assert json.loads(common["shaders/shaders.json"])[0]["variants"] == [SHADERS[0]["variants"][0]]
    assert {"videos/videos.json", "videos/v.webm", "live2d/m/m.moc3", "textures/t.png"} <= set(common)
    assert {"ui/textures/sprites.png", "ui/shaders/shaders.json", "ui/shaders/S/gles3/a.glsl"} <= set(common)
    assert set(per["ja"]) == {"ui/ui.json", "ui/fonts.json", "ui/languages.json", "ui/fonts/font_A_0.png"}
    assert set(per["en"]) == {"ui/ui.json", "ui/fonts.json", "ui/languages.json", "ui/fonts/font_B_0.png"}


def test_collect_story_flac(tmp_path):
    common, _ = storysite.collect_story(story_dirs(tmp_path), True, "flac")
    assert common["audio/sheet/cue.flac"] == b"fLaC-cue" and "audio/sheet/cue.m4a" not in common
    assert json.loads(common["audio/sheet/cues.json"])["cue"]["file"] == "cue.flac"
    common, _ = storysite.collect_story(story_dirs(tmp_path / "b"), False, "aac")
    assert not any(p.startswith("audio/") for p in common)


def test_collect_story_refuses_a_ui_file_of_some_languages(tmp_path):
    dirs = story_dirs(tmp_path)
    write(dirs["languages"]["ja"] / "ui/textures/extra.png", b"x")
    with pytest.raises(RuntimeError, match="some languages only"):
        storysite.collect_story(dirs, True, "aac")


def host_files(dirs, kind="home"):
    """What storyhost.build adds to the directories of an Overlay story: host/ in the story directory and ui/simple/
    per language; -> the manifest's host entry."""
    write(dirs["story"] / "host/host.json", {"format": "ournotes.story-host/1", "kind": kind, "ui": "host/ui/ui.json"})
    write(dirs["story"] / "host/ui/ui.json", {"nodes": []})
    for lang, d in dirs["languages"].items():
        write(d / "ui/simple/ui.json", {"nodes": [{"path": "w"}]})
        write(d / "ui/simple/fonts.json", {"format": "ournotes.story-fonts/1", "language": "same"})
        write(d / f"ui/simple/fonts/font_{lang}_0.png", b"\x89PNG-" + lang.encode())
    return {"kind": kind, "doc": "host/host.json", "ui": "ui/simple/ui.json"}


def test_collect_story_host_files(tmp_path):
    dirs = story_dirs(tmp_path)
    host_files(dirs)
    common, per = storysite.collect_story(dirs, True, "aac")
    assert {"host/host.json", "host/ui/ui.json", "ui/simple/ui.json"} <= set(common)     # equal in every language
    for lang in ("ja", "en"):                    # the simple window's fonts are language files even when equal
        assert {"ui/simple/fonts.json", f"ui/simple/fonts/font_{lang}_0.png"} <= set(per[lang])
        assert "ui/simple/ui.json" not in per[lang]


def test_story_task_builds_the_host_with_open_fonts_only(tmp_path, monkeypatch):
    calls = []

    def fake_dirs(cat, master_dir, player, adv_id, work, job):
        return story_dirs(work, adv_id, mode=1 if adv_id == 5 else 0)

    def fake_host(cat, master_dir, player, episode, story_dir, language_dirs, groups, *, fonts, font_files):
        calls.append((episode["advId"], fonts, font_files))
        if episode["master"]["_playbackMode"] != 1:
            return None
        return host_files({"story": story_dir, "languages": language_dirs})
    monkeypatch.setattr(storysite, "build_dirs", fake_dirs)
    monkeypatch.setattr(storysite.storyhost, "build", fake_host)
    monkeypatch.setattr(storysite, "font_file", lambda job, lang: f"font-{lang}")
    (tmp_path / "tmp").mkdir()

    def run(adv_id, site, **job):
        job = {**JOB, "site": str(tmp_path / site), "tmp": str(tmp_path / "tmp"), **job}
        r = storysite.story_task(adv_id, job, data=(None, None, None), groups={})
        assert r["ok"], r
        return json.loads((tmp_path / site / "stories" / f"{adv_id}.json").read_text(encoding="utf-8"))
    man = run(5, "open")
    assert calls == [(5, "open", {"ja": "font-ja", "en": "font-en"})]
    assert man["host"] == {"kind": "home", "doc": "host/host.json", "ui": "ui/simple/ui.json"}
    assert {"host/host.json", "ui/simple/ui.json"} <= set(man["files"])
    assert all("ui/simple/fonts.json" in g["files"] for g in man["languages"].values())
    game = run(5, "game", fonts="game")
    assert len(calls) == 1 and "host" not in game                  # no host data with game fonts
    assert not any(p.startswith(("host/", "ui/simple/")) for p in game["files"])
    normal = run(6, "normal")
    assert calls[-1][0] == 6 and "host" not in normal
    site, _ = ingest(tmp_path, "direct", adv_id=6, mode=0)           # a Normal story: as without the host step
    assert (site / "stories" / "6.json").read_bytes() == (tmp_path / "normal" / "stories" / "6.json").read_bytes()


def test_facts_and_requires():
    ep, sc = episode(), scene()
    assert storysite.run_commands(ep, sc) == ["Bgm", "FadeOut", "Talk"]
    assert storysite.needs_motion_sync(ep)
    ep["commands"][0]["IgnoreLipSync"] = 1
    assert not storysite.needs_motion_sync(ep)
    f = storysite.story_facts(episode(), sc, [], ["ja", "en", "ko"], "en")
    assert f == {"advId": 5, "asset": "adv_5", "sheetName": "sheet_5", "playbackMode": 1,
                 "titles": {"ja": "タイトル", "en": "Title", "ko": "제목"}, "groups": [],
                 "commands": ["Bgm", "FadeOut", "Talk"], "commandCount": 3, "language": "en",
                 "languages": ["ja", "en", "ko"]}


def ingest(tmp_path, site_name="site", adv_id=5, mode=1, job=None, ui_variant=""):
    site = tmp_path / site_name
    dirs = story_dirs(tmp_path / f"work-{site_name}-{adv_id}", adv_id, mode, ui_variant)
    r = storysite.ingest(web.Store(site), site, adv_id, dirs, {**JOB, **(job or {})}, [])
    return site, r


def test_ingest_manifest(tmp_path):
    site, r = ingest(tmp_path)
    man = json.loads((site / "stories" / "5.json").read_text(encoding="utf-8"))
    assert man["format"] == storysite.MANIFEST_FORMAT and man["advId"] == 5 and man["regions"] == ["r1"]
    assert (man["language"], man["audio"], man["audioFormat"], man["fonts"]) == ("en", True, "aac", "open")
    assert man["requires"] == {"commands": ["Bgm", "FadeOut", "Talk"], "cubismCore": True, "motionSync": True}
    assert "substitute" not in man
    assert sorted(man["languages"]) == ["en", "ja"]
    for lang, g in man["languages"].items():
        assert not set(g["files"]) & set(man["files"])
        assert {"ui/ui.json", "ui/fonts.json", "ui/languages.json"} <= set(g["files"])
    # scene.json and ui/ui.json are split per top-level key whatever their size; the parts rebuild the text
    scene_entry = man["files"]["scene.json"]
    assert [p[0] for p in scene_entry["parts"]] == ["player", "settings", "stages"]
    assert web.entry_text(site, scene_entry) == web.text_asset("scene.json", json.dumps(scene()))
    ui_ja, ui_en = man["languages"]["ja"]["files"]["ui/ui.json"], man["languages"]["en"]["files"]["ui/ui.json"]
    shared = {k: a for k, a, _ in ui_ja["parts"]}.items() & {k: a for k, a, _ in ui_en["parts"]}.items()
    assert {k for k, _ in shared} == {"sprites"}                    # the equal part stored once
    for e in [*man["files"].values(), *(e for g in man["languages"].values() for e in g["files"].values())]:
        for a in web.entry_assets(e):
            data = (site / a).read_bytes()
            assert a == f"assets/{hashlib.sha256(data).hexdigest()}{a[a.rindex('.'):]}"
    assert r["size"]["common"] == sum(e["size"] for e in man["files"].values())
    raw = (site / "stories" / "5.json").read_bytes()
    assert raw.endswith(b"}\n") and raw == web._dump(man)            # canonical: sorted keys, indent 1


def test_ingest_normal_story_and_no_audio(tmp_path):
    site, _ = ingest(tmp_path, mode=0, job={"audio": False, "regions": None})
    man = json.loads((site / "stories" / "5.json").read_text(encoding="utf-8"))
    assert man["audioFormat"] is None and "regions" not in man
    assert not any(p.startswith("audio/") for p in man["files"])


def test_ingest_is_deterministic(tmp_path):
    a, _ = ingest(tmp_path, "a")
    b, _ = ingest(tmp_path, "b")
    assert (a / "stories" / "5.json").read_bytes() == (b / "stories" / "5.json").read_bytes()
    assert sorted(p.name for p in (a / "assets").iterdir()) == sorted(p.name for p in (b / "assets").iterdir())


def chart_and_model(site):
    store = web.Store(site)
    (site / "charts").mkdir(parents=True, exist_ok=True)
    (site / "models").mkdir(parents=True, exist_ok=True)
    chart = {"musicId": 1, "difficulty": "easy", "audio": True, "audioFormat": "aac", "flows": ["direct"],
             "files": {"live.json": store.put("live.json", b'{"c":1}')}, "chart": {"title": "t"}}
    (site / "charts" / "1_easy.json").write_text(json.dumps(chart), encoding="utf-8")
    model = {"format": 2, "id": "m", "key": "Character/Live2D/g/m/model/m", "model": {},
             "files": {"model.json": store.put("model.json", b'{"m":1}')}}
    (site / "models" / "m.json").write_text(json.dumps(model), encoding="utf-8")
    return web.entry_assets(chart["files"]["live.json"]) + web.entry_assets(model["files"]["model.json"])


def referenced(site) -> set:
    used = set()
    for p in [*sorted((site / "charts").glob("*.json")), *sorted((site / "models").glob("*.json"))]:
        for e in json.loads(p.read_text(encoding="utf-8"))["files"].values():
            used.update(web.entry_assets(e))
    for p in storysite.story_manifests(site):
        used |= storysite.manifest_assets(json.loads(p.read_text(encoding="utf-8")))
    return used


def test_write_index_keeps_every_kind(tmp_path):
    """Charts, models and stories in one site: every write_index (the end of a chart, model or story build) keeps
    what any manifest references, common and language files alike, and removes the rest."""
    site, _ = ingest(tmp_path)
    chart_and_model(site)
    stale = web.Store(site).put("old.json", b"[0]")
    used = referenced(site)
    lang_only = storysite.manifest_assets(json.loads((site / "stories/5.json").read_text(encoding="utf-8")))
    assert used > lang_only > set()
    r = web.write_index(site)
    assert (r["charts"], r["models"], r["stories"], r["removedAssets"]) == (1, 1, 1, 1)
    assert not (site / stale["asset"]).exists()
    for _ in range(2):                                             # rerun: a later build of any kind
        web.write_index(site)
        assert {f"assets/{p.name}" for p in (site / "assets").iterdir()} == used
    index = json.loads((site / "stories.json").read_text(encoding="utf-8"))
    (entry,) = index["stories"]
    assert index["format"] == storysite.STORIES_FORMAT and index["languages"] == ["ja", "en", "ko"]
    assert (entry["id"], entry["manifest"], entry["advId"], entry["regions"]) == ("5", "stories/5.json", 5, ["r1"])
    assert entry["size"]["languages"].keys() == {"ja", "en"} and entry["fonts"] == "open"


def test_story_only_site_and_sites_without_stories(tmp_path):
    site = tmp_path / "charts-only"
    kept = chart_and_model(site)
    r = web.write_index(site)
    assert "stories" not in r and not (site / "stories.json").exists()
    assert {f"assets/{p.name}" for p in (site / "assets").iterdir()} == set(kept)
    site, _ = ingest(tmp_path, "stories-only")
    r = web.write_index(site)
    assert (r["charts"], r["models"], r["stories"], r["removedAssets"]) == (0, 0, 1, 0)


def test_write_stories_index_meta(tmp_path):
    site, _ = ingest(tmp_path)
    storysite.write_stories_index(site, "ja", [{"id": "r1", "name": "One"}])
    storysite.write_stories_index(site)                             # keeps the meta of the existing index
    index = json.loads((site / "stories.json").read_text(encoding="utf-8"))
    assert index["language"] == "ja" and index["regions"] == [{"id": "r1", "name": "One"}]


def test_fold_variant(tmp_path):
    site, _ = ingest(tmp_path, job={"regions": ["r1"]})
    dirs = story_dirs(tmp_path / "w2")
    storysite.ingest(web.Store(site), site, 5, dirs, {**JOB, "prefix": "r2/", "regions": ["r2"]}, [])
    assert storysite.fold_variant(site, 5, "r2/")
    assert not (site / "stories/r2/5.json").exists()
    assert json.loads((site / "stories/5.json").read_text(encoding="utf-8"))["regions"] == ["r1", "r2"]
    dirs = story_dirs(tmp_path / "w3", ui_variant="x")
    storysite.ingest(web.Store(site), site, 5, dirs, {**JOB, "prefix": "r3/", "regions": ["r3"]}, [])
    assert not storysite.fold_variant(site, 5, "r3/") and (site / "stories/r3/5.json").exists()


def test_story_groups(tmp_path):
    md = tmp_path / "master"

    def table(name, rows):
        write(md / f"{name}.json", {"_allData": rows})

    def text(i, ja, en):
        return {"_id": i, "_japanese": ja, "_english": en, "_traditionalChinese": "", "_simplifiedChinese": None,
                "_korean": None}
    table("MasterText", [text("C1", "章", "Chapter"), text("N1", "あ", "A"), text("N2", "い", "B"),
                         text("S1", "店", "Shop")])
    table("MasterCharacter", [{"_id": 1, "_nameTextID": "N1"}, {"_id": 2, "_nameTextID": "N2"}])
    table("MasterStoryChapter", [{"_id": 10, "_nameTextId": "C1", "_bandId": 3, "_isSpecialStory": False}])
    table("MasterStoryEpisode", [{"_id": 101, "_advId": 7, "_chapterId": 10, "_episodeNumber": 2,
                                  "_characterId": 0}])
    table("MasterCharacterFriendship", [{"_id": 12, "_masterCharacterIdA": 1, "_masterCharacterIdB": 2}])
    table("MasterStoryFriendshipEpisode", [{"_id": 1, "_advId": 8, "_characterFriendshipId": 12,
                                            "_episodeNumber": 1}])
    table("MasterHomeSpot", [{"_id": 50, "_nameTextId": "S1", "_advId": 9}])
    table("MasterStoryHomeSpotTapTalkEpisode", [{"_id": 5001, "_advId": 7, "_spotId": 50, "_characterId": 2}])
    g = storysite.story_groups(md)
    assert g[7] == [{"kind": "chapter", "id": 101, "episodeNumber": 2,
                     "chapter": {"id": 10, "names": {"ja": "章", "en": "Chapter"}, "bandId": 3, "special": False},
                     "characters": []},
                    {"kind": "spotTalk", "id": 5001, "spot": {"id": 50, "names": {"ja": "店", "en": "Shop"}},
                     "characters": [{"id": 2, "names": {"ja": "い", "en": "B"}}]}]
    assert [c["id"] for c in g[8][0]["characters"]] == [1, 2] and g[8][0]["kind"] == "friendship"
    assert g[9] == [{"kind": "spot", "id": 50, "spot": {"id": 50, "names": {"ja": "店", "en": "Shop"}},
                     "characters": []}]


def test_region_groups_and_inputs(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d, text in ((a, "x"), (b, "x"), (c, "y")):
        write(d / "MasterAdv.json", {"_allData": [{"_id": 1}]})
        write(d / "MasterText.json", {"_allData": [{"_id": text}]})
        write(d / "MasterLiveMusic.json", {"_allData": [{"_id": text}]})    # not a story input
    write(b / "MasterLiveMusic.json", {"_allData": []})
    assert storysite.region_groups({"a": a, "b": b, "c": c}) == [["a", "b"], ["c"]]
    assert storysite.all_stories(a) == [1]


def test_font_files(tmp_path, monkeypatch):
    f = tmp_path / "f.otf"
    f.write_bytes(b"font")
    cfg = Config({"paths": {"fonts": {"ja": str(f)}}}, environ={})
    assert storysite.font_files(cfg, ["ja"]) == {"ja": str(f.resolve())}
    with pytest.raises(ConfigError, match="--font en=PATH"):
        storysite.font_files(cfg, ["ja", "en"])
    monkeypatch.chdir(tmp_path)
    assert storysite.font_files(cfg, ["en"], {"en": "f.otf"}) == {"en": str(f.resolve())}
    with pytest.raises(ConfigError, match="not found"):
        storysite.font_files(cfg, ["ko"], {"ko": "missing.otf"})
    assert storysite.check_languages(None) == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    assert storysite.check_languages(["ko", "ja"]) == ["ja", "ko"]
    with pytest.raises(ValueError):
        storysite.check_languages(["fr"])


def fake_player(root, story_page=True, story_bundle=True):
    files = {web.READ_SET_SCRIPT: "// read set\n", web.PLAYER_BUNDLES[0]: "/* bundle */\n",
             f"{web.PLAYER_PAGE_DIR}/index.html": "<!-- @PLAYER_VERSION@ -->\n",
             f"{web.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n'}
    if story_page:
        files[f"{web.STORY_PAGE_DIR}/index.html"] = '<script type="module" src="./story-list.js"></script>\n'
        files[f"{web.STORY_PAGE_DIR}/story-list.js"] = 'import { x } from "../../src/story/define.js";\n'
    if story_bundle:
        files[web.STORY_BUNDLES[0]] = "/* story */\n"
    for rel, text in files.items():
        write(root / rel, text)
    return root


def test_write_player_story_page(tmp_path):
    site = tmp_path / "site"
    r = web.write_player(site, fake_player(tmp_path / "p"))
    assert r["storyPageFiles"] == 2
    assert (site / "story/story-list.js").read_text(encoding="utf-8") == \
        'import { x } from "./ournotes-player.story.element.min.js";\n'
    assert (site / "story/ournotes-player.story.element.min.js").is_file()
    site2 = tmp_path / "site2"
    r = web.write_player(site2, fake_player(tmp_path / "q", story_bundle=False))
    assert "storyPageFiles" not in r and not (site2 / "story").exists()
    with pytest.raises(ConfigError, match="story page"):
        web.write_player(site2, fake_player(tmp_path / "q", story_bundle=False), stories=True)


def test_cli_web_stories(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    calls = []

    def fake_build(out, ids, cfg, player, fmt, **kw):
        calls.append((ids, fmt, kw))
        return {"site": str(out), "storiesBuilt": [], "storiesFailed": [], "storiesSkipped": []}
    monkeypatch.setattr(storysite, "build", fake_build)
    monkeypatch.setattr(storysite, "unknown_stories", lambda cfg, ids, regions=None: [])
    cli.main(["--apk", str(tmp_path / "base.apk"), "web", str(tmp_path / "s"), "--player",
              str(fake_player(tmp_path / "p")), "--story", "10462", "--story", "7", "--story-languages", "en,ja",
              "--font", "en=a.otf", "--font", "ja=b.otf", "--no-audio", "--workers", "2"])
    ((ids, fmt, kw),) = calls
    assert ids == [10462, 7] and fmt == "aac" and kw["audio"] is False and kw["workers"] == 2
    assert kw["story_languages"] == ["en", "ja"] and kw["fonts_flags"] == {"en": "a.otf", "ja": "b.otf"}
    assert kw["fonts"] == "open"
    assert json.loads(capsys.readouterr().out)["storiesFailed"] == []


@pytest.mark.parametrize("argv, message", [
    (["--story", "1", "--player-only"], "leave out --story"),
    (["--font", "fr=x.otf", "--all-stories"], "expected <language>=<font file>"),
    (["--story-languages", "en,xx", "--all-stories"], "expected languages"),
    (["--story", "1", "--all-stories"], "not allowed with argument"),
])
def test_cli_web_story_usage(tmp_path, capsys, argv, message):
    from nnnotes import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["web", str(tmp_path / "s"), "--player", str(fake_player(tmp_path / "p")), *argv])
    assert e.value.code == 2 and message in capsys.readouterr().err
