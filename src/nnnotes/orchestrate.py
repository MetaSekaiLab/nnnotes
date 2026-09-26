"""The local orchestrator: the stages of a pipeline over their subjects, with a pool of worker processes.

Stages run in the order given; a stage's tasks are described once the stages before it are done (Env: the facts of
the caller plus the keys of the tasks already done), so every input is known when a task is described. Per stage:
a task whose result is in the store is a hit; the others run in parallel, then the next stage starts.

The pool: `workers` spawned processes, long-lived (each imports what its stages need once), one task at a time
each. Tasks start in order of decreasing estimated CPU (longest first), a task only when the estimated peak memory
of the running tasks plus its own fits `memory` less `worker_bytes` per worker (what the processes hold before
any task); a task estimated above that budget runs alone and is reported. A worker is replaced after
`recycle_tasks` tasks, or when its resident memory after a task is above `recycle_rss`. `workers=0` runs the tasks
in this process, one after another.

Failures: a failed object is an item of its task's result, cached with it. An exception out of a stage, an input
that cannot be read, or a worker that dies fails the task: nothing of it is cached, the run records it
(failures(), the run log) and goes on; a task that reads the result of a failed task fails as `upstream.failed`.
A ConfigError (a setting or tool the run needs) stops the run: no new task starts, and run() raises it once the
running tasks have ended (their results stay valid).
Stage results do not depend on the number of workers, their order or their timing: a run with one worker and a run
with many leave identical cas/ and ac/ trees and the same run manifest.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from multiprocessing.connection import wait

from . import contract
from .config import ConfigError
from .contract import Task
from .stages import Env, Pending, Stage, describe, execute

WORKER_BYTES = 70 << 20        # resident memory of an idle worker process (interpreter and imports), per worker
# failure codes of tasks (failed items carry the codes of their stages)
FAILURE_CODES = ("task.error", "task.input", "task.io", "task.memory", "task.conflict", "task.incompatible",
                 "task.worker_died", "task.describe", "upstream.failed", "upstream.missing", "config")


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- process memory
def rss() -> int | None:
    """This process's resident memory in bytes (None where the platform does not tell)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


def reset_peak() -> None:
    """Restart the peak-memory counter of this process (Linux; elsewhere nothing)."""
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
    except OSError:
        pass


