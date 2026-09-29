#!/usr/bin/env python3
"""The music data CI steps (.github/workflows/music-data.yml): `nnnotes music-data` of moenotes-masterdata-sync's
decoded master data, checked by quality gates and published into the story site's bucket under $MUSIC_DATA_S3_PREFIX
(music-data.json, jackets/, archive/, build.json) for the chart data page of ournotes-player (examples/songs).

    plan                  the inputs of a build (the master data snapshot of $MASTERDATA_REGION in index.json, the
                          deck commit rust/Cargo.lock pins, the last nnnotes commit of src/, rust/ and pyproject.toml,
                          RECIPE) against those of the published build.json; GitHub output `build`: true when they
                          differ, nothing is published or $FORCE is true
    master OUT            every file of the snapshot of $MASTERDATA_REGION into OUT (SHA-256 checked against
                          index.json, MasterManifest.json included) and its index entry as OUT.snapshot.json
    build OUT             `nnnotes music-data --decoded-master --jackets OUT/jackets -o OUT/music-data.json` ([paths]
                          master: the master step's OUT), its printed summary in OUT/music-data.summary.json
    check OUT MASTER PAGE the quality gates (.github/MUSIC_DATA.md) on OUT/music-data.json, with the published file
                          as the baseline and PAGE the chart data page's modules (examples/songs) for the smoke test;
                          the report in OUT/check.json, and OUT/build.json (the build marker) when every gate passed;
                          exits 1 when one failed
    publish OUT [--dry-run]
                          the jackets the bucket lacks or has at another size (every one with $FORCE), the file's
                          archive copy, then music-data.json, build.json last; each read back and its SHA-256 checked.
                          Only a file whose check.json passed; never deletes. A dry run unless $MUSIC_DATA_PUBLISH is
                          `true` (the publishing switch, off by default): it lists what it would upload

Bucket: story_site.Bucket ($STORY_S3_ENDPOINT, $STORY_S3_BUCKET, credentials $STORY_S3_ACCESS_KEY /
$STORY_S3_SECRET_KEY) with the key prefix $MUSIC_DATA_S3_PREFIX (default music-data). The published files are read
over plain HTTP, as the page reads them (the bucket serves public read).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import story_site                                   # noqa: E402  (the bucket, HTTP and master data helpers)
from story_site import env, get, output, summary   # noqa: E402

FORMAT = "nnnotes.music-data/1"
BUILD_FORMAT = "moenotes.music-data-build/1"
# This script's own version of a build: bump it when what it builds or publishes changes, so that the next run builds
# although the master data, the deck model and nnnotes are the same.
RECIPE = 2
FILE, MARKER, JACKETS, ARCHIVE = "music-data.json", "build.json", "jackets/", "archive/"
MANIFEST = "MasterManifest.json"
SOURCE_PATHS = ("src", "rust", "pyproject.toml")    # nnnotes' code: the commit that last changed one of them
SCHEMA = Path("docs/schema/music-data.schema.json")
SMOKE = Path(__file__).resolve().parent / "music_data_smoke.mjs"
ARCHIVE_CACHE = story_site.ASSET_CACHE              # archive/<version>/<sha256>.json: content-addressed
FILE_CACHE = "no-cache"                             # music-data.json and build.json change in place
JACKET_CACHE = "public, max-age=86400"
SNAPSHOT_KEYS = ("version", "resource_version", "resource_hash", "client_version", "verified_at", "manifest_sha256", "table_count")

# the gates' bounds (MUSIC_DATA.md)
SIZE_RATIO = (0.8, 2.0)                             # against the published file
FILE_GZIP_MAX = 2_000_000                           # the file gzipped, the download (0.37 MB before the aptitude)
APTITUDE_GZIP_MAX = 1_200_000                       # the Gekisou skill aptitude gzipped (about 0.4 MB expected)
BGM_MS = (30_000, 600_000)                          # a song's BGM length
BGM_CUE_SLACK_MS = 1000                             # |durationMs - lengthMs|
BGM_TAIL_MS = 60_000                                # BGM after the last note: more is reported
RANKS = 5
LUCK_MISSION = 2                                    # a range of it draws lots: its chart has several seeds
JUST_MISSION = 3                                    # a range of it judges Just
MISSIONS = (1, 2, 3, 4)                             # a Gekisou skill's: combo, luck, Just, every one
PLAIN_KIND = (2000, 5000)                           # the page's plain kind (ranking.js plainKind): effect type, ms
BAND_CONDITION = 5000                               # the skill condition on the paired member (its band)
APTITUDE_SLACK = 1e-6                               # relative: the aptitude's identities on means of integers
DIFFICULTIES = ("easy", "normal", "hard", "expert")
SONG_TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterText", "MasterBand", "MasterCharacter", "MasterTag",
               "MasterLiveMusicCategory", "MasterSound", "MasterSoundCueSheet", "MasterLiveScoreRank")
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
LISTED = 20                                         # failures and warnings listed per gate


def fail(message: str):
    sys.exit(f"music_data: {message}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- bucket
def s3_prefix() -> str:
    p = (os.environ.get("MUSIC_DATA_S3_PREFIX") or "music-data").strip("/")
    return f"{p}/" if p else ""


def bucket() -> "story_site.Bucket":
    b = story_site.Bucket()
    b.prefix = s3_prefix()
    return b


def public_url(key: str) -> str:
    return f"{env('STORY_S3_ENDPOINT').rstrip('/')}/{env('STORY_S3_BUCKET')}/{s3_prefix()}{key}"


def published(key: str) -> bytes | None:
    """A published file (plain HTTP), None when the bucket has none."""
    try:
        return get(public_url(key), timeout=300)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def exists(key: str) -> bool:
    request = urllib.request.Request(public_url(key), method="HEAD",
                                     headers={"User-Agent": "moenotes-music-data (GitHub Actions)",
                                              "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=60):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


# ---------------------------------------------------------------- the inputs of a build
def deck_commit(root: Path = Path(".")) -> str:
    """The ournotes-deck commit nnnotes builds its deck model with (rust/Cargo.lock)."""
    lock = root / "rust" / "Cargo.lock"
    if not lock.is_file():
        fail("no rust/Cargo.lock: this nnnotes has no music-data command with the deck model (sync the fork with "
             "upstream, .github/MUSIC_DATA.md)")
    for block in lock.read_text(encoding="utf-8").split("[[package]]"):
        if re.search(r'^name = "ournotes-deck"$', block, re.M):
            m = re.search(r'^source = "git\+[^"#]*#([0-9a-f]{40})"$', block, re.M)
            if m:
                return m.group(1)
    fail("rust/Cargo.lock has no ournotes-deck git commit")


def nnnotes_commit(root: Path = Path(".")) -> str:
    """The commit that last changed nnnotes' code (SOURCE_PATHS); the checkout needs its history."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        fail("the checkout is shallow: the nnnotes commit needs the history (actions/checkout fetch-depth: 0)")
    commit = git("log", "-1", "--format=%H", "--", *SOURCE_PATHS).stdout.strip()
    if not COMMIT.fullmatch(commit):
        fail("no commit changed src/, rust/ or pyproject.toml")
    return commit


def require_decoded_master(root: Path = Path(".")) -> None:
    cli = root / "src" / "nnnotes" / "cli.py"
    if "--decoded-master" not in cli.read_text(encoding="utf-8"):
        fail("this nnnotes has no `music-data --decoded-master` (MetaSekaiLab/nnnotes#6): sync the fork with "
             "upstream (.github/MUSIC_DATA.md)")


def inputs(entry: dict, root: Path = Path(".")) -> dict:
    """What a build is made of: the master data snapshot, the deck model, nnnotes and this script."""
    return {"masterRegion": env("MASTERDATA_REGION"), "masterVersion": entry.get("version"),
            "resourceVersion": entry.get("resource_version"), "clientVersion": entry.get("client_version"),
            "resourceHash": entry.get("resource_hash"),
            "deckCommit": deck_commit(root), "nnnotesCommit": nnnotes_commit(root), "recipe": RECIPE}


def short(v) -> str:
    return str(v)[:12] if v is not None else "?"


