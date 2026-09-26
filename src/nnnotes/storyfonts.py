"""The TextMeshPro font data of a story's texts in one language -> <story>/ui/fonts.json, ui/fonts/*.png and
ui/languages.json (the site's per-language files, storysite.py; format in ournotes-player docs/story-data-format.md).

  ui/fonts.json       font assets reduced to the characters the episode shows in the language, their glyph pages
                      (ui/fonts/*.png), the text materials, their GLES3 keyword sets and the text binding of every
                      text node of ui/ui.json: its serialized TextMeshPro fields and the font asset, material and
                      line spacing it uses in the language (LocalizeText); with open fonts also `chatTexts` and
                      `dialogTexts`, the bindings of the chat window texts of ui.json `chatTexts` and of the text
                      nodes of its `dialogs`
  ui/languages.json   the language's LanguageMode, text field, line spacing and the font asset and line metrics of
                      each font role
  ui/shaders/         the shader the text materials name (TextMeshPro/Mobile/Distance Field) is added to the UI's

Two sources give the same format:

  open_fonts  (default) glyphs generated from a font file per language: each text's localized game font asset
              (LocalizeText) is replaced by an asset of the font file with the game asset's point size, padding,
              style settings and render mode, holding the characters of the episode's text table, its title, the
              static labels of the UI and the chat windows' status and run-time texts in that language plus
              TextMeshPro's synthesized control characters and U+005F (UNDERLINE_CHARACTER). Face info and glyph
              metrics come from the font file at that point size (FreeType, no hinting); the distance field is
              tmpfont.RuntimeGlyphs' (the supersampled render modes rasterize at up to MAX_OVERSAMPLE times the size,
              recorded in atlasRenderMode; the anti-aliased distance-field modes are generated as the 1X distance
              field and recorded as SDF). Glyphs are shelf-packed, U+005F first (on the first page), then in code
              point order of their first character into pages of PAGE_WIDTH texels, at most PAGE_MAX_HEIGHT high;
              every page of an asset has the same size. The text materials are the game's localized materials with
              the page as _MainTex and the page size as _TextureWidth / _TextureHeight. No glyph or atlas texel of
              the game is read.
  game_fonts  the game's TMP font assets (advui.extract with fonts="game", tmpfont.export_fonts): the same records
              moved from its ui.json into fonts.json, the glyph pages into ui/fonts/.

Same inputs (font file, episode, game data) and library versions give the same bytes; generated glyph cells are kept
in the process cache (disk layer when the build configures one) by font file, sizes and glyph.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np

from . import advui, cache, jsonio, languages, master, textstyle, tmpfont
from . import shader as shader_mod
from .export import Exporter, _safe, packed_png

FONTS_FORMAT = "ournotes.story-fonts/1"
LANGUAGE_FORMAT = "ournotes.story-language/1"
FONTS_DOC = "fonts.json"
LANGUAGE_DOC = "languages.json"
PAGES_DIR = "fonts"
PAGE_WIDTH = 2048
PAGE_MAX_HEIGHT = 4096
MAX_OVERSAMPLE = 8
SOURCES = ("open", "game")
TEXT_SHADER_PREFIX = "TextMeshPro/"
# UnityEngine.TextCore.LowLevel.GlyphRasterModes flags of the supersampled render modes -> raster pixels per texel
OVERSAMPLE_FLAGS = {tmpfont.RASTER_MODE["8X"]: 8, tmpfont.RASTER_MODE["16X"]: 16, tmpfont.RASTER_MODE["32X"]: 32}
ELEMENT_CHARACTER = 1                   # TMP TextElementType.Character
UNDERLINE_CHARACTER = advui.UNDERLINE_CHARACTER       # kept on the first page of every open font asset
# ruby settings of the ruby text classes (RubyTextMeshProUGUI, RubyEmojiTextMeshProUGUI) and of Fwk.UI.UIRubyText
RUBY_CLASSES = ("RubyTextMeshProUGUI", "RubyEmojiTextMeshProUGUI")
RUBY_FIELDS = ("_rubyVerticalOffset", "_rubyScale", "_rubyLineHeight", "_rubyShowType", "_rubyMargin")
UI_RUBY_CLASS = "UIRubyText"
UI_RUBY_FIELDS = ("_rubyMarginTop",)
KOREAN_ADJUST_CLASS = "LocalizeKoreanAdjust"            # the font style a text gets in Korean (LocalizeKoreanAdjust.Apply)
GLYPHS = cache.bucket("storyglyphs", disk=True)
FACE_KEYS = ("m_FaceIndex", "m_FamilyName", "m_StyleName", "m_PointSize", "m_Scale", "m_UnitsPerEM", "m_LineHeight",
             "m_AscentLine", "m_CapLine", "m_MeanLine", "m_Baseline", "m_DescentLine", "m_SuperscriptOffset",
             "m_SuperscriptSize", "m_SubscriptOffset", "m_SubscriptSize", "m_UnderlineOffset", "m_UnderlineThickness",
             "m_StrikethroughOffset", "m_StrikethroughThickness", "m_TabWidth")
STYLE_KEYS = ("normalStyle", "normalSpacingOffset", "boldStyle", "boldSpacing", "italicStyle", "tabSize")
# OpenType name table ids
NAME_FAMILY, NAME_STYLE, NAME_VERSION, NAME_LICENSE, NAME_LICENSE_URL = 1, 2, 5, 13, 14
NAME_TYPO_FAMILY, NAME_TYPO_STYLE = 16, 17


def _dump(obj) -> bytes:
    return jsonio.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8") + b"\n"


def _salt() -> str:
    return cache.key(cache.source_salt(__file__), cache.source_salt(tmpfont.__file__, "freetype-py", "scipy", "numpy"))


# ---------------------------------------------------------------- font files
class FontFile:
    """A font file of one's own (OpenType / TrueType; the first face of a collection): its bytes, identity and names."""

    def __init__(self, path, face_index: int = 0):
        from fontTools.ttLib import TTFont
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.face_index = face_index
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        import io
        ft = TTFont(io.BytesIO(self.data), fontNumber=face_index, lazy=True)
        names = ft["name"]

        def name(*ids) -> str:
            for i in ids:
                rec = names.getDebugName(i)
                if rec:
                    return rec.strip()
            return ""
        self.family = name(NAME_TYPO_FAMILY, NAME_FAMILY)
        self.style = name(NAME_TYPO_STYLE, NAME_STYLE)
        if not self.family:
            raise ValueError(f"font {self.path.name}: no family name")
        self.version = name(NAME_VERSION)
        self.license = name(NAME_LICENSE_URL, NAME_LICENSE)

    @property
    def asset_name(self) -> str:
        return f"{self.family} {self.style} SDF".replace("  ", " ")

    def source(self, generator: dict) -> dict:
        return {"family": self.family, "style": self.style, "version": self.version, "license": self.license,
                "file": self.path.name, "faceIndex": self.face_index, "bytes": len(self.data), "sha256": self.sha256,
                "generator": generator}


