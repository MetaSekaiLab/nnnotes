"""Live2D models assembled from the objects their prefab reaches: the player's model document and the Cubism SDK
interchange files.

Both read a product's object table (`Objects`, pipeline.assemble), never a bundle. The model is the GameObject among
the table's roots (the object its catalog key names, Character/Live2D/<group>/<name>/model/<name>); its components
are read from its prefab document (prefab.json), a MonoBehaviour's class being the last part of its script entry.

`model` -> model.json (MODEL_FORMAT):

    name, key    the model's id (model_id: the key's last part) and its key
    prefab       the object id of the model's GameObject
    moc          the object id of its CubismMoc (the root's CubismModel._moc); the moc3 is its file `blob:_bytes`
    canvas       the moc3's canvas (formats.cubism.moc3_canvas)
    textures     the object ids of the atlas pages the drawables draw with (CubismRenderer._mainTexture), by name
    shaders      {shader name: object id} of the shaders of the table (the drawables' materials', the mask
                 materials', ...)
    resources    {name: object id} of the materials the Cubism mask pass draws with (RESOURCES: Resources.Load
                 paths, each the Material among the roots named as the path's last part)
    motionSync   the root has the MotionSync controller and its CRI audio input (MOTION_SYNC_COMPONENTS); a model
                 without them takes its mouth in a story from the voice's lip sync analysis
    bindings     {clip object id: [{path, attribute}]}: the bindings of every AnimationClip of the table named in the
                 model's hierarchy, played from its root (unity.bindings.resolve)

Of the objects, the player reads all but the MonoScripts; of a shader, its parsed form (`json`), its variant index
(`index`) and the programs it draws the materials with (formats.shader.player_program): those of each drawable
material's keywords, alone and with STORY_KEYWORDS, and, when a drawable is masked (MASK_KEYWORD), the mask
materials'. A shader no drawn material uses keeps its parsed form and index.

`cubism` -> the Cubism SDK files other tools read: <name>.moc3, textures/<page>.png (the atlas pages, by name),
<name>.physics3.json (CubismPhysicsController._rig; when the rig has sub-rigs), <expression>.exp3.json (each
CubismExpressionData), motions/<motion>.motion3.json (each CubismFadeMotionData, with the sample rate of the clip of
its name) and <name>.model3.json (the files, the model's fade times of each motion, and the EyeBlink and LipSync
groups: the names of the GameObjects holding a CubismEyeBlinkParameter or a CubismMouthParameter).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Protocol

from ..core.jsonio import dumps
from ..formats import motion
from ..formats.cubism import expression_json, moc3_canvas, physics_json
from ..formats.shader import player_program
from ..unity import bindings
from ..unity.ids import object_id

__all__ = [
    "MASK_KEYWORD",
    "LIVE2D_PREFIX",
    "MODEL_FORMAT",
    "MODEL_ID",
    "MODEL_KEY",
    "MOTION_SYNC_COMPONENTS",
    "RESOURCES",
    "STORY_KEYWORDS",
    "Objects",
    "cubism",
    "model",
    "model_id",
    "model_keys",
    "model_match",
    "select_models",
]

MODEL_FORMAT = 3  # model.json "format"
LIVE2D_PREFIX = "Character/Live2D/"
MODEL_KEY = re.compile(r"Character/Live2D/(?P<group>[^/]+)/(?P<name>[^/]+)/model/(?P=name)")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# Resources.Load paths of the materials the Cubism mask pass draws with
RESOURCES = {
    "cubismMask": "Live2D/Cubism/Materials/Mask",
    "cubismMaskCulling": "Live2D/Cubism/Materials/MaskCulling",
}
MASK_KEYWORD = "CUBISM_MASK_ON"  # a drawable material's keyword: the drawable is masked
# keywords the story renderer adds to a character draw (_ADDITIONAL_LIGHTS_VERTEX: at quality 4); the mask pass draws
# with none
STORY_KEYWORDS = ("_ADDITIONAL_LIGHTS_VERTEX",)
# a model's MotionSync: both components on the prefab's root
MOTION_SYNC_COMPONENTS = ("CubismMotionSyncController", "Live2DMotionSyncCriAudioInput")
SHADER_ROLES = ("json", "index")  # a shader's files the player reads besides its programs
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class Objects(Protocol):
    """A product's object table and its objects' files (pipeline.assemble.Objects)."""

    roots: list[str]

    def __contains__(self, oid: str) -> bool: ...

    def ids(self, cls: str | None = None) -> list[str]: ...

    def info(self, oid: str) -> dict: ...

    def bytes(self, oid: str, role: str) -> bytes: ...

    def json(self, oid: str, role: str = "json") -> Any: ...


def model_match(key: str) -> re.Match[str]:
    """The parts of a model key: group, name (ValueError for a key that is not a model's)."""
    m = MODEL_KEY.fullmatch(key)
    if m is None:
        raise ValueError(f"{key}: not a Live2D model key (Character/Live2D/<group>/<name>/model/<name>)")
    return m


def model_id(key: str) -> str:
    """The id of a model key: its <name> (ValueError for a key that is not a model's, or a name that is not
    URL-safe)."""
    m = model_match(key)
    if not MODEL_ID.fullmatch(m["name"]):
        raise ValueError(f"{key}: model name {m['name']!r} is not URL-safe")
    return m["name"]


def model_keys(keys) -> dict[str, str]:
    """{id: key} of the model keys among `keys`, in id order; two keys with the same id raise."""
    out: dict[str, str] = {}
    for k in keys:
        if MODEL_KEY.fullmatch(k):
            i = model_id(k)
            if out.get(i, k) != k:
                raise ValueError(f"model id {i}: keys {out[i]} and {k}")
            out[i] = k
    return dict(sorted(out.items()))


def select_models(specs, models: dict[str, str]) -> dict[str, str]:
    """The models `specs` names (ids or keys; None: all) as {id: key} in id order, from `models` ({id: key})."""
    if specs is None:
        return dict(models)
    by_key = {k: i for i, k in models.items()}
    out, unknown = {}, []
    for s in specs:
        i = s if s in models else by_key.get(s)
        if i is None:
            unknown.append(s)
        else:
            out[i] = models[i]
    if unknown:
        raise ValueError(
            f"not a Live2D model of the catalog: {', '.join(unknown)} "
            f"(give a model id or a key Character/Live2D/<group>/<name>/model/<name>)"
        )
    return dict(sorted(out.items()))


def ref_id(v) -> str | None:
    """The object id of a reference value ({"$ref": {file, pathId}}); None for anything else."""
    r = v.get("$ref") if isinstance(v, dict) else None
    return object_id(r["file"], r["pathId"]) if isinstance(r, dict) and "file" in r else None


def _class(c: dict) -> str:
    """A component's class: a MonoBehaviour's is its script's."""
    if c["type"] == "MonoBehaviour" and isinstance(c.get("script"), str):
        return c["script"].rsplit("|", 1)[-1]
    return c["type"]


class _Model:
    """A model's GameObject, prefab document and components."""

    def __init__(self, objects: Objects, key: str):
        self.objects, self.key, self.name = objects, key, model_id(key)
        gos = [o for o in objects.roots if o in objects and objects.info(o).get("class") == "GameObject"]
        if len(gos) != 1:
            raise ValueError(f"{key}: {len(gos)} GameObjects among the roots, expected the model's")
        self.id = gos[0]
        self.prefab = objects.json(self.id)
        self.root = self.prefab["nodes"][0]

    def components(self, cls: str, root_only: bool = False) -> Iterator[tuple[dict, dict]]:
        """(node, component) of every component of class `cls` (of the root node only)."""
        for n in [self.root] if root_only else self.prefab["nodes"]:
            for c in n["components"]:
                if _class(c) == cls:
                    yield n, c

    def one(self, cls: str) -> dict:
        found = [c for _, c in self.components(cls, root_only=True)]
        if len(found) != 1:
            raise ValueError(f"{self.key}: {len(found)} {cls} on the model's root")
        return found[0]

    def moc(self) -> str:
        oid = ref_id(self.one("CubismModel")["fields"].get("_moc"))
        if oid is None or oid not in self.objects:
            raise ValueError(f"{self.key}: the CubismModel's moc is not in the object table")
        return oid

    def moc_bytes(self) -> bytes:
        return self.objects.bytes(self.moc(), "blob:_bytes")

    def textures(self) -> list[str]:
        """The atlas pages the drawables draw with, by name and object id."""
        pages = {ref_id(c["fields"].get("_mainTexture")) for _, c in self.components("CubismRenderer")}
        pages.discard(None)
        return sorted((o for o in pages if o is not None), key=lambda o: (self.objects.info(o).get("name") or "", o))

    def drawable_materials(self) -> list[str]:
        """The first material of every drawable's MeshRenderer."""
        out = []
        for _, c in self.components("MeshRenderer"):
            mats = c["fields"].get("m_Materials") or []
            oid = ref_id(mats[0]) if mats else None
            if oid is not None:
                out.append(oid)
        return out

    def scripted(self, cls: str) -> list[str]:
        """The MonoBehaviours of the table whose script class is `cls` (outside the prefab: data assets)."""
        return [o for o in self.objects.ids("MonoBehaviour") if _script_class(self.objects, o) == cls]


def _script_class(objects: Objects, oid: str) -> str | None:
    if "json" not in (objects.info(oid).get("files") or {}):
        return None
    entry = objects.json(oid).get("$script")
    return entry.rsplit("|", 1)[-1] if isinstance(entry, str) else None


def _resources(m: _Model) -> dict[str, str]:
    out = {}
    for name, path in RESOURCES.items():
        leaf = path.rsplit("/", 1)[-1]
        hits = [
            o
            for o in m.objects.roots
            if o in m.objects and m.objects.info(o).get("class") == "Material" and m.objects.info(o).get("name") == leaf
        ]
        if len(hits) != 1:
            raise ValueError(f"{m.key}: {len(hits)} Materials {leaf!r} among the roots (Resources.Load {path})")
        out[name] = hits[0]
    return out


def model(objects: Objects, key: str) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(model.json, the object ids the player reads, {shader id: the roles of its files it reads}) of the model
    `key` (module documentation)."""
    m = _Model(objects, key)
    moc = m.moc()
    resources = _resources(m)
    sets: dict[str, set[tuple]] = {}  # shader id -> keyword sets
    drawn = [objects.json(o) for o in m.drawable_materials()]
    for mat in drawn:
        sid = ref_id(mat["shader"])
        if sid is None:
            raise ValueError(f"{key}: material {mat['material']!r} has no shader")
        kws = tuple(mat["keywords"])
        sets.setdefault(sid, set()).update({kws, (*kws, *STORY_KEYWORDS)})
    masked = any(MASK_KEYWORD in mat["keywords"] for mat in drawn)
    for oid in resources.values():
        mat = objects.json(oid)
        sid = ref_id(mat["shader"])
        if sid is None:
            raise ValueError(f"{key}: material {mat['material']!r} has no shader")
        sets.setdefault(sid, set())
        if masked:
            sets[sid].add(tuple(mat["keywords"]))
    for sid in objects.ids("Shader"):
        sets.setdefault(sid, set())  # a shader no drawn material uses: its parsed form and index only
    shaders, roles = {}, {}
    for sid, kw_sets in sorted(sets.items()):
        index = objects.json(sid, "index")
        shaders[index["name"]] = sid
        keep = set(SHADER_ROLES)
        for kws in sorted(kw_sets):
            try:
                keep.add(player_program(index["variants"], kws)["role"])
            except ValueError as e:
                raise ValueError(f"{key}: shader {index['name']}: {e}") from None
        roles[sid] = keep
    classes = {_class(c) for c in m.root["components"]}

    def material(ref) -> dict | None:
        oid = ref_id(ref)
        return objects.json(oid) if oid is not None and oid in objects else None

    clips = {}
    for cid in objects.ids("AnimationClip"):
        if "json" in (objects.info(cid).get("files") or {}):
            clips[cid] = bindings.resolve(objects.json(cid)["bindings"], m.prefab, m.root["path"], material)
    doc = {
        "format": MODEL_FORMAT,
        "name": m.name,
        "key": key,
        "prefab": m.id,
        "moc": moc,
        "canvas": moc3_canvas(m.moc_bytes()),
        "textures": m.textures(),
        "shaders": dict(sorted(shaders.items())),
        "resources": resources,
        "motionSync": all(k in classes for k in MOTION_SYNC_COMPONENTS),
        "bindings": clips,
    }
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    return doc, keep, roles


def _json(doc, indent: int | None = None) -> bytes:
    return dumps(doc, indent=indent, ensure_ascii=False).encode("utf-8")


def _page_files(m: _Model) -> dict[str, str]:
    """{object id: path} of the atlas pages: textures/<name>.<ext>, a name two pages share numbered after it."""
    out, used = {}, set()
    for oid in m.textures():
        info = m.objects.info(oid)
        stem = _UNSAFE.sub("_", info.get("name") or "texture")
        ext = (info.get("files") or {}).get("image", {}).get("ext", "png")
        path, n = f"textures/{stem}.{ext}", 1
        while path.casefold() in used:
            n += 1
            path = f"textures/{stem}-{n}.{ext}"
        used.add(path.casefold())
        out[oid] = path
    return out


def cubism(objects: Objects, key: str) -> dict[str, bytes]:
    """{path: bytes} of the Cubism SDK files of the model `key` (module documentation)."""
    m = _Model(objects, key)
    name = m.name
    files = {f"{name}.moc3": m.moc_bytes()}
    pages = _page_files(m)
    for oid, path in pages.items():
        files[path] = objects.bytes(oid, "image")
    refs: dict = {"Moc": f"{name}.moc3", "Textures": list(pages.values())}
    controllers = [c for _, c in m.components("CubismPhysicsController", root_only=True)]
    rig = controllers[0]["fields"].get("_rig") if controllers else None
    if rig and rig.get("SubRigs"):
        refs["Physics"] = f"{name}.physics3.json"
        files[refs["Physics"]] = _json(physics_json(rig))
    expressions = []
    for oid in m.scripted("CubismExpressionData"):
        tt = objects.json(oid)
        ename = tt["m_Name"].removesuffix(".exp3")
        files[f"{ename}.exp3.json"] = _json(expression_json(tt))
        expressions.append({"Name": ename, "File": f"{ename}.exp3.json"})
    if expressions:
        refs["Expressions"] = sorted(expressions, key=lambda e: e["Name"])
    rates = {}
    for cid in objects.ids("AnimationClip"):
        if "json" in (objects.info(cid).get("files") or {}):
            clip = objects.json(cid)
            rates[clip["name"]] = float(clip["sampleRate"])
    motions = {}
    for oid in m.scripted("CubismFadeMotionData"):
        tt = objects.json(oid)
        mname = motion.motion_name(tt)
        files[f"motions/{mname}.motion3.json"] = _json(motion.convert_fade(tt, rates.get(mname)))
        entry = {"File": f"motions/{mname}.motion3.json"}
        for k, field in (("FadeInTime", "ModelFadeInTime"), ("FadeOutTime", "ModelFadeOutTime")):
            if tt[field] >= 0:
                entry[k] = tt[field]
        motions[mname] = [entry]
    refs["Motions"] = dict(sorted(motions.items()))
    groups = []
    for group, cls in (("EyeBlink", "CubismEyeBlinkParameter"), ("LipSync", "CubismMouthParameter")):
        ids = sorted(n["name"] for n, _ in m.components(cls))
        if ids:
            groups.append({"Target": "Parameter", "Name": group, "Ids": ids})
    files[f"{name}.model3.json"] = _json({"Version": 3, "FileReferences": refs, "Groups": groups}, indent=1)
    return files
