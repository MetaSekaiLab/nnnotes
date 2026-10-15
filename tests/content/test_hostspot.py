"""hostspot (the spot files of a story's home host, read from bundles): empty material slots and mesh filters, what
draws nothing, materials and textures of a bundle whose serialized files reuse path ids, Spine characters and tap
targets with and without the references the prefab leaves empty, and the Spine files of a skeleton (synthetic
objects)."""

import copy
import io
import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from UnityPy.classes import PPtr as UnityPPtr

from nnnotes.content import hostspot, room
from nnnotes.unity.reader import DEFAULT_RESOURCES
from tests.helpers.fakeunity import F32, POSITION, UV0, mesh_tt, submesh, unitypy_mesh
from tests.helpers.spotobjects import fv, material, shader


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

    prims = hostspot.submesh_primitives(tris, mats, material_for, nulls.append)
    assert nulls == [0] and used == [12]  # the empty submesh 1 gives nothing, silently
    assert len(prims) == 1 and prims[0][1] == 120
    assert prims[0][0].tolist() == [2, 0, 3, 0, 3, 1]  # winding reversed for glTF
    assert hostspot.submesh_primitives(tris[:1], mats[:1], material_for, nulls.append) == [] and nulls == [0, 0]


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
    mfs = {
        ("f", 1): Obj({}, m_Mesh=Ref(21, mesh)),
        ("f", 2): Obj({}, m_Mesh=Ref(0)),
        ("f", 3): Obj({}, m_Mesh=Ref(0)),
        ("f", 4): Obj({}, m_Mesh=Ref(24, mesh)),
    }
    mrs = {
        ("f", 1): Obj({"m_Enabled": 1}, m_Materials=["m1"]),
        ("f", 2): Obj({"m_Enabled": 1}, m_Materials=[]),
        ("f", 3): Obj({"m_Enabled": 0}, m_Materials=[]),  # 3: disabled renderer, 4: no renderer
        ("g", 4): Obj({"m_Enabled": 1}, m_Materials=["m4"]),
    }  # another file's GameObject 4
    nulls = []
    drawn = list(hostspot.drawn_meshes(mfs, mrs, Graph(), nulls.append))
    assert drawn == [(11, mesh, ["m1"])]
    assert nulls == ["Room/obj12"]  # the disabled one draws nothing either way


