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
cri.movie   subject: the sha256 of a USM: a raw file of the catalog (fact "raw", magic `CRID`) or held in a bundle
            (the `cri.usm` artifacts of unity.export: a movie asset's CriSerializedBytesAssetImpl bytes), so a movie
            stored in several places is converted once. Inputs "usm", "boot" (the USM masks and the HCA cipher use
            the same key). Artifacts, per stream as stored and unmasked (advvideo.demux), named by its kind and, past
            channel 0, its channel: `video.<ext>` / `alpha.<ext>` (`ivf` VP9, `m1v` MPEG-1, `h264` H.264),
            `audio.<ext>` (`adx`, `hca`), and for subtitles `subtitle.json` (the records as stored) with
            `subtitle.srt` and `subtitle.vtt`; then `movie.mkv`: every video and alpha stream copied, every audio
            stream as FLAC (ADX through ffmpeg, HCA through vgmstream), every subtitle channel as a SubRip track.
            Parameters `flac.level`, and `format` "webm", which makes `movie.webm` instead (advvideo.write_webm, the
            story videos' form).
            Atoms `usm.demux` (nnnotes), `movie.mux` (ffmpeg), `hca.decode` (vgmstream).

Each task has one item: object its task id, class "ACB" / "USM", status `exported` (its artifacts) or `unsupported`
(a reason code of docs/stages.md; a movie whose subtitles cannot be read keeps its other artifacts). An artifact names no object: layouts place it by its task (names(env) gives the
catalog keys and cue sheet names of each subject, for placing it under them).

Neither stage imports UnityPy, numpy or the decoders before a task runs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

from .apkset import ApkSet
from . import contract
from .atoms import impl_id
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

AUDIO, MOVIE, EXPORT = "cri.audio", "cri.movie", "unity.export"
ACB_MAGIC, AWB_MAGIC, USM_MAGIC = b"@UTF", b"AFS2", b"CRID"
ACB_KIND = "cri.acb"                      # semantics kind of the ACB artifacts of unity.export
USM_KIND = "cri.usm"                      # semantics kind of the USM artifacts of unity.export
BOOT_IN_APK = "assets/bin/Data/data.unity3d"
ACB_NAME = "sheet"                        # the stem of the ACB file vgmstream reads (names come from its cues)
TOOL_REVISION = 1
FORMATS = ("mkv", "webm")
FLAC_LEVEL = 8                            # ffmpeg's FLAC compression level by default (here and in cri.decode)
REASONS = ("unsupported.cri.awb_external", "unsupported.usm.codec", "unsupported.usm.stream",
           "unsupported.usm.subtitle")
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
    if not apk.is_dir() and apk.suffix.lower() not in (".apk", ".zip", ".apks", ".xapk"):
        sha, size = store.identify(apk)
        return Input("boot", sha, size, apk.name, ({"kind": "file", "path": str(apk.resolve())},))
    if apk.is_dir():
        with ApkSet(apk) as z:
            data = z.read(BOOT_IN_APK)
        sha = store.put(data)
        return Input("boot", sha, len(data), "data.unity3d", ({"kind": "store"},))
    apk_sha, _ = store.identify(apk)
    name = f"boot:{apk_sha}"
    known = store.named("files", name)
    if known is None or not store.has(*known):
        with ApkSet(apk) as z:
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
    groups of raw files one key loads together (an ACB with its AWB); and the keys whose dependency lists name each
    bundle."""

    def __init__(self, index: dict | None):
        self.keys: dict[str, set] = defaultdict(set)
        self.groups: dict[str, set] = defaultdict(set)
        self.bundle_keys: dict[str, set] = defaultdict(set)
        self.primary: set = set()
        if not index:
            return
        raw_of = {lid: f["stable"] for f in index.get("rawFiles", ()) for lid in f.get("locations", ())}
        bundle_of = {lid: f["stable"] for f in index.get("bundles", ()) for lid in f.get("locations", ())}
        for loc in index.get("locations", ()):
            self.primary.add(loc["primaryKey"])
            if loc.get("kind") in ("bundle", "raw"):
                continue
            deps = {raw_of[d] for d in loc.get("dependencies", ()) if d in raw_of}
            for s in deps:
                self.keys[s].add(loc["primaryKey"])
                self.groups[s] |= deps - {s}
            for d in loc.get("dependencies", ()):
                if d in bundle_of:
                    self.bundle_keys[bundle_of[d]].add(loc["primaryKey"])


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

    def _artifact(self, task, store, role: str, data: bytes, ext: str, kind: str, fmt: str, facts: dict,
                  media: str | None = None) -> dict:
        return contract.artifact(contract.artifact_id(task.id, role), store.add(data, ext, media),
                                 contract.provenance(task), {"kind": kind, "format": fmt, "facts": facts})


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
            for a in _export_artifacts(env.store, env.key(tid), ACB_KIND):
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


def _export_artifacts(store, key: str, kind: str) -> list[dict]:
    """The artifact records of semantics kind `kind` (cri.acb, cri.usm) of a unity.export result (a result without the
    kind is not parsed)."""
    try:
        data = store.result_path(key).read_bytes()
    except OSError:
        return []
    if f'"{kind}"'.encode() not in data:
        return []
    return [a for a in contract.loads(data)["artifacts"] if a["semantics"].get("kind") == kind]


# ---------------------------------------------------------------- cri.movie
USM_DEMUX_REVISION = 2
# stream codec -> (semantics format, media type) of its artifact
STREAM_FORMATS = {"vp9": ("ivf", "video/x-ivf"), "mpeg1": ("mpeg1video", "video/mpeg"), "h264": ("h264", "video/h264"),
                  "adx": ("adx", "audio/x-adx"), "hca": ("hca", "application/octet-stream")}
MOVIE_FORMATS = {"mkv": ("matroska", "video/x-matroska"), "webm": ("webm", "video/webm")}


class MovieStage(_CriStage):
    """cri.movie (module documentation)."""
    name = MOVIE
    version = 2
    after = (EXPORT,)
    CLASS = "USM"
    PARAMS = {"format": "mkv", "flac": {"level": FLAC_LEVEL}}

    @property
    def ATOMS(self) -> dict:
        return movie_atoms(_tool("ffmpeg"), _tool("vgmstream"))

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
        """{content id: {usm, depends, names}} of the raw USMs and the USM artifacts of the unity.export results."""
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

        def entry(inp: Input) -> dict:
            return out.setdefault(inp.sha256, {"usm": inp, "depends": set(), "names": set()})

        for stable, (inp, magic) in raw.items():
            if magic == USM_MAGIC:
                e = entry(Input("usm", inp.sha256, inp.size, inp.name, inp.locators))
                e["names"] |= cat.keys.get(stable) or {stable}
        for tid in tids:
            bundle = contract.parse_task_id(tid, EXPORT)[1]
            for a in _export_artifacts(env.store, env.key(tid), USM_KIND):
                c = a["content"]
                e = entry(Input("usm", c["sha256"], c["size"], None, ({"kind": "store"},)))
                e["depends"].add(tid)
                movie = (a["semantics"].get("facts") or {}).get("movie")
                keys = {k for k in cat.bundle_keys.get(bundle, ()) if movie and k.rsplit("/", 1)[-1] == movie}
                e["names"] |= keys or {movie or a["id"]}
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
        ff, vgm = _tool("ffmpeg"), _tool("vgmstream")
        self._check_atoms(task, movie_atoms(ff, vgm))
        usm = store.input_bytes(task.input("usm"))
        key = hca_key(store, task.input("boot"))
        try:
            streams = advvideo.demux(usm, key)
        except advvideo.Unsupported as e:
            return self._item(task, why=contract.reason(e.code, str(e)))
        arts, why, tracks = [], None, []
        for s in streams:
            if s.kind == "subtitle":
                made, problem = self._subtitle(task, store, s)
                arts += made
                if problem is None:
                    tracks.append(s)
                elif why is None:
                    why = contract.reason("unsupported.usm.subtitle", problem)
                continue
            ext = advvideo.EXTENSIONS[s.codec]
            fmt, media = STREAM_FORMATS[s.codec]
            if s.kind == "audio":
                try:
                    facts = advvideo.adx_info(s.data) if s.codec == "adx" else advvideo.hca_info(s.data)
                except (NotImplementedError, ValueError) as e:
                    return self._item(task, why=contract.reason("unsupported.usm.codec", f"{s.name}: {e}"))
                kind = "audio.stream"
            else:
                facts, kind = advvideo.video_facts(s), "video.stream" if s.kind == "video" else "video.alpha"
            arts.append(self._artifact(task, store, f"{s.name}.{ext}", s.data, ext, kind, fmt, facts, media))
            tracks.append(s)
        out_fmt = task.params["format"]
        with tempfile.TemporaryDirectory(prefix="nnnotes-usm-") as tmp:
            work = Path(tmp)
            dst = work / f"movie.{out_fmt}"
            try:
                if out_fmt == "mkv":
                    inputs = {s.name: advvideo.stream_input(s, work / "in", key, vgm) for s in tracks}
                    advvideo.run_ffmpeg(ff, advvideo.mkv_args(tracks, inputs, dst, task.params["flac"]["level"]),
                                        "mkv")
                else:
                    tracks = advvideo.write_webm(streams, dst, work / "in", ff, key, vgm)
            except advvideo.Unsupported as e:
                return self._item(task, why=contract.reason(e.code, str(e)))
            reencoded = out_fmt == "webm" and advvideo.webm_reencoded(tracks)
            facts = {"tracks": [_track(s, out_fmt, reencoded) for s in tracks]}
            fmt, media = MOVIE_FORMATS[out_fmt]
            arts.append(self._artifact(task, store, dst.name, dst.read_bytes(), out_fmt, "video.movie", fmt, facts,
                                       media))
        return self._item(task, arts, why)

    def _subtitle(self, task, store, s) -> tuple[list[dict], str | None]:
        """The artifacts of a subtitle channel and why it is not a track (None when it is): the records as JSON, and
        as SubRip and WebVTT when every text is UTF-8; the bytes as stored (`.sbt`) when they are not records."""
        from . import advvideo
        from .jsonio import dumps
        try:
            records = advvideo.subtitle_records(s.data)
        except ValueError as e:
            return [self._artifact(task, store, f"{s.name}.sbt", s.data, "sbt", "subtitle.stream", "sbt",
                                   {"channel": s.channel})], f"{s.name}: {e}"
        facts = {"channel": s.channel, "records": len(records)}
        doc = dumps(advvideo.subtitle_json(s.channel, records), indent=1, ensure_ascii=False).encode()
        arts = [self._artifact(task, store, f"{s.name}.json", doc, "json", "subtitle.records", "json", facts)]
        try:
            srt, vtt = advvideo.subtitle_srt(records), advvideo.subtitle_webvtt(records)
        except UnicodeDecodeError:
            return arts, f"{s.name}: a text that is not UTF-8"
        arts.append(self._artifact(task, store, f"{s.name}.srt", srt.encode(), "srt", "subtitle.track", "srt", facts,
                                   "application/x-subrip"))
        arts.append(self._artifact(task, store, f"{s.name}.vtt", vtt.encode(), "vtt", "subtitle.track", "webvtt",
                                   facts, "text/vtt"))
        return arts, None


def movie_atoms(ffmpeg: str, vgmstream: str) -> dict:
    """The atoms of a cri.movie task: the demuxer, ffmpeg (the mux, ADX decoding, FLAC / Opus / VP9 encoding) and
    vgmstream (HCA decoding)."""
    return {"usm.demux": impl_id(("numpy",), USM_DEMUX_REVISION), "movie.mux": tool_id("ffmpeg", ffmpeg),
            "hca.decode": tool_id("vgmstream", vgmstream)}


def _track(s, fmt: str, reencoded: bool) -> dict:
    """A track of the movie file: kind, channel, codec, and `source` (the stream's codec) when it was converted."""
    if s.kind in ("video", "alpha"):
        if reencoded:
            return {"kind": s.kind, "channel": s.channel, "codec": "vp9", "source": s.codec}
        return {"kind": s.kind, "channel": s.channel, "codec": s.codec}
    if s.kind == "audio":
        return {"kind": "audio", "channel": s.channel, "codec": "flac" if fmt == "mkv" else "opus", "source": s.codec}
    return {"kind": "subtitle", "channel": s.channel, "codec": "srt"}


STAGES = (AudioStage, MovieStage)
