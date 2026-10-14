"""One bundle as the converters read it: its objects and their names, typetrees and scripts, and the object JSON
of a typetree (references, byte arrays, strings that are not UTF-8; see the package documentation).

Artifacts go out through `emit(key, role, data, ext, kind, format, facts, *, issues=(), refs=(), owner=None)`, which
the caller gives: it stores the bytes and records the artifact "<owner>#<role>" (owner: the object `key`, else the
task-level name `owner`) and returns (artifact id, content record).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from ...core.canon import encode
from ..ids import object_id

__all__ = [
    "NO_SCRIPT",
    "BundleObjects",
    "Capture",
    "ReadError",
    "container_ext",
    "encode_object",
    "label",
    "message",
    "ref_object_id",
]

NO_SCRIPT = "\0none"  # the script entry of a MonoBehaviour whose m_Script is null
_BYTE_TYPES = frozenset({"UInt8", "SInt8", "char"})
_SCALARS = (int, float, str, bool, type(None))
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
_EXT = re.compile(r"[0-9a-z]+")

Emit = Callable[..., tuple[str, dict]]


def message(e: BaseException) -> str:
    """A deterministic message of an error for a record: no memory addresses, at most 500 characters."""
    return label(_ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:500])


def label(s):
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
        return {label(k): _stored(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_stored(x) for x in v]
    return v


def encode_object(doc) -> bytes:
    """canon/1 bytes of an object document (its own key order)."""
    try:
        return encode(doc, sort_keys=False)
    except UnicodeEncodeError:
        return encode(_stored(doc), sort_keys=False)


def ref_object_id(v) -> str | None:
    """The object id of a {"$ref": {...}} value (None for null or an unresolved file)."""
    if not isinstance(v, dict) or "$ref" not in v or "file" not in v["$ref"]:
        return None
    return object_id(v["$ref"]["file"], v["$ref"]["pathId"])


def container_ext(path: str | None) -> str | None:
    """The extension of a container path's last part (lower case letters and digits), None when it has none."""
    if not path:
        return None
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return None
    ext = last.rsplit(".", 1)[1].lower()
    return ext if _EXT.fullmatch(ext) else None


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _file_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


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


class ReadError(Exception):
    """An object's typetree could not be read."""


@dataclass(frozen=True)
class Capture:
    """A byte array of the object being walked that is an artifact of its own whatever its size: role `role` (also
    its extension and format), kind `kind`, facts `facts`. Its bytes start with `signature` (else the error
    `mismatch`); `missing` is the error when the walk does not reach it."""

    role: str
    kind: str
    signature: bytes
    mismatch: str
    missing: str
    facts: dict = field(default_factory=dict)


