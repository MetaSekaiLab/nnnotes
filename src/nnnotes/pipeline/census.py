"""The census of a bundle (stage unity.census, document nnnotes.census/2): what a bundle holds and what its
objects reference, read without decoding any object.

    files          the serialized files, sorted by name, each with its Unity version, platform, whether it embeds
                   type trees, its externals ({file, path, guid, type}; `file` is the name an object id uses: the
                   last component of the path, e.g. CAB-... or "unity default resources") and its objects sorted by
                   path id: {pathId, classId, class, byteSize, typeHash, name, script, bindingScripts, refs, unit}.
                   `name` is the object's m_Name when its type has one, `script` the {file, pathId} of a
                   MonoBehaviour's m_Script (null for a null reference), `bindingScripts` the {file, pathId} of the
                   MonoScripts an AnimationClip's bindings name (MonoBehaviour fields it animates; sorted, only when
                   there are any). `refs` the object ids every reference in its typetree names (sorted, itself left
                   out; only when there are any), `unit` the object id of the root GameObject of the hierarchy whose
                   prefab holds it (unity.export converts the objects of a hierarchy together; only for the objects
                   of a hierarchy other than its root). Objects are grouped per file: the files of one bundle may use
                   the same path ids (a scene bundle's scene and its shared assets).
    resources      the other files of the bundle (.resS / .resource): {name, size}
    assetBundles   the AssetBundle objects: {file, pathId, name, assetBundleName, dependencies,
                   isStreamedSceneAssetBundle, sceneHashes [[scene, hash]] (the hash is the name of the scene's
                   serialized file), container [{path, file, pathId, preloadIndex, preloadSize}] (stored order)}
    resourcePaths  the container of the ResourceManager objects (the paths Resources.Load finds objects at, in the
                   APK's global managers): [{path, file, pathId}] in stored order; a path names one object per
                   class it was saved as
    scripts        the MonoScript objects: {file, pathId, assembly, namespace, class, name}
    classes        objects per class; `objects` the total
    errors         [{object, message}] of the objects whose typetree could not be read (their record has no name,
                   script or refs)

The objects reachable from an object follow from `refs` and `unit` across censuses: they are a superset of what the
documents of unity.export reference (their items' `refs`), so a request's closure computed from censuses holds every
object its products name. A file without type trees (the APK's built-in files) gives a MonoBehaviour's fields only
with a type tree nnnotes ships for its type hash (`shipped`), else its header.

A census is a function of the bundle's bytes: it holds no file name, time or path (a serialized file outside a
bundle, one of the APK's built-in files, has the name its subject gives: builtins.serialized_name).
"""

from __future__ import annotations

import re

from ..unity import scripttypes
from ..unity.atoms import impl_id
from . import builtins, contract
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")


def _ext_file(path: str) -> str:
    """The name an object id uses for an external file: the last component of its path."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _message(e: BaseException) -> str:
    return _ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:300]


def _serialized(env) -> tuple[list, list]:
    """(serialized files, {name, size} of the other files) of a loaded environment, walking nested bundles."""
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
    """PPtr resolution within one serialized file: its name and the names of its externals."""

    def __init__(self, name: str, externals: list[str]):
        self.name, self.externals = name, list(externals)

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
        return (
            {"file": f, "pathId": int(p["m_PathID"])}
            if f is not None
            else {"fileId": fid, "pathId": int(p["m_PathID"])}
        )


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
        container.append(
            {
                "path": path,
                "file": target.get("file"),
                "pathId": target.get("pathId"),
                "preloadIndex": info.get("preloadIndex"),
                "preloadSize": info.get("preloadSize"),
            }
        )
    return {
        "file": oid[0],
        "pathId": oid[1],
        "name": d.get("m_Name"),
        "assetBundleName": d.get("m_AssetBundleName"),
        "dependencies": list(d.get("m_Dependencies") or ()),
        "isStreamedSceneAssetBundle": bool(d.get("m_IsStreamedSceneAssetBundle")),
        "sceneHashes": [[k, v] for k, v in _pairs(d.get("m_SceneHashes"))],
        "container": container,
    }


def _resource_paths(d: dict, refs: _Refs) -> list:
    out = []
    for path, p in _pairs(d.get("m_Container")):
        target = refs.ref(p) or {}
        out.append({"path": path, "file": target.get("file"), "pathId": target.get("pathId")})
    return out


def _binding_scripts(d: dict, refs: _Refs) -> list:
    """The MonoScripts an AnimationClip's generic bindings name, {file, pathId} (or {fileId, pathId}), sorted."""
    out = {}
    for b in (d.get("m_ClipBindingConstant") or {}).get("genericBindings") or ():
        r = refs.ref(b.get("script")) if isinstance(b, dict) else None
        if r is not None:
            out[(r.get("file") is None, r.get("file") or "", r.get("fileId", 0), r["pathId"])] = r
    return [out[k] for k in sorted(out)]


