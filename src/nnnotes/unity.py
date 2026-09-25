"""UnityPy helpers for the game's bundles: typetree reads, TextAssets, shard JSON, meshes, the
GameObject/Transform scene graph and PPtr references."""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import UnityPy
from UnityPy.classes import PPtr
from UnityPy.helpers import MeshHelper


def load(path: str | Path) -> "UnityPy.environment.Environment":
    return UnityPy.load(str(path))


def load_bytes(data: bytes) -> "UnityPy.environment.Environment":
    return UnityPy.load(io.BytesIO(data))


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
        parts = []
        while tf_pid in self.tf:
            parts.append(self.go.get(self.tf[tf_pid]["m_GameObject"]["m_PathID"], {}).get("m_Name", "?"))
            tf_pid = self.tf[tf_pid]["m_Father"]["m_PathID"]
        return "/".join(reversed(parts))


def script_class(obj) -> str:
    """Class name of a MonoBehaviour (needs the monoscript bundle loaded)."""
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
