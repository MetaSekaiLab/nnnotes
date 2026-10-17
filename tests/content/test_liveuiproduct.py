"""Live UI products with synthetic scenes, fonts and master rows."""

import copy
import io
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from nnnotes import schemas
from nnnotes.content import liveui, liveuiproduct
from nnnotes.pipeline import contract, live, textfonts
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env
from tests.content.test_storyfontproduct import fixture as font_fixture
from tests.helpers import livenotes as ln
from tests.helpers import storyui as sui
from tests.helpers.fonts import make_font
from tests.helpers.live2dmodel import ref
from tests.pipeline.test_live import run, stored

SCENE, JACKET, ICON = "CAB-live:1", "CAB-jacket:1", "CAB-icon:1"


def master():
    def text(tid, ja, zh=None):
        return {"_id": tid, "_japanese": ja, "_traditionalChinese": ja if zh is None else zh}

    return {
        "MasterLiveMusic": [
            {
                "_id": 7,
                "_expertID": 73,
                "_normalID": 71,
                "_titleTextID": "title",
                "_bandNameTextID": "",
                "_bandIDs": [2],
                "_lyricistTextID": "author",
                "_composerTextID": "",
                "_arrangerTextID": "author",
                "_jacketAssetName": "jacket7",
            }
        ],
        "MasterLiveMusicScore": [{"_id": 73, "_musicScoreLevel": 27}, {"_id": 71, "_musicScoreLevel": 5}],
        "MasterBand": [{"_id": 2, "_nameTextID": "band"}],
        "MasterText": [
            text("title", "AB", "BA"),
            text("author", "A", "B"),
            text("band", "A", "BA"),
            text("ui_credit_lyrics", "{0} A"),
            text("ui_credit_composer", "B{0}"),
            text("ui_credit_arranger", ""),
            text("label", "A", "B"),
        ],
    }


def fixture(depot):
    inputs, table, _ = font_fixture(depot)
    fields, nodes = {}, []
    for index, (field, _) in enumerate(liveui.TEXT_FIELDS.items()):
        path = f"{liveui.DRAWN[1]}/{index}"
        nodes.append(
            ln.node(
                path, ln.comp("TextMeshProUGUI", 100 + index, **sui.tmp_fields(sui.FONT_JA, sui.MAT_JA_DEFAULT, "AB"))
            )
        )
        target = ln.at(path, "TextMeshProUGUI")
        fields[field] = [target] if field.endswith("Texts") else target
    path = f"{liveui.DRAWN[0]}/label"
    nodes.append(
        ln.node(
            path,
            ln.comp("TextMeshProUGUI", 150, **sui.tmp_fields(sui.FONT_JA, sui.MAT_JA_DEFAULT, "AB")),
            ln.comp("MonoBehaviour", 151, sui.LOCALIZE_SCRIPT, m_Enabled=1, _localizeEnabled=1, _masterTextID="label"),
        )
    )
    for i, field in enumerate(("_jacketImages", "_difficultyImages")):
        path = f"{liveui.DRAWN[2]}/{i}"
        nodes.append(ln.node(path, ln.comp("Image", 200 + i, m_Sprite=None)))
        fields[field] = [ln.at(path, "Image")]
    fields["_difficultyPairs"] = [{"Difficulty": 3, "Icon": ref(ICON)}]
    nodes.insert(0, ln.node(liveui.CANVAS, ln.comp("MonoBehaviour", 2, "App|App.Live|UILiveStartCanvas", **fields)))
    extra = ln.table(
        depot,
        {liveui.SCENE_KEY: [SCENE], "Image/Jacket/jacket7": [JACKET]},
        {
            SCENE: ("GameObject", "Live", {"json": {"name": "Live", "nodes": nodes}}),
            JACKET: ("Sprite", "jacket7", {"json": {}}),
            ICON: ("Sprite", "Expert", {"json": {}}),
        },
    )
    table["objects"].update(extra["objects"])
    table["keys"].update(extra["keys"])
    table["roots"] += extra["roots"]
    inputs = [i for i in inputs if i.role not in ("objects", "master", "episode")]
    inputs += [
        stored(depot, contract.encode(table), "objects"),
        stored(depot, contract.encode(liveuiproduct.record(master(), "7/expert/zh-Hant")), "master"),
        stored(depot, contract.encode({"objects": {JACKET: table["objects"][JACKET]}}), "jacket.objects"),
    ]
    return inputs, table


