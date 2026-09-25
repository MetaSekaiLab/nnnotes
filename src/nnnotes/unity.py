"""UnityPy helpers for the game's bundles: typetree reads, TextAssets, shard JSON, meshes, the
GameObject/Transform scene graph and PPtr references."""
from __future__ import annotations

import hashlib
import io
import json
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import UnityPy
from UnityPy.classes import PPtr
from UnityPy.helpers import MeshHelper
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from . import cache


# ---- typetree blobs ---------------------------------------------------------
# A serialized file lists the typetree of each of its classes as a blob, and UnityPy parses every blob in Python
# (TypeTreeNode.parse_blob) each time a file is loaded; the same class trees recur in almost every bundle. The parse
# is memoized per process by the blob itself: the parse arguments, the blob's length and its BLAKE2b-128 digest. A
# hit returns the tree parsed before, shared: UnityPy and nnnotes only read typetree nodes (nnnotes builds new nodes
# where it needs a cut tree, see _head_node), and one file's objects already share their class's tree.
BLOB_MEMO_MAX = 8192                                      # distinct class trees kept per process (least recent go)
_blobs: OrderedDict = OrderedDict()
_blobs_lock = threading.Lock()
_blob_stats = {"hits": 0, "misses": 0, "installed": False}


def blob_memo_stats() -> dict:
    """{installed, hits, misses, trees}: the typetree blob memo of this process."""
    with _blobs_lock:
        return {**_blob_stats, "trees": len(_blobs)}


def clear_blob_memo() -> None:
    with _blobs_lock:
        _blobs.clear()
        _blob_stats.update(hits=0, misses=0)


def _install_blob_memo() -> bool:
    """Wrap TypeTreeNode.parse_blob (once) with the memo; False (nothing changed) when this UnityPy has not the
    blob layout the wrapper reads: node count, string buffer size, the node records, the string buffer."""
    from UnityPy.helpers import TypeTreeNode as ttn
    node_struct = getattr(ttn, "_get_blob_node_struct", None)
    original = ttn.TypeTreeNode.__dict__.get("parse_blob")
    if node_struct is None or not isinstance(original, classmethod):
        return False
    if getattr(original.__func__, "_nnnotes_memo", False):
        return True
    parse = original.__func__

    def parse_blob(cls, reader, version):
        if not cache.enabled():
            return parse(cls, reader, version)
        start = reader.Position
        count, strings = reader.read_int(), reader.read_int()
        body = reader.read(node_struct(reader.endian, version)[0].size * count + strings)
        end = reader.Position
        key = (cls, reader.endian, version, count, strings, len(body), hashlib.blake2b(body, digest_size=16).digest())
        with _blobs_lock:
            node = _blobs.get(key)
            if node is not None:
                _blobs.move_to_end(key)
                _blob_stats["hits"] += 1
                return node
        reader.Position = start
        node = parse(cls, reader, version)
        if reader.Position != end:                         # another layout: this parse is not memoized
            return node
        with _blobs_lock:
            _blob_stats["misses"] += 1
            _blobs[key] = node
            while len(_blobs) > BLOB_MEMO_MAX:
                _blobs.popitem(last=False)
        return node

    parse_blob._nnnotes_memo = True
    parse_blob._original = parse
    ttn.TypeTreeNode.parse_blob = classmethod(parse_blob)
    _blob_stats["installed"] = True
    return True


_install_blob_memo()


def load(path: str | Path) -> "UnityPy.environment.Environment":
    return UnityPy.load(str(path))


def load_bytes(data: bytes) -> "UnityPy.environment.Environment":
    return UnityPy.load(io.BytesIO(data))


_closures = threading.local()


