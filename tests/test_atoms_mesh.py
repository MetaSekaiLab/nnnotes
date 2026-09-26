"""mesh.arrays and gltf.write on synthetic meshes: every channel with its stored component count and type (half-float
normals with four components), conformance with UnityPy's MeshHandler (and with unity.mesh_arrays), skins, meshes
without an index buffer or without vertices, and the GLB's structure (accessors, min / max, winding, alignment)."""
import struct

import numpy as np
import pytest
from UnityPy.helpers.MeshHelper import MeshHandler

from fakeunity import (BLENDINDICES, BLENDWEIGHT, COLOR, F16, F32, NORMAL, POSITION, SNORM16, TANGENT, UINT32,
                       UNORM8, UNORM16, UV0, UV1, VERSION, FakeEnvironment, matrix, mesh_tt, submesh, unitypy_mesh)
from nnnotes import unity
from nnnotes.atoms import ATOMS, Unsupported, gltf
from nnnotes.atoms.mesh import MeshInput, arrays
from nnnotes.atoms.reader import UnityBundle, handler_input, mesh_input_from_typetree

rng = np.random.default_rng(7)


def channels(count, *, half=False, color=True, skin=None, uv1=False, streams=False):
    """A full vertex layout: Float positions; Float x3 normals / x4 tangents / x2 uv0, or (half) Float16 x4 normals
    with the dimension byte 0x34, Float16 x4 tangents, Float16 x2 uv0; UNorm8 x4 colours."""
    pos = rng.normal(size=(count, 3)).astype(np.float32)
    ch = {POSITION: (F32, 3, pos)}
    if half:
        n = np.hstack([rng.normal(size=(count, 3)), np.zeros((count, 1))]).astype(np.float16)
        ch[NORMAL] = (F16, 0x34, n)
        ch[TANGENT] = (F16, 4, np.hstack([rng.normal(size=(count, 3)), np.sign(rng.normal(size=(count, 1)))]))
        ch[UV0] = (F16, 2, rng.random((count, 2)))
    else:
        ch[NORMAL] = (F32, 3, rng.normal(size=(count, 3)))
        ch[TANGENT] = (F32, 4, np.hstack([rng.normal(size=(count, 3)), np.ones((count, 1))]))
        ch[UV0] = (F32, 2, rng.random((count, 2)), 1 if streams else 0)
    if color:
        ch[COLOR] = (UNORM8, 4, rng.integers(0, 256, (count, 4)), 1 if streams else 0)
    if uv1:
        ch[UV1] = (F32, 2, rng.random((count, 2)))
    if skin == "x2":
        ch[BLENDWEIGHT] = (F32, 2, rng.random((count, 2)))
        ch[BLENDINDICES] = (UINT32, 2, rng.integers(0, 5, (count, 2)))
    elif skin == "x1":
        ch[BLENDINDICES] = (UINT32, 1, rng.integers(0, 5, (count, 1)))
    return ch


def tris(count, n):
    return rng.integers(0, count, n * 3)


def mesh_of(tt, version=VERSION):
    return arrays(mesh_input_from_typetree(tt, version))


def handler(tt):
    h = MeshHandler(unitypy_mesh(tt), VERSION)
    h.process()
    return h


FIELDS = {"position": "m_Vertices", "normal": "m_Normals", "tangent": "m_Tangents", "color": "m_Colors",
          "uv0": "m_UV0", "uv1": "m_UV1", "blendWeight": "m_BoneWeights", "blendIndices": "m_BoneIndices"}


# ---------------------------------------------------------------- conformance with UnityPy
@pytest.mark.parametrize("layout", [dict(), dict(half=True), dict(skin="x2", uv1=True), dict(skin="x1"),
                                    dict(streams=True)])
