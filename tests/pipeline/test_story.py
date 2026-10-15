"""The story products of nnnotes.pipeline.story on synthetic cue sheet decodes and a synthetic CRI Lips library."""

import pytest

from nnnotes.content import storychat, storyepisode, storyscene
from nnnotes.formats import crilips
from nnnotes.pipeline import assemble, contract, cristages, live, story
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.contract import IncompatibleTask
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.live2d import MODEL
from nnnotes.pipeline.stages import Env
from nnnotes.pipeline.story import AssetStage, ChatStage, CriLipsStage, SceneStage, SoundStage, StoryStage
from tests.formats.test_crilips import _elf_with_rodata, _synthetic_version
from tests.helpers import story as sh
from tests.pipeline.test_live import master_record, run, stored, table
from tests.site.test_story_site import mp4

# the streams of a voice sheet as cri.audio lists them: cue v2 has two layers, cue loop loops
STREAMS = [
    {"stream": 1, "name": "v1", "file": "v1.flac", "sampleRate": 48000, "channels": 1, "samples": 100},
    {"stream": 2, "name": "v2", "file": "v2.flac", "sampleRate": 48000, "channels": 1, "samples": 200},
    {"stream": 3, "name": "v2", "file": "v2_3.flac", "sampleRate": 48000, "channels": 1, "samples": 300},
    {"stream": 4, "name": "loop", "file": "loop.flac", "sampleRate": 44100, "channels": 2, "samples": 400,
     "loopStart": 10, "loopEnd": 400},
]  # fmt: skip


def sheet(depot, name, streams=STREAMS) -> tuple[list, dict]:
    """The inputs "acb:<key>" and "sheets" of the cue sheet `name` decoded as `streams` (FLAC bytes per stream)."""
    key = f"Cri/Sound/{name}"
    acb = stored(depot, f"@UTF {name}".encode(), f"acb:{key}")
    files = {}
    for s in streams:
        data = f"fLaC {name} {s['stream']}".encode()
        files[s["file"]] = {"sha256": depot.put(data), "size": len(data), "ext": "flac"}
    body = contract.encode(streams)
    files["streams.json"] = {"sha256": depot.put(body), "size": len(body), "ext": "json"}
    doc = {key: {"acb": acb.sha256, "status": "exported", "files": files}}
    return [acb, stored(depot, contract.encode(doc), assemble.SHEETS_INPUT)], doc


def read_json(depot, art):
    return contract.loads(depot.read(art["content"]["sha256"]))


# ---------------------------------------------------------------- subjects, keys and manifests
def test_a_cue_sheet_is_named_by_its_key():
    stage = SoundStage()
    assert stage.keys("adv_voice_1", None) == ["Cri/Sound/adv_voice_1"]
    assert stage.cue_sheets("adv_voice_1", None) == ["Cri/Sound/adv_voice_1"]
    assert stage.manifest("adv_voice_1") == "cuesheets/adv_voice_1.json"
    assert stage.manifest("a:b") == "cuesheets/a%3Ab.json"
    keys = ["Cri/Sound/se_adv", "Cri/Sound/bgm/x", "Live/MusicScore/1", "Cri/Sound/adv_voice_1"]
    assert stage.select(keys, None) == ["adv_voice_1", "se_adv"]
    for bad in ("", "a/b"):
        with pytest.raises(ValueError, match="not a cue sheet name"):
            stage.keys(bad, None)
    assert stage.normalize(None) == {"audio": "aac"}
    with pytest.raises(ValueError, match="audio 'wav'"):
        stage.normalize({"audio": "wav"})


def test_the_crilips_product_has_one_subject_and_reads_the_library():
    stage = CriLipsStage()
    assert stage.manifest("default") == "crilips/default.json"
    with pytest.raises(ValueError, match="the only subject is default"):
        stage.manifest("other")
    assert stage.APK == ("lib/arm64-v8a/libcri_lips_unity.so",)
    assert stage.select(["Cri/Sound/a"], None) == []


def test_the_scene_product_has_one_subject_its_keys_and_the_graphics_settings():
    stage = SceneStage()
    assert stage.keys("default", None) == storyscene.keys() and len(storyscene.keys()) == 7
    assert stage.builtins("default", None) == ["GraphicsSettings", "PlayerSettings", "QualitySettings"]
    assert stage.manifest("default") == "storyscenes/default.json"
    assert stage.select([*storyscene.keys(), "Adv/Stage/bg"], None) == ["default"]
    assert stage.select(storyscene.keys()[1:], None) == []
    with pytest.raises(ValueError, match="the only subject is default"):
        stage.keys("other", None)


