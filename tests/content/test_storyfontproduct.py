"""Episode font products with synthetic font files, prefabs, glyphs and atlas pages."""

import copy
import io

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from nnnotes import schemas
from nnnotes.content import storyfontproduct as fp
from nnnotes.content import storyui
from nnnotes.pipeline import contract, story
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env
from tests.content.test_tmpassets import texture_doc
from tests.helpers import storyui as sui
from tests.helpers import synth
from tests.helpers.fonts import make_font
from tests.helpers.live2dmodel import ref
from tests.pipeline.test_live import run, stored


def fixture(depot):
    fonts, keys = sui.font_objects()
    objs = {**sui.objects(), **fonts}
    for cls, _, files in objs.values():
        if cls == "GameObject":
            for node in files["json"]["nodes"]:
                for comp in node["components"]:
                    if "m_text" in comp["fields"]:
                        comp["fields"]["m_text"] = "AB"
        elif cls == "Material":
            mat = files["json"]
            mat["shader"] = ref(sui.SHADER)
            mat["floats"].update({"_TextureWidth": 32, "_TextureHeight": 32})
        elif cls == "MonoBehaviour" and files["json"].get("$script", "").endswith("|TMP_FontAsset"):
            doc = files["json"]
            doc.update(
                {
                    "m_AtlasTextures": [ref("CAB-atlas:1")],
                    "m_AtlasWidth": 32,
                    "m_AtlasHeight": 32,
                    "m_FontFeatureTable": {"m_GlyphPairAdjustmentRecords": []},
                }
            )
            doc["m_CharacterTable"] = [
                {"m_Unicode": u, "m_GlyphIndex": u, "m_Scale": 1.0, "m_ElementType": 1} for u in (32, 65, 66, 95, 9633)
            ]
            doc["m_GlyphTable"] = [
                {
                    "m_Index": u,
                    "m_Metrics": {
                        "m_Width": 4,
                        "m_Height": 4,
                        "m_HorizontalBearingX": 0,
                        "m_HorizontalBearingY": 4,
                        "m_HorizontalAdvance": 5,
                    },
                    "m_GlyphRect": {"m_X": 16, "m_Y": 16, "m_Width": 4, "m_Height": 4},
                    "m_Scale": 1.0,
                    "m_AtlasIndex": 0,
                }
                for u in (32, 65, 66, 95, 9633)
            ]
    png = io.BytesIO()
    Image.fromarray(np.full((32, 32, 4), 100, dtype=np.uint8)).save(png, format="PNG")
    objs["CAB-atlas:1"] = ("Texture2D", "Atlas", {"json": texture_doc("Atlas", 32, 32, 4), "image": png.getvalue()})
    objs[sui.TMP_SETTINGS][2]["json"].update(
        {
            "m_missingGlyphCharacter": 9633,
            "m_fallbackFontAssets": [],
            "m_leadingCharacters": None,
            "m_followingCharacters": None,
            "m_UseModernHangulLineBreakingRules": True,
        }
    )
    variants = objs[sui.SHADER][2]["index"]["variants"]
    for v in variants:
        v["keywords"] = []
        if v["platform"] == "gles3":
            v["stage"] = "vertex"
    doc = sui.table_doc(depot, objs=objs, keys={**sui.table_keys(), **keys})
    doc["objects"]["CAB-atlas:1"]["files"]["image"]["ext"] = "png"
    episode = {
        "text": {"1": {"japanese": "AB", "traditionalChinese": "BA"}},
        "title": None,
        "commands": [],
        "resources": [],
    }
    inputs = [
        stored(depot, contract.encode(doc), "objects"),
        stored(depot, contract.encode({"texts": {str(i): "AB" for i in sui.MASTER_IDS.values()}}), "master"),
        stored(depot, contract.encode({"CAB-font": "font.bundle"}), "font-bundles"),
        stored(depot, synth.axml(["versionName", "manifest", "1.2.3"], 2), "apk:AndroidManifest.xml"),
        stored(depot, contract.encode(episode), "episode"),
    ]
    return inputs, doc, episode


