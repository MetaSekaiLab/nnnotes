"""ADV story UI -> <story>/ui/ (ui.json, textures/, shaders/).

Everything a player needs to draw the ADV front canvas (talk window, speaker
plate, location caption, episode title, rule transition cover, menu entry
button, curtains) and the letterbox bands, taken from the game data:

  nodes        RectTransform hierarchy of the drawn parts of UIAdvWidget's
               FrontCanvas with the default talk window (UIDefaultTalkWindow)
               attached under UIContainer/TalkView (AdvTalkView.SetWindow), and
               the AdvLetterBoxCanvas bands; per node the uGUI components the
               runtime needs (Image, CanvasGroup, UIGradientImage, TMP text
               settings, layout groups, DOTweenSequence durations)
  sprites      sprite geometry (rect, border, pivot, pixels per unit, texture
               rect/offset) remapped into packed textures
  fonts        TMP font assets reached from the localized fonts (face info,
               style values, fallback chain) with the character and glyph
               tables reduced to the characters the episode shows; characters
               a dynamic font asset would add at runtime (in the source font
               file, not baked) are generated here the way FontEngine renders
               them (see tmpfont.RuntimeGlyphs) and validated against the baked ones
  materials    TMP materials (localized "<font> - <type>"), fallback font
               materials, UI-Transition, the built-in Default UI Material
  clips        Animator clips (NextIndicator, AutoNext, AdvLocation/AdvTitle Play)
  transitions  RuleTransitionSettings of every transition the episode or the
               player settings reference
  shaders      UI/Default, TextMeshPro/Mobile/Distance Field, UI/Transition

Textures: sprite atlases and SDF font atlases are large; only the texel blocks
a draw can sample are kept (export.Exporter deferred-texture mode with
export.TexelPacker). Each block is the sprite's texture rect or a glyph rect
grown by the widest footprint the shader samples, plus one texel for bilinear
filtering, read with the source wrap mode, and copied into a packed texture.
UVs are remapped by an integer translation, so sampling returns the same texel
values as the full atlas.
"""
from __future__ import annotations

import re
from pathlib import Path

from .catalog import Catalog
from .export import Exporter, TexelPacker, _safe
from .jsonio import write_json
from .player import PlayerData
from . import tmpfont
from .tmpfont import LANGUAGE_FIELD, LANGUAGE_LINE_SPACING, LANGUAGE_MODE

WIDGET_KEY = "EmbUI/Prefab/UIAdvWidget"
WINDOW_KEY = "EmbUI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow"
WINDOW_PARENT = "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/TalkView"
LETTERBOX_KEY = "Textures/letterbox-image"
TRANSITION_PREFIX = "Adv/Transition/"
PLAYER_SETTINGS_KEY = "EmbCommon/Adv/Settings/AdvPlayerSettings"
MASTER_ID_SETTINGS_KEY = "EmbCommon/Adv/Settings/AdvMasterIdSettings"
CLIP_KEYS = {
    "NextIndicator/Loop": "EmbUI/Animations/NextIndicator/Loop",
    "AutoNext/Loop": "EmbUI/Animations/Auto/Loop",
    "AdvLocation/Play": "EmbUI/Animations/AdvLocation/Play",
    "AdvTitle/Play": "EmbUI/Animations/AdvTitle/Play",
}
CONTROLLER_KEYS = {
    "NextIndicator": "EmbUI/Animations/NextIndicator/NextIndicator",
    "AutoNext": "EmbUI/Animations/Auto/AutoNext",
    "AdvLocation": "EmbUI/Animations/AdvLocation/AdvLocation",
    "AdvTitle": "EmbUI/Animations/AdvTitle/AdvTitle",
}
# Parts of the widget the runtime draws (and their ancestors). Hidden parts
# that nothing in the episode shows (backlog, choices, subtitles, menu panel,
# video controls, flash, stills, video) are not exported.
DRAWN = (
    "UIAdvWidget/FrontCanvas/RuleTransition",
    "UIAdvWidget/FrontCanvas/LocationVIew",
    "UIAdvWidget/FrontCanvas/RightCurtain",
    "UIAdvWidget/FrontCanvas/LeftCurtain",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/TalkView",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/MenuView/MenuEntryButton",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/TitleView",
    "UIAdvWidget/AdvLetterBoxCanvas",
)

