"""Plan: what a run of a pipeline would do, and why, without running anything.

Every subject of every stage is a node. A node is a `hit` (its result is in the store), `run` (it would run), or
`unknown` (it reads the result of a task that would run first, or of one that has not run: its inputs are not known
yet). Why a node runs is the difference between its key parts and those of the same task id in a previous run
manifest (the latest of the same selection, or one named with --since):

    new-input                  the previous run had no task with this id
    input-changed{role,old,new,name}  an input's content id (null: added / removed), the new input's name
    stage-version{old,new}     the stage's version
    atom-changed{atom,old,new} an atom's implementation id (null: added / removed)
    params-changed{path,old,new}  an output-affecting parameter (path: /name/sub...)
    context-changed{entry}     a consumed context entry
    previous-failed            the task failed in the previous run
    result-missing             same key as before, but the result is not in the store
    forced                     asked to run again
Hit reasons: `unchanged` (same key as before), `cached` (the key's result is in the store from another run, e.g.
content that changed back). Unknown: `pending{task}`.

The plan document (nnnotes.plan/1) lists the nodes in pipeline order with key, status, reasons and cost estimate,
the edges (Stage.depends) and a summary: counts, estimated CPU seconds and peak memory of what would run with the
given workers and memory budget, and per output layout the files it would write, remove and keep.
"""
from __future__ import annotations

from pathlib import Path

from . import contract
from .orchestrate import WORKER_BYTES
from .stages import Env, Pending, Stage, describe
from .store import write_file


def _leaves(old, new, path: str = ""):
    """(path, old, new) of every differing leaf of two JSON values (dicts are walked, other values compared)."""
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            yield from _leaves(old.get(k), new.get(k), f"{path}/{k}")
    elif contract.key_text(old) != contract.key_text(new):
        yield path or "/", old, new


def why(old_parts: dict, new_parts: dict) -> list[dict]:
    """The reasons two key parts differ (empty when they do not)."""
    out = []
    ov, nv = old_parts["stage"]["version"], new_parts["stage"]["version"]
    if ov != nv:
        out.append({"code": "stage-version", "old": ov, "new": nv})
    oi, ni = dict(map(tuple, old_parts["inputs"])), dict(map(tuple, new_parts["inputs"]))
    for role in sorted(set(oi) | set(ni)):
        if oi.get(role) != ni.get(role):
            out.append({"code": "input-changed", "role": role, "old": oi.get(role), "new": ni.get(role)})
    oa, na = old_parts["atoms"], new_parts["atoms"]
    for atom in sorted(set(oa) | set(na)):
        if oa.get(atom) != na.get(atom):
            out.append({"code": "atom-changed", "atom": atom, "old": oa.get(atom), "new": na.get(atom)})
    for path, o, n in _leaves(old_parts["params"], new_parts["params"]):
        out.append({"code": "params-changed", "path": path, "old": o, "new": n})
    oc, nc = old_parts["context"], new_parts["context"]
    for entry in sorted(set(oc) | set(nc)):
        if contract.key_text(oc.get(entry)) != contract.key_text(nc.get(entry)):
            out.append({"code": "context-changed", "entry": entry})
    return out


class Plan:
    """A computed plan: `doc` (nnnotes.plan/1) and the runnable tasks (status `run`)."""

    def __init__(self, doc: dict, tasks: list):
        self.doc, self.tasks = doc, tasks

    def check(self) -> int:
        """1 when anything would run or change (a node not a hit, a layout file to write or remove), else 0."""
        if any(n["status"] != "hit" for n in self.doc["nodes"]):
            return 1
        return int(any(v["write"] or v["remove"] for v in self.doc["summary"]["layouts"].values()))

    def emit(self, directory) -> list[Path]:
        """Write the runnable tasks as task descriptions <directory>/<key>.json (one per distinct key)."""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        out = {}
        for t in self.tasks:
            if t.key not in out:
                out[t.key] = d / f"{t.key}.json"
                write_file(out[t.key], contract.encode(t.to_json()))
        return sorted(out.values())

    def text(self, limit: int | None = 20) -> str:
        """Per stage its counts and a table of its first `limit` nodes that are not hits (None: all), then the
        summary."""
        rows, stages, shown = [], {}, {}
        for n in self.doc["nodes"]:
            c = stages.setdefault(n["stage"], {"hit": 0, "run": 0, "unknown": 0})
            c[n["status"]] += 1
            if n["status"] == "hit":
                continue
            shown[n["stage"]] = shown.get(n["stage"], 0) + 1
            if limit is not None and shown[n["stage"]] > limit:
                continue
            reasons = ", ".join(_reason_text(r) for r in n["reasons"])
            cpu = f"{n['cost']['cpuSeconds']:.1f}" if n.get("cost") else "-"
            rows.append((n["status"], n["id"], cpu, reasons))
        width = max((len(r[1]) for r in rows), default=4)
        lines = [f"{'status':<8} {'task':<{width}} {'cpu s':>8}  why"] if rows else []
        lines += [f"{s:<8} {i:<{width}} {c:>8}  {w}" for s, i, c, w in rows]
        for stage, c in stages.items():
            more = shown.get(stage, 0) - (limit if limit is not None else shown.get(stage, 0))
            lines.append(f"{stage}: {c['hit']} hits, {c['run']} to run, {c['unknown']} unknown"
                         + (f" ({more} more not listed)" if more > 0 else ""))
        s = self.doc["summary"]
        lines.append(f"{s['tasks']} tasks: {s['hit']} hits, {s['run']} to run, {s['unknown']} unknown")
        gib = f"{s['peakBytes'] / (1 << 30):.2f} GiB" if s["peakBytes"] else "0"
        lines.append(f"estimated: {s['cpuSeconds']:.1f} CPU s, peak memory {gib}"
                     + (f" ({len(s['oversize'])} tasks above the budget run alone)" if s["oversize"] else ""))
        for name, v in s["layouts"].items():
            lines.append(f"layout {name}: write {v['write']} / remove {v['remove']} / keep {v['keep']}")
        return "\n".join(lines) + "\n"


