"""The story products of nnnotes.pipeline.story on synthetic cue sheet decodes and a synthetic CRI Lips library."""

import pytest

from nnnotes.formats import crilips
from nnnotes.pipeline import assemble, contract, cristages, live, story
from nnnotes.pipeline.contract import IncompatibleTask
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env
from nnnotes.pipeline.story import CriLipsStage, SoundStage
from tests.formats.test_crilips import _elf_with_rodata, _synthetic_version
from tests.pipeline.test_live import run, stored, table
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
