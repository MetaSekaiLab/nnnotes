"""Atom `sprite.crop`: a Sprite's image from its texture's decoded image.

A port of UnityPy's `SpriteHelper.get_image_from_sprite` (MIT) that works on values instead of objects: the texture
rect is cut out of the texture (merged with the alpha texture's first channel when there is one), a packed sprite's
packing rotation or flip is undone, and a tightly packed sprite is masked with its mesh (or, when its mesh has
texture coordinates, drawn from them triangle by triangle); the result is top row first. The output equals UnityPy's
for the same sprite (tests compare the two).

The texture image is the one `texture.decode` returns (top row first); the rect is cut from it directly, so an atlas
texture is not copied once per sprite.

`canvas` places the result on the sprite's full rect (Sprite.m_Rect size, transparent elsewhere) at its
textureRectOffset -- where a tightly packed or trimmed sprite sits in its original bounds; a sprite drawn from its
mesh is placed by its mesh bounds and the pivot. Sizes are rounded; when rounding fractional rects leaves the canvas
one pixel narrower or lower than the cut, the canvas takes that pixel.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import Unsupported, estimate

TIGHT, RECTANGLE = 0, 1
ROTATIONS = {0: "none", 1: "flipHorizontal", 2: "flipVertical", 3: "rotate180", 4: "rotate90"}


@dataclass(frozen=True)
class Settings:
    packed: bool
    packing_mode: int            # 0 tight, 1 rectangle
    packing_rotation: int        # 0 none, 1 flip horizontal, 2 flip vertical, 3 rotate 180, 4 rotate 90
    mesh_type: int               # 0 full rect, 1 tight


def settings(raw: int) -> Settings:
    """SpriteSettings bits of `settingsRaw`."""
    raw = int(raw)
    return Settings(bool(raw & 1), (raw >> 1) & 1, (raw >> 2) & 0xF, (raw >> 6) & 1)


def mesh_values(mesh) -> dict:
    """The part of a sprite's MeshArrays sprite.crop reads, as plain values (for a document that carries a sprite
    to a task without its bundle): positions, triangles per submesh, texture coordinates (or None). Floats are the
    stored float32 values, so they read back exactly."""
    uv = mesh.channels.get("uv0")
    return {"positions": mesh.channels["position"].data.astype(float).tolist(),
            "submeshes": [p.indices.tolist() for p in mesh.primitives
                          if p.topology == "triangles" and p.indices is not None],
            "uv0": None if uv is None else uv.data.astype(float).tolist()}


def mesh_from_values(values: dict):
    """The MeshArrays of mesh_values(...)."""
    import numpy as np
    from .mesh import Channel, MeshArrays, Primitive
    pos = np.asarray(values["positions"], np.float32).reshape(-1, 3)
    channels = {"position": Channel(pos)}
    if values.get("uv0") is not None:
        channels["uv0"] = Channel(np.asarray(values["uv0"], np.float32).reshape(len(pos), -1))
    prims = [Primitive("triangles", np.asarray(t, np.int64).reshape(-1, 3), i)
             for i, t in enumerate(values.get("submeshes") or [])]
    return MeshArrays("", len(pos), channels, prims)


def _cut(image, rect: dict):
    """The texture rect of a top-row-first image as UnityPy cuts it from the bottom-row-first image (Pillow's crop
    rounds the box and pads outside the image with zeros), still bottom row first."""
    from PIL.Image import Transpose
    x, y, w, h = rect["x"], rect["y"], rect["width"], rect["height"]
    if x + w < x or y + h < y:
        raise ValueError(f"texture rect {rect}: negative size")
    x0, y0, x1, y1 = (int(round(v)) for v in (x, y, x + w, y + h))
    top = image.height
    return image.crop((x0, top - y1, x1, top - y0)).transpose(Transpose.FLIP_TOP_BOTTOM)


def _merge(rgb, alpha):
    from PIL import Image
    return Image.merge("RGBA", (*rgb.split()[:3], alpha.split()[0]))


def _positions(mesh) -> list:
    ch = mesh.channels.get("position")
    if ch is None or ch.data.shape[1] != 3 or not len(ch.data):
        shape = None if ch is None else ch.data.shape
        raise Unsupported("unsupported.sprite.tight_mask", f"sprite mesh positions {shape}")
    return [tuple(v) for v in ch.data.tolist()]


def _triangles(mesh) -> list[list[tuple]]:
    return [[tuple(t) for t in p.indices.tolist()] for p in mesh.primitives
            if p.topology == "triangles" and p.indices is not None]


def _mask(mesh, image, ppu: float):
    from PIL import Image, ImageDraw
    positions = _positions(mesh)
    mask = Image.new("1", image.size, color=0)
    draw = ImageDraw.ImageDraw(mask)
    min_x = min(x for x, _y, _z in positions)
    min_y = min(y for _x, y, _z in positions)
    flat = [((x - min_x) * ppu, (y - min_y) * ppu) for x, y, _z in positions]
    for tris in _triangles(mesh):
        for a, b, c in tris:
            draw.polygon((flat[a], flat[b], flat[c]), fill=1)
    if image.mode == "RGBA":
        return Image.composite(image, Image.new(image.mode, image.size, color=0), mask)
    image = image.copy()
    image.putalpha(mask)
    return image


def _copy_triangle(src, src_tri, dst, dst_tri) -> None:
    import numpy as np
    from PIL import Image, ImageDraw
    from PIL.Image import Transform
    src_off = ((src_tri[1][0] - src_tri[0][0], src_tri[1][1] - src_tri[0][1]),
               (src_tri[2][0] - src_tri[0][0], src_tri[2][1] - src_tri[0][1]))
    dst_off = ((dst_tri[1][0] - dst_tri[0][0], dst_tri[1][1] - dst_tri[0][1]),
               (dst_tri[2][0] - dst_tri[0][0], dst_tri[2][1] - dst_tri[0][1]))
    if src_off == dst_off:                   # same shape: copy the triangle's box through a triangle mask
        upper_left = (min(p[0] for p in src_tri), min(p[1] for p in src_tri))
        lower_right = (max(p[0] for p in src_tri), max(p[1] for p in src_tri))
        part = src.crop((*upper_left, *lower_right))
        mask = Image.new("1", part.size)
        ImageDraw.Draw(mask).polygon([(x - upper_left[0], y - upper_left[1]) for x, y in src_tri], fill=255)
        dst.paste(part, (int(min(p[0] for p in dst_tri)), int(min(p[1] for p in dst_tri))), mask=mask)
        return
    (x11, x12), (x21, x22), (x31, x32) = src_tri   # the affine map from destination to source coordinates
    (y11, y12), (y21, y22), (y31, y32) = dst_tri
    m = [[y11, y12, 1, 0, 0, 0], [y21, y22, 1, 0, 0, 0], [y31, y32, 1, 0, 0, 0],
         [0, 0, 0, y11, y12, 1], [0, 0, 0, y21, y22, 1], [0, 0, 0, y31, y32, 1]]
    coeffs = np.linalg.solve(m, [x11, x21, x31, x12, x22, x32])
    moved = src.transform(dst.size, Transform.AFFINE, coeffs)
    mask = Image.new("1", dst.size)
    ImageDraw.Draw(mask).polygon(dst_tri, fill=255)
    dst.paste(moved, mask=mask)


def _render(mesh, texture, ppu: float):
    """The sprite drawn from its first submesh's triangles through their texture coordinates (bottom row first),
    and its mesh bounds' minimum corner in units."""
    from PIL import Image
    tris = _triangles(mesh)
    positions = _positions(mesh)
    uv = mesh.channels["uv0"].data
    if not tris:
        raise Unsupported("unsupported.sprite.tight_mask", "sprite mesh without triangles")
    if uv.shape[1] != 2:
        raise Unsupported("unsupported.sprite.tight_mask", f"sprite texture coordinates with {uv.shape[1]} components")
    axes = [[p[i] for p in positions] for i in range(3)]
    for i in range(2, -1, -1):
        if len(set(axes[i])) == 1:
            break
    else:
        raise Unsupported("unsupported.sprite.tight_mask", "sprite mesh is not flat")
    axes = axes[:i] + axes[i + 1:]
    x_min, y_min, x_max, y_max = min(axes[0]), min(axes[1]), max(axes[0]), max(axes[1])
    pos = [(round((x - x_min) * ppu), round((y - y_min) * ppu)) for x, y in zip(*axes)]
    width, height = texture.size
    uv_abs = [(round(u * width), round(v * height)) for u, v in uv.tolist()]
    sprite = Image.new(texture.mode, (round((x_max - x_min) * ppu), round((y_max - y_min) * ppu)))
    for tri in tris[0]:
        _copy_triangle(texture, tuple(uv_abs[k] for k in tri), sprite, tuple(pos[k] for k in tri))
    return sprite, (x_min, y_min)