def _reason_text(r: dict) -> str:
    extra = {k: v for k, v in r.items() if k != "code"}
    if not extra:
        return r["code"]
    return r["code"] + "{" + ",".join(f"{k}={_short(v)}" for k, v in extra.items()) + "}"


def _short(v) -> str:
    s = v if isinstance(v, str) else contract.key_text(v)
    return s[:12] if contract.is_sha256(s) else s


def plan(stages: dict[str, Stage], pipeline: list[str], store, *, params: dict | None = None,
         facts: dict | None = None, previous: dict | None = None, force=(), workers: int = 1,
         memory: int | None = None, worker_bytes: int = WORKER_BYTES, layouts: dict | None = None) -> Plan:
    """The plan of running `pipeline` (stage names, in order) with `params` ({stage: parameters}) over `facts`.
    `previous`: the run manifest to explain against; `force`: task ids or stage names ("*": all); `workers`,
    `memory`, `worker_bytes`: as the orchestrator's, for the peak-memory estimate; `layouts`: {name: {write,
    remove, keep}} (layout.diff)."""
    params = params or {}
    env = Env(store, facts)
    force = set(force)
    prev = {t["id"]: t for t in (previous or {}).get("tasks", [])}
    nodes, edges, runnable = [], set(), []
    for name in pipeline:
        stage = stages[name]
        p = stage.normalize(params.get(name))
        forced = "*" in force or name in force
        try:
            subjects = stage.subjects(env)
        except Pending as e:
            nodes.append({"id": contract.task_id(name, "*"), "stage": name, "key": None, "status": "unknown",
                          "reasons": [{"code": "pending", "task": e.task}], "cost": None})
            continue
        for subject in subjects:
            tid = contract.task_id(name, subject)
            try:
                edges.update((d, tid) for d in stage.depends(subject, env))
            except Pending:
                pass
            try:
                t = describe(stage, subject, p, env)
            except Pending as e:
                env.waiting(tid)
                nodes.append({"id": tid, "stage": name, "key": None, "status": "unknown",
                              "reasons": [{"code": "pending", "task": e.task}], "cost": None})
                continue
            old = prev.get(tid)
            if not (forced or tid in force) and store.has_result(t.key):
                env.done(tid, t.key)
                reasons = [{"code": "unchanged" if old is not None and old.get("key") == t.key else "cached"}]
                status = "hit"
            else:
                env.waiting(tid)
                reasons = _run_reasons(t, old, store, forced or tid in force)
                status = "run"
                runnable.append(t)
            nodes.append({"id": tid, "stage": name, "key": t.key, "status": status, "reasons": reasons,
                          "cost": t.cost.to_json() if t.cost is not None else None})
    return Plan(_doc(nodes, edges, runnable, workers, memory, worker_bytes, layouts or {}), runnable)


def _run_reasons(t, old: dict | None, store, forced: bool) -> list[dict]:
    if forced:
        return [{"code": "forced"}]
    if old is None:
        return [{"code": "new-input"}]
    out = []
    if old["status"] == "failed":
        out.append({"code": "previous-failed"})
    if old.get("key") == t.key:
        return out or [{"code": "result-missing"}]
    prior = store.result(old["key"]) if old.get("key") else None
    if prior is not None:
        reasons = why(prior["keyParts"], t.key_parts()) or [{"code": "key-changed"}]
        for r in reasons:
            if r["code"] == "input-changed" and r["new"] is not None and t.input(r["role"]).name:
                r["name"] = t.input(r["role"]).name
        out += reasons
    return out or [{"code": "previous-failed"}]


def _doc(nodes: list, edges: set, runnable: list, workers: int, memory: int | None, worker_bytes: int,
         layouts: dict) -> dict:
    count = {s: sum(n["status"] == s for n in nodes) for s in ("hit", "run", "unknown")}
    peaks = sorted((t.cost.peak_bytes if t.cost else 0 for t in runnable), reverse=True)
    procs = max(1, workers)
    budget = None if memory is None else max(0, memory - procs * worker_bytes)
    peak = sum(peaks[:procs])
    if budget is not None and peaks:
        peak = min(peak, max(budget, peaks[0]))
    peak += procs * worker_bytes if peaks else 0
    oversize = sorted({t.id for t in runnable if budget is not None and t.cost and t.cost.peak_bytes > budget})
    summary = {"tasks": len(nodes), **count,
               "cpuSeconds": round(sum(t.cost.cpu_seconds for t in runnable if t.cost), 3),
               "peakBytes": int(peak), "budget": memory, "oversize": oversize,
               "layouts": {k: dict(v) for k, v in sorted(layouts.items())}}
    return {"schema": contract.PLAN, "nodes": nodes, "edges": sorted([a, b] for a, b in edges), "summary": summary}
