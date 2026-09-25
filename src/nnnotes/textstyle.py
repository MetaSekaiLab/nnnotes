"""TextMesh Pro text layout and style as plain values (shared by the UI exports).

A text record describes what a TMP text draws with any font: its layout settings (size, auto-size range,
alignment, wrapping, overflow, spacing, margins), its colours and style flags, and the look of its TMP
distance-field material turned into face / outline / underlay values in em. `fontRole` names which of the
game's localized font slots the text uses, so a renderer maps roles to fonts of its own.

  language_fonts   Fwk.Localization.LocalizeManager font names per LanguageMode (level0)
  localize_text    LocalizeText.Init / OnFontChanged: the font asset, material and line spacing a TMP text ends up
                   with in the current language
  font_role        the role of a font asset: its slot in LocalizeManager's font name list
  material_style   TMP distance-field material properties -> face / outline / underlay values in em
  text_style       one TMP text component -> its text record
  TextStyles       text records of an exporter's TMP texts (reads the localized materials and face info)
"""
from __future__ import annotations

import struct

from .export import Exporter
from .languages import LANGUAGES
from .player import PlayerData

FONT_PREFIX = "EmbFont/"

# Fwk.Localization.LanguageMode / language field names of the text tables.
LANGUAGE_MODE = 2                      # TraditionalChinese (tw build)
LANGUAGE_FIELD = "traditionalChinese"
JAPANESE_MODE = 0
LANGUAGE_FIELDS = {mode: column[1:] for mode, column in LANGUAGES.values()}   # LanguageMode -> text field
# LocalizeManager.ApplyLanguageLineSpacing: lineSpacing per LanguageMode
# (immediates in code, not serialized data).
LANGUAGE_LINE_SPACING = {0: 0.0, 1: -100.0, 2: 5.0, 3: 5.0, 4: 5.0}

# Roles of LocalizeManager's font slots (_fontNames index): the main text face and the Latin / numeral face.
FONT_ROLES = ("primary", "number")

# TMPro enums (flags for FontStyles).
FONT_STYLES = {1: "bold", 2: "italic", 4: "underline", 8: "lowerCase", 16: "upperCase", 32: "smallCaps",
               64: "strikethrough", 128: "superscript", 256: "subscript", 512: "highlight"}
HORIZONTAL_ALIGNMENT = {1: "left", 2: "center", 4: "right", 8: "justified", 16: "flush", 32: "geometry"}
VERTICAL_ALIGNMENT = {256: "top", 512: "middle", 1024: "bottom", 2048: "baseline", 4096: "geometry",
                      8192: "capline"}
WRAPPING = {0: "noWrap", 1: "normal", 2: "preserveWhitespace", 3: "preserveWhitespaceNoWrap"}
OVERFLOW = {0: "overflow", 1: "ellipsis", 2: "masking", 3: "truncate", 4: "scrollRect", 5: "page", 6: "linked"}
COLOR_MODE = {0: "single", 1: "horizontalGradient", 2: "verticalGradient", 3: "fourCornersGradient"}
KERN = 0x6B65726E                      # OpenType feature tag "kern" (m_ActiveFontFeatures)

# Keywords of the TMP distance-field shaders the style values cover.
STYLE_KEYWORDS = {"OUTLINE_ON", "UNDERLAY_ON", "UNDERLAY_INNER", "RATIOS_OFF"}
CLAMP = 1.0                            # ShaderUtilities.m_clamp

UNITS = {
    "fontSize": "canvas units (fontSize, autoSize min / max)",
    "spacing": "characterSpacing, wordSpacing, lineSpacing, paragraphSpacing: 1/100 em, added to the advance / "
               "line pitch as TMP does",
    "em": "*Em values: fraction of the font size the text is drawn at",
    "offsetEm": "[x, y], +x right, +y down",
    "color": "{r, g, b, a} as serialized; fill = color x face.color",
}