@pytest.mark.parametrize("source", ["open", "game"])
def test_font_product_pages_texts_and_retained_shader_objects(tmp_path, source):
    depot = Depot(tmp_path / "d")
    inputs, _, _ = fixture(depot)
    if source == "open":
        for code in ("ja", "zh-Hant"):
            data = make_font(tmp_path / f"{code}.ttf", underscore=True).read_bytes()
            inputs.append(stored(depot, data, f"font:{code}"))
    _, _, arts = run(depot, story.FontsStage(), "1/zh-Hant", inputs, {"fonts": source}, story.font_atoms())
    doc = contract.loads(depot.read(arts["fonts.json"]["content"]["sha256"]))
    Draft202012Validator(schemas.load("story-fonts")).validate(doc)
    assert doc["format"] == fp.FORMAT and doc["source"] == source
    assert set(doc["texts"]) == {"CAB-ui:101", "CAB-ui:103", "CAB-ui:111"}
    assert doc["texts"]["CAB-ui:103"]["localized"] is None
    assert doc["texts"]["CAB-ui:101"]["style"]["textKey"] == "101"
    assert doc["coverage"]["missing"] == []
    assert doc["lineBreaking"]["useModernHangulLineBreakingRules"]
    assert set(contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]) == {sui.SHADER}
    for texture in doc["textures"].values():
        data = depot.read(arts[texture["texture"]]["content"]["sha256"])
        image = Image.open(io.BytesIO(data))
        assert image.size == (texture["width"], texture["height"])
    for record in doc["texts"].values():
        assert record["font"]["fontAsset"] in doc["fonts"]
        assert record["font"]["material"] in doc["materials"]


def test_text_targets_resolve_internal_component_paths(tmp_path):
    depot = Depot(tmp_path / "d")
    _, table, _ = fixture(depot)
    objects = Objects(table, depot)
    widget = copy.deepcopy(objects.json(sui.WIDGET))
    widget["nodes"][0]["components"].append(
        {
            "type": "MonoBehaviour",
            "pathId": 500,
            "script": "Fwk|Fwk.UI|UIText",
            "fields": {"_targetText": {"component": "TextMeshProUGUI", "gameObject": "UIAdvWidget/Talk"}},
        }
    )
    widget["nodes"][1]["components"][0]["fields"]["m_tintAllSprites"] = True
    body = contract.encode(widget)
    table["objects"][sui.WIDGET]["files"]["json"].update(sha256=depot.put(body), size=len(body))
    from nnnotes.unity import textstyle

    records, sprites, _ = fp.text_records(
        Objects(table, depot),
        [storyui.WIDGET_KEY],
        "zh-Hant",
        textstyle.language_fonts_json(sui.LOCALIZE_JSON),
        sui.font_settings(),
    )
    assert set(sprites) == {"CAB-ui:101"}
    assert sprites["CAB-ui:101"] == {"m_tintAllSprites": True}
    assert set(records) == {"CAB-ui:101", "CAB-ui:103"}


def test_episode_snapshot_roots_and_font_content_are_inputs(tmp_path):
    depot = Depot(tmp_path / "d")
    episode = {"resources": [{"kind": "frame", "address": "Adv/Frame/a"}, {"kind": "stage", "address": "Adv/Stage/b"}]}
    env = Env(
        depot,
        {"index": sui.index(), "product_inputs": {"episode:1": stored(depot, contract.encode(episode), "episode:1")}},
    )
    stage = story.FontsStage()
    assert stage.keys("1/ja", env) == sorted({*story.UiStage().keys("ja", env), "Adv/Frame/a"})
    assert stage.manifest("1/ja") == "storyfonts/1_ja.json"
    assert stage.normalize(None) == {"fonts": "open"}
    with pytest.raises(ValueError):
        stage.manifest("1/fr")
    with pytest.raises(ValueError):
        stage.normalize({"fonts": "unknown"})


def test_requested_languages_select_ui_and_fonts_only_for_those_languages():
    parts = {"assets": [], "models": [], "sounds": [], "videos": [], "chat": False, "crilips": False}
    doc = {"advId": 7, "parts": story.part_paths(parts, 7)}
    assert story.part_products(doc, ["ja", "ja", "en"]) == {
        story.SCENE: ["default"],
        story.UI: ["en", "ja"],
        story.FONTS: ["7/en", "7/ja"],
    }


