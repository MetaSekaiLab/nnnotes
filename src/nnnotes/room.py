"""Spot background prefab -> binary glTF (.glb).

A spot background is a few hundred textured cards (MeshFilter + MeshRenderer
under a Transform hierarchy). Each mesh is baked into prefab space by walking
the Transform TRS chain, converted from Unity's left-handed space to glTF's
right-handed space (negate Z, reverse winding), and written with its texture(s)
embedded. Geometry is read from the mesh's vertex/index buffers; each submesh
becomes one primitive with the renderer's material of the same index.

Materials are translated from the Unity material's shader and that shader's own
pass render state (culling, blend factors and operations, depth write and test,
colour mask, alpha to mask, queue tags) -- not from names. A render state value
that names a property (URP/Lit's `Blend [_SrcBlend] [_DstBlend]`, `Cull [_Cull]`,
`ZWrite [_ZWrite]`) takes the material's value of that property, else the
shader's default for it, else the value the shader stores (render_state). Each
glTF material records the resolved state in `extras.unityRenderState`.
Unsupported shaders and states raise instead of being approximated. A submesh
whose material slot is empty (null) is not written: nothing says what it would
be drawn with; room.json lists it (`nullMaterialSubmeshes`). A MeshFilter with
no mesh, or a mesh with no triangles, draws nothing; room.json lists the object
(`nullMeshFilters`, `meshesWithoutTriangles`).

The situation's backgroundPosition/Rotation/Scale (spot.json) is not baked in: it
goes on top of this, as the game applies it to the prefab root. Objects that
are inactive in the prefab are kept, flagged `extras.unityActive = false`.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import UnityPy

from .catalog import Catalog
from .jsonio import dumps, write_json
from .unity import SceneGraph, mesh_arrays, texture_image

# UnityEngine.Rendering.BlendMode
BLEND_ONE, BLEND_ZERO, BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA = 1, 0, 5, 10
BLEND_OP_ADD = 0                                 # UnityEngine.Rendering.BlendOp.Add
# UnityEngine.Rendering.CullMode
CULL_OFF, CULL_FRONT, CULL_BACK = 0, 1, 2
# glTF sampler enums
NEAREST, LINEAR, LINEAR_MIPMAP_LINEAR, NEAREST_MIPMAP_NEAREST = 9728, 9729, 9987, 9984
REPEAT, CLAMP, MIRROR = 10497, 33071, 33648
# UnityEngine.TextureWrapMode
WRAP = {0: REPEAT, 1: CLAMP, 2: MIRROR, 3: MIRROR}
UNLIT_SHADERS = ("Unlit/", "Universal Render Pipeline/Unlit")
# render state of a pass (SerializedShaderState) read by room: record key -> path in m_State (render target 0)
STATE_FIELDS = {
    "srcBlend": ("rtBlend0", "srcBlend"), "dstBlend": ("rtBlend0", "destBlend"),
    "srcBlendAlpha": ("rtBlend0", "srcBlendAlpha"), "dstBlendAlpha": ("rtBlend0", "destBlendAlpha"),
    "blendOp": ("rtBlend0", "blendOp"), "blendOpAlpha": ("rtBlend0", "blendOpAlpha"),
    "colorMask": ("rtBlend0", "colMask"), "cull": ("culling",), "zWrite": ("zWrite",), "zTest": ("zTest",),
    "alphaToMask": ("alphaToMask",),
}
NO_PROPERTY = "<noninit>"                        # SerializedShaderFloatValue.name of a fixed value


# ---- material translation -------------------------------------------------
def shader_defaults(shader_tt: dict) -> dict:
    """{property name: default value} of a shader's properties (m_ParsedForm.m_PropInfo, first component)."""
    props = shader_tt["m_ParsedForm"].get("m_PropInfo", {}).get("m_Props", [])
    return {p["m_Name"]: p["m_DefValue[0]"] for p in props if "m_DefValue[0]" in p}


def material_values(mat_tt: dict) -> dict:
    """{property name: value} of a material's int and float properties."""
    sp = mat_tt["m_SavedProperties"]
    return {**dict(sp.get("m_Ints", [])), **dict(sp.get("m_Floats", []))}


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


