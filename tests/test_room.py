"""room: material translation from the pass render state (property-driven values), empty material slots, and the
glTF textures, images and materials of a bundle whose serialized files reuse path ids (synthetic shader / material
typetrees and objects)."""
import copy
import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from UnityPy.classes import PPtr as UnityPPtr

from fakeunity import F32, POSITION, UV0, mesh_tt, submesh, unitypy_mesh
from nnnotes import room
from nnnotes.unity import DEFAULT_RESOURCES

FIXED = "<noninit>"


def fv(val, name=FIXED):
    return {"val": val, "name": name}


def shader(name="Universal Render Pipeline/Lit", props=None, tags=(), **state):
    """A shader typetree: one subshader, one pass with render target 0 and the other states given (defaults:
    opaque, back-face culling, depth write on, LEqual, all colour channels)."""
    rt = {"srcBlend": fv(1.0), "destBlend": fv(0.0), "srcBlendAlpha": fv(1.0), "destBlendAlpha": fv(0.0),
          "blendOp": fv(0.0), "blendOpAlpha": fv(0.0), "colMask": fv(15.0)}
    st = {"culling": fv(2.0), "zWrite": fv(1.0), "zTest": fv(4.0), "alphaToMask": fv(0.0)}
    for k, v in state.items():
        (rt if k in rt else st)[k] = v
    return {"m_ParsedForm": {
        "m_Name": name,
        "m_PropInfo": {"m_Props": [{"m_Name": n, "m_Type": 2, "m_DefValue[0]": d, "m_DefValue[1]": 0.0,
                                    "m_DefValue[2]": 0.0, "m_DefValue[3]": 0.0} for n, d in (props or {}).items()]},
        "m_SubShaders": [{"m_Tags": {"tags": list(tags)},
                          "m_Passes": [{"m_State": {"rtBlend0": rt, **st}, "m_Tags": {"tags": []}}]}]}}


def material(floats=None, ints=None, name="m"):
    return {"m_Name": name, "m_SavedProperties": {"m_TexEnvs": [], "m_Ints": list((ints or {}).items()),
                                                  "m_Floats": list((floats or {}).items()), "m_Colors": []}}


LIT_BLEND = dict(srcBlend=fv(0.0, "_SrcBlend"), destBlend=fv(0.0, "_DstBlend"), culling=fv(0.0, "_Cull"),
                 zWrite=fv(0.0, "_ZWrite"))
