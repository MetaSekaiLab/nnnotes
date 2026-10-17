"""TextMesh Pro font assets read from an object table (content/prefabs.Objects).

The records tmpfont.py reads through an Exporter, read from the object layer instead: a font asset's fields from its
`json`, its source font file from its `font` file, its atlas pages from their `image` files (PNG of the decoded image),
its materials from their `json` (the shader by name, the textures as texture records).

  Texture             a Texture2D of the table: its record and its RGBA texels
  Font                a TMP_FontAsset of the table (the fields tmpfont.Font reads)
  FontSet             the font assets reached from a font asset, with the TMP character lookup of tmpfont.FontLookup
  build_font_settings the font settings table (fontsettings.FORMAT) read from the table
  missing_glyph_character
                      the game's TMP Settings resource (tmpfont.missing_glyph_character)

The glyph tables and runtime glyphs are the tmpfont functions (export_fonts, runtime_glyphs), which take an
export.MemoryPacker to hold the packed pages in memory. Reading a font needs the `fonts` extra (tmpfont.require_extra).
"""

from __future__ import annotations

import copy
import hashlib
import io
from collections.abc import Callable
from typing import cast

import numpy as np
from PIL import Image

from ..unity.export import texel_rows
from ..unity.fontsettings import FONT_CLASS, font_key
from ..unity.ids import parse_object_id
from ..unity.tmpfont import (
    MISSING_GLYPH_CHARACTER,
    BuiltFontSettings,
    FontAsset,
    FontLookup,
    font_cmap,
    font_document,
    font_record,
    load_listed,
)
from .prefabs import Objects, key_object, ref_id


def _ref(v) -> str | None:
    """The object id a reference value of the table names (None for null); a reference the table does not resolve
    raises."""
    if v is None:
        return None
    oid = ref_id(v)
    if oid is None:
        raise ValueError(f"reference {v!r} does not resolve in the object table")
    return oid


class _Keys:
    """The catalog's `has` (fontsettings.font_key) over the keys of an object table."""

    def __init__(self, objects: Objects):
        self.objects = objects

    def has(self, key: str) -> bool:
        try:
            self.objects.key(key)
        except KeyError:
            return False
        return True


class Texture:
    """A Texture2D of an object table: its record (name, size, format, mip count and sampler settings, from its
    `json`) and its RGBA texels, bottom row first, decoded from its `image` file."""

    def __init__(self, objects: Objects, oid: str):
        self.oid = oid
        self._objects = objects
        tt = objects.json(oid)
        self.key: tuple[str, int] = parse_object_id(oid)
        self.name: str = tt["m_Name"]
        self.width: int = tt["m_Width"]
        self.height: int = tt["m_Height"]
        self.format: int = tt["m_TextureFormat"]
        self.mip_count: int = tt.get("m_MipCount", 1)
        self.settings: dict = tt["m_TextureSettings"]
        self._pixels: np.ndarray | None = None

    @property
    def pixels(self) -> np.ndarray:
        """The texels as RGBA, bottom row first (read-only), decoded once."""
        if self._pixels is None:
            ext = self._objects.info(self.oid)["files"]["image"]["ext"]
            if ext != "png":
                raise NotImplementedError(f"texture {self.name}: image file {ext}")
            img = Image.open(io.BytesIO(self._objects.bytes(self.oid, "image")))
            arr = texel_rows(img, self.format, self.width, self.height, self.name)
            arr.setflags(write=False)
            self._pixels = arr
        return self._pixels

    def record(self) -> dict:
        """The record of a material's texture reference (export.Exporter.texture in deferred mode)."""
        return {
            "textureRef": self.oid,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mipCount": self.mip_count,
            "settings": self.settings,
        }


