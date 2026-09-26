"""Plan: statuses, reasons against the previous run, --check, emitted tasks (toy stages of test_stages)."""
from pathlib import Path

from nnnotes import contract
from nnnotes.contract import Task
from nnnotes.orchestrate import Orchestrator
from nnnotes.plan import plan, why
from nnnotes.store import Store
from test_stages import PIPELINE, toy_files, toy_stages

TEXTS = {"a": "alpha\nbeta", "b": "gamma", "c": "delta\n?x"}


def setup(tmp_path, texts=TEXTS):
    store = Store(tmp_path / "store")
    facts = {"files": toy_files(tmp_path / "in", texts)}
    return store, facts


def run(store, facts, stages=None, params=None, selection=("all",)):
    r = Orchestrator(store, stages or toy_stages(), log=lambda m: None).run(
        PIPELINE, params=params, facts=facts, selection=list(selection))
    return r.write()


def nodes(p) -> dict:
    return {n["id"]: (n["status"], n["reasons"]) for n in p.doc["nodes"]}


def test_a_cold_plan_runs_the_first_stage_and_knows_nothing_after_it(tmp_path):
    store, facts = setup(tmp_path)
    p = plan(toy_stages(), PIPELINE, store, facts=facts)
    n = nodes(p)
    assert n["toy.src:a"] == ("run", [{"code": "new-input"}])
    assert n["toy.count:a"] == ("unknown", [{"code": "pending", "task": "toy.src:a"}])
    assert n["toy.total:all"] == ("unknown", [{"code": "pending", "task": "toy.count:a"}])
    assert [x["id"] for x in p.doc["nodes"]] == ["toy.src:a", "toy.src:b", "toy.src:c", "toy.count:a",
                                                 "toy.count:b", "toy.count:c", "toy.total:all"]
    assert ["toy.src:a", "toy.count:a"] in p.doc["edges"] and ["toy.count:c", "toy.total:all"] in p.doc["edges"]
    s = p.doc["summary"]
    assert (s["tasks"], s["hit"], s["run"], s["unknown"]) == (7, 0, 3, 4)
    assert s["cpuSeconds"] == round(sum(t.cost.cpu_seconds for t in p.tasks), 3)
    assert p.check() == 1 and [t.id for t in p.tasks] == ["toy.src:a", "toy.src:b", "toy.src:c"]


def test_unchanged_inputs_are_all_hits(tmp_path):
    store, facts = setup(tmp_path)
    prev = run(store, facts)
    p = plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev)
    assert all(v == ("hit", [{"code": "unchanged"}]) for v in nodes(p).values())
    assert p.check() == 0 and p.tasks == [] and p.emit(tmp_path / "none") == []
    assert "7 tasks: 7 hits, 0 to run, 0 unknown" in p.text()


def test_a_stage_bump_reruns_it_and_leaves_downstream_pending(tmp_path):
    store, facts = setup(tmp_path)
    prev = run(store, facts)
    bumped = toy_stages(Count=2)
    n = nodes(plan(bumped, PIPELINE, store, facts=facts, previous=prev))
    assert n["toy.src:a"][0] == "hit"
    assert n["toy.count:a"] == ("run", [{"code": "stage-version", "old": 1, "new": 2}])
    assert n["toy.total:all"][0] == "unknown"
    after = run(store, facts, bumped)
    ran = {t["id"] for t in after["tasks"] if t["status"] == "ran"}
    assert ran == {"toy.count:a", "toy.count:b", "toy.count:c", "toy.total:all"}
    assert plan(bumped, PIPELINE, store, facts=facts, previous=after).check() == 0


def test_parameter_atom_input_and_context_reasons(tmp_path):
    store, facts = setup(tmp_path)
    prev = run(store, facts)
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev, params={"toy.src": {"suffix": "!"}}))
    assert n["toy.src:b"] == ("run", [{"code": "params-changed", "path": "/suffix", "old": "", "new": "!"}])

    stages = toy_stages()
    stages["toy.src"].ATOMS = {"upper": "toy-upper/2"}
    n = nodes(plan(stages, PIPELINE, store, facts=facts, previous=prev))
    assert n["toy.src:a"] == ("run", [{"code": "atom-changed", "atom": "upper", "old": "toy-upper/1",
                                       "new": "toy-upper/2"}])

    Path(facts["files"]["b"]).write_bytes(b"GAMMA!")
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev))
    (status, reasons), = [n["toy.src:b"]]
    assert status == "run" and reasons[0]["code"] == "input-changed" and reasons[0]["role"] == "file"
    assert reasons[0]["new"] == contract.sha256(b"GAMMA!") and reasons[0]["name"] == "b.txt"
    assert n["toy.src:a"][0] == "hit" and n["toy.count:b"][0] == "unknown"

    n = nodes(plan(toy_stages(), PIPELINE, store, facts=dict(facts, context={"c": "x"}), previous=prev))
    assert n["toy.src:c"] == ("run", [{"code": "context-changed", "entry": "c"}])


