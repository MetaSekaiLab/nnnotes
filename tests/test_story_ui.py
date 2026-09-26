"""The story UI export (advui.py) on synthetic prefab nodes and episode rows: the records of the screen canvases,
views, camera and video buttons, the widget's canvases, the texts whose characters the game fonts keep, the
AdvMasterIdSettings texts, the chat window texts and the emoji sequence table. Nothing here comes from game data."""
import json

import pytest

from nnnotes import advui

WHITE = {"spriteRef": "cab:1", "name": "WhiteRect"}


def image(sprite=WHITE, material=None):
    return {"type": "MonoBehaviour", "class": "Image", "m_Enabled": 1, "m_Color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
            "m_Type": 0, "m_PreserveAspect": 0, "m_FillCenter": 1, "m_FillMethod": 4, "m_FillAmount": 1.0,
            "m_UseSpriteMesh": 0, "m_PixelsPerUnitMultiplier": 1.0, "m_Maskable": 1, "m_Sprite": sprite,
            "m_Material": material}


def canvas(camera=None, mode=1, order=302):
    return {"type": "Canvas", "m_Enabled": 1, "m_RenderMode": mode, "m_SortingOrder": order,
            "m_VertexColorAlwaysGammaSpace": True, "m_AdditionalShaderChannelsFlag": 25, "m_OverrideSorting": False,
            "m_PixelPerfect": False, "m_PlaneDistance": 100.0, "m_Camera": camera}


def scaler(cls):
    c = {"type": "MonoBehaviour", "class": cls, "m_Enabled": 1, "m_UiScaleMode": 1, "m_ReferencePixelsPerUnit": 100.0,
         "m_ScaleFactor": 1.0, "m_ReferenceResolution": {"x": 1920.0, "y": 1080.0}, "m_ScreenMatchMode": 0,
         "m_MatchWidthOrHeight": 0.0}
    if cls == "ClampedCanvasScaler":
        c["_maxAspectThreshold"] = 2.1666667461395264
    return c


def test_canvases_keep_their_records_and_add_the_camera_plane():
    base = {"m_Enabled", "m_RenderMode", "m_SortingOrder", "m_VertexColorAlwaysGammaSpace",
            "m_AdditionalShaderChannelsFlag", "m_OverrideSorting", "m_PixelPerfect"}
    front = advui._layout_components({"components": [canvas(), scaler("CanvasScaler")]})
    assert set(front["canvas"]) == base | {"m_PlaneDistance"}      # screen space - camera without a camera
    assert "class" not in front["canvasScaler"]
    overlay = advui._layout_components({"components": [canvas(mode=0)]})
    assert set(overlay["canvas"]) == base
    still = advui._layout_components({"components": [
        canvas({"component": "Camera", "gameObject": "W/VideoAndStillCamera"}), scaler("ClampedCanvasScaler")]})
    assert still["canvas"]["camera"] == "W/VideoAndStillCamera" and still["canvas"]["m_PlaneDistance"] == 100.0
    assert still["canvasScaler"]["class"] == "ClampedCanvasScaler"
    assert still["canvasScaler"]["_maxAspectThreshold"] == pytest.approx(2.1666667)


def test_views_raw_images_buttons_and_safe_area_anchors():
    rec = advui._layout_components({"components": [
        {"type": "MonoBehaviour", "class": "AdvStillView", "m_Enabled": 1,
         "_overlay": {"component": "MonoBehaviour", "gameObject": "S/V/Overlay", "class": "Image"},
         "_background": {"component": "MonoBehaviour", "gameObject": "S/V/Background", "class": "Image"},
         "_target": {"transform": "S/V/Target"}},
        {"type": "MonoBehaviour", "class": "RawImage", "m_Enabled": 1, "m_Color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
         "m_UVRect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}, "m_RaycastTarget": 0, "m_Maskable": 0,
         "m_Texture": None, "m_Material": None},
        {"type": "MonoBehaviour", "class": "UISafeAreaEdgeAnchor", "m_Enabled": 1,
         "_left": {"_target": 1, "_anchor": 0, "_offset": 0.0}, "_right": {"_target": 1, "_anchor": 2, "_offset": 0.0},
         "_top": {"_target": 1, "_anchor": 2, "_offset": 0.0}, "_bottom": {"_target": 1, "_anchor": 0, "_offset": 0.0}},
        {"type": "MonoBehaviour", "class": "ButtonImageState", "_normaledSprite": {"spriteRef": "cab:2", "name": "stop"},
         "_selectedSprite": {"spriteRef": "cab:3", "name": "play"},
         "_target": {"component": "MonoBehaviour", "gameObject": "M/Pause/Icon", "class": "Image"}},
    ]})
    assert rec["stillView"] == {"enabled": 1, "_overlay": "S/V/Overlay", "_background": "S/V/Background",
                                "_target": "S/V/Target"}
    assert rec["rawImage"]["texture"] is None and rec["rawImage"]["m_UVRect"]["width"] == 1.0
    assert rec["safeAreaEdgeAnchor"]["_right"] == {"_target": 1, "_anchor": 2, "_offset": 0.0}
    assert rec["buttonImageState"] == {"normal": "stop", "selected": "play", "selectedRef": "cab:3",
                                       "target": "M/Pause/Icon"}
    plain = advui._layout_components({"components": [
        {"type": "MonoBehaviour", "class": "ButtonImageState", "_normaledSprite": None, "_selectedSprite": None,
         "_target": None}, {"type": "MonoBehaviour", "class": "AdvFrameView", "m_Enabled": 1}]})
    assert plain == {"buttonImageState": {"normal": None}, "frameView": {"enabled": 1}}


