"""Runtime inputs retain normalized note order/identities and bind the engine to the pinned model."""
import hashlib
import json

import pytest

from nnnotes import deckdata, musicdata, replaydata
from test_musicdata import CHARTS, KEY, PROV, FakeDeck, bgm, export


def test_export_replay_keeps_main_file_small_and_preserves_runtime_input(tmp_path):
    result = export(tmp_path, replay_dir=tmp_path / "replay")
    doc = json.loads((tmp_path / "music.json").read_text())
    manifest_path = tmp_path / doc["replay"]["manifestUrl"]
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    data = json.loads((manifest_path.parent / "deck-data.json").read_text())
    assert "master" not in doc and "charts" not in doc
    assert result["replay"]["sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_path.parent.name == result["replay"]["sha256"]
    assert data["format"] == deckdata.DECK_FORMAT
    assert [c["scoreId"] for c in data["charts"]] == [10, 20, 30]
    assert len(data["master"]) == len(deckdata.TABLES)
    for item, record in zip(manifest["charts"], data["charts"]):
        payload = (manifest_path.parent / item["url"]).read_bytes()
        chart = json.loads(payload)
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert chart["chart"] == record
        assert chart["musicLengthMs"] == data["provenance"]["replay"]["musicLengthsMs"][str(record["scoreId"])]
    assert manifest["engine"] is None


def test_replay_requires_actual_audio_length_and_contains_no_guess(tmp_path):
    with pytest.raises(musicdata.MusicDataError, match="actual BGM lengths"):
        export(tmp_path, bgm=None, replay_dir=tmp_path / "replay")
    assert not (tmp_path / "music.json").exists()


def test_engine_manifest_rejects_stale_pin_and_changed_wasm(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()
    js, wasm = b"export class ReplaySession {}", b"\x00asm\x01\x00\x00\x00"
    (engine / replaydata.JS).write_bytes(js)
    (engine / replaydata.WASM).write_bytes(wasm)
    built = {"format": "ournotes.replay-engine/1", "commit": "0" * 40,
             "jsSha256": hashlib.sha256(js).hexdigest(), "wasmSha256": hashlib.sha256(wasm).hexdigest()}
    (engine / "build.json").write_text(json.dumps(built))
    stale = tmp_path / "stale"
    stale.mkdir()
    with pytest.raises(musicdata.MusicDataError, match="commit differs"):
        export(stale, deck=FakeDeck(), replay_dir=stale / "replay", replay_engine=engine)
    assert not (stale / "music.json").exists()
    built["commit"] = FakeDeck.COMMIT
    (engine / "build.json").write_text(json.dumps(built))
    valid = tmp_path / "valid"
    valid.mkdir()
    result = export(valid, deck=FakeDeck(), replay_dir=valid / "replay", replay_engine=engine)
    old_manifest_path = valid / result["replay"]["manifestUrl"]
    old_bytes = old_manifest_path.read_bytes()
    manifest = json.loads(old_bytes)
    assert manifest["engine"]["model"]["commit"] == FakeDeck.COMMIT
    new_js = js + b"\n// updated engine"
    (engine / replaydata.JS).write_bytes(new_js)
    built["jsSha256"] = hashlib.sha256(new_js).hexdigest()
    (engine / "build.json").write_text(json.dumps(built))
    result = musicdata.export(valid / "music.json", deckdata.master_files(valid / "m"), KEY,
                              CHARTS.__getitem__, bgm, **PROV, deck=musicdata.Deck(module=FakeDeck()),
                              replay_dir=valid / "replay", replay_engine=engine)
    assert valid / result["replay"]["manifestUrl"] != old_manifest_path
    assert old_manifest_path.read_bytes() == old_bytes  # readers of the preceding document keep its resource tree
    (engine / replaydata.WASM).write_bytes(wasm + b"changed")
    changed = tmp_path / "changed"
    changed.mkdir()
    with pytest.raises(musicdata.MusicDataError, match="SHA differs"):
        export(changed, deck=FakeDeck(), replay_dir=changed / "replay", replay_engine=engine)


def package(directory, js_name, wasm_name, build_format, commit, js=b"export class Session {}"):
    """A synthetic wasm-bindgen package with its build.json."""
    directory.mkdir(parents=True, exist_ok=True)
    wasm = b"\x00asm\x01\x00\x00\x00"
    (directory / js_name).write_bytes(js)
    (directory / wasm_name).write_bytes(wasm)
    built = {"format": build_format, "commit": commit, "workingTreeDirty": False,
             "jsSha256": hashlib.sha256(js).hexdigest(), "wasmSha256": hashlib.sha256(wasm).hexdigest()}
    (directory / "build.json").write_text(json.dumps(built))
    return built


def replay_package(tmp_path):
    return package(tmp_path / "replay-pkg", replaydata.JS, replaydata.WASM, "ournotes.replay-engine/1", FakeDeck.COMMIT)


def recommend_package(tmp_path, commit=FakeDeck.COMMIT, build_format="ournotes.recommend-engine/1"):
    return package(tmp_path / "recommend-pkg", replaydata.RECOMMEND_JS, replaydata.RECOMMEND_WASM, build_format, commit,
                   b"export class RecommendationSession {}")


def test_recommend_engine_shares_the_model_of_the_deck_data(tmp_path):
    replay_package(tmp_path)
    built = recommend_package(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    result = export(plain, deck=FakeDeck(), replay_dir=plain / "replay", replay_engine=tmp_path / "replay-pkg")
    assert "recommendEngine" not in json.loads((plain / result["replay"]["manifestUrl"]).read_bytes())
    out = tmp_path / "out"
    out.mkdir()
    result = export(out, deck=FakeDeck(), replay_dir=out / "replay", replay_engine=tmp_path / "replay-pkg",
                    recommend_engine=tmp_path / "recommend-pkg")
    manifest_path = out / result["replay"]["manifestUrl"]
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest["recommendEngine"]
    data = json.loads((manifest_path.parent / manifest["deckData"]["url"]).read_bytes())
    assert list(entry) == ["model", "js", "wasm", "build"]
    assert entry["model"] == manifest["engine"]["model"] == data["provenance"]["deck"]
    assert entry["model"]["commit"] == FakeDeck.COMMIT
    assert [entry[k]["url"] for k in ("js", "wasm", "build")] == [
        "recommend/ournotes_recommend.js", "recommend/ournotes_recommend_bg.wasm", "recommend/build.json"]
    assert manifest["engine"]["build"]["url"] == "engine/build.json"
    for key, name in (("js", replaydata.RECOMMEND_JS), ("wasm", replaydata.RECOMMEND_WASM)):
        payload = (manifest_path.parent / entry[key]["url"]).read_bytes()
        assert payload == (tmp_path / "recommend-pkg" / name).read_bytes()
        assert (entry[key]["sha256"], entry[key]["bytes"]) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert (entry["js"]["sha256"], entry["wasm"]["sha256"]) == (built["jsSha256"], built["wasmSha256"])
    build = (manifest_path.parent / entry["build"]["url"]).read_bytes()
    assert json.loads(build) == built
    assert (entry["build"]["sha256"], entry["build"]["bytes"]) == (hashlib.sha256(build).hexdigest(), len(build))


@pytest.mark.parametrize("change,message", [
    ("commit", "recommend engine: build.json commit differs"),
    ("format", "recommend engine: build.json commit differs"),
    ("wasm", "recommend engine: JS/WASM SHA differs"),
    ("missing", "recommend engine: missing ournotes_recommend.js"),
    ("no-replay-dir", "--recommend-engine needs --replay-dir"),
])
def test_recommend_engine_rejects_another_build(tmp_path, change, message):
    replay_package(tmp_path)
    recommend_package(tmp_path, commit="0" * 40 if change == "commit" else FakeDeck.COMMIT,
                      build_format="ournotes.replay-engine/1" if change == "format" else "ournotes.recommend-engine/1")
    pkg = tmp_path / "recommend-pkg"
    if change == "wasm":
        (pkg / replaydata.RECOMMEND_WASM).write_bytes(b"\x00asm\x01\x00\x00\x00changed")
    if change == "missing":
        (pkg / replaydata.RECOMMEND_JS).unlink()
    replay = {} if change == "no-replay-dir" else {"replay_dir": tmp_path / "replay",
                                                    "replay_engine": tmp_path / "replay-pkg"}
    with pytest.raises(musicdata.MusicDataError, match=message):
        export(tmp_path, deck=FakeDeck(), recommend_engine=pkg, **replay)
    assert not (tmp_path / "music.json").exists() and not (tmp_path / "replay").exists()


def test_final_export_rejects_unmet_sampling_instead_of_publishing_flag(tmp_path):
    def unmet(chart):
        apt = chart.get("gekisouAptitude")
        if apt and apt["variants"]:
            apt["variants"][0]["seTargetMet"] = False
    with pytest.raises(musicdata.MusicDataError, match="did not meet BOTH score SE targets"):
        export(tmp_path, deck=FakeDeck(change=unmet))
    assert not (tmp_path / "music.json").exists()
