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
    <site>/stories.json, stories/<id>.json     story index and manifests (storysite.py), same entry forms and the
                                               same assets/; write_index keeps what they reference as well
    <site>/story/                              the player's story page and its bundle, when the player has them
                                               (STORY_PAGE_DIR, STORY_BUNDLES)

Per music (the four difficulties share the scene, the sounds and the note assets): score + BGM decode, live sounds and
scene extracted once into a temporary base directory, the note assets once per build (they depend on no music); per
difficulty a directory of hard links to those plus its own score and start canvas, composed as `live.build` composes
a live directory. Per chart: the read set (READ_SET_SCRIPT of the player: the files the player reads for the whole
chart, its own code run in Node over the live directory; from the player's plan mode when it has one, checked
against the full run on a sample of charts, see ReadSets), those files collected (GLES3 shader programs only,
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

Live options (`live_options`, from `--live-option`; liveoptions.py): a site can offer the Live options that select
files (mirrored score, note skins, effect sets and qualities, bar lines, note sound sets) besides the defaults. The
live directories then also carry those files (live.build's `options`), and each chart's read set is the union of the
default read set and the player's read sets with the settings of every offered variant (LiveOptions.read_variants;
the player's read-set script needs its "settings" feature); the full-simulation check of the plans runs on the
default settings. The manifests list the offered qualities (`options`). Without live options
the site holds only what the default options read.

A JSON object larger than SPLIT_MIN_BYTES is stored per top-level key: each value's text (the same minified encoding)
is its own asset, and the player rebuilds the file as `{"k1":` + t1 + `,"k2":` + t2 + `}`, so the values the charts
share (most of livescene/scene.json) are stored once.
"""
from __future__ import annotations

import collections
import hashlib
import json
import multiprocessing as mp
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from . import cache, cri, jsonio, languages, live, liveoptions, livenotes, livescene
from .config import Config, ConfigError, tool, usable_cpus, use
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
# the live directories of a web build: no liveui/ (the player reads none of it), the BGM decoded once for the read
# set (FLAC) and, from the same samples, into the site format (bgm_options)
WEB_LIVEUI = False
BGM_DIRECT = True
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
# the player's story page (when the player has it and its bundle): its files and bundle go to SITE/<STORY_PAGE_SITE_DIR>/
STORY_PAGE_DIR = "examples/story-list"
STORY_PAGE_SITE_DIR = "story"
STORY_BUNDLES = ("dist/ournotes-player.story.element.min.js",)
STORY_PAGE_IMPORTS = {"../../src/story/define.js": "./ournotes-player.story.element.min.js"}
# site data written by the build (never overwritten by page files)
MODELS_DIR = "models"
MODELS_INDEX = "models.json"
STORIES_DIR = "stories"
STORIES_INDEX = "stories.json"
SITE_DATA = ("assets", "charts", MODELS_DIR, "charts.json", MODELS_INDEX, STORIES_DIR, STORIES_INDEX)
PLAYER_VERSION_MARKER = "@PLAYER_VERSION@"         # replaced in the page's files by the bundles' short hash
# collected files: text as UTF-8, binary as is; shader programs of the WebGL2 tier only
TEXT_EXT = {".json", ".glsl"}
BINARY_EXT = {".png", ".flac", ".ogg", ".wav", ".moc3", ".glb", ".atlas", ".skel"}   # .glb / .atlas / .skel: an Overlay story's home spot
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
                pre = live_dir / new                   # made from the same samples by the decode (bgm_options)
                data = pre.read_bytes() if pre.is_file() else transcode(live_dir / old, fmt, work)
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
        self._have: set[str] = set()               # names this store has written or found

    def put(self, path: str, data: bytes) -> dict:
        ext = Path(path).suffix.lower().lstrip(".") or "bin"
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        dst = self.dir / name
        if name in self._have:
            self.reused += 1
        elif dst.exists() and dst.stat().st_size == len(data):
            self.reused += 1
            self._have.add(name)
        else:
            tmp = cache.temp_path(dst)
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
            self._have.add(name)
        return {"asset": f"assets/{name}", "size": len(data)}

    def put_file(self, path: str, data: bytes) -> dict:
        """One file of a chart: whole, or per top-level key for a large JSON object (see split_json)."""
        parts = split_json(data) if path.endswith(".json") else None
        if parts is None:
            return self.put(path, data)
        return {"size": len(data), "parts": [[k, self.put(f"{path}#{k}.json", t)["asset"], len(t)] for k, t in parts]}


def entry_assets(e: dict) -> list[str]:
    return [p[1] for p in e["parts"]] if "parts" in e else [e["asset"]]


_TEXTS = cache.bucket("webtext")                  # (build, store, path, text) -> manifest entry


def stored_text(store: Store, path: str, s: str, memo: str | None = None) -> dict:
    """store.put_file(path, text_asset(path, s)), made once per text in this process during the build `memo` (the
    charts of a music share most files, every chart the note assets)."""
    if memo is None:
        return store.put_file(path, text_asset(path, s))
    k = _TEXTS.key(memo, str(store.dir), path, s)
    hit = _TEXTS.get(k)
    if hit is None:
        e = store.put_file(path, text_asset(path, s))
        _TEXTS.put(k, json.dumps(e), 256)
        return e
    return json.loads(hit)


# ---------------------------------------------------------------- data
def open_data(cfg: Config, region: str | None = None):
    """Catalog (fetching from the CDN of `region`), the master dir of `region` and PlayerData from the settings (as
    the command line opens them; `region` None: [catalog] region)."""
    from .cli import master_dir, open_catalog, player_data
    return open_catalog(cfg, region=region), master_dir(cfg, region), player_data(cfg)


def _lock_fetches(cat, lock) -> None:
    """Catalog downloads of this process under a lock shared by the workers (one writer per cache file); a file
    already in the cache is returned without the lock (Catalog.cached)."""
    for name in ("fetch", "fetch_raw"):
        f = getattr(cat, name)
        def locked(x, _f=f, _name=name):
            hit = cat.cached(x) if _name == "fetch" else cat.cached_raw(x)
            if hit is not None:
                return hit
            with lock:
                return _f(x)
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


def unknown_pairs(cfg: Config, pairs, regions=None) -> list[tuple[int, str]]:
    """The charts of `pairs` ([(musicId, difficulty)], duplicates dropped) that no region of the site
    (site_regions(cfg, regions)) has: no MasterLiveMusicScore row for that music and difficulty in its master data."""
    have: set = set()
    for md in dict.fromkeys(region_masters(cfg, site_regions(cfg, regions)).values()):
        try:
            have.update(all_pairs(md))
        except FileNotFoundError as e:
            raise ConfigError(f"master data {md}: no {Path(e.filename).name} (a directory written by "
                              f"`nnnotes master decode`)") from None
    return [p for p in dict.fromkeys((int(m), d) for m, d in pairs) if p not in have]


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
    """Drop the region manifest charts/<prefix><id>.json when its files (and offered live options) equal those of
    the shared charts/<id>.json; its regions join the shared manifest. True when it was dropped."""
    var, shared = manifest_file(site, chart_id, prefix), manifest_file(site, chart_id)
    if not (prefix and var.is_file() and shared.is_file()):
        return False
    v, s = json.loads(var.read_text(encoding="utf-8")), json.loads(shared.read_text(encoding="utf-8"))
    if v["files"] != s["files"] or v.get("options") != s.get("options"):
        return False
    add_regions(shared, v.get("regions") or [])
    var.unlink()
    return True


# ---------------------------------------------------------------- live directories
def _link_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, copy_function=os.link)


def bgm_options(audio_format: str, audio: bool) -> dict:
    """cri.decode options of a web build's BGM: the live directory keeps FLAC (the read set reads its header only:
    level 0 unless the site stores that FLAC) and, when the site takes another format, the same samples encoded
    into it as well (BGM_DIRECT; _web_audio then stores that file)."""
    if not audio:
        return {"flac_level": 0}
    ext, codec = WEB_AUDIO[audio_format]
    if codec is None:
        return {}
    return {"flac_level": 0, **({"also": (ext, codec)} if BGM_DIRECT else {})}


def build_music_dirs(cat, master: Path, player, music_id: int, difficulties: list[str], root: Path,
                     livenotes_dir: Path, band: int | None = None, leader_card: int | None = None, *,
                     language: str, fonts: str = "open", scene=None, bgm: dict | None = None,
                     options: liveoptions.LiveOptions = liveoptions.LiveOptions()) -> dict:
    """The live directories of one music's charts under root/<difficulty>/, composed as live.build composes one:
    BGM decode (bgm: cri.decode options), live sounds and scene into root/base, shared by hard links; per difficulty
    its own score/ (score.extract without audio) and live.json (liveui/ only with WEB_LIVEUI); livenotes/ linked
    from `livenotes_dir` (livenotes.extract reads no music input). `scene`: (band part, its files) of the music's
    band (livescene.band_part), else the scene is exported whole. `band` / `leader_card` / `language` / `fonts` /
    `options`: as for live.build (livenotes_dir made with the same options). Returns {difficulty: (dir, summary)}."""
    from . import liveaudio, liveui, score
    base = root / "base"
    base.mkdir(parents=True)
    choice = live.resolve_band(cat, master, music_id, band=band, leader_card=leader_card)
    first = {"audio": score.extract_audio(cat, master, music_id, base, "flac", **(bgm or {}))}
    la = liveaudio.extract(cat, master, player, music_id, base, fmt="flac", options=options)
    mode = "whole (no band part)"                      # how the scene was made: reported per chart
    if scene is not None and scene[0]["band"] == choice["band"]:
        _link_tree(scene[1], base / "livescene")
        try:
            livescene.music_part(cat, player, base, scene[0], master, music_id, band_choice=choice)
            mode = "band"
        except livescene.SharedSceneMismatch as e:
            mode = f"whole ({e})"
            _log(f"{music_id}: scene exported whole: {e}")
            shutil.rmtree(base / "livescene")
    if mode != "band":
        livescene.extract(cat, player, base, master=master, music_id=music_id, band=choice["band"],
                          band_choice=choice)
    out = {}
    for d in difficulties:
        pdir = root / d
        pdir.mkdir()
        for sub in ("audio", "livescene"):
            _link_tree(base / sub, pdir / sub)
        _link_tree(livenotes_dir, pdir / "livenotes")
        s = score.extract(cat, master, music_id, d, pdir, audio=False, audio_fmt="flac", mirror=options.mirror)
        if WEB_LIVEUI:
            liveui.extract(cat, player, pdir, master=master, music_id=music_id, difficulty=d, language=language,
                           fonts=fonts)
        jsonio.write_json(pdir / "live.json", live.index_doc(music_id, d, s, first["audio"], la["index"]))
        out[d] = (pdir, {"band": choice, "judgementNoteCount": s["judgementNoteCount"],
                         "fullComboCount": s["fullComboCount"], "scene": mode})
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
           language: str, prefix: str = "", regions: list[str] | None = None, memo: str | None = None,
           options: liveoptions.LiveOptions | None = None) -> dict:
    """One chart's files into the store + its manifest charts/<prefix><id>.json, serving `regions` (added to the
    regions of the manifest it replaces). `memo`: the build's id for stored_text. `options`: the live options the
    files carry (the manifest's `options`: LiveOptions.manifest_options)."""
    text, binary = collect(live_dir, files)
    facts = chart_facts(live_dir, summary, difficulty, language)
    if audio:
        _web_audio(live_dir, text, binary, audio_format, work, bgm_cache)
    else:
        binary = {k: v for k, v in binary.items() if Path(k).suffix.lower() not in AUDIO_EXT}
    entries = {p: stored_text(store, p, s, memo) for p, s in text.items()}
    entries.update({p: store.put(p, b) for p, b in binary.items()})
    offered = options.manifest_options() if options else None
    manifest = {"format": SITE_FORMAT, "musicId": music_id, "difficulty": difficulty, "audio": bool(audio),
                "audioFormat": audio_format if audio else None, "flows": flows, "quality": TRACE_QUALITY,
                **({"options": offered} if offered else {}), "chart": facts, "files": dict(sorted(entries.items()))}
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
    configure_caches(job)
    cat, master, player = open_data(cfg, job.get("region"))
    if lock is not None:
        _lock_fetches(cat, lock)
    _W.update(cat=cat, master=master, player=player, cfg=job, scenes={})


def configure_caches(job: dict) -> None:
    """The process caches of a build: the disk layer under the build's temporary root (shared by its processes)."""
    if job.get("cache"):
        cache.configure(directory=job["cache"])


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)


