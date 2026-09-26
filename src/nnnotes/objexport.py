"""Stages `unity.export` (one bundle -> an artifact per object) and `sprite.crop` (the images of the sprites whose atlas
is in another bundle).

unity.export reads one bundle alone (atom `reader`) and gives every object exactly one item (contract.item):
`exported` (its artifacts), `contained` (part of another artifact: the objects of a hierarchy in its root's prefab,
the MonoScripts in `scripts.json`, the AssetBundle objects in `bundle.json`), `generic` (the object's typetree JSON,
with the reason its converter gave for not making the specialized form), `unsupported` (a class this stage does not
convert; its typetree JSON is written all the same) or `failed` (not even the typetree JSON could be made). The
converters (CONVERTERS; each has its own version, and a bundle's task key includes only the converters of the classes
its census lists):

    Texture2D             tex.png          `image`: PNG of the first mip level (HDR ASTC: the .astc file)
    Sprite                sprite.png       `image`: PNG on the sprite's rect; `meta`: JSON
    Mesh                  mesh.glb         `mesh`: binary glTF in the mesh's local space
    GameObject (a root)   prefab.json      `prefab`: the hierarchy below it with its components
    Material              material.json    `json`
    Shader                shader.json      `json`: the parsed form as `nnnotes shader` writes it; `program:<platform>/
                                           s<S>p<P>_<stage>_<N>`: each compiled sub-program as stored; `index`: the
                                           variants (platform, pass, stage, GPU program type, keywords); `typetree`
    AnimationClip         clip.json        `json`
    AnimatorController    controller.json  `json`
    MonoBehaviour         mono.json        `json` (a MonoBehaviour that is not part of a hierarchy); a cue sheet asset
                                           also `acb`: its ACB (kind cri.acb, the cri.audio stage decodes it)
    TextAsset             data             `data`: the stored bytes
    Font                  font             `font`: the font file
    MonoScript            scripts.json     "<task id>#scripts.json": every MonoScript of the bundle
    AssetBundle           bundle.json      "<task id>#bundle.json": serialized files, externals, AssetBundle objects
    any other class       generic.json     `json`: the object's typetree

Object JSON (canon/1 in the object's own field order):
- a reference is {"$ref": {"file", "pathId"}}, with "class" and "name" when the target is in the bundle; a null
  reference is null. Inside a prefab, a GameObject, Transform or component of the same prefab is named by its path:
  {"gameObject": path}, {"transform": path}, {"component": class, "gameObject": path};
- a byte array (vector of UInt8 / SInt8 / char, TypelessData) shorter than `blobMin` bytes is {"$hex": "..."}; a
  longer one is {"$blob": {"sha256", "size", "format"}} with its bytes in the artifact
  "<object id>#blob:<field path>" (field names and array indices joined by "/", the format from atom `sniff`);
- a string whose stored bytes are not UTF-8 is {"$hex": "<the stored bytes>"};
- NaN is {"$float": "nan"}, infinities 1e999 / -1e999.

A cue sheet held in a bundle is a MonoBehaviour in one of two layouts (cri.py): a SplitAcbData (`_cueSheetName`,
`_chunks`: TextAssets of this bundle, joined and unmasked into the ACB) or a CriWare.Assets asset named like the sheet
whose `implementation` managed reference is a CriSerializedBytesAssetImpl (its `data.data` bytes are the ACB: the
field's {"$blob"} names the `acb` artifact, whatever its size). The artifact's facts name the cue sheet.

A converter's documented limit (atoms.Unsupported) gives the typetree JSON with its reason code (status `generic`);
any other error of a converter gives it with `generic.error` and the error's message. The reason codes are listed in
docs/stages.md.

sprite.crop: a sprite whose SpriteAtlas is in another bundle gets its `meta` from unity.export (with the sprite's
mesh and object facts); one sprite.crop task per atlas then reads the atlas' JSON, the images of its textures and the
metas, and makes "<sprite object id>#image" exactly as unity.export makes the image of a sprite whose atlas is in its
own bundle.
"""
from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from . import census as census_mod, contract, link
from .atoms import ATOMS as ATOM_REGISTRY, Impl, Unsupported, atom_id, resolve
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

EXPORT, CROP, CENSUS, SCRIPTS = "unity.export", "sprite.crop", "unity.census", "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")

# converter -> version
CONVERTERS = {"tex.png": 1, "sprite.png": 1, "mesh.glb": 1, "prefab.json": 1, "material.json": 1, "clip.json": 1,
              "controller.json": 1, "mono.json": 2, "data": 1, "font": 1, "scripts.json": 1, "bundle.json": 1,
              "shader.json": 1, "generic.json": 1}
# class -> the converters its objects may use (every class may fall back to generic.json)
CLASS_CONVERTERS = {"Texture2D": ("tex.png",), "Sprite": ("sprite.png",), "Mesh": ("mesh.glb",),
                    "GameObject": ("prefab.json",), "Material": ("material.json",), "AnimationClip": ("clip.json",),
                    "AnimatorController": ("controller.json",), "AnimatorOverrideController": ("controller.json",),
                    "MonoBehaviour": ("mono.json",), "TextAsset": ("data",), "Font": ("font",),
                    "MonoScript": ("scripts.json",), "AssetBundle": ("bundle.json",), "Shader": ("shader.json",)}
ALWAYS = ("generic.json",)
BASE_ATOMS = ("reader", "sniff")
CONVERTER_ATOMS = {"tex.png": ("texture.decode", "png.encode", "astc.container"),
                   "sprite.png": ("texture.decode", "png.encode", "sprite.crop", "mesh.arrays"),
                   "mesh.glb": ("mesh.arrays", "gltf.write")}
UNSUPPORTED_CLASSES = ("AudioClip", "VideoClip", "MovieTexture")
LATE_CLASSES = ("AnimationClip", "AnimatorController", "AnimatorOverrideController")
FONT_FORMATS = ("ttf", "otf", "ttc", "woff", "woff2")
# every reason code this module gives (the atoms give more; docs/stages.md lists them all)
REASONS = ("generic.error", "generic.clip.legacy", "generic.controller.override", "script.unresolved",
           "script.missing", "no_data.sprite", "no_data.font", "unsupported.sprite.external_texture",
           "unsupported.cri.awb_external", "failed.read", "failed.export",
           *(f"unsupported.class.{c}" for c in UNSUPPORTED_CLASSES))
ACB_KIND = "cri.acb"
SPLIT_ACB, EMBEDDED_ACB = "split", "embedded"
EMBEDDED_ACB_IMPL = "CriSerializedBytesAssetImpl"

_BYTE_TYPES = frozenset({"UInt8", "SInt8", "char"})
_SCALARS = (int, float, str, bool, type(None))
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
_EXT = re.compile(r"[0-9a-z]+")
_NO_SCRIPT = "\0none"                      # a MonoBehaviour whose m_Script is null
ATOM_NAMES = frozenset(ATOM_REGISTRY)


def converter_id(name: str) -> str:
    return f"{name}/{CONVERTERS[name]}"


def converters_for(classes) -> list[str]:
    """The converters a bundle holding objects of `classes` may use."""
    out = set(ALWAYS)
    for c in classes:
        out.update(CLASS_CONVERTERS.get(c, ()))
    return sorted(out)


def export_atoms(classes, reader_id: str | None = None) -> dict:
    """The `atoms` part of a unity.export task for a bundle holding `classes`: the atoms and converters it uses."""
    convs = converters_for(classes)
    names = set(BASE_ATOMS)
    for c in convs:
        names.update(CONVERTER_ATOMS.get(c, ()))
    out = {n: (reader_id if n == "reader" and reader_id else atom_id(n)) for n in names}
    out.update((c, converter_id(c)) for c in convs)
    return dict(sorted(out.items()))


def _message(e: BaseException) -> str:
    return _label(_ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:500])


def _label(s):
    """A name for a record: the string, with bytes that are not UTF-8 shown as U+FFFD."""
    if not isinstance(s, str) or s.isascii():
        return s
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _stored(v):
    """`v` with every string that is not UTF-8 (read with surrogateescape) as {"$hex": its stored bytes}."""
    if isinstance(v, str):
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return {"$hex": v.encode("utf-8", "surrogateescape").hex()}
    if isinstance(v, dict):
        return {_label(k): _stored(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_stored(x) for x in v]
    return v


def encode_object(doc) -> bytes:
    """canon/1 bytes of an object document (its own key order)."""
    try:
        return contract.encode(doc, sort_keys=False)
    except UnicodeEncodeError:
        return contract.encode(_stored(doc), sort_keys=False)


def _canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=True)


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _file_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _container_ext(path: str | None) -> str | None:
    if not path:
        return None
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return None
    ext = last.rsplit(".", 1)[1].lower()
    return ext if _EXT.fullmatch(ext) else None


