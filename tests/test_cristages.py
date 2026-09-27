"""cri.audio and cri.movie on synthetic data: subjects by content (raw files and the ACB artifacts of unity.export),
the ACB / AWB pairing, the key's source, the tools' ids, the artifacts equal to cri.decode's files and to
advvideo.demux's streams, the unsupported forms, laziness, the documentation and the golden records' providers."""
import json
import re
import subprocess
import sys
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

import pytest

from nnnotes import cache, contract, cri, crikey, cristages
from nnnotes.contract import IncompatibleTask, Input, Task
from nnnotes.cristages import AUDIO, AudioStage, MovieStage
from nnnotes.stages import Env, Pending, describe, execute, registry
from nnnotes.store import Store
from test_speed_exact import FakeTools
from test_advvideo import ADX, HCA, KEY, MPEG1, VP9, audio_header, build, chunk, rand, sbt, video_header

ROOT = Path(__file__).resolve().parents[1]
ACB = b"@UTF" + b"cue sheet bytes"
AWB = b"AFS2" + b"streamed waveforms"
NAMES = ["a", "b", "a", "c", "c"]                     # stream names (layered waveforms repeat a name)
FAKE_ATOMS = {"hca.decode": "vgmstream-fake/1", "flac.encode": "ffmpeg-fake/1"}
MOVIE_ATOMS = {"usm.demux", "movie.mux", "hca.decode"}


def usm_file(audio=True, extra=(), streams=()):
    """A VP9 movie (with its ADX audio) plus `streams` (test_advvideo.build entries) and `extra` chunks."""
    base = [(b"@SFV", 0, None, VP9, "video")] + ([(b"@SFA", 0, None, ADX, "audio")] if audio else [])
    return build([*base, *streams], extra=extra), VP9


class FakeMux(FakeTools):
    """FakeTools, and an ffmpeg mux whose output is its arguments (file names without their directories) followed
    by its inputs' bytes."""

    def __call__(self, args, **kw):
        if Path(args[0]).name == "vgmstream" and args[1:3] == ["-i", "-o"]:      # an HCA stream of a movie
            with self.lock:
                self.calls.append(args[1:3])
            Path(args[3]).write_bytes(b"RIFF" + Path(args[4]).read_bytes())
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if not (len(args) > 3 and args[-3] == "-f" and args[-2] in ("matroska", "webm")):
            return super().__call__(args, **kw)
        with self.lock:
            self.calls.append(["mux"])
        ins = [Path(args[i + 1]) for i, a in enumerate(args) if a == "-i"]
        text = "|".join(Path(a).name if ("/" in a or "\\" in a) else a for a in args[1:-1])
        Path(args[-1]).write_bytes(text.encode() + b"".join(p.read_bytes() for p in ins))
        return subprocess.CompletedProcess(args, 0, "", "")


@contextmanager
def fake_tools(key=0, names=NAMES):
    """vgmstream / ffmpeg stand-ins (test_speed_exact.FakeTools, FakeMux), tool ids without executables, and the
    keycode `key` for any boot data."""
    fake = FakeMux(list(names))
    with ExitStack() as st:
        st.enter_context(mock.patch.object(subprocess, "run", fake))
        st.enter_context(mock.patch.object(cristages, "_tool", lambda which: which))
        st.enter_context(mock.patch.object(cristages, "tool_id", lambda which, exe: f"{which}-fake/1"))
        st.enter_context(mock.patch.object(cristages, "_keys", {}))
        st.enter_context(mock.patch.object(crikey, "find_key", lambda path: key))
        st.enter_context(mock.patch.object(cri, "tool", lambda name, exe: name))
        yield fake


@pytest.fixture
def tools():
    cache.clear()
    with fake_tools() as fake:
        yield fake


def file_input(store, tmp: Path, stable: str, data: bytes, role="raw") -> Input:
    p = tmp / "files" / stable.replace("/", "_")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    sha, size = store.identify(p, "raw", stable)
    return Input(role, sha, size, f"{stable}_{'0' * 32}", ({"kind": "file", "path": str(p)},))


