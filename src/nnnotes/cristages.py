"""Stages `cri.audio` (CRI cue sheets -> FLAC per stream) and `cri.movie` (CRI movies -> their streams and a Matroska
file), one task per content.

cri.audio   subject: the sha256 of an ACB ("cri.audio:<sha256>"). The ACB is a raw file of the catalog (magic
            `@UTF`, fact "raw") or held in a bundle: the `cri.acb` artifacts of unity.export (a SplitAcbData's joined
            chunks, a CriSerializedBytesAssetImpl's bytes), so a cue sheet stored in both forms, or in several
            bundles, is decoded once. Inputs: "acb"; "awb" when the catalog pairs a raw ACB with an AWB (`AFS2`: its
            streamed waveforms, which the decoder does not read: `unsupported.cri.awb_external`); "boot", the game's
            boot data (fact "boot", see boot_input), from which the HCA keycode is read when the task runs: the key
            itself never enters a key, a record or a log. Atoms: `hca.decode` (vgmstream) and `flac.encode` (ffmpeg),
            each named by the version the tool reports and the sha256 of its executable. Artifacts
            "cri.audio:<sha256>#<file>": one FLAC per vgmstream stream, `cues.json` and `streams.json`, the bytes
            cri.decode writes for the sheet. Parameter `flac.level` (ffmpeg's FLAC compression level; every level
            decodes to the same samples).
cri.movie   subject: the sha256 of a USM (fact "raw", magic `CRID`). Inputs "usm", "boot". Artifacts `video.ivf` and
            `audio.adx` (the streams as stored, unmasked: advvideo.demux; no audio stream, no `audio.adx`) and
            `movie.mkv` (the VP9 stream copied, the ADX audio as FLAC); parameter `format` "webm" makes `movie.webm`
            instead (VP9 copied, Opus audio as the story videos have it). Atoms `usm.demux` (nnnotes), `movie.mux`
            (ffmpeg).

Each task has one item: object its task id, class "ACB" / "USM", status `exported` (its artifacts) or `unsupported`
(a reason code of docs/stages.md). An artifact names no object: layouts place it by its task (names(env) gives the
catalog keys and cue sheet names of each subject, for placing it under them).

Neither stage imports UnityPy, numpy or the decoders before a task runs.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import threading
import zipfile
from collections import defaultdict
from pathlib import Path

from . import contract
from .atoms import impl_id
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

AUDIO, MOVIE, EXPORT = "cri.audio", "cri.movie", "unity.export"
ACB_MAGIC, AWB_MAGIC, USM_MAGIC = b"@UTF", b"AFS2", b"CRID"
ACB_KIND = "cri.acb"                      # semantics kind of the ACB artifacts of unity.export
BOOT_IN_APK = "assets/bin/Data/data.unity3d"
ACB_NAME = "sheet"                        # the stem of the ACB file vgmstream reads (names come from its cues)
TOOL_REVISION = 1
FORMATS = ("mkv", "webm")
WEBM_AUDIO_BITRATE = "192k"               # as advvideo.AUDIO_BITRATE
FLAC_LEVEL = 8                            # ffmpeg's FLAC compression level by default (here and in cri.decode)
REASONS = ("unsupported.cri.awb_external", "unsupported.usm.codec", "unsupported.usm.alpha",
           "unsupported.usm.subtitle", "unsupported.usm.audio_streams")
_lock = threading.Lock()
_magic: dict[str, bytes] = {}             # content id -> first 4 bytes
_tools: dict[tuple, str] = {}             # (executable, size, mtime) -> tool id
_keys: dict[str, int] = {}                # boot data content id -> HCA keycode


# ---------------------------------------------------------------- inputs shared by the stages
def boot_input(store, apk) -> Input | None:
    """The input "boot" of the CRI stages: the boot data of an APK (`assets/bin/Data/data.unity3d`, where the HCA
    keycode is read), stored in `store` once; the APK is hashed once (store.identify memo) and its boot data is
    remembered by the APK's content id. A path that is not an .apk / .zip is the boot data itself. None without an
    APK."""
    if apk is None:
        return None
    apk = Path(apk)
    if apk.suffix.lower() not in (".apk", ".zip"):
        sha, size = store.identify(apk)
        return Input("boot", sha, size, apk.name, ({"kind": "file", "path": str(apk.resolve())},))
    apk_sha, _ = store.identify(apk)
    name = f"boot:{apk_sha}"
    known = store.named("files", name)
    if known is None or not store.has(*known):
        with zipfile.ZipFile(apk) as z:
            data = z.read(BOOT_IN_APK)
        known = store.identify(store.path(store.put(data)), "files", name)
    return Input("boot", known[0], known[1], "data.unity3d", ({"kind": "store"},))


def _boot(env) -> Input:
    inp = env.facts.get("boot")
    if inp is None:
        raise ValueError("the HCA key is read from the game's boot data: set [paths] apk")
    return inp


def hca_key(store, inp: Input) -> int:
    """The HCA keycode in the boot data `inp` (crikey.find_key), read once per content and process."""
    with _lock:
        if inp.sha256 in _keys:
            return _keys[inp.sha256]
    from . import crikey
    key = crikey.find_key(store.open_input(inp))
    with _lock:
        _keys[inp.sha256] = key
    return key


def tool_version(which: str, exe: str) -> str:
    """The version an external tool reports: vgmstream-cli -V (its JSON "version"), ffmpeg -version (the word after
    "version"); "unknown" when it reports none."""
    if which == "vgmstream":
        r = subprocess.run([exe, "-V"], capture_output=True, text=True)
        try:
            return str(json.loads(r.stdout)["version"])
        except (ValueError, KeyError, TypeError):
            return "unknown"
    r = subprocess.run([exe, "-version"], capture_output=True, text=True)
    words = (r.stdout.splitlines() or [""])[0].split()
    return words[2] if len(words) > 2 and words[1] == "version" else "unknown"


def tool_id(which: str, exe: str) -> str:
    """The implementation id of an external tool: `<tool>-<version>+sha256.<sha256 of the executable>/<revision>`,
    computed once per executable file and process."""
    p = Path(exe).resolve()
    st = p.stat()
    k = (str(p), st.st_size, st.st_mtime_ns)
    with _lock:
        hit = _tools.get(k)
    if hit is None:
        with open(p, "rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        hit = f"{which}-{tool_version(which, str(p))}+sha256.{sha}/{TOOL_REVISION}"
        with _lock:
            _tools[k] = hit
    return hit


def _tool(which: str) -> str:
    from .config import tool
    return tool(which, "vgmstream-cli" if which == "vgmstream" else "ffmpeg")


def _magic_of(store, inp: Input, name: str) -> bytes:
    """The first 4 bytes of a raw file, from a local copy (the store, the cache, a file); one that is not fetched
    yet is Pending("fetch:<name>")."""
    with _lock:
        m = _magic.get(inp.sha256)
    if m is None:
        from .store import InputMissing
        local = Input(inp.role, inp.sha256, inp.size, inp.name,
                      tuple(loc for loc in inp.locators if loc.get("kind") != "catalog"))
        try:
            path = store.open_input(local)
        except InputMissing:
            raise Pending(f"fetch:{inp.name or name}") from None
        with open(path, "rb") as f:
            m = f.read(4)
        with _lock:
            _magic[inp.sha256] = m
    return m


def _raw(env) -> dict[str, tuple[Input, bytes]]:
    """{stable name: (Input, magic)} of the raw files of the fact "raw"; files not fetched yet: Pending (the first by
    name, after all were looked at)."""
    raw = env.facts.get("raw") or {}
    out, waiting = {}, []
    for stable in sorted(raw):
        try:
            inp = raw[stable]
            if inp is None:
                raise Pending(f"fetch:{stable}")
            if isinstance(inp, Exception):
                raise inp
            out[stable] = (inp, _magic_of(env.store, inp, stable))
        except Pending as p:
            waiting.append(p)
    if waiting:
        raise waiting[0]
    return out


class _Catalog:
    """What the catalog index says about raw files: the keys that load each (their dependency lists name it) and the
    groups of raw files one key loads together (an ACB with its AWB)."""

    def __init__(self, index: dict | None):
        self.keys: dict[str, set] = defaultdict(set)
        self.groups: dict[str, set] = defaultdict(set)
        self.primary: set = set()
        if not index:
            return
        raw_of = {lid: f["stable"] for f in index.get("rawFiles", ()) for lid in f.get("locations", ())}
        for loc in index.get("locations", ()):
            self.primary.add(loc["primaryKey"])
            if loc.get("kind") in ("bundle", "raw"):
                continue
            deps = {raw_of[d] for d in loc.get("dependencies", ()) if d in raw_of}
            for s in deps:
                self.keys[s].add(loc["primaryKey"])
                self.groups[s] |= deps - {s}


class _CriStage(Stage):
    """What the two stages share: subjects by content from an index built once per planning state."""
    CLASS = ""

    def __init__(self):
        self._memo = None
        self._catalog = (None, _Catalog(None))

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.__init__()

    def catalog(self, env) -> _Catalog:
        index = env.facts.get("index")
        if self._catalog[0] is not index:
            self._catalog = (index, _Catalog(index))
        return self._catalog[1]

    def index(self, env) -> dict:
        raise NotImplementedError

    def subjects(self, env) -> list[str]:
        return sorted(self.index(env))

    def names(self, env) -> dict[str, list[str]]:
        """{subject: the catalog keys that load its content, or the names it is known by}, for placing a subject's
        artifacts under them (layout time only: names never enter a key)."""
        return {s: sorted(e["names"]) for s, e in sorted(self.index(env).items())}

    def _entry(self, subject: str, env) -> dict:
        idx = self.index(env)
        if subject not in idx:
            raise ValueError(f"{self.name}: no input has the content {subject}")
        return idx[subject]

    def depends(self, subject: str, env) -> list[str]:
        return sorted(self._entry(subject, env)["depends"])

    def _item(self, task, artifacts=(), why=None) -> Output:
        status = "exported" if why is None else "unsupported"
        return Output(list(artifacts), [contract.item(task.id, status, artifacts=[a["id"] for a in artifacts],
                                                      why=why, cls=self.CLASS)])

    def _check_atoms(self, task, want: dict) -> None:
        if task.atoms != want:
            diff = sorted(k for k in set(task.atoms) | set(want) if task.atoms.get(k) != want.get(k))
            raise IncompatibleTask(f"task {task.id}: {', '.join(diff)} differ here: "
                                   + "; ".join(f"{k} {want.get(k)} (task: {task.atoms.get(k)})" for k in diff))

    def _artifact(self, task, store, role: str, data: bytes, ext: str, kind: str, fmt: str, facts: dict) -> dict:
        return contract.artifact(contract.artifact_id(task.id, role), store.add(data, ext), contract.provenance(task),
                                 {"kind": kind, "format": fmt, "facts": facts})


def _flac_params(p) -> dict:
    flac = dict(p or {})
    unknown = sorted(set(flac) - {"level"})
    if unknown:
        raise ValueError(f"unknown flac parameters {', '.join(unknown)}")
    level = flac.get("level", FLAC_LEVEL)
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 12:
        raise ValueError(f"flac level {level!r}: expected 0-12")
    return {"level": level}


# ---------------------------------------------------------------- cri.audio
class AudioStage(_CriStage):
    """cri.audio (module documentation)."""
    name = AUDIO
    version = 1
    after = (EXPORT,)
    CLASS = "ACB"
    PARAMS = {"flac": {"level": FLAC_LEVEL}}

    @property
    def ATOMS(self) -> dict:
        return {"hca.decode": tool_id("vgmstream", _tool("vgmstream")), "flac.encode": tool_id("ffmpeg", _tool("ffmpeg"))}

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        return {"flac": _flac_params(p.get("flac"))}

    def index(self, env) -> dict:
        """{content id: {acb, awb, depends, names}} of the raw ACBs and the ACB artifacts of the unity.export
        results."""
        pending = env.pending(EXPORT)
        if pending:
            raise Pending(pending[0])
        tids = env.tasks(EXPORT)
        raw = _raw(env)
        cat = self.catalog(env)
        sig = (tuple((s, i.sha256) for s, (i, _m) in raw.items()), tuple(env.key(t) for t in tids), id(cat))
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        out: dict[str, dict] = {}

        def entry(sha: str) -> dict:
            return out.setdefault(sha, {"acb": None, "awb": None, "depends": set(), "names": set()})

        for stable, (inp, magic) in raw.items():
            if magic != ACB_MAGIC:
                continue
            e = entry(inp.sha256)
            if e["acb"] is None:
                e["acb"] = Input("acb", inp.sha256, inp.size, inp.name, inp.locators)
            e["names"] |= cat.keys.get(stable) or {stable}
            for other in sorted(cat.groups.get(stable, ())):
                if other in raw and raw[other][1] == AWB_MAGIC and e["awb"] is None:
                    a = raw[other][0]
                    e["awb"] = Input("awb", a.sha256, a.size, a.name, a.locators)
        for tid in tids:
            for a in _acb_artifacts(env.store, env.key(tid)):
                c = a["content"]
                e = entry(c["sha256"])
                if e["acb"] is None:
                    e["acb"] = Input("acb", c["sha256"], c["size"], None, ({"kind": "store"},))
                e["depends"].add(tid)
                sheet = (a["semantics"].get("facts") or {}).get("cueSheet")
                if sheet:
                    key = f"Cri/Sound/{sheet}"
                    e["names"].add(key if key in cat.primary else sheet)
        self._memo = (sig, out)
        return out

    def inputs(self, subject: str, env) -> list[Input]:
        e = self._entry(subject, env)
        return [e["acb"], *([e["awb"]] if e["awb"] is not None else []), _boot(env)]

    def uses(self, subject: str, env) -> dict:
        return self.ATOMS

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the ACB's size (a whole catalog: about 2 CPU seconds per MB, the tools' processes included)."""
        size = sum(i.size for i in inputs if i.role == "acb")
        return Cost(0.3 + size * 2.0e-6, (64 << 20) + 8 * size)

    def run(self, task, store) -> Output:
        from . import cri
        vgm, ff = _tool("vgmstream"), _tool("ffmpeg")
        self._check_atoms(task, {"hca.decode": tool_id("vgmstream", vgm), "flac.encode": tool_id("ffmpeg", ff)})
        if any(i.role == "awb" for i in task.inputs):
            return self._item(task, why=contract.reason(
                "unsupported.cri.awb_external", "the cue sheet's waveforms are streamed from an external AWB"))
        acb = store.input_bytes(task.input("acb"))
        key = hca_key(store, task.input("boot"))
        level = int(task.params["flac"]["level"])
        with tempfile.TemporaryDirectory(prefix="nnnotes-cri-") as tmp:
            out = Path(tmp)
            d = cri.decode_acb_bytes(acb, None, key, "flac", out, name=ACB_NAME, flac_level=level,
                                     workers=cri.default_workers(), vgmstream=vgm, ffmpeg=ff)
            arts = [self._artifact(task, store, s["file"], (out / s["file"]).read_bytes(), "flac", "audio.stream",
                                   "flac", {k: v for k, v in s.items() if k != "file"}) for s in d.streams]
            arts.append(self._artifact(task, store, "cues.json", (out / "cues.json").read_bytes(), "json",
                                       "cri.cues", "json", {"cues": len(d.cues)}))
            arts.append(self._artifact(task, store, "streams.json", (out / "streams.json").read_bytes(), "json",
                                       "cri.streams", "json", {"streams": len(d.streams)}))
        return self._item(task, arts)


