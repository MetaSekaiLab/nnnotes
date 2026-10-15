"""A synthetic Live2D model as a product's object table (nnnotes.objects/1) whose files are in a depot: a prefab with
a moc, two drawables (one masked), a physics rig, MotionSync, eye blink and lip sync parameters, a clip, its fade
motion data, an expression, the mask materials of the built-in files and a MonoScript."""

from __future__ import annotations

import struct

from nnnotes.pipeline import contract

KEY = "Character/Live2D/c001/m001/model/m001"
CAB, RES = "CAB-m", "resources.assets"
CUBISM = "Live2D.Cubism|Live2D.Cubism.Core|"
FRAMEWORK = "Live2D.Cubism|Live2D.Cubism.Framework|"
ALV, MASK = "_ADDITIONAL_LIGHTS_VERTEX", "CUBISM_MASK_ON"
ROOT, MOC, TEX0, TEX1, MAT0, MAT1, SHADER = (f"{CAB}:{i}" for i in (1, 10, 20, 21, 30, 31, 40))
CLIP, FADE, EXPR, SCRIPT = f"{CAB}:50", f"{CAB}:60", f"{CAB}:70", "CAB-s:1"
MASK_MAT, CULL_MAT, MASK_SHADER = f"{RES}:4", f"{RES}:5", f"{RES}:6"
CANVAS = (100.0, 0.5, 0.5, 20.0, 30.0)


def ref(oid: str, cls: str | None = None) -> dict:
    file, _, pid = oid.rpartition(":")
    r = {"file": file, "pathId": pid}
    if cls:
        r["class"] = cls
    return {"$ref": r}


def moc3() -> bytes:
    """A moc3 header whose section table's entry 1 points at the canvas info."""
    data = bytearray(0x80)
    data[:4] = b"MOC3"
    data[4] = 5
    struct.pack_into("<I", data, 0x44, 0x60)
    struct.pack_into("<5f", data, 0x60, *CANVAS)
    return bytes(data)


def mono(script: str, **fields) -> dict:
    return {"type": "MonoBehaviour", "pathId": "0", "script": script, "fields": fields}


def node(path: str, *components) -> dict:
    return {"path": path, "name": path.rsplit("/", 1)[-1], "components": list(components)}


def prefab() -> dict:
    rig = {
        "Gravity": {"x": 0.0, "y": -1.0},
        "Wind": {"x": 0.0, "y": 0.0},
        "Fps": 0.0,
        "SubRigs": [
            {
                "Name": "Hair",
                "Input": [{"SourceId": "ParamAngleX", "Weight": 60.0, "SourceComponent": 0, "IsInverted": 0}],
                "Output": [
                    {
                        "DestinationId": "ParamHair",
                        "ParticleIndex": 1,
                        "AngleScale": 1.5,
                        "Weight": 100.0,
                        "SourceComponent": 2,
                        "IsInverted": 1,
                    }
                ],
                "Particles": [
                    {
                        "InitialPosition": {"x": 0.0, "y": 0.0},
                        "Mobility": 1.0,
                        "Delay": 1.0,
                        "Acceleration": 1.0,
                        "Radius": 0.0,
                    }
                ],
                "Normalization": {
                    "Position": {"Minimum": -10.0, "Default": 0.0, "Maximum": 10.0},
                    "Angle": {"Minimum": -10.0, "Default": 0.0, "Maximum": 10.0},
                },
            }
        ],
    }
    return {
        "name": "m001",
        "nodes": [
            node(
                "m001",
                mono(CUBISM + "CubismModel", _moc=ref(MOC, "MonoBehaviour")),
                mono(FRAMEWORK + "CubismPhysicsController", _rig=rig),
                mono("Live2D.Cubism|Live2D.Cubism.Framework.MotionSync|CubismMotionSyncController"),
                mono("Assembly-CSharp||Live2DMotionSyncCriAudioInput"),
                {"type": "Animator", "pathId": "2", "fields": {}},
            ),
            node("m001/Parameters"),
            node(
                "m001/Parameters/ParamEyeLOpen",
                mono(CUBISM + "CubismParameter", Value=1.0),
                mono(FRAMEWORK + "CubismEyeBlinkParameter"),
            ),
            node(
                "m001/Parameters/ParamMouthOpenY",
                mono(CUBISM + "CubismParameter", Value=0.0),
                mono(FRAMEWORK + "CubismMouthParameter"),
            ),
            node("m001/Drawables"),
            node(
                "m001/Drawables/ArtMesh0",
                mono("Live2D.Cubism|Live2D.Cubism.Rendering|CubismRenderer", _mainTexture=ref(TEX1, "Texture2D")),
                {"type": "MeshRenderer", "pathId": "3", "fields": {"m_Materials": [ref(MAT0, "Material")]}},
            ),
            node(
                "m001/Drawables/ArtMesh1",
                mono("Live2D.Cubism|Live2D.Cubism.Rendering|CubismRenderer", _mainTexture=ref(TEX0, "Texture2D")),
                {"type": "MeshRenderer", "pathId": "4", "fields": {"m_Materials": [ref(MAT1, "Material")]}},
            ),
        ],
    }


def variant(n: int, keywords, platform="gles3", type_="GLES3") -> dict:
    return {
        "file": f"s/{platform}/s0p0_progVertex_{n}.glsl",
        "platform": platform,
        "subShader": 0,
        "pass": 0,
        "stage": "progVertex",
        "type": type_,
        "keywords": list(keywords),
        "role": f"program:{platform}/s0p0_progVertex_{n}",
    }