def acb_asset(tt: dict) -> dict | None:
    """Which cue sheet layout a MonoBehaviour's typetree holds (cri.py): {"layout": "split", "cueSheet", "chunks"}
    for a SplitAcbData, {"layout": "embedded", "cueSheet", "ref": index in references.RefIds, "impl"} for an asset
    whose `implementation` is a CriSerializedBytesAssetImpl; None for any other. SplitAcbData first."""
    sheet = tt.get("_cueSheetName")
    if isinstance(sheet, str) and isinstance(tt.get("_chunks"), list):
        return {"layout": SPLIT_ACB, "cueSheet": sheet, "chunks": tt["_chunks"]}
    impl = tt.get("implementation")
    if not isinstance(impl, dict) or "rid" not in impl:
        return None
    ids = ((tt.get("references") or {}).get("RefIds")) or []
    for i, r in enumerate(ids):
        typ = r.get("type") if isinstance(r, dict) else None
        if isinstance(typ, dict) and r.get("rid") == impl["rid"] and typ.get("class") == EMBEDDED_ACB_IMPL:
            return {"layout": EMBEDDED_ACB, "cueSheet": tt.get("m_Name"), "ref": i, "impl": r}
    return None


def _ref_type_node(ref: dict, assets_file):
    """The type tree of a managed reference's data (UnityPy's get_ref_type_node over the file's ref types)."""
    typ = ref.get("type") or {}
    cls = typ.get("class") if isinstance(typ, dict) else getattr(typ, "class", None)
    if not cls:
        return None
    ns = typ.get("ns") if isinstance(typ, dict) else typ.ns
    asm = typ.get("asm") if isinstance(typ, dict) else typ.asm
    for rt in getattr(assets_file, "ref_types", None) or ():
        if rt.m_ClassName == cls and rt.m_NameSpace == ns and rt.m_AssemblyName == asm:
            return rt.node
    raise ValueError(f"referenced type {asm}|{ns}|{cls} is not among the file's reference types")


class _ReadError(Exception):
    """An object's typetree could not be read."""


class _Script:
    """A MonoScript outside the bundle, known from the task's script table: what Exporter code reads of it."""
    type = SimpleNamespace(name="MonoScript", value=115)

    def __init__(self, file: str, path_id: int, entry: str):
        self.assets_file = SimpleNamespace(name=file)
        self.path_id = path_id
        asm, ns, cls = (entry.split("|", 2) + ["", ""])[:3]
        self._ms = SimpleNamespace(m_AssemblyName=asm, m_Namespace=ns, m_ClassName=cls, m_Name=cls)

    def read(self):
        return self._ms


