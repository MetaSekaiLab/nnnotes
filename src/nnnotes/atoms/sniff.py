"""Atom `sniff`: what a byte string is, from its content.

`sniff(data, hint)` returns the format, the file extension and the media type. Binary formats are recognized by
their magic numbers (the table below); then JSON (strictly: UTF-8 text that parses as JSON, a Spine skeleton when it
has a `skeleton.spine` version), UTF-8 text, and otherwise `binary`. `hint`, a file name, only chooses among
equivalent answers: a text extension it ends with (`.csv`, `.atlas`, ...; a trailing `.bytes` is skipped) for text,
`.acf` for a CRI table. Magic numbers take precedence over the hint.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import estimate
from .. import contract


@dataclass(frozen=True)
class Sniffed:
    format: str
    ext: str
    media_type: str


# (offset, magic, format, extension, media type); the first match wins
MAGIC: tuple = (
    (0, b"\x89PNG\r\n\x1a\n", "png", "png", "image/png"),
    (0, b"\xff\xd8\xff", "jpeg", "jpg", "image/jpeg"),
    (0, b"GIF87a", "gif", "gif", "image/gif"),
    (0, b"GIF89a", "gif", "gif", "image/gif"),
    (0, b"\x13\xab\xa1\x5c", "astc", "astc", "image/astc"),
    (0, b"DDS ", "dds", "dds", "image/vnd-ms.dds"),
    (0, b"\xabKTX 11\xbb\r\n\x1a\n", "ktx", "ktx", "image/ktx"),
    (0, b"\xabKTX 20\xbb\r\n\x1a\n", "ktx2", "ktx2", "image/ktx2"),
    (0, b"OTTO", "otf", "otf", "font/otf"),
    (0, b"\x00\x01\x00\x00", "ttf", "ttf", "font/ttf"),
    (0, b"true", "ttf", "ttf", "font/ttf"),
    (0, b"ttcf", "ttc", "ttc", "font/collection"),
    (0, b"wOFF", "woff", "woff", "font/woff"),
    (0, b"wOF2", "woff2", "woff2", "font/woff2"),
    (0, b"@UTF", "cri-utf", "acb", "application/octet-stream"),
    (0, b"AFS2", "afs2", "awb", "application/octet-stream"),
    (0, b"CRID", "usm", "usm", "application/octet-stream"),
    (0, b"HCA\x00", "hca", "hca", "application/octet-stream"),
    (0, b"\xc8\xc3\xc1\x00", "hca", "hca", "application/octet-stream"),       # HCA with masked chunk names
    (0, b"UnityFS\x00", "unityfs", "bundle", "application/octet-stream"),
    (0, b"UnityWeb\x00", "unityweb", "bundle", "application/octet-stream"),
    (0, b"UnityRaw\x00", "unityraw", "bundle", "application/octet-stream"),
    (0, b"UnityArchive", "unityarchive", "bundle", "application/octet-stream"),
    (0, b"MOC3", "moc3", "moc3", "application/octet-stream"),
    (0, b"glTF", "glb", "glb", "model/gltf-binary"),
    (0, b"OggS", "ogg", "ogg", "audio/ogg"),
    (0, b"fLaC", "flac", "flac", "audio/flac"),
    (0, b"ID3", "mp3", "mp3", "audio/mpeg"),
    (0, b"MThd", "midi", "mid", "audio/midi"),
    (0, b"DKIF", "ivf", "ivf", "video/x-ivf"),
    (0, b"\x1a\x45\xdf\xa3", "matroska", "mkv", "video/x-matroska"),
    (4, b"ftyp", "mp4", "mp4", "video/mp4"),
    (0, b"%PDF-", "pdf", "pdf", "application/pdf"),
    (0, b"\x1f\x8b", "gzip", "gz", "application/gzip"),
    (0, b"PK\x03\x04", "zip", "zip", "application/zip"),
    (0, b"PK\x05\x06", "zip", "zip", "application/zip"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "7z", "application/x-7z-compressed"),
    (0, b"\xfd7zXZ\x00", "xz", "xz", "application/x-xz"),
    (0, b"\x28\xb5\x2f\xfd", "zstd", "zst", "application/zstd"),
    (0, b"\x04\x22\x4d\x18", "lz4", "lz4", "application/octet-stream"),
)
# RIFF containers by form type
RIFF = {b"WAVE": ("wav", "wav", "audio/wav"), b"WEBP": ("webp", "webp", "image/webp"),
        b"AVI ": ("avi", "avi", "video/x-msvideo")}
TEXT_EXTS = frozenset({"txt", "csv", "tsv", "xml", "yaml", "yml", "ini", "md", "html", "css", "js", "lua", "atlas",
                       "srt", "vtt", "svg", "plist"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")      # not in text: C0 controls except \t \n \f \r, DEL
_SPINE_VERSION = re.compile(r"[34]\.\d+(\.\d+)*")


def _media(ext: str, fallback: str = "application/octet-stream") -> str:
    m = contract.media_type(ext)
    return fallback if m == "application/octet-stream" else m


def _hint_ext(hint: str | None) -> str:
    if not hint:
        return ""
    parts = re.split(r"[\\/]", hint)[-1].lower().split(".")[1:]
    if parts and parts[-1] == "bytes":
        parts.pop()
    return parts[-1] if parts else ""


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """Spine's variable-length unsigned int (7 bits per byte, least significant first, at most 5 bytes)."""
    value = shift = 0
    for i in range(5):
        if pos + i >= len(data):
            raise ValueError
        b = data[pos + i]
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos + i + 1
        shift += 7
    raise ValueError


