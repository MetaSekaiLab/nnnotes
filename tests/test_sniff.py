"""sniff: the magic-number table, JSON / text detection, the hint's limited role and the validation of short magics."""
import gzip
import io
import json
import struct
import zipfile

import pytest

from nnnotes import contract
from nnnotes.atoms import ATOMS
from nnnotes.atoms.sniff import MAGIC, Sniffed, sniff, spine_binary_version


def sfnt(tag: bytes, tables: int = 3) -> bytes:
    search = 16 << (tables.bit_length() - 1)
    return tag + struct.pack(">HHHH", tables, search, 0, 0) + b"\0" * (16 * tables)


def spine4(version="4.1.23") -> bytes:
    v = version.encode()
    return b"\x01\x02\x03\x04\x05\x06\x07\x08" + bytes([len(v) + 1]) + v + b"\0" * 20


def spine3(version="3.8.99") -> bytes:
    h, v = b"qnBnkZrXgbhX2bOJq1WBxc6HfdQ", version.encode()
    return bytes([len(h) + 1]) + h + bytes([len(v) + 1]) + v + b"\0" * 20


def adx() -> bytes:
    return b"\x80\x00\x00\x20" + b"\0" * 26 + b"(c)CRI" + b"\0" * 32


CASES = [
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 16, "png", "png"),
    (b"\xff\xd8\xff\xe0" + b"\0" * 16, "jpeg", "jpg"),
    (b"GIF89a" + b"\0" * 8, "gif", "gif"),
    (b"RIFF\0\0\0\0WEBPVP8 ", "webp", "webp"),
    (b"RIFF\0\0\0\0WAVEfmt ", "wav", "wav"),
    (sfnt(b"OTTO"), "otf", "otf"),
    (sfnt(b"\x00\x01\x00\x00", 12), "ttf", "ttf"),
    (sfnt(b"true"), "ttf", "ttf"),
    (b"ttcf\x00\x01\x00\x00" + b"\0" * 8, "ttc", "ttc"),
    (b"wOFF" + b"\0" * 40, "woff", "woff"),
    (b"wOF2" + b"\0" * 40, "woff2", "woff2"),
    (b"@UTF\0\0\x01\0" + b"\0" * 8, "cri-utf", "acb"),
    (b"AFS2\x02\x04\x02\0" + b"\0" * 8, "afs2", "awb"),
    (b"CRID\0\0\0\x18" + b"\0" * 8, "usm", "usm"),
    (b"HCA\0\x03\0\0\x60" + b"\0" * 8, "hca", "hca"),
    (b"\xc8\xc3\xc1\0\x03\0\0\x60" + b"\0" * 8, "hca", "hca"),
    (b"UnityFS\x00\0\0\0\x08" + b"\0" * 8, "unityfs", "bundle"),
    (b"MOC3\x04" + b"\0" * 60, "moc3", "moc3"),
    (spine4(), "spine-skel", "skel"),
    (spine3(), "spine-skel", "skel"),
    (json.dumps({"skeleton": {"hash": "x", "spine": "4.1.23"}, "bones": []}).encode(), "spine-json", "json"),
    (gzip.compress(b"hello", mtime=0), "gzip", "gz"),
    (b"\x13\xab\xa1\x5c\x06\x06\x01" + b"\0" * 9, "astc", "astc"),
    (b"glTF\x02\0\0\0" + b"\0" * 8, "glb", "glb"),
    (b"OggS\0\x02" + b"\0" * 10, "ogg", "ogg"),
    (b"fLaC\0\0\0\x22" + b"\0" * 8, "flac", "flac"),
    (b"DKIF\0\0\x20\0VP90", "ivf", "ivf"),
    (b"\x1a\x45\xdf\xa3\x01\0\0\0", "matroska", "mkv"),
    (b"\0\0\0\x20ftypisom" + b"\0" * 4, "mp4", "mp4"),
    (b"MThd\0\0\0\x06\0\x01\0\x02\x01\xe0", "midi", "mid"),
    (adx(), "adx", "adx"),
]


@pytest.mark.parametrize("data, fmt, ext", CASES, ids=[c[1] + "-" + str(i) for i, c in enumerate(CASES)])
def test_magic_table(data, fmt, ext):
    s = sniff(data)
    assert (s.format, s.ext) == (fmt, ext)


def test_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "x")
    assert sniff(buf.getvalue()) == Sniffed("zip", "zip", "application/zip")


@pytest.mark.parametrize("text, ext", [('{"a": [1, 2]}', "json"), ("[1, 2, 3]\n", "json"),
                                       ("﻿{\"k\": \"字\"}", "json"), ("  \n{\"a\": 1e999}", "json")])
def test_json(text, ext):
    s = sniff(text.encode("utf-8"))
    assert (s.format, s.ext, s.media_type) == ("json", ext, "application/json")


def test_json_like_text_that_does_not_parse_is_text():
    assert sniff(b"{not json}") == Sniffed("text", "txt", "text/plain")


def test_text_and_the_hint():
    atlas = b"page.png\nsize: 1024,1024\nformat: RGBA8888\n"
    assert sniff(atlas) == Sniffed("text", "txt", "text/plain")
    assert sniff(atlas, "Assets/x/char.atlas.txt").ext == "txt"
    assert sniff(atlas, "Assets/x/char.atlas.bytes").ext == "atlas"
    assert sniff(b"a,b\n1,2\n", "t.csv") == Sniffed("text", "csv", "text/csv")
    assert sniff(b"<?xml version='1.0'?><a/>") == Sniffed("text", "xml", "application/xml")
    assert sniff(b"plain \xe6\x96\x87\xe5\xad\x97\n", "x.unknownext").ext == "txt"


def test_magic_wins_over_the_hint_and_the_hint_picks_acf():
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "x.txt").format == "png"
    assert sniff(b"@UTF" + b"\0" * 12, "Cri/x.acf").ext == "acf"
    assert sniff(b"@UTF" + b"\0" * 12, "Cri/x.acb.bytes").ext == "acb"


@pytest.mark.parametrize("data", [b"\x00\x01\x00\x00garbage-garbage-garbage", b"true or false\n", b"OTTO\n",
                                  b"\x1f\x8b\x00junk", b"MThd-not-midi", b"glTF but text"])
def test_short_magics_are_validated(data):
    assert sniff(data).format in ("binary", "text")


def test_binary_empty_and_control_characters():
    assert sniff(b"") == Sniffed("empty", "bin", "application/octet-stream")
    assert sniff(bytes(range(256))) == Sniffed("binary", "bin", "application/octet-stream")
    assert sniff(b"abc\x00def").format == "binary"
    assert sniff(b"\xff\xfe\x00\x01").format == "binary"                 # not UTF-8
    assert sniff(b"tab\tnewline\r\nform\x0c").format == "text"


def test_spine_versions():
    assert spine_binary_version(spine4("4.2.11")) == "4.2.11"
    assert spine_binary_version(spine3("3.8.99")) == "3.8.99"
    assert spine_binary_version(b"\0" * 8 + b"\x063.8.9") is None          # a 3.x version after an 8-byte hash
    assert spine_binary_version(b"\x05abcd\x05efgh") is None


def test_media_types_agree_with_the_contract():
    for _, _, fmt, ext, media in MAGIC:
        if ext in contract.MEDIA_TYPES:
            assert contract.media_type(ext) == media, ext


def test_registry_entry():
    assert ATOMS["sniff"].fn is sniff and ATOMS["sniff"].cost({"size": 100})["peakBytes"] == 300
