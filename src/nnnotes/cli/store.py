"""`nnnotes store`: what `export assets` would run and why (plan), task descriptions run into the store (run), and the
check of every stored content and result (verify)."""

from __future__ import annotations

import sys

from ..source.config import usable_cpus
from ..source.session import Session
from . import assets
from .common import command, group, report, workers_arg


def cmd_verify(args, cfg):
    from ..pipeline.store import Store

    workers = usable_cpus() if args.workers is None else args.workers
    r = Store(Session(cfg).store_root()).verify(quick=args.quick, workers=workers)
    report(args, r)
    if r["problems"]:
        sys.exit(assets.EXIT_FAILED)


def register(sub) -> None:
    ss = group(sub, "store", "the store of `export assets`: its plan, single tasks, its check")

    c = command(
        ss,
        "plan",
        assets.cmd_plan,
        "what `export assets` would run and why, without running it (--json: nnnotes.plan/1)",
    )
    c.add_argument("-o", "--out", help="output directory of the export (counts the layout files to write)")
    assets.run_args(c)
    c.add_argument("--since", metavar="RUN", help="explain against this run (default: the latest of the selection)")
    c.add_argument("--check", action="store_true", help="exit 1 when anything would run or change")
    c.add_argument("--census", action="store_true", help="first fetch and census the bundles without one")
    c.add_argument("--emit-tasks", metavar="DIR", help="write the runnable tasks as task descriptions")
    c.add_argument("--why", metavar="TASK", help="the reasons and key parts of one task")

    c = command(ss, "run", assets.cmd_run_stage, "run task descriptions (nnnotes.task/1) into the store")
    c.add_argument("tasks", nargs="+", metavar="TASK", help="task description files, or directories of them")
    c.add_argument("--fetch", action="store_true", help="fetch inputs through their catalog locators")
    c.add_argument("--force", action="store_true", help="run tasks whose result is stored (it must not change)")

    c = command(ss, "verify", cmd_verify, "check every stored content and result")
    c.add_argument("--quick", action="store_true", help="check sizes only, do not hash the contents")
    workers_arg(c, "parallel hashing", "the CPUs this process may use", zero="one at a time")
