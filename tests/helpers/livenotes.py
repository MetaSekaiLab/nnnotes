"""A synthetic note highway as product object tables (nnnotes.objects/1) whose files are in a depot: the note prefabs
(the flick views with an animated arrow), the lane effect settings and an effect prefab with an Animator, a note skin
whose flick unit overrides the arrow loop, a note effect set, a material and its shader, and a MonoScript."""

from __future__ import annotations

from nnnotes.content import livenotes
from nnnotes.pipeline import contract
from nnnotes.unity.bindings import crc
from tests.helpers.live2dmodel import ref, variant

CAB = "CAB-n"
SIRIUS = "SiriusAsset|SiriusAsset|"
LIVE = "App.Runtime|App.Live.View|"
FLICK_CTL, FLICK_CLIP, LOOP_CLIP, SKIN_CLIP = (f"{CAB}:{i}" for i in (900, 901, 902, 903))
LANE, LANE_FX, FX_CTL, FX_CLIP = (f"{CAB}:{i}" for i in (950, 951, 952, 953))
SKIN, UNIT_FLICK, UNIT_LEFT = f"{CAB}:960", f"{CAB}:961", f"{CAB}:962"
EFFECT, EFFECT_FX = f"{CAB}:970", f"{CAB}:971"
MAT, SHADER, SCRIPT = f"{CAB}:980", f"{CAB}:981", "CAB-s:1"
SHADER_VARIANTS = [
    variant(0, []),
    {**variant(1, []), "pass": 1, "role": "program:gles3/s0p1_progVertex_1"},
    variant(2, [], "vulkan", "SPIRV"),
]


def prefab_id(name: str) -> str:
    return f"{CAB}:{100 + sorted(livenotes.NOTE_PREFABS).index(name)}"


def comp(type_: str, pid: int, script: str | None = None, **fields) -> dict:
    c = {"type": type_, "pathId": str(pid), "fields": fields}
    if script:
        c["script"] = script
    return c


def node(path: str, *components) -> dict:
    return {"path": path, "name": path.rsplit("/", 1)[-1], "components": list(components)}


def at(path: str, cls: str = "Animator", script: str | None = None) -> dict:
    r = {"component": cls, "gameObject": path}
    if script:
        r["class"] = script
    return r


def flick_view(name: str) -> dict:
    """A flick view: its view on the root names the LoopAnimationTimeSync of anim_root, whose Animator plays the
    controller's clip and the sync's loop clip on the arrow."""
    root, anim = name, f"{name}/anim_root"
    return {
        "name": name,
        "nodes": [
            node(
                root,
                comp(
                    "MonoBehaviour",
                    1,
                    LIVE + "LiveFlickNoteView",
                    _loopAnimationTimeSync=at(anim, "MonoBehaviour", "LoopAnimationTimeSync"),
                ),
            ),
            node(
                anim,
                comp("Animator", 2, m_Controller=ref(FLICK_CTL)),
                comp("MonoBehaviour", 3, LIVE + "LoopAnimationTimeSync", _animator=at(anim), _loopAnim=ref(LOOP_CLIP)),
            ),
            node(f"{anim}/arrow", comp("SpriteRenderer", 4, m_Materials=[ref(MAT)])),
        ],
    }


def plain(name: str) -> dict:
    return {"name": name, "nodes": [node(name, comp("SpriteRenderer", 1, m_Materials=[ref(MAT)]))]}


def effect_prefab(name: str) -> dict:
    return {
        "name": name,
        "nodes": [
            node(name),
            node(f"{name}/root", comp("Animator", 2, m_Controller=ref(FX_CTL))),
            node(f"{name}/root/glow", comp("SpriteRenderer", 3, m_Materials=[ref(MAT)])),
        ],
    }


def clip(name: str, path: str, attribute: str = "m_Color.a") -> dict:
    binding = {"pathCrc": crc(path), "attributeCrc": crc(attribute), "typeID": 212, "class": "SpriteRenderer"}
    return {"name": name, "sampleRate": 60.0, "bindings": [binding]}


