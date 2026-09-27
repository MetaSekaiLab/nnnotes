"""Stored encodings of site assets: deterministic gzip and brotli bytes, and their decoding.

gzip: the 10-byte header 1f 8b 08 00 00 00 00 00 02 ff (no name, no mtime, XFL 2, OS unknown), the raw deflate
stream of zlib at level 9 (window 15, default memory level), the CRC-32 and the length mod 2^32, little-endian; the
header is written here, the same on every platform (zlib's own gzip wrapper writes the platform's OS byte).
brotli: `brotli.compress(data, quality=11)` (the `brotli` module, imported when first used). The bytes are the same
for the same zlib / brotli versions.
"""
from __future__ import annotations

import gzip
import struct
import zlib
from pathlib import Path

ENCODINGS = ("gzip", "br", "none")                 # web --compress
DEFAULT_ENCODING = "gzip"
SUFFIXES = {"gzip": ".gz", "br": ".br"}            # stored name suffix of an encoded asset
# extensions of the stored names worth encoding (text, meshes, raw PCM); images and compressed audio are left as
# they are
COMPRESSIBLE = frozenset({"json", "glsl", "moc3", "atlas", "skel", "bin", "wav", "glb"})
GZIP_HEADER = bytes.fromhex("1f 8b 08 00 00 00 00 00 02 ff")


def gzip_bytes(data: bytes) -> bytes:
    """`data` as a gzip member (module docstring)."""
    return (GZIP_HEADER + zlib.compress(data, level=9, wbits=-15)
            + struct.pack("<II", zlib.crc32(data), len(data) & 0xFFFFFFFF))


def _brotli():
    try:
        import brotli
    except ImportError:
        raise ImportError("brotli site assets need the brotli module (pip install brotli)") from None
    return brotli


def brotli_bytes(data: bytes) -> bytes:
    """`data` as a brotli stream at quality 11."""
    return _brotli().compress(data, quality=11)


def check(encoding: str) -> None:
    """ValueError for an unknown encoding; ImportError when "br" is asked for without the brotli module."""
    if encoding not in ENCODINGS:
        raise ValueError(f"asset encoding {encoding!r}: one of {', '.join(ENCODINGS)}")
    if encoding == "br":
        _brotli()


def encode(encoding: str, data: bytes) -> bytes:
    """`data` in `encoding` ("gzip" or "br")."""
    if encoding == "gzip":
        return gzip_bytes(data)
    if encoding == "br":
        return brotli_bytes(data)
    raise ValueError(f"asset encoding {encoding!r}: gzip or br")


def decode(name: str, data: bytes) -> bytes:
    """The decoded bytes of a stored file named `name` (or given its suffix): .gz and .br decoded, other names as
    they are."""
    if name.endswith(".gz"):
        return gzip.decompress(data)
    if name.endswith(".br"):
        return _brotli().decompress(data)
    return data


def holds(path: Path, data: bytes) -> bool:
    """True when the stored file at `path` decodes to `data` (False for a damaged or truncated one)."""
    path = Path(path)
    errors = (OSError, EOFError, zlib.error) + ((_brotli().error,) if path.name.endswith(".br") else ())
    try:
        return decode(path.name, path.read_bytes()) == data
    except errors:
        return False


def decoded_size(path: Path) -> int:
    """The decoded size of a stored file: its size, the length field of a .gz member (below 4 GiB), the length of a
    .br stream once decoded."""
    path = Path(path)
    if path.name.endswith(".gz"):
        with open(path, "rb") as f:
            f.seek(-4, 2)
            return struct.unpack("<I", f.read(4))[0]
    if path.name.endswith(".br"):
        return len(decode(path.name, path.read_bytes()))
    return path.stat().st_size