def test_extract_room_lists_what_draws_nothing(tmp_path, monkeypatch):
    """A mesh without triangles, a MeshFilter without a mesh and a submesh without a material are not written but
    listed in hostspot.json."""

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
    arrays = {
        "quad": (np.zeros((3, 3)), np.empty((0, 3)), np.zeros((3, 2)), [tri]),
        "placeholder": (np.zeros((4, 3)), np.empty((0, 3)), np.zeros((4, 2)), [np.zeros((0, 3), int)]),
        "bare": (np.zeros((4, 3)), np.empty((0, 3)), np.zeros((4, 2)), []),
    }

    def drawn(mf_by_go, mr_by_go, graph, on_null_mesh):
        on_null_mesh("Room/obj14")
        yield 11, Mesh("quad"), [PPtr(0)]
        yield 12, Mesh("placeholder"), []
        yield 13, Mesh("bare"), []

    monkeypatch.setattr(hostspot.UnityPy, "load", lambda *a: type("Env", (), {"objects": []})())
    monkeypatch.setattr(hostspot, "SceneGraph", SceneGraphStub)
    monkeypatch.setattr(hostspot, "drawn_meshes", drawn)
    monkeypatch.setattr(hostspot, "mesh_arrays", lambda mesh: arrays[mesh.m_Name])
    doc = hostspot.extract_room(Cat(), "Spot/a/Background/b", tmp_path / "hostspot.glb")
    assert doc["meshCount"] == 0 and doc["materials"] == []
    assert doc["nullMeshFilters"] == ["Room/obj14"]
    assert doc["meshesWithoutTriangles"] == [
        {"mesh": "placeholder", "path": "Room/obj12"},
        {"mesh": "bare", "path": "Room/obj13"},
    ]
    assert doc["nullMaterialSubmeshes"] == [{"mesh": "quad", "path": "Room/obj11", "submesh": 0}]
    assert (tmp_path / "hostspot.glb").is_file() and (tmp_path / "hostspot.json").is_file()


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
        o = SimpleNamespace(
            type=SimpleNamespace(name=kind),
            path_id=pid,
            assets_file=self,
            read_typetree=lambda: copy.deepcopy(tt),
            read=lambda: obj,
        )
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
    return unitypy_mesh(
        mesh_tt(name, 3, {POSITION: (F32, 3, np.eye(3)), UV0: (F32, 2, np.zeros((3, 2)))}, [0, 1, 2], [submesh(0, 3)])
    )


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
        f.add(
            "Texture2D",
            7,
            {
                "m_Name": name,
                "m_MipCount": 1,
                "m_TextureSettings": {"m_FilterMode": filter_mode, "m_WrapU": 1, "m_WrapV": 1},
            },
            pixels.copy(),
        )
    a.add("AssetBundle", 1, {"m_Container": [[PREFAB.lower(), {"asset": ref(100)}]]})
    a.add("GameObject", 100, {"m_Name": "room", "m_IsActive": 1, "m_Layer": 0})
    a.add(
        "Transform",
        200,
        {
            "m_GameObject": ref(100),
            "m_Father": ref(0),
            "m_LocalPosition": V0,
            "m_LocalRotation": Q0,
            "m_LocalScale": V1,
            "m_Children": [ref(201 + i) for i in range(len(ROOM_OBJECTS))],
        },
    )
    for i, (name, mat, file_id) in enumerate(ROOM_OBJECTS):
        go, mesh = 101 + i, quad(f"{name}_mesh")
        a.add("GameObject", go, {"m_Name": name, "m_IsActive": 1, "m_Layer": 0})
        a.add(
            "Transform",
            201 + i,
            {
                "m_GameObject": ref(go),
                "m_Father": ref(200),
                "m_Children": [],
                "m_LocalPosition": V0,
                "m_LocalRotation": Q0,
                "m_LocalScale": V1,
            },
        )
        a.add(
            "MeshFilter",
            301 + i,
            {"m_GameObject": ref(go)},
            SimpleNamespace(m_Mesh=SimpleNamespace(path_id=21 + i, read=lambda mesh=mesh: mesh)),
        )
        a.add(
            "MeshRenderer",
            401 + i,
            {"m_GameObject": ref(go), "m_Enabled": 1},
            SimpleNamespace(m_Materials=[a.pptr(mat, file_id)]),
        )
    return SimpleNamespace(objects=[o for f in (a, b) for o in f.objects.values()])


class RoomCatalog:
    def fetch_key(self, key):
        return []

    def entry(self, key):
        return {"internal_id": PREFAB}


def load_room(monkeypatch, env):
    """UnityPy.load gives `env`; a texture's image is the object it reads as."""
    monkeypatch.setattr(hostspot.UnityPy, "load", lambda *a: env)
    monkeypatch.setattr(hostspot, "texture_image", lambda image: image)


def glb_json(path):
    data = path.read_bytes()
    (n,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + n])


def test_mesh_components_are_keyed_by_file_and_game_object():
    bundle = SimpleNamespace(files={})
    a, b = File("CAB-a", bundle), File("CAB-b", bundle)
    mf_a = a.add("MeshFilter", 9, {"m_GameObject": ref(1)})
    mf_b = b.add("MeshFilter", 9, {"m_GameObject": ref(1)})
    mr_b = b.add("MeshRenderer", 10, {"m_GameObject": ref(1)})
    assert hostspot.mesh_components(SimpleNamespace(objects=[mf_a, mf_b, mr_b])) == (
        {("CAB-a", 1): mf_a, ("CAB-b", 1): mf_b},
        {("CAB-b", 1): mr_b},
    )