def test_font_input_keys_follow_file_bytes_and_source_mode(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "d")
    path = tmp_path / "font.ttf"
    path.write_bytes(b"first font bytes")
    episode = stored(depot, contract.encode({"resources": []}), "episode:1")
    env = Env(depot, {"product_inputs": {"episode:1": episode}, "font_files": {"ja": path}})
    env.params = {"fonts": "open"}
    stage = story.FontsStage()
    monkeypatch.setattr(stage, "font_bundles", lambda subject, env: stored(depot, b"{}", "font-bundles"))
    monkeypatch.setattr(stage, "record", lambda subject, env: {"texts": {}})
    first = stage.more_inputs("1/ja", env)
    a = contract.Task(stage.name, stage.version, "1/ja", env.params, {}, tuple(first))
    path.write_bytes(b"second font bytes")
    second = stage.more_inputs("1/ja", env)
    b = contract.Task(stage.name, stage.version, "1/ja", env.params, {}, tuple(second))
    assert a.key != b.key
    assert depot.input_bytes(a.input("font:ja")) == b"first font bytes"
    assert depot.input_bytes(b.input("font:ja")) == b"second font bytes"
    assert a.input("episode").sha256 == b.input("episode").sha256 == episode.sha256
    env.params = {"fonts": "game"}
    assert all(not i.role.startswith("font:") for i in stage.more_inputs("1/ja", env))


def test_emoji_subclass_and_sprite_page_are_exported_for_driven_text(tmp_path):
    from nnnotes.content import advui, tmpassets
    from nnnotes.unity import fontsettings, textstyle

    depot = Depot(tmp_path / "d")
    _, table, episode = fixture(depot)
    emoji_id = "CAB-emoji:1"
    body = contract.encode(
        {
            "$script": "Kyub.EmojiSearch|Kyub.EmojiSearch|TMP_EmojiSpriteAsset",
            "m_Name": "Emoji",
            "m_FaceInfo": {},
            "m_Material": ref("CAB-font:20"),
            "spriteSheet": ref("CAB-atlas:1"),
            "m_SpriteCharacterTable": [{"m_Unicode": 0x1F600, "m_Name": "1F600", "m_GlyphIndex": 1, "m_Scale": 1}],
            "m_GlyphTable": [
                {
                    "m_Index": 1,
                    "m_Metrics": {},
                    "m_GlyphRect": {"m_X": 0, "m_Y": 0, "m_Width": 4, "m_Height": 4},
                    "m_Scale": 1,
                    "m_AtlasIndex": 0,
                }
            ],
        }
    )
    table["objects"][emoji_id] = {
        "class": "MonoBehaviour",
        "name": "Emoji",
        "files": {"json": {"sha256": depot.put(body), "size": len(body), "ext": "json"}},
    }
    table["keys"][advui.EMOJI_SPRITE_ASSET_KEY] = [emoji_id]
    objects = Objects(table, depot)
    widget = objects.json(sui.WIDGET)
    widget["nodes"][0]["components"].append(
        {
            "type": "MonoBehaviour",
            "pathId": 500,
            "script": "Fwk|Fwk.UI|UIText",
            "fields": {"_targetText": {"component": "TextMeshProUGUI", "gameObject": "UIAdvWidget/Talk"}},
        }
    )
    widget["nodes"][1]["components"][0]["fields"]["m_tintAllSprites"] = True
    episode["text"]["1"]["traditionalChinese"] += "\U0001f600"
    lang = textstyle.language_fonts_json(sui.LOCALIZE_JSON)
    settings = fontsettings.FontSettings(
        tmpassets.build_font_settings(objects, lang, "1.2.3", lambda name: "font.bundle")
    )
    doc, pages, keep, _ = fp.build(
        objects, episode, "zh-Hant", {str(i): "AB" for i in sui.MASTER_IDS.values()}, settings, "game", {}
    )
    assert doc["emojiSpriteAsset"] == "Emoji"
    assert doc["sprites"] == {"CAB-ui:101": {"spriteAsset": "Emoji", "m_tintAllSprites": True}}
    glyph = doc["spriteAssets"]["Emoji"]["glyphs"]["1"]
    texture = doc["textures"][glyph["packed"]["texture"]]
    assert pages[texture["texture"]] == objects.bytes("CAB-atlas:1", "image")
    assert keep == {sui.SHADER}
