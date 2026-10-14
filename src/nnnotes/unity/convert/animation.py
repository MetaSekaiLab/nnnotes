"""AnimationClip (clip.json) and AnimatorController (controller.json).

A clip's document holds the Mecanim data the Animator samples and its bindings as stored. A binding names what it
animates by crc32 values (`pathCrc`: the path of the animated object relative to the GameObject whose Animator plays
the clip; `attributeCrc`: the animated property), which only the hierarchy that plays the clip resolves; the document
resolves what needs no hierarchy: the class of the bound component, the script of a MonoBehaviour binding, and the
attributes of a Transform binding.

Curve indices run over the streamed curves, then the dense ones, then the constant ones; each float binding (not
`pptr`) takes `curves` of them in `bindings` order (1 when the binding has no `curves`). A pptr binding indexes
`pptrCurveMapping` with its streamed values.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from ..atoms import Unsupported
from .objects import BundleObjects, encode_object

__all__ = [
    "CLASS_ID",
    "FLT_MAX",
    "TRANSFORM_ATTR",
    "clip",
    "clip_doc",
    "controller",
    "controller_doc",
    "streamed_frames",
    "transition",
]

FLT_MAX = 3.4028234663852886e38
# Unity class IDs (GenericBinding.typeID) -> class name
CLASS_ID = {
    1: "GameObject",
    4: "Transform",
    23: "MeshRenderer",
    95: "Animator",
    198: "ParticleSystem",
    199: "ParticleSystemRenderer",
    212: "SpriteRenderer",
    224: "RectTransform",
    225: "CanvasGroup",
    114: "MonoBehaviour",
    137: "SkinnedMeshRenderer",
    120: "LineRenderer",
    96: "TrailRenderer",
    331: "SpriteMask",
    222: "CanvasRenderer",
}
# Transform binding attribute -> (property, curve count) (GenericBinding, typeID 4)
TRANSFORM_ATTR = {
    1: ("m_LocalPosition", 3),
    2: ("m_LocalRotation", 4),
    3: ("m_LocalScale", 3),
    4: ("localEulerAnglesRaw", 3),
}


def streamed_frames(words: list[int]) -> list[list]:
    """Mecanim StreamedClip data (uint32 words) -> [[time, [[curve, c0, c1, c2, c3], ...]], ...].

    A frame holds the cubic segments that start at its time; a curve's value at t
    comes from its last key at or before t: ((c0*x + c1)*x + c2)*x + c3, x = t - time.
    The stream usually opens with a frame at -FLT_MAX (every curve, constant); clips
    whose curves all start at t >= 0 open later. It closes with an empty frame at +inf,
    which is dropped here (JSON has no infinity). An empty stream (no streamed
    curves) gives no frames.
    """
    if not words:
        return []
    buf = struct.pack(f"<{len(words)}I", *words)
    frames, i = [], 0
    while i < len(buf):
        t, n = struct.unpack_from("<fi", buf, i)
        i += 8
        keys = [list(struct.unpack_from("<i4f", buf, i + 20 * k)) for k in range(n)]
        i += 20 * n
        frames.append([t, keys])
    if i != len(buf):
        raise ValueError("streamed clip data does not end on a frame boundary")
    last = frames.pop()
    if last[0] != float("inf") or last[1]:
        raise ValueError(f"streamed clip does not close with an empty +inf frame: {last[0]}")
    return frames


# ---------------------------------------------------------------- clips
def _binding(b: dict, ref: Callable[[dict], object], script: Callable[[dict], str | None]) -> dict:
    tid = b["typeID"]
    d = {"pathCrc": b["path"], "attributeCrc": b["attribute"], "typeID": tid}
    if tid in CLASS_ID:
        d["class"] = CLASS_ID[tid]
    d.update(
        customType=b["customType"],
        pptr=bool(b["isPPtrCurve"]),
        int=bool(b["isIntCurve"]),
        serializeReference=bool(b["isSerializeReferenceCurve"]),
    )
    if b["script"].get("m_PathID"):
        d["script"] = ref(b["script"])
        d["scriptClass"] = script(b["script"])
    if tid == 4 and b["attribute"] in TRANSFORM_ATTR and not d["pptr"]:
        d["attribute"], d["curves"] = TRANSFORM_ATTR[b["attribute"]]
    return d


def clip_doc(tt: dict, ref: Callable[[dict], object], value, script: Callable[[dict], str | None]) -> dict:
    """An AnimationClip's document: name, sample rate, wrap mode, the muscle clip's time range, loop and cycle
    offset, its events, its bindings, its streamed (cubic keys by frame), dense and constant curves and its
    pptr curve mapping. `ref(pptr)`: the value of a reference; `value(v)`: the JSON of a typetree value (an event);
    `script(pptr)`: the script table entry of a MonoScript reference (None when unknown). Unsupported for a legacy
    or compressed clip."""
    if tt["m_Legacy"] or tt["m_Compressed"]:
        raise Unsupported("generic.clip.legacy", f"clip {tt['m_Name']}: legacy/compressed")
    mc = tt["m_MuscleClip"]
    data = mc["m_Clip"]["data"]
    st, de = data["m_StreamedClip"], data["m_DenseClip"]
    cbc = tt["m_ClipBindingConstant"]
    streamed = {"curveCount": st["curveCount"]}
    if "discreteCurveCount" in st:
        streamed["discreteCurveCount"] = st["discreteCurveCount"]
    streamed["frames"] = streamed_frames(st["data"])
    return {
        "name": tt["m_Name"],
        "sampleRate": tt["m_SampleRate"],
        "wrapMode": tt["m_WrapMode"],
        "startTime": mc["m_StartTime"],
        "stopTime": mc["m_StopTime"],
        "loopTime": bool(mc["m_LoopTime"]),
        "cycleOffset": mc["m_CycleOffset"],
        "events": [value(e) for e in tt["m_Events"]],
        "bindings": [_binding(b, ref, script) for b in cbc["genericBindings"]],
        "streamed": streamed,
        "dense": {
            "curveCount": de["m_CurveCount"],
            "frameCount": de["m_FrameCount"],
            "sampleRate": de["m_SampleRate"],
            "beginTime": de["m_BeginTime"],
            "samples": list(de["m_SampleArray"]),
        },
        "constant": list(data["m_ConstantClip"]["data"]),
        "pptrCurveMapping": [ref(p) for p in cbc.get("pptrCurveMapping", [])],
    }


def clip(ctx: BundleObjects, info) -> None:
    """`json`: clip_doc with references as {"$ref"}."""
    key = (info.file, info.path_id)
    r = ctx.readers[key]
    doc = clip_doc(
        ctx.tt(key),
        lambda p: ctx.ref(r, p),
        lambda v: ctx.value(r, v),
        lambda p: ctx.script_entry(key[0], p),
    )
    ctx.emit(key, "json", encode_object(doc), "json", "animation.clip", "json", {})


# ---------------------------------------------------------------- controllers
def transition(t: dict, tos: dict) -> dict:
    """A state machine transition: destination state, name, duration, offset, exit time, flags, conditions."""
    return {
        "destination": t["m_DestinationState"],
        "name": tos.get(t["m_ID"], t["m_ID"]),
        "duration": t["m_TransitionDuration"],
        "offset": t["m_TransitionOffset"],
        "exitTime": t["m_ExitTime"],
        "hasExitTime": t["m_HasExitTime"],
        "fixedDuration": t["m_HasFixedDuration"],
        "canTransitionToSelf": t["m_CanTransitionToSelf"],
        "conditions": [
            {
                "mode": x["data"]["m_ConditionMode"],
                "event": tos.get(x["data"]["m_EventID"], x["data"]["m_EventID"]),
                "threshold": x["data"]["m_EventThreshold"],
                "exitTime": x["data"]["m_ExitTime"],
            }
            for x in t["m_ConditionConstantArray"]
        ],
    }


def controller_doc(tt: dict, ref: Callable[[dict], object], value) -> dict:
    """An AnimatorController's name, clips, layers, state machines (states with speed, loop, blend trees,
    transitions), parameters and default values. Names are looked up in its TOS table. `ref(pptr)`: the value of a
    clip reference, taken first and in order; `value(v)`: the JSON of the default values."""
    tos = dict(tt["m_TOS"])
    clips = [ref(c) for c in tt["m_AnimationClips"]]
    c = tt["m_Controller"]
    machines = []
    for sm in c["m_StateMachineArray"]:
        sm = sm["data"]
        states = []
        for st in sm["m_StateConstantArray"]:
            st = st["data"]
            trees = []
            for bt in st["m_BlendTreeConstantArray"]:
                trees.append(
                    [
                        {
                            "clip": n["data"]["m_ClipID"],
                            "duration": n["data"]["m_Duration"],
                            "blendType": n["data"]["m_BlendType"],
                            "children": n["data"]["m_ChildIndices"],
                        }
                        for n in bt["data"]["m_NodeArray"]
                    ]
                )
            states.append(
                {
                    "name": tos.get(st["m_NameID"], st["m_NameID"]),
                    "path": tos.get(st["m_FullPathID"]),
                    "speed": st["m_Speed"],
                    "cycleOffset": st["m_CycleOffset"],
                    "loop": st["m_Loop"],
                    "writeDefaultValues": st["m_WriteDefaultValues"],
                    "mirror": st["m_Mirror"],
                    "speedParam": tos.get(st["m_SpeedParamID"], st["m_SpeedParamID"]),
                    "timeParam": tos.get(st["m_TimeParamID"], st["m_TimeParamID"]),
                    "blendTrees": trees,
                    "transitions": [transition(t["data"], tos) for t in st["m_TransitionConstantArray"]],
                }
            )
        machines.append(
            {
                "defaultState": sm["m_DefaultState"],
                "states": states,
                "anyStateTransitions": [transition(t["data"], tos) for t in sm["m_AnyStateTransitionConstantArray"]],
            }
        )
    layers = [
        {
            "name": tos.get(layer["data"]["m_Binding"]),
            "stateMachine": layer["data"]["m_StateMachineIndex"],
            "defaultWeight": layer["data"]["m_DefaultWeight"],
            "blending": layer["data"]["(int&)m_LayerBlendingMode"],
        }
        for layer in c["m_LayerArray"]
    ]
    vals = c["m_Values"]["data"]["m_ValueArray"] if "data" in c["m_Values"] else []
    params = [{"name": tos.get(v["m_ID"], v["m_ID"]), "type": v["m_Type"], "index": v["m_Index"]} for v in vals]
    return {
        "name": tt["m_Name"],
        "clips": clips,
        "layers": layers,
        "stateMachines": machines,
        "parameters": params,
        "defaultValues": value(c["m_DefaultValues"]),
    }


def controller(ctx: BundleObjects, info) -> None:
    """`json`: controller_doc with the clips as {"$ref"}. Unsupported for an AnimatorOverrideController."""
    key = (info.file, info.path_id)
    if info.class_name != "AnimatorController":
        raise Unsupported("generic.controller.override", f"{info.class_name} is exported as its typetree")
    r = ctx.readers[key]
    doc = controller_doc(ctx.tt(key), lambda p: ctx.ref(r, p), lambda v: ctx.value(r, v))
    ctx.emit(key, "json", encode_object(doc), "json", "animator.controller", "json", {})
