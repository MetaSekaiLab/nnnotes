"""Site publication order, interruption recovery and ownership over synthetic files."""

import json

import pytest

from nnnotes.pipeline import layout
from nnnotes.pipeline.depot import Depot
from nnnotes.site import publish


def entries(depot, files):
    return [{"path": path, "sha256": depot.put(data), "size": len(data), "id": path} for path, data in files.items()]


def test_publish_orders_assets_and_manifests_before_indexes(tmp_path, monkeypatch):
    depot, out = Depot(tmp_path / "depot"), tmp_path / "site"
    files = {
        "charts.json": b"index",
        "charts/1.json": b"alias",
        "products/one.json": b"product",
        "assets/one.json": b"asset",
    }
    calls, place = [], layout._place

    def record(src, dst, *args):
        calls.append(dst.relative_to(out).as_posix())
        return place(src, dst, *args)

    monkeypatch.setattr(layout, "_place", record)
    result = publish.materialize(out, entries(depot, files), depot)
    assert not result["failed"]
    assert calls == ["assets/one.json", "products/one.json", "charts/1.json", "charts.json"]


@pytest.mark.parametrize("fail_at", ["assets/new.json", "products/new.json", "charts/1.json"])
def test_failed_publish_keeps_indexes_and_retired_assets_then_recovers(tmp_path, monkeypatch, fail_at):
    depot, out = Depot(tmp_path / "depot"), tmp_path / "site"
    old = {"assets/old.json": b"old", "products/old.json": b"old", "charts/1.json": b"old", "charts.json": b"old"}
    first = publish.materialize(out, entries(depot, old), depot)
    (out / "custom.txt").write_text("user file")
    new = {"assets/new.json": b"new", "products/new.json": b"new", "charts/1.json": b"new", "charts.json": b"new"}
    place = layout._place

    def fail(src, dst, *args):
        if dst.relative_to(out).as_posix() == fail_at:
            raise OSError("write failed")
        return place(src, dst, *args)

    monkeypatch.setattr(layout, "_place", fail)
    with pytest.raises(layout.LayoutError, match="could not publish"):
        publish.materialize(out, entries(depot, new), depot)
    assert (out / "charts.json").read_bytes() == b"old"
    assert (out / "assets/old.json").read_bytes() == b"old"
    assert layout.read_manifest(out) == first["manifest"]
    assert (out / layout.JOURNAL).is_file()
    monkeypatch.setattr(layout, "_place", place)
    result = publish.materialize(out, entries(depot, new), depot)
    assert not result["failed"]
    assert (out / "charts.json").read_bytes() == b"new"
    assert not (out / "assets/old.json").exists()
    assert not (out / layout.JOURNAL).exists()
    assert (out / "custom.txt").read_text() == "user file"


def test_new_snapshot_collects_unreferenced_files_from_an_interrupted_attempt(tmp_path, monkeypatch):
    depot, out = Depot(tmp_path / "depot"), tmp_path / "site"
    place = layout._place

    def fail(src, dst, *args):
        if dst.name == "charts.json":
            raise OSError("index locked")
        return place(src, dst, *args)

    monkeypatch.setattr(layout, "_place", fail)
    with pytest.raises(layout.LayoutError):
        publish.materialize(out, entries(depot, {"assets/abandoned.json": b"data", "charts.json": b"index"}), depot)
    monkeypatch.setattr(layout, "_place", place)
    publish.materialize(out, entries(depot, {"charts.json": b"{}"}), depot)
    assert not (out / "assets/abandoned.json").exists()
    assert json.loads((out / "charts.json").read_bytes()) == {}


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../outside", "a\\outside", "C:/outside"])
def test_site_paths_are_confined_to_the_output(tmp_path, path):
    depot = Depot(tmp_path / "depot")
    with pytest.raises(layout.LayoutError, match="relative path"):
        publish.materialize(tmp_path / "site", entries(depot, {path: b"x"}), depot)
    assert not (tmp_path / "site").exists()
