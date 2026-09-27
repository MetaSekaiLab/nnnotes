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

Emoji: every text a Fwk.UI.UIText drives has LocalizeManager's emoji sprite asset (UIText.Awake), which draws the
characters its font assets lack and the <sprite name> tags the emoji search writes for emoji sequences. With open
fonts the sprite asset holds the sprites the episode's texts can draw (sprite_needs), each image drawn from the emoji
font (EmojiFont; none: empty glyphs) at the game sprite's size, and the open font assets of those texts leave out the
code points a sprite draws; with game fonts it is the game's asset with its sheet. Without a sprite to draw, fonts.json
has no sprite asset.

Same inputs (font file, episode, game data) and library versions give the same bytes; generated glyph cells are kept
in the process cache (disk layer when the build configures one) by font file, sizes and glyph.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
import unicodedata
from pathlib import Path

import numpy as np

from . import adv, advui, cache, jsonio, languages, master, textstyle, tmpfont
from . import shader as shader_mod
from .export import Exporter, _safe, packed_png, texture_png

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
UI_TEXT_CLASSES = ("UIText", "UIRubyText")                 # Fwk.UI.UIText and its subclass
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
def _ui_prefab_nodes(ex: Exporter, ui_doc: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """({path: node} of UIAdvWidget's prefab, {path: node} of the talk windows of ui.json under TalkView)."""
    widget = {n["path"]: n for n in ex.prefab(advui.WIDGET_KEY)["nodes"]}
    names = [rec["name"] for rec in ui_doc["nodes"] if rec["path"] == f"{advui.WINDOW_PARENT}/{rec['name']}"]
    return widget, {n["path"]: n for n in advui.window_nodes(ex, names)}


def ui_text_targets(nodes, root: str = "") -> set[str]:
    """The paths (`root` + the prefab path) of the texts the Fwk.UI.UIText components (and its subclass UIRubyText) of
    the prefab `nodes` drive (_targetText, on any node): UIText.Awake registers OnEmojiSpriteAssetLoaded with
    LocalizeManager, which sets the text's sprite asset (TMP_Text.spriteAsset) to LocalizeManager's emoji sprite
    asset, over any serialized one (the asset is loaded at the title, before any story). A UIText without a target
    text drives none."""
    out = set()
    for n in nodes:
        for c in n["components"]:
            if c.get("class") in UI_TEXT_CLASSES:
                target = (c.get("_targetText") or {}).get("gameObject")
                if target is not None:
                    out.add(root + target)
    return out


def story_ui_text_targets(ex: Exporter, ui_doc: dict) -> set[str]:
    """ui_text_targets of UIAdvWidget and of the talk windows of ui.json (paths under TalkView)."""
    names = [rec["name"] for rec in ui_doc["nodes"] if rec["path"] == f"{advui.WINDOW_PARENT}/{rec['name']}"]
    out = ui_text_targets(ex.prefab(advui.WIDGET_KEY)["nodes"])
    for name in names:
        out |= ui_text_targets(ex.prefab(advui.window_key(ex.cat, name))["nodes"], f"{advui.WINDOW_PARENT}/")
    return out


def sprite_binding(path: str, node: list[dict], driven: bool, sprite: str | None) -> dict:
    """The sprite settings of a text binding: for a text a UIText drives (`driven`), `spriteAsset` = `sprite` (the
    emoji sprite asset's name in fonts.json) and the text's serialized `m_tintAllSprites`; {} for any other text, which
    keeps its serialized sprite asset (only none is implemented)."""
    comps = [c for c in node if c.get("class") in advui.TMP_CLASSES]
    if len(comps) != 1:
        raise RuntimeError(f"{path}: {len(comps)} text components")
    c = comps[0]
    if not driven:
        if c.get("m_spriteAsset"):
            raise NotImplementedError(f"{path}: a text with its own sprite asset")
        return {}
    if sprite is None:
        raise RuntimeError(f"{path}: a UIText text without the emoji sprite asset")
    return {"spriteAsset": sprite, "m_tintAllSprites": c["m_tintAllSprites"]}


def text_components(ex: Exporter, ui_doc: dict) -> dict[str, list[dict]]:
    """{node path: the node's components} of every text node of the story UI (ui.json nodes with a text record), from
    the prefabs advui assembles (UIAdvWidget with the talk windows of ui.json, the children of TalkView, under it)."""
    widget, window = _ui_prefab_nodes(ex, ui_doc)
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


def _binding(path: str, node: list[dict], swap: dict, lang: dict, mode: int, driven: bool = False,
             sprite: str | None = None) -> dict:
    """The text binding of a text node (its components `node`): the serialized TextMeshPro fields of its text
    component, as advui writes them with fonts="game", the settings of its other text components (text_extras),
    `localized` = textstyle.localize_text (LocalizeText) in the language `mode` and its sprite settings
    (sprite_binding: `driven` by a UIText, `sprite` the emoji sprite asset's name)."""
    comps = [c for c in node if c.get("class") in advui.TMP_CLASSES]
    if len(comps) != 1:
        raise RuntimeError(f"{path}: {len(comps)} text components")
    c = comps[0]
    t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
    t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
              "material": c["m_sharedMaterial"]["material"]})
    t.update(text_extras(path, node))
    t["localized"] = textstyle.localize_text(path, t["fontAsset"], t["material"], swap, lang, mode)
    t.update(sprite_binding(path, node, driven, sprite))
    return t


