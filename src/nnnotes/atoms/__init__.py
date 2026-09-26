"""Atoms: the pure functions the export stages are built from.

An atom takes values and returns values: no file or network IO, no threads, no state kept between calls, and the
same inputs give the same output. Each atom has exactly one reference implementation, registered in `ATOMS` under
the atom's name as an `Impl(id, fn, cost)`:

- `id` names the implementation and the versions of the libraries its output depends on
  (`unitypy-1.25.0+pillow-12.1.1/1`: the libraries, then the revision of the nnnotes code). A task's key includes
  the ids of the atoms it uses, so a library upgrade or a revision bump re-runs exactly the tasks that use the atom.
- `fn` is the function.
- `cost(facts)` estimates the work of one call from facts known before the call (sizes, counts, formats):
  `{"cpuSeconds": float, "peakBytes": int}`. The estimates schedule work; they never change an output.

`NNNOTES_ATOM_<NAME>` (the name upper-cased, `.` as `_`, e.g. `NNNOTES_ATOM_PNG_ENCODE`) replaces an atom's
implementation for evaluation: the value is `module:attribute`, an importable `Impl` (or a callable returning
one). The replacement's id differs from the reference's, so keys and provenance record it.

An input an atom documents as out of its scope raises `Unsupported(code, message)`; `code` is one of the reason
codes listed in docs/stages.md.

Heavy libraries (UnityPy, Pillow's codecs) are imported by the atom functions when first called, so importing this
package or listing the registry does not load them.
"""
from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from typing import Callable

__all__ = ["ATOMS", "Impl", "Unsupported", "atom_id", "ids", "impl_id", "resolve"]


class Unsupported(Exception):
    """An input outside an atom's documented scope. `code` is a reason code (docs/stages.md), `message` names the
    object or the value that is out of scope."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Impl:
    id: str
    fn: Callable
    cost: Callable[[dict], dict]


@lru_cache(maxsize=None)
def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "absent"


def impl_id(libraries: tuple[str, ...], revision: int) -> str:
    """`lib-version+lib-version/revision` for the installed versions of `libraries` (distribution names), or
    `nnnotes/revision` for an implementation that depends on no library's behaviour."""
    if not libraries:
        return f"nnnotes/{revision}"
    return "+".join(f"{d.lower()}-{_version(d)}" for d in libraries) + f"/{revision}"


# name -> (module, function, cost function, libraries whose behaviour the output depends on, revision)
_REFERENCE: dict[str, tuple[str, str, str, tuple[str, ...], int]] = {
    "reader": ("reader", "open_bundle", "cost", ("UnityPy",), 1),
    "texture.decode": ("texture", "decode", "cost", ("UnityPy", "texture2ddecoder", "astc-encoder-py", "Pillow"), 1),
    "png.encode": ("png", "encode", "cost", ("Pillow",), 1),
    "astc.container": ("astc", "container", "cost", (), 1),
    "sprite.crop": ("sprite", "crop", "cost", ("Pillow", "numpy"), 1),
    "mesh.arrays": ("mesh", "arrays", "cost", ("numpy",), 1),
    "gltf.write": ("gltf", "write", "cost", ("numpy",), 1),
    "sniff": ("sniff", "sniff", "cost", (), 1),
}


def env_name(name: str) -> str:
    """The environment variable that replaces atom `name` for evaluation."""
    return "NNNOTES_ATOM_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


@lru_cache(maxsize=None)
def _reference(name: str) -> Impl:
    module, fn, cost, libraries, revision = _REFERENCE[name]
    mod = importlib.import_module(f"{__name__}.{module}")
    return Impl(impl_id(libraries, revision), getattr(mod, fn), getattr(mod, cost))


def _replacement(name: str, spec: str) -> Impl:
    module, sep, attr = spec.partition(":")
    if not sep or not module or not attr:
        raise ValueError(f"{env_name(name)}={spec!r}: expected module:attribute")
    obj = getattr(importlib.import_module(module), attr)
    impl = obj if isinstance(obj, Impl) else obj()
    if not isinstance(impl, Impl):
        raise ValueError(f"{env_name(name)}={spec!r}: not an Impl")
    if impl.id == _reference(name).id:
        raise ValueError(f"{env_name(name)}={spec!r}: the replacement has the reference's id {impl.id!r}")
    return impl


def resolve(name: str) -> Impl:
    """The implementation of atom `name`: the reference, or the replacement named by NNNOTES_ATOM_<NAME>."""
    if name not in _REFERENCE:
        raise KeyError(f"unknown atom {name!r}")
    spec = os.environ.get(env_name(name))
    return _replacement(name, spec) if spec else _reference(name)


class _Registry(Mapping):
    """ATOMS[name] -> Impl (see `resolve`)."""

    def __getitem__(self, name: str) -> Impl:
        return resolve(name)

    def __iter__(self):
        return iter(_REFERENCE)

    def __len__(self) -> int:
        return len(_REFERENCE)


ATOMS: Mapping[str, Impl] = _Registry()


def atom_id(name: str) -> str:
    """The implementation id of atom `name` (resolve(name).id); the reference's id is computed without importing
    the atom's module."""
    if name not in _REFERENCE:
        raise KeyError(f"unknown atom {name!r}")
    if os.environ.get(env_name(name)):
        return resolve(name).id
    _module, _fn, _cost, libraries, revision = _REFERENCE[name]
    return impl_id(libraries, revision)


def ids(*names: str) -> dict[str, str]:
    """{atom name: implementation id} of `names`: the `atoms` part of a task (contract.Task.atoms)."""
    return {n: atom_id(n) for n in sorted(names)}


def estimate(cpu_seconds: float, peak_bytes: float) -> dict:
    """A cost estimate as the atoms' cost functions return it."""
    return {"cpuSeconds": round(float(cpu_seconds), 6), "peakBytes": int(peak_bytes)}