def test_channels_equal_meshhandler(layout):
    count = 23
    tt = mesh_tt("m", count, channels(count, **layout), tris(count, 9), [submesh(0, 27)])
    ours, h = mesh_of(tt), handler(tt)
    assert set(ours.channels) == {k for k, f in FIELDS.items() if getattr(h, f)}
    for name, ch in ours.channels.items():
        theirs = np.asarray(getattr(h, FIELDS[name]), np.float64)
        assert ch.data.shape == theirs.shape, name                       # component count as stored
        assert np.array_equal(ch.data.astype(np.float64), theirs), name  # the stored values, not renormalized
    assert [p.indices.tolist() for p in ours.primitives] == [[list(t) for t in s] for s in h.get_triangles()]


def test_half_float_normals_keep_four_components_and_the_dimension_byte():
    count = 6
    tt = mesh_tt("m", count, channels(count, half=True, color=False), tris(count, 2), [submesh(0, 6)])
    m = mesh_of(tt)
    assert m.channels["normal"].data.shape == (6, 4) and m.channels["normal"].data.dtype == np.float16
    assert m.channels["uv0"].data.dtype == np.float16 and m.channels["tangent"].data.shape == (6, 4)
    assert {c["channel"]: c["dimension"] for c in m.layout}["normal"] == 0x34
    assert {c["channel"]: c["format"] for c in m.layout}["normal"] == F16


def test_equal_to_unity_mesh_arrays_on_what_it_returns():
    count = 12
    tt = mesh_tt("m", count, channels(count, half=True), tris(count, 6), [submesh(0, 9), submesh(9, 9)])
    v, n, uv, t = unity.mesh_arrays(unitypy_mesh(tt))
    m = mesh_of(tt)
    assert np.array_equal(m.channels["position"].data, v)
    assert np.array_equal(m.channels["normal"].data[:, :3].astype(np.float64), n)
    assert np.array_equal(m.channels["uv0"].data.astype(np.float64), uv)
    assert [p.indices.tolist() for p in m.primitives] == [x.tolist() for x in t]


@pytest.mark.parametrize("topology, count", [(2, 8), (1, 7)])
def test_quads_and_strips_become_triangles_as_meshhandler_does(topology, count):
    idx = [0, 1, 2, 3, 3, 4, 5, 6] if topology == 2 else [0, 1, 2, 2, 3, 4, 5]
    tt = mesh_tt("m", 8, channels(8, color=False), idx, [submesh(0, count, topology)])
    m = mesh_of(tt)
    assert m.primitives[0].topology == "triangles"
    assert m.primitives[0].indices.tolist() == [list(t) for t in handler(tt).get_triangles()[0]]


def test_base_vertex_and_32_bit_indices():
    count = 70000
    ch = {POSITION: (F32, 3, rng.normal(size=(count, 3)))}
    idx = [0, 1, 2, 69997, 69998, 69999, 0, 1, 2]
    tt = mesh_tt("m", count, ch, idx, [submesh(0, 6, bits=32), submesh(6, 3, base_vertex=5, bits=32)], bits=32)
    m = mesh_of(tt)
    assert m.primitives[0].indices.tolist() == [[0, 1, 2], [69997, 69998, 69999]]
    assert m.primitives[1].indices.tolist() == [[5, 6, 7]]                 # baseVertex added (UnityPy does not)
    doc, body = gltf.read(gltf.write([m]))
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]
    assert acc["componentType"] == 5125                                  # 69999 does not fit an unsigned short
    assert gltf.accessor_array(doc, body, doc["meshes"][0]["primitives"][0]["indices"]).tolist() == \
        [0, 2, 1, 69997, 69999, 69998]


def test_lines_and_points_stay_what_they_are():
    ch = {POSITION: (F32, 3, rng.normal(size=(5, 3)))}
    tt = mesh_tt("m", 5, ch, [0, 1, 1, 2, 3, 4, 0], [submesh(0, 4, 3), submesh(4, 2, 4), submesh(6, 1, 5)])
    m = mesh_of(tt)
    assert [(p.topology, p.indices.tolist()) for p in m.primitives] == \
        [("lines", [0, 1, 1, 2]), ("line_strip", [3, 4]), ("points", [0])]
    doc, _ = gltf.read(gltf.write([m]))
    assert [p["mode"] for p in doc["meshes"][0]["primitives"]] == [1, 3, 0]


