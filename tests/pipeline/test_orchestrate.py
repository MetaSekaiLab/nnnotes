"""The local orchestrator (toy stages of test_stages; spawned worker processes where the pool matters)."""

import hashlib
import os
from pathlib import Path

import pytest

from nnnotes.pipeline import contract
from nnnotes.pipeline.contract import Cost, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.orchestrate import Job, Orchestrator, Scheduler, coverage, jobs
from nnnotes.pipeline.plan import plan
from nnnotes.pipeline.stages import execute, registry
from tests.pipeline.test_stages import PIPELINE, Count, Src, Total, toy_files, toy_stages

TEXTS = {
    "a": "alpha\nbeta\n\nFAIL here\n?what",
    "b": "gamma",
    "c": "delta\nepsilon",
    "d": "zeta\neta\ntheta",
    "e": "iota",
}


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
    depot = Depot(tmp_path / name)
    files = toy_files(tmp_path / "in", texts)
    orch = Orchestrator(depot, stages or toy_stages(), workers=workers, log=lambda m: None, **kw)
    run = orch.run(PIPELINE, facts={"files": files}, context={"region": "test"}, selection=["all"], force=force)
    return depot, run, orch


def test_one_worker_and_four_workers_give_identical_stores(tmp_path):
    s0, r0, _ = orchestrate(tmp_path, "inline", TEXTS, workers=0)
    s1, r1, _ = orchestrate(tmp_path, "one", TEXTS, workers=1)
    s4, r4, _ = orchestrate(tmp_path, "four", TEXTS, workers=4)
    t0 = tree(s0.root, "cas", "ac")
    assert t0 and t0 == tree(s1.root, "cas", "ac") == tree(s4.root, "cas", "ac")
    m0, m1, m4 = r0.write(), r1.write(), r4.write()
    assert contract.encode(m0) == contract.encode(m1) == contract.encode(m4)
    assert s0.run_path(m0["id"]).read_bytes() == s4.run_path(m4["id"]).read_bytes()
    assert m0["summary"]["ran"] == len(TEXTS) * 2 + 1 and m0["summary"]["items"]["failed"] == 1
    assert s0.latest_run(["all"])["id"] == m0["id"]


def test_run_stage_task_by_task_equals_the_orchestrated_store(tmp_path):
    s0, _, _ = orchestrate(tmp_path, "orch", TEXTS)
    s1 = Depot(tmp_path / "tasks-depot")
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
    depot, run, _ = orchestrate(tmp_path, "s", texts)
    st = {t: v["status"] for t, v in run.tasks.items()}
    assert st == {
        "toy.src:a": "ran",
        "toy.src:b": "failed",
        "toy.src:c": "ran",
        "toy.count:a": "ran",
        "toy.count:b": "failed",
        "toy.count:c": "ran",
        "toy.total:all": "ran",
    }
    assert run.tasks["toy.count:b"]["key"] is None
    codes = [(f["task"], f["object"], f["code"]) for f in run.failures()]
    assert codes == [
        ("toy.count:b", None, "upstream.failed"),
        ("toy.src:a", "F-a:2", "toy.fail"),
        ("toy.src:b", None, "task.error"),
    ]
    assert run.exit_code == 1
    total = depot.result(run.tasks["toy.total:all"]["key"])
    assert [i[0] for i in total["keyParts"]["inputs"]] == ["count:a", "count:c"]
    _, again, _ = orchestrate(tmp_path, "s", texts)
    st = {t: v["status"] for t, v in again.tasks.items()}
    assert st["toy.src:a"] == "hit" and st["toy.src:b"] == "failed" and st["toy.total:all"] == "hit"
    assert again.summary()["items"]["failed"] == 1  # the cached item failure is reported again


