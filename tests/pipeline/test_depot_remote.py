"""A depot with a remote: what the local depot writes goes to the remote too, and a depot that lacks something reads it
from the remote and keeps it."""

from datetime import UTC, datetime

import pytest

from nnnotes.pipeline import contract
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot, DepotConflict
from nnnotes.pipeline.remote import FileRemote, open_remote


def commit(d: Depot, task: Task, payload: bytes) -> dict:
    aid = contract.artifact_id(task.id, "data.bin")
    art = contract.artifact(aid, d.add(payload, "bin"), contract.provenance(task), {"kind": "test"})
    doc = contract.result(task, [art], [])
    d.commit(doc)
    return doc


def run_doc(selection, tasks) -> dict:
    return contract.run_doc(
        context={"region": "r"},
        selection=selection,
        params={},
        stages={"t.x": 1},
        tasks=[{"id": t.id, "key": t.key, "status": "ran"} for t in tasks],
        layouts={},
        summary={},
    )


def test_open_remote_names_a_directory_or_a_bucket(tmp_path):
    r = open_remote(tmp_path.as_uri())
    assert isinstance(r, FileRemote) and r.root == tmp_path
    s3 = open_remote("s3://bucket/some/prefix", endpoint="https://storage.example", key_id="KEYID", secret="SECRET")
    assert (s3.bucket, s3.prefix) == ("bucket", "some/prefix/")
    assert "KEYID" not in repr(s3) and "SECRET" not in repr(s3)
    for bad in ("http://example/x", "s3:///x", "file://host/share"):
        with pytest.raises(ValueError):
            open_remote(bad)