def _data(data=None):
    return data or (_W["cat"], _W["master"], _W["player"])


def _scene_part(cfg: dict, band: int):
    """(band part document, its files) of `band` from the build's scene directory, None when it has none."""
    d = Path(cfg["scenes"]) / str(band) if cfg.get("scenes") else None
    if d is None or not (d / livescene.BAND_DOC).is_file():
        return None
    parts = _W.setdefault("scenes", {})
    if band not in parts:
        parts[band] = json.loads((d / livescene.BAND_DOC).read_text(encoding="utf-8"))
    return parts[band], d / "livescene"


def band_task(band: int, music_id: int, cfg: dict | None = None, data=None) -> str:
    """The scene of `band` (livescene.band_part) into <scenes>/<band>/; `music_id`: a music of that band."""
    cat, master, player = _data(data)
    cfg = cfg or _W["cfg"]
    out = Path(cfg["scenes"]) / str(band)
    livescene.band_part(cat, player, out, master, music_id, band)
    return str(out)


def extract_task(music_id: int, difficulties: list[str], cfg: dict | None = None, data=None) -> dict:
    """The live directories of one music under a new temporary root: {music, root, dirs {d: (dir, summary)},
    extractSec}. The root is removed on failure (else by the caller after ingest)."""
    cat, master, player = _data(data)
    cfg = cfg or _W["cfg"]
    root = Path(tempfile.mkdtemp(prefix=f"m{music_id}-", dir=cfg["tmp"]))
    t0 = time.time()
    try:
        choice = live.resolve_band(cat, master, music_id, band=cfg["band"], leader_card=cfg["leaderCard"])
        dirs = build_music_dirs(cat, master, player, music_id, difficulties, root, Path(cfg["livenotes"]),
                                band=cfg["band"], leader_card=cfg["leaderCard"], language=cfg["language"],
                                fonts=cfg.get("fonts", "open"), scene=_scene_part(cfg, choice["band"]),
                                bgm=bgm_options(cfg["audioFormat"], cfg["audio"]),
                                options=cfg.get("options") or liveoptions.LiveOptions())
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return {"music": music_id, "root": str(root), "dirs": {d: (str(p), s) for d, (p, s) in dirs.items()},
            "extractSec": round(time.time() - t0, 1)}


