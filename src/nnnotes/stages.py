"""The stage protocol: how a stage describes its tasks and runs one, and the one function that runs a task.

A stage is a named, versioned unit of work applied to one subject at a time (a bundle, a content id, a model key,
a view, or the single subject of a global stage). It declares, for a subject:

    subjects(env)                  the subjects it has, sorted (from the planning environment: catalog, censuses, ...)
    normalize(params)              the output-affecting parameters with every default filled (unknown ones refused)
    inputs(subject, env)           its inputs: roles, content ids, sizes, names, locators
    uses(subject, env)             the implementation ids of the atoms its task uses (only those)
    context(subject, env)          the context entries its task consumes (only those)
    depends(subject, env)          the task ids whose results it reads (the plan's edges)
    estimate(subject, env, inputs) the expected cost (CPU seconds, peak bytes)
    run(task, store)               the work: reads the inputs through the store, writes the bytes of its artifacts
                                   into the store, returns their records and one item per covered object

A stage that needs the output of a task that has not run raises Pending from inputs/uses/context (the environment
does it for it: Env.key / Env.result). While a task is described, `env.params` holds its normalized parameters, for
answers that depend on them (the atoms of the converters a parameter selects). `describe` turns the answers into a Task; `execute` runs a Task once and
commits its result (the store's action cache) after the artifacts' bytes are stored. The output of `run` must be a
function of the task's key parts: a change of what a stage writes is a change of its version (golden tests check).
Deterministic per-object failures are items (status `failed`, cached with the result); an exception out of `run`
fails the task, and nothing of it is cached.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from importlib import metadata

from . import contract
from .contract import Cost, IncompatibleTask, Input, Task

__all__ = ["Stage", "Env", "Pending", "Output", "Execution", "IncompatibleTask", "describe", "execute", "registry",
           "golden", "golden_problems", "library_versions"]


class Pending(Exception):
    """A task cannot be described yet: it reads the result of `task`, which has not run (`failed`: which failed)."""

    def __init__(self, task: str, failed: bool = False):
        super().__init__(f"{task} {'failed' if failed else 'has not run'}")
        self.task, self.failed = task, failed


@dataclass
class Output:
    """What Stage.run returns: artifact records (their bytes already in the store) and one item per object."""
    artifacts: list = field(default_factory=list)
    items: list = field(default_factory=list)


class Stage:
    """Base of the stages. Subclasses set `name` and `version` and override what their subjects need; `ATOMS` is
    every atom the stage may use (atom name -> implementation id), the default answer of `uses`."""
    name: str = ""
    version: int = 0
    after: tuple = ()                  # stages that run before this one (their results feed its tasks)
    ATOMS: dict = {}
    PARAMS: dict = {}                  # parameter defaults; normalize fills them and refuses unknown names

    def subjects(self, env: "Env") -> list[str]:
        raise NotImplementedError

    def normalize(self, params: dict | None) -> dict:
        params = dict(params or {})
        unknown = sorted(set(params) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        return {**self.PARAMS, **params}

    def inputs(self, subject: str, env: "Env") -> list[Input]:
        return []

    def uses(self, subject: str, env: "Env") -> dict:
        return dict(self.ATOMS)

    def context(self, subject: str, env: "Env") -> dict:
        return {}

    def depends(self, subject: str, env: "Env") -> list[str]:
        return []

    def estimate(self, subject: str, env: "Env", inputs: list[Input]) -> Cost:
        return Cost(0.0, sum(i.size for i in inputs))

    def run(self, task: Task, store) -> Output:
        raise NotImplementedError


def registry(*stages: Stage) -> dict[str, Stage]:
    """{name: stage}; names must be unique."""
    out: dict[str, Stage] = {}
    for s in stages:
        if not s.name or s.name in out:
            raise ValueError(f"stage name {s.name!r} missing or repeated")
        out[s.name] = s
    return out


class Env:
    """What a stage reads while it describes a task: named facts (catalog index, censuses, script table, ...) and
    the tasks of the current plan or run with their keys. `key` / `result` raise Pending for a task that has not
    run (or failed); a stage lets that propagate."""

    def __init__(self, store, facts: dict | None = None):
        self.store = store
        self.facts = dict(facts or {})
        self.params: dict = {}                   # the normalized parameters of the task being described
        self._keys: dict[str, str] = {}          # task id -> key, for tasks whose result is in the store
        self._open: dict[str, bool] = {}         # task id -> failed?, for tasks without a result

    def fact(self, name: str):
        try:
            return self.facts[name]
        except KeyError:
            raise KeyError(f"no fact {name!r} in the planning environment") from None

    def done(self, tid: str, key: str) -> None:
        self._open.pop(tid, None)
        self._keys[tid] = key

    def waiting(self, tid: str, failed: bool = False) -> None:
        self._keys.pop(tid, None)
        self._open[tid] = failed

    def known(self, tid: str) -> bool:
        return tid in self._keys or tid in self._open

    def key(self, tid: str) -> str:
        if tid in self._keys:
            return self._keys[tid]
        raise Pending(tid, self._open.get(tid, False))

    def result(self, tid: str) -> dict:
        doc = self.store.result(self.key(tid))
        if doc is None:
            raise Pending(tid)
        return doc

    def tasks(self, stage: str) -> list[str]:
        """The task ids of `stage` with a result, sorted."""
        return sorted(t for t in self._keys if t.startswith(stage + ":"))

    def missing(self, stage: str) -> list[str]:
        """The task ids of `stage` without a result (not run yet, or failed), sorted."""
        return sorted(t for t in self._open if t.startswith(stage + ":"))

    def pending(self, stage: str) -> list[str]:
        """The task ids of `stage` that have not run yet (a plan: they would run first), sorted. A stage that reads
        every task of another raises Pending for these; failed ones it skips (their failure is reported)."""
        return sorted(t for t, failed in self._open.items() if not failed and t.startswith(stage + ":"))

    def artifact(self, tid: str, role: str) -> dict:
        """The record of the artifact "<tid>#<role>" of a task's result."""
        aid = contract.artifact_id(tid, role)
        for a in self.result(tid)["artifacts"]:
            if a["id"] == aid:
                return a
        raise KeyError(f"result of {tid} has no artifact {aid}")

    def input_of(self, tid: str, role: str, as_role: str | None = None) -> Input:
        """A task input made of the artifact "<tid>#<role>" (read from the store)."""
        c = self.artifact(tid, role)["content"]
        return Input(as_role or role, c["sha256"], c["size"], None, ({"kind": "store"},))


