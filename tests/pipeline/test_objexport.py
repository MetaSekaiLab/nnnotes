"""unity.export and sprite.crop on synthetic bundles (fakeunity environments behind a stand-in reader atom, with
their real censuses): one task per unit, every object exactly one item, the converters' artifacts, references, byte
arrays and float forms, the generic fallback with its reason codes, unit selection, batches, determinism, the
cross-bundle sprite crop, and the golden records' providers."""

import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from nnnotes.pipeline import census as census_mod
from nnnotes.pipeline import contract, objexport, units
from nnnotes.pipeline.contract import IncompatibleTask, Input, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.objexport import CROP, ExportStage, SpriteCropStage
from nnnotes.pipeline.stages import Env, describe, execute, registry
from nnnotes.unity.atoms import Impl, astc, estimate, gltf
from nnnotes.unity.atoms.reader import UnityBundle
from nnnotes.unity.convert import CONVERTERS, REASONS, USM_ASSET_SCRIPT, export_atoms, files, material, mono, prefab
from tests.helpers.fakeunity import (
    F32,
    NORMAL,
    POSITION,
    UV0,
    FakeEnvironment,
    FakeTexture2D,
    mesh_tt,
    pptr,
    quad,
    rect,
    render_data,
    rgba_bytes,
    sprite_tt,
    submesh,
)

ROOT = Path(__file__).resolve().parents[2]
CAB, ATLAS_CAB = "CAB-a", "CAB-atlas"
SCRIPTS = {
    f"{CAB}:80": "A|Game|Local",
    "CAB-scripts:7": "Assembly-CSharp|Game|Behaviour",
    "CAB-scripts:8": "Assembly-CSharp||Holder",
    "CAB-scripts:10": "Assembly-CSharp|Fwk.Sound|SplitAcbData",
    "CAB-scripts:11": "CriMw.CriWare.Assets.Runtime|CriWare.Assets|CriAtomAcbAsset",
    "CAB-scripts:12": "Assembly-CSharp|Game|Fader",
}
INF = float("inf")


# ---------------------------------------------------------------- synthetic bundles
def _sprite_mesh(r):
    pos, idx = quad(r)
    return pos, idx


def _atlas_entry(tex_pid, tr, raw=3):
    return {
        "texture": pptr(tex_pid),
        "alphaTexture": pptr(),
        "textureRect": tr,
        "textureRectOffset": {"x": 0.0, "y": 0.0},
        "atlasRectOffset": {"x": 0.0, "y": 0.0},
        "uvTransform": {"x": 100.0, "y": 0.0, "z": 100.0, "w": 0.0},
        "downscaleMultiplier": 1.0,
        "settingsRaw": raw,
        "secondaryTextures": [],
    }


def _key(n):
    return [{"data[0]": n, "data[1]": 2, "data[2]": 3, "data[3]": 4}, 0]


def _transform(go, children=(), father=0):
    return {
        "m_GameObject": pptr(go),
        "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "m_LocalPosition": {"x": 1.0, "y": 2.0, "z": 3.0},
        "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
        "m_Children": [pptr(c) for c in children],
        "m_Father": pptr(father),
    }


def _go(name, comps):
    return {
        "m_Component": [{"component": pptr(c)} for c in comps],
        "m_Layer": 5,
        "m_Name": name,
        "m_Tag": 0,
        "m_IsActive": True,
    }


def _mono(go, script, name="", **fields):
    return {"m_GameObject": pptr(go), "m_Enabled": 1, "m_Script": script, "m_Name": name, **fields}


def _clip():
    return {
        "m_Name": "clip",
        "m_Legacy": False,
        "m_Compressed": False,
        "m_SampleRate": 60.0,
        "m_WrapMode": 0,
        "m_MuscleClip": {
            "m_StartTime": 0.0,
            "m_StopTime": 1.0,
            "m_LoopTime": 0,
            "m_CycleOffset": 0.0,
            "m_Clip": {
                "data": {
                    "m_StreamedClip": {"data": [], "curveCount": 0},
                    "m_DenseClip": {
                        "m_FrameCount": 0,
                        "m_CurveCount": 0,
                        "m_SampleRate": 60.0,
                        "m_BeginTime": 0.0,
                        "m_SampleArray": [],
                    },
                    "m_ConstantClip": {"data": [0.5, 1.0, 2.0]},
                }
            },
        },
        "m_ClipBindingConstant": {
            "genericBindings": [
                {
                    "path": 0,
                    "attribute": 1,
                    "script": pptr(),
                    "typeID": 4,
                    "customType": 0,
                    "isPPtrCurve": 0,
                    "isIntCurve": 0,
                    "isSerializeReferenceCurve": 0,
                }
            ],
            "pptrCurveMapping": [],
        },
        "m_Events": [],
    }


def _fade_clip():
    """A clip animating a field of a MonoBehaviour class whose script is in another bundle."""
    clip = _clip()
    clip["m_Name"] = "fade"
    clip["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"] = [0.25]
    clip["m_ClipBindingConstant"]["genericBindings"] = [
        {
            "path": 0,
            "attribute": 77,
            "script": pptr(12, 2),
            "typeID": 114,
            "customType": 0,
            "isPPtrCurve": 0,
            "isIntCurve": 0,
            "isSerializeReferenceCurve": 0,
        }
    ]
    return clip


# cue sheets held in the bundle: a SplitAcbData (chunk TextAssets joined, XOR 0x5a) and an embedded ACB
SPLIT_ACB = b"@UTF" + bytes(range(256)) * 4
SPLIT_CHUNKS = [bytes(b ^ 0x5A for b in SPLIT_ACB)[:600], bytes(b ^ 0x5A for b in SPLIT_ACB)[600:]]
EMBEDDED_ACB = b"@UTF" + bytes(range(255, -1, -1)) * 20
IMPL = ("CriMw.CriWare.Assets.Runtime", "CriWare.Assets", "CriSerializedBytesAssetImpl")


def _embedded_acb_asset(f, pid: int, name: str, acb: bytes, script: int = 11, **fields):
    """A CriWare.Assets asset (script `script`) whose `implementation` managed reference is a
    CriSerializedBytesAssetImpl holding `acb`, with UnityPy's managed reference nodes; `fields` after `implementation`
    (by default an ACB asset's `awb`)."""
    from tests.helpers.fakeunity import node_of

    tt = _mono(
        0,
        pptr(script, 2),
        name,
        implementation={"rid": 1000},
        **(fields or {"awb": pptr()}),
        references={
            "version": 2,
            "RefIds": [
                {"rid": 1000, "type": {"class": IMPL[2], "ns": IMPL[1], "asm": IMPL[0]}, "data": {"data": list(acb)}}
            ],
        },
    )
    node = node_of(tt, "Base", "MonoBehaviour", ("data",))
    refs = next(c for c in node.m_Children if c.m_Name == "references")
    refs.m_Type = "ManagedReferencesRegistry"
    item = next(c for c in refs.m_Children if c.m_Name == "RefIds").m_Children[0].m_Children[1]
    item.m_Type = "ReferencedObject"
    for c in item.m_Children:
        c.m_Type = {"type": "ReferencedManagedType", "data": "ReferencedObjectData"}.get(c.m_Name, c.m_Type)
    from types import SimpleNamespace

    f.ref_types.append(
        SimpleNamespace(
            m_AssemblyName=IMPL[0],
            m_NameSpace=IMPL[1],
            m_ClassName=IMPL[2],
            node=node_of({"data": list(acb)}, "data", IMPL[2], ("data",)),
        )
    )
    return f.add(pid, "MonoBehaviour", tt, node=node)


def _shader_blob():
    """(compressed blob, decompressed length, parsed form) of a GLES3 shader: one pass, a vertex and a fragment
    sub-program."""
    import struct

    import lz4.block
    from UnityPy.enums import ShaderGpuProgramType

    def code(text: bytes, keywords=()):
        out = struct.pack("<ii4i", 201708220, 0, 0, 0, 0, 0) + struct.pack("<i", len(keywords))
        for k in keywords:
            out += struct.pack("<i", len(k)) + k + b"\0" * (-len(k) % 4)
        return out + struct.pack("<i", len(text)) + text

    entries = [code(b"#ifdef VERTEX\nvoid main(){}\n#endif\n", [b"KW_A"]), code(b"#ifdef FRAGMENT\n#endif\n")]
    table, body = struct.pack("<i", len(entries)), b""
    at = 4 + 12 * len(entries)
    for e in entries:
        table += struct.pack("<3i", at + len(body), len(e), 0)
        body += e
    raw = table + body
    gles3 = ShaderGpuProgramType.kShaderGpuProgramGLES3.value

    def prog(i, kws):
        return {"m_PlayerSubPrograms": [[{"m_BlobIndex": i, "m_KeywordIndices": kws, "m_GpuProgramType": gles3}]]}

    parsed = {
        "m_Name": "Test/Unlit",
        "m_PropInfo": {"m_Props": [{"m_Name": "_Color", "m_DefValue[0]": 1.0}]},
        "m_KeywordNames": ["KW_A", "KW_B"],
        "m_FallbackName": "",
        "m_SubShaders": [
            {
                "m_Tags": {"tags": [("Queue", "Geometry")]},
                "m_LOD": 100,
                "m_Passes": [
                    {
                        "m_Name": "",
                        "m_State": {"m_Name": "MAIN", "m_ZWrite": {"val": 1.0}},
                        "m_Tags": {"tags": []},
                        "progVertex": prog(0, [0]),
                        "progFragment": prog(1, []),
                    }
                ],
            }
        ],
    }
    return lz4.block.compress(raw, store_size=False), len(raw), parsed


def _controller(clip_pid):
    return {
        "m_Name": "ctl",
        "m_Controller": {
            "m_LayerArray": [],
            "m_StateMachineArray": [],
            "m_Values": {"data": {"m_ValueArray": []}},
            "m_DefaultValues": {"data": {"m_BoolValues": [True]}},
        },
        "m_TOS": [],
        "m_AnimationClips": [pptr(clip_pid)],
    }


