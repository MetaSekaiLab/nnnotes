"""Live charts -> one static site: the ournotes-player chart player plus per-chart data.

    <site>/index.html, chart-list.js           the player's chart-list page (the files of its PLAYER_PAGE_DIR, the
                                               source import pointed at the bundle); without a query it lists the
                                               charts, with ?music=<musicId>&difficulty=<d> it plays one
    <site>/ournotes-player.element.min.js      the player's built bundle (PLAYER_BUNDLES, with its source map)
    <site>/charts.json                         the chart index (facts for a listing in every language, regions,
                                               manifest path, sizes)
    <site>/charts/<musicId>_<difficulty>.json  chart manifest: every path the player reads -> {asset, size}, or,
                                               for a large JSON object, {parts: [[key, asset, size], ...], size};
                                               `regions`: the regions it serves
    <site>/charts/<region>/<id>.json           the manifest of a region whose chart inputs differ from the first
                                               region's (only where its files differ)
    <site>/assets/<sha256>.<ext>               content-addressed files (text assets as UTF-8, JSON without
                                               whitespace); charts share the note skins, effects, SE sheets, band
                                               stages and the common parts of the scene, each stored once
    <site>/models.json, models/<id>.json       Live2D model index and manifests (webmodel.py), same entry forms and
                                               the same assets/; write_index keeps what either kind references
    <site>/live2d/                             the player's Live2D model page and its bundle, when the player has
                                               them (LIVE2D_PAGE_DIR, LIVE2D_BUNDLES)

Per music (the four difficulties share the scene, the sounds and the note assets): score + BGM decode, live sounds and
scene extracted once into a temporary base directory, the note assets once per build (they depend on no music); per
difficulty a directory of hard links to those plus its own score and start canvas, composed as `live.build` composes
a live directory. Per chart: the read set (READ_SET_SCRIPT of the player: the files the player reads for the whole
chart, its own code run in Node over the live directory), those files collected (GLES3 shader programs only,
filtered shaders.json), the BGM transcoded to the web format (`audio_format`, once per music; note SE, cheers and
voices stay FLAC: small and shared), ingest. Musics run in parallel worker processes (catalog cache writes serialized
by a lock); temporary directories are deleted after use. A chart whose manifest exists is skipped unless `force`.
Same inputs give byte-identical outputs. `audio=False` exports no audio file (the player then runs the chart on its
own clock, silent).

Regions: one build can serve several regions. The regions serve the same catalog for a language, so the chart files
depend on a region's master data only: regions whose chart tables (LIVE_TABLES) are byte-identical share one manifest
(charts/<id>.json for the first region's group); another group's charts go to charts/<region>/<id>.json (region = the
first of the group), and such a manifest whose files equal the shared one's is dropped, its regions joining the
shared manifest. A region offers the charts its master data has a MasterLiveMusicScore row for. The listing texts
(title, band names) are in every language of the text tables (languages.LANGUAGES); `title` / `bands` in
[catalog] language.

A JSON object larger than SPLIT_MIN_BYTES is stored per top-level key: each value's text (the same minified encoding)
is its own asset, and the player rebuilds the file as `{"k1":` + t1 + `,"k2":` + t2 + `}`, so the values the charts
share (most of livescene/scene.json) are stored once.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import jsonio, languages, live
from .config import Config, ConfigError, tool, use
from .score import DIFFICULTIES, master_table

SITE_FORMAT = 2
AUDIO_EXT = {".flac", ".ogg", ".opus", ".wav", ".m4a", ".mp3"}
# BGM encodings for the web (ffmpeg). Chromium's decodeAudioData decodes each sample-aligned with the FLAC (encoder
# delay / pre-skip removed). Default AAC-LC 160 kbit/s in MP4: decodes in Safari / iOS as well.
WEB_AUDIO = {
    "aac": (".m4a", ["-c:a", "aac", "-b:a", "160k"]),
    "opus": (".opus", ["-c:a", "libopus", "-b:a", "128k"]),
    "vorbis": (".ogg", ["-c:a", "libvorbis", "-q:a", "5"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "flac": (".flac", None),
}
DEFAULT_AUDIO_FORMAT = "aac"
SPLIT_MIN_BYTES = 512 * 1024
SPLIT_KEY = re.compile(r"[A-Za-z0-9_$.\-]+")       # keys whose JSON text is the same in Python and JavaScript
TRACE_QUALITY = 1                                  # the quality the read set is taken at (the player default, Middle)
FLOWS = ("direct",)                                # the start flow the player has
# files of the ournotes-player checkout / package the site uses
READ_SET_SCRIPT = "scripts/read-set.mjs"           # node <script> <live dir> -> JSON list of the paths the player reads
PLAYER_BUNDLES = ("dist/ournotes-player.element.min.js",)
PLAYER_PAGE_DIR = "examples/chart-list"
# module specifiers of the page that name the player's sources -> the bundle copied next to the page
PAGE_IMPORTS = {"../../src/element.js": "./ournotes-player.element.min.js"}
# the player's Live2D model page (when the player has it): its files and bundle go to SITE/<LIVE2D_PAGE_SITE_DIR>/
LIVE2D_PAGE_DIR = "examples/live2d"
LIVE2D_PAGE_SITE_DIR = "live2d"
LIVE2D_BUNDLES = ("dist/ournotes-player.live2d.element.min.js",)
LIVE2D_PAGE_IMPORTS = {"../../src/live2d/define.js": "./ournotes-player.live2d.element.min.js"}
# site data written by the build (never overwritten by page files)
MODELS_DIR = "models"
MODELS_INDEX = "models.json"
SITE_DATA = ("assets", "charts", MODELS_DIR, "charts.json", MODELS_INDEX)
PLAYER_VERSION_MARKER = "@PLAYER_VERSION@"         # replaced in the page's files by the bundles' short hash
# collected files: text as UTF-8, binary as is; shader programs of the WebGL2 tier only
TEXT_EXT = {".json", ".glsl"}
BINARY_EXT = {".png", ".flac", ".ogg", ".wav", ".moc3"}
SHADER_PLATFORM = "gles3"
SHADER_TYPE = "GLES3"
# master tables the chart build reads (score, live, liveaudio, livescene, livenotes, liveui): a region's chart inputs
LIVE_TABLES = ("MasterBand", "MasterCharacter", "MasterLiveJudgementSprite", "MasterLiveLaneSkin", "MasterLiveMusic",
               "MasterLiveMusicScore", "MasterLiveNoteEffectSkin", "MasterLiveNoteSe", "MasterLiveNoteSkin",
               "MasterLiveQualitySettings", "MasterLiveSe", "MasterLiveSettings", "MasterLiveStageVideo",
               "MasterLiveStartCharacterVoice", "MasterMemberCard", "MasterOptionDefault", "MasterOptionRange",
               "MasterSound", "MasterSoundCueSheet", "MasterText")
CHARTS_INDEX = "charts.json"
# documentation fields of the extractors the player does not read (live-audio.json `spec`, the voice rule notes,
# scene.json `slice` / `derived` notes): left out of the site's copies
DOC_FIELDS = {
    "audio/live-audio.json": [("spec",), ("voice", "rule"), ("voice", "decision")],
    "livescene/scene.json": [("slice", "bandChoice", "source"), ("slice", "bandChoice", "note"),
                             ("derived", "lane", "sources"), ("derived", "fovRule", "source")],
}


def _dump(obj) -> bytes:
    return jsonio.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8") + b"\n"


def _minify(obj) -> str:
    return jsonio.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def text_asset(path: str, s: str) -> bytes:
    """A text asset's stored bytes: JSON re-encoded without whitespace (the same values: JSON.parse of either text
    gives the same result; non-finite numbers stay 1e999 / -1e999) and without the DOC_FIELDS, other text (GLSL)
    as is, UTF-8."""
    if path.endswith(".json"):
        obj = json.loads(s)
        for keys in DOC_FIELDS.get(path, ()):
            o = obj
            for k in keys[:-1]:
                o = o.get(k) if isinstance(o, dict) else None
            if isinstance(o, dict):
                o.pop(keys[-1], None)
        s = _minify(obj)
    return s.encode("utf-8")


def join_parts(parts: list[tuple[str, bytes]]) -> bytes:
    """The text the player's AssetStore rebuilds from a split JSON file: {"k1":t1,"k2":t2}."""
    return b"{" + b",".join(json.dumps(k).encode("ascii") + b":" + t for k, t in parts) + b"}"


