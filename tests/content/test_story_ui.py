"""The story UI export (advui.py) on synthetic prefab nodes and episode rows: the records of the screen canvases,
views, camera and video buttons, the widget's canvases, the texts whose characters the game fonts keep, the
AdvMasterIdSettings texts, the chat window texts and the emoji sequence table. Nothing here comes from game data."""

import pytest

from nnnotes.content import advui

WHITE = {"spriteRef": "cab:1", "name": "WhiteRect"}


def image(sprite=WHITE, material=None):
    return {
        "type": "MonoBehaviour",
        "class": "Image",
        "m_Enabled": 1,
        "m_Color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
        "m_Type": 0,
        "m_PreserveAspect": 0,
        "m_FillCenter": 1,
        "m_FillMethod": 4,
        "m_FillAmount": 1.0,
        "m_UseSpriteMesh": 0,
        "m_PixelsPerUnitMultiplier": 1.0,
        "m_Maskable": 1,
        "m_Sprite": sprite,
        "m_Material": material,
    }


def canvas(camera=None, mode=1, order=302):
    return {
        "type": "Canvas",
        "m_Enabled": 1,
        "m_RenderMode": mode,
        "m_SortingOrder": order,
        "m_VertexColorAlwaysGammaSpace": True,
        "m_AdditionalShaderChannelsFlag": 25,
        "m_OverrideSorting": False,
        "m_PixelPerfect": False,
        "m_PlaneDistance": 100.0,
        "m_Camera": camera,
    }


def scaler(cls):
    c = {
        "type": "MonoBehaviour",
        "class": cls,
        "m_Enabled": 1,
        "m_UiScaleMode": 1,
        "m_ReferencePixelsPerUnit": 100.0,
        "m_ScaleFactor": 1.0,
        "m_ReferenceResolution": {"x": 1920.0, "y": 1080.0},
        "m_ScreenMatchMode": 0,
        "m_MatchWidthOrHeight": 0.0,
    }
    if cls == "ClampedCanvasScaler":
        c["_maxAspectThreshold"] = 2.1666667461395264
    return c


class _Obj:
    def __init__(self, pid, type_name, tt, cls=None):
        self.path_id, self.tt, self.cls = pid, tt, cls
        self.type = type("T", (), {"name": type_name})

    def read_typetree(self):
        return self.tt


def ref(path, component="MonoBehaviour", cls="UIButton"):
    return {"component": component, "gameObject": path, "class": cls}


def test_emoji_sequence_keys():
    key = advui.emoji_sequence_key
    assert key("1f636-200d-1f32b-fe0f", 0x1F636) == "\U0001f636\u200d\U0001f32b\ufe0f"
    assert key("2764-FE0F", 0x2764) == "\u2764\ufe0f"  # lower case, padded parts
    assert key("Emoji/1f590-fe0f.png", 0x1F590) == "\U0001f590\ufe0f"  # file name without extension
    assert key("0001f600", 0x1F600) is None  # the sprite's own code point
    with pytest.raises(NotImplementedError, match="not a code point"):
        key("d83d-de00", 0)


class FakeObj:
    def __init__(self, tree):
        self.tree = tree

    def read_typetree(self):
        return self.tree


def test_emoji_sequence_parse():
    """TMP_EmojiSearchEngine: the table (the first entry of a key wins) and the names a parse writes: the longest run
    of UTF-16 units whose prefixes are all in the fast set, no backtracking, the scan going on after a failed run."""
    seqs = [
        {"name": "2764-fe0f", "unicode": 0x2764},
        {"name": "2764-FE0F", "unicode": 0x2764},
        {"name": "1f647-200d-2640-fe0f", "unicode": 0x1F647},
        {"name": "2764-fe0f-200d-1f525", "unicode": 0x2764},
    ]
    table, fast = advui.emoji_sequence_table(seqs)
    assert table[advui._utf16("\u2764\ufe0f")] == "2764-fe0f" and len(table) == 3
    assert (0xD83D,) in fast  # a half of a surrogate pair
    names = advui.emoji_sequence_names
    assert names(table, fast, "a\u2764\ufe0fb\U0001f647\u200d\u2640\ufe0f") == ["2764-fe0f", "1f647-200d-2640-fe0f"]
    assert names(table, fast, "\u2764\ufe0f\u200dx") == [], "the run's prefix is no key: no shorter key is tried"
    assert names(table, fast, "\u2764\ufe0f\u200d\U0001f525") == ["2764-fe0f-200d-1f525"]
    assert names(table, fast, "\u2764\u2764\ufe0f") == ["2764-fe0f"]
    assert names({}, set(), "\u2764\ufe0f") == []


