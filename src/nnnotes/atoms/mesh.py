"""Atom `mesh.arrays`: every vertex channel, the primitives and the skin of a Mesh (or a Sprite's render mesh).

The input (`MeshInput`, made by a reader) carries the vertex and index buffers as stored. Vertex buffers of Unity
2018.2 and later are decoded here: channel formats (VertexFormat; VertexFormat2017 before 2019) and dimensions come
from the channel table, streams are laid out as Unity lays them (each stream's vertices packed, streams 16-byte
aligned). A channel keeps its stored component type and its dimension (the low four bits of the stored dimension
byte: a normal stored as four half floats has 4 float16 components); UNorm / SNorm channels are marked normalized.
The stored channel table, dimension bytes included, is kept in `MeshArrays.layout`. Meshes of other layouts (older
versions, compressed meshes) come already decoded by the reader (`MeshInput.decoded`, UnityPy's MeshHandler).

Primitives: one per submesh, with the submesh's indices plus its baseVertex. Triangle lists are (n, 3) arrays in
Unity's winding; quads become two triangles each and triangle strips a triangle list (degenerate triangles dropped,
every other triangle's winding swapped), as UnityPy's MeshHandler.get_triangles does; lines, line strips and points
stay what they are. A mesh without an index buffer has one `points` primitive without indices (every vertex).

Skin: the BlendIndices / BlendWeight channels as four joints and four weights per vertex (unused slots 0); bind
poses, bone name hashes and the root bone hash as serialized. What this atom does not convert is listed in
`MeshArrays.issues` as (reason code, message): skins with more than four weights per vertex or inconsistent
channels (`partial.mesh.skin`), blend shapes (`unsupported.mesh.blendshapes`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import Unsupported, estimate

# channel index (Unity 2018 and later) -> name
CHANNELS = ("position", "normal", "tangent", "color", "uv0", "uv1", "uv2", "uv3", "uv4", "uv5", "uv6", "uv7",
            "blendWeight", "blendIndices")
# vertex format -> (numpy dtype, normalized)
FORMATS_2019 = {0: ("<f4", False), 1: ("<f2", False), 2: ("u1", True), 3: ("i1", True), 4: ("<u2", True),
                5: ("<i2", True), 6: ("u1", False), 7: ("i1", False), 8: ("<u2", False), 9: ("<i2", False),
                10: ("<u4", False), 11: ("<i4", False)}
FORMATS_2017 = {0: ("<f4", False), 1: ("<f2", False), 2: ("u1", True), 3: ("u1", True), 4: ("i1", True),
                5: ("<u2", True), 6: ("<i2", True), 7: ("u1", False), 8: ("i1", False), 9: ("<u2", False),
                10: ("<i2", False), 11: ("<u4", False), 12: ("<i4", False)}
TOPOLOGIES = {0: "triangles", 1: "triangle_strip", 2: "quads", 3: "lines", 4: "line_strip", 5: "points"}
RAW_LAYOUT_SINCE = (2018, 2)


@dataclass(frozen=True)
class Submesh:
    first_byte: int
    index_count: int
    topology: int = 0
    base_vertex: int = 0
    first_vertex: int = 0
    vertex_count: int = 0


@dataclass(frozen=True)
class MeshInput:
    """A mesh as stored. `channels`: the channel table ((stream, offset, format, dimension) per channel index),
    `vertex_data` the vertex buffer (the streamed resource data when the mesh streams it); `index_buffer` raw bytes
    of `index_bits`-bit indices, or a sequence of ints when the reader decoded it; `decoded`: {channel name:
    (array (vertices, components), normalized)} for meshes whose buffers the reader decoded; `bind_poses`:
    Matrix4x4f typetrees (e00 .. e33)."""
    name: str
    version: tuple
    vertex_count: int
    channels: tuple = ()
    vertex_data: bytes = b""
    index_buffer: object = b""
    index_bits: int = 16
    submeshes: tuple = ()
    decoded: dict | None = None
    bind_poses: tuple = ()
    bone_name_hashes: tuple = ()
    root_bone_name_hash: int | None = None
    blend_shapes: int = 0
    variable_bone_weights: int = 0


@dataclass
class Channel:
    data: np.ndarray                  # (vertices, components), the stored component type
    normalized: bool = False          # integers standing for [0, 1] (unsigned) or [-1, 1] (signed)

    def as_float(self) -> np.ndarray:
        """The values as float32 (normalized integers dequantized as Unity does: v / max, signed clamped to -1)."""
        a = self.data
        if a.dtype.kind == "f":
            return a.astype(np.float32)
        if not self.normalized:
            return a.astype(np.float32)
        top = float(np.iinfo(a.dtype).max)
        f = a.astype(np.float64) / top
        return (np.maximum(f, -1.0) if a.dtype.kind == "i" else f).astype(np.float32)


@dataclass
class Primitive:
    topology: str                     # triangles | lines | line_strip | points
    indices: np.ndarray | None        # triangles (n, 3), others flat; absolute vertex indices; None: every vertex
    submesh: int | None = None        # submesh index (None: the mesh has no index buffer)


@dataclass
class Skin:
    joints: np.ndarray                # (vertices, 4) uint16
    weights: Channel                  # (vertices, 4)


@dataclass
class MeshArrays:
    name: str
    vertex_count: int
    channels: dict = field(default_factory=dict)       # name -> Channel, in channel order
    primitives: list = field(default_factory=list)
    submeshes: list = field(default_factory=list)      # Submesh per submesh, as stored
    skin: Skin | None = None
    bind_poses: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 4), np.float32))  # row-major, Unity space
    bone_name_hashes: list = field(default_factory=list)
    root_bone_name_hash: int | None = None
    layout: list = field(default_factory=list)         # the stored channel table (vertex buffers decoded here)
    issues: list = field(default_factory=list)         # (reason code, message)


def _formats(version: tuple) -> dict:
    return FORMATS_2019 if tuple(version)[:1] >= (2019,) else FORMATS_2017


def decode_vertex_data(inp: MeshInput) -> dict:
    """{channel name: Channel} of a Unity 2018.2+ vertex buffer."""
    if tuple(inp.version)[:2] < RAW_LAYOUT_SINCE:
        raise ValueError(f"mesh {inp.name!r}: vertex layout of Unity {inp.version[:2]} is decoded by the reader")
    count = int(inp.vertex_count)
    if not count or not inp.channels:
        return {}
    formats = _formats(inp.version)
    chans = []
    for i, (stream, offset, fmt, dim) in enumerate(inp.channels):
        dim = int(dim) & 0xF
        if not dim:
            continue
        if i >= len(CHANNELS):
            raise ValueError(f"mesh {inp.name!r}: channel {i} of {len(inp.channels)}")
        if int(fmt) not in formats:
            raise ValueError(f"mesh {inp.name!r}: channel {CHANNELS[i]}: vertex format {fmt}")
        dtype, norm = formats[int(fmt)]
        chans.append((i, int(stream), int(offset), np.dtype(dtype), norm, dim))
    streams, pos = {}, 0
    for s in range(1 + max(int(c[0]) for c in inp.channels)):
        stride = sum(dt.itemsize * dim for _, st, _, dt, _, dim in chans if st == s)
        streams[s] = (pos, stride)
        pos = (pos + count * stride + 15) & ~15
    raw = np.frombuffer(bytes(inp.vertex_data), np.uint8)
    out = {}
    for i, s, offset, dt, norm, dim in chans:
        start, stride = streams[s]
        end = start + count * stride
        if end > raw.size or offset + dt.itemsize * dim > stride:
            raise ValueError(f"mesh {inp.name!r}: channel {CHANNELS[i]} outside the vertex data "
                             f"({raw.size} bytes, stream {s} ends at {end})")
        cols = raw[start:end].reshape(count, stride)[:, offset:offset + dt.itemsize * dim]
        values = np.ascontiguousarray(cols).view(dt).reshape(count, dim)
        out[CHANNELS[i]] = Channel(values.astype(dt.newbyteorder("=")), norm)
    return out


def _indices(inp: MeshInput) -> np.ndarray:
    ib = inp.index_buffer
    if isinstance(ib, (bytes, bytearray, memoryview)):
        if inp.index_bits not in (16, 32):
            raise ValueError(f"mesh {inp.name!r}: {inp.index_bits}-bit indices")
        return np.frombuffer(bytes(ib), "<u2" if inp.index_bits == 16 else "<u4").astype(np.int64)
    return np.asarray(list(ib), np.int64)


def _strip(idx: np.ndarray) -> np.ndarray:
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = (int(x) for x in idx[i:i + 3])
        if a == b or a == c or b == c:
            continue
        tris.append((b, a, c) if i & 1 else (a, b, c))
    return np.asarray(tris, np.int64).reshape(-1, 3)


def _primitives(inp: MeshInput, count: int) -> list[Primitive]:
    idx = _indices(inp)
    if not idx.size or not inp.submeshes:
        return [Primitive("points", None)]
    per = 2 if inp.index_bits == 16 else 4
    prims = []
    for n, sm in enumerate(inp.submeshes):
        first = int(sm.first_byte) // per
        part = idx[first:first + int(sm.index_count)]
        if len(part) != int(sm.index_count):
            raise ValueError(f"mesh {inp.name!r}: submesh {n} indices {first}..{first + sm.index_count} of "
                             f"{len(idx)}")
        topology = TOPOLOGIES.get(int(sm.topology or 0))
        if topology is None:
            raise ValueError(f"mesh {inp.name!r}: submesh {n} topology {sm.topology}")
        if topology == "triangles":
            if len(part) % 3:
                raise ValueError(f"mesh {inp.name!r}: submesh {n}: {len(part)} triangle indices")
            part = part.reshape(-1, 3)
        elif topology == "triangle_strip":
            part, topology = _strip(part), "triangles"
        elif topology == "quads":
            if len(part) % 4:
                raise ValueError(f"mesh {inp.name!r}: submesh {n}: {len(part)} quad indices")
            q = part.reshape(-1, 4)
            part, topology = np.stack([q[:, [0, 1, 2]], q[:, [0, 2, 3]]], 1).reshape(-1, 3), "triangles"
        part = part + int(sm.base_vertex or 0)
        if part.size and (part.min() < 0 or part.max() >= count):
            raise ValueError(f"mesh {inp.name!r}: submesh {n} index {int(part.max())} of {count} vertices")
        prims.append(Primitive(topology, part, n))
    return prims


def _skin(channels: dict, inp: MeshInput, issues: list) -> Skin | None:
    ix, w = channels.get("blendIndices"), channels.get("blendWeight")
    if inp.variable_bone_weights:
        issues.append(("partial.mesh.skin", f"{inp.variable_bone_weights} variable bone count weights (more than "
                                            f"four bones per vertex) not converted; four per vertex kept"))
    if ix is None:
        if w is not None:
            issues.append(("partial.mesh.skin", "BlendWeight without BlendIndices"))
        return None
    count, k = ix.data.shape
    if w is None:
        if k != 1:
            issues.append(("partial.mesh.skin", f"{k} BlendIndices per vertex without BlendWeight"))
            return None
        weights = Channel(np.zeros((count, 4), np.float32))
        weights.data[:, 0] = 1.0
    else:
        if w.data.shape[1] != k:
            issues.append(("partial.mesh.skin", f"{w.data.shape[1]} BlendWeight / {k} BlendIndices per vertex"))
            return None
        wd = np.zeros((count, 4), w.data.dtype)
        wd[:, :k] = w.data
        weights = Channel(wd, w.normalized)
    if ix.data.dtype.kind == "f" or (ix.data.size and (ix.data.min() < 0 or ix.data.max() > 0xFFFF)):
        issues.append(("partial.mesh.skin", "BlendIndices outside 0..65535"))
        return None
    joints = np.zeros((count, 4), np.uint16)
    joints[:, :k] = ix.data
    return Skin(joints, weights)


def _matrix(m: dict) -> list:
    return [[m[f"e{r}{c}"] for c in range(4)] for r in range(4)]


def arrays(inp: MeshInput) -> MeshArrays:
    count = int(inp.vertex_count)
    if inp.decoded is not None:
        channels = {}
        for name in CHANNELS:
            if name in inp.decoded:
                data, norm = inp.decoded[name]
                a = np.asarray(data)
                a = a.reshape(len(a), -1) if a.ndim == 1 and count and a.size == count else a
                if a.ndim != 2 or a.shape[0] != count:
                    raise ValueError(f"mesh {inp.name!r}: decoded {name} shape {a.shape} for {count} vertices")
                channels[name] = Channel(a, bool(norm))
    else:
        channels = decode_vertex_data(inp)
    issues: list = []
    out = MeshArrays(inp.name, count, channels, _primitives(inp, count) if count else [], list(inp.submeshes),
                     _skin(channels, inp, issues), bone_name_hashes=[int(h) for h in inp.bone_name_hashes],
                     root_bone_name_hash=inp.root_bone_name_hash, issues=issues)
    if inp.decoded is None:
        out.layout = [{"channel": CHANNELS[i] if i < len(CHANNELS) else str(i), "stream": int(st),
                       "offset": int(off), "format": int(fmt), "dimension": int(dim)}
                      for i, (st, off, fmt, dim) in enumerate(inp.channels) if int(dim) & 0xF]
    if inp.bind_poses:
        out.bind_poses = np.asarray([_matrix(m) for m in inp.bind_poses], np.float32).reshape(-1, 4, 4)
    if inp.blend_shapes:
        issues.append(("unsupported.mesh.blendshapes", f"{inp.blend_shapes} blend shapes not converted"))
    return out


def require_vertices(m: MeshArrays) -> None:
    """Unsupported (`empty.mesh`) for a mesh without vertices."""
    if not m.vertex_count or "position" not in m.channels:
        raise Unsupported("empty.mesh", f"mesh {m.name!r} has no vertices")


def cost(facts: dict) -> dict:
    """facts: vertexCount, indexCount, vertexBytes."""
    v, i = int(facts.get("vertexCount", 0)), int(facts.get("indexCount", 0))
    vb = int(facts.get("vertexBytes", v * 48))
    return estimate(2e-5 + v * 1e-7 + i * 2e-8, vb * 4 + i * 16)