def catalog_index(keys: dict, bundles=()) -> dict:
    """A catalog index naming raw files and bundles: {primary key: [stable raw or bundle names it depends on]};
    `bundles`: the names that are bundles."""
    files = sorted({s for deps in keys.values() for s in deps})
    lid = {s: f"remote:{i}" for i, s in enumerate(files)}
    locations = [{"id": lid[s], "primaryKey": s, "kind": "bundle" if s in bundles else "raw", "dependencies": []}
                 for s in files]
    locations += [{"id": f"remote:{100 + i}", "primaryKey": k, "kind": "asset", "dependencies": [lid[s] for s in deps]}
                  for i, (k, deps) in enumerate(sorted(keys.items()))]
    return {"rawFiles": [{"stable": s, "locations": [lid[s]]} for s in files if s not in bundles],
            "bundles": [{"stable": s, "locations": [lid[s]]} for s in files if s in bundles], "locations": locations}


def planning(store, tmp, files: dict, keys=None, boot=True, bundles=()) -> Env:
    raw = {s: file_input(store, tmp, s, d) for s, d in files.items()}
    b = tmp / "data.unity3d"
    b.write_bytes(b"UnityFS boot data")
    facts = {"raw": raw, "index": catalog_index(keys or {}, bundles),
             "boot": cristages.boot_input(store, b) if boot else None}
    return Env(store, facts)


def export_result(store, env, subject: str, acbs: dict, usms=None) -> str:
    """A committed unity.export result holding `cri.acb` artifacts {cue sheet: ACB bytes} and `cri.usm` artifacts
    {movie asset name: USM bytes}."""
    t = Task(EXPORT, 1, subject, {}, {}, (Input("bundle", store.put(subject.encode()), len(subject)),))
    recs = [contract.artifact(f"CAB-{subject}:{i + 1}#acb", store.add(data, "acb"), contract.provenance(t),
                              {"kind": "cri.acb", "format": "acb", "facts": {"cueSheet": sheet, "layout": "embedded"}})
            for i, (sheet, data) in enumerate(sorted(acbs.items()))]
    recs += [contract.artifact(f"CAB-{subject}:{100 + i}#usm", store.add(data, "usm"), contract.provenance(t),
                               {"kind": "cri.usm", "format": "usm", "facts": {"movie": name}})
             for i, (name, data) in enumerate(sorted((usms or {}).items()))]
    store.commit(contract.result(t, recs, []))
    env.done(t.id, t.key)
    return t.id


EXPORT = "unity.export"


def run(stage, env, subject, params=None):
    t = describe(stage, subject, params, env)
    execute(t, env.store, registry(stage), force=True)
    return t, env.store.result(t.key)


def art(doc, aid):
    return next(a for a in doc["artifacts"] if a["id"] == aid)


def load(store, doc, aid):
    return store.read(art(doc, aid)["content"]["sha256"])


# ---------------------------------------------------------------- the key's source
def test_boot_input_from_an_apk_is_stored_once(tmp_path):
    store = Store(tmp_path / "s")
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr(cristages.BOOT_IN_APK, b"UnityFS boot")
        z.writestr("classes.dex", b"dex")
    a = cristages.boot_input(store, apk)
    assert (a.role, a.sha256, a.size, a.name, a.locators) == ("boot", contract.sha256(b"UnityFS boot"), 12,
                                                              "data.unity3d", ({"kind": "store"},))
    assert store.read(a.sha256) == b"UnityFS boot"
    with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("read again")):
        assert cristages.boot_input(Store(tmp_path / "s"), apk) == a              # remembered by the APK's content
    d = tmp_path / "data.unity3d"
    d.write_bytes(b"UnityFS other")
    b = cristages.boot_input(store, d)
    assert b.sha256 == contract.sha256(b"UnityFS other") and b.locators[0]["kind"] == "file"
    assert cristages.boot_input(store, None) is None


def test_hca_key_is_read_once_per_content(tmp_path):
    store = Store(tmp_path / "s")
    inp = Input("boot", store.put(b"boot"), 4, None, ({"kind": "store"},))
    calls = []
    with mock.patch.object(cristages, "_keys", {}), \
            mock.patch.object(crikey, "find_key", lambda p: calls.append(Path(p).read_bytes()) or 77):
        assert cristages.hca_key(store, inp) == cristages.hca_key(store, inp) == 77
    assert calls == [b"boot"]


