"""The ffmpeg encoder settings the audio and movie writers share. Every encode is bit-exact: the same input and ffmpeg
build give the same bytes.

WEB_AUDIO lists the audio formats for the web (`format` of the live.music product, `site build --format`): per format
the file suffix and the ffmpeg codec options (None: the FLAC as decoded). A browser's decodeAudioData decodes each
sample-aligned with the FLAC (the encoder delay and pre-skip removed); AAC-LC 160 kbit/s in MP4, the default, decodes
in Safari and iOS as well. A decoder that does not apply the MP4 edit list outputs `mp4_priming(data)` priming samples
first.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

FLAC_LEVEL = 8  # ffmpeg's FLAC compression level by default (-compression_level of the flac output)
WEB_AUDIO = {
    "aac": (".m4a", ["-c:a", "aac", "-b:a", "160k"]),
    "opus": (".opus", ["-c:a", "libopus", "-b:a", "128k"]),
    "vorbis": (".ogg", ["-c:a", "libvorbis", "-q:a", "5"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "flac": (".flac", None),
}
DEFAULT_AUDIO_FORMAT = "aac"


def encode_args(src: Path, dst: Path, args: list[str]) -> list[str]:
    """ffmpeg arguments (after the executable) of a bit-exact encode: no metadata, bitexact container and codec
    flags, then `args` (codec options)."""
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        *args,
        str(dst),
    ]


def web_encode(ffmpeg: str, src: Path, dst: Path, fmt: str) -> bytes:
    """The bytes of the audio file `src` in the web format `fmt` (WEB_AUDIO; a bit-exact encode written to `dst`;
    "flac": the file as it is)."""
    codec = WEB_AUDIO[fmt][1]
    if codec is None:
        return Path(src).read_bytes()
    r = subprocess.run([ffmpeg, *encode_args(src, dst, codec)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg {fmt} failed on {src}: {r.stderr[-2000:]}")
    return Path(dst).read_bytes()


def mp4_priming(data: bytes) -> int:
    """Encoder delay of an MP4 audio track: media_time of the first edit list entry (moov/trak/edts/elst)."""

    def boxes(b, off, end):
        while off + 8 <= end:
            size, kind = struct.unpack(">I4s", b[off : off + 8])
            hdr = 8
            if size == 1:
                size, hdr = struct.unpack(">Q", b[off + 8 : off + 16])[0], 16
            elif size == 0:
                size = end - off
            yield kind, off + hdr, off + size
            off += size

    def find(path, off, end):
        for kind, a, z in boxes(data, off, end):
            if kind == path[0]:
                return (a, z) if len(path) == 1 else find(path[1:], a, z)
        return None

    loc = find([b"moov", b"trak", b"edts", b"elst"], 0, len(data))
    if loc is None:
        return 0
    a, _ = loc
    version = data[a]
    count = struct.unpack(">I", data[a + 4 : a + 8])[0]
    if count < 1:
        return 0
    if version == 1:
        return max(0, struct.unpack(">q", data[a + 16 : a + 24])[0])
    return max(0, struct.unpack(">i", data[a + 12 : a + 16])[0])
