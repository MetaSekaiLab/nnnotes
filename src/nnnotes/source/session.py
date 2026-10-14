"""The data sources of one configuration: the catalog, the decoded master data, the APK and the keys.

A Session reads a setting when a command first needs it, and names the setting in a ConfigError when it is missing
or wrong. The game API answers a Session needs (the master version, the resource version of an international
catalog) come from the region's `[servers.<region>] api`; JP regions go through jp.Client.
"""

from __future__ import annotations

from pathlib import Path

from . import gameapi
from .addressables import BundleKey
from .catalog import Catalog
from .config import Config, ConfigError, check_catalog_version
from .master import MasterKey

__all__ = ["Session"]


class Session:
    """The data sources `cfg` names. `region` arguments select another configured region; None: `[catalog] region`."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def bundle_key(self) -> BundleKey:
        """`[bundle] key` and `[bundle] nonce_seed`."""
        return BundleKey(self.cfg.hex("bundle", "key", 16), self.cfg.hex("bundle", "nonce_seed"))

    def master_key(self) -> MasterKey:
        """`[master] key` and `[master] iv`."""
        return MasterKey(self.cfg.hex("master", "key", 32), self.cfg.hex("master", "iv", 32))

    def path(self, section: str, key: str, kind: str = "file") -> Path | None:
        """The path a setting names, checked to exist (`kind` "file" or "directory"; an APK may be a directory of
        splits); None when the setting is unset."""
        p = self.cfg.path(section, key)
        if key == "apk" and p is not None and p.is_dir():
            return p
        if p is not None and not (p.is_file() if kind == "file" else p.is_dir()):
            raise ConfigError(f"setting {section}.{key}: {kind} {p} not found")
        return p

    def required_path(self, section: str, key: str, kind: str = "file") -> Path:
        """The path a required setting names, checked to exist (a ConfigError naming the setting when it is unset)."""
        self.cfg.require_path(section, key)
        p = self.path(section, key, kind)
        assert p is not None  # require_path: the setting is set
        return p

    def depot_root(self) -> Path:
        """The depot directory: `[paths] depot`, required."""
        return self.cfg.require_path("paths", "depot")

    def downloads(self) -> Path:
        """The directory of the depot that holds the downloaded game files (its downloads/ directory)."""
        return self.depot_root() / "downloads"

    def apk(self, region: str | None = None) -> Path:
        """`[paths] apk` of `region`, required."""
        cfg = self.cfg.for_region(region) if region else self.cfg
        return Session(cfg).required_path("paths", "apk")

    def master_dir(self, region: str | None = None) -> Path:
        """The decoded master data of `region`: the --master flag, else `[servers.<region>] master`, else
        `[paths] master`."""
        section, key = self.cfg.master(region or self.cfg.get("catalog", "region"))
        return self.required_path(section, key, "directory")

    def master_version(self, region: str | None = None, *, timeout: float = gameapi.TIMEOUT) -> gameapi.MasterVersion:
        """The master data version `region` serves now (its `[servers.<region>] api`)."""
        region = region or self.cfg.region()
        if self.cfg.provider(region) == "jp":
            from .jp import Client

            return Client(self.cfg, region).observe(timeout=timeout).version
        section = f"servers.{region}"
        root = gameapi.api_root(self.cfg, section)
        return gameapi.fetch_master_version(
            root, gameapi.client_version(self.cfg, region), timeout=timeout, setting=f"[{section}] api"
        )

    def catalog_version(self, region: str | None = None) -> str:
        """The resource version of an international catalog: the --catalog-version flag, the regional or global pin,
        the resource version the region's API serves, else `main`."""
        region = region or self.cfg.get("catalog", "region")
        pin = self.cfg.catalog_pin(region)
        if pin is not None:
            value, section, key = pin
        elif region and self.cfg.has(f"servers.{region}", "api"):
            value, section, key = self.master_version(region).resource_version, f"servers.{region}", "api"
        else:
            value, section, key = "main", "catalog", "version"
        try:
            return check_catalog_version(value)
        except ValueError as error:
            raise ConfigError(f"setting {section}.{key}: {error}") from None

    def catalog(self, bundles: bool = True, region: str | None = None) -> Catalog:
        """The catalog of `[catalog] language` (merged with the APK's when `[paths] apk` is set), fetching from the CDN
        of `region`. International versioned catalogs are isolated by CDN root and resource version. `bundles`:
        bundles will be fetched; else only the catalog is read. An explicit `[paths] catalog` file bypasses
        discovery; a version pin bypasses the API but needs the CDN root to identify the downloads. The bundle key is
        only read for a missing encrypted bundle."""
        cfg = self.cfg.for_region(region) if region else self.cfg
        session = Session(cfg)
        downloads = session.downloads()
        catbin = session.path("paths", "catalog")
        apk = session.path("paths", "apk")
        key = (lambda: session.bundle_key()) if bundles else None
        if cfg.provider() == "jp":
            from .jp import open_catalog

            return open_catalog(cfg, cfg.region(), downloads, catalog_file=catbin, bundle_key=key, apk=apk)
        cdn = (lambda: cfg.cdn(region or cfg.region())) if bundles or catbin is None else None
        if catbin is not None:
            return Catalog(catbin.read_bytes(), downloads, cdn=cdn, bundle_key=key, apk=apk)
        language = cfg.require("catalog", "language")
        return Catalog.load(language, downloads, cdn=cdn, bundle_key=key, apk=apk, version=session.catalog_version())