def render_state(mat_tt: dict, shader_tt: dict) -> dict:
    """The render state (STATE_FIELDS) of the shader's first pass for this material."""
    state, _ = _pass_state(shader_tt)
    values, defaults = material_values(mat_tt), shader_defaults(shader_tt)
    out = {}
    for key, path in STATE_FIELDS.items():
        x = state
        for part in path:
            x = x.get(part, {}) if isinstance(x, dict) else {}
        out[key] = state_value(x, values, defaults) if x != {} else None
    return out


def _pass_state(shader_tt: dict) -> tuple[dict, dict]:
    """(render state of the first pass, merged subshader+pass tags)."""
    ss = shader_tt["m_ParsedForm"]["m_SubShaders"][0]
    ps = ss["m_Passes"][0]
    tags = dict(ss.get("m_Tags", {}).get("tags", []))
    tags.update(dict(ps.get("m_Tags", {}).get("tags", [])))
    return ps["m_State"], tags


def translate_material(mat_tt: dict, shader_tt: dict) -> dict:
    """Unity material + shader -> glTF material fields (texture filled by caller)."""
    shader_name = shader_tt["m_ParsedForm"]["m_Name"]
    _, tags = _pass_state(shader_tt)
    rs = render_state(mat_tt, shader_tt)
    src, dst, cull = rs["srcBlend"], rs["dstBlend"], rs["cull"]
    floats = dict(mat_tt["m_SavedProperties"]["m_Floats"])
    colors = dict(mat_tt["m_SavedProperties"]["m_Colors"])

    unlit = shader_name.startswith(UNLIT_SHADERS)
    if not unlit and shader_name != "Universal Render Pipeline/Lit":
        raise NotImplementedError(f"shader not translated: {shader_name}")

    out: dict = {"name": mat_tt["m_Name"], "extras": {"unityShader": shader_name, "unityRenderState": rs}}
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


def _main_tex_slot(shader_name: str) -> str:
    return "_BaseMap" if shader_name.startswith("Universal Render Pipeline/") else "_MainTex"


def _sampler(tex_tt: dict) -> dict:
    s = tex_tt.get("m_TextureSettings", {})
    mips = tex_tt.get("m_MipCount", 1) > 1
    fm = s.get("m_FilterMode", 1)
    mag = NEAREST if fm == 0 else LINEAR
    if fm == 0:
        mn = NEAREST_MIPMAP_NEAREST if mips else NEAREST
    else:
        mn = LINEAR_MIPMAP_LINEAR if mips else LINEAR
    return {"magFilter": mag, "minFilter": mn,
            "wrapS": WRAP.get(s.get("m_WrapU", 0), REPEAT),
            "wrapT": WRAP.get(s.get("m_WrapV", 0), REPEAT)}


# ---- glTF builder -------------------------------------------------------
class _Glb:
    def __init__(self):
        self.bin = bytearray()
        self.bufferViews, self.accessors, self.meshes, self.nodes = [], [], [], []
        self.materials, self.textures, self.images, self.samplers = [], [], [], []
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

    def accessor_f32(self, arr: np.ndarray, kind: str, minmax=False) -> int:
        a = np.ascontiguousarray(arr, dtype="<f4")
        acc = {"bufferView": self._view(a.tobytes(), 34962), "componentType": 5126,
               "count": int(arr.shape[0]), "type": kind}
        if minmax:
            acc["min"] = a.min(0).tolist()
            acc["max"] = a.max(0).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def accessor_idx(self, idx: np.ndarray) -> int:
        a = np.ascontiguousarray(idx, dtype="<u4")
        self.accessors.append({"bufferView": self._view(a.tobytes(), 34963), "componentType": 5125,
                               "count": int(idx.shape[0]), "type": "SCALAR"})
        return len(self.accessors) - 1

    def texture(self, png: bytes, name: str, sampler: dict) -> int:
        self.images.append({"name": name, "bufferView": self._view(png), "mimeType": "image/png"})
        if sampler not in self.samplers:
            self.samplers.append(sampler)
        self.textures.append({"source": len(self.images) - 1,
                              "sampler": self.samplers.index(sampler)})
        return len(self.textures) - 1

    def material(self, m: dict) -> int:
        self.ext_used.update(m.get("extensions", {}).keys())
        self.materials.append(m)
        return len(self.materials) - 1

    def add_mesh(self, name, pos, nrm, uv, prims, active=True) -> None:
        """prims: [(triangle index array, material index)] -- one per submesh."""
        attrs = {"POSITION": self.accessor_f32(pos, "VEC3", minmax=True),
                 "TEXCOORD_0": self.accessor_f32(uv, "VEC2")}
        if len(nrm):
            attrs["NORMAL"] = self.accessor_f32(nrm, "VEC3")
        primitives = []
        for idx, material in prims:
            p = {"attributes": attrs, "indices": self.accessor_idx(idx), "mode": 4}
            if material is not None:
                p["material"] = material
            primitives.append(p)
        self.meshes.append({"name": name, "primitives": primitives})
        self.nodes.append({"name": name, "mesh": len(self.meshes) - 1,
                           "extras": {"unityActive": active}})

    def write(self, path: Path, extras: dict):
        gltf = {
            "asset": {"version": "2.0", "generator": "nnnotes/room", "extras": extras},
            "scene": 0, "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes, "meshes": self.meshes, "accessors": self.accessors,
            "bufferViews": self.bufferViews, "buffers": [{"byteLength": len(self.bin)}],
            "materials": self.materials, "textures": self.textures,
            "images": self.images, "samplers": self.samplers,
        }
        if self.ext_used:
            gltf["extensionsUsed"] = sorted(self.ext_used)
        js = dumps(gltf, separators=(",", ":")).encode("utf-8")
        js += b" " * (-len(js) % 4)
        self.bin.extend(b"\0" * (-len(self.bin) % 4))
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942)); f.write(self.bin)