def test_a_story_asset_is_named_by_its_key():
    stage = AssetStage()
    assert stage.keys("Adv/Effect/fx/fx", None) == ["Adv/Effect/fx/fx"]
    assert stage.manifest("Adv/Effect/fx/fx") == "storyassets/Adv~Effect~fx~fx.json"
    assert stage.select(["Adv/Stage/bg", "Adv/Still/st"], None) == []
    with pytest.raises(ValueError, match="not a story asset key"):
        stage.keys("Adv/Stage/a b", None)


def test_cues_take_the_first_stream_of_each_name():
    doc, files = story.cues(STREAMS, ".m4a")
    assert doc == {
        "v1": {"file": "v1.m4a", "sampleRate": 48000, "channels": 1, "samples": 100},
        "v2": {"file": "v2.m4a", "sampleRate": 48000, "channels": 1, "samples": 200},
        "loop": {"file": "loop.m4a", "sampleRate": 44100, "channels": 2, "samples": 400, "loopStart": 10,
                 "loopEnd": 400},
    }  # fmt: skip
    assert files == {"v1.m4a": "v1.flac", "v2.m4a": "v2.flac", "loop.m4a": "loop.flac"}


# ---------------------------------------------------------------- the products
def test_the_sound_product_keeps_the_decoded_flac(tmp_path):
    depot = Depot(tmp_path / "d")
    inputs, doc = sheet(depot, "adv_voice_1")
    _, res, arts = run(depot, SoundStage(), "adv_voice_1", [table(depot), *inputs], {"audio": "flac"})
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "cues.json": "story.sound",
        "loop.flac": "audio.story",
        "objects": "product.objects",
        "v1.flac": "audio.story",
        "v2.flac": "audio.story",
    }
    files = doc["Cri/Sound/adv_voice_1"]["files"]
    for name in ("v1.flac", "v2.flac", "loop.flac"):
        assert arts[name]["content"]["sha256"] == files[name]["sha256"]  # the decode, not a copy
    cues = read_json(depot, arts["cues.json"])
    assert cues == story.cues(STREAMS, ".flac")[0] and "encoderDelay" not in cues["v1"]
    assert read_json(depot, arts["objects"])["objects"] == {}


def test_the_sound_product_in_aac_records_each_encoder_delay(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "d")
    monkeypatch.setattr(cristages, "tool_id", lambda which, exe: f"{which}-fake/1")
    monkeypatch.setattr(story, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(live, "_ffmpeg", lambda: "ffmpeg")
    seen = []

    def encode(ffmpeg, src, dst, fmt):
        seen.append((src.read_bytes(), fmt))
        return mp4(1024 + len(seen))

    monkeypatch.setattr(story, "web_encode", encode)
    inputs, _ = sheet(depot, "adv_voice_1")
    atoms = {"audio.encode": "ffmpeg-fake/1"}
    _, _, arts = run(depot, SoundStage(), "adv_voice_1", [table(depot), *inputs], None, atoms)
    assert sorted(seen) == [(f"fLaC adv_voice_1 {i}".encode(), "aac") for i in (1, 2, 4)]
    cues = read_json(depot, arts["cues.json"])
    assert {c: (e["file"], e["encoderDelay"]) for c, e in cues.items()} == {
        "v1": ("v1.m4a", mp4_delay(depot, arts["v1.m4a"])),
        "v2": ("v2.m4a", mp4_delay(depot, arts["v2.m4a"])),
        "loop": ("loop.m4a", mp4_delay(depot, arts["loop.m4a"])),
    }
    assert {e["encoderDelay"] for e in cues.values()} == {1025, 1026, 1027}
    with pytest.raises(IncompatibleTask, match="audio encoder"):
        run(depot, SoundStage(), "adv_voice_1", [table(depot), *inputs], None, {"audio.encode": "ffmpeg-other/1"})


def mp4_delay(depot, art) -> int:
    from nnnotes.formats.encoders import mp4_priming

    return mp4_priming(depot.read(art["content"]["sha256"]))


def test_a_cue_sheet_that_was_not_decoded_makes_the_sound_product_unsupported(tmp_path):
    depot = Depot(tmp_path / "d")
    inputs, doc = sheet(depot, "adv_voice_1")
    e = doc["Cri/Sound/adv_voice_1"]
    doc["Cri/Sound/adv_voice_1"] = {"acb": e["acb"], "status": "unsupported", "reason": "unsupported.cri.awb_external",
                                    "files": {}}  # fmt: skip
    inputs[-1] = stored(depot, contract.encode(doc), assemble.SHEETS_INPUT)
    _, res, _ = run(depot, SoundStage(), "adv_voice_1", [table(depot), *inputs], {"audio": "flac"})
    (item,) = res["items"]
    assert item["status"] == "unsupported" and item["reason"]["code"] == "unsupported.cri.awb_external"


def test_the_crilips_product_holds_the_descriptor_and_the_weights(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "d")
    ver, rodata, _ = _synthetic_version()
    so = _elf_with_rodata(rodata)
    monkeypatch.setattr(crilips, "SUPPORTED", (ver,))
    lib = stored(depot, so, f"apk:{story.LIBRARY}")
    _, res, arts = run(depot, CriLipsStage(), "default", [table(depot), lib])
    blob, desc = crilips.extract(so, (ver,))
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "crilips.bin": "crilips.weights",
        "crilips.json": "story.crilips",
        "objects": "product.objects",
    }
    assert depot.read(arts["crilips.bin"]["content"]["sha256"]) == blob
    assert read_json(depot, arts["crilips.json"]) == desc and desc["version"] == "9.9.9"


