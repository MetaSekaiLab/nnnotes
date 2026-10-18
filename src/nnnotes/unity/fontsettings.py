"""Font settings tables for TextMesh Pro assets and open-font generation.

A table records each font's face metrics, atlas parameters, style settings, fallback order, character coverage,
variation sequences and bundle identity. Materials carry shader names and texture settings. The object index
maps font and material path ids to their names. FontSettings reads either the bundled table or a table assembled
from product objects by content.tmpassets.build_font_settings."""

from __future__ import annotations

import base64
import copy
import functools
import json
import zlib
from pathlib import Path

FORMAT = "nnnotes.font-settings/1"
FILE = Path(__file__).with_name("fontsettings.json")
STYLE_KEYS = ("normalStyle", "normalSpacingOffset", "boldStyle", "boldSpacing", "italicStyle", "tabSize")
FONT_CLASS = "TMP_FontAsset"
UNDERLINE = 0x5F
FONT_PREFIX = "EmbFont/"  # the embedded font assets and their materials: <prefix><font>/
FONT_ADDRESS = "Font/"  # LocalizeManager.FontAddressablePath; the embedded fonts are at "Emb" + address
# TextMeshPro synthesized control characters (TMP_FontAsset.AddSynthesizedCharactersAndFaceMetrics):
# zero-metric glyphs used when the font file does not map them.
TMP_SYNTHESIZED = (0x03, 0x09, 0x0A, 0x0B, 0x0D, 0x061C, 0x200B, 0x200E, 0x200F, 0x2028, 0x2029, 0x2060)
SPACE, END_OF_TEXT = 0x20, 0x03


def is_variation_selector(u: int) -> bool:
    """U+FE00..U+FE0F, U+E0100..U+E01EF (SetArraySizes: next >> 4 == 0xFE0 or next - 0xE0100 < 0xF0)."""
    return u >> 4 == 0xFE0 or 0xE0100 <= u < 0xE01F0


def font_dir(font_asset: str) -> str:
    """Addressable folder of a font asset and its materials: EmbFont/<font name without " SDF">/."""
    return f"{FONT_PREFIX}{font_asset[:-4] if font_asset.endswith(' SDF') else font_asset}/"


def font_key(cat, font_asset: str, name: str) -> str:
    """The address of `name` (a font asset or one of its materials) in the folder of `font_asset`: the embedded copy
    "EmbFont/<font>/" (font_dir) when the catalog has it, else LocalizeManager's "Font/<font>/"
    (FONT_ADDRESS; the additional fonts)."""
    key = font_dir(font_asset) + name
    if cat.has(key):
        return key
    base = font_asset[:-4] if font_asset.endswith(" SDF") else font_asset
    return f"{FONT_ADDRESS}{base}/{name}"


def encode_coverage(code_points) -> str:
    """A set of code points as the table stores it: bit u set in a byte string, zlib-compressed, base64."""
    cps = sorted(set(code_points))
    bits = bytearray((cps[-1] >> 3) + 1 if cps else 0)
    for u in cps:
        bits[u >> 3] |= 1 << (u & 7)
    return base64.b64encode(zlib.compress(bytes(bits), 9)).decode("ascii")


def decode_coverage(s: str) -> bytes:
    """The bit set of encode_coverage."""
    return zlib.decompress(base64.b64decode(s))


