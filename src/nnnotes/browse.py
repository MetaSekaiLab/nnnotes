"""A local catalog browser: the catalogs of the configured regions as directory listings, and their bundles as
decrypted downloads (`nnnotes browse`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from . import __version__
from .addressables import BundleKey, decrypt, remote_path
from .catalog import Bundle, Catalog
from .config import ConfigError
from .gameapi import GameApiError
from .jp import open_catalog
from .session import Session


def browse(entries: list[dict]) -> tuple[dict, dict]:
    """(remote bundles by offset, {asset path or `bundles/<key>`: bundle offsets}) of a parsed catalog."""
    bundles = {
        e["offset"]: e
        for e in entries
        if remote_path(e["internal_id"]) is not None and e["internal_id"].endswith(".bundle")
    }
    files: dict[str, set] = {}
    for entry in entries:
        if entry["internal_id"].startswith("Assets/"):
            ids = [i for i in entry["dependencies"] if i in bundles]
            if ids:
                files.setdefault(entry["primary_key"], set()).update(ids)
    for ident, entry in bundles.items():
        files["bundles/" + entry["primary_key"]] = {ident}
    return bundles, files


@dataclass
class Region:
    """One region of the browser: label, CDN base and catalog languages."""

    name: str
    label: str
    cdn: str = field(repr=False)
    languages: list[str]
    config: object = field(default=None, repr=False)


class Handler(BaseHTTPRequestHandler):
    """/ -> regions, /<region>/ -> languages, /<region>/<language>/<dir>/ -> listing,
    /<region>/<language>/download/<offset> -> the decrypted bundle."""

    def listing(self, label: str, rows) -> None:
        title = f"nnnotes {__version__} /" + label
        data = (
            '<!doctype html><meta charset="utf-8"><title>'
            + escape(title)
            + "</title><h1>"
            + escape(title)
            + "</h1><hr><ul>"
            + "\n".join(sorted(rows))
            + "</ul><hr>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def catalog(self, region: Region, language: str):
        key = (region.name, language)
        if key not in self.server.catalogs:
            if region.config is not None and region.config.provider(region.name) == "jp":
                cat = open_catalog(region.config, region.name, bundle_key=self.server.bundle_key)
                self.server.catalogs[key] = browse(cat.entries)
                self.server.jp_catalogs[key] = cat
                return self.server.catalogs[key]
            version = Session(region.config).catalog_version(region.name) if region.config is not None else "main"
            cat = Catalog.load(language, self.server.cache / "catalogs" / region.name, cdn=region.cdn, version=version)
            self.server.catalogs[key] = browse(cat.entries)
        return self.server.catalogs[key]

    def do_GET(self):
        regions: dict[str, Region] = self.server.regions
        path = unquote(urlsplit(self.path).path)
        parts = path.strip("/").split("/") if path.strip("/") else []
        if not parts:
            self.listing("", [f'<li><a href="/{quote(r.name)}/">{escape(r.label)}/</a></li>' for r in regions.values()])
            return
        region = regions.get(parts[0])
        if region is None:
            self.send_error(404)
            return
        if len(parts) == 1:
            self.listing(
                region.label + "/",
                ['<li><a href="/">../</a></li>']
                + [
                    f'<li><a href="/{quote(region.name)}/{quote(lang)}/">{escape(lang)}/</a></li>'
                    for lang in region.languages
                ],
            )
            return
        language = parts[1]
        if language not in region.languages:
            self.send_error(404)
            return
        try:
            bundles, files = self.catalog(region, language)
        except (GameApiError, ConfigError, ValueError):
            self.send_error(502, "Catalog could not be loaded")
            return
        base = f"/{quote(region.name)}/{quote(language)}/"
        if len(parts) > 2 and parts[2] == "download":
            ident = parts[3] if len(parts) == 4 else ""
            if not ident.isdecimal() or int(ident) not in bundles:
                self.send_error(404)
                return
            entry = bundles[int(ident)]
            name = entry["internal_id"].rsplit("/", 1)[1]
            try:
                cat = getattr(self.server, "jp_catalogs", {}).get((region.name, language))
                if cat is not None:
                    data = cat.fetch(Bundle(int(ident), entry["internal_id"], name, True)).read_bytes()
                else:
                    with urlopen(region.cdn + remote_path(entry["internal_id"]), timeout=60) as response:
                        data = decrypt(response.read(), name, self.server.bundle_key)
            except (GameApiError, ConfigError, ValueError):
                self.send_error(502, "Resource could not be downloaded")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        prefix = "/".join(parts[2:])
        prefix += "/" if prefix else ""
        parent = base + prefix.rstrip("/").rsplit("/", 1)[0] + "/" if "/" in prefix.rstrip("/") else base
        if not prefix:
            parent = f"/{quote(region.name)}/"
        rows = {f'<li><a href="{quote(parent)}">../</a></li>'}
        for name, ids in files.items():
            if not name.startswith(prefix):
                continue
            tail = name[len(prefix) :]
            if "/" in tail:
                directory = prefix + tail.split("/")[0] + "/"
                rows.add(f'<li><a href="{base}{quote(directory)}">{escape(tail.split("/")[0])}/</a></li>')
            else:
                for ident in sorted(ids):
                    label = tail if len(ids) == 1 else tail + " — " + bundles[ident]["primary_key"]
                    rows.add(f'<li><a href="{base}download/{ident}">{escape(label)}</a></li>')
        self.listing(region.label + "/" + language + "/" + prefix, rows)


def serve(regions: list[Region], bundle_key: BundleKey, cache: Path, port: int, host: str = "127.0.0.1") -> None:
    """Browse the catalogs of `regions` and download decrypted bundles on `host:port` (catalogs cached under
    <cache>/catalogs/<region>/)."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.regions = {r.name: r for r in regions}
    server.bundle_key = bundle_key
    server.cache = Path(cache)
    server.catalogs = {}
    server.jp_catalogs = {}
    print(f"nnnotes browse: serving on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
