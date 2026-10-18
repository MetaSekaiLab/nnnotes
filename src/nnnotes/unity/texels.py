"""In-memory texel block packing and PNG encoding for object-based text products."""

from __future__ import annotations

import io
import json
import math
import re
from typing import Literal

import numpy as np
from PIL import Image

from ..core import cache

_LIBS = ("UnityPy", "Pillow", "texture2ddecoder", "astc-encoder-py", "etcpak", "numpy")
# decoded textures and PNG encodings memoized by content within this process
PNG = cache.bucket("png", salt=cache.source_salt(__file__, *_LIBS))


def packed_png(rgba: np.ndarray) -> bytes:
    """PNG bytes (Pillow, optimize=True) of an RGBA array (bottom row first), cached by the array's content."""
    rgba = np.ascontiguousarray(rgba, np.uint8)
    k = PNG.key("packed", rgba.shape, rgba.data)
    data = PNG.get(k)
    if data is None:
        buf = io.BytesIO()
        Image.fromarray(rgba[::-1].copy(), "RGBA").save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        PNG.put(k, data)
    return data


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


# --- texel blocks -> packed textures ---------------------------------------------
def grow_box(x0: float, y0: float, x1: float, y1: float, margin: int) -> tuple[int, int, int, int]:
    """Texel box covering [x0, x1) x [y0, y1) grown by `margin` texels on every side."""
    return (math.floor(x0) - margin, math.floor(y0) - margin, math.ceil(x1) + margin, math.ceil(y1) + margin)


def wrap_mode(mode: int) -> Literal["wrap", "clip"]:
    """The np.take mode of a texture's wrap mode (m_WrapU / m_WrapV: 0 repeat, 1 clamp)."""
    if mode == 0:
        return "wrap"
    if mode == 1:
        return "clip"
    raise NotImplementedError(f"texture wrap mode {mode}")


def texel_rows(img, texture_format: int, width: int, height: int, name: str) -> np.ndarray:
    """The RGBA texels of a decoded Texture2D image `img` (a PIL image; the texture's format, size and name), bottom
    row first. Alpha8 keeps its value in the alpha channel."""
    if texture_format == 1:  # Alpha8: value in the alpha channel
        if img.mode == "L":
            a = np.asarray(img, np.uint8)
        elif img.mode == "RGBA":
            a = np.asarray(img, np.uint8)[:, :, 3]
        else:
            raise NotImplementedError(f"Alpha8 decoded as {img.mode}")
        arr = np.zeros(a.shape + (4,), np.uint8)
        arr[:, :, 3] = a  # rgb 0 (GL alpha texture); TMP reads .a only
    else:
        arr = np.asarray(img.convert("RGBA"), np.uint8)
    if arr.shape[:2] != (height, width):
        raise RuntimeError(f"{name}: decoded size {arr.shape}")
    return arr[::-1].copy()  # bottom row first (Unity v = 0)


