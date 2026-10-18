"""Synthetic UI product graphs, library indexes and CLI selection."""

from types import SimpleNamespace as NS

import pytest

from nnnotes.cli import export
from nnnotes.content import ui
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.contract import Task, encode
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import execute, registry
from nnnotes.pipeline.ui import UI, UiStage
from tests.helpers.live2dmodel import ref
from tests.helpers.livenotes import clip, comp, node, table

KEY = "EmbUI/Example/Box"
ROOT, CHILD, CTL, CLIP = (f"example:{i}" for i in range(1, 5))


def graph(tmp_path):
    depot = Depot(tmp_path / "depot")
    doc = {
        "name": "Box",
        "nodes": [
            node("Box", comp("Animator", 10, m_Controller=ref(CTL))),
            node("Box/Image", comp("SpriteRenderer", 11, m_Color={"r": 1, "g": 1, "b": 1, "a": 1})),
        ],
    }
    records = {
        ROOT: ("GameObject", "Box", {"json": doc}),
        CHILD: ("GameObject", "Box", {"json": {"name": "Box", "nodes": [node("Box")]}}),
        CTL: ("AnimatorController", "Show", {"json": {"name": "Show", "clips": [ref(CLIP)]}}),
        CLIP: ("AnimationClip", "Show", {"json": clip("Show", "Image")}),
    }
    return depot, Objects(table(depot, {KEY: [ROOT]}, records), depot)


def test_ui_objects_keep_prefab_and_animation_identity(tmp_path):
    _, objects = graph(tmp_path)
    before = encode(objects.json(ROOT))
    doc, roles = ui.pack(objects, KEY)
    assert doc["roots"] == [ROOT]
    assert doc["prefabs"] == [ROOT, CHILD]
    assert doc["controllers"] == [CTL]
    assert doc["bindings"][ROOT]["Box"][CLIP][0]["path"] == "Image"
    assert doc["bindings"][ROOT]["Box"][CLIP][0]["attribute"] == "m_Color.a"
    assert doc["entries"][0]["nodeCount"] == 2
    assert doc["entries"][0]["classes"] == {"Animator": 1, "SpriteRenderer": 1}
    assert encode(objects.json(ROOT)) == before
    assert roles == {}
    with pytest.raises(KeyError):
        ui.pack(objects, "unknown")


def test_library_indexes_share_dependency_objects_and_distinguish_keys(tmp_path):
    _, objects = graph(tmp_path)
    doc, _ = ui.pack(objects, KEY)
    other = {**doc, "key": KEY + "Alias"}
    index = ui.library([("uipacks/b.json", other), ("uipacks/a.json", doc)])
    assert index["format"] == ui.LIBRARY
    assert len({e["id"] for e in index["assets"]}) == 2
    assert len(index["embedded"]) == len(index["controllers"]) == 1
    assert index["embedded"][0]["id"] == CHILD
    assert index["controllers"][0]["id"] == CTL
    assert index["assets"][0]["roots"] == [ROOT]
    assert ui.library(reversed([("uipacks/b.json", other), ("uipacks/a.json", doc)])) == index


def test_ui_stage_retains_the_graph_in_a_product(tmp_path):
    depot, objects = graph(tmp_path)
    stage = UiStage()
    source = objects.doc
    from nnnotes.pipeline.contract import Input

    body = encode(source)
    task = Task(
        UI, stage.version, KEY, {}, {}, (Input("objects", depot.put(body), len(body), None, ({"kind": "depot"},)),)
    )
    execute(task, depot, registry(stage))
    result = depot.result(task.key)
    path, body = stage.layout_index([(KEY, result)], depot)
    assert path == "index.json"
    assert ui.identity(KEY).encode() in body
    assert stage.INCLUDE_FONTS


def test_ui_command_requests_only_selected_apk_keys(monkeypatch, tmp_path):
    requests = []
    ws = NS(
        run_index=lambda: None,
        index=lambda: {
            "locations": [
                {"id": "apk:1", "primaryKey": KEY, "kind": "asset"},
                {"id": "apk:2", "primaryKey": KEY + "Alias", "kind": "asset"},
                {"id": "remote:1", "primaryKey": "EmbUI/Remote", "kind": "asset"},
            ]
        },
        request=lambda products, selection: requests.append((products, selection)),
    )
    monkeypatch.setattr(export.assets, "Workspace", lambda *a, **kw: ws)
    monkeypatch.setattr(export.assets, "export", lambda *a: None)
    monkeypatch.setattr(export, "use", lambda cfg: None)
    args = NS(limit=1, key=None, prefix="EmbUI/", out=tmp_path)
    export.cmd_ui(args, NS(require_path=lambda *a: tmp_path / "game.apk"))
    assert requests == [({UI: [KEY]}, [f"key:{KEY}"])]