class Font:
    """A TMP_FontAsset of an object table: the fields tmpfont.Font reads, its atlas pages (Texture, None for a missing
    page), the bytes and character map of its source font file, and its default material."""

    def __init__(self, fonts: FontSet, oid: str):
        objects = fonts.objects
        tt = objects.json(oid)
        self.oid = oid
        self._fonts = fonts
        self.tt = tt
        self.name: str = tt["m_Name"]
        self.chars = {c["m_Unicode"]: c for c in tt["m_CharacterTable"]}
        self.glyphs = {g["m_Index"]: g for g in tt["m_GlyphTable"]}
        self.dynamic = tt["m_AtlasPopulationMode"] == 1
        self.atlases: list[Texture | None] = []
        for p in tt["m_AtlasTextures"]:
            page = _ref(p)
            self.atlases.append(None if page is None else fonts.texture(page))
        material = _ref(tt["m_Material"])
        if material is None:
            raise KeyError(f"{self.name}: a required reference is null (m_Material)")
        self.material_oid: str = material
        self.fallback_oids: list[str | None] = [_ref(p) for p in tt["m_FallbackFontAssetTable"]]
        if any(_ref(p["regularTypeface"]) or _ref(p["italicTypeface"]) for p in tt["m_FontWeightTable"]):
            raise NotImplementedError(f"{self.name}: font weight table typefaces")
        self.cmap: set[int] = set()
        self.variants: dict[tuple[int, int], str] = {}
        self.font_data: bytes | None = None
        self.source_font: dict | None = None
        src = _ref(tt["m_SourceFontFile"])
        if src is not None:
            data = objects.bytes(src, "font")
            self.cmap, self.variants = font_cmap(data, tt["m_FaceInfo"]["m_FaceIndex"])
            self.font_data = data
            self.source_font = {
                "name": objects.json(src)["m_Name"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

    def atlas_names(self) -> list[str | None]:
        return [None if a is None else a.name for a in self.atlases]

    def atlas_settings(self) -> list[dict]:
        return [a.settings for a in self.atlases if a is not None]

    def default_material(self) -> dict:
        """The record of the default material (export.Exporter.material)."""
        return self._fonts.material(self.material_oid)


class FontSet(FontLookup):
    """The font assets of an object table reached from a font asset (through their fallback chains), with the TMP
    character lookup of FontLookup."""

    def __init__(self, objects: Objects):
        super().__init__()
        self.objects = objects
        self._by_oid: dict[str, Font] = {}
        self._textures: dict[str, Texture] = {}

    def texture(self, oid: str) -> Texture:
        if oid not in self._textures:
            cls = self.objects.info(oid).get("class")
            if cls != "Texture2D":
                raise NotImplementedError(f"{oid}: a texture reference to a {cls}")
            self._textures[oid] = Texture(self.objects, oid)
        return self._textures[oid]

    def by_key(self, key: str) -> Font:
        """The font asset of the catalog key `key` (its font folder's TMP_FontAsset)."""
        return self.of(key_object(self.objects, key, FONT_CLASS))

    def loaded(self, name: str) -> Font:
        """The font asset `name`, read by `of` or `by_key`."""
        return cast(Font, self.fonts[name])

    def of(self, oid: str) -> Font:
        if oid not in self._by_oid:
            f = Font(self, oid)
            if f.name in self.fonts:
                raise RuntimeError(f"two font assets named {f.name}")
            self._by_oid[oid] = f
            self.fonts[f.name] = f
            self._fallback_names[f.name] = [self.of(fb).name for fb in f.fallback_oids if fb is not None]
        return self._by_oid[oid]

    def material(self, oid: str) -> dict:
        """The record of the Material `oid` (export.Exporter.material): its shader by name, its textures as texture
        records, its other fields as in its `json`."""
        doc = self.objects.json(oid)
        shader = _ref(doc["shader"])
        return {
            "material": doc["material"],
            "shader": None if shader is None else {"shader": self.objects.json(shader)["name"]},
            "keywords": doc["keywords"],
            "invalidKeywords": doc["invalidKeywords"],
            "renderQueue": doc["renderQueue"],
            "tags": doc["tags"],
            "disabledPasses": doc["disabledPasses"],
            "textures": {k: self._texture_entry(v) for k, v in doc["textures"].items()},
            "ints": doc["ints"],
            "floats": doc["floats"],
            "colors": doc["colors"],
        }

    def _texture_entry(self, v: dict) -> dict:
        tex = _ref(v["texture"])
        return {
            "texture": None if tex is None else self.texture(tex).record(),
            "scale": v["scale"],
            "offset": v["offset"],
        }

    def named_material(self, font: FontAsset, name: str) -> dict:
        """The material `name` of the font asset `font`, at its key in the font's folder (tmpfont.material_by_name)."""
        key = font_key(_Keys(self.objects), font.name, name)
        if not _Keys(self.objects).has(key):
            raise KeyError(f"material key {key}")
        return self.material(key_object(self.objects, key, "Material"))


class TableFontSettings(BuiltFontSettings):
    """FontSettings read from the font assets of an object table (FontSet): each font asset's bundle from
    `bundle_of(name)`, the records as tmpfont.AssetFontSettings reads them from the game's assets."""

    def __init__(self, fonts: FontSet, bundle_of: Callable[[str], str]):
        super().__init__()
        self._set = fonts
        self._keys = _Keys(fonts.objects)
        self._bundle_of = bundle_of

    def font(self, name: str) -> dict:
        if name not in self.fonts:
            if name not in self._set.fonts:
                self._set.by_key(font_key(self._keys, name, name))
            f = self._set.loaded(name)
            mat = f.default_material()
            self.materials.setdefault(mat["material"], mat)
            self.material_ids.setdefault(mat["material"], parse_object_id(f.material_oid)[1])
            self.font_ids[name] = parse_object_id(f.oid)[1]
            fallbacks = [fb.name for fb in self._set.fallbacks(f)]
            self.fonts[name] = font_record(f, mat["material"], fallbacks, self._bundle_of(name))
        return self.fonts[name]

    def has_material(self, font: str, name: str) -> bool:
        return name in self.materials or self._keys.has(font_key(self._keys, font, name))

    def material(self, font: str | None, name: str) -> dict:
        if name not in self.materials:
            if font is None:
                raise KeyError(f"material {name}: not read yet, and no font asset to find it by")
            key = font_key(self._keys, font, name)
            if not self._keys.has(key):
                raise KeyError(f"material key {key}")
            oid = key_object(self._set.objects, key, "Material")
            self.material_ids[name] = parse_object_id(oid)[1]
            self.materials[name] = self._set.material(oid)
        return copy.deepcopy(self.materials[name])

    def where(self) -> str:
        return "the font assets of the object table"


def build_font_settings(objects: Objects, lang: dict, game_version: str, bundle_of: Callable[[str], str]) -> dict:
    """The font settings table (fontsettings.FORMAT) read from the font assets of an object table: the document
    tmpfont.build_font_settings gives for the game's assets. `lang` is LocalizeManager's font data
    (textstyle.language_fonts), `bundle_of(name)` the bundle that holds the font asset `name`."""
    settings = TableFontSettings(FontSet(objects), bundle_of)
    return font_document(settings, load_listed(settings, lang), game_version)


def missing_glyph_character(objects: Objects) -> int:
    """TMP_Settings.missingGlyphCharacter of the table's TMP Settings resource (tmpfont.missing_glyph_character)."""
    hits = [
        o
        for o in objects.roots
        if o in objects
        and objects.info(o).get("name") == "TMP Settings"
        and objects.info(o).get("class") == "MonoBehaviour"
    ]
    if len(hits) != 1:
        raise ValueError(f"TMP Settings: {len(hits)} resources in the table, expected one")
    tt = objects.json(hits[0])
    if tt["m_fallbackFontAssets"] or _ref(tt["m_defaultFontAsset"]) is not None:
        raise NotImplementedError("TMP Settings fallback font assets / default font asset")
    return tt["m_missingGlyphCharacter"] or MISSING_GLYPH_CHARACTER
