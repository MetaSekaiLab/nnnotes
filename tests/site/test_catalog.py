"""Incremental catalogs, publication recovery and depot reachability over synthetic products."""

import json

import pytest

from nnnotes.pipeline import contract, layout
from nnnotes.pipeline.depot import Depot
from nnnotes.site import catalog, pages
from nnnotes.site.build import Builder
from nnnotes.site.products import Snapshot
from tests.site.test_products import graph, product


def model(depot, subject="sample", value=1):
    path = f"models/{subject}.json"
    manifests = {path: product(depot, "live2d.model", subject, {"model.json": {"value": value}})}
    rows = []
    for p, doc in manifests.items():
        body = contract.encode(doc)
        rows.append({"path": p, "sha256": depot.put(body), "size": len(body), "id": p})
    return path, rows


@pytest.fixture
def setup(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "depot")
    monkeypatch.setattr(pages, "gather", lambda *a, **kw: ({"index.html": b"page"}, {}))

    def builder():
        return Builder(depot, "example", "en", [{"id": "one", "name": "One"}], tmp_path)

    return depot, tmp_path / "site", builder


def test_partial_build_retains_topics_and_complete_selection_replaces_group(setup):
    depot, out, make = setup
    first = make()
    for name in ["a", "b"]:
        path, rows = model(depot, name)
        first.add(rows, path, "model", name, "one", {"key": name})
    first.finish(out)
    second = make()
    path, rows = model(depot, "a", 2)
    second.add(rows, path, "model", "a", "one", {"key": "a"})
    second.finish(out)
    listing = json.loads((out / "models.json").read_bytes())
    assert [m["id"] for m in listing["models"]] == ["a", "b"]
    third = make()
    third.replace.add(("one", "model"))
    third.add(rows, path, "model", "a", "one", {"key": "a"})
    third.finish(out)
    assert not (out / "models/b.json").exists()
    assert [t["id"] for t in catalog.load(depot, "example")[0]["topics"]] == ["a"]


def test_failed_publication_preserves_pointer_and_recovers(setup, monkeypatch):
    depot, out, make = setup
    first = make()
    path, rows = model(depot)
    first.add(rows, path, "model", "sample", "one", {})
    first.finish(out)
    prior = depot.latest_run(["site:example"])
    second = make()
    path, rows = model(depot, value=2)
    second.add(rows, path, "model", "sample", "one", {})
    place = layout._place

    def fail(src, dst, *args):
        if dst.name == "models.json":
            raise OSError("index unavailable")
        return place(src, dst, *args)

    # Change the listing too, so its replacement exercises the failure path.
    second.topics[0]["entry"]["label"] = "Updated"
    monkeypatch.setattr(layout, "_place", fail)
    with pytest.raises(layout.LayoutError, match="could not publish"):
        second.finish(out)
    assert depot.latest_run(["site:example"])["id"] == prior["id"]
    monkeypatch.setattr(layout, "_place", place)
    second.finish(out)
    assert depot.latest_run(["site:example"])["id"] != prior["id"]
    assert not (out / layout.JOURNAL).exists()


def test_saved_complete_layout_survives_collection_and_restores_new_directory(setup, tmp_path):
    depot, out, make = setup
    first = make()
    path, rows = model(depot)
    first.add(rows, path, "model", "sample", "one", {})
    first.finish(out)
    second = make()
    second.finish(out)
    depot.gc(keep=1, grace=0)
    third = make()
    restored = tmp_path / "restored"
    third.finish(restored)
    assert (out / "models/sample.json").read_bytes() == (restored / "models/sample.json").read_bytes()
    assert {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} == {
        p.relative_to(restored).as_posix() for p in restored.rglob("*") if p.is_file()
    }
    from tests.pipeline.test_contract import validators

    schemas = validators()
    assert not list(schemas[contract.RUN].iter_errors(depot.latest_run(["site:example"])))
    assert not list(schemas["nnnotes.site/1"].iter_errors(catalog.load(depot, "example")[0]))


