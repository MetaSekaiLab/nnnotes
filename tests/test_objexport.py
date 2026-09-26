"""unity.export and sprite.crop on synthetic bundles (fakeunity environments behind a stand-in reader atom): every
object gets exactly one item, the converters' artifacts, references, byte arrays and float forms, the generic
fallback with its reason codes, class selection, determinism, the cross-bundle sprite crop, and the golden records'
providers."""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fakeunity import (F32, NORMAL, POSITION, UV0, FakeEnvironment, FakeTexture2D, mesh_tt, pptr, quad, rect,
                       render_data, rgba_bytes, sprite_tt, submesh)
from nnnotes import census as census_mod, contract, objexport
from nnnotes.atoms import Impl, astc, estimate, gltf
from nnnotes.atoms.reader import UnityBundle
from nnnotes.contract import IncompatibleTask, Input, Task
from nnnotes.objexport import CONVERTERS, CROP, EXPORT, ExportStage, SpriteCropStage, export_atoms
from nnnotes.stages import Env, describe, execute, registry
from nnnotes.store import Store

ROOT = Path(__file__).resolve().parents[1]
CAB, ATLAS_CAB = "CAB-a", "CAB-atlas"
SCRIPTS = {f"{CAB}:80": "A|Game|Local", "CAB-scripts:7": "Assembly-CSharp|Game|Behaviour",
           "CAB-scripts:8": "Assembly-CSharp||Holder", "CAB-scripts:10": "Assembly-CSharp|Fwk.Sound|SplitAcbData",
           "CAB-scripts:11": "CriMw.CriWare.Assets.Runtime|CriWare.Assets|CriAtomAcbAsset",
           "CAB-scripts:12": "Assembly-CSharp|Game|Fader"}
INF = float("inf")


# ---------------------------------------------------------------- synthetic bundles
def _sprite_mesh(r):
    pos, idx = quad(r)
    return pos, idx


def _atlas_entry(tex_pid, tr, raw=3):
    return {"texture": pptr(tex_pid), "alphaTexture": pptr(), "textureRect": tr,
            "textureRectOffset": {"x": 0.0, "y": 0.0}, "atlasRectOffset": {"x": 0.0, "y": 0.0},
            "uvTransform": {"x": 100.0, "y": 0.0, "z": 100.0, "w": 0.0}, "downscaleMultiplier": 1.0,
            "settingsRaw": raw, "secondaryTextures": []}


def _key(n):
    return [{"data[0]": n, "data[1]": 2, "data[2]": 3, "data[3]": 4}, 0]


def _transform(go, children=(), father=0):
    return {"m_GameObject": pptr(go), "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "m_LocalPosition": {"x": 1.0, "y": 2.0, "z": 3.0}, "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
            "m_Children": [pptr(c) for c in children], "m_Father": pptr(father)}


def _go(name, comps):
    return {"m_Component": [{"component": pptr(c)} for c in comps], "m_Layer": 5, "m_Name": name, "m_Tag": 0,
            "m_IsActive": True}


def _mono(go, script, name="", **fields):
    return {"m_GameObject": pptr(go), "m_Enabled": 1, "m_Script": script, "m_Name": name, **fields}


def _clip():
    return {"m_Name": "clip", "m_Legacy": False, "m_Compressed": False, "m_SampleRate": 60.0, "m_WrapMode": 0,
            "m_MuscleClip": {"m_StartTime": 0.0, "m_StopTime": 1.0, "m_LoopTime": 0, "m_CycleOffset": 0.0,
                             "m_Clip": {"data": {
                                 "m_StreamedClip": {"data": [], "curveCount": 0},
                                 "m_DenseClip": {"m_FrameCount": 0, "m_CurveCount": 0, "m_SampleRate": 60.0,
                                                 "m_BeginTime": 0.0, "m_SampleArray": []},
                                 "m_ConstantClip": {"data": [0.5, 1.0, 2.0]}}}},
            "m_ClipBindingConstant": {"genericBindings": [
                {"path": 0, "attribute": 1, "script": pptr(), "typeID": 4, "customType": 0, "isPPtrCurve": 0,
                 "isIntCurve": 0, "isSerializeReferenceCurve": 0}], "pptrCurveMapping": []},
            "m_Events": []}


def _fade_clip():
    """A clip animating a field of a MonoBehaviour class whose script is in another bundle."""
    clip = _clip()
    clip["m_Name"] = "fade"
    clip["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"] = [0.25]
    clip["m_ClipBindingConstant"]["genericBindings"] = [
        {"path": 0, "attribute": 77, "script": pptr(12, 2), "typeID": 114, "customType": 0, "isPPtrCurve": 0,
         "isIntCurve": 0, "isSerializeReferenceCurve": 0}]
    return clip


# cue sheets held in the bundle: a SplitAcbData (chunk TextAssets joined, XOR 0x5a) and an embedded ACB
SPLIT_ACB = b"@UTF" + bytes(range(256)) * 4
SPLIT_CHUNKS = [bytes(b ^ 0x5A for b in SPLIT_ACB)[:600], bytes(b ^ 0x5A for b in SPLIT_ACB)[600:]]
EMBEDDED_ACB = b"@UTF" + bytes(range(255, -1, -1)) * 20
IMPL = ("CriMw.CriWare.Assets.Runtime", "CriWare.Assets", "CriSerializedBytesAssetImpl")


def _embedded_acb_asset(f, pid: int, name: str, acb: bytes):
    """A CriWare.Assets asset whose `implementation` managed reference is a CriSerializedBytesAssetImpl holding
    `acb`, with UnityPy's managed reference nodes."""
    from fakeunity import node_of
    tt = _mono(0, pptr(11, 2), name, implementation={"rid": 1000}, awb=pptr(),
               references={"version": 2, "RefIds": [{"rid": 1000, "type": {"class": IMPL[2], "ns": IMPL[1],
                                                                             "asm": IMPL[0]},
                                                     "data": {"data": list(acb)}}]})
    node = node_of(tt, "Base", "MonoBehaviour", ("data",))
    refs = next(c for c in node.m_Children if c.m_Name == "references")
    refs.m_Type = "ManagedReferencesRegistry"
    item = next(c for c in refs.m_Children if c.m_Name == "RefIds").m_Children[0].m_Children[1]
    item.m_Type = "ReferencedObject"
    for c in item.m_Children:
        c.m_Type = {"type": "ReferencedManagedType", "data": "ReferencedObjectData"}.get(c.m_Name, c.m_Type)
    from types import SimpleNamespace
    f.ref_types.append(SimpleNamespace(m_AssemblyName=IMPL[0], m_NameSpace=IMPL[1], m_ClassName=IMPL[2],
                                       node=node_of({"data": list(acb)}, "data", IMPL[2], ("data",))))
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
    prog = lambda i, kws: {"m_PlayerSubPrograms": [[{"m_BlobIndex": i, "m_KeywordIndices": kws,  # noqa: E731
                                                     "m_GpuProgramType": gles3}]]}
    parsed = {"m_Name": "Test/Unlit", "m_PropInfo": {"m_Props": [{"m_Name": "_Color", "m_DefValue[0]": 1.0}]},
              "m_KeywordNames": ["KW_A", "KW_B"], "m_FallbackName": "",
              "m_SubShaders": [{"m_Tags": {"tags": [("Queue", "Geometry")]}, "m_LOD": 100, "m_Passes": [
                  {"m_Name": "", "m_State": {"m_Name": "MAIN", "m_ZWrite": {"val": 1.0}}, "m_Tags": {"tags": []},
                   "progVertex": prog(0, [0]), "progFragment": prog(1, [])}]}]}
    return lz4.block.compress(raw, store_size=False), len(raw), parsed


