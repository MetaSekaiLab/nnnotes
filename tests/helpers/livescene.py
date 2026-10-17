"""A synthetic live scene as a product object table (nnnotes.objects/1) whose files are in a depot: the scene's root
GameObjects (an Animator playing the timeline's tracks on each, the combo Animator with its controller, the bar line
view chain into another prefab), the start timeline of band 1 (a group track, a signal track and an infinite clip),
its background, the lane skin, the lane line and judge effect prefabs, the UI sprite assets, the built-in graphics
settings and the shaders the live screen finds by name (a copy of UI/Default in a bundle that differs)."""

from __future__ import annotations

from nnnotes.content import livescene
from nnnotes.pipeline import contract
from tests.helpers.live2dmodel import ref
from tests.helpers.livenotes import SHADER_VARIANTS, at, clip, comp, node

SCENE, TIMELINE = "CAB-s", "CAB-t"
LIVE, GAME, DIRECTOR = f"{SCENE}:10", f"{SCENE}:20", f"{SCENE}:30"
STAGE_ANIMATOR, COMBO_ANIMATOR, VIEW_ANIMATOR = f"{SCENE}:11", f"{SCENE}:12", f"{SCENE}:21"
RENDER, MESH = f"{SCENE}:40", f"{SCENE}:41"
BAR_LINE, BAR_LINE_VIEW = "CAB-b:1", "CAB-b:2"
TL = f"{TIMELINE}:1"
ASSET, GROUP, STAGE_TRACK, LANE_TRACK, SIGNALS, VIEW_TRACK = (f"{TIMELINE}:{i}" for i in (10, 11, 12, 13, 14, 15))
VIEW_PA, STAGE_PA, LANE_PA = (f"{TIMELINE}:{i}" for i in (20, 21, 22))
VIEW_CLIP, RECORDED, STAGE_CLIP, LANE_CLIP, COMBO_CLIP, COMBO_CTL = (f"CAB-c:{i}" for i in range(1, 7))
BACKGROUND = "CAB-g:1"
SKIN = {p: f"CAB-l:{i}" for i, p in enumerate(livescene.LANE_SKIN_PARTS, 1)}
LANE_LINE, JUDGE_EFFECT = "CAB-p:1", "CAB-p:2"
COMBO_ASSET, JUDGEMENT_ASSET = "CAB-u:1", "CAB-u:2"
GRAPHICS, QUALITY, PLAYER = "globalgamemanagers:7", "globalgamemanagers:12", "globalgamemanagers:1"
SHADERS = {n: f"globalgamemanagers.assets:{i}" for i, n in enumerate(livescene.PLAYER_SHADERS, 1)}
UI_COPY = "CAB-x:1"
SCRIPT = "CAB-m:1"
APP = "App.Runtime|App.Live|"
TIMELINE_SCRIPT = "Unity.Timeline|UnityEngine.Timeline|"
NOTE_VIEW = "LiveGameView/LiveGameCamera/screen_root/LiveGameAllNoteView"


def mono(script: str, **fields) -> dict:
    return {"$script": script, **fields}