def objects() -> dict:
    """{object id: (class, name, {role: JSON document | bytes})}."""
    out = {}
    for name in livenotes.NOTE_PREFABS:
        doc = flick_view(name) if name in livenotes.FLICK_SKIN_UNITS else plain(name)
        out[prefab_id(name)] = ("GameObject", name, {"json": doc})
    shader_files = {v["role"]: b"program" for v in SHADER_VARIANTS}
    out.update(
        {
            FLICK_CTL: ("AnimatorController", "flick", {"json": {"name": "flick", "clips": [ref(FLICK_CLIP)]}}),
            FLICK_CLIP: ("AnimationClip", "flick_loop", {"json": clip("flick_loop", "arrow")}),
            LOOP_CLIP: ("AnimationClip", "sync_loop", {"json": clip("sync_loop", "arrow")}),
            SKIN_CLIP: ("AnimationClip", "skin_loop", {"json": clip("skin_loop", "arrow")}),
            LANE: (
                "MonoBehaviour",
                "LiveLaneEffectAssetSettings",
                {"json": {"$script": SIRIUS + "LiveLaneEffectAssetSettings", "NormalEffect": ref(LANE_FX)}},
            ),
            LANE_FX: ("GameObject", "lane_tap", {"json": effect_prefab("lane_tap")}),
            FX_CTL: (
                "AnimatorController",
                "tap",
                {"json": {"name": "tap", "clips": [ref(FX_CLIP), ref("CAB-gone:1")]}},
            ),
            FX_CLIP: ("AnimationClip", "tap_perfect", {"json": clip("tap_perfect", "glow")}),
            SKIN: (
                "MonoBehaviour",
                "LiveNoteSkinAsset",
                {
                    "json": {
                        "$script": SIRIUS + "LiveNoteSkinAsset",
                        "FlickNoteAsset": ref(UNIT_FLICK),
                        "LeftFlickNoteAsset": ref(UNIT_LEFT),
                        "RightFlickNote": None,
                    }
                },
            ),
            UNIT_FLICK: (
                "MonoBehaviour",
                "FlickNoteSkinAssetUnit",
                {"json": {"$script": SIRIUS + "FlickNoteSkinAssetUnit", "_arrowLoopAnimation": ref(SKIN_CLIP)}},
            ),
            UNIT_LEFT: (
                "MonoBehaviour",
                "FlickLeftNoteSkinAssetUnit",
                {"json": {"$script": SIRIUS + "FlickNoteSkinAssetUnit", "_arrowLoopAnimation": None}},
            ),
            EFFECT: (
                "MonoBehaviour",
                "LiveNoteEffectAssetSettings",
                {"json": {"$script": SIRIUS + "LiveNoteEffectAssetSettings", "NormalEffect": ref(EFFECT_FX)}},
            ),
            EFFECT_FX: ("GameObject", "note_normal", {"json": effect_prefab("note_normal")}),
            MAT: ("Material", "sprite", {"json": {"material": "sprite", "shader": ref(SHADER), "keywords": []}}),
            SHADER: (
                "Shader",
                "Sirius/Sprite",
                {
                    "json": {"name": "Sirius/Sprite"},
                    "index": {"name": "Sirius/Sprite", "variants": SHADER_VARIANTS},
                    "typetree": {"m_Name": ""},
                    **shader_files,
                },
            ),
            SCRIPT: ("MonoScript", "LiveFlickNoteView", {"json": {"m_ClassName": "LiveFlickNoteView"}}),
        }
    )
    return out


NOTES_KEYS = {**{k: [prefab_id(n)] for n, k in livenotes.NOTE_PREFABS.items()}, livenotes.LANE_EFFECT_KEY: [LANE]}
SKIN_KEYS = {
    livenotes.skin_key("skin001"): [SKIN],
    **{livenotes.NOTE_PREFABS[v]: [prefab_id(v)] for v in livenotes.FLICK_SKIN_UNITS},
}
EFFECT_KEYS = {livenotes.effect_settings_key("effect001Light"): [EFFECT]}


def table(depot, keys: dict, objs: dict | None = None) -> dict:
    """The object table of `objs` (objects()) with the roots of `keys` {key: object ids}, every file put in
    `depot`."""
    out = {}
    for oid, (cls, name, files) in sorted((objs if objs is not None else objects()).items()):
        recs = {}
        for role, body in sorted(files.items()):
            data = body if isinstance(body, bytes) else contract.encode(body)
            ext = "glsl" if isinstance(body, bytes) else "json"
            recs[role] = {"sha256": depot.put(data), "size": len(data), "ext": ext}
        out[oid] = {"status": "exported", "class": cls, "name": name, "files": recs}
    roots = sorted({o for v in keys.values() for o in v})
    return {"schema": contract.OBJECTS, "roots": roots, "keys": keys, "objects": out, "fenced": {}, "missing": []}


def master() -> dict:
    """Master tables {name: rows} of the notes settings."""
    items = {"NoteSpeed": 1, "LiveQuality": 6, "NoteDesignId": 306, "NoteEffectId": 307, "LiveMusicVolume": 410}
    values = {
        "NoteSpeed": "5.00",
        "LiveQuality": "1",
        "NoteDesignId": "2",
        "NoteEffectId": "1",
        "LiveMusicVolume": "100",
    }
    defaults = [
        {"_id": i, "_presetId": p, "_optionItemType": t, "_valueString": values[n]}
        for p in (1, 2)
        for i, (n, t) in enumerate(items.items())
    ]
    defaults.append({"_id": 99, "_presetId": 1, "_optionItemType": 999, "_valueString": "x"})  # not a note item
    ls = {
        "note_speed_min": 1.0,
        "note_speed_max": 12.0,
        "note_speed_view_min": 0.4,
        "note_speed_view_max": 7.0,
        "slide_offset_min_note_lane_width": 0.5,
        "unrelated": 3,
    }
    return {
        "MasterOptionDefault": defaults,
        "MasterOptionRange": [
            {"_optionItemType": 1, "_minValue": 100, "_maxValue": 1200},
            {"_optionItemType": 999, "_minValue": 0, "_maxValue": 1},
        ],
        "MasterLiveSettings": [{"_key": k, "_value": v} for k, v in ls.items()],
        "MasterLiveNoteSkin": [{"_id": 1, "_assetName": "skin001"}, {"_id": 2, "_assetName": "skin002"}],
        "MasterLiveNoteEffectSkin": [
            {"_id": 1, "_assetName": "effect001"},
            {"_id": 2, "_assetName": "effect001Simple"},
        ],
    }