def _controller(clip_pid):
    return {"m_Name": "ctl", "m_Controller": {"m_LayerArray": [], "m_StateMachineArray": [],
                                               "m_Values": {"data": {"m_ValueArray": []}},
                                               "m_DefaultValues": {"data": {"m_BoolValues": [True]}}},
            "m_TOS": [], "m_AnimationClips": [pptr(clip_pid)]}


def _material():
    return {"m_Name": "mat", "m_Shader": pptr(5, 1), "m_ValidKeywords": ["K"], "m_InvalidKeywords": [],
            "m_CustomRenderQueue": -1, "stringTagMap": [], "disabledShaderPasses": [],
            "m_SavedProperties": {
                "m_TexEnvs": [("_MainTex", {"m_Texture": pptr(10), "m_Scale": {"x": 1.0, "y": 1.0},
                                            "m_Offset": {"x": 0.0, "y": 0.0}})],
                "m_Ints": [], "m_Floats": [("_Cutoff", 0.5)],
                "m_Colors": [("_Color", {"r": 1.0, "g": INF, "b": 0.0, "a": 1.0})]}}


def _ttf(n=40):
    body = b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + (16).to_bytes(2, "big") + b"\0" * 4 + b"\0" * 16
    return body + bytes(range(n))


def _asset_bundle(entries):
    return {"m_Name": "b", "m_PreloadTable": [pptr(10)],
            "m_Container": [(path, {"preloadIndex": 0, "preloadSize": 1, "asset": pptr(pid)})
                            for path, pid in entries],
            "m_MainAsset": {"preloadIndex": 0, "preloadSize": 0, "asset": pptr()}, "m_Dependencies": [],
            "m_AssetBundleName": "b.bundle", "m_IsStreamedSceneAssetBundle": False, "m_SceneHashes": []}


W, H = 32, 24
TEX_PIXELS = rgba_bytes(W, H, seed=3)
MOC = b"MOC3" + bytes(range(256)) * 20


def bundle_all() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file(CAB, externals=["archive:/CAB-atlas/CAB-atlas", "archive:/CAB-scripts/CAB-scripts"])
    f.add(1, "AssetBundle", _asset_bundle([("assets/x/tex.png", 10), ("assets/x/tex.png", 11),
                                           ("assets/x/readme.txt", 40), ("assets/p/root.prefab", 60)]))
    for path, pid in (("assets/x/tex.png", 10), ("assets/x/tex.png", 11), ("assets/x/readme.txt", 40),
                      ("assets/p/root.prefab", 60)):
        f.contain(path, pid)
    tex = FakeTexture2D(TEX_PIXELS, W, H, 4, "tex")
    f.add(10, "Texture2D", {"m_Name": "tex", "m_Width": W, "m_Height": H, "m_TextureFormat": 4, "m_MipCount": 1,
                            "image data": TEX_PIXELS}, obj=tex)
    r1 = rect(2, 3, 12, 10)
    pos, idx = _sprite_mesh(r1)
    f.add(11, "Sprite", sprite_tt("square", r1, render_data(pptr(10), r1, 66, pos, idx)))
    r2 = rect(16, 4, 10, 12)
    pos2, idx2 = _sprite_mesh(r2)
    f.add(12, "Sprite", sprite_tt("packed", r2, render_data(pptr(), r2, 3, pos2, idx2), atlas=pptr(20),
                                  atlas_tags=["atlas"], key=((5, 2, 3, 4), 0)))
    f.add(20, "SpriteAtlas", {"m_Name": "atlas", "m_PackedSprites": [pptr(12)], "m_PackedSpriteNamesToIndex":
                              ["packed"], "m_RenderDataMap": [(_key(5), _atlas_entry(10, r2))], "m_Tag": "atlas",
                              "m_IsVariant": False})
    r3 = rect(0, 0, 8, 8)
    pos3, idx3 = _sprite_mesh(r3)
    f.add(13, "Sprite", sprite_tt("remote", r3, render_data(pptr(), r3, 3, pos3, idx3), atlas=pptr(99, 1),
                                  atlas_tags=["remote"], key=((9, 2, 3, 4), 0)))
    f.add(14, "Texture2D", {"m_Name": "empty", "m_Width": 0, "m_Height": 0, "m_TextureFormat": 4,
                            "m_MipCount": 1, "image data": b""}, obj=FakeTexture2D(b"", 0, 0, 4, "empty"))
    hdr = bytes(range(64))
    f.add(15, "Texture2D", {"m_Name": "hdr", "m_Width": 8, "m_Height": 8, "m_TextureFormat": 66, "m_MipCount": 2,
                            "image data": hdr}, obj=FakeTexture2D(hdr + b"\1" * 16, 8, 8, 66, "hdr"))
    tri_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    f.add(30, "Mesh", mesh_tt("tri", 3, {POSITION: (F32, 3, tri_pos), NORMAL: (F32, 3, np.tile([0, 0, 1], (3, 1))),
                                         UV0: (F32, 2, tri_pos[:, :2])}, [0, 1, 2], [submesh(0, 3)]))
    f.add(31, "Mesh", mesh_tt("none", 0, {}))
    f.add(40, "TextAsset", {"m_Name": "readme", "m_Script": "hello\n"})
    f.add(41, "TextAsset", {"m_Name": "raw", "m_Script": b"\xff\xfe\x00bin".decode("utf-8", "surrogateescape")})
    f.add(42, "Font", {"m_Name": "font", "m_FontData": list(_ttf())}, bytes_fields=("m_FontData",))
    f.add(43, "Font", {"m_Name": "nofont", "m_FontData": []}, bytes_fields=("m_FontData",))
    f.add(50, "Material", _material())
    # a hierarchy: root (60/61) with a behaviour (62), child (63/64) with a behaviour (65)
    f.add(60, "GameObject", _go("root", [61, 62]))
    f.add(61, "Transform", _transform(60, [64]))
    f.add(62, "MonoBehaviour", _mono(60, pptr(7, 2), target=pptr(63), tex=pptr(10), value=float("nan"),
                                     small=list(b"abc")), bytes_fields=("small",))
    f.add(63, "GameObject", _go("child", [64, 65]))
    f.add(64, "Transform", _transform(63, father=61))
    f.add(65, "MonoBehaviour", _mono(63, pptr(8, 2), up=pptr(61), comp=pptr(62), far=pptr(3, 2)))
    f.add(70, "MonoBehaviour", _mono(0, pptr(80), "moc", _bytes=list(MOC), text="caf\udcc3"),
          bytes_fields=("_bytes",))
    f.add(71, "MonoBehaviour", _mono(0, pptr(9, 2), "lost"))
    f.add(72, "MonoBehaviour", _mono(0, pptr(), "noscript"))
    f.add(80, "MonoScript", {"m_Name": "Local", "m_ExecutionOrder": 0, "m_ClassName": "Local",
                             "m_Namespace": "Game", "m_AssemblyName": "A.dll"})
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
    f.add(73, "MonoBehaviour", _mono(0, pptr(10, 2), "bgm_test", _cueSheetName="bgm_test",
                                     _chunks=[pptr(44), pptr(45)]))
    _embedded_acb_asset(f, 74, "se_test", EMBEDDED_ACB)
    blob, size, parsed = _shader_blob()
    from types import SimpleNamespace
    f.add(51, "Shader", {"m_ParsedForm": parsed, "platforms": [9], "offsets": [[0]], "compressedLengths": [[len(blob)]],
                         "decompressedLengths": [[size]], "compressedBlob": list(blob), "m_Dependencies": [],
                         "m_NonModifiableTextures": [], "m_ShaderIsBaked": True},
          obj=SimpleNamespace(compressedBlob=blob, platforms=[9], offsets=[[0]], compressedLengths=[[len(blob)]],
                              decompressedLengths=[[size]]), bytes_fields=("compressedBlob",))
    return env