def load_closure(paths) -> "UnityPy.environment.Environment":
    """UnityPy environment of the bundle files `paths` (a key's closure, in this order), shared by the loads of the
    same files in this thread: the last few closures stay loaded (cache.configure `closures` / `closure_mb`), keyed
    by each file's path, size and modification time. Reading an object gives fresh values every time, so a shared
    environment reads as a new one; per thread because UnityPy's readers are not thread-safe."""
    paths = [str(p) for p in paths]
    limit, budget = cache.settings()["closures"], cache.settings()["closure_bytes"]
    if not cache.enabled() or limit <= 0:
        return UnityPy.load(*paths)
    ids = tuple(cache.file_id(p) for p in paths)
    lru = getattr(_closures, "lru", None)
    if lru is None:
        lru = _closures.lru = OrderedDict()
    hit = lru.get(ids)
    if hit is not None:
        lru.move_to_end(ids)
        return hit[0]
    env = UnityPy.load(*paths)
    size = sum(i[1] for i in ids)
    if size <= budget:
        lru[ids] = (env, size)
        while len(lru) > limit or sum(v[1] for v in lru.values()) > budget:
            lru.popitem(last=False)
    return env


def clear_closures() -> None:
    """Drop this thread's loaded closures."""
    lru = getattr(_closures, "lru", None)
    if lru is not None:
        lru.clear()


def mono_typetrees(env) -> list[dict]:
    """Every MonoBehaviour in the environment, read via TypeTree."""
    out = []
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            try:
                out.append(o.read_typetree())
            except Exception:
                continue
    return out


def first_mono(env, must_have: str | None = None) -> dict | None:
    for tt in mono_typetrees(env):
        if must_have is None or must_have in tt:
            return tt
    return None


def textassets(env) -> dict[str, str]:
    out = {}
    for o in env.objects:
        if o.type.name == "TextAsset":
            d = o.read()
            s = d.m_Script
            out[d.m_Name] = s if isinstance(s, str) else bytes(s).decode("utf-8", "replace")
    return out


def parse_shard(text: str) -> dict:
    """A `-Text/-Sound/...` shard: {_header:[{_name,_type}], _allData:[rows]}.

    Returns {"header": [...], "rows": [...]}. Row keys keep their leading
    underscore + lowercased-first-letter form as serialized (e.g. `_cueName`).
    """
    d = json.loads(text)
    return {"header": d.get("_header", []), "rows": d.get("_allData", [])}


def shard_index(rows: list[dict], id_key: str = "_id") -> dict:
    return {r[id_key]: r for r in rows if id_key in r}


# ---- meshes -------------------------------------------------------------
def mesh_arrays(mesh):
    """(positions, normals, uv0, [triangles per submesh]) straight from the vertex/index buffers."""
    h = MeshHelper.MeshHandler(mesh)
    h.process()
    if not h.m_VertexCount:
        return None
    v = np.asarray(h.m_Vertices, np.float64).reshape(-1, 3)
    n = np.asarray(h.m_Normals, np.float64).reshape(-1, 3) if h.m_Normals else np.empty((0, 3))
    uv = np.asarray(h.m_UV0, np.float64).reshape(-1, 2) if h.m_UV0 else np.zeros((len(v), 2))
    tris = [np.asarray(t, np.int64).reshape(-1, 3) for t in h.get_triangles()]
    return v, n, uv, tris


# ---- scene graph ----------------------------------------------------------
def quat_matrix(q: dict) -> np.ndarray:
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w),     0],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w),     0],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1],
    ], dtype=np.float64)


def trs(pos: dict, rot: dict, scale: dict) -> np.ndarray:
    t = np.eye(4)
    t[:3, 3] = [pos["x"], pos["y"], pos["z"]]
    return t @ quat_matrix(rot) @ np.diag([scale["x"], scale["y"], scale["z"], 1.0])


