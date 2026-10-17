"""A synthetic story UI as an object table (content.storyui): the ADV widget and its talk windows, the chat phone, the
dialogs, UIManager, the letterbox and the settings, and the built-in LocalizeManager, GraphicsSettings, DOTweenSettings
and TMP_Settings. The font asset of the texts is fenced out of the table; their materials are in it."""

from nnnotes.content import storyui
from nnnotes.unity import fontsettings
from tests.helpers import livescene as ls
from tests.helpers.live2dmodel import ref
from tests.helpers.livenotes import comp, node, table

CAB = "CAB-ui"
WIDGET, PLAIN, BOTH_UI, EMB, DEFAULT, CHAT, UI_MANAGER, DIALOG_A, DIALOG_B = (f"{CAB}:{i}" for i in range(1, 10))
LETTERBOX = f"{CAB}:10"
CLIPS = [f"{CAB}:{i}" for i in range(11, 15)]
CONTROLLERS = [f"{CAB}:{i}" for i in range(15, 19)]
MAT_JA_DEFAULT, MAT_JA_OUTLINE = f"{CAB}:20", f"{CAB}:21"
FONT_JA = "CAB-font:5"  # the font asset of the Japanese texts: in a font bundle, so fenced
SETTINGS = "CAB-st:1"
GRAPHICS, LOCALIZE, DOTWEEN, TMP_SETTINGS = "globalgamemanagers:7", "CAB-lm:1", "CAB-dt:1", "CAB-tmp:1"
SHADER, SCRIPT = "CAB-sh:1", "CAB-m:1"
ADV_SCRIPT = "App.Runtime|App.Adv|"
LOCALIZE_SCRIPT = "App.Runtime|Fwk.Localization|LocalizeText"
WHITE = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
BLACK = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}
BUILTIN_ROOTS = [GRAPHICS, LOCALIZE, DOTWEEN, TMP_SETTINGS]
# catalog keys the story UI is planned from that its table does not hold: an embedded talk window whose plain key the
# catalog also has, and a key of another feature
CATALOG_EXTRA = ["EmbUI/Prefab/Parts/Adv/Talk/UIBoth", "Adv/Chat/Prefabs/win_7"]
MASTER_IDS = {k: 101 + i for i, k in enumerate(storyui.MASTER_ID_TEXTS)}
WINDOWS = {  # catalog key -> the talk window's GameObject
    "UI/Prefab/Parts/Adv/Talk/UIPlain": PLAIN,
    "UI/Prefab/Parts/Adv/Talk/UIBoth": BOTH_UI,
    "EmbUI/Prefab/Parts/Adv/Talk/UIEmb": EMB,
    storyui.WINDOW_KEY: DEFAULT,
}
LOCALIZE_JSON = {
    "$script": "Fwk|Fwk.Localization|LocalizeManager",
    "_dontDestroyOnload": 1,
    "_languageDataList": [
        {"_languageMode": 0, "_fontNames": ["MainJ", "Digits"], "_additionalFontNames": []},
        {"_languageMode": 2, "_fontNames": ["MainT", "Digits"], "_additionalFontNames": ["Extra"]},
    ],
    "_materialTypeList": ["Default", "Outline"],
}
# MasterText rows: the texts of the ids of MASTER_IDS in each language, and one row without a traditional Chinese text
MASTER_TEXTS = [
    {"_id": i, "_japanese": f"ja {i}", "_traditionalChinese": f"zh {i}", "_english": f"en {i}"}
    for i in (*MASTER_IDS.values(), 301, 302)
] + [{"_id": 999, "_traditionalChinese": None}]
# the master record of the traditional Chinese texts (the texts of the MASTER_IDS ids)
MASTER_RECORD = {"texts": {str(i): f"zh {i}" for i in (*MASTER_IDS.values(), 301, 302)}}


