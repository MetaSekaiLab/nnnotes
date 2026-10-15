"""The files of a static site that the chart, model and story builds share (the site's layout: web.py).

Store, site_store       site/assets/: write-once content-addressed files named by the SHA-256 of their decoded
                        bytes, stored encoded where that makes them smaller
text_asset, split_json  the stored text of a JSON or GLSL file; a large JSON object stored per top-level key and
                        rejoined by the player (join_parts)
entry_assets, entry_text, reingest_json
                        manifest entries: the assets they reference, the file text they stand for, every JSON file
                        stored again
collect                 the files a read set names in a live or story directory
check_player, write_player
                        the ournotes-player build: its bundles and its pages (chart list, songs, Live2D, story)
open_data, site_regions, region_masters, region_meta, merge_regions
                        the data of the regions a site serves
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

from ..core import cache, jsonio
from ..formats.shader import PLAYER_PLATFORM as SHADER_PLATFORM
from ..formats.shader import PLAYER_TYPE as SHADER_TYPE
from ..source.config import Config, ConfigError
from . import compress
from .compress import DEFAULT_ENCODING

SITE_FORMAT = 2  # charts.json "format"
MANIFEST_FORMAT = 3  # chart manifest "format" (3: entries may be stored encoded)
SPLIT_MIN_BYTES = 512 * 1024
SPLIT_KEY = re.compile(r"[A-Za-z0-9_$.\-]+")  # keys whose JSON text is the same in Python and JavaScript
# files of the ournotes-player checkout / package the site uses
READ_SET_SCRIPT = "scripts/read-set.mjs"  # node <script> <live dir> -> JSON list of the paths the player reads
PLAYER_BUNDLES = ("dist/ournotes-player.element.min.js",)
PLAYER_PAGE_DIR = "examples/chart-list"
SONGS_PAGE_DIR = "examples/songs"
SONGS_PAGE_SITE_DIR = "songs"
# module specifiers of the page that name the player's sources -> the bundle copied next to the page
PAGE_IMPORTS = {"../../src/element.js": "./ournotes-player.element.min.js"}
# the player's Live2D model page (when the player has it): its files and bundle go to SITE/<LIVE2D_PAGE_SITE_DIR>/
LIVE2D_PAGE_DIR = "examples/live2d"
LIVE2D_PAGE_SITE_DIR = "live2d"
LIVE2D_BUNDLES = ("dist/ournotes-player.live2d.element.min.js",)
LIVE2D_PAGE_IMPORTS = {"../../src/live2d/define.js": "./ournotes-player.live2d.element.min.js"}
# the player's story page (when the player has it and its bundle): its files and bundle go to
# SITE/<STORY_PAGE_SITE_DIR>/
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
PLAYER_VERSION_MARKER = "@PLAYER_VERSION@"  # replaced in the page's files by the bundles' short hash
# collected files: text as UTF-8, binary as is; shader programs of the WebGL2 tier only
TEXT_EXT = {".json", ".glsl"}
BINARY_EXT = {
    ".png",
    ".flac",
    ".ogg",
    ".wav",
    ".moc3",
    ".glb",
    ".atlas",
    ".skel",
    ".bin",
}  # .glb / .atlas / .skel: an Overlay story's home spot; .bin: a story's CRI Lips data (crilips.py)
CHARTS_INDEX = "charts.json"
# documentation fields of the extractors the player does not read (live-audio.json `spec`, the voice rule notes,
# scene.json `slice` / `derived` notes): left out of the site's copies
DOC_FIELDS = {
    "audio/live-audio.json": [("spec",), ("voice", "rule"), ("voice", "decision")],
    "livescene/scene.json": [
        ("slice", "bandChoice", "source"),
        ("slice", "bandChoice", "note"),
        ("derived", "lane", "sources"),
        ("derived", "fovRule", "source"),
    ],
}


# ---------------------------------------------------------------- stored text
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
def mp4_priming(data: bytes) -> int:
    """Encoder delay of an MP4 audio track: media_time of the first edit list entry (moov/trak/edts/elst)."""

    def boxes(b, off, end):
        while off + 8 <= end:
            size, kind = struct.unpack(">I4s", b[off : off + 8])
            hdr = 8
            if size == 1:
                size, hdr = struct.unpack(">Q", b[off + 8 : off + 16])[0], 16
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
    count = struct.unpack(">I", data[a + 4 : a + 8])[0]
    if count < 1:
        return 0
    if version == 1:
        return max(0, struct.unpack(">q", data[a + 16 : a + 24])[0])
    return max(0, struct.unpack(">i", data[a + 12 : a + 16])[0])


# ---------------------------------------------------------------- store
class Store:
    """site/assets/<sha256>.<ext>[.gz|.br]: write-once content-addressed files named by the SHA-256 of their decoded
    bytes (safe for concurrent writers). `encoding` (compress.ENCODINGS): a file whose extension is
    compress.COMPRESSIBLE is stored gzip- or brotli-encoded when that makes it smaller, under its name plus .gz / .br,
    and its entry gains `stored` (the encoded length); "none" stores every file as it is."""

    def __init__(self, site: Path, encoding: str = DEFAULT_ENCODING):
        compress.check(encoding)
        self.encoding = encoding
        self.dir = Path(site) / "assets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.written = 0
        self.reused = 0
        self._have: dict[str, dict] = {}  # <sha256>.<ext> this store has written or found -> its entry

    def put(self, path: str, data: bytes) -> dict:
        ext = Path(path).suffix.lower().lstrip(".") or "bin"
        key = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        e = self._have.get(key)
        if e is None:
            e = self._have[key] = self._put_encoded(key, ext, data) or self._put_raw(key, data)
        else:
            self.reused += 1
        return dict(e)

    def _put_raw(self, name: str, data: bytes) -> dict:
        dst = self.dir / name
        if dst.exists() and dst.stat().st_size == len(data):
            self.reused += 1
        else:
            self._write(dst, data)
        return {"asset": f"assets/{name}", "size": len(data)}

    def _put_encoded(self, key: str, ext: str, data: bytes) -> dict | None:
        """`data` stored encoded as `key` + .gz / .br, None when this store does not encode it (encoding "none", an
        extension not compressible, an encoded form not smaller). A file of that name that holds `data` is kept
        whatever encoder wrote it, so the entries naming it keep their `stored`."""
        if self.encoding == "none" or ext not in compress.COMPRESSIBLE:
            return None
        name = key + compress.SUFFIXES[self.encoding]
        dst = self.dir / name
        stored = dst.stat().st_size if dst.is_file() else None
        if stored is not None and stored < len(data) and compress.holds(dst, data):
            self.reused += 1
        else:
            enc = compress.encode(self.encoding, data)
            if len(enc) >= len(data):
                return None
            self._write(dst, enc)
            stored = len(enc)
        return {"asset": f"assets/{name}", "size": len(data), "stored": stored}

    def _write(self, dst: Path, stored: bytes) -> None:
        tmp = cache.temp_path(dst)
        tmp.write_bytes(stored)
        for i in range(20):
            try:
                os.replace(tmp, dst)
                break
            except PermissionError:
                if dst.exists() and dst.stat().st_size == len(stored):
                    tmp.unlink()
                    break
                time.sleep(0.1 * (i + 1))
        else:
            raise RuntimeError(f"could not store {dst}")
        self.written += 1

    def put_file(self, path: str, data: bytes) -> dict:
        """One file of a chart: whole, or per top-level key for a large JSON object (see split_json)."""
        parts = split_json(data) if path.endswith(".json") else None
        if parts is None:
            return self.put(path, data)
        return self.put_parts(path, parts, len(data))

    def put_parts(self, path: str, parts: list[tuple[str, bytes]], size: int) -> dict:
        """A JSON file of `size` bytes stored per top-level key (`parts`: [(key, value text)], join_parts rebuilds
        it): {size, parts: [[key, asset, size], ...]}, a part [key, asset, size, stored] when encoded."""
        return {"size": size, "parts": [part_entry(k, self.put(f"{path}#{k}.json", t)) for k, t in parts]}


def part_entry(key: str, e: dict) -> list:
    """The split-JSON part [key, asset, size(, stored)] of the Store entry `e`."""
    return [key, e["asset"], e["size"], *([e["stored"]] if "stored" in e else [])]


def site_store(site: Path, encoding: str = DEFAULT_ENCODING) -> Store:
    """The asset store of a site, writing its assets in `encoding`."""
    return Store(site, encoding)


def read_asset(site: Path, asset: str) -> bytes:
    """The decoded bytes of a site asset (`assets/...`; by the name's suffix, compress.decode)."""
    return compress.decode(asset, (Path(site) / asset).read_bytes())


def entry_assets(e: dict) -> list[str]:
    return [p[1] for p in e["parts"]] if "parts" in e else [e["asset"]]


# ---------------------------------------------------------------- data
def open_data(cfg: Config, region: str | None = None):
    """Catalog (fetching from the CDN of `region`), the master dir of `region` and PlayerData from the settings (as
    the command line opens them; `region` None: [catalog] region)."""
    from ..source.session import Session
    from ..unity.player import PlayerData

    session = Session(cfg)
    return session.catalog(region=region), session.master_dir(region), PlayerData(session.apk(region))


def _lock_fetches(cat, lock) -> None:
    """Catalog downloads of this process under a lock shared by the workers (one writer per cache file); a file
    already in the cache is returned without the lock (Catalog.cached)."""
    for name in ("fetch", "fetch_raw"):
        f = getattr(cat, name)

        def locked(x, _f=f, _name=name):
            hit = cat.downloaded(x) if _name == "fetch" else cat.downloaded_raw(x)
            if hit is not None:
                return hit
            with lock:
                return _f(x)

        setattr(cat, name, locked)


# ---------------------------------------------------------------- regions
def site_regions(cfg: Config, regions=None, all_regions: bool = False) -> list[str]:
    """The regions of a build: `regions` (given order, duplicates dropped), every configured region
    (`all_regions`, config order), else the one [catalog] region. Each needs its `[servers.<region>]` table."""
    if all_regions:
        names = cfg.regions()
        if not names:
            raise ConfigError(
                "no region configured: add a [servers.<region>] table with `cdn` to the config file "
                "(or set NNNOTES_SERVERS_<REGION>_CDN)"
            )
    else:
        names = list(dict.fromkeys(regions or [])) or [cfg.region()]
    known = set(cfg.regions())
    for r in names:
        if r not in known:
            raise cfg.missing(f"servers.{r}", "cdn")
    return names


def region_masters(cfg: Config, regions: list[str]) -> dict[str, Path]:
    """{region: decoded master dir} (Session.master_dir). With more than one region the --master flag is refused and
    at most one region may use [paths] master (the others need their own `[servers.<region>] master`)."""
    from ..source.session import Session

    if len(regions) > 1 and cfg.origin("paths", "master") == "flag":
        raise ConfigError(
            "--master names one directory: give each region's master data as `master` in its "
            "[servers.<region>] table of the config file (NNNOTES_SERVERS_<REGION>_MASTER)"
        )
    shared = [r for r in regions if cfg.master(r) == ("paths", "master")]
    if len(shared) > 1:
        raise cfg.missing(f"servers.{shared[1]}", "master")
    return {r: Session(cfg).master_dir(r) for r in regions}


def region_meta(cfg: Config, regions: list[str]) -> list[dict]:
    """The index records of `regions`: id, name (`[servers.<region>] name`, else the id) and, when configured, the
    client languages (`[servers.<region>] languages`)."""
    out = []
    for r in regions:
        rec: dict = {"id": r, "name": cfg.get(f"servers.{r}", "name") or r}
        langs = cfg.get_list(f"servers.{r}", "languages")
        if langs:
            rec["languages"] = langs
        out.append(rec)
    return out


def merge_regions(old, new) -> list[str]:
    """`old` (a manifest's regions, may be None) followed by the regions of `new` it lacks."""
    out = list(old or [])
    return out + [r for r in new if r not in out]


# ---------------------------------------------------------------- manifests and live directories
def chart_manifests(site: Path) -> list[Path]:
    """Every chart manifest: charts/*.json, then the region manifests charts/<region>/*.json."""
    d = Path(site) / "charts"
    return [*sorted(d.glob("*.json")), *sorted(d.glob("*/*.json"))]


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
            rec["variants"] = [
                v for v in rec["variants"] if v["platform"] == SHADER_PLATFORM and v["type"] == SHADER_TYPE
            ]
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
            continue  # other platforms / GLES 3.1 programs
        if sdir is not None and f.suffix not in (".glsl", ".json"):
            continue  # Vulkan / SPIR-V containers
        if f.suffix in TEXT_EXT:
            text[rel] = f.read_text(encoding="utf-8")
        elif f.suffix in BINARY_EXT:
            binary[rel] = f.read_bytes()
        else:
            raise ValueError(f"unexpected file in the live directory: {rel}")
    return text, binary


# ---------------------------------------------------------------- build processes
def configure_caches(job: dict) -> None:
    """The process caches of a build: the disk layer under the build's temporary root (shared by its processes)."""
    if job.get("cache"):
        cache.configure(directory=job["cache"])


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- player
def check_player(player_dir) -> Path:
    """The ournotes-player checkout or installed package: its read-set script, built bundle and page must exist."""
    if player_dir is None:
        raise ConfigError(
            "the site needs the player: give --player <ournotes-player checkout or package> "
            "(or `player` in the [paths] table of the config file, NNNOTES_PATHS_PLAYER)"
        )
    d = Path(player_dir)
    missing = [
        rel for rel in (READ_SET_SCRIPT, *PLAYER_BUNDLES, f"{PLAYER_PAGE_DIR}/index.html") if not (d / rel).is_file()
    ]
    if missing:
        raise ConfigError(
            f"{d} is not a built ournotes-player (missing {', '.join(missing)}; a checkout needs its build first)"
        )
    return d.resolve()


def player_bundles(player_dir: Path, rels) -> tuple[dict[str, bytes], str]:
    """The bundles `rels` of the player and their source maps ({file name: bytes}), and the bundles' short hash."""
    bundles = {}
    for rel in rels:
        bundles[Path(rel).name] = (player_dir / rel).read_bytes()
        if (player_dir / f"{rel}.map").is_file():
            bundles[Path(rel).name + ".map"] = (player_dir / f"{rel}.map").read_bytes()
    return bundles, hashlib.sha256(b"".join(bundles[Path(rel).name] for rel in rels)).hexdigest()[:16]


def page_files(page_dir: Path, imports: dict[str, str], version: str, names: str = "PAGE_IMPORTS") -> dict[str, bytes]:
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
    so a story page without its bundles is a ConfigError instead of being left out). The songs page and all its
    local dependencies are copied into SITE/songs when the checkout supplies it."""
    player_dir = check_player(player_dir)
    bundles, version = player_bundles(player_dir, PLAYER_BUNDLES)
    page = page_files(player_dir / PLAYER_PAGE_DIR, PAGE_IMPORTS, version)
    files = {**page, **bundles}
    songs = {}
    if (player_dir / SONGS_PAGE_DIR / "index.html").is_file():
        songs = page_files(player_dir / SONGS_PAGE_DIR, {}, version, "SONGS_PAGE_IMPORTS")
        files.update({f"{SONGS_PAGE_SITE_DIR}/{r}": d for r, d in songs.items()})
    live2d = {}
    if (player_dir / LIVE2D_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in LIVE2D_BUNDLES if not (player_dir / rel).is_file()]
        if missing:
            raise ConfigError(
                f"{player_dir} has the Live2D page but not {', '.join(missing)} (a checkout needs its build first)"
            )
        live2d_bundles, live2d_version = player_bundles(player_dir, LIVE2D_BUNDLES)
        live2d = page_files(player_dir / LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS, live2d_version, "LIVE2D_PAGE_IMPORTS")
        files.update({f"{LIVE2D_PAGE_SITE_DIR}/{r}": d for r, d in {**live2d, **live2d_bundles}.items()})
    story_page = {}
    if (player_dir / STORY_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in STORY_BUNDLES if not (player_dir / rel).is_file()]
        if missing and stories:
            raise ConfigError(
                f"{player_dir} has the story page but not {', '.join(missing)} (a checkout needs its build first)"
            )
        if not missing:
            story_bundles, story_version = player_bundles(player_dir, STORY_BUNDLES)
            story_page = page_files(
                player_dir / STORY_PAGE_DIR, STORY_PAGE_IMPORTS, story_version, "STORY_PAGE_IMPORTS"
            )
            files.update({f"{STORY_PAGE_SITE_DIR}/{r}": d for r, d in {**story_page, **story_bundles}.items()})
    for rel, data in files.items():
        if rel.split("/", 1)[0] in SITE_DATA:
            raise RuntimeError(f"player page file {rel} would overwrite the site data")
        dst = Path(site) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    out = {
        "playerVersion": version,
        "playerBytes": sum(map(len, bundles.values())),
        "pageFiles": len(page),
        "live2dPageFiles": len(live2d),
    }
    if story_page:
        out["storyPageFiles"] = len(story_page)
    if songs:
        out["songsPageFiles"] = len(songs)
    return out


# ---------------------------------------------------------------- stored manifests
def entry_text(site: Path, e: dict) -> bytes:
    """The file text of a manifest entry (a split JSON file rejoined)."""
    if "parts" in e:
        return join_parts([(q[0], read_asset(site, q[1])) for q in e["parts"]])
    return read_asset(site, e["asset"])


def reingest_json(site: Path, encoding: str = DEFAULT_ENCODING) -> dict:
    """Every JSON file of every chart and model stored again through text_asset (after a change of the stored-text
    rules), in `encoding`; the manifests follow, unreferenced assets go with write_index."""
    store, changed, memo = site_store(site, encoding), 0, {}
    for mf in [*chart_manifests(site), *sorted((site / MODELS_DIR).glob("*.json"))]:
        man = json.loads(mf.read_text(encoding="utf-8"))
        files = man["files"]
        for p, e in files.items():
            if not p.endswith(".json"):
                continue
            key = (p, json.dumps(e, sort_keys=True))  # charts share most files: each stored once
            if key not in memo:
                memo[key] = store.put_file(p, text_asset(p, entry_text(site, e).decode("utf-8")))
            ne = memo[key]
            if ne != e:
                files[p] = ne
                changed += 1
        mf.write_bytes(_dump(man))
    return {"changedEntries": changed}
