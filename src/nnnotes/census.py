"""The census of a bundle (stage unity.census, document nnnotes.census/1): what a bundle holds, read without
decoding any object.

    files          the serialized files, sorted by name, each with its Unity version, platform, whether it embeds
                   type trees, its externals ({file, path, guid, type}; `file` is the name an object id uses: the
                   last component of the path, e.g. CAB-... or "unity default resources") and its objects sorted by
                   path id: {pathId, classId, class, byteSize, typeHash, name, script}. `name` is the object's m_Name
                   when its type has one (read without the fields after it), `script` the {file, pathId} of a
                   MonoBehaviour's m_Script (null for a null reference), `bindingScripts` the {file, pathId} of the
                   MonoScripts an AnimationClip's bindings name (MonoBehaviour fields it animates; sorted, only when
                   there are any). Objects are grouped per file: the files of one bundle may use the same path ids
                   (a scene bundle's scene and its shared assets).
    resources      the other files of the bundle (.resS / .resource): {name, size}
    assetBundles   the AssetBundle objects: {file, pathId, name, assetBundleName, dependencies,
                   isStreamedSceneAssetBundle, sceneHashes [[scene, hash]], container [{path, file, pathId,
                   preloadIndex, preloadSize}] (stored order)}
    scripts        the MonoScript objects: {file, pathId, assembly, namespace, class, name}
    classes        objects per class; `objects` the total
    errors         [{object, message}] of the objects whose header could not be read

A census is a function of the bundle's bytes: it holds no file name, time or path.
"""
from __future__ import annotations

import re

from . import contract
from .atoms import impl_id
from .contract import Cost, Input
from .stages import Output, Pending, Stage

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
HEAD_FIELDS = ("m_Name", "m_Script")
CLIP_FIELDS = ("m_Name", "m_ClipBindingConstant")        # an AnimationClip is read up to its bindings


def _ext_file(path: str) -> str:
    """The name an object id uses for an external file: the last component of its path."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _message(e: BaseException) -> str:
    return _ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:300]


def _serialized(env) -> tuple[list, list]:
    """(serialized files, [name, size] of the other files) of a loaded environment, walking nested bundles."""
    files, resources = [], []

    def walk(container):
        for name, f in getattr(container, "files", {}).items():
            if hasattr(f, "objects") and hasattr(f, "externals"):
                files.append(f)
            elif hasattr(f, "files"):
                walk(f)
            else:
                size = getattr(f, "Length", None)
                if size is None and hasattr(f, "bytes"):
                    size = len(f.bytes)
                resources.append({"name": name, "size": size})

    for f in getattr(env, "files", {}).values():
        if hasattr(f, "objects") and hasattr(f, "externals"):
            files.append(f)
        else:
            walk(f)
    return sorted(files, key=lambda f: f.name), sorted(resources, key=lambda r: r["name"])


class _Refs:
    """PPtr resolution within one serialized file."""

    def __init__(self, sf):
        self.name = sf.name
        self.externals = [_ext_file(x.path) for x in sf.externals]

    def file(self, file_id: int) -> str | None:
        if file_id == 0:
            return self.name
        return self.externals[file_id - 1] if 0 < file_id <= len(self.externals) else None

    def ref(self, p) -> dict | None:
        """{file, pathId} of a PPtr read as a dict; None for a null reference. An unknown file index keeps it as
        fileId."""
        if not isinstance(p, dict) or not p.get("m_PathID"):
            return None
        fid = int(p.get("m_FileID", 0))
        f = self.file(fid)
        return {"file": f, "pathId": int(p["m_PathID"])} if f is not None else {"fileId": fid,
                                                                                "pathId": int(p["m_PathID"])}


def _pairs(v) -> list:
    """A typetree map as [(key, value)] (UnityPy gives lists of pairs)."""
    out = []
    for item in v or ():
        try:
            k, x = item
        except (TypeError, ValueError):
            continue
        out.append((k, x))
    return out


def _asset_bundle(d: dict, refs: _Refs, oid: tuple[str, int]) -> dict:
    container = []
    for path, info in _pairs(d.get("m_Container")):
        info = info if isinstance(info, dict) else {}
        target = refs.ref(info.get("asset")) or {}
        container.append({"path": path, "file": target.get("file"), "pathId": target.get("pathId"),
                          "preloadIndex": info.get("preloadIndex"), "preloadSize": info.get("preloadSize")})
    return {"file": oid[0], "pathId": oid[1], "name": d.get("m_Name"), "assetBundleName": d.get("m_AssetBundleName"),
            "dependencies": list(d.get("m_Dependencies") or ()),
            "isStreamedSceneAssetBundle": bool(d.get("m_IsStreamedSceneAssetBundle")),
            "sceneHashes": [[k, v] for k, v in _pairs(d.get("m_SceneHashes"))], "container": container}


def _binding_scripts(d: dict, refs: "_Refs") -> list:
    """The MonoScripts an AnimationClip's generic bindings name, {file, pathId} (or {fileId, pathId}), sorted."""
    out = {}
    for b in ((d.get("m_ClipBindingConstant") or {}).get("genericBindings") or ()):
        r = refs.ref(b.get("script")) if isinstance(b, dict) else None
        if r is not None:
            out[(r.get("file") is None, r.get("file") or "", r.get("fileId", 0), r["pathId"])] = r
    return [out[k] for k in sorted(out)]