def test_indices_outside_the_vertices_are_an_error():
    tt = mesh_tt("m", 3, {POSITION: (F32, 3, np.zeros((3, 3)))}, [0, 1, 3], [submesh(0, 3)])
    with pytest.raises(ValueError, match="index 3 of 3 vertices"):
        mesh_of(tt)


def test_vertex_data_too_short_is_an_error():
    tt = mesh_tt("m", 4, {POSITION: (F32, 3, np.zeros((4, 3)))}, [0, 1, 2], [submesh(0, 3)], data=b"\0" * 40)
    with pytest.raises(ValueError, match="outside the vertex data"):
        mesh_of(tt)


def test_decoded_path_gives_the_same_arrays():
    count = 9
    tt = mesh_tt("m", count, channels(count, half=True, skin="x2"), tris(count, 3), [submesh(0, 9)])
    raw, dec = mesh_of(tt), arrays(handler_input(unitypy_mesh(tt), tt, VERSION))
    assert set(raw.channels) == set(dec.channels)
    for k in raw.channels:
        assert np.array_equal(raw.channels[k].data.astype(np.float64), dec.channels[k].data.astype(np.float64)), k
    assert [p.indices.tolist() for p in raw.primitives] == [p.indices.tolist() for p in dec.primitives]
    assert np.array_equal(raw.skin.joints, dec.skin.joints)


# ---------------------------------------------------------------- skins, blend shapes, empty and index-less meshes
def test_skin_with_two_weights_per_vertex():
    count = 5
    ch = channels(count, skin="x2")
    poses = [matrix(np.eye(4) * (i + 1)) for i in range(5)]
    m = mesh_of(mesh_tt("m", count, ch, tris(count, 2), [submesh(0, 6)], bind_poses=poses,
                        bone_hashes=[11, 12, 13, 14, 15], root_hash=99))
    assert m.skin.joints.dtype == np.uint16 and m.skin.joints.shape == (5, 4)
    assert np.array_equal(m.skin.joints[:, :2], ch[BLENDINDICES][2]) and not m.skin.joints[:, 2:].any()
    assert np.array_equal(m.skin.weights.data[:, :2], ch[BLENDWEIGHT][2].astype(np.float32))
    assert not m.skin.weights.data[:, 2:].any() and m.issues == []
    assert m.bind_poses.shape == (5, 4, 4) and m.bind_poses[2][1][1] == 3.0
    doc, body = gltf.read(gltf.write([m]))
    prim = doc["meshes"][0]["primitives"][0]
    assert gltf.accessor_array(doc, body, prim["attributes"]["JOINTS_0"]).tolist() == m.skin.joints.tolist()
    assert np.array_equal(gltf.accessor_array(doc, body, prim["attributes"]["WEIGHTS_0"]), m.skin.weights.data)
    ex = doc["meshes"][0]["extras"]["unity"]
    assert ex["boneNameHashes"] == [11, 12, 13, 14, 15] and ex["rootBoneNameHash"] == 99
    assert ex["bindPoses"][1] == (np.eye(4) * 2).tolist()


def test_indices_only_skin_is_one_bone_per_vertex():
    count = 4
    ch = channels(count, skin="x1")
    m = mesh_of(mesh_tt("m", count, ch, tris(count, 1), [submesh(0, 3)], bind_poses=[matrix(np.eye(4))] * 5))
    assert m.skin.weights.data.tolist() == [[1.0, 0.0, 0.0, 0.0]] * 4
    assert m.skin.joints[:, 0].tolist() == ch[BLENDINDICES][2][:, 0].tolist() and m.issues == []


