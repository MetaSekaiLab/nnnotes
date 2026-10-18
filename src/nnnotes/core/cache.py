"""Memo caches for work that repeats within a run: decoded textures, PNG encodings, shader dumps, decoded cue sheets.

A bucket maps a content key to a value computed from exactly the inputs the key covers, so a hit returns what the
computation would return: the extractors' outputs stay byte-identical with or without the cache. Buckets live in the
process (LRU, bounded by a byte budget, thread-safe). Persistent task results belong to the depot.

    from nnnotes.core import cache
    cache.configure(enabled=False)                # every get misses, nothing is stored
    cache.configure(memory_mb=512)
    PNG = cache.bucket("png", salt=cache.source_salt(__file__))
    k = PNG.key(data, width, height)
    png = PNG.get(k)
    if png is None:
        png = encode(...)
        PNG.put(k, png)
"""

from __future__ import annotations

import hashlib
import os
import secrets
import struct
import threading
from collections import OrderedDict
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

DEFAULT_MEMORY_MB = 128


class Settings(TypedDict):
    """The cache settings of a process (configure)."""

    enabled: bool
    memory: int  # byte budget of each bucket
    closures: int  # loaded bundle closures kept per thread
    closure_bytes: int  # their bundle bytes


_settings: Settings = {
    "enabled": True,
    "memory": DEFAULT_MEMORY_MB << 20,
    "closures": 4,
    "closure_bytes": 512 << 20,
}
_buckets: dict[str, Bucket] = {}
_lock = threading.Lock()


def configure(
    *,
    enabled: bool | None = None,
    memory_mb: int | None = None,
    closures: int | None = None,
    closure_mb: int | None = None,
) -> Settings:
    """Set the caches of this process: `enabled`; `memory_mb` (byte budget of each bucket);
    `closures` / `closure_mb` (loaded bundle closures kept by
    unity.load_closure, per thread: count and bundle bytes). Clears the buckets. Returns the settings."""
    with _lock:
        if enabled is not None:
            _settings["enabled"] = bool(enabled)
        if memory_mb is not None:
            _settings["memory"] = max(0, int(memory_mb)) << 20
        if closures is not None:
            _settings["closures"] = max(0, int(closures))
        if closure_mb is not None:
            _settings["closure_bytes"] = max(0, int(closure_mb)) << 20
        for b in _buckets.values():
            b.clear()
        return settings()


def settings() -> Settings:
    """The current settings (a copy)."""
    return _settings.copy()


def enabled() -> bool:
    return _settings["enabled"]


def bucket(name: str, *, salt: str = "", max_item: int | None = None) -> Bucket:
    """The process's bucket `name` (created on first use).
    `max_item`: values larger than this many bytes are not kept (default: a quarter of the budget)."""
    with _lock:
        b = _buckets.get(name)
        if b is None:
            b = _buckets[name] = Bucket(name, salt, max_item)
        return b


def stats() -> dict[str, dict[str, int]]:
    """Per bucket: items, bytes, hits and misses."""
    return {n: b.stats() for n, b in sorted(_buckets.items())}


def clear() -> None:
    for b in _buckets.values():
        b.clear()


# ---------------------------------------------------------------- keys
class _Hash(Protocol):
    def update(self, data: bytes | memoryview, /) -> None: ...


def _feed(h: _Hash, part: object) -> None:
    if isinstance(part, (bytes, bytearray, memoryview)):
        mv = memoryview(cast("bytes", part)).cast("B")  # any buffer, read as bytes
        h.update(b"b" + struct.pack("<Q", mv.nbytes))
        h.update(mv)
    elif isinstance(part, str):
        b = part.encode("utf-8", "surrogatepass")
        h.update(b"s" + struct.pack("<Q", len(b)) + b)
    elif isinstance(part, (tuple, list)):
        items = cast("list[object]", part)
        h.update(b"t" + struct.pack("<Q", len(items)))
        for p in items:
            _feed(h, p)
    elif part is None or isinstance(part, (bool, int, float)):
        r = repr(part).encode("ascii")
        h.update(b"r" + struct.pack("<Q", len(r)) + r)
    else:
        raise TypeError(f"cache key part of type {type(part).__name__}")


