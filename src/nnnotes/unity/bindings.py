"""Clip bindings named in the hierarchy that plays the clip.

A binding of an AnimationClip (clip.json `bindings`, convert.animation.clip_doc) names what it animates by crc32
values: `pathCrc`, the path of the animated object relative to the GameObject whose Animator plays the clip, and
`attributeCrc`, the animated property. Only that hierarchy names them. `resolve` takes the bindings, the hierarchy
(a prefab.json document: its nodes with their paths and components) and the path of the Animator's GameObject in it,
and gives per binding the path and the attribute it names, or None where no name, or several, fit.

Path: the crc32 of the path, relative to the Animator's GameObject, of a transform below it ("" for itself). A path
no transform below it has stays unresolved: the Animator leaves such a binding unbound (an object parented there at
run time aside).

Attribute, from the bound component (the components of the binding's class on the GameObjects at its path; for a
MonoBehaviour those of its script):
- a Transform binding's attribute the clip names already (convert.animation.TRANSFORM_ATTR);
- a renderer material binding (customType 22) names a property of the renderer's materials (`material_attribute`);
- any other binding names a field path of the bound component (`flat_names` of its fields), else one of the engine
  properties (ENGINE_PROPS, RECT_PROPS), a material property (`material_prop_names`) or a field path of another
  component of its class in the hierarchy.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable

__all__ = [
    "ENGINE_PROPS",
    "MATERIAL_PROPS",
    "RECT_PROPS",
    "RENDERER_MATERIAL",
    "crc",
    "flat_names",
    "material_attribute",
    "material_prop_names",
    "resolve",
]

RENDERER_MATERIAL = 22  # GenericBinding.customType of a renderer's material property
# Animatable property names a binding can name that are no field path of a component's fields: common component
# properties and RectTransform driven fields.
ENGINE_PROPS = (
    "m_IsActive",
    "m_Enabled",
    "m_Color.r",
    "m_Color.g",
    "m_Color.b",
    "m_Color.a",
    "m_Size.x",
    "m_Size.y",
    "m_FlipX",
    "m_FlipY",
    "m_SortingOrder",
    "m_Sprite",
    "m_Alpha",
    "m_SizeDelta.x",
    "m_SizeDelta.y",
    "m_AnchoredPosition.x",
    "m_AnchoredPosition.y",
    "m_LocalPosition.x",
    "m_LocalPosition.y",
    "m_LocalPosition.z",
    "m_LocalScale.x",
    "m_LocalScale.y",
    "m_LocalScale.z",
)
RECT_PROPS = (
    "m_AnchoredPosition.x",
    "m_AnchoredPosition.y",
    "m_SizeDelta.x",
    "m_SizeDelta.y",
    "m_AnchorMin.x",
    "m_AnchorMin.y",
    "m_AnchorMax.x",
    "m_AnchorMax.y",
    "m_Pivot.x",
    "m_Pivot.y",
)
# material properties a binding can name as "material.<name>[.<channel>]" (renderer material property blocks)
MATERIAL_PROPS = (
    "_Color",
    "_TintColor",
    "_MainTex_ST",
    "_BaseColor",
    "_EmissionColor",
    "_Alpha",
    "_Cutoff",
    "_Intensity",
    "_Offset",
    "_Scroll",
    "_ScrollX",
    "_ScrollY",
    "_Power",
    "_Value",
    "_Glow",
    "_GlowColor",
    "_AddColor",
    "_MultiplyColor",
    "_Progress",
    "_Fade",
    "_Brightness",
    "_Dissolve",
    "_MainTex",
    "_Speed",
    "_Rotation",
)
_CHANNELS = ("r", "g", "b", "a", "x", "y", "z", "w")


def crc(s: str) -> int:
    """The crc32 a binding names a path or an attribute by."""
    return zlib.crc32(s.encode("utf-8"))


def flat_names(tt, prefix: str = "") -> list[str]:
    """Every field path of a typetree or of an object's fields ("m_Color", "m_Color.r", "m_Enabled", ...); lists are
    leaves."""
    out = []
    if isinstance(tt, dict):
        for k, v in tt.items():
            p = f"{prefix}.{k}" if prefix else k
            out.append(p)
            out.extend(flat_names(v, p))
    return out


def material_prop_names(extra: Iterable[str] = ()) -> list[str]:
    """The attributes of material properties (MATERIAL_PROPS and `extra`): "material.<name>" and every channel."""
    out = []
    for p in sorted(set(MATERIAL_PROPS) | set(extra)):
        out.append(f"material.{p}")
        out.extend(f"material.{p}.{c}" for c in _CHANNELS)
    return out


def material_attribute(attribute: int, kinds: dict[str, str]) -> str | None:
    """The attribute a renderer material binding (customType 22) names, "material.<property>[.<channel>]", among
    the properties of the renderer's materials `kinds` {name: "float" | "vector"} (a colour is a vector); None when
    no property, or several, fit. The attribute is (crc32(property) & 0x0FFFFFFF) | channel << 28 with bit 30 set
    for a colour channel (r, g, b, a) and clear for a vector channel (x, y, z, w) or a float property."""
    hits = [k for k in kinds if crc(k) & 0x0FFFFFFF == attribute & 0x0FFFFFFF]
    if len(hits) != 1:
        return None
    name, channel, colour = hits[0], (attribute >> 28) & 3, (attribute >> 30) & 1
    if kinds[name] == "float" and not colour and channel == 0:
        return f"material.{name}"
    return f"material.{name}.{('rgba' if colour else 'xyzw')[channel]}"


def _class_name(type_id: int) -> str | None:
    from UnityPy.enums import ClassIDType

    try:
        return ClassIDType(type_id).name
    except ValueError:
        return None


class _Hierarchy:
    """A prefab document's nodes by path and the crc32 tables of paths relative to a GameObject."""

    def __init__(self, prefab: dict):
        self.nodes: dict[str, list[dict]] = {}
        for n in prefab["nodes"]:
            self.nodes.setdefault(n["path"], []).append(n)
        self._tables: dict[str, dict[int, set[str]]] = {}
        self._names: dict[object, set[str]] = {}

    def relative(self, root: str) -> dict[int, set[str]]:
        t = self._tables.get(root)
        if t is None:
            if root not in self.nodes:
                raise KeyError(f"the Animator's GameObject {root!r} is not in the hierarchy")
            t = {}
            for path in self.nodes:
                if path == root or path.startswith(root + "/"):
                    rel = path[len(root) + 1 :]
                    t.setdefault(crc(rel), set()).add(rel)
            self._tables[root] = t
        return t

    def class_names(self, match: Callable[[dict], bool], key) -> set[str]:
        """The field paths of every component of the hierarchy `match` accepts (cached by `key`)."""
        names = self._names.get(key)
        if names is None:
            names = self._names[key] = {
                x
                for ns in self.nodes.values()
                for n in ns
                for c in n["components"]
                if match(c)
                for x in flat_names(c.get("fields"))
            }
        return names


