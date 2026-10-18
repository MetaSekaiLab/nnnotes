"""Publish a complete site layout in dependency order, then collect retired owned files."""

from __future__ import annotations

import re

from ..pipeline import layout

INDEXES = frozenset({"charts.json", "models.json", "stories.json", "index.json", "site.json"})


def order(path: str) -> tuple[int, str]:
    if path in INDEXES:
        return 3, path
    if path.startswith("assets/"):
        return 0, path
    if path.startswith("products/"):
        return 1, path
    return 2, path


def materialize(directory, entries, depot, *, link="copy", params=None):
    """Place all files before replacing indexes. Failure keeps the ownership manifest and defers collection.

    Content-addressed products keep every replaced public manifest's dependencies coherent. The caller supplies
    the complete reference closure of selected and retained public entries. Unowned files are preserved.
    """
    seen = {}
    for entry in entries:
        path = entry["path"]
        if path.casefold() in {layout.MANIFEST.casefold(), layout.JOURNAL.casefold()}:
            raise layout.LayoutError(f"site file uses a reserved layout path: {path}")
        if re.search(r"[\\:?#\x00]", path) or any(p in {"", ".", ".."} for p in path.split("/")):
            raise layout.LayoutError(f"site file is not a relative path: {path}")
        old = seen.setdefault(path.casefold(), path)
        if old != path:
            raise layout.LayoutError(f"site paths differ only by case: {old}, {path}")
    return layout.materialize(
        directory, "site", params or {}, entries, layout.from_depot(depot), link=link, write_order=order, fail_fast=True
    )