class _Reader:
    """Little-endian serialized-field reader with 4-byte alignment."""

    def __init__(self, raw: bytes, pos: int = 0):
        self.raw, self.pos = raw, pos

    def align(self):
        self.pos = (self.pos + 3) & ~3

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.raw, self.pos)[0]
        self.pos += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.raw, self.pos)[0]
        self.pos += 8
        return v

    def u8(self) -> int:
        v = self.raw[self.pos]
        self.pos += 1
        return v

    def string(self) -> str:
        n = self.i32()
        s = self.raw[self.pos:self.pos + n].decode("utf-8")
        self.pos += n
        self.align()
        return s

    def strings(self) -> list[str]:
        return [self.string() for _ in range(self.i32())]


def language_fonts(player: PlayerData) -> dict:
    """Fwk.Localization.LocalizeManager (level0): font names per LanguageMode.

    Read field by field: the generated typetree for this class omits the
    alignment after `_dontDestroyOnload`, so the serialized bytes are parsed
    directly (layout: m_GameObject, m_Enabled, m_Script, m_Name,
    _dontDestroyOnload, _languageDataList[{_languageMode, _fontNames[],
    _additionalFontNames[]}], _materialTypeList[]).
    """
    hits = [o for o in player._by_type.get("MonoBehaviour", [])
            if player.script(o)[1:] == ("Fwk.Localization", "LocalizeManager")]
    if len(hits) != 1:
        raise RuntimeError(f"LocalizeManager: {len(hits)} objects")
    r = _Reader(hits[0].get_raw_data())
    r.i32(); r.i64()                      # m_GameObject
    r.u8(); r.align()                     # m_Enabled
    r.i32(); r.i64()                      # m_Script
    r.string()                            # m_Name
    r.u8(); r.align()                     # _dontDestroyOnload
    langs = {}
    for _ in range(r.i32()):
        mode = r.i32()
        langs[mode] = {"fontNames": r.strings(), "additionalFontNames": r.strings()}
    materials = r.strings()
    if r.pos != len(r.raw):
        raise RuntimeError(f"LocalizeManager: {len(r.raw) - r.pos} trailing bytes")
    return {"languages": langs, "materialTypes": materials}


# --------------------------------------------------------------------------
# localized font / material of a TMP text (LocalizeText)
# --------------------------------------------------------------------------
def font_swap(lang: dict, mode: int = LANGUAGE_MODE) -> dict:
    """LocalizeManager.TryGetFontIndex + LocalizeText.OnFontChanged: the index of a font
    asset name in the Japanese lookup table (Japanese LanguageData font names + " SDF") selects fonts[index] of
    the current language."""
    ja = lang["languages"][JAPANESE_MODE]["fontNames"]
    zh = lang["languages"][mode]["fontNames"]
    return {f"{j} SDF": f"{z} SDF" for j, z in zip(ja, zh)}


def material_type(material_name: str | None) -> str:
    """LocalizeManager.GetMaterialType: "Default", or the part after " - " without " (Instance)".
    (Its ".mat" branch rewrites the literal "Default", a no-op.)"""
    if material_name is None or " - " not in material_name:
        return "Default"
    return material_name[material_name.index(" - "):].replace(" - ", "").replace(" (Instance)", "")


def localize_text(path: str, font_asset: str, material: str, swap: dict, lang: dict,
                  mode: int = LANGUAGE_MODE) -> dict:
    """LocalizeText.Init -> OnFontChanged for a text with _localizeEnabled: font =
    fonts[TryGetFontIndex(font)], material = GetFontMaterial(index, GetMaterialType(sharedMaterial.name))
    ("<font> - <type>", key EmbFont/<font>/...), lineSpacing = ApplyLanguageLineSpacing."""
    if font_asset not in swap:
        raise RuntimeError(f"{path}: font {font_asset} not in the Japanese font lookup table")
    font_name = swap[font_asset]
    mat_type = material_type(material)
    if mat_type not in lang["materialTypes"]:
        # ExistsFontMaterial false -> GetDefaultFontMaterial; the loaded handle set is not modelled
        raise NotImplementedError(f"{path}: material type {mat_type} not in LocalizeManager._materialTypeList")
    return {"fontAsset": font_name, "material": f"{font_name[:-4]} - {mat_type}",
            "lineSpacing": LANGUAGE_LINE_SPACING[mode]}