def _material():
    return {
        "m_Name": "mat",
        "m_Shader": pptr(5, 1),
        "m_ValidKeywords": ["K"],
        "m_InvalidKeywords": [],
        "m_CustomRenderQueue": -1,
        "stringTagMap": [],
        "disabledShaderPasses": [],
        "m_SavedProperties": {
            "m_TexEnvs": [
                ("_MainTex", {"m_Texture": pptr(10), "m_Scale": {"x": 1.0, "y": 1.0}, "m_Offset": {"x": 0.0, "y": 0.0}})
            ],
            "m_Ints": [],
            "m_Floats": [("_Cutoff", 0.5)],
            "m_Colors": [("_Color", {"r": 1.0, "g": INF, "b": 0.0, "a": 1.0})],
        },
    }


def _ttf(n=40):
    body = b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + (16).to_bytes(2, "big") + b"\0" * 4 + b"\0" * 16
    return body + bytes(range(n))


def _asset_bundle(entries):
    return {
        "m_Name": "b",
        "m_PreloadTable": [pptr(10)],
        "m_Container": [(path, {"preloadIndex": 0, "preloadSize": 1, "asset": pptr(pid)}) for path, pid in entries],
        "m_MainAsset": {"preloadIndex": 0, "preloadSize": 0, "asset": pptr()},
        "m_Dependencies": [],
        "m_AssetBundleName": "b.bundle",
        "m_IsStreamedSceneAssetBundle": False,
        "m_SceneHashes": [],
    }


W, H = 32, 24
TEX_PIXELS = rgba_bytes(W, H, seed=3)
MOC = b"MOC3" + bytes(range(256)) * 20


def bundle_all() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file(CAB, externals=["archive:/CAB-atlas/CAB-atlas", "archive:/CAB-scripts/CAB-scripts"])
    f.add(
        1,
        "AssetBundle",
        _asset_bundle(
            [
                ("assets/x/tex.png", 10),
                ("assets/x/tex.png", 11),
                ("assets/x/readme.txt", 40),
                ("assets/p/root.prefab", 60),
            ]
        ),
    )
    for path, pid in (
        ("assets/x/tex.png", 10),
        ("assets/x/tex.png", 11),
        ("assets/x/readme.txt", 40),
        ("assets/p/root.prefab", 60),
    ):
        f.contain(path, pid)
    tex = FakeTexture2D(TEX_PIXELS, W, H, 4, "tex")
    f.add(
        10,
        "Texture2D",
        {"m_Name": "tex", "m_Width": W, "m_Height": H, "m_TextureFormat": 4, "m_MipCount": 1, "image data": TEX_PIXELS},
        obj=tex,
    )
    r1 = rect(2, 3, 12, 10)
    pos, idx = _sprite_mesh(r1)
    f.add(11, "Sprite", sprite_tt("square", r1, render_data(pptr(10), r1, 66, pos, idx)))
    r2 = rect(16, 4, 10, 12)
    pos2, idx2 = _sprite_mesh(r2)
    f.add(
        12,
        "Sprite",
        sprite_tt(
            "packed",
            r2,
            render_data(pptr(), r2, 3, pos2, idx2),
            atlas=pptr(20),
            atlas_tags=["atlas"],
            key=((5, 2, 3, 4), 0),
        ),
    )
    f.add(
        20,
        "SpriteAtlas",
        {
            "m_Name": "atlas",
            "m_PackedSprites": [pptr(12)],
            "m_PackedSpriteNamesToIndex": ["packed"],
            "m_RenderDataMap": [(_key(5), _atlas_entry(10, r2))],
            "m_Tag": "atlas",
            "m_IsVariant": False,
        },
    )
    r3 = rect(0, 0, 8, 8)
    pos3, idx3 = _sprite_mesh(r3)
    f.add(
        13,
        "Sprite",
        sprite_tt(
            "remote",
            r3,
            render_data(pptr(), r3, 3, pos3, idx3),
            atlas=pptr(99, 1),
            atlas_tags=["remote"],
            key=((9, 2, 3, 4), 0),
        ),
    )
    f.add(
        14,
        "Texture2D",
        {"m_Name": "empty", "m_Width": 0, "m_Height": 0, "m_TextureFormat": 4, "m_MipCount": 1, "image data": b""},
        obj=FakeTexture2D(b"", 0, 0, 4, "empty"),
    )
    hdr = bytes(range(64))
    f.add(
        15,
        "Texture2D",
        {"m_Name": "hdr", "m_Width": 8, "m_Height": 8, "m_TextureFormat": 66, "m_MipCount": 2, "image data": hdr},
        obj=FakeTexture2D(hdr + b"\1" * 16, 8, 8, 66, "hdr"),
    )
    tri_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    f.add(
        30,
        "Mesh",
        mesh_tt(
            "tri",
            3,
            {POSITION: (F32, 3, tri_pos), NORMAL: (F32, 3, np.tile([0, 0, 1], (3, 1))), UV0: (F32, 2, tri_pos[:, :2])},
            [0, 1, 2],
            [submesh(0, 3)],
        ),
    )
    f.add(31, "Mesh", mesh_tt("none", 0, {}))
    f.add(40, "TextAsset", {"m_Name": "readme", "m_Script": "hello\n"})
    f.add(41, "TextAsset", {"m_Name": "raw", "m_Script": b"\xff\xfe\x00bin".decode("utf-8", "surrogateescape")})
    f.add(42, "Font", {"m_Name": "font", "m_FontData": list(_ttf())}, bytes_fields=("m_FontData",))
    f.add(43, "Font", {"m_Name": "nofont", "m_FontData": []}, bytes_fields=("m_FontData",))
    f.add(50, "Material", _material())
    # a hierarchy: root (60/61) with a behaviour (62), child (63/64) with a behaviour (65)
    f.add(60, "GameObject", _go("root", [61, 62]))
    f.add(61, "Transform", _transform(60, [64]))
    f.add(
        62,
        "MonoBehaviour",
        _mono(60, pptr(7, 2), target=pptr(63), tex=pptr(10), value=float("nan"), small=list(b"abc")),
        bytes_fields=("small",),
    )
    f.add(63, "GameObject", _go("child", [64, 65]))
    f.add(64, "Transform", _transform(63, father=61))
    f.add(65, "MonoBehaviour", _mono(63, pptr(8, 2), up=pptr(61), comp=pptr(62), far=pptr(3, 2)))
    f.add(70, "MonoBehaviour", _mono(0, pptr(80), "moc", _bytes=list(MOC), text="caf\udcc3"), bytes_fields=("_bytes",))
    f.add(71, "MonoBehaviour", _mono(0, pptr(9, 2), "lost"))
    f.add(72, "MonoBehaviour", _mono(0, pptr(), "noscript"))
    f.add(
        80,
        "MonoScript",
        {
            "m_Name": "Local",
            "m_ExecutionOrder": 0,
            "m_ClassName": "Local",
            "m_Namespace": "Game",
            "m_AssemblyName": "A.dll",
        },
    )
    f.add(90, "AnimationClip", _clip())
    legacy = _clip()
    legacy.update(m_Name="legacy", m_Legacy=True)
    f.add(91, "AnimationClip", legacy)
    f.add(92, "AnimatorController", _controller(90))
    f.add(93, "AnimatorOverrideController", {"m_Name": "over", "m_Controller": pptr(92), "m_Clips": []})
    f.add(94, "AudioClip", {"m_Name": "sound", "m_Length": 1.5})
    f.add(95, "Animator", {"m_GameObject": pptr(), "m_Enabled": 1, "m_Controller": pptr(92), "m_Speed": INF})
    f.add(96, "AnimationClip", _fade_clip())
    for pid, chunk in ((44, SPLIT_CHUNKS[0]), (45, SPLIT_CHUNKS[1])):
        f.add(pid, "TextAsset", {"m_Name": f"chunk{pid}", "m_Script": chunk.decode("utf-8", "surrogateescape")})
    f.add(
        73, "MonoBehaviour", _mono(0, pptr(10, 2), "bgm_test", _cueSheetName="bgm_test", _chunks=[pptr(44), pptr(45)])
    )
    _embedded_acb_asset(f, 74, "se_test", EMBEDDED_ACB)
    blob, size, parsed = _shader_blob()
    from types import SimpleNamespace

    f.add(
        51,
        "Shader",
        {
            "m_ParsedForm": parsed,
            "platforms": [9],
            "offsets": [[0]],
            "compressedLengths": [[len(blob)]],
            "decompressedLengths": [[size]],
            "compressedBlob": list(blob),
            "m_Dependencies": [],
            "m_NonModifiableTextures": [],
            "m_ShaderIsBaked": True,
        },
        obj=SimpleNamespace(
            compressedBlob=blob,
            platforms=[9],
            offsets=[[0]],
            compressedLengths=[[len(blob)]],
            decompressedLengths=[[size]],
        ),
        bytes_fields=("compressedBlob",),
    )
    return env


def bundle_atlas() -> FakeEnvironment:
    """An atlas bundle: its SpriteAtlas (99) packs the remote sprite of bundle_all and a local copy of it (97)."""
    env = FakeEnvironment()
    f = env.file(ATLAS_CAB)
    f.add(1, "AssetBundle", _asset_bundle([("assets/atlas/a.spriteatlas", 99)]))
    f.contain("assets/atlas/a.spriteatlas", 99)
    px = rgba_bytes(W, H, seed=9)
    f.add(
        98,
        "Texture2D",
        {"m_Name": "page", "m_Width": W, "m_Height": H, "m_TextureFormat": 4, "m_MipCount": 1, "image data": px},
        obj=FakeTexture2D(px, W, H, 4, "page"),
    )
    tr = rect(3, 5, 8, 8)
    f.add(
        99,
        "SpriteAtlas",
        {
            "m_Name": "remote",
            "m_PackedSprites": [pptr(13, 0), pptr(97)],
            "m_PackedSpriteNamesToIndex": ["remote", "copy"],
            "m_RenderDataMap": [(_key(9), _atlas_entry(98, tr, raw=3 | (1 << 2)))],
            "m_Tag": "remote",
            "m_IsVariant": False,
        },
    )
    r3 = rect(0, 0, 8, 8)
    pos3, idx3 = _sprite_mesh(r3)
    f.add(
        97,
        "Sprite",
        sprite_tt(
            "remote",
            r3,
            render_data(pptr(), r3, 3, pos3, idx3),
            atlas=pptr(99),
            atlas_tags=["remote"],
            key=((9, 2, 3, 4), 0),
        ),
    )
    return env