def oversample_of(render_mode: int) -> tuple[int, int]:
    """(raster pixels per texel, the GlyphRenderMode recorded) for a game font asset's render mode: 1X modes as
    they are, the supersampled ones at most MAX_OVERSAMPLE (the recorded mode names the factor used). The
    anti-aliased distance-field modes (SDFAA, SDFAA_HINTED: a distance field from the 8-bit coverage raster) are
    generated as the 1X distance field of the monochrome raster and recorded as SDF; TextMeshPro draws both with
    the same distance-field shader and spread."""
    mode = tmpfont.RASTER_MODE
    if render_mode & mode["SDFAA"] and not render_mode & (mode["SDF"] | mode["BITMAP"]):
        return 1, mode["1X"] | mode["SDF"] | mode["NO_HINTING"] | mode["MONO"]
    if not render_mode & mode["SDF"]:
        raise NotImplementedError(f"render mode {render_mode}: not a distance-field mode")
    for flag, k in OVERSAMPLE_FLAGS.items():
        if render_mode & flag:
            if k <= MAX_OVERSAMPLE:
                return k, render_mode
            capped = next(f for f, v in OVERSAMPLE_FLAGS.items() if v == MAX_OVERSAMPLE)
            return MAX_OVERSAMPLE, (render_mode & ~flag) | capped
    return 1, render_mode


def face_info(gen: tmpfont.RuntimeGlyphs, font: FontFile, point_size: int) -> dict:
    """TMP face info (UnityEngine.TextCore.FaceInfo) of `font` at `point_size` px per em, as the game's font assets
    that embed their source font have it: line height, ascent and descent from the face's design metrics (unrounded),
    cap and mean line at the top of the H / x glyph (unhinted outline) rounded up to whole px (0 without the glyph),
    super- / subscript at the ascent / descent line with size 0.5, underline as FreeType gives it (the post table's
    position minus half its thickness), strikethrough at mean line / 2.5 with the underline thickness, tab width the
    space advance rounded to whole px. Values are float32, the type of the serialized fields."""
    face = gen.face
    s = point_size / face.units_per_EM

    def f32(v: float) -> float:
        return float(np.float32(v))

    def top(ch: str) -> float:
        gi = face.get_char_index(ord(ch))
        return float(math.ceil(gen.metrics(gi)["m_HorizontalBearingY"])) if gi else 0.0
    cap, mean = top("H"), top("x")
    space = face.get_char_index(0x20)
    underline = f32(face.underline_thickness * s)
    return {
        "m_FaceIndex": font.face_index, "m_FamilyName": font.family, "m_StyleName": font.style,
        "m_PointSize": float(point_size), "m_Scale": 1.0, "m_UnitsPerEM": face.units_per_EM,
        "m_LineHeight": f32(face.height * s), "m_AscentLine": f32(face.ascender * s), "m_CapLine": cap,
        "m_MeanLine": mean, "m_Baseline": 0.0, "m_DescentLine": f32(face.descender * s),
        "m_SuperscriptOffset": f32(face.ascender * s), "m_SuperscriptSize": 0.5,
        "m_SubscriptOffset": f32(face.descender * s), "m_SubscriptSize": 0.5,
        "m_UnderlineOffset": f32(face.underline_position * s), "m_UnderlineThickness": underline,
        "m_StrikethroughOffset": f32(mean / 2.5), "m_StrikethroughThickness": underline,
        "m_TabWidth": float(round(gen.metrics(space)["m_HorizontalAdvance"])) if space else 0.0,
    }


