"""Atom `gltf.write`: binary glTF 2.0 (.glb) of meshes in their local space.

Each mesh becomes one node (no transform) and one glTF mesh with one primitive per non-empty submesh (a mesh without
an index buffer: one POINTS primitive over every vertex). Unity's left-handed space becomes glTF's right-handed one
as in the room export: positions, normals and tangents have z negated (tangent w too, so the bitangent follows),
triangle winding is reversed, texture coordinates have v flipped (1 - v; a normalized integer: max - v).

Attributes: POSITION (float, with min / max), NORMAL, TANGENT, COLOR_0, TEXCOORD_0..7, JOINTS_0 / WEIGHTS_0 (the
skin's four joints and weights). Component types glTF allows are kept (float; normalized unsigned byte / short for
colours, texture coordinates and weights); other stored types are converted to float (half floats exactly;
normalized integers dequantized). Components beyond the ones a semantic takes (a normal's w, a texture coordinate's
z and w) are written as the custom attribute `_<SEMANTIC>_EXTRA` unless they are all zero; a channel no glTF
semantic can hold (BlendWeight / BlendIndices of a skin that is not converted, a tangent with fewer than four
components) as `_<CHANNEL>`. Float attributes carry min / max (NaN components ignored).

Mesh extras (`extras.unity`): the submeshes as stored (topology, firstByte, indexCount, baseVertex, firstVertex,
vertexCount), the stored vertex channel table (channel, stream, offset, format, dimension byte), submeshes left out
because they are empty, and for skinned meshes the bind poses (row-major 4x4, Unity
space), the bone name hashes and the root bone name hash. glTF's skin needs the skeleton's nodes, which a mesh does
not have. The JSON is compact, NaN in extras as {"$float": "nan"}, non-finite floats as 1e999 / -1e999.
"""
from __future__ import annotations

import math
import struct

import numpy as np

from . import Unsupported, estimate
from .mesh import Channel, MeshArrays, require_vertices
from .. import jsonio

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
COMPONENT = {np.dtype("int8"): 5120, np.dtype("uint8"): 5121, np.dtype("int16"): 5122, np.dtype("uint16"): 5123,
             np.dtype("uint32"): 5125, np.dtype("float32"): 5126}
TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}
MODES = {"points": 0, "lines": 1, "line_strip": 3, "triangles": 4}


