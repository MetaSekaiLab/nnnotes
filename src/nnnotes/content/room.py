"""A spot background (a room) assembled from the objects its prefab reaches: binary glTF (.glb) and its summary.

A spot background is a few hundred textured cards: MeshFilter and MeshRenderer components under a Transform
hierarchy. `room` reads them from the prefab document of the background's GameObject (content.prefabs) and the
objects its components name: each Mesh's `mesh` (binary glTF in the mesh's local space, unity.atoms.gltf), each
Material's `json` and its Shader's parsed form, each Texture2D's `json` and `image`. It never reads a bundle.

Each drawn mesh (a MeshFilter with a mesh beside an enabled MeshRenderer) becomes one node without a transform: its
vertices are baked into the prefab's space through the world matrix of its node (the root's own transform included),
in glTF's right-handed space (z negated), with Unity's texture coordinates flipped (v = 1 - v), as the mesh's glTF
holds them. Each non-empty submesh becomes one primitive with the renderer's material of the same index. A node
inactive in the prefab is kept, flagged `extras.unityActive = false`.

Materials are translated from the Unity material's shader and that shader's own pass render state (culling, blend
factors and operations, depth write and test, colour mask, alpha to mask, queue tags), not from names. A render
state value that names a property (URP/Lit's `Blend [_SrcBlend] [_DstBlend]`, `Cull [_Cull]`, `ZWrite [_ZWrite]`)
takes the material's value of that property, else the shader's default for it, else the value the shader stores
(render_state). Each glTF material records the resolved state in `extras.unityRenderState`. Unsupported shaders and
states raise instead of being approximated. Each Texture2D a material's main slot names is one glTF texture with its
own sampler; textures whose PNG bytes are equal share one glTF image.

What draws nothing is not written and room.json lists it: a submesh whose material slot is empty
(`nullMaterialSubmeshes`), a MeshFilter without a mesh (`nullMeshFilters`), a mesh without vertices or triangles
(`emptyMeshes`).

The situation's backgroundPosition, backgroundRotation and backgroundScale (spot.json) are not baked in: they go on
top of this, as the game applies them to the prefab root.
"""

from __future__ import annotations

import hashlib
import re
import struct

from ..core.jsonio import dumps
from .prefabs import Objects, Prefab, component_class, ref_id, root_game_object

__all__ = ["BACKGROUND_KEY", "room", "room_name", "render_state", "translate_material"]

# a spot background's catalog key: Spot/<place>/Background/<place>
BACKGROUND_KEY = r"Spot/(?P<place>[^/]+)/Background/(?P=place)"
# UnityEngine.Rendering.BlendMode
BLEND_ONE, BLEND_ZERO, BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA = 1, 0, 5, 10
BLEND_OP_ADD = 0  # UnityEngine.Rendering.BlendOp.Add
# UnityEngine.Rendering.CullMode
CULL_OFF, CULL_FRONT, CULL_BACK = 0, 1, 2
# glTF sampler enums
NEAREST, LINEAR, LINEAR_MIPMAP_LINEAR, NEAREST_MIPMAP_NEAREST = 9728, 9729, 9987, 9984
REPEAT, CLAMP, MIRROR = 10497, 33071, 33648
# UnityEngine.TextureWrapMode
WRAP = {0: REPEAT, 1: CLAMP, 2: MIRROR, 3: MIRROR}
UNLIT_SHADERS = ("Unlit/", "Universal Render Pipeline/Unlit")
# render state of a pass (SerializedShaderState) read by room: record key -> path in the pass state (render target 0)
STATE_FIELDS = {
    "srcBlend": ("rtBlend0", "srcBlend"),
    "dstBlend": ("rtBlend0", "destBlend"),
    "srcBlendAlpha": ("rtBlend0", "srcBlendAlpha"),
    "dstBlendAlpha": ("rtBlend0", "destBlendAlpha"),
    "blendOp": ("rtBlend0", "blendOp"),
    "blendOpAlpha": ("rtBlend0", "blendOpAlpha"),
    "colorMask": ("rtBlend0", "colMask"),
    "cull": ("culling",),
    "zWrite": ("zWrite",),
    "zTest": ("zTest",),
    "alphaToMask": ("alphaToMask",),
}
NO_PROPERTY = "<noninit>"  # SerializedShaderFloatValue.name of a fixed value
TRIANGLES = 4  # glTF primitive mode