def bundle_plain() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file("CAB-plain")
    f.add(1, "AssetBundle", _asset_bundle([]))
    f.add(2, "TextAsset", {"m_Name": "t", "m_Script": "{}"})
    return env


# a movie asset holding its USM (script 13), beside a cue sheet asset
USM_SCRIPTS = {**SCRIPTS, "CAB-scripts:13": USM_ASSET_SCRIPT}
WARMUP = b"CRID" + bytes(range(256)) * 2
MOVIE_INFO = {"numAlphaStreams": 0, "width": 64, "height": 64, "codecType": 1, "numAudioStreams": 0}


def bundle_movie() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file("CAB-movie", externals=["archive:/CAB-atlas/CAB-atlas", "archive:/CAB-scripts/CAB-scripts"])
    f.add(1, "AssetBundle", _asset_bundle([("assets/cri/warmup.asset", 2)]))
    f.contain("assets/cri/warmup.asset", 2)
    _embedded_acb_asset(f, 2, "warmup", WARMUP, 13, movieInfo=MOVIE_INFO, assetInfo={"loop": 0, "additive": 0})
    _embedded_acb_asset(f, 3, "se_movie", EMBEDDED_ACB)
    return env


# a player's own file: no type trees; MonoBehaviours whose type hashes have a shipped type tree (one that does not
# fit the object), and none
PLAYER = "globalgamemanagers.assets"
PLAYER_HASH, OTHER_HASH, IMAGE_HASH = "a1" * 16, "b2" * 16, "c3" * 16
SETTINGS, IMAGE, CHANGED = "Fwk|Fwk.Sound|SoundVolumeSettings", "UnityEngine.UI|UnityEngine.UI|Image", "Game|Game|Moved"
PLAYER_SCRIPTS = {f"{PLAYER}:50": SETTINGS, f"{PLAYER}:51": IMAGE, f"{PLAYER}:52": CHANGED}
SETTINGS_ROWS = [
    [0, "MonoBehaviour", "Base", 0],
    [1, "PPtr<GameObject>", "m_GameObject", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "UInt8", "m_Enabled", 0x4000],
    [1, "PPtr<MonoScript>", "m_Script", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "string", "m_Name", 0],
    [1, "PPtr<Texture2D>", "target", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "float", "volume", 0],
]
PLAYER_TYPETREES = {PLAYER_HASH: SETTINGS_ROWS, OTHER_HASH: SETTINGS_ROWS[:-1]}  # the second lacks a field


def bundle_player() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file(PLAYER, externals=["Library/unity default resources"], typetrees=False)
    h = bytes.fromhex(PLAYER_HASH)
    settings = {"productGUID": "00", "m_ActiveColorSpace": 1, "m_MTRendering": True}
    f.add(1, "PlayerSettings", settings, type_hash=bytes.fromhex("936fd1913a4f0b5da2ce7cadb0ce9666"), short_read=True)
    f.add(6, "PlayerSettings", settings, type_hash=h, short_read=True)
    f.add(2, "MonoBehaviour", _mono(0, pptr(50), "volume", target=pptr(10, 1), volume=0.5), type_hash=h)
    image, other = bytes.fromhex(IMAGE_HASH), bytes.fromhex(OTHER_HASH)
    f.add(3, "MonoBehaviour", _mono(20, pptr(51), "", m_Sprite=pptr(10, 1)), type_hash=image)
    f.add(4, "MonoBehaviour", _mono(0, pptr(52), "moved", target=pptr(0), volume=1.0), type_hash=other)
    f.add(5, "MonoBehaviour", _mono(0, pptr(51), "image", m_Sprite=pptr(10, 1)), type_hash=image)
    f.add(20, "GameObject", _go("Canvas", [21, 3]))
    f.add(21, "Transform", _transform(20))
    for pid, entry in ((50, SETTINGS), (51, IMAGE), (52, CHANGED)):
        asm, ns, cls = entry.split("|")
        f.add(pid, "MonoScript", {"m_Name": cls, "m_ClassName": cls, "m_Namespace": ns, "m_AssemblyName": asm})
    return env


BUNDLES = {
    "all": bundle_all,
    "atlas": bundle_atlas,
    "plain": bundle_plain,
    "movie": bundle_movie,
    "player": bundle_player,
}


def open_fake(data: bytes, name: str | None = None) -> UnityBundle:
    return UnityBundle(BUNDLES[bytes(data).decode().removeprefix("fake:")]())


FAKE_READER = Impl("fakeunity/1", open_fake, lambda facts: estimate(0.01, 1 << 20))


def classes_of(name):
    return sorted({r.type.name for f in BUNDLES[name]().assets for r in f.objects.values()})


def planning_env(depot, names=("all", "atlas"), selected=None, scripts=SCRIPTS, classes=None):
    """A planning environment over fake bundles: their inputs, their censuses (census.census_env, the shipped type
    trees read for files without type trees), the script table; `selected` (default: every bundle of `names`) and
    `classes` as the export command gives them."""
    names = tuple(sorted(set(names) | set(selected or ())))
    bundles = {}
    for n in names:
        data = f"fake:{n}".encode()
        bundles[n] = Input("bundle", depot.put(data), len(data), f"{n}.bundle", ({"kind": "depot"},))
    facts = {
        "bundles": bundles,
        "catalogs": {},
        "selected": sorted(selected if selected is not None else names),
        "classes": classes,
    }
    env = Env(depot, facts)
    for n in names:
        t = Task(census_mod.CensusStage.name, census_mod.CensusStage.version, n, {}, {}, (bundles[n],))
        doc = census_mod.census_env(BUNDLES[n](), shipped=True)
        rec = contract.artifact(
            contract.artifact_id(t.id, "census"),
            depot.add(contract.encode(doc), "json"),
            contract.provenance(t),
            {"kind": "unity.census", "format": "json", "facts": census_mod.facts(doc)},
        )
        depot.commit(contract.result(t, [rec], []))
        env.done(t.id, t.key)
    doc = {"schema": "nnnotes.scripts/1", "scripts": scripts}
    data = contract.encode(doc)  # its input: another table is another task
    table = Task("link.scripts", 1, "all", {}, {}, (Input("census", depot.put(data), len(data), None, ()),))
    committed(depot, env, table, "scripts", doc, "link.scripts")
    return env


def committed(depot, env, task, role, doc, kind):
    rec = contract.artifact(
        contract.artifact_id(task.id, role),
        depot.add(contract.encode(doc), "json"),
        contract.provenance(task),
        {"kind": kind, "format": "json", "facts": {}},
    )
    depot.commit(contract.result(task, [rec], []))
    env.done(task.id, task.key)


def unit_task(depot, env, subject, params=None):
    return describe(ExportStage(FAKE_READER), subject, params, env)


def export(depot, env, params=None, stage=None) -> dict:
    """Run every unity.export task of a planning environment (in one stage object, as a worker runs a batch): the
    results merged {"items", "artifacts"} with {"tasks": {task id: Task}, "status": {task id: result status}}."""
    stage = stage or ExportStage(FAKE_READER)
    out: dict = {"items": [], "artifacts": [], "tasks": {}, "status": {}}
    for s in stage.subjects(env):
        t = describe(stage, s, params, env)
        execute(t, depot, registry(stage), force=True)
        doc = depot.result(t.key)
        env.done(t.id, t.key)
        out["items"] += doc["items"]
        out["artifacts"] += doc["artifacts"]
        out["tasks"][t.id] = t
        out["status"][t.id] = doc["status"]
    return out


def run_all(depot, names=("all",), params=None, classes=None, scripts=SCRIPTS) -> dict:
    """export() of the units of the bundles `names` (only those of `classes` when given)."""
    return export(depot, planning_env(depot, names, scripts=scripts, classes=classes), params)


def by_object(doc):
    return {i["object"]: i for i in doc["items"]}


def art(doc, aid):
    return next(a for a in doc["artifacts"] if a["id"] == aid)


def load(depot, doc, aid):
    return depot.read(art(doc, aid)["content"]["sha256"])


def obj(depot, doc, aid):
    return contract.loads(load(depot, doc, aid))


def oid(pid, cab=CAB):
    return f"{cab}:{pid}"


@pytest.fixture
def exported(tmp_path):
    depot = Depot(tmp_path / "depot")
    return depot, run_all(depot)


# ---------------------------------------------------------------- items
def test_every_object_has_exactly_one_item(exported):
    depot, doc = exported
    objects = [contract.object_id(f.name, pid) for f in bundle_all().assets for pid in f.objects]
    items = by_object(doc)
    assert sorted(items) == sorted(objects) and len(doc["items"]) == len(objects)
    status = {o: (i["status"], (i.get("reason") or {}).get("code")) for o, i in items.items()}
    assert status == {
        oid(1): ("exported", None),
        oid(10): ("exported", None),
        oid(11): ("exported", None),
        oid(12): ("exported", None),
        oid(13): ("exported", None),
        oid(14): ("generic", "empty.texture"),
        oid(15): ("exported", None),
        oid(20): ("exported", None),
        oid(30): ("exported", None),
        oid(31): ("generic", "empty.mesh"),
        oid(40): ("exported", None),
        oid(41): ("exported", None),
        oid(42): ("exported", None),
        oid(43): ("generic", "no_data.font"),
        oid(50): ("exported", None),
        oid(60): ("exported", None),
        oid(61): ("contained", None),
        oid(62): ("contained", None),
        oid(63): ("contained", None),
        oid(64): ("contained", None),
        oid(65): ("contained", None),
        oid(70): ("exported", None),
        oid(71): ("generic", "script.unresolved"),
        oid(72): ("generic", "script.missing"),
        oid(80): ("exported", None),
        oid(90): ("exported", None),
        oid(91): ("generic", "generic.clip.legacy"),
        oid(92): ("exported", None),
        oid(93): ("generic", "generic.controller.override"),
        oid(94): ("unsupported", "unsupported.class.AudioClip"),
        oid(95): ("exported", None),
        oid(96): ("exported", None),
        oid(44): ("exported", None),
        oid(45): ("exported", None),
        oid(73): ("exported", None),
        oid(74): ("exported", None),
        oid(51): ("exported", None),
    }
    assert items[oid(61)]["in"] == f"{oid(60)}#json"
    assert items[oid(94)]["artifacts"] == [f"{oid(94)}#json"]
    assert set(doc["status"].values()) == {"ok"} and all(i["class"] for i in doc["items"])
    for a in doc["artifacts"]:
        assert "object" in a["provenance"] and depot.has(a["content"]["sha256"], a["content"]["size"])
    # one task per unit: a hierarchy (its root's subject) or an object outside any
    assert sorted(doc["tasks"]) == sorted(
        f"unity.export:all:{contract.parse_object_id(o)[1]}"
        for o in objects
        if o not in {oid(p) for p in (61, 62, 63, 64, 65)}
    )