def ingest_task(music_id: int, charts: list, root: str, cfg: dict | None = None) -> list[dict]:
    """Ingest the charts [(difficulty, dir, summary, files)] of one music; one result per chart."""
    cfg = cfg or _W["cfg"]
    site = Path(cfg["site"])
    store, bgm, out = Store(site), {}, []
    for d, pdir, summary, files in charts:
        try:
            r = ingest(store, site, music_id, d, Path(pdir), summary, files, list(FLOWS), cfg["audioFormat"],
                       cfg["audio"], Path(root), bgm, cfg["language"], cfg.get("prefix", ""), cfg.get("regions"),
                       memo=cfg.get("build"), options=cfg.get("options"))
            r["scene"] = summary.get("scene")
            out.append(r)
        except ConfigError:
            raise                                      # a setting the whole build needs (Pipeline)
        except Exception as e:
            out.append(_failure(f"{music_id}_{d}", "ingest", e))
    return out


def _failure(cid: str, stage: str, e: BaseException) -> dict:
    cause = f"{type(e).__name__}: {e}"
    _log(f"{cid}: {stage} failed: {cause[:300]}")
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return {"id": cid, "ok": False, "stage": stage, "error": cause[:2000], "trace": tb[-3000:]}


class Pipeline:
    """Charts of many musics through extract -> read sets -> ingest, each stage on its own executor.

    `extract(music, difficulties)` / `ingest(music, charts, root)` are submitted to `workers` (an Executor: the
    extraction processes), `read(dir, chart id)` to `readers` (threads that start Node). `window`: musics between
    the start of their extraction and the end of their ingest. `cleanup(root)` after a music's ingest. -> one result per chart.
    A failure fails its charts only, except a ConfigError (a setting or tool the build needs): run raises it once
    the stages in flight have ended."""

    def __init__(self, workers, readers, extract, read, ingest, cleanup, window: int, log=_log):
        self.workers, self.readers, self.window, self.log = workers, readers, max(1, window), log
        self.extract_fn, self.read_fn, self.ingest_fn, self.cleanup = extract, read, ingest, cleanup
        self.events: "queue.Queue" = queue.Queue()

    def run(self, musics: list[tuple[int, list[str]]]) -> list[dict]:
        todo = list(musics)
        todo.reverse()
        active, results, t0 = {}, [], {}

        def start():
            while todo and len(active) < self.window:
                m, ds = todo.pop()
                active[m] = {"ds": ds, "files": {}, "fails": [], "left": len(ds)}
                t0[m] = time.time()
                f = self.workers.submit(self.extract_fn, m, ds)
                f.add_done_callback(lambda f, m=m: self.events.put(("extracted", m, f)))

        start()
        stop = None                                    # the ConfigError that ends the run

        def failed(e) -> None:
            nonlocal stop
            if isinstance(e, ConfigError):
                stop = stop or e
                todo.clear()

        while active:
            kind, m, f, *rest = self.events.get()
            st = active[m]
            if stop is not None and kind == "extracted" and f.exception() is None:
                self.cleanup(f.result()["root"])
                del active[m]
                continue
            if kind == "extracted":
                try:
                    x = f.result()
                except Exception as e:
                    failed(e)
                    results += [_failure(f"{m}_{d}", "extract", e) for d in st["ds"]]
                    del active[m]
                    start()
                    continue
                st.update(x=x)
                for d in st["ds"]:
                    g = self.readers.submit(self.read_fn, x["dirs"][d][0], f"{m}_{d}")
                    g.add_done_callback(lambda g, m=m, d=d: self.events.put(("read", m, g, d)))
            elif kind == "read":
                d = rest[0]
                try:
                    st["files"][d] = f.result()
                except Exception as e:
                    failed(e)
                    st["fails"].append(_failure(f"{m}_{d}", "read set", e))
                st["left"] -= 1
                if st["left"] == 0 and stop is not None:
                    self.cleanup(st["x"]["root"])
                    del active[m]
                elif st["left"] == 0:
                    x = st["x"]
                    charts = [(d, *x["dirs"][d], st["files"][d]) for d in st["ds"] if d in st["files"]]
                    g = self.workers.submit(self.ingest_fn, m, charts, x["root"])
                    g.add_done_callback(lambda g, m=m: self.events.put(("ingested", m, g)))
            else:
                x = st["x"]
                try:
                    rs = f.result()
                except Exception as e:
                    failed(e)
                    rs = [_failure(f"{m}_{d}", "ingest", e) for d in st["files"]]
                rs += st["fails"]
                dt = time.time() - t0[m]
                for r in rs:
                    r.update(music=m, extractSec=x["extractSec"], musicSec=round(dt, 1))
                self.log(f"{m}: {sum(r['ok'] for r in rs)}/{len(rs)} charts, extract {x['extractSec']:.0f} s, "
                         f"total {dt:.0f} s")
                results += rs
                self.cleanup(x["root"])
                del active[m]
                start()
        if stop is not None:
            raise stop
        return results


