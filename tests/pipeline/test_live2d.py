"""The Live2D product stages (pipeline.live2d) run on a synthetic object table."""

import json
import subprocess
import sys

import pytest

from nnnotes.pipeline import contract, layout
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.live2d import CubismStage, ModelStage, cubism_kind
from nnnotes.pipeline.stages import execute, registry
from tests.helpers import live2dmodel as lm


def run(tmp_path, stage):
    depot = Depot(tmp_path / "d")
    data = contract.encode(lm.table(depot))
    inp = Input("objects", depot.put(data), len(data), None, ({"kind": "depot"},))
    t = Task(stage.name, stage.version, lm.KEY, {}, {}, (inp,))
    execute(t, depot, registry(stage), force=True)
    doc = depot.result(t.key)
    arts = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    return depot, t, doc, arts


def test_the_stages_start_from_the_key_and_the_mask_materials():
    model, cubism = ModelStage(), CubismStage()
    assert model.keys(lm.KEY, None) == [lm.KEY] and cubism.keys(lm.KEY, None) == [lm.KEY]
    assert model.resources(lm.KEY, None) == ["Live2D/Cubism/Materials/Mask", "Live2D/Cubism/Materials/MaskCulling"]
    assert cubism.resources(lm.KEY, None) == []
    assert model.manifest(lm.KEY) == "models/m001.json" and cubism.manifest(lm.KEY) == "cubism/m001.json"
    with pytest.raises(ValueError, match="not a Live2D model key"):
        model.keys("Character/Live2D/c001/m001/other", None)


def test_the_model_product(tmp_path):
    depot, t, doc, arts = run(tmp_path, ModelStage())
    assert set(arts) == {"model.json", "objects"} and arts["model.json"]["semantics"]["kind"] == "live2d.model"
    model = contract.loads(depot.read(arts["model.json"]["content"]["sha256"]))
    assert model["prefab"] == lm.ROOT and model["format"] == 3
    table = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))
    assert lm.SCRIPT not in table["objects"]
    shader = table["objects"][lm.SHADER]["files"]
    assert "typetree" not in shader and "program:vulkan/s0p0_progVertex_4" not in shader
    assert doc["items"][0]["class"] == "Live2DModel" and doc["items"][0]["status"] == "exported"
    manifest = layout.product_manifest(t.id, doc, table)
    assert manifest["product"] == "live2d.model" and set(manifest["files"]) == {"model.json"}
    assert lm.TEX0 in manifest["objects"] and "image" in manifest["objects"][lm.TEX0]["files"]


def test_the_cubism_product(tmp_path):
    depot, t, doc, arts = run(tmp_path, CubismStage())
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "m001.moc3": "live2d.moc3",
        "m001.model3.json": "live2d.model3",
        "m001.physics3.json": "live2d.physics3",
        "motions/idle.motion3.json": "live2d.motion",
        "smile.exp3.json": "live2d.expression",
        "textures/texture_00.png": "live2d.texture",
        "textures/texture_01.png": "live2d.texture",
        "objects": "product.objects",
    }
    table = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))
    assert table["objects"] == {}
    model3 = json.loads(depot.read(arts["m001.model3.json"]["content"]["sha256"]))
    assert model3["FileReferences"]["Moc"] == "m001.moc3"
    assert doc["items"][0]["class"] == "CubismModel"


def test_cubism_kinds():
    assert cubism_kind("x.model3.json") == "live2d.model3" and cubism_kind("textures/a.png") == "live2d.texture"
    with pytest.raises(ValueError, match="not a Cubism SDK file"):
        cubism_kind("other.txt")


def test_planning_the_stages_does_not_import_unitypy():
    code = (
        "import sys, nnnotes.pipeline.live2d as m; m.ModelStage(); m.CubismStage(); "
        "print(sorted(x for x in ('UnityPy', 'numpy', 'PIL') if x in sys.modules))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stderr[-2000:]