def tmp_fields(font: str, material: str, text: str) -> dict:
    """The serialized fields of a TMP text, its font asset and material as references (the fields textstyle reads)."""
    return {
        "m_Enabled": 1,
        "m_fontAsset": ref(font),
        "m_sharedMaterial": ref(material),
        "m_text": text,
        "m_isRichText": 1,
        "m_parseCtrlCharacters": 1,
        "m_fontSize": 36.0,
        "m_enableAutoSizing": 0,
        "m_fontSizeMin": 18.0,
        "m_fontSizeMax": 40.0,
        "m_charWidthMaxAdj": 0.0,
        "m_lineSpacingMax": 0.0,
        "m_fontStyle": 0,
        "m_fontWeight": 400,
        "m_HorizontalAlignment": 2,
        "m_VerticalAlignment": 512,
        "m_TextWrappingMode": 1,
        "m_overflowMode": 0,
        "m_margin": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0},
        "m_lineSpacing": 0.0,
        "m_paragraphSpacing": 0.0,
        "m_characterSpacing": 0.0,
        "m_wordSpacing": 0.0,
        "m_enableKerning": 0,
        "m_ActiveFontFeatures": [],
        "m_isRightToLeft": 0,
        "m_isOrthographic": 1,
        "m_fontColor": WHITE,
        "m_colorMode": 0,
        "m_enableVertexGradient": 0,
        "m_fontColorGradient": {},
        "m_overrideHtmlColors": 0,
    }


def material(name: str, outline: bool = False) -> dict:
    """A TMP distance-field material record: the values of the font settings' material (and of the Material object)."""
    floats = {"_GradientScale": 16.0, "_WeightNormal": 0.0, "_WeightBold": 0.0, "_FaceDilate": 0.0}
    colors, keywords = {}, []
    if outline:
        floats.update({"_OutlineWidth": 0.2, "_OutlineSoftness": 0.0})
        colors["_OutlineColor"] = BLACK
        keywords.append("OUTLINE_ON")
    return {"material": name, "keywords": keywords, "floats": floats, "colors": colors, "textures": {}}


def prefab(name: str, *components) -> dict:
    return {"name": name, "nodes": [node(name, *components)]}


def widget() -> dict:
    """UIAdvWidget: a text with an enabled LocalizeText (its key 101) and a text without a LocalizeText."""
    return {
        "name": "UIAdvWidget",
        "nodes": [
            node("UIAdvWidget"),
            node(
                "UIAdvWidget/Talk",
                comp("TextMeshProUGUI", 101, **tmp_fields(FONT_JA, MAT_JA_OUTLINE, "talk")),
                comp("MonoBehaviour", 102, LOCALIZE_SCRIPT, m_Enabled=1, _localizeEnabled=1, _masterTextID="101"),
            ),
            node("UIAdvWidget/Plain", comp("TextMeshProUGUI", 103, **tmp_fields(FONT_JA, MAT_JA_DEFAULT, "plain"))),
        ],
    }


def plain_window() -> dict:
    """The plain talk window: its text's LocalizeText is switched off."""
    return {
        "name": "UIPlain",
        "nodes": [
            node(
                "UIPlain",
                comp("TextMeshProUGUI", 111, **tmp_fields(FONT_JA, MAT_JA_DEFAULT, "window")),
                comp("MonoBehaviour", 112, LOCALIZE_SCRIPT, m_Enabled=1, _localizeEnabled=0, _masterTextID="0"),
            )
        ],
    }