def _tag_nan(v):
    if isinstance(v, float) and v != v:
        return {"$float": "nan"}
    if isinstance(v, dict):
        return {k: _tag_nan(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_tag_nan(x) for x in v]
    return v


def _floats(a: np.ndarray) -> list:
    return [float(x) for x in a]


class _Builder:
    def __init__(self):
        self.bin = bytearray()
        self.views: list = []
        self.accessors: list = []

    def _view(self, data: bytes, target: int, stride: int | None = None) -> int:
        self.bin.extend(b"\0" * (-len(self.bin) % 4))
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if stride:
            v["byteStride"] = stride
        v["target"] = target
        self.bin.extend(data)
        self.views.append(v)
        return len(self.views) - 1

    def vertex(self, a: np.ndarray, normalized: bool = False, minmax: bool | None = None) -> int:
        """Accessor of per-vertex values (vertices, components); elements padded to 4 bytes (glTF alignment)."""
        a = np.ascontiguousarray(a)
        a = a.astype(a.dtype.newbyteorder("<"))
        count, n = a.shape
        elem = a.dtype.itemsize * n
        stride = -(-elem // 4) * 4
        if stride == elem:
            data = a.tobytes()
            view = self._view(data, ARRAY_BUFFER)
        else:
            padded = np.zeros((count, stride), np.uint8)
            padded[:, :elem] = a.view(np.uint8).reshape(count, elem)
            view = self._view(padded.tobytes(), ARRAY_BUFFER, stride)
        acc = {"bufferView": view, "componentType": COMPONENT[a.dtype.newbyteorder("=")], "count": count,
               "type": TYPES[n]}
        if normalized:
            acc["normalized"] = True
        if minmax if minmax is not None else a.dtype.kind == "f":
            nan = np.isnan(a)
            lo = np.where(nan, np.inf, a).min(0).astype(np.float64)
            hi = np.where(nan, -np.inf, a).max(0).astype(np.float64)
            lo[nan.all(0)] = hi[nan.all(0)] = 0.0
            acc["min"], acc["max"] = _floats(lo), _floats(hi)
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def indices(self, idx: np.ndarray) -> int:
        flat = np.ascontiguousarray(idx, np.int64).reshape(-1)
        dt = "<u2" if flat.max(initial=0) < 0xFFFF else "<u4"
        view = self._view(flat.astype(dt).tobytes(), ELEMENT_ARRAY_BUFFER)
        self.accessors.append({"bufferView": view, "componentType": 5123 if dt == "<u2" else 5125,
                               "count": int(flat.size), "type": "SCALAR"})
        return len(self.accessors) - 1


def _allowed(ch: Channel, unorm_ok: bool) -> tuple[np.ndarray, bool]:
    """(values, normalized) in a component type glTF allows for the semantic: float32, or (when `unorm_ok`) the
    stored normalized unsigned byte / short."""
    a = ch.data
    if unorm_ok and ch.normalized and a.dtype in (np.dtype("uint8"), np.dtype("uint16")):
        return a, True
    return ch.as_float(), False


def _custom(ch: Channel) -> tuple[np.ndarray, bool]:
    """Values of a custom attribute: byte / short types as stored, 32-bit integers as unsigned short when they fit,
    anything else as float32."""
    a = ch.data
    if a.dtype in (np.dtype("int8"), np.dtype("uint8"), np.dtype("int16"), np.dtype("uint16")):
        return a, ch.normalized
    if a.dtype.kind in "iu" and (not a.size or (a.min() >= 0 and a.max() <= 0xFFFF)):
        return a.astype(np.uint16), False
    return ch.as_float(), False


def _extra(b: _Builder, attrs: dict, name: str, ch: Channel, take: int) -> None:
    rest = ch.data[:, take:]
    if rest.size and np.any(rest != 0):
        a, norm = _custom(Channel(rest, ch.normalized))
        attrs[f"_{name}_EXTRA"] = b.vertex(a, norm)


def _flip_v(a: np.ndarray, normalized: bool) -> np.ndarray:
    a = a.copy()
    if normalized:
        a[:, 1] = np.iinfo(a.dtype).max - a[:, 1]
    else:
        a[:, 1] = (1.0 - a[:, 1].astype(np.float64)).astype(np.float32)
    return a


def _attributes(b: _Builder, m: MeshArrays) -> dict:
    attrs: dict = {}
    ch = m.channels["position"]
    pos = ch.as_float()
    if pos.shape[1] < 3:
        pos = np.hstack([pos, np.zeros((len(pos), 3 - pos.shape[1]), np.float32)])
    pos = pos[:, :3].copy()
    pos[:, 2] *= -1
    attrs["POSITION"] = b.vertex(pos, minmax=True)
    _extra(b, attrs, "POSITION", ch, 3)
    ch = m.channels.get("normal")
    if ch is not None:
        if ch.data.shape[1] >= 3:
            n = ch.as_float()[:, :3].copy()
            n[:, 2] *= -1
            attrs["NORMAL"] = b.vertex(n)
            _extra(b, attrs, "NORMAL", ch, 3)
        else:
            attrs["_NORMAL"] = b.vertex(*_custom(ch))
    ch = m.channels.get("tangent")
    if ch is not None:
        t = ch.as_float()
        if t.shape[1] >= 3:
            t = t.copy()
            t[:, 2] *= -1
            if t.shape[1] >= 4:
                t[:, 3] *= -1
        if t.shape[1] == 4:
            attrs["TANGENT"] = b.vertex(t)
        else:
            attrs["_TANGENT"] = b.vertex(t)
    ch = m.channels.get("color")
    if ch is not None:
        if ch.data.shape[1] in (3, 4):
            attrs["COLOR_0"] = b.vertex(*_allowed(ch, True))
        else:
            attrs["_COLOR"] = b.vertex(*_custom(ch))
    for i in range(8):
        ch = m.channels.get(f"uv{i}")
        if ch is None:
            continue
        if ch.data.shape[1] >= 2:
            a, norm = _allowed(Channel(ch.data[:, :2], ch.normalized), True)
            attrs[f"TEXCOORD_{i}"] = b.vertex(_flip_v(a, norm), norm)
            _extra(b, attrs, f"TEXCOORD_{i}", ch, 2)
        else:
            attrs[f"_TEXCOORD_{i}"] = b.vertex(*_custom(ch))
    if m.skin is not None:
        attrs["JOINTS_0"] = b.vertex(m.skin.joints.astype(np.uint16), minmax=False)
        attrs["WEIGHTS_0"] = b.vertex(*_allowed(m.skin.weights, True))
    else:
        for name, key in (("blendWeight", "_BLENDWEIGHT"), ("blendIndices", "_BLENDINDICES")):
            if name in m.channels:
                attrs[key] = b.vertex(*_custom(m.channels[name]))
    return attrs


def _extras(m: MeshArrays, empty: list) -> dict:
    unity: dict = {"submeshes": [{"topology": int(s.topology or 0), "firstByte": int(s.first_byte),
                                  "indexCount": int(s.index_count), "baseVertex": int(s.base_vertex or 0),
                                  "firstVertex": int(s.first_vertex), "vertexCount": int(s.vertex_count)}
                                 for s in m.submeshes]}
    if m.layout:
        unity["channels"] = m.layout
    if empty:
        unity["emptySubmeshes"] = empty
    if len(m.bind_poses):
        unity["bindPoses"] = [[[float(x) for x in row] for row in mat] for mat in m.bind_poses]
        unity["boneNameHashes"] = [int(h) for h in m.bone_name_hashes]
        unity["rootBoneNameHash"] = None if m.root_bone_name_hash is None else int(m.root_bone_name_hash)
    return {"unity": unity}


def write(meshes, *, extras: dict | None = None, generator: str = "nnnotes") -> bytes:
    """GLB bytes of `meshes` (MeshArrays, one node each). `extras` goes into the asset's extras."""
    b = _Builder()
    gl_meshes, nodes = [], []
    for m in meshes:
        require_vertices(m)
        attrs = _attributes(b, m)
        prims, empty = [], []
        for p in m.primitives:
            if p.indices is None:
                prims.append({"attributes": attrs, "mode": MODES["points"]})
                continue
            if not p.indices.size:
                empty.append(p.submesh)
                continue
            idx = p.indices[:, [0, 2, 1]] if p.topology == "triangles" else p.indices
            prims.append({"attributes": attrs, "indices": b.indices(idx), "mode": MODES[p.topology]})
        if not prims:
            raise Unsupported("empty.mesh", f"mesh {m.name!r}: every submesh is empty")
        gl_meshes.append({"name": m.name, "primitives": prims, "extras": _extras(m, empty)})
        nodes.append({"name": m.name, "mesh": len(gl_meshes) - 1})
    asset: dict = {"version": "2.0", "generator": generator}
    if extras:
        asset["extras"] = extras
    doc = {"asset": asset, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
           "meshes": gl_meshes, "accessors": b.accessors, "bufferViews": b.views,
           "buffers": [{"byteLength": len(b.bin)}]}
    js = jsonio.dumps(_tag_nan(doc), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * (-len(js) % 4)
    body = bytes(b.bin) + b"\0" * (-len(b.bin) % 4)
    total = 12 + 8 + len(js) + 8 + len(body)
    return (struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), JSON_CHUNK) + js
            + struct.pack("<II", len(body), BIN_CHUNK) + body)


def read(glb: bytes) -> tuple[dict, bytes]:
    """(JSON document, binary chunk) of GLB bytes."""
    import json
    magic, version, total = struct.unpack_from("<III", glb)
    if magic != GLB_MAGIC or version != 2 or total != len(glb):
        raise ValueError("not a glTF 2.0 binary")
    n, kind = struct.unpack_from("<II", glb, 12)
    if kind != JSON_CHUNK:
        raise ValueError("GLB: first chunk is not JSON")
    doc = json.loads(glb[20:20 + n])
    pos = 20 + n
    body = b""
    if pos < len(glb):
        m, kind = struct.unpack_from("<II", glb, pos)
        if kind != BIN_CHUNK:
            raise ValueError("GLB: second chunk is not BIN")
        body = glb[pos + 8:pos + 8 + m]
    return doc, body


def accessor_array(doc: dict, body: bytes, index: int) -> np.ndarray:
    """The values of accessor `index` as an array (count, components) (flat for SCALAR)."""
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    dt = np.dtype({v: k for k, v in COMPONENT.items()}[acc["componentType"]]).newbyteorder("<")
    n = {v: k for k, v in TYPES.items()}[acc["type"]]
    count, elem = acc["count"], dt.itemsize * n
    stride = view.get("byteStride", elem)
    start = view["byteOffset"] + acc.get("byteOffset", 0)
    raw = np.frombuffer(body, np.uint8, count=stride * (count - 1) + elem if count else 0, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, (count, elem), (stride, 1)) if count else raw.reshape(0, elem)
    a = np.ascontiguousarray(rows).view(dt).reshape(count, n)
    return a[:, 0] if n == 1 else a


def cost(facts: dict) -> dict:
    """facts: vertexCount, indexCount, attributeBytes (per vertex, default 48)."""
    v, i = int(facts.get("vertexCount", 0)), int(facts.get("indexCount", 0))
    size = v * int(facts.get("attributeBytes", 48)) + i * 4
    return estimate(5e-5 + v * 5e-8 + i * 1e-8, size * 3 + math.ceil(size / 4))