def test_tool_ids_name_the_version_and_the_executable(tmp_path):
    exe = tmp_path / "vgmstream-cli"
    exe.write_bytes(b"binary 1")
    reply = subprocess.CompletedProcess([], 0, json.dumps({"version": "r2117", "extensions": {}}), "")
    with mock.patch.object(cristages, "_tools", {}), mock.patch.object(subprocess, "run", return_value=reply):
        a = cristages.tool_id("vgmstream", str(exe))
        assert a == f"vgmstream-r2117+sha256.{contract.sha256(b'binary 1')}/1"
        exe.write_bytes(b"binary 22")
        assert cristages.tool_id("vgmstream", str(exe)) != a
    reply = subprocess.CompletedProcess([], 0, "ffmpeg version 5.1.9-0+deb12u1 Copyright (c) 2000-2026\nbuilt", "")
    with mock.patch.object(subprocess, "run", return_value=reply):
        assert cristages.tool_version("ffmpeg", "ffmpeg") == "5.1.9-0+deb12u1"
    with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "usage", "")):
        assert cristages.tool_version("vgmstream", "x") == cristages.tool_version("ffmpeg", "x") == "unknown"


# ---------------------------------------------------------------- cri.audio
def test_audio_subjects_by_content(tmp_path, tools):
    store = Store(tmp_path / "s")
    other = b"@UTF" + b"another sheet"
    env = planning(store, tmp_path, {"cri_assets_cri/sound/bgm_x": ACB, "cri_assets_cri/video/m": usm_file()[0]},
                   {"Cri/Sound/bgm_x": ["cri_assets_cri/sound/bgm_x"], "Cri/Video/m": ["cri_assets_cri/video/m"]})
    tid = export_result(store, env, "cri_bundle", {"bgm_x": ACB, "se_live": other})
    stage = AudioStage()
    sha, other_sha = contract.sha256(ACB), contract.sha256(other)
    assert stage.subjects(env) == sorted([sha, other_sha])                  # the raw ACB and its bundle copy: one
    assert stage.names(env) == {sha: ["Cri/Sound/bgm_x"], other_sha: ["se_live"]}
    assert stage.depends(sha, env) == [tid] and stage.depends(other_sha, env) == [tid]
    t = describe(stage, sha, None, env)
    assert t.id == f"{AUDIO}:{sha}" and [i.role for i in t.inputs] == ["acb", "boot"]
    assert t.input("acb").locators[0]["kind"] == "file" and t.atoms == FAKE_ATOMS
    assert t.params == {"flac": {"level": 8}}
    b = describe(stage, other_sha, None, env)
    assert b.input("acb").locators == ({"kind": "store"},)
    waiting = Env(store, env.facts)
    waiting.waiting(tid)
    with pytest.raises(Pending, match=tid):
        stage.subjects(waiting)
    with pytest.raises(ValueError, match="set \\[paths\\] apk"):
        describe(stage, sha, None, planning(store, tmp_path, {"cri_assets_cri/sound/bgm_x": ACB}, boot=False))


def test_audio_waits_for_raw_files_not_fetched(tmp_path, tools):
    from nnnotes.cli_assets import Inputs
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path, {"cri_assets_cri/sound/a": ACB})
    env.facts["raw"] = Inputs({**env.facts["raw"], "cri_assets_cri/sound/b": Pending("fetch:b_1")})
    with pytest.raises(Pending, match="fetch:b_1"):
        AudioStage().subjects(env)
    elsewhere = Input("raw", contract.sha256(b"x"), 1, "c_1", ({"kind": "catalog", "catalog": "v", "location": "r"},))
    env.facts["raw"] = {"cri_assets_cri/sound/c": elsewhere}                  # only a catalog locator: not fetched
    with pytest.raises(Pending, match="fetch:c_1"):
        AudioStage().subjects(env)