def test_a_dying_worker_fails_its_task_only(tmp_path):
    depot, run, orch = orchestrate(tmp_path, "s", {"a": "x", "d": "DIE", "c": "y"}, workers=2)
    assert run.tasks["toy.src:d"]["status"] == "failed"
    assert [f["code"] for f in run.failures() if f["task"] == "toy.src:d"] == ["task.worker_died"]
    assert run.tasks["toy.src:a"]["status"] == run.tasks["toy.src:c"]["status"] == "ran"
    assert run.tasks["toy.total:all"]["status"] == "ran"
    assert orch._started >= 3  # the dead worker was replaced


def test_equal_content_under_two_subjects_is_stored_once(tmp_path):
    depot, run, _ = orchestrate(tmp_path, "s", {"a": "same", "b": "same"}, workers=2)
    assert all(v["status"] == "ran" for v in run.tasks.values())
    assert run.tasks["toy.src:a"]["key"] != run.tasks["toy.src:b"]["key"]  # the subject names artifacts
    ra, rb = (depot.result(run.tasks[f"toy.src:{s}"]["key"]) for s in "ab")
    assert [a["id"] for a in rb["artifacts"]] == ["F-b:1#data", "toy.src:b#all.txt"]
    assert {a["content"]["sha256"] for a in ra["artifacts"]} == {a["content"]["sha256"] for a in rb["artifacts"]}
    assert len(tree(depot.root, "cas")) == 3  # SAME, the two equal counts, the total


def test_no_more_workers_start_than_a_stage_has_tasks(tmp_path):
    _, run, orch = orchestrate(tmp_path, "s", {"a": "x", "b": "y"}, workers=4)
    assert run.summary()["ran"] == 5 and orch._started == 2


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


# ---------------------------------------------------------------- batches
class Batched(Src):
    """toy.src whose tasks are batched by the first letter of their subject; every process logs to <log>/<pid>.log
    what it loads, runs and drops."""

    def __init__(self, log: Path):
        self.log, self.loaded = str(log), None

    def batch(self, task):
        return task.subject[0]

    def run(self, task, depot):
        if self.loaded != task.subject[0]:
            self.loaded = task.subject[0]
            self._note(f"load {self.loaded}")
        self._note(f"run {task.subject}")
        return super().run(task, depot)

    def end_batch(self, batch):
        self._note(f"drop {batch}")
        self.loaded = None

    def _note(self, line: str) -> None:
        with open(Path(self.log) / f"{os.getpid()}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")


def batched(tmp_path, name, texts, workers):
    log = tmp_path / f"{name}-log"
    log.mkdir()
    stages = registry(Batched(log), Count(), Total())
    depot, run, orch = orchestrate(tmp_path, name, texts, workers=workers, stages=stages)
    return depot, run, orch, [" ".join(f.read_text(encoding="utf-8").split()) for f in sorted(log.iterdir())]


BATCHED = {"a1": "x", "a2": "y\nz", "a3": "w", "b1": "v", "b2": "FAIL"}


def test_the_tasks_of_a_batch_run_in_one_process_which_drops_it_after_the_last(tmp_path):
    plain, _, _ = orchestrate(tmp_path, "plain", BATCHED)
    for workers in (0, 2):
        depot, run, orch, logs = batched(tmp_path, f"w{workers}", BATCHED, workers)
        assert tree(depot.root, "cas", "ac") == tree(plain.root, "cas", "ac")  # batches change no result
        assert all(v["status"] == "ran" for v in run.tasks.values())
        a, b = "load a run a1 run a2 run a3 drop a", "load b run b1 run b2 drop b"
        if workers:
            assert sorted(logs) == [a, b] and orch._started == 2
        else:
            assert logs == [f"{a} {b}"]  # equal estimates: by id
        jobs_of = {e["task"]: e["job"] for e in run.log if e["event"] == "start" and e["task"].startswith("toy.src")}
        assert jobs_of == {f"toy.src:{s}": f"toy.src:{s[0]}1" for s in BATCHED}


