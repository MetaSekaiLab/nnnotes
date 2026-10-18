"""The story UI document (content.storyui) on a synthetic object table: the talk window and catalog keys, the fields
of the document in a language, the text records with the font asset and material their language draws with, the
master id texts and validation errors."""

import pytest

from nnnotes.content import prefabs, storyui
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import storyui as sui
from tests.helpers.livenotes import comp
from tests.helpers.schemas import validate


def ui(objects, language, master):
    return storyui.ui(objects, language, master, sui.font_settings())


def story(tmp_path, name: str = "d", **kw) -> Objects:
    depot = Depot(tmp_path / name)
    return Objects(sui.table_doc(depot, **kw), depot)


def test_the_talk_windows_take_the_plain_key_or_the_embedded_one_and_the_default_window():
    assert storyui.window_keys(sui.catalog_keys()) == {
        "UIBoth": "UI/Prefab/Parts/Adv/Talk/UIBoth",
        "UIDefaultTalkWindow": storyui.WINDOW_KEY,
        "UIEmb": "EmbUI/Prefab/Parts/Adv/Talk/UIEmb",
        "UIPlain": "UI/Prefab/Parts/Adv/Talk/UIPlain",
    }
    assert storyui.window_keys([]) == {"UIDefaultTalkWindow": storyui.WINDOW_KEY}


def test_the_story_ui_keys_are_the_keys_of_its_table():
    keys = storyui.keys(sui.catalog_keys())
    assert keys == sorted(sui.table_keys()) and len(keys) == 19


def test_the_document_has_the_story_ui_fields_in_the_language(tmp_path):
    doc, keep, roles = ui(story(tmp_path), "zh-Hant", sui.MASTER_RECORD)
    validate("story-ui", doc)
    assert set(doc) == {
        "format",
        "language",
        "widget",
        "talkWindows",
        "chatWidget",
        "dialogs",
        "uiManager",
        "letterbox",
        "clips",
        "controllers",
        "settings",
        "graphics",
        "texts",
        "textStyle",
        "masterIdTexts",
        "chatStatusTexts",
        "bindings",
        "shaders",
    }
    assert doc["format"] == storyui.UI_FORMAT == 1
    assert doc["language"] == {
        "code": "zh-Hant",
        "mode": 2,
        "field": "traditionalChinese",
        "lineSpacing": 5.0,
        "fontNames": ["MainT", "Digits"],
        "additionalFontNames": ["Extra"],
        "materialTypes": ["Default", "Outline"],
        "fontSwap": {"MainJ SDF": "MainT SDF", "Digits SDF": "Digits SDF"},
    }
    assert doc["widget"] == sui.WIDGET and doc["chatWidget"] == sui.CHAT
    assert doc["uiManager"] == sui.UI_MANAGER and doc["letterbox"] == sui.LETTERBOX
    assert doc["talkWindows"] == {
        "UIBoth": sui.BOTH_UI,
        "UIDefaultTalkWindow": sui.DEFAULT,
        "UIEmb": sui.EMB,
        "UIPlain": sui.PLAIN,
    }
    assert doc["dialogs"] == {"UIAdvSkipConfirmDialogWidget": sui.DIALOG_A, "UICommonDialogWidget": sui.DIALOG_B}
    assert doc["clips"] == dict(zip(storyui.CLIP_KEYS, sui.CLIPS))
    assert doc["controllers"] == dict(zip(storyui.CONTROLLER_KEYS, sui.CONTROLLERS))
    assert doc["settings"] == {
        "masterIdSettings": sui.SETTINGS,
        "dotween": sui.DOTWEEN,
        "tmpSettings": sui.TMP_SETTINGS,
        "localizeManager": sui.LOCALIZE,
    }
    assert doc["graphics"] == {"graphics": sui.GRAPHICS}
    assert doc["bindings"] == {}
    assert doc["shaders"] == {"UI/Default": sui.SHADER}
    assert keep == set(sui.objects()) - {sui.SCRIPT}
    assert set(roles) == {sui.SHADER} and {"json", "index"} <= roles[sui.SHADER]


def test_a_text_draws_with_the_font_and_material_of_its_language(tmp_path):
    doc, _, _ = ui(story(tmp_path), "zh-Hant", sui.MASTER_RECORD)
    assert set(doc["texts"]) == {"CAB-ui:101", "CAB-ui:103", "CAB-ui:111"}
    talk = doc["texts"]["CAB-ui:101"]
    assert talk["localized"] == {"fontAsset": "MainT SDF", "material": "MainT - Outline", "lineSpacing": 5.0}
    style = talk["style"]
    assert style["class"] == "TextMeshProUGUI" and style["text"] == "talk" and style["localized"] is True
    assert style["fontRole"] == "primary" and style["materialType"] == "Outline" and style["textKey"] == "101"
    assert style["alignment"] == {"horizontal": "center", "vertical": "middle"}
    assert style["lineSpacing"]["applied"] == 5.0
    assert style["outline"]["widthEm"] == 0.03
    plain = doc["texts"]["CAB-ui:103"]
    assert plain["localized"] is None and plain["style"]["localized"] is False
    assert plain["style"]["textKey"] is None and plain["style"]["materialType"] == "Default"
    assert plain["style"]["outline"] is None
    window = doc["texts"]["CAB-ui:111"]
    assert window["localized"] is None and window["style"]["textKey"] is None
    assert doc["textStyle"]["language"] == "traditionalChinese"
    assert set(doc["textStyle"]["roles"]) == {"primary"}


