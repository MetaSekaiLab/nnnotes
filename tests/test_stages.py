"""The stage protocol and `execute` (toy stages over synthetic files; the plan and orchestrator tests use them too).

The toy pipeline: toy.src (one task per file: the text upper-cased, one item per line), toy.count (per file: the
number of exported lines), toy.total (one global task: the sum over the counts that exist). Lines select item
outcomes: "FAIL..." a failed item, "?..." an unsupported one, "" contained in the whole text; a file holding CRASH
fails its task, DIE kills the worker running it.
"""
import json
import os
from pathlib import Path

import pytest

from nnnotes import contract
from nnnotes.contract import Cost, IncompatibleTask, Input
from nnnotes.stages import Env, Output, Pending, Stage, describe, execute, registry
from nnnotes.store import InputMissing, Store, StoreConflict


class Src(Stage):
    name, version = "toy.src", 1
    ATOMS = {"upper": "toy-upper/1"}
    PARAMS = {"suffix": ""}

    def subjects(self, env):
        return sorted(env.fact("files"))

    def inputs(self, subject, env):
        p = Path(env.fact("files")[subject])
        sha, size = env.store.identify(p, "raw", p.name)
        return [Input("file", sha, size, p.name, ({"kind": "file", "path": str(p)},))]

    def context(self, subject, env):
        ctx = env.facts.get("context", {})
        return {k: v for k, v in ctx.items() if k == subject}

    def estimate(self, subject, env, inputs):
        return Cost(inputs[0].size / 100, inputs[0].size * 10)

    def run(self, task, store):
        data = store.input_bytes(task.input("file"))
        if b"CRASH" in data:
            raise RuntimeError("crash requested")
        if b"DIE" in data:
            os._exit(3)
        whole = contract.artifact_id(task.id, "all.txt")
        arts = [contract.artifact(whole, store.add(data.upper() + task.params["suffix"].encode(), "txt"),
                                  contract.provenance(task), {"kind": "toy.text"})]
        items = []
        for i, line in enumerate(data.decode().split("\n")):
            oid = contract.object_id(f"F-{task.subject}", i + 1)
            obj = {"file": f"F-{task.subject}", "pathId": i + 1, "class": "Line", "name": line[:8]}
            if line.startswith("FAIL"):
                items.append(contract.item(oid, "failed", why=contract.reason("toy.fail", f"line {i}"), cls="Line"))
            elif line.startswith("?"):
                items.append(contract.item(oid, "unsupported", why=contract.reason("toy.question"), cls="Line"))
            elif not line:
                items.append(contract.item(oid, "contained", within=whole, cls="Line"))
            else:
                aid = contract.artifact_id(oid, "data")
                arts.append(contract.artifact(aid, store.add(line.upper().encode(), "txt"),
                                              contract.provenance(task, obj=obj), {"kind": "toy.line"}))
                items.append(contract.item(oid, "exported", artifacts=[aid], cls="Line"))
        return Output(arts, items)


class Count(Stage):
    name, version = "toy.count", 1
    after = ("toy.src",)

    def subjects(self, env):
        return sorted(env.fact("files"))

    def inputs(self, subject, env):
        return [env.input_of(f"toy.src:{subject}", "all.txt", "text")]

    def depends(self, subject, env):
        return [f"toy.src:{subject}"]

    def run(self, task, store):
        text = store.input_bytes(task.input("text")).decode()
        doc = {"lines": sum(1 for line in text.split("\n") if line and not line.startswith(("FAIL", "?")))}
        if self.version > 1:
            doc["chars"] = len(text)
        aid = contract.artifact_id(task.id, "count.json")
        return Output([contract.artifact(aid, store.add(contract.encode(doc), "json"), contract.provenance(task),
                                         {"kind": "toy.count"})], [])