def glyph_cell(gen: tmpfont.RuntimeGlyphs, font: FontFile, gi: int, margin: int) -> tuple[dict, tuple, np.ndarray]:
    """gen.cell(gi, margin), kept in the GLYPHS cache."""
    k = GLYPHS.key(_salt(), font.sha256, font.face_index, gen.point_size, gen.pad, gen.oversample, margin, gi)
    hit = GLYPHS.get(k)
    if hit is not None:
        meta, (blob,) = cache.unpack(hit)
        m, box = json.loads(meta)
        return m, tuple(box), np.frombuffer(blob, np.uint8).reshape(box[3] + 2 * margin, box[2] + 2 * margin)
    m, box, a = gen.cell(gi, margin)
    GLYPHS.put(k, cache.pack(json.dumps([m, list(box)]).encode("utf-8"), [a.tobytes()]))
    return m, box, a


def pack_cells(cells: list[tuple[int, int, int]]) -> tuple[dict[int, tuple[int, int, int]], int, int]:
    """Shelf packing of cells [(glyph, width, height)] in the given order into pages PAGE_WIDTH wide (wider when a
    cell is), a new page when a shelf would pass PAGE_MAX_HEIGHT -> ({glyph: (page, x, y)}, width, height), every
    page of the same size."""
    width = max([PAGE_WIDTH] + [w for _, w, _ in cells])
    out, page, x, y, shelf, height = {}, 0, 0, 0, 0, 1
    for g, w, h in cells:
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        if y + h > PAGE_MAX_HEIGHT and y > 0:
            page, x, y, shelf = page + 1, 0, 0, 0
        out[g] = (page, x, y)
        x += w
        shelf = max(shelf, h)
        height = max(height, y + shelf)
    return out, width, height


# ---------------------------------------------------------------- texts
def text_components(ex: Exporter, ui_doc: dict) -> dict[str, list[dict]]:
    """{node path: the node's components} of every text node of the story UI (ui.json nodes with a text record), from
    the prefabs advui assembles (UIAdvWidget with the default talk window under TalkView)."""
    widget = {n["path"]: n for n in ex.prefab(advui.WIDGET_KEY)["nodes"]}
    window = {f"{advui.WINDOW_PARENT}/{n['path']}": n for n in ex.prefab(advui.WINDOW_KEY)["nodes"]}
    out = {}
    for rec in ui_doc["nodes"]:
        if "textStyle" not in rec:
            continue
        path = rec["path"]
        hits = [n for n in (widget.get(path), window.get(path)) if n is not None]
        if len(hits) != 1:
            raise RuntimeError(f"{path}: {len(hits)} prefab nodes")
        out[path] = hits[0]["components"]
    return out


def text_extras(path: str, comps: list[dict]) -> dict:
    """The settings of a text node's other text components, each only when the node has that component: `ruby` (the
    serialized RUBY_FIELDS of a ruby text component), `uiRubyText` (UI_RUBY_FIELDS of its UIRubyText component) and
    `localizeKoreanAdjust` (`_koreanFontStyle` and `m_Enabled` of its LocalizeKoreanAdjust component)."""
    out = {}
    for c in comps:
        if c.get("class") in RUBY_CLASSES:
            missing = [k for k in RUBY_FIELDS if k not in c]
            if missing:
                raise RuntimeError(f"{path}: ruby text without {missing}")
            out["ruby"] = {k: c[k] for k in RUBY_FIELDS}
        elif c.get("class") == UI_RUBY_CLASS:
            out["uiRubyText"] = {k: c[k] for k in UI_RUBY_FIELDS}
        elif c.get("class") == KOREAN_ADJUST_CLASS:
            out["localizeKoreanAdjust"] = {"_koreanFontStyle": c["_koreanFontStyle"], "m_Enabled": bool(c["m_Enabled"])}
    return out