def _acb_artifacts(store, key: str) -> list[dict]:
    """The `cri.acb` artifact records of a unity.export result (a result without the kind is not parsed)."""
    try:
        data = store.result_path(key).read_bytes()
    except OSError:
        return []
    if b'"cri.acb"' not in data:
        return []
    return [a for a in contract.loads(data)["artifacts"] if a["semantics"].get("kind") == ACB_KIND]


# ---------------------------------------------------------------- cri.movie
class MovieStage(_CriStage):
    """cri.movie (module documentation)."""
    name = MOVIE
    version = 1
    CLASS = "USM"
    PARAMS = {"format": "mkv", "flac": {"level": FLAC_LEVEL}}

    @property
    def ATOMS(self) -> dict:
        return {"usm.demux": impl_id(("numpy",), 1), "movie.mux": tool_id("ffmpeg", _tool("ffmpeg"))}

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        fmt = p.get("format", "mkv")
        if fmt not in FORMATS:
            raise ValueError(f"stage {self.name}: format {fmt!r}: expected {' or '.join(FORMATS)}")
        if fmt == "webm":                       # Opus audio: the FLAC level does not apply
            if "flac" in p:
                _flac_params(p["flac"])
            return {"format": "webm"}
        return {"format": fmt, "flac": _flac_params(p.get("flac"))}

    def index(self, env) -> dict:
        raw = _raw(env)
        cat = self.catalog(env)
        sig = (tuple((s, i.sha256) for s, (i, _m) in raw.items()), id(cat))
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        out: dict[str, dict] = {}
        for stable, (inp, magic) in raw.items():
            if magic != USM_MAGIC:
                continue
            e = out.setdefault(inp.sha256, {"usm": Input("usm", inp.sha256, inp.size, inp.name, inp.locators),
                                            "depends": set(), "names": set()})
            e["names"] |= cat.keys.get(stable) or {stable}
        self._memo = (sig, out)
        return out

    def inputs(self, subject: str, env) -> list[Input]:
        return [self._entry(subject, env)["usm"], _boot(env)]

    def uses(self, subject: str, env) -> dict:
        return self.ATOMS

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the USM's size (a whole catalog: about 0.1 CPU seconds per MB, ffmpeg included)."""
        size = sum(i.size for i in inputs if i.role == "usm")
        return Cost(0.3 + size * 1.1e-7, (96 << 20) + 12 * size)

    def run(self, task, store) -> Output:
        from . import advvideo
        ff = _tool("ffmpeg")
        self._check_atoms(task, {"usm.demux": impl_id(("numpy",), 1), "movie.mux": tool_id("ffmpeg", ff)})
        usm = store.input_bytes(task.input("usm"))
        why = usm_limits(usm)
        if why is not None:
            return self._item(task, why=contract.reason(*why))
        streams = advvideo.demux(usm, hca_key(store, task.input("boot")))
        video, audio = streams["video"], streams.get("audio")
        ivf = ivf_info(video)
        if ivf is None:
            return self._item(task, why=contract.reason(
                "unsupported.usm.codec", f"the video stream is not VP9 in IVF: {video[:4].hex()} {video[8:12].hex()}"))
        adx = None
        if audio is not None:
            try:
                adx = advvideo.adx_info(audio)
            except NotImplementedError as e:
                return self._item(task, why=contract.reason("unsupported.usm.codec", str(e)))
        fmt = task.params["format"]
        arts = [self._artifact(task, store, "video.ivf", video, "ivf", "video.stream", "ivf", ivf)]
        if audio is not None:
            arts.append(self._artifact(task, store, "audio.adx", audio, "adx", "audio.stream", "adx", adx))
        with tempfile.TemporaryDirectory(prefix="nnnotes-usm-") as tmp:
            work = Path(tmp)
            (work / "video.ivf").write_bytes(video)
            if audio is not None:
                (work / "audio.adx").write_bytes(audio)
            dst = work / f"movie.{fmt}"
            args = mux_args(work / "video.ivf", work / "audio.adx" if audio is not None else None, dst, fmt,
                            (task.params.get("flac") or {}).get("level", FLAC_LEVEL))
            r = subprocess.run([ff, *args], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg {fmt} failed: {r.stderr[-2000:]}")
            facts = {"video": "vp9", "audio": None if audio is None else ("flac" if fmt == "mkv" else "opus")}
            arts.append(self._artifact(task, store, dst.name, dst.read_bytes(), fmt, "video.movie",
                                       "matroska" if fmt == "mkv" else "webm", facts))
        return self._item(task, arts)


def usm_limits(data: bytes) -> tuple[str, str] | None:
    """(reason code, message) when a USM holds a stream cri.movie does not convert (an alpha or subtitle stream, a
    second audio or video stream, a stream of another kind), else None."""
    from .advvideo import chunks
    if data[:4] != USM_MAGIC:
        return "unsupported.usm.codec", f"not a USM: {data[:4].hex()}"
    for sig, channel, dtype, _payload in chunks(data):
        if dtype != 0 or sig == USM_MAGIC:
            continue
        name = sig.decode("latin-1")
        if sig == b"@ALP":
            return "unsupported.usm.alpha", f"{name} stream"
        if sig == b"@SBT":
            return "unsupported.usm.subtitle", f"{name} stream"
        if sig == b"@SFA" and channel != 0:
            return "unsupported.usm.audio_streams", f"{name} channel {channel}"
        if sig not in (b"@SFV", b"@SFA") or channel != 0:
            return "unsupported.usm.codec", f"{name} channel {channel}"
    return None


def ivf_info(video: bytes) -> dict | None:
    """The facts of a VP9 stream in an IVF container (None when it is not one): size, frame rate, frame count."""
    if len(video) < 32 or video[:4] != b"DKIF" or video[8:12] != b"VP90":
        return None
    w, h, rate, scale, frames = struct.unpack_from("<HHIII", video, 12)
    return {"codec": "vp9", "width": w, "height": h, "frameRate": [rate, scale], "frames": frames}


def mux_args(ivf: Path, adx: Path | None, dst: Path, fmt: str, flac_level: int = FLAC_LEVEL) -> list[str]:
    """ffmpeg arguments (after the executable) of the movie file: the VP9 stream copied; the ADX audio as FLAC in
    Matroska (`flac_level`), as Opus in WebM; no metadata, bit-exact container and codec flags."""
    inputs, maps = ["-f", "ivf", "-i", str(ivf)], ["-map", "0:v:0"]
    if adx is not None:
        inputs += ["-f", "adx", "-i", str(adx)]
        codec = (["-c:a", "flac", "-compression_level", str(int(flac_level))] if fmt == "mkv"
                 else ["-c:a", "libopus", "-b:a", WEBM_AUDIO_BITRATE])
        maps += ["-map", "1:a:0", *codec, "-flags:a", "+bitexact"]
    return ["-hide_banner", "-y", "-loglevel", "error", *inputs, *maps, "-c:v", "copy", "-map_metadata", "-1",
            "-fflags", "+bitexact", "-f", "matroska" if fmt == "mkv" else "webm", str(dst)]


STAGES = (AudioStage, MovieStage)