def text_bindings(ex: Exporter, ui_doc: dict, lang: dict, mode: int, sprite: str | None = None) -> dict[str, dict]:
    """{node path: text binding (_binding)} of every text node of the story UI (text_components); `sprite` = the
    emoji sprite asset's name for the texts a UIText drives (story_ui_text_targets)."""
    swap = textstyle.font_swap(lang, mode)
    driven = story_ui_text_targets(ex, ui_doc)
    return {path: _binding(path, node, swap, lang, mode, path in driven, sprite)
            for path, node in text_components(ex, ui_doc).items()}


def dialog_bindings(ex: Exporter, ui_doc: dict, lang: dict, mode: int,
                    sprite: str | None = None) -> dict[str, dict[str, dict]]:
    """{dialog: {node path: text binding (_binding)}} of the text nodes of the dialogs of ui.json (`dialogs`, each
    from its prefab `key`); `sprite` as text_bindings."""
    swap = textstyle.font_swap(lang, mode)
    out: dict[str, dict[str, dict]] = {}
    for name, d in sorted((ui_doc.get("dialogs") or {}).items()):
        nodes = ex.prefab(d["key"])["nodes"]
        prefab, driven = {n["path"]: n["components"] for n in nodes}, ui_text_targets(nodes)
        for rec in d["nodes"]:
            if "textStyle" in rec:
                out.setdefault(name, {})[rec["path"]] = _binding(rec["path"], prefab[rec["path"]], swap, lang, mode,
                                                                 rec["path"] in driven, sprite)
    return out


def chat_bindings(ex: Exporter, episode: dict, lang: dict, mode: int,
                  sprite: str | None = None) -> dict[str, dict[str, dict]]:
    """{chat window: {node path: text binding}} of the TMP texts of the episode's chat windows (advui.chat_text_nodes):
    the fields text_bindings gives, `localized` from advui.chat_localize for a text with LocalizeText enabled, else
    the serialized font asset, material and line spacing; `sprite` as text_bindings."""
    out: dict[str, dict[str, dict]] = {}
    nodes, _ = advui.chat_text_nodes(ex, episode)
    driven = set().union(*(ui_text_targets(ex.prefab(address)["nodes"])
                           for address in advui.chat_windows(episode).values()))
    for window, node, c, loc in nodes:
        t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
        t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
                  "material": c["m_sharedMaterial"]["material"]})
        t.update(text_extras(node["path"], node["components"]))
        if loc and loc["m_Enabled"] and loc["_localizeEnabled"]:
            t["localized"] = advui.chat_localize(t["fontAsset"], t["material"], lang, mode)
        else:
            t["localized"] = {"fontAsset": t["fontAsset"], "material": t["material"], "lineSpacing": c["m_lineSpacing"]}
        t.update(sprite_binding(node["path"], node["components"], node["path"] in driven, sprite))
        out.setdefault(window, {})[node["path"]] = t
    return out


# the IAdvFrameTextReceiver implementations (AdvSystem.Asset.UI): a frame with one gets its texts at run time
FRAME_TEXT_RECEIVERS = ("AdvSlanderCommentFrame",)
FRAME_PREFIX = adv.RESOURCE_PREFIX["Frame"][1]


def frame_prefabs(ex: Exporter, episode: dict) -> dict[str, list[dict]]:
    """{frame name (its address without the Frame prefix, the key of story/frames.json): its prefab node list} of the
    episode's Frame resources in the catalog."""
    return {r["address"][len(FRAME_PREFIX):]: ex.prefab(r["address"])["nodes"] for r in episode["resources"]
            if r["kind"] == "frame" and r.get("present", True)}


