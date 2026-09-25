"""Live2D (Cubism) model: Cubism-for-Unity prefab -> prefab.json + Cubism runtime files.

Components are identified by their MonoScript class (resolved through the APK's
shared_monoscripts bundle, so the Catalog must be opened with `apk=`).

`extract_runtime` writes what a re-implementation of the game's own runtime needs:

  <name>.moc3                 CubismMoc._bytes
  textures/*.png              atlas pages (Texture2D)
  <name>.prefab.json          the whole model prefab exported by export.Exporter:
                              every GameObject with every component (CubismRenderer/
                              MeshRenderer + materials per drawable, controllers,
                              Live2DCharacter, eye blink, harmonic motion, motion
                              sync, anchors ...), with the fade motion list, the
                              expression list and every AnimationClip (Mecanim clip
                              data + resolved bindings) inlined; plus `canvas` (moc3)

`extract_model` adds the Cubism SDK interchange files for other tools:

  <exp>.exp3.json             CubismExpressionData
  <name>.physics3.json        CubismPhysicsController._rig (inverse of ToRig), when the
                              model has physics
  motions/<m>.motion3.json    CubismFadeMotionData (see motion.py)
  <name>.model3.json          file refs + EyeBlink/LipSync groups from the
                              model's own CubismEyeBlinkParameter/CubismMouthParameter

model3.json lists the atlas pages sorted by name (the Cubism Editor's
texture_NN naming). The texture each drawable is actually drawn with is its
CubismRenderer._mainTexture in prefab.json, keyed by CubismDrawable._unmanagedIndex
(the Cubism Core drawable index).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from .catalog import Catalog
from .config import apk_missing
from .export import Exporter
from .jsonio import write_json
from . import motion as motion_mod
from .unity import script_class

# Live2D.Cubism.Core.CubismParameterBlendMode -> exp3.json "Blend"
EXP_BLEND = {0: "Overwrite", 1: "Add", 2: "Multiply"}
# Live2D.Cubism.Framework.Physics.CubismPhysicsSourceComponent -> physics3 "Type"
PHYS_TYPE = {0: "X", 1: "Y", 2: "Angle"}
# the moc is written as <name>.moc3; prefab.json names it only
STUB_ASSETS = ("CubismMoc",)


def moc3_canvas(moc: bytes) -> dict:
    """CanvasInfo from a moc3 (section table at 0x40; entry 1 = canvas info)."""
    if moc[:4] != b"MOC3":
        raise ValueError("not a moc3")
    canvas_off = struct.unpack_from("<I", moc, 0x40 + 4)[0]
    ppu, ox, oy, w, h = struct.unpack_from("<5f", moc, canvas_off)
    return {"pixelsPerUnit": ppu, "originX": ox, "originY": oy,
            "width": w, "height": h, "mocVersion": moc[4]}


def _vec2(v):
    return {"X": v["x"], "Y": v["y"]}


def physics_json(rig: dict, names: list[str]) -> dict:
    settings, dictionary = [], []
    n_in = n_out = n_vtx = 0
    for i, sr in enumerate(rig["SubRigs"]):
        sid = f"PhysicsSetting{i + 1}"
        dictionary.append({"Id": sid, "Name": sr["Name"]})
        inputs = [{
            "Source": {"Target": "Parameter", "Id": x["SourceId"]},
            "Weight": x["Weight"], "Type": PHYS_TYPE[x["SourceComponent"]],
            "Reflect": bool(x["IsInverted"]),
        } for x in sr["Input"]]
        outputs = [{
            "Destination": {"Target": "Parameter", "Id": x["DestinationId"]},
            "VertexIndex": x["ParticleIndex"], "Scale": x["AngleScale"],
            "Weight": x["Weight"], "Type": PHYS_TYPE[x["SourceComponent"]],
            "Reflect": bool(x["IsInverted"]),
        } for x in sr["Output"]]
        verts = [{
            "Position": _vec2(p["InitialPosition"]), "Mobility": p["Mobility"],
            "Delay": p["Delay"], "Acceleration": p["Acceleration"], "Radius": p["Radius"],
        } for p in sr["Particles"]]
        nz = sr["Normalization"]
        settings.append({
            "Id": sid, "Input": inputs, "Output": outputs, "Vertices": verts,
            "Normalization": {
                "Position": {k: nz["Position"][k] for k in ("Minimum", "Default", "Maximum")},
                "Angle": {k: nz["Angle"][k] for k in ("Minimum", "Default", "Maximum")},
            },
        })
        n_in += len(inputs); n_out += len(outputs); n_vtx += len(verts)
    meta = {
        "PhysicsSettingCount": len(settings), "TotalInputCount": n_in,
        "TotalOutputCount": n_out, "VertexCount": n_vtx,
        "EffectiveForces": {"Gravity": _vec2(rig["Gravity"]), "Wind": _vec2(rig["Wind"])},
        "PhysicsDictionary": dictionary,
    }
    if rig["Fps"] > 0:                       # 0 = unset in physics3 terms
        meta["Fps"] = rig["Fps"]
    return {"Version": 3, "Meta": meta, "PhysicsSettings": settings}


def expression_json(tt: dict) -> dict:
    return {
        "Type": tt["Type"],
        "FadeInTime": tt["FadeInTime"],
        "FadeOutTime": tt["FadeOutTime"],
        "Parameters": [{"Id": p["Id"], "Value": p["Value"], "Blend": EXP_BLEND[p["Blend"]]}
                       for p in tt["Parameters"]],
    }


def _classes(env) -> dict[str, list]:
    by_cls: dict[str, list] = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            by_cls.setdefault(script_class(o), []).append(o)
    return by_cls


def _only(by_cls, cls: str, name: str) -> dict:
    objs = by_cls.get(cls, [])
    if len(objs) != 1:
        raise RuntimeError(f"{name}: {len(objs)} {cls}")
    return objs[0].read_typetree()


def _extract_runtime(cat: Catalog, key: str, out_dir: Path):
    if cat.apk is None:
        raise apk_missing("resolving the Cubism component classes")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = key.rsplit("/", 1)[-1]
    ex = Exporter(cat, out_dir, inline_meshes=False, stub_assets=STUB_ASSETS)
    env, graph = ex.load(key)
    by_cls = _classes(env)
    moc = bytes(_only(by_cls, "CubismMoc", name)["_bytes"])
    (out_dir / f"{name}.moc3").write_bytes(moc)
    prefab = ex.prefab(key, loaded=(env, graph))
    prefab["canvas"] = moc3_canvas(moc)
    write_json(out_dir / f"{name}.prefab.json", prefab, indent=None)
    pages = sorted((o for o in env.objects if o.type.name == "Texture2D"), key=lambda o: o.read().m_Name)
    tex_files = [ex.texture(o)["texture"] for o in pages]
    res = {"name": name, "moc3": f"{name}.moc3", "prefab": f"{name}.prefab.json",
           "moc3Bytes": len(moc), "textures": tex_files, "nodes": len(prefab["nodes"])}
    return res, ex, env, graph, by_cls


def extract_runtime(cat: Catalog, key: str, out_dir: Path) -> dict:
    return _extract_runtime(cat, key, out_dir)[0]


def extract_model(cat: Catalog, key: str, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    res, ex, env, graph, by_cls = _extract_runtime(cat, key, out_dir)
    name = res["name"]
    (out_dir / "motions").mkdir(parents=True, exist_ok=True)

    def owner_names(cls):
        return sorted(graph.go[o.read_typetree()["m_GameObject"]["m_PathID"]]["m_Name"]
                      for o in by_cls.get(cls, []))

    # expressions
    exp_refs = []
    for o in by_cls.get("CubismExpressionData", []):
        tt = o.read_typetree()
        ename = tt["m_Name"].removesuffix(".exp3")
        fn = f"{ename}.exp3.json"
        write_json(out_dir / fn, expression_json(tt), indent=None)
        exp_refs.append({"Name": ename, "File": fn})
    exp_refs.sort(key=lambda e: e["Name"])

    # physics (none without a CubismPhysicsController)
    rig = (_only(by_cls, "CubismPhysicsController", name)["_rig"] if by_cls.get("CubismPhysicsController")
           else {"SubRigs": []})
    phys_file = None
    if rig["SubRigs"]:
        phys_file = f"{name}.physics3.json"
        write_json(out_dir / phys_file, physics_json(rig, []), indent=None)

    # motions
    motions = motion_mod.extract_motions(env, out_dir / "motions")
    model_fades = json.loads((out_dir / "motions" / "_fades.json").read_text(encoding="utf-8"))

    # model3.json
    fr = {"Moc": res["moc3"], "Textures": res["textures"]}
    if phys_file:
        fr["Physics"] = phys_file
    if exp_refs:
        fr["Expressions"] = exp_refs

    def motion_entry(m, f):
        e = {"File": f"motions/{f}"}
        for k in ("FadeInTime", "FadeOutTime"):
            if model_fades[m][k] >= 0:
                e[k] = model_fades[m][k]
        return [e]
    fr["Motions"] = {m: motion_entry(m, f) for m, f in sorted(motions.items())}
    groups = []
    eye_ids = owner_names("CubismEyeBlinkParameter")
    mouth_ids = owner_names("CubismMouthParameter")
    if eye_ids:
        groups.append({"Target": "Parameter", "Name": "EyeBlink", "Ids": eye_ids})
    if mouth_ids:
        groups.append({"Target": "Parameter", "Name": "LipSync", "Ids": mouth_ids})
    model3 = {"Version": 3, "FileReferences": fr, "Groups": groups}
    write_json(out_dir / f"{name}.model3.json", model3)

    return {**res, "model3": f"{name}.model3.json", "expressions": len(exp_refs),
            "motions": len(motions), "physicsSettings": len(rig["SubRigs"]),
            "eyeBlink": eye_ids, "lipSync": mouth_ids}
