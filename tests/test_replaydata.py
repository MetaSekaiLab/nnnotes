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


def test_final_export_rejects_unmet_sampling_instead_of_publishing_flag(tmp_path):
    def unmet(chart):
        apt = chart.get("gekisouAptitude")
        if apt and apt["variants"]:
            apt["variants"][0]["seTargetMet"] = False
    with pytest.raises(musicdata.MusicDataError, match="did not meet BOTH score SE targets"):
        export(tmp_path, deck=FakeDeck(change=unmet))
    assert not (tmp_path / "music.json").exists()