def font_role(font_asset: str, lang: dict) -> str:
    """The role of a serialized font asset: its slot in the Japanese font name list (the slot LocalizeText
    swaps to the current language's font), named by FONT_ROLES."""
    ja = [f"{j} SDF" for j in lang["languages"][JAPANESE_MODE]["fontNames"]]
    if font_asset not in ja:
        raise RuntimeError(f"font {font_asset} not in the Japanese font lookup table")
    i = ja.index(font_asset)
    return FONT_ROLES[i] if i < len(FONT_ROLES) else f"font{i}"


def font_dir(font_asset: str) -> str:
    """Addressable folder of a font asset and its materials: EmbFont/<font name without " SDF">/."""
    return f"{FONT_PREFIX}{font_asset[:-4] if font_asset.endswith(' SDF') else font_asset}/"


# --------------------------------------------------------------------------
# material -> style values
# --------------------------------------------------------------------------
def scale_ratios(floats: dict, keywords) -> tuple[float, float]:
    """ShaderUtilities.UpdateShaderRatios (run by TMP before drawing): _ScaleRatioA (face / outline) and
    _ScaleRatioC (underlay)."""
    if "RATIOS_OFF" in keywords:
        return 1.0, 1.0
    gs = floats["_GradientScale"]
    dilate = floats.get("_FaceDilate", 0.0)
    weight = max(floats.get("_WeightNormal", 0.0), floats.get("_WeightBold", 0.0)) / 4.0
    t = max(1.0, weight + dilate + floats.get("_OutlineWidth", 0.0) + floats.get("_OutlineSoftness", 0.0))
    a = (gs - CLAMP) / (gs * t)
    c = 1.0
    if "_UnderlayOffsetX" in floats:
        rng = (weight + dilate) * (gs - CLAMP)
        t = max(1.0, max(abs(floats["_UnderlayOffsetX"]), abs(floats.get("_UnderlayOffsetY", 0.0)))
                + floats.get("_UnderlayDilate", 0.0) + floats.get("_UnderlaySoftness", 0.0))
        c = max(0.0, gs - CLAMP - rng) / (gs * t)
    return a, c


