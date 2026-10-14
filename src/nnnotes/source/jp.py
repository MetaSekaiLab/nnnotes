"""Japanese release: anonymous Version discovery and snapshot-bound CDN downloads.

Addresses and client versions come from Config. Credentials remain in a Client, never in
source metadata, cache records, task descriptions or exception messages.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from ..core.cache import write_atomic
from . import gameapi
from .addressables import BundleKey, parse_header, relative_path
from .catalog import Catalog
from .config import Config, ConfigError

__all__ = ["Client", "Observation", "Source", "origin"]

PLACEHOLDER = "{Fwk.Resource.RemoteAssetDir}/"
CATALOG_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 1024 * 1024 * 1024


def numeric_version(value: object) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        raise ValueError("invalid numeric version")
    parts = tuple(map(int, value.split(".")))
    if max(parts) > 2147483647:
        raise ValueError("numeric version component is too large")
    return parts + (0,) * (4 - len(parts))


def master_version(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}/[0-9a-f]{32}", value):
        raise ValueError("invalid JP master version")
    return value


def select_asset(raw: str | None, client: str) -> tuple[str, str] | None:
    """Select the largest applicable live minimum; no top-level fallback when live has no match."""
    if not raw:
        return None
    try:
        data: object = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        data = cast("dict[str, object]", data)
        live = data.get("live")
        if live:
            if not isinstance(live, list):
                raise ValueError()
            current = numeric_version(client)
            candidates: list[tuple[tuple[int, ...], dict[str, object]]] = []
            for entry in cast("list[object]", live):
                if not isinstance(entry, dict):
                    continue
                entry = cast("dict[str, object]", entry)
                try:
                    minimum = numeric_version(entry.get("minClientVersion"))
                except ValueError:
                    continue
                if minimum <= current:
                    candidates.append((minimum, entry))
            if not candidates:
                return None
            data = max(candidates, key=lambda x: x[0])[1]
        version, digest = data.get("version"), data.get("Android")
        numeric_version(version)
        if not isinstance(version, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{32}", digest):
            raise ValueError()
        return version, digest
    except (ValueError, TypeError):
        raise gameapi.GameApiError("JP Version: invalid asset metadata") from None


def origin(value: str) -> str:
    """The origin (`scheme://host[:port]`, without a default port) of an HTTPS URL with no path, query or
    credentials (HTTP only on a loopback host); a ConfigError for any other value."""
    try:
        u = urlsplit(value)
        if (
            not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in ("", "/")
            or u.scheme not in ("https", "http")
            or u.scheme == "http"
            and u.hostname not in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError()
        port = u.port
        host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
        return f"{u.scheme}://{host}" + (f":{port}" if port and port != (443 if u.scheme == "https" else 80) else "")
    except (ValueError, TypeError, AttributeError):
        raise ConfigError(
            "JP origin must be HTTPS with no path or credentials (HTTP loopback is allowed for tests)"
        ) from None


@dataclass(frozen=True)
class Source:
    provider: str
    version: str
    hash: str
    cdn: str = field(repr=False)
    platform: str = "Android"

    def __post_init__(self) -> None:
        if self.provider != "jp" or self.platform != "Android":
            raise ValueError("unsupported JP asset source")
        numeric_version(self.version)
        if not re.fullmatch(r"[0-9a-f]{32}", self.hash):
            raise ValueError("invalid JP asset hash")
        if origin(self.cdn) != self.cdn:
            raise ValueError("JP source CDN is not a canonical origin")

    @property
    def root(self) -> str:
        return f"{self.cdn}/asset/{self.version}/Android/{self.hash}"

    @property
    def catalog_url(self) -> str:
        return self.root + "/catalog_main.bin"

    def url(self, internal_id: str) -> str:
        if not internal_id.startswith(PLACEHOLDER):
            raise ValueError("JP resource has no remote asset placeholder")
        return self.root + "/" + relative_path(internal_id[len(PLACEHOLDER) :])

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> Source:
        if not isinstance(data, dict):
            raise ValueError("invalid JP source metadata")
        fields = cast("dict[str, Any]", data)
        if set(fields) != {"provider", "version", "hash", "cdn", "platform"}:
            raise ValueError("invalid JP source metadata")
        return cls(**fields)

    def download_dir(self, downloads: str | os.PathLike[str]) -> Path:
        """The directory of this snapshot's files in `downloads`: jp/<site>/<version>/<hash>."""
        site = hashlib.sha256(self.cdn.encode()).hexdigest()[:16]
        return Path(downloads) / "jp" / site / self.version / self.hash