def _binding(path: str, node: list[dict], swap: dict, lang: dict, mode: int) -> dict:
    """The text binding of a text node (its components `node`): the serialized TextMeshPro fields of its text
    component, as advui writes them with fonts="game", the settings of its other text components (text_extras) and
    `localized` = textstyle.localize_text (LocalizeText) in the language `mode`."""
    comps = [c for c in node if c.get("class") in advui.TMP_CLASSES]
    if len(comps) != 1:
        raise RuntimeError(f"{path}: {len(comps)} text components")
    c = comps[0]
    t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
    t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
              "material": c["m_sharedMaterial"]["material"]})
    t.update(text_extras(path, node))
    t["localized"] = textstyle.localize_text(path, t["fontAsset"], t["material"], swap, lang, mode)
    return t


def text_bindings(ex: Exporter, ui_doc: dict, lang: dict, mode: int) -> dict[str, dict]:
    """{node path: text binding (_binding)} of every text node of the story UI (text_components)."""
    swap = textstyle.font_swap(lang, mode)
    return {path: _binding(path, node, swap, lang, mode) for path, node in text_components(ex, ui_doc).items()}


def dialog_bindings(ex: Exporter, ui_doc: dict, lang: dict, mode: int) -> dict[str, dict[str, dict]]:
    """{dialog: {node path: text binding (_binding)}} of the text nodes of the dialogs of ui.json (`dialogs`, each
    from its prefab `key`)."""
    swap = textstyle.font_swap(lang, mode)
    out: dict[str, dict[str, dict]] = {}
    for name, d in sorted((ui_doc.get("dialogs") or {}).items()):
        prefab = {n["path"]: n["components"] for n in ex.prefab(d["key"])["nodes"]}
        for rec in d["nodes"]:
            if "textStyle" in rec:
                out.setdefault(name, {})[rec["path"]] = _binding(rec["path"], prefab[rec["path"]], swap, lang, mode)
    return out


def chat_bindings(ex: Exporter, episode: dict, lang: dict, mode: int) -> dict[str, dict[str, dict]]:
    """{chat window: {node path: text binding}} of the TMP texts of the episode's chat windows (advui.chat_text_nodes):
    the fields text_bindings gives, `localized` from advui.chat_localize for a text with LocalizeText enabled, else
    the serialized font asset, material and line spacing."""
    out: dict[str, dict[str, dict]] = {}
    nodes, _ = advui.chat_text_nodes(ex, episode)
    for window, node, c, loc in nodes:
        t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
        t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
                  "material": c["m_sharedMaterial"]["material"]})
        t.update(text_extras(node["path"], node["components"]))
        if loc and loc["m_Enabled"] and loc["_localizeEnabled"]:
            t["localized"] = advui.chat_localize(t["fontAsset"], t["material"], lang, mode)
        else:
            t["localized"] = {"fontAsset": t["fontAsset"], "material": t["material"], "lineSpacing": c["m_lineSpacing"]}
        out.setdefault(window, {})[node["path"]] = t
    return out


def shown_characters(episode: dict, ui_doc: dict, master_dir: Path | None, field: str) -> list[int]:
    """The code points the episode shows in the text field `field`: every text of its text table (lines, speaker
    names, frame and choice texts), its title, the static labels of the UI (the MasterText rows of the text keys of
    the text nodes and of the dialogs' text nodes, the `masterIdTexts` of ui.json) and of the chat windows
    (`chatStatusTexts`) and the texts the chat windows format at run time (advui.chat_runtime_texts), markup
    included."""
    texts = [row.get(field) for row in episode["text"].values()]
    texts.append((episode.get("title") or {}).get(field))
    texts += [t["text"] for t in (ui_doc.get("masterIdTexts") or {}).values()]
    texts += list((ui_doc.get("chatStatusTexts") or {}).values()) + advui.chat_runtime_texts(episode)
    nodes = list(ui_doc["nodes"]) + [n for d in (ui_doc.get("dialogs") or {}).values() for n in d["nodes"]]
    keys = {rec["textStyle"]["textKey"] for rec in nodes if "textStyle" in rec and rec["textStyle"].get("textKey")}
    if keys:
        if master_dir is None:
            raise RuntimeError(f"UI text keys {sorted(keys)} need the master data")
        rows = {r["_id"]: r for r in master.table(master_dir, "MasterText") if r["_id"] in keys}
        missing = sorted(keys - set(rows))
        if missing:
            raise KeyError(f"UI text keys not in MasterText: {missing}")
        texts += [rows[k].get(f"_{field}") for k in sorted(keys)]
    return sorted({ord(ch) for s in texts if isinstance(s, str) for ch in s})


def _material_textures(m: dict) -> None:
    """Texture references of a material as advui writes them: {name, width, height, format}."""
    for v in m["textures"].values():
        t = v["texture"]
        if t is not None:
            v["texture"] = {"name": t["name"], "width": t["width"], "height": t["height"], "format": t.get("format")}