def scene_roots() -> dict:
    """{GameObject id: prefab document} of the scene's roots."""
    stage, combo = "Live/RenderCanvas/LightWeightBackgroundStageImage", "Live/LiveUICanvas/UILiveCombo"
    return {
        LIVE: {
            "name": "Live",
            "scene": "Assets/Scene/Live.unity",
            "nodes": [
                node("Live"),
                node(stage, comp("Animator", 11), comp("SpriteRenderer", 13)),
                node(combo, comp("Animator", 12, m_Controller=ref(COMBO_CTL))),
                node(f"{combo}/count", comp("SpriteRenderer", 14)),
            ],
        },
        GAME: {
            "name": "LiveGameView",
            "scene": "Assets/Scene/Live.unity",
            "nodes": [
                node("LiveGameView"),
                node("LiveGameView/root", comp("Animator", 21)),
                node("LiveGameView/root/LiveGameLane", comp("SpriteRenderer", 22)),
                node(
                    NOTE_VIEW,
                    comp(
                        "MonoBehaviour",
                        23,
                        APP + "LiveAllNoteView",
                        _barLineView=at(f"{NOTE_VIEW}/bar", "MonoBehaviour", "LiveAllBarLineView"),
                    ),
                ),
                node(
                    f"{NOTE_VIEW}/bar",
                    comp(
                        "MonoBehaviour",
                        24,
                        APP + "LiveAllBarLineView",
                        _container=at(f"{NOTE_VIEW}/bar", "MonoBehaviour", "LiveBarLineViewContainer"),
                    ),
                    comp("MonoBehaviour", 25, APP + "LiveBarLineViewContainer", _elementPrefab=ref(BAR_LINE_VIEW)),
                ),
            ],
        },
        DIRECTOR: {
            "name": "LiveStartTimelineDirector",
            "scene": "Assets/Scene/Live.unity",
            "nodes": [
                node(
                    "LiveStartTimelineDirector",
                    comp(
                        "MonoBehaviour",
                        31,
                        APP + "LiveStartTimelineDirector",
                        _liveGameViewAnimator=ref(VIEW_ANIMATOR),
                        _lightWeightBackgroundStageAnimator=ref(STAGE_ANIMATOR),
                        _liveCameras=None,
                    ),
                )
            ],
        },
    }


def timeline() -> dict:
    root = "live_start_playable_timeline"
    return {
        "name": root,
        "nodes": [
            node(
                root,
                comp("PlayableDirector", 2, m_PlayableAsset=ref(ASSET)),
                comp("MonoBehaviour", 3, APP + "LivePlayableTimeline", _laneInEffectAnimator=at(f"{root}/root")),
            ),
            node(f"{root}/root", comp("Animator", 4)),
            node(f"{root}/root/line", comp("SpriteRenderer", 5)),
        ],
    }


def shader(name: str, extra: int = 0) -> tuple:
    files = {v["role"]: b"program" for v in SHADER_VARIANTS}
    doc = {"name": name, **({"variant": extra} if extra else {})}
    return "Shader", name, {"json": doc, "index": {"name": name, "variants": SHADER_VARIANTS}, **files}


