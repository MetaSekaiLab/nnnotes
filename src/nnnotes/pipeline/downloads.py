"""Budgeted release of recoverable bundle downloads after complete conversion batches."""

from pathlib import Path

from ..core import downloads
from .contract import Input


class BundleDownloads:
    """A main-process batch observer; only closed, recoverable bundle inputs become eligible for release."""

    def __init__(self, depot, lease: Path | None, budget: int | None):
        self.depot, self.lease, self.budget = depot, Path(lease) if lease is not None else None, budget
        self.ready: dict[Path, Input] = {}
        self.removed = self.bytes_removed = 0

    def __call__(self, tasks):
        if self.budget is None or self.lease is None or not self.lease.is_file():
            return
        if self.lease.parent.resolve() != (self.depot.root / "leases").resolve() or self.lease.suffix != ".json":
            return
        if not tasks or any(task.stage != "unity.export" for task in tasks):
            return
        root = self.depot.downloads.resolve()
        for task in tasks:
            inp = task.input("bundle")
            kinds = {loc["kind"] for loc in inp.locators}
            if "catalog" not in kinds and not ("depot" in kinds and self.depot.has(inp.sha256, inp.size)):
                continue
            for loc in inp.locators:
                if loc["kind"] != "download":
                    continue
                path = (root / loc["path"]).resolve()
                if path.is_relative_to(root) and "bundles" in path.relative_to(root).parts[:-1]:
                    self.ready[path] = inp
        if not self.ready:
            return
        with downloads.exclusive(self.depot.root, self.lease) as alone:
            if not alone:
                return
            valid = []
            for path, inp in list(self.ready.items()):
                try:
                    matches = path.is_file() and self.depot.identify(path) == (inp.sha256, inp.size)
                except OSError:
                    matches = False
                if matches:
                    valid.append(path)
                else:
                    self.ready.pop(path)
            result = downloads.release(root, valid, self.budget)
            self.removed += result["removed"]
            self.bytes_removed += result["bytesRemoved"]
            for path in valid:
                if not path.exists():
                    self.ready.pop(path, None)
