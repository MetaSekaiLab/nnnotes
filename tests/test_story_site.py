"""The story part of a site (storysite.py and its hooks in web.py / cli.py) on synthetic story directories and master
data; nothing here comes from game data."""
import hashlib
import json
import struct
from pathlib import Path

import pytest

from nnnotes import jsonio, storysite, tmpfont, web, webmodel
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


MODEL_M = "Character/Live2D/g/m/model/m"


def story_dirs(root, adv_id=5, mode=1, ui_variant="", models=None):
    """A story directory and two language UI directories as storysite.build_dirs leaves them (`models`: story.json
    models, default the model m)."""
    s = root / "story"
    ep, sc = episode(adv_id, mode), scene()
    write(s / "story.json", {"advId": adv_id, "episode": "episode.json", "scene": "scene.json", "ui": "ui/ui.json",
                             "models": {MODEL_M: "m"} if models is None else models, "audio": {"sheet": "audio/sheet"},
                             "frames": None, "effects": None, "postEffects": None, "stills": None,
                             "talkWindows": None, "chat": None, "videos": "videos/videos.json"})
    write(s / "episode.json", ep)
    write(s / "scene.json", sc)
    write(s / "textures/t.png", b"\x89PNG-t")
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
       "prefix": "", "regions": ["r1"], "encoding": "gzip"}


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
    assert {"videos/videos.json", "videos/v.webm", "textures/t.png"} <= set(common)
    assert not any(p.startswith("live2d/") for p in common)          # the models are the site's
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


def sprite_shader(d):
    """A second UI shader in the directory `d` as text_shaders adds the sprite shader (index in name order, written as
    shader.write_index writes it)."""
    rec = {"name": "TextMeshPro/Sprite", "parsed": "TextMeshPro_Sprite.json", "variants": [
        {"file": "TextMeshPro_Sprite/gles3/v.glsl", "platform": "gles3", "type": "GLES3", "subShader": 0, "pass": 0,
         "stage": "vertex", "keywords": []}]}
    jsonio.write_json(d / "shaders.json", sorted([*json.loads((d / "shaders.json").read_text(encoding="utf-8")), rec],
                                                 key=lambda r: r["name"]))
    write(d / "TextMeshPro_Sprite.json", {"properties": []})
    write(d / "TextMeshPro_Sprite/gles3/v.glsl", GLSL)


def test_share_ui_shaders_gives_every_language_a_shader_one_language_needs(tmp_path):
    dirs = story_dirs(tmp_path)
    ja, en = (dirs["languages"][k] / "ui/shaders" for k in ("ja", "en"))
    sprite_shader(en)                                   # only the English texts draw sprites
    with pytest.raises(RuntimeError, match="some languages only"):
        storysite.collect_story(dirs, True, "aac")
    storysite.share_ui_shaders(dirs["languages"])
    for f in ("shaders.json", "TextMeshPro_Sprite.json", "TextMeshPro_Sprite/gles3/v.glsl"):
        assert (ja / f).read_bytes() == (en / f).read_bytes()
    names = [r["name"] for r in json.loads((ja / "shaders.json").read_text(encoding="utf-8"))]
    assert names == ["S", "TextMeshPro/Sprite"]
    common, per = storysite.collect_story(dirs, True, "aac")
    assert {"ui/shaders/shaders.json", "ui/shaders/TextMeshPro_Sprite.json",
            "ui/shaders/TextMeshPro_Sprite/gles3/v.glsl"} <= set(common)
    assert not any(p.startswith("ui/shaders/") for g in per.values() for p in g)