def test_new_failed_forced_and_cached_reasons(tmp_path):
    store, facts = setup(tmp_path, {"a": "alpha", "b": "CRASH"})
    prev = run(store, facts)
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev))
    assert n["toy.src:b"] == ("run", [{"code": "previous-failed"}])
    assert n["toy.src:a"] == ("hit", [{"code": "unchanged"}])
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev, force=["toy.src:a"]))
    assert n["toy.src:a"] == ("run", [{"code": "forced"}])
    facts["files"].update(toy_files(tmp_path / "more", {"z": "new"}))
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=prev))
    assert n["toy.src:z"] == ("run", [{"code": "new-input"}])
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts))           # no previous run: hits are cached
    assert n["toy.src:a"] == ("hit", [{"code": "cached"}])


def test_the_latest_run_of_the_selection_is_the_default_previous(tmp_path):
    store, facts = setup(tmp_path)
    prev = run(store, facts, selection=["group:x"])
    assert store.latest_run(["group:x"])["id"] == prev["id"]
    n = nodes(plan(toy_stages(), PIPELINE, store, facts=facts, previous=store.latest_run(["group:x"])))
    assert n["toy.src:a"] == ("hit", [{"code": "unchanged"}])


def test_why_lists_every_difference():
    old = Task("s.x", 1, "a", {"png": {"level": 6}, "sprites": True}, {"t": "u/1", "gone": "g/1"}).key_parts()
    new = Task("s.x", 2, "a", {"png": {"level": 9}, "sprites": True}, {"t": "u/1", "new": "n/1"},
               context={"scripts": {"x": 1}}).key_parts()
    assert why(old, new) == [
        {"code": "stage-version", "old": 1, "new": 2},
        {"code": "atom-changed", "atom": "gone", "old": "g/1", "new": None},
        {"code": "atom-changed", "atom": "new", "old": None, "new": "n/1"},
        {"code": "params-changed", "path": "/png/level", "old": 6, "new": 9},
        {"code": "context-changed", "entry": "scripts"}]
    assert why(old, old) == []


def test_emitted_tasks_are_runnable_descriptions(tmp_path):
    store, facts = setup(tmp_path)
    p = plan(toy_stages(), PIPELINE, store, facts=facts)
    files = p.emit(tmp_path / "tasks")
    assert [f.name for f in files] == sorted(f"{t.key}.json" for t in p.tasks)
    for f in files:
        t = Task.from_json(contract.loads(f.read_bytes()))
        assert f.read_bytes() == contract.encode(t.to_json())


def test_peak_estimate_oversize_and_layout_counts(tmp_path):
    store, facts = setup(tmp_path, {"a": "x" * 100, "b": "y" * 10, "c": "z" * 5})     # peaks 1000, 100, 50
    p = plan(toy_stages(), PIPELINE, store, facts=facts, workers=2, memory=500, worker_bytes=0,
             layouts={"original": {"write": 0, "remove": 0, "keep": 4}})
    s = p.doc["summary"]
    assert s["peakBytes"] == 1000 and s["oversize"] == ["toy.src:a"] and s["budget"] == 500
    assert "layout original: write 0 / remove 0 / keep 4" in p.text()
    p = plan(toy_stages(), PIPELINE, store, facts=facts, workers=2, memory=None, worker_bytes=10)
    assert p.doc["summary"]["peakBytes"] == 1000 + 100 + 2 * 10
    run(store, facts)
    p = plan(toy_stages(), PIPELINE, store, facts=facts, layouts={"cas": {"write": 1, "remove": 0, "keep": 3}})
    assert p.check() == 1                                               # nothing runs, but a file would be written