def _pool_call(fn_name: str, *args):
    return globals()[fn_name](*args)


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


READ_SETS = cache.bucket("readsets", salt=cache.source_salt(__file__), disk=True)
READ_SET_CHECK = 10          # plan mode: the build's first chart and 1 in this many others also run the full simulation
READ_SET_SERVE_TIMEOUT = 600.0   # seconds a served read set may take (ReadSetServers); then its process is ended
_player_ids: dict = {}
_features: dict = {}
_features_lock = threading.Lock()


def player_id(player_dir: Path) -> str:
    """Key of the player code a read set runs: every file of its src/, scripts/ and package.json, and the Node
    executable (file identity and version)."""
    player_dir = Path(player_dir)
    node = tool("node", "node")
    k = (str(player_dir), node)
    if k not in _player_ids:
        files = [p for d in ("src", "scripts") for p in sorted((player_dir / d).rglob("*")) if p.is_file()]
        files += [player_dir / "package.json"] if (player_dir / "package.json").is_file() else []
        version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        _player_ids[k] = cache.key(cache.file_id(node), version,
                                   [(p.relative_to(player_dir).as_posix(), p.read_bytes()) for p in files])
    return _player_ids[k]


def player_features(player_dir: Path) -> frozenset:
    """The read-set modes the player's READ_SET_SCRIPT offers beyond the full simulation (`--features` -> JSON
    {"features": [...]}; a script without the option exits with its usage: none). Asked once per player code."""
    k = player_id(player_dir)
    with _features_lock:
        if k not in _features:
            r = subprocess.run([tool("node", "node"), str(Path(player_dir) / READ_SET_SCRIPT), "--features"],
                               capture_output=True, text=True, encoding="utf-8")
            feats = frozenset()
            if r.returncode == 0:
                try:
                    doc = json.loads(r.stdout)
                except ValueError:
                    doc = None
                if not (isinstance(doc, dict) and isinstance(doc.get("features"), list)):
                    raise RuntimeError(f"read set --features: expected a JSON object with a features list, got "
                                       f"{r.stdout[:200]!r}")
                feats = frozenset(str(f) for f in doc["features"])
            _features[k] = feats
        return _features[k]


def live_dir_digest(live_dir: Path) -> list:
    """[(path relative to live_dir, sha256)] of every file of a live directory, in path order."""
    live_dir = Path(live_dir)
    files = sorted(p for p in live_dir.rglob("*") if p.is_file())
    return [(p.relative_to(live_dir).as_posix(), hashlib.sha256(p.read_bytes()).digest()) for p in files]


def read_set_cached(live_dir: Path, player_dir: Path, mode: str = "full", digest: list | None = None,
                    run=None, settings: dict | None = None) -> list[str]:
    """read_set, reused when the live directory's files, the player code, Node, the mode and the settings are the
    same as for a read set stored before (READ_SETS: this process, and the disk layer of the build's cache).
    `digest`: the live directory's live_dir_digest when the caller has it. `run`: what reads a set that is not
    stored, with read_set's arguments and answer (default read_set; ReadSetServers.read_set answers the same)."""
    live_dir = Path(live_dir)
    parts = [player_id(player_dir), mode, digest if digest is not None else live_dir_digest(live_dir)]
    if settings:
        parts.append(json.dumps(settings, sort_keys=True))
    k = READ_SETS.key(*parts)
    hit = READ_SETS.get(k)
    if hit is not None:
        return json.loads(hit)
    out = (run or read_set)(live_dir, player_dir, mode, **({"settings": settings} if settings else {}))
    READ_SETS.put(k, json.dumps(out).encode("utf-8"))
    return out


def read_set(live_dir: Path, player_dir: Path, mode: str = "full", settings: dict | None = None) -> list[str]:
    """Every file of `live_dir` the player reads for the whole chart in its default state (`settings`: with those
    Live options instead, player_features has "settings"), from the player's own code run in Node
    (READ_SET_SCRIPT). mode "full": the simulation (no-op WebGL2 / WebAudio, every frame from the start to the
    chart's end); "plan": the player lists the same files from the chart's contents without stepping frames
    (player_features has "plan"; ReadSets checks it against the full simulation)."""
    if mode not in ("full", "plan"):
        raise ValueError(f"read set mode {mode}")
    r = subprocess.run([tool("node", "node"), str(Path(player_dir) / READ_SET_SCRIPT), str(Path(live_dir)),
                        *(["--plan"] if mode == "plan" else []),
                        *([f"--settings={json.dumps(settings, sort_keys=True)}"] if settings else [])],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"read set of {Path(live_dir).name} failed:\n{(r.stderr or r.stdout)[-4000:]}")
    return read_set_answer(json.loads(r.stdout), mode)


