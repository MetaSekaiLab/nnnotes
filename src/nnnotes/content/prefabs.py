"""Prefab documents of a product's object table read as the game's hierarchy.

A root GameObject's `json` is its prefab document (prefab.json): `{name, nodes}`, the nodes in hierarchy order (depth
first), each with its path, local transform, `active` (GameObject.m_IsActive) and components `{type, pathId, script,
fields}`. A component's fields name objects of the table by reference (`{"$ref": {file, pathId}}`) and objects of the
same hierarchy by path: `{"gameObject": path}`, `{"transform": path}`, or `{"component": type, "gameObject": path,
"class": script class}` for a component. `Prefab` gives each node's parent, its world matrix (Unity's left-handed
space, the root's own transform included) and whether it is active in the hierarchy, and resolves those references.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

from ..unity.ids import object_id

if TYPE_CHECKING:
    import numpy as np

__all__ = ["Objects", "Prefab", "component_class", "mat4", "ref_id", "root_game_object", "script_class"]


class Objects(Protocol):
    """A product's object table and its objects' files (pipeline.assemble.Objects)."""

    roots: list[str]

    def __contains__(self, oid: str) -> bool: ...

    def ids(self, cls: str | None = None) -> list[str]: ...

    def key(self, key: str) -> list[str]: ...

    def info(self, oid: str) -> dict: ...

    def bytes(self, oid: str, role: str) -> bytes: ...

    def json(self, oid: str, role: str = "json") -> Any: ...


def ref_id(v) -> str | None:
    """The object id of a reference value ({"$ref": {file, pathId}}); None for anything else."""
    r = v.get("$ref") if isinstance(v, dict) else None
    return object_id(r["file"], r["pathId"]) if isinstance(r, dict) and "file" in r else None


def component_class(c: dict) -> str:
    """A component's class: a MonoBehaviour's is its script's (the last part of its script entry)."""
    if c["type"] == "MonoBehaviour" and isinstance(c.get("script"), str):
        return c["script"].rsplit("|", 1)[-1]
    return c["type"]


def script_class(objects: Objects, oid: str) -> str | None:
    """The script class of a MonoBehaviour outside a hierarchy (its `json`'s `$script`), None without one."""
    if "json" not in (objects.info(oid).get("files") or {}):
        return None
    entry = objects.json(oid).get("$script")
    return entry.rsplit("|", 1)[-1] if isinstance(entry, str) else None


def root_game_object(objects: Objects, what: str) -> str:
    """The one GameObject among the table's roots (ValueError naming `what` otherwise)."""
    gos = [o for o in objects.roots if o in objects and objects.info(o).get("class") == "GameObject"]
    if len(gos) != 1:
        raise ValueError(f"{what}: {len(gos)} GameObjects among the roots, expected one")
    return gos[0]


def mat4(m: np.ndarray) -> list[float]:
    """A 4x4 matrix as 16 floats, column-major."""
    return [float(x) for x in m.T.reshape(-1)]


class Prefab:
    """The nodes of a prefab document with their parents, world matrices and activity (module documentation)."""

    def __init__(self, doc: dict):
        self.name: str = doc["name"]
        self.nodes: list[dict] = doc["nodes"]
        self.parent: list[int | None] = []
        stack: list[int] = []
        for n in self.nodes:
            while stack and not n["path"].startswith(self.nodes[stack[-1]]["path"] + "/"):
                stack.pop()
            self.parent.append(stack[-1] if stack else None)
            stack.append(len(self.parent) - 1)
        self._by_path: dict[str, list[int]] = {}
        for i, n in enumerate(self.nodes):
            self._by_path.setdefault(n["path"], []).append(i)
        self._world: dict[int, np.ndarray] = {}  # numpy is imported when a world matrix is asked for

    def at(self, path: str) -> int:
        """The node at `path` (ValueError when no node, or several, has it)."""
        hits = self._by_path.get(path, [])
        if len(hits) != 1:
            raise ValueError(f"{self.name}: {len(hits)} nodes at {path!r}")
        return hits[0]

    def world(self, i: int) -> np.ndarray:
        """The world matrix of node `i`: its local transform under its parents'."""
        from ..unity.transform import trs

        if i not in self._world:
            n = self.nodes[i]
            m = trs(n["localPosition"], n["localRotation"], n["localScale"])
            p = self.parent[i]
            self._world[i] = m if p is None else self.world(p) @ m
        return self._world[i]

    def active(self, i: int | None) -> bool:
        """Node `i` and every parent of it are active."""
        while i is not None:
            if not self.nodes[i].get("active", True):
                return False
            i = self.parent[i]
        return True

    def components(self, cls: str) -> Iterator[tuple[int, dict]]:
        """(node, component) of every component of class `cls`, in hierarchy order."""
        for i, n in enumerate(self.nodes):
            for c in n["components"]:
                if component_class(c) == cls:
                    yield i, c

    def node_of(self, v) -> int | None:
        """The node a reference to an object of the hierarchy names (its GameObject, Transform or a component of
        it); None for null."""
        if v is None:
            return None
        if not isinstance(v, dict) or not ({"gameObject", "transform"} & v.keys()):
            raise ValueError(f"{self.name}: not a reference to an object of the hierarchy: {v!r}")
        return self.at(v["gameObject"] if "gameObject" in v else v["transform"])

    def component_of(self, v) -> tuple[int, dict] | None:
        """(node, component) a reference to a component of the hierarchy names; None for null."""
        i = self.node_of(v)
        if i is None:
            return None
        if "component" not in v:
            raise ValueError(f"{self.name}: not a reference to a component: {v!r}")
        hits = [
            c
            for c in self.nodes[i]["components"]
            if c["type"] == v["component"] and ("class" not in v or component_class(c) == v["class"])
        ]
        if len(hits) != 1:
            raise ValueError(f"{self.name}: {len(hits)} components {v!r}")
        return i, hits[0]
