"""Live (rhythm game) stage/render data -> livescene/scene.json + textures + shaders.

What exists on the live screen before the first note:

  player          colour space, quality levels, URP pipelines, renderer data (player.py)
  scene           the addressable scene EmbScene/Live as a node list (Live prefab instance with
                  cameras, render canvas, background view and UI canvases; LiveGameView with
                  the lane camera, lane, note/effect containers; LiveStartTimelineDirector)
  laneSkin        lane skin sprites (lane_base, lane_tap_area, out_side_line) of the LiveSkinId option's
                  MasterLiveLaneSkin row
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
profile is part of the scene (LiveEffectCamera's Volume).

Objects are exported with export.Exporter in its "livescene" clip format (clips inlined once,
bindings with crc32 keys, path candidates and curve counts).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .export import Exporter
from .jsonio import write_json
from .score import master_table


# --- keys -------------------------------------------------------------------
SCENE_KEY = "EmbScene/Live"
LANE_SKIN_PARTS = ("lane_base", "lane_tap_area", "out_side_line")
# asset keys exported whole ({band} = the band of the LightWeight background and the start timeline). The timeline
# prefab's PlayableDirector carries its TimelineAsset (Band/{b}/timeline/live_start_timeline_asset) inline.
ASSET_KEYS = {
    "startTimeline": "Band/{band}/timeline/live_start_playable_timeline",   # LiveStartTimelineLoadStep
    "laneLinePrefab": "EmbLive/Prefabs/LiveGame/lane_line",          # LiveLaneLineView._linePrefab (inner lines)
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
PLAYER_SHADERS = ("Hidden/CompositeRectBlit", "Hidden/SimpleGaussianBlur", "Sirius/Live/LiveSofdecPrimeYuv",
                  "Sirius/Live/LiveSofdecPrimeYuv_Android", "UI/Default")
# URP renderer indices used by the scene's live cameras
LIVE_RENDERERS = (2, 3, 4, 5)
# App.Options.OptionItemType names (MasterOptionDefault / MasterOptionRange _optionItemType)
OPTION_ITEM_TYPES = {1: "NoteSpeed", 3: "ChartPosition", 4: "MirrorChart", 6: "LiveQuality",
                     100: "FastSlowDisplay", 101: "PerfectFastSlowDisplay", 102: "JudgeOffsetMsDisplay",
                     103: "JudgeResultPositionType", 104: "JudgePosition", 105: "JudgePositionDisplay",
                     106: "SlideOpacity", 107: "GuideOpacity", 108: "SimultaneousLineDisplay",
                     109: "MeasureLineDisplay", 110: "MvQuality", 112: "LiveSkillEffect", 113: "ComboEffect",
                     115: "StageEffect", 117: "PreLiveSimpleOption", 119: "GekisouEffect", 200: "ScreenMode",
                     201: "BackgroundBrightness", 202: "MvModeBrightness", 203: "BackgroundSwitch",
                     205: "SkillEffectDisplay", 206: "ComboCountDisplay", 207: "JudgeDetailDisplay",
                     208: "ContinuationEffectDisplay", 300: "LaneOpacity", 301: "GuidelineOpacity",
                     302: "GuidelineCount", 305: "LiveSkinId", 306: "NoteDesignId", 307: "NoteEffectId",
                     308: "NoteStartPosition", 309: "LiveSkillActivationPositionDisplay",
                     310: "GekisouSimpleEffect", 600: "QualitySetting"}
# option items the render path reads
OPTION_ITEMS = {k: OPTION_ITEM_TYPES[k] for k in (3, 6, 104, 105, 106, 107, 108, 109, 110, 115, 200, 201, 202,
                                                  203, 300, 301, 302, 305, 306, 307, 308, 600)}


def master_rows(master: Path, music_id: int) -> dict:
    music = next(r for r in master_table(master, "MasterLiveMusic") if r["_id"] == music_id)
    opts = {r["_optionItemType"]: r["_valueString"] for r in master_table(master, "MasterOptionDefault")
            if r["_presetId"] == 1 and r["_optionItemType"] in OPTION_ITEMS}
    return {
        "liveMusic": music,
        "liveQualitySettings": master_table(master, "MasterLiveQualitySettings"),
        "liveLaneSkin": master_table(master, "MasterLiveLaneSkin"),
        "liveStageVideo": master_table(master, "MasterLiveStageVideo"),
        "optionDefaultsPreset1": {OPTION_ITEMS[k]: {"id": k, "value": v} for k, v in sorted(opts.items())},
        "optionRanges": {OPTION_ITEMS[r["_optionItemType"]]: [r["_minValue"], r["_maxValue"]]
                         for r in master_table(master, "MasterOptionRange") if r["_optionItemType"] in OPTION_ITEMS},
    }


# --- derived lane geometry (mirrors the game functions named per entry) ---
# LiveLaneLineView's constant tables: split border main / sub counts per LiveLaneSplitCountType
# (None, Lane4, Lane6, Lane8, Lane12)
SPLIT_MAIN = (0, 4, 6, 8, 12)
SPLIT_SUB = (12, 12, 12, 0, 0)
LANE_COUNT = 24            # LiveDataCreator.CreateBootData passes 0x18
# LiveBootDataCreator.CreateViewData: arguments of LiveSettingCreator.CreateLaneViewSettings
LANE_VIEW = {"size": (1920.0, 1080.0), "topRange": 0.05, "bottomRange": 1.0,
             "judgementScreenBottomPosition": 2.24, "topPosition": 0.0}
REF_ASPECT = 1.7777778     # LiveGameView.FullInitialize
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
    lv = _comp(_node(nodes, "LiveGameView/root/LiveGameLane"), "LiveLaneView")
    llv = _comp(_node(nodes, "LiveGameView/root/LiveGameLane/lines"), "LiveLaneLineView")
    jr = _node(nodes, "LiveGameView/root/LiveGameLane/judgement_root")
    n = LANE_COUNT
    width = float(np.float32(lv["_laneWidth"]))          # double field, passed on as (float)
    length = lv["_lineLength"]
    unit = width / n
    judge_z = jr["localPosition"]["z"]                   # parents are identity: world z of position 0
    split = int(options["GuidelineCount"])
    main, sub = SPLIT_MAIN[split], SPLIT_SUB[split]
    settings = {s["LineType"]: s for s in llv["_settings"]}
    lines = []
    for i in range(n + 1):                               # LiveLaneLineView.UpdateProperties
        if i == 0 or i == n:
            t = 1                                        # GetSettings: OutSide
        elif main and i % (n // main) == 0:
            t = 0                                        # Normal
        elif sub and i % (n // sub) == 0:
            t = 2                                        # Space
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
        lines.append({"index": i, "active": True, "type": ("Normal", "OutSide", "Space")[t],
                      "start": [x, 0.0, z0], "end": [x, 0.0, z0 + ln],
                      "startWidth": st["LineWidthFrom"], "endWidth": to_w,
                      "gradient": st["LineColor"], "alphaControlled": t != 1})
    return {
        "laneCount": n, "laneWidth": width, "unitWidth": unit, "lineLength": length,
        "judgementZ": judge_z,
        "unitCentersX": [unit * (i + 0.5) - width * 0.5 for i in range(n)],
        "spawnRoots": [[unit * (i + 0.5) - width * 0.5, 0.0, length] for i in range(n)],
        "judgementPositions": [[unit * (i + 0.5) - width * 0.5, 0.0, judge_z] for i in range(n)],
        "splitCountType": split, "splitBorderMainCount": main, "splitBorderSubCount": sub,
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
            "screenPlane": "LiveSettingCreator.CreateLaneViewSettings, JudgementPositionLogic.GetJudgementPositions, LiveViewUtility.GetNoteSpawnPosition",
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
    return {"vertices": [lt, rt, lb, rb], "judgementPositions": jp, "spawnPosition": [sx, c1 + s1 * sx],
            "pixelsPerUnit": 100.0}


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
    directors = [o for o in env.objects if o.type.name == "PlayableDirector"
                 and tl_graph.path(tl_graph.tf_of_go[o.read_typetree()["m_GameObject"]["m_PathID"]]) == dir_path]
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
        nodes, graph, owner = ((doc["scene"]["nodes"], scene_graph, SCENE_KEY) if where == "scene"
                               else (tl["nodes"], tl_graph, key))
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


def _drop_scene_textures(doc: dict, base: Path) -> list[str]:
    """Delete the texture files only the undrawn part of the scene prefab references (SCENE_DRAWN_TEXTURE_NODES)
    and mark their records "exported": false. Returns the deleted files."""
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
    for rel in dropped:
        (base / rel).unlink()
    return dropped


def extract(cat, player, out_dir: Path, master: Path | None = None, music_id: int = 100001,
            band: int | None = None, band_choice: dict | None = None, extra_keys: dict | None = None) -> dict:
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
            raise ValueError(f"music {music_id}: no band given; the LightWeight background and the start timeline "
                             "take the band of the deck centre (SelfMemberList[2]), see live.resolve_band")
        band = 1
    skin = lane_skin(rows) if rows else "skin001"
    base = out_dir / "livescene"
    base.mkdir(parents=True, exist_ok=True)
    ex = Exporter(cat, base, player=player, clip_format="livescene")
    graphics = player.graphics()
    doc: dict = {"player": graphics}
    doc["slice"] = {"musicId": music_id, "band": band, "laneSkin": skin}
    if band_choice:
        doc["slice"]["bandChoice"] = band_choice
    doc["scene"] = ex.prefab(SCENE_KEY)
    doc["laneSkin"] = {part: ex.key_sprite(f"Live/Lane/{skin}/{part}") for part in LANE_SKIN_PARTS}
    keys = dict(ASSET_KEYS)
    keys.update(extra_keys or {})
    doc["assets"] = {name: ex.export_key(key.format(band=band)) for name, key in keys.items()}
    timeline = bind_timeline(ex, doc, keys["startTimeline"].format(band=band))
    jacket = rows["liveMusic"]["_jacketAssetName"] if rows else "jkt_001_100001"
    doc["sprites"] = {name: ex.key_sprite(key.format(band=band, jacket=jacket))
                      for name, key in SPRITE_KEYS.items()}
    if rows:
        doc["master"] = rows
        opts = {k: v["value"] for k, v in rows["optionDefaultsPreset1"].items()}
        doc["derived"] = {
            "lane": derive_lane(doc["scene"]["nodes"], opts),
            "fovRule": {"refFov": REF_FOV, "refAspect": REF_ASPECT,
                        "source": "LiveGameView.FullInitialize / CalcVerticalFovKeepingHorizontal",
                        "examples": {f"{w}x{h}": vertical_fov(w, h)
                                     for w, h in ((2340, 1080), (1920, 1080), (1440, 1080), (2048, 1536))}}}
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
            doc["postTextures"][rn] = {"filmGrainTex": [ex.texture(o) for o in player.post_textures(rn, "filmGrainTex")]}
    index, summary = ex.dump_shaders(base / "shaders")
    doc["shaders"] = {"index": "shaders/shaders.json", "names": summary["names"], "cameraRenderers": renderers}
    doc["convention"] = ("Unity space (left-handed, Y up); transforms are local TRS; "
                         "quaternions (x,y,z,w); colours in the project's Gamma space")
    ex.resolve_pending()        # bindings whose target hierarchy / component class was exported after the clip
    dropped = _drop_scene_textures(doc, base)
    write_json(base / "scene.json", doc)
    gles3 = sum(1 for r in index for v in r["variants"] if v["platform"] == "gles3")
    return {"scene": "livescene/scene.json", "nodes": len(doc["scene"]["nodes"]),
            "assets": list(doc["assets"]), "textures": len(ex.textures) - len(dropped), "droppedTextures": len(dropped), "shaders": summary["count"],
            "variants": summary["variants"], "gles3Variants": gles3, "timelineBindings": timeline}