def bundle_atlas() -> FakeEnvironment:
    """An atlas bundle: its SpriteAtlas (99) packs the remote sprite of bundle_all and a local copy of it (97)."""
    env = FakeEnvironment()
    f = env.file(ATLAS_CAB)
    f.add(1, "AssetBundle", _asset_bundle([("assets/atlas/a.spriteatlas", 99)]))
    f.contain("assets/atlas/a.spriteatlas", 99)
    px = rgba_bytes(W, H, seed=9)
    f.add(98, "Texture2D", {"m_Name": "page", "m_Width": W, "m_Height": H, "m_TextureFormat": 4, "m_MipCount": 1,
                            "image data": px}, obj=FakeTexture2D(px, W, H, 4, "page"))
    tr = rect(3, 5, 8, 8)
    f.add(99, "SpriteAtlas", {"m_Name": "remote", "m_PackedSprites": [pptr(13, 0), pptr(97)],
                              "m_PackedSpriteNamesToIndex": ["remote", "copy"],
                              "m_RenderDataMap": [(_key(9), _atlas_entry(98, tr, raw=3 | (1 << 2)))],
                              "m_Tag": "remote", "m_IsVariant": False})
    r3 = rect(0, 0, 8, 8)
    pos3, idx3 = _sprite_mesh(r3)
    f.add(97, "Sprite", sprite_tt("remote", r3, render_data(pptr(), r3, 3, pos3, idx3), atlas=pptr(99),
                                  atlas_tags=["remote"], key=((9, 2, 3, 4), 0)))
    return env


def bundle_plain() -> FakeEnvironment:
    env = FakeEnvironment()
    f = env.file("CAB-plain")
    f.add(1, "AssetBundle", _asset_bundle([]))
    f.add(2, "TextAsset", {"m_Name": "t", "m_Script": "{}"})
    return env


BUNDLES = {"all": bundle_all, "atlas": bundle_atlas, "plain": bundle_plain}


def open_fake(data: bytes) -> UnityBundle:
    return UnityBundle(BUNDLES[bytes(data).decode().removeprefix("fake:")]())


FAKE_READER = Impl("fakeunity/1", open_fake, lambda facts: estimate(0.01, 1 << 20))


def classes_of(name):
    return sorted({r.type.name for f in BUNDLES[name]().assets for r in f.objects.values()})


def task_for(store, name, classes=None, params=None, scripts=None):
    data = f"fake:{name}".encode()
    sha = store.put(data)
    stage = ExportStage(FAKE_READER)
    p = stage.normalize(dict(params or {}, classes=classes))
    return Task(EXPORT, 1, name, p, export_atoms(classes_of(name), FAKE_READER.id),
                (Input("bundle", sha, len(data), f"{name}.bundle", ({"kind": "store"},)),),
                {"scripts": dict(SCRIPTS if scripts is None else scripts)})


def run(store, task):
    stage = ExportStage(FAKE_READER)
    execute(task, store, registry(stage), force=True)
    return store.result(task.key)


def by_object(doc):
    return {i["object"]: i for i in doc["items"]}


def art(doc, aid):
    return next(a for a in doc["artifacts"] if a["id"] == aid)


def load(store, doc, aid):
    return store.read(art(doc, aid)["content"]["sha256"])


def obj(store, doc, aid):
    return contract.loads(load(store, doc, aid))


def oid(pid, cab=CAB):
    return f"{cab}:{pid}"


@pytest.fixture
def exported(tmp_path):
    store = Store(tmp_path / "store")
    task = task_for(store, "all")
    return store, task, run(store, task)


# ---------------------------------------------------------------- items
def test_every_object_has_exactly_one_item(exported):
    store, task, doc = exported
    objects = [contract.object_id(f.name, pid) for f in bundle_all().assets for pid in f.objects]
    items = by_object(doc)
    assert sorted(items) == sorted(objects) and len(doc["items"]) == len(objects)
    status = {o: (i["status"], (i.get("reason") or {}).get("code")) for o, i in items.items()}
    assert status == {
        oid(1): ("contained", None), oid(10): ("exported", None), oid(11): ("exported", None),
        oid(12): ("exported", None), oid(13): ("exported", None), oid(14): ("generic", "empty.texture"),
        oid(15): ("exported", None), oid(20): ("exported", None), oid(30): ("exported", None),
        oid(31): ("generic", "empty.mesh"), oid(40): ("exported", None), oid(41): ("exported", None),
        oid(42): ("exported", None), oid(43): ("generic", "no_data.font"), oid(50): ("exported", None),
        oid(60): ("exported", None), oid(61): ("contained", None), oid(62): ("contained", None),
        oid(63): ("contained", None), oid(64): ("contained", None), oid(65): ("contained", None),
        oid(70): ("exported", None), oid(71): ("generic", "script.unresolved"),
        oid(72): ("generic", "script.missing"), oid(80): ("contained", None), oid(90): ("exported", None),
        oid(91): ("generic", "generic.clip.legacy"), oid(92): ("exported", None),
        oid(93): ("generic", "generic.controller.override"), oid(94): ("unsupported", "unsupported.class.AudioClip"),
        oid(95): ("exported", None), oid(96): ("exported", None), oid(44): ("exported", None),
        oid(45): ("exported", None), oid(73): ("exported", None), oid(74): ("exported", None),
        oid(51): ("exported", None)}
    assert items[oid(61)]["in"] == f"{oid(60)}#prefab" and items[oid(80)]["in"] == "unity.export:all#scripts.json"
    assert items[oid(1)]["in"] == "unity.export:all#bundle.json"
    assert items[oid(94)]["artifacts"] == [f"{oid(94)}#json"]
    assert doc["status"] == "ok" and all(i["class"] for i in doc["items"])
    for a in doc["artifacts"]:
        owner, _role = contract.parse_artifact_id(a["id"])
        assert ("object" in a["provenance"]) == (not owner.startswith("unity.export:"))
        assert store.has(a["content"]["sha256"], a["content"]["size"])


