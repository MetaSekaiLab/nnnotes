"""Synthetic media container bytes for product conversion tests."""

import struct


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(delay: int) -> bytes:
    """An MP4 file whose audio track has an edit list starting at media time `delay` (encoders.mp4_priming)."""
    elst = box(b"elst", b"\0\0\0\0" + struct.pack(">IIiI", 1, 48000, delay, 0x10000))
    return box(b"ftyp", b"M4A \0\0\0\0") + box(b"moov", box(b"trak", box(b"edts", elst)))