def test_audio_artifacts_are_the_files_of_cri_decode(tmp_path, tools, monkeypatch):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path, {"cri_assets_cri/sound/bgm_x": ACB})
    sha = contract.sha256(ACB)
    t, doc = run(AudioStage(), env, sha)
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": ACB}, cri.RAW_ACB))
    (tmp_path / "bin").mkdir()
    for n in ("vgmstream", "ffmpeg"):
        (tmp_path / "bin" / n).write_bytes(b"#!")
    monkeypatch.setattr(cri, "tool", lambda name, exe: str(tmp_path / "bin" / name))
    cache.clear()
    ref = tmp_path / "ref"
    cri.decode(None, "bgm_x", ref, key=0)
    files = sorted(p.name for p in ref.iterdir())
    assert files == ["a.flac", "a_3.flac", "b.flac", "c.flac", "c_5.flac", "cues.json", "streams.json"]
    assert sorted(a["id"] for a in doc["artifacts"]) == [f"{t.id}#{f}" for f in files]
    for f in files:
        assert load(store, doc, f"{t.id}#{f}") == (ref / f).read_bytes(), f
    s = art(doc, f"{t.id}#a_3.flac")
    assert s["content"]["mediaType"] == "audio/flac" and "object" not in s["provenance"]
    assert s["semantics"] == {"kind": "audio.stream", "format": "flac", "facts": {
        "stream": 3, "name": "a", "sampleRate": 48000, "channels": 2, "samples": 3000, "loopStart": 3,
        "loopEnd": 270}}
    assert art(doc, f"{t.id}#cues.json")["semantics"]["facts"] == {"cues": 3}
    assert doc["items"] == [{"object": t.id, "status": "exported", "artifacts": sorted(f"{t.id}#{f}" for f in files),
                             "class": "ACB"}]
    assert doc["status"] == "ok" and not list(tmp_path.glob("**/_work"))


def test_audio_two_runs_and_levels(tmp_path, tools):
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    ea, eb = planning(a, tmp_path / "x", {"s/a": ACB}), planning(b, tmp_path / "y", {"s/a": ACB})
    ta, da = run(AudioStage(), ea, contract.sha256(ACB))
    tb, db = run(AudioStage(), eb, contract.sha256(ACB))
    assert ta.key == tb.key and contract.encode(da) == contract.encode(db)
    t0, d0 = run(AudioStage(), ea, contract.sha256(ACB), {"flac": {"level": 0}})
    assert t0.key != ta.key and load(a, d0, f"{t0.id}#a.flac") != load(a, da, f"{ta.id}#a.flac")
    for bad in ({"flac": {"level": 13}}, {"flac": {"speed": 1}}, {"format": "ogg"}):
        with pytest.raises(ValueError):
            AudioStage().normalize(bad)


def test_an_acb_with_an_external_awb_is_unsupported(tmp_path, tools):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path, {"cri_assets_cri/sound/v": ACB, "cri_assets_cri/sound/v_awb": AWB},
                   {"Cri/Sound/v": ["cri_assets_cri/sound/v", "cri_assets_cri/sound/v_awb"]})
    stage = AudioStage()
    sha = contract.sha256(ACB)
    assert stage.subjects(env) == [sha]                                       # an AWB is not a subject
    t, doc = run(stage, env, sha)
    assert [i.role for i in t.inputs] == ["acb", "awb", "boot"] and t.input("awb").sha256 == contract.sha256(AWB)
    assert doc["artifacts"] == [] and doc["items"] == [{"object": t.id, "status": "unsupported", "class": "ACB",
                                                        "reason": {"code": "unsupported.cri.awb_external", "message":
                                                                   "the cue sheet's waveforms are streamed from an "
                                                                   "external AWB"}}]
    assert tools.calls == []


def test_a_task_of_other_tools_is_incompatible(tmp_path, tools):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path, {"s/a": ACB})
    t = describe(AudioStage(), contract.sha256(ACB), None, env)
    with mock.patch.object(cristages, "tool_id", lambda which, exe: f"{which}-other/1"):
        with pytest.raises(IncompatibleTask, match="flac.encode, hca.decode differ"):
            execute(t, store, registry(AudioStage()))


