"""Mesh (mesh.glb), TextAsset (data) and Font (font). Each also has `json`: its typetree JSON without the bytes its
other artifact holds."""

from __future__ import annotations

from ..atoms import Unsupported
from .objects import BundleObjects, container_ext, encode_object, omit

__all__ = ["FONT_FORMATS", "MESH_DATA", "font", "mesh", "text"]

FONT_FORMATS = ("ttf", "otf", "ttc", "woff", "woff2")
# the fields of a Mesh whose data its glTF holds decoded: vertices, indices, the compressed form
MESH_DATA = ("m_VertexData/m_DataSize", "m_IndexBuffer", "m_CompressedMesh")


def _json(ctx: BundleObjects, key: tuple, tt: dict, *data: str) -> None:
    doc = ctx.tree(ctx.readers[key], omit(tt, *data))
    ctx.emit(key, "json", encode_object(doc), "json", "unity.object", "json", {})


def mesh(ctx: BundleObjects, info) -> None:
    """`mesh`: binary glTF in the mesh's local space, the limits of its conversion as issues; `json` without the
    vertex, index and compressed mesh data (MESH_DATA)."""
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
