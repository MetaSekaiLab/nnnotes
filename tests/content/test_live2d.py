"""Live2D models assembled from a synthetic object table (content.live2d)."""

import json

import pytest

from nnnotes.content import live2d
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import live2dmodel as lm
from tests.helpers.schemas import validate

KEY_A = "Character/Live2D/001_adv/adv_model_a/model/adv_model_a"
KEY_B = "Character/Live2D/sub_x/adv_model_b/model/adv_model_b"


def objects_of(tmp_path, objs=None, roots=(lm.ROOT, lm.MASK_MAT, lm.CULL_MAT)) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(lm.table(depot, objs, roots), depot)


def test_model_document(tmp_path):
    doc, keep, roles = live2d.model(objects_of(tmp_path), lm.KEY)
    validate("model-json", doc)
    assert doc == {
        "format": live2d.MODEL_FORMAT,
        "name": "m001",
        "key": lm.KEY,
        "prefab": lm.ROOT,
        "moc": lm.MOC,
        "canvas": {
            "pixelsPerUnit": 100.0,
            "originX": 0.5,
            "originY": 0.5,
            "width": 20.0,
            "height": 30.0,
            "mocVersion": 5,
        },
        "textures": [lm.TEX0, lm.TEX1],  # by name
        "shaders": {"Live2D Cubism/Mask": lm.MASK_SHADER, "Live2D Cubism/Unlit": lm.SHADER},
        "resources": {"cubismMask": lm.MASK_MAT, "cubismMaskCulling": lm.CULL_MAT},
        "motionSync": True,
        "bindings": {lm.CLIP: [{"path": "Parameters/ParamEyeLOpen", "attribute": "Value"}]},
    }
    assert lm.SCRIPT not in keep and keep == set(lm.objects()) - {lm.SCRIPT}


def test_the_player_reads_the_programs_of_the_drawn_keywords(tmp_path):
    _, _, roles = live2d.model(objects_of(tmp_path), lm.KEY)
    programs = {v["role"] for v in lm.SHADER_VARIANTS if v["platform"] == "gles3"}  # each set alone and with ALV
    assert roles[lm.SHADER] == {"json", "index"} | programs  # no typetree, no other platform
    assert roles[lm.MASK_SHADER] == {"json", "index", "program:gles3/s0p0_progVertex_0"}  # a drawable is masked


def test_unmasked_models_read_no_mask_program(tmp_path):
    objs = lm.objects()
    cls, name, files = objs[lm.MAT1]
    objs[lm.MAT1] = (cls, name, {"json": {**files["json"], "keywords": []}})
    _, _, roles = live2d.model(objects_of(tmp_path, objs), lm.KEY)
    assert roles[lm.MASK_SHADER] == {"json", "index"}
    assert roles[lm.SHADER] == {"json", "index", "program:gles3/s0p0_progVertex_0", "program:gles3/s0p0_progVertex_2"}


def test_a_model_without_motion_sync(tmp_path):
    objs = lm.objects()
    prefab = objs[lm.ROOT][2]["json"]
    prefab["nodes"][0]["components"] = [
        c for c in prefab["nodes"][0]["components"] if c.get("script") is None or "MotionSync" not in c["script"]
    ]
    doc, _, _ = live2d.model(objects_of(tmp_path, objs), lm.KEY)
    assert doc["motionSync"] is False


def test_a_keyword_set_without_a_program_is_an_error(tmp_path):
    objs = lm.objects()
    shader = objs[lm.SHADER][2]
    shader["index"] = {**shader["index"], "variants": [v for v in lm.SHADER_VARIANTS if v["keywords"] != [lm.ALV]]}
    with pytest.raises(ValueError, match="Live2D Cubism/Unlit: 0 GLES3 programs"):
        live2d.model(objects_of(tmp_path, objs), lm.KEY)