def test_equal_region_products_share_alias_and_different_products_get_regional_roots(tmp_path):
    depot = Depot(tmp_path)
    first = Snapshot(depot, graph(depot), encoding="none")
    target = first.product("stories/1.json")
    second_sources = graph(depot)
    second_sources["stories/1.json"]["files"]["episode.json"] = product(
        depot, "story", "1", {"episode.json": {"text": "variant"}}
    )["files"]["episode.json"]
    second = Snapshot(depot, second_sources)
    other = second.product("stories/1.json")
    regions = [{"id": r, "name": r} for r in ["one", "two", "three"]]
    topics = [
        {"kind": "story", "id": "1", "region": r, "manifest": p, "entry": {"advId": 1}}
        for r, p in [("one", target), ("two", target), ("three", other)]
    ]
    doc = catalog.merge(None, "example", "ja", regions, topics)
    entries, _ = catalog.assemble(depot, doc, {**first.entries, **second.entries})
    listing = json.loads(depot.read(entries["stories.json"]["sha256"]))["stories"]
    assert [(e["manifest"], e["regions"]) for e in listing] == [
        ("stories/1.json", ["one", "two"]),
        ("stories/three/1.json", ["three"]),
    ]
    variant = json.loads(depot.read(entries["stories/three/1.json"]["sha256"]))
    assert variant["root"] == "../../"
    assert all(p in entries for p in first.entries)


def test_fresh_depot_restores_retained_topics_from_remote(tmp_path, monkeypatch):
    from nnnotes.pipeline.remote import open_remote

    monkeypatch.setattr(pages, "gather", lambda *a, **kw: ({"index.html": b"page"}, {}))
    remote = open_remote((tmp_path / "remote").as_uri())
    first = Depot(tmp_path / "first", remote=remote)
    args = ("example", "en", [{"id": "one", "name": "One"}], tmp_path)
    builder = Builder(first, *args)
    path, rows = model(first)
    builder.add(rows, path, "model", "sample", "one", {})
    builder.finish(tmp_path / "out1")
    second = Depot(tmp_path / "second", remote=remote)
    again = Builder(second, *args)
    again.finish(tmp_path / "out2")
    assert (tmp_path / "out1/models/sample.json").read_bytes() == (tmp_path / "out2/models/sample.json").read_bytes()


def test_model_names_may_differ_across_regions_for_the_same_content(tmp_path):
    depot = Depot(tmp_path)
    source, rows = model(depot)
    from nnnotes.site.build import documents

    snapshot = Snapshot(depot, documents(depot, rows))
    target = snapshot.product(source)
    regions = [{"id": r, "name": r} for r in ("one", "two")]
    topics = [
        {
            "kind": "model",
            "id": "sample",
            "region": r["id"],
            "manifest": target,
            "entry": {"key": "sample", "label": r["name"]},
        }
        for r in regions
    ]
    doc = catalog.merge(None, "example", "en", regions, topics)
    entries, _ = catalog.assemble(depot, doc, snapshot.entries)
    listing = json.loads(depot.read(entries["models.json"]["sha256"]))["models"]
    assert [(row["label"], row["regions"]) for row in listing] == [("one", ["one"]), ("two", ["two"])]
    assert "models/sample.json" in entries and "models/two/sample.json" in entries


def test_current_region_order_takes_precedence_and_unused_regions_are_dropped():
    regions = [{"id": r, "name": r} for r in ("one", "two", "unused")]
    topics = [{"region": "one", "kind": "model", "id": "a"}]
    old = catalog.merge(None, "example", "en", regions, topics)
    updated = catalog.merge(old, "example", "en", [regions[1], regions[0]], [])
    assert [r["id"] for r in updated["regions"]] == ["two", "one"]
    partial = catalog.merge(old, "example", "en", [regions[1]], [])
    assert [r["id"] for r in partial["regions"]] == ["two", "one"] and partial["topics"] == topics
