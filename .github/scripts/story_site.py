#!/usr/bin/env python3
"""The story site's CI steps (.github/workflows/story-site.yml): the published site lives in an S3 bucket, a run adds
the stories it lacks.

    regions               the regions this run builds (GitHub outputs `regions`, a JSON list, and `count`): those of
                          $STORY_REGIONS (default hk-tw-mo) that the repository_dispatch payload names, or
                          $REQUESTED_REGION of a workflow_dispatch (empty / "all": every one), or all on a schedule
    plan                  the stories to build: $REQUESTED, else every MasterAdv id without a manifest in the bucket
                          (at most $STORY_LIMIT, in id order); GitHub outputs `stories` (space separated) and `count`
    master OUT            the decoded master data of $MASTERDATA_REGION from moenotes-masterdata-sync (index.json:
                          every table with its SHA-256), for `nnnotes web`
    fetch SITE            every object of the site except assets/ into SITE (the manifests `nnnotes web` skips and the
                          indexes it rewrites from), with their SHA-256 in SITE.fetched.json
    build SITE ID...      `nnnotes web SITE --story ID ...` ($FORCE: --force), then `--player-only`, which rewrites the
                          player files and the indexes from every manifest present even after a failed build
    publish SITE [--dry-run]
                          the new assets (those the bucket lacks), then every other file that is new or changed since
                          `fetch`, the indexes (stories.json, models.json, charts.json) last; never deletes

Bucket: $STORY_S3_ENDPOINT, $STORY_S3_BUCKET, $STORY_S3_PREFIX (a key prefix, may be empty), credentials
$STORY_S3_ACCESS_KEY / $STORY_S3_SECRET_KEY (unset: anonymous, reads only). Objects are stored as nnnotes writes them:
a compressed asset (.gz / .br) as it is, without Content-Encoding (the player decodes it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INDEXES = ("stories.json", "models.json", "charts.json")
ASSETS = "assets/"
STORY_MANIFEST = re.compile(r"stories/(\d+)\.json")
ASSET_CACHE = "public, max-age=31536000, immutable"       # content-addressed: never changes
OTHER_CACHE = "public, max-age=300"
TYPES = {".json": "application/json", ".gz": "application/gzip", ".br": "application/octet-stream",
         ".m4a": "audio/mp4", ".mp4": "video/mp4", ".webm": "video/webm", ".png": "image/png", ".jpg": "image/jpeg",
         ".glsl": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".map": "application/json", ".txt": "text/plain; charset=utf-8",
         ".svg": "image/svg+xml", ".wasm": "application/wasm", ".bin": "application/octet-stream"}


def get(url: str, timeout: int = 120) -> bytes:
    # an explicit User-Agent: Cloudflare in front of the services refuses Python-urllib's
    request = urllib.request.Request(url, headers={"User-Agent": "moenotes-story-site (GitHub Actions)",
                                                   "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return r.read()


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    if default is None:
        sys.exit(f"story_site: set {name}")
    return default


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"{name}={value}")


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


# ---------------------------------------------------------------- bucket
class Bucket:
    def __init__(self):
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
        key, secret = os.environ.get("STORY_S3_ACCESS_KEY"), os.environ.get("STORY_S3_SECRET_KEY")
        # S3-compatible stores (SeaweedFS here) take path-style requests and no default CRC checksums
        config = Config(s3={"addressing_style": "path"}, retries={"max_attempts": 8, "mode": "adaptive"},
                        request_checksum_calculation="when_required", response_checksum_validation="when_required",
                        max_pool_connections=32, **({} if key and secret else {"signature_version": UNSIGNED}))
        self.writable = bool(key and secret)
        self.s3 = boto3.client("s3", endpoint_url=env("STORY_S3_ENDPOINT"), aws_access_key_id=key or None,
                               aws_secret_access_key=secret or None, region_name="us-east-1", config=config)
        self.name = env("STORY_S3_BUCKET")
        prefix = os.environ.get("STORY_S3_PREFIX", "").strip("/")
        self.prefix = f"{prefix}/" if prefix else ""

    def keys(self, sub: str = "") -> dict[str, int]:
        """{site path: size} of the objects under `sub` (a site path prefix)."""
        out = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.name, Prefix=self.prefix + sub):
            for o in page.get("Contents", []):
                out[o["Key"][len(self.prefix):]] = o["Size"]
        return out

    def download(self, path: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.name, self.prefix + path, str(dest))

    def upload(self, path: str, src: Path) -> None:
        suffix = Path(path).suffix.lower()
        extra = {"ContentType": TYPES.get(suffix, "application/octet-stream"),
                 "CacheControl": ASSET_CACHE if path.startswith(ASSETS) else OTHER_CACHE}
        self.s3.upload_file(str(src), self.name, self.prefix + path, ExtraArgs=extra)


def parallel(fn, items, workers: int = 16) -> None:
    with ThreadPoolExecutor(workers) as ex:
        for _ in ex.map(fn, items):
            pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- master data
def master_index() -> tuple[str, dict]:
    base = env("MASTERDATA_BASE_URL").rstrip("/")
    index = json.loads(get(f"{base}/index.json", 60))
    region = index["regions"][env("MASTERDATA_REGION")]
    if not re.fullmatch(r"/([a-z]+/)?master/", region["path"]):
        sys.exit(f"story_site: unexpected master path {region['path']!r}")
    return base + region["path"], region


# Regions with a story site (story-site-region.yml gives each its own bucket prefix: JP model ids overlap the
# international ones).
SITE_REGIONS = ("hk-tw-mo", "jp")


def split_list(text: str) -> list[str]:
    return [part for part in re.split(r"[\s,]+", text.strip()) if part]


def select_regions(enabled: str, event: str, payload: list[str] | None, requested: str) -> list[str]:
    """The regions a run builds: of $STORY_REGIONS, the dispatch's regions, the requested one, or all (schedule)."""
    allowed = [r for r in split_list(enabled) if r in SITE_REGIONS]
    unknown = [r for r in split_list(enabled) if r not in SITE_REGIONS]
    if unknown:
        sys.exit(f"story_site: no story site for region(s) {', '.join(unknown)}")
    if event == "repository_dispatch":
        # Older dispatches carry no regions: build every enabled one (a run without new stories ends at plan).
        wanted = payload if payload else allowed
    elif event == "workflow_dispatch" and requested and requested != "all":
        wanted = [requested]
    else:
        wanted = allowed
    return [r for r in allowed if r in wanted]