def test_provenance_facts_and_atoms(exported):
    store, task, doc = exported
    a = art(doc, f"{oid(10)}#image")
    assert a["provenance"]["object"] == {"file": CAB, "pathId": 10, "classId": 28, "class": "Texture2D",
                                         "name": "tex", "unityVersion": "6000.3.12f1",
                                         "container": "assets/x/tex.png"}
    assert set(a["provenance"]["atoms"]) == {"reader", "sniff", "texture.decode", "png.encode", "astc.container",
                                             "tex.png"}
    assert a["provenance"]["inputs"] == [{"role": "bundle", "sha256": task.inputs[0].sha256}]
    g = art(doc, f"{oid(95)}#json")
    assert set(g["provenance"]["atoms"]) == {"reader", "sniff", "generic.json"}
    assert task.atoms["tex.png"] == "tex.png/1" and task.atoms["reader"] == "fakeunity/1"


# ---------------------------------------------------------------- converters
def test_textures(exported):
    store, _task, doc = exported
    png = load(store, doc, f"{oid(10)}#image")
    img = Image.open(__import__("io").BytesIO(png))
    assert img.size == (W, H) and img.mode == "RGBA"
    expected = Image.frombytes("RGBA", (W, H), TEX_PIXELS).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    assert img.tobytes() == expected.tobytes()
    from nnnotes.export import texture_png
    tex = bundle_all().assets[0].objects[10].obj
    assert png == texture_png(tex)                                         # the other commands' bytes
    facts = art(doc, f"{oid(10)}#image")["semantics"]["facts"]
    assert facts == {"width": W, "height": H, "textureFormat": "RGBA32", "mipCount": 1, "exportedMips": [0],
                     "mode": "RGBA"}
    a = load(store, doc, f"{oid(15)}#image")
    assert astc.parse_header(a) == {"block": (4, 4, 1), "size": (8, 8, 1)} and a[16:] == bytes(range(64))
    assert art(doc, f"{oid(15)}#image")["content"]["ext"] == "astc"
    empty = obj(store, doc, f"{oid(14)}#json")
    assert empty["m_Width"] == 0 and empty["image data"] == {"$hex": ""}


def test_sprites_same_bundle(exported):
    store, _task, doc = exported
    from nnnotes.atoms import png, sprite
    from nnnotes.atoms.mesh import arrays
    from nnnotes.atoms.reader import mesh_input_from_typetree
    from nnnotes.atoms.texture import TextureInput, decode
    env = bundle_all()
    f = env.assets[0]
    image = decode(TextureInput(TEX_PIXELS, W, H, 4, f.version, 13, b""))
    for pid in (11, 12):
        tt = f.objects[pid].tt
        src = tt["m_RD"] if pid == 11 else f.objects[20].tt["m_RenderDataMap"][0][1]
        mesh = arrays(mesh_input_from_typetree(tt, f.version)) if not (src["settingsRaw"] >> 1) & 1 else None
        want = sprite.crop(image, src["textureRect"], src["settingsRaw"], mesh, 100.0,
                           canvas={"rect": tt["m_Rect"], "offset": src["textureRectOffset"], "pivot": tt["m_Pivot"]})
        assert load(store, doc, f"{oid(pid)}#image") == png.encode(want)
        meta = obj(store, doc, f"{oid(pid)}#meta")
        assert meta["image"]["texture"] == {"$ref": {"file": CAB, "pathId": 10, "class": "Texture2D", "name": "tex"}}
        assert meta["sprite"]["m_Name"] == tt["m_Name"]
    meta = obj(store, doc, f"{oid(12)}#meta")
    assert meta["image"]["source"] == "atlas" and meta["image"]["atlas"]["$ref"]["pathId"] == 20
    assert meta["sprite"]["m_RD"]["m_VertexData"]["m_DataSize"]["$hex"]                  # small: inline
    refs = art(doc, f"{oid(12)}#image")["semantics"]["refs"]
    assert refs == [{"rel": "texture", "object": oid(10)}, {"rel": "atlas", "object": oid(20)}]


def test_sprite_with_its_atlas_elsewhere_gets_a_meta_only(exported):
    store, _task, doc = exported
    assert by_object(doc)[oid(13)]["artifacts"] == [f"{oid(13)}#meta"]
    a = art(doc, f"{oid(13)}#meta")
    assert a["semantics"]["facts"] == {"crop": CROP, "atlas": f"{ATLAS_CAB}:99"}
    meta = obj(store, doc, a["id"])
    assert meta["image"] == {"source": "atlas", "atlas": {"$ref": {"file": ATLAS_CAB, "pathId": 99}}, "stage": CROP}
    assert meta["object"]["name"] == "remote" and len(meta["mesh"]["positions"]) == 4


