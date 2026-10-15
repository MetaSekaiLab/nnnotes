"""Mesh (mesh.glb, arrays), TextAsset (data) and Font (font). Each also has `json`: its typetree JSON without the bytes
its other artifact holds. A Mesh's `arrays` is its vertex channels, submeshes and skin as JSON (`arrays_document`)."""

from __future__ import annotations

from ..atoms import Unsupported
from .objects import BundleObjects, container_ext, encode_object, omit

__all__ = ["CHANNEL_KEYS", "FONT_FORMATS", "MESH_DATA", "arrays_document", "font", "mesh", "text"]

FONT_FORMATS = ("ttf", "otf", "ttc", "woff", "woff2")
# the fields of a Mesh whose data its glTF holds decoded: vertices, indices, the compressed form
MESH_DATA = ("m_VertexData/m_DataSize", "m_IndexBuffer", "m_CompressedMesh")
# (key in the arrays document, channel name of the mesh) of the vertex channels after `positions`
CHANNEL_KEYS = (
    ("normals", "normal"),
    ("tangents", "tangent"),
    ("colors", "color"),
    *((f"uv{i}", f"uv{i}") for i in range(8)),
)


def _json(ctx: BundleObjects, key: tuple, tt: dict, *data: str) -> None:
    doc = ctx.tree(ctx.readers[key], omit(tt, *data))
    ctx.emit(key, "json", encode_object(doc), "json", "unity.object", "json", {})


def _floats(a) -> list:
    return a.astype(float).tolist()


def arrays_document(m) -> dict:
    """The `arrays` document of a MeshArrays (module documentation): `positions` as stored, the other channels as
    floats with their own dimension (null when the mesh has no such channel), `submeshes` (per primitive: its
    topology and indices, null without an index buffer), `joints` and `weights` (null without a skin)."""
    ch = m.channels

    def channel(name):
        c = ch.get(name)
        return None if c is None else _floats(c.as_float())

    if "position" in ch:
        positions = _floats(ch["position"].data)
    elif m.vertex_count:
        raise ValueError(f"mesh {m.name!r}: {m.vertex_count} vertices without a position channel")
    else:
        positions = []  # a mesh without vertices stores no position channel
    doc: dict[str, object] = {"positions": positions}
    doc.update((key, channel(name)) for key, name in CHANNEL_KEYS)
    doc["submeshes"] = [
        {
            "topology": p.topology,
            "indices": None if p.indices is None else p.indices.tolist(),
        }
        for p in m.primitives
    ]
    if m.skin is None:
        doc["joints"] = doc["weights"] = None
    else:
        doc["joints"] = m.skin.joints.astype(int).tolist()
        doc["weights"] = _floats(m.skin.weights.as_float())
    return doc


def mesh(ctx: BundleObjects, info) -> None:
    """`mesh`: binary glTF in the mesh's local space, the limits of its conversion as issues; `arrays`: the document of
    `arrays_document` (canon/1 JSON); `json` without the vertex, index and compressed mesh data (MESH_DATA)."""
    key = (info.file, info.path_id)
    m = ctx.fns["mesh.arrays"](ctx.view.mesh_input(info))
    glb = ctx.fns["gltf.write"]([m])
    facts = {
        "vertexCount": int(m.vertex_count),
        "submeshes": len(m.submeshes),
        "channels": list(m.channels),
        "skinned": m.skin is not None,
    }
    ctx.emit(key, "mesh", glb, "glb", "mesh.gltf", "glb", facts, issues=m.issues)
    ctx.emit(key, "arrays", encode_object(arrays_document(m)), "json", "mesh.arrays", "json", {})
    _json(ctx, key, ctx.tt(key), *MESH_DATA)


def text(ctx: BundleObjects, info) -> None:
    """`data`: the stored bytes, the extension from the container path, else from the content; `json` without
    them (`m_Script`)."""
    key = (info.file, info.path_id)
    tt = ctx.tt(key)
    s = tt.get("m_Script", "")
    data = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s or b"")
    container = ctx.containers.get(key)
    sn = ctx.fns["sniff"](data, container)
    ext = container_ext(container) or sn.ext
    ctx.emit(key, "data", data, ext, "text", sn.format, {"size": len(data)})
    _json(ctx, key, tt, "m_Script")


def font(ctx: BundleObjects, info) -> None:
    """`font`: the font file; `json` without it (`m_FontData`)."""
    key = (info.file, info.path_id)
    tt = ctx.tt(key)
    data = bytes(tt.get("m_FontData") or b"")
    if not data:
        raise Unsupported("no_data.font", "the font has no font data")
    sn = ctx.fns["sniff"](data)
    ext = sn.ext if sn.format in FONT_FORMATS else (container_ext(ctx.containers.get(key)) or sn.ext)
    ctx.emit(key, "font", data, ext, "font", sn.format, {"size": len(data)})
    _json(ctx, key, tt, "m_FontData")
