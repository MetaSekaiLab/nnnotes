"""The ffmpeg encoder settings the audio and movie writers share. Every encode is bit-exact: the same input and ffmpeg
build give the same bytes."""

from __future__ import annotations

from pathlib import Path

FLAC_LEVEL = 8  # ffmpeg's FLAC compression level by default (-compression_level of the flac output)


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