def cmd_regions() -> None:
    payload = None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        regions = (json.loads(Path(event_path).read_text(encoding="utf-8")).get("client_payload") or {}).get("regions")
        payload = [r for r in regions if isinstance(r, str)] if isinstance(regions, list) else None
    regions = select_regions(env("STORY_REGIONS", "hk-tw-mo"), env("GITHUB_EVENT_NAME", ""), payload,
                             os.environ.get("REQUESTED_REGION", ""))
    output("regions", json.dumps(regions))
    output("count", str(len(regions)))


def configure_region() -> None:
    """Keep build inputs from one release; JP client/API/CDN are read from the public snapshot."""
    region = env("MASTERDATA_REGION")
    names = {"hk-tw-mo": "tw", "en": "en", "kr": "kr", "jp": "jp"}
    if region not in names or env("NNNOTES_CATALOG_REGION") != names[region]:
        sys.exit("story_site: master region and catalog region differ")
    if region != "jp":
        return
    _, snapshot = master_index()
    entry = snapshot.get("entry") or {}
    assets = entry.get("assets") or {}
    upstream = entry.get("upstream") or {}
    api = assets.get("api_root") or upstream.get("api_root")
    bundle = assets.get("bundle_root")
    from urllib.parse import urlsplit
    cdn = upstream.get("cdn_root")
    if bundle:
        u = urlsplit(bundle)
        cdn = f"{u.scheme}://{u.netloc}"
    client = entry.get("client_version")
    if not all(isinstance(v, str) and v for v in (api, cdn, client)):
        sys.exit("story_site: JP snapshot lacks API/CDN/client metadata")
    os.environ.update(NNNOTES_SERVERS_JP_API=api, NNNOTES_SERVERS_JP_CDN=cdn,
                      NNNOTES_SERVERS_JP_CLIENT_VERSION=client, NNNOTES_SERVERS_JP_PROVIDER="jp")