# ---------------------------------------------------------------- plan
def cmd_plan() -> None:
    _, region = story_site.master_index()
    require_decoded_master()
    now = inputs(region.get("entry") or {})
    raw = published(MARKER)
    marker = json.loads(raw) if raw else None
    have = marker.get("inputs") if isinstance(marker, dict) else None
    changed = [k for k in now if not isinstance(have, dict) or have.get(k) != now[k]]
    if have and not exists(FILE):
        changed.append("music-data.json (missing)")
    force = os.environ.get("FORCE") == "true"
    build = force or bool(changed)
    summary(f"### Music data\n\n- master: {now['masterVersion']} of {now['masterRegion']} (resource "
            f"{now['resourceVersion']}, client {now['clientVersion']}); deck {short(now['deckCommit'])}; nnnotes "
            f"{short(now['nnnotesCommit'])}; recipe {RECIPE}\n- published: "
            + (f"master {have.get('masterVersion')}, deck {short(have.get('deckCommit'))}, nnnotes "
               f"{short(have.get('nnnotesCommit'))}, built {marker.get('builtAt')}" if isinstance(have, dict)
               else "nothing")
            + "\n- this run: " + ("builds" + (" (force)" if force else "") + (f", changed: {', '.join(changed)}"
                                                                              if changed and have else "")
                                  if build else "nothing changed, nothing to build"))
    summary(f"- publishing: {'on' if publishing() else 'off (MUSIC_DATA_PUBLISH is not `true`: a dry run)'}")
    output("build", "true" if build else "false")


# ---------------------------------------------------------------- master data
def snapshot_file(master: Path) -> Path:
    return master.parent / f"{master.name}.snapshot.json"


def cmd_master(out: str) -> None:
    url, region = story_site.master_index()
    files = region["files"]
    entry = region.get("entry") or {}
    if MANIFEST not in files:
        fail(f"index.json lists no {MANIFEST} for {env('MASTERDATA_REGION')}")
    if entry.get("manifest_sha256") and entry["manifest_sha256"] != files[MANIFEST]:
        fail(f"index.json: the entry's manifest_sha256 is not the SHA-256 of its {MANIFEST}")
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    story_site.parallel(lambda item: story_site.fetch_table(url, item[0], item[1], d / item[0]), files.items(), 8)
    version = json.loads((d / MANIFEST).read_bytes()).get("version")
    if entry.get("version") is not None and str(version) != str(entry["version"]):
        fail(f"{MANIFEST} has version {version}, index.json {entry['version']} (the service changed snapshots? "
             f"run again)")
    snapshot = {"region": env("MASTERDATA_REGION"), "entry": {k: entry.get(k) for k in SNAPSHOT_KEYS},
                "files": files}
    snapshot_file(d).write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    print(f"master data: {version}, {len(files)} files into {d}")


# ---------------------------------------------------------------- build
def cmd_build(out: str) -> None:
    story_site.configure_region()
    o = Path(out).resolve()
    o.mkdir(parents=True, exist_ok=True)
    nnnotes = [sys.executable, "-m", "nnnotes"]
    usage = subprocess.run(nnnotes + ["music-data", "--help"], capture_output=True, text=True).stdout
    if "--decoded-master" not in usage:
        fail("the installed nnnotes has no `music-data --decoded-master` (sync the fork with upstream)")
    cmd = nnnotes + ["music-data", "--decoded-master", "--jackets", str(o / "jackets"), "-o", str(o / FILE)]
    print("+ " + " ".join(cmd[1:]), flush=True)
    with open(o / "music-data.summary.json", "wb") as f:
        status = subprocess.run(cmd, stdout=f).returncode
    if status:
        fail(f"nnnotes music-data exited with {status}")
    r = json.loads((o / "music-data.summary.json").read_text(encoding="utf-8"))
    summary(f"- built: {r.get('songs')} songs, {r.get('charts')} charts, {r.get('jackets')} jackets, deck "
            f"{short(r.get('deck'))}, {r.get('bytes')} bytes, sha256 {short(r.get('sha256'))}")


# ---------------------------------------------------------------- the gates
@dataclass
class Context:
    """What the gates check the file against; None: the gate (or that part of it) is skipped (the self-test)."""
    region: str | None = None               # [catalog] region: provenance.region
    language: str | None = None             # [catalog] language: a song title should have it
    master: Path | None = None              # the decoded master data read (MasterManifest.json, <Table>.json)
    snapshot: dict | None = None            # its index entry and files (the master step's OUT.snapshot.json)
    deck_commit: str | None = None
    nnnotes_version: str | None = None
    jackets: Path | None = None
    schema: Path | None = None
    published: bytes | None = None          # the published music-data.json (None: nothing is published)
    page: Path | None = None                # examples/songs of ournotes-player
    file: Path | None = None                # the file on disk, for the smoke test


class Gate:
    def __init__(self):
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.note = ""

    def fail(self, message: str):
        self.failures.append(message)

    def warn(self, message: str):
        self.warnings.append(message)


def charts_of(doc: dict):
    for song in doc.get("songs") or []:
        for chart in song.get("charts") or []:
            yield song, chart


def where(song: dict, chart: dict) -> str:
    return f"chart {chart.get('scoreId')} ({song.get('id')} {chart.get('difficulty')})"


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)) and math.isfinite(v)


def within(c) -> bool:
    return (isinstance(c, dict) and is_int(c.get("exact")) and is_num(c.get("predicted")) and is_num(c.get("bound"))
            and abs(c["exact"] - c["predicted"]) <= c["bound"])


def rank_bonus(range_score: int, percent: int) -> int:
    """trunc(rangeScore * percent / 100): a range's rank bonus."""
    q = abs(range_score * percent) // 100
    return q if range_score * percent >= 0 else -q


def plain_kind(doc: dict):
    """The id of the page's plain score-up kind in deck.kinds (ranking.js plainKind): effect type 2000 on the whole
    deck for 5 s, without targets, conditions or limits; None for none."""
    for k in ((doc.get("deck") or {}).get("kinds")) or []:
        if (isinstance(k, dict) and k.get("effectType") == PLAIN_KIND[0] and not k.get("skillTargetIds")
                and not any(k.get(x) for x in ("skillConditionGroup", "skillReleaseConditionGroup",
                                               "effectLimitCount", "effectExecuteLimitCount"))
                and (PLAIN_KIND[1] if k.get("durationMs") is None else k["durationMs"]) == PLAIN_KIND[1]):
            return k.get("id")
    return None


def luck_chart(deck: dict) -> bool:
    return any(isinstance(r, dict) and r.get("mission") == LUCK_MISSION for r in deck.get("ranges") or [])


def gate_schema(doc, ctx: Context, g: Gate):
    if ctx.schema is None:
        g.note = "skipped"
        return
    if not ctx.schema.is_file():
        g.fail(f"no JSON Schema {ctx.schema}")
        return
    import jsonschema
    schema = json.loads(ctx.schema.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        g.fail(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:160]}")
    g.note = ctx.schema.as_posix()