def pack_group(group: str, handles: list[dict], pixels, width_limit: int) -> tuple[dict, np.ndarray]:
    """The layout and the texels of one packed texture `group` of the blocks `handles` (MemoryPacker.build). Returns its
    record without its file (name, width, height, mipCount, settings, sources, blocks) and its RGBA array, bottom row
    first. `pixels(tex)` gives the texels of a block's source texture. Every handle gets its texture (`group`) and its
    integer `offset`."""
    settings = {json.dumps(h["settings"], sort_keys=True) for h in handles}
    if len(settings) != 1:
        raise RuntimeError(f"{group}: blocks from textures with different sampler settings")
    # dedupe identical blocks of the same texture
    uniq: dict[tuple, dict] = {}
    for h in handles:
        uniq.setdefault((h["key"], h["box"]), h)
    blocks = sorted(uniq.values(), key=lambda h: (-(h["box"][3] - h["box"][1]), h["box"]))
    x = y = shelf = 0
    width = max(width_limit, max(b["box"][2] - b["box"][0] for b in blocks))
    for b in blocks:
        w, hgt = b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        b["dst"] = (x, y)
        x += w
        shelf = max(shelf, hgt)
    height = y + shelf
    if len(blocks) == 1:  # a single block keeps its own size
        width = blocks[0]["box"][2] - blocks[0]["box"][0]
    out = np.zeros((height, width, 4), np.uint8)
    for b in blocks:
        x0, y0, x1, y1 = b["box"]
        dx, dy = b["dst"]
        if "array" in b:
            out[dy : dy + (y1 - y0), dx : dx + (x1 - x0)] = b["array"]
            continue
        src = pixels(b["tex"])
        st = b["settings"]
        rows = np.take(np.arange(src.shape[0]), np.arange(y0, y1), mode=wrap_mode(st["m_WrapV"]))
        cols = np.take(np.arange(src.shape[1]), np.arange(x0, x1), mode=wrap_mode(st["m_WrapU"]))
        out[dy : dy + (y1 - y0), dx : dx + (x1 - x0)] = src[np.ix_(rows, cols)]
    for h in handles:
        d = uniq[(h["key"], h["box"])]["dst"]
        h["offset"] = (d[0] - h["box"][0], d[1] - h["box"][1])
        h["texture"] = group
    s = next(iter(handles))["settings"]
    rec = {
        "name": group,
        "width": width,
        "height": height,
        "mipCount": 1,
        "settings": s,
        "sources": sorted({h["srcName"] for h in handles}),
        "blocks": len(blocks),
    }
    return rec, out


def texture_block(
    key: tuple, settings: dict, name: str, size: tuple[int, int], box: tuple[int, int, int, int], tex
) -> dict:
    """A block that copies the texel box `box` of the texture `tex` (its texels come from the packer's `pixels`).
    `key` identifies the texture, `settings` is its m_TextureSettings, `name` and `size` (width, height) its name and
    size."""
    return {"tex": tex, "key": key, "settings": settings, "box": box, "srcName": name, "srcSize": size}


def array_block(name: str, rgba: np.ndarray, origin: tuple[int, int], settings: dict) -> dict:
    """A block of generated texels: `rgba` (RGBA, bottom row first) placed at `origin` of its virtual texture."""
    ox, oy = origin
    return {
        "array": rgba,
        "key": ("generated", name),
        "settings": settings,
        "box": (ox, oy, ox + rgba.shape[1], oy + rgba.shape[0]),
        "srcName": name,
        "srcSize": (rgba.shape[1], rgba.shape[0]),
    }


class MemoryPacker:
    """Pack in-memory texture blocks into PNG pages. Textures supply a key, name, settings, width, height and
    bottom-first RGBA pixels. build() returns page records and encoded bytes by file name."""

    WIDTH = 2048

    def __init__(self):
        self.groups: dict[str, list] = {}

    @staticmethod
    def pixels(tex) -> np.ndarray:
        return tex.pixels

    def add(self, group: str, tex, x0: int, y0: int, x1: int, y1: int) -> dict:
        """Register a block; returns a handle filled in by build()."""
        h = texture_block(tex.key, tex.settings, tex.name, (tex.width, tex.height), (x0, y0, x1, y1), tex)
        self.groups.setdefault(group, []).append(h)
        return h

    def add_array(self, group: str, name: str, rgba: np.ndarray, origin: tuple[int, int], settings: dict) -> dict:
        """Register generated RGBA texels at their virtual texture origin."""
        h = array_block(name, rgba, origin, settings)
        self.groups.setdefault(group, []).append(h)
        return h

    def build(self) -> tuple[dict, dict[str, bytes]]:
        """The records of the packed textures by group, and the PNG bytes of their pages by file name."""
        textures: dict[str, dict] = {}
        pages: dict[str, bytes] = {}
        for group, handles in self.groups.items():
            rec, out = pack_group(group, handles, self.pixels, self.WIDTH)
            fn = f"textures/{_safe(group)}.png"
            pages[fn] = packed_png(out)
            textures[group] = {"texture": fn, **rec}
        return textures, pages