def crop(image, texture_rect: dict, settings_raw: int, mesh=None, pixels_to_units: float = 100.0, alpha=None, *,
         downscale: float = 1.0, canvas: dict | None = None):
    """The sprite's PIL image, top row first.

    image / alpha: the decoded texture and alpha texture (texture.decode); texture_rect: {x, y, width, height} in
    texels from the bottom-left; mesh: the MeshArrays of the sprite's own render data (tight packing);
    pixels_to_units: Sprite.m_PixelsToUnits; downscale: the atlas' downscaleMultiplier (only 1 is supported);
    canvas: {rect: {width, height}, offset: textureRectOffset {x, y}, pivot: m_Pivot {x, y}}."""
    from PIL.Image import Transpose
    if downscale != 1.0:
        raise Unsupported("unsupported.sprite.downscale", f"downscaleMultiplier {downscale}")
    if alpha is not None and alpha.size != image.size:
        raise ValueError(f"alpha texture {alpha.size} for texture {image.size}")
    img = _cut(image, texture_rect)
    if alpha is not None:
        img = _merge(img, _cut(alpha, texture_rect))
    s = settings(settings_raw)
    if s.packed:
        turn = {1: Transpose.FLIP_LEFT_RIGHT, 2: Transpose.FLIP_TOP_BOTTOM, 3: Transpose.ROTATE_180,
                4: Transpose.ROTATE_270}.get(s.packing_rotation)
        if turn is not None:
            img = img.transpose(turn)
    corner = None
    if s.packing_mode == TIGHT:
        if mesh is None:
            raise ValueError("tightly packed sprite without its mesh")
        uv = mesh.channels.get("uv0")
        if uv is not None and len(uv.data) and bool((uv.data[:, :2] != 0).any()):
            texture = image if alpha is None else _merge(image, alpha)
            img, corner = _render(mesh, texture.transpose(Transpose.FLIP_TOP_BOTTOM), pixels_to_units)
        else:
            img = _mask(mesh, img, pixels_to_units)
    img = img.transpose(Transpose.FLIP_TOP_BOTTOM)
    if canvas is None:
        return img
    cut_rounded = any(float(texture_rect[k]) % 1 for k in ("x", "y", "width", "height"))
    return _place(img, canvas, corner, pixels_to_units, cut_rounded)