def test_camera_record():
    widget = [{"path": "W/Cam", "active": True, "components": [
        {"type": "Camera", **{k: 0 for k in advui.CAMERA_FIELDS}, "m_Iso": 200},
        {"type": "MonoBehaviour", "class": "UniversalAdditionalCameraData", **{k: 1 for k in advui.CAMERA_DATA_FIELDS}}]}]
    cam = advui._camera(widget, "W/Cam")
    assert cam["path"] == "W/Cam" and set(cam["camera"]) == set(advui.CAMERA_FIELDS) and "m_Iso" not in cam["camera"]
    assert set(cam["additionalCameraData"]) == set(advui.CAMERA_DATA_FIELDS)
    assert {"m_VolumeFrameworkUpdateModeOption", "m_Dithering", "m_StopNaN"} <= set(cam["additionalCameraData"])
    with pytest.raises(RuntimeError, match="no node"):
        advui._camera(widget, "W/Other")


def test_shown_texts_keep_subtitles_and_ruby_readings():
    lines = lambda s: {"japanese": s, "english": s}  # noqa: E731
    episode = {"commands": [{"cmd": "Talk", "lines": lines("<r=よみ>読</r>み"), "TargetName": "a", "AdvTextID": "t",
                             "TargetTextIDs": ["n"]},
                            {"cmd": "Subtitles", "lines": lines("字幕")}, {"cmd": "Subtitles"},
                            {"cmd": "Bgm", "lines": lines("no")}],
               "text": {"n": lines("名")}, "title": lines("題")}
    got = advui.shown_texts(episode, {"_unknownCharacterNameTextId": "u", "_splitCharacterNameTextId": "s"},
                            "japanese")
    assert got == ["読み", "よみ", "字幕", "名", "題"]


