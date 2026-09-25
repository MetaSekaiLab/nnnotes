"""Memo caches for work that repeats within a run: decoded textures, PNG encodings, shader dumps, decoded cue sheets.

A bucket maps a content key to a value computed from exactly the inputs the key covers, so a hit returns what the
computation would return: the extractors' outputs stay byte-identical with or without the cache. Buckets live in the
process (LRU, bounded by a byte budget, thread-safe). Buckets that hold bytes can also keep them on disk
(`configure(directory=...)`): processes of one build then share the results, and a later run with the same inputs
reuses them. Keys of the disk layer are salted with the source of the module that produces the value and the
versions of the libraries it calls, so a code or library change never reads an older result.

    from nnnotes import cache
    cache.configure(enabled=False)                # every get misses, nothing is stored
    cache.configure(memory_mb=512, directory="work/cache")
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
import struct
import threading
from collections import OrderedDict
from importlib import metadata
from pathlib import Path

DISK_MAGIC = b"NNC1"
DEFAULT_MEMORY_MB = 128

_settings = {"enabled": True, "memory": DEFAULT_MEMORY_MB << 20, "directory": None, "closures": 4,
             "closure_bytes": 512 << 20}
_buckets: dict[str, "Bucket"] = {}
_lock = threading.Lock()


def configure(*, enabled: bool | None = None, memory_mb: int | None = None, directory=None,
              closures: int | None = None, closure_mb: int | None = None) -> dict:
    """Set the caches of this process: `enabled`; `memory_mb` (byte budget of each bucket); `directory` (disk layer
    of the byte buckets; "" or None for none); `closures` / `closure_mb` (loaded bundle closures kept by
    unity.load_closure, per thread: count and bundle bytes). Clears the buckets. Returns the settings."""
    with _lock:
        if enabled is not None:
            _settings["enabled"] = bool(enabled)
        if memory_mb is not None:
            _settings["memory"] = max(0, int(memory_mb)) << 20
        if directory is not None:
            _settings["directory"] = Path(directory) if directory else None
        if closures is not None:
            _settings["closures"] = max(0, int(closures))
        if closure_mb is not None:
            _settings["closure_bytes"] = max(0, int(closure_mb)) << 20
        for b in _buckets.values():
            b.clear()
        return settings()


def settings() -> dict:
    """The current settings (a copy)."""
    return dict(_settings)


def enabled() -> bool:
    return _settings["enabled"]


def bucket(name: str, *, salt: str = "", disk: bool = False, max_item: int | None = None) -> "Bucket":
    """The process's bucket `name` (created on first use). `disk`: its values are bytes and may be kept on disk;
    `max_item`: values larger than this many bytes are not kept (default: a quarter of the budget)."""
    with _lock:
        b = _buckets.get(name)
        if b is None:
            b = _buckets[name] = Bucket(name, salt, disk, max_item)
        return b


def stats() -> dict:
    """Per bucket: items, bytes, hits, misses, disk hits, disk writes."""
    return {n: b.stats() for n, b in sorted(_buckets.items())}


def clear() -> None:
    for b in _buckets.values():
        b.clear()


# ---------------------------------------------------------------- keys
def _feed(h, part) -> None:
    if isinstance(part, (bytes, bytearray, memoryview)):
        mv = memoryview(part).cast("B")
        h.update(b"b" + struct.pack("<Q", mv.nbytes))
        h.update(mv)
    elif isinstance(part, str):
        b = part.encode("utf-8", "surrogatepass")
        h.update(b"s" + struct.pack("<Q", len(b)) + b)
    elif isinstance(part, (tuple, list)):
        h.update(b"t" + struct.pack("<Q", len(part)))
        for p in part:
            _feed(h, p)
    elif part is None or isinstance(part, (bool, int, float)):
        r = repr(part).encode("ascii")
        h.update(b"r" + struct.pack("<Q", len(r)) + r)
    else:
        raise TypeError(f"cache key part of type {type(part).__name__}")


def key(*parts) -> str:
    """sha256 hex of `parts` (bytes, str, int, float, bool, None and tuples / lists of them), unambiguous."""
    h = hashlib.sha256()
    _feed(h, parts)
    return h.hexdigest()


_salts: dict[str, str] = {}


def source_salt(path, *dists: str) -> str:
    """A salt for keys of values a module produces: sha256 of the module's source file and the installed versions
    of the distributions `dists` (a missing one counts as "-")."""
    p = Path(path)
    k = f"{p}|{'|'.join(dists)}"
    s = _salts.get(k)
    if s is None:
        versions = []
        for d in dists:
            try:
                versions.append(f"{d}={metadata.version(d)}")
            except metadata.PackageNotFoundError:
                versions.append(f"{d}=-")
        s = _salts[k] = key(p.read_bytes(), versions)
    return s


def file_id(path) -> tuple:
    """(path, size, mtime) of a file: identifies an executable or input file in a key."""
    p = Path(path)
    st = p.stat()
    return (str(p), st.st_size, st.st_mtime_ns)


# ---------------------------------------------------------------- buckets
class Bucket:
    def __init__(self, name: str, salt: str, disk: bool, max_item: int | None):
        self.name, self.salt, self.disk, self._max_item = name, salt, disk, max_item
        self._items: OrderedDict[str, tuple] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = self.misses = self.disk_hits = self.disk_writes = 0

    def key(self, *parts) -> str:
        return key(self.name, self.salt, parts)

    def _limit(self) -> int:
        budget = _settings["memory"]
        return budget // 4 if self._max_item is None else min(self._max_item, budget)

    def get(self, k: str):
        """The value stored under `k`, or None."""
        if not _settings["enabled"]:
            return None
        with self._lock:
            hit = self._items.get(k)
            if hit is not None:
                self._items.move_to_end(k)
                self.hits += 1
                return hit[0]
        if self.disk and _settings["directory"] is not None:
            data = _disk_read(self._path(k))
            if data is not None:
                with self._lock:
                    self.disk_hits += 1
                self._remember(k, data, len(data))
                return data
        with self._lock:
            self.misses += 1
        return None

    def put(self, k: str, value, size: int | None = None) -> None:
        """Store `value` (bytes for a disk bucket) under `k`; `size`: its byte size (default len(value))."""
        if not _settings["enabled"]:
            return
        n = len(value) if size is None else int(size)
        self._remember(k, value, n)
        if self.disk and _settings["directory"] is not None and n <= max(self._limit(), 64 << 20):
            _disk_write(self._path(k), value)
            with self._lock:
                self.disk_writes += 1

    def _remember(self, k: str, value, n: int) -> None:
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

    def _path(self, k: str) -> Path:
        return _settings["directory"] / self.name / k[:2] / k

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {"items": len(self._items), "bytes": self._bytes, "hits": self.hits, "misses": self.misses,
                    "diskHits": self.disk_hits, "diskWrites": self.disk_writes}


def _disk_read(p: Path) -> bytes | None:
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if len(raw) < 12 or raw[:4] != DISK_MAGIC:
        return None
    n, = struct.unpack_from("<Q", raw, 4)
    return raw[12:] if len(raw) == 12 + n else None


def _disk_write(p: Path, data: bytes) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.part")
        tmp.write_bytes(DISK_MAGIC + struct.pack("<Q", len(data)) + bytes(data))
        os.replace(tmp, p)
    except OSError:
        pass                                    # a cache that cannot write is a cache that misses


# ---------------------------------------------------------------- packing several blobs into one value
def pack(meta: bytes, blobs: list[bytes]) -> bytes:
    """One bytes value from a header and blobs (for disk buckets whose value is several files)."""
    out = [struct.pack("<II", len(meta), len(blobs)), meta]
    for b in blobs:
        out += [struct.pack("<Q", len(b)), b]
    return b"".join(out)


def unpack(data: bytes) -> tuple[bytes, list[bytes]]:
    n, count = struct.unpack_from("<II", data, 0)
    pos = 8
    meta = data[pos:pos + n]
    pos += n
    blobs = []
    for _ in range(count):
        m, = struct.unpack_from("<Q", data, pos)
        pos += 8
        blobs.append(data[pos:pos + m])
        pos += m
    if pos != len(data):
        raise ValueError("packed cache value has trailing bytes")
    return meta, blobs