def test_share_ui_shaders_leaves_one_language_and_equal_languages_alone(tmp_path):
    dirs = story_dirs(tmp_path)
    en = dirs["languages"]["en"] / "ui/shaders"
    sprite_shader(en)
    before = {p: p.read_bytes() for p in en.rglob("*") if p.is_file()}
    storysite.share_ui_shaders({"en": dirs["languages"]["en"]})
    assert {p: p.read_bytes() for p in en.rglob("*") if p.is_file()} == before
    sprite_shader(dirs["languages"]["ja"] / "ui/shaders")
    storysite.share_ui_shaders(dirs["languages"])
    assert {p: p.read_bytes() for p in en.rglob("*") if p.is_file()} == before


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

    def fake_host(cat, master_dir, player, episode, story_dir, language_dirs, groups, *, fonts, font_files, font_of):
        calls.append((episode["advId"], fonts, font_files, font_of("ko")))
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
    assert calls == [(5, "open", {"ja": "font-ja", "en": "font-en"}, "font-ko")]    # font_of: the chains' files
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
    assert man["format"] == storysite.MANIFEST_FORMAT == "ournotes.story-manifest/2"
    assert man["advId"] == 5 and man["regions"] == ["r1"]
    assert man["root"] == "../" and man["models"] == {"m": "models/m.json"}
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
            assert a.split("/")[1].split(".")[0] == hashlib.sha256(web.read_asset(site, a)).hexdigest()
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
    model = {"format": 3, "id": "m", "key": MODEL_M, "model": {},
             "files": {"model.json": store.put("model.json", b'{"m":1}'), "m.moc3": store.put("m.moc3", b"MOC3-m")}}
    (site / "models" / "m.json").write_text(json.dumps(model), encoding="utf-8")
    return web.entry_assets(chart["files"]["live.json"]) + [a for e in model["files"].values()
                                                             for a in web.entry_assets(e)]


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
    assert entry["size"]["models"] == len(b'{"m":1}') + len(b"MOC3-m")       # the referenced model's files


def test_stories_reference_their_model_manifests(tmp_path):
    """The assets of the model manifests a story names count as referenced by the stories (write_index keeps
    them whatever models.json lists); a model manifest that is not there counts no bytes."""
    site, _ = ingest(tmp_path)
    model_assets = set(chart_and_model(site)[1:])
    n, used = storysite.write_stories_index(site)
    assert n == 1 and model_assets <= used
    assert model_assets.isdisjoint(storysite.manifest_assets(json.loads((site / "stories/5.json").read_text(
        encoding="utf-8"))))
    (site / "models/m.json").unlink()
    storysite.write_stories_index(site)
    (entry,) = json.loads((site / "stories.json").read_text(encoding="utf-8"))["stories"]
    assert entry["size"]["models"] == 0


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
    man = json.loads((site / "stories/r3/5.json").read_text(encoding="utf-8"))
    assert man["root"] == "../../" and man["models"] == {"m": "models/m.json"}


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
    # the other languages' files too, when given (the fallback chains of the open font assets)
    assert storysite.font_files(cfg, ["en"], {"en": "f.otf"}) == {"ja": str(f.resolve()), "en": str(f.resolve())}
    with pytest.raises(ConfigError, match="font file of ja not found"):
        storysite.font_files(cfg, ["en"], {"en": "f.otf", "ja": "missing.otf"})
    with pytest.raises(ConfigError, match="needs the font file of ko"):
        storysite.font_file({"fontFiles": {"en": str(f)}}, "ko")
    with pytest.raises(ConfigError, match="not found"):
        storysite.font_files(cfg, ["ko"], {"ko": "missing.otf"})
    assert storysite.emoji_file(cfg) is None                       # optional: the sprites then have no images
    assert storysite.emoji_file(cfg, {"emoji": "f.otf", "en": "x.otf"}) == str(f.resolve())
    assert storysite.emoji_file(Config({"paths": {"fonts": {"emoji": str(f)}}}, environ={})) == str(f.resolve())
    with pytest.raises(ConfigError, match="emoji font file not found"):
        storysite.emoji_file(cfg, {"emoji": "missing.ttf"})
    assert storysite.font_files(cfg, ["en"], {"en": "f.otf", "emoji": "missing.ttf"})["en"] == str(f.resolve())
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


# ---------------------------------------------------------------- the models of the stories
MODEL_A, MODEL_B = "Character/Live2D/g/a/model/a", "Character/Live2D/g/b/model/b"


def put_model(site, key, motion_sync=False):
    """A model manifest as webmodel.ingest writes it (made-up model.json and moc3)."""
    mid = webmodel.model_id(key)
    store = web.Store(site)
    doc = {"format": webmodel.MODEL_FORMAT, "key": key, "motionSync": motion_sync}
    man = {"format": web.MANIFEST_FORMAT, "id": mid, "key": key, "model": {},
           "files": {"model.json": store.put("model.json", json.dumps(doc).encode()),
                     f"{mid}.moc3": store.put(f"{mid}.moc3", b"MOC3-" + mid.encode())}}
    write(site / "models" / f"{mid}.json", web._dump(man))
    return {"id": mid, "ok": True, "files": 2, "bytes": sum(e["size"] for e in man["files"].values())}


