"""CubismFadeMotionData (Unity) -> motion3.json.

Cubism-for-Unity keeps each motion's per-parameter curves, keyed by Cubism id,
in a CubismFadeMotionData MonoBehaviour. Unity AnimationCurve segments are cubic
Hermite; a Hermite segment is exactly a cubic Bezier with control points at 1/3
of the segment (or at the key weights when weighted tangents are used), so the
conversion is lossless. Stepped keys (infinite tangents) map to Cubism stepped
segments.

Looping is not a property of the data here: every imported AnimationClip is
flagged loopTime, and the game's motion controller decides looping per call.
Meta.Loop is therefore written false and left to the player.
"""
from __future__ import annotations

import math
from pathlib import Path

from .jsonio import write_json

SEG_LINEAR, SEG_BEZIER, SEG_STEPPED = 0, 1, 2
WEIGHT_IN, WEIGHT_OUT = 1, 2          # UnityEngine.WeightedMode flags


def _segments(kf: list[dict]):
    t0, v0 = float(kf[0]["time"]), float(kf[0]["value"])
    segs: list[float] = [t0, v0]
    nseg = 0
    npts = 1
    restricted = True
    for k0, k1 in zip(kf, kf[1:]):
        a_t, a_v = float(k0["time"]), float(k0["value"])
        b_t, b_v = float(k1["time"]), float(k1["value"])
        out0, in1 = float(k0["outSlope"]), float(k1["inSlope"])
        dt = b_t - a_t
        nseg += 1
        if math.isinf(out0) or math.isinf(in1):
            segs += [SEG_STEPPED, b_t, b_v]
            npts += 1
            continue
        w_out = float(k0["outWeight"]) if int(k0["weightedMode"]) & WEIGHT_OUT else 1.0 / 3.0
        w_in = float(k1["inWeight"]) if int(k1["weightedMode"]) & WEIGHT_IN else 1.0 / 3.0
        if w_out != 1.0 / 3.0 or w_in != 1.0 / 3.0:
            restricted = False
        segs += [SEG_BEZIER,
                 a_t + w_out * dt, a_v + out0 * w_out * dt,
                 b_t - w_in * dt, b_v - in1 * w_in * dt,
                 b_t, b_v]
        npts += 3
    return segs, nseg, npts, restricted


def _clip_rates(env) -> dict[str, float]:
    return {o.read_typetree()["m_Name"]: float(o.read_typetree()["m_SampleRate"])
            for o in env.objects if o.type.name == "AnimationClip"}


def convert_fade(fade: dict, fps: float | None) -> dict:
    curves = []
    total_seg = total_pts = 0
    restricted = True
    ids, crv = fade["ParameterIds"], fade["ParameterCurves"]
    fin, fout = fade["ParameterFadeInTimes"], fade["ParameterFadeOutTimes"]
    if not len(ids) == len(crv) == len(fin) == len(fout):
        raise ValueError(f"{fade['MotionName']}: parameter arrays differ in length")
    for i, (pid, curve) in enumerate(zip(ids, crv)):
        kf = curve["m_Curve"]
        if not kf:
            # an empty AnimationCurve evaluates to 0 in Unity; motion3 cannot say that
            raise NotImplementedError(f"{fade['MotionName']}: empty curve for {pid}")
        segs, nseg, npts, r = _segments(kf)
        restricted = restricted and r
        c = {"Target": "Parameter", "Id": pid, "Segments": segs}
        if fin[i] >= 0:
            c["FadeInTime"] = fin[i]
        if fout[i] >= 0:
            c["FadeOutTime"] = fout[i]
        curves.append(c)
        total_seg += nseg
        total_pts += npts
    meta = {
        "Duration": float(fade["MotionLength"]),
        "Loop": False,
        "AreBeziersRestricted": restricted,
        "CurveCount": len(curves),
        "TotalSegmentCount": total_seg,
        "TotalPointCount": total_pts,
        "UserDataCount": 0,
        "TotalUserDataSize": 0,
    }
    # Fps is informational for Cubism runtimes; written only when the
    # motion's AnimationClip is present to state it.
    if fps:
        meta["Fps"] = fps
    if fade["FadeInTime"] >= 0:
        meta["FadeInTime"] = fade["FadeInTime"]
    if fade["FadeOutTime"] >= 0:
        meta["FadeOutTime"] = fade["FadeOutTime"]
    return {"Version": 3, "Meta": meta, "Curves": curves}


def motion_name(fade: dict) -> str:
    return Path(str(fade["MotionName"])).name.removesuffix(".motion3.json")


def extract_motions(env, out_dir: Path) -> dict[str, str]:
    """Every CubismFadeMotionData in env -> <out_dir>/<motion>.motion3.json.

    Returns {motionName: fileName}. Also writes `_fades.json` with the
    model-level fade overrides (ModelFadeIn/OutTime, -1 = unset) the model3
    motion entries need.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rates = _clip_rates(env)
    manifest: dict[str, str] = {}
    model_fades: dict[str, dict] = {}
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        if o.read().m_Script.read().m_ClassName != "CubismFadeMotionData":
            continue
        tt = o.read_typetree()
        name = motion_name(tt)
        fn = f"{name}.motion3.json"
        write_json(out_dir / fn, convert_fade(tt, rates.get(name)), indent=None)
        manifest[name] = fn
        model_fades[name] = {"FadeInTime": tt["ModelFadeInTime"],
                             "FadeOutTime": tt["ModelFadeOutTime"]}
    write_json(out_dir / "_fades.json", model_fades, indent=None, ensure_ascii=True)
    return manifest