def objects() -> dict:
    """{object id: (class, name, {role: JSON document | bytes})}."""
    out = {gid: ("GameObject", doc["name"], {"json": doc}) for gid, doc in scene_roots().items()}
    out.update(
        {
            RENDER: ("RenderSettings", None, {"json": {"m_Fog": 0}}),
            MESH: (
                "Mesh",
                "CreateedTrapeziumMesh",
                {
                    "json": {"m_Name": "CreateedTrapeziumMesh"},
                    "mesh": b"glTF",
                    "arrays": {"positions": [[0.0, 0.0, 0.0]], "submeshes": []},
                },
            ),
            BAR_LINE: (
                "GameObject",
                "LiveBarLineView",
                {
                    "json": {
                        "name": "LiveBarLineView",
                        "nodes": [node("LiveBarLineView", comp("MonoBehaviour", 2, APP + "LiveBarLineView"))],
                    }
                },
            ),
            TL: ("GameObject", "live_start_playable_timeline", {"json": timeline()}),
            ASSET: (
                "MonoBehaviour",
                "live_start_timeline_asset",
                {
                    "json": mono(
                        TIMELINE_SCRIPT + "TimelineAsset",
                        m_Tracks=[ref(GROUP), ref(STAGE_TRACK), ref(LANE_TRACK), ref(SIGNALS)],
                    )
                },
            ),
            GROUP: (
                "MonoBehaviour",
                "Group",
                {"json": mono(TIMELINE_SCRIPT + "GroupTrack", m_Name="Group", m_Children=[ref(VIEW_TRACK)])},
            ),
            VIEW_TRACK: (
                "MonoBehaviour",
                "LiveGameViewTrack",
                {
                    "json": mono(
                        TIMELINE_SCRIPT + "AnimationTrack",
                        m_Name="LiveGameViewTrack",
                        m_Clips=[{"m_Start": 0.0, "m_Asset": ref(VIEW_PA)}],
                        m_AnimClip=ref(RECORDED),
                    )
                },
            ),
            STAGE_TRACK: (
                "MonoBehaviour",
                "LightWeightBackgroundStageTrack",
                {
                    "json": mono(
                        TIMELINE_SCRIPT + "AnimationTrack",
                        m_Name="LightWeightBackgroundStageTrack",
                        m_Clips=[{"m_Asset": ref(STAGE_PA)}],
                        m_AnimClip=None,
                    )
                },
            ),
            LANE_TRACK: (
                "MonoBehaviour",
                "LiveGameLaneEffect",
                {
                    "json": mono(
                        TIMELINE_SCRIPT + "AnimationTrack",
                        m_Name="LiveGameLaneEffect",
                        m_Clips=[{"m_Asset": ref(LANE_PA)}],
                    )
                },
            ),
            SIGNALS: (
                "MonoBehaviour",
                "Signals",
                {"json": mono(TIMELINE_SCRIPT + "SignalTrack", m_Name="Signals", m_Clips=[])},
            ),
            VIEW_PA: (
                "MonoBehaviour",
                "",
                {"json": mono(TIMELINE_SCRIPT + "AnimationPlayableAsset", m_Clip=ref(VIEW_CLIP))},
            ),
            STAGE_PA: (
                "MonoBehaviour",
                "",
                {"json": mono(TIMELINE_SCRIPT + "AnimationPlayableAsset", m_Clip=ref(STAGE_CLIP))},
            ),
            LANE_PA: (
                "MonoBehaviour",
                "",
                {"json": mono(TIMELINE_SCRIPT + "AnimationPlayableAsset", m_Clip=ref(LANE_CLIP))},
            ),
            VIEW_CLIP: ("AnimationClip", "live_game_view_in", {"json": clip("live_game_view_in", "LiveGameLane")}),
            RECORDED: ("AnimationClip", "Recorded", {"json": clip("Recorded", "LiveGameLane")}),
            STAGE_CLIP: ("AnimationClip", "stage_in", {"json": clip("stage_in", "")}),
            LANE_CLIP: ("AnimationClip", "lane_in", {"json": clip("lane_in", "line")}),
            COMBO_CLIP: ("AnimationClip", "combo", {"json": clip("combo", "count")}),
            COMBO_CTL: (
                "AnimatorController",
                "UILiveCombo",
                {"json": {"name": "UILiveCombo", "clips": [ref(COMBO_CLIP)]}},
            ),
            BACKGROUND: ("Sprite", "lightweight_background", {"json": {"name": "lightweight_background"}}),
            LANE_LINE: ("GameObject", "lane_line", {"json": {"name": "lane_line", "nodes": [node("lane_line")]}}),
            JUDGE_EFFECT: (
                "GameObject",
                "UILiveNoteJudgeEffectView",
                {"json": {"name": "UILiveNoteJudgeEffectView", "nodes": [node("UILiveNoteJudgeEffectView")]}},
            ),
            COMBO_ASSET: (
                "MonoBehaviour",
                "LiveComboSpriteAsset",
                {"json": mono("SiriusAsset|SiriusAsset|LiveComboSpriteAsset")},
            ),
            JUDGEMENT_ASSET: (
                "MonoBehaviour",
                "LiveJudgementSpriteAsset",
                {"json": mono("SiriusAsset|SiriusAsset|LiveJudgementSpriteAsset")},
            ),
            GRAPHICS: (
                "GraphicsSettings",
                None,
                {
                    "json": {
                        "m_AlwaysIncludedShaders": [ref(s) for s in SHADERS.values()],
                        "m_CustomRenderPipeline": None,
                    }
                },
            ),
            QUALITY: ("QualitySettings", None, {"json": {"m_CurrentQuality": 0}}),
            PLAYER: ("PlayerSettings", None, {"json": {"m_ActiveColorSpace": 0}}),
            UI_COPY: shader("UI/Default", extra=1),
            SCRIPT: ("MonoScript", "LiveAllNoteView", {"json": {"m_ClassName": "LiveAllNoteView"}}),
        }
    )
    out.update({SKIN[p]: ("Sprite", p, {"json": {"name": p}}) for p in livescene.LANE_SKIN_PARTS})
    out.update({sid: shader(name) for name, sid in SHADERS.items()})
    return out