# ---------------------------------------------------------------- the exporter
class RefExporter:
    """unity.export on one bundle. Combined with export.Exporter (exporter_type), whose material, clip and controller
    code runs here in reference mode: `ref` gives $ref values, `deref` resolves inside the bundle only (a MonoScript
    of another bundle from the script table), and MonoBehaviour classes come from the task's script table."""

    def __init__(self, view, task, store, fns: dict):
        super().__init__(None, Path("."), class_of=self._script_name)
        self.view, self.task, self.store, self.fns = view, task, store, fns
        self.blob_min = int(task.params["blobMin"])
        self.png_level = int(task.params["png"]["level"])
        self.scripts = dict(task.context.get("scripts", {}))
        self.infos = view.objects()
        self.info = {(i.file, i.path_id): i for i in self.infos}
        self.readers = {k: view.reader(i) for k, i in self.info.items()}
        self._objects = self.readers
        self.files = view.files()
        self.ext_files = {f: [_file_name(p) for p in view.externals(f)] for f in self.files}
        self.versions = {f: view.unity_version(f) for f in self.files}
        self.containers: dict[tuple, str] = {}
        for path, file, pid in view.container():
            k = (file, int(pid))
            if k not in self.containers or path < self.containers[k]:
                self.containers[k] = path
        self.items: dict[str, dict] = {}
        self.artifacts: list[dict] = []
        self.emitted: set[str] = set()
        self.names: dict[tuple, str | None] = {}
        self.script_entry: dict[tuple, str | None] = {}
        self.special_memo: dict[int, tuple] = {}
        self.children_memo: dict[int, tuple] = {}
        self.heads: dict[int, tuple] = {}
        self.atlas_entries: dict[tuple, list] = {}
        self.graphs: dict[str, object] = {}
        self.member: dict[tuple, tuple] = {}
        self.comp_go: dict[tuple, int] = {}
        self.roots: list[tuple] = []
        self.scene_of: dict[str, str] = {}
        self.go_clash = False
        self.current: tuple | None = None
        self.staged: list[dict] = []
        self.blob_ids: list[str] = []
        self.conv = "generic.json"
        self.acb_field: tuple | None = None       # (object key, field path, facts) of the embedded ACB being walked
        self.atoms_of: dict[str, dict] = {}
        self.unresolved = 0
        classes = task.params.get("classes")
        self.selected = None if classes is None else frozenset(classes)
        self.has_clips = any(i.class_name == "AnimationClip" for i in self.infos) and self._selected("AnimationClip")

    # ---------------------------------------------------------------- identities, names, scripts
    @staticmethod
    def _key(o) -> tuple:
        return (o.assets_file.name, int(o.path_id))

    def _file_of(self, owner_file: str, fid: int):
        if fid == 0:
            return owner_file
        ext = self.ext_files.get(owner_file, ())
        return ext[fid - 1] if 0 < fid <= len(ext) else None

    def _target(self, owner, pptr: dict):
        """(file, pathId) a PPtr of `owner` names; None for a null reference or an unknown file index."""
        pid = int(pptr.get("m_PathID", 0) or 0)
        if not pid:
            return None
        file = self._file_of(owner.assets_file.name, int(pptr.get("m_FileID", 0) or 0))
        return None if file is None else (file, pid)

    def _peek(self, r) -> str | None:
        """An object's m_Name, read with its type tree cut after that field."""
        node = r._get_typetree_node()
        hit = self.heads.get(id(node))
        if hit is None or hit[0] is not node:
            names = [c.m_Name for c in node.m_Children]
            cut = None
            if "m_Name" in names:
                from UnityPy.helpers.TypeTreeNode import TypeTreeNode
                cut = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize, node.m_Version,
                                   m_MetaFlag=node.m_MetaFlag, m_Children=node.m_Children[:names.index("m_Name") + 1])
            hit = self.heads[id(node)] = (node, cut)
        if hit[1] is None:
            return None
        name = r.read_typetree(hit[1], check_read=False).get("m_Name")
        return name if isinstance(name, str) else None

    def _name_of(self, key: tuple) -> str | None:
        if key not in self.names:
            r = self.readers.get(key)
            name = None
            if r is not None:
                try:
                    name = self._peek(r)
                except Exception:
                    name = None
            self.names[key] = name
        return self.names[key]

    def _tt(self, key: tuple) -> dict:
        try:
            tt = self.view.typetree(self.info[key])
        except Exception as e:
            raise _ReadError(_message(e)) from None
        name = tt.get("m_Name")
        self.names[key] = name if isinstance(name, str) else None
        return tt

    def _note_script(self, key: tuple, tt: dict):
        s = tt.get("m_Script")
        if not isinstance(s, dict) or not s.get("m_PathID"):
            entry = _NO_SCRIPT
        else:
            file = self._file_of(key[0], int(s.get("m_FileID", 0) or 0))
            entry = None if file is None else self.scripts.get(contract.object_id(file, int(s["m_PathID"])))
        self.script_entry[key] = entry
        return entry

    def _script_name(self, o) -> str | None:
        """The class name of a MonoBehaviour from the task's script table (None when unknown)."""
        key = self._key(o)
        if key not in self.script_entry:
            self._note_script(key, self._tt(key))
        e = self.script_entry[key]
        return e.rsplit("|", 1)[-1] if e and e != _NO_SCRIPT else None

    # ---------------------------------------------------------------- references and values
    def _dref(self, key: tuple) -> dict:
        file, pid = key
        d = {"file": _label(file), "pathId": pid}
        r = self.readers.get(key)
        if r is not None:
            d["class"] = r.type.name
            name = self._name_of(key)
            if name is not None:
                d["name"] = _label(name)
        return {"$ref": d}

    def _local(self, key: tuple) -> dict:
        file, pid = key
        o, g = self.readers[key], self.graphs[file]
        t = o.type.name
        if t == "GameObject":
            return {"gameObject": g.path(g.tf_of_go[pid])}
        if t in ("Transform", "RectTransform"):
            return {"transform": g.path(pid)}
        d = {"component": t, "gameObject": g.path(g.tf_of_go[self.comp_go[key]])}
        if t == "MonoBehaviour":
            d["class"] = self._script_name(o)
        return d

    def ref(self, owner, pptr: dict):
        pid = int(pptr.get("m_PathID", 0) or 0)
        if not pid:
            return None
        fid = int(pptr.get("m_FileID", 0) or 0)
        file = self._file_of(owner.assets_file.name, fid)
        if file is None:
            return {"$ref": {"fileId": fid, "pathId": pid}}
        key = (file, pid)
        if self.current is not None and self.member.get(key) == self.current:
            return self._local(key)
        return self._dref(key)

    def deref(self, owner, pptr: dict):
        key = self._target(owner, pptr)
        if key is None:
            return None
        r = self.readers.get(key)
        if r is not None:
            return r
        entry = self.scripts.get(contract.object_id(*key))
        return None if entry is None else _Script(key[0], key[1], entry)

    def graph(self, o):
        return self.graphs[o.assets_file.name]

    def value(self, owner, v):
        if isinstance(v, dict):
            if v.keys() == {"m_FileID", "m_PathID"}:
                return self.ref(owner, v)
            return {k: self.value(owner, x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            if all(type(x) in _SCALARS for x in v):
                return list(v)
            return [self.value(owner, x) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return {"$hex": bytes(v).hex()}
        return v

    def _child(self, node, name: str):
        m = self.children_memo.get(id(node))
        if m is None or m[0] is not node:
            m = self.children_memo[id(node)] = (node, {c.m_Name: c for c in node.m_Children})
        return m[1].get(name)

    def _special(self, node) -> bool:
        """Whether values of `node` need rewriting: references, byte arrays, managed references below it."""
        hit = self.special_memo.get(id(node))
        if hit is not None and hit[0] is node:
            return hit[1]
        t, ch = node.m_Type, node.m_Children
        if t.startswith("PPtr<") or t in ("TypelessData", "ReferencedObject", "ManagedReferencesRegistry"):
            r = True
        elif t == "string" or not ch:
            r = False
        elif ch[0].m_Type == "Array" and len(ch[0].m_Children) > 1:
            sub = ch[0].m_Children[1]
            r = sub.m_Type in _BYTE_TYPES or self._special(sub)
        else:
            r = any(self._special(c) for c in ch)
        self.special_memo[id(node)] = (node, r)
        return r

    def tree(self, owner, tt: dict):
        """The object JSON of `owner`'s typetree `tt` (read with owner's type tree)."""
        return self._walk(owner, owner._get_typetree_node(), tt, "")

    def _walk(self, owner, node, v, path: str):
        if not self._special(node):
            return v
        t = node.m_Type
        if t.startswith("PPtr<"):
            return self.ref(owner, v) if isinstance(v, dict) else v
        if t == "TypelessData":
            return self._bytes_value(owner, v, path)
        ch = node.m_Children
        if t == "ReferencedObject":
            out = {}
            for k, x in v.items():
                c = self._child(node, k)
                if c is not None and c.m_Type == "ReferencedObjectData":
                    rn = _ref_type_node(v, owner.assets_file)
                    out[k] = x if rn is None else self._walk(owner, rn, x, _join(path, k))
                else:
                    out[k] = x if c is None else self._walk(owner, c, x, _join(path, k))
            return out
        if t != "string" and ch and ch[0].m_Type == "Array":
            sub = ch[0].m_Children[1]
            if sub.m_Type in _BYTE_TYPES:
                return self._bytes_value(owner, v, path, sub.m_Type == "SInt8")
            return [self._walk(owner, sub, x, _join(path, str(i))) for i, x in enumerate(v)]
        if t == "pair":
            return [self._walk(owner, ch[0], v[0], _join(path, "0")), self._walk(owner, ch[1], v[1], _join(path, "1"))]
        if not isinstance(v, dict):
            return v
        out = {}
        for k, x in v.items():
            c = self._child(node, k)
            out[k] = x if c is None else self._walk(owner, c, x, _join(path, k))
        return out

    def _bytes_value(self, owner, v, path: str, signed: bool = False) -> dict:
        if isinstance(v, (bytes, bytearray, memoryview)):
            data = bytes(v)
        elif signed:
            import numpy as np
            data = np.asarray(v, np.int16).astype(np.uint8).tobytes()
        else:
            data = bytes(v)
        if self.acb_field is not None and self.acb_field[:2] == (self._key(owner), path):
            from . import cri
            if data[:4] != cri.ACB_SIGNATURE:
                raise ValueError(f"{path}: the cue sheet data is not an ACB (@UTF): {data[:4].hex()}")
            aid, content = self._emit(self._key(owner), "acb", data, "acb", ACB_KIND, "acb", self.acb_field[2])
            self.blob_ids.append(aid)
            return {"$blob": {"sha256": content["sha256"], "size": content["size"], "format": "acb"}}
        if len(data) < self.blob_min:
            return {"$hex": data.hex()}
        s = self.fns["sniff"](data)
        aid, content = self._emit(self._key(owner), "blob:" + path, data, s.ext, "blob", s.format, {"field": path})
        self.blob_ids.append(aid)
        return {"$blob": {"sha256": content["sha256"], "size": content["size"], "format": s.format}}

    # ---------------------------------------------------------------- artifacts and items
    def _atoms(self, conv: str) -> dict:
        a = self.atoms_of.get(conv)
        if a is None:
            names = (*BASE_ATOMS, *CONVERTER_ATOMS.get(conv, ()), conv)
            a = self.atoms_of[conv] = {n: self.task.atoms[n] for n in sorted(names) if n in self.task.atoms}
        return a

    def _facts(self, key: tuple) -> dict:
        i = self.info[key]
        d = {"file": _label(i.file), "pathId": i.path_id, "classId": i.class_id, "class": i.class_name}
        name = self._name_of(key)
        if name is not None:
            d["name"] = _label(name)
        if i.type_hash:
            d["typeHash"] = i.type_hash
        d["unityVersion"] = self.versions.get(i.file)
        c = self.containers.get(key)
        if c:
            d["container"] = _label(c)
        return d

    def _emit(self, key, role: str, data: bytes, ext: str, kind: str, fmt: str, facts: dict, *, issues=(),
              refs=(), owner: str | None = None) -> tuple[str, dict]:
        """Store `data` and stage the artifact "<owner>#<role>" (owner: the object `key`, else a task-level name)."""
        aid = contract.artifact_id(owner or contract.object_id(*key), role)
        if aid in self.emitted or any(a["id"] == aid for a in self.staged):
            raise ValueError(f"artifact {aid} made twice")
        content = self.store.add(data, ext)
        prov = contract.provenance(self.task, atoms=self._atoms(self.conv), obj=None if owner else self._facts(key))
        sem = {"kind": kind, "format": fmt, "facts": facts}
        if issues:
            sem["issues"] = [{"code": c, "message": m} for c, m in issues]
        if refs:
            sem["refs"] = list(refs)
        self.staged.append(contract.artifact(aid, content, prov, sem))
        return aid, content

    def _begin(self, conv: str) -> None:
        self.staged, self.blob_ids, self.conv = [], [], conv

    def _commit(self, info, status: str, ids, why: dict | None = None) -> None:
        for a in self.staged:
            self.emitted.add(a["id"])
        self.artifacts.extend(self.staged)
        self.staged = []
        self.items[info.id] = contract.item(info.id, status, artifacts=list(ids), why=why, cls=info.class_name)

    def _need(self, conv: str) -> None:
        if conv not in self.task.atoms:
            raise IncompatibleTask(f"task {self.task.id}: converter {conv} is not in the task (its census lacks "
                                   f"the class)")

    def _convert(self, info, conv: str, fn, *args) -> bool:
        """Run a converter on one object: its artifacts (exported), else the typetree JSON with the reason."""
        if info.id in self.items:
            return False
        self._need(conv)
        self._begin(conv)
        try:
            ids = fn(info, *args)
        except Unsupported as e:
            self._fallback(info, e.code, e.message)
            return False
        except Exception as e:
            self._fallback(info, "generic.error", _message(e))
            return False
        self._commit(info, "exported", [*ids, *self.blob_ids])
        return True

    def _fallback(self, info, code: str, message: str, status: str = "generic") -> None:
        self._begin("generic.json")
        try:
            aid = self.conv_generic(info)[0]
        except Exception as e:
            self._begin("generic.json")
            what = "failed.read" if isinstance(e, _ReadError) else "failed.export"
            detail = str(e) if isinstance(e, _ReadError) else _message(e)
            self.items[info.id] = contract.item(info.id, "failed", why=contract.reason(
                what, _label(f"{code}: {message}; typetree JSON: {detail}")), cls=info.class_name)
            return
        self._commit(info, status, [aid, *self.blob_ids], contract.reason(code, _label(message)))

    def _generic(self, info) -> None:
        """An object of a class without a specialized converter: its typetree JSON."""
        if info.id in self.items:
            return
        self._begin("generic.json")
        try:
            ids = self.conv_generic(info)
        except Exception as e:
            self._begin("generic.json")
            what = "failed.read" if isinstance(e, _ReadError) else "failed.export"
            self.items[info.id] = contract.item(info.id, "failed", why=contract.reason(
                what, str(e) if isinstance(e, _ReadError) else _message(e)), cls=info.class_name)
            return
        self._commit(info, "exported", [*ids, *self.blob_ids])

    def _selected(self, cls: str) -> bool:
        return self.selected is None or cls in self.selected

    def _of(self, cls: str) -> list:
        """The objects of `cls` this task converts (none when the task's `classes` leave the class out)."""
        if not self._selected(cls):
            return []
        return [i for i in self.infos if i.class_name == cls]

    # ---------------------------------------------------------------- the bundle
    def run_bundle(self) -> tuple[list, list]:
        self._index()
        self._textures_sprites()
        for i in self._of("Mesh"):
            self._convert(i, "mesh.glb", self.conv_mesh)
        for i in self._of("TextAsset"):
            self._convert(i, "data", self.conv_text)
        for i in self._of("Font"):
            self._convert(i, "font", self.conv_font)
        for i in self._of("Material"):
            self._convert(i, "material.json", self.conv_material)
        for i in self._of("Shader"):
            self._convert(i, "shader.json", self.conv_shader)
        self._prefabs()
        for i in self._of("MonoBehaviour"):
            if i.id not in self.items:
                self._convert(i, "mono.json", self.conv_mono)
        self._loose("AssetBundle", "bundle.json")
        self._loose("MonoScript", "scripts.json")
        for i in self.infos:
            if i.id in self.items or i.class_name in LATE_CLASSES or not self._selected(i.class_name):
                continue
            if i.class_name in UNSUPPORTED_CLASSES:
                self._fallback(i, f"unsupported.class.{i.class_name}", f"{i.class_name} is not converted",
                               status="unsupported")
            else:
                self._generic(i)
        for i in self._of("AnimationClip"):
            self._convert(i, "clip.json", self.conv_clip)
        for cls in ("AnimatorController", "AnimatorOverrideController"):
            for i in self._of(cls):
                self._convert(i, "controller.json", self.conv_controller)
        for i in self.infos:
            if i.id not in self.items and self._selected(i.class_name):
                self.items[i.id] = contract.item(i.id, "failed", why=contract.reason(
                    "failed.export", "no converter took the object"), cls=i.class_name)
        return self.artifacts, list(self.items.values())

    def _index(self) -> None:
        """Hierarchies per serialized file (their roots, the objects each root's prefab holds), scene files."""
        from .unity import SceneGraph
        by_file = defaultdict(list)
        for i in self.infos:
            by_file[i.file].append(i)
        for f in self.files:
            objs = [self.readers[(i.file, i.path_id)] for i in by_file[f]
                    if i.class_name in ("GameObject", "Transform", "RectTransform")]
            try:
                g = SceneGraph(SimpleNamespace(objects=objs))
            except Exception:
                continue                           # the file's objects are exported one by one
            self.graphs[f] = g
            if by_file[f]:
                self._graph_of_file[id(self.readers[(f, by_file[f][0].path_id)].assets_file)] = g
            for go_pid in g.go:
                if self._go_file.get(go_pid, f) != f:
                    self.go_clash = True
                self._go_file[go_pid] = f
            for tf_pid, t in g.tf.items():
                father = int(t["m_Father"]["m_PathID"] or 0)
                if father and father in g.tf:
                    continue
                go_pid = int(t["m_GameObject"]["m_PathID"] or 0)
                members, comps, error = self._subtree(f, g, tf_pid)
                self.roots.append((f, tf_pid, go_pid, members, error))
                if error is None:
                    for k in members:
                        self.member[k] = (f, go_pid)
                    self.comp_go.update(comps)
        for i in self._of("AssetBundle"):
            try:
                tt = self._tt((i.file, i.path_id))
            except _ReadError:
                continue
            for pair in tt.get("m_SceneHashes") or ():
                scene, cab = pair[0], str(pair[1]).lower()
                for f in self.files:
                    if cab and f.lower().startswith(cab):
                        self.scene_of.setdefault(f, scene)

    def _subtree(self, f: str, g, root_tf: int):
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
                    if int(p.get("m_FileID", 0) or 0) or (f, pid) not in self.readers:
                        raise ValueError(f"GameObject {go_pid}: component {pid} is not in the file")
                    keys.append((f, pid))
                    comps[(f, pid)] = go_pid
                for k in keys:
                    if k in self.member:
                        raise ValueError(f"object {k[1]} belongs to two hierarchies")
                members.extend(keys)
                stack.extend(reversed([int(ch["m_PathID"]) for ch in t["m_Children"]]))
        except Exception as e:
            return members, comps, _message(e)
        return members, comps, None

    # ---------------------------------------------------------------- textures and sprites
    def _textures_sprites(self) -> None:
        waiting = defaultdict(list)
        for s in self._of("Sprite"):
            self._need("sprite.png")
            self._begin("sprite.png")
            try:
                plan = self._sprite_plan((s.file, s.path_id))
            except Unsupported as e:
                self._fallback(s, e.code, e.message)
                continue
            except Exception as e:
                self._fallback(s, "generic.error", _message(e))
                continue
            if plan["external"]:
                self._convert(s, "sprite.png", self.conv_sprite_external, plan)
            else:
                waiting[plan["texture"]].append((s, plan))
        export_textures = self._selected("Texture2D")
        for t in self.infos:
            if t.class_name != "Texture2D":
                continue
            sprites = waiting.pop((t.file, t.path_id), ())
            if not sprites and not export_textures:
                continue
            holder: dict = {}
            if export_textures:
                self._convert(t, "tex.png", self.conv_texture, holder)
            else:
                self._decode_into(t, holder)
            for s, plan in sprites:
                self._convert(s, "sprite.png", self.conv_sprite, plan, holder)
            holder.clear()
        for tkey, rest in waiting.items():
            for s, _plan in rest:
                self._fallback(s, "no_data.sprite", f"texture {contract.object_id(*tkey)} is not decoded")

    def _atlas_entry(self, akey: tuple, rd_key) -> dict:
        m = self.atlas_entries.get(akey)
        if m is None:
            at = self._tt(akey)
            m = self.atlas_entries[akey] = [(_canon(k), d) for k, d in at.get("m_RenderDataMap") or ()]
        want = _canon(rd_key)
        for k, d in m:
            if k == want:
                return d
        raise ValueError(f"atlas {contract.object_id(*akey)} has no render data for the sprite's key")

    def _sprite_plan(self, key: tuple) -> dict:
        """Where a sprite's image comes from (UnityPy's get_image_from_sprite): its SpriteAtlas' render data entry
        when it names an atlas (an atlas of another bundle: the sprite.crop stage), else the atlas of its file
        named by its first atlas tag, else its own render data."""
        r = self.readers[key]
        tt = self._tt(key)
        atlas_key = None
        sa = tt.get("m_SpriteAtlas")
        if isinstance(sa, dict) and sa.get("m_PathID"):
            akey = self._target(r, sa)
            if akey is None:
                raise ValueError("m_SpriteAtlas names an unknown file")
            if akey not in self.readers:
                return {"tt": tt, "external": True, "atlasKey": akey}
            atlas_key = akey
        elif tt.get("m_AtlasTags"):
            tag = tt["m_AtlasTags"][0]
            for i in self.infos:
                if i.file == key[0] and i.class_name == "SpriteAtlas" and self._name_of((i.file, i.path_id)) == tag:
                    atlas_key = (i.file, i.path_id)
                    break
        if atlas_key is not None:
            src, owner = self._atlas_entry(atlas_key, tt["m_RenderDataKey"]), self.readers[atlas_key]
        else:
            src, owner = tt["m_RD"], r
        tex = src.get("texture") or {}
        if not tex.get("m_PathID"):
            raise Unsupported("no_data.sprite", "the sprite's render data names no texture")
        tkey = self._texture_key(owner, tex)
        alpha = src.get("alphaTexture") or {}
        akey = self._texture_key(owner, alpha) if alpha.get("m_PathID") else None
        return {"tt": tt, "external": False, "src": src, "owner": owner, "atlasKey": atlas_key, "texture": tkey,
                "alpha": akey}

    def _texture_key(self, owner, pptr: dict) -> tuple:
        k = self._target(owner, pptr)
        if k is None or k not in self.info:
            raise Unsupported("unsupported.sprite.external_texture",
                              f"texture {pptr.get('m_FileID')}:{pptr.get('m_PathID')} is outside the bundle")
        if self.info[k].class_name != "Texture2D":
            raise Unsupported("no_data.sprite", f"the texture reference names a {self.info[k].class_name}")
        return k

    def conv_texture(self, info, holder: dict) -> list:
        from .atoms.texture import ASTC_BLOCKS, format_name, is_hdr_astc
        key = (info.file, info.path_id)
        try:
            tt = self._tt(key)
            inp = self.view.texture_input(info)
        except Exception as e:
            holder["error"] = e
            raise
        fmt, w, h = int(inp.format), int(inp.width), int(inp.height)
        facts = {"width": w, "height": h, "textureFormat": format_name(fmt),
                 "mipCount": int(tt.get("m_MipCount", 1) or 1), "exportedMips": [0]}
        if w and h and is_hdr_astc(fmt):
            holder["error"] = Unsupported("unsupported.texture.format",
                                          f"{format_name(fmt)} (HDR ASTC; the texture is exported as .astc)")
            data = self.fns["astc.container"](inp.data, w, h, ASTC_BLOCKS[fmt])
            return [self._emit(key, "image", data, "astc", "texture.image", "astc", facts)[0]]
        try:
            img = self.fns["texture.decode"](inp)
        except Exception as e:
            holder["error"] = e
            raise
        holder["image"] = img
        facts["mode"] = img.mode
        data = self.fns["png.encode"](img, self.png_level)
        return [self._emit(key, "image", data, "png", "texture.image", "png", facts)[0]]

    def _decode_into(self, info, holder: dict) -> None:
        """The texture's image for its sprites (a task whose `classes` leave Texture2D out)."""
        from .atoms.texture import format_name, is_hdr_astc
        try:
            inp = self.view.texture_input(info)
            if inp.width and inp.height and is_hdr_astc(inp.format):
                raise Unsupported("unsupported.texture.format", f"{format_name(inp.format)} (HDR ASTC)")
            holder["image"] = self.fns["texture.decode"](inp)
        except Exception as e:
            holder["error"] = e

    def _decoded(self, key: tuple):
        return self.fns["texture.decode"](self.view.texture_input(self.info[key]))

    def conv_sprite(self, info, plan: dict, holder: dict) -> list:
        from .atoms import sprite as sprite_atom
        err = holder.get("error")
        if err is not None:
            if isinstance(err, Unsupported):
                raise Unsupported(err.code, f"texture: {err.message}")
            raise RuntimeError(f"texture: {_message(err)}")
        if "image" not in holder:
            raise RuntimeError("texture image not decoded")
        key = (info.file, info.path_id)
        r, tt, src = self.readers[key], plan["tt"], plan["src"]
        alpha = self._decoded(plan["alpha"]) if plan["alpha"] else None
        raw = int(src["settingsRaw"])
        st = sprite_atom.settings(raw)
        mesh = self.fns["mesh.arrays"](self.view.mesh_input(info)) if st.packing_mode == sprite_atom.TIGHT else None
        downscale = float(src.get("downscaleMultiplier", 1.0))
        img = self.fns["sprite.crop"](holder["image"], src["textureRect"], raw, mesh, float(tt["m_PixelsToUnits"]),
                                      alpha, downscale=downscale,
                                      canvas={"rect": tt["m_Rect"], "offset": src["textureRectOffset"],
                                              "pivot": tt["m_Pivot"]})
        data = self.fns["png.encode"](img, self.png_level)
        tex_oid = contract.object_id(*plan["texture"])
        refs = [{"rel": "texture", "object": tex_oid}]
        if plan["atlasKey"] is not None:
            refs.append({"rel": "atlas", "object": contract.object_id(*plan["atlasKey"])})
        image_id = self._emit(key, "image", data, "png", "sprite.image", "png",
                              {"width": img.width, "height": img.height, "mode": img.mode}, refs=refs)[0]
        meta = {"sprite": self.tree(r, tt),
                "image": {"source": "atlas" if plan["atlasKey"] is not None else "renderData",
                          "atlas": None if plan["atlasKey"] is None else self._dref(plan["atlasKey"]),
                          "texture": self._dref(plan["texture"]),
                          "alphaTexture": None if plan["alpha"] is None else self._dref(plan["alpha"]),
                          "textureRect": src["textureRect"], "textureRectOffset": src["textureRectOffset"],
                          "settingsRaw": raw, "packed": st.packed, "packingMode": st.packing_mode,
                          "packingRotation": st.packing_rotation, "downscaleMultiplier": downscale,
                          "width": img.width, "height": img.height}}
        meta_id = self._emit(key, "meta", encode_object(meta), "json", "sprite.meta", "json", {}, refs=refs)[0]
        return [image_id, meta_id]

    def conv_sprite_external(self, info, plan: dict) -> list:
        from .atoms import sprite as sprite_atom
        key = (info.file, info.path_id)
        m = self.fns["mesh.arrays"](self.view.mesh_input(info))
        mesh = sprite_atom.mesh_values(m) if "position" in m.channels else None
        atlas_oid = contract.object_id(*plan["atlasKey"])
        meta = {"object": self._facts(key), "sprite": self.tree(self.readers[key], plan["tt"]),
                "image": {"source": "atlas", "atlas": self._dref(plan["atlasKey"]), "stage": CROP}, "mesh": mesh}
        return [self._emit(key, "meta", encode_object(meta), "json", "sprite.meta", "json",
                           {"crop": CROP, "atlas": atlas_oid}, refs=[{"rel": "atlas", "object": atlas_oid}])[0]]

    # ---------------------------------------------------------------- other converters
    def conv_mesh(self, info) -> list:
        key = (info.file, info.path_id)
        m = self.fns["mesh.arrays"](self.view.mesh_input(info))
        glb = self.fns["gltf.write"]([m])
        facts = {"vertexCount": int(m.vertex_count), "submeshes": len(m.submeshes), "channels": list(m.channels),
                 "skinned": m.skin is not None}
        return [self._emit(key, "mesh", glb, "glb", "mesh.gltf", "glb", facts, issues=m.issues)[0]]

    def conv_text(self, info) -> list:
        key = (info.file, info.path_id)
        s = self._tt(key).get("m_Script", "")
        data = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s or b"")
        container = self.containers.get(key)
        sn = self.fns["sniff"](data, container)
        ext = _container_ext(container) or sn.ext
        return [self._emit(key, "data", data, ext, "text", sn.format, {"size": len(data)})[0]]

    def conv_font(self, info) -> list:
        key = (info.file, info.path_id)
        fd = self._tt(key).get("m_FontData") or b""
        data = bytes(fd)
        if not data:
            raise Unsupported("no_data.font", "the font has no font data")
        sn = self.fns["sniff"](data)
        ext = sn.ext if sn.format in FONT_FORMATS else (_container_ext(self.containers.get(key)) or sn.ext)
        return [self._emit(key, "font", data, ext, "font", sn.format, {"size": len(data)})[0]]

    def conv_material(self, info) -> list:
        key = (info.file, info.path_id)
        doc = self.material(self.readers[key])
        return [self._emit(key, "json", encode_object(doc), "json", "material", "json", {})[0]]

    def conv_shader(self, info) -> list:
        """The parsed form (the bytes `nnnotes shader` writes as <name>.json), every compiled sub-program as stored
        (`program:<platform>/s<S>p<P>_<stage>_<N>`), the variant index and the typetree JSON."""
        from . import shader
        key = (info.file, info.path_id)
        r, tt = self.readers[key], self._tt(key)
        name = (tt.get("m_ParsedForm") or {}).get("m_Name") or f"shader_{info.path_id}"
        text, recs, codes = shader._render(r, tt, name)
        variants, ids = [], []
        for rec, code in zip(recs, codes):
            fn = rec["file"].rsplit("/", 1)[-1]
            stem, ext = fn.rsplit(".", 1)
            role = f"program:{rec['platform']}/{stem}"
            facts = {k: rec[k] for k in ("platform", "subShader", "pass", "stage", "type", "keywords")}
            ids.append(self._emit(key, role, code, ext, "shader.program", ext, facts)[0])
            variants.append({**rec, "role": role})
        platforms = sorted({v["platform"] for v in variants})
        facts = {"name": _label(name), "variants": len(variants), "platforms": platforms}
        ids.append(self._emit(key, "json", text.encode("utf-8"), "json", "shader", "json", facts)[0])
        ids.append(self._emit(key, "index", encode_object({"name": name, "variants": variants}), "json",
                              "shader.index", "json", {"variants": len(variants)})[0])
        ids.append(self._emit(key, "typetree", encode_object(self.tree(r, tt)), "json", "unity.object", "json",
                              {})[0])
        return ids

    def conv_clip(self, info) -> list:
        key = (info.file, info.path_id)
        if self.go_clash:
            raise RuntimeError("GameObject path ids repeat across the bundle's serialized files")
        try:
            doc = self.clip(self.readers[key])
        except NotImplementedError as e:
            if "legacy/compressed" in str(e):
                raise Unsupported("generic.clip.legacy", str(e)) from None
            raise
        return [self._emit(key, "json", encode_object(doc), "json", "animation.clip", "json", {})[0]]

    def conv_controller(self, info) -> list:
        key = (info.file, info.path_id)
        if info.class_name != "AnimatorController":
            raise Unsupported("generic.controller.override", f"{info.class_name} is exported as its typetree")
        doc = self.controller(self.readers[key])
        return [self._emit(key, "json", encode_object(doc), "json", "animator.controller", "json", {})[0]]

    def conv_mono(self, info) -> list:
        key = (info.file, info.path_id)
        tt = self._tt(key)
        entry = self._note_script(key, tt)
        if entry == _NO_SCRIPT:
            raise Unsupported("script.missing", "m_Script is null")
        if entry is None:
            s = tt.get("m_Script") or {}
            raise Unsupported("script.unresolved", f"script {s.get('m_FileID')}:{s.get('m_PathID')} is not in the "
                                                   f"task's script table")
        acb = acb_asset(tt)
        if acb is not None and acb["layout"] == EMBEDDED_ACB:
            if ((tt.get("awb") or {}).get("m_PathID")):
                raise Unsupported("unsupported.cri.awb_external", f"cue sheet {acb['cueSheet']}: its AWB is another "
                                                                  f"object")
            path = f"references/RefIds/{acb['ref']}/data/data"
            self.acb_field = (key, path, {"cueSheet": _label(acb["cueSheet"]), "layout": EMBEDDED_ACB})
        try:
            doc = {"$script": entry, **self.tree(self.readers[key], tt)}
        finally:
            self.acb_field = None
        ids = [self._emit(key, "json", encode_object(doc), "json", "mono.json", "json", {"script": entry})[0]]
        if acb is not None and acb["layout"] == EMBEDDED_ACB and not any(
                a["id"] == contract.artifact_id(info.id, "acb") for a in self.staged):
            raise ValueError(f"cue sheet {acb['cueSheet']}: no ACB bytes in its {EMBEDDED_ACB_IMPL}")
        if acb is not None and acb["layout"] == SPLIT_ACB:
            ids.append(self._split_acb(key, acb))
        return ids

    def _split_acb(self, key: tuple, acb: dict) -> str:
        """The `acb` artifact of a SplitAcbData: its chunk TextAssets (of this bundle) joined and unmasked."""
        from . import cri
        owner, parts = self.readers[key], []
        for c in acb["chunks"]:
            k = self._target(owner, c) if isinstance(c, dict) else None
            if k is None or k not in self.info or self.info[k].class_name != "TextAsset":
                raise ValueError(f"cue sheet {acb['cueSheet']}: chunk {c} is not a TextAsset of the bundle")
            parts.append(cri.text_bytes(self._tt(k).get("m_Script", "")))
        data = cri.split_acb(parts, acb["cueSheet"])
        facts = {"cueSheet": _label(acb["cueSheet"]), "layout": SPLIT_ACB, "chunks": len(parts)}
        return self._emit(key, "acb", data, "acb", ACB_KIND, "acb", facts,
                          refs=[{"rel": "chunk", "object": contract.object_id(*self._target(owner, c))}
                                for c in acb["chunks"]])[0]

    def conv_generic(self, info) -> list:
        key = (info.file, info.path_id)
        doc = self.tree(self.readers[key], self._tt(key))
        return [self._emit(key, "json", encode_object(doc), "json", "unity.object", "json", {})[0]]

    def component(self, o) -> dict:
        key = self._key(o)
        tt = self._tt(key)
        out = {"type": o.type.name, "pathId": key[1]}
        if o.type.name == "MonoBehaviour":
            entry = self._note_script(key, tt)
            out["script"] = entry if entry and entry != _NO_SCRIPT else None
            if entry is None:
                self.unresolved += 1
        if self.has_clips:
            self._register_names(o, tt)
        body = self.tree(o, tt)
        out["fields"] = {k: v for k, v in body.items() if k != "m_GameObject"}
        return out

    def conv_prefab(self, info, root_tf: int, members: list) -> list:
        key = (info.file, info.path_id)
        g = self.graphs[info.file]
        self.unresolved = 0
        nodes, stack = [], [root_tf]
        while stack:
            tf = stack.pop()
            t = g.tf[tf]
            go_pid = int(t["m_GameObject"]["m_PathID"])
            go = g.go[go_pid]
            comps = []
            for c in go["m_Component"]:
                pid = int(c["component"]["m_PathID"] or 0)
                if pid and pid != tf:
                    comps.append(self.component(self.readers[(info.file, pid)]))
            node = {"path": g.path(tf), "name": go["m_Name"], "pathId": go_pid, "transformPathId": tf,
                    "active": bool(go["m_IsActive"]), "layer": go["m_Layer"], "tag": go.get("m_Tag"),
                    "localPosition": t["m_LocalPosition"], "localRotation": t["m_LocalRotation"],
                    "localScale": t["m_LocalScale"]}
            if "m_AnchorMin" in t:
                node["rect"] = {k: t[k] for k in ("m_AnchorMin", "m_AnchorMax", "m_AnchoredPosition", "m_SizeDelta",
                                                  "m_Pivot")}
            node["components"] = comps
            nodes.append(node)
            stack.extend(reversed([int(ch["m_PathID"]) for ch in t["m_Children"]]))
        doc = {"name": nodes[0]["name"]}
        if info.file in self.scene_of:
            doc["scene"] = self.scene_of[info.file]
        doc["nodes"] = nodes
        issues = [("script.unresolved", f"{self.unresolved} MonoBehaviours whose script is not in the task's "
                                        f"script table")] if self.unresolved else []
        return [self._emit(key, "prefab", encode_object(doc), "json", "prefab", "json",
                           {"nodes": len(nodes), "objects": len(members)}, issues=issues)[0]]

    def _prefabs(self) -> None:
        if not self._selected("GameObject"):
            return
        for f, tf_pid, go_pid, members, error in self.roots:
            gkey = (f, go_pid)
            info = self.info.get(gkey)
            if error is not None or info is None or info.class_name != "GameObject":
                why = error or f"transform {tf_pid}: no GameObject"
                for k in members:
                    if k in self.info and self.info[k].id not in self.items:
                        self._fallback(self.info[k], "generic.error", f"hierarchy at transform {tf_pid}: {why}")
                continue
            self.current = gkey
            try:
                ok = self._convert(info, "prefab.json", self.conv_prefab, tf_pid, members)
            finally:
                self.current = None
            if ok:
                within = contract.artifact_id(info.id, "prefab")
                for k in members:
                    i = self.info[k]
                    if i.id != info.id:
                        self.items[i.id] = contract.item(i.id, "contained", within=within, cls=i.class_name)
            else:
                why = self.items[info.id].get("reason") or {"code": "generic.error", "message": ""}
                for k in members:
                    i = self.info[k]
                    if i.id not in self.items:
                        self._fallback(i, why["code"], f"hierarchy of {info.id}: {why['message']}")

    def _loose(self, cls: str, conv: str) -> None:
        """Objects of `cls` in one task-level artifact "<task id>#<conv>" (contained in it)."""
        infos = [i for i in self._of(cls) if i.id not in self.items]
        if not infos:
            return
        self._need(conv)
        self._begin(conv)
        entries, done, failed = {}, [], []
        for i in infos:
            mark = (len(self.staged), len(self.blob_ids))
            key = (i.file, i.path_id)
            try:
                entries[i.id] = self.tree(self.readers[key], self._tt(key))
                done.append(i)
            except Exception as e:
                del self.staged[mark[0]:], self.blob_ids[mark[1]:]
                failed.append((i, e))
        if done:
            if conv == "bundle.json":
                doc = {"files": [{"name": f, "unityVersion": self.versions.get(f),
                                  "externals": list(self.view.externals(f))} for f in self.files],
                       "assetBundles": entries}
                kind = "unity.bundle"
            else:
                doc = {"scripts": entries}
                kind = "unity.scripts"
            aid = self._emit(None, conv, encode_object(doc), "json", kind, "json", {"objects": len(done)},
                             owner=self.task.id)[0]
            for a in self.staged:
                self.emitted.add(a["id"])
            self.artifacts.extend(self.staged)
            self.staged = []
            for i in done:
                self.items[i.id] = contract.item(i.id, "contained", within=aid, cls=i.class_name)
        for i, e in failed:
            if isinstance(e, _ReadError):
                self.items[i.id] = contract.item(i.id, "failed", why=contract.reason("failed.read", str(e)),
                                                 cls=i.class_name)
            else:
                self._fallback(i, "generic.error", _message(e))


@lru_cache(maxsize=None)
def exporter_type():
    """RefExporter combined with export.Exporter (imported here: it loads UnityPy)."""
    from .export import Exporter
    return type("ObjectExporter", (RefExporter, Exporter), {})


# ---------------------------------------------------------------- stages
def _png_params(p: dict | None) -> dict:
    png = dict(p or {})
    unknown = sorted(set(png) - {"level"})
    if unknown:
        raise ValueError(f"unknown png parameters {', '.join(unknown)}")
    level = png.get("level", 6)
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 9:
        raise ValueError(f"png level {level!r}: expected 0-9")
    return {"level": level}


def _check_atoms(task, reader: Impl | None = None) -> None:
    """IncompatibleTask when this installation would run the task with other atoms or converters than it names."""
    for n, v in task.atoms.items():
        if n in CONVERTERS:
            want = converter_id(n)
        elif n == "reader" and reader is not None:
            want = reader.id
        elif n in ATOM_NAMES:
            want = atom_id(n)
        else:
            raise IncompatibleTask(f"task {task.id}: unknown atom or converter {n!r}")
        if v != want:
            raise IncompatibleTask(f"task {task.id}: {n} is {want} here, the task names {v}")


class _Docs:
    """A few JSON documents of the store by content id (the planning reads each census several times)."""

    def __init__(self, size: int = 64):
        self.size, self.docs = size, {}

    def get(self, store, sha: str) -> dict:
        doc = self.docs.get(sha)
        if doc is None:
            doc = contract.loads(store.read(sha))
            if len(self.docs) >= self.size:
                self.docs.pop(next(iter(self.docs)))
            self.docs[sha] = doc
        return doc


class ExportStage(Stage):
    """unity.export: one bundle -> an artifact per object, one item per object (see the module documentation).

    Subjects: the selected bundles (the fact "selected", stable bundle names) and, when a selected bundle holds
    sprites, the bundles of the fact "bundles" (the selection's closure) that hold a SpriteAtlas: a sprite reaches its
    atlas texture through the atlas' bundle, whose export makes the texture's image (sprite.crop reads it). Inputs:
    "bundles" {subject: Input (role "bundle")}, as unity.census. It reads the census of its bundle
    (unity.census:<subject>) for the classes (the converters and atoms of its key) and the MonoScripts its objects
    reference, and the script table (link.scripts:all) for the entries of those: its context
    {"scripts": {object id: "assembly|namespace|class"}}. Parameters: `blobMin` (bytes; byte arrays of this size or
    more are blobs), `png.level` (zlib level of the PNG files). `reader`: another implementation of the reader atom
    (its id enters the keys)."""
    name = EXPORT
    version = 1
    after = (CENSUS, SCRIPTS)
    PARAMS = {"blobMin": 4096, "classes": None, "png": {"level": 6}}
    LAYOUT_ROOT = "_bundles/{subject}"

    def __init__(self, reader: Impl | None = None):
        self.reader = reader
        self._docs = _Docs()

    def __getstate__(self):
        return {"reader": self.reader}

    def __setstate__(self, state):
        self.__init__(state["reader"])

    @property
    def ATOMS(self) -> dict:
        return export_atoms(CLASS_CONVERTERS, self.reader_id())

    def reader_id(self) -> str:
        return self.reader.id if self.reader is not None else atom_id("reader")

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        blob = p.get("blobMin", 4096)
        if not isinstance(blob, int) or isinstance(blob, bool) or blob < 1:
            raise ValueError(f"stage {self.name}: blobMin {blob!r}: expected a positive integer")
        classes = p.get("classes")
        if classes is not None:
            if isinstance(classes, str) or not all(isinstance(c, str) and c for c in classes):
                raise ValueError(f"stage {self.name}: classes {classes!r}: expected a list of class names")
            classes = sorted(set(classes))
        return {"blobMin": blob, "classes": classes, "png": _png_params(p.get("png"))}

    def subjects(self, env) -> list[str]:
        selected = set(env.fact("selected"))
        out = set(selected)
        if any("Sprite" in _census_classes(env, s) for s in sorted(selected)):
            out.update(s for s in env.fact("bundles") if s not in out and "SpriteAtlas" in _census_classes(env, s))
        return sorted(out)

    def inputs(self, subject: str, env) -> list[Input]:
        inp = env.fact("bundles")[subject]
        if inp.role != "bundle":
            raise ValueError(f"{self.name} {subject}: input role {inp.role!r}, expected 'bundle'")
        return [inp]

    def census(self, subject: str, env) -> dict:
        inp = env.input_of(contract.task_id(CENSUS, subject), "census")
        return self._docs.get(env.store, inp.sha256)

    def uses(self, subject: str, env) -> dict:
        """The atoms and converters of the classes the bundle's census lists; with the task's `classes` (env.params
        while the task is described), of those of them only."""
        classes = list(self.census(subject, env)["classes"])
        only = (getattr(env, "params", None) or {}).get("classes")
        if only is not None:
            keep = set(only)
            classes = [c for c in classes if c in keep]
        return export_atoms(classes, self.reader_id())

    def context(self, subject: str, env) -> dict:
        refs = census_mod.script_refs(self.census(subject, env))
        if not refs:
            return {}
        table = self._docs.get(env.store, env.input_of(SCRIPTS_TASK, "scripts").sha256)["scripts"]
        return {"scripts": link.script_context(table, refs)}

    def depends(self, subject: str, env) -> list[str]:
        return [contract.task_id(CENSUS, subject), SCRIPTS_TASK]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the bundle's size, its census' object count and the size of its resource files (least squares over
        a whole catalog: CPU seconds; peak resident bytes of a worker process, which grow with the objects)."""
        size = sum(i.size for i in inputs)
        try:
            doc = self.census(subject, env)
            n = int(doc["objects"])
            res = sum(int(r.get("size") or 0) for r in doc.get("resources") or ())
        except Exception:
            n = res = 0
        return Cost(0.12 + size * 3.3e-7 + n * 8.8e-5 + res * 6.6e-8, (80 << 20) + 80 * size + 17500 * n)

    def run(self, task, store) -> Output:
        reader = self.reader if self.reader is not None else resolve("reader")
        _check_atoms(task, reader)
        view = reader.fn(store.input_bytes(task.input("bundle")))
        fns = {n: resolve(n).fn for n in task.atoms if n in ATOM_NAMES and n != "reader"}
        arts, items = exporter_type()(view, task, store, fns).run_bundle()
        return Output(arts, items)


def _census_classes(env, subject: str) -> dict:
    """{class: objects} of a bundle's census ({} when its census has no result)."""
    try:
        return env.artifact(contract.task_id(CENSUS, subject), "census")["semantics"]["facts"]["classes"]
    except (Pending, KeyError):
        return {}


def _store_input(role: str, record: dict) -> Input:
    c = record["content"]
    return Input(role, c["sha256"], c["size"], None, ({"kind": "store"},))


def _pptr_oid(v) -> str | None:
    """The object id of a {"$ref": {...}} value (None for null or an unresolved file)."""
    if not isinstance(v, dict) or "$ref" not in v or "file" not in v["$ref"]:
        return None
    return contract.object_id(v["$ref"]["file"], v["$ref"]["pathId"])


class SpriteCropStage(Stage):
    """sprite.crop: the images of the sprites whose SpriteAtlas is in another bundle. One task per atlas, subject
    "<unity.export subject of the atlas' bundle>:<atlas pathId>"; inputs: "atlas" (the atlas' JSON), "sprite:<object
    id>" (each sprite's meta), "texture:<object id>" (the image of each atlas texture the sprites use), all artifacts
    of unity.export results. Waits for unity.export tasks that have not run. Artifacts "<sprite object id>#image"."""
    name = CROP
    version = 1
    after = (EXPORT,)
    PARAMS = {"png": {"level": 6}}
    CROP_ATOMS = ("png.encode", "sprite.crop")

    def __init__(self):
        self._memo = None
        self._docs = _Docs(256)

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.__init__()

    @property
    def ATOMS(self) -> dict:
        return {n: atom_id(n) for n in self.CROP_ATOMS}

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        return {"png": _png_params(p.get("png"))}

    @staticmethod
    def _may_hold(env, tid: str) -> bool:
        subject = contract.parse_task_id(tid, EXPORT)[1]
        try:
            classes = env.artifact(contract.task_id(CENSUS, subject), "census")["semantics"]["facts"]["classes"]
        except Exception:
            return True
        return "Sprite" in classes or "SpriteAtlas" in classes

    def index(self, env) -> dict:
        """{subjects: {subject: (atlas object id, [(sprite object id, export task id, meta record)])}, atlases,
        textures, missing: atlas object ids that sprites name but no result holds}."""
        pending = env.pending(EXPORT)
        if pending:
            raise Pending(pending[0])
        tids = env.tasks(EXPORT)
        sig = tuple(env.key(t) for t in tids)
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        atlases, textures, requests = {}, {}, defaultdict(list)
        for tid in tids:
            if not self._may_hold(env, tid):
                continue
            for a in env.result(tid)["artifacts"]:
                owner, role = contract.parse_artifact_id(a["id"])
                sem, obj = a["semantics"], a["provenance"].get("object") or {}
                facts = sem.get("facts") or {}
                if sem["kind"] == "sprite.meta" and facts.get("crop") == CROP:
                    requests[facts["atlas"]].append((owner, tid, a))
                elif role == "json" and obj.get("class") == "SpriteAtlas":
                    atlases[owner] = (tid, a)
                elif role == "image" and sem["kind"] == "texture.image":
                    textures[owner] = (tid, a)
        subjects = {}
        for atlas, reqs in requests.items():
            if atlas in atlases:
                tid = atlases[atlas][0]
                subject = f"{contract.parse_task_id(tid, EXPORT)[1]}:{contract.parse_object_id(atlas)[1]}"
                subjects[subject] = (atlas, sorted(reqs, key=lambda r: r[0]))
        idx = {"subjects": subjects, "atlases": atlases, "textures": textures,
               "missing": sorted(set(requests) - set(atlases))}
        self._memo = (sig, idx)
        return idx

    def subjects(self, env) -> list[str]:
        return sorted(self.index(env)["subjects"])

    def _subject(self, subject: str, env):
        idx = self.index(env)
        if subject not in idx["subjects"]:
            raise ValueError(f"{self.name}: no sprites wait for atlas {subject}")
        return idx, *idx["subjects"][subject]

    def inputs(self, subject: str, env) -> list[Input]:
        idx, atlas, reqs = self._subject(subject, env)
        rec = idx["atlases"][atlas][1]
        ins = [_store_input("atlas", rec)]
        entries = _entries(self._docs.get(env.store, rec["content"]["sha256"]))
        needed = set()
        for owner, _tid, meta in reqs:
            ins.append(_store_input("sprite:" + owner, meta))
            doc = self._docs.get(env.store, meta["content"]["sha256"])
            entry = _entry(entries, doc["sprite"].get("m_RenderDataKey"))
            if entry is not None:
                needed.update(t for t in (_pptr_oid(entry.get("texture")), _pptr_oid(entry.get("alphaTexture")))
                              if t)
        ins += [_store_input("texture:" + t, idx["textures"][t][1]) for t in sorted(needed) if t in idx["textures"]]
        return ins

    def uses(self, subject: str, env) -> dict:
        return dict(self.ATOMS)

    def depends(self, subject: str, env) -> list[str]:
        idx, atlas, reqs = self._subject(subject, env)
        return sorted({idx["atlases"][atlas][0], *(tid for _o, tid, _m in reqs)})

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        tex = sum(i.size for i in inputs if i.role.startswith("texture:"))
        n = sum(1 for i in inputs if i.role.startswith("sprite:"))
        return Cost(0.05 + n * 0.01 + tex * 5e-8, (72 << 20) + 40 * tex)

    def run(self, task, store) -> Output:
        from PIL import Image
        from .atoms import sprite as sprite_atom
        _check_atoms(task)
        crop, encode = resolve("sprite.crop").fn, resolve("png.encode").fn
        level = int(task.params["png"]["level"])
        entries = _entries(contract.loads(store.input_bytes(task.input("atlas"))))
        images = {}

        def image(oid: str):
            if oid not in images:
                try:
                    inp = task.input("texture:" + oid)
                except KeyError:
                    raise Unsupported("no_data.sprite", f"no image of the atlas texture {oid}") from None
                im = Image.open(io.BytesIO(store.input_bytes(inp)))
                im.load()
                images[oid] = im
            return images[oid]

        arts, items = [], []
        for inp in task.inputs:
            if not inp.role.startswith("sprite:"):
                continue
            oid = inp.role[len("sprite:"):]
            meta = contract.loads(store.input_bytes(inp))
            try:
                img = _crop_meta(meta, entries, image, crop, sprite_atom)
                aid = contract.artifact_id(oid, "image")
                arts.append(contract.artifact(aid, store.add(encode(img, level), "png"),
                                              contract.provenance(task, obj=meta.get("object")),
                                              {"kind": "sprite.image", "format": "png",
                                               "facts": {"width": img.width, "height": img.height,
                                                         "mode": img.mode}}))
                items.append(contract.item(oid, "exported", artifacts=[aid], cls="Sprite"))
            except Unsupported as e:
                items.append(contract.item(oid, "unsupported", why=contract.reason(e.code, _label(e.message)),
                                           cls="Sprite"))
            except Exception as e:
                items.append(contract.item(oid, "failed", why=contract.reason("failed.export", _message(e)),
                                           cls="Sprite"))
        return Output(arts, items)


def _entries(atlas_doc: dict) -> list:
    return [(_canon(k), d) for k, d in atlas_doc.get("m_RenderDataMap") or ()]


def _entry(entries: list, key):
    want = _canon(key)
    return next((d for k, d in entries if k == want), None)


def _crop_meta(meta: dict, entries: list, image, crop, sprite_atom):
    """A cross-bundle sprite's image from its meta, its atlas' render data entry and the atlas texture's image."""
    s = meta["sprite"]
    entry = _entry(entries, s.get("m_RenderDataKey"))
    if entry is None:
        raise ValueError("the atlas has no render data for the sprite's key")
    tex = _pptr_oid(entry.get("texture"))
    if tex is None:
        raise Unsupported("no_data.sprite", "the atlas entry names no texture")
    alpha_oid = _pptr_oid(entry.get("alphaTexture"))
    raw = int(entry["settingsRaw"])
    mesh = None
    if sprite_atom.settings(raw).packing_mode == sprite_atom.TIGHT:
        if not meta.get("mesh"):
            raise Unsupported("unsupported.sprite.tight_mask", "tightly packed sprite without a mesh")
        mesh = sprite_atom.mesh_from_values(meta["mesh"])
    return crop(image(tex), entry["textureRect"], raw, mesh, float(s["m_PixelsToUnits"]),
                None if alpha_oid is None else image(alpha_oid),
                downscale=float(entry.get("downscaleMultiplier", 1.0)),
                canvas={"rect": s["m_Rect"], "offset": entry["textureRectOffset"], "pivot": s["m_Pivot"]})


STAGES = (ExportStage, SpriteCropStage)
