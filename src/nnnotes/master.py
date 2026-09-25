"""Master data files -> JSON tables.

A master data file (`<Table>.bin`) is a 64-byte prefix followed by the table encrypted with Rijndael (256-bit
block, 256-bit key, CBC, PKCS7 padding); the plaintext is gzip-compressed UTF-8 JSON (`{"_allData": [rows]}`).
The game reads it in MasterDataModelBox<,>.Load: RijndaelEncryption.FixedDecrypt (the data after the prefix,
the CryptProvider key and IV), then Compression.Decompress (GZipStream), then JsonUtility.FromJson.

AES is Rijndael with 128-bit blocks only, so the 256-bit block cipher is implemented here (numpy, every block of a
file at once). Key and IV come from the configuration (`[master] key`, `[master] iv`).

`download` fetches a master data version from the region's CDN: `<cdn>/master/<version>/MasterManifest.json`
(`{"version", "files": [{"name", "hash", "size"}]}`) and the files it lists, each checked against its SHA-256.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PREFIX = 64                     # bytes before the ciphertext
BLOCK = 32                      # 256-bit block
NB = NK = 8                     # block / key length in 32-bit words
NR = 14                         # rounds


@dataclass(frozen=True)
class MasterKey:
    """Key and IV of the master data encryption (never shown in a repr)."""
    key: bytes = field(repr=False)
    iv: bytes = field(repr=False)

    def __post_init__(self):
        if len(self.key) != 32 or len(self.iv) != 32:
            raise ValueError("master key and IV must be 32 bytes each")


# ---------------------------------------------------------------- Rijndael-256
def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _sbox() -> list[int]:
    """The Rijndael S-box (multiplicative inverse in GF(2^8) followed by the affine map)."""
    box = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1 | q >> 7) & 0xFF) ^ ((q << 2 | q >> 6) & 0xFF) \
            ^ ((q << 3 | q >> 5) & 0xFF) ^ ((q << 4 | q >> 4) & 0xFF)
        box[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    box[0] = 0x63
    return box


SBOX = _sbox()
INV_SBOX = np.zeros(256, dtype=np.uint8)
INV_SBOX[np.array(SBOX)] = np.arange(256, dtype=np.uint8)
MUL = {k: np.array([_gmul(i, k) for i in range(256)], dtype=np.uint8) for k in (9, 11, 13, 14)}
INV_SHIFT = (0, 1, 3, 4)        # InvShiftRows: row r moves right by INV_SHIFT[r] columns (Nb = 8)


def round_keys(key: bytes) -> np.ndarray:
    """Key expansion (Nk = 8) -> (NR + 1, 4 rows, NB columns) uint8."""
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    words = [list(key[4 * i:4 * i + 4]) for i in range(NK)]
    rcon = 1
    for i in range(NK, NB * (NR + 1)):
        t = list(words[i - 1])
        if i % NK == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        elif i % NK == 4:
            t = [SBOX[b] for b in t]
        words.append([a ^ b for a, b in zip(words[i - NK], t)])
    return np.array(words, dtype=np.uint8).reshape(NR + 1, NB, 4).transpose(0, 2, 1)


def decrypt_blocks(ct: np.ndarray, rk: np.ndarray) -> np.ndarray:
    """(n, 32) ciphertext blocks -> (n, 32) block decryptions (no CBC chaining). A block's bytes are its state
    column by column."""
    s = ct.reshape(-1, NB, 4).transpose(0, 2, 1).copy()          # (n, 4, NB): s[r][c]
    s ^= rk[NR]
    cols = np.arange(NB)
    for rnd in range(NR - 1, -1, -1):
        for r in range(4):                                       # InvShiftRows
            s[:, r, :] = s[:, r, (cols - INV_SHIFT[r]) % NB]
        s = INV_SBOX[s]                                          # InvSubBytes
        s ^= rk[rnd]                                             # AddRoundKey
        if rnd:                                                  # InvMixColumns
            a0, a1, a2, a3 = s[:, 0, :], s[:, 1, :], s[:, 2, :], s[:, 3, :]
            s = np.stack([
                MUL[14][a0] ^ MUL[11][a1] ^ MUL[13][a2] ^ MUL[9][a3],
                MUL[9][a0] ^ MUL[14][a1] ^ MUL[11][a2] ^ MUL[13][a3],
                MUL[13][a0] ^ MUL[9][a1] ^ MUL[14][a2] ^ MUL[11][a3],
                MUL[11][a0] ^ MUL[13][a1] ^ MUL[9][a2] ^ MUL[14][a3],
            ], axis=1)
    return s.transpose(0, 2, 1).reshape(-1, BLOCK)


def decrypt_cbc(data: bytes, key: bytes, iv: bytes, rk: np.ndarray | None = None) -> bytes:
    """Rijndael-256 CBC decryption, padding kept."""
    if not data or len(data) % BLOCK:
        raise ValueError(f"ciphertext length {len(data)} is not a positive multiple of {BLOCK}")
    ct = np.frombuffer(data, dtype=np.uint8).reshape(-1, BLOCK)
    pt = decrypt_blocks(ct, round_keys(key) if rk is None else rk)
    pt ^= np.vstack([np.frombuffer(iv, dtype=np.uint8)[None, :], ct[:-1]])
    return pt.tobytes()


def decode(raw: bytes, key: MasterKey, rk: np.ndarray | None = None) -> bytes:
    """A master data file as stored -> its JSON text (UTF-8 bytes)."""
    out = decrypt_cbc(raw[PREFIX:], key.key, key.iv, rk)
    pad = out[-1]
    if not 1 <= pad <= BLOCK or out[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad PKCS7 padding (wrong key or IV, or not a master data file)")
    return gzip.decompress(out[:-pad])


def table_json(text: bytes) -> str:
    """The stored form of a decoded table: parsed and written again, UTF-8, one-space indent."""
    return json.dumps(json.loads(text.decode("utf-8")), ensure_ascii=False, indent=1)


def decode_files(files: list[Path], out_dir: Path, key: MasterKey, workers: int = 8) -> dict:
    """Decode master data files into <out_dir>/<stem>.json. Returns {decoded, failed: [{file, error}]}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rk = round_keys(key.key)

    def one(p: Path):
        try:
            text = table_json(decode(p.read_bytes(), key, rk))
        except Exception as e:                       # report and continue with the other tables
            return {"file": p.name, "error": f"{type(e).__name__}: {e}"}
        (out_dir / f"{p.stem}.json").write_text(text, encoding="utf-8", newline="\n")
        return None

    with ThreadPoolExecutor(max(1, workers)) as ex:
        failed = [r for r in ex.map(one, files) if r is not None]
    return {"decoded": len(files) - len(failed), "failed": failed, "out": str(out_dir)}