def _place(img, canvas: dict, corner, ppu: float, cut_rounded: bool = False):
    from PIL import Image
    rect, pivot = canvas["rect"], canvas.get("pivot") or {"x": 0.5, "y": 0.5}
    w, h = int(round(rect["width"])), int(round(rect["height"]))
    if corner is None:
        ox, oy = canvas["offset"]["x"], canvas["offset"]["y"]
    else:
        ox, oy = corner[0] * ppu + pivot["x"] * rect["width"], corner[1] * ppu + pivot["y"] * rect["height"]
    x, y = int(round(ox)), int(round(oy))
    # fractional values: the texture rect is cut at its rounded edges and the canvas has the rounded size, so the cut
    # can be one pixel wider or higher than the canvas; the canvas grows by that pixel
    if cut_rounded or rect["width"] % 1 or ox % 1:
        w = w + 1 if x + img.width == w + 1 else w
    if cut_rounded or rect["height"] % 1 or oy % 1:
        h = h + 1 if y + img.height == h + 1 else h
    if x < 0 or y < 0 or x + img.width > w or y + img.height > h:
        raise Unsupported("unsupported.sprite.canvas", f"{img.width}x{img.height} image at ({x}, {y}) outside the "
                                                       f"{w}x{h} rect")
    if "A" not in img.getbands():
        img = img.convert("RGBA")
    out = Image.new(img.mode, (w, h))
    out.paste(img, (x, h - y - img.height))
    return out


def cost(facts: dict) -> dict:
    """facts: width, height (texture rect), textureWidth, textureHeight, tight (bool)."""
    px = int(facts.get("width", 0)) * int(facts.get("height", 0))
    tex = int(facts.get("textureWidth", 0)) * int(facts.get("textureHeight", 0))
    return estimate(5e-5 + px * 1.5e-8 + (tex * 4e-9 if facts.get("tight") else 0), px * 8 + tex * 4)
