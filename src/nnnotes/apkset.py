"""Read a base APK and its splits, an APK directory, or an APKS/XAPK archive.

The base manifest wins over split manifests. Resource members are resolved across the set;
no merged APK is produced. Nested APKs are expanded once per archive revision to disk, with
at most two idle APK sets retained per process; active readers keep their set alive until closed.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import zipfile
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path

from .cache import file_id


_EXPAND_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _expanded_apks(identity):
    """An owned temporary directory and its APK paths; readers retain it across cache eviction."""
    with zipfile.ZipFile(identity[0]) as outer:
        names = [n for n in outer.namelist() if n.lower().endswith(".apk")]
        bases = [n for n in names if Path(n).name == "base.apk"]
        if len(bases) != 1:
            raise ValueError("APK archive must contain exactly one base.apk")
        directory = tempfile.TemporaryDirectory(prefix="nnnotes-apks-")
        try:
            paths = []
            for i, name in enumerate(bases + sorted(n for n in names if n not in bases)):
                path = Path(directory.name) / f"{i}.apk"
                with outer.open(name) as stream, path.open("wb") as target:
                    shutil.copyfileobj(stream, target)
                paths.append(path)
            return directory, paths
        except BaseException:
            directory.cleanup()
            raise


class ApkSet:
    def __init__(self, source):
        self.source = source
        self._stack = ExitStack()
        self._members = {}
        self._expanded = None

    def __enter__(self):
        try:
            self._open()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _add(self, source):
        archive = self._stack.enter_context(zipfile.ZipFile(source))
        for info in archive.infolist():
            self._members.setdefault(info.filename, (archive, info))

    def _open(self):
        if hasattr(self.source, "read"):
            self._add(self.source)
            return
        path = Path(self.source)
        if path.is_dir():
            base = path / "base.apk"
            if not base.is_file():
                raise FileNotFoundError("APK directory has no base.apk")
            paths = [base] + sorted(p for p in path.glob("*.apk") if p != base)
        elif path.suffix.lower() in (".apks", ".xapk"):
            with _EXPAND_LOCK:              # lru_cache alone permits duplicate concurrent expansions
                self._expanded = _expanded_apks(file_id(path.resolve()))
            paths = self._expanded[1]
        else:
            paths = [path]
            if path.name == "base.apk":
                paths += sorted(p for p in path.parent.glob("*.apk")
                                if p.name.startswith(("split_", "config.")))
        for apk in paths:
            self._add(apk)

    def namelist(self):
        return list(self._members)

    def infolist(self):
        return [info for _, info in self._members.values()]

    def read(self, name):
        name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        archive, info = self._members[name]
        return archive.read(info)

    def __exit__(self, *args):
        try:
            return self._stack.__exit__(*args)
        finally:
            self._members.clear()
            self._expanded = None