class SceneGraph:
    """GameObject/Transform hierarchy of an environment, with world matrices."""

    def __init__(self, env):
        self.go = {}                  # GameObject path_id -> typetree
        self.tf = {}                  # Transform path_id -> typetree
        for o in env.objects:
            if o.type.name == "GameObject":
                table = self.go
            elif o.type.name in ("Transform", "RectTransform"):
                table = self.tf
            else:
                continue
            if o.path_id in table:
                raise RuntimeError(f"{o.type.name} path_id {o.path_id} appears in two files")
            table[o.path_id] = o.read_typetree()
        self.tf_of_go = {t["m_GameObject"]["m_PathID"]: p for p, t in self.tf.items()}
        self._world: dict[int, np.ndarray] = {}
        self._paths: dict[int, str] = {}
        self._gos_by_path: dict[str, list[int]] | None = None

    def world(self, tf_pid: int) -> np.ndarray:
        if tf_pid not in self._world:
            t = self.tf[tf_pid]
            m = trs(t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"])
            f = t["m_Father"]["m_PathID"]
            self._world[tf_pid] = (self.world(f) @ m) if f in self.tf else m
        return self._world[tf_pid]

    def world_of_go(self, go_pid: int) -> np.ndarray:
        return self.world(self.tf_of_go[go_pid])

    def active(self, tf_pid: int) -> bool:
        while tf_pid in self.tf:
            if not self.go.get(self.tf[tf_pid]["m_GameObject"]["m_PathID"], {}).get("m_IsActive", 1):
                return False
            tf_pid = self.tf[tf_pid]["m_Father"]["m_PathID"]
        return True

    def path(self, tf_pid: int) -> str:
        p = self._paths.get(tf_pid)
        if p is None:
            parts, t = [], tf_pid
            while t in self.tf:
                parts.append(self.go.get(self.tf[t]["m_GameObject"]["m_PathID"], {}).get("m_Name", "?"))
                t = self.tf[t]["m_Father"]["m_PathID"]
            p = self._paths[tf_pid] = "/".join(reversed(parts))
        return p

    def gos_at_path(self, path: str) -> list[int]:
        """GameObjects whose transform path is `path`, in tf_of_go order."""
        if self._gos_by_path is None:
            index: dict[str, list[int]] = {}
            for go_pid, tf in self.tf_of_go.items():
                index.setdefault(self.path(tf), []).append(go_pid)
            self._gos_by_path = index
        return self._gos_by_path.get(path, [])


_heads: dict[int, tuple] = {}


def _head_node(node: TypeTreeNode) -> TypeTreeNode | None:
    """The object typetree `node` cut after m_Script (the MonoBehaviour header), or None without an m_Script."""
    hit = _heads.get(id(node))
    if hit is not None and hit[0] is node:
        return hit[1]
    names = [c.m_Name for c in node.m_Children]
    head = None
    if "m_Script" in names:
        head = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize, node.m_Version,
                            m_MetaFlag=node.m_MetaFlag, m_Children=node.m_Children[:names.index("m_Script") + 1])
    _heads[id(node)] = (node, head)             # the node is kept alive, so its id is not reused
    return head


def script_class(obj) -> str:
    """Class name of a MonoBehaviour (needs the monoscript bundle loaded). Reads the header up to m_Script with the
    object's own typetree (not its fields); the whole object when that is not possible."""
    try:
        head = _head_node(obj._get_typetree_node())
        if head is not None:
            ms = deref(obj, obj.read_typetree(head, check_read=False)["m_Script"])
            if ms is not None:
                return ms.read().m_ClassName
    except Exception:
        pass
    return obj.read().m_Script.read().m_ClassName


def strip_pptrs(tt: dict) -> dict:
    """Typetree without the Unity header fields (GameObject/Script/Enabled/Name)."""
    return {k: v for k, v in tt.items() if k not in ("m_GameObject", "m_Script", "m_Enabled", "m_Name")}


# ---- references ---------------------------------------------------------
DEFAULT_RESOURCES = "Library/unity default resources"


def is_pptr(v) -> bool:
    return isinstance(v, dict) and v.keys() == {"m_FileID", "m_PathID"}


def external_path(owner, pptr: dict) -> str | None:
    """Path of the serialized file a PPtr points into (None: the owner's own file)."""
    fid = pptr["m_FileID"]
    return None if fid == 0 else owner.assets_file.externals[fid - 1].path


def deref(owner, pptr: dict):
    """ObjectReader that a PPtr read from `owner`'s typetree points at; None if null."""
    if not pptr["m_PathID"]:
        return None
    return PPtr(m_FileID=pptr["m_FileID"], m_PathID=pptr["m_PathID"],
                assetsfile=owner.assets_file).deref()
