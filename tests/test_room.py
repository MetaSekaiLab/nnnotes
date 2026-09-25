"""room: material translation from the pass render state (property-driven values) and empty material slots
(synthetic shader / material typetrees)."""
import numpy as np
import pytest

from nnnotes import room

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
    mfs = {1: Obj({}, m_Mesh=Ref(21, mesh)), 2: Obj({}, m_Mesh=Ref(0)), 3: Obj({}, m_Mesh=Ref(0)),
           4: Obj({}, m_Mesh=Ref(24, mesh))}
    mrs = {1: Obj({"m_Enabled": 1}, m_Materials=["m1"]), 2: Obj({"m_Enabled": 1}, m_Materials=[]),
           3: Obj({"m_Enabled": 0}, m_Materials=[])}               # 3: disabled renderer, 4: no renderer
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