def split_json(data: bytes) -> list[tuple[str, bytes]] | None:
    """A minified JSON object larger than SPLIT_MIN_BYTES -> [(key, value text)] in key order; None when it stays
    whole. The parts rejoin to exactly `data`."""
    if len(data) <= SPLIT_MIN_BYTES or not data.startswith(b"{"):
        return None
    obj = json.loads(data)
    if not isinstance(obj, dict) or len(obj) < 2 or not all(SPLIT_KEY.fullmatch(k) for k in obj):
        return None
    parts = [(k, _minify(v).encode("utf-8")) for k, v in obj.items()]
    if join_parts(parts) != data:
        raise RuntimeError("split JSON does not rejoin to the stored text")
    return parts


# ---------------------------------------------------------------- audio
def transcode(src: Path, fmt: str, work: Path) -> bytes:
    """One FLAC waveform -> the web format's bytes (bit-exact container flags: the output depends on the input only)."""
    ext, codec = WEB_AUDIO[fmt]
    if codec is None:
        return Path(src).read_bytes()
    dst = Path(work) / f"transcode-{os.getpid()}{ext}"
    r = subprocess.run([tool("ffmpeg", "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map_metadata", "-1",
                        "-fflags", "+bitexact", "-flags:a", "+bitexact", *codec, str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg {fmt} failed on {src}: {r.stderr[-2000:]}")
    data = dst.read_bytes()
    dst.unlink()
    return data


def mp4_priming(data: bytes) -> int:
    """Encoder delay of an MP4 audio track: media_time of the first edit list entry (moov/trak/edts/elst)."""
    def boxes(b, off, end):
        while off + 8 <= end:
            size, kind = struct.unpack(">I4s", b[off:off + 8])
            hdr = 8
            if size == 1:
                size, hdr = struct.unpack(">Q", b[off + 8:off + 16])[0], 16
            elif size == 0:
                size = end - off
            yield kind, off + hdr, off + size
            off += size
    def find(path, off, end):
        for kind, a, z in boxes(data, off, end):
            if kind == path[0]:
                return (a, z) if len(path) == 1 else find(path[1:], a, z)
        return None
    loc = find([b"moov", b"trak", b"edts", b"elst"], 0, len(data))
    if loc is None:
        return 0
    a, _ = loc
    version = data[a]
    count = struct.unpack(">I", data[a + 4:a + 8])[0]
    if count < 1:
        return 0
    if version == 1:
        return max(0, struct.unpack(">q", data[a + 16:a + 24])[0])
    return max(0, struct.unpack(">i", data[a + 12:a + 16])[0])


def _web_audio(live_dir: Path, text: dict[str, str], binary: dict[str, bytes], fmt: str, work: Path,
               cache: dict | None = None) -> None:
    """The BGM layers (live-audio.json music sound) in the web format; the JSON paths follow. An AAC layer records
    its encoder delay (`encoderDelay`, samples): a decoder that does not apply the MP4 edit list outputs that many
    priming samples first, which the player then drops (LiveSoundManager.preload). `cache`: FLAC sha256 -> bytes."""
    ext = WEB_AUDIO[fmt][0]
    index = json.loads(text["live.json"])
    la_path = index["liveAudio"]
    la = json.loads(text[la_path])
    renamed = {}
    for layer in la["sounds"][str(la["music"]["soundId"])]["layers"]:
        old = layer["file"]
        if old not in binary or not old.endswith(".flac"):
            raise ValueError(f"BGM layer {old}: not a packed FLAC")
        new = old[:-len(".flac")] + ext
        if new != old:
            key = hashlib.sha256(binary[old]).hexdigest()
            data = cache.get(key) if cache is not None else None
            if data is None:
                data = transcode(live_dir / old, fmt, work)
                if cache is not None:
                    cache[key] = data
            binary[new] = data
            del binary[old]
            renamed[old] = new
            if fmt == "aac":
                layer["encoderDelay"] = mp4_priming(data)
        layer["file"] = new
    if not renamed:
        return
    text[la_path] = jsonio.dumps(la, ensure_ascii=False, indent=1)
    a = index.get("audio") or {}
    if a.get("file") in renamed:
        a["file"] = renamed[a["file"]]
    for k, v in (a.get("cues") or {}).items():
        if v in renamed:
            a["cues"][k] = renamed[v]
    text["live.json"] = jsonio.dumps(index, ensure_ascii=False, indent=1)


def localized_texts(master: dict) -> tuple[dict, dict]:
    """({language: title}, {language: [band names]}) of a live's master rows (score/master.json), every language of
    languages.LANGUAGES the texts have (for the band names: every band has one)."""
    title = master.get("title") or {}
    bands = [b.get("name") or {} for b in master.get("bands") or []]
    titles = {c: title[c] for c in languages.LANGUAGES if isinstance(title.get(c), str)}
    names = {c: [b[c] for b in bands] for c in languages.LANGUAGES if all(isinstance(b.get(c), str) for b in bands)}
    return titles, names


def chart_facts(live_dir: Path, summary: dict, difficulty: str, language: str) -> dict:
    """What a chart listing needs, from the live directory (master rows, sounds) and the build summary: `title` and
    `bands` in `language`, `titles` and `bandNames` in every language."""
    rd = lambda rel: json.loads((Path(live_dir) / rel).read_text(encoding="utf-8"))
    index = rd("live.json")
    master = rd(index["master"])
    la = rd(index["liveAudio"])
    layer = la["sounds"][str(la["music"]["soundId"])]["layers"][0]
    score_row = master["MasterLiveMusicScore"][difficulty]
    music = master["MasterLiveMusic"]
    titles, band_names = localized_texts(master)
    return {
        "title": (master.get("title") or {}).get(language),
        "bands": [(b.get("name") or {}).get(language) for b in master.get("bands") or []],
        "language": language,
        "titles": titles,
        "bandNames": band_names,
        "bandIds": list(music.get("_bandIDs") or []),
        "stageBand": summary["band"]["band"],
        "level": score_row["_musicScoreLevel"],
        "displayLevel": score_row["_musicScoreDisplayLevel"],
        "notes": summary["judgementNoteCount"],
        "fullComboCount": summary["fullComboCount"],
        "durationMs": layer["samples"] * 1000 // layer["sampleRate"],
        "sortOrder": music.get("_sortOrder"),
    }


# ---------------------------------------------------------------- store
class Store:
    """site/assets/<sha256>.<ext>: write-once content-addressed files (safe for concurrent writers)."""

    def __init__(self, site: Path):
        self.dir = Path(site) / "assets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.written = 0
        self.reused = 0

    def put(self, path: str, data: bytes) -> dict:
        ext = Path(path).suffix.lower().lstrip(".") or "bin"
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        dst = self.dir / name
        if dst.exists() and dst.stat().st_size == len(data):
            self.reused += 1
        else:
            tmp = dst.with_name(f"{name}.{os.getpid()}.{threading.get_ident()}.part")
            tmp.write_bytes(data)
            for i in range(20):
                try:
                    os.replace(tmp, dst)
                    break
                except PermissionError:
                    if dst.exists() and dst.stat().st_size == len(data):
                        tmp.unlink()
                        break
                    time.sleep(0.1 * (i + 1))
            else:
                raise RuntimeError(f"could not store {dst}")
            self.written += 1
        return {"asset": f"assets/{name}", "size": len(data)}

    def put_file(self, path: str, data: bytes) -> dict:
        """One file of a chart: whole, or per top-level key for a large JSON object (see split_json)."""
        parts = split_json(data) if path.endswith(".json") else None
        if parts is None:
            return self.put(path, data)
        return {"size": len(data), "parts": [[k, self.put(f"{path}#{k}.json", t)["asset"], len(t)] for k, t in parts]}


def entry_assets(e: dict) -> list[str]:
    return [p[1] for p in e["parts"]] if "parts" in e else [e["asset"]]


# ---------------------------------------------------------------- data
def open_data(cfg: Config, region: str | None = None):
    """Catalog (fetching from the CDN of `region`), the master dir of `region` and PlayerData from the settings (as
    the command line opens them; `region` None: [catalog] region)."""
    from .cli import master_dir, open_catalog, player_data
    return open_catalog(cfg, region=region), master_dir(cfg, region), player_data(cfg)


def _lock_fetches(cat, lock) -> None:
    """Catalog downloads of this process under a lock shared by the workers (one writer per cache file)."""
    for name in ("fetch", "fetch_raw"):
        f = getattr(cat, name)
        def locked(*a, _f=f, **k):
            with lock:
                return _f(*a, **k)
        setattr(cat, name, locked)


def all_pairs(master: Path) -> list[tuple[int, str]]:
    """Every MasterLiveMusic x difficulty with a MasterLiveMusicScore row, in music id order."""
    scores = {r["_id"] for r in master_table(Path(master), "MasterLiveMusicScore")}
    out = []
    for m in sorted(master_table(Path(master), "MasterLiveMusic"), key=lambda r: r["_id"]):
        out += [(m["_id"], d) for d in DIFFICULTIES if m.get(f"_{d}ID") in scores]
    return out


# ---------------------------------------------------------------- regions
def site_regions(cfg: Config, regions=None, all_regions: bool = False) -> list[str]:
    """The regions of a build: `regions` (given order, duplicates dropped), every configured region
    (`all_regions`, config order), else the one [catalog] region. Each needs its `[servers.<region>]` table."""
    if all_regions:
        names = cfg.regions()
        if not names:
            raise ConfigError("no region configured: add a [servers.<region>] table with `cdn` to the config file "
                              "(or set NNNOTES_SERVERS_<REGION>_CDN)")
    else:
        names = list(dict.fromkeys(regions or [])) or [cfg.region()]
    known = set(cfg.regions())
    for r in names:
        if r not in known:
            raise cfg.missing(f"servers.{r}", "cdn")
    return names


def region_masters(cfg: Config, regions: list[str]) -> dict[str, Path]:
    """{region: decoded master dir} (cli.master_dir). With more than one region the --master flag is refused and
    at most one region may use [paths] master (the others need their own `[servers.<region>] master`)."""
    from .cli import master_dir
    if len(regions) > 1 and cfg.origin("paths", "master") == "flag":
        raise ConfigError("--master names one directory: give each region's master data as `master` in its "
                          "[servers.<region>] table of the config file (NNNOTES_SERVERS_<REGION>_MASTER)")
    shared = [r for r in regions if cfg.master(r) == ("paths", "master")]
    if len(shared) > 1:
        raise cfg.missing(f"servers.{shared[1]}", "master")
    return {r: master_dir(cfg, r) for r in regions}


def chart_inputs(master: Path) -> str:
    """Fingerprint of a region's chart inputs: SHA-256 over the LIVE_TABLES files of its master dir."""
    h = hashlib.sha256()
    for t in LIVE_TABLES:
        f = Path(master) / f"{t}.json"
        h.update(t.encode("ascii") + b"\0" + (f.read_bytes() if f.is_file() else b"\0missing") + b"\0")
    return h.hexdigest()


def region_groups(masters: dict[str, Path]) -> list[list[str]]:
    """The regions grouped by chart inputs (chart_inputs), groups and regions in the order of `masters`."""
    groups: dict[str, list[str]] = {}
    for r, m in masters.items():
        groups.setdefault(chart_inputs(m), []).append(r)
    return list(groups.values())


def region_meta(cfg: Config, regions: list[str]) -> list[dict]:
    """The index records of `regions`: id, name (`[servers.<region>] name`, else the id) and, when configured, the
    client languages (`[servers.<region>] languages`)."""
    out = []
    for r in regions:
        rec = {"id": r, "name": cfg.get(f"servers.{r}", "name") or r}
        langs = cfg.get_list(f"servers.{r}", "languages")
        if langs:
            rec["languages"] = langs
        out.append(rec)
    return out


def merge_regions(old, new) -> list[str]:
    """`old` (a manifest's regions, may be None) followed by the regions of `new` it lacks."""
    out = list(old or [])
    return out + [r for r in new if r not in out]


def manifest_file(site: Path, chart_id: str, prefix: str = "") -> Path:
    """charts/<prefix><chart id>.json (`prefix`: "" or "<region>/")."""
    return Path(site) / "charts" / f"{prefix}{chart_id}.json"


def chart_manifests(site: Path) -> list[Path]:
    """Every chart manifest: charts/*.json, then the region manifests charts/<region>/*.json."""
    d = Path(site) / "charts"
    return [*sorted(d.glob("*.json")), *sorted(d.glob("*/*.json"))]


def add_regions(path: Path, regions: list[str]) -> bool:
    """Add `regions` to a manifest's regions; True when the file changed."""
    man = json.loads(Path(path).read_text(encoding="utf-8"))
    merged = merge_regions(man.get("regions"), regions)
    if merged == man.get("regions"):
        return False
    man["regions"] = merged
    Path(path).write_bytes(_dump(man))
    return True


def fold_variant(site: Path, chart_id: str, prefix: str) -> bool:
    """Drop the region manifest charts/<prefix><id>.json when its files equal those of the shared charts/<id>.json;
    its regions join the shared manifest. True when it was dropped."""
    var, shared = manifest_file(site, chart_id, prefix), manifest_file(site, chart_id)
    if not (prefix and var.is_file() and shared.is_file()):
        return False
    v = json.loads(var.read_text(encoding="utf-8"))
    if v["files"] != json.loads(shared.read_text(encoding="utf-8"))["files"]:
        return False
    add_regions(shared, v.get("regions") or [])
    var.unlink()
    return True


# ---------------------------------------------------------------- live directories
def _link_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, copy_function=os.link)


def build_music_dirs(cat, master: Path, player, music_id: int, difficulties: list[str], root: Path,
                     livenotes_dir: Path, band: int | None = None, leader_card: int | None = None, *,
                     language: str) -> dict:
    """The live directories of one music's charts under root/<difficulty>/, composed as live.build composes one:
    score + BGM decode (first difficulty), live sounds and scene into root/base, shared by hard links; per difficulty
    its own score/ (score.extract without audio), liveui/ and live.json; livenotes/ linked from `livenotes_dir`
    (livenotes.extract reads no music input). `band` / `leader_card` / `language`: as for live.build.
    Returns {difficulty: (dir, summary)}."""
    from . import liveaudio, livescene, liveui, score
    base = root / "base"
    base.mkdir(parents=True)
    choice = live.resolve_band(cat, master, music_id, band=band, leader_card=leader_card)
    first = score.extract(cat, master, music_id, difficulties[0], base, audio=True, audio_fmt="flac")
    la = liveaudio.extract(cat, master, player, music_id, base, fmt="flac")
    livescene.extract(cat, player, base, master=master, music_id=music_id, band=choice["band"], band_choice=choice)
    out = {}
    for d in difficulties:
        pdir = root / d
        pdir.mkdir()
        for sub in ("audio", "livescene"):
            _link_tree(base / sub, pdir / sub)
        _link_tree(livenotes_dir, pdir / "livenotes")
        s = score.extract(cat, master, music_id, d, pdir, audio=False, audio_fmt="flac")
        liveui.extract(cat, player, pdir, master=master, music_id=music_id, difficulty=d, language=language)
        index = {"musicId": music_id, "difficulty": d, "chart": s["chart"], "notes": s["notes"],
                 "master": s["master"], "audio": first["audio"], "liveAudio": la["index"],
                 "scene": "livescene/scene.json", "noteAssets": "livenotes/notes.json", "liveUi": "liveui/liveui.json"}
        jsonio.write_json(pdir / "live.json", index)
        out[d] = (pdir, {"band": choice, "judgementNoteCount": s["judgementNoteCount"],
                         "fullComboCount": s["fullComboCount"]})
    return out


def collect(live_dir: Path, files: list[str]) -> tuple[dict[str, str], dict[str, bytes]]:
    """The files `files` (paths relative to `live_dir`, every one must exist) as ({path: text}, {path: bytes}).
    Every shader index (<dir>/shaders.json) among them is filtered to the listed GLES3 programs (GLSL ES 3.00);
    other programs and SPIR-V containers of a shader directory are left out."""
    live_dir = Path(live_dir)
    exact = set(files)
    wanted = exact.__contains__
    lost = sorted(r for r in exact if not (live_dir / r).is_file())
    if lost:
        raise FileNotFoundError(f"{live_dir}: {len(lost)} listed files missing, e.g. {lost[:3]}")
    text: dict[str, str] = {}
    keep: set[str] = set()
    shader_dirs = sorted(p.parent for p in live_dir.rglob("shaders.json"))
    for sdir in shader_dirs:
        rel_dir = sdir.relative_to(live_dir).as_posix()
        if not wanted(f"{rel_dir}/shaders.json"):
            continue
        index = json.loads((sdir / "shaders.json").read_text(encoding="utf-8"))
        for rec in index:
            rec["variants"] = [v for v in rec["variants"]
                               if v["platform"] == SHADER_PLATFORM and v["type"] == SHADER_TYPE]
        index = [rec for rec in index if wanted(f"{rel_dir}/{rec['parsed']}")]
        for rec in index:
            rec["variants"] = [v for v in rec["variants"] if wanted(f"{rel_dir}/{v['file']}")]
        keep |= {f"{rel_dir}/{v['file']}" for rec in index for v in rec["variants"]}
        text[f"{rel_dir}/shaders.json"] = jsonio.dumps(index, ensure_ascii=False)
    binary: dict[str, bytes] = {}
    for f in sorted(live_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(live_dir).as_posix()
        if rel.endswith(".html") or rel in text or not wanted(rel):
            continue
        sdir = next((d for d in shader_dirs if f.is_relative_to(d)), None)
        if sdir is not None and f.suffix == ".glsl" and rel not in keep:
            continue                                   # other platforms / GLES 3.1 programs
        if sdir is not None and f.suffix not in (".glsl", ".json"):
            continue                                   # Vulkan / SPIR-V containers
        if f.suffix in TEXT_EXT:
            text[rel] = f.read_text(encoding="utf-8")
        elif f.suffix in BINARY_EXT:
            binary[rel] = f.read_bytes()
        else:
            raise ValueError(f"unexpected file in the live directory: {rel}")
    return text, binary


def ingest(store: Store, site: Path, music_id: int, difficulty: str, live_dir: Path, summary: dict,
           files: list[str], flows: list[str], audio_format: str, audio: bool, work: Path, bgm_cache: dict,
           language: str, prefix: str = "", regions: list[str] | None = None) -> dict:
    """One chart's files into the store + its manifest charts/<prefix><id>.json, serving `regions` (added to the
    regions of the manifest it replaces)."""
    text, binary = collect(live_dir, files)
    facts = chart_facts(live_dir, summary, difficulty, language)
    if audio:
        _web_audio(live_dir, text, binary, audio_format, work, bgm_cache)
    else:
        binary = {k: v for k, v in binary.items() if Path(k).suffix.lower() not in AUDIO_EXT}
    entries = {p: store.put_file(p, text_asset(p, s)) for p, s in text.items()}
    entries.update({p: store.put(p, b) for p, b in binary.items()})
    manifest = {"format": SITE_FORMAT, "musicId": music_id, "difficulty": difficulty, "audio": bool(audio),
                "audioFormat": audio_format if audio else None, "flows": flows, "quality": TRACE_QUALITY,
                "chart": facts, "files": dict(sorted(entries.items()))}
    path = manifest_file(site, f"{music_id}_{difficulty}", prefix)
    old = json.loads(path.read_text(encoding="utf-8")).get("regions") if path.is_file() else None
    if old or regions:
        manifest["regions"] = merge_regions(old, regions or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dump(manifest))
    return {"id": f"{music_id}_{difficulty}", "ok": True, "files": len(entries), "bytes": sum(e["size"] for e in entries.values()),
            "flows": flows}


# ---------------------------------------------------------------- per music (in a worker or in this process)
_W: dict = {}


def _worker_init(cfg: Config, lock, job: dict) -> None:
    use(cfg)
    cat, master, player = open_data(cfg, job.get("region"))
    _lock_fetches(cat, lock)
    _W.update(cat=cat, master=master, player=player, cfg=job)


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)


def music_task(music_id: int, difficulties: list[str], cfg: dict | None = None, data=None) -> list[dict]:
    """All charts of one music: live directories, read sets (2 at a time), ingest; one result per chart."""
    cat, master, player = data or (_W["cat"], _W["master"], _W["player"])
    cfg = cfg or _W["cfg"]
    site, tmp = Path(cfg["site"]), Path(cfg["tmp"])
    store = Store(site)
    root = Path(tempfile.mkdtemp(prefix=f"m{music_id}-", dir=tmp))
    t0 = time.time()
    results = []
    try:
        try:
            dirs = build_music_dirs(cat, master, player, music_id, difficulties, root, Path(cfg["livenotes"]),
                                    band=cfg["band"], leader_card=cfg["leaderCard"], language=cfg["language"])
        except Exception as e:
            cause = f"{type(e).__name__}: {e}"
            _log(f"{music_id}: live directories failed: {cause}")
            return [{"id": f"{music_id}_{d}", "ok": False, "stage": "extract", "error": cause[:2000],
                     "trace": traceback.format_exc()[-3000:]} for d in difficulties]
        t1 = time.time()
        bgm: dict = {}
        lock = threading.Lock()

        def one(d):
            pdir, summary = dirs[d]
            stage = "read set"
            try:
                files, flows = read_set(pdir, Path(cfg["player"])), list(FLOWS)
                stage = "ingest"
                with lock:                            # one ingest at a time (BGM transcode cache, memory)
                    return ingest(store, site, music_id, d, pdir, summary, files, flows, cfg["audioFormat"],
                                  cfg["audio"], root, bgm, cfg["language"], cfg.get("prefix", ""),
                                  cfg.get("regions"))
            except Exception as e:
                cause = f"{type(e).__name__}: {e}"
                _log(f"{music_id}_{d}: {stage} failed: {cause[:300]}")
                return {"id": f"{music_id}_{d}", "ok": False, "stage": stage, "error": cause[:2000],
                        "trace": traceback.format_exc()[-3000:]}

        with ThreadPoolExecutor(2) as ex:
            results = list(ex.map(one, difficulties))
        dt = time.time() - t0
        for r in results:
            r.update(music=music_id, extractSec=round(t1 - t0, 1), musicSec=round(dt, 1))
        _log(f"{music_id}: {sum(r['ok'] for r in results)}/{len(results)} charts, extract {t1 - t0:.0f} s, "
             f"total {dt:.0f} s")
        return results
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _pool_task(args):
    return music_task(*args)


# ---------------------------------------------------------------- player
def check_player(player_dir) -> Path:
    """The ournotes-player checkout or installed package: its read-set script, built bundle and page must exist."""
    if player_dir is None:
        raise ConfigError("the site needs the player: give --player <ournotes-player checkout or package> "
                          "(or `player` in the [paths] table of the config file, NNNOTES_PATHS_PLAYER)")
    d = Path(player_dir)
    missing = [rel for rel in (READ_SET_SCRIPT, *PLAYER_BUNDLES, f"{PLAYER_PAGE_DIR}/index.html")
               if not (d / rel).is_file()]
    if missing:
        raise ConfigError(f"{d} is not a built ournotes-player (missing {', '.join(missing)}; "
                          f"a checkout needs its build first)")
    return d.resolve()


def read_set(live_dir: Path, player_dir: Path) -> list[str]:
    """Every file of `live_dir` the player reads for the whole chart in its default state, from the player's own
    code run in Node (READ_SET_SCRIPT: no-op WebGL2 / WebAudio, every frame from the start to the chart's end)."""
    r = subprocess.run([tool("node", "node"), str(Path(player_dir) / READ_SET_SCRIPT), str(Path(live_dir))],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"read set of {Path(live_dir).name} failed:\n{(r.stderr or r.stdout)[-4000:]}")
    out = json.loads(r.stdout)
    if isinstance(out, dict):                          # {files, state, frames} form
        if out.get("state", "ended") != "ended":
            raise RuntimeError(f"read set: the chart did not end ({out.get('state')} after {out.get('frames')} frames)")
        out = out.get("files")
    if not isinstance(out, list) or not all(isinstance(p, str) for p in out):
        raise RuntimeError("read set: expected a JSON list of paths")
    return out


def player_bundles(player_dir: Path, rels) -> tuple[dict[str, bytes], str]:
    """The bundles `rels` of the player and their source maps ({file name: bytes}), and the bundles' short hash."""
    bundles = {}
    for rel in rels:
        bundles[Path(rel).name] = (player_dir / rel).read_bytes()
        if (player_dir / f"{rel}.map").is_file():
            bundles[Path(rel).name + ".map"] = (player_dir / f"{rel}.map").read_bytes()
    return bundles, hashlib.sha256(b"".join(bundles[Path(rel).name] for rel in rels)).hexdigest()[:16]


def page_files(page_dir: Path, imports: dict[str, str], version: str,
               names: str = "PAGE_IMPORTS") -> dict[str, bytes]:
    """The files of a player page directory; in its .html / .js files the module specifiers `imports` (`names`)
    point at the bundles copied next to the page and PLAYER_VERSION_MARKER is `version`."""
    page = {f.relative_to(page_dir).as_posix(): f.read_bytes() for f in sorted(page_dir.rglob("*")) if f.is_file()}
    for rel, data in page.items():
        if Path(rel).suffix not in (".html", ".js", ".mjs"):
            continue
        t = data.decode("utf-8")
        for src, dst in imports.items():
            for q in ('"', "'"):
                t = t.replace(f"{q}{src}{q}", f"{q}{dst}{q}")
        if re.search(r"""["'](?:\.\./)+src/""", t):
            raise RuntimeError(f"player page {rel} imports the player's sources beyond {names}")
        page[rel] = t.replace(PLAYER_VERSION_MARKER, version).encode("utf-8")
    return page


def write_player(site: Path, player_dir: Path) -> dict:
    """The player's page (every file of PLAYER_PAGE_DIR; in its .html / .js files the PAGE_IMPORTS specifiers point
    at the bundle and PLAYER_VERSION_MARKER is the bundles' short hash) and bundles (PLAYER_BUNDLES and their source
    maps) into the site root; when the player has the Live2D page, the same for LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS
    and LIVE2D_BUNDLES into LIVE2D_PAGE_SITE_DIR."""
    player_dir = check_player(player_dir)
    bundles, version = player_bundles(player_dir, PLAYER_BUNDLES)
    page = page_files(player_dir / PLAYER_PAGE_DIR, PAGE_IMPORTS, version)
    files = {**page, **bundles}
    live2d = {}
    if (player_dir / LIVE2D_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in LIVE2D_BUNDLES if not (player_dir / rel).is_file()]
        if missing:
            raise ConfigError(f"{player_dir} has the Live2D page but not {', '.join(missing)} "
                              f"(a checkout needs its build first)")
        live2d_bundles, live2d_version = player_bundles(player_dir, LIVE2D_BUNDLES)
        live2d = page_files(player_dir / LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS, live2d_version, "LIVE2D_PAGE_IMPORTS")
        files.update({f"{LIVE2D_PAGE_SITE_DIR}/{r}": d for r, d in {**live2d, **live2d_bundles}.items()})
    for rel, data in files.items():
        if rel.split("/", 1)[0] in SITE_DATA:
            raise RuntimeError(f"player page file {rel} would overwrite the site data")
        dst = Path(site) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    return {"playerVersion": version, "playerBytes": sum(map(len, bundles.values())), "pageFiles": len(page),
            "live2dPageFiles": len(live2d)}


def entry_text(site: Path, e: dict) -> bytes:
    """The file text of a manifest entry (a split JSON file rejoined)."""
    if "parts" in e:
        return join_parts([(k, (site / a).read_bytes()) for k, a, _ in e["parts"]])
    return (site / e["asset"]).read_bytes()


def reingest_json(site: Path) -> dict:
    """Every JSON file of every chart and model stored again through text_asset (after a change of the stored-text
    rules); the manifests follow, unreferenced assets go with write_index."""
    store, changed, memo = Store(site), 0, {}
    for mf in [*chart_manifests(site), *sorted((site / MODELS_DIR).glob("*.json"))]:
        man = json.loads(mf.read_text(encoding="utf-8"))
        files = man["files"]
        for p, e in files.items():
            if not p.endswith(".json"):
                continue
            key = (p, json.dumps(e, sort_keys=True))              # charts share most files: each stored once
            if key not in memo:
                memo[key] = store.put_file(p, text_asset(p, entry_text(site, e).decode("utf-8")))
            ne = memo[key]
            if ne != e:
                files[p] = ne
                changed += 1
        mf.write_bytes(_dump(man))
    return {"changedEntries": changed}


def index_meta(site: Path, charts: list[dict], language: str | None = None,
               regions: list[dict] | None = None) -> dict:
    """The top-level keys of charts.json besides `charts`: `language` (the default listing language), `languages`
    (the languages of the entries' `titles`) and `regions` (id, name, languages; merged by id into those of the
    existing charts.json, first the ones it has); keys without a value are left out."""
    old = {}
    f = Path(site) / CHARTS_INDEX
    if f.is_file():
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    new = {r["id"]: r for r in regions or []}
    recs = [new.pop(r["id"], r) for r in old.get("regions") or [] if isinstance(r, dict) and "id" in r]
    recs += list(new.values())
    known = {c for e in charts for c in (e.get("titles") or {})}
    langs = [c for c in languages.LANGUAGES if c in known] + sorted(known - set(languages.LANGUAGES))
    meta = {"language": language or old.get("language"), "languages": langs, "regions": recs}
    return {k: v for k, v in meta.items() if v}


def write_index(site: Path, language: str | None = None, regions: list[dict] | None = None) -> dict:
    """site/charts.json from every chart manifest present (index_meta: `language`, `regions` of this build) and
    site/models.json from every model manifest (webmodel.write_models_index); assets no chart and no model
    references removed."""
    from .webmodel import write_models_index
    order = {d: i for i, d in enumerate(DIFFICULTIES)}
    charts, used = [], set()
    (site / "assets").mkdir(parents=True, exist_ok=True)
    for p in chart_manifests(site):
        man = json.loads(p.read_text(encoding="utf-8"))
        for e in man["files"].values():
            used.update(entry_assets(e))
        entry = {"id": p.stem, "manifest": p.relative_to(site).as_posix(), "musicId": man["musicId"],
                 "difficulty": man["difficulty"], "audio": man["audio"], "audioFormat": man.get("audioFormat"),
                 "flows": man.get("flows"), "bytes": sum(f["size"] for f in man["files"].values()), **man["chart"]}
        if man.get("regions"):
            entry["regions"] = man["regions"]
        charts.append(entry)
    charts.sort(key=lambda c: (c["musicId"], order[c["difficulty"]], c["manifest"]))
    meta = index_meta(site, charts, language, regions)
    (site / CHARTS_INDEX).write_bytes(_dump({"format": SITE_FORMAT, **meta, "charts": charts}))
    models, model_assets = write_models_index(site)
    used |= model_assets
    removed = 0
    for f in (site / "assets").iterdir():
        if f"assets/{f.name}" not in used:
            f.unlink()
            removed += 1
    sizes = [f.stat().st_size for f in (site / "assets").iterdir()]
    return {"charts": len(charts), "models": models, "assets": len(sizes), "assetBytes": sum(sizes),
            "removedAssets": removed}


# ---------------------------------------------------------------- build
def _group_pairs(pairs, master: Path) -> list[tuple[int, str]]:
    """The charts of a region group: `pairs` (None: every chart) that its master data has, in the given order."""
    have = all_pairs(master)
    if pairs is None:
        return have
    have = set(have)
    return [p for p in pairs if p in have]


def _build_group(site: Path, tmp_root: Path, pairs, cfg: Config, region: str, prefix: str, regions: list[str],
                 job: dict, force: bool, workers: int | None, log) -> tuple[list, list, int]:
    """The charts `pairs` of one region group into charts/<prefix><id>.json (data of `region`); manifests that exist
    are skipped unless `force` and gain the group's `regions`. -> (results, skipped ids, workers)."""
    by_music: dict[int, list[str]] = {}
    skipped = []
    for music_id, difficulty in pairs:
        cid = f"{music_id}_{difficulty}"
        if manifest_file(site, cid, prefix).exists() and not force:
            add_regions(manifest_file(site, cid, prefix), regions)
            skipped.append(prefix + cid)
            continue
        ds = by_music.setdefault(music_id, [])
        if difficulty not in ds:
            ds.append(difficulty)
    for ds in by_music.values():
        ds.sort(key=DIFFICULTIES.index)
    if workers is None:
        workers = max(1, min(5, len(by_music), (os.cpu_count() or 2) // 2))
    results = []
    if not by_music:
        return results, skipped, workers
    gdir = Path(tempfile.mkdtemp(prefix="global-", dir=tmp_root))
    try:
        cat, master, player = open_data(cfg, region)
        from . import livenotes
        livenotes.extract(cat, player, gdir, master=master)
        job = {**job, "livenotes": str(gdir / "livenotes"), "region": region, "prefix": prefix, "regions": regions}
        tasks = [(m, ds, job) for m, ds in sorted(by_music.items())]
        log(f"{sum(len(ds) for ds in by_music.values())} charts of {len(tasks)} musics"
            f"{f' for {prefix[:-1]}' if prefix else ''}, {workers} worker(s)")
        if workers <= 1:
            for m, ds, c in tasks:
                results += music_task(m, ds, c, data=(cat, master, player))
        else:
            ctx = mp.get_context("spawn")
            with ctx.Manager() as mgr:
                lock = mgr.Lock()
                with ctx.Pool(workers, initializer=_worker_init, initargs=(cfg, lock, job)) as pool:
                    for rs in pool.imap_unordered(_pool_task, tasks):
                        results += rs
    finally:
        shutil.rmtree(gdir, ignore_errors=True)
    for r in results:
        if prefix:
            r["id"] = prefix + r["id"]
    return results, skipped, workers


def build(out_dir, pairs, cfg: Config, player_dir: Path, audio_format: str = DEFAULT_AUDIO_FORMAT,
          audio: bool = True, force: bool = False, *, tmp_dir=None, log=None, workers: int | None = None,
          band: int | None = None, leader_card: int | None = None, regions: list[str] | None = None) -> dict:
    """Add the charts `pairs` ([(musicId, difficulty)]; None: every chart of every region's master data) to the site
    at `out_dir`, with the player of the ournotes-player checkout or package at `player_dir`. The data comes from the
    settings `cfg` (each worker process opens its own). `regions`: the regions the charts serve (default: the one
    [catalog] region), grouped by chart inputs (module docstring); the first region's group writes charts/<id>.json,
    the others charts/<region>/<id>.json unless their files equal the shared manifest's. `workers`: parallel music
    processes (default up to 5). `band` / `leader_card`: the band of every chart's stage, as for live.build
    (default: the band of the music's first vocal character)."""
    player_dir = check_player(player_dir)
    if audio_format not in WEB_AUDIO:
        raise ValueError(f"audio format {audio_format}: one of {', '.join(WEB_AUDIO)}")
    if pairs is not None:
        pairs = list(dict.fromkeys((int(m), d) for m, d in pairs))
        bad = [d for _, d in pairs if d not in DIFFICULTIES]
        if bad:
            raise ValueError(f"difficulty {bad[0]}")
    regions = site_regions(cfg, regions)
    language = languages.check(cfg.require("catalog", "language"))
    masters = region_masters(cfg, regions)
    groups = region_groups(masters)
    site = Path(out_dir).resolve()
    (site / "charts").mkdir(parents=True, exist_ok=True)
    Store(site)
    tmp_root = Path(tmp_dir).resolve() if tmp_dir else site.parent / f"{site.name}.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log = log or _log
    t0 = time.time()
    job = {"site": str(site), "tmp": str(tmp_root), "audioFormat": audio_format, "audio": bool(audio),
           "player": str(player_dir), "language": language, "band": band, "leaderCard": leader_card}
    results, skipped, folded, used_workers = [], [], [], 1
    offered = set()
    for gi, group in enumerate(groups):
        rep = group[0]
        prefix = "" if gi == 0 else f"{rep}/"
        gpairs = _group_pairs(pairs, masters[rep])
        offered.update(gpairs)
        rs, sk, w = _build_group(site, tmp_root, gpairs, cfg, rep, prefix, group, job, force, workers, log)
        results += rs
        skipped += sk
        used_workers = max(used_workers, w)
        if prefix:
            folded += [prefix + f"{m}_{d}" for m, d in gpairs if fold_variant(site, f"{m}_{d}", prefix)]
    for m, d in pairs or []:
        if (m, d) not in offered:
            results.append({"id": f"{m}_{d}", "ok": False, "stage": "master",
                            "error": f"no MasterLiveMusicScore row for {m}_{d} in the master data of "
                                     f"{', '.join(regions)}"})
    v = write_player(site, player_dir)
    idx = write_index(site, language, region_meta(cfg, regions))
    failed = [r for r in results if not r["ok"]]
    if failed:
        (site.parent / f"{site.name}.failures.json").write_bytes(_dump(sorted(failed, key=lambda r: r["id"])))
    return {"site": str(site), "built": [{k: r[k] for k in ("id", "files", "bytes", "flows")} for r in results if r["ok"]],
            "failed": [{k: r.get(k) for k in ("id", "stage", "error")} for r in failed], "skipped": skipped,
            "regions": regions, "regionGroups": groups, "foldedRegionManifests": folded,
            "seconds": round(time.time() - t0, 1), "workers": used_workers, **v, **idx}
