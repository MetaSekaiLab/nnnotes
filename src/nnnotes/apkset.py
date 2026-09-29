"""Read a base APK and its splits, an APK directory, or an APKS/XAPK archive.

The base manifest wins over split manifests. Resource members are resolved across the set;
no merged APK is produced. Nested APKs are spooled so large asset packs need not stay in RAM.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path


class ApkSet:
    def __init__(self, source):
        self.source = source
        self._stack = ExitStack()
        self._members = {}

    def __enter__(self):
        try:
            self._open()
            return self
        except BaseException:
            self._stack.close()
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
            outer = self._stack.enter_context(zipfile.ZipFile(path))
            names = [n for n in outer.namelist() if n.lower().endswith(".apk")]
            bases = [n for n in names if Path(n).name == "base.apk"]
            if len(bases) != 1:
                raise ValueError("APK archive must contain exactly one base.apk")
            for name in bases + sorted(n for n in names if n not in bases):
                tmp = self._stack.enter_context(tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024))
                with outer.open(name) as stream:
                    shutil.copyfileobj(stream, tmp)
                tmp.seek(0)
                self._add(tmp)
            return
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
        return self._stack.__exit__(*args)