def test_tmp_hash():
    """TMP_TextUtilities.GetHashCode: case-insensitive for ASCII letters, 32 bits."""
    assert advui.tmp_hash("abc") == ((((65 * 33) ^ 66) * 33) ^ 67)
    assert advui.tmp_hash("1f600") == advui.tmp_hash("1F600") != advui.tmp_hash("1f601")
    assert 0 <= advui.tmp_hash("x" * 40) < 2**32


LANG = {
    "languages": {
        0: {"fontNames": ["JA-R", "JA-N"], "additionalFontNames": ["CHAT"]},
        1: {"fontNames": ["EN-R", "EN-N"], "additionalFontNames": ["EN-CHAT"]},
        4: {"fontNames": ["KO-R", "KO-N"], "additionalFontNames": []},
    },
    "materialTypes": ["Default", "Outline"],
}


def lines(s):
    return {"japanese": s, "english": s}


def chat_episode():
    return {
        "commands": [
            {"cmd": "ChatWindow", "TargetTextIDs": ["w"], "Parameter1": "80.9", "Parameter4": "3"},
            {"cmd": "ChatTalk", "lines": lines("やあ"), "TargetTextIDs": ["s"], "Parameter1": "2"},
            {"cmd": "ChatStamp", "TargetAssetName": "Stamp/st_01", "TargetTextIDs": ["s"], "Parameter1": "1"},
            {"cmd": "ChatRead", "Parameter1": "x"},
            {"cmd": "ChatTalk", "lines": lines("無視"), "IgnoreData": 1, "Parameter1": "9"},
        ],
        "text": {"w": lines("窓"), "s": lines("送"), "st_01_log": lines("スタンプ"), "60001": lines("既読")},
        "title": lines("題"),
        "resources": [
            {"kind": "chatwindow", "address": "Adv/Chat/Prefabs/Line", "present": True},
            {"kind": "chaticon", "address": "Adv/Chat/Icon/a", "present": True},
        ],
    }


def tmp_text(font, material, text=""):
    return {
        "type": "MonoBehaviour",
        "class": "TextMeshProUGUI",
        "m_Enabled": 1,
        "m_fontAsset": {"name": font},
        "m_sharedMaterial": {"material": material},
        "m_text": text,
        "m_isRichText": 1,
        "m_parseCtrlCharacters": 1,
        "m_fontSize": 24.0,
        "m_enableAutoSizing": 0,
        "m_fontSizeMin": 18.0,
        "m_fontSizeMax": 72.0,
        "m_charWidthMaxAdj": 0.0,
        "m_lineSpacingMax": 0.0,
        "m_fontStyle": 0,
        "m_fontWeight": 400,
        "m_HorizontalAlignment": 1,
        "m_VerticalAlignment": 256,
        "m_TextWrappingMode": 1,
        "m_overflowMode": 0,
        "m_margin": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0},
        "m_lineSpacing": 3.0,
        "m_paragraphSpacing": 0.0,
        "m_characterSpacing": 0.0,
        "m_wordSpacing": 0.0,
        "m_enableKerning": 1,
        "m_isRightToLeft": 0,
        "m_isOrthographic": 0,
        "m_fontColor": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
        "m_colorMode": 3,
        "m_enableVertexGradient": 0,
        "m_overrideHtmlColors": 0,
    }


def localize(enabled=1, key="0"):
    return {
        "type": "MonoBehaviour",
        "class": "LocalizeText",
        "m_Enabled": enabled,
        "_localizeEnabled": enabled,
        "_masterTextID": key,
    }
