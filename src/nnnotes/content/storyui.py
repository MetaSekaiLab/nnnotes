"""The ADV story UI of a product: what the ADV front canvas, its talk windows, the chat phone, the dialogs and the
settings draw with in one client language. One product (`ui`, ui.json format UI_FORMAT), read from a product's object
table (`Objects`, pipeline.assemble) the way storyscene reads the story scene:

    widget          the GameObject of UIAdvWidget (WIDGET_KEY); its prefab document holds the drawn hierarchy
    talkWindows     {window name: GameObject} of the talk windows of the catalog (window_keys); the default window
                    UIDefaultTalkWindow is always one of them
    chatWidget      the GameObject of the phone of the chat rows (UIAdvChatWidget)
    dialogs         {name: GameObject} of the skip confirm dialog and the common dialog (DIALOG_KEYS)
    uiManager       the GameObject of UIManager (its camera controller and the UI camera)
    letterbox       the Sprite of the letterbox bands
    clips           {name: AnimationClip} of the ADV animations (CLIP_KEYS)
    controllers     {name: AnimatorController} of the ADV animations (CONTROLLER_KEYS)
    settings        the AdvMasterIdSettings, DOTweenSettings, TMP_Settings and LocalizeManager objects
    graphics        {graphics: GraphicsSettings}
    language        the client language: its code, LanguageMode, text field and line spacing, the font names and
                    material types LocalizeManager holds for it (textstyle.language_fonts_json) and the font swap
    texts           {TMP component id: {localized, style}} of every TMP text of the prefabs above. `localized` is
                    LocalizeText's font asset, material and line spacing (textstyle.localize_text), null when the
                    text has no enabled LocalizeText; `style` is the text record (textstyle.text_style) with the font
                    settings read from the client's font assets
    textStyle       the language's field, the units and the line metrics of the font roles the texts use
    masterIdTexts   the AdvMasterIdSettings texts of MASTER_ID_TEXTS: {field: {id, text}}
    chatStatusTexts the incoming-call and lock-screen texts: {MasterText key: text}
    bindings        the animator bindings of the prefabs (livenotes.animator_bindings)
    shaders         {shader name: object id} (prefabs.shader_table)

A text's font asset and material are named by the object the table holds for them, or by the class and name the table
fences them with (Objects.fenced): a reference across the font fence carries only its file and path id.

Of the objects the document keeps all but the MonoScripts; of a shader its parsed form, variant index and the programs
of the player's graphics API; of a Mesh its typetree JSON and its arrays (prefabs.mesh_roles).

The master record is {"texts": {MasterText id: text in the language's text field}}.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..source import languages
from ..unity import fontsettings, textstyle
from ..unity.ids import object_id, parse_object_id
from . import adv
from .livenotes import animator_bindings
from .prefabs import (
    Objects,
    builtin_root,
    component_class,
    key_object,
    mesh_roles,
    mono_json,
    ref_id,
    script_class,
    shader_table,
)

UI_FORMAT = 1  # ui.json "format"
WIDGET_KEY = "EmbUI/Prefab/UIAdvWidget"
WINDOW_KEY = "EmbUI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow"  # the default talk window (embedded address)
CHAT_WIDGET_KEY = "EmbUI/Prefab/UIAdvChatWidget"
UI_MANAGER_KEY = "EmbCommon/Prefab/UIManager"
LETTERBOX_KEY = "Textures/letterbox-image"
MASTER_ID_SETTINGS_KEY = "EmbCommon/Adv/Settings/AdvMasterIdSettings"
DIALOG_KEYS = {
    "UIAdvSkipConfirmDialogWidget": "EmbUI/Prefab/UIAdvSkipConfirmDialogWidget",
    "UICommonDialogWidget": "EmbUI/Prefab/UICommonDialogWidget",
}
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
# AdvMasterIdSettings fields of the texts the drawn parts show: the video skip dialog, the skip confirm dialog and the
# interruption dialog
MASTER_ID_TEXTS = (
    "_skipVideoMessageTextId",
    "_skipButtonTextId",
    "_cancelButtonTextId",
    "_skipMessageTextId",
    "_continuousSkipMessageTextId",
    "_backEpisodeListButtonTextId",
    "_continueEpisodeButtonTextId",
    "_interruptionTitleTextId",
    "_interruptionMessageTextId",
    "_interruptionButtonTextId",
)
TEXT_CLASSES = ("TextMeshProUGUI", "RubyTextMeshProUGUI", "RubyEmojiTextMeshProUGUI")
# the built-in objects and the Resources.Load paths of the document (ProductStage.builtins, ProductStage.resources)
BUILTINS = ("GraphicsSettings", "MonoBehaviour:LocalizeManager")
RESOURCES = ("DOTweenSettings", "TMP Settings")
CHAT_STATUS_KEYS = ("_incomingCallStatusTextKey", "_lockScreenStatusTextKey")


def window_keys(catalog: Iterable[str]) -> dict[str, str]:
    """{talk window name: prefab key} of the talk windows of the catalog keys `catalog`: the names under the talk window
    prefix or its embedded form, each with its plain key when the catalog has that and else its embedded key (the
    rule of content.advui.window_key), and the default window."""
    keys = set(catalog)
    prefixes = (adv.TALK_WINDOW_PREFIX, adv.EMBEDDED_PREFIX + adv.TALK_WINDOW_PREFIX)
    names = {k[len(p) :] for k in keys for p in prefixes if k.startswith(p)} | {adv.DEFAULT_TALK_WINDOW}
    out = {}
    for name in sorted(names):
        if name == adv.DEFAULT_TALK_WINDOW:
            out[name] = WINDOW_KEY
        else:
            address = adv.TALK_WINDOW_PREFIX + name
            out[name] = address if address in keys else adv.EMBEDDED_PREFIX + address
    return out


def keys(catalog: Iterable[str]) -> list[str]:
    """The catalog keys of the story UI in the catalog `catalog` (its keys): the widget, the chat widget, UIManager, the
    letterbox, the master id settings, the dialogs, the animations and the talk windows (window_keys), sorted."""
    held = set(catalog)
    return sorted(
        {
            WIDGET_KEY,
            CHAT_WIDGET_KEY,
            *(k for k in (UI_MANAGER_KEY,) if k in held),
            LETTERBOX_KEY,
            MASTER_ID_SETTINGS_KEY,
            *DIALOG_KEYS.values(),
            *CLIP_KEYS.values(),
            *CONTROLLER_KEYS.values(),
            *window_keys(catalog).values(),
        }
    )


def font_keys(catalog: Iterable[str]) -> list[str]:
    """The keys of font assets and materials under the client's font folders, sorted."""
    return sorted({k for k in catalog if k.startswith((fontsettings.FONT_PREFIX, fontsettings.FONT_ADDRESS))})


