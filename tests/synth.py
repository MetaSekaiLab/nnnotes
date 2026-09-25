"""Synthetic inputs for the tests: made-up keys, an independent Rijndael encryptor, bundle encryption and a
binary catalog writer. Nothing here comes from game data."""
from __future__ import annotations

import gzip
import hashlib
import json
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


def master_file(table: dict, key: bytes = MASTER_KEY, iv: bytes = MASTER_IV, prefix: bytes | None = None) -> bytes:
    """A master data file as stored: 64-byte prefix + Rijndael-256-CBC(gzip(JSON))."""
    plain = gzip.compress(json.dumps(table, ensure_ascii=False).encode("utf-8"), mtime=0)
    return (prefix if prefix is not None else bytes(64)) + rijndael256_cbc_encrypt(plain, key, iv)


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
class CatalogWriter:
    """Writes the subset of the binary catalog layout the reader uses: a key table of (key, locations) pairs and
    location records (primary key, internal id, provider, dependencies)."""

    def __init__(self):
        self.buf = bytearray(12)

    def blob(self, data: bytes) -> int:
        self.buf += struct.pack("<I", len(data))
        off = len(self.buf)
        self.buf += data
        return off

    def text(self, s: str) -> int:
        try:
            return self.blob(s.encode("ascii"))
        except UnicodeEncodeError:
            return self.blob(s.encode("utf-16-le")) | 0x80000000

    def path(self, s: str) -> int:
        """A path stored as parts, last part first."""
        head = NONE
        for part in s.split("/"):
            head = self.blob(struct.pack("<II", self.text(part), head)) | 0x40000000
        return head

    def array(self, values) -> int:
        values = list(values)
        return self.blob(struct.pack(f"<{len(values)}I", *values)) if values else NONE

    def build(self, entries: list[tuple[str, str, list[int]]], path_ids: bool = False) -> bytes:
        """entries: (primary key, internal id, dependency indices). `path_ids`: internal ids as part lists."""
        records = [self.blob(bytes(16)) for _ in entries]
        for rec, (primary, internal, deps) in zip(records, entries):
            iid = self.path(internal) if path_ids and "/" in internal and "://" not in internal else self.text(internal)
            struct.pack_into("<4I", self.buf, rec, self.text(primary), iid, NONE,
                             self.array(records[d] for d in deps))
        by_key: dict[str, list[int]] = {}
        for rec, (primary, _, _) in zip(records, entries):
            by_key.setdefault(primary, []).append(rec)
        table = b"".join(struct.pack("<II", self.text(k), self.array(v)) for k, v in by_key.items())
        keys = self.blob(table)
        struct.pack_into("<III", self.buf, 0, CATALOG_MAGIC, 2, keys)
        return bytes(self.buf)


def remote(name: str) -> str:
    return f"{REMOTE_BASE}/asset/Android/{name}"


def local(name: str) -> str:
    return f"{LOCAL_PREFIX}/Android/{name}"
