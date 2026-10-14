"""`nnnotes depot`: what `export assets` would run and why (plan), task descriptions run into the depot (run), the
check of every stored content and result (verify), and the removal of what no kept run reaches (gc)."""

from __future__ import annotations

import sys

from ..source.config import usable_cpus
from ..source.session import Session
from . import assets
from .common import command, group, report, usage, workers_arg


def cmd_verify(args, cfg):
    from ..pipeline.depot import Depot

    workers = usable_cpus() if args.workers is None else args.workers
    r = Depot(Session(cfg).depot_root()).verify(quick=args.quick, workers=workers)
    report(args, r)
    if r["problems"]:
        sys.exit(assets.EXIT_FAILED)


def cmd_gc(args, cfg):
    from ..pipeline.depot import Depot

    if args.keep is not None and args.keep < 1:
        usage(args, "--keep must be at least 1")
    r = Depot(Session(cfg).depot_root()).gc(keep=args.keep, dry_run=args.dry_run)
    report(
        args,
        r,
        lambda r: [
            f"{'would remove' if args.dry_run else 'removed'}: {r['runs']['removed']} runs, "
            f"{r['results']['removed']} results, {r['contents']['removed']} contents "
            f"({r['bytesRemoved'] / 1e6:.1f} MB)",
            f"kept: {r['runs']['kept']} runs, {r['results']['kept']} results, {r['contents']['kept']} contents",
        ],
    )


def register(sub) -> None:
    ss = group(sub, "depot", "the depot: its plan, single tasks, its check, its collection")

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

    c = command(ss, "run", assets.cmd_run_stage, "run task descriptions (nnnotes.task/1) into the depot")
    c.add_argument("tasks", nargs="+", metavar="TASK", help="task description files, or directories of them")
    c.add_argument("--fetch", action="store_true", help="fetch inputs through their catalog locators")
    c.add_argument("--force", action="store_true", help="run tasks whose result is stored (it must not change)")

    c = command(ss, "verify", cmd_verify, "check every stored content and result")
    c.add_argument("--quick", action="store_true", help="check sizes only, do not hash the contents")
    workers_arg(c, "parallel hashing", "the CPUs this process may use", zero="one at a time")

    c = command(ss, "gc", cmd_gc, "remove the runs, results and contents no kept run reaches")
    c.add_argument(
        "--keep",
        type=int,
        metavar="N",
        help="keep the N latest runs of each selection (default: every run; the latest run of a selection is kept)",
    )
    c.add_argument("--dry-run", action="store_true", help="count what would be removed, remove nothing")