def objects() -> dict:
    """{object id: (class, name, {role: JSON document | bytes})} of the story UI and its built-in objects."""
    out = {
        WIDGET: ("GameObject", "UIAdvWidget", {"json": widget()}),
        PLAIN: ("GameObject", "UIPlain", {"json": plain_window()}),
        BOTH_UI: ("GameObject", "UIBoth", {"json": prefab("UIBoth")}),
        EMB: ("GameObject", "UIEmb", {"json": prefab("UIEmb")}),
        DEFAULT: ("GameObject", "UIDefaultTalkWindow", {"json": prefab("UIDefaultTalkWindow")}),
        CHAT: ("GameObject", "UIAdvChatWidget", {"json": prefab("UIAdvChatWidget")}),
        UI_MANAGER: ("GameObject", "UIManager", {"json": prefab("UIManager")}),
        DIALOG_A: ("GameObject", "UIAdvSkipConfirmDialogWidget", {"json": prefab("UIAdvSkipConfirmDialogWidget")}),
        DIALOG_B: ("GameObject", "UICommonDialogWidget", {"json": prefab("UICommonDialogWidget")}),
        LETTERBOX: ("Sprite", "letterbox-image", {}),
        MAT_JA_DEFAULT: ("Material", "MainJ - Default", {"json": material("MainJ - Default")}),
        MAT_JA_OUTLINE: ("Material", "MainJ - Outline", {"json": material("MainJ - Outline", outline=True)}),
        SETTINGS: (
            "MonoBehaviour",
            "AdvMasterIdSettings",
            {"json": {"$script": ADV_SCRIPT + "AdvMasterIdSettings", **MASTER_IDS}},
        ),
        GRAPHICS: ("GraphicsSettings", None, {"json": {"m_AlwaysIncludedShaders": [ref(SHADER)]}}),
        LOCALIZE: ("MonoBehaviour", "LocalizeManager", {"json": LOCALIZE_JSON}),
        DOTWEEN: (
            "MonoBehaviour",
            "DOTweenSettings",
            {"json": {"$script": "DOTween.dll|DG.Tweening.Core|DOTweenSettings", "timeScale": 1.0}},
        ),
        TMP_SETTINGS: (
            "MonoBehaviour",
            "TMP Settings",
            {"json": {"$script": "Unity.TextMeshPro|TMPro|TMP_Settings", "m_defaultFontAsset": None}},
        ),
        SHADER: ls.shader("UI/Default"),
        SCRIPT: ("MonoScript", "AdvMasterIdSettings", {"json": {"m_ClassName": "AdvMasterIdSettings"}}),
    }
    for oid, name in zip(CLIPS, storyui.CLIP_KEYS):
        out[oid] = ("AnimationClip", name, {"json": {"name": name, "sampleRate": 60.0, "bindings": []}})
    for oid, name in zip(CONTROLLERS, storyui.CONTROLLER_KEYS):
        out[oid] = ("AnimatorController", name, {"json": {"name": name, "clips": []}})
    return out


def table_keys() -> dict:
    """{catalog key: root object ids} of the story UI's table: the keys storyui.keys gives for the catalog."""
    out = {
        storyui.WIDGET_KEY: [WIDGET],
        storyui.CHAT_WIDGET_KEY: [CHAT],
        storyui.UI_MANAGER_KEY: [UI_MANAGER],
        storyui.LETTERBOX_KEY: [LETTERBOX],
        storyui.MASTER_ID_SETTINGS_KEY: [SETTINGS],
        storyui.DIALOG_KEYS["UIAdvSkipConfirmDialogWidget"]: [DIALOG_A],
        storyui.DIALOG_KEYS["UICommonDialogWidget"]: [DIALOG_B],
    }
    out.update({key: [oid] for key, oid in zip(storyui.CLIP_KEYS.values(), CLIPS)})
    out.update({key: [oid] for key, oid in zip(storyui.CONTROLLER_KEYS.values(), CONTROLLERS)})
    out.update({key: [oid] for key, oid in WINDOWS.items()})
    return out


def catalog_keys() -> list[str]:
    """The keys of the catalog the story UI is planned from: the keys of the table and CATALOG_EXTRA."""
    return sorted({*table_keys(), *CATALOG_EXTRA})


def index() -> dict:
    """The catalog index fact of the planning (its locations' primary keys)."""
    return {"locations": [{"primaryKey": key} for key in catalog_keys()]}


def fenced() -> dict:
    return {FONT_JA: {"class": "TMP_FontAsset", "name": "MainJ SDF"}}


