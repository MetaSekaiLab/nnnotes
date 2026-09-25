"""Addressables content catalogs, asset bundle decryption and a local catalog browser.

A remote content catalog (`catalog_main_<language>.bin`, Addressables binary catalog format version 2) lists every
addressable key with its location: an asset path inside a bundle, a bundle, or a raw file. Remote locations are
absolute URLs on a placeholder host that the game replaces with its CDN base at load time.

Asset bundles on the CDN and inside the APK are encrypted in their first 16 KiB with AES-128 in CTR mode: the nonce
is the first 8 bytes of SHA-256(nonce seed + UTF-8 bundle file name), the counter block is nonce + 64-bit big-endian
counter starting at 0. A file that already starts with the `UnityFS` signature is not encrypted. The key and the
nonce seed come from the configuration (`[bundle] key`, `[bundle] nonce_seed`).
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from Crypto.Cipher import AES

from . import __version__

UNITYFS = b"UnityFS\0"
ENCRYPTED_BYTES = 16384            # only the first 16 KiB of a bundle are encrypted
CATALOG_MAGIC = 0x0DE38942
CATALOG_VERSION = 2
NONE = 0xFFFFFFFF


@dataclass(frozen=True)
class BundleKey:
    """AES-128 key and nonce seed of the bundle encryption (never shown in a repr)."""
    key: bytes = field(repr=False)
    seed: bytes = field(repr=False)

    def __post_init__(self):
        if len(self.key) != 16:
            raise ValueError("bundle key must be 16 bytes")


def decrypt(data: bytes, filename: str, key: BundleKey) -> bytes:
    """A bundle file as stored -> UnityFS bytes. `filename`: the bundle's bare file name (it seeds the nonce)."""
    if data.startswith(UNITYFS):
        return data
    nonce = hashlib.sha256(key.seed + filename.encode("utf-8")).digest()[:8]
    aes = AES.new(key.key, AES.MODE_CTR, nonce=nonce, initial_value=0)
    return aes.decrypt(data[:ENCRYPTED_BYTES]) + data[ENCRYPTED_BYTES:]


def remote_path(internal_id: str) -> str | None:
    """The path below the CDN base of a remote location (an absolute URL: everything after its host), else None."""
    scheme, sep, rest = internal_id.partition("://")
    if not sep or not scheme.isalpha():
        return None
    _, slash, path = rest.partition("/")
    return slash + path


def parse(data: bytes) -> list[dict]:
    """Every location of a binary catalog: {offset, primary_key, internal_id, dependencies (location offsets)}."""
    def u32(offset):
        return struct.unpack_from("<I", data, offset)[0]

    def array(offset):
        if offset == NONE:
            return []
        return struct.unpack_from(f"<{u32(offset - 4) // 4}I", data, offset)

    def text(offset):
        if offset == NONE:
            return ""
        if offset & 0x40000000:                     # a path of parts, stored last part first
            parts = []
            while offset != NONE:
                part, offset = struct.unpack_from("<II", data, offset & 0x3FFFFFFF)
                parts.append(text(part))
            return "/".join(reversed(parts))
        pos = offset & 0x3FFFFFFF
        return data[pos:pos + u32(pos - 4)].decode("utf-16-le" if offset & 0x80000000 else "ascii")

    magic, version, keys = struct.unpack_from("<III", data)
    if magic != CATALOG_MAGIC or version != CATALOG_VERSION:
        raise ValueError("unsupported catalog format")
    locations = set()
    for pos in range(keys, keys + u32(keys - 4), 8):
        locations.update(array(u32(pos + 4)))
    result = []
    for pos in sorted(locations):
        primary, internal, _, deps = struct.unpack_from("<4I", data, pos)
        result.append({"offset": pos, "primary_key": text(primary),
                       "internal_id": text(internal), "dependencies": array(deps)})
    return result


