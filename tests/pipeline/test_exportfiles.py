"""Standalone file exports consume the common object and cue-sheet results."""

import io
import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest

from nnnotes.content import adv, storyepisode
from nnnotes.pipeline import contract
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.contract import Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.exportfiles import AudioFilesStage, EpisodeStage, ShaderFilesStage
from nnnotes.pipeline.stages import Env, execute
from tests.helpers import story as sh
from tests.pipeline.test_live import master_record, run, stored
from tests.pipeline.test_objexport import exported as exported
from tests.pipeline.test_story import STREAMS, sheet


@pytest.mark.parametrize("apk", [False, True])
def test_shader_command_converts_hashed_files_even_without_catalog_entries(tmp_path, monkeypatch, capsys, apk):
    from nnnotes.cli import export as cli_export
    from nnnotes.pipeline import census, objexport
    from nnnotes.source.config import Config
    from nnnotes.source.session import Session
    from tests.pipeline.test_objexport import FAKE_READER, bundle_all

    path = tmp_path / ("sample_" + "a" * 32 + ".bundle")
    path.write_bytes(b"fake:all")
    cat = SimpleNamespace(apk=object(), has=lambda _: True, fetch_key=lambda _: [path], apk_bundle=lambda _: path)
    monkeypatch.setattr(Session, "catalog", lambda self: cat)
    monkeypatch.setattr(census, "census_file", lambda *a, **kw: census.census_env(bundle_all(), shipped=True))
    original = objexport.ExportStage
    monkeypatch.setattr(objexport, "ExportStage", lambda: original(FAKE_READER))
    args = SimpleNamespace(
        key=None if apk else "Example/Shader",
        apk_bundle=["sample"] if apk else None,
        out=str(tmp_path / "out"),
        json=True,
    )
    cfg = Config({"paths": {"depot": str(tmp_path / "depot")}}, environ={})
    cli_export.cmd_shader(args, cfg)
    result = contract.loads(capsys.readouterr().out)
    assert result["count"] == 1 and result["names"] == ["Test/Unlit"]
    assert (tmp_path / "out/Test_Unlit.json").is_file()
    assert Depot(tmp_path / "depot").latest_run(["export.shader", "sample"])["tasks"]


@pytest.mark.parametrize("fmt", ["wav", "ogg"])
def test_audio_formats_encode_a_decoded_flac_stream(tmp_path, monkeypatch, fmt):
    from nnnotes.pipeline import exportfiles
    from nnnotes.pipeline.cristages import tool_id

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    pcm = b"\x10\x00" * 256
    source = tmp_path / "input.wav"
    with wave.open(str(source), "wb") as out:
        out.setparams((1, 2, 48000, 256, "NONE", "not compressed"))
        out.writeframes(pcm)
    flac = tmp_path / "input.flac"
    subprocess.run([ffmpeg, "-v", "error", "-i", str(source), str(flac)], check=True, capture_output=True)
    monkeypatch.setattr(exportfiles, "_tool", lambda _: ffmpeg)
    depot, stage = Depot(tmp_path / "depot"), AudioFilesStage()
    streams = [{"stream": 1, "name": "cue", "file": "cue.flac", "sampleRate": 48000, "channels": 1, "samples": 256}]
    inputs, sheets = sheet(depot, "sample", streams)
    files = sheets["Cri/Sound/sample"]["files"]
    files["cue.flac"] = depot.add(flac.read_bytes(), "flac")
    files["cues.json"] = depot.add(
        contract.encode({"cue": {k: v for k, v in streams[0].items() if k not in {"stream", "name"}}}), "json"
    )
    inputs[-1] = stored(depot, contract.encode(sheets), "sheets")
    inputs.append(
        stored(
            depot,
            contract.encode(
                {"schema": contract.OBJECTS, "roots": [], "keys": {}, "objects": {}, "fenced": {}, "missing": []}
            ),
            "objects",
        )
    )
    _, _, output = run(depot, stage, "sample", inputs, {"audio": fmt}, {"audio.encode": tool_id("ffmpeg", ffmpeg)})
    encoded = depot.read(output["cue." + fmt]["content"]["sha256"])
    assert contract.loads(depot.read(output["cues.json"]["content"]["sha256"]))["cue"]["file"] == "cue." + fmt
    if fmt == "wav":
        with wave.open(io.BytesIO(encoded)) as audio:
            assert audio.getframerate() == 48000 and audio.readframes(256) == pcm
    else:
        assert encoded.startswith(b"OggS")
        file = tmp_path / "encoded.ogg"
        file.write_bytes(encoded)
        subprocess.run([ffmpeg, "-v", "error", "-i", str(file), "-f", "null", "-"], check=True, capture_output=True)