# ---------------------------------------------------------------- cri.movie
def test_movie_streams_as_stored_and_the_mkv(tmp_path, tools):
    from nnnotes import advvideo
    store = Store(tmp_path / "s")
    data, frames = usm_file()
    env = planning(store, tmp_path, {"membercard/1/movie/anime_part1": data, "cri_assets_cri/sound/x": ACB},
                   {"MemberCard/1/movie/anime_part1": ["membercard/1/movie/anime_part1"]})
    stage = MovieStage()
    sha = contract.sha256(data)
    assert stage.subjects(env) == [sha] and stage.names(env) == {sha: ["MemberCard/1/movie/anime_part1"]}
    with fake_tools(key=KEY):
        t, doc = run(stage, env, sha)
    assert t.params == {"format": "mkv", "flac": {"level": 8}} and set(t.atoms) == MOVIE_ATOMS
    video, audio = advvideo.demux(data, KEY)
    assert load(store, doc, f"{t.id}#video.ivf") == video.data == b"".join(frames)
    assert load(store, doc, f"{t.id}#audio.adx") == audio.data
    assert art(doc, f"{t.id}#video.ivf")["semantics"] == {"kind": "video.stream", "format": "ivf", "facts": {
        "codec": "vp9", "width": 320, "height": 180, "frameRate": [30, 1], "frames": 3}}
    assert art(doc, f"{t.id}#audio.adx")["semantics"]["facts"] == advvideo.adx_info(audio.data)
    mkv = art(doc, f"{t.id}#movie.mkv")
    assert mkv["content"]["mediaType"] == "video/x-matroska" and mkv["semantics"]["facts"] == {"tracks": [
        {"kind": "video", "channel": 0, "codec": "vp9"}, {"kind": "audio", "channel": 0, "codec": "flac",
                                                          "source": "adx"}]}
    assert b"-c:a|flac|-compression_level|8" in load(store, doc, mkv["id"])
    assert doc["items"][0]["status"] == "exported" and doc["items"][0]["class"] == "USM"
    with fake_tools(key=KEY):
        tw, dw = run(stage, env, sha, {"format": "webm"})
    webm = art(dw, f"{tw.id}#movie.webm")
    assert tw.params == {"format": "webm"} and webm["content"]["mediaType"] == "video/webm"
    assert b"libopus" in load(store, dw, webm["id"]) and b"-c:v|copy" in load(store, dw, webm["id"])
    assert f"{tw.id}#movie.mkv" not in {a["id"] for a in dw["artifacts"]}


def test_movie_without_audio(tmp_path, tools):
    store = Store(tmp_path / "s")
    data, _ = usm_file(audio=False)
    env = planning(store, tmp_path, {"v/m": data})
    with fake_tools(key=KEY):
        t, doc = run(MovieStage(), env, contract.sha256(data))
    assert sorted(a["id"] for a in doc["artifacts"]) == [f"{t.id}#movie.mkv", f"{t.id}#video.ivf"]
    assert art(doc, f"{t.id}#movie.mkv")["semantics"]["facts"]["tracks"] == [{"kind": "video", "channel": 0,
                                                                            "codec": "vp9"}]


EVERY = [(b"@SFV", 0, video_header(1, disp=(320, 180)), MPEG1, "video"),
         (b"@ALP", 0, video_header(1, 320, 184, disp=(320, 180), alpha_type=1), MPEG1, "video"),
         (b"@SFA", 0, audio_header(2), ADX, "audio"), (b"@SFA", 1, audio_header(2, 1), ADX, "audio"),
         (b"@SFA", 2, audio_header(4), HCA, None),
         (b"@SBT", 0, None, [sbt(0, 1000, 0, 1500, "こんにちは"), sbt(0, 1000, 2000, 1000, "b")], None),
         (b"@SBT", 1, None, [sbt(1, 100, 0, 150, "en")], None)]


