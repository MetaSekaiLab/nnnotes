"""Media types of file extensions."""

from __future__ import annotations

__all__ = ["MEDIA_TYPES", "media_type"]

MEDIA_TYPES = {
    "png": "image/png",
    "astc": "image/astc",
    "jpg": "image/jpeg",
    "json": "application/json",
    "glb": "model/gltf-binary",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "mkv": "video/x-matroska",
    "ivf": "video/x-ivf",
    "adx": "audio/x-adx",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "ttc": "font/collection",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "txt": "text/plain",
    "xml": "application/xml",
    "csv": "text/csv",
    "bin": "application/octet-stream",
    "bytes": "application/octet-stream",
}


def media_type(ext: str) -> str:
    """The media type of a file extension (case-insensitive); application/octet-stream when unknown."""
    return MEDIA_TYPES.get(ext.lower(), "application/octet-stream")
