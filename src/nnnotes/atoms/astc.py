"""Atom `astc.container`: ASTC blocks in the standard `.astc` file layout.

A 16-byte header (magic 0x5CA1AB13, block width, height and depth, then the image width, height and depth as
24-bit little-endian integers) followed by the blocks of the first mip level, 16 bytes each, in the order they are
stored (rows of blocks from the first stored row). Texture data holding further mip levels is cut after the first.
"""
from __future__ import annotations

import struct

from . import estimate

MAGIC = 0x5CA1AB13
BLOCK_SIZES = frozenset({(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8), (10, 5), (10, 6), (10, 8),
                         (10, 10), (12, 10), (12, 12)})


def level0_size(width: int, height: int, block: tuple[int, int]) -> int:
    bx, by = block
    return -(-width // bx) * -(-height // by) * 16


def container(blocks: bytes, width: int, height: int, block: tuple[int, int]) -> bytes:
    """The .astc file of a `width` x `height` 2D image whose blocks (of `block` texels) start `blocks`."""
    block = tuple(block)
    if block not in BLOCK_SIZES:
        raise ValueError(f"ASTC block {block}: not a 2D ASTC block size")
    if not (0 < width < 1 << 24 and 0 < height < 1 << 24):
        raise ValueError(f"ASTC image {width}x{height}: sizes must be 1 .. 2^24 - 1")
    need = level0_size(width, height, block)
    if len(blocks) < need:
        raise ValueError(f"ASTC {width}x{height} {block[0]}x{block[1]}: {len(blocks)} bytes, {need} needed")
    head = struct.pack("<I3B", MAGIC, block[0], block[1], 1)
    dims = b"".join(v.to_bytes(3, "little") for v in (width, height, 1))
    return head + dims + bytes(blocks[:need])


def parse_header(data: bytes) -> dict:
    """{block: (x, y, z), size: (w, h, d)} of a .astc file's header."""
    if len(data) < 16:
        raise ValueError("ASTC header: fewer than 16 bytes")
    magic, bx, by, bz = struct.unpack_from("<I3B", data)
    if magic != MAGIC:
        raise ValueError(f"ASTC header: magic {magic:#010x}")
    size = tuple(int.from_bytes(data[7 + 3 * i:10 + 3 * i], "little") for i in range(3))
    return {"block": (bx, by, bz), "size": size}


def cost(facts: dict) -> dict:
    """facts: size (bytes of the blocks)."""
    n = int(facts.get("size", 0))
    return estimate(1e-5 + n * 2e-10, n * 2)
