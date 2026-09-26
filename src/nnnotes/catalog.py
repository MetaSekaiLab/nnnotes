"""Addressables catalog access: dependency closures, a bundle cache, remote and APK-local bundles.

Remote bundles are fetched from the region's CDN and decrypted (addressables.decrypt); local bundles (shipped inside
the APK) are read from a user-supplied base.apk. Nothing here bundles or redistributes game data: it only reads
what the user points it at into a local cache the user controls.
"""
from __future__ import annotations

import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .addressables import BundleKey, decrypt, parse, parse_locations, remote_path
from .cache import write_atomic as _write_atomic
from .config import ConfigError, apk_missing

LOCAL_PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
APK_AA_DIR = "assets/aa/"          # + "Android/<bundle>"
APK_CATALOG = "assets/aa/catalog.bin"
APK_OFFSET_BASE = 1 << 40          # namespace for APK catalog entry offsets


@dataclass(frozen=True)
class Bundle:
    offset: int
    internal_id: str
    name: str          # bare file name (embeds content hash)
    remote: bool       # True: on CDN; False: shipped inside the APK


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
    rel = remote_path(internal_id)
    if rel is None:
        rel = internal_id[len(LOCAL_PREFIX):] if internal_id.startswith(LOCAL_PREFIX) else internal_id
    head, sep, tail = rel.partition("/Android/")
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

    `cdn`: the region's CDN base (needed only to download what the cache lacks); `bundle_key`: the bundle
    decryption key (needed only for bundles not yet in the cache). Either may be a function that returns it, called
    the first time it is needed (a ConfigError it raises is raised naming the file that needed the setting).
    """

    def __init__(self, catalog_bytes: bytes, cache_dir: Path, *, cdn=None, bundle_key=None, apk: Path | None = None):
        self._settings = {"cdn": cdn, "bundle_key": bundle_key}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.apk = Path(apk) if apk else None
        self.entries = parse(catalog_bytes)
        self._sources = {"remote": catalog_bytes}
        self._locations: list[dict] | None = None
        if self.apk is not None:
            with zipfile.ZipFile(self.apk) as z:
                self._sources["apk"] = z.read(APK_CATALOG)
                for e in parse(self._sources["apk"]):
                    e["offset"] += APK_OFFSET_BASE
                    e["dependencies"] = [d + APK_OFFSET_BASE for d in e["dependencies"]]
                    self.entries.append(e)
        self._by_off = {e["offset"]: e for e in self.entries}
        self._by_key: dict[str, list[dict]] = {}
        for e in self.entries:
            self._by_key.setdefault(e["primary_key"], []).append(e)

    # --- construction ------------------------------------------------------
    @classmethod
    def load(cls, language: str, cache_dir: Path, *, cdn=None, bundle_key=None,
             apk: Path | None = None) -> "Catalog":
        """The remote catalog of `language` from the cache, downloaded from the CDN on first use (`cdn` /
        `bundle_key`: as for Catalog)."""
        cache_dir = Path(cache_dir)
        cat = cls.cache_file(language, cache_dir)
        if not cat.exists():
            cdn = _setting(cdn, cat.name)
            if not cdn:
                raise FileNotFoundError(f"{cat} not cached and no CDN base given")
            cat.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(cdn.rstrip("/") + f"/asset/Android/{cat.name}", timeout=120) as r:
                _write_atomic(cat, r.read())
        return cls(cat.read_bytes(), cache_dir, cdn=cdn, bundle_key=bundle_key, apk=apk)

    def _setting(self, name: str, needed_by: str):
        """The `cdn` / `bundle_key` given to the catalog, its function called (once) now that `needed_by` needs it."""
        v = self._settings[name] = _setting(self._settings[name], needed_by)
        return v

    @property
    def cdn(self) -> str | None:
        """The CDN base as given (None while it is given as a function not called yet)."""
        v = self._settings["cdn"]
        return None if callable(v) or not v else v.rstrip("/")

    @staticmethod
    def cache_file(language: str, cache_dir: Path) -> Path:
        """Where the remote catalog of `language` is cached."""
        return Path(cache_dir) / f"catalog_main_{language}.bin"

    # --- lookup ------------------------------------------------------------
    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._by_key if k.startswith(prefix))

    def has(self, key: str) -> bool:
        return key in self._by_key

    def entries_for(self, key: str) -> list[dict]:
        return list(self._by_key.get(key, []))

    def _entry(self, key: str) -> dict:
        ents = self._by_key.get(key)
        if not ents:
            raise KeyError(key)
        # Prefer the asset location (Assets/...) over a bundle alias.
        for e in ents:
            if e["internal_id"].startswith("Assets/") or e["internal_id"].startswith("Packages/"):
                return e
        return ents[0]

    @staticmethod
    def _as_bundle(e: dict) -> Bundle | None:
        iid = e["internal_id"]
        if iid.endswith(".bundle") and remote_path(iid) is not None:
            return Bundle(e["offset"], iid, iid.rsplit("/", 1)[1], remote=True)
        if iid.startswith(LOCAL_PREFIX) and iid.endswith(".bundle"):
            return Bundle(e["offset"], iid, iid.rsplit("/", 1)[1], remote=False)
        return None

    def sources(self) -> dict[str, bytes]:
        """The catalog files this catalog was made of: {"remote": bytes, "apk": bytes (with an APK)}."""
        return dict(self._sources)

    def locations(self) -> list[dict]:
        """Every location of the remote catalog and (with an APK) of the APK catalog, fully decoded
        (addressables.parse_locations), with `catalog` ("remote" / "apk") and `kind` (location_kind). Offsets and
        dependencies are those of `entries` (APK ones in their own namespace)."""
        if self._locations is None:
            out = []
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
            b = self._as_bundle(e)
            if b is not None:
                out.setdefault(b.name, b)
        return [out[n] for n in sorted(out)]

    def raw_files(self) -> list[dict]:
        """Every raw file the catalogs name (a CDN or APK file that is not a bundle, e.g. CRI ACB/AWB/USM data), one
        entry per file name (file_name), sorted by name, preferring the APK catalog's as bundles() does. Remote ones
        are what fetch_raw takes."""
        out: dict[str, dict] = {}
        for e in sorted(self.entries, key=lambda e: (e["offset"] < APK_OFFSET_BASE, e["offset"])):
            if location_kind(e["internal_id"]) == "raw":
                out.setdefault(file_name(e["internal_id"]), e)
        return [out[n] for n in sorted(out)]

    def resolve(self, key: str) -> list[Bundle]:
        """Full bundle closure for an addressable key (BFS over dependencies)."""
        seen: set[int] = set()
        out: dict[int, Bundle] = {}
        stack = [self._entry(key)["offset"]]
        while stack:
            off = stack.pop()
            if off in seen:
                continue
            seen.add(off)
            e = self._by_off.get(off)
            if e is None:
                continue
            b = self._as_bundle(e)
            if b is not None:
                out.setdefault(b.offset, b)
            for d in e["dependencies"]:
                if d not in seen:
                    stack.append(d)
        return list(out.values())

    # --- fetch -------------------------------------------------------------
    def cached(self, b: Bundle) -> Path | None:
        """The file fetch(b) returns when the bundle is in the cache already, else None."""
        dst = self.cache_dir / "bundles" / b.name
        return dst if dst.is_file() and dst.stat().st_size > 0 else None

    def cached_raw(self, e: dict) -> Path | None:
        """The file fetch_raw(e) returns when it is in the cache already, else None."""
        rel = remote_path(e["internal_id"])
        dst = self.cache_dir / "raw" / rel.lstrip("/") if rel is not None else None
        return dst if dst is not None and dst.is_file() and dst.stat().st_size > 0 else None

    def fetch(self, b: Bundle) -> Path:
        """Local path to the decrypted bundle (CDN download or APK read)."""
        dst = self.cache_dir / "bundles" / b.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size > 0:
            return dst
        if b.remote:
            url = self._url(b.internal_id)
            key = self._setting("bundle_key", b.name)   # before the download: a missing key fails first
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
        else:
            if self.apk is None:
                raise apk_missing(f"bundle {b.name}")
            rel = b.internal_id[len(LOCAL_PREFIX):].lstrip("/")
            with zipfile.ZipFile(self.apk) as z:
                data = z.read(APK_AA_DIR + rel)
            key = self._setting("bundle_key", b.name) if data[:7] != b"UnityFS" else None
        _write_atomic(dst, _unityfs(data, b.name, key))
        return dst

    def _url(self, internal_id: str) -> str:
        name = internal_id.rsplit('/', 1)[-1]
        cdn = self._setting("cdn", name)
        if not cdn:
            raise RuntimeError(f"{name} not cached and no CDN base given")
        return cdn.rstrip("/") + remote_path(internal_id)

    def fetch_key(self, key: str) -> list[Path]:
        """Every bundle of the key's closure; APK-local ones only when an APK is set."""
        return [self.fetch(b) for b in self.resolve(key) if b.remote or self.apk]

    def fetch_raw(self, e: dict) -> Path:
        """A non-bundle CDN asset (e.g. CRI ACB/AWB data) as stored -- no decryption."""
        iid = e["internal_id"]
        rel = remote_path(iid)
        if rel is None:
            raise ValueError(f"not a CDN asset: {iid}")
        dst = self.cache_dir / "raw" / rel.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not (dst.exists() and dst.stat().st_size > 0):
            with urllib.request.urlopen(self._url(iid), timeout=120) as r:
                _write_atomic(dst, r.read())
        return dst

    def apk_bundle(self, name_contains: str) -> Path:
        """An APK-local addressable bundle whose file name contains the substring."""
        if self.apk is None:
            raise apk_missing("an APK bundle")
        with zipfile.ZipFile(self.apk) as z:
            names = [n for n in z.namelist()
                     if n.startswith(APK_AA_DIR) and n.endswith(".bundle")
                     and name_contains in n.rsplit("/", 1)[1]]
            if len(names) != 1:
                raise KeyError(f"{name_contains!r}: {len(names)} APK bundles match")
            name = names[0].rsplit("/", 1)[1]
            dst = self.cache_dir / "bundles" / name
            if not (dst.exists() and dst.stat().st_size > 0):
                dst.parent.mkdir(parents=True, exist_ok=True)
                data = z.read(names[0])
                key = self._setting("bundle_key", name) if data[:7] != b"UnityFS" else None
                _write_atomic(dst, _unityfs(data, name, key))
        return dst


def _setting(v, needed_by: str):
    """`v`, or what it returns when it is a function; its ConfigError names `needed_by` (what is not cached)."""
    if not callable(v):
        return v
    try:
        return v()
    except ConfigError as e:
        raise ConfigError(f"{needed_by} is not in the cache: {e}") from None