def text_shaders(ex: Exporter, ui_dir: Path, materials: dict) -> dict[str, list[str]]:
    """Add the shaders the text `materials` name to ui/shaders (index kept in name order) and return each
    material's GLES3 keyword set (its keywords that the shader's GLES3 variants use; a vertex program with exactly
    those must exist)."""
    sdir = Path(ui_dir) / "shaders"
    index = json.loads((sdir / "shaders.json").read_text(encoding="utf-8"))
    have = {r["name"] for r in index}
    for name in sorted({m["shader"]["shader"] for m in materials.values()}):
        if name in have:
            continue
        if name not in ex.shaders:
            raise RuntimeError(f"shader {name} not in the loaded bundles")
        shader_mod.dump_objects([ex.shaders[name]], sdir, ex.shaders[name].assets_file.name, index)
    index.sort(key=lambda r: r["name"])
    shader_mod.write_index(index, sdir)
    recs = {r["name"]: r for r in index}
    out = {}
    for name, m in sorted(materials.items()):
        rec = recs[m["shader"]["shader"]]
        gles = [v for v in rec["variants"] if v["platform"] == "gles3" and v["type"] == "GLES3"]
        known = {k for v in gles for k in v["keywords"]}
        want = sorted(k for k in m["keywords"] if k in known)
        if not any(sorted(v["keywords"]) == want and v["stage"] == "vertex" for v in gles):
            raise RuntimeError(f"{rec['name']}: no GLES3 variant for material {name} keywords {want}")
        out[name] = want
    return out


def language_doc(language: str, source: str, fonts: dict, texts: dict, lang: dict) -> dict:
    """ui/languages.json of `language`: LanguageMode, text field, line spacing and per font role the font asset
    the role's texts use with its line metrics (textstyle.face_metrics)."""
    mode = languages.mode(language)
    roles: dict[str, dict] = {}
    for path, t in sorted(texts.items()):
        role = textstyle.font_role(t["fontAsset"], lang)
        fa = t["localized"]["fontAsset"]
        if roles.get(role, {}).get("fontAsset", fa) != fa:
            raise NotImplementedError(f"font role {role} uses two font assets ({roles[role]['fontAsset']}, {fa})")
        f = fonts[fa]
        roles[role] = {"fontAsset": fa, **textstyle.face_metrics(f["faceInfo"], {k: f[k] for k in
                                                                                  ("normalSpacingOffset",
                                                                                   "boldSpacing")})}
    return {"format": LANGUAGE_FORMAT, "language": language, "mode": mode, "field": languages.column(language)[1:],
            "lineSpacing": textstyle.LANGUAGE_LINE_SPACING[mode], "fonts": source, "roles": roles}


def _write(ui_dir: Path, fonts_doc: dict, lang_doc: dict) -> None:
    (Path(ui_dir) / FONTS_DOC).write_bytes(_dump(fonts_doc))
    (Path(ui_dir) / LANGUAGE_DOC).write_bytes(_dump(lang_doc))


def _exporter(cat, player, work: Path) -> Exporter:
    return Exporter(cat, Path(work), player=player, textures="deferred",
                    stub_assets=("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet"))


# ---------------------------------------------------------------- open fonts
PAGE_SETTINGS = {"m_FilterMode": 1, "m_Aniso": 1, "m_MipBias": 0.0, "m_WrapU": 1, "m_WrapV": 1, "m_WrapW": 1}
SUBSET = ("characters of the episode's text table, title and UI labels in this language, the synthesized "
          "control characters and U+005F")


def _own_material(m: dict, name: str, page: str, width: int, height: int) -> dict:
    """A game text material for an open font asset: named `name`, sampling `page` (width x height) as _MainTex,
    _TextureWidth / _TextureHeight the page size; every other value as in the game."""
    m = copy.deepcopy(m)
    _material_textures(m)
    m["material"] = name
    tex = m["textures"].get("_MainTex") or {}
    m["textures"]["_MainTex"] = {"texture": {"name": page, "width": width, "height": height,
                                             "format": (tex.get("texture") or {}).get("format")},
                                 "scale": tex.get("scale", {"x": 1.0, "y": 1.0}),
                                 "offset": tex.get("offset", {"x": 0.0, "y": 0.0})}
    m["floats"]["_TextureWidth"], m["floats"]["_TextureHeight"] = float(width), float(height)
    return m


