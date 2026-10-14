"""The settings of the game's TextMeshPro font assets that the exports with open fonts (`--fonts open`) take.

An open font asset stands for a game font asset: it gets that asset's point size, padding, render mode, style
settings, line metrics and text materials, mirrors its fallback chain, and the characters the game asset lacks with
its fallbacks take TextMeshPro's missing glyph (storyfonts.open_font_set). These values come from FontSettings:

  builtin()         the table shipped with nnnotes (fontsettings.json, FORMAT), so the open-font exports read no
                    game font asset, material or font bundle
  AssetFontSettings the same values read from the game's font assets through an Exporter (`--fonts game`, and
                    build(), which writes the table)

The table holds, per font asset LocalizeManager lists for a language and per asset of their fallback chains:
`faceInfo` (m_FaceInfo), `atlasPadding`, `atlasRenderMode`, the style settings (STYLE_KEYS), `material` (the name of
its default material), `fallbacks` (the names of its fallback font asset table, in order), `coverage` (the code
points the asset has: its character table, and for a dynamic asset the character map of its source font file, as a
bit set: bit u of the zlib-compressed bytes, base64), `variants` (the [code point, variation selector] pairs its
source font file maps to a glyph other than the code point's) and `bundle` (the file name of the bundle that holds
it); per material the material record (Exporter.material, its texture references reduced: table_material) of every
font asset's default material and of every localized text material ("<font> - <type>" for the types of
LocalizeManager); and `objects`, the path id, type and name of each of those font assets and materials, by which
references into the font bundles resolve without loading them (export.Exporter `font_stubs`).
"""

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

    def fallbacks(self, name: str) -> list[str]:
        return list(self.font(name)["fallbacks"])

    def has(self, name: str, u: int) -> bool:
        """Whether the font asset itself finds the code point (GetCharacterFromFontAsset without fallbacks: its
        characters, its dynamic characters, TextMeshPro's synthesized control characters)."""
        from .tmpfont import TMP_SYNTHESIZED

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
        from .tmpfont import END_OF_TEXT, SPACE

        miss = sorted(u for u in chars if self.lookup(name, u) is None)
        if not miss:
            return None
        sub = next(u for u in (character, SPACE, END_OF_TEXT) if self.lookup(name, u) is not None)
        return {"unicode": sub, "characters": miss}

    def check_glyph_variants(self, name: str, texts) -> None:
        """tmpfont.check_glyph_variants of the asset `name`: NotImplementedError when a character of `texts` the
        chain has is followed by a variation selector that the asset holding it maps to a variant glyph."""
        from .tmpfont import is_variation_selector

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


class AssetFontSettings(FontSettings):
    """FontSettings read from the game's font assets (`ex`: an Exporter of the catalog and player data): a font asset
    at its address (advui.font_key), its fallbacks through their references (tmpfont.FontSet), its materials at their
    addresses. Needs the `fonts` extra for the source font files of dynamic assets."""

    def __init__(self, ex):
        super().__init__({"format": FORMAT, "fonts": {}, "materials": {}})
        self.ex = ex
        self._set = None
        self._mat_objs: dict[str, object] = {}  # material name -> its object

    def _fontset(self):
        if self._set is None:
            from .tmpfont import FontSet

            self._set = FontSet(self.ex)
        return self._set

    def _font_obj(self, name: str):
        fs = self._fontset()
        if name not in fs.fonts:
            from .advui import font_key

            fs.by_key(font_key(self.ex.cat, name, name))
        return fs.fonts[name]

    def font(self, name: str) -> dict:
        if name not in self.fonts:
            f = self._font_obj(name)
            mat = self.ex.material(f.material_obj)
            self.materials.setdefault(mat["material"], mat)
            self._mat_objs.setdefault(mat["material"], f.material_obj)
            tt = f.tt
            self.fonts[name] = {
                "faceInfo": tt["m_FaceInfo"],
                "atlasPadding": tt["m_AtlasPadding"],
                "atlasRenderMode": tt["m_AtlasRenderMode"],
                **{k: tt[k] for k in STYLE_KEYS},
                "material": mat["material"],
                "fallbacks": [fb.name for fb in self._fontset().fallbacks(f)],
                "coverage": encode_coverage(set(f.chars) | (f.cmap if f.dynamic else set())),
                "variants": sorted([b, vs] for b, vs in f.variants),
                "bundle": f.obj.assets_file.parent.name,
            }
        return self.fonts[name]

    def has_material(self, font: str, name: str) -> bool:
        from .advui import font_key

        return name in self.materials or self.ex.cat.has(font_key(self.ex.cat, font, name))

    def material(self, font: str | None, name: str) -> dict:
        if name not in self.materials:
            from .advui import font_key

            key = font_key(self.ex.cat, font, name)
            if not self.ex.cat.has(key):
                raise KeyError(f"material key {key}")
            self._mat_objs[name] = self.ex.key_object(key)
            self.materials[name] = self.ex.material(self._mat_objs[name])
        return copy.deepcopy(self.materials[name])

    def where(self) -> str:
        return "the game's font assets"


def table_material(m: dict) -> dict:
    """A material record as the table stores it: its texture references reduced to {name, width, height, format},
    the values the open-font exports take from them."""
    m = copy.deepcopy(m)
    for v in m["textures"].values():
        t = v["texture"]
        if t is not None:
            v["texture"] = {"name": t["name"], "width": t["width"], "height": t["height"], "format": t.get("format")}
    return m


def build(ex, player, game_version: str) -> dict:
    """The table (FORMAT) from the game's assets: every font asset LocalizeManager lists for a language (font names
    and additional font names) with the assets of their fallback chains, their default materials and the localized
    text materials of the listed assets that the catalog has (table_material), and the objects index of those
    assets."""
    from . import textstyle

    lang = textstyle.language_fonts(player)
    settings = AssetFontSettings(ex)
    listed = sorted({f"{n} SDF" for d in lang["languages"].values() for n in d["fontNames"] + d["additionalFontNames"]})
    todo, seen = list(listed), set()
    while todo:
        name = todo.pop(0)
        if name not in seen:
            seen.add(name)
            todo += settings.font(name)["fallbacks"]
    for name in listed:
        base = name[:-4] if name.endswith(" SDF") else name
        for t in lang["materialTypes"]:
            if settings.has_material(name, f"{base} - {t}"):
                settings.material(name, f"{base} - {t}")
    fs = settings._fontset()
    objects = {
        str(fs.fonts[n].obj.path_id): {"type": "MonoBehaviour", "class": FONT_CLASS, "name": n} for n in sorted(seen)
    }
    objects.update({str(o.path_id): {"type": "Material", "name": n} for n, o in sorted(settings._mat_objs.items())})
    return {
        "format": FORMAT,
        "gameVersion": game_version,
        "fonts": {n: settings.fonts[n] for n in sorted(seen)},
        "materials": {n: table_material(settings.materials[n]) for n in sorted(settings.materials)},
        "objects": dict(sorted(objects.items(), key=lambda kv: int(kv[0]))),
    }


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
