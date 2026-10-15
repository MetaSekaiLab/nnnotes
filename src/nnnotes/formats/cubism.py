"""Cubism-for-Unity data -> the Cubism SDK interchange files: the canvas of a moc3, physics3.json, exp3.json.

Cubism-for-Unity keeps a model's physics rig (CubismPhysicsController._rig) and its expressions
(CubismExpressionData) in its own serialized layout; these functions write them in the layout of the Cubism SDK's
JSON files (motion3.json: formats.motion).
"""

from __future__ import annotations

import struct

__all__ = ["EXP_BLEND", "PHYS_TYPE", "expression_json", "moc3_canvas", "physics_json"]

# Live2D.Cubism.Core.CubismParameterBlendMode -> exp3.json "Blend"
EXP_BLEND = {0: "Overwrite", 1: "Add", 2: "Multiply"}
# Live2D.Cubism.Framework.Physics.CubismPhysicsSourceComponent -> physics3 "Type"
PHYS_TYPE = {0: "X", 1: "Y", 2: "Angle"}


def moc3_canvas(moc: bytes) -> dict:
    """CanvasInfo from a moc3 (section table at 0x40; entry 1 = canvas info)."""
    if moc[:4] != b"MOC3":
        raise ValueError("not a moc3")
    canvas_off = struct.unpack_from("<I", moc, 0x40 + 4)[0]
    ppu, ox, oy, w, h = struct.unpack_from("<5f", moc, canvas_off)
    return {"pixelsPerUnit": ppu, "originX": ox, "originY": oy, "width": w, "height": h, "mocVersion": moc[4]}


def _vec2(v):
    return {"X": v["x"], "Y": v["y"]}


def physics_json(rig: dict) -> dict:
    """physics3.json of a CubismPhysicsController's rig (the inverse of Cubism-for-Unity's import, ToRig)."""
    settings, dictionary = [], []
    n_in = n_out = n_vtx = 0
    for i, sr in enumerate(rig["SubRigs"]):
        sid = f"PhysicsSetting{i + 1}"
        dictionary.append({"Id": sid, "Name": sr["Name"]})
        inputs = [
            {
                "Source": {"Target": "Parameter", "Id": x["SourceId"]},
                "Weight": x["Weight"],
                "Type": PHYS_TYPE[x["SourceComponent"]],
                "Reflect": bool(x["IsInverted"]),
            }
            for x in sr["Input"]
        ]
        outputs = [
            {
                "Destination": {"Target": "Parameter", "Id": x["DestinationId"]},
                "VertexIndex": x["ParticleIndex"],
                "Scale": x["AngleScale"],
                "Weight": x["Weight"],
                "Type": PHYS_TYPE[x["SourceComponent"]],
                "Reflect": bool(x["IsInverted"]),
            }
            for x in sr["Output"]
        ]
        verts = [
            {
                "Position": _vec2(p["InitialPosition"]),
                "Mobility": p["Mobility"],
                "Delay": p["Delay"],
                "Acceleration": p["Acceleration"],
                "Radius": p["Radius"],
            }
            for p in sr["Particles"]
        ]
        nz = sr["Normalization"]
        settings.append(
            {
                "Id": sid,
                "Input": inputs,
                "Output": outputs,
                "Vertices": verts,
                "Normalization": {
                    "Position": {k: nz["Position"][k] for k in ("Minimum", "Default", "Maximum")},
                    "Angle": {k: nz["Angle"][k] for k in ("Minimum", "Default", "Maximum")},
                },
            }
        )
        n_in += len(inputs)
        n_out += len(outputs)
        n_vtx += len(verts)
    meta = {
        "PhysicsSettingCount": len(settings),
        "TotalInputCount": n_in,
        "TotalOutputCount": n_out,
        "VertexCount": n_vtx,
        "EffectiveForces": {"Gravity": _vec2(rig["Gravity"]), "Wind": _vec2(rig["Wind"])},
        "PhysicsDictionary": dictionary,
    }
    if rig["Fps"] > 0:  # 0 = unset in physics3 terms
        meta["Fps"] = rig["Fps"]
    return {"Version": 3, "Meta": meta, "PhysicsSettings": settings}


def expression_json(tt: dict) -> dict:
    """exp3.json of a CubismExpressionData's fields."""
    return {
        "Type": tt["Type"],
        "FadeInTime": tt["FadeInTime"],
        "FadeOutTime": tt["FadeOutTime"],
        "Parameters": [{"Id": p["Id"], "Value": p["Value"], "Blend": EXP_BLEND[p["Blend"]]} for p in tt["Parameters"]],
    }
