"""The scene of a live: what exists on the live screen before the first note, and the start timeline of each band.

One product (`scene`, scene.json format SCENE_FORMAT), read from a product's object table (`Objects`,
pipeline.assemble), never from a bundle, and shared by every live:

    scene           the root GameObjects of the addressable scene EmbScene/Live, each a prefab document (the Live
                    prefab instance with the cameras, render canvas, background view and UI canvases; LiveGameView
                    with the lane camera, the lane and the note and effect containers; LiveStartTimelineDirector);
                    their references to each other are `$ref`s to components (prefabs.component_ref)
    renderSettings  the scene's RenderSettings
    graphics        the built-in settings the render path reads, by id: `graphics` (GraphicsSettings: the render
                    pipeline asset, its renderers and their post-process data), `quality` (QualitySettings), `player`
                    (PlayerSettings: the colour space)
    laneSkins       {lane skin asset name: {part: Sprite}} of the lane skins of MasterLiveLaneSkin (LANE_SKIN_PARTS)
    prefabs         `laneLine` (LiveLaneLineView._linePrefab), `judgeEffectView` (UILiveNoteJudgeEffectView) and
                    `barLineView` (the LiveBarLineView prefab the scene's bar line container instantiates,
                    livenotes.BAR_LINE_CHAIN)
    spriteAssets    the combo and judgement sprite assets of the live UI (SPRITE_ASSETS)
    bands           {band id: {timeline, background, tracks}} of the bands of MasterBand whose start timeline the
                    catalog holds: the live_start_playable_timeline prefab (LiveStartTimelineLoadStep), the
                    LightWeight background sprite (LightWeightBackgroundLoadStep) and {AnimationTrack id: {name,
                    prefab, animator}}, the Animator each track of the timeline plays on (TIMELINE_TRACK_ANIMATORS)
    settings        the option defaults (preset 1) and ranges of the items the render path reads (OPTION_ITEMS), the
                    MasterLiveQualitySettings rows, the default lane skin and every lane skin by option value, and
                    the judgement sprite names by judgement (scene_settings)
    bindings        {prefab GameObject id: {Animator node path: {clip id: [{path, attribute}]}}}: the clips each
                    Animator plays through its controller and LoopAnimationTimeSyncs (livenotes.animator_bindings)
                    and the clips of each band's timeline tracks on theirs
    shaders         {shader name: object id}: what a lookup by name finds (prefabs.shader_table; of different
                    shaders of one name the one GraphicsSettings always includes), holding the shaders the live
                    screen finds by name (PLAYER_SHADERS); a material names its shader by id

A live plays the timeline and the background of one band: the band of the player's deck centre, which the live
chooses. Of the objects the product keeps all but the MonoScripts; of a shader its parsed form, variant index and the
programs of the player's graphics API; of a Mesh its typetree JSON and its `arrays` document (prefabs.mesh_roles).

`extract` writes the scene of a live directory instead (livescene/scene.json with textures and shaders) through
export.Exporter in its "livescene" clip format (clips inlined once, bindings with crc32 keys, path candidates and
curve counts):

  player          colour space, quality levels, URP pipelines, renderer data (player.py)
  scene           the scene as a node list
  laneSkin        lane skin sprites of the LiveSkinId option's MasterLiveLaneSkin row
  assets          further prefabs by key: the start timeline of the band, the lane line prefab
  master          master rows the render path reads (music, quality settings, option defaults)
  derived         lane geometry computed with the game's formulas for the default options
                  (each entry names the function it mirrors)
  postTextures    film grain textures of the live renderers' post-process data
  shaders         every Shader in the loaded bundle closures, everything referenced, and the
                  shaders of the live camera renderers (GLES3 + other platforms, shader.py layout)

Only what the LightWeight screen (mode 3) loads is exported (LiveResourceLoadPipelineFactory.Create): the mode-1
stage (Band/{b}/live_stage/live_stage, its Volume Profile, StageBlinkLight), the background director and camera
blends are not loaded in mode 3; EmbLive/Volume/LiveStageVolume is referenced by nothing; the LiveGameVolume
profile is part of the scene (LiveEffectCamera's Volume). Only the music row, the slice and the jacket sprite depend
on the music: `band_part` exports the rest once for a band and `music_part` completes it for each music of that band
(the same document as `extract`).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.jsonio import write_json
from .livenotes import BAR_LINE_CHAIN, LANE_COUNT, LANE_VIEW, NOTE_VIEW, animator_bindings
from .liveoptions import OPTION_ITEM_TYPES
from .prefabs import (
    Objects,
    Prefab,
    builtin_root,
    component_class,
    component_ref,
    has_json,
    key_object,
    mesh_roles,
    ref_id,
    shader_table,
)

if TYPE_CHECKING:
    from ..unity.export import Exporter

# --- keys -------------------------------------------------------------------
SCENE_KEY = "EmbScene/Live"
LANE_SKIN_PARTS = ("lane_base", "lane_tap_area", "out_side_line")
# asset keys exported whole ({band} = the band of the LightWeight background and the start timeline). The timeline
# prefab's PlayableDirector carries its TimelineAsset (Band/{b}/timeline/live_start_timeline_asset) inline.
ASSET_KEYS = {
    "startTimeline": "Band/{band}/timeline/live_start_playable_timeline",  # LiveStartTimelineLoadStep
    "laneLinePrefab": "EmbLive/Prefabs/LiveGame/lane_line",  # LiveLaneLineView._linePrefab (inner lines)
}
# Animator each AnimationTrack of the start timeline plays on (LiveStartTimelineDirector.BindTimelineTrack, by
# track name): a field of the scene's LiveStartTimelineDirector, or for LiveGameLaneEffect the timeline prefab's
# LivePlayableTimeline._laneInEffectAnimator. The SignalTrack goes to the prefab's SignalReceiver (no clips).
TIMELINE_TRACK_ANIMATORS = {
    "UILiveStartCanvasTrack": ("scene", "LiveStartTimelineDirector", "_uiLiveStartCanvasAnimator"),
    "LiveGameViewTrack": ("scene", "LiveStartTimelineDirector", "_liveGameViewAnimator"),
    "LiveBackgroundViewTrack": ("scene", "LiveStartTimelineDirector", "_liveBackgroundViewAnimator"),
    "LiveCamerasTrack": ("scene", "LiveStartTimelineDirector", "_liveCameras"),
    "MovingLightTrack": ("scene", "LiveStartTimelineDirector", "_movingLightAnimator"),
    "LightWeightBackgroundStageTrack": ("scene", "LiveStartTimelineDirector", "_lightWeightBackgroundStageAnimator"),
    "LiveGameLaneEffect": ("timeline", "LivePlayableTimeline", "_laneInEffectAnimator"),
}
# sprite keys (a .png key's Sprite sub-asset): LightWeight composite inputs (LiveViewPresenter)
SPRITE_KEYS = {
    "lightweightBackground": "Band/{band}/live_stage/lightweight_background",
    "jacket": "Image/Jacket/{jacket}",
}
# Scene nodes whose textures the LightWeight preview draws (Live/RenderCanvas images);
# every other texture of the scene prefab belongs to UI the preview does not show (headers, gauges, pause, gekisou,
# skill cut-in, finish direction, mode-1 background) and is not written: its record stays, with "exported": false.
SCENE_DRAWN_TEXTURE_NODES = ("Live/RenderCanvas/LightWeightBackgroundShadowImage",)
# shaders the live screen finds by name (GraphicsSettings always-included, APK player data):
# LightWeight composite and blur, MV/VJ video
PLAYER_SHADERS = (
    "Hidden/CompositeRectBlit",
    "Hidden/SimpleGaussianBlur",
    "Sirius/Live/LiveSofdecPrimeYuv",
    "Sirius/Live/LiveSofdecPrimeYuv_Android",
    "UI/Default",
)
# URP renderer indices used by the scene's live cameras
LIVE_RENDERERS = (2, 3, 4, 5)
# option items the render path reads
OPTION_ITEMS = {
    k: OPTION_ITEM_TYPES[k]
    for k in (3, 6, 104, 105, 106, 107, 108, 109, 110, 115, 200, 201, 202, 203, 300, 301, 302, 305, 306, 307, 308, 600)
}


SCENE_FORMAT = 1  # scene.json "format"
SCENE_TABLES = (
    "MasterOptionDefault",
    "MasterOptionRange",
    "MasterLiveQualitySettings",
    "MasterLiveLaneSkin",
    "MasterBand",
    "MasterLiveJudgementSprite",
)
LANE_LINE_KEY = ASSET_KEYS["laneLinePrefab"]
JUDGE_EFFECT_KEY = "EmbLive/Prefabs/UILiveNoteJudgeEffectView"
# the sprite assets of the live UI: {name: (key, script class)}
SPRITE_ASSETS = {
    "combo": ("Live/UiSpriteAssets/LiveComboSpriteAsset", "LiveComboSpriteAsset"),
    "judgement": ("Live/UiSpriteAssets/LiveJudgementSpriteAsset", "LiveJudgementSpriteAsset"),
}
TIMELINE_KEY = ASSET_KEYS["startTimeline"]
BACKGROUND_KEY = SPRITE_KEYS["lightweightBackground"]
# the built-in settings of `graphics`: {name: built-in selector}
GRAPHICS = {"graphics": "GraphicsSettings", "quality": "QualitySettings", "player": "PlayerSettings"}


def lane_skin_key(name: str, part: str) -> str:
    """The sprite of part `part` (LANE_SKIN_PARTS) of the lane skin of MasterLiveLaneSkin._assetName `name`."""
    return f"Live/Lane/{name}/{part}"


def master_rows(master: Path, music_id: int) -> dict:
    from .score import master_table

    music = next(r for r in master_table(master, "MasterLiveMusic") if r["_id"] == music_id)
    opts = {
        r["_optionItemType"]: r["_valueString"]
        for r in master_table(master, "MasterOptionDefault")
        if r["_presetId"] == 1 and r["_optionItemType"] in OPTION_ITEMS
    }
    return {
        "liveMusic": music,
        "liveQualitySettings": master_table(master, "MasterLiveQualitySettings"),
        "liveLaneSkin": master_table(master, "MasterLiveLaneSkin"),
        "liveStageVideo": master_table(master, "MasterLiveStageVideo"),
        "optionDefaultsPreset1": {OPTION_ITEMS[k]: {"id": k, "value": v} for k, v in sorted(opts.items())},
        "optionRanges": {
            OPTION_ITEMS[r["_optionItemType"]]: [r["_minValue"], r["_maxValue"]]
            for r in master_table(master, "MasterOptionRange")
            if r["_optionItemType"] in OPTION_ITEMS
        },
    }


# --- derived lane geometry (mirrors the game functions named per entry) ---
# LiveLaneLineView's constant tables: split border main / sub counts per LiveLaneSplitCountType
# (None, Lane4, Lane6, Lane8, Lane12)
SPLIT_MAIN = (0, 4, 6, 8, 12)
SPLIT_SUB = (12, 12, 12, 0, 0)
REF_ASPECT = 1.7777778  # LiveGameView.FullInitialize
REF_FOV = 54.0


def vertical_fov(width: int, height: int) -> float:
    """LiveGameView.CalcVerticalFovKeepingHorizontal, aspect clamped to <= 16:9 by the caller."""
    a = min(width / height, REF_ASPECT)
    h = math.atan(math.tan(math.radians(REF_FOV) * 0.5) * REF_ASPECT)
    return math.degrees(2 * math.atan(math.tan(h) / a))


def _node(nodes: list, path: str) -> dict:
    hit = [n for n in nodes if n["path"] == path]
    if len(hit) != 1:
        raise KeyError(f"{path}: {len(hit)} nodes")
    return hit[0]


def _comp(node: dict, cls: str) -> dict:
    hit = [c for c in node["components"] if c.get("class", c["type"]) == cls]
    if len(hit) != 1:
        raise KeyError(f"{node['path']}: {len(hit)} {cls}")
    return hit[0]


def derive_lane(nodes: list, options: dict) -> dict:
    """Lane geometry for the given option values (MasterOptionDefault preset 1 strings)."""
    import numpy as np

    lv = _comp(_node(nodes, "LiveGameView/root/LiveGameLane"), "LiveLaneView")
    llv = _comp(_node(nodes, "LiveGameView/root/LiveGameLane/lines"), "LiveLaneLineView")
    jr = _node(nodes, "LiveGameView/root/LiveGameLane/judgement_root")
    n = LANE_COUNT
    width = float(np.float32(lv["_laneWidth"]))  # double field, passed on as (float)
    length = lv["_lineLength"]
    unit = width / n
    judge_z = jr["localPosition"]["z"]  # parents are identity: world z of position 0
    split = int(options["GuidelineCount"])
    main, sub = SPLIT_MAIN[split], SPLIT_SUB[split]
    settings = {s["LineType"]: s for s in llv["_settings"]}
    lines = []
    for i in range(n + 1):  # LiveLaneLineView.UpdateProperties
        if i == 0 or i == n:
            t = 1  # GetSettings: OutSide
        elif main and i % (n // main) == 0:
            t = 0  # Normal
        elif sub and i % (n // sub) == 0:
            t = 2  # Space
        else:
            lines.append({"index": i, "active": False})
            continue
        st = settings[t]
        x = -width * 0.5 + i * unit
        if t == 2:
            ln, z0 = llv["_spaceLineLength"], judge_z - llv["_spaceLineLength"] * 0.5
        else:
            ln, z0 = length, 0.0
        to_w = st["LineWidthTo"] * 2 if (i == n // 2 and t != 2) else st["LineWidthTo"]
        # LiveLaneLine.SetLine: _anchor 2 (left side line) x -= startWidth/2, _anchor 1 (right) x += startWidth/2
        if i == 0:
            x -= st["LineWidthFrom"] * 0.5
        elif i == n:
            x += st["LineWidthFrom"] * 0.5
        lines.append(
            {
                "index": i,
                "active": True,
                "type": ("Normal", "OutSide", "Space")[t],
                "start": [x, 0.0, z0],
                "end": [x, 0.0, z0 + ln],
                "startWidth": st["LineWidthFrom"],
                "endWidth": to_w,
                "gradient": st["LineColor"],
                "alphaControlled": t != 1,
            }
        )
    return {
        "laneCount": n,
        "laneWidth": width,
        "unitWidth": unit,
        "lineLength": length,
        "judgementZ": judge_z,
        "unitCentersX": [unit * (i + 0.5) - width * 0.5 for i in range(n)],
        "spawnRoots": [[unit * (i + 0.5) - width * 0.5, 0.0, length] for i in range(n)],
        "judgementPositions": [[unit * (i + 0.5) - width * 0.5, 0.0, judge_z] for i in range(n)],
        "splitCountType": split,
        "splitBorderMainCount": main,
        "splitBorderSubCount": sub,
        "lines": lines,
        "lineAlpha": min(max(int(options["GuidelineOpacity"]) / 100.0, 0.0), 1.0),
        "laneBaseAlpha": min(max(int(options["LaneOpacity"]) / 100.0, 0.0), 1.0),
        "showJudgementLine": options["JudgePositionDisplay"].upper() == "TRUE",
        "laneJudgementPosOffset": (int(options["JudgePosition"]) + 5) / -10.0 + 1.0,
        "screenPlane": lane_2d(),
        "sources": {
            "lines": "LiveLaneLineView.UpdateProperties, GetSettings, LiveLaneLine.SetLine",
            "spawnRoots": "LiveNoteSpawnRoot.UpdateProperties",
            "judgementPositions": "LiveNoteJudgementRoot3D.UpdateProperties",
            "options": "LiveBootDataCreator.CreateViewData",
            "screenPlane": "LiveSettingCreator.CreateLaneViewSettings, JudgementPositionLogic.GetJudgementPositions, "
            "LiveViewUtility.GetNoteSpawnPosition",
        },
    }


def lane_2d(lane_count: int = LANE_COUNT) -> dict:
    """LiveSettingCreator.CreateLaneViewSettings as CreateViewData calls it (LANE_VIEW: size 1920x1080,
    topRange 0.05, bottomRange 1, judgementScreenBottomPosition 2.24, topPosition 0) and
    JudgementPositionLogic.GetJudgementPositions; units of 100 reference pixels."""
    w, h = LANE_VIEW["size"][0] / 100.0, LANE_VIEW["size"][1] / 100.0
    top, bottom = LANE_VIEW["topRange"], LANE_VIEW["bottomRange"]
    lt, rt = [-w * top * 0.5, h * 0.5, 0.0], [w * top * 0.5, h * 0.5, 0.0]
    lb, rb = [-w * bottom * 0.5, -h * 0.5, 0.0], [w * bottom * 0.5, -h * 0.5, 0.0]
    t = min(max(LANE_VIEW["judgementScreenBottomPosition"] / (lt[1] - lb[1]), 0.0), 1.0)
    jp = []
    for i in range(lane_count):
        f = min(max(i / lane_count, 0.0), 1.0)
        xb = (rb[0] - lb[0]) / lane_count * 0.5 + lb[0] + (rb[0] - lb[0]) * f
        xt = (rt[0] - lt[0]) / lane_count * 0.5 + lt[0] + (rt[0] - lt[0]) * f
        yb, yt = lb[1] + (rb[1] - lb[1]) * f, lt[1] + (rt[1] - lt[1]) * f
        jp.append([xb + t * (xt - xb), yb + (yt - yb) * t, 0.0])
    # LiveViewUtility.GetNoteSpawnPosition: intersection of the two side edges
    s1 = (lb[1] - lt[1]) / (lb[0] - lt[0])
    s2 = (rb[1] - rt[1]) / (rb[0] - rt[0])
    c1 = lt[1] - lt[0] * s1
    sx = ((rt[1] - rt[0] * s2) - c1) / (s1 - s2)
    return {
        "vertices": [lt, rt, lb, rb],
        "judgementPositions": jp,
        "spawnPosition": [sx, c1 + s1 * sx],
        "pixelsPerUnit": 100.0,
    }


# --- product ----------------------------------------------------------------
def scene_record(tables: dict) -> dict:
    """What scene_settings and scene_keys read of the master tables `tables` {name: rows} (SCENE_TABLES): the
    preset-1 defaults and the ranges of OPTION_ITEMS, the quality settings, the lane skins, the bands and the
    judgement sprites."""
    return {
        "MasterOptionDefault": [
            r for r in tables["MasterOptionDefault"] if r["_presetId"] == 1 and r["_optionItemType"] in OPTION_ITEMS
        ],
        "MasterOptionRange": [r for r in tables["MasterOptionRange"] if r["_optionItemType"] in OPTION_ITEMS],
        "MasterLiveQualitySettings": tables["MasterLiveQualitySettings"],
        "MasterLiveLaneSkin": tables["MasterLiveLaneSkin"],
        "MasterBand": tables["MasterBand"],
        "MasterLiveJudgementSprite": tables["MasterLiveJudgementSprite"],
    }


def scene_keys(record: dict) -> list[str]:
    """The catalog keys of the scene product: the scene, the lane line and judge effect prefabs, the UI sprite assets,
    the sprites of every lane skin and the start timeline and background of every band."""
    keys = {SCENE_KEY, LANE_LINE_KEY, JUDGE_EFFECT_KEY, *(k for k, _ in SPRITE_ASSETS.values())}
    keys.update(lane_skin_key(r["_assetName"], p) for r in record["MasterLiveLaneSkin"] for p in LANE_SKIN_PARTS)
    keys.update(k.format(band=r["_id"]) for r in record["MasterBand"] for k in (TIMELINE_KEY, BACKGROUND_KEY))
    return sorted(keys)


def scene_settings(record: dict) -> dict:
    """scene.json `settings` (module documentation) from a scene_record."""
    opts = {OPTION_ITEMS[r["_optionItemType"]]: r["_valueString"] for r in record["MasterOptionDefault"]}
    skins = {r["_id"]: r["_assetName"] for r in record["MasterLiveLaneSkin"]}
    judgements = sorted(record["MasterLiveJudgementSprite"], key=lambda r: r["_noteSimulateJudgement"])
    return {
        "optionDefaults": dict(sorted(opts.items())),
        "optionRanges": {
            OPTION_ITEMS[r["_optionItemType"]]: [r["_minValue"], r["_maxValue"]]
            for r in sorted(record["MasterOptionRange"], key=lambda r: r["_optionItemType"])
        },
        "qualitySettings": record["MasterLiveQualitySettings"],
        "laneSkin": skins[int(opts["LiveSkinId"])],
        "laneSkins": {str(i): n for i, n in sorted(skins.items())},
        "judgementSprites": {str(r["_noteSimulateJudgement"]): r["_spriteName"] for r in judgements},
    }


def _one_component(objects: Objects, prefabs: list[str], cls: str) -> tuple[str, dict]:
    """(prefab GameObject id, component) of the one component of class `cls` of the hierarchies `prefabs`."""
    hits = [(g, c) for g in prefabs for _, c in Prefab(objects.json(g)).components(cls)]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} {cls} components")
    return hits[0]


def _tracks(objects: Objects, asset: str) -> list[tuple[str, dict]]:
    """(id, document) of every track of a TimelineAsset, depth first through group tracks."""
    out = []

    def walk(refs) -> None:
        for r in refs:
            t = ref_id(r)
            if t is not None:
                out.append((t, objects.json(t)))
                walk(out[-1][1].get("m_Children", ()))

    walk(objects.json(asset).get("m_Tracks", ()))
    return out


def _track_clips(objects: Objects, track: dict) -> list[str]:
    """The clips a track plays: the clips of its TimelineClips' AnimationPlayableAssets and its infinite clip."""
    out = []
    for c in track.get("m_Clips", ()):
        a = ref_id(c.get("m_Asset"))
        clip = ref_id(objects.json(a).get("m_Clip")) if a is not None else None
        if clip is not None:
            out.append(clip)
    infinite = ref_id(track.get("m_AnimClip"))
    return out + ([infinite] if infinite is not None else [])