def open_asset(font: FontFile, name: str, chars, game: dict, text_materials: dict[str, dict]) -> dict:
    """An open font asset of `font` named `name` for the code points `chars`, in place of the game font asset
    `game` = {pointSize, padding, renderMode, style {STYLE_KEYS}, material (its default material record)} whose texts
    use `text_materials` ({name: game material record}). -> {font (the asset record), textures {page: descriptor},
    pages [(page, RGBA array bottom row first)], materials {name: record}, renames {game material: open material},
    missing (code points the font does not map)}."""
    pad, point = game["padding"], game["pointSize"]
    gs = game["material"]["floats"]["_GradientScale"]
    if gs != pad + 1:
        raise NotImplementedError(f"{name}: _GradientScale {gs} is not padding + 1")
    k, render_mode = oversample_of(game["renderMode"])
    gen = tmpfont.RuntimeGlyphs.from_font_file(font.data, font.face_index, point, pad, k)
    off = max([abs(m["floats"].get(p, 0.0)) for m in text_materials.values() if "UNDERLAY_ON" in m["keywords"]
               for p in ("_UnderlayOffsetX", "_UnderlayOffsetY")] + [0.0])
    margin = math.ceil(gs) + math.ceil(off * gs) + 1       # the texels a quad can sample (tmpfont.export_fonts)

    # characters -> glyphs (a synthesized control character the font lacks: TMP's zero glyph 0)
    characters, glyph_of, missing = {}, {}, set()
    for u in sorted(set(chars) | set(tmpfont.TMP_SYNTHESIZED) | {UNDERLINE_CHARACTER}):
        gi = gen.glyph_index(u)
        if gi == 0 and u not in tmpfont.TMP_SYNTHESIZED:
            if u in chars:                           # a font without '_' has no underline glyph, nothing is shown
                missing.add(u)
            continue
        characters[str(u)] = {"glyph": gi, "scale": 1.0, "elementType": ELEMENT_CHARACTER}
        glyph_of.setdefault(gi, u)
    cells, recs = [], {}
    for gi, u in sorted(glyph_of.items(), key=lambda kv: (kv[1] != UNDERLINE_CHARACTER, kv[1])):
        if gi == 0:
            recs[gi] = ({f: 0.0 for f in tmpfont.GLYPH_METRIC_FIELDS}, (0, 0, 0, 0), None)
            continue
        m, box, a = glyph_cell(gen, font, gi, margin)
        recs[gi] = (m, box, a)
        if box[2] > 0 and box[3] > 0:
            cells.append((gi, box[2] + 2 * margin, box[3] + 2 * margin))
    where, width, height = pack_cells(cells)
    under = gen.glyph_index(UNDERLINE_CHARACTER)
    if under in where and where[under][0] != 0:
        raise RuntimeError(f"{name}: U+005F is not on the first page")
    npages = 1 + max([p for p, _, _ in where.values()] + [0])
    arrays = [np.zeros((height, width, 4), np.uint8) for _ in range(npages)]
    page_names = [f"font_{name}_{i}" for i in range(npages)]
    glyphs = {}
    for gi, (m, box, a) in sorted(recs.items()):
        rec = {"metrics": m, "rect": {"m_X": 0, "m_Y": 0, "m_Width": 0, "m_Height": 0}, "scale": 1.0,
               "atlasIndex": 0}
        if gi in where:
            p, x, y = where[gi]
            arrays[p][y:y + a.shape[0], x:x + a.shape[1], 3] = a
            rec["rect"] = {"m_X": x + margin, "m_Y": y + margin, "m_Width": box[2], "m_Height": box[3]}
            rec["atlasIndex"] = p
            rec["packed"] = {"texture": page_names[p], "dx": 0, "dy": 0}
        glyphs[str(gi)] = rec
    textures = {pn: {"texture": f"{PAGES_DIR}/{_safe(pn)}.png", "name": pn, "width": width, "height": height,
                     "mipCount": 1, "settings": dict(PAGE_SETTINGS)} for pn in page_names}
    base = name[:-4] if name.endswith(" SDF") else name
    default = f"{name} Material"
    materials = {default: _own_material(game["material"], default, page_names[0], width, height)}
    renames = {}
    for mname, m in sorted(text_materials.items()):
        new = f"{base} - {textstyle.material_type(mname)}"
        materials[new] = _own_material(m, new, page_names[0], width, height)
        renames[mname] = new
    generator = {"renderMode": render_mode, "gameRenderMode": game["renderMode"], "oversample": k,
                 "pointSize": point, "padding": pad, "margin": margin,
                 "algorithm": gen.ALGORITHM + (f", rasterized at {k}x per texel" if k > 1 else ""),
                 "mapping": gen.MAPPING, "freetype": ".".join(map(str, gen.ft.version()))}
    asset = {"faceInfo": face_info(gen, font, point),
             "atlasWidth": width, "atlasHeight": height, "atlasPadding": pad, "atlasRenderMode": render_mode,
             "atlasPopulationMode": 0, "atlases": page_names, **{kk: game["style"][kk] for kk in STYLE_KEYS},
             "material": default, "fallbacks": [], "glyphPairAdjustmentRecords": 0,
             "characters": characters, "glyphs": glyphs, "runtimeCharacters": [], "runtimeGlyphs": None,
             "source": font.source(generator), "subset": SUBSET}
    return {"font": asset, "textures": textures, "pages": list(zip(page_names, arrays)), "materials": materials,
            "renames": renames, "missing": missing}