def census_env(env) -> dict:
    """The census document of a loaded UnityPy environment holding one bundle."""
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    heads: dict[tuple, tuple] = {}

    def head(node, fields=HEAD_FIELDS):
        """The type tree cut after the last of its `fields` (None when it has none of them)."""
        hit = heads.get((id(node), fields))
        if hit is not None and hit[0] is node:
            return hit[1]
        names = [c.m_Name for c in node.m_Children]
        last = max((names.index(n) for n in fields if n in names), default=None)
        cut = None
        if last is not None:
            cut = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize, node.m_Version,
                               m_MetaFlag=node.m_MetaFlag, m_Children=node.m_Children[:last + 1])
        heads[(id(node), fields)] = (node, cut)
        return cut

    sfiles, resources = _serialized(env)
    files, bundles, scripts, errors, classes = [], [], [], [], {}
    for sf in sfiles:
        refs = _Refs(sf)
        objects = []
        for pid in sorted(sf.objects):
            o = sf.objects[pid]
            try:
                cls = o.type.name
            except Exception:
                cls = f"Class{o.class_id}"
            st = getattr(o, "serialized_type", None)
            th = getattr(st, "old_type_hash", None) if st is not None else None
            rec = {"pathId": int(pid), "classId": int(o.class_id), "class": cls, "byteSize": int(o.byte_size),
                   "typeHash": th.hex() if th else None}
            classes[cls] = classes.get(cls, 0) + 1
            try:
                node = o._get_typetree_node()
                cut = head(node, CLIP_FIELDS if cls == "AnimationClip" else HEAD_FIELDS)
                d = o.read_typetree(cut, check_read=False) if cut is not None else {}
                if "m_Name" in d:
                    rec["name"] = d["m_Name"]
                if "m_Script" in d:
                    rec["script"] = refs.ref(d["m_Script"])
                if cls == "AnimationClip":
                    scripts_ = _binding_scripts(d, refs)
                    if scripts_:
                        rec["bindingScripts"] = scripts_
                if cls == "AssetBundle":
                    bundles.append(_asset_bundle(o.read_typetree(), refs, (sf.name, int(pid))))
                elif cls == "MonoScript":
                    m = o.read_typetree()
                    scripts.append({"file": sf.name, "pathId": int(pid), "assembly": m.get("m_AssemblyName"),
                                    "namespace": m.get("m_Namespace"), "class": m.get("m_ClassName"),
                                    "name": m.get("m_Name")})
            except Exception as e:
                errors.append({"object": contract.object_id(sf.name, pid), "message": _message(e)})
            objects.append(rec)
        files.append({"name": sf.name, "unityVersion": getattr(sf, "unity_version", None),
                      "platform": int(getattr(sf, "_m_target_platform", getattr(sf, "target_platform", 0)) or 0),
                      "typetree": bool(getattr(sf, "_enable_type_tree", True)),
                      "externals": [{"file": _ext_file(x.path), "path": x.path,
                                     "guid": x.guid.hex() if getattr(x, "guid", None) else None,
                                     "type": getattr(x, "type", None)} for x in sf.externals],
                      "objects": objects})
    return {"schema": contract.CENSUS, "files": files, "resources": resources,
            "assetBundles": sorted(bundles, key=lambda b: (b["file"], b["pathId"])),
            "scripts": sorted(scripts, key=lambda s: (s["file"], s["pathId"])),
            "classes": dict(sorted(classes.items())), "objects": sum(classes.values()), "errors": errors}