class Total(Stage):
    name, version = "toy.total", 1
    after = ("toy.count",)

    def subjects(self, env):
        return ["all"]

    def inputs(self, subject, env):
        for t in env.pending("toy.count"):
            raise Pending(t)
        return [env.input_of(t, "count.json", "count:" + contract.parse_task_id(t)[1]) for t in env.tasks("toy.count")]

    def depends(self, subject, env):
        return [f"toy.count:{s}" for s in sorted(env.fact("files"))]

    def run(self, task, store):
        n = sum(json.loads(store.input_bytes(i))["lines"] for i in task.inputs)
        aid = contract.artifact_id(task.id, "total.json")
        return Output([contract.artifact(aid, store.add(contract.encode({"lines": n}), "json"),
                                         contract.provenance(task), {"kind": "toy.total"})], [])


PIPELINE = ["toy.src", "toy.count", "toy.total"]


def toy_stages(**versions) -> dict:
    """The toy registry; `versions`: {class name: version} overrides (a bumped stage)."""
    out = []
    for cls in (Src, Count, Total):
        s = cls()
        if cls.__name__ in versions:
            s.version = versions[cls.__name__]
        out.append(s)
    return registry(*out)


def toy_files(root: Path, texts: dict) -> dict:
    """Write {subject: text} under root; the facts entry {subject: path}."""
    root.mkdir(parents=True, exist_ok=True)
    out = {}
    for subject, text in texts.items():
        p = root / f"{subject}.txt"
        p.write_bytes(text.encode())
        out[subject] = str(p)
    return out


# ---------------------------------------------------------------- tests
def test_describe_fills_defaults_and_refuses_unknown_parameters(tmp_path):
    store = Store(tmp_path / "store")
    env = Env(store, {"files": toy_files(tmp_path / "in", {"a": "x\ny"})})
    t = describe(Src(), "a", None, env)
    assert t.id == "toy.src:a" and t.params == {"suffix": ""} and t.atoms == {"upper": "toy-upper/1"}
    assert t.input("file").name == "a.txt" and t.cost.peak_bytes == 30
    assert describe(Src(), "a", {"suffix": "!"}, env).key != t.key
    with pytest.raises(ValueError, match="unknown parameters bogus"):
        describe(Src(), "a", {"bogus": 1}, env)


def test_execute_commits_then_hits(tmp_path):
    store = Store(tmp_path / "store")
    env = Env(store, {"files": toy_files(tmp_path / "in", {"a": "one\n\nFAIL\n?q"})})
    t = describe(Src(), "a", None, env)
    ex = execute(t, store, toy_stages())
    assert (ex.status, ex.result_status) == ("ran", "partial") and ex.cost["cpuSeconds"] >= 0
    doc = store.result(t.key)
    assert [i["status"] for i in doc["items"]] == ["exported", "contained", "failed", "unsupported"]
    arts = {a["id"]: a for a in doc["artifacts"]}
    assert sorted(arts) == ["F-a:1#data", "toy.src:a#all.txt"]
    assert store.read(arts["toy.src:a#all.txt"]["content"]["sha256"]) == b"ONE\n\nFAIL\n?Q"
    assert "task" not in doc and doc["keyParts"] == t.key_parts()
    assert execute(t, store, toy_stages()).status == "hit"
    assert execute(t, store, toy_stages(), force=True).status == "ran"     # identical output: accepted


def test_execute_refuses_other_versions_and_unknown_stages(tmp_path):
    store = Store(tmp_path / "store")
    env = Env(store, {"files": toy_files(tmp_path / "in", {"a": "x"})})
    t = describe(Src(), "a", None, env)
    with pytest.raises(IncompatibleTask, match="version 2 here"):
        execute(t, store, toy_stages(Src=2))
    with pytest.raises(IncompatibleTask, match="no stage"):
        execute(t, store, {})
    assert store.result(t.key) is None


