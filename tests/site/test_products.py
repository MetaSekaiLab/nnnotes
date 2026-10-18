"""Immutable product graphs and task replay with synthetic data."""

import copy
import json

import pytest

from nnnotes.formats import compression
from nnnotes.pipeline import contract
from nnnotes.pipeline.depot import Depot
from nnnotes.site.products import Snapshot


def file(depot, doc):
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode()
    return {"asset": f"assets/{depot.put(data)}.json", "size": len(data)}


def product(depot, kind, subject, files, objects=None):
    return {
        "schema": contract.PRODUCT,
        "product": kind,
        "subject": subject,
        "files": {k: file(depot, v) for k, v in files.items()},
        "objects": objects or {},
    }


def graph(depot):
    return {
        "stories/1.json": product(
            depot,
            "story",
            "1",
            {
                "story.json": {
                    "format": 1,
                    "advId": 1,
                    "parts": {
                        "scene": "storyscenes/default.json",
                        "ui": {"ja": "storyui/ja.json", "en": "storyui/en.json"},
                        "fonts": {"ja": "storyfonts/1_ja.json", "en": "storyfonts/1_en.json"},
                    },
                },
                "episode.json": {"advId": 1, "text": "storyscenes/default.json"},
            },
        ),
        "storyscenes/default.json": product(depot, "story.scene", "default", {"scene.json": {"field": 1}}),
        "storyui/ja.json": product(depot, "story.ui", "ja", {"ui.json": {"language": "ja"}}),
        "storyfonts/1_ja.json": product(depot, "story.fonts", "1/ja", {"fonts.json": {"language": "ja"}}),
    }


def read_file(snapshot, entry):
    def read(path):
        data = snapshot.depot.read(snapshot.entries[path]["sha256"])
        return compression.decode(path, data)

    if "parts" not in entry:
        return json.loads(read(entry["asset"]))
    return {k: json.loads(read(p)) for k, p, *_ in entry["parts"]}


def test_snapshot_routes_exact_products_and_only_available_languages(tmp_path):
    depot = Depot(tmp_path)
    manifests = graph(depot)
    before = copy.deepcopy(manifests)
    snapshot = Snapshot(depot, manifests, split=1)
    root = snapshot.product("stories/1.json")
    assert root.startswith("products/")
    man = snapshot.documents["stories/1.json"]
    story = read_file(snapshot, man["files"]["story.json"])
    assert story["parts"]["scene"] == snapshot.paths["storyscenes/default.json"]
    assert story["parts"]["ui"] == {"ja": snapshot.paths["storyui/ja.json"]}
    assert story["parts"]["fonts"] == {"ja": snapshot.paths["storyfonts/1_ja.json"]}
    assert man["language"] == "ja"
    assert read_file(snapshot, man["files"]["episode.json"])["text"] == "storyscenes/default.json"
    assert manifests == before
    assert snapshot.run.summary()["ran"] == len(snapshot.run.tasks)
    assert not snapshot.run.failures()
    from tests.pipeline.test_contract import validators

    schemas = validators()
    assert not list(schemas[contract.RUN].iter_errors(snapshot.run.manifest()))
    for _, result in snapshot.run.results():
        assert not list(schemas[contract.RESULT].iter_errors(result))


def test_shared_objects_and_product_snapshots_reuse_tasks(tmp_path):
    depot = Depot(tmp_path)
    manifests = graph(depot)
    shared = {"class": "MonoBehaviour", "name": "Shared", "files": {"json": file(depot, {"x": 1})}}
    manifests["storyui/ja.json"]["objects"] = {"example:1": shared}
    manifests["storyscenes/default.json"]["objects"] = {"example:1": shared}
    first = Snapshot(depot, manifests)
    old = first.product("stories/1.json")
    count = len(first.run.tasks)
    assert first.product("stories/1.json") == old and len(first.run.tasks) == count
    a = first.documents["storyui/ja.json"]["objects"]["example:1"]["files"]["json"]
    b = first.documents["storyscenes/default.json"]["objects"]["example:1"]["files"]["json"]
    assert a == b
    second = Snapshot(depot, manifests)
    assert second.product("stories/1.json") == old
    assert second.entries == first.entries
    assert second.run.summary()["hit"] == count
    assert second.run.summary()["ran"] == 0


def test_dependency_change_updates_parent_reference_without_changing_other_products(tmp_path):
    depot = Depot(tmp_path)
    manifests = graph(depot)
    old = Snapshot(depot, manifests)
    old.product("stories/1.json")
    manifests["storyscenes/default.json"]["files"]["scene.json"] = file(depot, {"field": 2})
    new = Snapshot(depot, manifests)
    new.product("stories/1.json")
    assert new.paths["stories/1.json"] != old.paths["stories/1.json"]
    assert new.paths["storyscenes/default.json"] != old.paths["storyscenes/default.json"]
    assert new.paths["storyui/ja.json"] == old.paths["storyui/ja.json"]