def test_episode_file_uses_the_product_episode_builder(tmp_path):
    depot, stage = Depot(tmp_path / "d"), EpisodeStage()
    env = Env(depot, {"master": sh.master_dir(tmp_path)})
    table = sh.episode_table(depot)
    _, _, artifacts = run(
        depot, stage, "1", [stored(depot, contract.encode(table), "objects"), master_record(env, stage, "1")]
    )
    record = stage.record("1", env)
    expected = adv.to_json(
        storyepisode.episode(Objects(table, depot), record, has=set(record["catalogKeys"]).__contains__)
    )
    assert contract.loads(depot.read(artifacts["episode.json"]["content"]["sha256"])) == contract.loads(
        contract.encode(expected)
    )
    assert set(artifacts) == {"objects", "episode.json"}
    assert all("present" in resource for resource in expected["resources"])


def test_episode_file_preserves_embedded_resource_fallback_and_presence(tmp_path, monkeypatch):
    from nnnotes.content.advcommand import COMMAND

    command = next(code for code, name in COMMAND.items() if name == "TalkWindow")
    monkeypatch.setattr(sh, "COLLECTION", [{"Index": 0, "Command": command}])
    depot, stage = Depot(tmp_path / "d"), EpisodeStage()
    address = adv.EMBEDDED_PREFIX + adv.TALK_WINDOW_PREFIX + adv.DEFAULT_TALK_WINDOW
    env = Env(depot, {"master": sh.master_dir(tmp_path), "index": {"locations": [{"primaryKey": address}]}})
    objects = stored(depot, contract.encode(sh.episode_table(depot)), "objects")
    _, _, artifacts = run(depot, stage, "1", [objects, master_record(env, stage, "1")])
    doc = contract.loads(depot.read(artifacts["episode.json"]["content"]["sha256"]))
    assert doc["resources"] == [{"kind": "talkwindow", "address": address, "present": True}]


def test_flac_files_reuse_decoded_stream_bytes_and_indexes(tmp_path):
    depot, stage = Depot(tmp_path), AudioFilesStage()
    inputs, sheets = sheet(depot, "sample")
    cues = {}
    for stream in STREAMS:
        cues.setdefault(stream["name"], {k: v for k, v in stream.items() if k not in {"name", "stream"}})
    data = contract.encode(cues)
    sheets["Cri/Sound/sample"]["files"]["cues.json"] = {"sha256": depot.put(data), "size": len(data), "ext": "json"}
    inputs[-1] = stored(depot, contract.encode(sheets), "sheets")
    inputs.append(
        stored(
            depot,
            contract.encode(
                {"schema": contract.OBJECTS, "roots": [], "keys": {}, "objects": {}, "fenced": {}, "missing": []}
            ),
            "objects",
        )
    )
    task, result, artifacts = run(depot, stage, "sample", inputs)
    for stream in STREAMS:
        assert (
            artifacts[stream["file"]]["content"]["sha256"]
            == sheets["Cri/Sound/sample"]["files"][stream["file"]]["sha256"]
        )
    assert contract.loads(depot.read(artifacts["cues.json"]["content"]["sha256"])) == cues
    assert execute(task, depot, {stage.name: stage}).status == "hit"
    assert depot.result(task.key) == result


def test_shader_files_reuse_converted_programs_and_parsed_forms(exported):
    depot, result = exported
    record = {
        "source": "example.bundle",
        "items": result["items"],
        "artifacts": {a["id"]: a for a in result["artifacts"]},
    }
    stage = ShaderFilesStage()
    inp = stored(depot, contract.encode([record]), "records")
    task = Task(stage.name, stage.version, "selected", inputs=(inp,))
    execute(task, depot, {stage.name: stage})
    output = {contract.parse_artifact_id(a["id"])[1]: a for a in depot.result(task.key)["artifacts"]}
    index = contract.loads(depot.read(output["shaders.json"]["content"]["sha256"]))
    assert [row["name"] for row in index] == ["Test/Unlit"]
    row = index[0]
    assert row["parsed"] == "Test_Unlit.json" and row["source"] == "example.bundle"
    sources = {a["content"]["sha256"] for a in result["artifacts"]}
    assert output[row["parsed"]]["content"]["sha256"] in sources
    assert all(output[v["file"]]["content"]["sha256"] in sources for v in row["variants"])