@dataclass(frozen=True)
class Observation:
    version: gameapi.MasterVersion
    source: Source | None
    cdn: str = field(repr=False)
    authorization: str = field(repr=False)
    user_agent: str = field(repr=False)


class Client:
    """The JP game API and CDN of one configured region: `observe()` asks the API for the current master version,
    asset source and CDN credential; `get()` downloads a file of one source or master version with that credential,
    observing again when it is older than five minutes."""

    def __init__(self, cfg: Config, region: str):
        self.cfg, self.region = cfg.for_region(region), region
        self._observation: Observation | None = None
        self._created = 0.0
        self._lock = threading.RLock()

    def observe(self, *, timeout: float = gameapi.TIMEOUT) -> Observation:
        """Ask the API for the current Observation: master version, asset source, CDN and its credential."""
        with self._lock:
            section = f"servers.{self.region}"
            api = gameapi.api_root(self.cfg, section).strip()
            api = origin(api if "://" in api else "https://" + api)
            allowed = origin(self.cfg.cdn(self.region))
            client = gameapi.client_version(self.cfg, self.region)
            headers: dict[str, Any] = {}
            body = gameapi.call(
                api,
                gameapi.VERSION_METHOD,
                b"",
                client,
                timeout=timeout,
                setting=f"[{section}] api",
                response_metadata=headers,
            )
            try:
                version = master_version(gameapi.parse_version(body).version)
                cdn = origin(headers.get("x-sirius-env", ""))
                if cdn != allowed:
                    raise gameapi.GameApiError("JP Version: CDN differs from the configured CDN origin")
                credential = headers.get("x-sirius-cred")
                if not isinstance(credential, str) or not credential or len(credential) > 8192:
                    raise ValueError("missing CDN credential")
                asset = select_asset(headers.get("x-asset-version"), client)
            except (ValueError, ConfigError):
                raise gameapi.GameApiError("JP Version: invalid master version or CDN metadata") from None
            source = Source("jp", *asset, cdn) if asset else None
            authorization = "Basic " + base64.b64encode(("sirius:" + credential).encode()).decode()
            result = Observation(
                gameapi.MasterVersion(version, asset[0] if asset else "", asset[1] if asset else None),
                source,
                cdn,
                authorization,
                "OurNotes/" + client,
            )
            self._observation, self._created = result, time.monotonic()
            return result

    def _current(self) -> Observation:
        with self._lock:
            if self._observation is None or time.monotonic() - self._created > 300:
                return self.observe()
            return self._observation

    def get(
        self, url: str, *, source: Source | None = None, master: str | None = None, limit: int = FILE_LIMIT
    ) -> bytes:
        """Download against one immutable source. Auth refresh never changes the requested snapshot."""
        for attempt in range(2):
            observation = self._current()
            if source is not None and observation.source != source:
                raise gameapi.GameApiError(
                    "JP assets changed: refresh the catalog before downloading missing resources"
                )
            if master is not None and observation.version.version != master:
                raise gameapi.GameApiError("JP master changed: start a new master download")
            parsed = urlsplit(url)
            base = origin(f"{parsed.scheme}://{parsed.netloc}")
            prefix = source.root + "/" if source else observation.cdn + "/master/" + master_version(master) + "/"
            if base != observation.cdn or not url.startswith(prefix):
                raise gameapi.GameApiError("JP download URL is outside the selected source")
            relative_path(url[len(prefix) :])
            conn_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = conn_type(parsed.netloc, timeout=120)
            try:
                connection.request(
                    "GET",
                    parsed.path,
                    headers={"Authorization": observation.authorization, "User-Agent": observation.user_agent},
                )
                response = connection.getresponse()
                status = response.status
                if status == 200:
                    header = response.getheader("Content-Length")
                    length = None
                    if header is not None and not response.chunked:
                        if not header.isdecimal():
                            raise gameapi.GameApiError("JP download has invalid Content-Length")
                        length = int(header)
                        if length > limit:
                            raise gameapi.GameApiError("JP download exceeds the size limit")
                    data = response.read(limit + 1)
                    if len(data) > limit:
                        raise gameapi.GameApiError("JP download exceeds the size limit")
                    if length is not None and len(data) != length:
                        raise gameapi.GameApiError("JP download is truncated")
                    return data
            except (OSError, http.client.HTTPException):
                raise gameapi.GameApiError("JP CDN download failed: connection error") from None
            finally:
                connection.close()
            if status in (401, 403) and attempt == 0:
                with self._lock:
                    if self._observation is observation:
                        self.observe()
                continue
            raise gameapi.GameApiError(f"JP CDN download failed: HTTP {status}")
        raise AssertionError("unreachable")


