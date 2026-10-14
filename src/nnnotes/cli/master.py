"""`nnnotes master`: the master data version a region serves, its download and its decoding."""

from __future__ import annotations

import sys
from pathlib import Path

from ..source.config import usable_cpus
from ..source.session import Session
from .common import command, group, out_arg, report, workers_arg

DOWNLOAD_WORKERS = 16


def _failed(r: dict) -> list[str]:
    return [f"failed: {f['file']}: {f['error']}" if "error" in f else f"failed: {f}" for f in r["failed"]]


def cmd_version(args, cfg):
    from ..source import gameapi

    region = cfg.region()
    try:
        v = Session(cfg).master_version(region)
    except gameapi.GameApiError as e:
        sys.exit(f"nnnotes: {e}")
    result = {"region": region, "masterVersion": v.version, "resourceVersion": v.resource_version}
    if v.resource_hash is not None:
        result["resourceHash"] = v.resource_hash
    report(args, result)


def cmd_decode(args, cfg):
    from ..source import master

    key = Session(cfg).master_key()
    files = master.input_files(args.inputs)
    if not files:
        raise SystemExit("nnnotes: no master data files (*.bin) among the inputs")
    workers = usable_cpus() if args.workers is None else args.workers
    r = master.decode_files(files, Path(args.out), key, workers=workers)
    report(args, r, lambda r: [f"{r['out']}  ({r['decoded']} tables decoded, {len(r['failed'])} failed)", *_failed(r)])
    if r["failed"]:
        sys.exit(1)


def cmd_download(args, cfg):
    from ..source import gameapi, master

    region = cfg.region()
    cdn = cfg.cdn(region)
    workers = DOWNLOAD_WORKERS if args.fetch_workers is None else args.fetch_workers
    try:
        if cfg.provider(region) == "jp":
            from ..source.jp import Client, master_version

            client = Client(cfg, region)
            observation = client.observe()
            version = observation.version.version if args.latest else master_version(args.version)
            r = master.download(
                observation.cdn,
                version,
                Path(args.out),
                workers=workers,
                get=lambda url: client.get(url, master=version),
                strict=True,
            )
        else:
            version = Session(cfg).master_version(region).version if args.latest else args.version
            r = master.download(cdn, version, Path(args.out), workers=workers)
    except (gameapi.GameApiError, master.DownloadError) as e:
        sys.exit(f"nnnotes: {e}")
    report(
        args,
        r,
        lambda r: [
            f"{r['out']}  (version {r['version']}: {r['downloaded']} downloaded, {r['kept']} kept, "
            f"{len(r['failed'])} failed of {r['files']} files)",
            *_failed(r),
        ],
    )
    if r["failed"]:
        sys.exit(1)


def register(sub) -> None:
    ms = group(sub, "master", "master data files: the version a region serves, download, decode")
    command(ms, "version", cmd_version, "the master data and resource version the region serves now (game API)")
    m = command(ms, "decode", cmd_decode, "master data .bin files -> <Table>.json")
    m.add_argument("inputs", nargs="+", type=Path, help="directories (their *.bin files) or files")
    out_arg(m, "output directory")
    workers_arg(m, "parallel decodes", "the CPUs this process may use", zero="one at a time")
    m = command(ms, "download", cmd_download, "a master data version from the region's CDN (SHA-256 checked)")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--version", help="master data version")
    g.add_argument("--latest", action="store_true", help="the version the region serves now (`master version`)")
    out_arg(m, "directory for MasterManifest.json and the .bin files")
    m.add_argument(
        "--fetch-workers",
        type=int,
        metavar="N",
        help=f"parallel downloads (default {DOWNLOAD_WORKERS})",
    )