def line_breaking(player) -> dict:
    """TMP_Settings' line breaking rules: the text of its leading and following characters TextAssets (as stored)
    and m_UseModernHangulLineBreakingRules."""
    o = player.resource("TMP Settings")
    tt = player.mono(o)

    def text(key: str) -> str:
        t = player.deref(o, tt[key])
        return t.read_typetree()["m_Script"] if t is not None else ""
    return {"leading": text("m_leadingCharacters"), "following": text("m_followingCharacters"),
            "useModernHangulLineBreakingRules": bool(tt["m_UseModernHangulLineBreakingRules"])}


def search_order(fallbacks_of, font, exported: set) -> list[str]:
    """The font assets TMP searches after `font` for a character `font` lacks, as tmpfont.FontSet.lookup
    (TMP_FontAssetUtilities.GetCharacterFromFontAsset_Internal: each fallback, then that fallback's own fallbacks,
    depth first, every asset once per search), reduced to the `exported` names, without `font` itself.
    `fallbacks_of(f)`: the fallback assets of f (objects with a `name`)."""
    out: list[str] = []
    searched: set = set()

    def walk(f) -> None:
        for fb in fallbacks_of(f):
            if fb.name in searched:
                continue
            searched.add(fb.name)
            if fb.name in exported and fb.name != font.name:
                out.append(fb.name)
            walk(fb)
    walk(font)
    return out


def fallback_chains(ex: Exporter, primaries: set, exported: set) -> dict[str, list[str]]:
    """{exported font asset: its search_order}, over the game's font assets reachable from the `primaries` (font
    asset names): the fallback list of a font record once the assets that serve no shown character are left out."""
    fs = tmpfont.FontSet(ex)
    for n in sorted(primaries):
        fs.by_key(textstyle.font_dir(n) + n)
    missing = sorted(exported - set(fs.fonts))
    if missing:
        raise RuntimeError(f"font assets {missing} are not reachable from {sorted(primaries)}")
    return {n: search_order(fs.fallbacks, fs.fonts[n], exported) for n in sorted(exported)}


def open_fonts(cat, player, episode: dict, ui_dir: Path, language: str, font: FontFile,
               master_dir: Path | None = None) -> dict:
    """ui/fonts.json, ui/fonts/*.png and ui/languages.json of `language` from the font file `font` (module
    docstring), next to the ui/ui.json advui.extract wrote for that language in `ui_dir`; the text shaders go to
    ui/shaders. Returns a summary."""
    tmpfont.require_extra()
    ui_dir = Path(ui_dir)
    ui_doc = json.loads((ui_dir / "ui.json").read_text(encoding="utf-8"))
    mode, field = languages.mode(language), languages.column(language)[1:]
    ex = _exporter(cat, player, ui_dir / "_fonts")
    lang = textstyle.language_fonts(player)
    texts = text_bindings(ex, ui_doc, lang, mode)
    dialogs = dialog_bindings(ex, ui_doc, lang, mode)
    chat = chat_bindings(ex, episode, lang, mode) if ui_doc.get("chatTexts") else {}
    chars = shown_characters(episode, ui_doc, master_dir, field)

    bindings = list(texts.values()) + [t for group in (dialogs, chat) for w in group.values() for t in w.values()]
    by_game: dict[str, list[dict]] = {}           # localized game font asset -> the bindings of its texts
    for t in bindings:
        by_game.setdefault(t["localized"]["fontAsset"], []).append(t)
    fonts, textures, materials, missing = {}, {}, {}, set()
    for game_name in sorted(by_game):
        gobj = ex.key_object(advui.font_key(cat, game_name, game_name))
        gtt = gobj.read_typetree()
        text_mats = {}
        for t in by_game[game_name]:
            mname = t["localized"]["material"]
            if mname not in text_mats:
                key = advui.font_key(cat, game_name, mname)
                if not cat.has(key):
                    raise KeyError(f"material key {key}")
                text_mats[mname] = ex.material(ex.key_object(key))
        game = {"pointSize": gtt["m_FaceInfo"]["m_PointSize"], "padding": gtt["m_AtlasPadding"],
                "renderMode": gtt["m_AtlasRenderMode"], "style": {kk: gtt[kk] for kk in STYLE_KEYS},
                "material": ex.material(ex.deref(gobj, gtt["m_Material"]))}
        name = font.asset_name if len(by_game) == 1 else f"{font.asset_name} ({game_name})"
        a = open_asset(font, name, chars, game, text_mats)
        fonts[name] = a["font"]
        textures.update(a["textures"])
        materials.update(a["materials"])
        missing |= a["missing"]
        (ui_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)
        for pname, px in a["pages"]:
            (ui_dir / a["textures"][pname]["texture"]).write_bytes(packed_png(px))
        for t in by_game[game_name]:
            loc = t["localized"]
            t["localized"] = {**loc, "fontAsset": name, "material": a["renames"][loc["material"]]}
    keywords = text_shaders(ex, ui_dir, materials)
    fonts_doc = {"format": FONTS_FORMAT, "language": language, "source": "open", "fonts": fonts,
                 "textures": textures, "materials": materials, "materialKeywords": keywords, "texts": texts,
                 "coverage": {"characters": len(chars), "missing": [chr(u) for u in sorted(missing)]},
                 "lineBreaking": line_breaking(player)}
    if dialogs:
        fonts_doc["dialogTexts"] = dialogs
    if chat:
        fonts_doc["chatTexts"] = chat
    _write(ui_dir, fonts_doc, language_doc(language, "open", fonts, texts, lang))
    shutil.rmtree(ui_dir / "_fonts", ignore_errors=True)
    return {"fonts": sorted(fonts), "characters": len(chars), "missing": len(missing),
            "glyphs": sum(len(f["glyphs"]) for f in fonts.values()), "pages": len(textures)}