def sidecar(path: str | os.PathLike[str]) -> Path:
    return Path(str(path) + ".source.json")


def read_source(path: str | os.PathLike[str], data: bytes) -> Source:
    try:
        doc = json.loads(sidecar(path).read_bytes())
        if doc["catalog_sha256"] != hashlib.sha256(data).hexdigest():
            raise ValueError()
        return Source.from_dict(doc["source"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ConfigError("JP catalog needs its matching .source.json sidecar (written by `catalog fetch`)") from None


def write_source(path: str | os.PathLike[str], data: bytes, source: Source) -> None:
    doc = {"catalog_sha256": hashlib.sha256(data).hexdigest(), "source": source.to_dict()}
    write_atomic(sidecar(path), (json.dumps(doc, sort_keys=True) + "\n").encode())


def open_catalog(
    cfg: Config,
    region: str,
    downloads: Path,
    *,
    catalog_file: str | os.PathLike[str] | None = None,
    bundle_key: BundleKey | Callable[[], BundleKey] | None = None,
    apk: Path | None = None,
) -> Catalog:
    """The catalog of the JP region `region`: `catalog_file` with its .source.json sidecar, else the catalog of the
    asset version the API serves now (from `downloads`, the depot's directory of downloaded files, downloaded on first
    use)."""
    client = Client(cfg, region)
    if catalog_file is not None:
        data = Path(catalog_file).read_bytes()
        source = read_source(catalog_file, data)
    else:
        source = client.observe().source
        if source is None:
            raise gameapi.GameApiError("JP Version: no Android asset version applies to this client")
        path = source.download_dir(downloads) / "catalog_main.bin"
        if path.exists():
            data = path.read_bytes()
            if not sidecar(path).exists():
                # Interrupted between the two atomic writes: fetch the same snapshot again.
                data = client.get(source.catalog_url, source=source, limit=CATALOG_LIMIT)
                parse_header(data)
                write_atomic(path, data)
                write_source(path, data, source)
            elif read_source(path, data) != source:
                raise ConfigError("JP downloaded catalog source differs from the selected version")
        else:
            data = client.get(source.catalog_url, source=source, limit=CATALOG_LIMIT)
            parse_header(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, data)
            write_source(path, data, source)
    return Catalog(
        data,
        source.download_dir(downloads),
        bundle_key=bundle_key,
        apk=apk,
        source=source,
        client=client,
    )
