"""TextMeshPro font assets for the UI exports with game fonts (`--fonts game`).

  FontSet          TMP font assets reachable from the localized fonts (fallback chains) with TMP
                   character lookup; RuntimeGlyphs generates the SDF glyphs a dynamic font asset adds
                   at runtime (FontEngine) from the source font file, validated against the baked ones
  glyph_coverage / export_fonts / resolve_font_blocks
                   the character and glyph tables reduced to the characters a page shows, their texel
                   blocks copied into packed textures (export.TexelPacker)

The localized font / material / line spacing of a text (LocalizeText) and the text records are in textstyle.py.
Needs the optional dependencies of the `fonts` extra (require_extra).
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import math

import numpy as np

from .config import ConfigError
from .export import Exporter, TexelPacker, _key, grow_box
from .textstyle import font_dir

EXTRA = "fonts"
EXTRA_MODULES = {"fontTools": "fonttools", "freetype": "freetype-py", "scipy": "scipy"}

# TextMeshPro synthesized control characters (TMP_FontAsset.AddSynthesizedCharactersAndFaceMetrics):
# zero-metric glyphs used when the font file does not map them.
TMP_SYNTHESIZED = (0x03, 0x09, 0x0A, 0x0B, 0x0D, 0x061C, 0x200B, 0x200E, 0x200F, 0x2028, 0x2029, 0x2060)


def require_extra() -> None:
    """Raise ConfigError naming the `fonts` extra when one of its packages is not installed."""
    missing = [pkg for mod, pkg in EXTRA_MODULES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        raise ConfigError(f"--fonts game needs the optional '{EXTRA}' dependencies ({', '.join(missing)} not "
                          f"installed): pip install 'nnnotes[{EXTRA}]'")


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------
class Font:
    def __init__(self, ex: Exporter, obj):
        self.obj = obj
        tt = obj.read_typetree()
        self.tt = tt
        self.name = tt["m_Name"]
        self.chars = {c["m_Unicode"]: c for c in tt["m_CharacterTable"]}
        self.glyphs = {g["m_Index"]: g for g in tt["m_GlyphTable"]}
        self.dynamic = tt["m_AtlasPopulationMode"] == 1
        self.atlases = [ex.deref(obj, p) for p in tt["m_AtlasTextures"]]
        self.material_obj = ex.deref(obj, tt["m_Material"])
        self.fallback_objs = [ex.deref(obj, p) for p in tt["m_FallbackFontAssetTable"]]
        if any(p["regularTypeface"]["m_PathID"] or p["italicTypeface"]["m_PathID"] for p in tt["m_FontWeightTable"]):
            raise NotImplementedError(f"{self.name}: font weight table typefaces")
        self.cmap: set[int] = set()
        self.font_data: bytes | None = None
        src = ex.deref(obj, tt["m_SourceFontFile"]) if tt["m_SourceFontFile"]["m_PathID"] else None
        if src is not None:
            from fontTools.ttLib import TTFont
            data = bytes(src.read_typetree()["m_FontData"])
            ft = TTFont(io.BytesIO(data), fontNumber=tt["m_FaceInfo"]["m_FaceIndex"], lazy=True)
            self.cmap = set(ft.getBestCmap() or {})
            self.font_data = data
            self.source_font = {"name": src.read_typetree()["m_Name"], "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()}
        else:
            self.source_font = None


class FontSet:
    """Font assets reachable from the localized fonts, with TMP character lookup."""

    def __init__(self, ex: Exporter):
        self.ex = ex
        self.fonts: dict[str, Font] = {}
        self._by_obj: dict[tuple, Font] = {}

    def by_key(self, key: str) -> Font:
        return self.of(self.ex.key_object(key))

    def of(self, obj) -> Font:
        k = _key(obj)
        if k not in self._by_obj:
            f = Font(self.ex, obj)
            if f.name in self.fonts:
                raise RuntimeError(f"two font assets named {f.name}")
            self._by_obj[k] = f
            self.fonts[f.name] = f
            for fb in f.fallback_objs:
                if fb is not None:
                    self.of(fb)
        return self._by_obj[k]

    def fallbacks(self, f: Font) -> list[Font]:
        return [self._by_obj[_key(o)] for o in f.fallback_objs if o is not None]

    def _in_font(self, f: Font, u: int):
        """GetCharacterFromFontAsset_Internal without fallbacks (regular weight, no italic)."""
        if u in f.chars:
            return ("baked", f)
        if u in TMP_SYNTHESIZED and not (f.dynamic and u in f.cmap):
            return ("synthesized", f)
        if f.dynamic and u in f.cmap:
            return ("runtime", f)                 # TryAddCharacterInternal: SDF rendered at runtime
        return None

    def _search(self, f: Font, u: int, searched: set):
        r = self._in_font(f, u)
        if r:
            return r
        for fb in self.fallbacks(f):
            if fb.name in searched:
                continue
            searched.add(fb.name)
            r = self._search(fb, u, searched)
            if r:
                return r
        return None

    def lookup(self, primary: Font, u: int):
        """TMP_Text.GetTextElement for a font asset with no global fallbacks set."""
        r = self._in_font(primary, u)
        if r:
            return r
        searched: set = set()
        for fb in self.fallbacks(primary):
            if fb.name in searched:
                continue
            searched.add(fb.name)
            r = self._search(fb, u, searched)
            if r:
                return r
        return None

    def baked_anywhere(self, u: int) -> list[str]:
        return sorted(f.name for f in self.fonts.values() if u in f.chars)


# --------------------------------------------------------------------------
# runtime glyphs of dynamic font assets
# --------------------------------------------------------------------------
# UnityEngine.TextCore.LowLevel.GlyphRasterModes: a GlyphRenderMode
# is a set of these flags; SDF = 4134 = RASTER_MODE_1X | RASTER_MODE_SDF | RASTER_MODE_NO_HINTING |
# RASTER_MODE_MONO.
RASTER_MODE = {"8BIT": 1, "MONO": 2, "NO_HINTING": 4, "HINTED": 8, "BITMAP": 16, "SDF": 32, "SDFAA": 64,
               "1X": 4096, "8X": 8192, "16X": 16384, "32X": 32768}
GLYPH_METRIC_FIELDS = ("m_Width", "m_Height", "m_HorizontalBearingX", "m_HorizontalBearingY",
                       "m_HorizontalAdvance")


class RuntimeGlyphs:
    """Glyphs TMP adds to a dynamic font asset at runtime (TMP_FontAsset.TryAddCharacterInternal ->
    FontEngine.TryAddGlyphToTexture), generated from the source font file.

    FontEngine is native (not in the IL2CPP code), so the algorithm is the one that reproduces
    the baked atlas of the same font asset (checked by validate()):
      - face at pointSize px/em (FT_Set_Char_Size(pointSize*64) at 72 dpi), outline loaded without
        hinting; metrics are FreeType's 26.6 glyph metrics;
      - glyph rect = the metrics box with each edge rounded to the nearest pixel, ties outwards;
      - binary raster = FreeType monochrome rasterizer (FT_RENDER_MODE_MONO: pixel centres inside
        the outline) placed on the rect + padding grid (glyph origin at the rounded box corner);
      - exact Euclidean distance transform between pixel centres on that grid: inside pixels get
        the distance to the nearest outside pixel, outside pixels to the nearest inside pixel;
      - alpha = round(255 * clamp01(0.5 + (dIn - dOut) / (2 * (padding + 1)))), i.e. the spread is
        padding + 1 texels (= the material _GradientScale) on each side.
    """

    ALGORITHM = ("FreeType monochrome raster (FT_RENDER_MODE_MONO, FT_LOAD_NO_HINTING, 1x) + exact Euclidean "
                 "distance transform between texel centres (scipy.ndimage.distance_transform_edt)")
    MAPPING = "alpha8 = round(255 * clamp01(0.5 + (dIn - dOut) / (2 * (padding + 1)))), distances in texels"
    ALIGNMENT = ("rect = metrics box (bearingX, bearingY - height, width, height) with each edge rounded to the "
                 "nearest pixel, ties outwards; the raster's pixel (i, j) of the rect is the glyph-space pixel "
                 "[xMin + i, xMin + i + 1) x [yMin + j, yMin + j + 1) at pointSize px/em, texel centres sampled; "
                 "the SDF block is the rect grown by padding on each side")

    def __init__(self, font: Font):
        import freetype
        from scipy.ndimage import distance_transform_edt
        self.ft = freetype
        self.edt = distance_transform_edt
        tt = font.tt
        mode = tt["m_AtlasRenderMode"]
        want = RASTER_MODE["1X"] | RASTER_MODE["SDF"] | RASTER_MODE["NO_HINTING"] | RASTER_MODE["MONO"]
        if mode != want:
            raise NotImplementedError(f"{font.name}: runtime glyphs for render mode {mode}")
        if font.font_data is None:
            raise RuntimeError(f"{font.name}: dynamic font asset without a source font file")
        self.font = font
        self.pad = tt["m_AtlasPadding"]
        self.spread = self.pad + 1
        self.point_size = tt["m_FaceInfo"]["m_PointSize"]
        if self.point_size != int(self.point_size):
            raise NotImplementedError(f"{font.name}: fractional point size {self.point_size}")
        self.face = freetype.Face(io.BytesIO(font.font_data), tt["m_FaceInfo"]["m_FaceIndex"])
        self.face.set_char_size(int(self.point_size) * 64, 0, 72, 72)
        if self.face.units_per_EM != tt["m_FaceInfo"]["m_UnitsPerEM"]:
            raise RuntimeError(f"{font.name}: units per EM differ from the face info")

    def glyph_index(self, u: int) -> int:
        return self.face.get_char_index(u)

    def metrics(self, gi: int) -> dict:
        self.face.load_glyph(gi, self.ft.FT_LOAD_NO_HINTING)
        m = self.face.glyph.metrics
        return {"m_Width": m.width / 64, "m_Height": m.height / 64, "m_HorizontalBearingX": m.horiBearingX / 64,
                "m_HorizontalBearingY": m.horiBearingY / 64, "m_HorizontalAdvance": m.horiAdvance / 64}

    @staticmethod
    def pixel_box(m: dict) -> tuple[int, int, int, int]:
        x0 = m["m_HorizontalBearingX"]; x1 = x0 + m["m_Width"]
        y1 = m["m_HorizontalBearingY"]; y0 = y1 - m["m_Height"]
        xmin, ymin = math.ceil(x0 - 0.5), math.ceil(y0 - 0.5)
        return xmin, ymin, math.floor(x1 + 0.5) - xmin, math.floor(y1 + 0.5) - ymin

    def sdf(self, gi: int) -> tuple[dict, tuple, np.ndarray]:
        """-> (metrics, (xMin, yMin, w, h) rect box in glyph pixels, alpha block (h+2p, w+2p) bottom row first)."""
        ft = self.ft
        m = self.metrics(gi)
        xmin, ymin, w, h = self.pixel_box(m)
        p = self.pad
        self.face.load_glyph(gi, ft.FT_LOAD_NO_HINTING)
        self.face.glyph.render(ft.FT_RENDER_MODE_MONO)
        bm = self.face.glyph.bitmap
        grid = np.zeros((h + 2 * p, w + 2 * p), bool)
        if bm.rows and bm.width:
            if bm.pixel_mode != ft.FT_PIXEL_MODE_MONO:
                raise RuntimeError(f"glyph {gi}: FreeType returned pixel mode {bm.pixel_mode}")
            raw = np.frombuffer(bytes(bm.buffer), np.uint8).reshape(bm.rows, bm.pitch)
            bits = np.unpackbits(raw, axis=1)[:, :bm.width].astype(bool)[::-1]      # bottom row first
            ox = self.face.glyph.bitmap_left - (xmin - p)
            oy = (self.face.glyph.bitmap_top - bm.rows) - (ymin - p)
            if ox < 0 or oy < 0 or ox + bm.width > grid.shape[1] or oy + bm.rows > grid.shape[0]:
                raise RuntimeError(f"glyph {gi}: raster outside the padded rect")
            grid[oy:oy + bm.rows, ox:ox + bm.width] = bits
        if not grid.any():
            return m, (xmin, ymin, w, h), np.zeros(grid.shape, np.uint8)
        d = self.edt(grid) - self.edt(~grid)
        a = np.floor(255.0 * np.clip(0.5 + d / (2.0 * self.spread), 0.0, 1.0) + 0.5).astype(np.uint8)
        return m, (xmin, ymin, w, h), a

    def validate(self, packer: TexelPacker, units) -> dict:
        """Generate the baked characters `units` of the same font asset and compare with its atlas."""
        f, p = self.font, self.pad
        errs, per, metric_diff = [], {}, {k: 0.0 for k in GLYPH_METRIC_FIELDS}
        index_mismatch, rect_mismatch = [], []
        for u in sorted(units):
            c = f.chars[u]
            g = f.glyphs[c["m_GlyphIndex"]]
            if self.glyph_index(u) != c["m_GlyphIndex"]:
                index_mismatch.append(u)
            gm = self.metrics(c["m_GlyphIndex"])
            for k in GLYPH_METRIC_FIELDS:
                metric_diff[k] = max(metric_diff[k], abs(gm[k] - g["m_Metrics"][k]))
            gr = g["m_GlyphRect"]
            if g["m_Metrics"]["m_Width"] <= 0 or g["m_Metrics"]["m_Height"] <= 0:
                continue                                    # no outline (space): nothing rendered
            m, (xmin, ymin, w, h), a = self.sdf(c["m_GlyphIndex"])
            if (w, h) != (gr["m_Width"], gr["m_Height"]):
                rect_mismatch.append(u)
                continue
            atlas = packer.pixels(f.atlases[g["m_AtlasIndex"]])[:, :, 3]
            y0, x0 = gr["m_Y"] - p, gr["m_X"] - p
            ref = atlas[y0:y0 + h + 2 * p, x0:x0 + w + 2 * p].astype(np.int32)
            if ref.shape != a.shape:
                raise RuntimeError(f"{f.name}: glyph {c['m_GlyphIndex']} block leaves the atlas")
            e = np.abs(a.astype(np.int32) - ref)
            errs.append(e.ravel())
            per[chr(u)] = {"max": int(e.max()), "mean": float(e.mean()), "differing": int((e > 0).sum()),
                           "texels": int(e.size)}
        allerr = np.concatenate(errs) if errs else np.zeros(0, np.int32)
        worst = sorted(per.items(), key=lambda kv: (-kv[1]["max"], -kv[1]["differing"]))[:5]
        return {"glyphs": len(per), "texels": int(allerr.size),
                "maxAbsError": int(allerr.max()) if allerr.size else 0,
                "meanAbsError": round(float(allerr.mean()), 6) if allerr.size else 0.0,
                "texelsDiffering": int((allerr > 0).sum()),
                "exactGlyphs": sum(1 for v in per.values() if v["max"] == 0),
                "worstGlyphs": [{"char": ch, **v} for ch, v in worst],
                "metricsMaxAbsDiff": metric_diff, "glyphIndexMismatches": index_mismatch,
                "rectSizeMismatches": rect_mismatch,
                "units": "alpha8 levels over the glyph rect grown by padding; metrics in pointSize px"}


def rgba_alpha(a: np.ndarray) -> np.ndarray:
    out = np.zeros(a.shape + (4,), np.uint8)
    out[:, :, 3] = a
    return out


def material_by_name(ex: Exporter, font: Font, mat_name: str):
    key = font_dir(font.name) + mat_name
    if not ex.cat.has(key):
        raise KeyError(f"material key {key}")
    return ex.material(ex.key_object(key))


def runtime_glyphs(f: Font, extra: set, baked: set, margin: int, packer: TexelPacker,
                    chars_out: dict, glyphs_out: dict) -> dict:
    """Characters of a dynamic font asset that TMP renders into the atlas at runtime: generate their
    SDF glyphs + metrics from the source font, add them to the glyph/character tables and the packed
    font texture, and validate the generator against the baked glyphs `baked` of the same asset."""
    gen = RuntimeGlyphs(f)
    p = gen.pad
    if margin < p:
        raise RuntimeError(f"{f.name}: sampling margin {margin} < padding {p}")
    validation = gen.validate(packer, baked)
    settings = [a.read_typetree()["m_TextureSettings"] for a in f.atlases if a is not None]
    if not settings or any(s != settings[0] for s in settings):
        raise RuntimeError(f"{f.name}: atlas pages with different sampler settings")
    x = 0
    generated = []
    for u in sorted(extra):
        gi = gen.glyph_index(u)
        if gi == 0:
            raise RuntimeError(f"{f.name}: U+{u:04X} not in the source font cmap")
        if str(gi) in glyphs_out:
            raise RuntimeError(f"{f.name}: runtime glyph {gi} already in the table")
        m, (xmin, ymin, w, h), a = gen.sdf(gi)
        # virtual atlas page: one cell per glyph, rect + margin on each side, cleared to 0
        rect = {"m_X": x + margin, "m_Y": margin, "m_Width": w, "m_Height": h}
        x += w + 2 * margin
        rec = {"metrics": m, "rect": rect, "scale": 1.0, "atlasIndex": None, "runtime": True}
        if w > 0 and h > 0:
            cell = np.zeros((h + 2 * margin, w + 2 * margin), np.uint8)
            cell[margin - p:margin - p + a.shape[0], margin - p:margin - p + a.shape[1]] = a
            rec["_block"] = packer.add_array(f"font_{f.name}", f"runtime glyph {gi}", rgba_alpha(cell),
                                             (rect["m_X"] - margin, rect["m_Y"] - margin), settings[0])
        chars_out[str(u)] = {"glyph": gi, "scale": 1.0, "elementType": 1}
        glyphs_out[str(gi)] = rec
        generated.append({"char": chr(u), "unicode": u, "glyph": gi})
    return {
        "renderMode": {"value": f.tt["m_AtlasRenderMode"],
                       "flags": [k for k, v in RASTER_MODE.items() if f.tt["m_AtlasRenderMode"] & v]},
        "pointSize": gen.point_size, "padding": p, "spread": gen.spread,
        "algorithm": gen.ALGORITHM, "mapping": gen.MAPPING, "alignment": gen.ALIGNMENT,
        "atlasPlacement": ("runtime state: rects are cells of a virtual page (atlasIndex null), one cell per "
                           "glyph with the sampling margin cleared to 0"),
        "generated": generated,
        "validation": validation,
        "unresolved": [
            "FontEngine is native code, so the algorithm is fitted to the baked atlas of this font asset. The "
            "GlyphRasterModes flags of render mode 4134 name MONO | NO_HINTING | SDF | 1X, and every baked glyph's "
            "texels equal the exact EDT of its own binary mask; the binary mask is taken from the installed "
            "FreeType's mono rasterizer. FreeType 2.13.2's mono raster differs from the baked masks in a few pixels "
            "whose centres lie within 0.12 px of curved outline parts (FreeType 2.10.3-2.13.2 give identical masks; "
            "2.8-2.10.1 differ more).",
            "Glyph rect size/placement rule (bbox edges rounded to the nearest pixel, ties outwards): fitted to the "
            "baked rects (all match).",
            "Runtime atlas placement: rect positions and the texels beyond the padding (neighbouring glyphs in "
            "the real dynamic atlas) are runtime state; zeros are used (a cleared page). Only samples beyond the "
            "padding (underlay offset, bilinear margin) can see them.",
            "The baked atlas is generated in the editor; runtime glyphs are assumed to be rasterized the same way by "
            "the player's FontEngine.",
        ],
    }


# --------------------------------------------------------------------------
# characters -> font assets, materials, glyph tables
# --------------------------------------------------------------------------
def glyph_coverage(fonts: FontSet, primary: Font, chars: list[int]) -> tuple[dict, dict, dict]:
    """TMP character lookup of `chars` (distinct code points) from `primary` through its fallback chain.
    -> (coverage report, {font: baked units}, {font: runtime units})."""
    coverage = {"primaryFont": primary.name, "characters": len(chars), "baked": [], "fallbackBaked": {},
                "runtimeGenerated": [], "synthesized": [], "missing": []}
    needed: dict[str, set] = {}
    runtime_units: dict[str, set] = {}
    for u in chars:
        r = fonts.lookup(primary, u)
        ch = chr(u)
        if r is None:
            coverage["missing"].append(ch)
            continue
        kind, f = r
        if kind == "baked":
            needed.setdefault(f.name, set()).add(u)
            if f is primary:
                coverage["baked"].append(ch)
            else:
                coverage["fallbackBaked"].setdefault(f.name, []).append(ch)
        elif kind == "synthesized":
            coverage["synthesized"].append(f"U+{u:04X}")
        else:
            runtime_units.setdefault(f.name, set()).add(u)
            coverage["runtimeGenerated"].append({"char": ch, "font": f.name,
                                                 "bakedIn": fonts.baked_anywhere(u)})
    coverage["counts"] = {"distinct": len(chars), "bakedPrimary": len(coverage["baked"]),
                          "bakedFallbackOnly": sum(len(v) for v in coverage["fallbackBaked"].values()),
                          "runtimeGenerated": len(coverage["runtimeGenerated"]),
                          "synthesized": len(coverage["synthesized"]), "missing": len(coverage["missing"])}
    # newline (and other control characters) resolve like any character in TMP;
    # report how the primary font serves them
    for u in (0x0A,):
        r = fonts.lookup(primary, u)
        coverage.setdefault("control", {})[f"U+{u:04X}"] = r[0] if r else None
    return coverage, needed, runtime_units


def text_materials(ex: Exporter, fonts: FontSet, localized: list[dict], primaries: set, needed: dict,
                   runtime_units: dict) -> tuple[dict, dict]:
    """The localized text materials (in text order), then the default material of every font asset that
    serves a character or is a primary. -> (all materials, text materials)."""
    materials: dict[str, dict] = {}
    for loc in localized:
        if loc["material"] not in materials:
            materials[loc["material"]] = material_by_name(ex, fonts.fonts[loc["fontAsset"]], loc["material"])
    text_mats = dict(materials)
    for f in fonts.fonts.values():
        if f.name in needed or f.name in runtime_units or f.name in primaries:
            m = ex.material(f.material_obj)
            materials.setdefault(m["material"], m)
    return materials, text_mats


def export_fonts(ex: Exporter, fonts: FontSet, primaries: set, needed: dict, runtime_units: dict,
                 text_mats: dict, packer: TexelPacker, subset: str) -> dict:
    """Font asset records with the character / glyph tables reduced to the `needed` baked characters (plus the
    baked control characters) and the runtime characters generated from the source font; glyph texel blocks
    go to `packer` (resolve them with resolve_font_blocks after packer.build())."""
    font_out = {}
    for f in fonts.fonts.values():
        units = needed.get(f.name, set())
        extra = runtime_units.get(f.name, set())
        if f.name not in primaries and not units and not extra:
            continue
        mat = ex.material(f.material_obj)
        gs = mat["floats"]["_GradientScale"]
        aw, ah = f.tt["m_AtlasWidth"], f.tt["m_AtlasHeight"]
        # the runtime replaces _TextureWidth/_TextureHeight with the packed size
        # (texel-space math in the shader stays the same); that needs them to be
        # the atlas size in the source material
        if (mat["floats"]["_TextureWidth"], mat["floats"]["_TextureHeight"]) != (aw, ah):
            raise RuntimeError(f"{f.name}: material texture size differs from the atlas size {aw}x{ah}")
        # Texels a glyph quad can sample, around its glyph rect: the quad padding
        # (padding + style padding, clamped to _GradientScale by TMP), the underlay
        # sample offset (-_UnderlayOffset * _ScaleRatioC * _GradientScale texels,
        # ScaleRatioC <= 1) and one texel for bilinear filtering.
        off = max([abs(m["floats"].get(k, 0.0)) for m in text_mats.values()
                   if "UNDERLAY_ON" in m["keywords"] for k in ("_UnderlayOffsetX", "_UnderlayOffsetY")] + [0.0])
        margin = math.ceil(gs) + math.ceil(off * gs) + 1
        tt = f.tt
        chars_out, glyphs_out = {}, {}
        for u in sorted(units | {c for c in TMP_SYNTHESIZED if c in f.chars}):
            c = f.chars[u]
            chars_out[str(u)] = {"glyph": c["m_GlyphIndex"], "scale": c["m_Scale"], "elementType": c["m_ElementType"]}
            g = f.glyphs[c["m_GlyphIndex"]]
            gr = g["m_GlyphRect"]
            rec = {"metrics": g["m_Metrics"], "rect": gr, "scale": g["m_Scale"], "atlasIndex": g["m_AtlasIndex"]}
            if gr["m_Width"] > 0 and gr["m_Height"] > 0:
                atlas = f.atlases[g["m_AtlasIndex"]]
                if atlas is None:
                    raise RuntimeError(f"{f.name}: glyph {g['m_Index']} on a missing atlas page")
                box = grow_box(gr["m_X"], gr["m_Y"], gr["m_X"] + gr["m_Width"], gr["m_Y"] + gr["m_Height"], margin)
                rec["_block"] = packer.add(f"font_{f.name}", atlas, *box)
            glyphs_out[str(g["m_Index"])] = rec
        runtime_rec = None
        if extra:
            runtime_rec = runtime_glyphs(f, extra, units, margin, packer, chars_out, glyphs_out)
        pair_records = tt["m_FontFeatureTable"]["m_GlyphPairAdjustmentRecords"]
        font_out[f.name] = {
            "faceInfo": tt["m_FaceInfo"], "atlasWidth": tt["m_AtlasWidth"], "atlasHeight": tt["m_AtlasHeight"],
            "atlasPadding": tt["m_AtlasPadding"], "atlasRenderMode": tt["m_AtlasRenderMode"],
            "atlasPopulationMode": tt["m_AtlasPopulationMode"],
            "atlases": [a.read_typetree()["m_Name"] if a is not None else None for a in f.atlases],
            "normalStyle": tt["normalStyle"], "normalSpacingOffset": tt["normalSpacingOffset"],
            "boldStyle": tt["boldStyle"], "boldSpacing": tt["boldSpacing"], "italicStyle": tt["italicStyle"],
            "tabSize": tt["tabSize"], "material": mat["material"],
            "fallbacks": [fb.name for fb in fonts.fallbacks(f)],
            "glyphPairAdjustmentRecords": len(pair_records),
            "sourceFont": f.source_font, "characters": chars_out, "glyphs": glyphs_out,
            "runtimeCharacters": sorted(extra),
            "runtimeGlyphs": runtime_rec,
            "subset": subset,
        }
        if pair_records:
            font_out[f.name]["glyphPairAdjustments"] = pair_adjustments(
                pair_records, {int(g) for g in glyphs_out})
    return font_out


def pair_adjustments(records: list, glyphs: set) -> dict:
    """The font asset's glyph pair adjustment lookup as TMP_FontAsset.InitializeGlyphPaidAdjustmentRecords-
    LookupDictionary builds it (key = first glyph | second glyph << 16, the first record of a key
    wins), reduced to the pairs whose two glyphs are exported. -> {key: {first, second, flags}} with the
    value records {xPlacement, yPlacement, xAdvance, yAdvance} and m_FeatureLookupFlags."""
    def value(r: dict) -> dict:
        v = r["m_GlyphValueRecord"]
        return {"xPlacement": v["m_XPlacement"], "yPlacement": v["m_YPlacement"],
                "xAdvance": v["m_XAdvance"], "yAdvance": v["m_YAdvance"]}
    out: dict[str, dict] = {}
    seen: set[int] = set()
    for r in records:
        a, b = r["m_FirstAdjustmentRecord"], r["m_SecondAdjustmentRecord"]
        key = a["m_GlyphIndex"] | (b["m_GlyphIndex"] << 16)
        if key in seen:
            continue
        seen.add(key)
        if a["m_GlyphIndex"] in glyphs and b["m_GlyphIndex"] in glyphs:
            out[str(key)] = {"first": value(a), "second": value(b), "flags": r["m_FeatureLookupFlags"]}
    return out


def resolve_font_blocks(font_out: dict) -> None:
    """After TexelPacker.build(): glyph texel blocks -> {texture, dx, dy} (integer offset into the packed texture)."""
    for fo in font_out.values():
        for g in fo["glyphs"].values():
            b = g.pop("_block", None)
            if b is not None:
                g["packed"] = {"texture": b["texture"], "dx": b["offset"][0], "dy": b["offset"][1]}