def describe(stage: Stage, subject: str, params: dict | None, env: Env) -> Task:
    """The task of `stage` for `subject` (raises Pending when an input is not known yet)."""
    p = stage.normalize(params)
    env.params = p
    try:
        ins = list(stage.inputs(subject, env))
        return Task(stage.name, stage.version, subject, p, dict(stage.uses(subject, env)), tuple(ins),
                    dict(stage.context(subject, env)), stage.estimate(subject, env, ins))
    finally:
        env.params = {}


@dataclass
class Execution:
    """The outcome of execute: `status` hit (the result was in the store) or ran; the measured cost of a run."""
    task: str
    key: str
    status: str
    result_status: str
    cost: dict | None = None


def execute(task: Task, store, stages: dict[str, Stage], *, force: bool = False) -> Execution:
    """Run `task` once: verify that this installation has its stage at its version and can read every input with
    its content id, run the stage, store its artifacts' bytes, then commit the result (write-once; a result already
    in the store is a hit unless `force`, which runs again and requires identical output). Exceptions fail the task
    and leave no result. The measured cost (never part of a key) counts the CPU time of this process and of the child
    processes the stage ran and waited for (childCpuSeconds; null where the platform does not report it)."""
    stage = stages.get(task.stage)
    if stage is None:
        raise IncompatibleTask(f"task {task.id}: no stage {task.stage!r} in this installation")
    if stage.version != task.version:
        raise IncompatibleTask(f"task {task.id}: stage {task.stage} is version {stage.version} here, "
                               f"the task was described for version {task.version}")
    if not force:
        hit = store.result(task.key)
        if hit is not None:
            return Execution(task.id, task.key, "hit", hit["status"])
    for i in task.inputs:
        store.open_input(i)
    children = children_cpu()
    cpu, wall = time.process_time(), time.perf_counter()
    out = stage.run(task, store)
    doc = contract.result(task, out.artifacts, out.items)
    store.commit(doc)
    own = time.process_time() - cpu
    child = None if children is None else max(0.0, children_cpu() - children)
    cost = {"schema": contract.COST, "key": task.key, "task": task.id,
            "cpuSeconds": round(own + (child or 0.0), 3), "wallSeconds": round(time.perf_counter() - wall, 3),
            "childCpuSeconds": None if child is None else round(child, 3)}
    return Execution(task.id, task.key, "ran", doc["status"], cost)


