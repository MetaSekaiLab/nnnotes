"""The collection of the depot: what kept runs reach stays, the rest goes."""

import json
import os
import socket
import time

import pytest

from nnnotes import cli
from nnnotes.pipeline import contract
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot


def commit(d: Depot, task: Task, payload: bytes) -> dict:
    aid = contract.artifact_id(task.id, "data.bin")
    art = contract.artifact(aid, d.add(payload, "bin"), contract.provenance(task), {"kind": "test"})
    doc = contract.result(task, [art], [])
    d.commit(doc)
    return doc


def write_run(d: Depot, selection, tasks, layouts=None, mtime=None) -> dict:
    doc = contract.run_doc(
        context={"region": "r"},
        selection=selection,
        params={},
        stages={"t.x": 1},
        tasks=[{"id": t.id, "key": t.key, "status": "ran"} for t in tasks],
        layouts=layouts or {},
        summary={},
    )
    p = d.write_run(doc)
    if mtime is not None:
        os.utime(p, ns=(mtime, mtime))
    return doc


def test_gc_keeps_what_the_kept_runs_reach(tmp_path):
    d = Depot(tmp_path)
    stored = d.put(b"a stored input")
    new = Task("t.x", 1, "a", inputs=(Input("doc", stored, 14, None, ({"kind": "depot"},)),))
    old = Task("t.x", 1, "b")
    new_art = commit(d, new, b"new")["artifacts"][0]["content"]["sha256"]
    old_art = commit(d, old, b"old")["artifacts"][0]["content"]["sha256"]
    orphan = d.put(b"no result names it")
    first = write_run(d, ["group:a"], [old], mtime=1_000_000_000)
    latest = write_run(d, ["group:a"], [new], mtime=2_000_000_000)

    counted = d.gc(keep=1, dry_run=True, grace=0)
    assert counted["runs"] == {"kept": 1, "removed": 1} and counted["contents"] == {"kept": 2, "removed": 2}
    assert d.run(first["id"]) is not None and d.has(orphan)  # a dry run removes nothing

    r = d.gc(keep=1, grace=0)
    assert r["runs"] == {"kept": 1, "removed": 1} and r["results"] == {"kept": 1, "removed": 1}
    assert r["contents"] == {"kept": 2, "removed": 2}
    assert d.run(first["id"]) is None and d.run(latest["id"]) is not None
    assert d.result(new.key) is not None and d.result(old.key) is None
    assert d.has(new_art) and d.has(stored) and not d.has(old_art) and not d.has(orphan)


def test_gc_keeps_every_run_by_default_and_the_latest_of_a_selection(tmp_path):
    d = Depot(tmp_path)
    a, b = Task("t.x", 1, "a"), Task("t.x", 1, "b")
    commit(d, a, b"a")
    commit(d, b, b"b")
    write_run(d, ["group:a"], [a], mtime=2_000_000_000)
    latest = write_run(d, ["group:a"], [b], mtime=1_000_000_000)  # written last: the selection's latest run
    assert d.gc()["results"] == {"kept": 2, "removed": 0}
    r = d.gc(keep=1)
    assert r["runs"] == {"kept": 2, "removed": 0} and d.latest_run(["group:a"]) == latest


def test_gc_keeps_the_files_of_a_layout_a_run_names(tmp_path):
    d = Depot(tmp_path)
    placed = d.put(b"a derived file")
    manifest = d.add(contract.encode({"files": [{"path": "x.json", "sha256": placed, "size": 14}]}), "json")
    write_run(d, ["group:a"], [], layouts={"original": manifest["sha256"]})
    assert d.gc()["contents"] == {"kept": 2, "removed": 0} and d.has(placed)


def test_the_gc_command_runs_only_when_no_other_command_does(tmp_path, capsys):
    d = Depot(tmp_path)
    orphan = d.put(b"no run reaches it")
    other = tmp_path / "leases" / "other.json"
    other.parent.mkdir()
    other.write_text(json.dumps({"host": socket.gethostname() + "-other", "pid": 1, "started": time.time()}))
    argv = ["--depot", str(tmp_path), "depot", "gc", "--grace", "0", "--json"]
    with pytest.raises(SystemExit, match="another command uses the depot"):
        cli.main(argv)
    assert d.path(orphan).exists()
    other.unlink()
    cli.main(argv)
    assert json.loads(capsys.readouterr().out)["contents"]["removed"] == 1 and not d.path(orphan).exists()
    assert not list((tmp_path / "leases").iterdir())


def test_the_gc_command_collects_the_remote_with_remote(tmp_path, capsys, monkeypatch):
    argv = ["--depot", str(tmp_path / "d"), "depot", "gc", "--remote", "--json"]
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert "the depot has no remote" in capsys.readouterr().err
    monkeypatch.setenv("NNNOTES_DEPOT_REMOTE", (tmp_path / "remote").as_uri())
    cli.main(argv)
    r = json.loads(capsys.readouterr().out)
    assert r["remote"]["runs"] == {"kept": 0, "removed": 0} and r["contents"] == {"kept": 0, "removed": 0}


def test_recent_result_keeps_older_inputs_and_artifacts_until_grace_expires(tmp_path):
    d = Depot(tmp_path)
    stored = d.put(b"old input")
    task = Task("t.x", 1, "a", inputs=(Input("doc", stored, 9, None, ({"kind": "depot"},)),))
    doc = commit(d, task, b"old output")
    art = doc["artifacts"][0]["content"]["sha256"]
    old = time.time() - 172800
    for sha in (stored, art):
        os.utime(d.path(sha), (old, old))
    assert d.gc()["results"] == {"kept": 1, "removed": 0}
    assert d.path(stored).exists() and d.path(art).exists()
    os.utime(d.result_path(task.key), (old, old))
    assert d.gc()["results"] == {"kept": 0, "removed": 1}
    assert not d.path(stored).exists() and not d.path(art).exists()


@pytest.mark.parametrize("fail", [False, True])
def test_command_records_keep_completed_results_and_content_even_on_failure(tmp_path, fail):
    from nnnotes.pipeline.depot import command_run

    depot = Depot(tmp_path)
    task = Task("t.x", 1, "a")
    try:
        with command_run(depot, "example"):
            other = Depot(tmp_path)
            content = other.put(b"read input")
            commit(other, task, b"completed output")
            if fail:
                raise RuntimeError("later operation failed")
    except RuntimeError:
        assert fail
    recorded = depot.latest_run(["command:example"])
    assert recorded["context"]["outcome"] == ("failed" if fail else "complete")
    depot.gc(keep=1, grace=0)
    assert depot.result(task.key) is not None and depot.read(content) == b"read input"


def test_cli_dry_run_preserves_downloads_and_writes_no_command_record(tmp_path, capsys):
    depot = Depot(tmp_path)
    path = depot.downloads / "bundles" / "example.bundle"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source bytes")
    orphan = depot.put(b"orphan")
    cli.main(["--depot", str(tmp_path), "depot", "gc", "--dry-run", "--grace", "0", "--json"])
    assert json.loads(capsys.readouterr().out)["contents"]["removed"] == 1
    assert depot.path(orphan).exists() and path.read_bytes() == b"source bytes"
    assert not list((tmp_path / "runs").rglob("*.json"))
