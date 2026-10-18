"""Budgeted depot downloads, with finished-batch release and command leases protecting active readers.

    <depot>/downloads/                the game files as downloaded (source.catalog)
    <depot>/leases/<lease>.json       one per running command: {"host", "pid", "started"}
    <depot>/leases/.exclusive         held by a command that must be the only one (trimming, `depot gc`)

A command holds a lease while it runs (`lease`). A successful object conversion batch may release its recoverable
bundle downloads toward the budget (`release`) while it is the only command. When the command ends and no other
lease is live (a lease of this host whose
process runs, or of another host younger than a day), the files under the `bundles/` and `raw/` directories of
downloads/ are removed, least recently used first (a file's modification time: the catalog sets it when it hands
the file out), until they fit the budget. Catalog files stay. A removed file is downloaded again when a command needs
it: tasks name their inputs by content and know where to fetch them.

A command that must run alone (`exclusive`) creates `.exclusive` before it looks for other leases, and a command
that starts writes its lease before it waits for `.exclusive` to go: either the first sees the new lease and does
nothing, or the new command touches nothing until the first is done.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import socket
import sys
import time
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = ["evictable", "exclusive", "lease", "parse_budget", "release", "trim"]

FOREIGN_LEASE_SECONDS = 24 * 3600  # a lease of another host counts as live this long
EXCLUSIVE = ".exclusive"
TORN_SECONDS = 60  # an .exclusive without a complete record (its writer stopped mid-write) counts as live this long
_UNITS = {"": 1, "b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}
_UNITS.update({"kib": 1 << 10, "mib": 1 << 20, "gib": 1 << 30, "tib": 1 << 40})
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*$", re.IGNORECASE)


def parse_budget(text: str | None) -> int | None:
    """The size a budget text names: bytes (`0`, `500MB`, `20GiB`, `1.5 TB`), None for `all` (no limit); empty: 0."""
    if text is None or not text.strip():
        return 0
    if text.strip().lower() == "all":
        return None
    m = _SIZE.match(text)
    unit = m.group(2).lower() if m else None
    if m is None or unit not in _UNITS:
        raise ValueError(f"{text!r} is not a size (e.g. 0, 500MB, 20GiB) or all")
    return int(float(m.group(1)) * _UNITS[unit])


def touch(path: Path) -> None:
    """Mark a downloaded file as used now (its modification time)."""
    try:
        os.utime(path)
    except OSError:
        pass


def evictable(downloads: Path) -> Iterator[Path]:
    """The bundles and raw files of a downloads directory: the files below a `bundles/` or `raw/` directory."""
    if not downloads.is_dir():
        return
    for p in downloads.rglob("*"):
        if p.is_file() and not p.name.endswith(".part"):
            if {"bundles", "raw"} & set(p.relative_to(downloads).parts[:-1]):
                yield p


def _files(paths: Iterable[Path]) -> list[tuple[int, int, Path]]:
    out: list[tuple[int, int, Path]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append((stat.st_mtime_ns, stat.st_size, path))
    return out


def _remove(files: list[tuple[int, int, Path]], needed: int, dry_run: bool) -> tuple[list[Path], int]:
    removed: list[Path] = []
    removed_bytes = 0
    for _, size, path in sorted(files, key=lambda f: (f[0], str(f[2]))):
        if removed_bytes >= needed:
            break
        if not dry_run:
            try:
                path.unlink()
            except OSError:
                continue
        removed.append(path)
        removed_bytes += size
    return removed, removed_bytes


def trim(downloads: Path, budget: int | None, *, dry_run: bool = False) -> dict[str, int]:
    """Remove the least recently used bundles and raw files until the rest fit `budget` bytes (None: nothing).
    -> {"kept", "removed", "bytesKept", "bytesRemoved"}."""
    files = _files(evictable(downloads))
    total = sum(f[1] for f in files)
    removed, removed_bytes = _remove(files, 0 if budget is None else total - budget, dry_run)
    if not dry_run and removed:
        _prune_dirs(downloads)
    return {
        "kept": len(files) - len(removed),
        "removed": len(removed),
        "bytesKept": total - removed_bytes,
        "bytesRemoved": removed_bytes,
    }


def release(downloads: Path, ready: Iterable[Path], budget: int | None) -> dict[str, int]:
    """Trim only finished downloads, in LRU order, toward the cache budget. The caller holds an exclusive lease.

    Active files remain protected even when they exceed the budget. A zero budget examines only finished files.
    Paths must resolve below a bundles/ or raw/ directory within downloads; partial files are retained.
    """
    if budget is None:
        return {"removed": 0, "bytesRemoved": 0}
    root = downloads.resolve()
    paths: set[Path] = set()
    for path in ready:
        path = path.resolve()
        if not path.is_relative_to(root) or not {"bundles", "raw"} & set(path.relative_to(root).parts[:-1]):
            raise ValueError("released file must be a bundle or raw file within the downloads directory")
        if path.is_file() and not path.name.endswith(".part"):
            paths.add(path)
    files = _files(paths)
    total = sum(f[1] for f in files) if budget == 0 else sum(f[1] for f in _files(evictable(root)))
    removed, size = _remove(files, total - budget, False)
    for path in removed:
        parent = path.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return {"removed": len(removed), "bytesRemoved": size}


def _prune_dirs(root: Path) -> None:
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()  # only an empty directory
        except OSError:
            pass


def _alive(pid: int) -> bool:
    """Whether a process of this host runs (an unknown answer counts as running)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live(doc: dict[str, Any], host: str, now: float) -> bool:
    if doc.get("host") == host:
        return _alive(int(doc.get("pid") or 0))
    return now - float(doc.get("started") or 0) < FOREIGN_LEASE_SECONDS