def key(*parts: object) -> str:
    """sha256 hex of `parts` (bytes, str, int, float, bool, None and tuples / lists of them), unambiguous."""
    h = hashlib.sha256()
    _feed(h, parts)
    return h.hexdigest()


_salts: dict[str, str] = {}


def source_salt(path: str | os.PathLike[str], *dists: str) -> str:
    """A salt for keys of values a module produces: sha256 of the module's source file and the installed versions
    of the distributions `dists` (a missing one counts as "-")."""
    p = Path(path)
    k = f"{p}|{'|'.join(dists)}"
    s = _salts.get(k)
    if s is None:
        versions: list[str] = []
        for d in dists:
            try:
                versions.append(f"{d}={metadata.version(d)}")
            except metadata.PackageNotFoundError:
                versions.append(f"{d}=-")
        s = _salts[k] = key(p.read_bytes(), versions)
    return s


def file_id(path: str | os.PathLike[str]) -> tuple[str, int, int]:
    """(path, size, mtime) of a file: identifies an executable or input file in a key."""
    p = Path(path)
    st = p.stat()
    return (str(p), st.st_size, st.st_mtime_ns)


# ---------------------------------------------------------------- buckets
class Bucket:
    """A memo cache of values by content key (see the module documentation)."""

    def __init__(self, name: str, salt: str, max_item: int | None):
        self.name, self.salt, self._max_item = name, salt, max_item
        self._items: OrderedDict[str, tuple[Any, int]] = OrderedDict()  # key -> (value, byte size)
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def key(self, *parts: object) -> str:
        return key(self.name, self.salt, parts)

    def _limit(self) -> int:
        budget = _settings["memory"]
        return budget // 4 if self._max_item is None else min(self._max_item, budget)

    def get(self, k: str) -> Any:
        """The value stored under `k`, or None."""
        if not _settings["enabled"]:
            return None
        with self._lock:
            hit = self._items.get(k)
            if hit is not None:
                self._items.move_to_end(k)
                self.hits += 1
                return hit[0]
        with self._lock:
            self.misses += 1
        return None

    def put(self, k: str, value: Any, size: int | None = None) -> None:
        """Store `value` under `k`; `size`: its byte size (default len(value))."""
        if not _settings["enabled"]:
            return
        n = len(value) if size is None else int(size)
        self._remember(k, value, n)

    def _remember(self, k: str, value: Any, n: int) -> None:
        if n > self._limit():
            return
        with self._lock:
            old = self._items.pop(k, None)
            if old is not None:
                self._bytes -= old[1]
            self._items[k] = (value, n)
            self._bytes += n
            budget = _settings["memory"]
            while self._bytes > budget and self._items:
                _, (_, m) = self._items.popitem(last=False)
                self._bytes -= m

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._items),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
            }


def temp_path(dst: Path) -> Path:
    """A new temporary file name next to `dst` for writing it atomically: short whatever `dst`'s name (a long name
    plus a suffix would pass the file system's name limit), random, so unique across processes and threads."""
    return Path(dst).with_name(f".{secrets.token_hex(8)}.part")


def write_atomic(dst: Path, data: bytes) -> None:
    """Write `data` to `dst` through temp_path(dst) (created exclusively, default permissions) and a rename; the
    temporary file is removed when anything fails."""
    tmp = temp_path(dst)
    try:
        with open(tmp, "xb") as f:
            f.write(data)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- packing several blobs into one value
def pack(meta: bytes, blobs: list[bytes]) -> bytes:
    """One bytes value from a header and blobs."""
    out = [struct.pack("<II", len(meta), len(blobs)), meta]
    for b in blobs:
        out += [struct.pack("<Q", len(b)), b]
    return b"".join(out)


def unpack(data: bytes) -> tuple[bytes, list[bytes]]:
    n, count = struct.unpack_from("<II", data, 0)
    pos = 8
    meta = data[pos : pos + n]
    pos += n
    blobs: list[bytes] = []
    for _ in range(count):
        (m,) = struct.unpack_from("<Q", data, pos)
        pos += 8
        blobs.append(data[pos : pos + m])
        pos += m
    if pos != len(data):
        raise ValueError("packed cache value has trailing bytes")
    return meta, blobs