def test_master_id_texts(tmp_path):
    settings = {k: f"id_{i}" for i, k in enumerate(advui.MASTER_ID_TEXTS)}
    settings.update({"_skipVideoMessageTextId": "ui_skip_movie", "_skipButtonTextId": "ui_skip",
                     "_cancelButtonTextId": "ui_cancel", "_unknownCharacterNameTextId": "x"})
    rows = [{"_id": i, "_japanese": f"ja {i}", "_english": f"en {i}"} for i in [*settings.values(), "y"]]
    (tmp_path / "MasterText.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    got = advui.master_id_texts(settings, tmp_path, "english")
    assert set(got) == set(advui.MASTER_ID_TEXTS) and "_unknownCharacterNameTextId" not in got
    assert got["_skipVideoMessageTextId"] == {"id": "ui_skip_movie", "text": "en ui_skip_movie"}
    assert got["_interruptionButtonTextId"]["text"].startswith("en id_")
    with pytest.raises(KeyError, match="not in MasterText"):
        advui.master_id_texts({**settings, "_skipButtonTextId": "gone"}, tmp_path, "english")


def ref(path, component="MonoBehaviour", cls="UIButton"):
    return {"component": component, "gameObject": path, "class": cls}


def test_menu_and_chat_view_records():
    menu = {"type": "MonoBehaviour", "class": "AdvMenuView", "m_Enabled": 1, "_fadeDuration": 0.2,
            **{k: ref(f"M/{k}") for k in advui.MENU_VIEW_REFS},
            "_menuButtonsBackground": {"transform": "M/Back"},
            "_fastForwardNormalSprite": {"spriteRef": "cab:5", "name": "fast_off"},
            "_fastForwardOnePointFiveSprite": {"spriteRef": "cab:6", "name": "fast15"},
            "_fastForwardOnePointSevenSprite": None,
            "_fastForwardDoubleSprite": {"spriteRef": "cab:8", "name": "fast2"}}
    node = advui._layout_components({"components": [menu]})
    rec = node["menuView"]
    assert rec["_skipButton"] == "M/_skipButton" and rec["_menuButtonsBackground"] == "M/Back"
    assert (rec["_fastForwardNormalSprite"], rec["_fastForwardOnePointSevenSprite"]) == ("fast_off", None)
    assert rec["_fadeDuration"] == 0.2 and node["_spriteRefs"] == ["cab:5", "cab:6", "cab:8"]
    assert set(rec) == {"enabled", "_fadeDuration", *advui.MENU_VIEW_REFS, *advui.MENU_VIEW_SPRITES}
    chat = {"type": "MonoBehaviour", "class": "AdvChatView", "m_Enabled": 1,
            **{k: 0.25 for k in advui.CHAT_VIEW_FIELDS}, "_windowParentRect": {"transform": "C/Canvas/View/Target"}}
    rec = advui._layout_components({"components": [chat]})["chatView"]
    assert rec == {"enabled": 1, **{k: 0.25 for k in advui.CHAT_VIEW_FIELDS},
                   "_windowParentRect": "C/Canvas/View/Target"}


def test_widget_canvases():
    widget = [{"path": "W", "components": []},
              {"path": "W/Block", "components": [canvas(order=300)]},
              {"path": "W/Front", "components": [canvas(mode=1, order=304)]},
              {"path": "W/Letter", "components": [canvas(mode=0, order=0)]}]
    comp = {"_canvasSortOrder": 99999, "_canvases": [{"component": "Canvas", "gameObject": p}
                                                      for p in ("W/Block", "W/Front", "W/Letter")]}
    assert advui.widget_canvases(widget, comp) == {"canvasSortOrder": 99999, "canvases": [
        {"path": "W/Block", "sortingOrder": 300}, {"path": "W/Front", "sortingOrder": 304},
        {"path": "W/Letter", "sortingOrder": 0}]}
    with pytest.raises(RuntimeError, match="0 Canvas"):
        advui.widget_canvases(widget, {**comp, "_canvases": [{"component": "Canvas", "gameObject": "W"}]})


def test_emoji_sequence_keys():
    key = advui.emoji_sequence_key
    assert key("1f636-200d-1f32b-fe0f", 0x1F636) == "\U0001F636\u200D\U0001F32B\uFE0F"
    assert key("2764-FE0F", 0x2764) == "\u2764\uFE0F"                       # lower case, padded parts
    assert key("Emoji/1f590-fe0f.png", 0x1F590) == "\U0001F590\uFE0F"      # file name without extension
    assert key("0001f600", 0x1F600) is None                                  # the sprite's own code point
    with pytest.raises(NotImplementedError, match="not a code point"):
        key("d83d-de00", 0)


class FakeObj:
    def __init__(self, tree):
        self.tree = tree

    def read_typetree(self):
        return self.tree


def test_emoji_sprites():
    tree = {"m_Name": "Emoji", "fallbackSpriteAssets": [],
            "spriteInfoList": [{"name": "1f600", "unicode": 0x1F600}, {"name": "2764-fe0f", "unicode": 0x2764},
                               {"name": "2764-FE0F", "unicode": 0x2764}, {"name": "", "unicode": 0}],
            "m_SpriteCharacterTable": [{"m_Unicode": 0x2764}, {"m_Unicode": 0x1F600}, {"m_Unicode": 0x2764}]}

    class Ex:
        def key_object(self, key):
            assert key == advui.EMOJI_SPRITE_ASSET_KEY
            return FakeObj(tree)
    nodes = [{"components": [{"class": "RubyEmojiTextMeshProUGUI", "m_spriteAsset": None}]}]
    assert advui.emoji_sprites(Ex(), nodes) == {"spriteAsset": "Emoji", "address": advui.EMOJI_SPRITE_ASSET_KEY,
                                                "sequences": {"\u2764\uFE0F": "2764-fe0f"},
                                                "characters": [0x2764, 0x1F600]}
    own = [{"components": [{"class": "RubyEmojiTextMeshProUGUI", "m_spriteAsset": {"name": "Own"}}]}]
    with pytest.raises(NotImplementedError, match="own sprite asset"):
        advui.emoji_sprites(Ex(), own)


LANG = {"languages": {0: {"fontNames": ["JA-R", "JA-N"], "additionalFontNames": ["CHAT"]},
                      1: {"fontNames": ["EN-R", "EN-N"], "additionalFontNames": ["EN-CHAT"]},
                      4: {"fontNames": ["KO-R", "KO-N"], "additionalFontNames": []}},
        "materialTypes": ["Default", "Outline"]}


def test_chat_localize():
    """TryGetFontIndex over the Japanese font names and additional fonts, the font of that index among the
    language's loaded fonts (index out of range: font 0), the material of the type."""
    assert advui.chat_localize("CHAT SDF", "CHAT - Default", LANG, 1) == {
        "fontAsset": "EN-CHAT SDF", "material": "EN-CHAT - Default", "lineSpacing": -100.0}
    assert advui.chat_localize("CHAT SDF", "CHAT - Outline", LANG, 4)["fontAsset"] == "KO-R SDF"
    assert advui.chat_localize("JA-N SDF", "JA-N - Outline", LANG, 0) == {
        "fontAsset": "JA-N SDF", "material": "JA-N - Outline", "lineSpacing": 0.0}
    with pytest.raises(NotImplementedError, match="lookup table"):
        advui.chat_localize("Other SDF", "Other - Default", LANG, 1)
    with pytest.raises(NotImplementedError, match="material type"):
        advui.chat_localize("CHAT SDF", "CHAT - Glow", LANG, 1)


def lines(s):
    return {"japanese": s, "english": s}


def chat_episode():
    return {"commands": [
        {"cmd": "ChatWindow", "TargetTextIDs": ["w"], "Parameter1": "80.9", "Parameter4": "3"},
        {"cmd": "ChatTalk", "lines": lines("やあ"), "TargetTextIDs": ["s"], "Parameter1": "2"},
        {"cmd": "ChatStamp", "TargetAssetName": "Stamp/st_01", "TargetTextIDs": ["s"], "Parameter1": "1"},
        {"cmd": "ChatRead", "Parameter1": "x"},
        {"cmd": "ChatTalk", "lines": lines("無視"), "IgnoreData": 1, "Parameter1": "9"}],
        "text": {"w": lines("窓"), "s": lines("送"), "st_01_log": lines("スタンプ"), "60001": lines("既読")},
        "title": lines("題"),
        "resources": [{"kind": "chatwindow", "address": "Adv/Chat/Prefabs/Line", "present": True},
                      {"kind": "chaticon", "address": "Adv/Chat/Icon/a", "present": True}]}


def test_chat_runtime_texts_and_shown_chat_rows():
    ep = chat_episode()
    assert advui.chat_runtime_texts(ep) == ["...", "80%", "(3)", " 2", " 1"]
    assert advui.chat_runtime_texts({"commands": [{"cmd": "Talk"}]}) == []
    got = advui.shown_texts(ep, {"_unknownCharacterNameTextId": "u", "_splitCharacterNameTextId": "p",
                                 "_chatReadTextId": "60001", "_chatStampLogTextId": "60004"}, "japanese")
    assert got == ["やあ", *sorted(["窓", "送", "スタンプ", "既読"]), "題", "...", "80%", "(3)", " 2", " 1"]
    assert advui.chat_windows(ep) == {"Line": "Adv/Chat/Prefabs/Line"}


def tmp_text(font, material, text=""):
    return {"type": "MonoBehaviour", "class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"name": font},
            "m_sharedMaterial": {"material": material}, "m_text": text, "m_isRichText": 1,
            "m_parseCtrlCharacters": 1, "m_fontSize": 24.0, "m_enableAutoSizing": 0, "m_fontSizeMin": 18.0,
            "m_fontSizeMax": 72.0, "m_charWidthMaxAdj": 0.0, "m_lineSpacingMax": 0.0, "m_fontStyle": 0,
            "m_fontWeight": 400, "m_HorizontalAlignment": 1, "m_VerticalAlignment": 256, "m_TextWrappingMode": 1,
            "m_overflowMode": 0, "m_margin": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}, "m_lineSpacing": 3.0,
            "m_paragraphSpacing": 0.0, "m_characterSpacing": 0.0, "m_wordSpacing": 0.0, "m_enableKerning": 1,
            "m_isRightToLeft": 0, "m_isOrthographic": 0, "m_fontColor": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
            "m_colorMode": 3, "m_enableVertexGradient": 0, "m_overrideHtmlColors": 0}