@pytest.mark.parametrize("source", ["open", "game"])
def test_start_canvas_text_pages_and_image_bindings(tmp_path, source):
    depot = Depot(tmp_path / "d")
    inputs, table = fixture(depot)
    if source == "open":
        data = make_font(tmp_path / "font.ttf", underscore=True, extra={ord(c): "A" for c in "0257"}).read_bytes()
        inputs += [stored(depot, data, f"font:{code}") for code in ("ja", "zh-Hant")]
    _, _, arts = run(depot, live.UiStage(), "7/expert/zh-Hant", inputs, {"fonts": source}, textfonts.font_atoms())
    doc = contract.loads(depot.read(arts["ui.json"]["content"]["sha256"]))
    Draft202012Validator(schemas.load("live-ui")).validate(doc)
    assert doc["strings"] == {
        "musicTitle": "AB",
        "singerName": "BA",
        "lyricsWriter": "B A",
        "composer": "B",
        "arranger": "",
        "musicLevel": "27",
        "highScore": "0",
    }
    assert doc["texts"]["CAB-live:100"]["text"] == "AB"
    assert doc["texts"]["CAB-live:101"]["text"] == "BA"
    assert doc["texts"]["CAB-live:150"]["text"] == "B"
    assert not doc["texts"]["CAB-live:150"]["setByInitialize"]
    assert doc["scene"] == {"manifest": "scenes/default.json", "prefab": SCENE, "canvas": liveui.CANVAS}
    assert doc["images"] == {"CAB-live:200": JACKET, "CAB-live:201": ICON}
    kept = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]
    assert set(kept) == {JACKET, sui.SHADER}
    for texture in doc["textures"].values():
        image = Image.open(io.BytesIO(depot.read(arts[texture["texture"]]["content"]["sha256"])))
        assert image.size == (texture["width"], texture["height"])
    for rec in doc["texts"].values():
        assert rec["font"]["fontAsset"] in doc["fonts"]
        assert rec["font"]["material"] in doc["materials"]
    objects = Objects(table, depot)
    # The shared input remains usable by subsequent products.
    assert objects.json(SCENE)["nodes"][1]["components"][0]["fields"]["m_text"] == "AB"


def test_master_record_tracks_difficulty_and_language():
    tables = master()
    record = liveuiproduct.record(tables, "7/normal/zh-Hant")
    assert record["MasterLiveMusicScore"] == [{"_id": 71, "_musicScoreLevel": 5}]
    assert liveui.record_strings(record, 7, "normal", "_traditionalChinese")[0]["musicLevel"] == "5"
    record["MasterText"][0]["_traditionalChinese"] = ""
    assert liveui.record_strings(record, 7, "normal", "_traditionalChinese")[0]["musicTitle"] == "AB"


def test_ui_subject_manifest_and_requested_languages(tmp_path):
    stage = live.UiStage()
    assert stage.manifest("7/expert/ja") == "liveui/7_expert_ja.json"
    for value in ("7", "7/expert", "7/expert/ja/1", "7/impossible/ja"):
        with pytest.raises(ValueError):
            stage.manifest(value)
    with pytest.raises(ValueError):
        stage.normalize({"fonts": "invalid"})
    assert stage.normalize(None) == {"fonts": "open"}
    from tests.pipeline.test_live import live_master_dir

    env = Env(Depot(tmp_path / "d"), {"master": live_master_dir(tmp_path / "m")})
    assert live.LiveStage().parts("7/expert/1", env, ["ja", "ja", "zh-Hant"])[live.UI] == [
        "7/expert/ja",
        "7/expert/zh-Hant",
    ]


def test_static_label_language_changes_the_text_record(tmp_path):
    depot = Depot(tmp_path / "d")
    inputs, table = fixture(depot)
    objects = Objects(table, depot)
    from nnnotes.unity import textstyle

    task = contract.Task(live.UI, 1, "7/expert/zh-Hant", {}, {}, tuple(inputs))
    settings = textfonts.font_settings(task, objects, textstyle.language_fonts_json(sui.LOCALIZE_JSON))
    tables = copy.deepcopy(master())
    tables["MasterText"][-1]["_traditionalChinese"] = ""
    _, records, _ = liveuiproduct.text_records(Objects(table, depot), tables, "7/expert/zh-Hant", settings)
    assert records["CAB-live:150"]["text"] == ""


def test_live_ui_task_key_tracks_font_bytes_and_localized_master_text(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "d")
    path = tmp_path / "font.ttf"
    path.write_bytes(b"font revision one")
    env = Env(depot, {"font_files": {"ja": path}})
    env.params = {"fonts": "open"}
    stage = live.UiStage()
    record = liveuiproduct.record(master(), "7/expert/ja")
    monkeypatch.setattr(stage, "record", lambda subject, env: record)
    monkeypatch.setattr(stage, "font_bundles", lambda subject, env: stored(depot, b"{}", "font-bundles"))
    monkeypatch.setattr(live.units, "root_objects", lambda *a, **kw: [JACKET])
    monkeypatch.setattr(live, "reach", lambda *a, **kw: SimpleNamespace(table={"objects": {}}))

    def task():
        return contract.Task(live.UI, 1, "7/expert/ja", env.params, {}, tuple(stage.more_inputs("7/expert/ja", env)))

    first = task()
    path.write_bytes(b"font revision two")
    second = task()
    assert first.key != second.key
    assert depot.input_bytes(first.input("font:ja")) == b"font revision one"
    record["MasterText"][0]["_japanese"] = "BA"
    assert task().key != second.key
    env.params = {"fonts": "game"}
    assert all(not i.role.startswith("font:") for i in task().inputs)