def test_movie_every_stream_kind(tmp_path, tools):
    """MPEG-1 video and alpha, two ADX channels, an HCA channel (decoded by vgmstream with the key) and two subtitle
    channels: every stream as stored, the subtitles as JSON / SubRip / WebVTT, every track in the Matroska file."""
    from nnnotes import advvideo
    store = Store(tmp_path / "s")
    data = build(EVERY)
    env = planning(store, tmp_path, {"v/m": data})
    with fake_tools(key=KEY) as fake:
        t, doc = run(MovieStage(), env, contract.sha256(data))
    roles = sorted(contract.parse_artifact_id(a["id"])[1] for a in doc["artifacts"])
    assert roles == sorted(["video.m1v", "alpha.m1v", "audio.adx", "audio_1.adx", "audio_2.hca", "subtitle.json",
                            "subtitle.srt", "subtitle.vtt", "subtitle_1.json", "subtitle_1.srt", "subtitle_1.vtt",
                            "movie.mkv"])
    assert doc["items"][0]["status"] == "exported"
    streams = {s.name: s for s in advvideo.demux(data, KEY)}
    assert load(store, doc, f"{t.id}#alpha.m1v") == streams["alpha"].data == b"".join(MPEG1)
    assert load(store, doc, f"{t.id}#audio_2.hca") == b"".join(HCA)
    alpha = art(doc, f"{t.id}#alpha.m1v")
    assert alpha["content"]["mediaType"] == "video/mpeg" and alpha["semantics"]["kind"] == "video.alpha"
    assert alpha["semantics"]["facts"]["alphaType"] == 1 and alpha["semantics"]["facts"]["displayHeight"] == 180
    assert art(doc, f"{t.id}#audio_2.hca")["semantics"]["facts"]["cipher"] == 56
    assert json.loads(load(store, doc, f"{t.id}#subtitle.json")) == {"channel": 0, "records": [
        {"language": 0, "timeUnit": 1000, "start": 0, "duration": 1500, "text": "こんにちは", "terminator": 1},
        {"language": 0, "timeUnit": 1000, "start": 2000, "duration": 1000, "text": "b", "terminator": 1}]}
    assert load(store, doc, f"{t.id}#subtitle_1.srt") == b"1\n00:00:00,000 --> 00:00:01,500\nen\n\n"
    assert load(store, doc, f"{t.id}#subtitle.vtt").startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n"
                                                                 "こんにちは\n".encode())
    assert ["-i", "-o"] in fake.calls                                  # vgmstream decoded the HCA stream
    mkv = art(doc, f"{t.id}#movie.mkv")
    assert [(x["kind"], x["channel"], x["codec"]) for x in mkv["semantics"]["facts"]["tracks"]] == [
        ("video", 0, "mpeg1"), ("alpha", 0, "mpeg1"), ("audio", 0, "flac"), ("audio", 1, "flac"), ("audio", 2, "flac"),
        ("subtitle", 0, "srt"), ("subtitle", 1, "srt")]
    args = load(store, doc, mkv["id"]).split(b"\x00")[0]
    assert (b"|+genpts|-f|mpegvideo|-i|video.m1v|" in args and b"|-f|wav|-i|audio_2.wav|" in args
            and b"|-metadata:s:v:1|title=alpha|-disposition:v:1|0|" in args and b"|-map|6:s:0|" in args
            and b"|-c:s|srt|" in args)


def test_movie_webm_forms(tmp_path, tools):
    """format webm: MPEG-1 video with its alpha re-encoded as VP9 with alpha; the first audio stream only; no
    subtitles."""
    store = Store(tmp_path / "s")
    data = build(EVERY)
    env = planning(store, tmp_path, {"v/m": data})
    with fake_tools(key=KEY):
        t, doc = run(MovieStage(), env, contract.sha256(data), {"format": "webm"})
    webm = art(doc, f"{t.id}#movie.webm")
    assert webm["semantics"]["facts"]["tracks"] == [
        {"kind": "video", "channel": 0, "codec": "vp9", "source": "mpeg1"},
        {"kind": "alpha", "channel": 0, "codec": "vp9", "source": "mpeg1"},
        {"kind": "audio", "channel": 0, "codec": "opus", "source": "adx"}]
    args = load(store, doc, webm["id"])
    assert b"alphamerge,format=yuva420p" in args and b"|-c:v|libvpx-vp9|-b:v|0|-crf|20|" in args
    assert b"audio_1" not in args and b"srt" not in args


