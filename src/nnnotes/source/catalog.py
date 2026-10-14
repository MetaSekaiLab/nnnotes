"""Addressables catalog access: dependency closures, downloaded files, remote and APK-local bundles.

Remote bundles are fetched from the region's CDN and decrypted (addressables.decrypt); local bundles (shipped inside
the APK) are read from a user-supplied base.apk. Nothing here bundles or redistributes game data: it only reads
what the user points it at into a directory of downloads the user controls (the depot's downloads/).
"""

from __future__ import annotations

import hashlib
import http.client
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, TypeVar, cast

from ..core.cache import write_atomic as _write_atomic
from ..core.downloads import touch
from .addressables import (
    REMOTE_PREFIX,
    BundleKey,
    Location,
    decrypt,
    parse,
    parse_locations,
    relative_path,
    remote_path,
)
from .apkset import ApkSet
from .config import ConfigError, apk_missing, check_catalog_version

if TYPE_CHECKING:
    from .jp import Client, Source

__all__ = ["Bundle", "Catalog"]

LOCAL_PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
APK_AA_DIR = "assets/aa/"  # + "Android/<bundle>"
APK_CATALOG = "assets/aa/catalog.bin"
APK_OFFSET_BASE = 1 << 40  # namespace for APK catalog entry offsets


T = TypeVar("T")
# a setting given as its value, or as a function that reads it when a file not downloaded yet needs it
Lazy: TypeAlias = "T | Callable[[], T] | None"


@dataclass(frozen=True)
class Bundle:
    offset: int
    internal_id: str
    name: str  # bare file name (embeds content hash)
    remote: bool  # True: on CDN; False: shipped inside the APK


def location_kind(internal_id: str) -> str:
    """What a location names: "bundle" or "raw" (a file on the CDN or in the APK, `.bundle` or not), "asset" (a path
    inside a bundle), else "other"."""
    if remote_path(internal_id) is not None or internal_id.startswith(LOCAL_PREFIX):
        return "bundle" if internal_id.endswith(".bundle") else "raw"
    if internal_id.startswith(("Assets/", "Packages/")):
        return "asset"
    return "other"


def file_name(internal_id: str) -> str:
    """The name of a bundle or raw file location: its path below the platform directory (`Android/`), which for a
    bundle is its bare file name."""
    if internal_id.startswith(REMOTE_PREFIX):
        path = relative_path(internal_id[len(REMOTE_PREFIX) :])
        return path.rsplit("/", 1)[-1] if path.endswith(".bundle") else path
    rel = remote_path(internal_id)
    if rel is None:
        rel = internal_id[len(LOCAL_PREFIX) :] if internal_id.startswith(LOCAL_PREFIX) else internal_id
    _, sep, tail = rel.partition("/Android/")
    return tail if sep else rel.rsplit("/", 1)[-1]


def _unityfs(data: bytes, name: str, key: BundleKey | None) -> bytes:
    if data[:7] == b"UnityFS":
        return data
    if key is None:
        raise RuntimeError(f"bundle {name} is encrypted and no bundle key was given")
    return decrypt(data, name, key)


