"""Read a base APK and its splits, an APK directory, or an APKS/XAPK archive.

The base manifest wins over split manifests. Resource members are resolved across the set;
no merged APK is produced. Nested APKs are expanded once per archive revision to disk, with
at most two idle APK sets retained per process; active readers keep their set alive until closed.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import threading
import zipfile
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path

from .cache import file_id

_EXPAND_LOCK = threading.Lock()
MANIFEST_IN_APK = "AndroidManifest.xml"


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
            with _EXPAND_LOCK:  # lru_cache alone permits duplicate concurrent expansions
                self._expanded = _expanded_apks(file_id(path.resolve()))
            paths = self._expanded[1]
        else:
            paths = [path]
            if path.name == "base.apk":
                paths += sorted(p for p in path.parent.glob("*.apk") if p.name.startswith(("split_", "config.")))
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


# ---------------------------------------------------------------- AndroidManifest.xml
def _manifest_attribute(data: bytes, attribute: str) -> tuple[str | None, int | None, int | None] | None:
    """An attribute of the <manifest> element of a binary AndroidManifest.xml: (its string, None when it has no
    string value; the Res_value data type; the Res_value data). None when absent or unreadable."""
    try:
        pos, strings = 8, []
        while pos + 8 <= len(data):
            typ, hsize, size = struct.unpack_from("<HHI", data, pos)
            if typ == 0x0001:  # string pool
                count, _styles, flags, start = struct.unpack_from("<IIII", data, pos + 8)
                for off in struct.unpack_from(f"<{count}I", data, pos + hsize):
                    q = pos + start + off
                    if flags & 0x100:  # UTF-8: UTF-16 length, UTF-8 length, bytes
                        q += 2 if data[q] & 0x80 else 1
                        n = data[q]
                        q += 1
                        if n & 0x80:
                            n = ((n & 0x7F) << 8) | data[q]
                            q += 1
                        strings.append(data[q : q + n].decode("utf-8"))
                    else:  # UTF-16LE: length in code units, chars
                        (n,) = struct.unpack_from("<H", data, q)
                        q += 2
                        if n & 0x8000:
                            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, q)[0]
                            q += 2
                        strings.append(data[q : q + 2 * n].decode("utf-16-le"))
            elif typ == 0x0102:  # start element
                _ns, name, astart, asize, acount = struct.unpack_from("<IIHHH", data, pos + 16)
                if strings[name] == "manifest":
                    for i in range(acount):
                        a = pos + 16 + astart + i * asize
                        _ans, aname, raw = struct.unpack_from("<III", data, a)
                        if strings[aname] == attribute:
                            text = strings[raw] if raw != 0xFFFFFFFF else None
                            if a + 20 > len(data):  # no typed value
                                return text, None, None
                            _vsize, _res0, vtype, value = struct.unpack_from("<HBBI", data, a + 12)
                            return text, vtype, value
                    return None
            if size < 8:
                return None
            pos += size
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    return None


def manifest_version_name(data: bytes) -> str | None:
    """`android:versionName` of a binary AndroidManifest.xml (None when absent or unreadable)."""
    a = _manifest_attribute(data, "versionName")
    return a[0] if a is not None else None


def manifest_version_code(data: bytes) -> int | None:
    """`android:versionCode` of a binary AndroidManifest.xml: an integer (decimal or hex type), or a string of
    digits (None when absent or unreadable)."""
    a = _manifest_attribute(data, "versionCode")
    if a is None:
        return None
    text, vtype, value = a
    if vtype in (0x10, 0x11):  # TYPE_INT_DEC, TYPE_INT_HEX
        return value
    return int(text) if text is not None and text.isdecimal() else None