TMP_CLASSES = ("TextMeshProUGUI", "RubyTextMeshProUGUI", "RubyEmojiTextMeshProUGUI")
TMP_FIELDS = (
    "m_fontSize", "m_fontSizeBase", "m_fontStyle", "m_fontWeight", "m_HorizontalAlignment", "m_VerticalAlignment",
    "m_characterSpacing", "m_wordSpacing", "m_lineSpacing", "m_paragraphSpacing", "m_characterHorizontalScale",
    "m_TextWrappingMode", "m_overflowMode", "m_enableAutoSizing", "m_fontSizeMin", "m_fontSizeMax",
    "m_enableKerning", "m_ActiveFontFeatures", "m_enableExtraPadding", "m_isRichText", "m_parseCtrlCharacters",
    "m_isOrthographic", "m_overrideHtmlColors", "m_margin", "m_fontColor", "m_Color", "m_colorMode",
    "m_enableVertexGradient", "m_TextStyleHashCode", "m_useMaxVisibleDescender", "m_horizontalMapping",
    "m_verticalMapping", "m_charWidthMaxAdj", "m_lineSpacingMax", "m_isRightToLeft", "m_text",
)
IMAGE_FIELDS = ("m_Enabled", "m_Color", "m_Type", "m_PreserveAspect", "m_FillCenter", "m_FillMethod",
                "m_FillAmount", "m_UseSpriteMesh", "m_PixelsPerUnitMultiplier", "m_Maskable")


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^<>]*>", "", s)


