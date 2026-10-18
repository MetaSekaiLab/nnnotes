"""spot: spot.json and the Spine files of a synthetic situation's object table, and the spot's master record."""

import pytest

from nnnotes.content import spot
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import spotobjects as so


def objects_of(tmp_path, objs=None) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(so.table(depot, objs or so.situation_objects(), [so.SIT_ROOT]), depot)


def translation(x, y, z):
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]  # column-major


def test_master_record():
    rec = spot.master_record(so.MASTER, so.SITUATION)
    assert rec == {
        "row": so.MASTER["MasterHomeSpot"][0],
        "name": {
            "japanese": "カフェ",
            "english": "Cafe",
            "traditionalChinese": "",
            "simplifiedChinese": "",
            "korean": "",
        },
        "tapTalk": {"5": [51], "7": [71, 72]},
    }
    with pytest.raises(ValueError, match="0 MasterHomeSpot rows"):
        spot.master_record(so.MASTER, "Spot/c/c_01/c_01")


def test_spot_document(tmp_path):
    doc, files = spot.spot(objects_of(tmp_path), so.SITUATION, spot.master_record(so.MASTER, so.SITUATION))
    from tests.helpers.schemas import validate

    validate("spot", doc)
    assert doc == {
        "spotId": 3,
        "master": so.MASTER["MasterHomeSpot"][0],
        "name": doc["name"],
        "tapTalk": {"5": [51], "7": [71, 72]},
        "situationSettings": {"backgroundPosition": so.V0, "cameraFov": 30.0},
        "controller": {"_speed": 2.0},
        "characters": [  # by character id; the root at (0, 0, 1)
            {
                "path": "sit/chr5",
                "_characterId": 5,
                "world": translation(2.0, 0.0, 1.0),
                "focusWorld": translation(2.0, 1.0, 1.0),
            },
            {
                "path": "sit/chr7",
                "_characterId": 7,
                "_radius": 0.5,
                "world": translation(0.0, 0.0, 1.0),
                "focusWorld": None,
            },
        ],
        "spineCharacters": [  # hierarchy order
            {"path": "sit/chr7", "skeletonData": None, "animation": None, "world": None},
            {
                "path": "sit/chr5",
                "skeletonData": "chr_SkeletonData",
                "animation": so.ANIMATION,
                "world": translation(2.0, 0.0, 1.0),
            },
        ],
        "skeletons": [  # by name; the shared atlas once
            {
                "name": "chr2_SkeletonData",
                "skeleton": "chr2.json",
                "atlases": ["chr.atlas"],
                "scale": 0.01,
                "defaultMix": 0.2,
                "mixes": [{"from": "a", "to": "b", "duration": 0.5}],
            },
            {
                "name": "chr_SkeletonData",
                "skeleton": "chr.skel",
                "atlases": ["chr.atlas"],
                "scale": 0.01,
                "defaultMix": 0.2,
                "mixes": [{"from": "a", "to": "b", "duration": 0.5}],
            },
        ],
        "matrixConvention": spot.MATRIX_CONVENTION,
    }
    objs = so.situation_objects()
    assert files == {
        "spine/chr2.json": b' {"skeleton": 1}',
        "spine/chr.atlas": so.ATLAS_TEXT.encode(),
        "spine/p1.png": objs[so.PAGE1][2]["image"],
        "spine/p2.png": objs[so.PAGE2][2]["image"],
        "spine/chr.skel": b"\x00binary",
    }


def test_a_skeleton_name_with_its_extension_is_kept(tmp_path):
    objs = so.situation_objects()
    cls, name, files, extra = objs[so.SKEL]
    objs[so.SKEL] = (cls, name, {**files, "json": {"m_Name": "chr.skel"}}, extra)
    _, files = spot.spot(objects_of(tmp_path, objs), so.SITUATION, spot.master_record(so.MASTER, so.SITUATION))
    assert "spine/chr.skel" in files and "spine/chr.skel.skel" not in files


def test_atlas_pages_match_its_materials(tmp_path):
    objs = so.situation_objects()
    cls, name, files, extra = objs[so.ATLAS]
    objs[so.ATLAS] = (cls, name, {"json": {**files["json"], "materials": files["json"]["materials"][:1]}}, extra)
    with pytest.raises(ValueError, match="chr.atlas: 2 pages vs 1 materials"):
        spot.spot(objects_of(tmp_path, objs), so.SITUATION, spot.master_record(so.MASTER, so.SITUATION))


def test_a_situation_without_its_settings_is_an_error(tmp_path):
    objs = so.situation_objects()
    cls, name, files, extra = objs[so.SIT_ROOT]
    doc = files["json"]
    root = {**doc["nodes"][0], "components": doc["nodes"][0]["components"][1:]}
    objs[so.SIT_ROOT] = (cls, name, {"json": {**doc, "nodes": [root, *doc["nodes"][1:]]}}, extra)
    with pytest.raises(ValueError, match="no SpotSituationSettings in the situation prefab"):
        spot.spot(objects_of(tmp_path, objs), so.SITUATION, spot.master_record(so.MASTER, so.SITUATION))


def test_situation_names():
    assert spot.situation_name(so.SITUATION) == "home_a_01"
    for key in (so.BACKGROUND, "Spot/a/b/c"):
        with pytest.raises(ValueError, match="not a spot situation key"):
            spot.situation_name(key)


def test_atlas_pages():
    assert spot.atlas_pages(so.ATLAS_TEXT) == ["p1.png", "p2.png"]
