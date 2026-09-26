"""The local orchestrator (toy stages of test_stages; spawned worker processes where the pool matters)."""
import hashlib
from pathlib import Path

import pytest

from nnnotes import contract
from nnnotes.contract import Cost, Task
from nnnotes.orchestrate import Orchestrator, Scheduler, coverage
from nnnotes.plan import plan
from nnnotes.stages import execute
from nnnotes.store import Store
from test_stages import PIPELINE, toy_files, toy_stages

TEXTS = {"a": "alpha\nbeta\n\nFAIL here\n?what", "b": "gamma", "c": "delta\nepsilon", "d": "zeta\neta\ntheta",
         "e": "iota"}


def tree(root: Path, *subs) -> dict:
    """{relative path: sha256} of the files under root/<sub> (no temporary files may be left)."""
    out = {}
    for sub in subs:
        for p in sorted((root / sub).rglob("*")):
            if p.is_file():
                assert not p.name.endswith(".part"), p
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def orchestrate(tmp_path, name, texts, workers=0, stages=None, force=(), **kw):
    store = Store(tmp_path / name)
    files = toy_files(tmp_path / "in", texts)
    orch = Orchestrator(store, stages or toy_stages(), workers=workers, log=lambda m: None, **kw)
    run = orch.run(PIPELINE, facts={"files": files}, context={"region": "test"}, selection=["all"], force=force)
    return store, run, orch


def test_one_worker_and_four_workers_give_identical_stores(tmp_path):
    s0, r0, _ = orchestrate(tmp_path, "inline", TEXTS, workers=0)
    s1, r1, _ = orchestrate(tmp_path, "one", TEXTS, workers=1)
    s4, r4, _ = orchestrate(tmp_path, "four", TEXTS, workers=4)
    t0 = tree(s0.root, "cas", "ac")
    assert t0 and t0 == tree(s1.root, "cas", "ac") == tree(s4.root, "cas", "ac")
    m0, m1, m4 = r0.write(), r1.write(), r4.write()
    assert contract.encode(m0) == contract.encode(m1) == contract.encode(m4)
    assert (s0.run_path(m0["id"]).read_bytes() == s4.run_path(m4["id"]).read_bytes())
    assert m0["summary"]["ran"] == len(TEXTS) * 2 + 1 and m0["summary"]["items"]["failed"] == 1
    assert s0.latest_run(["all"])["id"] == m0["id"]


def test_run_stage_task_by_task_equals_the_orchestrated_store(tmp_path):
    s0, _, _ = orchestrate(tmp_path, "orch", TEXTS)
    s1 = Store(tmp_path / "tasks-store")
    stages = toy_stages()
    facts = {"files": toy_files(tmp_path / "in", TEXTS)}
    rounds = 0
    while True:
        p = plan(stages, PIPELINE, s1, facts=facts)
        if not p.tasks:
            break
        rounds += 1
        for f in p.emit(tmp_path / f"round{rounds}"):
            execute(Task.from_json(contract.loads(f.read_bytes())), s1, stages)
    assert rounds == 3
    assert tree(s0.root, "cas", "ac") == tree(s1.root, "cas", "ac")


def test_item_failures_are_cached_task_failures_are_not(tmp_path):
    texts = {"a": "x\nFAIL", "b": "CRASH", "c": "y"}
    store, run, _ = orchestrate(tmp_path, "s", texts)
    st = {t: v["status"] for t, v in run.tasks.items()}
    assert st == {"toy.src:a": "ran", "toy.src:b": "failed", "toy.src:c": "ran", "toy.count:a": "ran",
                  "toy.count:b": "failed", "toy.count:c": "ran", "toy.total:all": "ran"}
    assert run.tasks["toy.count:b"]["key"] is None
    codes = [(f["task"], f["object"], f["code"]) for f in run.failures()]
    assert codes == [("toy.count:b", None, "upstream.failed"), ("toy.src:a", "F-a:2", "toy.fail"),
                     ("toy.src:b", None, "task.error")]
    assert run.exit_code == 1
    total = store.result(run.tasks["toy.total:all"]["key"])
    assert [i[0] for i in total["keyParts"]["inputs"]] == ["count:a", "count:c"]
    _, again, _ = orchestrate(tmp_path, "s", texts)
    st = {t: v["status"] for t, v in again.tasks.items()}
    assert st["toy.src:a"] == "hit" and st["toy.src:b"] == "failed" and st["toy.total:all"] == "hit"
    assert again.summary()["items"]["failed"] == 1                      # the cached item failure is reported again


