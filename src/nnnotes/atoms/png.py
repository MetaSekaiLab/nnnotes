"""Atom `png.encode`: PNG bytes of a PIL image, written by Pillow.

Level 6 (the default) gives the bytes Pillow writes without options (`image.save(f, "PNG")`), which the other
commands write for textures; other levels (0-9) change the bytes, never the pixels.
"""
from __future__ import annotations

import io

from . import estimate

DEFAULT_LEVEL = 6


def encode(image, level: int = DEFAULT_LEVEL) -> bytes:
    """PNG bytes of `image` (any mode Pillow writes as PNG) at zlib level `level`."""
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 9:
        raise ValueError(f"PNG level {level!r}: expected 0-9")
    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=level)
    return buf.getvalue()


def cost(facts: dict) -> dict:
    """facts: width, height, channels (default 4), level (default 6)."""
    raw = int(facts.get("width", 0)) * int(facts.get("height", 0)) * int(facts.get("channels", 4))
    per_byte = {0: 1e-9, 1: 4e-9, 2: 4e-9, 3: 5e-9}.get(int(facts.get("level", DEFAULT_LEVEL)), 1.2e-8)
    return estimate(5e-5 + raw * per_byte, raw * 2)