# components of a hierarchy that are referenced from outside it: {object id: the GameObject holding it}
CONTAINED = {STAGE_ANIMATOR: LIVE, COMBO_ANIMATOR: LIVE, VIEW_ANIMATOR: GAME, BAR_LINE_VIEW: BAR_LINE}


def keys(record: dict) -> dict:
    """{key: root object ids} of the scene product: band 1 has a timeline, the other bands none."""
    out = {k: [] for k in livescene.scene_keys(record)}
    out.update(
        {
            livescene.SCENE_KEY: [LIVE, GAME, DIRECTOR, RENDER, MESH],
            livescene.LANE_LINE_KEY: [LANE_LINE],
            livescene.JUDGE_EFFECT_KEY: [JUDGE_EFFECT],
            livescene.SPRITE_ASSETS["combo"][0]: [COMBO_ASSET],
            livescene.SPRITE_ASSETS["judgement"][0]: [JUDGEMENT_ASSET],
            livescene.TIMELINE_KEY.format(band=1): [TL],
            livescene.BACKGROUND_KEY.format(band=1): [BACKGROUND],
            **{livescene.lane_skin_key("skin001", p): [SKIN[p]] for p in livescene.LANE_SKIN_PARTS},
        }
    )
    return out


def table(depot, record: dict, objs: dict | None = None) -> dict:
    """The object table of `objs` (objects()) with the roots of keys(record) and the built-in settings, every file put
    in `depot`."""
    out = {}
    for oid, (cls, name, files) in sorted((objs if objs is not None else objects()).items()):
        recs = {}
        for role, body in sorted(files.items()):
            data = body if isinstance(body, bytes) else contract.encode(body)
            recs[role] = {
                "sha256": depot.put(data),
                "size": len(data),
                "ext": "glsl" if isinstance(body, bytes) else "json",
            }
        out[oid] = {"status": "exported", "class": cls, **({"name": name} if name else {}), "files": recs}
    for oid, holder in CONTAINED.items():
        out[oid] = {"status": "contained", "class": "Component", "in": holder}
    ks = keys(record)
    roots = sorted({o for v in ks.values() for o in v} | {GRAPHICS, QUALITY, PLAYER})
    return {"schema": contract.OBJECTS, "roots": roots, "keys": ks, "objects": out, "fenced": {}, "missing": []}


def master() -> dict:
    """Master tables {name: rows} of the scene settings (SCENE_TABLES)."""
    items = {"GuidelineCount": 302, "LiveSkinId": 305, "QualitySetting": 600, "NoteSpeed": 1}
    values = {"GuidelineCount": "2", "LiveSkinId": "1", "QualitySetting": "0", "NoteSpeed": "5.00"}
    return {
        "MasterOptionDefault": [
            {"_id": i, "_presetId": p, "_optionItemType": t, "_valueString": values[n]}
            for p in (1, 2)
            for i, (n, t) in enumerate(items.items())
        ],
        "MasterOptionRange": [
            {"_optionItemType": 600, "_minValue": 0, "_maxValue": 2},
            {"_optionItemType": 1, "_minValue": 100, "_maxValue": 1200},
        ],
        "MasterLiveQualitySettings": [{"_id": 1, "_quality": 1, "_effectRenderingScale": 0.5}],
        "MasterLiveLaneSkin": [{"_id": 1, "_assetName": "skin001"}],
        "MasterBand": [{"_id": 2}, {"_id": 1}],
        "MasterLiveJudgementSprite": [
            {"_id": 1, "_noteSimulateJudgement": 5, "_spriteName": "judgment_perfect"},
            {"_id": 2, "_noteSimulateJudgement": 1, "_spriteName": "judgment_miss"},
        ],
    }