def test_a_dying_worker_fails_its_task_only(tmp_path):
    store, run, orch = orchestrate(tmp_path, "s", {"a": "x", "d": "DIE", "c": "y"}, workers=2)
    assert run.tasks["toy.src:d"]["status"] == "failed"
    assert [f["code"] for f in run.failures() if f["task"] == "toy.src:d"] == ["task.worker_died"]
    assert run.tasks["toy.src:a"]["status"] == run.tasks["toy.src:c"]["status"] == "ran"
    assert run.tasks["toy.total:all"]["status"] == "ran"
    assert orch._started >= 3                                           # the dead worker was replaced


def test_equal_content_under_two_subjects_is_stored_once(tmp_path):
    store, run, _ = orchestrate(tmp_path, "s", {"a": "same", "b": "same"}, workers=2)
    assert all(v["status"] == "ran" for v in run.tasks.values())
    assert run.tasks["toy.src:a"]["key"] != run.tasks["toy.src:b"]["key"]     # the subject names artifacts
    ra, rb = (store.result(run.tasks[f"toy.src:{s}"]["key"]) for s in "ab")
    assert [a["id"] for a in rb["artifacts"]] == ["F-b:1#data", "toy.src:b#all.txt"]
    assert {a["content"]["sha256"] for a in ra["artifacts"]} == {a["content"]["sha256"] for a in rb["artifacts"]}
    assert len(tree(store.root, "cas")) == 3                          # SAME, the two equal counts, the total


def test_workers_are_recycled(tmp_path):
    _, run, orch = orchestrate(tmp_path, "s", {"a": "x", "b": "y"}, workers=1, recycle_tasks=1)
    assert run.summary()["ran"] == 5 and orch._started == 6


def test_force_runs_again_and_requires_the_same_output(tmp_path):
    orchestrate(tmp_path, "s", TEXTS)
    _, run, _ = orchestrate(tmp_path, "s", TEXTS, force=["toy.count"])
    st = {t: v["status"] for t, v in run.tasks.items()}
    assert all(v == "ran" for t, v in st.items() if t.startswith("toy.count:"))
    assert st["toy.src:a"] == "hit" and st["toy.total:all"] == "hit"


def test_a_bumped_stage_reruns_itself_and_what_reads_it(tmp_path):
    orchestrate(tmp_path, "s", TEXTS)
    _, run, _ = orchestrate(tmp_path, "s", TEXTS, stages=toy_stages(Count=2))
    st = {t: v["status"] for t, v in run.tasks.items()}
    assert {t for t, v in st.items() if v == "ran"} == {f"toy.count:{s}" for s in TEXTS} | {"toy.total:all"}


# ---------------------------------------------------------------- admission
def toy_task(name, cpu, peak):
    return Task("toy.src", 1, name, cost=Cost(cpu, peak))


def test_scheduler_orders_longest_first_and_admits_within_the_budget():
    s = Scheduler([toy_task("a", 1, 40), toy_task("b", 5, 70), toy_task("c", 3, 30), toy_task("d", 3, 20)], 100)
    assert s.next().subject == "b"                     # longest first
    assert s.next().subject == "c"                     # 70 + 30 fits
    assert s.next() is None                            # d (20) would pass 100
    s.finished("toy.src:b")
    assert [s.next().subject, s.next().subject] == ["d", "a"]
    assert s.next() is None and s.used() == 90 and s.oversize == []


def test_scheduler_runs_an_oversize_task_alone():
    s = Scheduler([toy_task("big", 1, 500), toy_task("x", 9, 10), toy_task("y", 0.5, 10)], 100)
    assert s.next().subject == "x"
    assert s.next() is None                            # big waits for the pool to drain; y must not pass it
    s.finished("toy.src:x")
    assert s.next().subject == "big" and s.oversize == ["toy.src:big"]
    assert s.next() is None                            # nothing joins it
    s.finished("toy.src:big")
    assert s.next().subject == "y"