def gate_provenance(doc, ctx: Context, g: Gate):
    if doc.get("format") != FORMAT:
        g.fail(f"format {doc.get('format')!r}, expected {FORMAT}")
    p = doc.get("provenance") or {}
    if ctx.region is not None and p.get("region") != ctx.region:
        g.fail(f"region {p.get('region')!r}, expected {ctx.region!r}")
    m = p.get("master") or {}
    if m.get("source") != "api":
        g.fail(f"master.source {m.get('source')!r}, expected 'api'")
    tables = m.get("tables") or {}
    missing = [t for t in SONG_TABLES if t not in tables]
    if missing:
        g.fail(f"master.tables lacks {', '.join(missing)}")
    if doc.get("deck") is not None and len(tables) <= len(SONG_TABLES):
        g.fail("master.tables has only the song tables, but the file has deck statistics")
    if ctx.snapshot is not None:
        v = ctx.snapshot["entry"].get("version")
        if m.get("version") != v:
            g.fail(f"master.version {m.get('version')!r}, the snapshot's {v!r}")
        if ctx.snapshot.get("region") == "jp":
            for expected, actual in (("resource_version", "resourceVersion"), ("resource_hash", "resourceHash")):
                if (p.get("catalog") or {}).get(actual) != ctx.snapshot["entry"].get(expected):
                    g.fail(f"JP catalog {actual} differs from the master snapshot")
    if ctx.master is not None:
        manifest = json.loads((ctx.master / MANIFEST).read_bytes())
        if m.get("version") != manifest.get("version"):
            g.fail(f"master.version {m.get('version')!r}, {MANIFEST}'s {manifest.get('version')!r}")
        listed = {f.get("name"): str(f.get("hash") or "").lower() for f in manifest.get("files") or []}
        files = (ctx.snapshot or {}).get("files") or {}
        for t, v in sorted(tables.items()):
            if (v or {}).get("sha256") != listed.get(f"{t}.bin"):
                g.fail(f"master.tables.{t}.sha256 is not {MANIFEST}'s {t}.bin")
            decoded = ctx.master / f"{t}.json"
            if not decoded.is_file():
                g.fail(f"no decoded table {t}.json")
            elif files and sha256(decoded.read_bytes()) != files.get(f"{t}.json"):
                g.fail(f"{t}.json read is not the one index.json lists")
    deck = p.get("deck")
    if doc.get("deck") is not None and not isinstance(deck, dict):
        g.fail("provenance.deck is null, but the file has deck statistics")
    if isinstance(deck, dict):
        if deck.get("name") != "ournotes-deck" or not COMMIT.fullmatch(str(deck.get("commit"))):
            g.fail("provenance.deck names no ournotes-deck commit")
        elif ctx.deck_commit is not None and deck["commit"] != ctx.deck_commit:
            g.fail(f"deck commit {deck['commit'][:12]}, rust/Cargo.lock pins {ctx.deck_commit[:12]}")
    ex = p.get("exporter") or {}
    if ex.get("name") != "nnnotes":
        g.fail(f"exporter {ex.get('name')!r}")
    elif ctx.nnnotes_version is not None and ex.get("version") != ctx.nnnotes_version:
        g.fail(f"exporter version {ex.get('version')!r}, installed nnnotes {ctx.nnnotes_version!r}")
    client = p.get("client") or {}
    if not isinstance(client.get("versionName"), str) or not is_int(client.get("versionCode")):
        g.fail("provenance.client has no APK version (was the APK read?)")
    elif ctx.snapshot is not None and ctx.snapshot["entry"].get("client_version") not in (None,
                                                                                          client["versionName"]):
        g.warn(f"the APK is {client['versionName']}, the snapshot names client "
               f"{ctx.snapshot['entry']['client_version']}")
    if not SHA256.fullmatch(str((p.get("catalog") or {}).get("sha256"))):
        g.fail("provenance.catalog has no SHA-256")
    g.note = f"master {m.get('version')}, deck {short((deck or {}).get('commit'))}, nnnotes {ex.get('version')}"


def baseline(ctx: Context, g: Gate):
    if ctx.published is None:
        g.note = "skipped: nothing is published yet"
        return None
    try:
        return json.loads(ctx.published)
    except ValueError:
        g.warn("the published music-data.json is not JSON: skipped")
        return None


def gate_counts(doc, ctx: Context, g: Gate):
    old = baseline(ctx, g)
    if old is None:
        return
    songs = {s.get("id") for s in doc.get("songs") or []}
    charts = {c.get("scoreId") for _, c in charts_of(doc)}
    old_songs = {s.get("id") for s in old.get("songs") or []}
    old_charts = {c.get("scoreId") for _, c in charts_of(old)}
    if len(songs) < len(old_songs):
        g.fail(f"{len(songs)} songs, the published file has {len(old_songs)}")
    if len(charts) < len(old_charts):
        g.fail(f"{len(charts)} charts, the published file has {len(old_charts)}")
    if old_songs - songs:
        g.warn(f"songs no longer in the file: {', '.join(map(str, sorted(old_songs - songs)))}")
    if old_charts - charts:
        g.warn(f"charts no longer in the file: {', '.join(map(str, sorted(old_charts - charts)))}")
    g.note = f"songs {len(old_songs)} -> {len(songs)}, charts {len(old_charts)} -> {len(charts)}"


def gate_size(doc, ctx: Context, g: Gate, raw: bytes):
    if ctx.published is None:
        g.note = "skipped: nothing is published yet"
        return
    ratio = len(raw) / max(1, len(ctx.published))
    if not SIZE_RATIO[0] <= ratio <= SIZE_RATIO[1]:
        g.fail(f"{len(raw)} bytes, {ratio:.2f} times the published {len(ctx.published)} (bounds {SIZE_RATIO[0]} to "
               f"{SIZE_RATIO[1]})")
    g.note = f"{len(ctx.published)} -> {len(raw)} bytes ({ratio:.2f})"


def weights_shape(w, kinds: int, positions: int, nullable: bool) -> bool:
    return isinstance(w, list) and len(w) == kinds and all(
        (nullable and k is None) or (isinstance(k, list) and len(k) == positions and all(map(is_num, k))) for k in w)


def gate_deck(doc, ctx: Context, g: Gate):
    """The deck statistics. Seeds (deck.model.seeds): the one seed 0 on a chart without a luck range, else two or more
    seeds, the same on every luck chart (their number is the file's); every range's rankBonus the rank 1 bonus and
    its luckPoints (the range's luck points without skills)."""
    deck = doc.get("deck")
    if not isinstance(deck, dict):
        g.fail("deck is null: no deck statistics (made with --no-deck?)")
        return
    kinds = len(deck.get("kinds") or [])
    if not kinds:
        g.fail("deck.kinds is empty")
    power = (deck.get("model") or {}).get("power")
    if not (is_num(power) and power > 0):
        g.fail(f"deck.model.power {power!r}")
    n = unplayable = seeds = 0
    luck_seeds = None
    for song, chart in charts_of(doc):
        n += 1
        w, d = where(song, chart), chart.get("deck")
        if not isinstance(d, dict):
            g.fail(f"{w}: no deck statistics")
            continue
        positions, events = d.get("positions"), d.get("events") or []
        if len(events) != len(chart.get("skillEventsMs") or []):
            g.fail(f"{w}: {len(events)} skill events, the chart has {len(chart.get('skillEventsMs') or [])}")
        if not is_int(positions) or (events and positions != max(e[0] for e in events) + 1):
            g.fail(f"{w}: positions {positions!r} do not match the events")
            continue
        if d.get("unplayable"):
            unplayable += 1
            g.warn(f"{w}: unplayable with Gekisou on ({d['unplayable']})")
            if d.get("seeds"):
                g.fail(f"{w}: unplayable, but has Gekisou on seeds")
        elif not d.get("seeds"):
            g.fail(f"{w}: no seeds")
        values = [s.get("seed") for s in d.get("seeds") or []]
        if values and not luck_chart(d):
            if len(values) != 1 or not is_int(values[0]) or values[0] != 0:
                g.fail(f"{w}: seeds {values[:4]!r} without a luck range, expected the one seed 0")
        elif values:
            if not all(map(is_int, values)) or len(values) < 2 or len(set(values)) != len(values):
                g.fail(f"{w}: {len(values)} seeds on a luck chart, expected two or more different int seeds")
            elif luck_seeds is None:
                luck_seeds = values
            elif values != luck_seeds:
                g.fail(f"{w}: its {len(values)} seeds are not the {len(luck_seeds)} of the first luck chart")
        for seed in d.get("seeds") or []:
            seeds += 1
            s = f"{w} seed {seed.get('seed')}"
            if not is_int(seed.get("score")):
                g.fail(f"{s}: score {seed.get('score')!r}")
            if not weights_shape(seed.get("weights"), kinds, positions, nullable=False):
                g.fail(f"{s}: weights are not [kind][position] numbers")
            if len(seed.get("ranges") or []) != len(d.get("ranges") or []):
                g.fail(f"{s}: {len(seed.get('ranges') or [])} range results for {len(d.get('ranges') or [])} ranges")
            for i, (r, rr) in enumerate(zip(seed.get("ranges") or [], d.get("ranges") or [], strict=False)):
                r, rr = (r if isinstance(r, dict) else {}), (rr if isinstance(rr, dict) else {})
                if not (is_int(r.get("rangeScore")) and is_int(r.get("rankBonus"))):
                    g.fail(f"{s} range {i}: rangeScore {r.get('rangeScore')!r}, rankBonus {r.get('rankBonus')!r}")
                elif is_int(rr.get("rankBonusPercent")) and r["rankBonus"] != rank_bonus(r["rangeScore"],
                                                                                         rr["rankBonusPercent"]):
                    g.fail(f"{s} range {i}: rankBonus {r['rankBonus']} is not trunc({r['rangeScore']} * "
                           f"{rr['rankBonusPercent']} / 100)")
                if not is_int(r.get("luckPoints")):
                    g.fail(f"{s} range {i}: luckPoints {'missing' if 'luckPoints' not in r else 'not an int'}")
            if not within(seed.get("check")):
                g.fail(f"{s}: the check deck is not within its bound")
    g.note = (f"{n} charts, {kinds} kinds, {seeds} seeds, {unplayable} unplayable, "
              f"{len(luck_seeds or [])} seeds per luck chart")


