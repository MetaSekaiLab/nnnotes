"""Synthetic inputs for the tests: made-up keys, an independent Rijndael encryptor, master data files and a master
data CDN tree, bundle encryption, a binary catalog writer and a binary AndroidManifest.xml. Nothing here comes from
game data. Helpers several test modules use live here (test modules do not import each other)."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import struct

from Crypto.Cipher import AES

# obviously fake keys
BUNDLE_KEY = bytes(range(16))
BUNDLE_SEED = b"nnnotes-test-seed"
MASTER_KEY = bytes(range(32))
MASTER_IV = bytes(range(32, 64))

CATALOG_MAGIC = 0x0DE38942
NONE = 0xFFFFFFFF
LOCAL_PREFIX = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
REMOTE_BASE = "https://example.invalid"          # the catalog's own placeholder host (reserved name)


# ---------------------------------------------------------------- Rijndael (any block / key length), encryption
def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _make_sbox() -> list[int]:
    exp, log = [1] * 255, [0] * 256              # powers of the generator 3 in GF(2^8)
    for i in range(1, 255):
        exp[i] = _mul(exp[i - 1], 3)
        log[exp[i]] = i
    inv = [0] + [exp[(255 - log[a]) % 255] for a in range(1, 256)]
    box = []
    for a in range(256):
        x = inv[a]
        y = x
        for _ in range(4):
            x = ((x << 1) | (x >> 7)) & 0xFF
            y ^= x
        box.append(y ^ 0x63)
    return box


SBOX = _make_sbox()
SHIFTS = {4: (0, 1, 2, 3), 6: (0, 1, 2, 3), 8: (0, 1, 3, 4)}


def rijndael_encrypt_block(block: bytes, key: bytes) -> bytes:
    """One block (16, 24 or 32 bytes) under a 16, 24 or 32 byte key; bytes are the state column by column."""
    nb, nk = len(block) // 4, len(key) // 4
    nr = max(nb, nk) + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, nb * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    s = [[block[4 * c + r] for c in range(nb)] for r in range(4)]

    def add(rnd):
        for c in range(nb):
            for r in range(4):
                s[r][c] ^= w[rnd * nb + c][r]

    add(0)
    for rnd in range(1, nr + 1):
        for r in range(4):
            s[r] = [SBOX[b] for b in s[r]]
            k = SHIFTS[nb][r]
            s[r] = s[r][k:] + s[r][:k]
        if rnd != nr:
            for c in range(nb):
                a = [s[r][c] for r in range(4)]
                for r in range(4):
                    s[r][c] = (_mul(a[r], 2) ^ _mul(a[(r + 1) % 4], 3) ^ a[(r + 2) % 4] ^ a[(r + 3) % 4])
        add(rnd)
    return bytes(s[r][c] for c in range(nb) for r in range(4))


def rijndael256_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """Rijndael with 256-bit blocks, CBC, PKCS7 padding."""
    pad = 32 - len(data) % 32
    data += bytes([pad]) * pad
    out, prev = bytearray(), iv
    for i in range(0, len(data), 32):
        prev = rijndael_encrypt_block(bytes(a ^ b for a, b in zip(data[i:i + 32], prev)), key)
        out += prev
    return bytes(out)


MASTER_TABLE = {"_allData": [{"_id": 1, "_name": "テスト", "_value": 0.5}, {"_id": 2, "_name": "test", "_value": -3}]}


def master_file(table: dict, key: bytes = MASTER_KEY, iv: bytes = MASTER_IV, prefix: bytes | None = None) -> bytes:
    """A master data file as stored: 64-byte prefix + Rijndael-256-CBC(gzip(JSON))."""
    plain = gzip.compress(json.dumps(table, ensure_ascii=False).encode("utf-8"), mtime=0)
    return (prefix if prefix is not None else bytes(64)) + rijndael256_cbc_encrypt(plain, key, iv)


def serve_master_version(root, version, files, bad_hash=()):
    """A CDN tree on disk: <root>/master/<version>/MasterManifest.json and the files it lists."""
    d = root / "master" / version
    d.mkdir(parents=True)
    listed = []
    for name, data in files.items():
        (d / name).write_bytes(data)
        sha = hashlib.sha256(b"other" if name in bad_hash else data).hexdigest()
        listed.append({"name": name, "hash": sha, "size": len(data)})
    (d / "MasterManifest.json").write_text(json.dumps({"version": version, "files": listed}))
    return root.as_uri()


# ---------------------------------------------------------------- bundles
def fake_bundle(name: str, size: int = 20000) -> bytes:
    """UnityFS-signed bytes longer than the 16 KiB encrypted head."""
    body = hashlib.sha256(name.encode()).digest() * (size // 32 + 1)
    return (b"UnityFS\0" + body)[:size]


def encrypt_bundle(data: bytes, filename: str, key: bytes = BUNDLE_KEY, seed: bytes = BUNDLE_SEED) -> bytes:
    """AES-128-CTR over the first 16 KiB, keystream built block by block from AES-ECB (nonce + 64-bit BE counter)."""
    nonce = hashlib.sha256(seed + filename.encode("utf-8")).digest()[:8]
    ecb = AES.new(key, AES.MODE_ECB)
    head = data[:16384]
    stream = b"".join(ecb.encrypt(nonce + i.to_bytes(8, "big")) for i in range((len(head) + 15) // 16))
    return bytes(a ^ b for a, b in zip(head, stream)) + data[16384:]


# ---------------------------------------------------------------- binary catalog (format version 2)
RM = "UnityEngine.ResourceManagement.ResourceProviders."
RM_ASSEMBLY = "Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
CORE_ASSEMBLY = "UnityEngine.CoreModule, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
LIB_ASSEMBLY = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
BUNDLE_PROVIDER = RM + "AssetBundleProvider"
ASSET_PROVIDER = RM + "BundledAssetProvider"
BUNDLE_TYPE = RM + "IAssetBundleResource"
REQUEST_OPTIONS = RM + "AssetBundleRequestOptions"
_HASHED = re.compile(r"_([0-9a-f]{32})(\.bundle)?$")


def is_file_location(internal_id: str) -> bool:
    return "://" in internal_id or internal_id.startswith(LOCAL_PREFIX)


def request_options(internal_id: str, **given) -> dict:
    """The AssetBundleRequestOptions a bundle or raw file location gets unless told otherwise: the file name's
    `_<32 hex>` as hash (else a digest of the name), a two-part internal bundle name (one part for raw files), crc
    and size 0, flags 4 for files in the APK."""
    name = internal_id.rsplit("/", 1)[-1]
    m = _HASHED.search(name)
    h = hashlib.md5(name.encode()).hexdigest()
    parts = [hashlib.md5(b"bundle:" + name.encode()).hexdigest()]
    if name.endswith(".bundle"):
        parts.append(h[:12])
    opts = {"hash": m.group(1) if m else h, "bundleName": "_".join(parts), "crc": 0, "bundleSize": 0,
            "timeout": 0, "redirectLimit": 32, "retryCount": 0,
            "flags": 4 if internal_id.startswith(LOCAL_PREFIX) else 0}
    opts.update(given)
    return opts


class CatalogWriter:
    """Writes the binary catalog layout (format 2): the 32-byte header (locator id, instance and scene providers,
    initialization objects, build result hash), the key table (key objects -> location sets), 28-byte location
    records with no length prefix (primary key, internal id, provider, dependencies, dependency hash, extra data,
    resource type), AssetBundleRequestOptions extra data on bundle and raw file locations, type records and part
    lists. Arrays and strings carry a u32 byte length before their offset; fixed-size values do not."""

    def __init__(self):
        self.buf = bytearray(32)
        self._types: dict = {}
        self._commons: dict = {}

    def blob(self, data: bytes) -> int:
        self.buf += struct.pack("<I", len(data))
        off = len(self.buf)
        self.buf += data
        return off

    def value(self, data: bytes) -> int:
        """A fixed-size value: no length prefix."""
        off = len(self.buf)
        self.buf += data
        return off

    def reserve(self, size: int) -> int:
        return self.value(bytes(size))

    def text(self, s: str) -> int:
        try:
            return self.blob(s.encode("ascii"))
        except UnicodeEncodeError:
            return self.blob(s.encode("utf-16-le")) | 0x80000000

    def parts(self, s: str, sep: str) -> int:
        """A string stored as parts split at `sep`, chained last part first (DynamicString values)."""
        head = NONE
        for part in s.split(sep):
            head = self.value(struct.pack("<II", self.text(part), head)) | 0x40000000
        return head

    def path(self, s: str) -> int:
        """A path stored as parts, last part first."""
        return self.parts(s, "/")

    def string(self, s: str | None, sep: str) -> int:
        if s is None:
            return NONE
        return self.parts(s, sep) if sep and sep in s else self.text(s)

    def array(self, values) -> int:
        values = list(values)
        return self.blob(struct.pack(f"<{len(values)}I", *values)) if values else NONE

    def type_data(self, cls: str, assembly: str) -> int:
        """A TypeSerializer.Data {assembly, class} (both read with '.'), one per type."""
        if (cls, assembly) not in self._types:
            self._types[(cls, assembly)] = self.value(struct.pack("<II", self.string(assembly, "."),
                                                                  self.string(cls, ".")))
        return self._types[(cls, assembly)]

    def key_object(self, key: str) -> int:
        """An ObjectTypeData of a System.String key: an ObjectToStringRemap {string, u16 separator}."""
        sep = "/" if "/" in key else ""
        obj = self.value(struct.pack("<IH", self.string(key, sep), ord(sep) if sep else 0) + bytes(2))
        return self.value(struct.pack("<II", self.type_data("System.String", LIB_ASSEMBLY), obj))

    def object_init(self, ident: str, cls: str) -> int:
        """An ObjectInitializationData {id, type, data}."""
        return self.value(struct.pack("<III", self.text(ident), self.type_data(cls, RM_ASSEMBLY), NONE))

    def request_options(self, opts: dict) -> int:
        """An ObjectTypeData of an AssetBundleRequestOptions: SerializedData {Hash128, bundle name (read with
        '_'), crc, size, Common}; equal Common values are written once."""
        common = (opts["timeout"], opts["redirectLimit"], opts["retryCount"], opts["flags"])
        if common not in self._commons:
            self._commons[common] = self.value(struct.pack("<hBBi", *common))
        hash_id = NONE if opts["hash"] is None else self.value(bytes.fromhex(opts["hash"]))
        data = self.value(struct.pack("<5I", hash_id, self.string(opts["bundleName"], "_"), opts["crc"],
                                      opts["bundleSize"], self._commons[common]))
        return self.value(struct.pack("<II", self.type_data(REQUEST_OPTIONS, RM_ASSEMBLY), data))

    def build(self, entries: list[tuple], path_ids: bool = False, *, build_hash: str = "0" * 32) -> bytes:
        """entries: (primary key, internal id, dependency indices[, options]). `path_ids`: internal ids as part
        lists. Options: provider, type (resource type class), extra (AssetBundleRequestOptions fields over the
        defaults of `request_options`; None: no extra data; default: extra data on bundle and raw file
        locations), extra_object ((class name, bytes): extra data of another type), dependency_hash."""
        by_key: dict[str, list[int]] = {}
        for i, e in enumerate(entries):
            by_key.setdefault(e[0], []).append(i)
        table_len = 8 * len(by_key)
        self.buf += struct.pack("<I", table_len)
        keys = self.reserve(table_len)
        records = [self.reserve(28) for _ in entries]           # packed back to back, no length prefix
        for rec, e in zip(records, entries):
            primary, internal, deps = e[:3]
            opts = e[3] if len(e) > 3 else {}
            file = is_file_location(internal)
            iid = self.path(internal) if path_ids and "/" in internal and "://" not in internal else self.text(internal)
            provider = opts.get("provider", BUNDLE_PROVIDER if file else ASSET_PROVIDER)
            rtype = opts.get("type", BUNDLE_TYPE if file else "UnityEngine.Object")
            extra = opts.get("extra", {}) if "extra" in opts or file else None
            extra_id = NONE if extra is None else self.request_options(request_options(internal, **extra))
            if "extra_object" in opts:                              # (class name, bytes) of another extra type
                cls, raw = opts["extra_object"]
                extra_id = self.value(struct.pack("<II", self.type_data(cls, CORE_ASSEMBLY), self.value(raw)))
            struct.pack_into("<IIIIiII", self.buf, rec, self.string(primary, "/"), iid, self.string(provider, "."),
                             self.array(records[d] for d in deps), opts.get("dependency_hash", 0), extra_id,
                             self.type_data(rtype, RM_ASSEMBLY if rtype.startswith(RM) else CORE_ASSEMBLY))
        for i, (k, idx) in enumerate(by_key.items()):
            struct.pack_into("<II", self.buf, keys + 8 * i, self.key_object(k), self.array(records[j] for j in idx))
        providers = [self.object_init(RM + n, RM + n) for n in ("AssetBundleProvider", "BundledAssetProvider")]
        struct.pack_into("<iiIIIIII", self.buf, 0, CATALOG_MAGIC, 2, keys, self.text("AddressablesMainContentCatalog"),
                         self.object_init(RM + "InstanceProvider", RM + "InstanceProvider"),
                         self.object_init(RM + "SceneProvider", RM + "SceneProvider"), self.array(providers),
                         self.text(build_hash))
        return bytes(self.buf)


def remote(name: str) -> str:
    return f"{REMOTE_BASE}/asset/Android/{name}"


def local(name: str) -> str:
    return f"{LOCAL_PREFIX}/Android/{name}"


# ---------------------------------------------------------------- APK manifest
def axml(strings: list[str], version_index: int | None, utf8: bool = False) -> bytes:
    """A binary AndroidManifest.xml with a string pool and a <manifest> start element."""
    blobs = []
    for s in strings:
        if utf8:
            b = s.encode("utf-8")
            blobs.append(bytes([len(s), len(b)]) + b + b"\x00")
        else:
            blobs.append(struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00")
    offsets, pos = [], 0
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
    body = struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(blobs)
    body += b"\x00" * (-len(body) % 4)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, 28 + len(body), len(strings), 0, 0x100 if utf8 else 0,
                       28 + 4 * len(strings), 0) + body
    attrs = b""
    if version_index is not None:
        attrs = struct.pack("<IIIHBBI", 0xFFFFFFFF, strings.index("versionName"), version_index, 8, 0, 3,
                            version_index)
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, strings.index("manifest"), 20, 20, 1 if attrs else 0, 0, 0, 0)
    elem_body = struct.pack("<II", 1, 0xFFFFFFFF) + ext + attrs
    elem = struct.pack("<HHI", 0x0102, 16, 8 + len(elem_body)) + elem_body
    chunks = pool + elem
    return struct.pack("<HHI", 0x0003, 8, 8 + len(chunks)) + chunks
