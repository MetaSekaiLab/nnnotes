import json

import pytest

from nnnotes.formats.acbwrite import author
from nnnotes.packing import AudioResult, AudioTools, build_package, plan_package, verify_package
from nnnotes.source import master
from tests.helpers import synth
from tests.helpers.packing import hca_stream, package_inputs


def tools(monkeypatch):
    monkeypatch.setattr(AudioTools, "fingerprints", lambda self: {})
    monkeypatch.setattr("nnnotes.packing.plan.probe_audio", lambda *a, **k: {"channels": 2, "sampleRate": 48000})
    return AudioTools("ffmpeg", "ffprobe", "encoder", "decoder")


def encoded_audio(source, template, *, cue_name, **kwargs):
    data, _ = author(template, hca_stream(samples=2048), cue_name=cue_name)
    return AudioResult(
        data,
        {
            "processed": {"samples": 2048, "sampleRate": 48000},
            "validation": {"structure": "passed", "decode": {"status": "passed"}},
        },
    )


def test_complete_package_build_roundtrip_and_determinism(tmp_path, monkeypatch):
    path = package_inputs(tmp_path, monkeypatch)
    configured = tools(monkeypatch)
    monkeypatch.setattr("nnnotes.packing.build.build_audio", encoded_audio)
    plan = plan_package(path, tools=configured)
    assert not plan.report["diagnostics"], plan.report
    first = build_package(plan, out=tmp_path / "first", tools=configured)
    assert verify_package(first.root, target=tmp_path / "target.json")["status"] == "passed"
    second = build_package(plan, out=tmp_path / "second", tools=configured)
    assert first.manifest == second.manifest
    for item in first.manifest["files"]:
        assert (first.root / item["path"]).read_bytes() == (second.root / item["path"]).read_bytes()
    key = master.MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
    songs = json.loads(master.decode((first.root / "master/MasterLiveMusic.bin").read_bytes(), key))["_allData"]
    assert songs[-1]["_id"] == 7 and songs[-1]["_bandIDs"] == []
    (first.root / "audio/full.acb").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        verify_package(first.root, target=tmp_path / "target.json")


def test_changed_input_prevents_build_and_missing_input_is_a_diagnostic(tmp_path, monkeypatch):
    path = package_inputs(tmp_path, monkeypatch)
    configured = tools(monkeypatch)
    plan = plan_package(path, tools=configured)
    (tmp_path / "chart.json").write_text("{}")
    with pytest.raises(ValueError, match="input changed"):
        build_package(plan, out=tmp_path / "out", tools=configured)
    assert not (tmp_path / "out").exists()
    (tmp_path / "jacket.png").unlink()
    report = plan_package(path, tools=configured).report
    assert report["diagnostics"]