def peak_rss() -> int | None:
    """The peak resident memory of this process since reset_peak (Linux), or None."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


# ---------------------------------------------------------------- one task, here or in a worker
def failure(task: str, stage: str, code: str, message: str, inp: str | None = None, obj: str | None = None) -> dict:
    """A failures/1 entry."""
    return {"task": task, "stage": stage, "input": inp, "object": obj, "code": code, "message": message}


def _error_code(e: BaseException) -> str:
    from .contract import IncompatibleTask
    from .store import InputMissing, StoreConflict
    if isinstance(e, IncompatibleTask):
        return "task.incompatible"
    if isinstance(e, InputMissing):
        return "task.input"
    if isinstance(e, StoreConflict):
        return "task.conflict"
    if isinstance(e, MemoryError):
        return "task.memory"
    if isinstance(e, OSError):
        return "task.io"
    return "task.error"


def run_one(task: Task, store, stages: dict, force: bool) -> dict:
    """Execute one task and measure it: the reply a worker sends (status, result status, cost, or error)."""
    reset_peak()
    try:
        ex = execute(task, store, stages, force=force)
    except ConfigError as e:
        return {"ok": False, "code": "config", "message": str(e), "trace": "", "rss": rss()}
    except Exception as e:
        return {"ok": False, "code": _error_code(e), "message": f"{type(e).__name__}: {e}"[:2000],
                "trace": "".join(traceback.format_exception(type(e), e, e.__traceback__))[-4000:], "rss": rss()}
    reply = {"ok": True, "status": ex.status, "resultStatus": ex.result_status, "rss": rss()}
    if ex.cost is not None:
        cost = dict(ex.cost, peakRssBytes=peak_rss())
        store.write_cost(task.key, cost)
        reply["cost"] = cost
    return reply


def _worker_main(conn, store, stages: dict, initializer, initargs) -> None:
    if initializer is not None:
        initializer(store, *initargs)
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        doc, force = msg
        try:
            task = Task.from_json(doc)
        except Exception as e:
            conn.send({"ok": False, "code": "task.incompatible", "message": str(e), "trace": "", "rss": rss()})
            continue
        conn.send(run_one(task, store, stages, force))


class _Worker:
    def __init__(self, ctx, n: int, store, stages, initializer, initargs):
        self.n = n
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, store, stages, initializer, initargs),
                                name=f"nnnotes-worker-{n}", daemon=True)
        self.proc.start()
        child.close()
        self.task: Task | None = None
        self.done = 0

    def send(self, task: Task, force: bool) -> None:
        self.task = task
        self.conn.send((task.to_json(), force))

    def stop(self, wait_s: float = 10.0) -> None:
        try:
            self.conn.send(None)
        except OSError:
            pass
        self.proc.join(wait_s)
        if self.proc.is_alive():
            self.proc.kill()
            self.proc.join()
        self.conn.close()


# ---------------------------------------------------------------- scheduling
class Scheduler:
    """The start order of a stage's tasks: decreasing estimated CPU (then id), admitted while the estimated peak
    memory of the running tasks plus the next one fits `budget` (bytes; None: no limit). A task estimated above the
    budget waits until nothing runs, then runs alone; no task behind it in the order starts before it."""

    def __init__(self, tasks, budget: int | None):
        self.queue = sorted(tasks, key=lambda t: (-_cpu(t), t.id))
        self.budget = budget
        self.running: dict[str, int] = {}
        self.oversize: list[str] = []

    def used(self) -> int:
        return sum(self.running.values())

    def next(self) -> Task | None:
        used = self.used()
        for i, t in enumerate(self.queue):
            need = _peak(t)
            if self.budget is None or used + need <= self.budget:
                return self._take(i)
            if need > self.budget:
                if self.running:
                    return None
                self.oversize.append(t.id)
                return self._take(i)
        return None

    def _take(self, i: int) -> Task:
        t = self.queue.pop(i)
        self.running[t.id] = _peak(t)
        return t

    def finished(self, tid: str) -> None:
        self.running.pop(tid, None)


def _cpu(t: Task) -> float:
    return t.cost.cpu_seconds if t.cost is not None else 0.0


def _peak(t: Task) -> int:
    return t.cost.peak_bytes if t.cost is not None else 0


# ---------------------------------------------------------------- the run
@dataclass
class Run:
    """What an orchestrated run did: per task id its key and status (hit, ran, failed), the failures, the log."""
    store: object
    context: dict
    selection: list
    params: dict
    stages: dict
    tasks: dict = field(default_factory=dict)          # task id -> {"id", "key", "status"}
    failures_: list = field(default_factory=list)
    log: list = field(default_factory=list)
    oversize: list = field(default_factory=list)

    def results(self):
        """(task id, result document) of every task with a result, by task id."""
        for tid in sorted(self.tasks):
            t = self.tasks[tid]
            if t["status"] != "failed":
                yield tid, self.store.result(t["key"])

    def failures(self) -> list[dict]:
        """Task failures and failed items, sorted (task, object)."""
        out = list(self.failures_)
        for tid, doc in self.results():
            stage = contract.parse_task_id(tid)[0]
            for it in doc["items"]:
                if it["status"] == "failed":
                    out.append(failure(tid, stage, it["reason"]["code"], it["reason"]["message"], obj=it["object"]))
        return sorted(out, key=lambda f: (f["task"], f["object"] or "", f["code"]))

    def summary(self) -> dict:
        statuses = Counter(t["status"] for t in self.tasks.values())
        items: Counter = Counter()
        artifacts = 0
        for _, doc in self.results():
            items.update(i["status"] for i in doc["items"])
            artifacts += len(doc["artifacts"])
        return {"tasks": len(self.tasks), **{s: statuses.get(s, 0) for s in contract.RUN_TASK_STATUSES},
                "items": {s: items.get(s, 0) for s in contract.ITEM_STATUSES}, "artifacts": artifacts}

    @property
    def exit_code(self) -> int:
        """0 when every task and item succeeded (unsupported objects allowed), else 1."""
        return 1 if self.failures() else 0

    def manifest(self, layouts: dict | None = None) -> dict:
        return contract.run_doc(context=self.context, selection=self.selection, params=self.params,
                                stages=self.stages, tasks=list(self.tasks.values()), layouts=layouts or {},
                                summary=self.summary())

    def write(self, layouts: dict | None = None) -> dict:
        """Write the run manifest (layouts: {name: sha256 of its layout manifest}) and this execution's log."""
        doc = self.manifest(layouts)
        self.store.write_run(doc, self.log)
        return doc

    def coverage(self, census: dict | None = None) -> dict:
        return coverage(self.results(), census)

    def failures_doc(self) -> dict:
        return {"schema": contract.FAILURES, "failures": self.failures()}