def _layout_components(node: dict) -> dict:
    out: dict = {}
    for c in node["components"]:
        cls = c.get("class")
        t = c["type"]
        if t == "CanvasGroup":
            out["canvasGroup"] = {k: c[k] for k in ("m_Enabled", "m_Alpha", "m_IgnoreParentGroups")}
        elif t == "Canvas":
            out["canvas"] = {k: c[k] for k in ("m_Enabled", "m_RenderMode", "m_SortingOrder",
                                               "m_VertexColorAlwaysGammaSpace", "m_AdditionalShaderChannelsFlag",
                                               "m_OverrideSorting", "m_PixelPerfect")}
        elif t == "Animator":
            out["animator"] = {"controller": (c["m_Controller"] or {}).get("name"),
                               "enabled": c["m_Enabled"], "updateMode": c["m_UpdateMode"],
                               "keepStateOnDisable": c["m_KeepAnimatorStateOnDisable"]}
        elif cls == "CanvasScaler":
            out["canvasScaler"] = {k: c[k] for k in ("m_Enabled", "m_UiScaleMode", "m_ReferencePixelsPerUnit",
                                                     "m_ScaleFactor", "m_ReferenceResolution", "m_ScreenMatchMode",
                                                     "m_MatchWidthOrHeight")}
        elif cls == "Image":
            img = {k: c[k] for k in IMAGE_FIELDS}
            img["sprite"] = c["m_Sprite"]["name"] if c["m_Sprite"] else None
            img["spriteRef"] = c["m_Sprite"]["spriteRef"] if c["m_Sprite"] else None
            img["material"] = c["m_Material"]["material"] if c["m_Material"] else None
            out["image"] = img
        elif cls == "UIGradientImage":
            out["gradient"] = {"enabled": c["m_Enabled"], "gradient": c["_gradient"], "angleDeg": c["_angleDeg"],
                               "splitAtKeysWhenAxisAligned": c["_splitAtKeysWhenAxisAligned"],
                               "blendMode": c["_blendMode"]}
        elif cls in TMP_CLASSES:
            txt = {k: c[k] for k in TMP_FIELDS if k in c}
            txt["class"] = cls
            txt["enabled"] = c["m_Enabled"]
            txt["fontAsset"] = c["m_fontAsset"]["name"]
            txt["material"] = c["m_sharedMaterial"]["material"]
            out["text"] = txt
        elif cls == "LocalizeText":
            out["localizeText"] = {"enabled": c["m_Enabled"], "localizeEnabled": c["_localizeEnabled"]}
        elif cls in ("HorizontalLayoutGroup", "VerticalLayoutGroup"):
            out["layoutGroup"] = {"class": cls, **{k: c[k] for k in c if k.startswith("m_")}}
        elif cls == "ContentSizeFitter":
            out["contentSizeFitter"] = {k: c[k] for k in ("m_Enabled", "m_HorizontalFit", "m_VerticalFit")}
        elif cls == "LayoutElement":
            out["layoutElement"] = {k: c[k] for k in c if k.startswith("m_")}
        elif cls == "DOTweenSequence":
            out["tweenSequence"] = {"list": [{k: e[k] for k in ("_commandType", "_duration") if k in e}
                                             for e in c["_list"]],
                                    "raw": {k: c[k] for k in c if k in ("updateType", "isSpeedBased")}}
        elif cls == "SimpleAnimationTrigger":
            out["animationTrigger"] = {"blendTime": c["_blendTime"]}
        elif cls == "UIAdvTalkWindow":
            out["talkWindow"] = {k: c[k] for k in ("_typingDelay", "_safeAreaTalkBackgroundExpansionFactor",
                                                   "_useBackdropFilter", "_backdropFilterColor", "_talkTextColor",
                                                   "_talkTextOutlineColor")}
        elif cls == "ButtonImageState":
            out["buttonImageState"] = {"normal": c["_normaledSprite"]["name"] if c["_normaledSprite"] else None}
        elif cls == "Outline":
            out["outline"] = {"enabled": c["m_Enabled"]}
    return out


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def extract(cat: Catalog, player: PlayerData, episode: dict, out_dir: Path) -> dict:
    ui_dir = Path(out_dir) / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    ex = Exporter(cat, ui_dir, player=player, textures="deferred",
                  stub_assets=("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet"))
    packer = TexelPacker(ui_dir)

    # -- prefabs -------------------------------------------------------------
    widget = ex.prefab(WIDGET_KEY)["nodes"]
    window = ex.prefab(WINDOW_KEY)["nodes"]
    paths = {n["path"] for n in widget}
    if WINDOW_PARENT not in paths:
        raise RuntimeError(f"{WINDOW_PARENT} not in {WIDGET_KEY}")

    def kept(p: str) -> bool:
        return any(p == d or p.startswith(d + "/") or d.startswith(p + "/") for d in DRAWN)

    nodes = []
    for n in widget:
        if n["path"] == "UIAdvWidget" or not kept(n["path"]):
            continue
        nodes.append(n)
        if n["path"] == WINDOW_PARENT:
            # AdvTalkView.SetWindow: parented under TalkView, localPosition 0, last sibling
            for w in window:
                nodes.append({**w, "path": f"{WINDOW_PARENT}/{w['path']}"})
    for d in DRAWN:
        if d not in {n["path"] for n in nodes}:
            raise RuntimeError(f"{d} missing from the prefabs")
    out_nodes = []
    for n in nodes:
        rec = {"path": n["path"], "name": n["name"], "active": n["active"],
               "localPosition": n["localPosition"], "localRotation": n["localRotation"],
               "localScale": n["localScale"], "rect": n.get("rect")}
        rec.update(_layout_components(n))
        out_nodes.append(rec)
    front = [n for n in widget if n["path"].startswith("UIAdvWidget/FrontCanvas/") and n["path"].count("/") == 2]
    front_order = [n["name"] for n in front]

    # -- localization: fonts of the language mode -----------------------------
    lang = tmpfont.language_fonts(player)
    font_swap = tmpfont.font_swap(lang)

    fonts = tmpfont.FontSet(ex)
    used_text_nodes = [n for n in out_nodes if "text" in n]
    primaries = {}
    for n in used_text_nodes:
        t = n["text"]
        if not (n.get("localizeText") or {}).get("localizeEnabled"):
            raise NotImplementedError(f"{n['path']}: text without LocalizeText")
        t["localized"] = tmpfont.localize_text(n["path"], t["fontAsset"], t["material"], font_swap, lang)
        primaries[t["localized"]["fontAsset"]] = None
    for fname in primaries:
        primaries[fname] = fonts.by_key(f"{tmpfont.FONT_PREFIX}{fname[:-4]}/{fname}")

    # -- characters the episode shows ------------------------------------------
    lines = []
    for c in episode["commands"]:
        if c["cmd"] in ("Talk", "Location") and c.get("lines"):
            lines.append(c["lines"][LANGUAGE_FIELD])
    ms = ex.asset(MASTER_ID_SETTINGS_KEY)
    names = set()
    for c in episode["commands"]:
        if c["cmd"] == "Talk" and c.get("TargetName") and c.get("TargetStatus", 0) != 2 and c.get("AdvTextID"):
            ids = list(c.get("TargetTextIDs") or [])
            if c.get("TargetStatus", 0) == 1:
                ids = [ms["_unknownCharacterNameTextId"]]
            elif len(ids) > 1:
                ids.append(ms["_splitCharacterNameTextId"])
            for i in ids:
                row = episode["text"].get(i)
                if row is None:
                    raise KeyError(f"speaker text id {i} not in the episode text shard")
                names.add(row[LANGUAGE_FIELD])
    title = episode.get("title")
    if not title:
        raise RuntimeError("episode.json has no title lines (adv.extract)")
    title_text = title[LANGUAGE_FIELD]
    shown = [_strip_tags(s) for s in lines + sorted(names) + [title_text]]
    chars = sorted({ord(ch) for s in shown for ch in s})

    primary = [primaries[p] for p in primaries]
    if len(primary) != 1:
        raise NotImplementedError(f"texts use {len(primary)} primary fonts")
    primary = primary[0]
    coverage, needed, runtime_units = tmpfont.glyph_coverage(fonts, primary, chars)

    # -- materials ---------------------------------------------------------------
    materials, text_materials = tmpfont.text_materials(ex, fonts, [n["text"]["localized"] for n in used_text_nodes],
                                                       {primary.name}, needed, runtime_units)
    rule = next(n for n in out_nodes if n["path"].endswith("FrontCanvas/RuleTransition"))
    rt_comp = [c for c in next(w for w in widget if w["path"] == rule["path"])["components"]
               if c.get("class") == "UIAdvRuleTransitionView"][0]
    materials[rt_comp["_material"]["material"]] = rt_comp["_material"]
    rule["ruleTransition"] = {"material": rt_comp["_material"]["material"]}
    # Graphic.defaultGraphicMaterial (Canvas.GetDefaultCanvasMaterial): the player
    # ships the built-in UI/Default shader (unity_builtin_extra) but no serialized
    # "Default UI Material", so its values are the shader's property defaults.
    ui_default = player.shader("UI/Default", file="unity_builtin_extra")
    materials["Default UI Material"] = {"material": "Default UI Material", "shader": {"shader": "UI/Default"},
                                        "keywords": [], "textures": {}, "ints": {}, "floats": {}, "colors": {},
                                        "shaderDefaults": True}

    # -- glyph blocks -------------------------------------------------------------
    font_out = tmpfont.export_fonts(ex, fonts, {primary.name}, needed, runtime_units, text_materials, packer,
                                    "characters shown by this episode (plus baked control characters); runtime "
                                    "characters generated from the source font (runtimeGlyphs)")

    # -- sprites -------------------------------------------------------------------
    sprite_refs = set()
    for n in out_nodes:
        if n.get("image") and n["image"]["spriteRef"]:
            sprite_refs.add(n["image"]["spriteRef"])
    lb_sprite = ex.key_sprite(LETTERBOX_KEY)
    sprite_refs.add(lb_sprite["spriteRef"])
    sprites_out = {}
    for ref in sorted(sprite_refs):
        s = ex.sprite_recs[ref]
        tex_name = s["tex"].read_typetree()["m_Name"]
        ex.pack_sprite(ref, packer, "sprites" if "FixUiSpriteAtlas" in tex_name else f"sprite_{s['name']}")
    # transitions
    ps = ex.asset(PLAYER_SETTINGS_KEY)
    addresses = {ps["_defaultTransitionAssetAddress"]}
    for c in list(ps["_initializeEpisodes"]) + list(ps["_finalizeEpisodes"]) + episode["commands"]:
        cmd = c.get("Command") if "Command" in c else c.get("raw")
        if cmd in (5, 6) and c.get("TargetAssetName"):
            addresses.add(c["TargetAssetName"])
    transitions = {}
    for addr in sorted(addresses):
        a = ex.asset(TRANSITION_PREFIX + addr)
        tex = a["_texture"]
        if tex is None:
            raise RuntimeError(f"transition {addr}: no rule texture")
        if tex["mipCount"] > 1:
            raise NotImplementedError(f"transition {addr}: mipmapped rule texture")
        o = ex.tex_objs[tex["textureRef"]]
        a["_block"] = packer.add(f"rule_{_safe(addr)}", o, 0, 0, tex["width"], tex["height"])
        transitions[addr] = a

    textures = packer.build()

    # resolve blocks
    tmpfont.resolve_font_blocks(font_out)
    for ref in sorted(sprite_refs):
        sprites_out[ex.sprite_recs[ref]["name"]] = ex.packed_sprite(ref)
    for n in out_nodes:
        if n.get("image"):
            n["image"].pop("spriteRef", None)
    for addr, a in transitions.items():
        b = a.pop("_block")
        t = textures[b["texture"]]
        # rule textures are sampled with screen-space UVs over [0, 1]: they stay whole
        if b["offset"] != (0, 0) or (t["width"], t["height"]) != (a["_texture"]["width"], a["_texture"]["height"]):
            raise RuntimeError(f"transition {addr}: rule texture not copied whole")
        a["_texture"] = {"texture": b["texture"], "name": a["_texture"]["name"]}
    for m in materials.values():
        for k, v in m["textures"].items():
            if v["texture"] is not None:
                v["texture"] = {"name": v["texture"]["name"], "width": v["texture"]["width"],
                                "height": v["texture"]["height"], "format": v["texture"]["format"]}

    # -- clips ----------------------------------------------------------------------
    clips = {}
    for name, key in CLIP_KEYS.items():
        o = ex.key_object(key)
        if o.type.name != "AnimationClip":
            raise RuntimeError(f"{key}: container asset is {o.type.name}")
        clips[name] = ex.clip_curves(o)
    controllers = {name: ex.controller_states(ex.key_object(key)) for name, key in CONTROLLER_KEYS.items()}

    # -- DOTween defaults (Resources/DOTweenSettings) ------------------------------------
    dts = player.mono(player.resource("DOTweenSettings"))
    tmp_settings = player.names_in(player.resource("TMP Settings"), player.mono(player.resource("TMP Settings")))

    # -- shaders ----------------------------------------------------------------------
    needed = {}
    for name in sorted({m["shader"]["shader"] for m in materials.values()}):
        if name == "UI/Default":
            needed[name] = ui_default
        elif name in ex.shaders:
            needed[name] = ex.shaders[name]
        else:
            raise RuntimeError(f"shader {name} not in the loaded bundles")
    index, shader_summary = ex.dump_shaders(ui_dir / "shaders", needed)
    variants_needed = {}
    for m in materials.values():
        sh = m["shader"]["shader"]
        rec = next(r for r in index if r["name"] == sh)
        known = {k for v in rec["variants"] if v["platform"] == "gles3" and v["type"] == "GLES3" for k in v["keywords"]}
        want = sorted(k for k in m["keywords"] if k in known)
        ok = any(v["platform"] == "gles3" and v["type"] == "GLES3" and sorted(v["keywords"]) == want
                 and v["stage"] == "vertex" for v in rec["variants"])
        if not ok:
            raise RuntimeError(f"{sh}: no GLES3 variant for material {m['material']} keywords {want}")
        variants_needed[m["material"]] = want

    doc = {
        "about": "ADV front canvas UI data (UIAdvWidget + UIDefaultTalkWindow), zh-Hant",
        "language": {"mode": LANGUAGE_MODE, "field": LANGUAGE_FIELD, "fonts": lang,
                     "fontSwap": font_swap, "lineSpacing": LANGUAGE_LINE_SPACING[LANGUAGE_MODE]},
        "frontCanvasOrder": front_order,
        "nodes": out_nodes,
        "sprites": sprites_out,
        "letterBoxSprite": lb_sprite["name"],
        "textures": textures,
        "materials": materials,
        "materialKeywords": variants_needed,
        "fonts": font_out,
        "tmpSettings": {k: tmp_settings[k] for k in ("m_fallbackFontAssets", "m_defaultFontAsset",
                                                     "m_missingGlyphCharacter", "m_matchMaterialPreset",
                                                     "m_enableExtraPadding", "m_warningsDisabled")},
        "clips": clips,
        "controllers": controllers,
        "transitions": transitions,
        "playerSettings": {k: ps[k] for k in (
            "_defaultTransitionAssetAddress", "_waitAfterVoiceTime", "_waitTalkTextUnitTime",
            "_minTalkDisplayTime", "_isAdvViewportFollowOnResolutionChanged")},
        "dotween": {k: dts[k] for k in ("defaultEaseType", "defaultUpdateType", "defaultTimeScaleIndependent",
                                        "timeScale", "defaultEaseOvershootOrAmplitude", "defaultEasePeriod")},
        "shaders": {"index": "shaders/shaders.json", "names": shader_summary["names"]},
        "glyphCoverage": coverage,
    }
    write_json(ui_dir / "ui.json", doc)
    return {"textures": {k: (v["width"], v["height"]) for k, v in textures.items()},
            "shaders": shader_summary["names"], "coverage": coverage["counts"],
            "fonts": sorted(font_out),
            "runtimeGlyphs": {k: {"generated": len(v["runtimeGlyphs"]["generated"]),
                                  "validation": {kk: v["runtimeGlyphs"]["validation"][kk] for kk in
                                                 ("glyphs", "maxAbsError", "meanAbsError", "exactGlyphs")}}
                              for k, v in font_out.items() if v["runtimeGlyphs"]}}
