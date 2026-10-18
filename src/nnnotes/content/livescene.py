"""Live scene products assembled from shared prefab, shader and master records."""

from __future__ import annotations

from .livenotes import BAR_LINE_CHAIN, NOTE_VIEW, animator_bindings
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


# --- derived lane geometry (mirrors the game functions named per entry) ---
# LiveLaneLineView's constant tables: split border main / sub counts per LiveLaneSplitCountType
# (None, Lane4, Lane6, Lane8, Lane12)


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