@pytest.fixture
def story_build(tmp_path, monkeypatch):
    """storysite.build over synthetic master data (MasterAdv 5, 6, 7, 8) and episodes: episode_models gives the
    models each episode loads, build_dirs writes the story directory with the models it takes from the site
    (webmodel.SiteModels, as story.build does), webmodel's model task writes a model manifest. -> run(ids, **kw),
    with the calls in run.episodes (pre-pass) and run.models (model builds)."""
    uses = {5: [MODEL_A], 6: [MODEL_B, MODEL_A], 7: [], 8: [MODEL_A]}
    md = tmp_path / "master"
    write(md / "MasterAdv.json", {"_allData": [{"_id": i} for i in uses]})
    font = tmp_path / "f.otf"
    font.write_bytes(b"font")
    cfg = Config({"catalog": {"region": "zz", "language": "en"}, "servers": {"zz": {"cdn": "https://cdn.invalid"}},
                  "paths": {"master": str(md), "fonts": {"ja": str(font), "en": str(font)}}}, environ={})
    site = tmp_path / "site"
    episodes, models = [], []

    def episode_models(cat, master_dir, adv_id):
        episodes.append(adv_id)
        return sorted(uses[adv_id])

    def build_dirs(cat, master_dir, player, adv_id, work, job):
        src = webmodel.SiteModels(job["site"])
        return story_dirs(work, adv_id, mode=0, models={k: src.ensure(k)["id"] for k in uses[adv_id]})

    def model_task(mid, key, job, data=None):
        models.append(mid)
        return put_model(Path(job["site"]), key)
    monkeypatch.setattr(tmpfont, "require_extra", lambda what=None: None)
    monkeypatch.setattr(storysite, "open_data", lambda cfg, region=None: (None, md, None))
    monkeypatch.setattr(storysite, "episode_models", episode_models)
    monkeypatch.setattr(storysite, "build_dirs", build_dirs)
    monkeypatch.setattr(storysite.storyhost, "build", lambda *a, **kw: None)
    monkeypatch.setattr(storysite, "font_file", lambda job, lang: None)
    monkeypatch.setattr(webmodel, "model_task", model_task)
    monkeypatch.setattr(webmodel, "_open", lambda cfg, region=None: (None, None))
    player = fake_player(tmp_path / "p")

    def run(ids, **kw):
        return storysite.build(site, ids, cfg, player, audio=False, workers=1, story_languages=["ja", "en"], **kw)
    run.site, run.episodes, run.models = site, episodes, models
    return run


def manifest(site, adv_id):
    return json.loads((site / "stories" / f"{adv_id}.json").read_text(encoding="utf-8"))


def test_story_build_builds_the_models_of_its_stories_first(story_build):
    site = story_build.site
    r = story_build([5, 6, 7])
    assert r["storiesFailed"] == [] and [s["id"] for s in r["storiesBuilt"]] == ["5", "6", "7"]
    assert sorted(story_build.episodes) == [5, 6, 7]
    assert story_build.models == ["a", "b"]                       # each model once, whatever the stories using it
    assert [m["id"] for m in r["storyModels"]["modelsBuilt"]] == ["a", "b"] and r["models"] == 2
    index = json.loads((site / "models.json").read_text(encoding="utf-8"))
    assert [m["id"] for m in index["models"]] == ["a", "b"]
    assert manifest(site, 5)["models"] == {"a": "models/a.json"}
    assert manifest(site, 6)["models"] == {"a": "models/a.json", "b": "models/b.json"}
    assert manifest(site, 7)["models"] == {} and manifest(site, 7)["root"] == "../"
    assert json.loads(web.entry_text(site, manifest(site, 6)["files"]["story.json"]))["models"] == {
        MODEL_B: "b", MODEL_A: "a"}
    stories = {e["id"]: e for e in json.loads((site / "stories.json").read_text(encoding="utf-8"))["stories"]}
    size = {m: sum(e["size"] for e in json.loads((site / f"models/{m}.json").read_text(encoding="utf-8"))
                   ["files"].values()) for m in ("a", "b")}
    assert (stories["5"]["size"]["models"], stories["6"]["size"]["models"], stories["7"]["size"]["models"]) == (
        size["a"], size["a"] + size["b"], 0)
    for mf in (site / "models").glob("*.json"):                   # write_index kept the models' assets
        for e in json.loads(mf.read_text(encoding="utf-8"))["files"].values():
            assert all((site / a).is_file() for a in web.entry_assets(e))