def room_name(key: str) -> str:
    """The <place> of a background key (ValueError for another key)."""
    m = re.fullmatch(BACKGROUND_KEY, key)
    if m is None:
        raise ValueError(f"{key}: not a spot background key (Spot/<place>/Background/<place>)")
    return m["place"]


# ---- material translation -------------------------------------------------
# A material is its `json` (material.json: material, shader, keywords, floats, colors, ints, textures), a shader its
# parsed form (name, properties, subShaders[].tags, subShaders[].passes[].tags and state).
def shader_defaults(shader: dict) -> dict:
    """{property name: default value} of a shader's properties (the first component)."""
    return {p["m_Name"]: p["m_DefValue[0]"] for p in shader.get("properties") or () if "m_DefValue[0]" in p}


def material_values(material: dict) -> dict:
    """{property name: value} of a material's int and float properties."""
    return {**(material.get("ints") or {}), **(material.get("floats") or {})}


def state_value(x, values: dict, defaults: dict):
    """One pass render state value. A SerializedShaderFloatValue {val, name} whose name is a property (not
    "<noninit>") takes the material's value of it (`values`), else the shader's default (`defaults`), else `val`;
    a fixed value is `val`. Enum values come back as int."""
    if not isinstance(x, dict):
        return x
    name = x.get("name")
    v = x.get("val")
    if name and name != NO_PROPERTY:
        v = values.get(name, defaults.get(name, v))
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _pass_state(shader: dict) -> tuple[dict, dict]:
    """(render state of the first pass, merged subshader and pass tags)."""
    ss = shader["subShaders"][0]
    ps = ss["passes"][0]
    tags = dict((ss.get("tags") or {}).get("tags", []))
    tags.update(dict((ps.get("tags") or {}).get("tags", [])))
    return ps["state"], tags


def render_state(material: dict, shader: dict) -> dict:
    """The render state (STATE_FIELDS) of the shader's first pass for this material."""
    state, _ = _pass_state(shader)
    values, defaults = material_values(material), shader_defaults(shader)
    out = {}
    for key, path in STATE_FIELDS.items():
        x = state
        for part in path:
            x = x.get(part, {}) if isinstance(x, dict) else {}
        out[key] = state_value(x, values, defaults) if x != {} else None
    return out


def translate_material(material: dict, shader: dict) -> dict:
    """A Unity material and its shader -> glTF material fields (the texture is the caller's)."""
    shader_name = shader["name"]
    _, tags = _pass_state(shader)
    rs = render_state(material, shader)
    src, dst, cull = rs["srcBlend"], rs["dstBlend"], rs["cull"]
    floats = material.get("floats") or {}
    colors = material.get("colors") or {}

    unlit = shader_name.startswith(UNLIT_SHADERS)
    if not unlit and shader_name != "Universal Render Pipeline/Lit":
        raise NotImplementedError(f"shader not translated: {shader_name}")

    out: dict = {"name": material["material"], "extras": {"unityShader": shader_name, "unityRenderState": rs}}
    if (src, dst) == (BLEND_ONE, BLEND_ZERO):
        cutout = tags.get("RenderType") == "TransparentCutout" or tags.get("QUEUE") == "AlphaTest"
        if cutout:
            out["alphaMode"] = "MASK"
            out["alphaCutoff"] = floats["_Cutoff"]
    elif (src, dst) == (BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA):
        if rs["blendOp"] not in (None, BLEND_OP_ADD):
            raise NotImplementedError(f"blend op {rs['blendOp']} in {shader_name}")
        out["alphaMode"] = "BLEND"
    else:
        raise NotImplementedError(f"blend {src}/{dst} in {shader_name}")
    if cull == CULL_OFF:
        out["doubleSided"] = True
    elif cull != CULL_BACK:
        raise NotImplementedError(f"cull mode {cull} in {shader_name}")

    pbr: dict = {}
    if unlit:
        out["extensions"] = {"KHR_materials_unlit": {}}
        pbr.update(metallicFactor=0.0, roughnessFactor=1.0)
    else:
        c = colors.get("_BaseColor")
        if c:
            pbr["baseColorFactor"] = [c["r"], c["g"], c["b"], c["a"]]
        pbr["metallicFactor"] = floats.get("_Metallic", 0.0)
        pbr["roughnessFactor"] = 1.0 - floats.get("_Smoothness", 0.5)
    out["pbrMetallicRoughness"] = pbr
    return out


def main_tex_slot(shader_name: str) -> str:
    """The texture property a shader samples as its main texture."""
    return "_BaseMap" if shader_name.startswith("Universal Render Pipeline/") else "_MainTex"