def census_env(env, shipped: bool = False) -> dict:
    """The census document of a loaded UnityPy environment holding one bundle (or one serialized file). `shipped`:
    the MonoBehaviours of files without type trees are read with the type trees nnnotes ships
    (nnnotes.unity.scripttypes) for their type hashes."""
    from ..unity.atoms.reader import UnityBundle
    from ..unity.convert import prefab
    from ..unity.convert.objects import BundleObjects, ReadError

    _, resources = _serialized(env)
    view = UnityBundle(env)
    trees: dict = {}
    if shipped:
        for f in view.files():
            if not view.stores_typetrees(f):
                trees.update(scripttypes.script_types(view.unity_version(f)))
    ctx = BundleObjects(view, {}, scripts={}, blob_min=1, png_level=0, emit=_no_emit, typetrees=trees)
    units = _units(ctx, prefab.hierarchies(ctx))
    by_file: dict[str, list] = {}
    for i in ctx.infos:
        by_file.setdefault(i.file, []).append(i)
    files, bundles, paths, scripts, errors, classes = [], [], [], [], [], {}
    for name in sorted(by_file):
        sf = view.serialized_file(name)
        refs = _Refs(name, ctx.ext_files[name])
        objects = []
        for i in sorted(by_file[name], key=lambda i: i.path_id):
            key = (i.file, i.path_id)
            rec = {
                "pathId": i.path_id,
                "classId": i.class_id,
                "class": i.class_name,
                "byteSize": i.byte_size,
                "typeHash": i.type_hash,
            }
            classes[i.class_name] = classes.get(i.class_name, 0) + 1
            try:
                d = ctx.tt(key, whole=False)
                found = ctx.references(key, d)
            except Exception as e:
                why = str(e) if isinstance(e, ReadError) else _message(e)
                errors.append({"object": contract.object_id(*key), "message": why})
            else:
                if isinstance(d.get("m_Name"), str):
                    rec["name"] = d["m_Name"]
                if "m_Script" in d:
                    rec["script"] = refs.ref(d["m_Script"])
                if i.class_name == "AnimationClip":
                    scripts_ = _binding_scripts(d, refs)
                    if scripts_:
                        rec["bindingScripts"] = scripts_
                if found:
                    rec["refs"] = sorted(contract.object_id(*k) for k in found)
                if i.class_name == "AssetBundle":
                    bundles.append(_asset_bundle(d, refs, key))
                elif i.class_name == "ResourceManager":
                    paths.extend(_resource_paths(d, refs))
                elif i.class_name == "MonoScript":
                    scripts.append(
                        {
                            "file": name,
                            "pathId": i.path_id,
                            "assembly": d.get("m_AssemblyName"),
                            "namespace": d.get("m_Namespace"),
                            "class": d.get("m_ClassName"),
                            "name": d.get("m_Name"),
                        }
                    )
            if key in units:
                rec["unit"] = units[key]
            objects.append(rec)
        files.append(
            {
                "name": name,
                "unityVersion": view.unity_version(name),
                "platform": int(getattr(sf, "_m_target_platform", getattr(sf, "target_platform", 0)) or 0),
                "typetree": view.stores_typetrees(name),
                "externals": [
                    {
                        "file": _ext_file(x.path),
                        "path": x.path,
                        "guid": x.guid.hex() if getattr(x, "guid", None) else None,
                        "type": getattr(x, "type", None),
                    }
                    for x in sf.externals
                ],
                "objects": objects,
            }
        )
    return {
        "schema": contract.CENSUS,
        "files": files,
        "resources": resources,
        "assetBundles": sorted(bundles, key=lambda b: (b["file"], b["pathId"])),
        "resourcePaths": paths,
        "scripts": sorted(scripts, key=lambda s: (s["file"], s["pathId"])),
        "classes": dict(sorted(classes.items())),
        "objects": sum(classes.values()),
        "errors": errors,
    }