def test_inconsistent_or_variable_skins_are_reported():
    count = 4
    ch = channels(count, skin="x2")
    ch[BLENDWEIGHT] = (F32, 3, rng.random((count, 3)))
    m = mesh_of(mesh_tt("m", count, ch, tris(count, 1), [submesh(0, 3)]))
    assert m.skin is None and [c for c, _ in m.issues] == ["partial.mesh.skin"]
    doc, _ = gltf.read(gltf.write([m]))
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    assert "JOINTS_0" not in attrs and {"_BLENDWEIGHT", "_BLENDINDICES"} <= set(attrs)   # kept as custom attributes
    m = mesh_of(mesh_tt("m", count, channels(count, skin="x2"), tris(count, 1), [submesh(0, 3)], variable_weights=3))
    assert m.skin is not None and [c for c, _ in m.issues] == ["partial.mesh.skin"]


def test_blend_shapes_are_reported():
    m = mesh_of(mesh_tt("m", 3, channels(3), [0, 1, 2], [submesh(0, 3)], shapes=2))
    assert m.issues == [("unsupported.mesh.blendshapes", "2 blend shapes not converted")]


@pytest.mark.parametrize("subs", [[submesh(0, 0)], []])
def test_no_index_buffer_is_one_points_primitive(subs):
    count = 6
    m = mesh_of(mesh_tt("m", count, channels(count, color=False), [], subs))
    assert [(p.topology, p.indices) for p in m.primitives] == [("points", None)]
    doc, _ = gltf.read(gltf.write([m]))
    assert doc["meshes"][0]["primitives"] == [{"attributes": doc["meshes"][0]["primitives"][0]["attributes"],
                                               "mode": 0}]


def test_empty_mesh_is_refused():
    m = mesh_of(mesh_tt("empty", 0, {}, [], [submesh(0, 0)]))
    assert m.vertex_count == 0 and m.channels == {} and m.primitives == []
    with pytest.raises(Unsupported) as e:
        gltf.write([m])
    assert e.value.code == "empty.mesh"


def test_empty_submeshes_are_left_out_and_listed():
    count = 6
    m = mesh_of(mesh_tt("m", count, channels(count), tris(count, 2), [submesh(0, 3), submesh(3, 0), submesh(3, 3)]))
    doc, _ = gltf.read(gltf.write([m]))
    assert len(doc["meshes"][0]["primitives"]) == 2
    assert doc["meshes"][0]["extras"]["unity"]["emptySubmeshes"] == [1]
    assert len(doc["meshes"][0]["extras"]["unity"]["submeshes"]) == 3