def test_aliases_include_their_relative_site_root(tmp_path):
    depot = Depot(tmp_path)
    snapshot = Snapshot(depot, graph(depot))
    snapshot.alias("stories/1.json", "stories/1.json")
    snapshot.alias("stories/1.json", "stories/region/1.json")
    for path, root in [("stories/1.json", "../"), ("stories/region/1.json", "../../")]:
        man = json.loads(depot.read(snapshot.entries[path]["sha256"]))
        assert man["root"] == root
        assert read_file(snapshot, man["files"]["story.json"])["parts"]["scene"] in snapshot.entries
    with pytest.raises(ValueError, match="relative manifest"):
        snapshot.alias("stories/1.json", "../outside.json")


def test_requested_missing_language_or_product_fails_without_a_public_layout(tmp_path):
    depot = Depot(tmp_path)
    manifests = graph(depot)
    with pytest.raises(ValueError, match="missing product"):
        Snapshot(depot, manifests, languages=["en"]).product("stories/1.json")
    with pytest.raises(ValueError, match="missing languages"):
        Snapshot(depot, manifests, languages=["ko"]).product("stories/1.json")
    manifests.pop("storyscenes/default.json")
    with pytest.raises(ValueError, match="missing product"):
        Snapshot(depot, manifests).product("stories/1.json")


def test_cycles_are_reported(tmp_path):
    depot = Depot(tmp_path)
    manifests = {
        path: product(depot, "live", path, {"live.json": {"parts": {"scene": other}}})
        for path, other in [("lives/a.json", "lives/b.json"), ("lives/b.json", "lives/a.json")]
    }
    with pytest.raises(ValueError, match="cyclic"):
        Snapshot(depot, manifests).product("lives/a.json")


def test_home_host_and_story_share_the_same_room_and_spot_snapshots(tmp_path):
    depot = Depot(tmp_path)
    manifests = graph(depot)
    root = manifests["stories/1.json"]
    story = json.loads(depot.read(root["files"]["story.json"]["asset"].split("/")[1].split(".")[0]))
    story["parts"].update(
        host={"home/1": "storyhosts/home_1.json"}, rooms={"room": "rooms/room.json"}, spots={"spot": "spots/spot.json"}
    )
    root["files"]["story.json"] = file(depot, story)
    manifests["storyhosts/home_1.json"] = product(
        depot,
        "story.host",
        "home/1",
        {
            "host.json": {"home": {"parts": {"room": "rooms/room.json", "spot": "spots/spot.json"}}},
        },
    )
    manifests["rooms/room.json"] = product(depot, "room.gltf", "room", {"room.json": {"name": "room"}})
    manifests["spots/spot.json"] = product(depot, "spot.files", "spot", {"spot.json": {"name": "spot"}})
    snapshot = Snapshot(depot, manifests)
    snapshot.product("stories/1.json")
    host = read_file(snapshot, snapshot.documents["storyhosts/home_1.json"]["files"]["host.json"])
    assert host["home"]["parts"] == {
        "room": snapshot.paths["rooms/room.json"],
        "spot": snapshot.paths["spots/spot.json"],
    }


def test_live_selection_preserves_documents_and_only_read_binary_roles(tmp_path):
    depot = Depot(tmp_path)
    data = b"pixels"
    blob = {"asset": f"assets/{depot.put(data)}.bin", "size": len(data)}
    objects = {"a:1": {"files": {"json": file(depot, {"value": True}), "data": blob}}}
    manifests = {"notes/default.json": product(depot, "live.notes", "default", {"notes.json": {}}, objects)}
    snapshot = Snapshot(depot, manifests, read_files=[])
    snapshot.product("notes/default.json")
    kept = snapshot.documents["notes/default.json"]
    assert set(kept["files"]) == {"notes.json"}
    assert set(kept["objects"]["a:1"]["files"]) == {"json"}
    other = Snapshot(depot, manifests, read_files=["a:1#data"])
    other.product("notes/default.json")
    assert set(other.documents["notes/default.json"]["objects"]["a:1"]["files"]) == {"json", "data"}


def test_live_ui_routes_its_scene_reference(tmp_path):
    depot = Depot(tmp_path)
    manifests = {
        "liveui/a.json": product(depot, "live.ui", "a", {"ui.json": {"scene": {"manifest": "scenes/default.json"}}}),
        "scenes/default.json": product(depot, "live.scene", "default", {"scene.json": {}}),
    }
    snapshot = Snapshot(depot, manifests)
    snapshot.product("liveui/a.json")
    ui = read_file(snapshot, snapshot.documents["liveui/a.json"]["files"]["ui.json"])
    assert ui["scene"]["manifest"] == snapshot.paths["scenes/default.json"]