def test_movie_subtitles_that_cannot_be_read(tmp_path, tools):
    """Subtitle bytes that are not records: stored as they are (`.sbt`); a text that is not UTF-8: the JSON only.
    Either way the movie is `unsupported` with its other artifacts, and the Matroska file has no track for them."""
    store = Store(tmp_path / "s")
    data = build([(b"@SFV", 0, None, VP9, "video"), (b"@SBT", 0, None, [rand(0x30, 20)], None),
                  (b"@SBT", 1, None, [sbt(0, 1000, 0, 10, b"\x82\xa0")], None)])
    env = planning(store, tmp_path, {"v/m": data})
    with fake_tools(key=KEY):
        t, doc = run(MovieStage(), env, contract.sha256(data))
    (item,) = doc["items"]
    assert item["status"] == "unsupported" and item["reason"]["code"] == "unsupported.usm.subtitle"
    assert sorted(contract.parse_artifact_id(a["id"])[1] for a in doc["artifacts"]) == [
        "movie.mkv", "subtitle.sbt", "subtitle_1.json", "video.ivf"]
    assert sorted(item["artifacts"]) == sorted(a["id"] for a in doc["artifacts"])
    assert json.loads(load(store, doc, f"{t.id}#subtitle_1.json"))["records"][0]["textHex"] == "82a0"
    assert art(doc, f"{t.id}#movie.mkv")["semantics"]["facts"]["tracks"] == [{"kind": "video", "channel": 0,
                                                                            "codec": "vp9"}]


@pytest.mark.parametrize("streams, extra, code", [
    ((), [chunk(b"@CUE", rand(0x40, 6))], "unsupported.usm.stream"),
    ([(b"@ALP", 0, video_header(10), MPEG1, "video")], (), "unsupported.usm.codec"),
    ([(b"@SFA", 1, audio_header(7), ADX, "audio")], (), "unsupported.usm.codec"),
])
def test_unsupported_movies(tmp_path, tools, streams, extra, code):
    store = Store(tmp_path / "s")
    data, _ = usm_file(streams=streams, extra=extra)
    env = planning(store, tmp_path, {"v/m": data})
    with fake_tools(key=KEY):
        t, doc = run(MovieStage(), env, contract.sha256(data))
    (item,) = doc["items"]
    assert item["status"] == "unsupported" and item["reason"]["code"] == code and doc["artifacts"] == []


def test_movie_held_in_a_bundle(tmp_path, tools):
    """A USM artifact of unity.export is a subject like a raw USM (one task for both copies), named by the keys of
    its bundle that end in the movie asset's name; it waits for unity.export."""
    store = Store(tmp_path / "s")
    held, _ = usm_file(audio=False)
    raw, _ = usm_file()
    env = planning(store, tmp_path, {"cri_assets_cri/video/m": raw},
                   {"Cri/Video/m": ["cri_assets_cri/video/m"], "EmbCri/Video/common/warmup": ["embcri_video_common"],
                    "EmbCri/Video/common/other": ["embcri_video_common"]}, bundles=("embcri_video_common",))
    tid = export_result(store, env, "embcri_video_common", {}, {"warmup": held, "loose": raw})
    stage = MovieStage()
    sha, raw_sha = contract.sha256(held), contract.sha256(raw)
    assert stage.subjects(env) == sorted([sha, raw_sha])
    assert stage.names(env) == {sha: ["EmbCri/Video/common/warmup"], raw_sha: ["Cri/Video/m", "loose"]}
    assert stage.depends(sha, env) == [tid] and stage.depends(raw_sha, env) == [tid]
    with fake_tools(key=KEY):
        t = describe(stage, sha, None, env)
        assert t.input("usm").locators == ({"kind": "store"},) and sorted(i.role for i in t.inputs) == ["boot", "usm"]
        t, doc = run(stage, env, sha)
    assert doc["items"][0]["status"] == "exported" and load(store, doc, f"{t.id}#video.ivf") == b"".join(VP9)
    waiting = Env(store, env.facts)
    waiting.waiting(tid)
    with pytest.raises(Pending, match=tid):
        stage.subjects(waiting)


def test_movie_parameters():
    assert MovieStage().normalize({"format": "webm", "flac": {"level": 3}}) == {"format": "webm"}
    for bad in ({"format": "avi"}, {"flac": {"level": -1}}, {"x": 1}, {"format": "webm", "flac": {"level": 99}}):
        with pytest.raises(ValueError):
            MovieStage().normalize(bad)