# ---------------------------------------------------------------- local browser
def browse(entries: list[dict]) -> tuple[dict, dict]:
    """(remote bundles by offset, {asset path or `bundles/<key>`: bundle offsets}) of a parsed catalog."""
    bundles = {e["offset"]: e for e in entries
               if remote_path(e["internal_id"]) is not None and e["internal_id"].endswith(".bundle")}
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


class Handler(BaseHTTPRequestHandler):
    """/ -> regions, /<region>/ -> languages, /<region>/<language>/<dir>/ -> listing,
    /<region>/<language>/download/<offset> -> the decrypted bundle."""

    def listing(self, label: str, rows) -> None:
        title = f"nnnotes {__version__} /" + label
        data = ("<!doctype html><meta charset=\"utf-8\"><title>" + escape(title)
                + "</title><h1>" + escape(title) + "</h1><hr><ul>"
                + "\n".join(sorted(rows)) + "</ul><hr>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def catalog(self, region: Region, language: str):
        key = (region.name, language)
        if key not in self.server.catalogs:
            catalog = self.server.cache / "catalogs" / region.name / f"catalog_main_{language}.bin"
            if not catalog.exists():
                with urlopen(region.cdn + f"/asset/Android/{catalog.name}", timeout=60) as response:
                    raw = response.read()
                catalog.parent.mkdir(parents=True, exist_ok=True)
                catalog.write_bytes(raw)
            self.server.catalogs[key] = browse(parse(catalog.read_bytes()))
        return self.server.catalogs[key]

    def do_GET(self):
        regions: dict[str, Region] = self.server.regions
        path = unquote(urlsplit(self.path).path)
        parts = path.strip("/").split("/") if path.strip("/") else []
        if not parts:
            self.listing("", [f"<li><a href=\"/{quote(r.name)}/\">{escape(r.label)}/</a></li>" for r in regions.values()])
            return
        region = regions.get(parts[0])
        if region is None:
            self.send_error(404)
            return
        if len(parts) == 1:
            self.listing(region.label + "/", ["<li><a href=\"/\">../</a></li>"] +
                         [f"<li><a href=\"/{quote(region.name)}/{quote(lang)}/\">{escape(lang)}/</a></li>"
                          for lang in region.languages])
            return
        language = parts[1]
        if language not in region.languages:
            self.send_error(404)
            return
        bundles, files = self.catalog(region, language)
        base = f"/{quote(region.name)}/{quote(language)}/"
        if len(parts) > 2 and parts[2] == "download":
            ident = parts[3] if len(parts) == 4 else ""
            if not ident.isdecimal() or int(ident) not in bundles:
                self.send_error(404)
                return
            entry = bundles[int(ident)]
            name = entry["internal_id"].rsplit("/", 1)[1]
            with urlopen(region.cdn + remote_path(entry["internal_id"]), timeout=60) as response:
                data = decrypt(response.read(), name, self.server.bundle_key)
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
        rows = {f"<li><a href=\"{quote(parent)}\">../</a></li>"}
        for name, ids in files.items():
            if not name.startswith(prefix):
                continue
            tail = name[len(prefix):]
            if "/" in tail:
                directory = prefix + tail.split("/")[0] + "/"
                rows.add(f"<li><a href=\"{base}{quote(directory)}\">{escape(tail.split('/')[0])}/</a></li>")
            else:
                for ident in sorted(ids):
                    label = tail if len(ids) == 1 else tail + " — " + bundles[ident]["primary_key"]
                    rows.add(f"<li><a href=\"{base}download/{ident}\">{escape(label)}</a></li>")
        self.listing(region.label + "/" + language + "/" + prefix, rows)


def serve(regions: list[Region], bundle_key: BundleKey, cache: Path, port: int, host: str = "127.0.0.1") -> None:
    """Browse the catalogs of `regions` and download decrypted bundles on `host:port` (catalogs cached under
    <cache>/catalogs/<region>/)."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.regions = {r.name: r for r in regions}
    server.bundle_key = bundle_key
    server.cache = Path(cache)
    server.catalogs = {}
    print(f"nnnotes browse: serving on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