def input_files(inputs: list[Path]) -> list[Path]:
    """Files given, and the `*.bin` files of directories given, sorted by name per directory."""
    out: list[Path] = []
    for p in map(Path, inputs):
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".bin")
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(p)
    return out


# ---------------------------------------------------------------- download
class DownloadError(RuntimeError):
    """A CDN download failed (the message names the file, not the URL)."""


def _get(url: str, retries: int = 3, timeout: int = 60) -> bytes:
    name = url.rsplit("/", 1)[-1]
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == retries - 1:
                raise DownloadError(f"download of {name} failed: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if i == retries - 1:
                raise DownloadError(f"download of {name} failed: {type(e).__name__}") from None
        time.sleep(0.5 * (i + 1))
    raise AssertionError("unreachable")


def download(cdn: str, version: str, out_dir: Path, workers: int = 16) -> dict:
    """Master data `version` from the CDN into <out_dir>/ (the manifest and every listed file, SHA-256 checked;
    files already present with the right hash are kept)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{cdn.rstrip('/')}/master/{version}"
    manifest_raw = _get(f"{base}/MasterManifest.json")
    manifest = json.loads(manifest_raw.decode("utf-8"))
    (out_dir / "MasterManifest.json").write_bytes(manifest_raw)
    files = manifest.get("files", [])

    def one(f: dict):
        name, sha = f["name"], (f.get("hash") or "").lower()
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            return {"file": name, "error": "unexpected file name"}
        dst = out_dir / name
        if sha and dst.is_file() and hashlib.sha256(dst.read_bytes()).hexdigest() == sha:
            return "kept"
        data = _get(f"{base}/{name}")
        if sha and hashlib.sha256(data).hexdigest() != sha:
            return {"file": name, "error": "sha256 differs from the manifest"}
        tmp = dst.with_name(f"{name}.{os.getpid()}.part")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
        return "downloaded"

    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(one, files))
    return {"version": manifest.get("version", version), "files": len(files),
            "downloaded": results.count("downloaded"), "kept": results.count("kept"),
            "failed": [r for r in results if isinstance(r, dict)], "out": str(out_dir)}
