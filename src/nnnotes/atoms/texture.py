"""Atom `texture.decode`: the image of a Texture2D's first mip level from its stored bytes.

The reference implementation is UnityPy's `Texture2DConverter.parse_image_data` with the arguments UnityPy's
`Texture2D.image` passes (top row first), so the image, and its PNG encoding, equal the ones the other commands
write. HDR ASTC formats are refused: the decoder turns their blocks into its error colour (they are exported as
.astc, see `astc.container`).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import Unsupported, estimate

HDR_ASTC = frozenset(range(66, 72))                       # TextureFormat ASTC_HDR_4x4 .. ASTC_HDR_12x12
# TextureFormat -> ASTC block size (width, height)
ASTC_BLOCKS = {
    **{48 + i: b for i, b in enumerate(((4, 4), (5, 5), (6, 6), (8, 8), (10, 10), (12, 12)))},   # ASTC_RGB_*
    **{54 + i: b for i, b in enumerate(((4, 4), (5, 5), (6, 6), (8, 8), (10, 10), (12, 12)))},   # ASTC_RGBA_*
    **{66 + i: b for i, b in enumerate(((4, 4), (5, 5), (6, 6), (8, 8), (10, 10), (12, 12)))},   # ASTC_HDR_*
}
# formats stored uncompressed (bytes per texel is all their decode needs)
_RAW = frozenset({*range(1, 10), *range(13, 21), 62, 63, *range(72, 83)})


@dataclass(frozen=True)
class TextureInput:
    """What a Texture2D's image depends on: the stored image data (every mip level, as `get_image_data` returns
    it), size, TextureFormat, the serialized file's Unity version and build target, and the platform blob."""
    data: bytes
    width: int
    height: int
    format: int
    version: tuple = (0, 0, 0, 0)
    platform: int = 0
    platform_blob: bytes | None = None

    def key(self) -> tuple:
        """The decode inputs as one tuple (the order export._decode_inputs uses)."""
        return (self.data, self.width, self.height, self.format, tuple(self.version), self.platform,
                self.platform_blob)


def is_hdr_astc(fmt: int) -> bool:
    return int(fmt) in HDR_ASTC


def format_name(fmt: int) -> str:
    from UnityPy.enums import TextureFormat
    try:
        return TextureFormat(int(fmt)).name
    except ValueError:
        return str(int(fmt))


def decode(inp: TextureInput):
    """PIL image of the first mip level, top row first. Unsupported: `empty.texture` for a texture without pixels
    (0 wide or high), `unsupported.texture.format` for HDR ASTC and for formats the decoder does not implement."""
    from UnityPy.export.Texture2DConverter import parse_image_data
    fmt = int(inp.format)
    if not inp.width or not inp.height:
        raise Unsupported("empty.texture", f"{inp.width}x{inp.height} texture")
    if fmt in HDR_ASTC:
        raise Unsupported("unsupported.texture.format",
                          f"{format_name(fmt)} (HDR ASTC): the decoder returns its error colour for these blocks")
    try:
        return parse_image_data(inp.data, inp.width, inp.height, fmt, tuple(inp.version), inp.platform,
                                inp.platform_blob, True)
    except NotImplementedError as e:
        raise Unsupported("unsupported.texture.format", str(e)) from None
    except ValueError as e:
        if "is not a valid TextureFormat" in str(e):
            raise Unsupported("unsupported.texture.format", f"texture format {fmt}") from None
        raise


def cost(facts: dict) -> dict:
    """facts: width, height, format."""
    px = int(facts.get("width", 0)) * int(facts.get("height", 0))
    per_px = 4e-9 if int(facts.get("format", 4)) in _RAW else 2.5e-8
    return estimate(1e-4 + px * per_px, px * 4 * 3)