def test_provenance_facts_and_atoms(exported):
    depot, doc = exported
    a = art(doc, f"{oid(10)}#image")
    assert a["provenance"]["object"] == {
        "file": CAB,
        "pathId": 10,
        "classId": 28,
        "class": "Texture2D",
        "name": "tex",
        "unityVersion": "6000.3.12f1",
        "container": "assets/x/tex.png",
    }
    assert set(a["provenance"]["atoms"]) == {
        "reader",
        "sniff",
        "texture.decode",
        "png.encode",
        "astc.container",
        "tex.png",
    }
    task = doc["tasks"]["unity.export:all:10"]
    assert a["provenance"]["inputs"] == [{"role": "bundle", "sha256": task.inputs[0].sha256}]
    assert a["provenance"]["stage"]["version"] == ExportStage.version == 2
    g = art(doc, f"{oid(95)}#json")
    assert set(g["provenance"]["atoms"]) == {"reader", "sniff", "generic.json"}
    assert task.atoms["tex.png"] == "tex.png/2" and task.atoms["reader"] == "fakeunity/1"


# ---------------------------------------------------------------- converters
def test_textures(exported):
    depot, doc = exported
    png = load(depot, doc, f"{oid(10)}#image")
    img = Image.open(__import__("io").BytesIO(png))
    assert img.size == (W, H) and img.mode == "RGBA"
    expected = Image.frombytes("RGBA", (W, H), TEX_PIXELS).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    assert img.tobytes() == expected.tobytes()
    encoded = __import__("io").BytesIO()
    expected.save(encoded, format="PNG")
    assert png == encoded.getvalue()
    facts = art(doc, f"{oid(10)}#image")["semantics"]["facts"]
    assert facts == {
        "width": W,
        "height": H,
        "textureFormat": "RGBA32",
        "mipCount": 1,
        "exportedMips": [0],
        "mode": "RGBA",
    }
    a = load(depot, doc, f"{oid(15)}#image")
    assert astc.parse_header(a) == {"block": (4, 4, 1), "size": (8, 8, 1)} and a[16:] == bytes(range(64))
    assert art(doc, f"{oid(15)}#image")["content"]["ext"] == "astc"
    empty = obj(depot, doc, f"{oid(14)}#json")
    assert empty["m_Width"] == 0 and empty["image data"] == {"$hex": ""}


def test_sprites_same_bundle(exported):
    depot, doc = exported
    from nnnotes.unity.atoms import png, sprite
    from nnnotes.unity.atoms.mesh import arrays
    from nnnotes.unity.atoms.reader import mesh_input_from_typetree
    from nnnotes.unity.atoms.texture import TextureInput, decode

    env = bundle_all()
    f = env.assets[0]
    image = decode(TextureInput(TEX_PIXELS, W, H, 4, f.version, 13, b""))
    for pid in (11, 12):
        tt = f.objects[pid].tt
        src = tt["m_RD"] if pid == 11 else f.objects[20].tt["m_RenderDataMap"][0][1]
        mesh = arrays(mesh_input_from_typetree(tt, f.version)) if not (src["settingsRaw"] >> 1) & 1 else None
        want = sprite.crop(
            image,
            src["textureRect"],
            src["settingsRaw"],
            mesh,
            100.0,
            canvas={"rect": tt["m_Rect"], "offset": src["textureRectOffset"], "pivot": tt["m_Pivot"]},
        )
        assert load(depot, doc, f"{oid(pid)}#image") == png.encode(want)
        meta = obj(depot, doc, f"{oid(pid)}#json")
        rd = meta["renderData"]
        assert rd["texture"] == {"$ref": {"file": CAB, "pathId": "10", "class": "Texture2D", "name": "tex"}}
        assert rd["textureRect"] == src["textureRect"] and rd["uvTransform"] == src["uvTransform"]
        assert rd["settingsRaw"] == src["settingsRaw"] and rd["alphaTexture"] is None
        assert meta["sprite"]["m_Name"] == tt["m_Name"]
        pos, idx = _sprite_mesh(tt["m_Rect"])
        assert meta["mesh"]["positions"] == np.asarray(pos, np.float32).astype(float).tolist()
        assert meta["mesh"]["submeshes"] == [np.asarray(idx).reshape(-1, 3).tolist()]
        assert meta["image"] == {
            "packed": bool(src["settingsRaw"] & 1),
            "packingMode": (src["settingsRaw"] >> 1) & 1,
            "packingRotation": (src["settingsRaw"] >> 2) & 0xF,
            "width": int(tt["m_Rect"]["width"]),
            "height": int(tt["m_Rect"]["height"]),
        }
        assert by_object(doc)[oid(pid)]["refs"] == [oid(10)] + ([oid(20)] if pid == 12 else [])
    meta = obj(depot, doc, f"{oid(11)}#json")
    assert meta["renderData"]["source"] == "renderData" and meta["renderData"]["atlas"] is None
    meta = obj(depot, doc, f"{oid(12)}#json")
    assert meta["renderData"]["source"] == "atlas" and meta["renderData"]["atlas"]["$ref"]["pathId"] == "20"
    assert meta["sprite"]["m_RD"]["m_VertexData"]["m_DataSize"]["$hex"]  # small: inline
    refs = art(doc, f"{oid(12)}#image")["semantics"]["refs"]
    assert refs == [{"rel": "texture", "object": oid(10)}, {"rel": "atlas", "object": oid(20)}]


def test_sprite_with_its_atlas_elsewhere_gets_a_meta_only(exported):
    depot, doc = exported
    assert by_object(doc)[oid(13)]["artifacts"] == [f"{oid(13)}#json"]
    a = art(doc, f"{oid(13)}#json")
    assert a["semantics"]["facts"] == {"crop": CROP, "atlas": f"{ATLAS_CAB}:99"}
    meta = obj(depot, doc, a["id"])
    assert meta["image"] == {"source": "atlas", "atlas": {"$ref": {"file": ATLAS_CAB, "pathId": "99"}}, "stage": CROP}
    assert meta["object"]["name"] == "remote" and len(meta["mesh"]["positions"]) == 4