def test_the_roots_name_the_model_and_the_mask_materials(tmp_path):
    with pytest.raises(ValueError, match="0 GameObjects among the roots"):
        live2d.model(objects_of(tmp_path, roots=(lm.MASK_MAT, lm.CULL_MAT)), lm.KEY)
    with pytest.raises(ValueError, match="'MaskCulling' among the roots"):
        live2d.model(objects_of(tmp_path, roots=(lm.ROOT, lm.MASK_MAT)), lm.KEY)
    with pytest.raises(ValueError, match="not a Live2D model key"):
        live2d.model(objects_of(tmp_path), "Character/Live2D/c001/m001/model/other")


def test_cubism_files(tmp_path):
    files = live2d.cubism(objects_of(tmp_path), lm.KEY)
    assert sorted(files) == [
        "m001.moc3",
        "m001.model3.json",
        "m001.physics3.json",
        "motions/idle.motion3.json",
        "smile.exp3.json",
        "textures/texture_00.png",
        "textures/texture_01.png",
    ]
    assert files["m001.moc3"] == lm.moc3() and files["textures/texture_00.png"] == b"png0"
    model3 = json.loads(files["m001.model3.json"])
    assert model3 == {
        "Version": 3,
        "FileReferences": {
            "Moc": "m001.moc3",
            "Textures": ["textures/texture_00.png", "textures/texture_01.png"],
            "Physics": "m001.physics3.json",
            "Expressions": [{"Name": "smile", "File": "smile.exp3.json"}],
            "Motions": {"idle": [{"File": "motions/idle.motion3.json", "FadeInTime": 0.5}]},
        },
        "Groups": [
            {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen"]},
            {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
        ],
    }
    motion = json.loads(files["motions/idle.motion3.json"])
    assert motion["Meta"]["Fps"] == 30.0 and motion["Curves"][0]["Id"] == "ParamEyeLOpen"
    assert json.loads(files["smile.exp3.json"])["Parameters"] == [
        {"Id": "ParamEyeLOpen", "Value": 0.0, "Blend": "Multiply"}
    ]
    physics = json.loads(files["m001.physics3.json"])
    assert physics["Meta"]["PhysicsSettingCount"] == 1 and physics["PhysicsSettings"][0]["Output"][0]["Type"] == "Angle"


def test_pages_that_share_a_name_are_numbered(tmp_path):
    objs = lm.objects()
    cls, _, files = objs[lm.TEX1]
    objs[lm.TEX1] = (cls, "texture_00", files)
    files_ = live2d.cubism(objects_of(tmp_path, objs), lm.KEY)
    assert [p for p in sorted(files_) if p.startswith("textures/")] == [
        "textures/texture_00-2.png",
        "textures/texture_00.png",
    ]


def test_model_id_is_the_model_name():
    assert live2d.model_id(KEY_A) == "adv_model_a"
    for bad in (
        "Character/Live2D/001_adv/a/model/b",
        "Character/Live2D/a/model/a",
        "Other/001/a/model/a",
        "Character/Live2D/001_adv/a/model/a/extra",
    ):
        with pytest.raises(ValueError, match="not a Live2D model key"):
            live2d.model_id(bad)
    with pytest.raises(ValueError, match="not URL-safe"):
        live2d.model_id("Character/Live2D/g/a b/model/a b")


def test_model_keys_and_select():
    keys = [KEY_B, "Character/Live2D/001_adv/adv_model_a/motion/x", KEY_A, "Live/Score/1"]
    models = live2d.model_keys(keys)
    assert models == {"adv_model_a": KEY_A, "adv_model_b": KEY_B}
    assert list(models) == ["adv_model_a", "adv_model_b"]
    assert live2d.select_models(None, models) == models
    assert live2d.select_models([KEY_B, "adv_model_a"], models) == models
    assert live2d.select_models(["adv_model_b"], models) == {"adv_model_b": KEY_B}
    with pytest.raises(ValueError, match="nope"):
        live2d.select_models(["adv_model_a", "nope"], models)
    with pytest.raises(ValueError, match="model id adv_model_a"):
        live2d.model_keys([KEY_A, "Character/Live2D/002_adv/adv_model_a/model/adv_model_a"])