def children_cpu() -> float | None:
    """User + system CPU seconds of this process's finished and waited-for child processes (the external tools a
    stage runs), where the platform reports them (POSIX getrusage); None elsewhere."""
    try:
        import resource
    except ImportError:
        return None
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


# ---------------------------------------------------------------- golden tests
def library_versions(dists) -> dict[str, str]:
    """{distribution: installed version} ("-" when not installed)."""
    out = {}
    for d in sorted(dists):
        try:
            out[d] = metadata.version(d)
        except metadata.PackageNotFoundError:
            out[d] = "-"
    return out


def golden(stage: Stage, fixtures: dict, store, libraries=()) -> dict:
    """The golden record of a stage: its version, the atoms its fixtures use, the versions of `libraries`, and per
    fixture the key and the sha256 of the canonical result. `fixtures`: {name: callable(store) -> Task} (each
    builds its inputs into the store)."""
    atoms, out = {}, {}
    for name in sorted(fixtures):
        task = fixtures[name](store)
        if task.stage != stage.name:
            raise ValueError(f"fixture {name} is a task of {task.stage}, not {stage.name}")
        execute(task, store, {stage.name: stage}, force=True)
        doc = store.result(task.key)
        atoms.update(task.atoms)
        out[name] = {"key": task.key, "result": contract.sha256(contract.encode(doc))}
    return {"stage": stage.name, "version": stage.version, "atoms": dict(sorted(atoms.items())),
            "libraries": library_versions(libraries), "fixtures": out}


def golden_problems(expected: dict, actual: dict, stage: Stage | None = None) -> list[str]:
    """Why `actual` (golden()) does not match the committed `expected`; [] when it does. An output change without a
    version bump is the error the golden test exists for; a version bump requires a regenerated golden record.
    With `stage`, every atom it declares must be used by some fixture."""
    problems = []
    if expected.get("version") != actual["version"]:
        problems.append(f"{actual['stage']}: version {actual['version']}, golden record has "
                        f"{expected.get('version')}: regenerate the golden record")
    if expected.get("atoms") != actual["atoms"]:
        problems.append(f"{actual['stage']}: atoms {actual['atoms']} differ from the golden record's "
                        f"{expected.get('atoms')}")
    exp, act = expected.get("fixtures", {}), actual["fixtures"]
    for name in sorted(set(exp) | set(act)):
        if name not in act:
            problems.append(f"{actual['stage']}: fixture {name} missing")
        elif name not in exp:
            problems.append(f"{actual['stage']}: fixture {name} has no golden entry")
        elif exp[name] != act[name]:
            what = "output" if exp[name].get("key") == act[name]["key"] else "task key"
            bump = " without a version bump" if expected.get("version") == actual["version"] else ""
            problems.append(f"{actual['stage']}: fixture {name}: {what} changed{bump}")
    if stage is not None:
        unused = sorted(set(stage.ATOMS) - set(actual["atoms"]))
        if unused:
            problems.append(f"{actual['stage']}: no fixture uses {', '.join(unused)}")
    return problems