SEVERITY = ("failed", "unsupported", "generic", "exported", "contained")   # most severe first


def coverage(results, census: dict | None = None) -> dict:
    """The coverage document (nnnotes.coverage/1) of results [(task id, result)]: per class the count of every item
    status and the census total (`census`: {class: objects}; default: the item count; `missing` = total - objects
    with an item); per reason code its count and the first five object ids (sorted). An object with items in several
    results (a sprite whose image sprite.crop makes) counts once, with the most severe of its statuses (SEVERITY)
    and that item's reason."""
    best: dict[str, dict] = {}
    for _, doc in results:
        for it in doc["items"]:
            old = best.get(it["object"])
            if old is None or SEVERITY.index(it["status"]) < SEVERITY.index(old["status"]):
                best[it["object"]] = it
    classes: dict[str, Counter] = {}
    reasons: dict[str, list] = {}
    for it in best.values():
        classes.setdefault(it.get("class", "?"), Counter())[it["status"]] += 1
        if "reason" in it:
            reasons.setdefault(it["reason"]["code"], []).append(it["object"])
    out_classes = {}
    for cls in sorted(set(classes) | set(census or {})):
        c = classes.get(cls, Counter())
        n = sum(c.values())
        total = (census or {}).get(cls, n)
        out_classes[cls] = {"total": total, "missing": total - n, **{s: c.get(s, 0) for s in contract.ITEM_STATUSES}}
    return {"schema": contract.COVERAGE, "classes": out_classes,
            "reasons": {code: {"count": len(ids), "examples": sorted(ids)[:5]}
                        for code, ids in sorted(reasons.items())}}