class Catalog:
    """Remote content catalog, merged with the APK's local catalog when an APK is given.

    The game loads both: the APK catalog (embedded assets, e.g. ADV settings and
    shared prefabs) and the remote catalog (downloadable content). Entry offsets
    are per-catalog, so APK entries are moved into their own offset namespace.

    `download_dir`: the directory of this catalog's downloaded files. `cdn`: the region's CDN base (needed only to
    download what is not downloaded yet); `bundle_key`: the bundle decryption key (needed only for bundles not
    downloaded yet). Either may be a function that returns it, called
    the first time it is needed (a ConfigError it raises is raised naming the file that needed the setting).
    `apk_catalog`: the stored catalog to replay without opening an APK; JP local reads verify it before extraction.
    """

    def __init__(
        self,
        catalog_bytes: bytes,
        download_dir: Path,
        *,
        cdn: Lazy[str] = None,
        bundle_key: Lazy[BundleKey] = None,
        apk: Path | None = None,
        source: Source | None = None,
        client: Client | None = None,
        apk_catalog: bytes | None = None,
        resource_version: str | None = None,
    ):
        self._cdn, self._bundle_key = cdn, bundle_key
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.apk = Path(apk) if apk else None
        self.source, self.client = source, client
        self.resource_version = resource_version
        self._sources = {"remote": catalog_bytes}
        self._locations: list[Location] | None = None
        self._parsed: tuple[list[Location], dict[int, Location], dict[str, list[Location]]] | None = None
        if apk_catalog is not None:
            self._sources["apk"] = apk_catalog
        elif self.apk is not None:
            with ApkSet(self.apk) as z:
                self._sources["apk"] = z.read(APK_CATALOG)

    def check_apk(self, archive: ApkSet) -> None:
        """Before extracting a JP local file, verify the APK against this catalog's snapshot."""
        if self.source is not None and "apk" in self._sources:
            try:
                matches = archive.read(APK_CATALOG) == self._sources["apk"]
            except KeyError:
                matches = False
            if not matches:
                raise ConfigError("JP historical catalog needs the APK set it was imported with")

    def local_download_dir(self) -> Path:
        """JP embedded files also depend on the APK catalog, independently of the CDN snapshot."""
        if self.source is not None and "apk" in self._sources:
            return self.download_dir / "apk" / hashlib.sha256(self._sources["apk"]).hexdigest()
        return self.download_dir

    def _parse(self) -> tuple[list[Location], dict[int, Location], dict[str, list[Location]]]:
        """(entries, by offset, by primary key), parsed the first time a lookup needs them (a command that only
        wants the catalog files, sources(), does not pay for it)."""
        if self._parsed is None:
            entries = parse(self._sources["remote"])
            if "apk" in self._sources:
                for e in parse(self._sources["apk"]):
                    e["offset"] += APK_OFFSET_BASE
                    e["dependencies"] = [d + APK_OFFSET_BASE for d in e["dependencies"]]
                    entries.append(e)
            by_key: dict[str, list[Location]] = {}
            for e in entries:
                by_key.setdefault(e["primary_key"], []).append(e)
            self._parsed = (entries, {e["offset"]: e for e in entries}, by_key)
        return self._parsed

    @property
    def entries(self) -> list[Location]:
        return self._parse()[0]

    @property
    def _by_off(self) -> dict[int, Location]:
        return self._parse()[1]

    @property
    def _by_key(self) -> dict[str, list[Location]]:
        return self._parse()[2]

    # --- construction ------------------------------------------------------
    @classmethod
    def load(
        cls,
        language: str,
        download_dir: Path,
        *,
        cdn: Lazy[str] = None,
        bundle_key: Lazy[BundleKey] = None,
        apk: Path | None = None,
        version: str = "main",
    ) -> Catalog:
        """The remote catalog of `language` from `download_dir`, downloaded from the CDN on first use (`cdn` /
        `bundle_key`: as for Catalog). A versioned catalog is isolated by CDN root and version
        (international/<sha256 of the CDN root>/<version>/); main is at the top of `download_dir`. Missing versioned
        files never fall back to main."""
        download_dir = Path(download_dir)
        filename = cls.file_name(language, version=version)
        if version != "main":
            cdn = _setting(cdn, filename)
            if not cdn:
                raise ConfigError("a versioned catalog needs its CDN root to identify its downloads")
            download_dir = (
                download_dir / "international" / hashlib.sha256(cdn.rstrip("/").encode()).hexdigest() / version
            )
        cat = download_dir / filename
        if not cat.exists():
            root = _setting(cdn, cat.name)
            if not root:
                raise FileNotFoundError(f"{cat} not downloaded and no CDN base given")
            cat.parent.mkdir(parents=True, exist_ok=True)
            data = download(root.rstrip("/") + f"/asset/Android/{cat.name}")
            parse(data)  # Never install a corrupt/error response as a reusable catalog.
            _write_atomic(cat, data)
        return cls(
            cat.read_bytes(),
            download_dir,
            cdn=cdn,
            bundle_key=bundle_key,
            apk=apk,
            resource_version=version if version != "main" else None,
        )

    def _cdn_for(self, needed_by: str) -> str | None:
        """The `cdn` given to the catalog, its function called (once) now that `needed_by` needs it."""
        self._cdn = _setting(self._cdn, needed_by)
        return self._cdn

    def _bundle_key_for(self, needed_by: str) -> BundleKey | None:
        """The `bundle_key` given to the catalog, its function called (once) now that `needed_by` needs it."""
        self._bundle_key = _setting(self._bundle_key, needed_by)
        return self._bundle_key

    @property
    def cdn(self) -> str | None:
        """The CDN base as given (None while it is given as a function not called yet)."""
        v = self._cdn
        return None if callable(v) or not v else v.rstrip("/")

    @staticmethod
    def file_name(language: str, *, version: str = "main") -> str:
        """The file name of the remote catalog of `language` (on the CDN and in a download directory)."""
        check_catalog_version(version)
        check_catalog_version(language)
        return f"catalog_{version}_{language}.bin"

    # --- lookup ------------------------------------------------------------
    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._by_key if k.startswith(prefix))

    def has(self, key: str) -> bool:
        return key in self._by_key

    def entries_for(self, key: str) -> list[Location]:
        return list(self._by_key.get(key, []))

    def entry(self, key: str) -> Location:
        """The location of `key`: its asset location (internal id under Assets/ or Packages/) when it has one, else
        its first location. KeyError when the catalog lacks the key."""
        ents = self._by_key.get(key)
        if not ents:
            raise KeyError(key)
        for e in ents:
            if e["internal_id"].startswith("Assets/") or e["internal_id"].startswith("Packages/"):
                return e
        return ents[0]

    def dependencies(self, key: str) -> list[Location]:
        """The locations the location of `key` (entry) depends on, in its dependency order (those the catalogs
        hold)."""
        return [self._by_off[d] for d in self.entry(key)["dependencies"] if d in self._by_off]

    @staticmethod
    def as_bundle(e: Location) -> Bundle | None:
        """The bundle file a location names (a remote or APK-local .bundle), else None."""
        iid = e["internal_id"]
        if iid.endswith(".bundle") and remote_path(iid) is not None:
            return Bundle(e["offset"], iid, iid.rsplit("/", 1)[1], remote=True)
        if iid.startswith(LOCAL_PREFIX) and iid.endswith(".bundle"):
            return Bundle(e["offset"], iid, iid.rsplit("/", 1)[1], remote=False)
        return None

    def sources(self) -> dict[str, bytes]:
        """The catalog files this catalog was made of: {"remote": bytes, "apk": bytes (with an APK)}."""
        return dict(self._sources)

    def locations(self) -> list[Location]:
        """Every location of the remote catalog and (with an APK) of the APK catalog, fully decoded
        (addressables.parse_locations), with `catalog` ("remote" / "apk") and `kind` (location_kind). Offsets and
        dependencies are those of `entries` (APK ones in their own namespace)."""
        if self._locations is None:
            out: list[Location] = []
            for name, base in (("remote", 0), ("apk", APK_OFFSET_BASE)):
                if name not in self._sources:
                    continue
                for e in parse_locations(self._sources[name]):
                    e["offset"] += base
                    e["dependencies"] = [d + base for d in e["dependencies"]]
                    e["catalog"] = name
                    e["kind"] = location_kind(e["internal_id"])
                    out.append(e)
            self._locations = out
        return [dict(e) for e in self._locations]

    def bundles(self) -> list[Bundle]:
        """Every bundle file the catalogs name (remote and APK-local), one per file name, sorted by name. A file the
        APK catalog lists gets the APK catalog's location (the remote catalog's references to APK files may describe
        another build), else its first location."""
        out: dict[str, Bundle] = {}
        for e in sorted(self.entries, key=lambda e: (e["offset"] < APK_OFFSET_BASE, e["offset"])):
            b = self.as_bundle(e)
            if b is not None:
                out.setdefault(b.name, b)
        return [out[n] for n in sorted(out)]

    def raw_files(self) -> list[Location]:
        """Every raw file the catalogs name (a CDN or APK file that is not a bundle, e.g. CRI ACB/AWB/USM data), one
        entry per file name (file_name), sorted by name, preferring the APK catalog's as bundles() does. Remote ones
        are what fetch_raw takes."""
        out: dict[str, Location] = {}
        for e in sorted(self.entries, key=lambda e: (e["offset"] < APK_OFFSET_BASE, e["offset"])):
            if location_kind(e["internal_id"]) == "raw":
                out.setdefault(file_name(e["internal_id"]), e)
        return [out[n] for n in sorted(out)]

    def resolve(self, key: str) -> list[Bundle]:
        """Full bundle closure for an addressable key (BFS over dependencies)."""
        seen: set[int] = set()
        out: dict[int, Bundle] = {}
        stack = [self.entry(key)["offset"]]
        while stack:
            off = stack.pop()
            if off in seen:
                continue
            seen.add(off)
            e = self._by_off.get(off)
            if e is None:
                continue
            b = self.as_bundle(e)
            if b is not None:
                out.setdefault(b.offset, b)
            for d in e["dependencies"]:
                if d not in seen:
                    stack.append(d)
        return list(out.values())

    # --- fetch -------------------------------------------------------------
    def downloaded(self, b: Bundle) -> Path | None:
        """The file fetch(b) returns when the bundle is downloaded already (marked as used now), else None."""
        dst = (self.download_dir if b.remote else self.local_download_dir()) / "bundles" / b.name
        return _used(dst)

    def downloaded_raw(self, e: Location) -> Path | None:
        """The file fetch_raw(e) returns when it is downloaded already (marked as used now), else None."""
        rel = remote_path(e["internal_id"])
        return _used(self.download_dir / "raw" / rel.lstrip("/")) if rel is not None else None

    def fetch(self, b: Bundle) -> Path:
        """Local path to the decrypted bundle (CDN download or APK read), marked as used now."""
        dst = (self.download_dir if b.remote else self.local_download_dir()) / "bundles" / b.name
        if _used(dst) is not None:
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        if b.remote:
            url = self._url(b.internal_id)
            key = self._bundle_key_for(b.name)  # before the download: a missing key fails first
            data = self._download(url)
        else:
            if self.apk is None:
                raise apk_missing(f"bundle {b.name}")
            rel = b.internal_id[len(LOCAL_PREFIX) :].lstrip("/")
            with ApkSet(self.apk) as z:
                self.check_apk(z)
                data = z.read(APK_AA_DIR + rel)
            key = self._bundle_key_for(b.name) if data[:7] != b"UnityFS" else None
        _write_atomic(dst, _unityfs(data, b.name, key))
        return dst

    def _url(self, internal_id: str) -> str:
        if self.source is not None:
            return self.source.url(internal_id)
        name = internal_id.rsplit("/", 1)[-1]
        cdn = self._cdn_for(name)
        if not cdn:
            raise RuntimeError(f"{name} not downloaded and no CDN base given")
        path = remote_path(internal_id)
        if path is None:
            raise ValueError(f"{internal_id}: not a remote location")
        return cdn.rstrip("/") + path

    def _download(self, url: str) -> bytes:
        if self.source is not None:
            if self.client is None:
                raise ConfigError("JP downloads require a configured JP client")
            return self.client.get(url, source=self.source)
        return download(url)

    def fetch_key(self, key: str, skip: Collection[str] = frozenset()) -> list[Path]:
        """Every bundle of the key's closure but those named in `skip`; APK-local ones only when an APK is set."""
        return [self.fetch(b) for b in self.resolve(key) if (b.remote or self.apk) and b.name not in skip]

    def bundle_of(self, key: str) -> Bundle:
        """The bundle that holds the asset `key`: the first bundle among its location's dependencies (a bundle key:
        that bundle)."""
        e = self.entry(key)
        own = self.as_bundle(e)
        if own is not None:
            return own
        for d in e["dependencies"]:
            de = self._by_off.get(d)
            b = self.as_bundle(de) if de is not None else None
            if b is not None:
                return b
        raise KeyError(f"{key}: no bundle among its dependencies")

    def fetch_raw(self, e: Location) -> Path:
        """A non-bundle CDN asset (e.g. CRI ACB/AWB data) as stored -- no decryption; marked as used now."""
        iid = e["internal_id"]
        rel = remote_path(iid)
        if rel is None:
            raise ValueError(f"not a CDN asset: {iid}")
        dst = self.download_dir / "raw" / rel.lstrip("/")
        if _used(dst) is None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(dst, self._download(self._url(iid)))
        return dst

    def apk_bundle(self, name_contains: str) -> Path:
        """An APK-local addressable bundle whose file name contains the substring."""
        if self.apk is None:
            raise apk_missing("an APK bundle")
        with ApkSet(self.apk) as z:
            names = [
                n
                for n in z.namelist()
                if n.startswith(APK_AA_DIR) and n.endswith(".bundle") and name_contains in n.rsplit("/", 1)[1]
            ]
            if len(names) != 1:
                raise KeyError(f"{name_contains!r}: {len(names)} APK bundles match")
            name = names[0].rsplit("/", 1)[1]
            dst = self.local_download_dir() / "bundles" / name
            if _used(dst) is None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                self.check_apk(z)
                data = z.read(names[0])
                key = self._bundle_key_for(name) if data[:7] != b"UnityFS" else None
                _write_atomic(dst, _unityfs(data, name, key))
        return dst


