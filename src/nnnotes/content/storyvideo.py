"""The document of one story video: a Movie / Clip command's `Cri/Video/<asset>` key.

The key loads a CriWare.Assets MonoBehaviour that describes the movie (`movieInfo`: size, display size, frame rate,
frame count, codec) and the movie itself, a USM. cri.movie converts the USM to `movie.webm` (VP9 as stored, or
re-encoded as VP9 when the video is in another codec or has an alpha stream, the alpha becoming the WebM's alpha
channel; the first audio stream as Opus). video.json carries the movie's description and the tracks of `movie.webm`,
so the product keeps no objects.
"""

from __future__ import annotations

import re

from .prefabs import Objects, has_json

__all__ = ["CODECS", "KEY_PREFIX", "VIDEO_FORMAT", "manifest_name", "video"]

VIDEO_FORMAT = 1  # video.json "format"
KEY_PREFIX = "Cri/Video/"
ASSET = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")  # the asset part of a video key
CODECS = {1: "sofdec.prime", 5: "h264", 9: "vp9"}  # movieInfo.codecType (CriMana.CodecType)


def manifest_name(key: str) -> str:
    """The manifest name of the video key `key` (`Cri/Video/<asset>`): `<asset>` with "/" as "~" (ValueError for
    another key)."""
    asset = key[len(KEY_PREFIX) :] if key.startswith(KEY_PREFIX) else ""
    if not ASSET.fullmatch(asset):
        raise ValueError(f"{key!r}: not a video key ({KEY_PREFIX}<asset>)")
    return asset.replace("/", "~")


def _info(objects: Objects, key: str) -> dict:
    """The `movieInfo` of the CriWare.Assets MonoBehaviour among the objects of `key`."""
    hits = [o for o in objects.key(key) if has_json(objects, o) and "movieInfo" in objects.json(o)]
    if len(hits) != 1:
        raise ValueError(f"{key}: {len(hits)} MonoBehaviours with a movieInfo, expected one")
    return objects.json(hits[0])["movieInfo"]


def video(objects: Objects, key: str, tracks: list[dict]) -> dict:
    """video.json of the video key `key`: `format`, `file` (movie.webm), the movie's `codec` (as stored), `width` and
    `height` (the coded size), `displayWidth` and `displayHeight`, `frameRate` [numerator, denominator], `frames`, and
    `tracks`, those of movie.webm (cri.movie: kind, channel, codec and, for a converted stream, its `source` codec)."""
    mi = _info(objects, key)
    return {
        "format": VIDEO_FORMAT,
        "file": "movie.webm",
        "codec": CODECS.get(mi["codecType"], f"codecType {mi['codecType']}"),
        "width": mi["width"],
        "height": mi["height"],
        "displayWidth": mi["dispWidth"],
        "displayHeight": mi["dispHeight"],
        "frameRate": [mi["framerateN"], mi["framerateD"]],
        "frames": mi["totalFrames"],
        "tracks": tracks,
    }