def test_a_worker_dying_in_a_job_fails_its_task_and_the_rest_runs_in_another(tmp_path):
    texts = {"a1": "x", "a2": "DIE", "a3": "y", "a4": "z", "b1": "v"}
    _, run, orch, logs = batched(tmp_path, "s", texts, workers=1)
    st = {t: v["status"] for t, v in run.tasks.items() if t.startswith("toy.src")}
    assert st == {
        "toy.src:a1": "ran",
        "toy.src:a2": "failed",
        "toy.src:a3": "ran",
        "toy.src:a4": "ran",
        "toy.src:b1": "ran",
    }
    assert [f["code"] for f in run.failures() if f["task"] == "toy.src:a2"] == ["task.worker_died"]
    assert sorted(logs) == ["load a run a1 run a2", "load a run a3 run a4 drop a load b run b1 drop b"]
    assert orch._started >= 2


def test_jobs_group_a_stages_batches():
    def task(stage, name, cpu, peak):
        return Task(stage, 1, name, cost=Cost(cpu, peak))

    class Firsts(Src):
        def batch(self, task):
            return task.subject[0] if task.subject != "a9" else None

    stages = registry(Firsts(), Count())
    ts = [task("toy.src", s, 1.0, p) for s, p in (("a1", 10), ("a2", 30), ("b1", 5), ("a9", 7))]
    ts.append(task("toy.count", "a1", 2.0, 1))
    js = jobs(reversed(ts), stages)
    assert [([t.subject for t in j.tasks], j.batch) for j in js] == [
        (["a1"], None),
        (["a9"], None),
        (["a1", "a2"], ("toy.src", "a")),
        (["b1"], ("toy.src", "b")),
    ]
    assert (js[2].id, js[2].cpu, js[2].peak) == ("toy.src:a1", 2.0, 30)


# ---------------------------------------------------------------- admission
def toy_job(name, cpu, peak):
    return Job([Task("toy.src", 1, name, cost=Cost(cpu, peak))])


def test_scheduler_orders_longest_first_and_admits_within_the_budget():
    s = Scheduler([toy_job("a", 1, 40), toy_job("b", 5, 70), toy_job("c", 3, 30), toy_job("d", 3, 20)], 100)
    assert s.next().id == "toy.src:b"  # longest first
    assert s.next().id == "toy.src:c"  # 70 + 30 fits
    assert s.next() is None  # d (20) would pass 100
    s.finished("toy.src:b")
    assert [s.next().id, s.next().id] == ["toy.src:d", "toy.src:a"]
    assert s.next() is None and s.used() == 90 and s.oversize == []
    s.put(toy_job("e", 9, 1))
    assert s.next().id == "toy.src:e"  # queued again in its place
    s.stop()
    s.put(toy_job("f", 1, 1))
    assert s.next() is None and s.queue == []


def test_scheduler_runs_an_oversize_job_alone():
    big = Job([Task("toy.src", 1, n, cost=Cost(0.5, 500)) for n in ("big1", "big2")])
    s = Scheduler([big, toy_job("x", 9, 10), toy_job("y", 0.5, 10)], 100)
    assert s.next().id == "toy.src:x"
    assert s.next() is None  # big waits for the pool to drain; y must not pass it
    s.finished("toy.src:x")
    assert s.next() is big and s.oversize == ["toy.src:big1", "toy.src:big2"]
    assert s.next() is None  # nothing joins it
    s.finished("toy.src:big1")
    assert s.next().id == "toy.src:y"


def test_pool_admission_respects_the_budget(tmp_path):
    texts = {f"t{i}": "x" * (10 + 7 * i) for i in range(8)}  # estimated peaks 100 .. 590 bytes
    texts["huge"] = "y" * 200  # 2000 bytes: above the budget
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
    assert cov["classes"]["Line"] == {
        "total": 6,
        "missing": 2,
        "exported": 1,
        "contained": 1,
        "generic": 0,
        "unsupported": 1,
        "failed": 1,
    }
    assert cov["classes"]["Mesh"]["missing"] == 2
    assert cov["reasons"] == {
        "toy.fail": {"count": 1, "examples": ["F-a:2"]},
        "toy.question": {"count": 1, "examples": ["F-a:3"]},
    }
    fails = run.failures_doc()
    assert fails["schema"] == contract.FAILURES and len(fails["failures"]) == 3
    assert coverage([], None)["classes"] == {}


