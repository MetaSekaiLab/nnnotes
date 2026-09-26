"""Addressables content catalogs, asset bundle decryption and a local catalog browser.

A remote content catalog (`catalog_main_<language>.bin`, Addressables binary catalog format version 2) lists every
addressable key with its location: an asset path inside a bundle, a bundle, or a raw file. Remote locations are
absolute URLs on a placeholder host that the game replaces with its CDN base at load time. `parse` reads what the
extractors need (keys, internal ids, dependencies); `parse_locations` decodes every field of every location,
including the AssetBundleRequestOptions of bundle and raw file locations (content hash, CRC, size, internal bundle
name), and `parse_header` / `parse_keys` the header and the key table.

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


# ---------------------------------------------------------------- full decode
# The binary catalog is a BinaryStorageBuffer: every value lives at an offset (0xFFFFFFFF = null). Fixed-size values
# (the header, ResourceLocation.Serializer.Data, ObjectTypeData, TypeSerializer.Data, DynamicString,
# AssetBundleRequestOptionsSerializationAdapter.SerializedData and .Common, Hash128) are read as `sizeof` bytes at
# their offset with no length prefix; arrays and strings carry a u32 byte length just before their offset. A string
# offset has flags in its top bits: bit 31 UTF-16LE (else one byte per character), bit 30 a DynamicString chain
# {u32 part, u32 previous} that starts at the last part; the reader supplies the separator the parts are joined with.
UNICODE = 0x80000000
DYNAMIC = 0x40000000
OFFSET_MASK = 0x3FFFFFFF
HEADER = struct.Struct("<iiIIIIII")          # ContentCatalogData.ResourceLocator.Header, 32 bytes
LOCATION = struct.Struct("<IIIIiII")         # ResourceLocation.Serializer.Data, 28 bytes
REQUEST_OPTIONS = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
# AssetBundleRequestOptions flag bits (SerializedData.Common.flags)
FLAG_BITS = (("assetLoadMode", 1), ("chunkedTransfer", 2), ("useCrcForCachedBundle", 4),
             ("useUnityWebRequestForLocalBundles", 8), ("clearOtherCachedVersionsWhenLoaded", 16))


class _Buffer:
    """Typed reads from a binary catalog. Plain strings and type names are read once per offset: the parts of the
    internal ids (the CDN prefix, directories) and the few type names repeat in every location."""

    def __init__(self, data: bytes):
        self.data = data
        self._plain: dict[int, str] = {}
        self._types: dict[int, tuple] = {}

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def array(self, offset: int) -> list[int]:
        """A u32 array (its byte length is the u32 before it); [] for null."""
        if offset == NONE:
            return []
        n = self.u32(offset - 4)
        if n % 4:
            raise ValueError(f"catalog array at {offset}: byte length {n} is not a multiple of 4")
        return list(struct.unpack_from(f"<{n // 4}I", self.data, offset))

    def plain(self, offset: int) -> str:
        s = self._plain.get(offset)
        if s is None:
            pos = offset & OFFSET_MASK
            raw = self.data[pos:pos + self.u32(pos - 4)]
            s = self._plain[offset] = raw.decode("utf-16-le" if offset & UNICODE else "ascii")
        return s

    def string(self, offset: int, sep: str) -> str | None:
        """A string read with separator `sep` ("" reads plain strings only); None for null."""
        if offset == NONE:
            return None
        if not offset & DYNAMIC:
            return self.plain(offset)
        if not sep:
            raise ValueError(f"catalog string at {offset & OFFSET_MASK}: a part list where a plain string belongs")
        parts, link, seen = [], offset, set()
        while link != NONE:
            pos = link & OFFSET_MASK
            if pos in seen:
                raise ValueError(f"catalog string at {offset & OFFSET_MASK}: the part chain loops")
            seen.add(pos)
            part, link = struct.unpack_from("<II", self.data, pos)
            if part != NONE and part & DYNAMIC:
                raise ValueError(f"catalog string at {pos}: a nested part list")
            parts.append("" if part == NONE else self.plain(part))
        return sep.join(reversed(parts))

    def type_name(self, offset: int) -> tuple[str | None, str | None] | None:
        """TypeSerializer.Data {assembly, class}, both read with '.'; None for null."""
        if offset == NONE:
            return None
        t = self._types.get(offset)
        if t is None:
            assembly, cls = struct.unpack_from("<II", self.data, offset)
            t = self._types[offset] = (self.string(assembly, "."), self.string(cls, "."))
        return t

    def object_init(self, offset: int) -> dict | None:
        """ObjectInitializationData.Serializer.Data {id, type, data}: id and data plain strings, type a
        TypeSerializer.Data."""
        if offset == NONE:
            return None
        ident, typ, data = struct.unpack_from("<III", self.data, offset)
        t = self.type_name(typ) or (None, None)
        return {"id": self.string(ident, ""), "assembly": t[0], "type": t[1], "data": self.string(data, "")}

    def key(self, offset: int) -> tuple[str | None, object]:
        """A key object (ObjectTypeData {type, object}) -> (type name, value). System.String keys are an
        ObjectToStringRemap {u32 string, u16 separator}; System.Int32 keys a 4-byte integer; other types None."""
        typ, obj = struct.unpack_from("<II", self.data, offset)
        cls = (self.type_name(typ) or (None, None))[1]
        if cls == "System.String":
            sid, sep = struct.unpack_from("<IH", self.data, obj)
            return cls, self.string(sid, chr(sep) if sep else "")
        if cls == "System.Int32":
            return cls, struct.unpack_from("<i", self.data, obj)[0]
        return cls, None

    def extra(self, offset: int) -> dict | None:
        """A location's extra data (ObjectTypeData {type, object}): {"type": class name} plus, for
        AssetBundleRequestOptions, the decoded options; None for null."""
        if offset == NONE:
            return None
        typ, obj = struct.unpack_from("<II", self.data, offset)
        cls = (self.type_name(typ) or (None, None))[1]
        out: dict = {"type": cls}
        if cls == REQUEST_OPTIONS and obj != NONE:
            out.update(self.request_options(obj))
        return out

    def request_options(self, offset: int) -> dict:
        """AssetBundleRequestOptionsSerializationAdapter.SerializedData {u32 hash, u32 bundleName, u32 crc,
        u32 bundleSize, u32 common}: hash a Hash128 (16 raw bytes, hex in stored order), bundleName read with '_',
        common a SerializedData.Common {i16 timeout, u8 redirectLimit, u8 retryCount, i32 flags}."""
        hash_id, name_id, crc, size, common = struct.unpack_from("<5I", self.data, offset)
        out = {"hash": None if hash_id == NONE else self.data[hash_id:hash_id + 16].hex(),
               "bundleName": self.string(name_id, "_"), "crc": crc, "bundleSize": size}
        if common == NONE:
            out.update(timeout=None, redirectLimit=None, retryCount=None, flags=None)
            return out
        timeout, redirects, retries, flags = struct.unpack_from("<hBBi", self.data, common)
        out.update(timeout=timeout, redirectLimit=redirects, retryCount=retries, flags=flags)
        for name, bit in FLAG_BITS:
            out[name] = (flags & bit) // bit if name == "assetLoadMode" else bool(flags & bit)
        return out


def parse_header(data: bytes) -> dict:
    """The header of a binary catalog: magic, version, keysOffset, locatorId, instanceProvider, sceneProvider
    ({id, assembly, type, data}), initObjects (the same, the providers the catalog initializes), buildResultHash."""
    if len(data) < HEADER.size:
        raise ValueError("unsupported catalog format")
    magic, version, keys, locator, instance, scene, init, build = HEADER.unpack_from(data, 0)
    if magic != CATALOG_MAGIC or version != CATALOG_VERSION:
        raise ValueError("unsupported catalog format")
    buf = _Buffer(data)
    return {"magic": magic, "version": version, "keysOffset": keys, "locatorId": buf.string(locator, ""),
            "instanceProvider": buf.object_init(instance), "sceneProvider": buf.object_init(scene),
            "initObjects": [buf.object_init(o) for o in buf.array(init)],
            "buildResultHash": buf.string(build, "")}


def parse_keys(data: bytes) -> list[dict]:
    """The key table of a binary catalog in stored order (ContentCatalogData.ResourceLocator.KeyData {u32 key object,
    u32 location set}): {"key": value, "type": the key's type name, "locations": location offsets}."""
    header = parse_header(data)
    buf = _Buffer(data)
    out = []
    table = header["keysOffset"]
    n = buf.u32(table - 4)
    if n % 8:
        raise ValueError(f"catalog key table: byte length {n} is not a multiple of 8")
    for pos in range(table, table + n, 8):
        key_obj, locations = struct.unpack_from("<II", data, pos)
        cls, value = buf.key(key_obj) if key_obj != NONE else (None, None)
        out.append({"key": value, "type": cls, "locations": buf.array(locations)})
    return out


def parse_locations(data: bytes) -> list[dict]:
    """Every location of a binary catalog, fully decoded, by offset: what parse() gives (offset, primary_key,
    internal_id, dependencies) plus provider, dependency_hash, resource_type (class names) and extra_data.

    A location record (ResourceLocation.Serializer.Data) is a fixed 28-byte value with no length prefix: primary key
    and internal id (strings read with '/'), provider (read with '.'), dependency set (u32 array of location
    offsets), dependency hash (i32), extra data (an ObjectTypeData) and resource type (a TypeSerializer.Data).
    extra_data of bundle and raw file locations is an AssetBundleRequestOptions: {type, hash, bundleName, crc,
    bundleSize, timeout, redirectLimit, retryCount, flags and the flag bits by name}."""
    header = parse_header(data)
    buf = _Buffer(data)
    offsets: set[int] = set()
    table = header["keysOffset"]
    for pos in range(table, table + buf.u32(table - 4), 8):
        offsets.update(buf.array(buf.u32(pos + 4)))
    out = []
    for pos in sorted(offsets):
        if pos + LOCATION.size > len(data):
            raise ValueError(f"catalog location at {pos}: past the end of the catalog")
        primary, internal, provider, deps, dep_hash, extra, typ = LOCATION.unpack_from(data, pos)
        rtype = buf.type_name(typ)
        out.append({"offset": pos, "primary_key": buf.string(primary, "/") or "",
                    "internal_id": buf.string(internal, "/") or "", "dependencies": buf.array(deps),
                    "provider": buf.string(provider, "."), "dependency_hash": dep_hash,
                    "resource_type": rtype[1] if rtype else None, "extra_data": buf.extra(extra)})
    return out


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