def read_set_answer(out, mode: str) -> list[str]:
    """The files of an answer of the read-set script (its JSON output) in `mode`, checked: a plan answered in plan
    mode, a full simulation that reached the end of the chart, a list of paths."""
    if mode == "plan" and not (isinstance(out, dict) and out.get("mode") == "plan"):
        raise RuntimeError("read set --plan: the player did not answer in plan mode")
    if isinstance(out, dict):                          # {files, state, frames} form
        if mode == "full" and out.get("state", "ended") != "ended":
            raise RuntimeError(f"read set: the chart did not end ({out.get('state')} after {out.get('frames')} frames)")
        out = out.get("files")
    if not isinstance(out, list) or not all(isinstance(p, str) for p in out):
        raise RuntimeError("read set: expected a JSON list of paths")
    return out


class ReadSetServerError(RuntimeError):
    """A read-set server that exited, answered something else than an answer, or none within the timeout (hung)."""

    def __init__(self, message: str, hung: bool = False):
        super().__init__(message)
        self.hung = hung


class ReadSetServer:
    """One `node READ_SET_SCRIPT --serve` process (the player's feature "serve"): read sets one at a time, each asked
    by a JSON line on its stdin and answered by one on its stdout; the last lines of its stderr are kept for errors."""

    def __init__(self, player_dir: Path):
        self.proc = subprocess.Popen([tool("node", "node"), str(Path(player_dir) / READ_SET_SCRIPT), "--serve"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace")
        self.lines: queue.Queue = queue.Queue()
        self.stderr: collections.deque = collections.deque(maxlen=50)
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines.put, True), daemon=True).start()
        self._stderr_pump = threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr.append, False),
                                             daemon=True)
        self._stderr_pump.start()

    @staticmethod
    def _pump(stream, put, eof: bool) -> None:
        for line in stream:
            put(line)
        if eof:
            put(None)

    def ask(self, request: dict, timeout: float) -> dict:
        """The answer to `request`: {"ok": true, "result"} or {"ok": false, "error"}. ReadSetServerError when the
        process has exited, answers something else or nothing within `timeout` seconds."""
        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass                                       # exited: the end of its stdout follows
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise ReadSetServerError(f"no answer within {timeout:.0f} s", hung=True) from None
        if line is None:
            code = self.proc.wait()
            self._stderr_pump.join(timeout=2)
            raise ReadSetServerError(f"the read-set server exited ({code}):\n"
                                     f"{''.join(self.stderr)[-4000:]}")
        try:
            doc = json.loads(line)
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("ok"), bool):
            raise ReadSetServerError(f"the read-set server answered {line[:200]!r}")
        return doc

    def close(self, wait: float = 10.0) -> None:
        """End of its stdin: the server exits (ended after `wait` seconds if it does not)."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait()
        try:
            self.proc.stdin.close()
        except OSError:
            pass


class ReadSetServers:
    """Read-set servers for the reads of a build: a read takes an idle server or starts one (so there are at most as
    many as reads run at a time: one per read-set slot), and gives it back for the build's later charts; close()
    ends them. `read_set` has read_set's arguments and answers as read_set does. A server that exits or answers
    something else is replaced and the chart asked once more; a chart it answers no read set for within `timeout`
    seconds fails (RuntimeError, as a failed read_set) and the server is ended."""

    def __init__(self, player_dir: Path, timeout: float = READ_SET_SERVE_TIMEOUT):
        self.player_dir, self.timeout = Path(player_dir), timeout
        self.lock = threading.Lock()
        self.idle: list[ReadSetServer] = []
        self.started = 0
        self.closed = False

    def _take(self) -> ReadSetServer:
        with self.lock:
            if self.closed:
                raise RuntimeError("read set: the build's read-set servers are closed")
            while self.idle:
                s = self.idle.pop()
                if s.proc.poll() is None:
                    return s
                s.kill()                               # exited while idle
            self.started += 1
        return ReadSetServer(self.player_dir)

    def _give(self, s: ReadSetServer) -> None:
        with self.lock:
            if not self.closed:
                self.idle.append(s)
                return
        s.close()

    def read_set(self, live_dir: Path, player_dir: Path, mode: str = "full", settings: dict | None = None) -> list[str]:
        if mode not in ("full", "plan"):
            raise ValueError(f"read set mode {mode}")
        name = Path(live_dir).name
        request = {"chart": str(Path(live_dir)), "plan": mode == "plan", **({"settings": settings} if settings else {})}
        for attempt in (1, 2):
            s = self._take()
            try:
                doc = s.ask(request, self.timeout)
            except ReadSetServerError as e:
                s.kill()
                if e.hung or attempt == 2:
                    raise RuntimeError(f"read set of {name} failed: {e}") from None
                continue
            self._give(s)
            if not doc["ok"]:
                raise RuntimeError(f"read set of {name} failed:\n{str(doc.get('error'))[-4000:]}")
            return read_set_answer(doc.get("result"), mode)

    def close(self) -> None:
        with self.lock:
            self.closed, idle, self.idle = True, self.idle, []
        for s in idle:
            s.close()


class ReadSetMismatch(RuntimeError):
    """The player's plan and its full simulation read different files for a chart."""


