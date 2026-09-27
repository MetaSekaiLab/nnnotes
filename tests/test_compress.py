import gzip
import json
import random
import struct
import zlib

import pytest

from nnnotes import compress

TEXT = json.dumps({str(i): [i, i * i, f"v{i % 7}"] for i in range(500)}).encode()
NOISE = random.Random(7).randbytes(4096)


@pytest.mark.parametrize("data", [b"", b"a", TEXT, NOISE])
def test_gzip_bytes_round_trip(data):
    out = compress.gzip_bytes(data)
    assert out[:10] == bytes.fromhex("1f 8b 08 00 00 00 00 00 02 ff")
    assert out[-8:] == struct.pack("<II", zlib.crc32(data), len(data))
    assert gzip.decompress(out) == data
    assert compress.decode(".gz", out) == compress.decode("assets/x.json.gz", out) == data
    assert compress.gzip_bytes(data) == out


@pytest.mark.skipif(hasattr(zlib, "ZLIBNG_VERSION") or "ng" in zlib.ZLIB_RUNTIME_VERSION,
                    reason="zlib-ng writes other deflate streams")
def test_gzip_bytes_are_fixed():
    data = b'{"a":[1,2,3],"b":"' + b"xyz" * 40 + b'"}'
    assert compress.gzip_bytes(data).hex() == ("1f8b08000000000002ffab564a54b28a36d431d2318ed5514a52b252aaa8ac1a10a4540b"
                                               "008c9586d38c000000")


def test_brotli_round_trip():
    brotli = pytest.importorskip("brotli")
    out = compress.brotli_bytes(TEXT)
    assert out == brotli.compress(TEXT, quality=11) == compress.encode("br", TEXT)
    assert len(out) < len(TEXT) and compress.decode(".br", out) == TEXT


def test_encode_decode_and_names():
    assert compress.encode("gzip", TEXT) == compress.gzip_bytes(TEXT)
    with pytest.raises(ValueError):
        compress.encode("none", TEXT)
    assert compress.decode("assets/x.json", TEXT) == TEXT          # other names: as they are
    assert compress.SUFFIXES == {"gzip": ".gz", "br": ".br"}
    assert {"json", "glsl", "moc3", "atlas", "skel", "bin", "wav", "glb"} == compress.COMPRESSIBLE
    compress.check("none")
    with pytest.raises(ValueError, match="one of gzip, br, none"):
        compress.check("zstd")


def test_holds_and_decoded_size(tmp_path):
    gz = tmp_path / "a.json.gz"
    gz.write_bytes(compress.gzip_bytes(TEXT))
    assert compress.holds(gz, TEXT) and not compress.holds(gz, TEXT + b" ")
    assert compress.decoded_size(gz) == len(TEXT)
    gz.write_bytes(compress.gzip_bytes(TEXT)[:-9])                  # truncated
    assert not compress.holds(gz, TEXT)
    raw = tmp_path / "a.json"
    raw.write_bytes(TEXT)
    assert compress.decoded_size(raw) == len(TEXT)
    pytest.importorskip("brotli")
    br = tmp_path / "a.json.br"
    br.write_bytes(compress.brotli_bytes(TEXT))
    assert compress.holds(br, TEXT) and compress.decoded_size(br) == len(TEXT)
    br.write_bytes(b"not brotli")
    assert not compress.holds(br, TEXT)