def test_unknown_or_misordered_stages_are_refused(tmp_path):
    orch = Orchestrator(Depot(tmp_path / "s"), toy_stages(), log=lambda m: None)
    with pytest.raises(ValueError, match="unknown stages"):
        orch.run(["toy.nope"], facts={"files": {}})
    with pytest.raises(ValueError, match="runs before toy.src"):
        orch.run(["toy.count", "toy.src"], facts={"files": {}})


def test_a_config_error_stops_the_run(tmp_path):
    from nnnotes.source.config import ConfigError
    from tests.pipeline.test_stages import Src

    class NeedsTool(Src):
        def run(self, task, depot):
            raise ConfigError("setting paths.tool is not set")

    stages = toy_stages()
    stages["toy.src"] = NeedsTool()
    orch = Orchestrator(Depot(tmp_path / "s"), stages, log=lambda m: None)
    with pytest.raises(ConfigError, match="paths.tool"):
        orch.run(PIPELINE, facts={"files": toy_files(tmp_path / "in", {"a": "x", "b": "y"})})


def test_a_stage_whose_subjects_need_a_failed_task_fails_once(tmp_path):
    from nnnotes.pipeline.stages import Pending, Stage

    class AfterAll(Stage):
        name, version, after = "toy.after", 1, ("toy.src",)

        def subjects(self, env):
            env.key("toy.src:b")
            return ["x"]

    stages = dict(toy_stages(), **{"toy.after": AfterAll()})
    orch = Orchestrator(Depot(tmp_path / "s"), stages, log=lambda m: None)
    run = orch.run(["toy.src", "toy.after"], facts={"files": toy_files(tmp_path / "in", {"a": "x", "b": "CRASH"})})
    assert run.tasks["toy.after:*"]["status"] == "failed"
    assert [f["code"] for f in run.failures() if f["task"] == "toy.after:*"] == ["upstream.failed"]
    assert Pending


def test_coverage_counts_an_object_of_several_results_once():
    a = {
        "items": [
            contract.item("F:1", "exported", artifacts=["F:1#meta"], cls="Sprite"),
            contract.item("F:2", "exported", artifacts=["F:2#meta"], cls="Sprite"),
        ]
    }
    b = {
        "items": [
            contract.item("F:1", "exported", artifacts=["F:1#image"], cls="Sprite"),
            contract.item("F:2", "failed", why=contract.reason("x.bad", "no atlas"), cls="Sprite"),
        ]
    }
    doc = coverage([("unity.export:x", a), ("sprite.crop:y", b)], {"Sprite": 2})
    assert doc["classes"]["Sprite"] == {
        "total": 2,
        "missing": 0,
        "exported": 1,
        "contained": 0,
        "generic": 0,
        "unsupported": 0,
        "failed": 1,
    }
    assert doc["reasons"] == {"x.bad": {"count": 1, "examples": ["F:2"]}}


def _coverage_of_whole_items(results, census=None):
    """coverage() as a plain reading of every item (the reference for the compact one)."""
    from collections import Counter

    order = ("failed", "unsupported", "generic", "exported", "contained")
    best = {}
    for _, doc in results:
        for it in doc["items"]:
            old = best.get(it["object"])
            if old is None or order.index(it["status"]) < order.index(old["status"]):
                best[it["object"]] = it
    classes, reasons = {}, {}
    for it in best.values():
        classes.setdefault(it.get("class", "?"), Counter())[it["status"]] += 1
        if "reason" in it:
            reasons.setdefault(it["reason"]["code"], []).append(it["object"])
    out = {}
    for cls in sorted(set(classes) | set(census or {})):
        c = classes.get(cls, Counter())
        total = (census or {}).get(cls, sum(c.values()))
        out[cls] = {"total": total, "missing": total - sum(c.values()), **{s: c.get(s, 0) for s in order}}
    return {
        "schema": contract.COVERAGE,
        "classes": out,
        "reasons": {k: {"count": len(v), "examples": sorted(v)[:5]} for k, v in sorted(reasons.items())},
    }