class FontSettings:
    """Font asset settings, character coverage and text materials by name (the table document, FORMAT); character
    lookup through the fallback chains as TextMeshPro does (tmpfont.FontSet)."""

    def __init__(self, doc: dict):
        if doc.get("format") != FORMAT:
            raise ValueError(f"font settings: format {doc.get('format')!r}, not {FORMAT}")
        self.doc = doc
        self.fonts: dict[str, dict] = doc["fonts"]
        self.materials: dict[str, dict] = doc["materials"]
        self.objects: dict[int, dict] = {int(k): v for k, v in doc.get("objects", {}).items()}
        self._bits: dict[str, bytes] = {}
        self._variants: dict[str, set] = {}

    def font(self, name: str) -> dict:
        """The settings of the font asset `name`."""
        f = self.fonts.get(name)
        if f is None:
            raise KeyError(f"font asset {name} not in the font settings ({self.where()})")
        return f

    def bundles(self, cat) -> frozenset:
        """The names of the bundles that hold the table's font assets: an addressable asset's bundle in `cat` now,
        else the one recorded."""
        names = set()
        for n, f in sorted(self.fonts.items()):
            k = font_key(cat, n, n)
            names.add(cat.bundle_of(k).name if cat.has(k) else f["bundle"])
        return frozenset(names)

    def fallbacks(self, name: str) -> list[str]:
        return list(self.font(name)["fallbacks"])

    def has(self, name: str, u: int) -> bool:
        """Whether the font asset itself finds the code point (GetCharacterFromFontAsset without fallbacks: its
        characters, its dynamic characters, TextMeshPro's synthesized control characters)."""
        if u in TMP_SYNTHESIZED:
            return True
        if name not in self._bits:
            self._bits[name] = decode_coverage(self.font(name)["coverage"])
        b = self._bits[name]
        return (u >> 3) < len(b) and bool(b[u >> 3] >> (u & 7) & 1)

    def lookup(self, primary: str, u: int) -> str | None:
        """The font asset TMP_Text.GetTextElement finds `u` in, starting at `primary` (tmpfont.FontSet.lookup: the
        asset, then each fallback with its own fallbacks depth first, every asset once), or None."""
        if self.has(primary, u):
            return primary
        searched: set[str] = set()

        def search(name: str) -> str | None:
            if self.has(name, u):
                return name
            for fb in self.fallbacks(name):
                if fb not in searched:
                    searched.add(fb)
                    hit = search(fb)
                    if hit is not None:
                        return hit
            return None

        for fb in self.fallbacks(primary):
            if fb not in searched:
                searched.add(fb)
                hit = search(fb)
                if hit is not None:
                    return hit
        return None

    def underline(self, name: str) -> bool:
        """Whether the asset itself has U+005F (tmpfont.underline_character): the glyph its texts' underline and
        highlight draw with."""
        return self.has(name, UNDERLINE)

    def missing_glyph(self, name: str, chars, character: int) -> dict | None:
        """tmpfont.missing_glyph of the asset `name`: {unicode: the substitute (`character` when the chain has it,
        else U+0020, else U+0003), characters: the code points of `chars` the chain lacks}, or None."""
        miss = sorted(u for u in chars if self.lookup(name, u) is None)
        if not miss:
            return None
        sub = next(u for u in (character, SPACE, END_OF_TEXT) if self.lookup(name, u) is not None)
        return {"unicode": sub, "characters": miss}

    def check_glyph_variants(self, name: str, texts) -> None:
        """tmpfont.check_glyph_variants of the asset `name`: NotImplementedError when a character of `texts` the
        chain has is followed by a variation selector that the asset holding it maps to a variant glyph."""
        for s in texts:
            cps = [ord(c) for c in s]
            for base, vs in zip(cps, cps[1:]):
                if is_variation_selector(vs):
                    hit = self.lookup(name, base)
                    if hit is not None and (base, vs) in self._variant_set(hit):
                        raise NotImplementedError(f"{hit}: glyph variant of U+{base:04X} U+{vs:04X}")

    def _variant_set(self, name: str) -> set:
        if name not in self._variants:
            self._variants[name] = {tuple(p) for p in self.font(name).get("variants", [])}
        return self._variants[name]

    def has_material(self, font: str, name: str) -> bool:
        return name in self.materials

    def material(self, font: str | None, name: str) -> dict:
        """The record of the material `name` of the font asset `font` (a copy)."""
        m = self.materials.get(name)
        if m is None:
            raise KeyError(f"material {name} of {font} not in the font settings ({self.where()})")
        return copy.deepcopy(m)

    def game_record(self, name: str) -> dict:
        """The settings storyfonts.open_asset takes: point size, padding, render mode, style, default material."""
        f = self.font(name)
        return {
            "pointSize": f["faceInfo"]["m_PointSize"],
            "padding": f["atlasPadding"],
            "renderMode": f["atlasRenderMode"],
            "style": {k: f[k] for k in STYLE_KEYS},
            "material": self.material(name, f["material"]),
        }

    def where(self) -> str:
        return f"nnnotes font settings of game version {self.doc.get('gameVersion')}"


@functools.lru_cache(maxsize=1)
def builtin() -> FontSettings:
    """The table shipped with nnnotes."""
    return FontSettings(json.loads(FILE.read_text(encoding="utf-8")))


def table_material(m: dict) -> dict:
    """A material record as the table stores it: its texture references reduced to {name, width, height, format},
    the values the open-font exports take from them."""
    m = copy.deepcopy(m)
    for v in m["textures"].values():
        t = v["texture"]
        if t is not None:
            v["texture"] = {"name": t["name"], "width": t["width"], "height": t["height"], "format": t.get("format")}
    return m


def write(doc: dict, path: Path = FILE) -> None:
    """The table, one font asset, material or object per line."""

    def entries(table: dict) -> str:
        rows = [
            f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
            for k, v in table.items()
        ]
        return "{\n" + ",\n".join(rows) + "}"

    head = {k: v for k, v in doc.items() if k not in ("fonts", "materials", "objects")}
    body = ",\n".join(f"{json.dumps(k)}:{entries(doc[k])}" for k in ("fonts", "materials", "objects"))
    text = json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + ",\n" + body + "}\n"
    Path(path).write_text(text, encoding="utf-8", newline="\n")