def test_extract_room_keys_materials_and_textures_by_file(tmp_path, monkeypatch):
    load_room(monkeypatch, two_file_room())
    doc = hostspot.extract_room(RoomCatalog(), ROOM_KEY, tmp_path / "hostspot.glb")
    gltf = glb_json(tmp_path / "hostspot.glb")
    assert [n["name"] for n in gltf["nodes"]] == ["wall_mesh", "wall_b_mesh", "floor_mesh", "wall_c_mesh"]
    # material 5 of CAB-a and of CAB-b are two materials; wall_c draws with CAB-a's again
    assert [m["name"] for m in gltf["materials"]] == ["a_wall", "b_wall", "a_floor"]
    assert [p["material"] for m in gltf["meshes"] for p in m["primitives"]] == [0, 1, 2, 0]
    # texture 7 of CAB-a and of CAB-b are two textures, each with its sampler, of one image; a_floor takes CAB-b's
    assert [m["pbrMetallicRoughness"]["baseColorTexture"]["index"] for m in gltf["materials"]] == [0, 1, 1]
    assert gltf["textures"] == [
        {"name": "wall_a", "source": 0, "sampler": 0},
        {"name": "wall_b", "source": 0, "sampler": 1},
    ]
    assert [im["name"] for im in gltf["images"]] == ["wall_a"]
    assert [s["magFilter"] for s in gltf["samplers"]] == [room.LINEAR, room.NEAREST]
    assert doc["textures"] == ["wall_a", "wall_b"] and doc["samplers"] == gltf["samplers"]


# ---------------------------------------------------------------- spot
class SpineRef:
    def __init__(self, path_id, target=None):
        self.path_id, self.target = path_id, target

    def read(self):
        assert self.path_id, "an empty reference is not read"
        return self.target


class Reader:
    def __init__(self, tt):
        self.tt = tt

    def read_typetree(self):
        return self.tt


class Read:
    def __init__(self, tt=None, **fields):
        self.object_reader = Reader(tt)
        self.__dict__.update(fields)


class SpineObj:
    def __init__(self, tt, **fields):
        self.tt, self.fields = tt, fields

    def read_typetree(self):
        return self.tt

    def read(self):
        return Read(**self.fields)


class SituationGraph:
    tf_of_go = {1: 11, 2: 12, 3: 13, 4: 14}

    def path(self, tf_pid):
        return f"Situation/obj{tf_pid}"


def go(pid):
    return {"m_FileID": 0, "m_PathID": pid}


def world_of(go_ref):
    return [float(go_ref["m_PathID"])]


ANIM = {
    "m_GameObject": go(3),
    "_animationName": "idle",
    "loop": 1,
    "timeScale": 1.0,
    "initialSkinName": "default",
    "initialFlipX": 0,
    "initialFlipY": 0,
    "pmaVertexColors": 1,
    "tintBlack": 0,
    "zSpacing": 0.0,
}


def test_spine_characters_keep_one_without_animation_as_null():
    anim = Read(ANIM, skeletonDataAsset=SpineRef(50))
    objs = [
        SpineObj({"m_GameObject": go(1)}, _animation=SpineRef(30, anim)),
        SpineObj({"m_GameObject": go(2)}, _animation=SpineRef(0)),
    ]
    got = hostspot.spine_characters(objs, SituationGraph(), {50: {"name": "chr_sd"}}, world_of)
    assert got[0] == {
        "path": "Situation/obj11",
        "skeletonData": "chr_sd",
        "animation": {k: v for k, v in ANIM.items() if k != "m_GameObject"},
        "world": [3.0],
    }
    assert got[1] == {"path": "Situation/obj12", "skeletonData": None, "animation": None, "world": None}


def test_tap_targets_without_focus_have_a_null_focus_world():
    focus = Read({"m_GameObject": go(3)})
    objs = [
        SpineObj({"m_GameObject": go(2), "m_Enabled": 1, "_characterId": 7, "_focus": go(0)}, _focus=SpineRef(0)),
        SpineObj(
            {"m_GameObject": go(1), "m_Enabled": 1, "_characterId": 5, "_focus": go(33)}, _focus=SpineRef(33, focus)
        ),
    ]
    got = hostspot.tap_targets(objs, SituationGraph(), world_of)
    assert [c["_characterId"] for c in got] == [5, 7]
    assert got[0] == {"path": "Situation/obj11", "_characterId": 5, "world": [1.0], "focusWorld": [3.0]}
    assert got[1] == {"path": "Situation/obj12", "_characterId": 7, "world": [2.0], "focusWorld": None}