def gate_scenarios(doc, ctx: Context, g: Gate):
    """The play scenario fields (Gekisou off, every rank, the Perfect play): offSeeds exactly one, every range's
    rankBonusPercents five ints, every seed scorePerfect, rangeWeights and rankCheck, every seed range
    rangeScorePerfect. A null rangeWeights, a null kind in it or in offSeeds' weights is a warning (none in TW)."""
    kinds = len(((doc.get("deck") or {}).get("kinds")) or [])
    null_rw = null_kind = null_off = 0
    for song, chart in charts_of(doc):
        w, d = where(song, chart), chart.get("deck")
        if not isinstance(d, dict):
            g.fail(f"{w}: no deck statistics")
            continue
        positions, ranges = d.get("positions"), d.get("ranges") or []
        off = d.get("offSeeds")
        if not isinstance(off, list) or len(off) != 1:
            g.fail(f"{w}: offSeeds {'missing' if off is None else f'has {len(off)} entries'}, expected exactly one")
        else:
            o = off[0]
            if o.get("seed") != 0 or not is_int(o.get("score")):
                g.fail(f"{w}: Gekisou off seed {o.get('seed')!r} score {o.get('score')!r}")
            if not weights_shape(o.get("weights"), kinds, positions, nullable=True):
                g.fail(f"{w}: Gekisou off weights are not [kind][position] numbers")
            else:
                null_off += sum(k is None for k in o["weights"])
            if not within(o.get("check")):
                g.fail(f"{w}: the Gekisou off check deck is not within its bound")
        for i, r in enumerate(ranges):
            p = r.get("rankBonusPercents")
            if not (isinstance(p, list) and len(p) == RANKS and all(map(is_int, p))):
                g.fail(f"{w} range {i}: rankBonusPercents {'missing' if p is None else 'not five ints'}")
            elif p[0] != r.get("rankBonusPercent"):
                g.fail(f"{w} range {i}: rankBonusPercents[0] {p[0]} is not rankBonusPercent "
                       f"{r.get('rankBonusPercent')}")
        for seed in d.get("seeds") or []:
            s = f"{w} seed {seed.get('seed')}"
            absent = [k for k in ("scorePerfect", "rangeWeights", "rankCheck") if k not in seed]
            if absent:
                g.fail(f"{s}: no {', '.join(absent)}")
                continue
            if not is_int(seed["scorePerfect"]):
                g.fail(f"{s}: scorePerfect {seed['scorePerfect']!r}")
            for i, r in enumerate(seed.get("ranges") or []):
                if not is_int(r.get("rangeScorePerfect")):
                    why = "missing" if "rangeScorePerfect" not in r else "not an int"
                    g.fail(f"{s} range {i}: rangeScorePerfect {why}")
            rw = seed["rangeWeights"]
            if rw is None:
                null_rw += 1
            elif not (isinstance(rw, list) and len(rw) == kinds and all(
                    k is None or (isinstance(k, list) and len(k) == positions and all(
                        isinstance(x, list) and len(x) == len(ranges) and all(map(is_num, x)) for x in k))
                    for k in rw)):
                g.fail(f"{s}: rangeWeights are not [kind][position][range] numbers")
            else:
                null_kind += sum(k is None for k in rw)
            rc = seed["rankCheck"]
            if rc is not None:
                if len(rc.get("ranks") or []) != len(ranges) or not all(
                        is_int(x) and 1 <= x <= RANKS for x in rc.get("ranks") or []):
                    g.fail(f"{s}: rankCheck ranks are not one rank per range")
                elif not within(rc):
                    g.fail(f"{s}: the rank check deck is not within its bound")
    if null_rw:
        g.warn(f"{null_rw} seeds have rangeWeights null (overlapping ranges: no rank scenarios on those charts)")
    if null_kind:
        g.warn(f"{null_kind} rangeWeights kinds are null (conditions on the confirmed rank)")
    if null_off:
        g.warn(f"{null_off} Gekisou off weight kinds are null (conditions on the Gekisou state)")
    g.note = "offSeeds, rankBonusPercents, scorePerfect, rangeWeights, rankCheck, rangeScorePerfect"


APTITUDE_KEYS = ("plainKind", "host", "seedRule", "shapes")
SEED_RULE_KEYS = ("deterministicTest", "batches", "relative", "baseline", "crossSeeds")
SHAPE_KEYS = ("id", "source", "mission", "bandCondition", "effects", "skills")
EFFECT_INTS = ("effectType", "triggerType", "effectValue", "maxEffectValue", "effectLimitCount",
               "effectExecuteLimitCount")
EFFECT_GROUPS = ("trigger", "condition", "release", "reset")
FACTOR_INTS = ("judgedNotes", "justNotes", "perfectNotes", "tailNotes", "comboAtStart")
VARIANT_KEYS = ("shape", "bandMatch", "deterministic", "seeds", "seTargetMet", "crossSeeds", "score", "scorePerfect",
                "tail", "tailPerfect", "converted", "ranges", "weights", "rangeWeights", "check")
VARIANT_PAIRS = ("score", "scorePerfect", "tail", "tailPerfect", "converted")
VARIANT_RANGE_PAIRS = ("rangeScore", "rankBonus", "rangeScorePerfect", "maxCombo", "justCount", "luckPoints")
APTITUDE_CHECK_KEYS = ("seed", "ranks", "deck", "exact", "predicted", "bound")


def pair(v) -> bool:
    """A [mean, standard error]: two finite numbers, the error not negative."""
    return isinstance(v, list) and len(v) == 2 and is_num(v[0]) and is_num(v[1]) and v[1] >= 0


def close(a: float, b: float) -> bool:
    return abs(a - b) <= APTITUDE_SLACK * max(1.0, abs(a), abs(b))