def material_style(material: dict, point_size: float, face_scale: float = 1.0) -> dict:
    """The look a TMP distance-field material gives a text, in em of the drawn font size.

    Distance-field values are texels of the font atlas (rendered at `point_size` px per em, times the face info
    scale), scaled as TextMeshPro/Mobile/Distance Field does:
      face edge moved outwards by (weight / 4 + _FaceDilate) * ratioA * _GradientScale texels, weight =
      _WeightNormal (_WeightBold for bold text);
      outline band +-_OutlineWidth * ratioA * _GradientScale texels around the face edge (OUTLINE_ON);
      edge ramp width 2 * _OutlineSoftness * ratioA * _GradientScale texels;
      underlay (UNDERLAY_ON / UNDERLAY_INNER) = the face shape moved by _UnderlayOffset * ratioC * _GradientScale
      texels (+y up in the atlas), grown by _UnderlayDilate * ratioC * _GradientScale texels, ramp width
      2 * _UnderlaySoftness * ratioC * _GradientScale texels, drawn behind the face (inside it for
      UNDERLAY_INNER).
    """
    f, c, kw = material["floats"], material["colors"], set(material["keywords"])
    extra = kw - STYLE_KEYWORDS
    if extra:
        raise NotImplementedError(f"{material['material']}: style keywords {sorted(extra)}")
    if "_GradientScale" not in f:
        raise NotImplementedError(f"{material['material']}: not a distance-field text material")
    ra, rc = scale_ratios(f, kw)
    em = f["_GradientScale"] * face_scale / point_size          # em per unit of SDF distance

    def r6(v: float) -> float:
        return round(v, 6)

    face = {"color": c.get("_FaceColor", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
            "dilateEm": r6((f.get("_WeightNormal", 0.0) / 4 + f.get("_FaceDilate", 0.0)) * ra * em),
            "boldDilateEm": r6((f.get("_WeightBold", 0.0) / 4 + f.get("_FaceDilate", 0.0)) * ra * em),
            "softnessEm": r6(2 * f.get("_OutlineSoftness", 0.0) * ra * em)}
    outline = None
    if "OUTLINE_ON" in kw:
        outline = {"color": c["_OutlineColor"], "widthEm": r6(f["_OutlineWidth"] * ra * em),
                   "softnessEm": face["softnessEm"]}
    underlay = None
    if kw & {"UNDERLAY_ON", "UNDERLAY_INNER"}:
        underlay = {"color": c["_UnderlayColor"],
                    "offsetEm": [r6(f["_UnderlayOffsetX"] * rc * em), r6(-f["_UnderlayOffsetY"] * rc * em)],
                    "dilateEm": r6(f["_UnderlayDilate"] * rc * em),
                    "softnessEm": r6(2 * f["_UnderlaySoftness"] * rc * em),
                    "inner": "UNDERLAY_INNER" in kw}
    return {"face": face, "outline": outline, "underlay": underlay}


def face_metrics(face_info: dict, spacing: dict | None = None) -> dict:
    """Line metrics of a font asset in em (TMP line pitch = lineHeight + lineSpacing / 100 em), and the
    spacing it adds to every advance (normalSpacingOffset; boldSpacing for bold text) in 1/100 em."""
    k = face_info["m_Scale"] / face_info["m_PointSize"]
    out = {"lineHeightEm": round(face_info["m_LineHeight"] * k, 6),
           "ascentEm": round(face_info["m_AscentLine"] * k, 6),
           "descentEm": round(face_info["m_DescentLine"] * k, 6)}
    if spacing is not None:
        out["spacingOffset"] = spacing["normalSpacingOffset"]
        out["boldSpacing"] = spacing["boldSpacing"]
    return out


# --------------------------------------------------------------------------
# text record
# --------------------------------------------------------------------------
def _flags(value: int, names: dict) -> list[str]:
    return [n for bit, n in names.items() if value & bit]


def _enum(value: int, names: dict):
    return names.get(value, value)


def text_style(comp: dict, localize: dict | None, lang: dict, style: dict, mode: int = LANGUAGE_MODE) -> dict:
    """The text record of one TMP text component (serialized fields, as in the prefab / scene node) with its
    LocalizeText component (or None) and the material_style of the material it draws with."""
    localized = bool(localize and localize["m_Enabled"] and localize["_localizeEnabled"])
    key = (localize or {}).get("_masterTextID")
    m = comp["m_margin"]
    rec = {
        "class": comp.get("class", comp.get("type")),
        "enabled": bool(comp["m_Enabled"]),
        "fontRole": font_role(comp["m_fontAsset"]["name"], lang),
        "materialType": material_type(comp["m_sharedMaterial"]["material"]),
        "localized": localized,
        "textKey": key if key not in (None, "", "0") else None,
        "text": comp["m_text"],
        "richText": bool(comp["m_isRichText"]),
        "parseControlCharacters": bool(comp["m_parseCtrlCharacters"]),
        "fontSize": comp["m_fontSize"],
        "autoSize": {"enabled": bool(comp["m_enableAutoSizing"]), "min": comp["m_fontSizeMin"],
                     "max": comp["m_fontSizeMax"], "maxCharWidthAdjust": comp["m_charWidthMaxAdj"],
                     "maxLineSpacingAdjust": comp["m_lineSpacingMax"]},
        "fontStyle": _flags(comp["m_fontStyle"], FONT_STYLES),
        "fontWeight": comp["m_fontWeight"],
        "alignment": {"horizontal": _enum(comp["m_HorizontalAlignment"], HORIZONTAL_ALIGNMENT),
                      "vertical": _enum(comp["m_VerticalAlignment"], VERTICAL_ALIGNMENT)},
        "wrapping": _enum(comp["m_TextWrappingMode"], WRAPPING),
        "overflow": _enum(comp["m_overflowMode"], OVERFLOW),
        "margin": {"left": m["x"], "top": m["y"], "right": m["z"], "bottom": m["w"]},
        "lineSpacing": {"serialized": comp["m_lineSpacing"],
                        "applied": LANGUAGE_LINE_SPACING[mode] if localized else comp["m_lineSpacing"],
                        "byLanguage": ({LANGUAGE_FIELDS[k]: v for k, v in LANGUAGE_LINE_SPACING.items()}
                                       if localized else None)},
        "paragraphSpacing": comp["m_paragraphSpacing"],
        "characterSpacing": comp["m_characterSpacing"],
        "wordSpacing": comp["m_wordSpacing"],
        "characterHorizontalScale": comp.get("m_characterHorizontalScale", 1.0),
        "kerning": bool(comp["m_enableKerning"]) or KERN in comp.get("m_ActiveFontFeatures", []),
        "rightToLeft": bool(comp["m_isRightToLeft"]),
        "orthographic": bool(comp["m_isOrthographic"]),
        "color": comp["m_fontColor"],
        "colorMode": _enum(comp["m_colorMode"], COLOR_MODE),
        "colorGradient": comp["m_fontColorGradient"] if comp["m_enableVertexGradient"] else None,
        "overrideHtmlColors": bool(comp["m_overrideHtmlColors"]),
        **style,
    }
    return rec


class TextStyles:
    """Text records of TMP texts read through an Exporter: the material each text draws with in the current
    language (LocalizeText) and the face info of its font asset; no font data is kept."""

    def __init__(self, ex: Exporter, player: PlayerData, mode: int = LANGUAGE_MODE):
        self.ex, self.mode = ex, mode
        self.lang = language_fonts(player)
        self.swap = font_swap(self.lang, mode)
        self.roles: dict[str, dict] = {}
        self._styles: dict[tuple, dict] = {}
        self._faces: dict[str, tuple] = {}

    def _face(self, font_asset: str) -> tuple[dict, dict]:
        if font_asset not in self._faces:
            tt = self.ex.key_object(font_dir(font_asset) + font_asset).read_typetree()
            self._faces[font_asset] = (tt["m_FaceInfo"], {k: tt[k] for k in ("normalSpacingOffset", "boldSpacing")})
        return self._faces[font_asset]

    def record(self, path: str, comp: dict, localize: dict | None) -> dict:
        """The text record of `comp` (TMP text component) with its LocalizeText component `localize`."""
        fa, mat = comp["m_fontAsset"]["name"], comp["m_sharedMaterial"]["material"]
        if localize and localize["m_Enabled"] and localize["_localizeEnabled"]:
            loc = localize_text(path, fa, mat, self.swap, self.lang, self.mode)
            fa, mat = loc["fontAsset"], loc["material"]
        if (fa, mat) not in self._styles:
            key = font_dir(fa) + mat
            if not self.ex.cat.has(key):
                raise KeyError(f"material key {key}")
            face, extra = self._face(fa)
            self._styles[(fa, mat)] = material_style(self.ex.material(self.ex.key_object(key)),
                                                     face["m_PointSize"], face["m_Scale"])
        rec = text_style(comp, localize, self.lang, self._styles[(fa, mat)], self.mode)
        if rec["fontRole"] not in self.roles:
            face, extra = self._face(fa)
            self.roles[rec["fontRole"]] = face_metrics(face, extra)
        return rec

    def summary(self) -> dict:
        """The document-level part: units and the line metrics of each role used (current language)."""
        return {"language": LANGUAGE_FIELDS[self.mode], "units": UNITS, "roles": self.roles}