_KEPT = threading.local()  # per thread: {(scheme, host): an open connection}
_AGENT = "Python-urllib/{}.{}".format(*sys.version_info[:2])  # what urllib.request sends


def download(url: str, timeout: float = 120) -> bytes:
    """The body of `url`. HTTP(S) goes over one connection per thread and host, kept open between files (a new
    connection per file costs more than a small bundle's transfer); a stale kept connection is replaced once. Other
    schemes, a proxy from the environment, and any answer but a complete 200 (a redirect, an error) go through
    urllib.request, as before, which follows or raises."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or _proxied(parts):
        return _urlopen(url, timeout)
    conns: dict[tuple[str, str], http.client.HTTPConnection] = _KEPT.__dict__.setdefault("conns", {})
    key = (parts.scheme, parts.netloc)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    for _ in range(2):
        conn, kept = conns.pop(key, None), True
        if conn is None:
            kept = False
            make = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            conn = make(parts.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": _AGENT})
            r = conn.getresponse()
            body = r.read()
        except (http.client.HTTPException, OSError):
            conn.close()
            if kept:
                continue  # closed by the server while idle: once more, on a new one
            break
        if r.status != 200 or r.will_close:
            conn.close()
        else:
            conns[key] = conn
        if r.status == 200:
            return body
        break
    return _urlopen(url, timeout)


def _proxied(parts: urllib.parse.SplitResult) -> bool:
    proxies = urllib.request.getproxies()
    return parts.scheme in proxies and not urllib.request.proxy_bypass(parts.hostname or "")


def _urlopen(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _used(path: Path) -> Path | None:
    """`path` when it is a downloaded file (not empty), marked as used now (the downloads are trimmed least recently
    used first, core.downloads); else None."""
    try:
        if path.stat().st_size > 0:
            touch(path)
            return path
    except OSError:
        pass
    return None


def _setting(v: Lazy[T], needed_by: str) -> T | None:
    """`v`, or what it returns when it is a function; its ConfigError names `needed_by` (what is not downloaded)."""
    if not callable(v):
        return cast("T | None", v)
    try:
        return cast("T", v())
    except ConfigError as e:
        raise ConfigError(f"{needed_by} is not downloaded: {e}") from None