def test_pool_admission_respects_the_budget(tmp_path):
    texts = {f"t{i}": "x" * (10 + 7 * i) for i in range(8)}          # estimated peaks 100 .. 590 bytes
    texts["huge"] = "y" * 200                                         # 2000 bytes: above the budget
    _, run, orch = orchestrate(tmp_path, "s", texts, workers=3, memory=1000, worker_bytes=0)
    starts = [e for e in run.log if e["event"] == "start" and e["task"].startswith("toy.src:")]
    assert len(starts) == len(texts)
    for e in starts:
        assert e["runningBytes"] + e["estimate"]["peakBytes"] <= 1000 or e["runningBytes"] == 0
    assert run.oversize == ["toy.src:huge"] and run.exit_code == 0
    assert any("memory budget" in line for line in orch.explain())


# ---------------------------------------------------------------- reports
def test_coverage_and_failures_documents(tmp_path):
    _, run, _ = orchestrate(tmp_path, "s", {"a": "x\nFAIL\n?y\n", "b": "CRASH"})
    cov = run.coverage({"Line": 6, "Mesh": 2})
    assert cov["classes"]["Line"] == {"total": 6, "missing": 2, "exported": 1, "contained": 1, "generic": 0,
                                      "unsupported": 1, "failed": 1}
    assert cov["classes"]["Mesh"]["missing"] == 2
    assert cov["reasons"] == {"toy.fail": {"count": 1, "examples": ["F-a:2"]},
                              "toy.question": {"count": 1, "examples": ["F-a:3"]}}
    fails = run.failures_doc()
    assert fails["schema"] == contract.FAILURES and len(fails["failures"]) == 3
    assert coverage([], None)["classes"] == {}


def test_unknown_or_misordered_stages_are_refused(tmp_path):
    orch = Orchestrator(Store(tmp_path / "s"), toy_stages(), log=lambda m: None)
    with pytest.raises(ValueError, match="unknown stages"):
        orch.run(["toy.nope"], facts={"files": {}})
    with pytest.raises(ValueError, match="runs before toy.src"):
        orch.run(["toy.count", "toy.src"], facts={"files": {}})


def test_a_config_error_stops_the_run(tmp_path):
    from nnnotes.config import ConfigError
    from test_stages import Src

    class NeedsTool(Src):
        def run(self, task, store):
            raise ConfigError("setting paths.tool is not set")

    stages = toy_stages()
    stages["toy.src"] = NeedsTool()
    orch = Orchestrator(Store(tmp_path / "s"), stages, log=lambda m: None)
    with pytest.raises(ConfigError, match="paths.tool"):
        orch.run(PIPELINE, facts={"files": toy_files(tmp_path / "in", {"a": "x", "b": "y"})})


def test_a_stage_whose_subjects_need_a_failed_task_fails_once(tmp_path):
    from nnnotes.stages import Pending, Stage

    class AfterAll(Stage):
        name, version, after = "toy.after", 1, ("toy.src",)

        def subjects(self, env):
            env.key("toy.src:b")
            return ["x"]

    stages = dict(toy_stages(), **{"toy.after": AfterAll()})
    orch = Orchestrator(Store(tmp_path / "s"), stages, log=lambda m: None)
    run = orch.run(["toy.src", "toy.after"], facts={"files": toy_files(tmp_path / "in", {"a": "x", "b": "CRASH"})})
    assert run.tasks["toy.after:*"]["status"] == "failed"
    assert [f["code"] for f in run.failures() if f["task"] == "toy.after:*"] == ["upstream.failed"]
    assert Pending


def test_coverage_counts_an_object_of_several_results_once():
    a = {"items": [contract.item("F:1", "exported", artifacts=["F:1#meta"], cls="Sprite"),
                   contract.item("F:2", "exported", artifacts=["F:2#meta"], cls="Sprite")]}
    b = {"items": [contract.item("F:1", "exported", artifacts=["F:1#image"], cls="Sprite"),
                   contract.item("F:2", "failed", why=contract.reason("x.bad", "no atlas"), cls="Sprite")]}
    doc = coverage([("unity.export:x", a), ("sprite.crop:y", b)], {"Sprite": 2})
    assert doc["classes"]["Sprite"] == {"total": 2, "missing": 0, "exported": 1, "contained": 0, "generic": 0,
                                        "unsupported": 0, "failed": 1}
    assert doc["reasons"] == {"x.bad": {"count": 1, "examples": ["F:2"]}}