def test_a_second_depot_finds_the_results_and_contents_of_the_first(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    first, second = Depot(tmp_path / "a", remote=remote), Depot(tmp_path / "b", remote=remote)
    stored = first.put(b"a stored input")
    task = Task("t.x", 1, "a", inputs=(Input("doc", stored, 14, None, ({"kind": "depot"},)),))
    doc = commit(first, task, b"payload")
    run = run_doc(["group:a"], [task])
    first.write_run(run)
    first.identify(first.path(stored), "bundles", "x_0123.bundle")

    art = doc["artifacts"][0]["content"]["sha256"]
    assert not second.path(art).exists() and second.has(art, 7) and second.has_result(task.key)
    assert second.result(task.key) == doc and second.read(art) == b"payload"
    assert second.path(art).is_file()  # kept locally
    assert second.open_input(task.inputs[0]).read_bytes() == b"a stored input"
    assert second.latest_run(["group:a"]) == run and second.run(run["id"]) == run
    assert second.named("bundles", "x_0123.bundle") == (stored, 14)
    assert second.named("bundles", "other.bundle") is None


class Counting(FileRemote):
    """A directory remote that counts the objects it is asked the size of."""

    def __init__(self, url, root):
        super().__init__(url, root)
        self.asked = []

    def size(self, key):
        self.asked.append(key)
        return super().size(key)


def test_remote_results_are_checked_independently_of_the_run_pointer(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    first = Depot(tmp_path / "a", remote=remote)
    tasks = [Task("t.x", 1, n) for n in "abc"]
    for t in tasks:
        commit(first, t, t.subject.encode())
    first.write_run(run_doc(["group:a"], tasks))
    counting = Counting(remote.url, remote.root)
    second = Depot(tmp_path / "b", remote=counting)
    assert second.latest_run(["group:a"])["id"] == run_doc(["group:a"], tasks)["id"]
    assert all(second.has_result(t.key) for t in tasks) and len(counting.asked) == 3
    assert not second.has_result(Task("t.x", 1, "z").key) and len(counting.asked) == 4
    remote.delete([f"ac/{tasks[0].key[:2]}/{tasks[0].key}.json"])
    fresh = Depot(tmp_path / "c", remote=remote)
    assert fresh.latest_run(["group:a"]) is not None
    assert not fresh.has_result(tasks[0].key)


def test_sharing_a_cached_run_uploads_results_layouts_and_stored_inputs(tmp_path):
    first = Depot(tmp_path / "a")
    inp = first.put(b"input")
    task = Task("t.x", 1, "a", inputs=(Input("doc", inp, 5, None, ({"kind": "depot"},)),))
    result = commit(first, task, b"payload")
    placed = first.put(b"layout file")
    manifest = first.put(contract.encode({"files": [{"sha256": placed, "size": 11}]}))
    run = run_doc(["group:a"], [task])
    run["tasks"][0]["status"] = "hit"
    run["layouts"] = {"product": manifest}
    remote = open_remote((tmp_path / "remote").as_uri())
    Depot(first.root, remote=remote).write_run(run)
    second = Depot(tmp_path / "b", remote=remote)
    assert second.latest_run(["group:a"]) == run
    assert second.committed(task.key, task.id) == result
    assert second.open_input(task.inputs[0]).read_bytes() == b"input"
    assert second.read(placed) == b"layout file" and second.read(manifest)


@pytest.mark.parametrize("quick", [False, True])
def test_verify_checks_referenced_remote_contents_without_materializing(tmp_path, quick):
    remote = open_remote((tmp_path / "remote").as_uri())
    first, second = Depot(tmp_path / "a", remote=remote), Depot(tmp_path / "b", remote=remote)
    task = Task("t.x", 1, "a")
    doc = commit(first, task, b"payload")
    assert second.result(task.key) == doc
    assert second.verify(quick=quick)["problemCount"] == 0
    assert not (second.root / "cas").exists()
    sha = doc["artifacts"][0]["content"]["sha256"]
    remote.put(f"cas/sha256/{sha[:2]}/{sha}", b"invalid")
    assert second.verify(quick=False)["problemCount"] == 1


@pytest.mark.parametrize("dry_run", [True, False])
def test_remote_collection_does_not_fill_the_local_cache(tmp_path, dry_run):
    remote = open_remote((tmp_path / "remote").as_uri())
    first = Depot(tmp_path / "a", remote=remote)
    task = Task("t.x", 1, "a")
    commit(first, task, b"payload")
    first.write_run(run_doc(["group:a"], [task]))
    second = Depot(tmp_path / "b", remote=remote)
    assert second.gc(remote=True, dry_run=dry_run)["remote"]["results"]["kept"] == 1
    assert not second.root.exists()


def test_a_depot_crosses_processes_with_its_remote(tmp_path):
    import pickle

    remote = open_remote((tmp_path / "remote").as_uri())
    d = pickle.loads(pickle.dumps(Depot(tmp_path / "a", remote=remote)))
    assert isinstance(d.remote, FileRemote) and d.remote.root == remote.root
    assert "remote=" in repr(d)


def test_contents_stored_before_the_remote_go_to_it_with_their_result(tmp_path):
    task = Task("t.x", 1, "a")
    local = Depot(tmp_path / "a")
    aid = contract.artifact_id(task.id, "data.bin")
    art = contract.artifact(aid, local.add(b"payload", "bin"), contract.provenance(task), {"kind": "test"})
    remote = open_remote((tmp_path / "remote").as_uri())
    Depot(tmp_path / "a", remote=remote).commit(contract.result(task, [art], []))
    assert Depot(tmp_path / "b", remote=remote).read(art["content"]["sha256"]) == b"payload"


def test_a_content_of_the_remote_is_checked_against_its_id(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    sha = Depot(tmp_path / "a", remote=remote).put(b"right")
    remote.put(f"cas/sha256/{sha[:2]}/{sha}", b"wrong")
    with pytest.raises(RuntimeError, match="differs from its id"):
        Depot(tmp_path / "b", remote=remote).read(sha)


def test_s3_requests_name_the_bucket_and_the_prefixed_keys():
    pytest.importorskip("boto3")
    from botocore.stub import Stubber

    r = open_remote("s3://bucket/depot", endpoint="https://storage.example", key_id="k", secret="s")
    written = datetime(2026, 1, 2, tzinfo=UTC)
    with Stubber(r._s3) as stub:
        stub.add_response("head_object", {"ContentLength": 3}, {"Bucket": "bucket", "Key": "depot/ac/ab/x.json"})
        stub.add_client_error(
            "head_object",
            service_error_code="404",
            http_status_code=404,
            expected_params={"Bucket": "bucket", "Key": "depot/ac/ab/y.json"},
        )
        stub.add_response("put_object", {}, {"Bucket": "bucket", "Key": "depot/runs/r.json", "Body": b"{}"})
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "depot/cas/sha256/ab/abc", "Size": 5, "LastModified": written}],
                "IsTruncated": False,
            },
            {"Bucket": "bucket", "Prefix": "depot/cas/"},
        )
        stub.add_response(
            "delete_objects",
            {},
            {"Bucket": "bucket", "Delete": {"Objects": [{"Key": "depot/cas/sha256/ab/abc"}], "Quiet": True}},
        )
        assert r.size("ac/ab/x.json") == 3 and r.size("ac/ab/y.json") is None
        r.put("runs/r.json", b"{}")
        assert list(r.list("cas/")) == [("cas/sha256/ab/abc", 5, written.timestamp())]
        r.delete(["cas/sha256/ab/abc"])
        stub.assert_no_pending_responses()


def test_a_result_differing_from_the_remote_one_is_a_conflict(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    task = Task("t.x", 1, "a")
    commit(Depot(tmp_path / "a", remote=remote), task, b"one")
    with pytest.raises(DepotConflict):
        commit(Depot(tmp_path / "b", remote=remote), task, b"two")


def test_gc_of_the_remote_keeps_what_its_kept_runs_reach(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    d = Depot(tmp_path / "a", remote=remote)
    kept, gone = Task("t.x", 1, "a"), Task("t.x", 1, "b")
    commit(d, kept, b"kept")
    commit(d, gone, b"gone")
    d.write_run(run_doc(["group:a"], [kept]))
    assert Depot(tmp_path / "b", remote=remote).gc(remote=True)["remote"]["results"] == {"kept": 2, "removed": 0}
    r = Depot(tmp_path / "b", remote=remote).gc(remote=True, grace=0)
    assert r["remote"]["results"] == {"kept": 1, "removed": 1} and r["remote"]["contents"] == {"kept": 1, "removed": 1}
    assert (
        remote.size(f"ac/{kept.key[:2]}/{kept.key}.json") and remote.size(f"ac/{gone.key[:2]}/{gone.key}.json") is None
    )
