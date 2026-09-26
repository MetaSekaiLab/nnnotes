"""Candidate `png.encode` implementations on Pillow, for scripts/atom_eval.py only.

The reference (`nnnotes.atoms.png.encode`) writes at the level a task's `png.level` parameter gives (6 by
default). These fix the setting instead, so several can be measured side by side in one `atom_eval.py m1` run:

    level1, level3, level6, level9   Pillow at that zlib level
    optimize                         Pillow's `optimize=True` (zlib level 9 and a search over the filters)

For a stage run, `atom_eval.py m3 --param png.level=N` changes the level through the task parameter instead.
"""
from __future__ import annotations

import io
from functools import cache
from importlib import metadata

from nnnotes.atoms import Impl
from nnnotes.atoms import png as png_atom


def _pillow() -> str:
    try:
        return metadata.version("Pillow")
    except metadata.PackageNotFoundError:
        return "absent"


def _fixed(**options):
    def encode(image, level: int = png_atom.DEFAULT_LEVEL) -> bytes:
        buf = io.BytesIO()
        image.save(buf, format="PNG", **options)
        return buf.getvalue()
    return encode


def _impl(tag: str, **options) -> Impl:
    return Impl(f"pillow-{_pillow()}+{tag}/eval1", _fixed(**options), png_atom.cost)


@cache
def level1() -> Impl:
    return _impl("level1", compress_level=1)


@cache
def level3() -> Impl:
    return _impl("level3", compress_level=3)


@cache
def level6() -> Impl:
    return _impl("level6", compress_level=6)


@cache
def level9() -> Impl:
    return _impl("level9", compress_level=9)


@cache
def optimize() -> Impl:
    return _impl("optimize", optimize=True)