def chat_window_keys(catalog: Iterable[str]) -> list[str]:
    """The chat-window prefab keys from which the shared status-text keys are read, sorted."""
    return sorted({k for k in catalog if k.startswith(adv.CHAT_WINDOW_PREFIX)})


def chat_status_texts(objects: Objects, texts: dict) -> dict[str, str]:
    """The incoming-call and lock-screen texts of the chat windows in `objects`, resolved in `texts`."""
    wanted = set()
    for key in chat_window_keys(objects.keys()):
        prefab = key_object(objects, key, "GameObject")
        for node in objects.json(prefab)["nodes"]:
            for comp in node["components"]:
                if component_class(comp) == "AdvChatWindow":
                    wanted.update(str(comp["fields"][k]) for k in CHAT_STATUS_KEYS if comp["fields"].get(k))
    return {k: texts[k] for k in sorted(wanted)}


def ui(
    objects: Objects,
    language: str,
    master: dict,
    fonts: fontsettings.FontSettings,
    *,
    status_texts: dict[str, str] | None = None,
) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(ui.json, the object ids the player reads, {object id: the roles of its files it reads}) of the story UI in the
    client language `language` (module documentation)."""
    code = languages.check(language)
    mode, field = languages.mode(code), languages.column(code)[1:]
    localize = script_root(objects, "LocalizeManager")
    lang = textstyle.language_fonts_json(mono_json(objects, localize))
    styles = _Styles(lang, mode, fonts)
    widget = key_object(objects, WIDGET_KEY, "GameObject")
    windows = {name: key_object(objects, key, "GameObject") for name, key in window_keys(objects.keys()).items()}
    chat = key_object(objects, CHAT_WIDGET_KEY, "GameObject")
    ui_manager = key_object(objects, UI_MANAGER_KEY, "GameObject")
    dialogs = {name: key_object(objects, key, "GameObject") for name, key in DIALOG_KEYS.items()}
    texts = {}
    for prefab in (widget, *windows.values(), chat, *dialogs.values(), ui_manager):
        texts.update(_texts(objects, prefab, styles))
    settings = key_object(objects, MASTER_ID_SETTINGS_KEY, "AdvMasterIdSettings")
    graphics = builtin_root(objects, "GraphicsSettings")
    always = (ref_id(r) for r in objects.json(graphics).get("m_AlwaysIncludedShaders", ()))
    shaders, roles = shader_table(objects, found=[o for o in always if o is not None])
    doc = {
        "format": UI_FORMAT,
        "language": {
            "code": code,
            "mode": mode,
            "field": field,
            "lineSpacing": textstyle.LANGUAGE_LINE_SPACING[mode],
            "fontNames": lang["languages"][mode]["fontNames"],
            "additionalFontNames": lang["languages"][mode]["additionalFontNames"],
            "materialTypes": lang["materialTypes"],
            "fontSwap": styles.swap,
        },
        "widget": widget,
        "talkWindows": windows,
        "chatWidget": chat,
        "dialogs": dialogs,
        "uiManager": ui_manager,
        "letterbox": key_object(objects, LETTERBOX_KEY, "Sprite"),
        "clips": {name: key_object(objects, key, "AnimationClip") for name, key in CLIP_KEYS.items()},
        "controllers": {name: key_object(objects, key, "AnimatorController") for name, key in CONTROLLER_KEYS.items()},
        "settings": {
            "masterIdSettings": settings,
            "dotween": script_root(objects, "DOTweenSettings"),
            "tmpSettings": script_root(objects, "TMP_Settings"),
            "localizeManager": localize,
        },
        "graphics": {"graphics": graphics},
        "texts": texts,
        "textStyle": styles.summary(),
        "masterIdTexts": master_id_texts(objects.json(settings), master["texts"]),
        "chatStatusTexts": status_texts if status_texts is not None else chat_status_texts(objects, master["texts"]),
        "bindings": animator_bindings(objects),
        "shaders": shaders,
    }
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    return doc, keep, roles


def master_id_texts(settings: dict, texts: dict) -> dict:
    """masterIdTexts: {AdvMasterIdSettings field of MASTER_ID_TEXTS: {id (MasterText id), text}}; `texts` is the master
    record's {MasterText id: text} (JSON keys are strings). A missing id raises KeyError."""
    return {k: {"id": settings[k], "text": texts[str(settings[k])]} for k in MASTER_ID_TEXTS}


