"""The spot product stages (pipeline.spot) run on synthetic object tables."""

import json
import subprocess
import sys

import pytest

from nnnotes.content import spot
from nnnotes.pipeline import contract, layout
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.spot import RoomStage, SpotStage
from nnnotes.pipeline.stages import Env, execute, registry
from nnnotes.pipeline.views import ViewError
from tests.helpers import spotobjects as so


def master_dir(tmp_path):
    md = tmp_path / "master"
    md.mkdir()
    for name, rows in so.MASTER.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return md


def run(depot, stage, subject, objs, roots, *more):
    data = contract.encode(so.table(depot, objs, roots))
    inp = Input("objects", depot.put(data), len(data), None, ({"kind": "depot"},))
    t = Task(stage.name, stage.version, subject, {}, {}, (inp, *more))
    execute(t, depot, registry(stage), force=True)
    doc = depot.result(t.key)
    arts = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    return t, doc, arts


def test_the_stages_start_from_their_key():
    rooms, spots = RoomStage(), SpotStage()
    assert rooms.keys(so.BACKGROUND, None) == [so.BACKGROUND] and spots.keys(so.SITUATION, None) == [so.SITUATION]
    assert rooms.manifest(so.BACKGROUND) == "rooms/home_a.json"
    assert spots.manifest(so.SITUATION) == "spots/home_a_01.json"
    keys = [so.BACKGROUND, so.SITUATION, "Spot/b/b_01/b_01", "Spot/a/b/c", "Character/x"]
    assert rooms.select(keys) == [so.BACKGROUND]
    assert spots.select(keys) == ["Spot/b/b_01/b_01", so.SITUATION]
    assert rooms.MASTER == () and spots.MASTER == spot.MASTER_TABLES
    with pytest.raises(ValueError, match="not a spot situation key"):
        spots.keys(so.BACKGROUND, None)


def test_the_room_product(tmp_path):
    depot = Depot(tmp_path / "d")
    t, doc, arts = run(depot, RoomStage(), so.BACKGROUND, so.room_objects(), [so.ROOM_ROOT])
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "room.glb": "spot.room",
        "room.json": "spot.room-summary",
        "objects": "product.objects",
    }
    assert arts["room.glb"]["content"]["ext"] == "glb"
    summary = contract.loads(depot.read(arts["room.json"]["content"]["sha256"]))
    assert summary["meshCount"] == 3 and summary["glb"] == "room.glb"
    table = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))
    assert table["objects"] == {}
    assert doc["items"][0]["class"] == "SpotRoom" and doc["items"][0]["status"] == "exported"
    manifest = layout.product_manifest(t.id, doc, table)
    assert manifest["product"] == "room.gltf" and set(manifest["files"]) == {"room.glb", "room.json"}


def test_the_spot_product_reads_its_master_record(tmp_path):
    depot = Depot(tmp_path / "d")
    stage = SpotStage()
    env = Env(depot, {"master": master_dir(tmp_path)})
    (master,) = stage.more_inputs(so.SITUATION, env)
    assert master.role == "master"
    assert contract.loads(depot.input_bytes(master)) == spot.master_record(so.MASTER, so.SITUATION)
    t, doc, arts = run(depot, stage, so.SITUATION, so.situation_objects(), [so.SIT_ROOT], master)
    assert {p: (a["semantics"]["kind"], a["content"]["ext"]) for p, a in arts.items()} == {
        "spot.json": ("spot.situation", "json"),
        "spine/chr2.json": ("spine.skeleton", "json"),
        "spine/chr.skel": ("spine.skeleton", "skel"),
        "spine/chr.atlas": ("spine.atlas", "atlas"),
        "spine/p1.png": ("spine.texture", "png"),
        "spine/p2.png": ("spine.texture", "png"),
        "objects": ("product.objects", "json"),
    }
    doc_json = contract.loads(depot.read(arts["spot.json"]["content"]["sha256"]))
    assert doc_json["spotId"] == 3 and doc_json["tapTalk"] == {"5": [51], "7": [71, 72]}
    assert doc["items"][0]["class"] == "SpotSituation"


def test_a_master_table_the_master_data_lacks_is_an_error(tmp_path):
    md = master_dir(tmp_path)
    (md / "MasterText.json").unlink()
    with pytest.raises(ViewError, match="master table MasterText is not in the master data"):
        SpotStage().more_inputs(so.SITUATION, Env(Depot(tmp_path / "d"), {"master": md}))


def test_planning_the_stages_does_not_import_unitypy():
    code = (
        "import sys, nnnotes.pipeline.spot as m; m.RoomStage(); m.SpotStage(); "
        "print(sorted(x for x in ('UnityPy', 'numpy', 'PIL') if x in sys.modules))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stderr[-2000:]