def test_mesh(exported):
    store, _task, doc = exported
    d, body = gltf.read(load(store, doc, f"{oid(30)}#mesh"))
    (prim,) = d["meshes"][0]["primitives"]
    assert set(prim["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"} and prim["mode"] == 4
    pos = gltf.accessor_array(d, body, prim["attributes"]["POSITION"])
    assert pos.tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert art(doc, f"{oid(30)}#mesh")["semantics"]["facts"] == {"vertexCount": 3, "submeshes": 1,
                                                                 "channels": ["position", "normal", "uv0"],
                                                                 "skinned": False}


def test_text_and_fonts(exported):
    store, _task, doc = exported
    a = art(doc, f"{oid(40)}#data")
    assert load(store, doc, a["id"]) == b"hello\n" and a["content"]["ext"] == "txt"
    assert load(store, doc, f"{oid(41)}#data") == b"\xff\xfe\x00bin"
    assert art(doc, f"{oid(41)}#data")["content"]["ext"] == "bin"
    f = art(doc, f"{oid(42)}#font")
    assert load(store, doc, f["id"]) == _ttf() and f["content"]["ext"] == "ttf"


def test_prefab(exported):
    store, _task, doc = exported
    p = obj(store, doc, f"{oid(60)}#prefab")
    assert p["name"] == "root" and [n["path"] for n in p["nodes"]] == ["root", "root/child"]
    root, child = p["nodes"]
    assert root["pathId"] == 60 and root["transformPathId"] == 61 and root["layer"] == 5
    (b,) = root["components"]
    assert b["type"] == "MonoBehaviour" and b["script"] == "Assembly-CSharp|Game|Behaviour"
    fields = b["fields"]
    assert fields["target"] == {"gameObject": "root/child"}
    assert fields["tex"] == {"$ref": {"file": CAB, "pathId": 10, "class": "Texture2D", "name": "tex"}}
    assert fields["m_Script"] == {"$ref": {"file": "CAB-scripts", "pathId": 7}}
    assert math.isnan(fields["value"]) and fields["small"] == {"$hex": "616263"}
    assert b'{"$float": "nan"}' in load(store, doc, f"{oid(60)}#prefab").replace(b"\n", b"").replace(b" ", b"") \
        or b'"$float"' in load(store, doc, f"{oid(60)}#prefab")
    (c,) = child["components"]
    assert c["fields"]["up"] == {"transform": "root"}
    assert c["fields"]["comp"] == {"component": "MonoBehaviour", "gameObject": "root", "class": "Behaviour"}
    assert c["fields"]["far"] == {"$ref": {"file": "CAB-scripts", "pathId": 3}}
    assert c["script"] == "Assembly-CSharp||Holder"


def test_scriptable_objects_blobs_and_strings(exported):
    store, _task, doc = exported
    m = obj(store, doc, f"{oid(70)}#json")
    assert m["$script"] == "A|Game|Local" and m["m_Name"] == "moc"
    blob = m["_bytes"]["$blob"]
    assert blob == {"sha256": contract.sha256(MOC), "size": len(MOC), "format": "moc3"}
    b = art(doc, f"{oid(70)}#blob:_bytes")
    assert b["content"]["ext"] == "moc3" and b["semantics"] == {"kind": "blob", "format": "moc3",
                                                                "facts": {"field": "_bytes"}}
    assert load(store, doc, b["id"]) == MOC
    assert m["text"] == {"$hex": "caf".encode().hex() + "c3"}
    assert by_object(doc)[oid(70)]["artifacts"] == [f"{oid(70)}#blob:_bytes", f"{oid(70)}#json"]
    lost = obj(store, doc, f"{oid(71)}#json")
    assert "$script" not in lost and lost["m_Script"] == {"$ref": {"file": "CAB-scripts", "pathId": 9}}


def test_cue_sheets_held_in_the_bundle(exported):
    store, _task, doc = exported
    from nnnotes import cri
    a = art(doc, f"{oid(73)}#acb")
    assert load(store, doc, a["id"]) == SPLIT_ACB == cri.split_acb(SPLIT_CHUNKS)
    assert a["content"]["ext"] == "acb" and a["semantics"] == {
        "kind": "cri.acb", "format": "acb", "facts": {"cueSheet": "bgm_test", "layout": "split", "chunks": 2},
        "refs": [{"rel": "chunk", "object": oid(44)}, {"rel": "chunk", "object": oid(45)}]}
    assert by_object(doc)[oid(73)]["artifacts"] == [f"{oid(73)}#acb", f"{oid(73)}#json"]
    assert obj(store, doc, f"{oid(73)}#json")["$script"] == "Assembly-CSharp|Fwk.Sound|SplitAcbData"
    assert load(store, doc, f"{oid(44)}#data") == SPLIT_CHUNKS[0]                  # the chunks stay exported
    e = art(doc, f"{oid(74)}#acb")
    assert load(store, doc, e["id"]) == EMBEDDED_ACB
    assert e["semantics"]["facts"] == {"cueSheet": "se_test", "layout": "embedded"}
    m = obj(store, doc, f"{oid(74)}#json")
    ref = m["references"]["RefIds"][0]
    assert ref["type"] == {"class": IMPL[2], "ns": IMPL[1], "asm": IMPL[0]}
    assert ref["data"]["data"] == {"$blob": {"sha256": contract.sha256(EMBEDDED_ACB), "size": len(EMBEDDED_ACB),
                                             "format": "acb"}}
    assert by_object(doc)[oid(74)]["artifacts"] == [f"{oid(74)}#acb", f"{oid(74)}#json"]   # no blob: artifact
    small = run(store, task_for(store, "all", classes=["MonoBehaviour"], params={"blobMin": 1 << 20}))
    assert load(store, small, f"{oid(74)}#acb") == EMBEDDED_ACB                  # whatever its size


def test_acb_layouts(tmp_path):
    from nnnotes import cri
    assert objexport.acb_asset({"_cueSheetName": "x", "_chunks": []})["layout"] == "split"
    t = {"m_Name": "s", "implementation": {"rid": 5}, "references": {"RefIds": [
        {"rid": 4, "type": {"class": IMPL[2]}}, {"rid": 5, "type": {"class": IMPL[2]}, "data": {"data": b"@UTF"}}]}}
    assert objexport.acb_asset(t) == {"layout": "embedded", "cueSheet": "s", "ref": 1, "impl": t["references"]
                                      ["RefIds"][1]}
    assert objexport.acb_asset({"implementation": {"rid": 5}, "references": {"RefIds": [
        {"rid": 5, "type": {"class": "Other"}}]}}) is None and objexport.acb_asset({"m_Name": "x"}) is None
    assert cri.embedded_acb(t, t["references"]["RefIds"][1]) == b"@UTF"
    with pytest.raises(NotImplementedError, match="external AWB"):
        cri.embedded_acb({**t, "awb": pptr(3)}, t["references"]["RefIds"][1], "s")
    with pytest.raises(RuntimeError, match="not an ACB"):
        cri.split_acb([b"\x01\x02\x03\x04", b"\x05"], "s")
    with pytest.raises(RuntimeError):
        cri.split_acb([])


def test_a_split_sheet_with_a_missing_chunk_gives_the_typetree(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    real = objexport.acb_asset
    monkeypatch.setattr(objexport, "acb_asset", lambda tt: (dict(real(tt), chunks=[pptr(44), pptr(99, 1)])
                                                            if real(tt) and real(tt)["layout"] == "split"
                                                            else real(tt)))
    doc = run(store, task_for(store, "all", classes=["MonoBehaviour"]))
    item = by_object(doc)[oid(73)]
    assert item["status"] == "generic" and item["reason"]["code"] == "generic.error"
    assert "is not a TextAsset of the bundle" in item["reason"]["message"]


def test_shader(exported):
    store, _task, doc = exported
    from nnnotes import shader
    env = bundle_all()
    r = env.assets[0].objects[51]
    text, recs, codes = shader._render(r, r.tt, "Test/Unlit")                  # what `nnnotes shader` writes
    assert load(store, doc, f"{oid(51)}#json") == text.encode("utf-8")
    index = obj(store, doc, f"{oid(51)}#index")
    assert index["name"] == "Test/Unlit" and [{k: v for k, v in x.items() if k != "role"}
                                               for x in index["variants"]] == recs
    assert [x["role"] for x in index["variants"]] == ["program:gles3/s0p0_vertex_0", "program:gles3/s0p0_fragment_0"]
    for x, code in zip(index["variants"], codes):
        a = art(doc, f"{oid(51)}#{x['role']}")
        assert load(store, doc, a["id"]) == code and a["content"]["ext"] == "glsl"
        assert a["semantics"]["facts"]["keywords"] == x["keywords"]
    assert codes[0] == b"#ifdef VERTEX\nvoid main(){}\n#endif\n" and index["variants"][0]["keywords"] == ["KW_A"]
    t = obj(store, doc, f"{oid(51)}#typetree")
    assert t["m_ParsedForm"]["m_Name"] == "Test/Unlit" and "$hex" in t["compressedBlob"]
    assert art(doc, f"{oid(51)}#json")["semantics"]["facts"] == {"name": "Test/Unlit", "variants": 2,
                                                                  "platforms": ["gles3"]}


def test_clip_bindings_name_scripts_of_other_bundles(exported):
    store, _task, doc = exported
    clip = obj(store, doc, f"{oid(96)}#json")
    (b,) = clip["bindings"]
    assert b["typeID"] == 114 and b["class"] == "Fader" and b["path"] == ""
    assert census_mod.script_refs(census_doc(open_fake(b"fake:all"))) == sorted(SCRIPTS) + ["CAB-scripts:9"]


def test_material_clip_controller(exported):
    store, _task, doc = exported
    mat = obj(store, doc, f"{oid(50)}#json")
    assert mat["shader"] == {"$ref": {"file": ATLAS_CAB, "pathId": 5}}
    assert mat["textures"]["_MainTex"]["texture"]["$ref"]["pathId"] == 10
    assert mat["colors"]["_Color"]["g"] == INF and b"1e999" in load(store, doc, f"{oid(50)}#json")
    clip = obj(store, doc, f"{oid(90)}#json")
    assert clip["clip"] == "clip" and clip["bindings"] == [{"path": "", "typeID": 4, "class": "Transform",
                                                            "attribute": "m_LocalPosition", "curves": 3}]
    ctl = obj(store, doc, f"{oid(92)}#json")
    assert ctl["clips"] == [{"$ref": {"file": CAB, "pathId": 90, "class": "AnimationClip", "name": "clip"}}]
    assert obj(store, doc, f"{oid(95)}#json")["m_Speed"] == INF


def test_loose_documents(exported):
    store, task, doc = exported
    s = obj(store, doc, "unity.export:all#scripts.json")
    assert list(s["scripts"]) == [oid(80)] and s["scripts"][oid(80)]["m_ClassName"] == "Local"
    b = obj(store, doc, "unity.export:all#bundle.json")
    assert b["files"] == [{"name": CAB, "unityVersion": "6000.3.12f1",
                           "externals": ["archive:/CAB-atlas/CAB-atlas", "archive:/CAB-scripts/CAB-scripts"]}]
    ab = b["assetBundles"][oid(1)]
    assert ab["m_Container"][0] == ["assets/x/tex.png", {"preloadIndex": 0, "preloadSize": 1,
                                                          "asset": {"$ref": {"file": CAB, "pathId": 10,
                                                                             "class": "Texture2D", "name": "tex"}}}]


def test_two_runs_are_identical(tmp_path):
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    ta, tb = task_for(a, "all"), task_for(b, "all")
    assert ta.key == tb.key
    da, db = run(a, ta), run(b, tb)
    assert contract.encode(da) == contract.encode(db)
    for x in da["artifacts"]:
        assert a.read(x["content"]["sha256"]) == b.read(x["content"]["sha256"])


def test_key_follows_the_census_classes_and_params(tmp_path):
    store = Store(tmp_path / "s")
    t = task_for(store, "atlas")
    assert "mesh.glb" not in t.atoms and "mesh.arrays" in t.atoms                  # sprites use mesh.arrays
    assert {"tex.png", "sprite.png", "generic.json", "bundle.json"} <= set(t.atoms)
    assert task_for(store, "atlas", params={"png": {"level": 3}}).key != t.key
    assert export_atoms(["Texture2D"]) == export_atoms(["Texture2D", "Texture2D"])
    assert set(export_atoms(["Mesh"])) == {"reader", "sniff", "mesh.arrays", "gltf.write", "mesh.glb",
                                           "generic.json"}
    with pytest.raises(ValueError, match="png level"):
        ExportStage().normalize({"png": {"level": 11}})
    with pytest.raises(ValueError, match="unknown parameters"):
        ExportStage().normalize({"mesh": "obj"})
    assert ExportStage().normalize(None) == {"blobMin": 4096, "classes": None, "png": {"level": 6}}


def test_png_level_changes_bytes_not_pixels(tmp_path):
    store = Store(tmp_path / "s")
    d6 = run(store, task_for(store, "all", classes=["Texture2D"]))
    d0 = run(store, task_for(store, "all", classes=["Texture2D"], params={"png": {"level": 0}}))
    p6, p0 = load(store, d6, f"{oid(10)}#image"), load(store, d0, f"{oid(10)}#image")
    assert p6 != p0
    io = __import__("io")
    assert Image.open(io.BytesIO(p6)).tobytes() == Image.open(io.BytesIO(p0)).tobytes()


def test_blob_threshold(tmp_path):
    store = Store(tmp_path / "s")
    doc = run(store, task_for(store, "all", classes=["MonoBehaviour"], params={"blobMin": 1 << 20}))
    m = obj(store, doc, f"{oid(70)}#json")
    assert m["_bytes"] == {"$hex": MOC.hex()}


def test_class_selection(tmp_path):
    store = Store(tmp_path / "s")
    doc = run(store, task_for(store, "all", classes=["Sprite"]))
    items = by_object(doc)
    assert set(items) == {oid(11), oid(12), oid(13)}                   # textures decoded, not exported
    assert all(i["status"] == "exported" for i in items.values())
    doc = run(store, task_for(store, "all", classes=["GameObject"]))
    assert set(by_object(doc)) == {oid(p) for p in (60, 61, 62, 63, 64, 65)}


def test_scripts_come_from_the_context_only(tmp_path):
    store = Store(tmp_path / "s")
    doc = run(store, task_for(store, "all", classes=["MonoBehaviour"], scripts={}))
    items = by_object(doc)
    assert items[oid(70)]["reason"]["code"] == "script.unresolved"
    assert items[oid(62)]["reason"]["code"] == "script.unresolved"             # no hierarchy: one by one


def test_a_converter_error_gives_the_typetree(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    real = objexport.RefExporter.conv_material

    def broken(self, info):
        raise KeyError("m_SavedProperties")

    monkeypatch.setattr(objexport.RefExporter, "conv_material", broken)
    doc = run(store, task_for(store, "all", classes=["Material"]))
    item = by_object(doc)[oid(50)]
    assert item["status"] == "generic" and item["reason"] == {"code": "generic.error",
                                                               "message": "KeyError: 'm_SavedProperties'"}
    assert obj(store, doc, f"{oid(50)}#json")["m_Name"] == "mat"
    monkeypatch.setattr(objexport.RefExporter, "conv_material", real)


def test_a_broken_hierarchy_gives_its_objects_one_by_one(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")

    def broken(self, o):
        raise RuntimeError("component failed")

    monkeypatch.setattr(objexport.RefExporter, "component", broken)
    doc = run(store, task_for(store, "all", classes=["GameObject"]))
    items = by_object(doc)
    assert {i["status"] for i in items.values()} == {"generic"}
    assert all(i["reason"]["code"] == "generic.error" for i in items.values())
    assert items[oid(63)]["reason"]["message"].startswith(f"hierarchy of {oid(60)}: RuntimeError")


def test_unreadable_objects_fail(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    real = UnityBundle.typetree

    def typetree(self, o):
        if o.path_id == 95:
            raise ValueError("Expected to read 40 bytes, but only read 12 bytes")
        return real(self, o)

    monkeypatch.setattr(UnityBundle, "typetree", typetree)
    doc = run(store, task_for(store, "all", classes=["Animator"]))
    (item,) = doc["items"]
    assert item["status"] == "failed" and item["reason"]["code"] == "failed.read" and doc["status"] == "partial"


def test_task_with_other_atoms_is_refused(tmp_path):
    store = Store(tmp_path / "s")
    t = task_for(store, "all")
    bad = Task(t.stage, t.version, t.subject, t.params, {**t.atoms, "tex.png": "tex.png/0"}, t.inputs, t.context)
    with pytest.raises(IncompatibleTask, match="tex.png"):
        execute(bad, store, registry(ExportStage(FAKE_READER)))
    missing = Task(t.stage, t.version, t.subject, t.params, {k: v for k, v in t.atoms.items() if k != "mesh.glb"},
                   t.inputs, t.context)
    with pytest.raises(IncompatibleTask, match="mesh.glb"):
        execute(missing, store, registry(ExportStage(FAKE_READER)))


# ---------------------------------------------------------------- describing tasks
def census_doc(view: UnityBundle) -> dict:
    """The part of a census the export stage reads (classes, objects, m_Script references)."""
    files, classes = [], {}
    for f in view.files():
        objs = []
        for o in view.objects():
            if o.file != f:
                continue
            classes[o.class_name] = classes.get(o.class_name, 0) + 1
            rec = {"pathId": o.path_id, "classId": o.class_id, "class": o.class_name}
            tt = view.typetree(o)
            ext = view.externals(f)

            def ref(s):
                file = f if not s["m_FileID"] else ext[s["m_FileID"] - 1].rsplit("/", 1)[-1]
                return {"file": file, "pathId": s["m_PathID"]}
            s = tt.get("m_Script")
            if isinstance(s, dict) and s.get("m_PathID"):
                rec["script"] = ref(s)
            bindings = [b["script"] for b in (tt.get("m_ClipBindingConstant") or {}).get("genericBindings", ())
                        if b["script"].get("m_PathID")]
            if bindings:
                rec["bindingScripts"] = [ref(b) for b in bindings]
            objs.append(rec)
        files.append({"name": f, "objects": objs})
    return {"schema": contract.CENSUS, "files": files, "resources": [], "assetBundles": [], "scripts": [],
            "classes": dict(sorted(classes.items())), "objects": sum(classes.values()), "errors": []}


def committed(store, env, task, role, doc, kind):
    rec = contract.artifact(contract.artifact_id(task.id, role), store.add(contract.encode(doc), "json"),
                            contract.provenance(task), {"kind": kind, "format": "json", "facts": {}})
    store.commit(contract.result(task, [rec], []))
    env.done(task.id, task.key)


def planning_env(store, names=("all", "atlas"), selected=None):
    names = tuple(sorted(set(names) | set(selected or ())))
    bundles = {}
    for n in names:
        data = f"fake:{n}".encode()
        bundles[n] = Input("bundle", store.put(data), len(data), f"{n}.bundle", ({"kind": "store"},))
    env = Env(store, {"bundles": bundles, "selected": sorted(selected if selected is not None else names)})
    for n in names:
        t = Task(census_mod.CensusStage.name, 1, n, {}, {}, (bundles[n],))
        doc = census_doc(open_fake(f"fake:{n}".encode()))
        rec = contract.artifact(contract.artifact_id(t.id, "census"), store.add(contract.encode(doc), "json"),
                                contract.provenance(t), {"kind": "unity.census", "format": "json",
                                                         "facts": census_mod.facts(doc)})
        store.commit(contract.result(t, [rec], []))
        env.done(t.id, t.key)
    scripts = Task("link.scripts", 1, "all")
    committed(store, env, scripts, "scripts", {"schema": "nnnotes.scripts/1", "scripts": SCRIPTS}, "link.scripts")
    return env


def test_describe_reads_the_census_and_the_script_table(tmp_path):
    store = Store(tmp_path / "s")
    env = planning_env(store)
    stage = ExportStage(FAKE_READER)
    assert stage.subjects(env) == ["all", "atlas"]
    # every closure bundle holding a SpriteAtlas joins a selection that holds sprites (a sprite reaches its
    # texture through its atlas' bundle); a selection without sprites stays as it is
    assert stage.subjects(planning_env(Store(tmp_path / "t"), selected=["all"])) == ["all", "atlas"]
    assert stage.subjects(planning_env(Store(tmp_path / "u"), selected=["plain"])) == ["plain"]
    t = describe(stage, "all", None, env)
    assert t.atoms == export_atoms(classes_of("all"), FAKE_READER.id)
    assert t.context == {"scripts": SCRIPTS} and t.key == task_for(store, "all").key
    assert stage.depends("all", env) == ["unity.census:all", "link.scripts:all"]
    a = describe(stage, "atlas", None, env)
    assert a.context == {} and "mesh.glb" not in a.atoms
    assert t.cost.peak_bytes >= 72 << 20
    fresh = Env(store, env.facts)
    from nnnotes.stages import Pending
    fresh.waiting("unity.census:all")
    with pytest.raises(Pending):
        describe(stage, "all", None, fresh)


def test_class_selection_keeps_only_its_converters_in_the_key(tmp_path):
    store = Store(tmp_path / "s")
    env = planning_env(store)
    stage = ExportStage(FAKE_READER)
    full = describe(stage, "all", None, env)
    assert full.key == describe(stage, "all", {"classes": None}, env).key == task_for(store, "all").key
    meshes = describe(stage, "all", {"classes": ["Mesh"]}, env)
    assert meshes.atoms == export_atoms(["Mesh"], FAKE_READER.id) and meshes.params["classes"] == ["Mesh"]
    assert "tex.png" not in meshes.atoms and meshes.key != full.key
    assert describe(stage, "all", {"classes": ["Mesh", "Nothing"]}, env).atoms == meshes.atoms
    assert describe(stage, "all", {"classes": ["Nothing"]}, env).atoms == export_atoms([], FAKE_READER.id)
    assert env.params == {}                                                     # set while describing only
    for classes in (["Mesh"], ["Sprite"], ["GameObject"], ["Material", "AnimationClip"]):
        t = describe(stage, "all", {"classes": classes}, env)
        execute(t, store, registry(stage))
        doc = store.result(t.key)
        assert doc["items"] and all(i["class"] in {*classes, "Transform", "MonoBehaviour"} for i in doc["items"])


def crop_setup(store):
    env = planning_env(store)
    stage = ExportStage(FAKE_READER)
    for n in ("all", "atlas"):
        t = describe(stage, n, None, env)
        execute(t, store, registry(stage))
        env.done(t.id, t.key)
    return env


def test_sprite_crop_equals_the_same_sprite_cropped_in_its_atlas_bundle(tmp_path):
    store = Store(tmp_path / "s")
    env = crop_setup(store)
    stage = SpriteCropStage()
    assert stage.subjects(env) == ["atlas:99"]
    t = describe(stage, "atlas:99", None, env)
    assert [i.role for i in t.inputs] == ["atlas", f"sprite:{oid(13)}", f"texture:{ATLAS_CAB}:98"]
    assert stage.depends("atlas:99", env) == ["unity.export:all", "unity.export:atlas"]
    assert set(t.atoms) == {"png.encode", "sprite.crop"}
    execute(t, store, registry(stage))
    doc = store.result(t.key)
    (item,) = doc["items"]
    assert item == {"object": oid(13), "status": "exported", "artifacts": [f"{oid(13)}#image"], "class": "Sprite"}
    a = doc["artifacts"][0]
    assert a["provenance"]["object"]["name"] == "remote" and a["provenance"]["object"]["file"] == CAB
    local = store.result(env.key("unity.export:atlas"))
    same = art(local, f"{ATLAS_CAB}:97#image")
    assert a["content"] == same["content"]                                  # the same bytes as a local crop
    assert Image.open(__import__("io").BytesIO(store.read(a["content"]["sha256"]))).size == (8, 8)


def test_sprite_crop_waits_for_exports(tmp_path):
    store = Store(tmp_path / "s")
    env = planning_env(store)
    env.waiting("unity.export:all")
    from nnnotes.stages import Pending
    with pytest.raises(Pending):
        SpriteCropStage().subjects(env)


def test_modules_import_without_unitypy():
    code = ("import sys; import nnnotes.objexport as o; o.ExportStage().ATOMS; o.SpriteCropStage().ATOMS; "
            "print(sorted(m for m in ('UnityPy', 'PIL', 'nnnotes.export') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "[]"


# ---------------------------------------------------------------- the documentation
def test_every_reason_code_and_converter_is_documented():
    doc = (ROOT / "docs" / "stages.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+(?:\.[A-Za-z_]+)+)` \|", doc, re.M))
    sources = [ROOT / "src" / "nnnotes" / "objexport.py", *sorted((ROOT / "src" / "nnnotes" / "atoms").glob("*.py"))]
    raised = set(objexport.REASONS)
    for p in sources:
        text = p.read_text(encoding="utf-8")
        raised |= set(re.findall(r'Unsupported\(\s*"([^"]+)"', text))
        raised |= set(re.findall(r'_fallback\([^,]+,\s*"([^"]+)"', text))
        raised |= set(re.findall(r'\(\s*"((?:partial|unsupported|empty|no_data|generic|script|failed)\.[^"]+)",',
                                 text))
    assert raised - documented - set(CONVERTERS) == set()
    for name, version in CONVERTERS.items():
        assert f"`{name}/{version}`" in doc, name
    for stage in (ExportStage, SpriteCropStage):
        assert f"`{stage.name}`" in doc


# ---------------------------------------------------------------- golden records
CONVERTER_CLASSES = {"tex.png": ["Texture2D"], "sprite.png": ["Sprite"], "mesh.glb": ["Mesh"],
                     "prefab.json": ["GameObject"], "material.json": ["Material"], "clip.json": ["AnimationClip"],
                     "controller.json": ["AnimatorController", "AnimatorOverrideController"],
                     "mono.json": ["MonoBehaviour"], "data": ["TextAsset"], "font": ["Font"], "shader.json": ["Shader"],
                     "scripts.json": ["MonoScript"], "bundle.json": ["AssetBundle"],
                     "generic.json": ["Animator", "AudioClip", "SpriteAtlas"]}
LIBRARIES = ["Pillow", "UnityPy", "astc-encoder-py", "numpy", "texture2ddecoder"]


def export_golden():
    """Provider of tests/golden/unity.export.json: one fixture per converter (the synthetic bundle with the
    classes the converter takes), and the whole bundle."""
    def fixture(classes):
        return lambda store: task_for(store, "all", classes=classes)
    fixtures = {name: fixture(classes) for name, classes in CONVERTER_CLASSES.items()}
    fixtures["all"] = fixture(None)
    return {"stage": ExportStage(FAKE_READER), "fixtures": fixtures, "libraries": LIBRARIES}


def crop_golden():
    """Provider of tests/golden/sprite.crop.json."""
    def fixture(store):
        env = crop_setup(store)
        return describe(SpriteCropStage(), "atlas:99", None, env)
    return {"stage": SpriteCropStage(), "fixtures": {"atlas": fixture}, "libraries": LIBRARIES}


def test_golden_providers_cover_every_converter():
    p = export_golden()
    assert set(p["fixtures"]) == set(CONVERTERS) | {"all"}
    assert json.dumps(CONVERTER_CLASSES)                                        # every converter named once


def test_a_converter_change_without_a_version_bump_fails_its_golden(tmp_path, monkeypatch):
    from nnnotes.stages import golden, golden_problems, library_versions
    expected = contract.loads((ROOT / "tests" / "golden" / "unity.export.json").read_bytes())
    if library_versions(expected["libraries"]) != expected["libraries"]:
        pytest.skip("golden record of other library versions")
    real = objexport.RefExporter.conv_font

    def changed(self, info):
        ids = real(self, info)
        self.staged[-1]["semantics"]["facts"]["extra"] = 1
        return ids

    monkeypatch.setattr(objexport.RefExporter, "conv_font", changed)
    p = export_golden()
    actual = golden(p["stage"], p["fixtures"], Store(tmp_path / "s"), p["libraries"])
    problems = golden_problems(expected, actual, p["stage"])
    assert "unity.export: fixture font: output changed without a version bump" in problems
    assert "unity.export: fixture all: output changed without a version bump" in problems
    assert not any("fixture tex.png" in x for x in problems)