# ---------------------------------------------------------------- GLB structure
def test_glb_structure_space_conversion_and_minmax():
    count = 10
    ch = channels(count, half=True)
    idx = tris(count, 4)
    m = mesh_of(mesh_tt("cube", count, ch, idx, [submesh(0, 6), submesh(6, 6)]))
    glb = gltf.write([m], extras={"source": "x"})
    magic, version, total = struct.unpack_from("<III", glb)
    assert (magic, version, total) == (0x46546C67, 2, len(glb)) and len(glb) % 4 == 0
    doc, body = gltf.read(glb)
    assert doc["asset"] == {"version": "2.0", "generator": "nnnotes", "extras": {"source": "x"}}
    assert doc["nodes"] == [{"name": "cube", "mesh": 0}] and doc["scenes"] == [{"nodes": [0]}]
    prims = doc["meshes"][0]["primitives"]
    assert len(prims) == 2 and all(p["mode"] == 4 for p in prims)
    attrs = prims[0]["attributes"]
    assert set(attrs) == {"POSITION", "NORMAL", "TANGENT", "TEXCOORD_0", "COLOR_0"}
    pos = gltf.accessor_array(doc, body, attrs["POSITION"])
    want = ch[POSITION][2].astype(np.float32) * [1, 1, -1]
    assert np.array_equal(pos, want)
    acc = doc["accessors"][attrs["POSITION"]]
    assert acc["min"] == want.min(0).tolist() and acc["max"] == want.max(0).tolist()
    nrm = gltf.accessor_array(doc, body, attrs["NORMAL"])
    assert np.array_equal(nrm, ch[NORMAL][2][:, :3].astype(np.float32) * [1, 1, -1])      # w (zero) dropped
    tan = gltf.accessor_array(doc, body, attrs["TANGENT"])
    assert np.array_equal(tan, np.asarray(ch[TANGENT][2], np.float16).astype(np.float32) * [1, 1, -1, -1])
    uv = gltf.accessor_array(doc, body, attrs["TEXCOORD_0"])
    src = np.asarray(ch[UV0][2], np.float16).astype(np.float64)
    assert np.array_equal(uv[:, 0], src[:, 0].astype(np.float32))
    assert np.array_equal(uv[:, 1], (1.0 - src[:, 1]).astype(np.float32))
    col = doc["accessors"][attrs["COLOR_0"]]
    assert col["componentType"] == 5121 and col["normalized"] is True and col["type"] == "VEC4"
    assert np.array_equal(gltf.accessor_array(doc, body, attrs["COLOR_0"]), ch[COLOR][2])
    first = gltf.accessor_array(doc, body, prims[0]["indices"])
    assert first.tolist() == np.asarray(idx[:6]).reshape(-1, 3)[:, [0, 2, 1]].reshape(-1).tolist()
    assert doc["accessors"][prims[0]["indices"]]["componentType"] == 5123
    for v in doc["bufferViews"]:
        assert v["byteOffset"] % 4 == 0 and v.get("byteStride", 4) % 4 == 0
    assert gltf.write([m], extras={"source": "x"}) == glb                                 # deterministic


def test_nonzero_extra_components_become_custom_attributes():
    count = 4
    n = np.hstack([rng.normal(size=(count, 3)), np.full((count, 1), 0.5)]).astype(np.float16)
    uv = rng.random((count, 4)).astype(np.float32)
    ch = {POSITION: (F32, 3, np.zeros((count, 3))), NORMAL: (F16, 0x34, n), UV0: (F32, 4, uv)}
    doc, body = gltf.read(gltf.write([mesh_of(mesh_tt("m", count, ch, [0, 1, 2], [submesh(0, 3)]))]))
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    assert gltf.accessor_array(doc, body, attrs["_NORMAL_EXTRA"]).tolist() == [0.5] * count
    assert np.array_equal(gltf.accessor_array(doc, body, attrs["_TEXCOORD_0_EXTRA"]), uv[:, 2:])


def test_normalized_and_small_attributes_are_aligned():
    count = 5
    ch = {POSITION: (F32, 3, rng.normal(size=(count, 3))),
          NORMAL: (SNORM16, 3, rng.integers(-32768, 32768, (count, 3))),
          UV0: (UNORM16, 2, rng.integers(0, 65536, (count, 2))),
          COLOR: (UNORM8, 3, rng.integers(0, 256, (count, 3)))}
    m = mesh_of(mesh_tt("m", count, ch, [0, 1, 2], [submesh(0, 3)]))
    assert m.channels["normal"].normalized and m.channels["normal"].data.dtype == np.int16
    doc, body = gltf.read(gltf.write([m]))
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    col = doc["accessors"][attrs["COLOR_0"]]
    assert col["type"] == "VEC3" and doc["bufferViews"][col["bufferView"]]["byteStride"] == 4
    assert np.array_equal(gltf.accessor_array(doc, body, attrs["COLOR_0"]), ch[COLOR][2])
    uv = gltf.accessor_array(doc, body, attrs["TEXCOORD_0"])
    assert doc["accessors"][attrs["TEXCOORD_0"]]["normalized"] is True
    assert uv[:, 1].tolist() == (65535 - ch[UV0][2][:, 1]).tolist()                      # v flipped exactly
    nrm = gltf.accessor_array(doc, body, attrs["NORMAL"])                                # SNorm -> float (glTF)
    want = np.maximum(ch[NORMAL][2] / 32767.0, -1.0).astype(np.float32) * [1, 1, -1]
    assert nrm.dtype == np.float32 and np.array_equal(nrm, want.astype(np.float32))