def test_compact_coverage_equals_a_reading_of_whole_items():
    why = contract.reason
    big, neg = (1 << 63) - 1, -(1 << 63)
    a = {
        "artifacts": [],
        "items": [
            contract.item("F:1", "contained", within="F:9#prefab", cls="Mesh"),
            contract.item("F:01", "failed", why=why("x.a", "leading zero: another object"), cls="Mesh"),
            contract.item(f"F:{big}", "generic", artifacts=["F:big#json"], why=why("x.g", "g"), cls="Tex"),
            contract.item(f"F:{neg}", "unsupported", why=why("x.u", "u"), cls="Tex"),
            contract.item(f"F:{1 << 64}", "exported", artifacts=["F:huge#png"], cls="Tex"),
            contract.item("G:1", "exported", artifacts=["G:1#png"], cls="Sprite"),
            contract.item("odd", "unsupported", why=why("x.u", "no pathId"), cls="Sprite"),
            contract.item("F:+1", "exported", artifacts=["F:+1#png"]),
        ],
    }
    b = {
        "artifacts": [],
        "items": [
            contract.item("F:1", "exported", artifacts=["F:1#glb"], cls="Mesh"),  # more severe: replaces
            contract.item("G:1", "exported", artifacts=["G:1#meta"], cls="Texture2D"),  # a tie: the first stays
            contract.item(f"F:{neg}", "failed", why=why("x.f", "worse"), cls="Tex"),
            contract.item(f"F:{big}", "contained", within="F:9#prefab", cls="Tex"),  # less severe: ignored
            contract.item("G:1", "failed", why=why("x.f", "worst"), cls="Sprite"),
        ],
    }
    results = [("unity.export:a", a), ("sprite.crop:b", b)]
    for census in (None, {"Mesh": 5, "Audio": 1}):
        assert coverage(results, census) == _coverage_of_whole_items(results, census)
        assert coverage(results[::-1], census) == _coverage_of_whole_items(results[::-1], census)


def test_the_reports_read_the_results_once_until_the_tasks_change(tmp_path):
    depot, run, _ = orchestrate(tmp_path, "s", {"a": "x\nFAIL\n?y\n", "b": "z"})
    reads = []
    real = depot.result
    depot.result = lambda key: reads.append(key) or real(key)
    first = (run.summary(), run.failures(), run.coverage())
    n = len(reads)
    assert n == sum(t["status"] != "failed" for t in run.tasks.values())
    assert (run.summary(), run.failures(), run.coverage(), run.exit_code) == (*first, 1) and len(reads) == n
    seen = []
    run.scan(lambda tid, doc: seen.append(tid))
    assert seen == sorted(t for t, v in run.tasks.items() if v["status"] != "failed") and len(reads) == 2 * n
    run.tasks["toy.src:a"] = dict(run.tasks["toy.src:a"], status="failed")
    assert run.summary()["items"]["failed"] == 0 and len(reads) == 3 * n - 1


def test_a_scan_with_the_census_keeps_only_the_coverage_document(tmp_path):
    _, run, _ = orchestrate(tmp_path, "s", {"a": "x\nFAIL\n?y\n", "b": "CRASH"})
    want = run.coverage({"Line": 6})
    run.scan(census=lambda: {"Line": 6})
    digest = run._scan[1]
    assert digest.best == digest.kinds == digest.reasoned == {} and run.coverage({"Line": 6}) == want
    with pytest.raises(RuntimeError, match="settled"):
        digest.add("toy.src:a", {"items": []})
    assert run.coverage() == coverage(run.results()) and run._scan[1] is not digest  # another census: read again
