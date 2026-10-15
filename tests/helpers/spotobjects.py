"""A synthetic spot as products' object tables (nnnotes.objects/1) whose files are in a depot: a background prefab
(content.room) with drawn, inactive, hidden, mesh-less and empty meshes, two materials of two shaders and their
textures; a situation prefab (content.spot) with tap targets, Spine characters, two skeletons sharing one atlas; and
the master tables of the spot. Shaders and materials are made as typetrees and turned into their object documents
(formats.shader.parsed_summary, unity.convert.material.material_doc)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from nnnotes.formats.shader import parsed_summary
from nnnotes.pipeline import contract
from nnnotes.unity.atoms import gltf
from nnnotes.unity.atoms.mesh import arrays
from nnnotes.unity.atoms.reader import mesh_input_from_typetree
from nnnotes.unity.convert.material import material_doc
from tests.helpers.fakeunity import F32, NORMAL, POSITION, UV0, VERSION, mesh_tt, submesh

FIXED = "<noninit>"
BACKGROUND = "Spot/home_a/Background/home_a"
SITUATION = "Spot/home_a/home_a_01/home_a_01"
CAB, SIT = "CAB-r", "CAB-s"
ROOM_ROOT, QUAD, QUAD2, POINTS, EMPTY = (f"{CAB}:{i}" for i in (1, 10, 11, 12, 13))
MAT_WALL, MAT_FLOOR, UNLIT, LIT, TEX_WALL, TEX_FLOOR = (f"{CAB}:{i}" for i in (20, 21, 30, 31, 40, 41))
SIT_ROOT, SDA, SDA2, ATLAS, SKEL, SKEL2, ATLAS_TEXT_ASSET = (f"{SIT}:{i}" for i in (1, 50, 51, 52, 60, 61, 62))
PAGE_MAT1, PAGE_MAT2, PAGE1, PAGE2 = (f"{SIT}:{i}" for i in (70, 71, 80, 81))
SPINE = "spine-unity|Spine.Unity|"
GAME = "Assembly-CSharp||"
ATLAS_TEXT = "\np1.png\nsize: 2,2\nformat: RGBA8888\nr\n  bounds: 0,0,1,1\n\np2.png\nsize: 1,1\nr2\n  bounds: 0,0,1,1\n"
V0, V1, Q0 = {"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 1.0, "z": 1.0}, {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
ROOM_OFFSET = {"x": 1.0, "y": 0.0, "z": 2.0}  # the background root's own position
VERTICES = [(0.0, 0.0, 0.5), (1.0, 0.0, 0.5), (1.0, 1.0, 0.5), (0.0, 1.0, 0.5)]
UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def ref(oid: str | None, cls: str | None = None):
    if oid is None:
        return None
    file, _, pid = oid.rpartition(":")
    r = {"file": file, "pathId": pid}
    if cls:
        r["class"] = cls
    return {"$ref": r}


# ---------------------------------------------------------------- shader and material typetrees
def fv(val, name=FIXED):
    return {"val": val, "name": name}


def shader(name="Universal Render Pipeline/Lit", props=None, tags=(), **state):
    """A shader typetree: one subshader, one pass with render target 0 and the other states given (defaults:
    opaque, back-face culling, depth write on, LEqual, all colour channels)."""
    rt = {
        "srcBlend": fv(1.0),
        "destBlend": fv(0.0),
        "srcBlendAlpha": fv(1.0),
        "destBlendAlpha": fv(0.0),
        "blendOp": fv(0.0),
        "blendOpAlpha": fv(0.0),
        "colMask": fv(15.0),
    }
    st = {"culling": fv(2.0), "zWrite": fv(1.0), "zTest": fv(4.0), "alphaToMask": fv(0.0)}
    for k, v in state.items():
        (rt if k in rt else st)[k] = v
    return {
        "m_ParsedForm": {
            "m_Name": name,
            "m_PropInfo": {
                "m_Props": [
                    {
                        "m_Name": n,
                        "m_Type": 2,
                        "m_DefValue[0]": d,
                        "m_DefValue[1]": 0.0,
                        "m_DefValue[2]": 0.0,
                        "m_DefValue[3]": 0.0,
                    }
                    for n, d in (props or {}).items()
                ]
            },
            "m_SubShaders": [
                {
                    "m_Tags": {"tags": list(tags)},
                    "m_Passes": [{"m_State": {"rtBlend0": rt, **st}, "m_Tags": {"tags": []}}],
                }
            ],
        }
    }


def material(floats=None, ints=None, name="m", textures=None, colors=None, shader_ref=None):
    """A material typetree; `textures` {slot: (texture reference, scale (x, y), offset (x, y))}."""
    return {
        "m_Name": name,
        "m_Shader": shader_ref,
        "m_SavedProperties": {
            "m_TexEnvs": [
                [k, {"m_Texture": t, "m_Scale": {"x": s[0], "y": s[1]}, "m_Offset": {"x": o[0], "y": o[1]}}]
                for k, (t, s, o) in (textures or {}).items()
            ],
            "m_Ints": list((ints or {}).items()),
            "m_Floats": list((floats or {}).items()),
            "m_Colors": list((colors or {}).items()),
        },
    }


def material_json(tt: dict) -> dict:
    """The `json` of a Material of typetree `tt` (its references already {"$ref"})."""
    return material_doc(tt, lambda r: r)


def shader_json(tt: dict) -> dict:
    return parsed_summary(tt)


# ---------------------------------------------------------------- object files
def png(color, size=(2, 2)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def mesh_glb(name: str, indices=(), submeshes=()) -> bytes:
    """The `mesh` of a Mesh: four vertices (VERTICES, UVS, normals -z) with `indices` in `submeshes`."""
    ch = {
        POSITION: (F32, 3, np.array(VERTICES)),
        NORMAL: (F32, 3, np.tile([0.0, 0.0, -1.0], (4, 1))),
        UV0: (F32, 2, np.array(UVS)),
    }
    return gltf.write([arrays(mesh_input_from_typetree(mesh_tt(name, 4, ch, list(indices), list(submeshes)), VERSION))])


def texture(name: str, filter_mode: int, wrap: int) -> dict:
    return {
        "m_Name": name,
        "m_MipCount": 1,
        "m_TextureSettings": {"m_FilterMode": filter_mode, "m_WrapU": wrap, "m_WrapV": wrap},
    }


def node(path: str, *components, position=V0, active=True) -> dict:
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "active": active,
        "localPosition": position,
        "localRotation": Q0,
        "localScale": V1,
        "components": list(components),
    }


def component(type_: str, **fields) -> dict:
    return {"type": type_, "pathId": "0", "fields": fields}


def mono(script: str, **fields) -> dict:
    return {"type": "MonoBehaviour", "pathId": "0", "script": script, "fields": fields}


def drawn(path: str, mesh: str | None, materials, enabled=1, **kw) -> dict:
    return node(
        path,
        component("MeshFilter", m_Mesh=ref(mesh, "Mesh")),
        component("MeshRenderer", m_Enabled=enabled, m_Materials=[ref(m, "Material") for m in materials]),
        **kw,
    )


# ---------------------------------------------------------------- the background
def room_objects() -> dict:
    """{object id: (class, name, files, entry extras)} of the background BACKGROUND: under the root `room` (at
    ROOM_OFFSET) `wall` (QUAD, MAT_WALL), `floor` (inactive, QUAD2: two submeshes, the second without a material),
    `wall2` (QUAD and MAT_WALL again), `hidden` (a disabled renderer), `nomesh` (no mesh), `empty` (a mesh the
    conversion left empty) and `points` (a mesh without an index buffer)."""
    prefab = {
        "name": "room",
        "nodes": [
            node("room", position=ROOM_OFFSET),
            drawn("room/wall", QUAD, [MAT_WALL]),
            drawn("room/floor", QUAD2, [MAT_FLOOR, None], active=False),
            drawn("room/wall2", QUAD, [MAT_WALL]),
            drawn("room/hidden", QUAD, [MAT_WALL], enabled=0),
            drawn("room/nomesh", None, []),
            drawn("room/empty", EMPTY, [MAT_WALL]),
            drawn("room/points", POINTS, [MAT_WALL]),
        ],
    }
    unlit = shader(name="Unlit/Transparent", srcBlend=fv(5.0), destBlend=fv(10.0))
    wall = material(
        name="a_wall",
        shader_ref=ref(UNLIT, "Shader"),
        textures={"_MainTex": (ref(TEX_WALL, "Texture2D"), (1.0, 1.0), (0.0, 0.0)), "_Mask": (None, (1, 1), (0, 0))},
    )
    floor = material(
        name="a_floor",
        shader_ref=ref(LIT, "Shader"),
        textures={"_BaseMap": (ref(TEX_FLOOR, "Texture2D"), (2.0, 2.0), (0.0, 0.5))},
        floats={"_Metallic": 0.0, "_Smoothness": 0.25},
        colors={"_BaseColor": {"r": 1.0, "g": 0.5, "b": 0.25, "a": 1.0}},
    )
    return {
        ROOM_ROOT: ("GameObject", "room", {"json": prefab}, {}),
        QUAD: ("Mesh", "quad", {"mesh": mesh_glb("quad", [0, 1, 2, 0, 2, 3], [submesh(0, 6)])}, {}),
        QUAD2: ("Mesh", "quad2", {"mesh": mesh_glb("quad2", [0, 1, 2, 0, 2, 3], [submesh(0, 3), submesh(3, 3)])}, {}),
        POINTS: ("Mesh", "points", {"mesh": mesh_glb("points")}, {}),
        EMPTY: (
            "Mesh",
            "empty_mesh",
            {"json": {"m_Name": "empty_mesh"}},
            {"status": "generic", "reason": "empty.mesh"},
        ),
        MAT_WALL: ("Material", "a_wall", {"json": material_json(wall)}, {}),
        MAT_FLOOR: ("Material", "a_floor", {"json": material_json(floor)}, {}),
        UNLIT: ("Shader", "Unlit/Transparent", {"json": shader_json(unlit)}, {}),
        LIT: ("Shader", "Universal Render Pipeline/Lit", {"json": shader_json(shader())}, {}),
        TEX_WALL: ("Texture2D", "wall_a", {"json": texture("wall_a", 1, 1), "image": png((200, 120, 40, 255))}, {}),
        TEX_FLOOR: ("Texture2D", "floor_b", {"json": texture("floor_b", 0, 0), "image": png((9, 8, 7, 255))}, {}),
    }


# ---------------------------------------------------------------- the situation
ANIMATION = {
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
HEADER = {"m_Enabled": 1, "m_Script": ref("CAB-x:9", "MonoScript"), "m_Name": ""}
SITUATION_OFFSET = {"x": 0.0, "y": 0.0, "z": 1.0}


def skeleton_data(name: str, skeleton: str) -> dict:
    return {
        "$script": SPINE + "SkeletonDataAsset",
        "m_Name": name,
        "skeletonJSON": ref(skeleton, "TextAsset"),
        "atlasAssets": [ref(ATLAS, "MonoBehaviour")],
        "scale": 0.01,
        "defaultMix": 0.2,
        "fromAnimation": ["a"],
        "toAnimation": ["b"],
        "duration": [0.5],
    }


def situation_objects() -> dict:
    """{object id: (class, name, files, entry extras)} of the situation SITUATION: the root `sit` (at
    SITUATION_OFFSET) with SpotSituationSettings and SpotCharacterController; `sit/chr7` (character 7, no focus, a
    Spine character without animation) before `sit/chr5` (character 5 at (2, 0, 0), focus `sit/chr5/focus` at
    (0, 1, 0), a Spine character driving `sit/chr5/spine`'s SkeletonAnimation of SDA); skeletons SDA (binary .skel)
    and SDA2 (JSON) sharing the two-page atlas ATLAS."""
    prefab = {
        "name": "sit",
        "nodes": [
            node(
                "sit",
                mono(GAME + "SpotSituationSettings", **HEADER, backgroundPosition=V0, cameraFov=30.0),
                mono(
                    GAME + "SpotCharacterController",
                    **HEADER,
                    _spotSpineCharacters=[{"component": "MonoBehaviour", "gameObject": "sit/chr5"}],
                    _characters=[],
                    _speed=2.0,
                ),
                position=SITUATION_OFFSET,
            ),
            node(
                "sit/chr7",
                mono(GAME + "SpotCharacter", **HEADER, _characterId=7, _focus=None, _radius=0.5),
                mono(GAME + "SpotSpineCharacter", **HEADER, _animation=None),
            ),
            node(
                "sit/chr5",
                mono(GAME + "SpotCharacter", **HEADER, _characterId=5, _focus={"transform": "sit/chr5/focus"}),
                mono(
                    GAME + "SpotSpineCharacter",
                    **HEADER,
                    _animation={
                        "component": "MonoBehaviour",
                        "gameObject": "sit/chr5/spine",
                        "class": "SkeletonAnimation",
                    },
                ),
                position={"x": 2.0, "y": 0.0, "z": 0.0},
            ),
            node("sit/chr5/focus", position={"x": 0.0, "y": 1.0, "z": 0.0}),
            node(
                "sit/chr5/spine",
                mono(SPINE + "SkeletonAnimation", **HEADER, skeletonDataAsset=ref(SDA, "MonoBehaviour"), **ANIMATION),
            ),
        ],
    }
    page = lambda tex: {"json": material_json(material(textures={"_MainTex": (ref(tex, "Texture2D"), (1, 1), (0, 0))}))}  # noqa: E731
    return {
        SIT_ROOT: ("GameObject", "sit", {"json": prefab}, {}),
        SDA: ("MonoBehaviour", "chr_SkeletonData", {"json": skeleton_data("chr_SkeletonData", SKEL)}, {}),
        SDA2: ("MonoBehaviour", "chr2_SkeletonData", {"json": skeleton_data("chr2_SkeletonData", SKEL2)}, {}),
        ATLAS: (
            "MonoBehaviour",
            "chr_Atlas",
            {
                "json": {
                    "$script": SPINE + "SpineAtlasAsset",
                    "m_Name": "chr_Atlas",
                    "atlasFile": ref(ATLAS_TEXT_ASSET, "TextAsset"),
                    "materials": [ref(PAGE_MAT1, "Material"), ref(PAGE_MAT2, "Material")],
                }
            },
            {},
        ),
        SKEL: ("TextAsset", "chr", {"json": {"m_Name": "chr"}, "data": b"\x00binary"}, {}),
        SKEL2: ("TextAsset", "chr2", {"json": {"m_Name": "chr2"}, "data": b' {"skeleton": 1}'}, {}),
        ATLAS_TEXT_ASSET: ("TextAsset", "chr", {"json": {"m_Name": "chr"}, "data": ATLAS_TEXT.encode()}, {}),
        PAGE_MAT1: ("Material", "p1", page(PAGE1), {}),
        PAGE_MAT2: ("Material", "p2", page(PAGE2), {}),
        PAGE1: ("Texture2D", "p1", {"json": texture("p1", 1, 1), "image": png((1, 2, 3, 4))}, {}),
        PAGE2: ("Texture2D", "p2", {"json": texture("p2", 1, 1), "image": png((9, 8, 7, 6), (1, 1))}, {}),
    }


MASTER = {
    "MasterHomeSpot": [
        {"_id": 3, "_nameTextId": 900, "_backgroundAssetPath": BACKGROUND, "_situationAssetPath": SITUATION},
        {"_id": 4, "_nameTextId": 901, "_backgroundAssetPath": BACKGROUND, "_situationAssetPath": "Spot/b/b_01/b_01"},
    ],
    "MasterText": [{"_id": 900, "_japanese": "カフェ", "_english": "Cafe"}],
    "MasterStoryHomeSpotTapTalkEpisode": [
        {"_spotId": 3, "_characterId": 7, "_advId": 71},
        {"_spotId": 4, "_characterId": 7, "_advId": 99},
        {"_spotId": 3, "_characterId": 5, "_advId": 51},
        {"_spotId": 3, "_characterId": 7, "_advId": 72},
    ],
}


def table(depot, objs: dict, roots) -> dict:
    """The object table of `objs` (room_objects(), situation_objects()), every file put in `depot`."""
    out = {}
    for oid, (cls, name, files, extra) in sorted(objs.items()):
        recs = {}
        for role, body in sorted(files.items()):
            data = body if isinstance(body, bytes) else contract.encode(body)
            ext = {"image": "png", "mesh": "glb"}.get(role, "bin") if isinstance(body, bytes) else "json"
            recs[role] = {"sha256": depot.put(data), "size": len(data), "ext": ext}
        out[oid] = {"status": "exported", "class": cls, "name": name, "files": recs, **extra}
    return {"schema": contract.OBJECTS, "roots": sorted(roots), "objects": out, "missing": []}