class Orchestrator:
    """Runs pipelines of stages into a store (see the module documentation). `initializer(store, *initargs)` runs
    first in every worker (settings, caches, the store's fetcher); it must be importable (spawned processes)."""

    def __init__(self, store, stages: dict[str, Stage], *, workers: int = 0, memory: int | None = None,
                 worker_bytes: int = WORKER_BYTES, recycle_tasks: int | None = None, recycle_rss: int | None = None,
                 initializer=None, initargs: tuple = (), log=_log):
        self.store, self.stages = store, stages
        self.workers, self.memory, self.worker_bytes = max(0, int(workers)), memory, int(worker_bytes)
        self.recycle_tasks, self.recycle_rss = recycle_tasks, recycle_rss
        self.initializer, self.initargs = initializer, tuple(initargs)
        self.log = log
        self._pool: list[_Worker] = []
        self._started = 0

    def explain(self) -> list[str]:
        """Every knob of the run, with its value."""
        gib = lambda b: "none" if b is None else f"{b / (1 << 30):.1f} GiB"   # noqa: E731
        return [f"workers: {self.workers or 'none (tasks run in this process)'}",
                f"memory budget: {gib(self.memory)}, of which {gib(self.task_budget())} for the running tasks' "
                f"estimated peaks ({gib(self.worker_bytes)} per worker process)",
                "order: longest estimated CPU first",
                f"worker recycling: after {self.recycle_tasks or 'unlimited'} tasks, "
                f"above {gib(self.recycle_rss)} resident"
                + ("" if rss() is not None else " (resident memory not measurable here: size limit inactive)")]

    def task_budget(self) -> int | None:
        """The memory the running tasks' estimates may sum to: the budget less what the worker processes hold."""
        if self.memory is None:
            return None
        return max(0, self.memory - max(1, self.workers) * self.worker_bytes)

    # ---------------------------------------------------------------- pipeline
    def run(self, pipeline: list[str], *, params: dict | None = None, facts: dict | None = None,
            context: dict | None = None, selection=(), force=()) -> Run:
        """Run the stages named in `pipeline`, in order. `params`: {stage: parameters}; `facts`: the planning
        facts; `context` / `selection`: recorded in the run manifest; `force`: task ids or stage names ("*": all)
        run again even when their result is in the store (the new result must be identical)."""
        params = params or {}
        unknown = [n for n in pipeline if n not in self.stages]
        if unknown:
            raise ValueError(f"unknown stages: {', '.join(unknown)}")
        for i, n in enumerate(pipeline):
            late = [a for a in self.stages[n].after if a in pipeline and pipeline.index(a) > i]
            if late:
                raise ValueError(f"stage {n} runs before {', '.join(late)}, which it reads")
        norm = {n: self.stages[n].normalize(params.get(n)) for n in pipeline}
        run = Run(self.store, dict(context or {}), list(selection), norm,
                  {n: self.stages[n].version for n in pipeline})
        env = Env(self.store, facts)
        force = set(force)
        for line in self.explain():
            run.log.append({"event": "knob", "value": line})
        try:
            for name in pipeline:
                self._stage(self.stages[name], norm[name], env, force, run)
        except ConfigError as e:
            e.run = run                      # what ran before the stop: its results stay valid
            raise
        finally:
            self.close()
        return run

    def _stage(self, stage: Stage, params: dict, env: Env, force: set, run: Run) -> None:
        t0 = time.perf_counter()
        tasks: list[Task] = []
        try:
            subjects = stage.subjects(env)
        except Pending as p:
            self._fail(run, env, contract.task_id(stage.name, "*"), None, stage.name,
                       "upstream.failed" if p.failed else "upstream.missing", f"needs {p.task}: {p}")
            return
        for subject in subjects:
            tid = contract.task_id(stage.name, subject)
            try:
                tasks.append(describe(stage, subject, params, env))
            except Pending as p:
                self._fail(run, env, tid, None, stage.name,
                           "upstream.failed" if p.failed else "upstream.missing", f"needs {p.task}: {p}")
            except Exception as e:
                self._fail(run, env, tid, None, stage.name, "task.describe", f"{type(e).__name__}: {e}"[:2000])
        forced = "*" in force or stage.name in force
        todo = []
        for t in sorted(tasks, key=lambda t: t.id):
            if not (forced or t.id in force) and self.store.has_result(t.key):
                run.tasks[t.id] = {"id": t.id, "key": t.key, "status": "hit"}
                env.done(t.id, t.key)
            else:
                todo.append(t)
        replies = self._execute(todo, force=lambda t: forced or t.id in force, run=run)
        stop = next((r for r in replies.values() if r.get("code") == "config"), None)
        for t in todo:
            reply = replies.get(t.id) or {"ok": False, "code": "config", "message": "not started: the run stopped"}
            if reply["ok"]:
                run.tasks[t.id] = {"id": t.id, "key": t.key, "status": "ran"}
                env.done(t.id, t.key)
            else:
                self._fail(run, env, t.id, t.key, stage.name, reply["code"], reply["message"], _input_name(t))
        n = Counter(run.tasks[t.id]["status"] for t in tasks)
        self.log(f"{stage.name}: {len(tasks)} tasks, {n['hit']} hits, {n['ran']} ran, {n['failed']} failed "
                 f"({time.perf_counter() - t0:.1f} s)")
        if stop is not None:
            raise ConfigError(stop["message"])

    @staticmethod
    def _fail(run: Run, env: Env, tid: str, key, stage: str, code: str, message: str, inp=None) -> None:
        run.tasks[tid] = {"id": tid, "key": key, "status": "failed"}
        run.failures_.append(failure(tid, stage, code, message, inp))
        env.waiting(tid, failed=True)

    # ---------------------------------------------------------------- execution
    def _execute(self, tasks: list[Task], force, run: Run) -> dict[str, dict]:
        """Run `tasks`; {task id: reply}."""
        if not tasks:
            return {}
        sched = Scheduler(tasks, self.task_budget())
        replies: dict[str, dict] = {}
        if self.workers == 0:
            while True:
                t = sched.next()
                if t is None:
                    break
                self._record(run, t, sched, None)
                replies[t.id] = run_one(t, self.store, self.stages, force(t))
                self._done(run, t, replies[t.id], None)
                sched.finished(t.id)
                if replies[t.id].get("code") == "config":
                    sched.queue.clear()
        else:
            self._pool_run(sched, force, replies, run)
        run.oversize += sched.oversize
        return replies

    def _spawn(self) -> _Worker:
        self._started += 1
        return _Worker(mp.get_context("spawn"), self._started, self.store, self.stages, self.initializer,
                       self.initargs)

    def _pool_run(self, sched: Scheduler, force, replies: dict, run: Run) -> None:
        while len(self._pool) < self.workers:
            self._pool.append(self._spawn())
        try:
            while sched.queue or sched.running:
                for i in range(len(self._pool)):
                    if self._pool[i].task is None:
                        t = sched.next()
                        if t is None:
                            break
                        if not self._pool[i].proc.is_alive():      # died while idle
                            self._replace(self._pool[i])
                        w = self._pool[i]
                        self._record(run, t, sched, w)
                        w.send(t, force(t))
                busy = [w for w in self._pool if w.task is not None]
                ready = wait([w.conn for w in busy] + [w.proc.sentinel for w in busy])
                for w in busy:
                    if w.conn not in ready and w.proc.sentinel not in ready:
                        continue
                    t = w.task
                    reply = None
                    try:
                        if w.conn.poll():
                            reply = w.conn.recv()
                    except (EOFError, OSError):
                        reply = None
                    if reply is None:
                        w.proc.join(5)
                        reply = {"ok": False, "code": "task.worker_died",
                                 "message": f"worker exited with code {w.proc.exitcode} while running the task",
                                 "trace": "", "rss": None}
                    replies[t.id] = reply
                    self._done(run, t, reply, w)
                    sched.finished(t.id)
                    if reply.get("code") == "config":
                        sched.queue.clear()
                    w.task = None
                    w.done += 1
                    if not w.proc.is_alive() or reply.get("code") == "task.worker_died" or self._recycle(w, reply):
                        self._replace(w)
        except BaseException:
            self.close(kill=True)
            raise

    def _recycle(self, w: _Worker, reply: dict) -> bool:
        if self.recycle_tasks and w.done >= self.recycle_tasks:
            return True
        r = reply.get("rss")
        return bool(self.recycle_rss and r is not None and r > self.recycle_rss)

    def _replace(self, w: _Worker) -> None:
        if w.proc.is_alive():
            w.stop()
        else:
            w.proc.join(5)
            w.conn.close()
        self._pool[self._pool.index(w)] = self._spawn()

    def _record(self, run: Run, t: Task, sched: Scheduler, w) -> None:
        run.log.append({"event": "start", "task": t.id, "key": t.key, "worker": None if w is None else w.n,
                        "estimate": t.cost.to_json() if t.cost is not None else None,
                        "runningBytes": sched.used() - _peak(t), "budget": sched.budget})

    def _done(self, run: Run, t: Task, reply: dict, w) -> None:
        e = {"event": "end", "task": t.id, "worker": None if w is None else w.n, "ok": reply["ok"],
             "rss": reply.get("rss")}
        if reply["ok"]:
            e.update(status=reply["status"], cost=reply.get("cost"))
        else:
            e.update(code=reply["code"], message=reply["message"], trace=reply.get("trace", ""))
            self.log(f"{t.id}: failed: {reply['message'][:300]}")
        run.log.append(e)

    def close(self, kill: bool = False) -> None:
        """Stop the worker processes."""
        pool, self._pool = self._pool, []
        for w in pool:
            if kill:
                w.proc.kill()
                w.proc.join()
                w.conn.close()
            else:
                w.stop()


def _input_name(t: Task) -> str | None:
    if not t.inputs:
        return None
    i = t.inputs[0]
    return i.name or i.sha256