# ---- extraction ---------------------------------------------------------
def submesh_primitives(tris: list, mats: list, material_for, on_null) -> list:
    """[(glTF triangle indices, material index)] of a mesh's submeshes: `tris` (per submesh, Unity winding),
    `mats` the renderer's material PPtrs of the same index. An empty submesh gives nothing; a submesh whose material
    slot is empty (path id 0) gives nothing and is reported to `on_null(submesh index)`."""
    prims = []
    for i, t in enumerate(tris):
        if not len(t):
            continue
        if not mats[i].path_id:                  # empty slot: no material to draw the submesh with
            on_null(i)
            continue
        prims.append((t[:, [0, 2, 1]].reshape(-1).astype(np.uint32), material_for(mats[i])))
    return prims


def drawn_meshes(mf_by_go: dict, mr_by_go: dict, graph, on_null_mesh):
    """(transform path id, Mesh, material PPtrs) of each GameObject with a MeshFilter and an enabled MeshRenderer.
    A MeshFilter whose mesh reference is empty (path id 0) draws nothing: its object path goes to
    `on_null_mesh(path)`."""
    for go_pid, mf in mf_by_go.items():
        tf_pid = graph.tf_of_go.get(go_pid)
        mr = mr_by_go.get(go_pid)
        if tf_pid is None or mr is None:
            continue
        if not mr.read_typetree().get("m_Enabled", 1):
            continue
        mesh_ref = mf.read().m_Mesh
        if not mesh_ref.path_id:
            on_null_mesh(graph.path(tf_pid))
            continue
        yield tf_pid, mesh_ref.read(), mr.read().m_Materials