def others_live(root: Path, own: Path | None = None) -> bool:
    """Whether a lease other than `own` is live in the depot `root` (stale leases are removed on the way)."""
    host, now = socket.gethostname(), time.time()
    for p in sorted((root / "leases").glob("*.json")):
        if own is not None and p == own:
            continue
        try:
            doc = json.loads(p.read_bytes())
        except (OSError, ValueError):
            continue
        if _live(doc, host, now):
            return True
        p.unlink(missing_ok=True)
    return False


def _record() -> str:
    return json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "started": time.time()})


def _wait_alone(leases: Path, timeout: float) -> None:
    """Wait at most `timeout` seconds for `.exclusive`; leave a live owner's lock intact."""
    p, host = leases / EXCLUSIVE, socket.gethostname()
    deadline, announced = time.monotonic() + timeout, False
    while True:
        try:
            data, mtime = p.read_bytes(), p.stat().st_mtime
        except FileNotFoundError:
            return
        try:
            live = _live(json.loads(data), host, time.time())
        except ValueError:  # being written
            live = time.time() - mtime < TORN_SECONDS
        if not live:
            p.unlink(missing_ok=True)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"depot exclusive lease {p} is still held; retry after its owner finishes")
        if not announced:
            print(f"waiting for depot exclusive lease {p} (at most {timeout:g} seconds)", file=sys.stderr, flush=True)
            announced = True
        time.sleep(0.1)


@contextmanager
def exclusive(root: Path, own: Path | None) -> Generator[bool]:
    """Hold `.exclusive` of the depot `root` while the block runs: True when no command but the one holding `own`
    holds a lease (a command that starts meanwhile waits for the block to end); False when another one does."""
    lock = root / "leases" / EXCLUSIVE
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock, "x", encoding="utf-8") as f:
            f.write(_record())
    except FileExistsError:  # another command holds it
        yield False
        return
    try:
        yield not others_live(root, own)
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def lease(root: Path, budget: int | None, *, wait_timeout: float = 30) -> Generator[Path]:
    """Hold a lease on the depot `root` while the block runs; at its end, when no other lease is live, trim the
    downloads to `budget` (None: keep them all)."""
    d = root / "leases"
    d.mkdir(parents=True, exist_ok=True)
    own = d / f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}.json"
    own.write_text(_record(), encoding="utf-8")
    try:
        _wait_alone(d, wait_timeout)
        yield own
    finally:
        try:
            if budget is not None:
                with exclusive(root, own) as alone:
                    if alone:
                        trim(root / "downloads", budget)
        finally:
            own.unlink(missing_ok=True)