def texture_info(index: int, scale: dict, offset: dict) -> tuple[dict, bool]:
    """(glTF texture info of texture `index`, whether it uses KHR_texture_transform) for a texture slot's scale and
    offset."""
    info: dict = {"index": index}
    sx, sy, ox, oy = scale["x"], scale["y"], offset["x"], offset["y"]
    if (sx, sy, ox, oy) == (1.0, 1.0, 0.0, 0.0):
        return info, False
    # Unity samples (u*sx+ox, v*sy+oy) with a bottom-left origin; in glTF's top-left texture space that is this
    info["extensions"] = {"KHR_texture_transform": {"scale": [sx, sy], "offset": [ox, 1.0 - sy - oy]}}
    return info, True


def sampler(texture: dict) -> dict:
    """The glTF sampler of a Texture2D's settings and mip count (its typetree fields)."""
    s = texture.get("m_TextureSettings", {})
    mips = texture.get("m_MipCount", 1) > 1
    fm = s.get("m_FilterMode", 1)
    mag = NEAREST if fm == 0 else LINEAR
    if fm == 0:
        mn = NEAREST_MIPMAP_NEAREST if mips else NEAREST
    else:
        mn = LINEAR_MIPMAP_LINEAR if mips else LINEAR
    return {
        "magFilter": mag,
        "minFilter": mn,
        "wrapS": WRAP.get(s.get("m_WrapU", 0), REPEAT),
        "wrapT": WRAP.get(s.get("m_WrapV", 0), REPEAT),
    }