def gate_aptitude(doc, ctx: Context, g: Gate):
    """The charts' Gekisou skill aptitude: deck.gekisouAptitude (its plain kind the page's, the seed rule, shapes
    numbered from 0: source, mission, band condition, effect rows, skills); every chart's deck.gekisouAptitude, null
    exactly when the chart is unplayable with Gekisou on, has no Gekisou range or there is no shape; else factors per
    range and one variant per shape of the chart's missions (or mission 4) in shape order, a band condition shape's
    bandMatch true then false: every [mean, se] two finite numbers with se >= 0 (0 when deterministic), ranges per
    range, tail = score - the ranges' rangeScore and rankBonus, the seeds and the cross seeds by the seed rule, weights
    and rangeWeights where the plain kind and the chart's rank weights are, the check within its bound. Warnings:
    variants whose standard error missed the seed rule's target."""
    deck = doc.get("deck")
    if not isinstance(deck, dict):
        g.fail("deck is null: no Gekisou skill aptitude")
        return
    plain = plain_kind(doc)
    if not (isinstance((deck.get("model") or {}).get("gekisouAptitude"), str) and deck["model"]["gekisouAptitude"]):
        g.fail("deck.model.gekisouAptitude: no text")
    head = deck.get("gekisouAptitude")
    if not isinstance(head, dict):
        g.fail("deck.gekisouAptitude missing" if head is None else f"deck.gekisouAptitude {head!r}")
        head = {}
    elif [k for k in APTITUDE_KEYS if k not in head]:
        g.fail(f"deck.gekisouAptitude: no {', '.join(k for k in APTITUDE_KEYS if k not in head)}")
    if head:
        pk = head.get("plainKind", "missing")
        if not (pk is None or is_int(pk)) or pk != plain:
            g.fail(f"deck.gekisouAptitude.plainKind {pk!r}, the page's plain kind is {plain!r}")
        if not (isinstance(head.get("host"), str) and head["host"].strip()):
            g.fail("deck.gekisouAptitude.host: no text")
    rule = head.get("seedRule") if isinstance(head.get("seedRule"), dict) else {}
    batches = rule.get("batches")
    if head and not (all(k in rule for k in SEED_RULE_KEYS) and is_int(rule["deterministicTest"])
                     and rule["deterministicTest"] >= 1 and isinstance(batches, list) and batches
                     and all(map(is_int, batches)) and batches == sorted(set(batches)) and batches[0] >= 1
                     and is_num(rule["relative"]) and rule["relative"] >= 0 and is_num(rule["baseline"])
                     and rule["baseline"] >= 0 and is_int(rule["crossSeeds"]) and rule["crossSeeds"] >= 1):
        g.fail(f"deck.gekisouAptitude.seedRule {rule!r}"[:200])
        rule, batches = {}, None

    # the shapes
    shapes = head.get("shapes") if isinstance(head.get("shapes"), list) else []
    if head and not isinstance(head.get("shapes"), list):
        g.fail("deck.gekisouAptitude.shapes is not a list")
    if any(not isinstance(s, dict) or not is_int(s.get("id")) or s["id"] != i
           for i, s in enumerate(shapes)):
        g.fail("deck.gekisouAptitude.shapes: ids are not 0, 1, 2, ... in order")
    by_id: dict = {}
    for s in shapes:
        if not isinstance(s, dict):
            continue
        w = f"shape {s.get('id')}"
        absent = [k for k in SHAPE_KEYS if k not in s]
        if absent:
            g.fail(f"{w}: no {', '.join(absent)}")
            continue
        if is_int(s["id"]):
            by_id[s["id"]] = s
        if s["source"] not in ("member", "support"):
            g.fail(f"{w}: source {s['source']!r}")
        if not is_int(s["mission"]) or s["mission"] not in MISSIONS:
            g.fail(f"{w}: mission {s['mission']!r}")
        band = s["bandCondition"]
        if not isinstance(band, bool) or (band and s["source"] != "support"):
            g.fail(f"{w}: bandCondition {band!r} (a support skill's alone)")
        effects = s["effects"]
        fives = 0
        if not isinstance(effects, list):
            g.fail(f"{w}: effects are not a list")
            effects = []
        for i, e in enumerate(effects):
            if not isinstance(e, dict):
                g.fail(f"{w} effect {i}: not an object")
                continue
            bad = [k for k in EFFECT_INTS if not is_int(e.get(k))]
            if not is_num(e.get("activationTimeSecond")):
                bad.append("activationTimeSecond")
            if not (isinstance(e.get("skillTargetIds"), list) and all(map(is_int, e["skillTargetIds"]))):
                bad.append("skillTargetIds")
            for k in EFFECT_GROUPS:
                sets = e.get(k)
                if not (isinstance(sets, list) and all(isinstance(x, list) for x in sets)):
                    bad.append(k)
                    continue
                for c in (c for x in sets for c in x):
                    if not (isinstance(c, dict) and is_int(c.get("type")) and isinstance(c.get("values"), list)
                            and isinstance(c.get("positive"), bool) and "targetIds" in c):
                        bad.append(k)
                    elif c["type"] == BAND_CONDITION:
                        fives += 1
                        if c["targetIds"] is not None:
                            bad.append(f"{k} (condition {BAND_CONDITION} targetIds not null)")
                    elif not (isinstance(c["targetIds"], list) and all(map(is_int, c["targetIds"]))):
                        bad.append(k)
            cu = e.get("cumulative", "missing")
            if cu is not None and not (isinstance(cu, dict) and all(
                    k in cu for k in ("type", "values", "targetIds", "maxCumulativeCount"))):
                bad.append("cumulative")
            if bad:
                g.fail(f"{w} effect {i}: {', '.join(dict.fromkeys(bad))} missing or malformed")
        if isinstance(band, bool) and band != (fives > 0):
            g.fail(f"{w}: bandCondition {band}, its effects have {fives} condition {BAND_CONDITION}")
        skills = s["skills"]
        if not (isinstance(skills, list) and skills):
            g.fail(f"{w}: no skills")
            continue
        for k in skills:
            ok = (isinstance(k, dict) and is_int(k.get("id")) and is_int(k.get("level")) and k["level"] >= 1
                  and "memberTargetIds" in k and "bandIds" in k)
            targets, band_ids = (k.get("memberTargetIds"), k.get("bandIds")) if isinstance(k, dict) else (0, 0)
            if band is True:
                ok = (ok and isinstance(targets, list) and bool(targets) and all(map(is_int, targets))
                      and targets == sorted(set(targets)) and isinstance(band_ids, list)
                      and all(map(is_int, band_ids)))
            else:
                ok = ok and targets is None and band_ids is None
            if not ok:
                g.fail(f"{w}: skill {k!r} is not an id, a level and (with a band condition alone) member targets "
                       f"and bands"[:240])

    # the charts
    aptitudes = nulls = variants = deterministic = 0
    missed = []
    for song, chart in charts_of(doc):
        w, d = where(song, chart), chart.get("deck")
        if not isinstance(d, dict):
            continue                                 # the deck gate fails it
        if "gekisouAptitude" not in d:
            g.fail(f"{w}: no deck.gekisouAptitude")
            continue
        a, ranges, dseeds = d["gekisouAptitude"], d.get("ranges") or [], d.get("seeds") or []
        why = ("unplayable with Gekisou on" if d.get("unplayable") else "without a Gekisou range" if not ranges
               else None if shapes else "without a Gekisou skill shape")
        if a is None:
            nulls += 1
            if why is None:
                g.fail(f"{w}: deck.gekisouAptitude is null, but the chart is playable with Gekisou on")
            continue
        if why is not None:
            g.fail(f"{w}: a Gekisou skill aptitude on a chart {why}")
            continue
        if not (isinstance(a, dict) and isinstance(a.get("factors"), list) and isinstance(a.get("variants"), list)):
            g.fail(f"{w}: deck.gekisouAptitude has no factors and variants lists")
            continue
        aptitudes += 1
        positions = d.get("positions")
        missions = {r.get("mission") for r in ranges if isinstance(r, dict)}
        linear = bool(dseeds) and dseeds[0].get("rangeWeights") is not None

        # factors
        if len(a["factors"]) != len(ranges):
            g.fail(f"{w}: {len(a['factors'])} factors for {len(ranges)} ranges")
        for j, (f, r) in enumerate(zip(a["factors"], ranges, strict=False)):
            f, r = (f if isinstance(f, dict) else {}), (r if isinstance(r, dict) else {})
            bad = [k for k in FACTOR_INTS if not (is_int(f.get(k)) and f[k] >= 0)]
            if not pair(f.get("lotteries")):
                bad.append("lotteries")
            if bad:
                g.fail(f"{w} factors {j}: {', '.join(bad)} missing or not counts")
                continue
            if r.get("mission") != JUST_MISSION and (f["justNotes"] or f["perfectNotes"]):
                g.fail(f"{w} factors {j}: Just or Perfect notes in a range without the Just mission")
            if f["justNotes"] + f["perfectNotes"] > f["judgedNotes"]:
                g.fail(f"{w} factors {j}: {f['justNotes']} Just and {f['perfectNotes']} Perfect notes of "
                       f"{f['judgedNotes']} judged")
            at = [s["ranges"][j] for s in dseeds if isinstance(s.get("ranges"), list) and len(s["ranges"]) > j
                  and isinstance(s["ranges"][j], dict)]
            lots = [sum(x["lotResults"]) for x in at
                    if isinstance(x.get("lotResults"), list) and all(map(is_int, x["lotResults"]))]
            if r.get("mission") != LUCK_MISSION and f["lotteries"] != [0, 0]:
                g.fail(f"{w} factors {j}: lotteries {f['lotteries']} in a range without the luck mission")
            elif lots and len(lots) == len(dseeds) and not close(f["lotteries"][0], sum(lots) / len(lots)):
                g.fail(f"{w} factors {j}: lotteries {f['lotteries'][0]}, deck.seeds' lotResults give "
                       f"{sum(lots) / len(lots)}")

        # the variants: which, in order
        want = [(s["id"], match) for s in sorted(by_id.values(), key=lambda s: s["id"])
                if s["mission"] == 4 or s["mission"] in missions
                for match in ((True, False) if s["bandCondition"] is True else (None,))]
        got = [(v.get("shape"), v.get("bandMatch")) if isinstance(v, dict) else None for v in a["variants"]]
        unknown = [v[0] for v in got if v is not None and not (is_int(v[0]) and v[0] in by_id)]
        other = [v[0] for v in got if v is not None and is_int(v[0]) and v[0] in by_id
                 and by_id[v[0]]["mission"] != 4 and by_id[v[0]]["mission"] not in missions]
        if unknown:
            g.fail(f"{w}: variants of shapes {sorted(set(map(repr, unknown)))} not in deck.gekisouAptitude.shapes")
        if other:
            g.fail(f"{w}: variants of shapes {sorted(set(other))} of a mission the chart does not play")
        if got != want and not unknown and not other:
            lacking = [x for x in want if x not in got]
            g.fail(f"{w}: variants {'lack ' + repr(lacking[:6]) if lacking else 'not in shape order, true first'}"
                   f" ({len(got)} for {len(want)})")

        for v in a["variants"]:
            if not isinstance(v, dict):
                g.fail(f"{w}: a variant {v!r}")
                continue
            s = f"{w} shape {v.get('shape')}" + ("" if v.get("bandMatch") is None else f" {v['bandMatch']}")
            absent = [k for k in VARIANT_KEYS if k not in v]
            if absent:
                g.fail(f"{s}: no {', '.join(absent)}")
                continue
            variants += 1
            shape = by_id.get(v["shape"]) if is_int(v["shape"]) else None
            det = v["deterministic"]
            if not isinstance(det, bool):
                g.fail(f"{s}: deterministic {det!r}")
                det = False
            deterministic += det
            if v["bandMatch"] is not None and not isinstance(v["bandMatch"], bool):
                g.fail(f"{s}: bandMatch {v['bandMatch']!r}")
            elif shape is not None and (v["bandMatch"] is None) == (shape.get("bandCondition") is True):
                g.fail(f"{s}: bandMatch {v['bandMatch']!r} for a shape "
                       f"{'with' if v['bandMatch'] is None else 'without'} a band condition")
            n, cross, met = v["seeds"], v["crossSeeds"], v["seTargetMet"]
            if not (is_int(n) and n >= 1 and isinstance(met, bool)):
                g.fail(f"{s}: seeds {n!r}, seTargetMet {met!r}")
            elif det and (n != 1 or not met):
                g.fail(f"{s}: deterministic, but {n} seeds and seTargetMet {met}")
            elif not det and batches and (n not in batches or (not met and n != batches[-1])):
                g.fail(f"{s}: {n} seeds (seTargetMet {met}), not a batch of the seed rule {batches}")
            elif not det and not met:
                missed.append(f"{chart.get('scoreId')} shape {v['shape']}")
            if (not is_int(cross) or cross < 1 or (is_int(n) and is_int(rule.get("crossSeeds"))
                                                 and cross != min(n, rule["crossSeeds"]))):
                g.fail(f"{s}: crossSeeds {cross!r}, expected min({n}, {rule['crossSeeds']})")

            # every [mean, se]
            values = {k: v[k] for k in VARIANT_PAIRS}
            rs = v["ranges"]
            if not isinstance(rs, list) or len(rs) != len(ranges):
                g.fail(f"{s}: {len(rs) if isinstance(rs, list) else repr(rs)} range results for {len(ranges)} ranges")
                rs = []
            for j, r in enumerate(rs):
                for k in VARIANT_RANGE_PAIRS:
                    values[f"ranges[{j}].{k}"] = r.get(k) if isinstance(r, dict) else None
            wt, rw = v["weights"], v["rangeWeights"]
            if plain is None:
                if wt is not None or rw is not None:
                    g.fail(f"{s}: weights or rangeWeights without a plain kind")
            else:
                if not (isinstance(wt, list) and len(wt) == positions):
                    g.fail(f"{s}: weights are not one [mean, se] per position")
                else:
                    values.update({f"weights[{k}]": x for k, x in enumerate(wt)})
                if not linear:
                    if rw is not None:
                        g.fail(f"{s}: rangeWeights, but deck.seeds[0].rangeWeights is null")
                elif not (isinstance(rw, list) and len(rw) == positions and all(
                        isinstance(x, list) and len(x) == len(ranges) for x in rw)):
                    g.fail(f"{s}: rangeWeights are not [position][range] [mean, se]")
                else:
                    values.update({f"rangeWeights[{k}][{j}]": y for k, x in enumerate(rw) for j, y in enumerate(x)})
            bad = [k for k, x in values.items() if not pair(x)]
            if bad:
                g.fail(f"{s}: {', '.join(bad[:6])}{' ...' if len(bad) > 6 else ''} not [mean, se] (finite, se >= 0)")
            elif det and any(x[1] for x in values.values()):
                g.fail(f"{s}: deterministic, but a standard error is not 0: "
                       f"{', '.join(k for k, x in values.items() if x[1])[:160]}")
            elif rs and all(pair(r.get(k)) for r in rs for k in ("rangeScore", "rankBonus")):
                inside = sum(r["rangeScore"][0] + r["rankBonus"][0] for r in rs)
                if abs(v["tail"][0] - (v["score"][0] - inside)) > (
                        0.0005 * (2 + 2 * len(rs)) + APTITUDE_SLACK):
                    g.fail(f"{s}: tail {v['tail'][0]} is not score {v['score'][0]} less the ranges' rangeScore and "
                           f"rankBonus {inside}")

            # Perfect rank-bonus deltas are not exported. Only a necessary rounding bound can be checked
            # for stochastic means: each difference of two truncated bonuses is within 2 points of delta*p/100.
            if not bad and len(rs) == len(ranges):
                perfect_terms = [r["rangeScorePerfect"][0] * (1 + info["rankBonusPercent"] / 100)
                                 for r, info in zip(rs, ranges, strict=True)]
                rounding = 0.001 + sum(0.0005 * abs(1 + info["rankBonusPercent"] / 100) for info in ranges)
                residual = v["scorePerfect"][0] - v["tailPerfect"][0] - sum(perfect_terms)
                if abs(residual) > 2 * len(ranges) + rounding + APTITUDE_SLACK:
                    g.fail(f"{s}: tailPerfect violates the necessary Perfect rank-bonus rounding bound")

            # Deterministic point deltas are integers. Perfect rank bonuses can then be recovered exactly
            # from the first baseline seed (stochastic Perfect rank-bonus deltas are not exported).
            if det and not bad and rs and dseeds:
                points = [x[0] for k, x in values.items() if not k.startswith(("weights", "rangeWeights"))]
                if not all(float(x).is_integer() for x in points):
                    g.fail(f"{s}: deterministic, but a point delta is not an integer")
                else:
                    perfect = 0
                    for r, info, base in zip(rs, ranges, dseeds[0]["ranges"], strict=True):
                        delta, baseline = int(r["rangeScorePerfect"][0]), base["rangeScorePerfect"]
                        percent = info["rankBonusPercent"]
                        perfect += delta + rank_bonus(baseline + delta, percent) - rank_bonus(baseline, percent)
                    if v["tailPerfect"][0] != v["scorePerfect"][0] - perfect:
                        g.fail(f"{s}: tailPerfect is not scorePerfect less the Perfect range scores and bonuses")

            # the check
            c = v["check"]
            if not (isinstance(c, dict) and all(k in c for k in APTITUDE_CHECK_KEYS)):
                g.fail(f"{s}: the check has no {', '.join(APTITUDE_CHECK_KEYS)}")
                continue
            if dseeds and c["seed"] != dseeds[0].get("seed"):
                g.fail(f"{s}: the check's seed {c['seed']!r} is not deck.seeds[0]'s {dseeds[0].get('seed')!r}")
            ranks = c["ranks"]
            if not (isinstance(ranks, list) and len(ranks) == len(ranges)
                    and all(is_int(x) and 1 <= x <= RANKS for x in ranks)):
                g.fail(f"{s}: the check's ranks {ranks!r} are not one rank per range")
            elif not linear and any(x != 1 for x in ranks):
                g.fail(f"{s}: the check's ranks {ranks} on a chart whose ranks are not linear (rank 1 alone)")
            cd = c["deck"]
            if not (isinstance(cd, list) and len(cd) == positions and all(
                    x is None or (plain is not None and isinstance(x, list) and len(x) == 2 and x[0] == plain
                                  and is_num(x[1])) for x in cd)):
                g.fail(f"{s}: the check deck is not a [plain kind, value] or null per position")
            if not within(c):
                g.fail(f"{s}: the check is not within its bound")
    if missed:
        g.warn(f"{len(missed)} variants missed the seed rule's standard error target: {', '.join(missed[:10])}"
               + (" ..." if len(missed) > 10 else ""))
    g.note = (f"{len(shapes)} shapes; {aptitudes} charts with an aptitude, {nulls} null; {variants} variants, "
              f"{deterministic} deterministic; plain kind {plain}")