LIT_PROPS = {"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 2.0, "_ZWrite": 1.0}


def test_property_driven_blend_takes_the_material_value():
    sh = shader(props=LIT_PROPS, **LIT_BLEND)
    opaque = room.translate_material(material({"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 2.0}), sh)
    assert "alphaMode" not in opaque and "doubleSided" not in opaque
    assert opaque["extras"]["unityRenderState"]["srcBlend"] == 1 and opaque["extras"]["unityRenderState"]["zWrite"] == 1
    blended = room.translate_material(material({"_SrcBlend": 5.0, "_DstBlend": 10.0, "_Cull": 0.0, "_ZWrite": 0.0}), sh)
    assert blended["alphaMode"] == "BLEND" and blended["doubleSided"] is True
    assert blended["extras"]["unityRenderState"]["zWrite"] == 0
    with pytest.raises(NotImplementedError, match="cull mode 1"):
        room.translate_material(material({"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 1.0}), sh)
    with pytest.raises(NotImplementedError, match="blend 1/1"):
        room.translate_material(material({"_SrcBlend": 1.0, "_DstBlend": 1.0}), sh)


def test_missing_material_value_falls_back_to_the_shader_default_then_the_stored_value():
    sh = shader(props=LIT_PROPS, **LIT_BLEND)
    rs = room.render_state(material({}), sh)                   # the material has none of the properties
    assert (rs["srcBlend"], rs["dstBlend"], rs["cull"], rs["zWrite"]) == (1, 0, 2, 1)
    rs = room.render_state(material({}), shader(**LIT_BLEND))  # no shader default either: the stored value
    assert (rs["srcBlend"], rs["dstBlend"], rs["cull"], rs["zWrite"]) == (0, 0, 0, 0)
    # a fixed value ignores a material property of the same name
    rs = room.render_state(material({"culling": 0.0, "_Cull": 0.0}), shader(culling=fv(2.0)))
    assert rs["cull"] == 2


@pytest.mark.parametrize("key, field", [
    ("srcBlend", "srcBlend"), ("dstBlend", "destBlend"), ("srcBlendAlpha", "srcBlendAlpha"),
    ("dstBlendAlpha", "destBlendAlpha"), ("blendOp", "blendOp"), ("blendOpAlpha", "blendOpAlpha"),
    ("colorMask", "colMask"), ("cull", "culling"), ("zWrite", "zWrite"), ("zTest", "zTest"),
    ("alphaToMask", "alphaToMask"),
])
def test_every_state_field_resolves_through_its_property(key, field):
    sh = shader(props={"_P": 3.0}, **{field: fv(7.0, "_P")})
    assert room.render_state(material({"_P": 6.0}), sh)[key] == 6          # the material's float
    assert room.render_state(material(ints={"_P": 5}), sh)[key] == 5        # or int
    assert room.render_state(material(), sh)[key] == 3                      # the shader's default
    assert room.render_state(material(), shader(**{field: fv(7.0, "_P")}))[key] == 7   # the stored value
    assert room.render_state(material({"_P": 6.0}), shader(**{field: fv(7.0)}))[key] == 7  # fixed: stored value


def test_blend_op_other_than_add_is_not_approximated():
    sh = shader(name="Unlit/Transparent", srcBlend=fv(5.0), destBlend=fv(10.0), blendOp=fv(2.0, "_BlendOp"))
    with pytest.raises(NotImplementedError, match="blend op 2"):
        room.translate_material(material(), sh)
    assert room.translate_material(material({"_BlendOp": 0.0}), sh)["alphaMode"] == "BLEND"


def test_fixed_state_translation_is_unchanged():
    sh = shader(name="Unlit/Transparent Cutout", tags=[("RenderType", "TransparentCutout")])
    cutout = room.translate_material(material({"_Cutoff": 0.5}), sh)
    assert cutout["alphaMode"] == "MASK" and cutout["alphaCutoff"] == 0.5
    assert cutout["extensions"] == {"KHR_materials_unlit": {}}
    rs = cutout["extras"]["unityRenderState"]
    assert rs == {"srcBlend": 1, "dstBlend": 0, "srcBlendAlpha": 1, "dstBlendAlpha": 0, "blendOp": 0,
                  "blendOpAlpha": 0, "colorMask": 15, "cull": 2, "zWrite": 1, "zTest": 4, "alphaToMask": 0}
    with pytest.raises(NotImplementedError, match="shader not translated"):
        room.translate_material(material(), shader(name="Custom/Other"))


class PPtr:
    def __init__(self, path_id):
        self.path_id = path_id


def test_empty_material_slots_are_skipped_and_reported():
    tris = [np.array([[0, 1, 2]]), np.zeros((0, 3), int), np.array([[2, 3, 0], [0, 1, 3]])]
    mats = [PPtr(0), PPtr(11), PPtr(12)]
    nulls, used = [], []

    def material_for(p):
        used.append(p.path_id)
        return p.path_id * 10

    prims = room.submesh_primitives(tris, mats, material_for, nulls.append)
    assert nulls == [0] and used == [12]                   # the empty submesh 1 gives nothing, silently
    assert len(prims) == 1 and prims[0][1] == 120
    assert prims[0][0].tolist() == [2, 0, 3, 0, 3, 1]      # winding reversed for glTF
    assert room.submesh_primitives(tris[:1], mats[:1], material_for, nulls.append) == [] and nulls == [0, 0]


class Obj:
    """A component: its typetree and the object UnityPy reads for it."""
    def __init__(self, tt, **fields):
        self.tt, self.fields = tt, fields

    def read_typetree(self):
        return self.tt

    def read(self):
        return type("Read", (), self.fields)()


class Ref(PPtr):
    def __init__(self, path_id, target=None):
        super().__init__(path_id)
        self.target = target

    def read(self):
        assert self.path_id, "an empty reference is not read"
        return self.target


class Graph:
    tf_of_go = {1: 11, 2: 12, 3: 13, 4: 14}

    def path(self, tf_pid):
        return f"Room/obj{tf_pid}"


def test_a_mesh_filter_without_a_mesh_is_skipped_and_reported():
    mesh = object()
    mfs = {("f", 1): Obj({}, m_Mesh=Ref(21, mesh)), ("f", 2): Obj({}, m_Mesh=Ref(0)), ("f", 3): Obj({}, m_Mesh=Ref(0)),
           ("f", 4): Obj({}, m_Mesh=Ref(24, mesh))}
    mrs = {("f", 1): Obj({"m_Enabled": 1}, m_Materials=["m1"]), ("f", 2): Obj({"m_Enabled": 1}, m_Materials=[]),
           ("f", 3): Obj({"m_Enabled": 0}, m_Materials=[]),         # 3: disabled renderer, 4: no renderer
           ("g", 4): Obj({"m_Enabled": 1}, m_Materials=["m4"])}    # another file's GameObject 4
    nulls = []
    drawn = list(room.drawn_meshes(mfs, mrs, Graph(), nulls.append))
    assert drawn == [(11, mesh, ["m1"])]
    assert nulls == ["Room/obj12"]                                  # the disabled one draws nothing either way


def test_extract_room_lists_what_draws_nothing(tmp_path, monkeypatch):
    """A mesh without triangles, a MeshFilter without a mesh and a submesh without a material are not written but
    listed in room.json."""
    class Mesh:
        def __init__(self, name):
            self.m_Name = name

    class Cat:
        def fetch_key(self, key):
            return []

    class SceneGraphStub(Graph):
        def __init__(self, env):
            pass

        def world(self, tf_pid):
            return np.eye(4)

        def active(self, tf_pid):
            return True

    tri = np.array([[0, 1, 2]])
    arrays = {"quad": (np.zeros((3, 3)), np.empty((0, 3)), np.zeros((3, 2)), [tri]),
              "placeholder": (np.zeros((4, 3)), np.empty((0, 3)), np.zeros((4, 2)), [np.zeros((0, 3), int)]),
              "bare": (np.zeros((4, 3)), np.empty((0, 3)), np.zeros((4, 2)), [])}

    def drawn(mf_by_go, mr_by_go, graph, on_null_mesh):
        on_null_mesh("Room/obj14")
        yield 11, Mesh("quad"), [PPtr(0)]
        yield 12, Mesh("placeholder"), []
        yield 13, Mesh("bare"), []

    monkeypatch.setattr(room.UnityPy, "load", lambda *a: type("Env", (), {"objects": []})())
    monkeypatch.setattr(room, "SceneGraph", SceneGraphStub)
    monkeypatch.setattr(room, "drawn_meshes", drawn)
    monkeypatch.setattr(room, "mesh_arrays", lambda mesh: arrays[mesh.m_Name])
    doc = room.extract_room(Cat(), "Spot/a/Background/b", tmp_path / "room.glb")
    assert doc["meshCount"] == 0 and doc["materials"] == []
    assert doc["nullMeshFilters"] == ["Room/obj14"]
    assert doc["meshesWithoutTriangles"] == [{"mesh": "placeholder", "path": "Room/obj12"},
                                             {"mesh": "bare", "path": "Room/obj13"}]
    assert doc["nullMaterialSubmeshes"] == [{"mesh": "quad", "path": "Room/obj11", "submesh": 0}]
    assert (tmp_path / "room.glb").is_file() and (tmp_path / "room.json").is_file()


# ---------------------------------------------------------------- a bundle whose serialized files reuse path ids
V0, V1 = {"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 1.0, "z": 1.0}
Q0 = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
ROOM_KEY = "Spot/a/Background/b"
PREFAB = "Assets/Room.prefab"
# the quads under the prefab root: (object name, material path id, its file id in CAB-a: 1 is CAB-b)
ROOM_OBJECTS = [("wall", 5, 0), ("wall_b", 5, 1), ("floor", 6, 0), ("wall_c", 5, 0)]


def ref(pid, file_id=0):
    return {"m_FileID": file_id, "m_PathID": pid}


class File:
    """A serialized file as UnityPy's PPtr resolves it: name, objects by path id, externals (paths) and the bundle
    holding it (`parent`); a file outside the loaded bundles is not found."""
    def __init__(self, name, bundle, externals=()):
        self.name, self.parent, self.objects = name, bundle, {}
        self.externals = [SimpleNamespace(path=p) for p in externals]
        self.environment = SimpleNamespace(find_file=lambda name: None)
        bundle.files[name] = self

    def add(self, kind, pid, tt, obj=None):
        o = SimpleNamespace(type=SimpleNamespace(name=kind), path_id=pid, assets_file=self,
                            read_typetree=lambda: copy.deepcopy(tt), read=lambda: obj)
        self.objects[pid] = o
        return o

    def pptr(self, pid, file_id=0):
        """A PPtr as UnityPy parses it from an object of this file."""
        return UnityPPtr(m_FileID=file_id, m_PathID=pid, assetsfile=self)


def tex_env(pid, file_id=0):
    return {"m_Texture": ref(pid, file_id), "m_Scale": {"x": 1.0, "y": 1.0}, "m_Offset": {"x": 0.0, "y": 0.0}}


def textured(name, **slots):
    """A material typetree of shader 3 with the texture envs `slots` {slot: tex_env}."""
    tt = material(name=name)
    tt.update(m_Shader=ref(3), m_ValidKeywords=[], m_CustomRenderQueue=-1)
    tt["m_SavedProperties"]["m_TexEnvs"] = [[k, v] for k, v in slots.items()]
    return tt


def quad(name):
    return unitypy_mesh(mesh_tt(name, 3, {POSITION: (F32, 3, np.eye(3)), UV0: (F32, 2, np.zeros((3, 2)))},
                                [0, 1, 2], [submesh(0, 3)]))


def two_file_room():
    """The environment of a bundle of two serialized files that reuse path ids. CAB-a holds the background prefab
    (root `room`, GameObject 100, over ROOM_OBJECTS), shader 3, material 5 `a_wall` (main texture its texture 7,
    `_Mask` in unity default resources), material 6 `a_floor` (main texture CAB-b's texture 7, `_Detail` its own
    texture 7) and texture 7 `wall_a`. CAB-b holds shader 3, material 5 `b_wall` (main texture its texture 7) and
    texture 7 `wall_b`: the pixels of `wall_a` with another filter."""
    bundle = SimpleNamespace(files={})
    a = File("CAB-a", bundle, ["archive:/CAB-b/CAB-b", DEFAULT_RESOURCES])
    b = File("CAB-b", bundle)
    pixels = Image.new("RGBA", (2, 2), (200, 120, 40, 255))
    for f in (a, b):
        f.add("Shader", 3, shader(name="Unlit/Transparent", srcBlend=fv(5.0), destBlend=fv(10.0)))
    a.add("Material", 5, textured("a_wall", _MainTex=tex_env(7), _Mask=tex_env(9, 2)))
    a.add("Material", 6, textured("a_floor", _MainTex=tex_env(7, 1), _Detail=tex_env(7)))
    b.add("Material", 5, textured("b_wall", _MainTex=tex_env(7)))
    for f, name, filter_mode in ((a, "wall_a", 1), (b, "wall_b", 0)):
        f.add("Texture2D", 7, {"m_Name": name, "m_MipCount": 1,
                               "m_TextureSettings": {"m_FilterMode": filter_mode, "m_WrapU": 1, "m_WrapV": 1}},
              pixels.copy())
    a.add("AssetBundle", 1, {"m_Container": [[PREFAB.lower(), {"asset": ref(100)}]]})
    a.add("GameObject", 100, {"m_Name": "room", "m_IsActive": 1, "m_Layer": 0})
    a.add("Transform", 200, {"m_GameObject": ref(100), "m_Father": ref(0), "m_LocalPosition": V0,
                             "m_LocalRotation": Q0, "m_LocalScale": V1,
                             "m_Children": [ref(201 + i) for i in range(len(ROOM_OBJECTS))]})
    for i, (name, mat, file_id) in enumerate(ROOM_OBJECTS):
        go, mesh = 101 + i, quad(f"{name}_mesh")
        a.add("GameObject", go, {"m_Name": name, "m_IsActive": 1, "m_Layer": 0})
        a.add("Transform", 201 + i, {"m_GameObject": ref(go), "m_Father": ref(200), "m_Children": [],
                                     "m_LocalPosition": V0, "m_LocalRotation": Q0, "m_LocalScale": V1})
        a.add("MeshFilter", 301 + i, {"m_GameObject": ref(go)},
              SimpleNamespace(m_Mesh=SimpleNamespace(path_id=21 + i, read=lambda mesh=mesh: mesh)))
        a.add("MeshRenderer", 401 + i, {"m_GameObject": ref(go), "m_Enabled": 1},
              SimpleNamespace(m_Materials=[a.pptr(mat, file_id)]))
    return SimpleNamespace(objects=[o for f in (a, b) for o in f.objects.values()])


class RoomCatalog:
    def fetch_key(self, key):
        return []

    def _entry(self, key):
        return {"internal_id": PREFAB}


def load_room(monkeypatch, env):
    """UnityPy.load gives `env`; a texture's image is the object it reads as."""
    monkeypatch.setattr(room.UnityPy, "load", lambda *a: env)
    monkeypatch.setattr(room, "texture_image", lambda image: image)


def glb_json(path):
    data = path.read_bytes()
    (n,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20:20 + n])


def test_textures_with_the_same_png_bytes_share_one_image():
    glb = room._Glb()
    linear, point = room._sampler({}), room._sampler({"m_TextureSettings": {"m_FilterMode": 0}})
    got = [glb.texture(png, name, s) for png, name, s in ((b"png-1", "a", linear), (b"png-1", "b", point),
                                                          (b"png-2", "c", linear))]
    assert got == [0, 1, 2]
    assert glb.textures == [{"name": "a", "source": 0, "sampler": 0}, {"name": "b", "source": 0, "sampler": 1},
                            {"name": "c", "source": 1, "sampler": 0}]
    assert [im["name"] for im in glb.images] == ["a", "c"] and len(glb.bufferViews) == 2


def test_mesh_components_are_keyed_by_file_and_game_object():
    bundle = SimpleNamespace(files={})
    a, b = File("CAB-a", bundle), File("CAB-b", bundle)
    mf_a = a.add("MeshFilter", 9, {"m_GameObject": ref(1)})
    mf_b = b.add("MeshFilter", 9, {"m_GameObject": ref(1)})
    mr_b = b.add("MeshRenderer", 10, {"m_GameObject": ref(1)})
    assert room.mesh_components(SimpleNamespace(objects=[mf_a, mf_b, mr_b])) == (
        {("CAB-a", 1): mf_a, ("CAB-b", 1): mf_b}, {("CAB-b", 1): mr_b})


def test_extract_room_keys_materials_and_textures_by_file(tmp_path, monkeypatch):
    load_room(monkeypatch, two_file_room())
    doc = room.extract_room(RoomCatalog(), ROOM_KEY, tmp_path / "room.glb")
    gltf = glb_json(tmp_path / "room.glb")
    assert [n["name"] for n in gltf["nodes"]] == ["wall_mesh", "wall_b_mesh", "floor_mesh", "wall_c_mesh"]
    # material 5 of CAB-a and of CAB-b are two materials; wall_c draws with CAB-a's again
    assert [m["name"] for m in gltf["materials"]] == ["a_wall", "b_wall", "a_floor"]
    assert [p["material"] for m in gltf["meshes"] for p in m["primitives"]] == [0, 1, 2, 0]
    # texture 7 of CAB-a and of CAB-b are two textures, each with its sampler, of one image; a_floor takes CAB-b's
    assert [m["pbrMetallicRoughness"]["baseColorTexture"]["index"] for m in gltf["materials"]] == [0, 1, 1]
    assert gltf["textures"] == [{"name": "wall_a", "source": 0, "sampler": 0},
                                {"name": "wall_b", "source": 0, "sampler": 1}]
    assert [im["name"] for im in gltf["images"]] == ["wall_a"]
    assert [s["magFilter"] for s in gltf["samplers"]] == [room.LINEAR, room.NEAREST]
    assert doc["textures"] == ["wall_a", "wall_b"] and doc["samplers"] == gltf["samplers"]