class _Styles:
    """The text records of one language: LocalizeText's resolution of a text, the style values of its material, and the
    line metrics of the font roles the texts use."""

    def __init__(self, lang: dict, mode: int, fonts: fontsettings.FontSettings):
        self.lang, self.mode, self.fonts = lang, mode, fonts
        self.swap = textstyle.font_swap(lang, mode)
        self.roles: dict[str, dict] = {}
        self._styles: dict[tuple, dict] = {}

    def _face(self, font_asset: str) -> tuple[dict, dict]:
        f = self.fonts.font(font_asset)
        return f["faceInfo"], {k: f[k] for k in ("normalSpacingOffset", "boldSpacing")}

    def record(self, path: str, comp: dict, localize: dict | None) -> dict:
        """{localized, style} of the TMP text `comp` (serialized fields, font and material named) with its LocalizeText
        `localize` (or None)."""
        fa, mat = comp["m_fontAsset"]["name"], comp["m_sharedMaterial"]["material"]
        localized = None
        if localize and localize["m_Enabled"] and localize["_localizeEnabled"]:
            localized = textstyle.localize_text(path, fa, mat, self.swap, self.lang, self.mode)
            fa, mat = localized["fontAsset"], localized["material"]
        if (fa, mat) not in self._styles:
            face, _ = self._face(fa)
            self._styles[(fa, mat)] = textstyle.material_style(
                self.fonts.material(fa, mat), face["m_PointSize"], face["m_Scale"]
            )
        style = textstyle.text_style(comp, localize, self.lang, self._styles[(fa, mat)], self.mode)
        if style["fontRole"] not in self.roles:
            face, extra = self._face(fa)
            self.roles[style["fontRole"]] = textstyle.face_metrics(face, extra)
        return {"localized": localized, "style": style}

    def summary(self) -> dict:
        return {"language": textstyle.LANGUAGE_FIELDS[self.mode], "units": textstyle.UNITS, "roles": self.roles}


def _texts(objects: Objects, prefab: str, styles: _Styles) -> dict[str, dict]:
    """{TMP component id: {localized, style}} of the TMP texts of the prefab document of GameObject `prefab`.
    A component id is the prefab's file and the component's path id."""
    file, _ = parse_object_id(prefab)
    out = {}
    for n in objects.json(prefab)["nodes"]:
        tmp = [c for c in n["components"] if component_class(c) in TEXT_CLASSES]
        loc = [c for c in n["components"] if component_class(c) == "LocalizeText"]
        if not tmp:
            continue
        if len(tmp) > 1 or len(loc) > 1:
            raise ValueError(f"{n['path']}: {len(tmp)} TMP texts and {len(loc)} LocalizeText components")
        comp = {"class": component_class(tmp[0]), **tmp[0]["fields"]}
        comp["m_fontAsset"] = {"name": _name(objects, comp["m_fontAsset"])}
        comp["m_sharedMaterial"] = {"material": _name(objects, comp["m_sharedMaterial"])}
        localize = {"class": "LocalizeText", **loc[0]["fields"]} if loc else None
        out[object_id(file, int(tmp[0]["pathId"]))] = styles.record(n["path"], comp, localize)
    return out


def _name(objects: Objects, ref) -> str:
    """The name of the object a reference names: from the table, or from the class and name the table fences it with."""
    oid = ref_id(ref)
    if oid is None:
        raise ValueError(f"not a reference to an object: {ref!r}")
    hit = objects.info(oid) if oid in objects else objects.fenced(oid)
    if hit is None or not hit.get("name"):
        raise ValueError(f"{oid}: no name in the object table")
    return hit["name"]


def script_root(objects: Objects, script: str) -> str:
    """The one MonoBehaviour root of the script class `script` (ValueError otherwise)."""
    hits = [
        o
        for o in objects.roots
        if o in objects and objects.info(o).get("class") == "MonoBehaviour" and script_class(objects, o) == script
    ]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} {script} roots")
    return hits[0]
