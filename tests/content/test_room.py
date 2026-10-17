"""room: material translation from the pass render state (property-driven values) on the documents of a Material and
its Shader, the glTF writer, and room.glb / room.json of a synthetic background's object table."""

import json
import struct

import numpy as np
import pytest

from nnnotes.content import room
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from nnnotes.unity.atoms import gltf
from tests.helpers import spotobjects as so
from tests.helpers.spotobjects import fv, material, shader


def translate(mat_tt, shader_tt):
    return room.translate_material(so.material_json(mat_tt), so.shader_json(shader_tt))


def render_state(mat_tt, shader_tt):
    return room.render_state(so.material_json(mat_tt), so.shader_json(shader_tt))


LIT_BLEND = dict(
    srcBlend=fv(0.0, "_SrcBlend"), destBlend=fv(0.0, "_DstBlend"), culling=fv(0.0, "_Cull"), zWrite=fv(0.0, "_ZWrite")
)
LIT_PROPS = {"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 2.0, "_ZWrite": 1.0}


def test_property_driven_blend_takes_the_material_value():
    sh = shader(props=LIT_PROPS, **LIT_BLEND)
    opaque = translate(material({"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 2.0}), sh)
    assert "alphaMode" not in opaque and "doubleSided" not in opaque
    assert opaque["extras"]["unityRenderState"]["srcBlend"] == 1 and opaque["extras"]["unityRenderState"]["zWrite"] == 1
    blended = translate(material({"_SrcBlend": 5.0, "_DstBlend": 10.0, "_Cull": 0.0, "_ZWrite": 0.0}), sh)
    assert blended["alphaMode"] == "BLEND" and blended["doubleSided"] is True
    assert blended["extras"]["unityRenderState"]["zWrite"] == 0
    with pytest.raises(NotImplementedError, match="cull mode 1"):
        translate(material({"_SrcBlend": 1.0, "_DstBlend": 0.0, "_Cull": 1.0}), sh)
    with pytest.raises(NotImplementedError, match="blend 1/1"):
        translate(material({"_SrcBlend": 1.0, "_DstBlend": 1.0}), sh)


def test_missing_material_value_falls_back_to_the_shader_default_then_the_stored_value():
    sh = shader(props=LIT_PROPS, **LIT_BLEND)
    rs = render_state(material({}), sh)  # the material has none of the properties
    assert (rs["srcBlend"], rs["dstBlend"], rs["cull"], rs["zWrite"]) == (1, 0, 2, 1)
    rs = render_state(material({}), shader(**LIT_BLEND))  # no shader default either: the stored value
    assert (rs["srcBlend"], rs["dstBlend"], rs["cull"], rs["zWrite"]) == (0, 0, 0, 0)
    # a fixed value ignores a material property of the same name
    rs = render_state(material({"culling": 0.0, "_Cull": 0.0}), shader(culling=fv(2.0)))
    assert rs["cull"] == 2


@pytest.mark.parametrize(
    "key, field",
    [
        ("srcBlend", "srcBlend"),
        ("dstBlend", "destBlend"),
        ("srcBlendAlpha", "srcBlendAlpha"),
        ("dstBlendAlpha", "destBlendAlpha"),
        ("blendOp", "blendOp"),
        ("blendOpAlpha", "blendOpAlpha"),
        ("colorMask", "colMask"),
        ("cull", "culling"),
        ("zWrite", "zWrite"),
        ("zTest", "zTest"),
        ("alphaToMask", "alphaToMask"),
    ],
)
def test_every_state_field_resolves_through_its_property(key, field):
    sh = shader(props={"_P": 3.0}, **{field: fv(7.0, "_P")})
    assert render_state(material({"_P": 6.0}), sh)[key] == 6  # the material's float
    assert render_state(material(ints={"_P": 5}), sh)[key] == 5  # or int
    assert render_state(material(), sh)[key] == 3  # the shader's default
    assert render_state(material(), shader(**{field: fv(7.0, "_P")}))[key] == 7  # the stored value
    assert render_state(material({"_P": 6.0}), shader(**{field: fv(7.0)}))[key] == 7  # fixed: stored value


def test_blend_op_other_than_add_is_not_approximated():
    sh = shader(name="Unlit/Transparent", srcBlend=fv(5.0), destBlend=fv(10.0), blendOp=fv(2.0, "_BlendOp"))
    with pytest.raises(NotImplementedError, match="blend op 2"):
        translate(material(), sh)
    assert translate(material({"_BlendOp": 0.0}), sh)["alphaMode"] == "BLEND"


def test_fixed_state_translation():
    sh = shader(name="Unlit/Transparent Cutout", tags=[("RenderType", "TransparentCutout")])
    cutout = translate(material({"_Cutoff": 0.5}), sh)
    assert cutout["alphaMode"] == "MASK" and cutout["alphaCutoff"] == 0.5
    assert cutout["extensions"] == {"KHR_materials_unlit": {}}
    rs = cutout["extras"]["unityRenderState"]
    assert rs == {
        "srcBlend": 1,
        "dstBlend": 0,
        "srcBlendAlpha": 1,
        "dstBlendAlpha": 0,
        "blendOp": 0,
        "blendOpAlpha": 0,
        "colorMask": 15,
        "cull": 2,
        "zWrite": 1,
        "zTest": 4,
        "alphaToMask": 0,
    }
    with pytest.raises(NotImplementedError, match="shader not translated"):
        translate(material(), shader(name="Custom/Other"))


# ---------------------------------------------------------------- the glTF writer
def glb_chunks(data: bytes):
    magic, version, size = struct.unpack_from("<III", data, 0)
    assert (magic, version, size) == (0x46546C67, 2, len(data))
    chunks, at = [], 12
    while at < len(data):
        n, kind = struct.unpack_from("<II", data, at)
        chunks.append((kind, data[at + 8 : at + 8 + n]))
        at += 8 + n
    return chunks


def test_textures_with_the_same_png_bytes_share_one_image():
    glb = room.Glb()
    linear, point = room.sampler({}), room.sampler({"m_TextureSettings": {"m_FilterMode": 0}})
    got = [
        glb.texture(png, name, s)
        for png, name, s in ((b"png-1", "a", linear), (b"png-1", "b", point), (b"png-2", "c", linear))
    ]
    assert got == [0, 1, 2]
    assert glb.textures == [
        {"name": "a", "source": 0, "sampler": 0},
        {"name": "b", "source": 0, "sampler": 1},
        {"name": "c", "source": 1, "sampler": 0},
    ]
    assert [im["name"] for im in glb.images] == ["a", "c"] and len(glb.bufferViews) == 2


def test_a_room_without_geometry_is_a_valid_empty_gltf():
    """No empty arrays (glTF requires the arrays it has to be non-empty), no buffer and no BIN chunk."""
    chunks = glb_chunks(room.Glb().bytes({"backgroundKey": "k"}))
    assert [kind for kind, _ in chunks] == [0x4E4F534A]
    assert json.loads(chunks[0][1]) == {
        "asset": {"version": "2.0", "generator": "nnnotes/room", "extras": {"backgroundKey": "k"}},
        "scene": 0,
        "scenes": [{}],
    }


def test_a_room_with_geometry_keeps_its_arrays_and_bin_chunk():
    glb = room.Glb()
    glb.texture(b"png-1", "a", room.sampler({}))
    chunks = glb_chunks(glb.bytes({}))
    assert [kind for kind, _ in chunks] == [0x4E4F534A, 0x004E4942]
    doc = json.loads(chunks[0][1])
    assert doc["buffers"] == [{"byteLength": 5}] and len(chunks[1][1]) == 8  # the chunk is padded to 4 bytes
    assert list(doc) == ["asset", "scene", "scenes", "bufferViews", "buffers", "textures", "images", "samplers"]


# ---------------------------------------------------------------- a background's object table
def objects_of(tmp_path, objs=None) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(so.table(depot, objs or so.room_objects(), [so.ROOM_ROOT]), depot)


def test_room_bakes_the_drawn_meshes_and_lists_what_draws_nothing(tmp_path):
    glb, doc = room.room(objects_of(tmp_path), so.BACKGROUND)
    gdoc, body = gltf.read(glb)
    assert [n["name"] for n in gdoc["nodes"]] == ["quad", "quad2", "quad"]  # wall, floor, wall2: hierarchy order
    assert [n["extras"]["unityActive"] for n in gdoc["nodes"]] == [True, False, True]
    assert doc == {
        "backgroundKey": so.BACKGROUND,
        "glb": "room.glb",
        "meshCount": 3,
        "inactiveMeshes": 1,
        "nullMaterialSubmeshes": [{"mesh": "quad2", "path": "room/floor", "submesh": 1}],
        "nullMeshFilters": ["room/nomesh"],
        "emptyMeshes": [{"mesh": "empty_mesh", "path": "room/empty"}, {"mesh": "points", "path": "room/points"}],
        "nodes": [
            {"mesh": "CAB-r:10", "path": "room/wall"},
            {"mesh": "CAB-r:11", "path": "room/floor"},
            {"mesh": "CAB-r:10", "path": "room/wall2"},
        ],
        "materialIds": ["CAB-r:20", "CAB-r:21"],
        **{k: doc[k] for k in ("materials", "textures", "samplers")},
    }
    assert gdoc["asset"]["extras"] == {"backgroundKey": so.BACKGROUND}
    prim = gdoc["meshes"][0]["primitives"][0]
    pos = gltf.accessor_array(gdoc, body, prim["attributes"]["POSITION"])
    # the mesh's vertices under the root at (1, 0, 2), in glTF's space (z negated)
    expected = np.array(so.VERTICES) + [1.0, 0.0, 2.0]
    expected[:, 2] *= -1.0
    assert np.allclose(pos, expected)
    assert np.allclose(gltf.accessor_array(gdoc, body, prim["attributes"]["NORMAL"]), [[0.0, 0.0, 1.0]] * 4)
    uv = gltf.accessor_array(gdoc, body, prim["attributes"]["TEXCOORD_0"])
    assert np.allclose(uv, [(u, 1.0 - v) for u, v in so.UVS])  # v flipped once
    assert gltf.accessor_array(gdoc, body, prim["indices"]).reshape(-1).tolist() == [0, 2, 1, 0, 3, 2]
    assert len(gdoc["meshes"][1]["primitives"]) == 1  # the floor's second submesh has no material


def test_room_materials_and_textures_are_shared_by_object(tmp_path):
    glb, doc = room.room(objects_of(tmp_path), so.BACKGROUND)
    gdoc, _ = gltf.read(glb)
    assert [m["name"] for m in gdoc["materials"]] == ["a_wall", "a_floor"]
    assert [p["material"] for m in gdoc["meshes"] for p in m["primitives"]] == [0, 1, 0]
    wall, floor = gdoc["materials"]
    assert wall["alphaMode"] == "BLEND" and wall["extensions"] == {"KHR_materials_unlit": {}}
    assert wall["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert floor["pbrMetallicRoughness"] == {
        "baseColorFactor": [1.0, 0.5, 0.25, 1.0],
        "metallicFactor": 0.0,
        "roughnessFactor": 0.75,
        "baseColorTexture": {
            "index": 1,
            "extensions": {"KHR_texture_transform": {"scale": [2.0, 2.0], "offset": [0.0, -1.5]}},
        },
    }
    assert gdoc["extensionsUsed"] == ["KHR_materials_unlit", "KHR_texture_transform"]
    assert doc["textures"] == ["wall_a", "floor_b"] and len(gdoc["images"]) == 2
    assert doc["samplers"] == [
        {"magFilter": room.LINEAR, "minFilter": room.LINEAR, "wrapS": room.CLAMP, "wrapT": room.CLAMP},
        {"magFilter": room.NEAREST, "minFilter": room.NEAREST, "wrapS": room.REPEAT, "wrapT": room.REPEAT},
    ]
    assert [m["name"] for m in doc["materials"]] == ["a_wall", "a_floor"]
    assert doc["materials"][1]["alphaMode"] == "OPAQUE" and doc["materials"][0]["unlit"] is True


def test_a_mesh_the_conversion_failed_on_is_an_error(tmp_path):
    objs = so.room_objects()
    objs[so.EMPTY] = ("Mesh", "broken", {"json": {}}, {"status": "generic", "reason": "generic.error"})
    with pytest.raises(ValueError, match="room/empty: mesh 'broken' was not converted"):
        room.room(objects_of(tmp_path, objs), so.BACKGROUND)


def test_a_material_without_its_shader_is_an_error(tmp_path):
    objs = so.room_objects()
    del objs[so.LIT]
    with pytest.raises(ValueError, match="material 'a_floor' has no shader in the object table"):
        room.room(objects_of(tmp_path, objs), so.BACKGROUND)


def test_room_names():
    assert room.room_name(so.BACKGROUND) == "home_a"
    with pytest.raises(ValueError, match="not a spot background key"):
        room.room_name(so.SITUATION)