def localize(enabled=1, key="0"):
    return {"type": "MonoBehaviour", "class": "LocalizeText", "m_Enabled": enabled, "_localizeEnabled": enabled,
            "_masterTextID": key}


def test_chat_texts(monkeypatch):
    window = [{"path": "Line", "components": [{"class": "AdvChatWindow", "_incomingCallStatusTextKey": "call",
                                               "_lockScreenStatusTextKey": ""}]},
              {"path": "Line/Top/Name", "components": [tmp_text("CHAT SDF", "CHAT - Default"), localize()]},
              {"path": "Line/Top/Percent", "components": [tmp_text("JA-N SDF", "JA-N - Outline", "100%")]}]
    keys = []

    class Cat:
        def has(self, key):
            return key.startswith("Font/")

    class Ex:
        cat = Cat()

        def prefab(self, address):
            assert address == "Adv/Chat/Prefabs/Line"
            return {"nodes": window}

        def key_object(self, key):
            keys.append(key)
            return FakeObj({"m_FaceInfo": {"m_PointSize": 40.0, "m_Scale": 1.0}})

        def material(self, o):
            return {"floats": {}, "colors": {}, "keywords": []}
    monkeypatch.setattr(advui.textstyle, "material_style", lambda m, point, scale: {"face": {"point": point}})
    got, status = advui.chat_texts(Ex(), LANG, 1, chat_episode())
    assert status == ["call"]
    name, pct = got["Line"]["Line/Top/Name"], got["Line"]["Line/Top/Percent"]
    assert name["localizeText"] == {"enabled": 1, "localizeEnabled": 1}
    assert name["textStyle"]["fontRole"] == "font2" and name["textStyle"]["localized"] is True
    assert name["textStyle"]["lineSpacing"]["applied"] == -100.0 and name["textStyle"]["face"] == {"point": 40.0}
    assert pct["localizeText"] is None and pct["textStyle"]["fontRole"] == "number"
    assert pct["textStyle"]["lineSpacing"]["applied"] == 3.0
    # the localized font's folder: the embedded copy is not in this catalog, so LocalizeManager's Font/
    assert keys == ["Font/EN-CHAT/EN-CHAT SDF", "Font/EN-CHAT/EN-CHAT - Default", "Font/JA-N/JA-N SDF",
                    "Font/JA-N/JA-N - Outline"]
    assert advui.font_key(type("C", (), {"has": lambda self, k: True})(), "A SDF", "A - Default") == \
        "EmbFont/A/A - Default"


