"""Overlay UI products and their episode font bindings on synthetic objects."""

import copy

import pytest
from jsonschema import Draft202012Validator

from nnnotes import schemas
from nnnotes.content import storysimpleui, storyui
from nnnotes.pipeline import contract, story
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env
from tests.content.test_storyfontproduct import fixture
from tests.helpers import storyui as sui
from tests.helpers.fonts import make_font
from tests.pipeline.test_live import run, stored


def simple_keys():
    return {
        "Emb" + storysimpleui.ADDRESSES["talkWindow"]: [sui.WIDGET],
        "Emb" + storysimpleui.ADDRESSES["systemMessage"]: [sui.PLAIN],
        storyui.UI_MANAGER_KEY: [sui.UI_MANAGER],
    }


def test_plain_and_embedded_addresses():
    assert storysimpleui.addresses(simple_keys()) == {k: "Emb" + v for k, v in storysimpleui.ADDRESSES.items()}
    held = {*simple_keys(), storysimpleui.ADDRESSES["talkWindow"]}
    assert storysimpleui.addresses(held)["talkWindow"] == storysimpleui.ADDRESSES["talkWindow"]
    with pytest.raises(ValueError, match="no plain or embedded"):
        storysimpleui.keys([])


def test_simple_ui_localizes_text_and_retains_prefab_references(tmp_path):
    depot = Depot(tmp_path / "d")
    table = sui.table_doc(depot, keys=simple_keys())
    doc, keep, _ = storysimpleui.ui(Objects(table, depot), "zh-Hant", sui.font_settings())
    Draft202012Validator(schemas.load("story-simple-ui")).validate(doc)
    assert doc["talkWindow"] == sui.WIDGET and doc["systemMessage"] == sui.PLAIN
    assert doc["texts"]["CAB-ui:101"]["localized"]["fontAsset"] == "MainT SDF"
    assert doc["texts"]["CAB-ui:111"]["localized"] is None
    assert sui.WIDGET in keep and sui.SCRIPT not in keep and sui.FONT_JA not in keep
    assert doc["language"]["field"] == "traditionalChinese"


@pytest.mark.parametrize("source", ["open", "game"])
def test_overlay_font_product_includes_simple_ui_texts(tmp_path, source):
    depot = Depot(tmp_path / "d")
    inputs, table, episode = fixture(depot)
    table["keys"].update(simple_keys())
    episode["master"] = {"_playbackMode": 1}
    # A distinct system-message text makes omission from the font product observable.
    plain = copy.deepcopy(Objects(table, depot).json(sui.PLAIN))
    plain["nodes"][0]["components"][0]["pathId"] = "711"
    body = contract.encode(plain)
    table["objects"]["CAB-simple:1"] = copy.deepcopy(table["objects"][sui.PLAIN])
    table["objects"]["CAB-simple:1"]["files"]["json"].update(sha256=depot.put(body), size=len(body))
    table["keys"]["Emb" + storysimpleui.ADDRESSES["systemMessage"]] = ["CAB-simple:1"]
    inputs = [i for i in inputs if i.role not in ("objects", "episode")]
    inputs += [stored(depot, contract.encode(table), "objects"), stored(depot, contract.encode(episode), "episode")]
    if source == "open":
        font = make_font(tmp_path / "font.ttf", underscore=True).read_bytes()
        inputs += [stored(depot, font, f"font:{code}") for code in ("ja", "zh-Hant")]
    _, _, artifacts = run(depot, story.FontsStage(), "1/zh-Hant", inputs, {"fonts": source}, story.font_atoms())
    doc = contract.loads(depot.read(artifacts["fonts.json"]["content"]["sha256"]))
    assert "CAB-simple:711" in doc["texts"]
    binding = doc["texts"]["CAB-simple:711"]["font"]
    assert binding["fontAsset"] in doc["fonts"] and binding["material"] in doc["materials"]


def test_overlay_parts_request_simple_ui_for_selected_languages():
    parts = {"assets": [], "models": [], "sounds": [], "videos": [], "chat": False, "crilips": False}
    normal = story.part_paths(parts, 1)
    overlay = story.part_paths(parts, 1, requested_languages=["ja"], overlay=True)
    assert "simpleui" not in normal
    assert overlay["simpleui"]["ja"] == "storysimpleui/ja.json"
    products = story.part_products({"advId": 1, "parts": overlay}, ["ja", "ja", "zh-Hant"])
    assert products[story.SIMPLE_UI] == ["ja", "zh-Hant"]


def test_font_roots_include_simple_ui_for_overlay_episodes(tmp_path):
    depot = Depot(tmp_path / "d")
    episode = {"master": {"_playbackMode": 1}, "resources": []}
    index = {"locations": [{"primaryKey": k} for k in {*sui.catalog_keys(), *simple_keys()}]}
    env = Env(
        depot, {"index": index, "product_inputs": {"episode:1": stored(depot, contract.encode(episode), "episode:1")}}
    )
    assert set(simple_keys()) <= set(story.FontsStage().keys("1/ja", env))
    assert story.SimpleUiStage().manifest("ja") == "storysimpleui/ja.json"