def _band(objects: Objects, band: int, scene: list[str], plays: dict) -> dict:
    """The `bands` entry of band `band` (module documentation); adds what each track plays to `plays` {prefab
    GameObject id: {Animator node path: clip ids}}."""
    key = TIMELINE_KEY.format(band=band)
    gid = key_object(objects, key, "GameObject")
    assets = [
        ref_id(c["fields"].get("m_PlayableAsset")) for _, c in Prefab(objects.json(gid)).components("PlayableDirector")
    ]
    if len(assets) != 1 or assets[0] is None:
        raise ValueError(f"{key}: {len(assets)} PlayableDirectors, expected one playing a TimelineAsset")
    owners = {"scene": scene, "timeline": [gid]}
    tracks = {}
    for tid, track in _tracks(objects, assets[0]):
        name, clips = track.get("m_Name"), _track_clips(objects, track)
        if name not in TIMELINE_TRACK_ANIMATORS:
            if clips:
                raise ValueError(f"{key}: track {name} plays clips on no known Animator")
            continue
        where, cls, field = TIMELINE_TRACK_ANIMATORS[name]
        owner, comp = _one_component(objects, owners[where], cls)
        hit = component_ref(objects, owner, comp["fields"].get(field))
        if hit is None or component_class(hit[2]) != "Animator":
            raise ValueError(f"{key}: {cls}.{field} of track {name} is no Animator")
        path = objects.json(hit[0])["nodes"][hit[1]]["path"]
        tracks[tid] = {"name": name, "prefab": hit[0], "animator": path}
        plays.setdefault(hit[0], {}).setdefault(path, []).extend(clips)
    return {
        "timeline": gid,
        "background": key_object(objects, BACKGROUND_KEY.format(band=band), "Sprite"),
        "tracks": tracks,
    }