def test_behaviours_record_every_serialized_field():
    """BEHAVIOURS components: every field but the object header, references as node paths, sprites as names (the
    references collected for the UI textures), assets and materials as names; several components of a class in
    order; a node with several ButtonImageState components has them there instead of `buttonImageState`."""
    sprite = {"spriteRef": "cab:9", "name": "sp_voice"}
    comps = [
        {"type": "MonoBehaviour", "class": "UIButton", "m_Enabled": 1, "m_GameObject": {"m_PathID": 1},
         "m_Script": {"m_PathID": 2}, "m_Name": "", "_initialButtonState": 0,
         "_buttonStates": [ref("B/State"), ref("B/State2", cls="ButtonImageState")],
         "_buttonAnimation": ref("B", cls="ButtonAnimationScale"),
         "m_OnClick": {"m_PersistentCalls": {"m_Calls": [{"m_Target": ref("B/T", cls="Image"), "m_MethodName": "x"}]}}},
        {"type": "MonoBehaviour", "class": "ButtonActiveState", "m_Enabled": 1, "_target": {"gameObject": "B/On"},
         "_selectedActive": 1},
        {"type": "MonoBehaviour", "class": "ButtonActiveState", "m_Enabled": 0, "_target": None, "_selectedActive": 0},
        {"type": "MonoBehaviour", "class": "ButtonImageState", "m_Enabled": 1, "_target": ref("B/Icon", cls="Image"),
         "_normaledSprite": sprite, "_selectedSprite": None},
        {"type": "MonoBehaviour", "class": "ButtonImageState", "m_Enabled": 1, "_target": ref("B/Frame", cls="Image"),
         "_normaledSprite": {"spriteRef": "cab:10", "name": "frame"}, "_selectedSprite": sprite},
        {"type": "MonoBehaviour", "class": "SimpleRaycastTarget", "m_Enabled": 1, "m_Material": None,
         "m_RaycastPadding": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}},
        {"type": "MonoBehaviour", "class": "LocalizeSpriteEvent", "m_Enabled": 1,
         "m_LocalizedAssetReference": {"m_TableReference": {"m_TableCollectionName": "GUID:abc"},
                                       "m_TableEntryReference": {"m_KeyId": 7, "m_Key": ""}}},
        {"type": "MonoBehaviour", "class": "GraphicRaycaster", "m_Enabled": 1},
    ]
    rec = advui._layout_components({"components": comps})
    b = rec["behaviours"]
    assert set(b) == {"UIButton", "ButtonActiveState", "ButtonImageState", "SimpleRaycastTarget", "LocalizeSpriteEvent"}
    assert b["UIButton"] == [{"m_Enabled": 1, "_initialButtonState": 0, "_buttonStates": ["B/State", "B/State2"],
                              "_buttonAnimation": "B", "m_OnClick": {"m_PersistentCalls": {"m_Calls": [
                                  {"m_Target": "B/T", "m_MethodName": "x"}]}}}]
    assert [x["_target"] for x in b["ButtonActiveState"]] == ["B/On", None]
    assert [x["_normaledSprite"] for x in b["ButtonImageState"]] == ["sp_voice", "frame"]
    assert "buttonImageState" not in rec and rec["_spriteRefs"] == ["cab:9", "cab:10", "cab:9"]
    assert b["LocalizeSpriteEvent"][0]["m_LocalizedAssetReference"]["m_TableEntryReference"]["m_KeyId"] == 7
    one = advui._layout_components({"components": comps[3:4]})
    assert one["buttonImageState"] == {"normal": "sp_voice"} and "behaviours" not in one
    assert advui._plain({"asset": "UIPictogramCatalog", "name": "UIPictogramCatalog", "_list": []}, []) == \
        "UIPictogramCatalog"
    assert advui._plain({"material": "M", "shader": {"shader": "UI/Default"}}, []) == "M"