def test_execute_verifies_inputs_and_caches_no_task_failure(tmp_path):
    store = Store(tmp_path / "store")
    files = toy_files(tmp_path / "in", {"a": "x", "b": "CRASH"})
    env = Env(store, {"files": files})
    ta, tb = describe(Src(), "a", None, env), describe(Src(), "b", None, env)
    with pytest.raises(RuntimeError, match="crash requested"):
        execute(tb, store, toy_stages())
    assert store.result(tb.key) is None
    Path(files["a"]).write_bytes(b"y")                  # same size, other content: the input is refused
    with pytest.raises(InputMissing, match="other content"):
        execute(ta, store, toy_stages())
    assert store.result(ta.key) is None


def test_a_stage_whose_output_is_not_a_function_of_its_key_is_caught(tmp_path):
    class Clock(Src):
        n = 0

        def run(self, task, store):
            Clock.n += 1
            aid = contract.artifact_id(task.id, "all.txt")
            return Output([contract.artifact(aid, store.add(str(Clock.n).encode(), "txt"),
                                             contract.provenance(task), {"kind": "toy.text"})], [])

    store = Store(tmp_path / "store")
    env = Env(store, {"files": toy_files(tmp_path / "in", {"a": "x"})})
    t = describe(Clock(), "a", None, env)
    execute(t, store, {"toy.src": Clock()})
    with pytest.raises(StoreConflict):
        execute(t, store, {"toy.src": Clock()}, force=True)


def test_env_pending_and_failed_tasks(tmp_path):
    store = Store(tmp_path / "store")
    env = Env(store, {"files": {"a": "unused", "b": "unused"}})
    env.waiting("toy.src:a")
    env.waiting("toy.src:b", failed=True)
    with pytest.raises(Pending) as e:
        describe(Count(), "a", None, env)
    assert e.value.task == "toy.src:a" and not e.value.failed
    with pytest.raises(Pending) as e:
        describe(Count(), "b", None, env)
    assert e.value.failed
    assert env.pending("toy.src") == ["toy.src:a"] and env.missing("toy.src") == ["toy.src:a", "toy.src:b"]
    with pytest.raises(KeyError, match="no fact"):
        env.fact("census")


def test_registry_refuses_repeated_names():
    with pytest.raises(ValueError):
        registry(Src(), Src())


def test_describe_gives_the_stage_its_parameters(tmp_path):
    from nnnotes.contract import Task

    class Selective(Stage):
        name, version = "toy.sel", 1
        PARAMS = {"classes": None}
        ATOMS = {"a": "x/1", "b": "y/1"}

        def subjects(self, env):
            return ["s"]

        def uses(self, subject, env):
            want = env.params["classes"]
            return {k: v for k, v in self.ATOMS.items() if want is None or k in want}

    env = Env(Store(tmp_path / "s"))
    assert describe(Selective(), "s", None, env).atoms == {"a": "x/1", "b": "y/1"}
    t = describe(Selective(), "s", {"classes": ["b"]}, env)
    assert isinstance(t, Task) and t.atoms == {"b": "y/1"} and env.params == {}


class _Spawner(Stage):
    """A stage that runs an external program (as the CRI stages run vgmstream and ffmpeg)."""
    name, version = "toy.spawn", 1

    def subjects(self, env):
        return ["s"]

    def run(self, task, store):
        import subprocess
        import sys
        subprocess.run([sys.executable, "-c", "sum(i * i for i in range(3_000_000))"], check=True)
        return Output([], [])


def test_the_cost_counts_the_child_processes(tmp_path):
    from nnnotes.contract import Task
    from nnnotes.stages import children_cpu
    store = Store(tmp_path / "s")
    ex = execute(Task("toy.spawn", 1, "s"), store, registry(_Spawner()))
    cost = ex.cost
    if children_cpu() is None:                          # the platform does not report child processes
        assert cost["childCpuSeconds"] is None
    else:
        assert cost["childCpuSeconds"] > 0.05
        assert cost["cpuSeconds"] >= cost["childCpuSeconds"]