def test_mesh(exported):
    depot, doc = exported
    d, body = gltf.read(load(depot, doc, f"{oid(30)}#mesh"))
    (prim,) = d["meshes"][0]["primitives"]
    assert set(prim["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"} and prim["mode"] == 4
    pos = gltf.accessor_array(d, body, prim["attributes"]["POSITION"])
    assert pos.tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert art(doc, f"{oid(30)}#mesh")["semantics"]["facts"] == {
        "vertexCount": 3,
        "submeshes": 1,
        "channels": ["position", "normal", "uv0"],
        "skinned": False,
    }
    assert art(doc, f"{oid(30)}#arrays")["semantics"] == {"kind": "mesh.arrays", "format": "json", "facts": {}}
    arrays = obj(depot, doc, f"{oid(30)}#arrays")
    assert list(arrays) == [
        "positions",
        "normals",
        "tangents",
        "colors",
        *(f"uv{i}" for i in range(8)),
        "submeshes",
        "joints",
        "weights",
    ]
    assert arrays["positions"] == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert arrays["normals"] == [[0, 0, 1]] * 3 and arrays["uv0"] == [[0, 0], [1, 0], [0, 1]]
    assert arrays["tangents"] is None and arrays["colors"] is None and arrays["uv1"] is None
    assert arrays["submeshes"] == [{"topology": "triangles", "indices": [[0, 1, 2]]}]
    assert arrays["joints"] is None and arrays["weights"] is None


def test_text_and_fonts(exported):
    depot, doc = exported
    a = art(doc, f"{oid(40)}#data")
    assert load(depot, doc, a["id"]) == b"hello\n" and a["content"]["ext"] == "txt"
    assert load(depot, doc, f"{oid(41)}#data") == b"\xff\xfe\x00bin"
    assert art(doc, f"{oid(41)}#data")["content"]["ext"] == "bin"
    f = art(doc, f"{oid(42)}#font")
    assert load(depot, doc, f["id"]) == _ttf() and f["content"]["ext"] == "ttf"


def test_every_converted_object_has_its_json(exported):
    depot, doc = exported
    tex = obj(depot, doc, f"{oid(10)}#json")
    assert "image data" not in tex and tex["m_Width"] == W and tex["m_TextureFormat"] == 4
    assert art(doc, f"{oid(10)}#json")["semantics"] == {"kind": "unity.object", "format": "json", "facts": {}}
    assert obj(depot, doc, f"{oid(15)}#json")["m_MipCount"] == 2  # HDR ASTC: the .astc image and the json
    mesh = obj(depot, doc, f"{oid(30)}#json")
    assert "m_DataSize" not in mesh["m_VertexData"] and "m_IndexBuffer" not in mesh
    assert mesh["m_VertexData"]["m_VertexCount"] == 3 and mesh["m_Name"] == "tri"
    text = obj(depot, doc, f"{oid(40)}#json")
    assert text == {"m_Name": "readme"}
    font = obj(depot, doc, f"{oid(42)}#json")
    assert font == {"m_Name": "font"}
    items = by_object(doc)
    for pid in (10, 11, 12, 15, 30, 40, 41, 42, 44, 45, 50, 60, 70, 73, 74, 90, 92, 95, 96):
        assert f"{oid(pid)}#json" in items[oid(pid)]["artifacts"], pid
    assert all(
        i["status"] != "exported" or any(a.endswith("#json") for a in i["artifacts"]) or i["object"] == oid(13)
        for i in doc["items"]
    )


def test_items_name_the_objects_they_reference(exported):
    _depot, doc = exported
    items = by_object(doc)
    assert items[oid(50)]["refs"] == [oid(10), f"{ATLAS_CAB}:5"]  # the material's shader and texture
    # a prefab: the objects its components reference outside it, other bundles' included; its own objects are named
    # by their paths
    assert items[oid(60)]["refs"] == [oid(10), "CAB-scripts:3", "CAB-scripts:7", "CAB-scripts:8"]
    assert "refs" not in items[oid(61)] and "refs" not in items[oid(63)]  # contained in the prefab
    assert items[oid(1)]["refs"] == [oid(10), oid(11), oid(40), oid(60)]  # the AssetBundle's container
    assert items[oid(92)]["refs"] == [oid(90)]  # the controller's clip
    assert items[oid(96)]["refs"] == ["CAB-scripts:12"]  # a clip binding's script
    assert items[oid(13)]["refs"] == [f"{ATLAS_CAB}:99"]  # a sprite's atlas in another bundle
    assert items[oid(70)]["refs"] == [oid(80)]  # its script
    assert "refs" not in items[oid(30)] and "refs" not in items[oid(40)]


def test_prefab(exported):
    depot, doc = exported
    p = obj(depot, doc, f"{oid(60)}#json")
    from tests.helpers.schemas import validate

    validate("prefab", p)
    assert p["name"] == "root" and [n["path"] for n in p["nodes"]] == ["root", "root/child"]
    root, child = p["nodes"]
    assert root["pathId"] == "60" and root["transformPathId"] == "61" and root["layer"] == 5
    (b,) = root["components"]
    assert b["type"] == "MonoBehaviour" and b["script"] == "Assembly-CSharp|Game|Behaviour"
    fields = b["fields"]
    assert fields["target"] == {"gameObject": "root/child"}
    assert fields["tex"] == {"$ref": {"file": CAB, "pathId": "10", "class": "Texture2D", "name": "tex"}}
    assert fields["m_Script"] == {"$ref": {"file": "CAB-scripts", "pathId": "7"}}
    assert math.isnan(fields["value"]) and fields["small"] == {"$hex": "616263"}
    assert b'{"$float": "nan"}' in load(depot, doc, f"{oid(60)}#json").replace(b"\n", b"").replace(
        b" ", b""
    ) or b'"$float"' in load(depot, doc, f"{oid(60)}#json")
    (c,) = child["components"]
    assert c["fields"]["up"] == {"transform": "root"}
    assert c["fields"]["comp"] == {"component": "MonoBehaviour", "gameObject": "root", "class": "Behaviour"}
    assert c["fields"]["far"] == {"$ref": {"file": "CAB-scripts", "pathId": "3"}}
    assert c["script"] == "Assembly-CSharp||Holder"


def test_scriptable_objects_blobs_and_strings(exported):
    depot, doc = exported
    m = obj(depot, doc, f"{oid(70)}#json")
    assert m["$script"] == "A|Game|Local" and m["m_Name"] == "moc"
    blob = m["_bytes"]["$blob"]
    assert blob == {"sha256": contract.sha256(MOC), "size": len(MOC), "format": "moc3"}
    b = art(doc, f"{oid(70)}#blob:_bytes")
    assert b["content"]["ext"] == "moc3" and b["semantics"] == {
        "kind": "blob",
        "format": "moc3",
        "facts": {"field": "_bytes"},
    }
    assert load(depot, doc, b["id"]) == MOC
    assert m["text"] == {"$hex": b"caf".hex() + "c3"}
    assert by_object(doc)[oid(70)]["artifacts"] == [f"{oid(70)}#blob:_bytes", f"{oid(70)}#json"]
    lost = obj(depot, doc, f"{oid(71)}#json")
    assert "$script" not in lost and lost["m_Script"] == {"$ref": {"file": "CAB-scripts", "pathId": "9"}}


def test_cue_sheets_held_in_the_bundle(exported):
    depot, doc = exported
    from nnnotes.formats import cri

    a = art(doc, f"{oid(73)}#acb")
    assert load(depot, doc, a["id"]) == SPLIT_ACB == cri.split_acb(SPLIT_CHUNKS)
    assert a["content"]["ext"] == "acb" and a["semantics"] == {
        "kind": "cri.acb",
        "format": "acb",
        "facts": {"cueSheet": "bgm_test", "layout": "split", "chunks": 2},
        "refs": [{"rel": "chunk", "object": oid(44)}, {"rel": "chunk", "object": oid(45)}],
    }
    assert by_object(doc)[oid(73)]["artifacts"] == [f"{oid(73)}#acb", f"{oid(73)}#json"]
    assert obj(depot, doc, f"{oid(73)}#json")["$script"] == "Assembly-CSharp|Fwk.Sound|SplitAcbData"
    assert load(depot, doc, f"{oid(44)}#data") == SPLIT_CHUNKS[0]  # the chunks stay exported
    e = art(doc, f"{oid(74)}#acb")
    assert load(depot, doc, e["id"]) == EMBEDDED_ACB
    assert e["semantics"]["facts"] == {"cueSheet": "se_test", "layout": "embedded"}
    m = obj(depot, doc, f"{oid(74)}#json")
    ref = m["references"]["RefIds"][0]
    assert ref["type"] == {"class": IMPL[2], "ns": IMPL[1], "asm": IMPL[0]}
    assert ref["data"]["data"] == {
        "$blob": {"sha256": contract.sha256(EMBEDDED_ACB), "size": len(EMBEDDED_ACB), "format": "acb"}
    }
    assert by_object(doc)[oid(74)]["artifacts"] == [f"{oid(74)}#acb", f"{oid(74)}#json"]  # no blob: artifact
    small = run_all(depot, classes=["MonoBehaviour"], params={"blobMin": 1 << 20})
    assert load(depot, small, f"{oid(74)}#acb") == EMBEDDED_ACB  # whatever its size


def movie_task(depot, converter=True):
    """The task of the movie asset's unit: converter cri.usm in its atoms, as uses() gives it for that unit."""
    env = planning_env(depot, ("movie",), scripts=USM_SCRIPTS)
    t = unit_task(depot, env, "movie:2")
    atoms = t.atoms if converter else {k: v for k, v in t.atoms.items() if k != "cri.usm"}
    return Task(t.stage, t.version, t.subject, t.params, atoms, t.inputs, t.context)


def run(depot, task):
    execute(task, depot, registry(ExportStage(FAKE_READER)), force=True)
    return depot.result(task.key)


def test_movies_held_in_the_bundle(tmp_path):
    """A movie asset's USM bytes (CriSerializedBytesAssetImpl) are its `usm` artifact whatever their size; the cue
    sheet beside it keeps its `acb`. Without converter cri.usm the movie asset is exported as before (its bytes are
    not an ACB: the typetree with generic.error)."""
    depot = Depot(tmp_path / "s")
    doc = run(depot, movie_task(depot))
    m, a = "CAB-movie:2", "CAB-movie:3"
    usm = art(doc, f"{m}#usm")
    assert load(depot, doc, usm["id"]) == WARMUP and usm["content"]["ext"] == "usm"
    assert usm["semantics"] == {"kind": "cri.usm", "format": "usm", "facts": {"movie": "warmup"}}
    tree = obj(depot, doc, f"{m}#json")
    assert tree["$script"] == USM_ASSET_SCRIPT and tree["movieInfo"] == MOVIE_INFO
    assert tree["references"]["RefIds"][0]["data"]["data"] == {
        "$blob": {"sha256": contract.sha256(WARMUP), "size": len(WARMUP), "format": "usm"}
    }
    assert by_object(doc)[m]["status"] == "exported" and by_object(doc)[m]["artifacts"] == [f"{m}#json", f"{m}#usm"]
    old = run(depot, movie_task(depot, converter=False))
    assert by_object(old)[m]["status"] == "generic" and by_object(old)[m]["reason"]["code"] == "generic.error"
    assert f"{m}#usm" not in {x["id"] for x in old["artifacts"]}
    sheet = run_all(depot, ("movie",), scripts=USM_SCRIPTS)
    assert load(depot, sheet, f"{a}#acb") == EMBEDDED_ACB


def test_the_movie_converter_is_keyed_by_the_script_context(tmp_path):
    depot = Depot(tmp_path / "s")
    env = planning_env(depot, ("all", "movie"), scripts=USM_SCRIPTS)
    stage = ExportStage(FAKE_READER)
    assert describe(stage, "movie:2", None, env).atoms["cri.usm"] == "cri.usm/1"
    assert "cri.usm" not in describe(stage, "movie:3", None, env).atoms  # a cue sheet asset
    assert "cri.usm" not in describe(stage, "all:70", None, env).atoms
    assert "cri.usm" in stage.ATOMS
    plain = planning_env(Depot(tmp_path / "p"), ("movie",))
    assert "cri.usm" not in describe(stage, "movie:2", None, plain).atoms


def test_acb_layouts(tmp_path):
    from nnnotes.formats import cri

    assert mono.acb_asset({"_cueSheetName": "x", "_chunks": []})["layout"] == "split"
    t = {
        "m_Name": "s",
        "implementation": {"rid": 5},
        "references": {
            "RefIds": [
                {"rid": 4, "type": {"class": IMPL[2]}},
                {"rid": 5, "type": {"class": IMPL[2]}, "data": {"data": b"@UTF"}},
            ]
        },
    }
    assert mono.acb_asset(t) == {
        "layout": "embedded",
        "cueSheet": "s",
        "ref": 1,
        "impl": t["references"]["RefIds"][1],
    }
    assert (
        mono.acb_asset(
            {"implementation": {"rid": 5}, "references": {"RefIds": [{"rid": 5, "type": {"class": "Other"}}]}}
        )
        is None
        and mono.acb_asset({"m_Name": "x"}) is None
    )
    assert cri.embedded_acb(t, t["references"]["RefIds"][1]) == b"@UTF"
    with pytest.raises(NotImplementedError, match="external AWB"):
        cri.embedded_acb({**t, "awb": pptr(3)}, t["references"]["RefIds"][1], "s")
    with pytest.raises(RuntimeError, match="not an ACB"):
        cri.split_acb([b"\x01\x02\x03\x04", b"\x05"], "s")
    with pytest.raises(RuntimeError):
        cri.split_acb([])


def test_a_split_sheet_with_a_missing_chunk_gives_the_typetree(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "s")
    real = mono.acb_asset
    monkeypatch.setattr(
        mono,
        "acb_asset",
        lambda tt: (
            dict(real(tt), chunks=[pptr(44), pptr(99, 1)]) if real(tt) and real(tt)["layout"] == "split" else real(tt)
        ),
    )
    doc = run_all(depot, classes=["MonoBehaviour"])
    item = by_object(doc)[oid(73)]
    assert item["status"] == "generic" and item["reason"]["code"] == "generic.error"
    assert "is not a TextAsset of the bundle" in item["reason"]["message"]


def test_shader(exported):
    depot, doc = exported
    from nnnotes.formats import shader

    env = bundle_all()
    r = env.assets[0].objects[51]
    text, recs, codes = shader._render(r, r.tt, "Test/Unlit")  # what `nnnotes shader` writes
    assert load(depot, doc, f"{oid(51)}#json") == text.encode("utf-8")
    index = obj(depot, doc, f"{oid(51)}#index")
    assert (
        index["name"] == "Test/Unlit"
        and [{k: v for k, v in x.items() if k != "role"} for x in index["variants"]] == recs
    )
    assert [x["role"] for x in index["variants"]] == ["program:gles3/s0p0_vertex_0", "program:gles3/s0p0_fragment_0"]
    for x, code in zip(index["variants"], codes):
        a = art(doc, f"{oid(51)}#{x['role']}")
        assert load(depot, doc, a["id"]) == code and a["content"]["ext"] == "glsl"
        assert a["semantics"]["facts"]["keywords"] == x["keywords"]
    assert codes[0] == b"#ifdef VERTEX\nvoid main(){}\n#endif\n" and index["variants"][0]["keywords"] == ["KW_A"]
    t = obj(depot, doc, f"{oid(51)}#typetree")
    assert t["m_ParsedForm"]["m_Name"] == "Test/Unlit" and "$hex" in t["compressedBlob"]
    assert art(doc, f"{oid(51)}#json")["semantics"]["facts"] == {
        "name": "Test/Unlit",
        "variants": 2,
        "platforms": ["gles3"],
    }


def test_clip_bindings_name_scripts_of_other_bundles(exported):
    depot, doc = exported
    clip = obj(depot, doc, f"{oid(96)}#json")
    (b,) = clip["bindings"]
    assert b == {
        "pathCrc": 0,
        "attributeCrc": 77,
        "typeID": 114,
        "class": "MonoBehaviour",
        "customType": 0,
        "pptr": False,
        "int": False,
        "serializeReference": False,
        "script": {"$ref": {"file": "CAB-scripts", "pathId": "12"}},
        "scriptClass": "Assembly-CSharp|Game|Fader",
    }
    assert census_mod.script_refs(census_mod.census_env(bundle_all())) == sorted(SCRIPTS) + ["CAB-scripts:9"]


def test_material_clip_controller(exported):
    depot, doc = exported
    mat = obj(depot, doc, f"{oid(50)}#json")
    assert mat["shader"] == {"$ref": {"file": ATLAS_CAB, "pathId": "5"}}
    assert mat["textures"]["_MainTex"]["texture"]["$ref"]["pathId"] == "10"
    assert mat["colors"]["_Color"]["g"] == INF and b"1e999" in load(depot, doc, f"{oid(50)}#json")
    clip = obj(depot, doc, f"{oid(90)}#json")
    assert list(clip) == [
        "name",
        "sampleRate",
        "wrapMode",
        "startTime",
        "stopTime",
        "loopTime",
        "cycleOffset",
        "events",
        "bindings",
        "streamed",
        "dense",
        "constant",
        "pptrCurveMapping",
    ]
    assert clip["name"] == "clip" and clip["constant"] == [0.5, 1.0, 2.0] and clip["streamed"]["frames"] == []
    assert clip["bindings"] == [
        {
            "pathCrc": 0,
            "attributeCrc": 1,
            "typeID": 4,
            "class": "Transform",
            "customType": 0,
            "pptr": False,
            "int": False,
            "serializeReference": False,
            "attribute": "m_LocalPosition",
            "curves": 3,
        }
    ]
    ctl = obj(depot, doc, f"{oid(92)}#json")
    assert list(ctl) == ["name", "clips", "layers", "stateMachines", "parameters", "defaultValues"]
    assert ctl["clips"] == [{"$ref": {"file": CAB, "pathId": "90", "class": "AnimationClip", "name": "clip"}}]
    assert obj(depot, doc, f"{oid(95)}#json")["m_Speed"] == INF


def test_scripts_and_asset_bundles_have_their_typetree(exported):
    depot, doc = exported
    s = obj(depot, doc, f"{oid(80)}#json")
    assert s["m_ClassName"] == "Local" and by_object(doc)[oid(80)]["artifacts"] == [f"{oid(80)}#json"]
    ab = obj(depot, doc, f"{oid(1)}#json")
    assert ab["m_Container"][0] == [
        "assets/x/tex.png",
        {
            "preloadIndex": 0,
            "preloadSize": 1,
            "asset": {"$ref": {"file": CAB, "pathId": "10", "class": "Texture2D", "name": "tex"}},
        },
    ]


def test_two_runs_are_identical(tmp_path):
    a, b = Depot(tmp_path / "a"), Depot(tmp_path / "b")
    da, db = run_all(a), run_all(b)
    assert {k: x.key for k, x in da["tasks"].items()} == {k: x.key for k, x in db["tasks"].items()}
    assert contract.encode(da["items"]) == contract.encode(db["items"])
    assert contract.encode(da["artifacts"]) == contract.encode(db["artifacts"])
    for x in da["artifacts"]:
        assert a.read(x["content"]["sha256"]) == b.read(x["content"]["sha256"])


def test_a_units_key_holds_its_classes_and_the_params(tmp_path):
    depot = Depot(tmp_path / "s")
    env = planning_env(depot, ("atlas",))
    t = unit_task(depot, env, "atlas:97")  # a sprite
    assert "mesh.glb" not in t.atoms and "mesh.arrays" in t.atoms  # sprites use mesh.arrays
    assert set(t.atoms) == set(export_atoms(["Sprite"], FAKE_READER.id))
    assert t.context == {"unit": f"{ATLAS_CAB}:97", "bundle": "atlas"}
    assert unit_task(depot, env, "atlas:97", {"png": {"level": 3}}).key != t.key
    assert export_atoms(["Texture2D"]) == export_atoms(["Texture2D", "Texture2D"])
    assert set(export_atoms(["Mesh"])) == {"reader", "sniff", "mesh.arrays", "gltf.write", "mesh.glb", "generic.json"}
    with pytest.raises(ValueError, match="png level"):
        ExportStage().normalize({"png": {"level": 11}})
    with pytest.raises(ValueError, match="unknown parameters"):
        ExportStage().normalize({"classes": ["Mesh"]})
    assert ExportStage().normalize(None) == {"blobMin": 4096, "png": {"level": 6}}


def test_png_level_changes_bytes_not_pixels(tmp_path):
    depot = Depot(tmp_path / "s")
    d6 = run_all(depot, classes=["Texture2D"])
    d0 = run_all(depot, classes=["Texture2D"], params={"png": {"level": 0}})
    p6, p0 = load(depot, d6, f"{oid(10)}#image"), load(depot, d0, f"{oid(10)}#image")
    assert p6 != p0
    io = __import__("io")
    assert Image.open(io.BytesIO(p6)).tobytes() == Image.open(io.BytesIO(p0)).tobytes()


def test_blob_threshold(tmp_path):
    depot = Depot(tmp_path / "s")
    doc = run_all(depot, classes=["MonoBehaviour"], params={"blobMin": 1 << 20})
    m = obj(depot, doc, f"{oid(70)}#json")
    assert m["_bytes"] == {"$hex": MOC.hex()}


def test_classes_select_units_by_their_root(tmp_path):
    depot = Depot(tmp_path / "s")
    doc = run_all(depot, classes=["Sprite"])
    items = by_object(doc)
    assert set(items) == {oid(11), oid(12), oid(13)}  # their textures decoded, not exported
    assert all(i["status"] == "exported" for i in items.values())
    doc = run_all(depot, classes=["GameObject"])
    assert set(by_object(doc)) == {oid(p) for p in (60, 61, 62, 63, 64, 65)}
    assert list(doc["tasks"]) == ["unity.export:all:60"]
    keys = {k: x.key for k, x in run_all(depot)["tasks"].items()}
    assert keys["unity.export:all:60"] == doc["tasks"]["unity.export:all:60"].key  # a selection is no part of keys


def test_scripts_come_from_the_context_only(tmp_path):
    depot = Depot(tmp_path / "s")
    doc = run_all(depot, classes=["MonoBehaviour", "GameObject"], scripts={})
    items = by_object(doc)
    assert items[oid(70)]["reason"]["code"] == "script.unresolved"
    issues = art(doc, f"{oid(60)}#json")["semantics"]["issues"]  # the components of a hierarchy
    assert issues[0]["code"] == "script.unresolved" and issues[0]["message"].startswith("2 MonoBehaviours")


def test_a_converter_error_gives_the_typetree(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "s")

    def broken(tt, ref):
        raise KeyError("m_SavedProperties")

    monkeypatch.setattr(material, "material_doc", broken)
    doc = run_all(depot, classes=["Material"])
    item = by_object(doc)[oid(50)]
    assert item["status"] == "generic" and item["reason"] == {
        "code": "generic.error",
        "message": "KeyError: 'm_SavedProperties'",
    }
    assert obj(depot, doc, f"{oid(50)}#json")["m_Name"] == "mat"


def test_a_broken_hierarchy_gives_its_objects_one_by_one(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "s")

    def broken(*args):
        raise RuntimeError("component failed")

    monkeypatch.setattr(prefab, "_component", broken)
    doc = run_all(depot, classes=["GameObject"])
    items = by_object(doc)
    assert {i["status"] for i in items.values()} == {"generic"}
    assert all(i["reason"]["code"] == "generic.error" for i in items.values())
    assert items[oid(63)]["reason"]["message"].startswith(f"hierarchy of {oid(60)}: RuntimeError")


def test_unreadable_objects_fail(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "s")
    real = UnityBundle.typetree

    def typetree(self, o):
        if o.path_id == 95:
            raise ValueError("Expected to read 40 bytes, but only read 12 bytes")
        return real(self, o)

    monkeypatch.setattr(UnityBundle, "typetree", typetree)
    doc = run_all(depot, classes=["Animator"])
    (item,) = doc["items"]
    assert item["status"] == "failed" and item["reason"]["code"] == "failed.read"
    assert doc["status"] == {"unity.export:all:95": "partial"}


def test_task_with_other_atoms_is_refused(tmp_path):
    depot = Depot(tmp_path / "s")
    env = planning_env(depot, ("all",))
    t = unit_task(depot, env, "all:10")
    bad = Task(t.stage, t.version, t.subject, t.params, {**t.atoms, "tex.png": "tex.png/0"}, t.inputs, t.context)
    with pytest.raises(IncompatibleTask, match="tex.png"):
        execute(bad, depot, registry(ExportStage(FAKE_READER)))
    missing = Task(
        t.stage,
        t.version,
        t.subject,
        t.params,
        {k: v for k, v in t.atoms.items() if k != "tex.png"},
        t.inputs,
        t.context,
    )
    with pytest.raises(IncompatibleTask, match="tex.png"):
        execute(missing, depot, registry(ExportStage(FAKE_READER)))
    other = Task(t.stage, t.version, t.subject, t.params, t.atoms, t.inputs, {**t.context, "unit": f"{CAB}:999"})
    with pytest.raises(IncompatibleTask, match="not in the bundle"):
        execute(other, depot, registry(ExportStage(FAKE_READER)))


# ---------------------------------------------------------------- describing tasks
def test_describe_reads_the_units_and_the_script_table(tmp_path):
    depot = Depot(tmp_path / "s")
    env = planning_env(depot)
    stage = ExportStage(FAKE_READER)
    subjects = stage.subjects(env)
    assert "all:60" in subjects and "all:61" not in subjects and "atlas:1" in subjects
    # a selection's units and the units their objects reach in other bundles (a sprite's atlas and its texture, not
    # the other sprites the atlas packs); not the rest of those bundles
    assert [
        s for s in stage.subjects(planning_env(Depot(tmp_path / "t"), selected=["all"])) if s.startswith("atlas")
    ] == ["atlas:98", "atlas:99"]
    assert stage.subjects(planning_env(Depot(tmp_path / "u"), selected=["plain"])) == ["plain:1", "plain:2"]
    t = describe(stage, "all:10", None, env)
    assert t.atoms == export_atoms(["Texture2D"], FAKE_READER.id) and t.subject == "all:10"
    assert t.context == {"unit": oid(10), "bundle": "all"}
    assert stage.depends("all:10", env) == ["unity.census:all", "link.scripts:all"]
    m = describe(stage, "all:70", None, env)
    assert m.context["scripts"] == {oid(80): "A|Game|Local"}
    p = describe(stage, "all:60", None, env)  # a hierarchy: its objects' classes and scripts
    assert set(p.atoms) == set(export_atoms(["GameObject", "MonoBehaviour", "Transform"], FAKE_READER.id))
    assert p.context["scripts"] == {
        "CAB-scripts:7": SCRIPTS["CAB-scripts:7"],
        "CAB-scripts:8": SCRIPTS["CAB-scripts:8"],
    }
    clip = describe(stage, "all:96", None, env)  # its bindings' scripts
    assert clip.context["scripts"] == {"CAB-scripts:12": "Assembly-CSharp|Game|Fader"}
    assert t.cost.peak_bytes >= 72 << 20 and stage.batch(t) == t.inputs[0].sha256 == stage.batch(p)
    fresh = Env(depot, env.facts)
    from nnnotes.pipeline.stages import Pending

    fresh.waiting("unity.census:all")
    with pytest.raises(Pending):
        stage.subjects(fresh)


def test_the_tasks_of_a_bundle_load_it_once_and_their_order_changes_nothing(tmp_path):
    opened = []

    def counting(data, name=None):
        opened.append(bytes(data))
        return open_fake(data, name)

    reader = Impl("fakeunity/1", counting, lambda facts: estimate(0.01, 1 << 20))
    depot = Depot(tmp_path / "s")
    env = planning_env(depot, ("all",))
    stage = ExportStage(reader)
    subjects = stage.subjects(env)
    tasks = [describe(stage, s, None, env) for s in subjects]
    results = {}
    for t in tasks:
        results[t.id] = stage.run(t, depot)
    assert opened == [b"fake:all"]
    stage.end_batch(stage.batch(tasks[0]))
    assert stage._loaded is None
    backwards = ExportStage(reader)
    for t in reversed(tasks):
        out = backwards.run(t, depot)
        assert contract.encode(out.items) == contract.encode(results[t.id].items), t.id
        assert contract.encode(out.artifacts) == contract.encode(results[t.id].artifacts), t.id
    assert len(opened) == 2


def test_files_without_type_trees_read_their_scripts_with_the_shipped_ones(tmp_path, monkeypatch):
    from nnnotes.unity import scripttypes

    monkeypatch.setattr(scripttypes, "script_types", lambda version: PLAYER_TYPETREES)
    depot = Depot(tmp_path / "s")
    doc = run_all(depot, ("player",), scripts=PLAYER_SCRIPTS)
    items = by_object(doc)

    def p(pid):
        return oid(pid, PLAYER)

    def script(pid, cls):
        return {"$ref": {"file": PLAYER, "pathId": str(pid), "class": "MonoScript", "name": cls}}

    assert items[p(2)]["status"] == "exported"
    assert obj(depot, doc, f"{p(2)}#json") == {
        "$script": SETTINGS,
        "m_GameObject": None,
        "m_Enabled": 1,
        "m_Script": script(50, "SoundVolumeSettings"),
        "m_Name": "volume",
        "target": {"$ref": {"file": "unity default resources", "pathId": "10"}},
        "volume": 0.5,
    }
    assert items[p(2)]["refs"] == [p(50), "unity default resources:10"]
    # a type hash without a shipped type tree, or with one that does not fit the object: its header, and the reason
    for pid, why in (
        (5, f"none is given for {IMAGE} (type hash {IMAGE_HASH})"),
        (4, f"type tree of {CHANGED} does not fit"),
    ):
        assert items[p(pid)]["status"] == "generic"
        assert items[p(pid)]["reason"]["code"] == "unsupported.typetree" and why in items[p(pid)]["reason"]["message"]
    assert obj(depot, doc, f"{p(5)}#json") == {
        "m_GameObject": None,
        "m_Enabled": 1,
        "m_Script": script(51, "Image"),
        "m_Name": "image",
    }
    # a hierarchy keeps its components' headers and says so
    assert art(doc, f"{p(20)}#json")["semantics"]["issues"] == [
        {
            "code": "unsupported.typetree",
            "message": "1 components read in part (their file stores no type tree that fits)",
        }
    ]
    # a class whose type tree reads the stored layout right up to a known field (by its type hash): those fields
    assert items[p(1)]["status"] == "generic" and items[p(1)]["reason"]["code"] == "unsupported.typetree"
    assert "read up to m_ActiveColorSpace" in items[p(1)]["reason"]["message"]
    assert obj(depot, doc, f"{p(1)}#json") == {"productGUID": "00", "m_ActiveColorSpace": 1}
    assert items[p(6)]["status"] == "failed" and items[p(6)]["reason"]["code"] == "failed.read"
    (component,) = obj(depot, doc, f"{p(20)}#json")["nodes"][0]["components"]
    assert component["script"] == IMAGE and set(component["fields"]) == {"m_Enabled", "m_Script", "m_Name"}
    assert items[p(3)]["status"] == "contained"


def test_the_context_holds_the_shipped_type_trees_of_its_scripts(tmp_path, monkeypatch):
    from nnnotes.unity import scripttypes

    shipped = scripttypes.load_typetrees("6000.3.12f1")["classes"]["DOTween.dll|DG.Tweening.Core|DOTweenSettings"]
    assert scripttypes.script_types("6000.3.12f1")[shipped["typeHash"]] == shipped["nodes"]
    trees = {**PLAYER_TYPETREES, shipped["typeHash"]: shipped["nodes"]}
    monkeypatch.setattr(scripttypes, "script_types", lambda version: trees)
    env = planning_env(Depot(tmp_path / "s"), names=("player",), scripts=PLAYER_SCRIPTS)
    stage = ExportStage(FAKE_READER)
    # the trees of its MonoBehaviours' type hashes; a hierarchy whose component has none: no trees
    assert describe(stage, "player:2", None, env).context["typetrees"] == {PLAYER_HASH: SETTINGS_ROWS}
    assert describe(stage, "player:4", None, env).context["typetrees"] == {OTHER_HASH: SETTINGS_ROWS[:-1]}
    assert "typetrees" not in describe(stage, "player:20", None, env).context
    assert "typetrees" not in describe(stage, "all:70", None, planning_env(Depot(tmp_path / "t"))).context


def test_the_font_fence_of_a_request(tmp_path):
    """units.build: a reference of an unfenced unit into a fenced bundle reaches nothing, from a root or from a unit
    the walk reaches; a fenced unit's references reach as usual; `info` holds the objects of the fenced units."""
    censuses = [(n, census_mod.census_env(BUNDLES[n](), shipped=True)) for n in ("all", "atlas")]
    atlas = f"{ATLAS_CAB}:99"
    t = units.build(censuses, ["all", "atlas"], ["all"], fenced=["atlas"])
    assert "atlas:99" not in t.subjects() and "all:13" in t.subjects()
    assert t.fences("all:13", "atlas:99") and not t.reaches("all:13", "atlas:99", True)
    assert not t.fences("atlas:99", "all:13") and t.reaches("atlas:99", "all:13", True)
    assert t.fenced == {s for s in t.kinds if s.startswith("atlas:")}
    assert t.info[atlas] == {"class": "SpriteAtlas", "name": "remote"} and oid(13) not in t.info
    r = units.build(censuses, ["all", "atlas"], [], roots=[atlas], fenced=["atlas"])
    assert r.subjects() == ["atlas:97", "atlas:98", "atlas:99"]  # the root's references, across no fence


def test_the_unit_table(tmp_path):
    env = planning_env(Depot(tmp_path / "s"), ("all", "atlas", "plain"), selected=["all"], classes=["Sprite"])
    t = units.table(env)
    assert t.subjects() == ["all:11", "all:12", "all:13"]  # the atlas' own sprite 97 is not reached through 13
    u = t.unit("all:13")
    assert (u.bundle, u.root, u.cls, u.members) == ("all", oid(13), "Sprite", (oid(13),))
    assert t.subject_of(oid(62)) == "all:60" and t.subject_of(f"{ATLAS_CAB}:98") == "atlas:98"
    assert t.subject_of("CAB-plain:2") == "plain:2" and t.subject_of("CAB-x:1") is None
    assert t.files == {CAB: "all", ATLAS_CAB: "atlas", "CAB-plain": "plain"}
    assert t.per_bundle == {"all": 3} and t.classes == {"Sprite": 3}
    assert units.table(env) is t  # kept for the same planning state
    h = units.table(planning_env(Depot(tmp_path / "t"), ("all",))).unit("all:60")
    assert h.members == tuple(oid(p) for p in (60, 61, 62, 63, 64, 65))
    assert h.counts == (("GameObject", 2), ("MonoBehaviour", 2), ("Transform", 2))
    with pytest.raises(ValueError, match="no requested unit"):
        t.unit("all:61")


def crop_setup(depot):
    env = planning_env(depot)
    export(depot, env)
    return env


def test_sprite_crop_equals_the_same_sprite_cropped_in_its_atlas_bundle(tmp_path):
    depot = Depot(tmp_path / "s")
    env = crop_setup(depot)
    stage = SpriteCropStage()
    assert stage.subjects(env) == ["atlas:99"]
    t = describe(stage, "atlas:99", None, env)
    assert [i.role for i in t.inputs] == ["atlas", f"sprite:{oid(13)}", f"texture:{ATLAS_CAB}:98"]
    assert stage.depends("atlas:99", env) == [
        "unity.export:all:13",
        "unity.export:atlas:98",
        "unity.export:atlas:99",
    ]
    assert set(t.atoms) == {"png.encode", "sprite.crop"}
    execute(t, depot, registry(stage))
    doc = depot.result(t.key)
    (item,) = doc["items"]
    assert item == {"object": oid(13), "status": "exported", "artifacts": [f"{oid(13)}#image"], "class": "Sprite"}
    a = doc["artifacts"][0]
    assert a["provenance"]["object"]["name"] == "remote" and a["provenance"]["object"]["file"] == CAB
    local = depot.result(env.key("unity.export:atlas:97"))
    same = art(local, f"{ATLAS_CAB}:97#image")
    assert a["content"] == same["content"]  # the same bytes as a local crop
    assert Image.open(__import__("io").BytesIO(depot.read(a["content"]["sha256"]))).size == (8, 8)


def test_sprite_crop_waits_for_exports(tmp_path):
    depot = Depot(tmp_path / "s")
    env = planning_env(depot)
    env.waiting("unity.export:all:13")
    from nnnotes.pipeline.stages import Pending

    with pytest.raises(Pending):
        SpriteCropStage().subjects(env)


def test_modules_import_without_unitypy():
    code = (
        "import sys; import nnnotes.pipeline.objexport as o; o.ExportStage().ATOMS; o.SpriteCropStage().ATOMS; "
        "print(sorted(m for m in ('UnityPy', 'PIL', 'nnnotes.unity.texels') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "[]"


# ---------------------------------------------------------------- the documentation
def test_every_reason_code_and_converter_is_documented():
    doc = (ROOT / "docs" / "stages.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+(?:\.[A-Za-z_]+)+)` \|", doc, re.M))
    src = ROOT / "src" / "nnnotes"
    sources = [
        src / "pipeline" / "objexport.py",
        *sorted((src / "unity" / "convert").glob("*.py")),
        *sorted((src / "unity" / "atoms").glob("*.py")),
    ]
    assert len(sources) > 10
    raised = set(REASONS)
    for p in sources:
        text = p.read_text(encoding="utf-8")
        raised |= set(re.findall(r'Unsupported\(\s*"([^"]+)"', text))
        raised |= set(re.findall(r'_fallback\([^,]+,\s*"([^"]+)"', text))
        raised |= set(re.findall(r'\(\s*"((?:partial|unsupported|empty|no_data|generic|script|failed)\.[^"]+)",', text))
    assert raised - documented - set(CONVERTERS) == set()
    for name, version in CONVERTERS.items():
        assert f"`{name}/{version}`" in doc, name
    for stage in (ExportStage, SpriteCropStage):
        assert f"`{stage.name}`" in doc


# ---------------------------------------------------------------- golden records
# converter -> the unit of the synthetic bundles its golden fixture converts
CONVERTER_UNITS = {
    "tex.png": "all:10",
    "sprite.png": "all:12",
    "mesh.glb": "all:30",
    "prefab.json": "all:60",
    "material.json": "all:50",
    "clip.json": "all:96",
    "controller.json": "all:92",
    "mono.json": "all:73",
    "data": "all:40",
    "font": "all:42",
    "shader.json": "all:51",
    "generic.json": "all:95",
}
LIBRARIES = ["Pillow", "UnityPy", "astc-encoder-py", "numpy", "texture2ddecoder"]


def export_golden():
    """Provider of tests/golden/unity.export.json: one fixture per converter (a unit of the synthetic bundles that
    the converter converts)."""

    def fixture(subject):
        return lambda depot: unit_task(depot, planning_env(depot, ("all",)), subject)

    fixtures = {name: fixture(subject) for name, subject in CONVERTER_UNITS.items()}
    fixtures["cri.usm"] = movie_task
    return {"stage": ExportStage(FAKE_READER), "fixtures": fixtures, "libraries": LIBRARIES}


def crop_golden():
    """Provider of tests/golden/sprite.crop.json."""

    def fixture(depot):
        env = crop_setup(depot)
        return describe(SpriteCropStage(), "atlas:99", None, env)

    return {"stage": SpriteCropStage(), "fixtures": {"atlas": fixture}, "libraries": LIBRARIES}


def test_golden_providers_cover_every_converter():
    p = export_golden()
    assert set(p["fixtures"]) == set(CONVERTERS)


def test_a_converter_change_without_a_version_bump_fails_its_golden(tmp_path, monkeypatch):
    from nnnotes.pipeline.stages import golden, golden_problems, library_versions

    expected = contract.loads((ROOT / "tests" / "golden" / "unity.export.json").read_bytes())
    if library_versions(expected["libraries"]) != expected["libraries"]:
        pytest.skip("golden record of other library versions")

    def changed(ctx, info):
        emit = ctx.emit
        ctx.emit = lambda *a, **k: emit(*a[:6], {**a[6], "extra": 1}, *a[7:], **k)
        try:
            files.font(ctx, info)
        finally:
            ctx.emit = emit

    simple = tuple((cls, conv, changed if conv == "font" else fn) for cls, conv, fn in objexport._SIMPLE)
    monkeypatch.setattr(objexport, "_SIMPLE", simple)
    p = export_golden()
    actual = golden(p["stage"], p["fixtures"], Depot(tmp_path / "s"), p["libraries"])
    problems = golden_problems(expected, actual, p["stage"])
    assert "unity.export: fixture font: output changed without a version bump" in problems
    assert not any("fixture tex.png" in x for x in problems)


def test_a_sprite_reaches_its_atlas_and_not_the_atlas_other_sprites():
    from tests.helpers.units import census_doc

    def doc(file, objects, refs):
        d = census_doc(file, objects)
        for o in d["files"][0]["objects"]:
            o["refs"] = [f"{file}:{t}" if isinstance(t, int) else t for t in refs.get(o["pathId"], ())]
        return d

    # the atlas packs sprites 2 and 3 and holds texture 4; each sprite references the atlas and the texture
    atlas = doc(
        "CAB-a",
        [(1, "SpriteAtlas"), (2, "Sprite"), (3, "Sprite"), (4, "Texture2D")],
        {1: [2, 3, 4], 2: [1, 4], 3: [1, 4]},
    )
    prefab = doc("CAB-p", [(9, "MonoBehaviour")], {9: ["CAB-a:2"]})
    censuses = [("atlas", atlas), ("prefab", prefab)]
    t = units.build(censuses, ["atlas", "prefab"], ["prefab"])
    assert t.subjects() == ["atlas:1", "atlas:2", "atlas:4", "prefab:9"]  # not sprite 3
    assert not t.reaches("atlas:1", "atlas:3", False) and t.reaches("atlas:1", "atlas:3", True)
    assert t.reaches("atlas:1", "atlas:4", False)
    # an atlas the request starts from reaches every sprite it packs
    assert units.build(censuses, ["atlas", "prefab"], [], roots=["CAB-a:1"]).subjects() == [
        "atlas:1",
        "atlas:2",
        "atlas:3",
        "atlas:4",
    ]