# ---------------------------------------------------------------- Spine files (synthetic objects)
class Text:
    def __init__(self, name, script):
        self.m_Name, self.m_Script = name, script


class Tex:
    m_TextureFormat = 4  # RGBA32

    def __init__(self, image):
        self.m_Name, self.image = "page", image


class TexEnv:
    def __init__(self, tex):
        self.m_Texture = SpineRef(1, tex)


class SpineMaterial:
    def __init__(self, tex):
        self.m_SavedProperties = Read(m_TexEnvs=[("_BumpMap", TexEnv(None)), ("_MainTex", TexEnv(tex))])


ATLAS_TEXT = "\np1.png\nsize: 2,2\nformat: RGBA8888\nr\n  bounds: 0,0,1,1\n\np2.png\nsize: 1,1\nr2\n  bounds: 0,0,1,1\n"


def atlas_asset(name, pages):
    return Read(
        atlasFile=SpineRef(20, Text(name, ATLAS_TEXT)),
        materials=[SpineRef(30 + i, SpineMaterial(t)) for i, t in enumerate(pages)],
    )


def skeleton(name, script, atlases):
    tt = {
        "m_Name": name + "_SkeletonData",
        "scale": 0.01,
        "defaultMix": 0.2,
        "fromAnimation": ["a"],
        "toAnimation": ["b"],
        "duration": [0.5],
    }
    return SpineObj(tt, skeletonJSON=SpineRef(9, Text(name, script)), atlasAssets=atlases)


def images():
    return Image.new("RGBA", (2, 2), (1, 2, 3, 4)), Image.new("RGBA", (1, 1), (9, 8, 7, 6))


def png_of(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_write_skeleton_writes_the_skeleton_atlas_and_pages_in_order():
    a, b = images()
    shared = SpineRef(40, atlas_asset("chr", [Tex(a), Tex(b)]))
    got = []
    written = {}
    rec = hostspot.write_skeleton(skeleton("chr", b"\x00binary", [shared]), lambda n, d: got.append((n, d)), written)
    assert [n for n, _ in got] == ["chr.skel", "chr.atlas", "p1.png", "p2.png"]
    assert got[0][1] == b"\x00binary" and got[1][1] == ATLAS_TEXT.encode()
    assert got[2][1] == png_of(a) and got[3][1] == png_of(b)
    assert rec == {
        "name": "chr_SkeletonData",
        "skeleton": "chr.skel",
        "atlases": ["chr.atlas"],
        "scale": 0.01,
        "defaultMix": 0.2,
        "mixes": [{"from": "a", "to": "b", "duration": 0.5}],
    }
    assert written == {40: "chr.atlas"}
    got.clear()  # a second skeleton on the same atlas: written once
    rec2 = hostspot.write_skeleton(
        skeleton("chr2", ' {"skeleton": 1}', [shared]), lambda n, d: got.append((n, d)), written
    )
    assert got == [("chr2.json", b' {"skeleton": 1}')] and rec2["atlases"] == ["chr.atlas"]


def test_write_skeleton_keeps_a_name_that_already_has_the_extension():
    a, b = images()
    got = []
    shared = SpineRef(40, atlas_asset("chr.atlas", [Tex(a), Tex(b)]))
    rec = hostspot.write_skeleton(skeleton("chr.skel", b"\x00binary", [shared]), lambda n, d: got.append((n, d)))
    assert [n for n, _ in got] == ["chr.skel", "chr.atlas", "p1.png", "p2.png"] and rec["skeleton"] == "chr.skel"


def test_write_skeleton_checks_pages_against_materials():
    a, _ = images()
    with pytest.raises(RuntimeError, match="2 pages vs 1 materials"):
        hostspot.write_skeleton(
            skeleton("x", b"s", [SpineRef(41, atlas_asset("x.atlas", [Tex(a)]))]), lambda n, d: None
        )
