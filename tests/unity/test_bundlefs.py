import struct
import zlib

import pytest

from nnnotes.unity import bundlefs
from tests.helpers import synth

PAYLOADS = [b"file-table", b"x" * 5000, b""]


def test_content_is_the_blocks_in_order():
    data = synth.unityfs(PAYLOADS)
    assert bundlefs.content(data) == b"".join(PAYLOADS)


@pytest.mark.parametrize("compression", ["none", "lz4", "lzma"])
def test_every_compression_reads_back(compression):
    data = synth.unityfs(PAYLOADS, compression=compression)
    assert bundlefs.content(data) == b"".join(PAYLOADS)


@pytest.mark.parametrize("compression", ["none", "lz4", "lzma"])
def test_a_block_table_at_the_end(compression):
    data = synth.unityfs(PAYLOADS, compression=compression, table_at_end=True)
    assert bundlefs.content(data) == b"".join(PAYLOADS)


def test_padded_block_data():
    data = synth.unityfs(PAYLOADS, compression="lz4", padding=True, table_at_end=True)
    assert bundlefs.parse_header(data).flags & bundlefs.BLOCK_INFO_PADDING
    assert bundlefs.content(data) == b"".join(PAYLOADS)


def test_content_crc_is_the_crc_of_the_content_not_of_the_file():
    data = synth.unityfs(PAYLOADS, compression="lz4")
    content = b"".join(PAYLOADS)
    assert bundlefs.content_crc(data) == zlib.crc32(content)
    assert bundlefs.content_crc(data) != zlib.crc32(data)


def test_the_header_reports_the_layout():
    plain = bundlefs.parse_header(synth.unityfs(PAYLOADS))
    assert (plain.version, plain.compression, plain.table_at_end) == (8, bundlefs.COMPRESSION_NONE, False)
    lz4 = bundlefs.parse_header(synth.unityfs(PAYLOADS, compression="lz4", table_at_end=True))
    assert (lz4.compression, lz4.table_at_end) == (bundlefs.COMPRESSION_LZ4, True)


def test_a_file_that_is_not_a_bundle_is_rejected():
    with pytest.raises(ValueError):
        bundlefs.parse_header(b"PK\x03\x04 not a bundle")
    with pytest.raises(ValueError):
        bundlefs.content_crc(b"UnityFS\0 short")


def test_an_unknown_compression_is_rejected():
    data = synth.unityfs(PAYLOADS, bits=5)  # neither none, LZMA nor LZ4
    assert bundlefs.parse_header(data).compression == 5
    with pytest.raises(ValueError):
        bundlefs.content(data)


def test_a_container_that_carries_its_own_key_is_rejected():
    data = synth.unityfs(PAYLOADS, extra_flags=0x400)
    with pytest.raises(ValueError):
        bundlefs.parse_header(data)


def test_file_size_mismatch_is_rejected():
    data = synth.unityfs(PAYLOADS)
    with pytest.raises(ValueError, match="file size"):
        bundlefs.content(data[:-1])
    with pytest.raises(ValueError, match="file size"):
        bundlefs.content(data + b"extra")


def test_block_size_mismatch_is_rejected():
    data = bytearray(synth.unityfs([b"payload"]))
    at = bundlefs.parse_header(data).offset + bundlefs.HASH_SIZE + 4
    struct.pack_into(">I", data, at, 2)
    with pytest.raises(ValueError, match="region size mismatch"):
        bundlefs.content(data)


def test_block_count_is_checked_before_reading_records():
    data = bytearray(synth.unityfs([b"payload"]))
    at = bundlefs.parse_header(data).offset + bundlefs.HASH_SIZE
    struct.pack_into(">I", data, at, 2**32 - 1)
    with pytest.raises(ValueError, match="block count"):
        bundlefs.content(data)


def test_compressed_block_cannot_overlap_block_information():
    data = bytearray(synth.unityfs([b"payload"], table_at_end=True))
    header = bundlefs.parse_header(data)
    at = len(data) - header.info_compressed + bundlefs.HASH_SIZE + 4 + 4
    struct.pack_into(">I", data, at, len(data))
    with pytest.raises(ValueError, match="content region"):
        bundlefs.content(data)