def frame_bindings(frames: dict[str, list[dict]], lang: dict, mode: int,
                   sprite: str | None = None) -> dict[str, dict[str, dict]]:
    """{frame name: {node path: text binding}} of the TMP texts of the episode's frames (`frames`: frame_prefabs), as
    chat_bindings: `localized` from LocalizeText (advui.chat_localize) when it is enabled, else the serialized font
    asset, material and line spacing; `sprite` as text_bindings (the texts a UIText drives)."""
    out: dict[str, dict[str, dict]] = {}
    for name, nodes in frames.items():
        driven = ui_text_targets(nodes)
        for node in nodes:
            comps = [c for c in node["components"] if c.get("class") in advui.TMP_CLASSES]
            if not comps:
                continue
            c = comps[0]
            loc = next((x for x in node["components"] if x.get("class") == "LocalizeText"), None)
            t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
            t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
                      "material": c["m_sharedMaterial"]["material"]})
            t.update(text_extras(node["path"], node["components"]))
            if loc and loc["m_Enabled"] and loc["_localizeEnabled"]:
                t["localized"] = advui.chat_localize(t["fontAsset"], t["material"], lang, mode)
            else:
                t["localized"] = {"fontAsset": t["fontAsset"], "material": t["material"],
                                  "lineSpacing": c["m_lineSpacing"]}
            t.update(sprite_binding(node["path"], node["components"], node["path"] in driven, sprite))
            out.setdefault(name, {})[node["path"]] = t
    return out


def frame_texts(frames: dict[str, list[dict]], episode: dict, field: str) -> list[str]:
    """The texts the episode's frames (`frames`: frame_prefabs) show: the serialized texts of their text nodes, and
    the texts AdvFrameCommand.SetFrameTexts gives the frames with an IAdvFrameTextReceiver (FRAME_TEXT_RECEIVERS): per
    Frame row with TargetTextIDs, the localized text of each id (the episode's text table in the text field `field`;
    an unknown id gives ""), in the form AdvSlanderCommentTextHelper shows it (NFC; "@" before the user id, line
    feeds), plus "@" and a line feed when there is any."""
    receivers = {name for name, nodes in frames.items()
                 if any(c.get("class") in FRAME_TEXT_RECEIVERS for n in nodes for c in n["components"])}
    serialized = [c["m_text"] for nodes in frames.values() for n in nodes for c in n["components"]
                  if c.get("class") in advui.TMP_CLASSES and isinstance(c.get("m_text"), str) and c["m_text"]]
    out = []
    for c in episode["commands"]:
        if c["cmd"] != "Frame" or c.get("IgnoreData") or not c.get("TargetTextIDs"):
            continue
        if (c.get("TargetAssetName") or "").strip() not in receivers:
            continue
        for i in c["TargetTextIDs"]:
            row = episode["text"].get(i)
            if row is not None and isinstance(row.get(field), str):
                out.append(unicodedata.normalize("NFC", row[field]))
    return serialized + (out + ["@\n"] if out else out)


def shown_texts(episode: dict, ui_doc: dict, master_dir: Path | None, field: str) -> list[str]:
    """The texts the episode shows in the text field `field`: every text of its text table (lines, speaker names,
    frame and choice texts), its title, the static labels of the UI (the MasterText rows of the text keys of the text
    nodes and of the dialogs' text nodes, the `masterIdTexts` of ui.json) and of the chat windows
    (`chatStatusTexts`), the serialized texts of the chat windows' text nodes (`chatTexts`, shown until the player
    sets them) and the texts the chat windows format at run time (advui.chat_runtime_texts), markup included."""
    texts = [row.get(field) for row in episode["text"].values()]
    texts.append((episode.get("title") or {}).get(field))
    texts += [t["text"] for t in (ui_doc.get("masterIdTexts") or {}).values()]
    texts += list((ui_doc.get("chatStatusTexts") or {}).values()) + advui.chat_runtime_texts(episode)
    texts += [rec["textStyle"].get("text") for w in (ui_doc.get("chatTexts") or {}).values() for rec in w.values()]
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
    return [s for s in texts if isinstance(s, str)]


def shown_characters(episode: dict, ui_doc: dict, master_dir: Path | None, field: str) -> list[int]:
    """The code points of shown_texts, sorted."""
    return sorted({ord(ch) for s in shown_texts(episode, ui_doc, master_dir, field) for ch in s})


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


