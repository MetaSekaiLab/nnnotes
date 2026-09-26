"""The content-addressed store: write-once bytes, results as the commit point, input identities, runs."""
import hashlib
import multiprocessing as mp
import os
from pathlib import Path

import pytest

from nnnotes import contract, store as store_mod
from nnnotes.contract import Input, Task
from nnnotes.store import InputMissing, Store, StoreConflict


def a_result(s: Store, task: Task, payload: bytes) -> dict:
    aid = contract.artifact_id(task.id, "data.bin")
    art = contract.artifact(aid, s.add(payload, "bin"), contract.provenance(task), {"kind": "test"})
    return contract.result(task, [art], [])


def test_put_is_write_once_and_content_addressed(tmp_path):
    s = Store(tmp_path)
    sha = s.put(b"hello")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert s.path(sha) == tmp_path / "cas" / "sha256" / sha[:2] / sha and s.read(sha) == b"hello"
    assert s.put(b"hello") == sha and (s.written, s.reused) == (1, 1)
    assert Store(tmp_path).put(b"hello") == sha                     # another writer finds it
    assert s.add(b"{}", "JSON") == {"sha256": contract.sha256(b"{}"), "size": 2, "mediaType": "application/json",
                                    "ext": "json"}
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(3 << 20))
    sha2, size = s.put_file(src)
    assert s.read(sha2) == src.read_bytes() and size == 3 << 20
    assert not list(tmp_path.rglob("*.part"))


def test_commit_needs_the_bytes_and_is_write_once(tmp_path):
    s = Store(tmp_path)
    task = Task("t.x", 1, "a")
    doc = a_result(s, task, b"payload")
    assert s.result(task.key) is None
    assert s.commit(doc) is True and s.result(task.key) == doc and s.has_result(task.key)
    assert s.result_path(task.key) == tmp_path / "ac" / task.key[:2] / f"{task.key}.json"
    assert s.result_path(task.key).read_bytes() == contract.encode(doc)
    assert s.commit(doc) is False                                   # identical: kept
    other = dict(doc, items=[contract.item("F:1", "unsupported", why=contract.reason("x"))])
    with pytest.raises(StoreConflict):
        s.commit(other)
    t2 = Task("t.x", 1, "b")
    missing = contract.result(t2, [contract.artifact(contract.artifact_id(t2.id, "d.bin"),
                                                     contract.content(b"never stored", "bin"),
                                                     contract.provenance(t2), {"kind": "test"})], [])
    with pytest.raises(RuntimeError, match="not stored"):
        s.commit(missing)
    assert s.result(t2.key) is None


def _crash_after_put(root: str, key_file: str) -> None:
    s = Store(root)
    task = Task("t.x", 1, "crash")
    Path(key_file).write_text(task.key)
    a_result(s, task, b"written before the crash")              # bytes stored, result never committed
    os._exit(9)


def test_a_crash_before_the_result_leaves_no_hit(tmp_path):
    p = mp.get_context("spawn").Process(target=_crash_after_put, args=(str(tmp_path / "s"), str(tmp_path / "k")))
    p.start()
    p.join(60)
    assert p.exitcode == 9
    s = Store(tmp_path / "s")
    key = (tmp_path / "k").read_text()
    assert s.result(key) is None and not s.has_result(key)
    assert s.has(contract.sha256(b"written before the crash"))
    assert not list((tmp_path / "s").rglob("*.part"))


def _writer(root: str, n: int, barrier) -> None:
    s = Store(root)
    barrier.wait()
    for i in range(40):
        task = Task("t.x", 1, f"s{i % 10}")
        s.commit(a_result(s, task, f"blob {i % 10}".encode() * 1000))
        s.put(f"shared {i}".encode())
        s.identify(__file__, "files")