SHADER_VARIANTS = [
    variant(0, []),
    variant(1, [MASK]),
    variant(2, [ALV]),
    variant(3, [MASK, ALV]),
    variant(4, [], "vulkan", "SPIRV"),
]
MASK_VARIANTS = [variant(0, []), variant(1, ["CUBISM_INVERT"])]


def fade() -> dict:
    keys = [
        {"time": 0.0, "value": 1.0, "inSlope": 0.0, "outSlope": 0.0, "weightedMode": 0, "inWeight": 0, "outWeight": 0},
        {"time": 1.0, "value": 0.0, "inSlope": 0.0, "outSlope": 0.0, "weightedMode": 0, "inWeight": 0, "outWeight": 0},
    ]
    return {
        "$script": FRAMEWORK + "CubismFadeMotionData",
        "m_Name": "idle",
        "MotionName": "motions/idle.motion3.json",
        "ModelFadeInTime": 0.5,
        "ModelFadeOutTime": -1.0,
        "FadeInTime": 1.0,
        "FadeOutTime": 1.0,
        "ParameterIds": ["ParamEyeLOpen"],
        "ParameterCurves": [{"m_Curve": keys}],
        "ParameterFadeInTimes": [-1.0],
        "ParameterFadeOutTimes": [-1.0],
        "MotionLength": 1.0,
    }


def objects() -> dict:
    """{object id: (class, name, {role: bytes | JSON document})} of the model."""
    clip_binding = {
        "pathCrc": 0,
        "attributeCrc": 0,
        "typeID": 114,
        "customType": 0,
        "pptr": False,
        "int": False,
        "serializeReference": False,
        "script": ref("CAB-s:2"),
        "scriptClass": CUBISM + "CubismParameter",
    }
    from nnnotes.unity.bindings import crc

    clip_binding.update(pathCrc=crc("Parameters/ParamEyeLOpen"), attributeCrc=crc("Value"))
    material = {"material": "Unlit", "shader": ref(SHADER, "Shader"), "keywords": [], "floats": {}, "colors": {}}
    return {
        ROOT: ("GameObject", "m001", {"json": prefab()}),
        MOC: ("MonoBehaviour", "m001", {"json": {"$script": CUBISM + "CubismMoc"}, "blob:_bytes": moc3()}),
        TEX0: ("Texture2D", "texture_00", {"json": {"m_Name": "texture_00"}, "image": b"png0"}),
        TEX1: ("Texture2D", "texture_01", {"json": {"m_Name": "texture_01"}, "image": b"png1"}),
        MAT0: ("Material", "Unlit", {"json": material}),
        MAT1: ("Material", "UnlitMasked", {"json": {**material, "material": "UnlitMasked", "keywords": [MASK]}}),
        SHADER: (
            "Shader",
            "Live2D Cubism/Unlit",
            {
                "json": {"name": "Live2D Cubism/Unlit"},
                "index": {"name": "Live2D Cubism/Unlit", "variants": SHADER_VARIANTS},
                "typetree": {"m_Name": ""},
                **{v["role"]: f"program {v['role']}".encode() for v in SHADER_VARIANTS},
            },
        ),
        CLIP: ("AnimationClip", "idle", {"json": {"name": "idle", "sampleRate": 30.0, "bindings": [clip_binding]}}),
        FADE: ("MonoBehaviour", "idle", {"json": fade()}),
        EXPR: (
            "MonoBehaviour",
            "smile.exp3",
            {
                "json": {
                    "$script": FRAMEWORK + "CubismExpressionData",
                    "m_Name": "smile.exp3",
                    "Type": "Live2D Expression",
                    "FadeInTime": 0.5,
                    "FadeOutTime": 0.5,
                    "Parameters": [{"Id": "ParamEyeLOpen", "Value": 0.0, "Blend": 2}],
                }
            },
        ),
        SCRIPT: ("MonoScript", "CubismModel", {"json": {"m_ClassName": "CubismModel"}}),
        MASK_MAT: ("Material", "Mask", {"json": {**material, "material": "Mask", "shader": ref(MASK_SHADER)}}),
        CULL_MAT: (
            "Material",
            "MaskCulling",
            {"json": {**material, "material": "MaskCulling", "shader": ref(MASK_SHADER)}},
        ),
        MASK_SHADER: (
            "Shader",
            "Live2D Cubism/Mask",
            {
                "json": {"name": "Live2D Cubism/Mask"},
                "index": {"name": "Live2D Cubism/Mask", "variants": MASK_VARIANTS},
                **{v["role"]: b"mask program" for v in MASK_VARIANTS},
            },
        ),
    }


def table(depot, objs: dict | None = None, roots=(ROOT, MASK_MAT, CULL_MAT)) -> dict:
    """The object table of `objs` (objects()), every file put in `depot`."""
    out = {}
    for oid, (cls, name, files) in sorted((objs if objs is not None else objects()).items()):
        recs = {}
        for role, body in sorted(files.items()):
            data = body if isinstance(body, bytes) else contract.encode(body)
            ext = ("png" if role == "image" else "bin") if isinstance(body, bytes) else "json"
            recs[role] = {"sha256": depot.put(data), "size": len(data), "ext": ext}
        out[oid] = {"status": "exported", "class": cls, "name": name, "files": recs}
    return {"schema": contract.OBJECTS, "roots": sorted(roots), "keys": {}, "objects": out, "missing": []}