class BundleObjects:
    """The objects of one bundle (`view`, a reader atom's BundleView). `fns`: the atom functions by name; `scripts`:
    the script table entries the bundle's objects use ({MonoScript object id: "assembly|namespace|class"});
    `blob_min`: byte arrays of this size or more are blobs; `png_level`: the zlib level of PNG files."""

    def __init__(self, view, fns: dict, *, scripts: dict, blob_min: int, png_level: int, emit: Emit):
        self.view, self.fns, self.emit = view, fns, emit
        self.scripts, self.blob_min, self.png_level = dict(scripts), int(blob_min), int(png_level)
        self.infos = view.objects()
        self.info = {(i.file, i.path_id): i for i in self.infos}
        self.readers = {k: view.reader(i) for k, i in self.info.items()}
        self.files = view.files()
        self.ext_files = {f: [_file_name(p) for p in view.externals(f)] for f in self.files}
        self.versions = {f: view.unity_version(f) for f in self.files}
        self.containers: dict[tuple, str] = {}
        for path, file, pid in view.container():
            k = (file, int(pid))
            if k not in self.containers or path < self.containers[k]:
                self.containers[k] = path
        self.names: dict[tuple, str | None] = {}
        self._mono_scripts: dict[tuple, str | None] = {}
        self._special_memo: dict[int, tuple] = {}
        self._children_memo: dict[int, tuple] = {}
        self._heads: dict[int, tuple] = {}
        self._captures: dict[str, Capture] = {}
        self._local: Callable[[tuple], dict | None] | None = None

    # ---------------------------------------------------------------- identities and names
    @staticmethod
    def key(o) -> tuple:
        return (o.assets_file.name, int(o.path_id))

    def of(self, cls: str) -> list:
        """The objects of class `cls`."""
        return [i for i in self.infos if i.class_name == cls]

    def file_of(self, owner_file: str, fid: int) -> str | None:
        if fid == 0:
            return owner_file
        ext = self.ext_files.get(owner_file, ())
        return ext[fid - 1] if 0 < fid <= len(ext) else None

    def target(self, owner, pptr: dict) -> tuple | None:
        """(file, pathId) a PPtr of `owner` names; None for a null reference or an unknown file index."""
        pid = int(pptr.get("m_PathID", 0) or 0)
        if not pid:
            return None
        file = self.file_of(owner.assets_file.name, int(pptr.get("m_FileID", 0) or 0))
        return None if file is None else (file, pid)

    def _peek(self, r) -> str | None:
        """An object's m_Name, read with its type tree cut after that field."""
        node = r._get_typetree_node()
        hit = self._heads.get(id(node))
        if hit is None or hit[0] is not node:
            names = [c.m_Name for c in node.m_Children]
            cut = None
            if "m_Name" in names:
                from UnityPy.helpers.TypeTreeNode import TypeTreeNode

                cut = TypeTreeNode(
                    node.m_Level,
                    node.m_Type,
                    node.m_Name,
                    node.m_ByteSize,
                    node.m_Version,
                    m_MetaFlag=node.m_MetaFlag,
                    m_Children=node.m_Children[: names.index("m_Name") + 1],
                )
            hit = self._heads[id(node)] = (node, cut)
        if hit[1] is None:
            return None
        name = r.read_typetree(hit[1], check_read=False).get("m_Name")
        return name if isinstance(name, str) else None

    def name_of(self, key: tuple) -> str | None:
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

    def tt(self, key: tuple) -> dict:
        """The object's typetree (ReadError when it cannot be read)."""
        try:
            tt = self.view.typetree(self.info[key])
        except Exception as e:
            raise ReadError(message(e)) from None
        name = tt.get("m_Name")
        self.names[key] = name if isinstance(name, str) else None
        return tt

    def facts(self, key: tuple) -> dict:
        """The object facts of an artifact's provenance: file, path id, class, name, type hash, Unity version,
        container path."""
        i = self.info[key]
        d = {"file": label(i.file), "pathId": i.path_id, "classId": i.class_id, "class": i.class_name}
        name = self.name_of(key)
        if name is not None:
            d["name"] = label(name)
        if i.type_hash:
            d["typeHash"] = i.type_hash
        d["unityVersion"] = self.versions.get(i.file)
        c = self.containers.get(key)
        if c:
            d["container"] = label(c)
        return d

    # ---------------------------------------------------------------- scripts
    def script_entry(self, owner_file: str, pptr) -> str | None:
        """The script table entry of the MonoScript `pptr` (read from an object of `owner_file`) names; None for a
        null reference or a script the table lacks."""
        if not isinstance(pptr, dict) or not pptr.get("m_PathID"):
            return None
        file = self.file_of(owner_file, int(pptr.get("m_FileID", 0) or 0))
        return None if file is None else self.scripts.get(object_id(file, int(pptr["m_PathID"])))

    def mono_script(self, key: tuple, tt: dict) -> str | None:
        """The script entry of a MonoBehaviour: NO_SCRIPT when its m_Script is null, None when the table lacks it."""
        s = tt.get("m_Script")
        entry = NO_SCRIPT if not isinstance(s, dict) or not s.get("m_PathID") else self.script_entry(key[0], s)
        self._mono_scripts[key] = entry
        return entry

    def script_class(self, key: tuple) -> str | None:
        """The class name of a MonoBehaviour from the script table (None when unknown)."""
        if key not in self._mono_scripts:
            self.mono_script(key, self.tt(key))
        e = self._mono_scripts[key]
        return e.rsplit("|", 1)[-1] if e and e != NO_SCRIPT else None

    # ---------------------------------------------------------------- references and values
    def dref(self, key: tuple) -> dict:
        """{"$ref"} of an object, with its class and name when it is in the bundle."""
        file, pid = key
        d = {"file": label(file), "pathId": pid}
        r = self.readers.get(key)
        if r is not None:
            d["class"] = r.type.name
            name = self.name_of(key)
            if name is not None:
                d["name"] = label(name)
        return {"$ref": d}

    def ref(self, owner, pptr: dict):
        """The value of a PPtr read from `owner`'s typetree: null, {"$ref"}, or a prefab's local name of the object
        while a prefab is walked."""
        pid = int(pptr.get("m_PathID", 0) or 0)
        if not pid:
            return None
        fid = int(pptr.get("m_FileID", 0) or 0)
        file = self.file_of(owner.assets_file.name, fid)
        if file is None:
            return {"$ref": {"fileId": fid, "pathId": pid}}
        key = (file, pid)
        if self._local is not None:
            local = self._local(key)
            if local is not None:
                return local
        return self.dref(key)

    def value(self, owner, v):
        """The JSON of a value read from `owner`'s typetree without its type tree: PPtrs as references, bytes as
        {"$hex"}."""
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

    def tree(self, owner, tt: dict, *, captures: dict[str, Capture] | None = None, local=None) -> dict:
        """The object JSON of `owner`'s typetree `tt` (read with owner's type tree). `captures`: {field path:
        Capture}, byte arrays emitted as artifacts of their own (every one must be found); `local(key)`: the local
        name of an object of the prefab being walked (None for any other)."""
        self._captures, self._local = dict(captures or {}), local
        try:
            doc = cast(dict, self._walk(owner, owner._get_typetree_node(), tt, ""))
            left = self._captures
        finally:
            self._captures, self._local = {}, None
        for c in left.values():
            raise ValueError(c.missing)
        return doc

    def _child(self, node, name: str):
        m = self._children_memo.get(id(node))
        if m is None or m[0] is not node:
            m = self._children_memo[id(node)] = (node, {c.m_Name: c for c in node.m_Children})
        return m[1].get(name)

    def _special(self, node) -> bool:
        """Whether values of `node` need rewriting: references, byte arrays, managed references below it."""
        hit = self._special_memo.get(id(node))
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
        self._special_memo[id(node)] = (node, r)
        return r

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
        c = self._captures.pop(path, None)
        if c is not None:
            if not data.startswith(c.signature):
                raise ValueError(f"{path}: {c.mismatch}: {data[:4].hex()}")
            _aid, content = self.emit(self.key(owner), c.role, data, c.role, c.kind, c.role, c.facts)
            return {"$blob": {"sha256": content["sha256"], "size": content["size"], "format": c.role}}
        if len(data) < self.blob_min:
            return {"$hex": data.hex()}
        s = self.fns["sniff"](data)
        _aid, content = self.emit(self.key(owner), "blob:" + path, data, s.ext, "blob", s.format, {"field": path})
        return {"$blob": {"sha256": content["sha256"], "size": content["size"], "format": s.format}}
