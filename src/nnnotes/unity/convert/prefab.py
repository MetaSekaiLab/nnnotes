"""GameObject hierarchies (prefab.json): a root GameObject's prefab holds the hierarchy below it with its components.

`hierarchies` finds, per serialized file, the roots and the objects each root's hierarchy holds (its GameObjects,
Transforms and components); inside a prefab those objects are named by their paths.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace

from .objects import NO_SCRIPT, TYPETREE, BundleObjects, ReadError, encode_object, message

__all__ = ["Hierarchies", "Root", "hierarchies", "prefab"]


@dataclass
class Root:
    """A hierarchy's root: its file, Transform and GameObject path ids, the keys of the objects it holds in
    hierarchy order, and why it could not be walked (None when it could)."""

    file: str
    transform: int
    game_object: int
    members: list
    error: str | None


@dataclass
class Hierarchies:
    graphs: dict = field(default_factory=dict)  # serialized file -> SceneGraph
    roots: list[Root] = field(default_factory=list)
    member: dict = field(default_factory=dict)  # object key -> the key of its root's GameObject
    comp_go: dict = field(default_factory=dict)  # component key -> its GameObject's path id
    scene_of: dict = field(default_factory=dict)  # serialized file -> the scene it holds

    def local(self, ctx: BundleObjects, key: tuple, root: tuple) -> dict | None:
        """The local name of an object of the hierarchy at `root` (None for any other object)."""
        if self.member.get(key) != root:
            return None
        file, pid = key
        o, g = ctx.readers[key], self.graphs[file]
        t = o.type.name
        if t == "GameObject":
            return {"gameObject": g.path(g.tf_of_go[pid])}
        if t in ("Transform", "RectTransform"):
            return {"transform": g.path(pid)}
        d = {"component": t, "gameObject": g.path(g.tf_of_go[self.comp_go[key]])}
        if t == "MonoBehaviour":
            d["class"] = ctx.script_class(key)
        return d


def hierarchies(ctx: BundleObjects) -> Hierarchies:
    """The hierarchies of every serialized file and the scenes of the files a scene bundle holds."""
    from ..reader import SceneGraph

    h = Hierarchies()
    by_file = defaultdict(list)
    for i in ctx.infos:
        by_file[i.file].append(i)
    for f in ctx.files:
        objs = [
            ctx.readers[(i.file, i.path_id)]
            for i in by_file[f]
            if i.class_name in ("GameObject", "Transform", "RectTransform")
        ]
        try:
            g = SceneGraph(SimpleNamespace(objects=objs))
        except Exception:
            continue  # the file's objects are exported one by one
        h.graphs[f] = g
        for tf_pid, t in g.tf.items():
            father = int(t["m_Father"]["m_PathID"] or 0)
            if father and father in g.tf:
                continue
            go_pid = int(t["m_GameObject"]["m_PathID"] or 0)
            members, comps, error = _subtree(ctx, h, f, g, tf_pid)
            h.roots.append(Root(f, tf_pid, go_pid, members, error))
            if error is None:
                for k in members:
                    h.member[k] = (f, go_pid)
                h.comp_go.update(comps)
    for i in ctx.of("AssetBundle"):
        try:
            tt = ctx.tt((i.file, i.path_id))
        except ReadError:
            continue
        for pair in tt.get("m_SceneHashes") or ():
            scene, cab = pair[0], str(pair[1]).lower()
            for f in ctx.files:
                if cab and f.lower().startswith(cab):
                    h.scene_of.setdefault(f, scene)
    return h


def _subtree(ctx: BundleObjects, h: Hierarchies, f: str, g, root_tf: int):
    """(member keys in hierarchy order, {component key: GameObject pathId}, error) of the hierarchy at root_tf."""
    members, comps, seen, stack = [], {}, set(), [root_tf]
    try:
        while stack:
            tf = stack.pop()
            if tf in seen:
                raise ValueError(f"transform {tf} appears twice in the hierarchy")
            if tf not in g.tf:
                raise ValueError(f"child transform {tf} is not in the file")
            seen.add(tf)
            t = g.tf[tf]
            go_pid = int(t["m_GameObject"]["m_PathID"] or 0)
            if go_pid not in g.go:
                raise ValueError(f"transform {tf}: GameObject {go_pid} is not in the file")
            keys = [(f, go_pid), (f, tf)]
            for c in g.go[go_pid]["m_Component"]:
                p = c["component"]
                pid = int(p["m_PathID"] or 0)
                if not pid or pid == tf:
                    continue
                if int(p.get("m_FileID", 0) or 0) or (f, pid) not in ctx.readers:
                    raise ValueError(f"GameObject {go_pid}: component {pid} is not in the file")
                keys.append((f, pid))
                comps[(f, pid)] = go_pid
            for k in keys:
                if k in h.member:
                    raise ValueError(f"object {k[1]} belongs to two hierarchies")
            members.extend(keys)
            stack.extend(reversed([int(ch["m_PathID"]) for ch in t["m_Children"]]))
    except Exception as e:
        return members, comps, message(e)
    return members, comps, None


def _component(ctx: BundleObjects, h: Hierarchies, o, root: tuple, unresolved: list) -> dict:
    key = ctx.key(o)
    tt = ctx.tt(key, whole=False)
    out = {"type": o.type.name, "pathId": key[1]}
    if o.type.name == "MonoBehaviour":
        entry = ctx.mono_script(key, tt)
        out["script"] = entry if entry and entry != NO_SCRIPT else None
        if entry is None:
            unresolved.append(key)
    body = ctx.tree(o, tt, local=lambda k: h.local(ctx, k, root))
    out["fields"] = {k: v for k, v in body.items() if k != "m_GameObject"}
    return out


def prefab(ctx: BundleObjects, info, h: Hierarchies, root: Root) -> None:
    """`json`: {"name", "scene" (a root of a scene's file), "nodes"}: every node of the hierarchy in hierarchy
    order (path, name, path ids, active, layer, tag, local transform, rect, components with their fields)."""
    key = (info.file, info.path_id)
    g = h.graphs[info.file]
    unresolved: list = []
    nodes, stack = [], [root.transform]
    while stack:
        tf = stack.pop()
        t = g.tf[tf]
        go_pid = int(t["m_GameObject"]["m_PathID"])
        go = g.go[go_pid]
        comps = []
        for c in go["m_Component"]:
            pid = int(c["component"]["m_PathID"] or 0)
            if pid and pid != tf:
                comps.append(_component(ctx, h, ctx.readers[(info.file, pid)], key, unresolved))
        node = {
            "path": g.path(tf),
            "name": go["m_Name"],
            "pathId": go_pid,
            "transformPathId": tf,
            "active": bool(go["m_IsActive"]),
            "layer": go["m_Layer"],
            "tag": go.get("m_Tag"),
            "localPosition": t["m_LocalPosition"],
            "localRotation": t["m_LocalRotation"],
            "localScale": t["m_LocalScale"],
        }
        if "m_AnchorMin" in t:
            node["rect"] = {
                k: t[k] for k in ("m_AnchorMin", "m_AnchorMax", "m_AnchoredPosition", "m_SizeDelta", "m_Pivot")
            }
        node["components"] = comps
        nodes.append(node)
        stack.extend(reversed([int(ch["m_PathID"]) for ch in t["m_Children"]]))
    doc = {"name": nodes[0]["name"]}
    if info.file in h.scene_of:
        doc["scene"] = h.scene_of[info.file]
    doc["nodes"] = nodes
    issues = []
    if unresolved:
        issues.append(
            ("script.unresolved", f"{len(unresolved)} MonoBehaviours whose script is not in the task's script table")
        )
    partial = sum(1 for k in root.members if k in ctx.partial)
    if partial:
        issues.append((TYPETREE, f"{partial} components read in part (their file stores no type tree that fits)"))
    facts = {"nodes": len(nodes), "objects": len(root.members)}
    ctx.emit(key, "json", encode_object(doc), "json", "prefab", "json", facts, issues=issues)