def census_file(path) -> dict:
    """The census of a bundle file (decrypted UnityFS)."""
    from . import unity
    env = unity.load(path)
    try:
        return census_env(env)
    finally:
        del env


# ---------------------------------------------------------------- reading a census
def objects(doc: dict):
    """(file, object record) of every object of a census, by file and path id."""
    for f in doc["files"]:
        for o in f["objects"]:
            yield f["name"], o


def container(doc: dict) -> list[dict]:
    """The container entries of every AssetBundle object of a census, in stored order."""
    return [e for b in doc["assetBundles"] for e in b["container"]]


def script_refs(doc: dict) -> list[str]:
    """The object ids of the MonoScripts a census's objects reference (a MonoBehaviour's m_Script, the scripts an
    AnimationClip's bindings name), sorted."""
    out = set()
    for _, o in objects(doc):
        for s in (o.get("script"), *o.get("bindingScripts", ())):
            if s and s.get("file") is not None:
                out.add(contract.object_id(s["file"], s["pathId"]))
    return sorted(out)


def facts(doc: dict) -> dict:
    """The facts a census artifact record carries (what planning reads without opening the document)."""
    return {"files": len(doc["files"]), "objects": doc["objects"], "classes": doc["classes"],
            "scripts": len(doc["scripts"]), "scriptRefs": len(script_refs(doc)), "errors": len(doc["errors"])}


def bundle_subjects(index: dict) -> dict[str, dict]:
    """{census subject: bundle entry} of a catalog index: the subject of a bundle is its stable name (the full file
    name for names whose stable names collide, as the index records them)."""
    return {b["stable"]: b for b in index["bundles"]}


# ---------------------------------------------------------------- stage
class CensusStage(Stage):
    """unity.census: one bundle -> its census. Subjects and inputs come from the fact "bundles": {subject: Input
    (role "bundle": the decrypted bundle's sha256, size, name, locators)}; a bundle not fetched yet is Pending
    (raised by the fact, or given as a Pending / None). Artifact "<task id>#census". The census
    reads the bundle with UnityPy: its atom is the reader's ("reader", the UnityPy version)."""
    name = "unity.census"
    version = 2
    ATOMS = {"reader": impl_id(("UnityPy",), 1)}

    def subjects(self, env) -> list[str]:
        return sorted(env.fact("bundles"))

    def inputs(self, subject: str, env) -> list[Input]:
        inp = env.fact("bundles")[subject]
        if inp is None:                             # listed but not fetched yet
            raise Pending(f"fetch:{subject}")
        if isinstance(inp, Exception):              # a bundle the orchestrator cannot give yet (Pending "fetch:...")
            raise inp
        if inp.role != "bundle":
            raise ValueError(f"unity.census {subject}: input role {inp.role!r}, expected 'bundle'")
        return [inp]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.002 + size / 40e6, (32 << 20) + 12 * size)

    def run(self, task, store) -> Output:
        doc = census_file(store.open_input(task.input("bundle")))
        content = store.add(contract.encode(doc), "json")
        rec = contract.artifact(contract.artifact_id(task.id, "census"), content, contract.provenance(task),
                                {"kind": "unity.census", "format": "json", "facts": facts(doc)})
        return Output([rec], [])