def test_a_library_of_an_unknown_version_makes_the_crilips_product_unsupported(tmp_path):
    depot = Depot(tmp_path / "d")
    lib = stored(depot, _elf_with_rodata(b"\x00" * 64 + b"nothing here"), f"apk:{story.LIBRARY}")
    _, res, arts = run(depot, CriLipsStage(), "default", [table(depot), lib])
    (item,) = res["items"]
    assert item["status"] == "unsupported" and item["reason"]["code"] == "unsupported.crilips.version"
    assert arts == {}


def test_the_crilips_product_needs_the_library_in_the_apk(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"apk": {}, "products": {story.CRILIPS: ["default"]}})
    with pytest.raises(ValueError, match="libcri_lips_unity.so of the APK"):
        CriLipsStage().inputs("default", env)


# ---------------------------------------------------------------- the story product: subjects and inputs
def test_the_story_product_selects_the_episodes_whose_keys_it_holds(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": sh.master_dir(tmp_path)})
    stage = StoryStage()
    assert stage.select(storyepisode.keys(sh.ASSET), env) == ["1", "10"]
    assert stage.select(["Adv/Episode/other/other", "Cri/Sound/x"], env) == ["2"]
    assert stage.select(["Cri/Sound/x"], env) == []
    assert stage.select(storyepisode.keys(sh.ASSET), Env(depot, {"master": None})) == []


def test_the_story_product_names_its_keys_and_manifest(tmp_path):
    env = Env(Depot(tmp_path / "d"), {"master": sh.master_dir(tmp_path)})
    stage = StoryStage()
    assert stage.keys("1", env) == storyepisode.keys(sh.ASSET)
    assert stage.manifest("1") == "stories/1.json"
    for bad in ("", "1a", "-1"):
        with pytest.raises(ValueError, match="not an episode id"):
            stage.manifest(bad)


def test_the_story_master_input_is_the_episode_record(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": sh.master_dir(tmp_path)})
    inp = master_record(env, StoryStage(), "1")
    assert inp.role == assemble.MASTER_INPUT
    record = contract.loads(depot.input_bytes(inp))
    assert record == storyepisode.episode_record(sh.master_tables(), 1)
    assert [r["_id"] for r in record["MasterAdvChat"]] == [7, 9]


# ---------------------------------------------------------------- the story product: assembly
def test_the_story_product_holds_the_episode_and_the_story_document(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": sh.master_dir(tmp_path)})
    stage = StoryStage()
    doc = sh.episode_table(depot)
    objects = stored(depot, contract.encode(doc), "objects")
    _, _, arts = run(depot, stage, "1", [objects, master_record(env, stage, "1")])
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "episode.json": "story.episode",
        "objects": "product.objects",
        "story.json": "story",
    }
    record = storyepisode.episode_record(sh.master_tables(), 1)
    # the stored document is the JSON of the episode: integer keys read back as strings
    episode = contract.loads(
        contract.encode(storyepisode.episode_json(storyepisode.episode(Objects(doc, depot), record)))
    )
    assert read_json(depot, arts["episode.json"]) == episode
    assert all(set(r) == {"kind", "address"} for r in episode["resources"])
    assert read_json(depot, arts["objects"])["objects"] == {}

    story_doc = read_json(depot, arts["story.json"])
    assert story_doc == {
        "format": 1,
        "advId": 1,
        "episode": "episode.json",
        "chats": {"7": {"_id": 7, "_chatWindowAssetName": "win_7", "_chatIconAssetName": "icon_7"}},
        "effects": {},
        "parts": {
            "scene": "storyscenes/default.json",
            "assets": {
                "Adv/Chat/Prefabs/win_7": "storyassets/Adv~Chat~Prefabs~win_7.json",
                "Adv/Stage/st_a/st_a": "storyassets/Adv~Stage~st_a~st_a.json",
                "Adv/Transition/adv_transition_default/x": "storyassets/Adv~Transition~adv_transition_default~x.json",
            },
            "models": {},
            "sounds": {},
            "chat": "storychat/default.json",
            "crilips": None,
        },
    }
    assert story_doc["parts"] == story.part_paths(storyepisode.parts(episode))


# ---------------------------------------------------------------- the chat product
def test_the_chat_product_has_one_default_subject_and_needs_every_shard(tmp_path):
    stage = ChatStage()
    assert stage.keys("default", None) == storychat.keys() == sorted(storychat.SHARDS.values())
    assert stage.manifest("default") == "storychat/default.json"
    assert stage.select(storychat.keys(), None) == ["default"]
    assert stage.select(storychat.keys()[1:], None) == []
    with pytest.raises(ValueError, match="the only subject is default"):
        stage.keys("other", None)
    with pytest.raises(ValueError, match="the only subject is default"):
        stage.manifest("other")


def test_the_chat_product_holds_the_rows_of_the_shards(tmp_path):
    depot = Depot(tmp_path / "d")
    objects = stored(depot, contract.encode(sh.chat_table(depot)), "objects")
    _, _, arts = run(depot, ChatStage(), "default", [objects])
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "chat.json": "story.chat",
        "objects": "product.objects",
    }
    assert read_json(depot, arts["chat.json"]) == {
        "format": 1,
        "text": [{"_id": 1, "_text": "hello"}],
        "sounds": [{"_id": 2, "_cueName": "chat_se"}],
        "cueSheets": [{"_id": 3, "_cueSheetName": "chat_sheet"}],
    }
    assert read_json(depot, arts["objects"])["objects"] == {}


def test_a_chat_shard_without_a_text_asset_has_no_rows(tmp_path):
    depot = Depot(tmp_path / "d")
    objects = stored(depot, contract.encode(sh.chat_table(depot, present=("text",))), "objects")
    _, _, arts = run(depot, ChatStage(), "default", [objects])
    chat = read_json(depot, arts["chat.json"])
    assert chat["text"] == [{"_id": 1, "_text": "hello"}]
    assert chat["sounds"] == [] and chat["cueSheets"] == []


# ---------------------------------------------------------------- the products a story plays with
def test_part_paths_give_the_manifest_of_each_part():
    parts = {
        "assets": ["Adv/Stage/st_a/st_a", "Adv/Still/st"],
        "models": ["Character/Live2D/g/m/model/m"],
        "sounds": ["sheet_a"],
        "chat": True,
        "crilips": False,
    }
    assert story.part_paths(parts) == {
        "scene": "storyscenes/default.json",
        "assets": {
            "Adv/Stage/st_a/st_a": "storyassets/Adv~Stage~st_a~st_a.json",
            "Adv/Still/st": "storyassets/Adv~Still~st.json",
        },
        "models": {"Character/Live2D/g/m/model/m": "models/m.json"},
        "sounds": {"sheet_a": "cuesheets/sheet_a.json"},
        "chat": "storychat/default.json",
        "crilips": None,
    }
    quiet = {**parts, "assets": [], "models": [], "sounds": [], "chat": False, "crilips": True}
    assert story.part_paths(quiet) == {
        "scene": "storyscenes/default.json",
        "assets": {},
        "models": {},
        "sounds": {},
        "chat": None,
        "crilips": "crilips/default.json",
    }


def test_part_products_list_the_subjects_of_each_product_the_story_needs():
    parts = {
        "assets": ["Adv/Stage/z", "Adv/Stage/a"],
        "models": ["Character/Live2D/g/m/model/m"],
        "sounds": ["sheet_a"],
        "chat": True,
        "crilips": True,
    }
    doc = {"parts": story.part_paths(parts)}  # story.json names each part by its manifest
    assert story.part_products(doc) == {
        story.SCENE: ["default"],
        story.ASSET: ["Adv/Stage/a", "Adv/Stage/z"],
        MODEL: ["Character/Live2D/g/m/model/m"],
        story.SOUND: ["sheet_a"],
        story.CHAT: ["default"],
        story.CRILIPS: ["default"],
    }


def test_part_products_omit_the_products_a_story_does_not_need():
    parts = {"assets": [], "models": [], "sounds": [], "chat": False, "crilips": False}
    assert story.part_products({"parts": story.part_paths(parts)}) == {story.SCENE: ["default"]}
    parts["sounds"] = ["sheet_a"]
    doc = {"parts": story.part_paths(parts)}
    assert story.part_products(doc) == {story.SCENE: ["default"], story.SOUND: ["sheet_a"]}
