"""The game's own Unity files in its APK as object sources.

The player reads files under `assets/bin/Data/` of the APK: `data.unity3d` (a bundle of the global managers,
globalgamemanagers.assets, the first scene level0, resources.assets, sharedassets0.assets and unity_builtin_extra),
`datapack.unity3d` in split builds, and the serialized file `Resources/unity default resources` (which bundles name
`Library/unity default resources`). Each of them the APK holds is a subject of unity.census and unity.export:
`apk/<its path under assets/bin/Data>` (`apk/data.unity3d`, `apk/Resources/unity default resources`). Their objects
have the ids of any serialized file's (`unity_builtin_extra:12`, `unity default resources:10`): a serialized file
outside a bundle takes the last part of its path as its name (serialized_name), as references into it name it.

inputs(depot, apk) gives them as Inputs (role "bundle"), each stored in the depot once; for an APK file the set is
recorded by the APK's content id (the APK is hashed once, depot.identify memo), so a later call reads nothing of it.
"""

from __future__ import annotations

from pathlib import Path

from ..source.apkset import ApkSet
from . import contract
from .contract import Input

__all__ = ["DATA", "DATA_DIR", "MEMBERS", "PREFIX", "inputs", "is_builtin", "serialized_name"]

DATA_DIR = "assets/bin/Data/"
MEMBERS = ("data.unity3d", "datapack.unity3d", "Resources/unity default resources")
PREFIX = "apk/"
DATA = PREFIX + "data.unity3d"  # the boot data (the CRI stages read the HCA keycode from it)


def is_builtin(subject: str) -> bool:
    return subject.startswith(PREFIX)


def serialized_name(subject: str) -> str | None:
    """The name a built-in subject's file is loaded under: the last part of its path (a serialized file outside a
    bundle is named so); None for a catalog bundle."""
    return subject.rsplit("/", 1)[-1] if is_builtin(subject) else None


def _read(depot, apk: Path) -> dict:
    """{member: {sha256, size}} of the built-in files an APK holds, their bytes stored in `depot`."""
    out = {}
    with ApkSet(apk) as z:
        names = set(z.namelist())
        for m in MEMBERS:
            if DATA_DIR + m in names:
                data = z.read(DATA_DIR + m)
                out[m] = {"sha256": depot.put(data), "size": len(data)}
    return out


def inputs(depot, apk) -> dict[str, Input]:
    """{subject: Input "bundle"} of the built-in files of an APK (a file, an APK set or a directory; see the module
    documentation), sorted; {} without an APK."""
    if apk is None:
        return {}
    apk = Path(apk)
    if apk.is_dir():
        found = _read(depot, apk)
    else:
        name = f"builtins:{depot.identify(apk)[0]}"
        known = depot.named("files", name)
        found = contract.loads(depot.read(known[0])) if known is not None and depot.has(*known) else None
        if found is None or not all(depot.has(r["sha256"], r["size"]) for r in found.values()):
            found = _read(depot, apk)
            depot.identify(depot.local(depot.put(contract.encode(found))), "files", name)
    return {
        PREFIX + m: Input("bundle", r["sha256"], int(r["size"]), m.rsplit("/", 1)[-1], ({"kind": "depot"},))
        for m, r in sorted(found.items())
    }