def resolve(
    bindings: list[dict], prefab: dict, root: str, material: Callable[[object], dict | None] = lambda ref: None
) -> list[dict]:
    """[{path, attribute}] of clip bindings (module documentation) in the hierarchy of `prefab` (a prefab.json
    document) played by the Animator on the GameObject at `root` (a node path); `pathCandidates` (sorted) where
    several paths fit. `material(ref)`: the material.json document of a reference a renderer's m_Materials holds (None
    when unknown)."""
    h = _Hierarchy(prefab)
    table = h.relative(root)
    out = []
    for b in bindings:
        cands = table.get(b["pathCrc"], set())
        path = next(iter(cands)) if len(cands) == 1 else None
        r: dict = {"path": path, "attribute": b.get("attribute")}
        if len(cands) > 1:
            r["pathCandidates"] = sorted(cands)
        if r["attribute"] is None:
            r["attribute"] = _attribute(h, b, root, path, material)
        out.append(r)
    return out


def _attribute(h: _Hierarchy, b: dict, root: str, path: str | None, material) -> str | None:
    tid = b["typeID"]
    if tid == 114:
        script = b.get("scriptClass")

        def match(c: dict) -> bool:
            return c["type"] == "MonoBehaviour" and script is not None and c.get("script") == script

        key: object = ("script", script)
    else:
        cls = b.get("class") or _class_name(tid)

        def match(c: dict) -> bool:
            return c["type"] == cls

        key = cls
    nodes = h.nodes.get(root if path == "" else f"{root}/{path}", []) if path is not None else []
    bound = [c for n in nodes for c in n["components"] if match(c)]
    if b.get("customType") == RENDERER_MATERIAL:
        kinds: dict[str, str] = {}
        for c in bound:
            for ref in (c.get("fields") or {}).get("m_Materials") or ():
                m = material(ref) if ref is not None else None
                if m is not None:
                    kinds.update({k: "float" for k in m["floats"]})
                    kinds.update({k: "vector" for k in m["colors"]})
        return material_attribute(b["attributeCrc"], kinds)
    a = b["attributeCrc"]
    hit = {x for c in bound for x in flat_names(c.get("fields")) if crc(x) == a}
    if len(hit) != 1:
        names = set(ENGINE_PROPS) | set(RECT_PROPS) | set(material_prop_names()) | h.class_names(match, key)
        hit = {x for x in names if crc(x) == a}
    return next(iter(hit)) if len(hit) == 1 else None