def test_full_views_and_pictograms():
    view = {"type": "MonoBehaviour", "class": "AdvChoiceView", "m_Enabled": 1, "m_Script": {},
            "_choiceItems": [ref("C/Item", cls="AdvChoiceItem"), ref("C/Item (1)", cls="AdvChoiceItem")],
            "_choiceItemParent": {"transform": "C/Choices"}, "_animationIntervalIn": 0.1}
    rec = advui._layout_components({"components": [view]})
    assert rec["choiceView"] == {"enabled": 1, "m_Enabled": 1, "_choiceItems": ["C/Item", "C/Item (1)"],
                                 "_choiceItemParent": "C/Choices", "_animationIntervalIn": 0.1}
    catalog = {"asset": "Cat", "name": "Cat", "m_Enabled": 1,
               "_spritesByKey": {"_list": [{"Key": 98, "Value": {"spriteRef": "cab:98", "name": "IconClose"}}]},
               "_defaultSprite": {"spriteRef": "cab:1", "name": "IconSetting"}}
    picto = {"type": "MonoBehaviour", "class": "UIPictogram", "m_Enabled": 1, "_key": 98, "_spriteCatalog": catalog,
             "_image": ref("C/Close", cls="Image"), "_setNativeSize": 0}
    nodes = [{"path": "C/Close", "components": [picto]},
             {"path": "C/Other",
              "components": [{**picto, "_key": 5, "_spriteCatalog": {"asset": "Cat", "name": "Cat"}}]}]
    cats = advui.pictogram_catalogs(nodes)
    assert set(cats) == {"Cat"}
    recs = []
    for n in nodes:
        r = {"path": n["path"], **advui._layout_components(n)}
        advui.resolve_pictograms(r, cats)
        recs.append(r["behaviours"]["UIPictogram"][0])
    assert recs[0] == {"m_Enabled": 1, "_key": 98, "_image": "C/Close", "_setNativeSize": 0, "catalog": "Cat",
                       "sprite": "IconClose", "defaultSprite": "IconSetting", "_spriteRef": "cab:98",
                       "_defaultRef": None}
    assert (recs[1]["sprite"], recs[1]["_defaultRef"]) == (None, "cab:1")
    with pytest.raises(RuntimeError, match="not exported"):
        advui.resolve_pictograms({"path": "x", **advui._layout_components(nodes[1])}, {})