def table_doc(depot, objs: dict | None = None, keys: dict | None = None, extra_roots=()) -> dict:
    """The object table (nnnotes.objects/1) of the story UI, or of `objs` with the roots of `keys`: the built-in
    objects and `extra_roots` are roots as well, and the font asset is fenced."""
    doc = table(depot, table_keys() if keys is None else keys, objects() if objs is None else objs)
    doc["roots"] = sorted({*doc["roots"], *BUILTIN_ROOTS, *extra_roots})
    doc["fenced"] = fenced()
    return doc


def font_settings() -> fontsettings.FontSettings:
    """The font settings the texts draw with: the Japanese and the traditional Chinese font assets, and the default and
    outline materials of the Japanese font and the outline material of the traditional Chinese one."""
    face = {"m_Scale": 1.0, "m_PointSize": 100.0, "m_LineHeight": 120.0, "m_AscentLine": 90.0, "m_DescentLine": -30.0}
    fonts = {
        name: {"faceInfo": face, "normalSpacingOffset": 0.0, "boldSpacing": 7.0} for name in ("MainJ SDF", "MainT SDF")
    }
    materials = {
        "MainJ - Default": material("MainJ - Default"),
        "MainJ - Outline": material("MainJ - Outline", outline=True),
        "MainT - Outline": material("MainT - Outline", outline=True),
    }
    return fontsettings.FontSettings({"format": fontsettings.FORMAT, "fonts": fonts, "materials": materials})


def font_objects() -> tuple[dict, dict]:
    """Synthetic font assets and materials read while building text styles, with their catalog keys."""
    out, keys = {}, {}
    font_ids = {"MainJ SDF": FONT_JA, "MainT SDF": "CAB-font:6", "Digits SDF": "CAB-font:7", "Extra SDF": "CAB-font:8"}
    for i, (name, oid) in enumerate(font_ids.items()):
        base = name[:-4]
        mats = {}
        for j, kind in enumerate(("Default", "Outline")):
            mid = f"CAB-font:{20 + 2 * i + j}"
            mat = material(f"{base} - {kind}", outline=kind == "Outline")
            mat.update(
                {"shader": None, "invalidKeywords": [], "renderQueue": -1, "tags": {}, "disabledPasses": [], "ints": {}}
            )
            out[mid] = ("Material", mat["material"], {"json": mat})
            keys[fontsettings.font_dir(name) + mat["material"]] = [mid]
            mats[kind] = mid
        settings = font_settings().fonts["MainJ SDF"]
        doc = {
            "$script": "Unity.TextMeshPro|TMPro|TMP_FontAsset",
            "m_Name": name,
            "m_CharacterTable": [],
            "m_GlyphTable": [],
            "m_AtlasPopulationMode": 0,
            "m_AtlasTextures": [],
            "m_Material": ref(mats["Default"]),
            "m_FallbackFontAssetTable": [],
            "m_FontWeightTable": [],
            "m_SourceFontFile": None,
            "m_FaceInfo": {"m_FaceIndex": 0, **settings["faceInfo"]},
            "m_AtlasPadding": 15,
            "m_AtlasRenderMode": 4134,
            **{k: settings.get(k, 0) for k in fontsettings.STYLE_KEYS},
        }
        out[oid] = ("MonoBehaviour", name, {"json": doc})
        keys[fontsettings.font_dir(name) + name] = [oid]
    return out, keys


def chat_window() -> tuple[str, str, tuple]:
    """A chat window with two status keys and one empty key."""
    oid = "CAB-chat:1"
    doc = prefab(
        "win_7",
        comp(
            "MonoBehaviour",
            2,
            ADV_SCRIPT + "AdvChatWindow",
            _incomingCallStatusTextKey="301",
            _lockScreenStatusTextKey="302",
        ),
        comp(
            "MonoBehaviour",
            3,
            ADV_SCRIPT + "AdvChatWindow",
            _incomingCallStatusTextKey="301",
            _lockScreenStatusTextKey="",
        ),
    )
    return "Adv/Chat/Prefabs/win_7", oid, ("GameObject", "win_7", {"json": doc})