# ---- glTF builder -------------------------------------------------------
class Glb:
    """A binary glTF of baked meshes: one node per mesh, one primitive per submesh."""

    def __init__(self):
        self.bin = bytearray()
        self.bufferViews, self.accessors, self.meshes, self.nodes = [], [], [], []
        self.materials, self.textures, self.images, self.samplers = [], [], [], []
        self._image_of: dict[str, int] = {}  # sha256 of the PNG bytes -> image index
        self.ext_used: set[str] = set()

    def _view(self, data: bytes, target=None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin.extend(data)
        self.bufferViews.append(bv)
        return len(self.bufferViews) - 1

    def accessor_f32(self, arr, kind: str, minmax=False) -> int:
        import numpy as np

        a = np.ascontiguousarray(arr, dtype="<f4")
        acc = {
            "bufferView": self._view(a.tobytes(), 34962),
            "componentType": 5126,
            "count": int(arr.shape[0]),
            "type": kind,
        }
        if minmax:
            acc["min"] = a.min(0).tolist()
            acc["max"] = a.max(0).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def accessor_idx(self, idx) -> int:
        import numpy as np

        a = np.ascontiguousarray(idx, dtype="<u4")
        self.accessors.append(
            {
                "bufferView": self._view(a.tobytes(), 34963),
                "componentType": 5125,
                "count": int(idx.shape[0]),
                "type": "SCALAR",
            }
        )
        return len(self.accessors) - 1

    def texture(self, png: bytes, name: str, sampler: dict) -> int:
        """A new texture of `png` with `sampler`. Its image is shared with the textures of the same PNG bytes and
        keeps the name of the first."""
        digest = hashlib.sha256(png).hexdigest()
        if digest not in self._image_of:
            self.images.append({"name": name, "bufferView": self._view(png), "mimeType": "image/png"})
            self._image_of[digest] = len(self.images) - 1
        if sampler not in self.samplers:
            self.samplers.append(sampler)
        self.textures.append({"name": name, "source": self._image_of[digest], "sampler": self.samplers.index(sampler)})
        return len(self.textures) - 1

    def material(self, m: dict) -> int:
        self.ext_used.update(m.get("extensions", {}).keys())
        self.materials.append(m)
        return len(self.materials) - 1

    def add_mesh(self, name, pos, nrm, uv, prims, active=True) -> None:
        """prims: [(triangle index array, material index)] -- one per submesh."""
        attrs = {"POSITION": self.accessor_f32(pos, "VEC3", minmax=True), "TEXCOORD_0": self.accessor_f32(uv, "VEC2")}
        if len(nrm):
            attrs["NORMAL"] = self.accessor_f32(nrm, "VEC3")
        primitives = []
        for idx, material in prims:
            p = {"attributes": attrs, "indices": self.accessor_idx(idx), "mode": TRIANGLES}
            if material is not None:
                p["material"] = material
            primitives.append(p)
        self.meshes.append({"name": name, "primitives": primitives})
        self.nodes.append({"name": name, "mesh": len(self.meshes) - 1, "extras": {"unityActive": active}})

    def bytes(self, extras: dict) -> bytes:
        """glTF requires the arrays it has to be non-empty and a buffer to hold bytes: an empty array is left out,
        and a room without geometry is a scene without nodes and a file without the BIN chunk."""
        scene = {"nodes": list(range(len(self.nodes)))} if self.nodes else {}
        gltf = {
            "asset": {"version": "2.0", "generator": "nnnotes/room", "extras": extras},
            "scene": 0,
            "scenes": [scene],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "accessors": self.accessors,
            "bufferViews": self.bufferViews,
            "buffers": [{"byteLength": len(self.bin)}] if self.bin else [],
            "materials": self.materials,
            "textures": self.textures,
            "images": self.images,
            "samplers": self.samplers,
        }
        gltf = {k: v for k, v in gltf.items() if v != []}
        if self.ext_used:
            gltf["extensionsUsed"] = sorted(self.ext_used)
        js = dumps(gltf, separators=(",", ":")).encode("utf-8")
        js += b" " * (-len(js) % 4)
        body = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        size = 12 + 8 + len(js) + (8 + len(body) if body else 0)
        out = struct.pack("<III", 0x46546C67, 2, size) + struct.pack("<II", len(js), 0x4E4F534A) + js
        if body:
            out += struct.pack("<II", len(body), 0x004E4942) + body
        return out

    def summary(self) -> dict:
        """The materials, textures and samplers as room.json lists them."""
        return {
            "materials": [
                {
                    "name": m["name"],
                    "shader": m["extras"]["unityShader"],
                    "alphaMode": m.get("alphaMode", "OPAQUE"),
                    "alphaCutoff": m.get("alphaCutoff"),
                    "doubleSided": m.get("doubleSided", False),
                    "unlit": "KHR_materials_unlit" in m.get("extensions", {}),
                    "renderState": m["extras"]["unityRenderState"],
                }
                for m in self.materials
            ],
            "textures": [t["name"] for t in self.textures],
            "samplers": self.samplers,
        }


# ---- assembly -------------------------------------------------------------
def _floats(doc: dict, body: bytes, index: int):
    """The values of a glTF accessor as float32, a normalized integer accessor dequantized."""
    import numpy as np

    from ..unity.atoms.gltf import accessor_array

    a = accessor_array(doc, body, index)
    if doc["accessors"][index].get("normalized"):
        return (a.astype(np.float64) / np.iinfo(a.dtype).max).astype(np.float32)
    return a.astype(np.float32)


class _Mesh:
    """A Mesh object's local-space glTF: positions and normals back in Unity's space (z as the game stores it),
    texture coordinates as the glTF holds them (v = 1 - v), and per submesh its triangle indices (glTF winding) or
    None for an empty submesh; no submeshes for a mesh without an index buffer (one points primitive)."""

    def __init__(self, objects: Objects, oid: str):
        from ..unity.atoms.gltf import accessor_array, read

        doc, body = read(objects.bytes(oid, "mesh"))
        (mesh,) = doc["meshes"]
        prims = mesh["primitives"]
        attrs = prims[0]["attributes"]
        self.name = mesh.get("name") or objects.info(oid).get("name") or oid
        self.pos = _floats(doc, body, attrs["POSITION"])[:, :3].copy()
        self.pos[:, 2] *= -1.0
        self.nrm = _floats(doc, body, attrs["NORMAL"])[:, :3].copy() if "NORMAL" in attrs else None
        if self.nrm is not None:
            self.nrm[:, 2] *= -1.0
        self.uv = _floats(doc, body, attrs["TEXCOORD_0"])[:, :2] if "TEXCOORD_0" in attrs else None
        unity = (mesh.get("extras") or {}).get("unity") or {}
        empty = set(unity.get("emptySubmeshes") or ())
        count = len(unity.get("submeshes") or ())
        self.submeshes: list = []
        if any("indices" not in p for p in prims):
            return
        it = iter(prims)
        for i in range(count):
            if i in empty:
                self.submeshes.append(None)
                continue
            p = next(it)
            if p.get("mode", TRIANGLES) != TRIANGLES or "indices" not in p:
                raise NotImplementedError(f"mesh {self.name!r}: submesh {i} is not a triangle list")
            self.submeshes.append(accessor_array(doc, body, p["indices"]).reshape(-1))
        if next(it, None) is not None:
            raise ValueError(f"mesh {self.name!r}: more primitives than non-empty submeshes")


def room(objects: Objects, key: str) -> tuple[bytes, dict]:
    """(room.glb, room.json) of the background `key` (module documentation)."""
    import numpy as np

    prefab = Prefab(objects.json(root_game_object(objects, key)))
    glb = Glb()
    textures: dict[str, int] = {}  # Texture2D object id -> glTF texture
    materials: dict[str, int] = {}  # Material object id -> glTF material

    def material_for(oid: str) -> int:
        if oid in materials:
            return materials[oid]
        mat = objects.json(oid)
        sid = ref_id(mat["shader"])
        if sid is None or sid not in objects:
            raise ValueError(f"{key}: material {mat['material']!r} has no shader in the object table")
        shader = objects.json(sid)
        gm = translate_material(mat, shader)
        slot = (mat.get("textures") or {}).get(main_tex_slot(shader["name"]))
        tid = ref_id(slot["texture"]) if slot else None
        if slot and tid is not None:
            if tid not in textures:
                tex = objects.json(tid)
                textures[tid] = glb.texture(objects.bytes(tid, "image"), tex["m_Name"], sampler(tex))
            info, transform = texture_info(textures[tid], slot["scale"], slot["offset"])
            if transform:
                glb.ext_used.add("KHR_texture_transform")
            gm["pbrMetallicRoughness"]["baseColorTexture"] = info
        materials[oid] = glb.material(gm)
        return materials[oid]

    counts = {"meshes": 0, "inactive": 0}
    null_slots, null_meshes, empty, nodes = [], [], [], []
    for i, node in enumerate(prefab.nodes):
        comps = {component_class(c): c["fields"] for c in node["components"]}
        mf, mr = comps.get("MeshFilter"), comps.get("MeshRenderer")
        if mf is None or mr is None or not mr.get("m_Enabled", 1):
            continue
        path = node["path"]
        mesh_id = ref_id(mf.get("m_Mesh"))
        if mesh_id is None:
            null_meshes.append(path)
            continue
        info = objects.info(mesh_id)
        if "mesh" not in (info.get("files") or {}):
            if info.get("reason") != "empty.mesh":
                raise ValueError(f"{key}: {path}: mesh {info.get('name')!r} was not converted ({info.get('reason')})")
            empty.append({"mesh": info.get("name"), "path": path})
            continue
        mesh = _Mesh(objects, mesh_id)
        if not any(tri is not None and len(tri) for tri in mesh.submeshes):
            empty.append({"mesh": mesh.name, "path": path})
            continue
        mats = mr.get("m_Materials") or []
        if len(mats) < len(mesh.submeshes):
            raise NotImplementedError(f"{mesh.name}: {len(mesh.submeshes)} submeshes, {len(mats)} materials")
        prims = []
        for s, tri in enumerate(mesh.submeshes):
            if tri is None or not len(tri):
                continue
            mid = ref_id(mats[s])
            if mid is None:  # empty slot: no material to draw the submesh with
                null_slots.append({"mesh": mesh.name, "path": path, "submesh": s})
                continue
            prims.append((tri.astype(np.uint32), material_for(mid)))
        if not prims:
            continue
        m = prefab.world(i)
        p = (np.hstack([mesh.pos, np.ones((len(mesh.pos), 1))]) @ m.T)[:, :3]
        p[:, 2] *= -1.0  # Unity LH -> glTF RH
        if mesh.nrm is not None:
            nw = mesh.nrm @ np.linalg.inv(m[:3, :3]).T
            nw /= np.maximum(np.linalg.norm(nw, axis=1, keepdims=True), 1e-12)
            nw[:, 2] *= -1.0
        else:
            nw = np.empty((0, 3))
        uv = mesh.uv if mesh.uv is not None else np.zeros((len(mesh.pos), 2), np.float32)
        active = prefab.active(i)
        glb.add_mesh(mesh.name, p, nw, uv, prims, active=active)
        nodes.append({"path": path, "mesh": mesh_id})
        counts["meshes"] += 1
        counts["inactive"] += 0 if active else 1

    doc = {
        "backgroundKey": key,
        "glb": "room.glb",
        "meshCount": counts["meshes"],
        "inactiveMeshes": counts["inactive"],
        "nullMaterialSubmeshes": null_slots,
        "nullMeshFilters": null_meshes,
        "emptyMeshes": empty,
        "nodes": nodes,
        "materialIds": list(materials),
        **glb.summary(),
    }
    return glb.bytes({"backgroundKey": key}), doc