def _spine_string(data: bytes, pos: int) -> tuple[str, int]:
    n, pos = _varint(data, pos)
    if n == 0 or pos + n - 1 > len(data):
        raise ValueError
    return data[pos:pos + n - 1].decode("ascii"), pos + n - 1


def spine_binary_version(data: bytes) -> str | None:
    """The Spine version a binary skeleton (.skel) states, None if `data` is not one: 4.x files start with an
    8-byte hash then the version string, 3.x files with the hash string then the version string."""
    for start in (8, None):
        try:
            pos = start if start is not None else _spine_string(data, 0)[1]
            version = _spine_string(data, pos)[0]
        except (ValueError, UnicodeDecodeError):
            continue
        if _SPINE_VERSION.fullmatch(version) and version[0] == ("4" if start is not None else "3"):
            return version
    return None


def _sfnt(data: bytes) -> bool:
    """An OpenType / TrueType table directory: a table count and the search range that goes with it."""
    if len(data) < 12:
        return False
    n, search = int.from_bytes(data[4:6], "big"), int.from_bytes(data[6:8], "big")
    return 0 < n <= 512 and len(data) >= 12 + 16 * n and search == 16 << (n.bit_length() - 1)


# checks beyond the magic, for magics short or common enough to begin other data
VALIDATE = {
    "ttf": _sfnt, "otf": _sfnt,
    "gzip": lambda d: d[2:3] == b"\x08",
    "midi": lambda d: d[4:8] == b"\x00\x00\x00\x06",
    "glb": lambda d: d[4:8] in (b"\x01\x00\x00\x00", b"\x02\x00\x00\x00"),
}


def _adx(data: bytes) -> bool:
    if len(data) < 6 or data[:2] != b"\x80\x00":
        return False
    off = int.from_bytes(data[2:4], "big")
    return data[off - 2:off + 4] == b"(c)CRI"


def sniff(data: bytes, hint: str | None = None) -> Sniffed:
    data = bytes(data)
    if not data:
        return Sniffed("empty", "bin", "application/octet-stream")
    head = data[:16]
    for off, magic, fmt, ext, media in MAGIC:
        if head[off:off + len(magic)] == magic and VALIDATE.get(fmt, bool)(data):
            if fmt == "cri-utf" and _hint_ext(hint) == "acf":
                ext = "acf"
            return Sniffed(fmt, ext, media)
    if head[:4] == b"RIFF" and head[8:12] in RIFF:
        return Sniffed(*RIFF[head[8:12]])
    if _adx(data):
        return Sniffed("adx", "adx", "audio/x-adx")
    version = spine_binary_version(data)
    if version is not None:
        return Sniffed("spine-skel", "skel", "application/octet-stream")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Sniffed("binary", "bin", "application/octet-stream")
    if text.startswith("﻿"):
        text = text[1:]
    if _CONTROL.search(text):
        return Sniffed("binary", "bin", "application/octet-stream")
    stripped = text.lstrip(" \t\r\n")
    if stripped[:1] in ("{", "["):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        else:
            skeleton = doc.get("skeleton") if isinstance(doc, dict) else None
            if isinstance(skeleton, dict) and isinstance(skeleton.get("spine"), str):
                return Sniffed("spine-json", "json", "application/json")
            return Sniffed("json", "json", "application/json")
    ext = _hint_ext(hint)
    if stripped.startswith("<?xml") and ext not in TEXT_EXTS:
        ext = "xml"
    if ext not in TEXT_EXTS:
        ext = "txt"
    return Sniffed("text", ext, _media(ext, "text/plain"))


def cost(facts: dict) -> dict:
    """facts: size."""
    n = int(facts.get("size", 0))
    return estimate(1e-5 + n * 2e-9, n * 3)