def test_the_japanese_texts_draw_with_the_japanese_font(tmp_path):
    doc, _, _ = ui(story(tmp_path), "ja", sui.MASTER_RECORD)
    assert doc["texts"]["CAB-ui:101"]["localized"] == {
        "fontAsset": "MainJ SDF",
        "material": "MainJ - Outline",
        "lineSpacing": 0.0,
    }
    assert doc["language"]["fontSwap"] == {"MainJ SDF": "MainJ SDF", "Digits SDF": "Digits SDF"}
    assert doc["textStyle"]["roles"] == {
        "primary": {"lineHeightEm": 1.2, "ascentEm": 0.9, "descentEm": -0.3, "spacingOffset": 0.0, "boldSpacing": 7.0}
    }


def test_the_master_id_texts_are_the_texts_of_the_settings_ids(tmp_path):
    doc, _, _ = ui(story(tmp_path), "zh-Hant", sui.MASTER_RECORD)
    assert doc["masterIdTexts"] == {
        field: {"id": sui.MASTER_IDS[field], "text": f"zh {sui.MASTER_IDS[field]}"} for field in storyui.MASTER_ID_TEXTS
    }


def test_a_master_id_without_a_text_is_a_key_error(tmp_path):
    with pytest.raises(KeyError):
        ui(story(tmp_path, name="missing"), "zh-Hant", {"texts": {}})


def test_a_second_localize_manager_is_refused(tmp_path):
    depot = Depot(tmp_path / "d")
    objs = {**sui.objects(), "CAB-lm:2": ("MonoBehaviour", "LocalizeManager", {"json": sui.LOCALIZE_JSON})}
    doc = sui.table_doc(depot, objs=objs, extra_roots=["CAB-lm:2"])
    with pytest.raises(ValueError, match="2 LocalizeManager roots"):
        ui(Objects(doc, depot), "zh-Hant", sui.MASTER_RECORD)


def test_localize_manager_fields_are_read_from_its_scene_component(tmp_path):
    depot = Depot(tmp_path / "d")
    holder = "CAB-lm:20"
    fields = {k: v for k, v in sui.LOCALIZE_JSON.items() if k != "$script"}
    objs = {
        **sui.objects(),
        holder: (
            "GameObject",
            "Localization",
            {"json": sui.prefab("Localization", comp("MonoBehaviour", 1, sui.LOCALIZE_JSON["$script"], **fields))},
        ),
    }
    doc = sui.table_doc(depot, objs=objs)
    doc["objects"][sui.LOCALIZE] = {"class": "MonoBehaviour", "status": "contained", "in": holder}
    objects = Objects(doc, depot)
    assert prefabs.script_class(objects, sui.LOCALIZE) == "LocalizeManager"
    assert prefabs.mono_json(objects, sui.LOCALIZE) == sui.LOCALIZE_JSON
    result, _, _ = ui(objects, "zh-Hant", sui.MASTER_RECORD)
    assert result["settings"]["localizeManager"] == sui.LOCALIZE
    assert result["language"]["fontNames"] == ["MainT", "Digits"]


def test_a_font_asset_the_fence_does_not_name_is_refused(tmp_path):
    depot = Depot(tmp_path / "d")
    doc = sui.table_doc(depot)
    doc["fenced"] = {}
    with pytest.raises(ValueError, match="CAB-font:5: no name in the object table"):
        ui(Objects(doc, depot), "zh-Hant", sui.MASTER_RECORD)


def test_the_supplied_font_metrics_and_materials_determine_the_text_style(tmp_path):
    settings = sui.font_settings()
    settings.fonts["MainT SDF"]["faceInfo"] = {**settings.fonts["MainT SDF"]["faceInfo"], "m_LineHeight": 150.0}
    settings.materials["MainT - Outline"]["floats"]["_OutlineWidth"] = 0.4
    doc, _, _ = storyui.ui(story(tmp_path), "zh-Hant", sui.MASTER_RECORD, settings)
    assert doc["textStyle"]["roles"]["primary"]["lineHeightEm"] == 1.5
    assert doc["texts"]["CAB-ui:101"]["style"]["outline"]["widthEm"] == 0.06


def test_chat_status_texts_resolve_each_nonempty_key_once(tmp_path):
    depot = Depot(tmp_path / "d")
    key, oid, entry = sui.chat_window()
    objects = Objects(
        sui.table_doc(depot, objs={**sui.objects(), oid: entry}, keys={**sui.table_keys(), key: [oid]}), depot
    )
    assert storyui.chat_status_texts(objects, sui.MASTER_RECORD["texts"]) == {"301": "zh 301", "302": "zh 302"}
    assert storyui.chat_status_texts(story(tmp_path, name="empty"), sui.MASTER_RECORD["texts"]) == {}
    with pytest.raises(KeyError, match="302"):
        storyui.chat_status_texts(objects, {"301": "incoming"})


def test_font_keys_cover_both_font_folders_and_chat_windows_have_their_own_roots():
    keys = ["Font/Extra/Extra SDF", "EmbFont/MainJ/MainJ - Outline", "Adv/Chat/Prefabs/win_7", "UI/Prefab/UIAdvWidget"]
    assert storyui.font_keys(keys) == ["EmbFont/MainJ/MainJ - Outline", "Font/Extra/Extra SDF"]
    assert storyui.chat_window_keys(keys) == ["Adv/Chat/Prefabs/win_7"]