# ---------------------------------------------------------------- laziness and documentation
def test_stage_modules_import_nothing_heavy():
    code = ("import sys; import nnnotes.cristages as c; c.AudioStage().normalize(None); "
            "print(sorted(m for m in ('UnityPy', 'numpy', 'PIL', 'nnnotes.cri', 'nnnotes.advvideo', 'nnnotes.export')"
            " if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "[]"


def test_reason_codes_and_stages_are_documented():
    doc = (ROOT / "docs" / "stages.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+(?:\.[A-Za-z_]+)+)` \|", doc, re.M))
    text = "".join((ROOT / "src" / "nnnotes" / f).read_text(encoding="utf-8") for f in ("cristages.py", "advvideo.py"))
    raised = set(cristages.REASONS) | set(re.findall(r'"(unsupported\.[a-z_.]+)"', text))
    assert raised - documented == set() and raised == set(cristages.REASONS)
    usm_codes = {c for c in documented if c.startswith("unsupported.usm.")}
    assert usm_codes == {c for c in cristages.REASONS if c.startswith("unsupported.usm.")}
    for stage in (AudioStage, MovieStage):
        assert f"`{stage.name}`" in doc
    for atom in ("hca.decode", "flac.encode", "usm.demux", "movie.mux"):
        assert f"`{atom}`" in doc


# ---------------------------------------------------------------- golden records
class GoldenAudio(AudioStage):
    """cri.audio with the stand-in tools: its golden record follows the stage's own code."""

    @property
    def ATOMS(self) -> dict:
        return dict(FAKE_ATOMS)

    def run(self, task, store):
        with fake_tools():
            return super().run(task, store)


class GoldenMovie(MovieStage):
    @property
    def ATOMS(self) -> dict:
        from nnnotes.atoms import impl_id
        return {"usm.demux": impl_id(("numpy",), cristages.USM_DEMUX_REVISION), "movie.mux": "ffmpeg-fake/1",
                "hca.decode": "vgmstream-fake/1"}

    def run(self, task, store):
        with fake_tools(key=KEY):
            return super().run(task, store)


def _fixture(stage, role, data: bytes, params=None):
    def build(store):
        sha = store.put(data)
        boot = store.put(b"UnityFS boot data")
        return Task(stage.name, stage.version, sha, stage.normalize(params), stage.ATOMS,
                    (Input(role, sha, len(data), None, ({"kind": "store"},)),
                     Input("boot", boot, 17, None, ({"kind": "store"},))))
    return build


def audio_golden():
    """Provider of tests/golden/cri.audio.json."""
    s = GoldenAudio()
    return {"stage": s, "fixtures": {"sheet": _fixture(s, "acb", ACB),
                                     "level0": _fixture(s, "acb", ACB, {"flac": {"level": 0}})}, "libraries": []}


def movie_golden():
    """Provider of tests/golden/cri.movie.json."""
    s = GoldenMovie()
    data = usm_file()[0]
    alpha = usm_file(streams=[(b"@ALP", 0, video_header(1, alpha_type=1), MPEG1, "video")])[0]
    every = build([(b"@SFV", 0, video_header(1), MPEG1, "video"), (b"@SFA", 0, None, ADX, "audio"),
                   (b"@SFA", 1, audio_header(4), HCA, None), (b"@SBT", 0, None, [sbt(0, 1000, 0, 900, "a")], None)])
    return {"stage": s, "fixtures": {"mkv": _fixture(s, "usm", data), "webm": _fixture(s, "usm", data, {"format": "webm"}),
                                     "silent": _fixture(s, "usm", usm_file(audio=False)[0]),
                                     "alpha": _fixture(s, "usm", alpha),
                                     "alpha_webm": _fixture(s, "usm", alpha, {"format": "webm"}),
                                     "every_kind": _fixture(s, "usm", every)},
            "libraries": ["numpy"]}


def test_golden_providers_run_twice_alike(tmp_path):
    from nnnotes.stages import golden, golden_problems
    for provider in (audio_golden, movie_golden):
        p = provider()
        a = golden(p["stage"], p["fixtures"], Store(tmp_path / f"{p['stage'].name}a"), p["libraries"])
        b = golden(p["stage"], p["fixtures"], Store(tmp_path / f"{p['stage'].name}b"), p["libraries"])
        assert a == b and golden_problems(a, b, p["stage"]) == []