def open_asset(font: FontFile, name: str, chars, game: dict, text_materials: dict[str, dict],
               underline: bool = True) -> dict:
    """An open font asset of `font` named `name` for the code points `chars`, in place of the game font asset
    `game` = {pointSize, padding, renderMode, style {STYLE_KEYS}, material (its default material record)} whose texts
    use `text_materials` ({name: game material record}); with `underline` (the game asset itself has U+005F: its
    underline and highlight glyph) also U+005F. -> {font (the asset record), textures {page: descriptor},
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
    for u in sorted(set(chars) | set(tmpfont.TMP_SYNTHESIZED) | ({UNDERLINE_CHARACTER} if underline else set())):
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
               master_dir: Path | None = None, emoji: EmojiFont | None = None) -> dict:
    """ui/fonts.json, ui/fonts/*.png and ui/languages.json of `language` from the font file `font` and the emoji font
    `emoji` (module docstring; without one the sprites keep their metrics and have no texels), next to the
    ui/ui.json advui.extract wrote for that language in `ui_dir`; the text shaders go to ui/shaders. Returns a
    summary."""
    tmpfont.require_extra()
    ui_dir = Path(ui_dir)
    ui_doc = json.loads((ui_dir / "ui.json").read_text(encoding="utf-8"))
    mode, field = languages.mode(language), languages.column(language)[1:]
    ex = _exporter(cat, player, ui_dir / "_fonts")
    lang = textstyle.language_fonts(player)
    sprite_name = emoji.asset_name if emoji is not None else NO_EMOJI_FONT
    texts = text_bindings(ex, ui_doc, lang, mode, sprite_name)
    dialogs = dialog_bindings(ex, ui_doc, lang, mode, sprite_name)
    chat = chat_bindings(ex, episode, lang, mode, sprite_name) if ui_doc.get("chatTexts") else {}
    frames = frame_prefabs(ex, episode)
    frame = frame_bindings(frames, lang, mode, sprite_name)
    shown = shown_texts(episode, ui_doc, master_dir, field) + frame_texts(frames, episode, field)
    chars = sorted({ord(ch) for s in shown for ch in s})

    bindings = list(texts.values()) + [t for group in (dialogs, chat, frame) for w in group.values()
                                       for t in w.values()]
    by_game: dict[str, list[dict]] = {}           # localized game font asset -> the bindings of its texts
    for t in bindings:
        by_game.setdefault(t["localized"]["fontAsset"], []).append(t)
    # the code points the sprite asset draws for the texts with it (sprite_drawn); the open font asset of those
    # texts leaves them out
    sprite_drawn: dict[str, set[int]] = {}
    rec = None
    if any("spriteAsset" in t for t in bindings):
        rec = advui.sprite_asset_record(ex)
        sprite_drawn = drawn_by_sprites(ex, rec, [t for t in bindings if "spriteAsset" in t], chars)
    # the game's missing glyph per game font asset (tmpfont.missing_glyph): the code points its texts draw as the
    # substitute; the open font asset holds the substitute instead of them
    fs = tmpfont.FontSet(ex)
    missing_char = tmpfont.missing_glyph_character(player)
    fonts, textures, materials, missing, substituted = {}, {}, {}, set(), set()
    for game_name in sorted(by_game):
        gfont = fs.by_key(advui.font_key(cat, game_name, game_name))
        tmpfont.check_glyph_variants(fs, gfont, shown)
        mg = tmpfont.missing_glyph(fs, gfont, chars, missing_char)
        gone = set(mg["characters"]) if mg else set()
        substituted |= gone
        # GetUnderlineSpecialCharacter: '_' of the game asset itself (no fallback); a '_' its fallback draws for the
        # text would be the underline glyph too in one open asset
        underline = tmpfont.underline_character(fs, gfont)
        if not underline and UNDERLINE_CHARACTER in chars and UNDERLINE_CHARACTER not in gone:
            raise NotImplementedError(f"{game_name}: U+005F from a fallback without the asset's own underline glyph")
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
        own = sorted({u for u in chars if u not in gone and u not in sprite_drawn.get(game_name, ())}
                     | ({mg["unicode"]} if mg else set()))
        a = open_asset(font, name, own, game, text_mats, underline)
        if mg:
            a["font"]["missingGlyph"] = mg
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
    coverage = {"characters": len(chars), "missing": [chr(u) for u in sorted(missing)]}
    if substituted:
        coverage["missingGlyph"] = [chr(u) for u in sorted(substituted)]
    sprites = None
    indices = sprite_needs(rec, shown, set().union(*sprite_drawn.values())) if rec is not None else []
    if not indices:                                 # no sprite to draw: the data holds no sprite asset
        for t in bindings:
            t.pop("spriteAsset", None)
            t.pop("m_tintAllSprites", None)
    else:
        sprites = open_sprite_asset(emoji, rec, indices, sprite_name)
        for pname, px in sprites["pages"]:
            (ui_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)
            (ui_dir / sprites["textures"][pname]["texture"]).write_bytes(packed_png(px))
        textures.update(sprites["textures"])
        materials[sprites["material"]["material"]] = sprites["material"]
        coverage["sprites"] = {"characters": len(sprites["asset"]["characters"]), "missing": sprites["missing"]}
    keywords = text_shaders(ex, ui_dir, materials)
    fonts_doc = {"format": FONTS_FORMAT, "language": language, "source": "open", "fonts": fonts,
                 "textures": textures, "materials": materials, "materialKeywords": keywords, "texts": texts,
                 "coverage": coverage, "lineBreaking": line_breaking(player)}
    if sprites is not None:
        fonts_doc["spriteAssets"] = {sprite_name: sprites["asset"]}
        fonts_doc["emojiSpriteAsset"] = sprite_name
    if dialogs:
        fonts_doc["dialogTexts"] = dialogs
    if chat:
        fonts_doc["chatTexts"] = chat
    if frame:
        fonts_doc["frameTexts"] = frame
    _write(ui_dir, fonts_doc, language_doc(language, "open", fonts, texts, lang))
    shutil.rmtree(ui_dir / "_fonts", ignore_errors=True)
    return {"fonts": sorted(fonts), "characters": len(chars), "missing": len(missing),
            "glyphs": sum(len(f["glyphs"]) for f in fonts.values()), "pages": len(textures),
            "sprites": len(sprites["asset"]["characters"]) if sprites else 0,
            "missingSprites": len(sprites["missing"]) if sprites else 0}


# ---------------------------------------------------------------- emoji sprites (open)
EMOJI_VS16 = 0xFE0F
SPRITE_MARGIN = 1            # transparent texels around each sprite cell (a bilinear quad edge samples half a texel)
TMP_MISSING_SPRITE_UNICODE = 0            # TMP_Settings.missingCharacterSpriteUnicode of the game's settings
NO_EMOJI_FONT = "Emoji Sprites"           # the open sprite asset's name without an emoji font
SPRITE_SUBSET = ("sprites of the emoji sequences in the episode's texts in this language (TMP_EmojiSearchEngine) "
                 "and of the characters they show that the game's font assets lack")


def _ligatures(tt) -> dict[tuple, str]:
    """{(glyph, glyph, ...): ligature glyph} of the GSUB ligature substitutions of a fontTools font (extension
    lookups included)."""
    out: dict[tuple, str] = {}
    if "GSUB" not in tt:
        return out
    for lookup in tt["GSUB"].table.LookupList.Lookup:
        for st in lookup.SubTable:
            st = st.ExtSubTable if lookup.LookupType == 7 else st
            for first, ligs in (getattr(st, "ligatures", None) or {}).items():
                for lig in ligs:
                    out.setdefault((first, *lig.Component), lig.LigGlyph)
    return out


class EmojiFont:
    """A colour emoji font of one's own with PNG bitmap glyphs (CBDT / CBLC or sbix; e.g. Noto Color Emoji, SIL Open
    Font License 1.1): the image of an emoji, a code point or a sequence resolved through the font's ligature
    substitutions (with U+FE0F dropped when the full sequence has no ligature), from the largest strike."""

    def __init__(self, path, face_index: int = 0):
        import io

        from fontTools.ttLib import TTFont
        self.file = FontFile(path, face_index)
        self.tt = TTFont(io.BytesIO(self.file.data), fontNumber=face_index)
        self.cmap = self.tt.getBestCmap() or {}
        self.ligatures = _ligatures(self.tt)
        if "CBDT" in self.tt:
            strikes = self.tt["CBLC"].strikes
            i = max(range(len(strikes)), key=lambda k: strikes[k].bitmapSizeTable.ppemY)
            self.ppem = strikes[i].bitmapSizeTable.ppemY
            self._glyphs = self.tt["CBDT"].strikeData[i]
            self._png = lambda g: getattr(g, "imageData", None)
        elif "sbix" in self.tt:
            self.ppem = max(self.tt["sbix"].strikes)
            self._glyphs = self.tt["sbix"].strikes[self.ppem].glyphs
            self._png = lambda g: g.imageData if g.graphicType == "png " else None
        else:
            raise ValueError(f"{self.file.path.name}: no CBDT or sbix bitmap table (a colour bitmap emoji font)")

    def glyph(self, seq: str) -> str | None:
        """The glyph name of the emoji `seq` (a string of one or more code points), None when the font lacks it."""
        for s in (seq, "".join(c for c in seq if ord(c) != EMOJI_VS16)):
            names = [self.cmap.get(ord(c)) for c in s]
            if not names or None in names:
                continue
            if len(names) == 1:
                return names[0]
            lig = self.ligatures.get(tuple(names))
            if lig:
                return lig
        return None

    def image(self, seq: str, width: int, height: int):
        """RGBA array (height x width, bottom row first, straight alpha) of the emoji `seq`: its bitmap centred in a
        square of the larger side, resampled (Lanczos on premultiplied alpha, Pillow) to width x height; None when
        the font lacks it."""
        import io

        from PIL import Image
        name = self.glyph(seq)
        g = self._glyphs.get(name) if name else None
        data = self._png(g) if g is not None else None
        if not data:
            return None
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2))
        out = sq.convert("RGBa").resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
        return np.asarray(out, np.uint8)[::-1].copy()

    @property
    def asset_name(self) -> str:
        return f"{self.file.family} {self.file.style} Sprites".replace("  ", " ")

    def source(self) -> dict:
        return self.file.source({"strikePpem": self.ppem, "fit": "bitmap centred in a square, Lanczos resampling"})


def sprite_sequence(name: str, unicode: int) -> str:
    """The emoji a sprite of the game's emoji sprite asset stands for: its sequence (advui.emoji_sequence_key of its
    name), else its code point."""
    return advui.emoji_sequence_key(name, unicode) or chr(unicode)


def drawn_by_sprites(ex: Exporter, rec: dict, bindings: list[dict], chars) -> dict[str, set[int]]:
    """{game font asset: the code points of `chars` the sprite asset record `rec` draws for the texts of `bindings`
    that use that font asset}: the ones the font asset lacks with its fallbacks (tmpfont.FontSet lookup, as
    TMP_Text.GetTextElement before the sprite asset) and a sprite character of `rec` has."""
    fs = tmpfont.FontSet(ex)
    have = {c["unicode"] for c in rec["characters"] if str(c["glyph"]) in rec["glyphs"]}
    out = {}
    for game_name in sorted({t["localized"]["fontAsset"] for t in bindings}):
        f = fs.by_key(advui.font_key(ex.cat, game_name, game_name))
        out[game_name] = {u for u in chars if u in have and fs.lookup(f, u) is None}
    return out


def sprite_needs(rec: dict, texts: list[str], drawn: set[int]) -> list[int]:
    """The sprite character indices of the sprite asset record `rec` (advui.sprite_asset_record) that `texts` can
    draw: the sprites of the emoji sequences TMP_EmojiSearchEngine finds in them (the <sprite name> tag's lookup: the
    first character whose name has the value's hash, else the missing character sprite) and those of the code
    points `drawn` (the first character with the code point). Characters whose glyph the asset lacks are left out,
    as TMP_SpriteAsset.UpdateLookupTables does."""
    by_hash: dict[int, dict] = {}
    by_unicode: dict[int, dict] = {}
    for c in rec["characters"]:
        if str(c["glyph"]) not in rec["glyphs"]:
            continue
        by_hash.setdefault(advui.tmp_hash(c["name"]), c)
        if c["unicode"] != 0xFFFE:
            by_unicode.setdefault(c["unicode"], c)
    table, fast = advui.emoji_sequence_table(rec["sequences"])
    out = {by_unicode[u]["index"] for u in drawn if u in by_unicode}
    for s in texts:
        for name in advui.emoji_sequence_names(table, fast, s):
            c = by_hash.get(advui.tmp_hash(name)) or by_unicode.get(TMP_MISSING_SPRITE_UNICODE)
            if c is not None:
                out.add(c["index"])
    return sorted(out)


def _sheet_material(m: dict, name: str, page: str | None, width: int, height: int) -> dict:
    """A game sprite material for an open sprite asset: named `name`, sampling `page` (width x height; None: no
    texture) as _MainTex; every other value as in the game."""
    m = copy.deepcopy(m)
    _material_textures(m)
    m["material"] = name
    tex = m["textures"].get("_MainTex") or {}
    m["textures"]["_MainTex"] = {"texture": {"name": page, "width": width, "height": height,
                                             "format": (tex.get("texture") or {}).get("format")} if page else None,
                                 "scale": tex.get("scale", {"x": 1.0, "y": 1.0}),
                                 "offset": tex.get("offset", {"x": 0.0, "y": 0.0})}
    return m


def open_sprite_asset(emoji: EmojiFont | None, game: dict, indices: list[int], name: str) -> dict:
    """An open sprite asset `name` in place of the game's sprite asset record `game` (advui.sprite_asset_record)
    holding the sprite characters `indices` (character table indices): the game's face info, character entries,
    glyph metrics and sequence list; each glyph's image drawn from `emoji` at the game glyph rect's size, shelf-packed
    (SPRITE_MARGIN transparent texels around each) into one page. A glyph without an image (no emoji font, or one
    that lacks the emoji) keeps its metrics with an empty rect and no page. -> {asset, textures, pages, material,
    missing (the names of the sprites without an image)}. A character whose glyph the record lacks is left out."""
    chars = [c for c in (game["characters"][i] for i in sorted(set(indices))) if str(c["glyph"]) in game["glyphs"]]
    glyph_ids = sorted({c["glyph"] for c in chars})
    images, missing, cells = {}, [], []
    for gi in glyph_ids:
        c = next(c for c in chars if c["glyph"] == gi)
        r = game["glyphs"][str(gi)]["rect"]
        w, h = r["m_Width"], r["m_Height"]
        if w <= 0 or h <= 0:
            continue
        a = emoji.image(sprite_sequence(c["name"], c["unicode"]), w, h) if emoji is not None else None
        if a is None:
            missing.append(c["name"])
            continue
        images[gi] = a
        cells.append((gi, w + 2 * SPRITE_MARGIN, h + 2 * SPRITE_MARGIN))
    where, width, height = pack_cells(cells)
    if any(pg for pg, _, _ in where.values()):
        raise NotImplementedError(f"{name}: sprites on more than one page")
    page = f"sprite_{name}" if images else None
    arr = np.zeros((height, width, 4), np.uint8) if images else None
    glyphs = {}
    for gi in glyph_ids:
        g = game["glyphs"][str(gi)]
        rec = {"metrics": g["metrics"], "rect": {"m_X": 0, "m_Y": 0, "m_Width": 0, "m_Height": 0}, "scale": g["scale"],
               "atlasIndex": 0}
        if gi in images:
            a = images[gi]
            _, x, y = where[gi]
            x, y = x + SPRITE_MARGIN, y + SPRITE_MARGIN
            arr[y:y + a.shape[0], x:x + a.shape[1]] = a
            rec["rect"] = {"m_X": x, "m_Y": y, "m_Width": a.shape[1], "m_Height": a.shape[0]}
            rec["packed"] = {"texture": page, "dx": 0, "dy": 0}
        glyphs[str(gi)] = rec
    textures = ({page: {"texture": f"{PAGES_DIR}/{_safe(page)}.png", "name": page, "width": width, "height": height,
                        "mipCount": 1, "settings": dict(PAGE_SETTINGS)}} if images else {})
    material = _sheet_material(game["material"], f"{name} Material", page, width, height)
    asset = {"faceInfo": game["faceInfo"], "characters": chars, "glyphs": glyphs, "sequences": game["sequences"],
             "material": material["material"], "source": emoji.source() if emoji is not None else None,
             "subset": SPRITE_SUBSET}
    return {"asset": asset, "textures": textures, "pages": [(page, arr)] if images else [], "material": material,
            "missing": missing}


# ---------------------------------------------------------------- game fonts
def game_sprite_asset(ex: Exporter, ui_dir: Path, rec: dict) -> dict:
    """LocalizeManager's emoji sprite asset from the game data (`rec`, advui.sprite_asset_record): every character and
    glyph, the sprite sheet as one page (ui/fonts/, the glyph rects unchanged) and its material sampling it.
    -> {asset_name, asset, textures, material}."""
    sh = rec["sheet"]
    if sh["mipCount"] != 1:
        raise NotImplementedError(f"{rec['name']}: sprite sheet with {sh['mipCount']} mip levels")
    o = ex.key_object(advui.EMOJI_SPRITE_ASSET_KEY)
    sheet = ex.deref(o, o.read_typetree()["spriteSheet"])
    page = f"sprite_{rec['name']}"
    dst = f"{PAGES_DIR}/{_safe(page)}.png"
    (Path(ui_dir) / PAGES_DIR).mkdir(parents=True, exist_ok=True)
    (Path(ui_dir) / dst).write_bytes(texture_png(sheet.read()))
    glyphs = {k: {**g, **({"packed": {"texture": page, "dx": 0, "dy": 0}}
                          if g["rect"]["m_Width"] > 0 and g["rect"]["m_Height"] > 0 else {})}
              for k, g in rec["glyphs"].items()}
    material = _sheet_material(rec["material"], rec["material"]["material"], page, sh["width"], sh["height"])
    asset = {"faceInfo": rec["faceInfo"], "characters": rec["characters"], "glyphs": glyphs,
             "sequences": rec["sequences"], "material": material["material"]}
    textures = {page: {"texture": dst, "name": sh["name"], "width": sh["width"], "height": sh["height"],
                       "mipCount": 1, "settings": sh["settings"]}}
    return {"asset_name": rec["name"], "asset": asset, "textures": textures, "material": material}


def game_fonts(cat, player, game_ui_dir: Path, ui_dir: Path, language: str, episode: dict | None = None,
               master_dir: Path | None = None) -> dict:
    """ui/fonts.json, ui/fonts/*.png and ui/languages.json of `language` from the ui/ directory advui.extract wrote
    with fonts="game" for that language (`game_ui_dir`): its font records (each font's `fallbacks` reduced to the
    exported assets in search order, fallback_chains), glyph pages, text materials, keyword sets, text records, glyph
    coverage and TMP settings, next to the fonts-open ui/ui.json in `ui_dir`; the text shaders go to ui/shaders as
    with open fonts. With the `episode` (and the master data for its UI labels), the texts a UIText drives get the
    game's emoji sprite asset when they draw a sprite (sprite_needs; its code points leave coverage.missing). Returns a
    summary."""
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
    driven = story_ui_text_targets(ex, ui_doc) & set(texts)
    sprites, drawn = None, set()
    if episode is not None and driven:
        rec = advui.sprite_asset_record(ex)
        shown = shown_texts(episode, ui_doc, master_dir, languages.column(language)[1:])
        chars = sorted({ord(ch) for s in shown for ch in s})
        drawn = set().union(*drawn_by_sprites(ex, rec, [texts[p] for p in sorted(driven)], chars).values())
        if sprite_needs(rec, shown, drawn):
            sprites = game_sprite_asset(ex, ui_dir, rec)
    if sprites is None:
        driven, drawn = set(), set()
    sprite_name = sprites["asset_name"] if sprites else None
    for path, comps in text_components(ex, ui_doc).items():
        texts[path] = {**texts[path], **text_extras(path, comps),
                       **sprite_binding(path, comps, path in driven, sprite_name)}
    if sprites is not None:
        textures.update(sprites["textures"])
        materials[sprites["material"]["material"]] = sprites["material"]
    for n in sorted({t["localized"]["material"] for t in texts.values()}):   # the text shaders for text_shaders
        fa = next(t["localized"]["fontAsset"] for t in texts.values() if t["localized"]["material"] == n)
        ex.material(ex.key_object(textstyle.font_dir(fa) + n))
    keywords = text_shaders(ex, ui_dir, materials)
    cov = doc["glyphCoverage"]
    if sprites is not None:
        cov = {**cov, "missing": [c for c in cov["missing"] if ord(c) not in drawn],
               "sprites": {"characters": len(sprites["asset"]["characters"]), "missing": []}}
    fonts_doc = {"format": FONTS_FORMAT, "language": language, "source": "game", "fonts": fonts,
                 "textures": textures, "materials": materials, "materialKeywords": keywords, "texts": texts,
                 "coverage": cov, "tmpSettings": doc["tmpSettings"], "lineBreaking": line_breaking(player)}
    if sprites is not None:
        fonts_doc["spriteAssets"] = {sprite_name: sprites["asset"]}
        fonts_doc["emojiSpriteAsset"] = sprite_name
    lang = textstyle.language_fonts(player)
    _write(ui_dir, fonts_doc, language_doc(language, "game", fonts, texts, lang))
    shutil.rmtree(ui_dir / "_fonts", ignore_errors=True)
    return {"fonts": sorted(fonts), "characters": cov["characters"], "missing": len(cov["missing"]),
            "glyphs": sum(len(f["glyphs"]) for f in fonts.values()), "pages": len(textures)}