def extract_room(cat: Catalog, bg_key: str, out_path: Path) -> dict:
    env = UnityPy.load(*[str(p) for p in cat.fetch_key(bg_key)])
    graph = SceneGraph(env)
    mf_by_go, mr_by_go = {}, {}
    for o in env.objects:
        if o.type.name == "MeshFilter":
            mf_by_go[o.read_typetree()["m_GameObject"]["m_PathID"]] = o
        elif o.type.name == "MeshRenderer":
            mr_by_go[o.read_typetree()["m_GameObject"]["m_PathID"]] = o

    glb = _Glb()
    tex_cache: dict[int, int] = {}
    mat_cache: dict[int, int] = {}

    def material_for(pptr) -> int:
        if pptr.path_id in mat_cache:
            return mat_cache[pptr.path_id]
        mat = pptr.read()
        mat_tt = mat.object_reader.read_typetree()
        shader_tt = mat.m_Shader.read().object_reader.read_typetree()
        gm = translate_material(mat_tt, shader_tt)
        slot = _main_tex_slot(shader_tt["m_ParsedForm"]["m_Name"])
        for key, env_ in mat.m_SavedProperties.m_TexEnvs:
            if key != slot or not env_.m_Texture.path_id:
                continue
            tid = env_.m_Texture.path_id
            if tid not in tex_cache:
                tex = env_.m_Texture.read()
                buf = io.BytesIO(); texture_image(tex).save(buf, format="PNG")
                tex_cache[tid] = glb.texture(buf.getvalue(), tex.m_Name,
                                             _sampler(tex.object_reader.read_typetree()))
            info = {"index": tex_cache[tid]}
            sx, sy = env_.m_Scale.x, env_.m_Scale.y
            ox, oy = env_.m_Offset.x, env_.m_Offset.y
            if (sx, sy, ox, oy) != (1.0, 1.0, 0.0, 0.0):
                # Unity samples (u*sx+ox, v*sy+oy) with a bottom-left origin;
                # in glTF's top-left texture space that is this transform.
                info["extensions"] = {"KHR_texture_transform":
                                      {"scale": [sx, sy], "offset": [ox, 1.0 - sy - oy]}}
                glb.ext_used.add("KHR_texture_transform")
            gm["pbrMetallicRoughness"]["baseColorTexture"] = info
        mat_cache[pptr.path_id] = glb.material(gm)
        return mat_cache[pptr.path_id]

    n_mesh = 0
    n_inactive = 0
    skipped = []
    null_slots = []                              # submeshes whose material slot is empty
    null_meshes = []                             # objects whose MeshFilter has no mesh
    no_triangles = []                            # meshes with vertices but no triangles
    for tf_pid, mesh, mats in drawn_meshes(mf_by_go, mr_by_go, graph, null_meshes.append):
        arrays = mesh_arrays(mesh)
        if arrays is None:
            skipped.append(mesh.m_Name)          # empty mesh (no vertex data)
            continue
        v, vn, uv0, tris = arrays
        if not any(len(t) for t in tris):        # vertices only (e.g. a placeholder mesh without index data)
            no_triangles.append({"mesh": mesh.m_Name, "path": graph.path(tf_pid)})
            continue
        if len(mats) < len(tris):
            raise NotImplementedError(f"{mesh.m_Name}: {len(tris)} submeshes, {len(mats)} materials")
        m = graph.world(tf_pid)
        p = (np.hstack([v, np.ones((len(v), 1))]) @ m.T)[:, :3]
        p[:, 2] *= -1.0                          # Unity LH -> glTF RH
        if len(vn):
            nw = vn @ np.linalg.inv(m[:3, :3]).T
            nw /= np.maximum(np.linalg.norm(nw, axis=1, keepdims=True), 1e-12)
            nw[:, 2] *= -1.0
        else:
            nw = np.empty((0, 3))
        uv = uv0.copy()
        uv[:, 1] = 1.0 - uv[:, 1]                # Unity UV origin bottom-left -> glTF top-left
        prims = submesh_primitives(tris, mats, material_for, lambda i: null_slots.append(
            {"mesh": mesh.m_Name, "path": graph.path(tf_pid), "submesh": i}))
        if not prims:
            continue
        is_active = graph.active(tf_pid)
        glb.add_mesh(mesh.m_Name, p, nw, uv, prims, active=is_active)
        n_mesh += 1
        n_inactive += 0 if is_active else 1

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    glb.write(out_path, {"backgroundKey": bg_key})
    doc = {
        "backgroundKey": bg_key, "glb": out_path.name, "meshCount": n_mesh,
        "inactiveMeshes": n_inactive,
        "skippedEmptyMeshes": skipped,
        "nullMaterialSubmeshes": null_slots,
        "nullMeshFilters": null_meshes,
        "meshesWithoutTriangles": no_triangles,
        "materials": [{"name": m["name"], "shader": m["extras"]["unityShader"],
                       "alphaMode": m.get("alphaMode", "OPAQUE"),
                       "alphaCutoff": m.get("alphaCutoff"),
                       "doubleSided": m.get("doubleSided", False),
                       "unlit": "KHR_materials_unlit" in m.get("extensions", {}),
                       "renderState": m["extras"]["unityRenderState"]}
                      for m in glb.materials],
        "textures": [im["name"] for im in glb.images],
        "samplers": glb.samplers,
    }
    write_json(out_path.with_suffix(".json"), doc)
    return doc
