"""Finite HCA framing, CRC validation and cipher-56 substitution with caller-supplied keys.

The key permutation follows the public HCA cipher described by VGAudio's CriHcaKey (MIT).
https://github.com/Thealexbarney/VGAudio/blob/master/src/VGAudio/Codecs/CriHca/CriHcaKey.cs
"""

from __future__ import annotations

import struct

import numpy as np

__all__ = ["crc16", "crypt", "inspect", "key_table"]


def _crc_table() -> np.ndarray:
    table = np.arange(256, dtype=np.uint16) << 8
    for _ in range(8):
        table = (table << 1) ^ np.where(table & 0x8000, 0x8005, 0).astype(np.uint16)
    return table


_CRC = _crc_table()


def crc16(data: bytes | bytearray) -> int:
    """CRC16 with polynomial 0x8005 and initial value zero."""
    value = 0
    for byte in data:
        value = ((value << 8) ^ int(_CRC[(value >> 8) ^ byte])) & 65535
    return value


def _tag(data: bytes | bytearray) -> bytes:
    return bytes(value & 127 for value in data)


def inspect(data: bytes, *, check_crc: bool = True) -> dict:
    """Read a finite mono/stereo HCA, rejecting truncated frames, loops and invalid checksums."""
    if len(data) < 48 or _tag(data[:4]) != b"HCA\0" or _tag(data[8:12]) != b"fmt\0":
        raise ValueError("invalid HCA header")
    version, header_size = struct.unpack_from(">HH", data, 4)
    channels, rate = data[12], int.from_bytes(data[13:16], "big")
    frames, inserted, appended = struct.unpack_from(">IHH", data, 16)
    at, frame_size, cipher, volume, tags = 24, 0, 0, 1.0, [0, 8]
    if not 48 <= header_size <= len(data):
        raise ValueError("invalid HCA header size")
    while at < header_size - 2:
        tag = _tag(data[at : at + 4])
        tags.append(at)
        if tag in (b"pad\0", b"comm"):
            break
        if tag == b"loop":
            raise ValueError("HCA input must have finite playback")
        sizes = {b"comp": 16, b"dec\0": 12, b"vbr\0": 8, b"ath\0": 6, b"ciph": 6, b"rva\0": 8}
        if tag not in sizes or at + sizes[tag] > header_size - 2:
            raise ValueError(f"unsupported or truncated HCA chunk at {at}")
        if tag in (b"comp", b"dec\0"):
            frame_size = struct.unpack_from(">H", data, at + 4)[0]
        elif tag == b"ciph":
            cipher = struct.unpack_from(">H", data, at + 4)[0]
        elif tag == b"rva\0":
            volume = struct.unpack_from(">f", data, at + 4)[0]
        at += sizes[tag]
    samples = frames * 1024 - inserted - appended
    if channels not in (1, 2) or rate <= 0 or samples <= 0 or frame_size < 8 or cipher not in (0, 56):
        raise ValueError("unsupported HCA dimensions or cipher")
    if len(data) != header_size + frames * frame_size:
        raise ValueError("HCA frames are truncated or contain trailing data")
    if volume != 1.0:
        raise ValueError("HCA relative volume must be 1")
    if check_crc:
        if crc16(data[: header_size - 2]) != int.from_bytes(data[header_size - 2 : header_size], "big"):
            raise ValueError("HCA header CRC mismatch")
        blocks = np.frombuffer(data, dtype=np.uint8, offset=header_size).reshape(frames, frame_size)
        sums = np.zeros(frames, dtype=np.uint16)
        for index in range(frame_size - 2):
            sums = (sums << 8) ^ _CRC[(sums >> 8) ^ blocks[:, index]]
        expected = (blocks[:, -2].astype(np.uint16) << 8) | blocks[:, -1]
        if np.any(sums != expected):
            raise ValueError("HCA frame CRC mismatch")
    return {
        "codec": "hca",
        "version": version,
        "channels": channels,
        "sampleRate": rate,
        "samples": samples,
        "frames": frames,
        "frameSize": frame_size,
        "headerSize": header_size,
        "inserted": inserted,
        "appended": appended,
        "cipherType": cipher,
        "lengthMs": (samples * 1000 + rate - 1) // rate,
        "tagOffsets": tags,
    }


def key_table(key: int, *, decrypt: bool = False) -> bytes:
    """Build the cipher-56 substitution permutation for a nonzero unsigned 64-bit key."""
    if isinstance(key, bool) or not isinstance(key, int) or not 0 < key < 1 << 64:
        raise ValueError("cipher 56 requires a nonzero unsigned 64-bit key")
    values = (key - 1).to_bytes(8, "little")
    a, b, c, d, e, f, g = values[:7]
    seeds = (b, g ^ b, c ^ d, c, b ^ c, d ^ e, d, c ^ d, e ^ f, e, d ^ e, f ^ g, f, e ^ f, g ^ b, g)

    def row(seed):
        value, result = seed >> 4, []
        for _ in range(16):
            value = (value * (((seed & 1) << 3) | 5) + ((seed & 14) | 1)) & 15
            result.append(value)
        return result

    grid = [(high << 4) | low for high, seed in zip(row(a), seeds) for low in row(seed)]
    table = bytes([0, *[grid[(i * 17) & 255] for i in range(1, 257) if grid[(i * 17) & 255] not in (0, 255)], 255])
    if decrypt:
        return table
    inverse = bytearray(256)
    for i, value in enumerate(table):
        inverse[value] = i
    return bytes(inverse)


def crypt(data: bytes, key: int, *, decrypt: bool = False) -> bytes:
    """Transform a finite HCA between cipher 0 and 56, rebuilding chunk flags and CRCs."""
    info = inspect(data)
    if info["cipherType"] != (56 if decrypt else 0):
        raise ValueError("HCA cipher does not match the requested transform")
    head = bytearray(data[: info["headerSize"]])
    cipher = None
    for at in info["tagOffsets"]:
        if _tag(head[at : at + 4]) == b"ciph":
            cipher = at
        for index in range(at, at + 4):
            value = head[index] & 127
            head[index] = value if decrypt or value == 0 else value | 128
    if cipher is None:
        raise ValueError("HCA cipher chunk is required")
    struct.pack_into(">H", head, cipher + 4, 0 if decrypt else 56)
    head[-2:] = struct.pack(">H", crc16(head[:-2]))
    translated = np.frombuffer(data[info["headerSize"] :], np.uint8).copy().reshape(info["frames"], info["frameSize"])
    table = np.frombuffer(key_table(key, decrypt=decrypt), np.uint8)
    translated[:, :-2] = table[translated[:, :-2]]
    sums = np.zeros(info["frames"], dtype=np.uint16)
    for index in range(info["frameSize"] - 2):
        sums = (sums << 8) ^ _CRC[(sums >> 8) ^ translated[:, index]]
    translated[:, -2], translated[:, -1] = sums >> 8, sums & 255
    output = bytes(head) + translated.tobytes()
    inspect(output)
    return output