def test_concurrent_writers_across_processes(tmp_path):
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(4)
    procs = [ctx.Process(target=_writer, args=(str(tmp_path), n, barrier)) for n in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0, 0, 0, 0]
    s = Store(tmp_path)
    for i in range(10):
        task = Task("t.x", 1, f"s{i}")
        doc = s.result(task.key)
        assert s.read(doc["artifacts"][0]["content"]["sha256"]) == f"blob {i}".encode() * 1000
    assert len(list((tmp_path / "ac").rglob("*.json"))) == 10
    assert sum(p.is_file() for p in (tmp_path / "cas").rglob("*")) == 50       # 10 blobs, 40 shared
    assert not list(tmp_path.rglob("*.part"))
    lines = (tmp_path / "inputs" / "files.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and all(line.startswith("{") and line.endswith("}") for line in lines)


def test_identify_memoizes_by_path_size_and_mtime(tmp_path, monkeypatch):
    f = tmp_path / "in.bin"
    f.write_bytes(b"abc")
    s = Store(tmp_path / "s")
    assert s.identify(f, "bundles", "in.bin") == (contract.sha256(b"abc"), 3)
    assert s.named("bundles", "in.bin") == (contract.sha256(b"abc"), 3)
    calls = []
    real = store_mod.hashlib.file_digest
    monkeypatch.setattr(store_mod.hashlib, "file_digest", lambda *a: calls.append(1) or real(*a))
    fresh = Store(tmp_path / "s")                               # the memo is on disk
    assert fresh.identify(f) == (contract.sha256(b"abc"), 3) and calls == []
    assert fresh.named("bundles", "in.bin")[1] == 3 and fresh.named("bundles", "other") is None
    f.write_bytes(b"abcd")
    os.utime(f, ns=(1, 1))
    assert fresh.identify(f) == (contract.sha256(b"abcd"), 4) and calls == [1]
    with open(tmp_path / "s" / "inputs" / "files.jsonl", "a", encoding="utf-8") as g:
        g.write('{"torn": ')                                    # a writer that died mid-line
    assert Store(tmp_path / "s").identify(f) == (contract.sha256(b"abcd"), 4)


def test_open_input_tries_locators_in_order(tmp_path):
    s = Store(tmp_path / "s", cache=tmp_path / "cache")
    data = b"bundle bytes"
    sha = contract.sha256(data)
    (tmp_path / "cache" / "bundles").mkdir(parents=True)
    (tmp_path / "cache" / "bundles" / "b.bundle").write_bytes(data)
    (tmp_path / "wrong.bin").write_bytes(b"other bytes!")
    inp = Input("bundle", sha, len(data), "b.bundle",
                ({"kind": "store"}, {"kind": "file", "path": str(tmp_path / "wrong.bin")},
                 {"kind": "cache", "path": "bundles/b.bundle"}))
    assert s.open_input(inp) == tmp_path / "cache" / "bundles" / "b.bundle"
    s.put(data)
    assert s.open_input(inp) == s.path(sha)
    lonely = Input("bundle", contract.sha256(b"x"), 1, "x", ({"kind": "catalog", "catalog": "c", "internalId": "i"},))
    with pytest.raises(InputMissing, match="catalog: no fetcher"):
        s.open_input(lonely)
    (tmp_path / "fetched").write_bytes(b"x")
    s.fetch = lambda inp, loc: tmp_path / "fetched"
    assert s.open_input(lonely) == tmp_path / "fetched"
    with pytest.raises(InputMissing, match="store: absent"):
        s.open_input(Input("x", contract.sha256(b"y"), 1))


def test_runs_costs_and_the_latest_run_of_a_selection(tmp_path):
    s = Store(tmp_path)
    doc = contract.run_doc(context={"region": "r"}, selection=["group:a"], params={}, stages={"t.x": 1},
                           tasks=[{"id": "t.x:a", "key": "0" * 64, "status": "ran"}], layouts={}, summary={})
    s.write_run(doc, [{"event": "x"}])
    assert s.run(doc["id"]) == doc and s.latest_run(["group:a"]) == doc and s.latest_run(["group:b"]) is None
    assert (tmp_path / "runs" / f"{doc['id']}.log.jsonl").read_text(encoding="utf-8") == '{"event": "x"}\n'
    s.write_cost("0" * 64, {"schema": contract.COST, "cpuSeconds": 1.5})
    assert s.cost("0" * 64)["cpuSeconds"] == 1.5 and s.cost("1" * 64) is None
    assert s.catalogs == tmp_path / "catalogs"


def test_a_store_crosses_processes_as_its_settings(tmp_path):
    import pickle
    s = Store(tmp_path / "s", cache=tmp_path / "c")
    s.put(b"x")
    t = pickle.loads(pickle.dumps(s))
    assert (t.root, t.cache, t.written) == (s.root, s.cache, 0)
