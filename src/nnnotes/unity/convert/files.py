"""Mesh (mesh.glb), TextAsset (data) and Font (font)."""

from __future__ import annotations

from ..atoms import Unsupported
from .objects import BundleObjects, container_ext

__all__ = ["FONT_FORMATS", "font", "mesh", "text"]

FONT_FORMATS = ("ttf", "otf", "ttc", "woff", "woff2")


def mesh(ctx: BundleObjects, info) -> None:
    """`mesh`: binary glTF in the mesh's local space; the limits of its conversion as issues."""
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


def text(ctx: BundleObjects, info) -> None:
    """`data`: the stored bytes, the extension from the container path, else from the content."""
    key = (info.file, info.path_id)
    s = ctx.tt(key).get("m_Script", "")
    data = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s or b"")
    container = ctx.containers.get(key)
    sn = ctx.fns["sniff"](data, container)
    ext = container_ext(container) or sn.ext
    ctx.emit(key, "data", data, ext, "text", sn.format, {"size": len(data)})


def font(ctx: BundleObjects, info) -> None:
    """`font`: the font file."""
    key = (info.file, info.path_id)
    data = bytes(ctx.tt(key).get("m_FontData") or b"")
    if not data:
        raise Unsupported("no_data.font", "the font has no font data")
    sn = ctx.fns["sniff"](data)
    ext = sn.ext if sn.format in FONT_FORMATS else (container_ext(ctx.containers.get(key)) or sn.ext)
    ctx.emit(key, "font", data, ext, "font", sn.format, {"size": len(data)})
