"""Site asset bytes, task identities and placement records over synthetic content."""

import json

import pytest

from nnnotes.formats import compression
from nnnotes.pipeline import contract, siteassets
from nnnotes.pipeline.contract import Input
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env, describe, execute


def task_for(depot, data, ext="json", params=None, name="first"):
    inp = Input("source", depot.put(data), len(data), name, ({"kind": "depot"},))
    subject = siteassets.subject(inp, ext)
    env = Env(depot, {"site_assets": {subject: inp}})
    return describe(siteassets.AssetStage(), subject, params, env)


def run(depot, data, ext="json", params=None):
    task = task_for(depot, data, ext, params)
    execute(task, depot, {siteassets.ASSET: siteassets.AssetStage()})
    result = depot.result(task.key)
    return task, result, siteassets.entry(result, depot)


def restored(depot, result, entry):
    by_path = {p["path"]: p for p in siteassets.placements(result)}

    def read(path, size, stored=None):
        p = by_path[path]
        raw = depot.read(p["sha256"])
        assert len(raw) == p["size"] == (size if stored is None else stored)
        data = compression.decode(path, raw)
        assert len(data) == size
        assert contract.sha256(data) == path.split("/")[1].split(".")[0]
        return data

    if "parts" not in entry:
        return read(entry["asset"], entry["size"], entry.get("stored"))
    body = b"{" + b",".join(json.dumps(k).encode() + b":" + read(*p) for k, *p in entry["parts"]) + b"}"
    assert len(body) == entry["size"]
    return body


@pytest.mark.parametrize("encoding", ["none", "gzip", "br"])
def test_assets_reconstruct_projected_json_from_depot_bytes(tmp_path, encoding):
    depot = Depot(tmp_path)
    doc = {"title": "text " * 1000, "nested": {"value": 9, "note": "label"}, "signed": -12.5}
    _, result, entry = run(
        depot, json.dumps(doc, indent=2).encode(), params={"encoding": encoding, "omit": [["nested", "note"]]}
    )
    expected = {**doc, "nested": {"value": 9}}
    assert json.loads(restored(depot, result, entry)) == expected
    assert ("stored" in entry) == (encoding != "none")
    assert all(p["path"].startswith("assets/") for p in siteassets.placements(result))


def test_split_parts_share_content_and_reconstruct_exact_text(tmp_path):
    depot = Depot(tmp_path)
    data = b'{"a": [1,2,3], "b": [1,2,3], "c": {"x":true}}'
    _, result, entry = run(depot, data, params={"split": 10})
    assert len(entry["parts"]) == 3
    assert entry["parts"][0][1] == entry["parts"][1][1]
    assert len(siteassets.placements(result)) == 2
    assert restored(depot, result, entry) == b'{"a":[1,2,3],"b":[1,2,3],"c":{"x":true}}'


@pytest.mark.parametrize("data", [b'{"a\\"b":1,"c":2}', b"[1,2,3]", b'{"only":123456}', b"{}"])
def test_json_without_splittable_top_level_keys_stays_whole(tmp_path, data):
    depot = Depot(tmp_path)
    _, result, entry = run(depot, data, params={"split": 1})
    assert "parts" not in entry
    assert json.loads(restored(depot, result, entry)) == json.loads(data)


def test_binary_and_small_files_keep_original_bytes(tmp_path):
    depot = Depot(tmp_path)
    for data, ext in [(bytes(range(256)), "png"), (b"a", "bin")]:
        _, result, entry = run(depot, data, ext)
        assert "stored" not in entry
        assert restored(depot, result, entry) == data


def test_task_keys_follow_content_transform_and_encoder_identity(tmp_path, monkeypatch):
    depot = Depot(tmp_path)
    data = b'{"x":1}'
    first = task_for(depot, data)
    assert first.key == task_for(depot, data, name="second").key
    assert first.key != task_for(depot, b'{"x":2}').key
    assert first.key != task_for(depot, data, params={"encoding": "none"}).key
    assert first.key != task_for(depot, data, params={"split": 0}).key
    assert first.key != task_for(depot, data, params={"omit": [["x"]]}).key
    monkeypatch.setattr(siteassets.zlib, "ZLIB_RUNTIME_VERSION", "different")
    assert first.key != task_for(depot, data).key
    with pytest.raises(contract.IncompatibleTask, match="encoder"):
        execute(first, depot, {siteassets.ASSET: siteassets.AssetStage()})


def test_task_replay_reuses_result_and_forced_execution_is_identical(tmp_path):
    depot = Depot(tmp_path)
    task, result, _ = run(depot, b'{"a":1}')
    registry = {siteassets.ASSET: siteassets.AssetStage()}
    assert execute(task, depot, registry).status == "hit"
    assert execute(task, depot, registry, force=True).status == "ran"
    assert depot.result(task.key) == result


def test_site_assets_are_available_to_the_task_runner():
    from nnnotes.cli.assets import installed

    stages = installed([siteassets.ASSET])
    assert isinstance(stages.stages[siteassets.ASSET], siteassets.AssetStage)
    assert not stages.missing


def test_site_asset_task_and_result_obey_shared_schemas(tmp_path):
    from tests.pipeline.test_contract import validators

    depot = Depot(tmp_path)
    task, result, _ = run(depot, b'{"a":"' + b"x" * 1024 + b'"}')
    schemas = validators()
    for name, doc in [
        (contract.TASK, task.to_json()),
        (contract.RESULT, result),
        *((contract.ARTIFACT, a) for a in result["artifacts"]),
    ]:
        assert not list(schemas[name].iter_errors(doc))


@pytest.mark.parametrize(
    "params", [{"encoding": "zip"}, {"split": -1}, {"split": True}, {"omit": ["x"]}, {"omit": [[]]}]
)
def test_asset_parameters_are_validated_before_execution(params):
    with pytest.raises(ValueError):
        siteassets.AssetStage().normalize(params)