class ReadSets:
    """The read sets of a build. With a player whose read-set script has the plan mode, each chart's set comes
    from the plan; the build's first chart (expect: the first in build order, whenever its read set comes) and 1 in
    `check_every` others (chosen by the player code and chart id) also run the full simulation, and the two must be
    the same set. Which charts are checked depends on the inputs only, not on which extraction ends first, so the
    same build reads and caches the same sets. A difference fails that chart (ReadSetMismatch, with both
    differences), the full simulation reads every later chart, and the charts read from an unchecked plan until then
    are listed. A chart the plan cannot list (the script fails) is read by the full simulation, logged and listed in
    the summary (planErrors). A player without the plan mode: the full simulation for every chart. The player is
    asked for its modes when the first chart needs a read set. With a player that also has the serve mode, the plans
    are read by long-lived processes (ReadSetServers, one per read-set slot) instead of one process per chart; the
    full simulation always runs in a process of its own. close() ends the servers. `summary()` goes into the build
    result. `read(..., variants)`: the union with the chart's read sets under each of the Live option settings
    `variants` (the player's feature "settings"; plans in plan mode, a variant whose plan fails read by the full
    simulation), not checked by the full simulation."""

    def __init__(self, player_dir: Path, log=_log, check_every: int = READ_SET_CHECK):
        self.player_dir = Path(player_dir)
        self.initial = self.mode = None
        self.check_every = max(1, check_every)
        self.log = log
        self.lock = threading.Lock()                   # counters, mode
        self.first: str | None = None                  # the chart checked as the build's first (expect)
        self.counts = {"plan": 0, "full": 0, "checked": 0}
        self.variants = 0                              # read sets of option variants (read's `variants`)
        self.features: frozenset = frozenset()
        self.unchecked: list[str] = []
        self.mismatches: list[dict] = []
        self.plan_errors: list[dict] = []
        self.servers: ReadSetServers | None = None      # the plans' servers (the player's serve mode)

    def start(self) -> str:
        """The mode (player_features), asked once."""
        with self.lock:
            if self.initial is None:
                features = self.features = player_features(self.player_dir)
                self.initial = self.mode = "plan" if "plan" in features else "full"
                if self.initial == "plan" and "serve" in features:
                    self.servers = ReadSetServers(self.player_dir)
            return self.mode

    def close(self) -> None:
        """Ends the plans' servers; a later plan runs in a process of its own."""
        with self.lock:
            servers, self.servers = self.servers, None
        if servers is not None:
            servers.close()

    def expect(self, chart_ids) -> None:
        """The charts a build (group) reads, in build order; the first of the first call is the chart checked as the
        build's first."""
        with self.lock:
            if self.first is None and chart_ids:
                self.first = chart_ids[0]

    def describe(self) -> str:
        if self.start() == "full":
            return "read sets: the full simulation of every chart (the player has no plan mode)"
        return (f"read sets: the player's plan{' (served)' if self.servers is not None else ''}; "
                f"{self.first or 'no first chart'} and 1 in {self.check_every} other "
                f"charts checked by the full simulation")

    def need_settings(self) -> None:
        """ConfigError unless the player's read-set script reads with given settings (feature "settings")."""
        self.start()
        if "settings" not in self.features:
            raise ConfigError(f"{self.player_dir}: its {READ_SET_SCRIPT} has no settings (feature \"settings\"), "
                              f"which the read sets of live options need")

    def sampled(self, chart_id: str) -> bool:
        h = hashlib.sha256(f"{player_id(self.player_dir)}|{chart_id}".encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.check_every == 0

    def read(self, live_dir, chart_id: str, variants=()) -> list[str]:
        live_dir = Path(live_dir)
        self.start()
        digest = live_dir_digest(live_dir)
        files = self._default(live_dir, chart_id, digest)
        if not variants:
            return files
        have = set(files)
        more: set[str] = set()
        for settings in variants:
            more.update(p for p in self._variant(live_dir, chart_id, digest, settings) if p not in have)
        with self.lock:
            self.variants += len(variants)
        return files + sorted(more)

    def _variant(self, live_dir: Path, chart_id: str, digest: list, settings: dict) -> list[str]:
        """The read set with `settings`: the plan in plan mode (the full simulation when the plan fails)."""
        if self.mode == "plan":
            servers = self.servers
            try:
                return read_set_cached(live_dir, self.player_dir, "plan", digest,
                                       run=servers.read_set if servers is not None else None, settings=settings)
            except RuntimeError as e:
                with self.lock:
                    self.plan_errors.append({"id": chart_id, "settings": settings, "error": str(e)[-500:]})
                self.log(f"{chart_id}: the player's read-set plan with {json.dumps(settings, sort_keys=True)} "
                         f"failed; the full simulation reads it")
        return read_set_cached(live_dir, self.player_dir, "full", digest, settings=settings)

    def _default(self, live_dir: Path, chart_id: str, digest: list) -> list[str]:
        if self.mode == "plan":
            if chart_id == self.first or self.sampled(chart_id):
                return self._checked(live_dir, chart_id, digest)
            files = self._plan(live_dir, chart_id, digest)
            if files is not None:
                with self.lock:
                    self.counts["plan"] += 1
                    self.unchecked.append(chart_id)
                return files
        files = read_set_cached(live_dir, self.player_dir, "full", digest)
        with self.lock:
            self.counts["full"] += 1
        return files

    def _plan(self, live_dir: Path, chart_id: str, digest: list) -> list[str] | None:
        """The plan's read set, None when the player's script failed on the chart (logged, listed)."""
        servers = self.servers
        try:
            return read_set_cached(live_dir, self.player_dir, "plan", digest,
                                   run=servers.read_set if servers is not None else None)
        except RuntimeError as e:
            with self.lock:
                self.plan_errors.append({"id": chart_id, "error": str(e)[-500:]})
            self.log(f"{chart_id}: the player's read-set plan failed; the full simulation reads it "
                     f"({str(e).strip().splitlines()[-1][:200] if str(e).strip() else type(e).__name__})")
            return None

    def _checked(self, live_dir: Path, chart_id: str, digest: list) -> list[str]:
        plan = self._plan(live_dir, chart_id, digest)
        full = read_set_cached(live_dir, self.player_dir, "full", digest)
        if plan is None:
            with self.lock:
                self.counts["full"] += 1
            return full
        missing, extra = sorted(set(full) - set(plan)), sorted(set(plan) - set(full))
        with self.lock:
            self.counts["checked"] += 1
            if not missing and not extra:
                return full
            self.mismatches.append({"id": chart_id, "missing": missing, "extra": extra})
            if self.mode == "plan":
                self.mode = "full"
                self.log(f"{chart_id}: the player's read-set plan differs from its full simulation "
                         f"({len(missing)} missing, {len(extra)} extra); the full simulation reads the other charts")
        raise ReadSetMismatch(f"the player's plan and its full simulation differ: missing {missing[:10]}"
                              f"{' ...' if len(missing) > 10 else ''}, extra {extra[:10]}"
                              f"{' ...' if len(extra) > 10 else ''}")

    def summary(self) -> dict:
        """{mode, plan, full, checked, variants?, mismatches, planErrors, unchecked}: charts read by each mode
        (checked: by both), read sets of option variants (when there were any); after a mismatch the charts whose
        set came from an unchecked plan are listed, else counted."""
        with self.lock:
            return {"mode": self.initial, **self.counts, **({"variants": self.variants} if self.variants else {}),
                    "mismatches": list(self.mismatches), "planErrors": list(self.plan_errors),
                    "unchecked": sorted(self.unchecked) if self.mismatches else len(self.unchecked)}


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


def write_player(site: Path, player_dir: Path, stories: bool = False) -> dict:
    """The player's page (every file of PLAYER_PAGE_DIR; in its .html / .js files the PAGE_IMPORTS specifiers point
    at the bundle and PLAYER_VERSION_MARKER is the bundles' short hash) and bundles (PLAYER_BUNDLES and their source
    maps) into the site root; when the player has the Live2D page, the same for LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS
    and LIVE2D_BUNDLES into LIVE2D_PAGE_SITE_DIR; when it has the story page and its bundles, the same for
    STORY_PAGE_DIR, STORY_PAGE_IMPORTS and STORY_BUNDLES into STORY_PAGE_SITE_DIR (`stories`: the build adds stories,
    so a story page without its bundles is a ConfigError instead of being left out)."""
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
    story_page = {}
    if (player_dir / STORY_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in STORY_BUNDLES if not (player_dir / rel).is_file()]
        if missing and stories:
            raise ConfigError(f"{player_dir} has the story page but not {', '.join(missing)} "
                              f"(a checkout needs its build first)")
        if not missing:
            story_bundles, story_version = player_bundles(player_dir, STORY_BUNDLES)
            story_page = page_files(player_dir / STORY_PAGE_DIR, STORY_PAGE_IMPORTS, story_version,
                                    "STORY_PAGE_IMPORTS")
            files.update({f"{STORY_PAGE_SITE_DIR}/{r}": d for r, d in {**story_page, **story_bundles}.items()})
    for rel, data in files.items():
        if rel.split("/", 1)[0] in SITE_DATA:
            raise RuntimeError(f"player page file {rel} would overwrite the site data")
        dst = Path(site) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    out = {"playerVersion": version, "playerBytes": sum(map(len, bundles.values())), "pageFiles": len(page),
           "live2dPageFiles": len(live2d)}
    if story_page:
        out["storyPageFiles"] = len(story_page)
    return out


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
    """site/charts.json from every chart manifest present (index_meta: `language`, `regions` of this build),
    site/models.json from every model manifest (webmodel.write_models_index) and site/stories.json from every story
    manifest (storysite.write_stories_index; its `language` and `regions` are those storysite.build wrote); assets
    no chart, no model and no story (common files and every language group) references removed."""
    from .storysite import write_stories_index
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
    stories, story_assets = write_stories_index(site)
    used |= story_assets
    removed = 0
    for f in (site / "assets").iterdir():
        if f"assets/{f.name}" not in used:
            f.unlink()
            removed += 1
    sizes = [f.stat().st_size for f in (site / "assets").iterdir()]
    out = {"charts": len(charts), "models": models, "assets": len(sizes), "assetBytes": sum(sizes),
           "removedAssets": removed}
    if stories:
        out["stories"] = stories
    return out


# ---------------------------------------------------------------- build
def pipeline_window(workers: int, read_workers: int) -> int:
    """Musics in flight in a build's Pipeline: one per extraction process, plus enough musics waiting for their read
    sets to keep every read-set slot busy (4 charts a music), at least 2 (each in-flight music keeps its live
    directories on disk until its ingest)."""
    return workers + max(2, -(-read_workers // 4))


def _group_pairs(pairs, master: Path) -> list[tuple[int, str]]:
    """The charts of a region group: `pairs` (None: every chart) that its master data has, in the given order."""
    have = all_pairs(master)
    if pairs is None:
        return have
    have = set(have)
    return [p for p in pairs if p in have]


def _build_group(site: Path, tmp_root: Path, pairs, cfg: Config, region: str, prefix: str, regions: list[str],
                 job: dict, force: bool, workers: int | None, log, read_workers: int | None = None,
                 reads: "ReadSets | None" = None) -> tuple[list, list, int]:
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
    cpus = usable_cpus()
    if workers is None:
        workers = max(1, min(8, len(by_music), cpus // 4))
    if read_workers is None:
        read_workers = max(1, min(16, 4 * len(by_music), cpus // 2))
    results = []
    if not by_music:
        return results, skipped, workers
    gdir = Path(tempfile.mkdtemp(prefix="global-", dir=tmp_root))
    try:
        cat, master, player = open_data(cfg, region)
        options = liveoptions.resolve(job.get("liveOptions"), master)
        livenotes.extract(cat, player, gdir, master=master, options=options)
        job = {**job, "livenotes": str(gdir / "livenotes"), "scenes": str(gdir / "scenes"), "region": region,
               "prefix": prefix, "regions": regions, "options": options}
        variants = options.read_variants()
        bands: dict[int, int] = {}                     # band -> a music of it (the scene is built once per band)
        for m in sorted(by_music):
            try:
                bands.setdefault(live.resolve_band(cat, master, m, band=job["band"],
                                                   leader_card=job["leaderCard"])["band"], m)
            except Exception:
                pass                                   # the music's extraction reports it
        log(f"{sum(len(ds) for ds in by_music.values())} charts of {len(by_music)} musics"
            f"{f' for {prefix[:-1]}' if prefix else ''}, {len(bands)} band scene(s); {workers} extraction "
            f"process(es) ({cri.default_workers()} stream decodes each), {read_workers} read set(s) at a time, "
            f"{pipeline_window(workers, read_workers)} musics in flight; caches "
            f"{cache.settings()['memory'] >> 20} MB per bucket, disk "
            f"{job.get('cache') or 'none'}")
        own_reads = reads is None
        reads = reads or ReadSets(Path(job["player"]), log)
        reads.expect([f"{m}_{d}" for m, ds in sorted(by_music.items()) for d in ds])     # the Pipeline's order
        log(reads.describe())
        if variants:
            reads.need_settings()
            log(f"live options: the read set of each chart adds {len(variants)} option variant(s)")

        if workers <= 1:
            data = (cat, master, player)
            _W.update(cfg=job, scenes={})
            ex = ThreadPoolExecutor(1)                 # UnityPy stays on one thread
            call = {"band": lambda b, m: band_task(b, m, job, data),
                    "extract": lambda m, ds: extract_task(m, ds, job, data),
                    "ingest": lambda m, charts, root: ingest_task(m, charts, root, job)}
            submit = lambda name, *a: ex.submit(call[name], *a)
            ctx = None
        else:
            ctx = mp.get_context("spawn")
            mgr = ctx.Manager()
            ex = ProcessPoolExecutor(workers, mp_context=ctx, initializer=_worker_init,
                                     initargs=(cfg, mgr.Lock(), job))
            submit = lambda name, *a: ex.submit(_pool_call, f"{name}_task", *a)
        try:
            for b, f in [(b, submit("band", b, m)) for b, m in sorted(bands.items())]:
                try:
                    f.result()
                except ConfigError:
                    raise
                except Exception as e:                  # its musics export the scene whole
                    log(f"band {b}: shared scene failed ({type(e).__name__}: {str(e)[:200]}); exported per music")
                    shutil.rmtree(gdir / "scenes" / str(b), ignore_errors=True)
            with ThreadPoolExecutor(read_workers) as readers:
                shim = SimpleNamespace(submit=lambda fn, *a: submit({extract_task: "extract",
                                                                     ingest_task: "ingest"}[fn], *a))
                def read(live_dir, chart_id):
                    return reads.read(live_dir, chart_id, variants)
                results = Pipeline(shim, readers, extract_task, read, ingest_task,
                                   lambda root: shutil.rmtree(root, ignore_errors=True),
                                   window=pipeline_window(workers, read_workers), log=log
                                   ).run(sorted(by_music.items()))
        finally:
            ex.shutdown()
            if ctx is not None:
                mgr.shutdown()
            if own_reads:
                reads.close()
    finally:
        shutil.rmtree(gdir, ignore_errors=True)
    for r in results:
        if prefix:
            r["id"] = prefix + r["id"]
    return results, skipped, workers


def build(out_dir, pairs, cfg: Config, player_dir: Path, audio_format: str = DEFAULT_AUDIO_FORMAT,
          audio: bool = True, force: bool = False, *, tmp_dir=None, log=None, workers: int | None = None,
          band: int | None = None, leader_card: int | None = None, regions: list[str] | None = None,
          fonts: str = "open", read_workers: int | None = None, live_options: dict | None = None) -> dict:
    """Add the charts `pairs` ([(musicId, difficulty)]; None: every chart of every region's master data) to the site
    at `out_dir`, with the player of the ournotes-player checkout or package at `player_dir`. The data comes from the
    settings `cfg` (each worker process opens its own). `regions`: the regions the charts serve (default: the one
    [catalog] region), grouped by chart inputs (module docstring); the first region's group writes charts/<id>.json,
    the others charts/<region>/<id>.json unless their files equal the shared manifest's. `workers`: parallel music
    processes (default a quarter of the CPUs, up to 8); `read_workers`: read sets at a time (default half the CPUs,
    up to 16). `band` / `leader_card`: the band of every chart's stage, as for live.build (default: the band of the
    music's first vocal character); `fonts`: the start canvas fonts (liveui.extract, with WEB_LIVEUI);
    `live_options`: the Live option variants the charts offer (a liveoptions.parse_specs request; checked against
    every region's master data before anything is built, liveoptions.OptionSpecError)."""
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
    for m in masters.values():
        liveoptions.resolve(live_options, m)
    groups = region_groups(masters)
    site = Path(out_dir).resolve()
    (site / "charts").mkdir(parents=True, exist_ok=True)
    Store(site)
    tmp_root = Path(tmp_dir).resolve() if tmp_dir else site.parent / f"{site.name}.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log = log or _log
    t0 = time.time()
    job = {"site": str(site), "tmp": str(tmp_root), "audioFormat": audio_format, "audio": bool(audio),
           "player": str(player_dir), "language": language, "fonts": fonts, "band": band, "leaderCard": leader_card,
           "cache": str(tmp_root / "cache"), "build": uuid.uuid4().hex, "liveOptions": live_options or None}
    configure_caches(job)
    results, skipped, folded, used_workers = [], [], [], 1
    offered = set()
    reads = ReadSets(player_dir, log)
    try:
        for gi, group in enumerate(groups):
            rep = group[0]
            prefix = "" if gi == 0 else f"{rep}/"
            gpairs = _group_pairs(pairs, masters[rep])
            offered.update(gpairs)
            rs, sk, w = _build_group(site, tmp_root, gpairs, cfg, rep, prefix, group, job, force, workers, log,
                                     read_workers, reads)
            results += rs
            skipped += sk
            used_workers = max(used_workers, w)
            if prefix:
                folded += [prefix + f"{m}_{d}" for m, d in gpairs if fold_variant(site, f"{m}_{d}", prefix)]
    finally:
        reads.close()
    for m, d in pairs or []:
        if (m, d) not in offered:
            results.append({"id": f"{m}_{d}", "ok": False, "stage": "master",
                            "error": f"no MasterLiveMusicScore row for {m}_{d} in the master data of "
                                     f"{', '.join(regions)}"})
    v = write_player(site, player_dir)
    idx = write_index(site, language, region_meta(cfg, regions))
    failed = [r for r in results if not r["ok"]]
    scenes: dict[str, int] = {}                        # how the charts' scenes were made (band part / whole)
    for r in results:
        if r["ok"]:
            scenes[r.get("scene") or "?"] = scenes.get(r.get("scene") or "?", 0) + 1
    if failed:
        (site.parent / f"{site.name}.failures.json").write_bytes(_dump(sorted(failed, key=lambda r: r["id"])))
    return {"site": str(site), "built": [{k: r[k] for k in ("id", "files", "bytes", "flows")} for r in results if r["ok"]],
            "failed": [{k: r.get(k) for k in ("id", "stage", "error")} for r in failed], "skipped": skipped,
            "regions": regions, "regionGroups": groups, "foldedRegionManifests": folded, "scenes": scenes,
            "readSets": reads.summary(),
            "seconds": round(time.time() - t0, 1), "workers": used_workers, **v, **idx}