def gate_gzip(doc, ctx: Context, g: Gate, raw: bytes):
    """The file's gzip size (what the page downloads) and that of its Gekisou skill aptitude, against loose caps."""
    whole = len(gzip.compress(raw, 6))
    deck = doc.get("deck") if isinstance(doc.get("deck"), dict) else {}
    part = {"deck": deck.get("gekisouAptitude"),
            "charts": [(c.get("deck") or {}).get("gekisouAptitude") if isinstance(c.get("deck"), dict) else None
                       for _, c in charts_of(doc)]}
    aptitude = len(gzip.compress(json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6))
    if whole > FILE_GZIP_MAX:
        g.fail(f"{whole} bytes gzipped, more than {FILE_GZIP_MAX}")
    if aptitude > APTITUDE_GZIP_MAX:
        g.fail(f"the Gekisou skill aptitude: {aptitude} bytes gzipped, more than {APTITUDE_GZIP_MAX}")
    g.note = f"{whole} bytes gzipped, the aptitude {aptitude}"


def nonfinite(v, path: str, out: list):
    if isinstance(v, float):
        if not math.isfinite(v):
            out.append(path)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            nonfinite(x, f"{path}[{i}]", out)
    elif isinstance(v, dict):
        for k, x in v.items():
            nonfinite(x, f"{path}.{k}" if path else k, out)