def test_story_build_skips_existing_models_unless_forced(story_build):
    story_build([5])
    assert story_build.models == ["a"]
    # stories whose manifest exists: no pre-pass, no model build
    r = story_build([5])
    assert r["storiesSkipped"] == ["5"] and r["storyModels"] is None and story_build.episodes == [5]
    # a new story whose model exists: the model manifest is skipped
    r = story_build([8])
    assert story_build.models == ["a"] and r["storyModels"]["modelsSkipped"] == ["a"]
    assert manifest(story_build.site, 8)["models"] == {"a": "models/a.json"}
    # force: the stories and their models built again, each model once
    r = story_build([5, 8, 6], force=True)
    assert story_build.models == ["a", "a", "b"] and r["storyModels"]["modelsSkipped"] == []
    assert sorted(s["id"] for s in r["storiesBuilt"]) == ["5", "6", "8"]


def test_story_build_rebuilds_outdated_models(story_build):
    """A model manifest whose model.json is older than MODEL_FORMAT (no motionSync) is built again by the stories'
    model build without force; a current one is skipped."""
    site = story_build.site
    put_model(site, MODEL_B)                                            # current
    put_model(site, MODEL_A)
    man = json.loads((site / "models/a.json").read_text(encoding="utf-8"))
    man["files"]["model.json"] = web.Store(site).put("model.json", json.dumps({"format": 1, "key": MODEL_A}).encode())
    write(site / "models/a.json", web._dump(man))
    r = story_build([6])
    assert story_build.models == ["a"]
    assert r["storyModels"]["modelsRebuilt"] == [{"id": "a", "reason": "outdated"}]
    assert r["storyModels"]["modelsSkipped"] == ["b"] and [m["id"] for m in r["storyModels"]["modelsBuilt"]] == ["a"]
    assert r["storiesFailed"] == [] and webmodel.outdated(site, "a") is None
    assert manifest(site, 6)["models"] == {"a": "models/a.json", "b": "models/b.json"}

def test_a_story_without_its_model_fails(story_build, monkeypatch):
    monkeypatch.setattr(webmodel, "model_task", lambda mid, key, job, data=None: {
        "id": mid, "ok": False, "stage": "export", "error": "x"})
    r = story_build([5, 7])
    assert r["storyModels"]["modelsFailed"] == [{"id": "a", "stage": "export", "error": "x"}]
    assert [(f["id"], f["stage"]) for f in r["storiesFailed"]] == [("5", "export")]
    assert "no model manifest models/a.json" in r["storiesFailed"][0]["error"]
    assert [s["id"] for s in r["storiesBuilt"]] == ["7"]


def test_cli_web_stories(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    calls = []

    def fake_build(out, ids, cfg, player, fmt, **kw):
        calls.append((ids, fmt, kw))
        return {"site": str(out), "storiesBuilt": [], "storiesFailed": [], "storiesSkipped": []}
    monkeypatch.setattr(storysite, "build", fake_build)
    monkeypatch.setattr(storysite, "unknown_stories", lambda cfg, ids, regions=None: [])
    monkeypatch.setattr(tmpfont, "require_extra", lambda what=None: None)      # the fonts extra is optional
    cli.main(["--apk", str(tmp_path / "base.apk"), "web", str(tmp_path / "s"), "--player",
              str(fake_player(tmp_path / "p")), "--story", "10462", "--story", "7", "--story-languages", "en,ja",
              "--font", "en=a.otf", "--font", "ja=b.otf", "--font", "emoji=e.ttf", "--no-audio", "--workers", "2"])
    ((ids, fmt, kw),) = calls
    assert ids == [10462, 7] and fmt == "aac" and kw["audio"] is False and kw["workers"] == 2
    assert kw["story_languages"] == ["en", "ja"]
    assert kw["fonts_flags"] == {"en": "a.otf", "ja": "b.otf", "emoji": "e.ttf"}
    assert cli.EMOJI_FONT == storysite.EMOJI_FONT
    assert kw["fonts"] == "open" and kw["encoding"] == "gzip"
    assert json.loads(capsys.readouterr().out)["storiesFailed"] == []


def test_cli_web_stories_need_the_fonts_extra(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    monkeypatch.setattr(tmpfont.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["--apk", str(tmp_path / "base.apk"), "web", str(tmp_path / "s"), "--player",
                  str(fake_player(tmp_path / "p")), "--story", "1", "--no-audio"])
    err = capsys.readouterr().err
    assert e.value.code == 2 and "--story / --all-stories" in err and "nnnotes[fonts]" in err


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