def test_infinite_values_are_written_and_nan_ignored_in_minmax():
    pos = np.array([[0, 0, 0], [np.inf, 1, np.nan], [2, np.nan, np.nan]], np.float32)
    doc, _ = gltf.read(gltf.write([mesh_of(mesh_tt("m", 3, {POSITION: (F32, 3, pos)}, [0, 1, 2], [submesh(0, 3)]))]))
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert acc["max"] == [float("inf"), 1.0, -0.0] and acc["min"] == [0.0, 0.0, -0.0]


def test_several_meshes_one_node_each():
    a = mesh_of(mesh_tt("a", 3, channels(3), [0, 1, 2], [submesh(0, 3)]))
    b = mesh_of(mesh_tt("b", 4, channels(4), [], []))
    doc, _ = gltf.read(gltf.write([a, b]))
    assert [n["name"] for n in doc["nodes"]] == ["a", "b"] and doc["scenes"][0]["nodes"] == [0, 1]
    assert doc["meshes"][1]["primitives"][0]["mode"] == 0


# ---------------------------------------------------------------- the reader's mesh input
def test_streamed_vertex_data_is_read_from_the_bundle():
    count = 6
    tt = mesh_tt("m", count, channels(count, half=True, color=False), tris(count, 2), [submesh(0, 6)])
    data = tt["m_VertexData"]["m_DataSize"]
    streamed = dict(tt, m_VertexData=dict(tt["m_VertexData"], m_DataSize=b""),
                    m_StreamData={"offset": 16, "size": len(data), "path": "archive:/CAB-a/CAB-a.resS"})
    env = FakeEnvironment()
    env.resources["CAB-a.resS"] = b"\xee" * 16 + data
    f = env.file("CAB-a")
    f.add(5, "Mesh", streamed)
    view = UnityBundle(env)
    inp = view.mesh_input(view.objects()[0])
    assert inp.vertex_data == data and env.lookups == ["CAB-a.resS"]
    assert np.array_equal(arrays(inp).channels["normal"].data, mesh_of(tt).channels["normal"].data)
    env.resources.clear()
    with pytest.raises(FileNotFoundError, match="not in the bundle"):
        view.mesh_input(view.objects()[0])


def test_sprite_render_mesh_input():
    from fakeunity import pptr, quad, rect, render_data, sprite_tt
    r = rect(0, 0, 40, 20)
    pos, idx = quad(r)
    tt = sprite_tt("s", r, render_data(pptr(3), r, 64, pos, idx))
    m = arrays(mesh_input_from_typetree(tt, VERSION))
    assert np.array_equal(m.channels["position"].data, pos) and m.primitives[0].indices.tolist() == [[0, 1, 2],
                                                                                                    [2, 1, 3]]


def test_mesh_input_of_older_layouts_is_refused_by_the_atom():
    with pytest.raises(ValueError, match="decoded by the reader"):
        arrays(MeshInput("m", (2017, 4, 0, 0), 3, ((0, 0, 0, 3),), b"\0" * 36))


def test_registry_entries():
    assert ATOMS["mesh.arrays"].fn is arrays and ATOMS["gltf.write"].id.startswith("numpy-")
    assert ATOMS["mesh.arrays"].id == f"numpy-{np.__version__}/1"
    c = ATOMS["mesh.arrays"].cost({"vertexCount": 1000, "indexCount": 3000})
    assert c["cpuSeconds"] > 0 and c["peakBytes"] > 0
