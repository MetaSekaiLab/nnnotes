"""Read-set task execution against a synthetic Node reader and isolated input trees."""

import json
import shutil

import pytest

from nnnotes.pipeline import contract, readsets
from nnnotes.pipeline.contract import Input
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env, describe, execute
from nnnotes.site.reads import Reader

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Node is not installed")

SCRIPT = """import fs from 'node:fs';
const args=process.argv.slice(2),doc=JSON.parse(fs.readFileSync(args[0],'utf8')),plan=args.includes('--plan');
if(plan&&doc.planError){console.error('plan unavailable');process.exit(1);}
const settings=JSON.parse((args.find(a=>a.startsWith('--settings='))||'--settings={}').slice(11));
const files=[...new Set([...(plan?(doc.plan||doc.files):doc.files),...(settings.Alternate?['extra.json']:[])])];
console.log(JSON.stringify({chart:args[0],seconds:Date.now(),mode:plan?'plan':'full',
state:doc.state||(plan?'planned':'ended'),frames:plan?0:12,readAfterLoad:1,read:files.length,files,keys:['doc#value']}));
"""


def fixture(tmp_path, doc=None):
    depot = Depot(tmp_path / "depot")
    player = tmp_path / "player"
    (player / "scripts").mkdir(parents=True)
    (player / "package.json").write_text('{"type":"module"}')
    (player / "scripts/read-set.mjs").write_text(SCRIPT)
    body = json.dumps(doc or {"files": ["model:1#json", "scene.json"]}).encode()
    entries = [{"path": "lives/one.json", "sha256": depot.put(body), "size": len(body)}]
    return depot, player, entries


def task_for(depot, player, entries, mode="plan"):
    spec = contract.encode(readsets.request("lives/one.json", mode=mode))
    inputs = [
        *readsets.player_inputs(depot, player),
        *readsets.data_inputs(entries),
        Input("request", depot.put(spec), len(spec), None, ({"kind": "depot"},)),
    ]
    return describe(readsets.ReadSetStage(), "one", None, Env(depot, {readsets.READ_SET: {"one": inputs}}))


def test_readset_result_excludes_execution_paths_and_wall_clock(tmp_path):
    depot, player, entries = fixture(tmp_path)
    task = task_for(depot, player, entries)
    stages = {readsets.READ_SET: readsets.ReadSetStage()}
    execute(task, depot, stages)
    result = depot.result(task.key)
    art = result["artifacts"][0]
    doc = json.loads(depot.read(art["content"]["sha256"]))
    assert doc == {
        "mode": "plan",
        "state": "planned",
        "frames": 0,
        "readAfterLoad": 1,
        "read": 2,
        "files": ["model:1#json", "scene.json"],
    }
    assert execute(task, depot, stages).status == "hit"
    execute(task, depot, stages, force=True)
    assert depot.result(task.key) == result
    assert not list((depot.root / "tmp").iterdir())


def test_readset_key_covers_player_source_data_and_node(tmp_path, monkeypatch):
    depot, player, entries = fixture(tmp_path)
    task = task_for(depot, player, entries)
    (player / "scripts/read-set.mjs").write_text(SCRIPT + "\n// extra\n")
    assert task_for(depot, player, entries).key != task.key
    (player / "scripts/read-set.mjs").write_text(SCRIPT)
    assert task_for(depot, player, entries).key == task.key
    assert task_for(depot, player, entries, "full").key != task.key
    monkeypatch.setattr(readsets, "node_identity", lambda _: "other")
    assert task_for(depot, player, entries).key != task.key
    with pytest.raises(contract.IncompatibleTask, match="Node"):
        execute(task, depot, {readsets.READ_SET: readsets.ReadSetStage()})


def test_reader_checks_plan_and_unions_variant_files(tmp_path):
    depot, player, entries = fixture(tmp_path)
    reader = Reader(depot, player)
    assert reader.read(entries, "lives/one.json", variants=[{"Alternate": True}], check=True) == [
        "extra.json",
        "model:1#json",
        "scene.json",
    ]
    assert reader.checked == 1 and not reader.mismatches
    assert len(reader.run.tasks) == 3
    again = Reader(depot, player)
    again.read(entries, "lives/one.json", variants=[{"Alternate": True}], check=True)
    assert again.run.summary()["ran"] == 0


def test_plan_mismatch_uses_full_results_for_default_and_variants(tmp_path):
    depot, player, entries = fixture(tmp_path, {"files": ["late.json"], "plan": ["early.json"]})
    reader = Reader(depot, player)
    assert reader.read(entries, "lives/one.json", variants=[{"Alternate": True}], check=True) == [
        "extra.json",
        "late.json",
    ]
    assert reader.mode == "full"
    assert reader.mismatches == [{"entry": "lives/one.json", "missing": ["late.json"], "extra": ["early.json"]}]


def test_failed_plan_falls_back_to_full_and_incomplete_full_fails(tmp_path):
    depot, player, entries = fixture(tmp_path, {"files": ["scene.json"], "planError": True})
    reader = Reader(depot, player)
    assert reader.read(entries, "lives/one.json") == ["scene.json"]
    assert len(reader.plan_errors) == 1
    body = json.dumps({"files": ["scene.json"], "state": "playing"}).encode()
    entries[0].update(sha256=depot.put(body), size=len(body))
    with pytest.raises(readsets.ReadSetError, match="did not reach ended"):
        reader.read(entries, "lives/one.json")


@pytest.mark.parametrize("value", ["../file", "/file", "a/../file", "a\\file", "C:/file"])
def test_relative_readset_paths(value):
    with pytest.raises(ValueError, match="relative"):
        readsets.request(value)