def gate_finite(doc, ctx: Context, g: Gate):
    """No NaN or infinity; one inside master data rows as served (songs[].master, master: `1e999` is how the format
    writes a binary32 infinity) is reported, not failed."""
    found: list[str] = []
    nonfinite(doc, "", found)
    for path in found:
        if re.match(r"(songs\[\d+\]\.master|master)\b", path):
            g.warn(f"{path}: not finite (master data as served)")
        else:
            g.fail(f"{path}: not finite")
    g.note = f"{len(found)} non-finite numbers"


def gate_references(doc, ctx: Context, g: Gate):
    langs = doc.get("languages") or []
    if not langs:
        g.fail("no languages")

    def text(t, what: str, required: bool) -> bool:
        if t is None:
            if required:
                g.fail(f"{what}: no text")
            return False
        if not isinstance(t, dict) or set(t) != set(langs) or not all(isinstance(v, str) for v in t.values()):
            g.fail(f"{what}: not a text in {', '.join(langs)}")
            return False
        if required and not any(t.values()):
            g.fail(f"{what}: empty in every language")
            return False
        return True

    def ids(rows, what: str) -> set:
        seen = [r.get("id") for r in rows]
        if len(set(seen)) != len(seen) or not all(map(is_int, seen)):
            g.fail(f"{what}: ids are not unique ints")
        return set(seen)

    bands = ids(doc.get("bands") or [], "bands")
    characters = ids(doc.get("characters") or [], "characters")
    tags = ids(doc.get("tags") or [], "tags")
    ids(doc.get("categories") or [], "categories")
    for b in doc.get("bands") or []:
        text(b.get("name"), f"band {b.get('id')} name", True)
    for c in doc.get("characters") or []:
        text(c.get("name"), f"character {c.get('id')} name", True)
        text(c.get("shortName"), f"character {c.get('id')} short name", False)
        if c.get("bandId") not in bands:
            g.warn(f"character {c.get('id')}: band {c.get('bandId')} is not in bands")
    for t in doc.get("tags") or []:
        text(t.get("name"), f"tag {t.get('id')} name", True)
    categories = set()
    for c in doc.get("categories") or []:
        text(c.get("name"), f"category {c.get('id')} name", True)
        categories.update(c.get("musicCategories") or [])
    songs = doc.get("songs") or []
    if not songs:
        g.fail("no songs")
    ids(songs, "songs")
    if [s.get("id") for s in songs] != sorted(s.get("id") for s in songs if is_int(s.get("id"))):
        g.fail("songs are not sorted by id")
    score_ids: set = set()
    untitled = []
    for s in songs:
        sid = f"song {s.get('id')}"
        if text(s.get("title"), f"{sid} title", True) and ctx.language and not s["title"].get(ctx.language):
            untitled.append(s.get("id"))
        for k in ("ruby", "phonetic", "bandName", "lyricist", "composer", "arranger"):
            text(s.get(k), f"{sid} {k}", False)
        for key, known, what in (("bandIds", bands, "band"), ("vocalCharacterIds", characters, "character"),
                                 ("bestMusicTagIds", tags, "tag")):
            unknown = [x for x in s.get(key) or [] if x not in known]
            if unknown:
                g.fail(f"{sid}: {what} {', '.join(map(str, unknown))} not in the file's {what}s")
        if not s.get("bandIds") and s.get("bandName") is None:
            g.fail(f"{sid}: neither a band nor a band name")
        other = [x for x in s.get("musicCategories") or [] if x not in categories]
        if other:
            g.warn(f"{sid}: music categories {', '.join(map(str, other))} are on no category tab")
        jacket = s.get("jacket")
        if not isinstance(jacket, str) or not jacket:
            g.fail(f"{sid}: no jacket")
        elif ctx.jackets is not None:
            f = ctx.jackets / f"{jacket}.webp"
            if not f.is_file() or not f.stat().st_size:
                g.fail(f"{sid}: no jacket file jackets/{jacket}.webp")
        bgm = s.get("bgm") or {}
        if not (is_int(bgm.get("soundId")) and bgm.get("cueSheet") and bgm.get("cue")):
            g.fail(f"{sid}: no BGM cue")
        ranks = [r.get("rank") for r in s.get("scoreRanks") or []]
        if not ranks or len(set(ranks)) != len(ranks):
            g.fail(f"{sid}: score ranks {ranks!r}")
        charts = s.get("charts") or []
        order = [c.get("difficulty") for c in charts]
        if not charts or order != [d for d in DIFFICULTIES if d in order] or len(set(order)) != len(order):
            g.fail(f"{sid}: charts {order!r}")
        for c in charts:
            if c.get("scoreId") in score_ids:
                g.fail(f"{where(s, c)}: the score id occurs twice")
            score_ids.add(c.get("scoreId"))
    if untitled:
        g.warn(f"songs without a {ctx.language} title: {', '.join(map(str, untitled))}")
    g.note = (f"{len(songs)} songs, {len(bands)} bands, {len(characters)} characters, {len(tags)} tags"
              + ("" if ctx.jackets is None else ", jackets present"))


def gate_bgm(doc, ctx: Context, g: Gate):
    lengths = []
    for s in doc.get("songs") or []:
        sid = f"song {s.get('id')}"
        L = (s.get("bgm") or {}).get("length")
        if not isinstance(L, dict):
            g.fail(f"{sid}: no BGM length")
            continue
        dur, samples, rate = L.get("durationMs"), L.get("samples"), L.get("sampleRate")
        if not (is_int(dur) and is_int(samples) and is_int(rate) and rate > 0 and samples > 0):
            g.fail(f"{sid}: BGM length {dur!r} ms, {samples!r} samples at {rate!r} Hz")
            continue
        if dur != samples * 1000 // rate:
            g.fail(f"{sid}: BGM durationMs {dur} is not samples * 1000 // sampleRate")
        if not BGM_MS[0] <= dur <= BGM_MS[1]:
            g.fail(f"{sid}: BGM of {dur} ms (bounds {BGM_MS[0]} to {BGM_MS[1]})")
        if is_int(L.get("lengthMs")) and abs(dur - L["lengthMs"]) > BGM_CUE_SLACK_MS:
            g.fail(f"{sid}: BGM stream {dur} ms, cue length {L['lengthMs']} ms")
        last = max((c.get("lastNoteMs") or 0 for c in s.get("charts") or []), default=0)
        if dur < last:
            g.fail(f"{sid}: BGM of {dur} ms ends before the last note at {last} ms")
        elif dur - last > BGM_TAIL_MS:
            g.warn(f"{sid}: BGM plays {dur - last} ms after the last note")
        lengths.append(dur)
    if lengths:
        g.note = f"{min(lengths) / 1000:.1f} s to {max(lengths) / 1000:.1f} s"


def gate_page(doc, ctx: Context, g: Gate):
    """The chart data page's own modules (catalog.js, ranking.js) over the file in Node.js: music_data_smoke.mjs."""
    if ctx.page is None:
        g.note = "skipped"
        return
    if not (ctx.page / "catalog.js").is_file() or not (ctx.page / "ranking.js").is_file():
        g.fail(f"{ctx.page}: no catalog.js / ranking.js (the chart data page, examples/songs)")
        return
    r = subprocess.run(["node", str(SMOKE), str(ctx.page), str(ctx.file)], capture_output=True, text=True,
                       timeout=600)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
    if r.returncode:
        for x in lines or [f"node exited with {r.returncode}"]:
            g.fail(x[:300])
    else:
        g.note = lines[-1][:200] if lines else "passed"