def _bar_line_view(objects: Objects, scene: list[str]) -> str:
    """The prefab GameObject of the bar line view: BAR_LINE_CHAIN from the scene's LiveAllNoteView."""
    path, cls = NOTE_VIEW
    owners = [g for g in scene if any(n["path"] == path for n in objects.json(g)["nodes"])]
    if len(owners) != 1:
        raise ValueError(f"{SCENE_KEY}: {len(owners)} hierarchies with a node at {path}")
    gid, comp = _one_component(objects, owners, cls)
    node = 0
    for field, want in BAR_LINE_CHAIN:
        hit = component_ref(objects, gid, comp["fields"].get(field))
        if hit is None or component_class(hit[2]) != want:
            raise ValueError(f"{SCENE_KEY}: {cls}.{field} is no {want}")
        (gid, node, comp), cls = hit, want
    if node != 0:
        raise ValueError(f"{SCENE_KEY}: the bar line view is not the root of its prefab")
    return gid


def scene(objects: Objects, record: dict) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(scene.json, the object ids the player reads, {object id: the roles of its files it reads}) of the scene
    product (module documentation; `record`: scene_record)."""
    roots = [o for o in objects.key(SCENE_KEY) if o in objects]
    gos = [o for o in roots if objects.info(o).get("class") == "GameObject" and has_json(objects, o)]
    render = [o for o in roots if objects.info(o).get("class") == "RenderSettings"]
    if len(render) != 1:
        raise ValueError(f"{SCENE_KEY}: {len(render)} RenderSettings")
    plays: dict[str, dict[str, list[str]]] = {}
    bands = {
        str(r["_id"]): _band(objects, r["_id"], gos, plays)
        for r in sorted(record["MasterBand"], key=lambda r: r["_id"])
        if objects.key(TIMELINE_KEY.format(band=r["_id"]))
    }
    graphics = {name: builtin_root(objects, cls) for name, cls in GRAPHICS.items()}
    always = (ref_id(r) for r in objects.json(graphics["graphics"]).get("m_AlwaysIncludedShaders", ()))
    shaders, roles = shader_table(objects, found=[o for o in always if o is not None])
    missing = [n for n in PLAYER_SHADERS if n not in shaders]
    if missing:
        raise ValueError(f"{SCENE_KEY}: a lookup by name finds no shader {', '.join(missing)}")
    skins = sorted(record["MasterLiveLaneSkin"], key=lambda r: r["_id"])
    doc = {
        "format": SCENE_FORMAT,
        "scene": gos,
        "renderSettings": render[0],
        "graphics": graphics,
        "laneSkins": {
            r["_assetName"]: {
                p: key_object(objects, lane_skin_key(r["_assetName"], p), "Sprite") for p in LANE_SKIN_PARTS
            }
            for r in skins
        },
        "prefabs": {
            "laneLine": key_object(objects, LANE_LINE_KEY, "GameObject"),
            "judgeEffectView": key_object(objects, JUDGE_EFFECT_KEY, "GameObject"),
            "barLineView": _bar_line_view(objects, gos),
        },
        "spriteAssets": {name: key_object(objects, key, cls) for name, (key, cls) in SPRITE_ASSETS.items()},
        "bands": bands,
        "settings": scene_settings(record),
        "bindings": animator_bindings(objects, plays),
        "shaders": shaders,
    }
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    return doc, keep, roles


# --- extraction -------------------------------------------------------------
def _component(nodes: list, cls: str) -> dict:
    hit = [c for n in nodes for c in n["components"] if c.get("class", c["type"]) == cls]
    if len(hit) != 1:
        raise KeyError(f"{len(hit)} {cls} components")
    return hit[0]


def bind_timeline(ex: Exporter, doc: dict, key: str) -> dict:
    """Bind the clips of the start timeline's tracks to the Animators the game binds them to
    (TIMELINE_TRACK_ANIMATORS): binding paths resolve against that GameObject's subtree, and each track records
    `boundAnimator` {key, gameObject, field}. Returns binding counts per track."""
    tl = doc["assets"]["startTimeline"]
    env, tl_graph = ex.load(key)
    scene_graph = ex.load(SCENE_KEY)[1]
    dir_json = _component(tl["nodes"], "PlayableDirector")
    dir_path = next(n["path"] for n in tl["nodes"] if any(c is dir_json for c in n["components"]))
    directors = [
        o
        for o in env.objects
        if o.type.name == "PlayableDirector"
        and tl_graph.path(tl_graph.tf_of_go[o.read_typetree()["m_GameObject"]["m_PathID"]]) == dir_path
    ]
    if len(directors) != 1:
        raise RuntimeError(f"{key}: {len(directors)} PlayableDirectors at {dir_path}")
    asset_o = ex.deref(directors[0], directors[0].read_typetree()["m_PlayableAsset"])
    tracks_json = {t["m_Name"]: t for t in dir_json["m_PlayableAsset"]["m_Tracks"]}
    counts = {}
    for _track_o, tt, clips in ex.timeline_tracks(asset_o):
        name = tt["m_Name"]
        if name not in TIMELINE_TRACK_ANIMATORS:
            if clips:
                raise RuntimeError(f"{key}: track {name} with clips has no known binding")
            continue
        where, cls, field = TIMELINE_TRACK_ANIMATORS[name]
        nodes, graph, owner = (
            (doc["scene"]["nodes"], scene_graph, SCENE_KEY) if where == "scene" else (tl["nodes"], tl_graph, key)
        )
        ref = _component(nodes, cls)[field]
        if not ref or ref.get("component") != "Animator":
            raise RuntimeError(f"{cls}.{field}: {ref}")
        root = ref["gameObject"]
        tracks_json[name]["boundAnimator"] = {"key": owner, "gameObject": root, "field": f"{cls}.{field}"}
        counts[name] = [ex.bind_clip(c, graph, root) for c in clips]
    return counts


def lane_skin(rows: dict) -> str:
    """Lane skin asset name: option LiveSkinId (305) -> MasterLiveLaneSkin row of that id (LaneSkinLoadStep)."""
    sid = int(rows["optionDefaultsPreset1"]["LiveSkinId"]["value"])
    hit = [r for r in rows["liveLaneSkin"] if r["_id"] == sid]
    if len(hit) != 1:
        raise KeyError(f"MasterLiveLaneSkin: {len(hit)} rows with id {sid} (option LiveSkinId)")
    return hit[0]["_assetName"]


def _texture_records(v, out: list) -> None:
    if isinstance(v, dict):
        if isinstance(v.get("texture"), str) and v["texture"].startswith("textures/"):
            out.append(v)
        for x in v.values():
            _texture_records(x, out)
    elif isinstance(v, list):
        for x in v:
            _texture_records(x, out)


def _drop_scene_textures(doc: dict, base: Path, unlink: bool = True) -> list[str]:
    """The texture files only the undrawn part of the scene prefab references (SCENE_DRAWN_TEXTURE_NODES): their
    records marked "exported": false, the files deleted (`unlink`; else the caller does not write them). Returns
    the files."""
    keep: list = []
    for k, v in doc.items():
        if k != "scene":
            _texture_records(v, keep)
    for n in doc["scene"]["nodes"]:
        if n["path"] in SCENE_DRAWN_TEXTURE_NODES:
            _texture_records(n["components"], keep)
    kept = {r["texture"] for r in keep}
    scene: list = []
    _texture_records(doc["scene"], scene)
    dropped = sorted({r["texture"] for r in scene} - kept)
    for r in scene:
        if r["texture"] in dropped:
            r["exported"] = False
    if unlink:
        for rel in dropped:
            (base / rel).unlink()
    return dropped


class SharedSceneMismatch(RuntimeError):
    """A music's scene cannot be composed from its band's part (see music_part); export it whole."""


def _export(
    cat,
    player,
    base: Path,
    rows: dict | None,
    band: int,
    skin: str,
    extra_keys: dict | None,
    slice_: dict | None,
    jacket: str | None,
):
    """The export sequence of extract (jacket None: without the jacket sprite, the slice and the music rows left as
    placeholders for music_part). -> (exporter, doc, shader index, shader summary, timeline counts, dropped)."""
    from ..unity.export import Exporter

    ex = Exporter(cat, base, player=player, clip_format="livescene", defer_writes=True)
    graphics = player.graphics()
    doc: dict = {"player": graphics}
    doc["slice"] = slice_
    doc["scene"] = ex.prefab(SCENE_KEY)
    doc["laneSkin"] = {part: ex.key_sprite(f"Live/Lane/{skin}/{part}") for part in LANE_SKIN_PARTS}
    keys = dict(ASSET_KEYS)
    keys.update(extra_keys or {})
    doc["assets"] = {name: ex.export_key(key.format(band=band)) for name, key in keys.items()}
    timeline = bind_timeline(ex, doc, keys["startTimeline"].format(band=band))
    doc["sprites"] = {
        name: ex.key_sprite(key.format(band=band, jacket=jacket))
        for name, key in SPRITE_KEYS.items()
        if jacket is not None or name != "jacket"
    }
    if rows:
        doc["master"] = rows if jacket is not None else None
        opts = {k: v["value"] for k, v in rows["optionDefaultsPreset1"].items()}
        doc["derived"] = {
            "lane": derive_lane(doc["scene"]["nodes"], opts),
            "fovRule": {
                "refFov": REF_FOV,
                "refAspect": REF_ASPECT,
                "source": "LiveGameView.FullInitialize / CalcVerticalFovKeepingHorizontal",
                "examples": {
                    f"{w}x{h}": vertical_fov(w, h) for w, h in ((2340, 1080), (1920, 1080), (1440, 1080), (2048, 1536))
                },
            },
        }
    for name in PLAYER_SHADERS:
        ex.add_shader(player.shader(name))
    rl = graphics["pipelines"][graphics["defaultPipeline"]]["m_RendererDataList"]
    renderers = [rl[i] for i in LIVE_RENDERERS]
    for rn in renderers:
        for o in player.renderer_shaders(rn):
            ex.add_shader(o)
    doc["postTextures"] = {}
    for rn in renderers:
        if graphics["renderers"][rn].get("postProcessData"):
            doc["postTextures"][rn] = {
                "filmGrainTex": [ex.texture(o) for o in player.post_textures(rn, "filmGrainTex")]
            }
    index, summary = ex.dump_shaders(base / "shaders")
    doc["shaders"] = {"index": "shaders/shaders.json", "names": summary["names"], "cameraRenderers": renderers}
    doc["convention"] = (
        "Unity space (left-handed, Y up); transforms are local TRS; "
        "quaternions (x,y,z,w); colours in the project's Gamma space"
    )
    ex.resolve_pending()  # bindings whose target hierarchy / component class was exported after the clip
    dropped = _drop_scene_textures(doc, base, unlink=False)
    ex.write_textures(skip=dropped)
    return ex, doc, index, summary, timeline, dropped


def _summary(doc: dict, index: list, summary: dict, textures: int, dropped: list, timeline) -> dict:
    gles3 = sum(1 for r in index for v in r["variants"] if v["platform"] == "gles3")
    return {
        "scene": "livescene/scene.json",
        "nodes": len(doc["scene"]["nodes"]),
        "assets": list(doc["assets"]),
        "textures": textures - len(dropped),
        "droppedTextures": len(dropped),
        "shaders": summary["count"],
        "variants": summary["variants"],
        "gles3Variants": gles3,
        "timelineBindings": timeline,
    }


def extract(
    cat,
    player,
    out_dir: Path,
    master: Path | None = None,
    music_id: int = 100001,
    band: int | None = None,
    band_choice: dict | None = None,
    extra_keys: dict | None = None,
) -> dict:
    """Write <out_dir>/livescene/scene.json (+ textures/, shaders/) and return a summary.

    `band`: the band of the LightWeight background (LightWeightBackgroundLoadStep.<LoadAsync>d__3:
    SelfMemberList[2].BandID) and of the start timeline (LiveResourceBandResolver.Resolve). The game
    takes it from the player's deck; the caller chooses it (live.resolve_band) and `band_choice` records how.
    `master` (decoded master JSON dir) adds the music/option rows and the derived lane geometry; with `master`
    the band is required. Without `master`: band 1 unless given, lane skin skin001."""
    out_dir = Path(out_dir)
    rows = master_rows(master, music_id) if master else None
    if band is None:
        if rows:
            raise ValueError(
                f"music {music_id}: no band given; the LightWeight background and the start timeline "
                "take the band of the deck centre (SelfMemberList[2]), see live.resolve_band"
            )
        band = 1
    skin = lane_skin(rows) if rows else "skin001"
    base = out_dir / "livescene"
    base.mkdir(parents=True, exist_ok=True)
    slice_ = {"musicId": music_id, "band": band, "laneSkin": skin}
    if band_choice:
        slice_["bandChoice"] = band_choice
    jacket = rows["liveMusic"]["_jacketAssetName"] if rows else "jkt_001_100001"
    ex, doc, index, summary, timeline, dropped = _export(
        cat, player, base, rows, band, skin, extra_keys, slice_, jacket
    )
    write_json(base / "scene.json", doc)
    return _summary(doc, index, summary, len(ex.textures), dropped, timeline)


BAND_DOC = "band.json"


def band_part(
    cat, player, out_dir: Path, master: Path, music_id: int, band: int, extra_keys: dict | None = None
) -> dict:
    """What `extract` writes for any music of `band`: <out_dir>/livescene/ (textures, shaders; no scene.json) and
    <out_dir>/band.json (the document with the slice, the jacket sprite and the music rows left out). `music_id`:
    any music of the band (the option rows it reads are the same for every music)."""
    out_dir = Path(out_dir)
    rows = master_rows(master, music_id)
    base = out_dir / "livescene"
    base.mkdir(parents=True, exist_ok=True)
    ex, doc, index, summary, timeline, dropped = _export(
        cat, player, base, rows, band, lane_skin(rows), extra_keys, None, None
    )
    part = {
        "band": band,
        "doc": doc,
        "summary": _summary(doc, index, summary, len(ex.textures), dropped, timeline),
        "textures": sorted({r["texture"] for r in _records(doc["scene"])}),
    }
    write_json(out_dir / BAND_DOC, part)
    return part


def _records(v) -> list:
    out: list = []
    _texture_records(v, out)
    return out


def music_part(
    cat, player, out_dir: Path, part: dict, master: Path, music_id: int, band_choice: dict | None = None
) -> dict:
    """<out_dir>/livescene/scene.json of one music from its band's part (band_part; the part's livescene/ files
    already in <out_dir>/livescene): the document extract writes. Raises SharedSceneMismatch when the jacket's
    bundle closure holds shaders or a hierarchy (extract would dump or resolve against them) or the jacket texture
    is one of the scene's (extract would keep it)."""
    from ..unity.export import Exporter

    out_dir = Path(out_dir)
    band = part["band"]
    rows = master_rows(master, music_id)
    doc = json.loads(json.dumps(part["doc"]))
    key = SPRITE_KEYS["jacket"].format(band=band, jacket=rows["liveMusic"]["_jacketAssetName"])
    ex = Exporter(cat, out_dir / "livescene", player=player, clip_format="livescene")
    env, _ = ex.load(key)
    kinds = {o.type.name for o in env.objects} & {"Shader", "GameObject", "Transform", "RectTransform"}
    if kinds:
        raise SharedSceneMismatch(f"{key}: its closure holds {', '.join(sorted(kinds))}")
    jacket = ex.key_sprite(key)
    if {r["texture"] for r in _records(jacket)} & set(part["textures"]):
        raise SharedSceneMismatch(f"{key}: its texture is one of the scene's")
    slice_ = {"musicId": music_id, "band": band, "laneSkin": lane_skin(rows)}
    if band_choice:
        slice_["bandChoice"] = band_choice
    doc["slice"] = slice_
    doc["sprites"]["jacket"] = jacket
    doc["master"] = rows
    write_json(out_dir / "livescene" / "scene.json", doc)
    s = dict(part["summary"])
    s["textures"] += len(ex.textures)
    return s
