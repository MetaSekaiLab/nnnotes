"""The UnityFS container: the bytes a bundle stores, and the checksum a client compares against the catalog.

A bundle has a header, compressed block and directory information, and file data divided into blocks. The content
CRC describes the concatenated decompressed blocks. The header and block/directory information are excluded.
A catalog entry's `AssetBundleRequestOptions.crc` is a value of this kind:

    from nnnotes.unity import bundlefs
    crc = bundlefs.content_crc(Path("assets.bundle").read_bytes())

The header flags decide the layout: the low six bits are the compression of the block information; each block has
its own compression flags
(0 none, 1 LZMA, 2/3 LZ4), `0x80` puts the block table at the end of the file, and `0x200` pads the block data to a
16-byte boundary. A version 7 or later header is itself padded to 16 bytes after its strings.
"""

from __future__ import annotations

import lzma
import struct
import zlib
from dataclasses import dataclass

import lz4.block

__all__ = ["Header", "content", "content_crc", "parse_header"]

SIGNATURE = b"UnityFS\0"
ALIGNMENT = 16
BLOCK_TABLE_AT_END = 0x80
BLOCK_INFO_PADDING = 0x200
ENCRYPTED = 0x400  # a key the container carries itself, not one a caller can supply
COMPRESSION_MASK = 0x3F
COMPRESSION_NONE = 0
COMPRESSION_LZMA = 1
COMPRESSION_LZ4 = 2
COMPRESSION_LZ4HC = 3
HASH_SIZE = 16
BLOCK_ENTRY = 10  # uncompressed size (u32), compressed size (u32), flags (u16)


@dataclass(frozen=True)
class Header:
    """The fields of a UnityFS header that decide where the block table and the block data are."""

    version: int
    engine: str
    revision: str
    size: int
    info_compressed: int
    info_uncompressed: int
    flags: int
    offset: int  # where the block table or the block data starts, whichever comes first

    @property
    def compression(self) -> int:
        return self.flags & COMPRESSION_MASK

    @property
    def table_at_end(self) -> bool:
        return bool(self.flags & BLOCK_TABLE_AT_END)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & ENCRYPTED)


def _string(data: bytes, at: int) -> tuple[str, int]:
    end = data.index(b"\0", at)
    return data[at:end].decode("utf-8", "replace"), end + 1


def _aligned(offset: int) -> int:
    return (offset + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def _decompress(data: bytes, compression: int, uncompressed: int) -> bytes:
    """One compressed region -> its bytes; `uncompressed` is what the header says it expands to."""
    if compression == COMPRESSION_NONE:
        result = data
    elif compression in (COMPRESSION_LZ4, COMPRESSION_LZ4HC):
        try:
            result = lz4.block.decompress(data, uncompressed_size=uncompressed)
        except lz4.block.LZ4BlockError as e:
            raise ValueError(f"an LZ4 region of this bundle does not decompress: {e}") from None
    elif compression == COMPRESSION_LZMA:
        # LZMA1 raw: one byte of lc/lp/pb, four of dictionary size, then the stream
        try:
            props, dictionary = struct.unpack("<BI", data[:5])
            remainder = props // 9
            decompressor = lzma.LZMADecompressor(
                format=lzma.FORMAT_RAW,
                filters=[
                    {
                        "id": lzma.FILTER_LZMA1,
                        "dict_size": dictionary,
                        "lc": props % 9,
                        "lp": remainder % 5,
                        "pb": remainder // 5,
                    }
                ],
            )
            result = decompressor.decompress(data[5:], max_length=uncompressed + 1)
        except (struct.error, lzma.LZMAError, ValueError) as e:
            raise ValueError(f"an LZMA region of this bundle does not decompress: {e}") from None
    else:
        raise ValueError(f"unknown UnityFS compression {compression}")
    if len(result) != uncompressed:
        raise ValueError(f"UnityFS region size mismatch: expected {uncompressed}, got {len(result)}")
    return result


def parse_header(data: bytes) -> Header:
    """The header of a UnityFS bundle; ValueError when the file is not one."""
    if not data.startswith(SIGNATURE):
        raise ValueError("not a UnityFS bundle")
    try:
        version = struct.unpack_from(">I", data, len(SIGNATURE))[0]
        engine, at = _string(data, len(SIGNATURE) + 4)
        revision, at = _string(data, at)
        size, info_compressed, info_uncompressed, flags = struct.unpack_from(">qIII", data, at)
    except (struct.error, ValueError) as e:
        raise ValueError(f"truncated or corrupt UnityFS header: {e}") from None
    at += 20
    if version >= 7:
        at = _aligned(at)
    header = Header(version, engine, revision, size, info_compressed, info_uncompressed, flags, at)
    if size != len(data) or at > len(data):
        raise ValueError("UnityFS file size does not match its header")
    if header.encrypted:
        raise ValueError("the bundle carries its own encryption key, which its header does not expose")
    return header


def _block_table(data: bytes, header: Header) -> tuple[list[tuple[int, int, int]], int]:
    """The block table and where the block data starts."""
    if header.table_at_end:
        at = len(data) - header.info_compressed
        body = header.offset
    else:
        at = header.offset
        body = at + header.info_compressed
    if at < header.offset or at + header.info_compressed > len(data):
        raise ValueError("UnityFS block information lies outside the file")
    table = _decompress(data[at : at + header.info_compressed], header.compression, header.info_uncompressed)
    try:
        count = struct.unpack_from(">I", table, HASH_SIZE)[0]
        at = HASH_SIZE + 4
        if count > (len(table) - at) // BLOCK_ENTRY:
            raise ValueError("UnityFS block count exceeds its block information")
        blocks = []
        for _ in range(count):
            uncompressed, compressed, flags = struct.unpack_from(">IIH", table, at)
            at += BLOCK_ENTRY
            blocks.append((uncompressed, compressed, flags & COMPRESSION_MASK))
    except struct.error:
        raise ValueError("truncated UnityFS block information") from None
    if header.flags & BLOCK_INFO_PADDING:
        body = _aligned(body)
    end = len(data) - header.info_compressed if header.table_at_end else len(data)
    if body + sum(block[1] for block in blocks) > end:
        raise ValueError("UnityFS block data lies outside the content region")
    return blocks, body


def content(data: bytes) -> bytes:
    """Concatenated decompressed file data, excluding the header and block/directory information."""
    header = parse_header(data)
    blocks, at = _block_table(data, header)
    out = []
    for uncompressed, compressed, compression in blocks:
        out.append(_decompress(data[at : at + compressed], compression, uncompressed))
        at += compressed
    return b"".join(out)


def content_crc(data: bytes) -> int:
    """The CRC32 a catalog entry has to carry for this bundle: the checksum of `content`, as unsigned."""
    return zlib.crc32(content(data)) & 0xFFFFFFFF