GATES = (("schema", gate_schema), ("provenance", gate_provenance), ("counts", gate_counts), ("deck", gate_deck),
         ("scenarios", gate_scenarios), ("aptitude", gate_aptitude), ("finite", gate_finite),
         ("references", gate_references), ("bgm", gate_bgm), ("size", gate_size), ("gzip", gate_gzip),
         ("page", gate_page))


def gates(raw: bytes, ctx: Context, only=None) -> dict:
    """The report of the gates (`only`: their names) on the file's bytes."""
    try:
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except ValueError as e:
        doc, results = None, [{"gate": "json", "passed": False, "failures": [f"not JSON: {str(e)[:160]}"],
                               "failureCount": 1, "warnings": [], "warningCount": 0, "note": ""}]
    if doc is not None:
        results = []
        for name, fn in GATES:
            if only is not None and name not in only:
                continue
            g = Gate()
            try:
                fn(doc, ctx, g, raw) if name in ("size", "gzip") else fn(doc, ctx, g)
            except Exception as e:                   # a malformed file the gate did not foresee fails the gate
                g.fail(f"the gate stopped: {type(e).__name__}: {str(e)[:160]}")
            results.append({"gate": name, "passed": not g.failures, "failures": g.failures[:LISTED],
                            "failureCount": len(g.failures), "warnings": g.warnings[:LISTED],
                            "warningCount": len(g.warnings), "note": g.note})
    return {"passed": all(r["passed"] for r in results), "sha256": sha256(raw), "bytes": len(raw),
            "gates": results}


def report_markdown(report: dict) -> str:
    rows = ["| Gate | Result | |", "|---|---|---|"]
    for r in report["gates"]:
        result = "passed" if r["passed"] else f"**failed** ({r['failureCount']})"
        if r["warningCount"]:
            result += f", {r['warningCount']} warnings"
        rows.append(f"| {r['gate']} | {result} | {r['note']} |")
    details = []
    for r in report["gates"]:
        for kind, key, n in (("failure", "failures", "failureCount"), ("warning", "warnings", "warningCount")):
            for x in r[key]:
                details.append(f"- {r['gate']} {kind}: {x}")
            if r[n] > len(r[key]):
                details.append(f"- {r['gate']}: {r[n] - len(r[key])} more {kind}s")
    return "\n".join(["", "#### Gates", ""] + rows + ([""] + details if details else []))


def cmd_check(out: str, master: str, page: str) -> None:
    import nnnotes
    o, m = Path(out), Path(master)
    raw = (o / FILE).read_bytes()
    snapshot = json.loads(snapshot_file(m).read_text(encoding="utf-8"))
    ctx = Context(region=env("NNNOTES_CATALOG_REGION"), language=os.environ.get("NNNOTES_CATALOG_LANGUAGE"),
                  master=m, snapshot=snapshot, deck_commit=deck_commit(), nnnotes_version=nnnotes.__version__,
                  jackets=o / "jackets", schema=SCHEMA, published=published(FILE), page=Path(page), file=o / FILE)
    report = gates(raw, ctx)
    (o / "check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    summary(report_markdown(report))
    if not report["passed"]:
        fail("a gate failed: nothing is published (the job summary lists why)")
    doc = json.loads(raw)
    p = doc["provenance"]
    version = re.sub(r"[^A-Za-z0-9._-]", "_", str(p["master"]["version"]))
    marker = {
        "format": BUILD_FORMAT,
        "file": FILE, "sha256": report["sha256"], "bytes": report["bytes"],
        "archive": f"{ARCHIVE}{version}/{report['sha256']}.json",
        "songs": len(doc["songs"]), "charts": sum(len(s["charts"]) for s in doc["songs"]),
        "jackets": len(list((o / "jackets").glob("*.webp"))),
        "inputs": inputs(snapshot["entry"]),
        "provenance": {"region": p["region"], "client": p["client"], "masterVersion": p["master"]["version"],
                       "deckCommit": p["deck"]["commit"], "exporterVersion": p["exporter"]["version"]},
        "player": {"repository": os.environ.get("PLAYER_REPOSITORY"), "ref": os.environ.get("PLAYER_REF")},
        "gates": {r["gate"]: {"passed": r["passed"], "warnings": r["warningCount"]} for r in report["gates"]},
        "builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                f"{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_RUN_ID") else None),
    }
    (o / MARKER).write_text(json.dumps(marker, indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- publish
def upload(b, key: str, src: Path, cache: str) -> None:
    extra = {"ContentType": story_site.TYPES.get(src.suffix.lower(), "application/octet-stream"),
             "CacheControl": cache}
    b.s3.upload_file(str(src), b.name, b.prefix + key, ExtraArgs=extra)


def read_back(b, key: str, digest: str) -> None:
    """The object as stored (S3 GET), else as served (plain HTTP), must have the SHA-256 uploaded."""
    for attempt in range(4):
        try:
            if sha256(b.s3.get_object(Bucket=b.name, Key=b.prefix + key)["Body"].read()) == digest:
                return
        except Exception as e:                       # Cloudflare in front of the store rejects a signed GET at times
            print(f"read back {key}: {type(e).__name__}", flush=True)
        time.sleep(3 * (attempt + 1))
    try:
        if sha256(get(public_url(key), timeout=300)) == digest:
            return
    except urllib.error.URLError as e:
        print(f"read back {key} over HTTP: {e}", flush=True)
    fail(f"{key}: the bucket does not serve what was uploaded (SHA-256 {digest[:12]})")


def publishing() -> bool:
    """The publishing switch: uploads only with MUSIC_DATA_PUBLISH `true` (a repository variable, unset: off)."""
    return os.environ.get("MUSIC_DATA_PUBLISH") == "true"


def cmd_publish(out: str, dry_run: bool = False) -> None:
    if not publishing() and not dry_run:
        summary("- publishing is off (the repository variable MUSIC_DATA_PUBLISH is not `true`): a dry run")
        dry_run = True
    o = Path(out)
    raw = (o / FILE).read_bytes()
    report = json.loads((o / "check.json").read_text(encoding="utf-8"))
    if not report.get("passed") or report.get("sha256") != sha256(raw) or not (o / MARKER).is_file():
        fail("the file has not passed the gates (check.json): nothing is published")
    marker = json.loads((o / MARKER).read_text(encoding="utf-8"))
    b = bucket()
    if not b.writable and not dry_run:
        fail("publish needs STORY_S3_ACCESS_KEY and STORY_S3_SECRET_KEY")
    force = os.environ.get("FORCE") == "true"
    try:
        have = b.keys(JACKETS)
        archived = b.keys(marker["archive"]).get(marker["archive"]) == len(raw)
    except Exception as e:                           # a dry run without a key where the bucket lists to none
        if not dry_run:
            raise
        print(f"cannot list the bucket ({type(e).__name__}): the dry run lists every object", flush=True)
        have, archived = {}, False
    jackets = sorted((o / "jackets").glob("*.webp"))
    new = [j for j in jackets if force or have.get(JACKETS + j.name) != j.stat().st_size]
    steps = [(JACKETS + j.name, j, JACKET_CACHE, False) for j in new]
    if not archived:
        steps.append((marker["archive"], o / FILE, ARCHIVE_CACHE, True))
    # the file before the marker that names it, the jackets and the archive copy before the file
    steps += [(FILE, o / FILE, FILE_CACHE, True), (MARKER, o / MARKER, FILE_CACHE, True)]
    if dry_run or not publishing():
        for key, _, _, _ in steps:
            print(f"would upload {b.prefix}{key}")
    else:
        story_site.parallel(lambda s: upload(b, s[0], s[1], s[2]), [s for s in steps if not s[3]])
        for key, src, cache, check in steps:
            if check:
                upload(b, key, src, cache)
                read_back(b, key, sha256(src.read_bytes()))
    summary(f"- {'would publish' if dry_run else 'published'} {len(new)} jackets (of {len(jackets)}), "
            f"{'the archive copy, ' if not archived else ''}{FILE} ({len(raw)} bytes, sha256 "
            f"{short(marker['sha256'])}), {MARKER}: {public_url(FILE)}")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "plan" and not args:
        cmd_plan()
    elif cmd == "master" and len(args) == 1:
        cmd_master(args[0])
    elif cmd == "build" and len(args) == 1:
        cmd_build(args[0])
    elif cmd == "check" and len(args) == 3:
        cmd_check(*args)
    elif cmd == "publish" and len(args) in (1, 2) and args[1:] in ([], ["--dry-run"]):
        cmd_publish(args[0], dry_run=bool(args[1:]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