# ---------------------------------------------------------------- game fonts
def game_fonts(cat, player, game_ui_dir: Path, ui_dir: Path, language: str) -> dict:
    """ui/fonts.json, ui/fonts/*.png and ui/languages.json of `language` from the ui/ directory advui.extract wrote
    with fonts="game" for that language (`game_ui_dir`): its font records (each font's `fallbacks` reduced to the
    exported assets in search order, fallback_chains), glyph pages, text materials, keyword sets, text records, glyph
    coverage and TMP settings, next to the fonts-open ui/ui.json in `ui_dir`; the text shaders go to ui/shaders as
    with open fonts. Returns a summary."""
    game_ui_dir, ui_dir = Path(game_ui_dir), Path(ui_dir)
    doc = json.loads((game_ui_dir / "ui.json").read_text(encoding="utf-8"))
    ui_doc = json.loads((ui_dir / "ui.json").read_text(encoding="utf-8"))
    texts = {n["path"]: n["text"] for n in doc["nodes"] if "text" in n}
    open_paths = {n["path"] for n in ui_doc["nodes"] if "textStyle" in n}
    if set(texts) != open_paths:
        raise RuntimeError("the text nodes of the fonts-game and fonts-open UI differ")
    fonts = doc["fonts"]
    pages = sorted({g["packed"]["texture"] for f in fonts.values() for g in f["glyphs"].values() if "packed" in g})
    textures = {}
    (ui_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)
    for p in pages:
        t = dict(doc["textures"][p])
        dst = f"{PAGES_DIR}/{Path(t['texture']).name}"
        shutil.copyfile(game_ui_dir / t["texture"], ui_dir / dst)
        t["texture"] = dst
        textures[p] = t
    names = {t["localized"]["material"] for t in texts.values()} | {f["material"] for f in fonts.values()}
    materials = {n: doc["materials"][n] for n in sorted(names)}
    ex = _exporter(cat, player, ui_dir / "_fonts")
    chains = fallback_chains(ex, {t["localized"]["fontAsset"] for t in texts.values()}, set(fonts))
    fonts = {n: {**f, "fallbacks": chains[n]} for n, f in fonts.items()}
    for path, comps in text_components(ex, ui_doc).items():
        texts[path] = {**texts[path], **text_extras(path, comps)}
    for n in sorted({t["localized"]["material"] for t in texts.values()}):   # the text shaders for text_shaders
        fa = next(t["localized"]["fontAsset"] for t in texts.values() if t["localized"]["material"] == n)
        ex.material(ex.key_object(textstyle.font_dir(fa) + n))
    keywords = text_shaders(ex, ui_dir, materials)
    cov = doc["glyphCoverage"]
    fonts_doc = {"format": FONTS_FORMAT, "language": language, "source": "game", "fonts": fonts,
                 "textures": textures, "materials": materials, "materialKeywords": keywords, "texts": texts,
                 "coverage": cov, "tmpSettings": doc["tmpSettings"], "lineBreaking": line_breaking(player)}
    lang = textstyle.language_fonts(player)
    _write(ui_dir, fonts_doc, language_doc(language, "game", fonts, texts, lang))
    shutil.rmtree(ui_dir / "_fonts", ignore_errors=True)
    return {"fonts": sorted(fonts), "characters": cov["characters"], "missing": len(cov["missing"]),
            "glyphs": sum(len(f["glyphs"]) for f in fonts.values()), "pages": len(textures)}