def fetch_table(url: str, name: str, digest: str, dest: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", name):
        sys.exit(f"story_site: unexpected master file name {name!r}")
    for _ in range(4):
        data = get(url + name)
        if hashlib.sha256(data).hexdigest() == digest:
            dest.write_bytes(data)
            return
    sys.exit(f"story_site: {name}: SHA-256 differs from index.json (the service changed snapshots? run again)")


def cmd_master(out: str) -> None:
    url, region = master_index()
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    parallel(lambda item: fetch_table(url, item[0], item[1], out_dir / item[0]), region["files"].items(), 8)
    print(f"master data: {len(region['files'])} tables into {out_dir}")


# ---------------------------------------------------------------- plan
def parse_ids(text: str) -> list[int]:
    ids = []
    for token in re.split(r"[\s,]+", text.strip()):
        if token:
            if not token.isdigit():
                sys.exit(f"story_site: not a story id: {token!r}")
            ids.append(int(token))
    return list(dict.fromkeys(ids))


def cmd_plan() -> None:
    url, region = master_index()
    data = get(url + "MasterAdv.json")
    if hashlib.sha256(data).hexdigest() != region["files"]["MasterAdv.json"]:
        sys.exit("story_site: MasterAdv.json: SHA-256 differs from index.json (run again)")
    stories = sorted(row["_id"] for row in json.loads(data)["_allData"])   # nnnotes storysite.all_stories
    have = {int(m.group(1)) for k in Bucket().keys("stories/") if (m := STORY_MANIFEST.fullmatch(k))}
    requested = parse_ids(os.environ.get("REQUESTED", ""))
    if requested:
        unknown = [i for i in requested if i not in set(stories)]
        if unknown:
            sys.exit(f"story_site: no MasterAdv row for {', '.join(map(str, unknown))}")
        todo, missing = requested, [i for i in requested if i not in have]
    else:
        missing = [i for i in stories if i not in have]
        todo = missing[:int(env("STORY_LIMIT", "40"))]
    summary(f"### Story site\n\n- master: {region.get('entry', {}).get('version', '?')} "
            f"({len(stories)} stories), the site has {len(have)}\n- missing: {len(missing)}; this run builds "
            f"{len(todo)}" + (f": {' '.join(map(str, todo))}" if todo else ""))
    output("stories", " ".join(map(str, todo)))
    output("count", str(len(todo)))


# ---------------------------------------------------------------- fetch / build / publish
def fetched_file(site: Path) -> Path:
    return site.parent / f"{site.name}.fetched.json"


def cmd_fetch(site_dir: str) -> None:
    site, bucket = Path(site_dir), Bucket()
    keys = [k for k in bucket.keys() if not k.startswith(ASSETS) and not k.endswith("/")]
    endpoint = env("STORY_S3_ENDPOINT").rstrip("/")

    # The bucket serves public read + list, so the fetch is plain HTTP like the player's, not a signed S3
    # call: Cloudflare's edge intermittently returned SignatureDoesNotMatch on signed ranged downloads.
    def fetch(key: str) -> None:
        dest = site / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(get(f"{endpoint}/{env('STORY_S3_BUCKET')}/{bucket.prefix}{key}", timeout=300))

    parallel(fetch, keys)
    (site / "assets").mkdir(parents=True, exist_ok=True)
    digests = {k: sha256(site / k) for k in keys}
    fetched_file(site).write_text(json.dumps(digests, indent=1, sort_keys=True), encoding="utf-8")
    print(f"fetched {len(keys)} files ({sum((site / k).stat().st_size for k in keys) / 1e6:.1f} MB) into {site}")


def cmd_build(site_dir: str, ids: list[str]) -> None:
    configure_region()
    site = Path(site_dir).resolve()
    stories = parse_ids(" ".join(ids))
    tmp = site.parent / f"{site.name}.tmp"
    nnnotes = [sys.executable, "-m", "nnnotes", "web", str(site), "--tmp", str(tmp)]
    if env("MASTERDATA_REGION") == "jp":
        nnnotes += ["--story-languages", "ja"]
    status = 0
    if stories:
        cmd = nnnotes + [a for i in stories for a in ("--story", str(i))]
        cmd += ["--workers", env("STORY_WORKERS", "2")]
        if os.environ.get("FORCE") == "true":
            cmd.append("--force")
        print("+ " + " ".join(cmd[1:]), flush=True)
        status = subprocess.run(cmd, stdout=open(site.parent / f"{site.name}.build.json", "wb")).returncode
    # The indexes and player files from every manifest present, whatever the build did.
    index = subprocess.run(nnnotes + ["--player-only"], stdout=subprocess.PIPE).returncode
    report = site.parent / f"{site.name}.build.json"
    if report.is_file():
        try:
            r = json.loads(report.read_text(encoding="utf-8"))
            summary(f"- built {len(r.get('storiesBuilt', []))}, failed {len(r.get('storiesFailed', []))}, "
                    f"skipped {len(r.get('storiesSkipped', []))}; models built "
                    f"{len((r.get('storyModels') or {}).get('modelsBuilt') or [])} in {r.get('storySeconds', '?')} s")
        except ValueError:
            pass
    if status or index:
        sys.exit(f"story_site: nnnotes web exited with {status or index}")


def cmd_publish(site_dir: str, dry_run: bool = False) -> None:
    site, bucket = Path(site_dir), Bucket()
    if not bucket.writable and not dry_run:
        sys.exit("story_site: publish needs STORY_S3_ACCESS_KEY and STORY_S3_SECRET_KEY")
    before = json.loads(fetched_file(site).read_text(encoding="utf-8"))
    local = sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())
    assets = [k for k in local if k.startswith(ASSETS)]
    have = bucket.keys(ASSETS)
    new_assets = [k for k in assets if have.get(k) != (site / k).stat().st_size]
    changed = [k for k in local if not k.startswith(ASSETS) and before.get(k) != sha256(site / k)]
    last = [k for k in changed if k in INDEXES]
    first = [k for k in changed if k not in INDEXES]
    if dry_run:
        for k in first + last:
            print(f"would upload {k}")
    else:
        # manifests before the indexes that list them, assets before the manifests that name them
        for group in (new_assets, first, last):
            parallel(lambda k: bucket.upload(k, site / k), group)
    summary(f"- {'would publish' if dry_run else 'published'} {len(new_assets)} assets ({sum((site / k).stat().st_size for k in new_assets) / 1e6:.1f} MB)"
            f", {len(first)} files, indexes: {', '.join(last) or 'unchanged'}")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "regions" and not args:
        cmd_regions()
    elif cmd == "plan" and not args:
        cmd_plan()
    elif cmd == "master" and len(args) == 1:
        cmd_master(args[0])
    elif cmd == "fetch" and len(args) == 1:
        cmd_fetch(args[0])
    elif cmd == "build" and args:
        cmd_build(args[0], args[1:])
    elif cmd == "publish" and len(args) in (1, 2) and args[1:] in ([], ["--dry-run"]):
        cmd_publish(args[0], dry_run=bool(args[1:]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
