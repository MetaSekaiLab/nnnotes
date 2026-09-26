"""ADV story UI -> <story>/ui/ (ui.json, textures/, shaders/).

Everything a player needs to draw the ADV front canvas (talk window, speaker
plate, location caption, episode title, rule transition cover, menu entry
button, menu panel and video buttons, backlog, choices, curtains, flash,
subtitles, next indicator, the tap areas), the screen canvases of stills,
frames and videos, the letterbox bands and the dialogs of the ADV screen,
taken from the game data:

  nodes        RectTransform hierarchy of the drawn parts of UIAdvWidget
               (DRAWN) with the default talk window (UIDefaultTalkWindow)
               attached under UIContainer/TalkView (AdvTalkView.SetWindow); per
               node the uGUI components the runtime needs (Canvas and scaler,
               Image, RawImage, CanvasGroup, UIGradientImage, layout groups,
               DOTweenSequence durations, the views' serialized references as
               node paths, `behaviours`: every serialized field of the other
               game components of BEHAVIOURS, references as node paths and
               sprites as names) and, for a TMP text, its text record `textStyle`
               (textstyle.py: layout, colours, style flags, face / outline /
               underlay values in em, font role)
  videoAndStillCamera  the camera the still and video canvases render with
               (UIAdvWidget._videoAndStillCamera) and the screen image showing
               its output (_videoAndStillScreenImage)
  masterIdTexts  the AdvMasterIdSettings texts of the skip and video skip
               dialogs and the interruption dialog in the language (with the
               master data)
  dialogs      the skip confirm dialog (UIAdvSkipConfirmDialogWidget) and
               CommonDialogManager's dialog (UICommonDialogWidget) as node
               records with text records
  chatWidget   the phone of the chat rows (UIAdvChatWidget): its canvas and
               scaler, AdvChatView with its durations and eases, the window
               parent rect
  chatTexts    per chat window of the episode (AdvChatWindow prefab), the text
               record of each TMP text in the language, and chatStatusTexts:
               the incoming call / lock screen status texts the windows set
  sprites      sprite geometry (rect, border, pivot, pixels per unit, texture
               rect/offset) remapped into packed textures
  materials    UI-Transition, the built-in Default UI Material, the materials
               of the drawn images
  clips        Animator clips (NextIndicator, AutoNext, AdvLocation/AdvTitle Play)
  transitions  RuleTransitionSettings of every transition the episode or the
               player settings reference
  shaders      UI/Default, UI/Transition

Language (`language`, a catalog language code; default `[catalog] language`):
the LanguageMode of the localized fonts, materials and line spacing
(LocalizeText) and the text field of the lines the episode shows.

Fonts (`fonts`): "open" (default) writes no font data; a renderer draws the
text records with fonts of its own. "game" also writes the game's TMP font
data (tmpfont.py, optional `fonts` extra): per node the serialized TMP text
settings with the localized font / material (`text`), the TMP font assets
reached from the localized fonts (face info, style values, fallback chain)
with the character and glyph tables reduced to the characters the episode
shows (characters a dynamic font asset would add at runtime are generated the
way FontEngine renders them, see tmpfont.RuntimeGlyphs), their materials, the
glyph coverage, the TMP settings and the TextMeshPro/Mobile/Distance Field
shader.

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
from . import adv, languages, textstyle
from .textstyle import LANGUAGE_LINE_SPACING

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
# Parts of the widget the runtime draws (and their ancestors). The block canvas
# is not exported.
DRAWN = (
    "UIAdvWidget/VideoCanvas",
    "UIAdvWidget/StillCanvas",
    "UIAdvWidget/VideoAndStillRenderScreenCanvas",
    "UIAdvWidget/FrameCanvas",
    "UIAdvWidget/FrontCanvas/FlashView",
    "UIAdvWidget/FrontCanvas/RuleTransition",
    "UIAdvWidget/FrontCanvas/LocationVIew",
    "UIAdvWidget/FrontCanvas/RightCurtain",
    "UIAdvWidget/FrontCanvas/LeftCurtain",
    "UIAdvWidget/FrontCanvas/CancelFullScreenButton",
    "UIAdvWidget/FrontCanvas/TalkLogView",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/NextButton",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/ChoiceView",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/SubtitlesView",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/TalkView",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/NextIndicator",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/MenuView/MenuButtonsParent",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/MenuView/MenuEntryButton",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/MenuView/VideoButtons",
    "UIAdvWidget/FrontCanvas/UISafeArea/UIContainer/TitleView",
    "UIAdvWidget/AdvLetterBoxCanvas",
)
# the view components of the drawn parts: record key and the serialized references kept (as node paths)
VIEWS = {
    "AdvVideoView": ("videoView", ("_video", "_maskArea", "_curtainCanvasGroup", "_videoCanvasGroup")),
    "AdvStillView": ("stillView", ("_overlay", "_background", "_target")),
    "AdvFrameView": ("frameView", ()),
    "AdvFlashView": ("flashView", ("_flash",)),
    "AdvSubtitlesView": ("subtitlesView", ("_subtitles", "_subtitlesText")),
    "AdvFrontScreenView": ("frontScreenView", ("_nextButton", "_nextIndicator", "_cancelFullScreenButton",
                                               "_uiContainer")),
}
# views recorded with every serialized field (references as node paths)
FULL_VIEWS = {"AdvTalkLogView": "talkLogView", "AdvChoiceView": "choiceView"}
# the other game and uGUI components recorded in `behaviours` (per class, every component of the node in order):
# buttons and their state and animation parts, raycast targets, colour synchronizers, texts' UIText, safe areas,
# scrolling, DOTween animations, localized sprites, the backlog entry and choice item views, the dialog parts
BEHAVIOURS = ("UIButton", "ButtonActiveState", "ButtonAnimationScale", "ButtonSound", "SimpleRaycastTarget",
              "GraphicColorSynchronizer", "UIText", "AspectRatioFitter", "UIToggle", "UIToggleGroup",
              "UIToggleButtonFrame", "UIDecoratedNormalButton", "UIDecoratedNormalButtonFrame", "UIAddressableImage",
              "UISafeArea", "AdvViewportSafeArea", "EnhancedScrollRect", "EnhancedScroller", "ScrollRect", "RectMask2D",
              "Scrollbar", "DOTweenAnimation", "LocalizeSpriteEvent", "AdvTalkLogEntryView", "AdvChoiceItem",
              "DialogButtons", "DialogAnimation", "DialogSizeFitter", "UIAdvSkipConfirmDialogWidget",
              "UICommonDialogWidget")
SERIALIZED_SKIP = ("type", "class", "m_GameObject", "m_Script", "m_Name", "m_EditorHideFlags",
                   "m_EditorClassIdentifier")
# the dialogs of the ADV screen: the skip confirm dialog and CommonDialogManager's dialog widget
DIALOG_KEYS = {"UIAdvSkipConfirmDialogWidget": "EmbUI/Prefab/UIAdvSkipConfirmDialogWidget",
               "UICommonDialogWidget": "EmbUI/Prefab/UICommonDialogWidget"}
CAMERA_FIELDS = ("m_Enabled", "m_ClearFlags", "m_BackGroundColor", "m_NormalizedViewPortRect", "near clip plane",
                 "far clip plane", "field of view", "orthographic", "orthographic size", "m_Depth", "m_CullingMask",
                 "m_HDR", "m_AllowMSAA")
CAMERA_DATA_FIELDS = ("m_RenderPostProcessing", "m_VolumeLayerMask", "m_RendererIndex", "m_Antialiasing",
                      "m_AntialiasingQuality", "m_RenderShadows", "m_RequiresDepthTextureOption",
                      "m_RequiresOpaqueTextureOption", "m_CameraType", "m_VolumeFrameworkUpdateModeOption",
                      "m_Dithering", "m_StopNaN")
# AdvMasterIdSettings text ids the drawn parts show: the video skip dialog (AdvMenuView's skip video button), the
# skip confirm dialog and the interruption dialog
MASTER_ID_TEXTS = ("_skipVideoMessageTextId", "_skipButtonTextId", "_cancelButtonTextId", "_skipMessageTextId",
                   "_continuousSkipMessageTextId", "_backEpisodeListButtonTextId", "_continueEpisodeButtonTextId",
                   "_interruptionTitleTextId", "_interruptionMessageTextId", "_interruptionButtonTextId")
RUBY = re.compile(r"<r=([^<>]*)>")                  # ruby reading markup of the text tables: <r=reading>base</r>
# the phone of the chat rows: UIAdvChatWidget's canvas, AdvChatView and the parent of the attached window
CHAT_WIDGET_KEY = "EmbUI/Prefab/UIAdvChatWidget"
CHAT_WIDGET_NODES = ("UIAdvChatWidget/ChatCanvas", "UIAdvChatWidget/ChatCanvas/AdvChatView",
                     "UIAdvChatWidget/ChatCanvas/AdvChatView/Target")
CHAT_VIEW_FIELDS = ("_showEaseDuration", "_hideEaseDuration", "_showEase", "_hideEase", "_scrollDuration",
                    "_typingDelay", "_typingTextBoxMinHeight", "_screenModeTransitionDuration",
                    "_screenModeTransitionEase", "_incomingCallPositionOffset")
CHAT_STATUS_KEYS = ("_incomingCallStatusTextKey", "_lockScreenStatusTextKey")    # AdvChatWindow.RefreshStatusTexts
CHAT_COMMANDS = ("ChatWindow", "ChatTalk", "ChatStamp", "ChatRead", "ChatTyping")
# AdvMenuView: its serialized references (node paths), the fast-forward button sprites (AdvMenuView.SetFastForwardSpeed)
MENU_VIEW_REFS = ("_menuButtonsParent", "_menuEntryButton", "_skipButton", "_autoButton", "_fastForwardButton",
                  "_logButton", "_continuousButton", "_fullScreenButton", "_interruptionButton",
                  "_menuButtonsBackground", "_canvasGroup", "_skipVideoButton", "_pauseVideoButton", "_subtitlesButton",
                  "_videoButtonParent", "_videoFilterButton", "_fastForwardButtonImage")
MENU_VIEW_SPRITES = ("_fastForwardNormalSprite", "_fastForwardOnePointFiveSprite", "_fastForwardOnePointSevenSprite",
                     "_fastForwardDoubleSprite")
EMOJI_SPRITE_ASSET_KEY = "Font/SiriusEmoji/SiriusEmojiData"   # LocalizeManager.EmojiSpriteAssetAddress
EMOJI_CLASSES = ("RubyEmojiTextMeshProUGUI",)
FONT_ADDRESS = "Font/"            # LocalizeManager.FontAddressablePath; the embedded fonts are at "Emb" + address
# TMP_Text.GetUnderlineSpecialCharacter: the underline and the text highlight (DrawTextHighlight) draw with the '_'
# of the text's font asset (no fallback), sampled on the asset's first atlas page
UNDERLINE_CHARACTER = 0x5F

TMP_CLASSES = ("TextMeshProUGUI", "RubyTextMeshProUGUI", "RubyEmojiTextMeshProUGUI")
TMP_FIELDS = (
    "m_fontSize", "m_fontSizeBase", "m_fontStyle", "m_fontWeight", "m_HorizontalAlignment", "m_VerticalAlignment",
    "m_characterSpacing", "m_wordSpacing", "m_lineSpacing", "m_paragraphSpacing", "m_characterHorizontalScale",
    "m_TextWrappingMode", "m_overflowMode", "m_enableAutoSizing", "m_fontSizeMin", "m_fontSizeMax",
    "m_enableKerning", "m_ActiveFontFeatures", "m_enableExtraPadding", "m_isRichText", "m_parseCtrlCharacters",
    "m_isOrthographic", "m_overrideHtmlColors", "m_margin", "m_fontColor", "m_Color", "m_colorMode",
    "m_enableVertexGradient", "m_TextStyleHashCode", "m_useMaxVisibleDescender", "m_horizontalMapping",
    "m_verticalMapping", "m_charWidthMaxAdj", "m_lineSpacingMax", "m_isRightToLeft", "m_text",
    "m_monospaceDistEm",                  # TMP_EmojiTextUGUI (the emoji text classes)
)
FONT_MODES = ("open", "game")
IMAGE_FIELDS = ("m_Enabled", "m_Color", "m_Type", "m_PreserveAspect", "m_FillCenter", "m_FillMethod",
                "m_FillAmount", "m_UseSpriteMesh", "m_PixelsPerUnitMultiplier", "m_Maskable")


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^<>]*>", "", s)


def _ref_path(v) -> str | None:
    """The node path of a serialized reference as the exporter writes it ({component | transform | gameObject})."""
    if not v:
        return None
    return v.get("gameObject") or v.get("transform")


def _plain(v, sprites: list):
    """A serialized value as plain data: a reference as its node path, a sprite as its name (its reference added to
    `sprites`), a ScriptableObject or material as its name, a texture as its name; containers element-wise."""
    if isinstance(v, list):
        return [_plain(x, sprites) for x in v]
    if not isinstance(v, dict):
        return v
    if "spriteRef" in v:
        sprites.append(v["spriteRef"])
        return v["name"]
    if "gameObject" in v or "transform" in v:
        return _ref_path(v)
    if "asset" in v or ("material" in v and "shader" in v):
        return v.get("name") or v.get("material")
    if "textureRef" in v:
        return v.get("name")
    return {k: _plain(x, sprites) for k, x in v.items()}


def _serialized(c: dict, sprites: list) -> dict:
    """Every serialized field of a component (SERIALIZED_SKIP left out), as _plain values."""
    return {k: _plain(v, sprites) for k, v in c.items() if k not in SERIALIZED_SKIP}


def _pictogram(c: dict) -> dict:
    """App.UI.UIPictogram: its key, image and native-size flag and the catalog it draws from (the sprite is
    resolved against the catalog in extract)."""
    return {"m_Enabled": c["m_Enabled"], "_key": c["_key"], "_image": _ref_path(c["_image"]),
            "_setNativeSize": c["_setNativeSize"], "catalog": (c["_spriteCatalog"] or {}).get("name")}


def _layout_components(node: dict) -> dict:
    out: dict = {}
    sprites: list = []
    behaviours: dict = {}
    image_states = [c for c in node["components"] if c.get("class") == "ButtonImageState"]
    for c in node["components"]:
        cls = c.get("class")
        t = c["type"]
        if t == "CanvasGroup":
            out["canvasGroup"] = {k: c[k] for k in ("m_Enabled", "m_Alpha", "m_IgnoreParentGroups")}
        elif t == "Canvas":
            out["canvas"] = {k: c[k] for k in ("m_Enabled", "m_RenderMode", "m_SortingOrder",
                                               "m_VertexColorAlwaysGammaSpace", "m_AdditionalShaderChannelsFlag",
                                               "m_OverrideSorting", "m_PixelPerfect")}
            if c["m_RenderMode"] == 1:         # screen space - camera: the plane it is drawn at, its camera if set
                out["canvas"]["m_PlaneDistance"] = c["m_PlaneDistance"]
                if c.get("m_Camera"):
                    out["canvas"]["camera"] = _ref_path(c["m_Camera"])
        elif t == "Animator":
            out["animator"] = {"controller": (c["m_Controller"] or {}).get("name"),
                               "enabled": c["m_Enabled"], "updateMode": c["m_UpdateMode"],
                               "keepStateOnDisable": c["m_KeepAnimatorStateOnDisable"]}
        elif cls in ("CanvasScaler", "ClampedCanvasScaler"):
            out["canvasScaler"] = {k: c[k] for k in ("m_Enabled", "m_UiScaleMode", "m_ReferencePixelsPerUnit",
                                                     "m_ScaleFactor", "m_ReferenceResolution", "m_ScreenMatchMode",
                                                     "m_MatchWidthOrHeight")}
            if cls == "ClampedCanvasScaler":   # Fwk.UI.ClampedCanvasScaler: the scale clamped past an aspect ratio
                out["canvasScaler"].update({"class": cls, "_maxAspectThreshold": c["_maxAspectThreshold"]})
        elif cls == "Image":
            img = {k: c[k] for k in IMAGE_FIELDS}
            img["sprite"] = c["m_Sprite"]["name"] if c["m_Sprite"] else None
            img["spriteRef"] = c["m_Sprite"]["spriteRef"] if c["m_Sprite"] else None
            img["material"] = c["m_Material"]["material"] if c["m_Material"] else None
            out["image"] = img
        elif cls == "RawImage":
            out["rawImage"] = {k: c[k] for k in ("m_Enabled", "m_Color", "m_UVRect", "m_RaycastTarget", "m_Maskable")}
            out["rawImage"]["texture"] = (c["m_Texture"] or {}).get("name")
            out["rawImage"]["material"] = c["m_Material"]["material"] if c["m_Material"] else None
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
        elif cls == "ButtonImageState" and len(image_states) > 1:
            behaviours.setdefault(cls, []).append(_serialized(c, sprites))
        elif cls == "ButtonImageState":
            out["buttonImageState"] = {"normal": c["_normaledSprite"]["name"] if c["_normaledSprite"] else None}
            if c["_selectedSprite"]:           # ButtonImageState.SetSelected: the sprite of the selected state
                out["buttonImageState"].update({"selected": c["_selectedSprite"]["name"],
                                                "selectedRef": c["_selectedSprite"]["spriteRef"],
                                                "target": _ref_path(c["_target"])})
        elif cls == "UISafeAreaEdgeAnchor":
            out["safeAreaEdgeAnchor"] = {"enabled": c["m_Enabled"],
                                         **{k: c[k] for k in ("_left", "_right", "_top", "_bottom")}}
        elif cls in VIEWS:
            key, refs = VIEWS[cls]
            out[key] = {"enabled": c["m_Enabled"], **{k: _ref_path(c[k]) for k in refs}}
        elif cls == "AdvMenuView":
            out["menuView"] = {"enabled": c["m_Enabled"], **{k: _ref_path(c[k]) for k in MENU_VIEW_REFS},
                               **{k: c[k]["name"] if c[k] else None for k in MENU_VIEW_SPRITES},
                               "_fadeDuration": c["_fadeDuration"]}
            sprites += [c[k]["spriteRef"] for k in MENU_VIEW_SPRITES if c[k]]
        elif cls in FULL_VIEWS:
            out[FULL_VIEWS[cls]] = {"enabled": c["m_Enabled"], **_serialized(c, sprites)}
        elif cls == "UIPictogram":
            behaviours.setdefault(cls, []).append(_pictogram(c))
        elif cls in BEHAVIOURS:
            behaviours.setdefault(cls, []).append(_serialized(c, sprites))
        elif cls == "AdvChatView":
            out["chatView"] = {"enabled": c["m_Enabled"], **{k: c[k] for k in CHAT_VIEW_FIELDS},
                               "_windowParentRect": _ref_path(c["_windowParentRect"])}
        elif cls == "Outline":
            out["outline"] = {"enabled": c["m_Enabled"]}
    if behaviours:
        out["behaviours"] = behaviours
    if sprites:
        out["_spriteRefs"] = sprites                    # packed into the UI textures, removed in extract
    return out


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def _node_record(n: dict) -> dict:
    """A node of ui.json: transform, rect and the uGUI component records (_layout_components)."""
    rec = {"path": n["path"], "name": n["name"], "active": n["active"], "localPosition": n["localPosition"],
           "localRotation": n["localRotation"], "localScale": n["localScale"], "rect": n.get("rect")}
    rec.update(_layout_components(n))
    return rec


def _component(node: dict, classes: tuple) -> dict | None:
    hit = [c for c in node["components"] if c.get("class") in classes]
    if len(hit) > 1:
        raise RuntimeError(f"{node['path']}: {len(hit)} components of {classes}")
    return hit[0] if hit else None


def extract(cat: Catalog, player: PlayerData, episode: dict, out_dir: Path, fonts: str = "open", *,
            language: str | None = None, master: Path | None = None) -> dict:
    """Write <out_dir>/ui/. `fonts`: "open" (text records only) or "game" (also the game's TMP font data);
    `language`: the client language (a languages.LANGUAGES code; None: `[catalog] language` of the settings);
    `master`: the decoded master data (MasterText of `masterIdTexts`; None: no `masterIdTexts`)."""
    if fonts not in FONT_MODES:
        raise ValueError(f"fonts must be one of {FONT_MODES}, not {fonts!r}")
    language = languages.check(language) if language is not None else languages.configured()
    mode, field = languages.mode(language), languages.column(language)[1:]
    game = fonts == "game"
    if game:
        from . import tmpfont
        tmpfont.require_extra()
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
    styles = textstyle.TextStyles(ex, player, mode)
    dialog_nodes = {name: ex.prefab(key)["nodes"] for name, key in DIALOG_KEYS.items()}
    catalogs = pictogram_catalogs(widget + window + [n for dn in dialog_nodes.values() for n in dn])

    def records(prefab_nodes: list, keep_text: bool) -> list:
        out = []
        for n in prefab_nodes:
            rec = _node_record(n)
            if "text" in rec:
                if not (rec.get("localizeText") or {}).get("localizeEnabled"):
                    raise NotImplementedError(f"{n['path']}: text without LocalizeText")
                rec["textStyle"] = styles.record(n["path"], _component(n, TMP_CLASSES),
                                                 _component(n, ("LocalizeText",)))
                if not keep_text:
                    del rec["text"]
            resolve_pictograms(rec, catalogs)
            out.append(rec)
        return out
    out_nodes = records(nodes, game)
    dialogs = {name: {"key": DIALOG_KEYS[name], "nodes": records(dn, False)} for name, dn in dialog_nodes.items()}
    front = [n for n in widget if n["path"].startswith("UIAdvWidget/FrontCanvas/") and n["path"].count("/") == 2]
    front_order = [n["name"] for n in front]
    widget_comp = next(c for c in widget[0]["components"] if c.get("class") == "UIAdvWidget")
    widget_rec = widget_canvases(widget, widget_comp)
    emoji = emoji_sprites(ex, nodes)
    camera = _camera(widget, _ref_path(widget_comp["_videoAndStillCamera"]))
    screen_image = _ref_path(widget_comp["_videoAndStillScreenImage"])
    if screen_image not in {n["path"] for n in out_nodes}:
        raise RuntimeError(f"{screen_image} (_videoAndStillScreenImage) is not a drawn node")
    master_ids = ex.asset(MASTER_ID_SETTINGS_KEY)          # exported once: a second export is a reference
    id_texts = master_id_texts(master_ids, master, field) if master is not None else None
    chat_nodes = {n["path"]: n for n in ex.prefab(CHAT_WIDGET_KEY)["nodes"]}
    missing = [p for p in CHAT_WIDGET_NODES if p not in chat_nodes]
    if missing:
        raise RuntimeError(f"{missing} missing from {CHAT_WIDGET_KEY}")
    chat_widget = {"nodes": [_node_record(chat_nodes[p]) for p in CHAT_WIDGET_NODES]}
    chat, status_ids = chat_texts(ex, styles.lang, mode, episode)
    status_texts = master_texts(master, status_ids, field) if master is not None and status_ids else None

    materials: dict = {}
    if game:
        materials, font_doc = _game_fonts(ex, styles, episode, out_nodes, field, master_ids,
                                          [t["text"] for t in (id_texts or {}).values()]
                                          + list((status_texts or {}).values()))
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
    # the materials of the drawn images (the video mask and the curtain it masks)
    for n in nodes + [n for dn in dialog_nodes.values() for n in dn]:
        for c in n["components"]:
            if c.get("class") in ("Image", "RawImage") and c["m_Material"]:
                materials.setdefault(c["m_Material"]["material"], c["m_Material"])

    # -- glyph blocks -------------------------------------------------------------
    if game:
        font_out = tmpfont.export_fonts(ex, font_doc["fonts"], font_doc["primaries"], font_doc["needed"],
                                        font_doc["runtime"], font_doc["textMaterials"], packer,
                                        "characters shown by this episode (plus baked control characters); runtime "
                                        "characters generated from the source font (runtimeGlyphs)")

    # -- sprites -------------------------------------------------------------------
    sprite_refs = set()
    all_records = out_nodes + [n for d in dialogs.values() for n in d["nodes"]]
    for n in all_records:
        if n.get("image") and n["image"]["spriteRef"]:
            sprite_refs.add(n["image"]["spriteRef"])
        if (n.get("buttonImageState") or {}).get("selectedRef"):
            sprite_refs.add(n["buttonImageState"]["selectedRef"])
        sprite_refs.update(n.get("_spriteRefs", ()))
        for p in (n.get("behaviours") or {}).get("UIPictogram", ()):
            sprite_refs.update(r for r in (p.pop("_spriteRef", None), p.pop("_defaultRef", None)) if r)
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
    if game:
        tmpfont.resolve_font_blocks(font_out)
    for ref in sorted(sprite_refs):
        sprites_out[ex.sprite_recs[ref]["name"]] = ex.packed_sprite(ref)
    for n in all_records:
        if n.get("image"):
            n["image"].pop("spriteRef", None)
        if n.get("buttonImageState"):
            n["buttonImageState"].pop("selectedRef", None)
        n.pop("_spriteRefs", None)
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
        "about": about(language),
        "language": language_doc(language, styles if game else None),
        "frontCanvasOrder": front_order,
        "widget": widget_rec,
        "nodes": out_nodes,
        "videoAndStillCamera": camera,
        "videoAndStillScreenImage": screen_image,
        "chatWidget": chat_widget,
        "emoji": emoji,
        "sprites": sprites_out,
        "letterBoxSprite": lb_sprite["name"],
        "textures": textures,
        "materials": materials,
        "materialKeywords": variants_needed,
    }
    if game:
        tmp_settings = player.names_in(player.resource("TMP Settings"), player.mono(player.resource("TMP Settings")))
        doc["fonts"] = font_out
        doc["tmpSettings"] = {k: tmp_settings[k] for k in ("m_fallbackFontAssets", "m_defaultFontAsset",
                                                          "m_missingGlyphCharacter", "m_matchMaterialPreset",
                                                          "m_enableExtraPadding", "m_warningsDisabled")}
    doc.update({
        "clips": clips,
        "controllers": controllers,
        "transitions": transitions,
        "playerSettings": {k: ps[k] for k in (
            "_defaultTransitionAssetAddress", "_waitAfterVoiceTime", "_waitTalkTextUnitTime",
            "_minTalkDisplayTime", "_isAdvViewportFollowOnResolutionChanged")},
        "dotween": {k: dts[k] for k in ("defaultEaseType", "defaultUpdateType", "defaultTimeScaleIndependent",
                                        "timeScale", "defaultEaseOvershootOrAmplitude", "defaultEasePeriod")},
        "shaders": {"index": "shaders/shaders.json", "names": shader_summary["names"]},
    })
    if game:
        doc["glyphCoverage"] = font_doc["coverage"]
    if id_texts is not None:
        doc["masterIdTexts"] = id_texts
    doc["dialogs"] = dialogs
    if chat:
        doc["chatTexts"] = chat
    if status_texts:
        doc["chatStatusTexts"] = status_texts
    doc["textStyle"] = styles.summary()
    write_json(ui_dir / "ui.json", doc)
    out = {"textures": {k: (v["width"], v["height"]) for k, v in textures.items()},
           "shaders": shader_summary["names"]}
    if not game:
        return {**out, "fonts": "open", "texts": sum(1 for n in out_nodes if "textStyle" in n)}
    return {**out, "coverage": font_doc["coverage"]["counts"],
            "fonts": sorted(font_out),
            "runtimeGlyphs": {k: {"generated": len(v["runtimeGlyphs"]["generated"]),
                                  "validation": {kk: v["runtimeGlyphs"]["validation"][kk] for kk in
                                                 ("glyphs", "maxAbsError", "meanAbsError", "exactGlyphs")}}
                              for k, v in font_out.items() if v["runtimeGlyphs"]}}


def pictogram_catalogs(nodes: list) -> dict:
    """{catalog name: its serialized record} of the UIPictogram sprite catalogs the prefab nodes name (the exporter
    writes an asset in full at its first reference)."""
    out = {}
    for n in nodes:
        for c in n["components"]:
            cat = c.get("_spriteCatalog") if c.get("class") == "UIPictogram" else None
            if cat and "_spritesByKey" in cat:
                out.setdefault(cat["name"], cat)
    return out


def resolve_pictograms(rec: dict, catalogs: dict) -> None:
    """The sprite of each UIPictogram record of a node (UISpriteCatalogBase.GetSprite: the catalog's sprite of the
    key, `sprite`; null when the catalog has none) and the catalog's default sprite (`defaultSprite`)."""
    for p in (rec.get("behaviours") or {}).get("UIPictogram", ()):
        cat = catalogs.get(p["catalog"])
        if cat is None:
            raise RuntimeError(f"{rec['path']}: sprite catalog {p['catalog']} not exported")
        by_key = {e["Key"]: e["Value"] for e in cat["_spritesByKey"]["_list"]}
        hit, default = by_key.get(p["_key"]), cat.get("_defaultSprite")
        p["sprite"] = hit["name"] if hit else None
        p["defaultSprite"] = default["name"] if default else None
        p["_spriteRef"] = hit["spriteRef"] if hit else None
        p["_defaultRef"] = default["spriteRef"] if default and not hit else None


def widget_canvases(widget: list, comp: dict) -> dict:
    """widget: UIWidget._canvasSortOrder and the sorting order of each canvas of UIWidget._canvases, in list order
    (UIWidget.TrueCanvasSortOrder reads the first)."""
    by_path = {n["path"]: n for n in widget}
    canvases = []
    for ref in comp["_canvases"]:
        path = _ref_path(ref)
        hit = [c for c in (by_path.get(path) or {"components": []})["components"] if c["type"] == "Canvas"]
        if len(hit) != 1:
            raise RuntimeError(f"_canvases: {path} has {len(hit)} Canvas components")
        canvases.append({"path": path, "sortingOrder": hit[0]["m_SortingOrder"]})
    return {"canvasSortOrder": comp["_canvasSortOrder"], "canvases": canvases}


def emoji_sequence_key(name: str, unicode: int) -> str | None:
    """TMP_EmojiSearchEngine.TryUpdateSequenceLookupTable: the lookup key of a sprite named `name` (a "-" separated
    hex code point name), None when it is a single code point (its name equals `unicode` in hex). The name goes
    through BuildNameInEmojiSurrogateFormat (file name without extension, lower case, each part left-padded with
    "0" to 8 digits); each 8-digit chunk is a code point (char.ConvertFromUtf32). A chunk that is not a scalar value
    (the game appends its 4-digit halves as UTF-16 code units) is not implemented."""
    n = name.rsplit("/", 1)[-1]
    if "." in n:
        n = n[:n.rindex(".")]
    n = n.lower()
    if "-" in n:
        n = "".join(p.rjust(8, "0") for p in n.split("-"))
    if not n or n == f"{unicode & 0xFFFFFFFF:08x}":
        return None
    out = []
    for i in range(0, len(n), 8):
        chunk = n[i:i + 8]
        v = int(chunk, 16)
        if v == 0 or v >= 0x10FFFF or 0xD800 <= v < 0xE000:
            raise NotImplementedError(f"emoji sprite {name}: {chunk} is not a code point")
        out.append(chr(v))
    return "".join(out)


def emoji_sprites(ex: Exporter, nodes: list) -> dict:
    """emoji: the sprite asset the emoji texts (EMOJI_CLASSES) of the drawn nodes use (their m_spriteAsset, else
    LocalizeManager.EmojiSpriteAsset, TmpTextHelper.CombineEmojiSequences), its sequence lookup table
    ({key: sprite name}, TMP_EmojiSearchEngine; the first sprite of a key wins) and the code points of its sprite
    characters (m_SpriteCharacterTable)."""
    own = {c["m_spriteAsset"]["name"] if c.get("m_spriteAsset") else None
           for n in nodes for c in n["components"] if c.get("class") in EMOJI_CLASSES}
    if own - {None}:
        raise NotImplementedError(f"emoji texts with their own sprite asset: {sorted(own - {None})}")
    o = ex.key_object(EMOJI_SPRITE_ASSET_KEY)
    tt = o.read_typetree()
    if tt.get("fallbackSpriteAssets"):
        raise NotImplementedError(f"{EMOJI_SPRITE_ASSET_KEY}: fallback sprite assets")
    sequences = {}
    for s in tt["spriteInfoList"]:
        if s.get("name") and "-" in s["name"]:
            key = emoji_sequence_key(s["name"], s["unicode"])
            if key and key not in sequences:
                sequences[key] = s["name"]
    return {"spriteAsset": tt["m_Name"], "address": EMOJI_SPRITE_ASSET_KEY, "sequences": sequences,
            "characters": sorted({c["m_Unicode"] for c in tt["m_SpriteCharacterTable"]})}


def _camera(widget: list, path: str | None) -> dict:
    """videoAndStillCamera: the Camera (CAMERA_FIELDS) and UniversalAdditionalCameraData (CAMERA_DATA_FIELDS) of the
    node at `path` (UIAdvWidget._videoAndStillCamera)."""
    node = next((n for n in widget if n["path"] == path), None)
    if node is None:
        raise RuntimeError(f"_videoAndStillCamera: no node {path}")
    cam = [c for c in node["components"] if c["type"] == "Camera"]
    data = [c for c in node["components"] if c.get("class") == "UniversalAdditionalCameraData"]
    if len(cam) != 1 or len(data) != 1:
        raise RuntimeError(f"{path}: {len(cam)} Camera, {len(data)} UniversalAdditionalCameraData")
    return {"path": path, "active": node["active"], "camera": {k: cam[0][k] for k in CAMERA_FIELDS},
            "additionalCameraData": {k: data[0][k] for k in CAMERA_DATA_FIELDS}}


def master_texts(master: Path, ids, field: str) -> dict[str, str]:
    """{MasterText id: its text in the text field `field`} of `ids`; a missing id raises."""
    from . import master as master_mod
    ids = set(ids)
    rows = {r["_id"]: r for r in master_mod.table(Path(master), "MasterText") if r["_id"] in ids}
    missing = sorted(ids - set(rows))
    if missing:
        raise KeyError(f"text ids not in MasterText: {missing}")
    return {i: rows[i].get(f"_{field}") or "" for i in sorted(ids)}


def master_id_texts(settings: dict, master: Path, field: str) -> dict:
    """masterIdTexts: {AdvMasterIdSettings field of MASTER_ID_TEXTS: {id (MasterText id), text (in `field`)}}."""
    ids = {k: settings[k] for k in MASTER_ID_TEXTS}
    texts = master_texts(master, ids.values(), field)
    return {k: {"id": i, "text": texts[i]} for k, i in ids.items()}


def font_key(cat, font_asset: str, name: str) -> str:
    """The address of `name` (a font asset or one of its materials) in the folder of `font_asset`: the embedded copy
    "EmbFont/<font>/" (textstyle.font_dir) when the catalog has it, else LocalizeManager's "Font/<font>/"
    (FONT_ADDRESS; the additional fonts)."""
    key = textstyle.font_dir(font_asset) + name
    if cat.has(key):
        return key
    base = font_asset[:-4] if font_asset.endswith(" SDF") else font_asset
    return f"{FONT_ADDRESS}{base}/{name}"


def _lookup_lang(lang: dict) -> dict:
    """`lang` (textstyle.language_fonts) with the Japanese font names of LocalizeManager.InitJapaneseFontLookUpTable:
    the Japanese font names, then its additional font names (the table TryGetFontIndex searches)."""
    ja = lang["languages"][textstyle.JAPANESE_MODE]
    table = {**ja, "fontNames": ja["fontNames"] + ja["additionalFontNames"]}
    return {**lang, "languages": {**lang["languages"], textstyle.JAPANESE_MODE: table}}


def chat_localize(font_asset: str, material: str, lang: dict, mode: int) -> dict:
    """LocalizeText.Init -> OnFontChanged of a text with _localizeEnabled whose font may be an additional font (the
    chat windows' font): TryGetFontIndex = its index in the lookup table (_lookup_lang), the font of that index among
    the language's loaded fonts (its font names, then the additional fonts LocalizeManager.LoadAdditionalAsset
    appends; an index out of range: font 0), GetFontMaterial of the material type, ApplyLanguageLineSpacing.
    -> {fontAsset, material, lineSpacing}."""
    table = [f"{n} SDF" for n in _lookup_lang(lang)["languages"][textstyle.JAPANESE_MODE]["fontNames"]]
    if font_asset not in table:
        raise NotImplementedError(f"font {font_asset} not in the Japanese font lookup table")
    own = lang["languages"][mode]
    fonts = [f"{n} SDF" for n in own["fontNames"] + own["additionalFontNames"]]
    i = table.index(font_asset)
    font = fonts[i] if i < len(fonts) else fonts[0]
    mat_type = textstyle.material_type(material)
    if mat_type not in lang["materialTypes"]:
        raise NotImplementedError(f"material type {mat_type} not in LocalizeManager._materialTypeList")
    return {"fontAsset": font, "material": f"{font[:-4]} - {mat_type}", "lineSpacing": LANGUAGE_LINE_SPACING[mode]}


def chat_windows(episode: dict) -> dict[str, str]:
    """{window name: address} of the chat windows of the episode's resources ("Adv/Chat/Prefabs/" + name)."""
    return {r["address"][len(adv.CHAT_WINDOW_PREFIX):]: r["address"]
            for r in episode["resources"] if r["kind"] == "chatwindow"}


def chat_text_nodes(ex: Exporter, episode: dict):
    """(window name, node, TMP component, LocalizeText component or None) of every TMP text of the episode's chat
    windows, and the status text ids their AdvChatWindow components set (CHAT_STATUS_KEYS)."""
    out, status = [], set()
    for name, address in sorted(chat_windows(episode).items()):
        for n in ex.prefab(address)["nodes"]:
            win = _component(n, ("AdvChatWindow",))
            if win is not None:
                status |= {win[k] for k in CHAT_STATUS_KEYS if win.get(k)}
            comp = _component(n, TMP_CLASSES)
            if comp is not None:
                out.append((name, n, comp, _component(n, ("LocalizeText",))))
    return out, sorted(status)


def chat_texts(ex: Exporter, lang: dict, mode: int, episode: dict) -> tuple[dict, list[str]]:
    """chatTexts {window: {node path: {textStyle, localizeText}}} of the episode's chat windows in the language `mode`
    (the text record of textstyle.text_style with the font and material chat_localize gives; font roles are the
    slots of the lookup table) and the status text ids of the windows."""
    table_lang, styles, out = _lookup_lang(lang), {}, {}
    nodes, status = chat_text_nodes(ex, episode)
    for name, n, comp, loc in nodes:
        fa, mat = comp["m_fontAsset"]["name"], comp["m_sharedMaterial"]["material"]
        if loc and loc["m_Enabled"] and loc["_localizeEnabled"]:
            loc_font = chat_localize(fa, mat, lang, mode)
            fa, mat = loc_font["fontAsset"], loc_font["material"]
        if (fa, mat) not in styles:
            face = ex.key_object(font_key(ex.cat, fa, fa)).read_typetree()["m_FaceInfo"]
            styles[(fa, mat)] = textstyle.material_style(ex.material(ex.key_object(font_key(ex.cat, fa, mat))),
                                                         face["m_PointSize"], face["m_Scale"])
        out.setdefault(name, {})[n["path"]] = {
            "textStyle": textstyle.text_style(comp, loc, table_lang, styles[(fa, mat)], mode),
            "localizeText": {"enabled": loc["m_Enabled"], "localizeEnabled": loc["_localizeEnabled"]} if loc else None}
    return out, status


def _number(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chat_runtime_texts(episode: dict) -> list[str]:
    """Texts the chat window formats at run time: the battery percentage of each ChatWindow row ("{0}%" of (int)
    Parameter1, when numeric), the member count a group chat name gets ("(n)" for Parameter4 2..99,
    AdvChatWindowNameHelper.Format), the ellipsis of a truncated name (TruncateName) and the read count a group read
    label appends (" n" of the ChatTalk / ChatStamp / ChatRead Parameter1, AdvChatView.BuildReadText)."""
    out = ["..."] if any(c["cmd"] in CHAT_COMMANDS for c in episode["commands"]) else []
    for c in episode["commands"]:
        if c["cmd"] not in CHAT_COMMANDS or c.get("IgnoreData"):
            continue
        p1 = _number(c.get("Parameter1"))
        if c["cmd"] == "ChatWindow":
            members = _number(c.get("Parameter4"))
            if p1 is not None and abs(p1) < 2 ** 31:
                out.append(f"{int(p1)}%")
            if members is not None and members == int(members) and 2 <= members <= 99:
                out.append(f"({int(members)})")
        elif c["cmd"] in ("ChatTalk", "ChatStamp", "ChatRead") and p1 is not None and p1 == int(p1) and p1 >= 1:
            out.append(f" {int(p1)}")
    return out


def about(language: str) -> str:
    """ui.json `about`: what the file holds, in which client language."""
    return f"ADV front canvas UI data (UIAdvWidget + UIDefaultTalkWindow), {language}"


def language_doc(language: str, styles: textstyle.TextStyles | None = None) -> dict:
    """ui.json `language`: the LanguageMode and text field of `language`, with `styles` (fonts game) the
    LocalizeManager font lists and the font swap, and the line spacing LocalizeText applies."""
    mode = languages.mode(language)
    doc = {"mode": mode, "field": languages.column(language)[1:]}
    if styles is not None:
        doc.update({"fonts": styles.lang, "fontSwap": styles.swap})
    doc["lineSpacing"] = LANGUAGE_LINE_SPACING[mode]
    return doc


def shown_texts(episode: dict, master_ids: dict, field: str) -> list[str]:
    """The texts the episode shows in the text field `field` (e.g. "traditionalChinese"), tags stripped: Talk,
    Location and Subtitles lines with their ruby readings (<r=reading>), the speaker names of the Talk rows
    (TargetTextIDs; the unknown-speaker text for TargetStatus 1, joined names with the split text; none for
    TargetStatus 2) and the title. `master_ids`: AdvMasterIdSettings."""
    lines = []
    for c in episode["commands"]:
        if c["cmd"] in ("Talk", "Location", "Subtitles", "ChoiceSet") and c.get("lines"):
            lines.append(c["lines"][field])
            lines += RUBY.findall(c["lines"][field] or "")
    names = set()
    for c in episode["commands"]:
        if c["cmd"] == "Talk" and c.get("TargetName") and c.get("TargetStatus", 0) != 2 and c.get("AdvTextID"):
            ids = list(c.get("TargetTextIDs") or [])
            if c.get("TargetStatus", 0) == 1:
                ids = [master_ids["_unknownCharacterNameTextId"]]
            elif len(ids) > 1:
                ids.append(master_ids["_splitCharacterNameTextId"])
            for i in ids:
                row = episode["text"].get(i)
                if row is None:
                    raise KeyError(f"speaker text id {i} not in the episode text shard")
                names.add(row[field])
    for c in episode["commands"]:                  # chat rows: lines, window / sender names, stamp logs
        if c["cmd"] not in CHAT_COMMANDS or c.get("IgnoreData"):
            continue
        if c.get("lines"):
            lines.append(c["lines"][field])
        ids = list(c.get("TargetTextIDs") or [])
        if c["cmd"] == "ChatStamp" and c.get("TargetAssetName"):   # AdvChatStampCommand.GetStampLogText
            ids.append(c["TargetAssetName"].rsplit("/", 1)[-1] + "_log")
        for i in ids:
            row = episode["text"].get(i)
            if row is not None:
                names.add(row[field])
    if any(c["cmd"] in CHAT_COMMANDS for c in episode["commands"]):
        for k in ("_chatReadTextId", "_chatStampLogTextId"):
            row = episode["text"].get(master_ids.get(k))
            if row is not None:
                names.add(row[field])
    title = episode.get("title")
    if not title:
        raise RuntimeError("episode.json has no title lines (adv.extract)")
    return [_strip_tags(s) for s in lines + sorted(names) + [title[field]]] + chat_runtime_texts(episode)


def _game_fonts(ex: Exporter, styles: textstyle.TextStyles, episode: dict, out_nodes: list,
                field: str, master_ids: dict, ui_texts: list[str] = ()) -> tuple[dict, dict]:
    """`--fonts game`: the localized font / material of every text node (`text.localized`), the TMP font assets,
    the glyph coverage of the characters the episode shows (in the text field `field`; `master_ids`:
    AdvMasterIdSettings; `ui_texts`: texts of the UI itself) and the text materials.
    -> (materials, the inputs of tmpfont.export_fonts + coverage)."""
    from . import tmpfont
    fonts = tmpfont.FontSet(ex)
    used_text_nodes = [n for n in out_nodes if "text" in n]
    primaries = {}
    for n in used_text_nodes:
        t = n["text"]
        t["localized"] = textstyle.localize_text(n["path"], t["fontAsset"], t["material"], styles.swap, styles.lang,
                                                 styles.mode)
        primaries[t["localized"]["fontAsset"]] = None
    for fname in primaries:
        primaries[fname] = fonts.by_key(textstyle.font_dir(fname) + fname)

    # -- characters the episode shows ------------------------------------------
    shown = shown_texts(episode, master_ids, field) + list(ui_texts)
    chars = sorted({ord(ch) for s in shown for ch in s})

    primary = [primaries[p] for p in primaries]
    if len(primary) != 1:
        raise NotImplementedError(f"texts use {len(primary)} primary fonts")
    primary = primary[0]
    coverage, needed, runtime_units = tmpfont.glyph_coverage(fonts, primary, chars)
    # the underline character from the primary asset itself (baked, or generated as its dynamic asset would)
    hit = fonts.lookup(primary, UNDERLINE_CHARACTER)
    in_asset = bool(hit and hit[1] is primary and hit[0] in ("baked", "runtime"))
    if in_asset:
        (needed if hit[0] == "baked" else runtime_units).setdefault(primary.name, set()).add(UNDERLINE_CHARACTER)
    coverage["underline"] = {"font": primary.name, "character": chr(UNDERLINE_CHARACTER), "inAsset": in_asset}

    # -- materials ---------------------------------------------------------------
    materials, text_materials = tmpfont.text_materials(ex, fonts, [n["text"]["localized"] for n in used_text_nodes],
                                                       {primary.name}, needed, runtime_units)
    return materials, {"fonts": fonts, "primaries": {primary.name}, "needed": needed, "runtime": runtime_units,
                       "textMaterials": text_materials, "coverage": coverage}