def _no_emit(*args, **kw):
    raise RuntimeError("a census writes no artifact")


def _units(ctx, h) -> dict[tuple, str]:
    """{object key: the object id of the root GameObject of the hierarchy it belongs to} of the objects of each
    hierarchy the converters export as one prefab (prefab.hierarchies: walked without error, rooted at a GameObject),
    the root left out."""
    out = {}
    for root in h.roots:
        go = (root.file, root.game_object)
        info = ctx.info.get(go)
        if root.error is not None or info is None or info.class_name != "GameObject":
            continue
        oid = contract.object_id(*go)
        for k in root.members:
            if k != go:
                out[k] = oid
    return out


def census_file(path, name: str | None = None, shipped: bool = False) -> dict:
    """The census of a bundle file (decrypted UnityFS), or of a serialized file named `name` (census_env)."""
    from ..unity import reader as unity

    env = unity.load(path, name)
    try:
        return census_env(env, shipped)
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


def scenes(doc: dict) -> list[dict]:
    """The objects of the scenes of a scene bundle as container entries {path, file, pathId}: a scene's container
    entry names no object, its scene hash names its serialized file, and the scene is the objects of that file outside
    a hierarchy (without `unit`: the root GameObjects and the scene's settings), by scene and path id."""
    files = {f["name"]: f for f in doc["files"]}
    out = []
    for b in doc["assetBundles"]:
        for path, name in b["sceneHashes"]:
            if name in files:
                out += [
                    {"path": path, "file": name, "pathId": o["pathId"]}
                    for o in files[name]["objects"]
                    if "unit" not in o
                ]
    return out


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
    return {
        "files": len(doc["files"]),
        "objects": doc["objects"],
        "classes": doc["classes"],
        "scripts": len(doc["scripts"]),
        "scriptRefs": len(script_refs(doc)),
        "errors": len(doc["errors"]),
    }


def bundle_subjects(index: dict) -> dict[str, dict]:
    """{census subject: bundle entry} of a catalog index: the subject of a bundle is its stable name (the full file
    name for names whose stable names collide, as the index records them)."""
    return {b["stable"]: b for b in index["bundles"]}


# ---------------------------------------------------------------- stage
class CensusStage(Stage):
    """unity.census: one bundle -> its census. Subjects and inputs come from the fact "bundles": {subject: Input
    (role "bundle": the decrypted bundle's sha256, size, name, locators)}, the APK's built-in files included
    (builtins); a bundle not fetched yet is Pending (raised by the fact, or given as a Pending / None). Artifact
    "<task id>#census". The census reads the bundle with UnityPy: its atom is the reader's ("reader", the UnityPy
    version); a built-in file's also the shipped type trees ("typetrees", scripttypes.types_id) it reads its
    MonoBehaviours with."""

    name = "unity.census"
    version = 4
    ATOMS = {"reader": impl_id(("UnityPy",), 1)}

    def subjects(self, env) -> list[str]:
        return sorted(env.fact("bundles"))

    def inputs(self, subject: str, env) -> list[Input]:
        inp = env.fact("bundles")[subject]
        if inp is None:  # listed but not fetched yet
            raise Pending(f"fetch:{subject}")
        if isinstance(inp, Exception):  # a bundle the orchestrator cannot give yet (Pending "fetch:...")
            raise inp
        if inp.role != "bundle":
            raise ValueError(f"unity.census {subject}: input role {inp.role!r}, expected 'bundle'")
        return [inp]

    def uses(self, subject: str, env) -> dict:
        if builtins.is_builtin(subject):
            return {**self.ATOMS, "typetrees": scripttypes.types_id()}
        return dict(self.ATOMS)

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.002 + size / 20e6, (32 << 20) + 14 * size)

    def run(self, task, depot) -> Output:
        if task.atoms.get("typetrees", scripttypes.types_id()) != scripttypes.types_id():
            raise IncompatibleTask(f"task {task.id}: typetrees are {scripttypes.types_id()} here")
        doc = census_file(
            depot.open_input(task.input("bundle")),
            builtins.serialized_name(task.subject),
            shipped="typetrees" in task.atoms,
        )
        content = depot.add(contract.encode(doc), "json")
        rec = contract.artifact(
            contract.artifact_id(task.id, "census"),
            content,
            contract.provenance(task),
            {"kind": "unity.census", "format": "json", "facts": facts(doc)},
        )
        return Output([rec], [])
