"""Prefab documents of a product's object table read as the game's hierarchy.

A root GameObject's `json` is its prefab document (prefab.json): `{name, nodes}`, the nodes in hierarchy order (depth
first), each with its path, local transform, `active` (GameObject.m_IsActive) and components `{type, pathId, script,
fields}`. A component's fields name objects of the table by reference (`{"$ref": {file, pathId}}`) and objects of the
same hierarchy by path: `{"gameObject": path}`, `{"transform": path}`, or `{"component": type, "gameObject": path,
"class": script class}` for a component. `Prefab` gives each node's parent, its world matrix (Unity's left-handed
space, the root's own transform included) and whether it is active in the hierarchy, and resolves those references.

A component of a hierarchy is in the table as an object contained in its root GameObject (its item `in`): a reference
to it from outside the hierarchy (another root of a scene, an asset) is a `$ref` that `component_ref` resolves.

What a player reads with the hierarchies of a table:

key_object        the object of a catalog key of a class (a prefab by its GameObject, a MonoBehaviour by its script
                  class)
controller_clips  the clips each Animator of a prefab plays through its controller (Animator.m_Controller, the
                  controller.json `clips`), by the Animator's node path
clip_bindings     the bindings of the clips each Animator plays, named in the prefab from the Animator's node
                  (unity.bindings.resolve)
shader_table      the files a player draws each shader of the table with: its parsed form (`json`), its variant
                  index (`index`) and every program of the player's graphics API (formats.shader.player_roles); and
                  what a lookup by name (Shader.Find) finds, by name (a material names its shader by id)
mesh_roles        the files a player draws each Mesh of a product with: its typetree JSON (`json`) and its `arrays`
                  document
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from ..formats.shader import player_roles
from ..unity import bindings
from ..unity.ids import object_id

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "Objects",
    "Prefab",
    "clip_bindings",
    "component_class",
    "component_ref",
    "controller_clips",
    "has_json",
    "key_object",
    "mat4",
    "mesh_roles",
    "prefab_ids",
    "ref_id",
    "root_game_object",
    "script_class",
    "shader_table",
]


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


def has_json(objects: Objects, oid: str) -> bool:
    """`oid` is an object of the table with a `json` file."""
    return oid in objects and "json" in (objects.info(oid).get("files") or {})


def script_class(objects: Objects, oid: str) -> str | None:
    """The script class of a MonoBehaviour outside a hierarchy (its `json`'s `$script`), None without one."""
    if not has_json(objects, oid):
        return None
    entry = objects.json(oid).get("$script")
    return entry.rsplit("|", 1)[-1] if isinstance(entry, str) else None


def root_game_object(objects: Objects, what: str) -> str:
    """The one GameObject among the table's roots (ValueError naming `what` otherwise)."""
    gos = [o for o in objects.roots if o in objects and objects.info(o).get("class") == "GameObject"]
    if len(gos) != 1:
        raise ValueError(f"{what}: {len(gos)} GameObjects among the roots, expected one")
    return gos[0]


def key_object(objects: Objects, key: str, cls: str) -> str:
    """The one object of the catalog key `key` of class `cls`: a GameObject with its prefab document, a MonoBehaviour
    by its script class, any other object by its class (ValueError otherwise)."""

    def of(oid: str) -> str | None:
        c = objects.info(oid).get("class")
        if c == "GameObject":
            return c if has_json(objects, oid) else None
        return script_class(objects, oid) if c == "MonoBehaviour" else c

    hits = [o for o in objects.key(key) if o in objects and of(o) == cls]
    if len(hits) != 1:
        raise ValueError(f"{key}: {len(hits)} {'prefab GameObjects' if cls == 'GameObject' else cls + ' objects'}")
    return hits[0]


def component_ref(objects: Objects, prefab: str, v) -> tuple[str, int, dict] | None:
    """(prefab GameObject id, node, component) that a component field of the hierarchy of GameObject `prefab` names:
    a component of the same hierarchy by path, or a `$ref` to a component of another hierarchy of the table; None for
    null (ValueError for a reference to anything else)."""
    if v is None:
        return None
    oid = ref_id(v)
    if oid is None:
        hit = Prefab(objects.json(prefab)).component_of(v)
        return None if hit is None else (prefab, *hit)
    holder = objects.info(oid).get("in") if oid in objects else None
    if holder is None or not has_json(objects, holder):
        raise ValueError(f"{oid}: not a component of a hierarchy of the table")
    pid = oid.rsplit(":", 1)[1]
    for i, n in enumerate(objects.json(holder)["nodes"]):
        for c in n["components"]:
            if c.get("pathId") == pid:
                return holder, i, c
    raise ValueError(f"{oid}: no component of {holder}")


def prefab_ids(objects: Objects) -> list[str]:
    """The GameObjects of the table with a prefab document (the roots of their hierarchies), sorted."""
    return [o for o in objects.ids("GameObject") if has_json(objects, o)]


def controller_clips(objects: Objects, prefab: dict) -> dict[str, list[str]]:
    """{node path: clip ids} of every Animator of a prefab document whose controller is in the table: the clips its
    controller plays, in the controller's order."""
    out: dict[str, list[str]] = {}
    for n in prefab["nodes"]:
        for c in n["components"]:
            ctl = ref_id(c["fields"].get("m_Controller")) if c["type"] == "Animator" else None
            if ctl is not None and has_json(objects, ctl):
                clips = [ref_id(r) for r in objects.json(ctl).get("clips", ())]
                out[n["path"]] = [o for o in clips if o is not None]
    return out


def clip_bindings(objects: Objects, prefab: dict, plays: Mapping[str, Iterable[str]]) -> dict[str, dict[str, list]]:
    """{node path: {clip id: [{path, attribute}]}} of the clips `plays` gives each Animator of a prefab document
    ({the Animator's node path: clip ids}), named in the prefab from the Animator's node (unity.bindings.resolve). A
    clip without its `json` in the table is left out, and so is an Animator left without clips. A path in `plays` that
    holds no Animator raises."""

    def material(ref) -> dict | None:
        oid = ref_id(ref)
        return objects.json(oid) if oid is not None and has_json(objects, oid) else None

    animators = {n["path"] for n in prefab["nodes"] for c in n["components"] if c["type"] == "Animator"}
    out = {}
    for path, clips in sorted(plays.items()):
        if path not in animators:
            raise ValueError(f"{prefab['name']}: no Animator at {path!r}")
        table = {}
        for cid in clips:
            if cid not in table and has_json(objects, cid):
                table[cid] = bindings.resolve(objects.json(cid)["bindings"], prefab, path, material)
        if table:
            out[path] = dict(sorted(table.items()))
    return out


def shader_table(objects: Objects, found: Iterable[str] = ()) -> tuple[dict[str, str], dict[str, set[str]]]:
    """({shader name: object id}, {object id: the roles of its files a player reads}) of the Shaders of the table that
    have their variant index (module documentation). The name table gives what a lookup by name finds. Bundles may
    each hold a copy of a shader: Shaders of one name whose parsed form and index are the same content are one shader,
    named by the first id. Of different Shaders of one name the lookup finds the one of `found` (the shaders it finds
    first: GraphicsSettings' always-included shaders); without one, the name names none of them."""
    groups: dict[str, list[str]] = {}
    roles: dict[str, set[str]] = {}
    first = set(found)

    def content(sid: str) -> tuple:
        files = objects.info(sid)["files"]
        return tuple(files[r]["sha256"] for r in ("json", "index"))

    for sid in objects.ids("Shader"):
        if "index" not in (objects.info(sid).get("files") or {}):
            continue
        index = objects.json(sid, "index")
        groups.setdefault(index["name"], []).append(sid)
        roles[sid] = {"json", "index"} | player_roles(index["variants"])
    names = {}
    for name, sids in sorted(groups.items()):
        pick = sids if len({content(s) for s in sids}) == 1 else [s for s in sids if s in first]
        if pick and len({content(s) for s in pick}) == 1:
            names[name] = pick[0]
    return names, roles


def mesh_roles(objects: Objects, keep: Iterable[str]) -> dict[str, set[str]]:
    """{Mesh id: {"json", "arrays"}} of the Meshes of `keep` that have their `arrays` document (module documentation).
    A Mesh without one (it converted as generic JSON) keeps all its files."""
    kept = set(keep)
    out = {}
    for mid in objects.ids("Mesh"):
        if mid in kept and "arrays" in (objects.info(mid).get("files") or {}):
            out[mid] = {"json", "arrays"}
    return out


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
