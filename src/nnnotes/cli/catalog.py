"""`nnnotes catalog`: the region's addressable keys, a local catalog browser, and the catalog versions of the depot
(import, fetch, list, diff)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..source.config import ConfigError, check_catalog_version
from ..source.session import Session
from .common import command, group, note, report, usage, write

CATALOG_FILE = re.compile(r"catalog_([A-Za-z0-9][A-Za-z0-9.-]*)_[A-Za-z0-9-]+\.bin")


def cmd_keys(args, cfg):
    cat = Session(cfg).catalog(bundles=False)
    ks = cat.keys(args.prefix)
    shown = ks[: args.limit] if args.limit else ks
    if args.json:
        report(args, {"keys": shown, "total": len(ks)})
        return
    write("".join(f"{k}\n" for k in shown))
    note(f"# {len(ks)} keys")


def cmd_browse(args, cfg):
    from .browse import Region, serve

    names = cfg.regions()
    if not names:
        raise ConfigError(
            "no region configured: add a [servers.<region>] table with `cdn` to the config file "
            "(or set NNNOTES_SERVERS_<REGION>_CDN)"
        )
    regions = []
    for r in names:
        langs = cfg.get_list(f"servers.{r}", "languages")
        if not langs:
            raise cfg.missing(f"servers.{r}", "languages")
        regions.append(Region(r, cfg.get(f"servers.{r}", "name") or r, cfg.cdn(r), langs, cfg))
    serve(regions, Session(cfg).bundle_key(), Session(cfg).downloads(), args.port, args.host)


# ---------------------------------------------------------------- catalog versions of the depot
def _db(cfg):
    from ..pipeline.catalogdb import CatalogDB

    return CatalogDB(Session(cfg).depot_root())


def version_line(v: dict) -> str:
    apk = v["apk"]["sha256"][:12] if v.get("apk") else "-"
    return (
        f"{v['seq']:>4}  {v['id'][:12]}  {','.join(v['labels']) or '-':<24}  {v.get('region') or '-':<6} "
        f"{v.get('language') or '-':<8} remote {v['remote']['sha256'][:12]}  apk {apk}  "
        f"resource {v.get('resourceVersion') or '-'}"
    )


def cmd_versions(args, cfg):
    from ..pipeline.catalogdb import CATALOGS

    vs = _db(cfg).versions()
    if args.json:
        report(args, {"schema": CATALOGS, "versions": vs})
        return
    write("".join(f"{version_line(v)}\n" for v in vs))
    note(f"# {len(vs)} catalog versions")


def _apk_catalog(args, cfg) -> bytes | None:
    from ..pipeline.catalogdb import apk_catalog

    if getattr(args, "apk_catalog", None):
        return Path(args.apk_catalog).read_bytes()
    apk = cfg.path("paths", "apk")
    if apk is None:
        return None
    if not apk.exists():
        raise ConfigError(f"setting paths.apk: file {apk} not found")
    return apk_catalog(apk)


def import_resource_version(given: str | None, file: Path, source: dict | None) -> str | None:
    """The resource version of an imported catalog: `given` (--resource-version), else the JP source's version, else
    the version in an international file name `catalog_<version>_<language>.bin` (none for `main`). A JP source is
    authoritative: a different `given` is an error."""
    if given is not None:
        try:
            check_catalog_version(given)
        except ValueError as e:
            raise ValueError(f"--resource-version {given!r}: {e}") from None
    if source is not None:
        if given is not None and given != source["version"]:
            raise ValueError(
                f"--resource-version {given} differs from the catalog's source version {source['version']}"
            )
        return source["version"]
    if given is not None:
        return given
    m = CATALOG_FILE.fullmatch(file.name)
    return m[1] if m and m[1] != "main" and ".." not in m[1] else None


def _version(args, v: dict) -> None:
    report(args, v, lambda v: [version_line(v)])


def cmd_import(args, cfg):
    from ..pipeline import catalogdb

    try:
        remote = Path(args.file).read_bytes()
    except OSError as e:
        usage(args, f"{args.file}: {e.strerror or e}")
    try:
        source = None
        if cfg.provider() == "jp":
            from ..source.jp import read_source

            source = read_source(args.file, remote).to_dict()
        resource = import_resource_version(args.resource_version, Path(args.file), source)
        v = _db(cfg).add(
            remote,
            _apk_catalog(args, cfg),
            label=args.label,
            region=cfg.get("catalog", "region"),
            language=cfg.get("catalog", "language"),
            resource_version=resource,
            apk_version_name=catalogdb.apk_version(cfg.path("paths", "apk")),
            source=source,
        )
    except ValueError as e:
        usage(args, str(e))
    _version(args, v)


def cmd_fetch(args, cfg):
    from ..pipeline import catalogdb
    from ..source.catalog import Catalog

    region = cfg.region()
    if cfg.provider(region) == "jp":
        from ..source.jp import open_catalog

        cat = open_catalog(cfg, region, Session(cfg).downloads(), apk=cfg.path("paths", "apk"))
        src, source = cat.sources(), cat.source
        assert source is not None  # a JP catalog names its version source
        v = _db(cfg).add(
            src["remote"],
            src.get("apk"),
            label=args.label,
            region=region,
            language=cfg.get("catalog", "language") or "ja",
            source=source.to_dict(),
            resource_version=source.version,
            apk_version_name=catalogdb.apk_version(cfg.path("paths", "apk")),
        )
        _version(args, v)
        return
    cdn = cfg.cdn(region)
    language = cfg.require("catalog", "language")
    version = Session(cfg).catalog_version(region)
    filename = Catalog.file_name(language, version=version)
    try:
        data, hash_text = catalogdb.fetch(cdn, language, version=version)
    except OSError as e:  # URLError, HTTPError, timeouts (the message has no URL)
        sys.exit(
            f"nnnotes: {filename} could not be fetched: {type(e).__name__}: "
            f"{getattr(e, 'reason', None) or getattr(e, 'code', None) or ''}".rstrip(": ")
        )
    resource = version if version != "main" else None
    try:
        v = _db(cfg).add(
            data,
            _apk_catalog(args, cfg),
            label=args.label,
            region=region,
            language=language,
            hash_text=hash_text,
            resource_version=resource,
            apk_version_name=catalogdb.apk_version(cfg.path("paths", "apk")),
        )
    except ValueError as e:
        usage(args, str(e))
    _version(args, v)


def cmd_diff(args, cfg):
    from ..pipeline import catalogdb
    from ..pipeline.depot import open_depot
    from .assets import addresses_of

    db = _db(cfg)
    vs = []
    for ref in (args.old, args.new):
        try:
            vs.append(db.get(ref))
        except (KeyError, ValueError) as e:
            usage(args, f"{ref}: {e.args[0] if e.args else e}")
    depot = open_depot(cfg)
    a, b = vs
    d = catalogdb.diff(db.index(a), db.index(b), addresses_of(depot, a["id"]), addresses_of(depot, b["id"]))
    if args.json:
        report(args, d)
    else:
        write(catalogdb.format_diff(d))


def register(sub) -> None:
    cs = group(sub, "catalog", "the region's catalog: its keys, a local browser, and the catalog versions of the depot")
    c = command(cs, "keys", cmd_keys, "the addressable keys of the catalog (the total on standard error)")
    c.add_argument("--prefix", default="", help="only the keys that start with this prefix")
    c.add_argument("--limit", type=int, default=200, help="at most N keys (default 200; 0: all)")
    c = command(cs, "browse", cmd_browse, "browse the configured regions' catalogs and bundles in a local web page")
    c.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    c.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    command(cs, "versions", cmd_versions, "the catalog versions imported into the depot (--json: nnnotes.catalogs/1)")
    c = command(cs, "import", cmd_import, "import a catalog file (with the APK's catalog when [paths] apk is set)")
    c.add_argument("file", help="catalog_<version>_<language>.bin")
    c.add_argument("--label", help="label of the version (default: its resource version, else the sha256 prefix)")
    c.add_argument("--apk-catalog", help="the APK's catalog.bin (default: read from [paths] apk)")
    c.add_argument(
        "--resource-version",
        help="the game's resource version of this catalog (default: the JP source's version, else the "
        "version in the file name catalog_<version>_<language>.bin)",
    )
    c = command(cs, "fetch", cmd_fetch, "fetch the region's current catalog and import it into the depot")
    c.add_argument("--label", help="label of the version (default: the resource version when an API root is set)")
    c = command(
        cs,
        "diff",
        cmd_diff,
        "keys, bundles, raw files (and objects) that differ between two catalog versions of the depot "
        "(--json: nnnotes.catalog-diff/1)",
    )
    c.add_argument("old", help="label, version id or sha256 prefix")
    c.add_argument("new", help="label, version id or sha256 prefix")
