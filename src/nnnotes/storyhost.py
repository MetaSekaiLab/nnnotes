"""The host screen of an Overlay story (MasterAdv._playbackMode 1) -> <story>/host/ and <language>/ui/simple/.

The game plays these episodes with App.Adv.SimpleAdvPlayer inside the screen that starts them, not in the ADV scene:
SimpleAdvView lays the characters out in the slots of the host's SimpleAdvLayoutRoot (each drawn by its own
CameraTargetRenderer) and attaches the UISimpleAdvTalkWindow under the root's TalkRoot. The host is the story group
of the episode: a tap talk (MasterStoryHomeSpotTapTalkEpisode) or an area talk (MasterHomeSpot._advId) of a home
spot (SpotManager), or the reward phase of a live result (LiveResultDisplayPresenter).

  host/host.json       kind, the capture camera of a slot (CameraTargetRenderer prefab), the SimpleAdv root and its
                       SimpleAdvLayoutRoot fields, the sequences the host screen has played when the talk starts,
                       and per kind:
                         home       spot.json + Spine files (spot.extract) and room.glb + room.json
                                    (room.extract_room) under host/spot/; per placed glb node its object path, its
                                    state after SpotBackground.Prepare and the SpotSceneRoot.SetObject "floor" render
                                    queue; the room materials as saved; the "Spot" scene's object root, lights and
                                    volumes; the background Volume; the Spine materials; the blur of renderer 0's
                                    UIRendererFeature; the situation's ambient sound; shaders in host/shaders/
                         afterlive  the result background node and sprite, the reward panel, the substitutes
  host/ui/ui.json      the host UI in the advui record format (no text nodes)
  ui/simple/ui.json    per language: UISimpleAdvTalkWindow (and UISystemMessageWidget for an area talk) in the
                       advui record format with text records, fonts.json and glyph pages (storyfonts format, open
                       fonts)

Same inputs give the same bytes.
"""
from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

import numpy as np
import UnityPy

from . import advscene, advui, languages, master, room, spot, storyfonts, textstyle, tmpfont
from . import shader as shader_mod
from .export import RECT_PROPS, Exporter, TexelPacker, packed_png, streamed_frames
from .jsonio import dumps, write_json
from .unity import SceneGraph, deref, mesh_arrays, script_class

FORMAT = "ournotes.story-host/1"
OVERLAY = 1                                   # MasterAdv._playbackMode of the episodes SimpleAdvPlayer plays
HOST_DIR = "host"
HOST_DOC = f"{HOST_DIR}/host.json"
HOST_UI_DOC = f"{HOST_DIR}/ui/ui.json"
SIMPLE_DIR = "simple"
SIMPLE_DOC = f"ui/{SIMPLE_DIR}/ui.json"
FONT_MODES = ("open", "game")
TMP_STUBS = ("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet")
# game addresses; a missing one is loaded from "Emb" + address (emb_key)
ADDRESSES = {
    "cameraTarget": "Common/Prefab/CameraTargetRenderer",
    "talkWindow": "UI/Prefab/Parts/Adv/Talk/UISimpleAdvTalkWindow",
    "systemMessage": "UI/Prefab/UISystemMessageWidget",
    "spotWidget": "UI/Prefab/UISpotWidget",
    "spotLetterbox": "UI/Prefab/Parts/Spot/UISpotLetterbox",
    "spotScene": "Scene/Spot",
    "resultWidget": "UI/Prefab/UILiveResultWidget",
    "resultBackground": "UI/Prefab/UILiveResultBackgroundWidget",
    "resultBackgroundSprite": "LowImage/LiveResult/live_result_background",
}
KIND_KEYS = {"home": ("spotWidget", "spotLetterbox", "spotScene"),
             "afterlive": ("resultWidget", "resultBackground", "resultBackgroundSprite")}
# story group kind (storysite.story_groups) -> (host kind, talk)
HOST_GROUPS = {"spotTalk": ("home", "tap"), "spot": ("home", "area"), "liveResult": ("afterlive", None)}
SUBSTITUTES = ["musicResultPanel", "rewardItems", "memberCard", "navigation", "gradeUpEffect"]
CLONE = "(Clone)"                             # Object.Instantiate's name suffix, removed by SpotBackground.Prepare
FLOOR_NAME, FLOOR_EXCLUDE, FLOOR_QUEUE = "floor", "shadow", 2000    # SpotSceneRoot.SetObject
BLUR_FEATURE = "Fwk.UI.Rendering.UIRendererFeature"
REWARD_PANEL = "RewardPanel"
COMPONENT_HEADER = ("type", "class", "m_Name")
NO_EPISODE = {"text": {}, "commands": []}    # the episode argument of storyfonts.shown_characters for a UI alone
DOTWEEN_FIELDS = ("defaultEaseType", "defaultUpdateType", "defaultTimeScaleIndependent", "timeScale",
                  "defaultEaseOvershootOrAmplitude", "defaultEasePeriod")


# ---------------------------------------------------------------- host, keys
def host_of(groups: list[dict]) -> dict:
    """The host of an Overlay story from its story groups (storysite.story_groups): a spotTalk group is a home tap
    talk (SpotManager.OnTalkCharacter: spot and tapped SpotCharacter id), a spot group a home area talk
    (SpotManager.PlayAreaTalkAsync), a liveResult group the live result reward phase. Exactly one such group."""
    hits = [g for g in groups if g.get("kind") in HOST_GROUPS]
    if len(hits) != 1:
        raise ValueError(f"an Overlay story needs exactly one spotTalk, spot or liveResult group, not {len(hits)} "
                         f"(groups: {[g.get('kind') for g in groups]})")
    g = hits[0]
    kind, talk = HOST_GROUPS[g["kind"]]
    out = {"kind": kind, "group": {"kind": g["kind"], "id": g["id"]}}
    if kind == "home":
        chars = g.get("characters") or []
        out.update(spotId=g["spot"]["id"], talk=talk,
                   characterId=chars[0]["id"] if talk == "tap" and chars else None)
    return out


def emb_key(cat, address: str) -> str:
    """The catalog key of a game address: the address itself, else the embedded copy "Emb" + address."""
    for key in (address, f"Emb{address}"):
        if cat.has(key):
            return key
    raise KeyError(f"{address}: neither it nor Emb{address} is in the catalog")


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _trs(node: dict) -> dict:
    return {k: node[k] for k in ("localPosition", "localRotation", "localScale")}


def _first(nodes: list[dict], path: str) -> dict:
    hits = [n for n in nodes if n["path"] == path]
    if len(hits) != 1:
        raise RuntimeError(f"{path}: {len(hits)} nodes")
    return hits[0]


# ---------------------------------------------------------------- records
def camera_target(nodes: list[dict]) -> dict:
    """cameraTarget: the CameraTargetRenderer prefab (SimpleAdvView slot capture): the transforms of the parent of
    `_stage` and `_captureCamera` (Container), of the stage and of the capture camera with its Camera and
    UniversalAdditionalCameraData settings (the slot's RenderTexture, layer and stage offset are code)."""
    crt = advui._component(nodes[0], ("CameraTargetRenderer",))
    if crt is None:
        raise RuntimeError(f"{nodes[0]['path']}: no CameraTargetRenderer")
    stage, cam = advui._ref_path(crt["_stage"]), advui._ref_path(crt["_captureCamera"])
    container = _parent(stage)
    if not stage or not cam or _parent(cam) != container:
        raise RuntimeError("CameraTargetRenderer: stage and capture camera not under one parent")
    node = _first(nodes, cam)
    c = [x for x in node["components"] if x["type"] == "Camera"]
    d = [x for x in node["components"] if x.get("class") == "UniversalAdditionalCameraData"]
    if len(c) != 1 or len(d) != 1:
        raise RuntimeError(f"{cam}: {len(c)} Camera, {len(d)} UniversalAdditionalCameraData")
    c, d = c[0], d[0]
    return {"container": {"path": container, **_trs(_first(nodes, container))},
            "stage": {"path": stage, **_trs(_first(nodes, stage))},
            "camera": {"path": cam, "localPosition": node["localPosition"], "localRotation": node["localRotation"],
                       "fieldOfView": c["field of view"], "near": c["near clip plane"], "far": c["far clip plane"],
                       "orthographic": bool(c["orthographic"]), "orthographicSize": c["orthographic size"],
                       "clearFlags": c["m_ClearFlags"], "backgroundColor": c["m_BackGroundColor"],
                       "hdr": bool(c["m_HDR"]), "allowMSAA": bool(c["m_AllowMSAA"]),
                       "rendererIndex": d["m_RendererIndex"], "renderPostProcessing": bool(d["m_RenderPostProcessing"]),
                       "antialiasing": d["m_Antialiasing"]}}


def _fields(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in COMPONENT_HEADER}


def layout_root(nodes: list[dict], path: str) -> dict:
    """layoutRoot: the serialized fields of the SimpleAdvLayoutRoot on the node at `path` (the profile
    SimpleAdvLayoutRoot.CreateProfile makes)."""
    c = advui._component(_first(nodes, path), ("SimpleAdvLayoutRoot",))
    if c is None:
        raise RuntimeError(f"{path}: no SimpleAdvLayoutRoot")
    return _fields(c)


def kept(path: str, subtrees=(), singles=()) -> bool:
    """A node kept in a host UI: inside one of `subtrees` (root included), one of `singles`, or an ancestor of
    either."""
    return (any(path == s or path.startswith(s + "/") for s in subtrees)
            or any(s == path or s.startswith(path + "/") for s in (*subtrees, *singles)))


# serialized references kept as node paths: component class -> (record key, fields)
REF_RECORDS = {
    "UIAdvTalkWindow": ("talkWindowRefs", ("SpeakerText", "TalkText", "TalkArea", "_speakerNameObjects",
                                           "_talkNextIndicator", "_talkBackground", "_autoIcon", "_fastIcon")),
    "UISystemMessage": ("systemMessage", ("_message", "_showAnimation", "_canvasGroup", "_animator")),
    "SpotLetterboxBands": ("letterboxBands", ("_topBand", "_bottomBand", "_leftBand", "_rightBand")),
}
WIDGET_FIELDS = ("_canvasSortOrder", "_isMainWidget", "_useBlur", "_blurDuration", "_useGlassMorphism")
TWEEN_EVENT = 1                          # DOTweenSequence command type of an event entry (persistent calls)


def _plain(v):
    """A value with serialized references ({gameObject | transform}) as node paths."""
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict) and ("gameObject" in v or "transform" in v):
        return advui._ref_path(v)
    return v


def tween_calls(entry: dict) -> list[dict]:
    """The persistent calls of a DOTweenSequence event entry (UnityEvent m_PersistentCalls)."""
    calls = (((entry.get("_tweenEvent") or {}).get("m_PersistentCalls") or {}).get("m_Calls")) or []
    out = []
    for x in calls:
        t, a = x.get("m_Target") or {}, x.get("m_Arguments") or {}
        out.append({"target": advui._ref_path(t), "class": t.get("class") or t.get("component"),
                    "method": x.get("m_MethodName"), "string": a.get("m_StringArgument"),
                    "float": a.get("m_FloatArgument"), "int": a.get("m_IntArgument"),
                    "bool": a.get("m_BoolArgument"), "mode": x.get("m_Mode")})
    return out


def node_record(node: dict) -> dict:
    """The advui record of a prefab node (transform, rect, advui._layout_components) with the records of the
    components the host screens add: aspectRatioFitter, simpleAdvLayoutRoot, safeArea (UISafeArea), the
    references of UIAdvTalkWindow / UISystemMessage / SpotLetterboxBands as node paths, `widget` (the UIManager
    widget fields of a *Widget component) and, per DOTweenSequence event entry, its persistent `calls`."""
    rec = {"path": node["path"], "name": node["name"], "active": node["active"],
           "localPosition": node["localPosition"], "localRotation": node["localRotation"],
           "localScale": node["localScale"], "rect": node.get("rect")}
    rec.update(advui._layout_components(node))
    for c in node["components"]:
        cls = c.get("class")
        if cls == "AspectRatioFitter":
            rec["aspectRatioFitter"] = {k: c[k] for k in ("m_Enabled", "m_AspectMode", "m_AspectRatio")}
        elif cls == "SimpleAdvLayoutRoot":
            rec["simpleAdvLayoutRoot"] = _fields(c)
        elif cls == "UISafeArea":
            rec["safeArea"] = {k: _plain(v) for k, v in _fields(c).items()}
        elif cls in REF_RECORDS:
            key, names = REF_RECORDS[cls]
            rec[key] = {k: _plain(c.get(k)) for k in names}
        elif cls == "DOTweenSequence":
            for entry, out in zip(c["_list"], rec["tweenSequence"]["list"]):
                if entry.get("_commandType") == TWEEN_EVENT:
                    out["calls"] = tween_calls(entry)
        elif cls and cls.endswith("Widget") and "_canvasSortOrder" in c:
            rec["widget"] = {"class": cls, **{k: c[k] for k in WIDGET_FIELDS if k in c}}
    return rec


# ---------------------------------------------------------------- clips, shaders
TRANSFORM_CURVES = {"m_LocalPosition": "xyz", "m_LocalScale": "xyz", "m_LocalRotation": "xyzw"}


def clip_view(ex: Exporter, o, graph, root: str) -> dict:
    """An AnimationClip played by the Animator at `root` as Exporter.clip_curves writes it, bindings below the
    animated root included: their properties are keyed "<path relative to root>:<property>". Properties:
    RectTransform driven fields, CanvasGroup.m_Alpha, Transform local position / rotation / scale / euler angles,
    GameObject.m_IsActive and MonoBehaviour fields ("<class>.<field>"). Streamed curves as keys, dense curves as
    {samples, sampleRate, beginTime} (the dense clip's frames), constant curves as {constant}; the discrete curves
    that follow the streamed float curves in the stream (a sprite reference's "<class>.m_Sprite": the value indexes
    `pptrCurveMapping`, the names of the referenced objects) as {discrete: [[time, value]]}, the value held until
    the next key. A binding whose path names no object of the prefab (the Animator ignores it) is keyed
    "#<path crc32>:<property>" and its crc listed in `unresolvedPaths`."""
    rec = ex.clip_record(o)
    ex.bind_clip(o, graph, root)
    tt, mc, st, de = rec["tt"], rec["muscle"], rec["streamed"], rec["dense"]
    name, const = rec["name"], rec["constant"]
    n_st, n_disc, n_de = st["curveCount"], st.get("discreteCurveCount", 0), de["m_CurveCount"]
    if n_disc and n_de:
        raise NotImplementedError(f"clip {name}: discrete and dense curves")
    props, lost = [], []
    for r in rec["bindings"]:
        if r["int"] or r["serializeReference"]:
            raise NotImplementedError(f"clip {name}: binding {r['raw']}")
        if r["path"] is None:
            lost.append(r["pathCrc"])
        pre = f"#{r['pathCrc']}:" if r["path"] is None else f"{r['path']}:" if r["path"] else ""
        tid, attr = r["typeID"], r["attribute"]
        if tid == 224 and attr in RECT_PROPS:
            names = ["RectTransform." + attr]
        elif tid == 225 and attr == "m_Alpha":
            names = ["CanvasGroup.m_Alpha"]
        elif tid == 4 and r["attributeCrc"] == 4:                    # localEulerAnglesRaw
            names = [f"Transform.localEulerAngles.{a}" for a in "xyz"]
        elif tid == 4 and r["transformAttr"] and attr in TRANSFORM_CURVES:
            names = [f"Transform.{attr}.{a}" for a in TRANSFORM_CURVES[attr]]
        elif tid == 1 and attr == "m_IsActive":
            names = ["GameObject.m_IsActive"]
        elif tid == 114 and r["script"] and attr:
            names = [f"{r['script'][1]}.{attr}"]
        elif r["pptr"] and attr == "m_Sprite" and tid == 212:
            names = ["SpriteRenderer.m_Sprite"]
        else:
            raise NotImplementedError(f"clip {name}: binding {r['raw']}")
        if r["pptr"] != (n_st <= len(props) < n_st + n_disc):
            raise NotImplementedError(f"clip {name}: reference curve {r['raw']} outside the discrete curves")
        props += [pre + n for n in names]
    if n_st + n_disc + n_de + len(const) != len(props):
        raise RuntimeError(f"clip {name}: curve count != bound properties")
    curves = {p: {"keys": []} for p in props[:n_st]}
    curves.update({p: {"discrete": []} for p in props[n_st:n_st + n_disc]})
    for t, keys in streamed_frames(st["data"]):
        for ci, c0, c1, c2, c3 in keys:
            if ci < n_st:
                curves[props[ci]]["keys"].append([t, c0, c1, c2, c3])
            else:
                curves[props[ci]]["discrete"].append([t, c3])
    samples = list(de["m_SampleArray"])
    for j in range(n_de):
        curves[props[n_st + j]] = {"samples": samples[j::n_de][:de["m_FrameCount"]], "sampleRate": de["m_SampleRate"],
                                   "beginTime": de["m_BeginTime"]}
    for i, v in enumerate(const):
        curves[props[n_st + n_disc + n_de + i]] = {"constant": v}
    out = {"name": name, "sampleRate": tt["m_SampleRate"], "startTime": mc["m_StartTime"],
           "stopTime": mc["m_StopTime"], "loopTime": bool(mc["m_LoopTime"]), "events": tt["m_Events"],
           "curves": curves,
           "keyFormat": "[time, c0, c1, c2, c3]; value(t) = ((c0*x + c1)*x + c2)*x + c3, x = t - time, "
                        "from the last key with time <= t (first key at -FLT_MAX)"}
    if rec["pptrCurveMapping"]:
        refs = [ex.deref(o, p) for p in rec["pptrCurveMapping"]]
        out["pptrCurveMapping"] = [x.read_typetree().get("m_Name") if x is not None else None for x in refs]
    if lost:
        out["unresolvedPaths"] = lost
    return out


def gles3_keywords(index: list, shader: str, keywords, what: str) -> list[str]:
    """The keywords of `keywords` that the GLES3 variants of `shader` use; a GLES3 vertex program with exactly
    those must exist (as advui checks its materials)."""
    rec = next((r for r in index if r["name"] == shader), None)
    if rec is None:
        raise RuntimeError(f"shader {shader} of {what} not dumped")
    gles = [v for v in rec["variants"] if v["platform"] == "gles3" and v["type"] == "GLES3"]
    known = {k for v in gles for k in v["keywords"]}
    want = sorted(k for k in keywords if k in known)
    if not any(sorted(v["keywords"]) == want and v["stage"] == "vertex" for v in gles):
        raise RuntimeError(f"{shader}: no GLES3 variant for {what} keywords {want}")
    return want


def dump_gles3(objects: dict, out_dir: Path) -> tuple[list, dict]:
    """Dump the Shader objects {name: object} in name order (shader.py layout) keeping only the GLES3 programs:
    the index lists those, the other programs are not kept. -> (index, write_index summary)."""
    index: list = []
    for name in sorted(objects):
        shader_mod.dump_objects([objects[name]], out_dir, objects[name].assets_file.name, index)
    for rec in index:
        keep = [v for v in rec["variants"] if v["platform"] == "gles3" and v["type"] == "GLES3"]
        for v in rec["variants"]:
            if v not in keep:
                (Path(out_dir) / v["file"]).unlink(missing_ok=True)
        rec["variants"] = keep
    for d in sorted(Path(out_dir).rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return index, shader_mod.write_index(index, out_dir)


# ---------------------------------------------------------------- UI documents
class UiDoc:
    """A ui.json of prefab parts in the advui record format, written into `out_dir`: node records (node_record;
    a TMP text gets its advui text record `textStyle` in the LanguageMode `mode`, None: no text allowed), the packed
    sprites and textures, the materials with their GLES3 keyword sets, the UI shaders, the DOTween defaults, and
    per Animator its controller (Exporter.controller_states) and clips (clip_view); a node whose controller cannot
    be exported loses its `animator` record and is listed in `animatorsDropped`."""

    def __init__(self, cat, player, out_dir: Path, mode: int | None = None):
        self.player, self.dir = player, Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ex = Exporter(cat, self.dir, player=player, textures="deferred", stub_assets=TMP_STUBS)
        self.packer = TexelPacker(self.dir)
        self.styles = textstyle.TextStyles(self.ex, player, mode) if mode is not None else None
        self.nodes: list[dict] = []
        self.sources: list[tuple[str, dict]] = []          # (key, prefab node) of each record
        self.roots: list[dict] = []
        self.doc: dict | None = None

    def prefab(self, key: str) -> list[dict]:
        return self.ex.prefab(key)["nodes"]

    def add(self, key: str, nodes: list[dict], subtrees=None, singles=()) -> list[dict]:
        """Records of the prefab `nodes` of `key` (every node, or those `kept` by subtrees / singles)."""
        out = []
        for n in nodes:
            if subtrees is not None and not kept(n["path"], subtrees, singles):
                continue
            rec = node_record(n)
            if "text" in rec:
                if self.styles is None:
                    raise RuntimeError(f"{n['path']}: a text node in a UI without texts")
                if not (rec.get("localizeText") or {}).get("localizeEnabled"):
                    raise NotImplementedError(f"{n['path']}: text without LocalizeText")
                rec["textStyle"] = self.styles.record(n["path"], advui._component(n, advui.TMP_CLASSES),
                                                      advui._component(n, ("LocalizeText",)))
                del rec["text"]
            self.nodes.append(rec)
            self.sources.append((key, n))
            out.append(rec)
        self.roots.append({"root": nodes[0]["path"], "key": key})
        return out

    def sprite(self, key: str) -> dict:
        """{spriteRef, name} of the sprite a key names (a Sprite, or a Texture2D's sprite sub-asset)."""
        o = self.ex.key_object(key)
        return self.ex.sprite(o) if o.type.name == "Sprite" else self.ex.key_sprite(key)

    def _controller(self, key: str, path: str):
        env, graph = self.ex.load(key)
        hits = []
        for o in env.objects:
            if o.type.name == "Animator":
                tf = graph.tf_of_go.get(o.read_typetree()["m_GameObject"]["m_PathID"])
                if tf is not None and graph.path(tf) == path:
                    hits.append(o)
        if len(hits) != 1:
            return None
        ctrl = self.ex.deref(hits[0], hits[0].read_typetree()["m_Controller"])
        if ctrl is None:
            return None
        try:
            states = self.ex.controller_states(ctrl)
            clips = {}
            for p in ctrl.read_typetree()["m_AnimationClips"]:
                c = self.ex.deref(ctrl, p)
                if c is None or c.type.name != "AnimationClip":
                    raise NotImplementedError("not an AnimationClip")
                v = clip_view(self.ex, c, graph, path)
                clips[f"{states['name']}/{v['name']}"] = v
        except NotImplementedError:
            return None
        return states, clips

    def write(self, about: str, head: dict | None = None, tail: dict | None = None) -> dict:
        clips, controllers, dropped = {}, {}, []
        for rec, (key, n) in zip(self.nodes, self.sources):
            if "animator" in rec:
                got = self._controller(key, n["path"])
                if got is None:
                    del rec["animator"]
                    dropped.append(rec["path"])
                else:
                    controllers[got[0]["name"]] = got[0]
                    clips.update(got[1])
        ex, refs = self.ex, set()
        for rec in self.nodes:
            if (rec.get("image") or {}).get("spriteRef"):
                refs.add(rec["image"]["spriteRef"])
            if (rec.get("buttonImageState") or {}).get("selectedRef"):
                refs.add(rec["buttonImageState"]["selectedRef"])
        for ref in sorted(refs):
            s = ex.sprite_recs[ref]
            tex = s["tex"].read_typetree()["m_Name"]
            ex.pack_sprite(ref, self.packer, "sprites" if "FixUiSpriteAtlas" in tex else f"sprite_{s['name']}")
        textures = self.packer.build()
        sprites = {ex.sprite_recs[ref]["name"]: ex.packed_sprite(ref) for ref in sorted(refs)}
        for rec in self.nodes:
            (rec.get("image") or {}).pop("spriteRef", None)
            (rec.get("buttonImageState") or {}).pop("selectedRef", None)
        materials = {"Default UI Material": {"material": "Default UI Material", "shader": {"shader": "UI/Default"},
                                             "keywords": [], "textures": {}, "ints": {}, "floats": {}, "colors": {},
                                             "shaderDefaults": True}}
        for _, n in self.sources:
            for c in n["components"]:
                if c.get("class") in ("Image", "RawImage") and c.get("m_Material"):
                    materials.setdefault(c["m_Material"]["material"], json.loads(dumps(c["m_Material"])))
        for m in materials.values():
            storyfonts._material_textures(m)
        needed = {}
        for name in sorted({m["shader"]["shader"] for m in materials.values()}):
            if name == "UI/Default":
                needed[name] = self.player.shader("UI/Default", file="unity_builtin_extra")
            elif name in ex.shaders:
                needed[name] = ex.shaders[name]
            else:
                raise RuntimeError(f"shader {name} not in the loaded bundles")
        index, summary = ex.dump_shaders(self.dir / "shaders", needed)
        keywords = {k: gles3_keywords(index, m["shader"]["shader"], m["keywords"], f"material {k}")
                    for k, m in materials.items()}
        dts = self.player.mono(self.player.resource("DOTweenSettings"))
        doc = {"about": about, **(head or {}), "roots": self.roots, "nodes": self.nodes, "sprites": sprites,
               "textures": textures, "materials": materials, "materialKeywords": keywords, "clips": clips,
               "controllers": controllers, "animatorsDropped": dropped,
               "dotween": {k: dts[k] for k in DOTWEEN_FIELDS},
               "shaders": {"index": "shaders/shaders.json", "names": summary["names"]}}
        if self.styles is not None:
            doc["textStyle"] = self.styles.summary()
        doc.update(tail or {})
        write_json(self.dir / "ui.json", doc)
        self.doc = doc
        return doc


# ---------------------------------------------------------------- home
def container_go(env, internal_id: str):
    """The GameObject an AssetBundle container entry names (a prefab's root)."""
    for o in env.objects:
        if o.type.name == "AssetBundle":
            for path, info in o.read_typetree()["m_Container"]:
                if path.lower() == internal_id.lower():
                    go = deref(o, info["asset"])
                    if go is not None and go.type.name == "GameObject":
                        return go
    raise KeyError(f"{internal_id}: no GameObject in the AssetBundle containers")


def _go_ref(owner, pptr: dict) -> int | None:
    if not pptr["m_PathID"]:
        return None
    if pptr["m_FileID"] == 0:
        return pptr["m_PathID"]
    o = deref(owner, pptr)
    return o.path_id if o is not None else None


def background(cat, key: str, situation_name: str, ex: Exporter) -> dict:
    """The placed background prefab `key` as SpotSceneRoot.SetObject leaves it, over the nodes and materials of
    room.extract_room (the same bundle load, glb_order): SpotBackground.Prepare(situation_name) on the prefab
    root, the "floor" render queue (FindFirstChildWithName(background, "floor", "shadow"), its
    GetComponentInChildren<MeshRenderer>().material.renderQueue = 2000, before the root is activated), then
    SetActiveFast(background, true). Objects outside the placed prefab (other prefabs of the bundle closure) are
    not placed: inactive."""
    env = UnityPy.load(*[str(p) for p in cat.fetch_key(key)])
    graph = SceneGraph(env)
    mf_by_go, mr_by_go = {}, {}
    for o in env.objects:
        if o.type.name == "MeshFilter":
            mf_by_go[o.read_typetree()["m_GameObject"]["m_PathID"]] = o
        elif o.type.name == "MeshRenderer":
            mr_by_go[o.read_typetree()["m_GameObject"]["m_PathID"]] = o
    root_go = container_go(env, cat._entry(key)["internal_id"]).path_id
    root = graph.tf_of_go[root_go]
    spot_bg, volumes = [], []
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        tf = graph.tf_of_go.get(o.read_typetree()["m_GameObject"]["m_PathID"])
        if tf is None or not under(graph, tf, root):
            continue
        cls = script_class(o)
        if cls == "SpotBackground" and tf == root:            # background.GetComponent<SpotBackground>()
            spot_bg.append(o)
        elif cls == "Volume":
            volumes.append((o, tf))
    if spot_bg:
        tt = spot_bg[0].read_typetree()
        entries = [(e.get("SituationName"), [_go_ref(spot_bg[0], h) for h in e.get("HideObjects") or []])
                   for e in tt.get("_situationObjects") or []]
        prep = prepare(graph, root, situation_name, entries)
    else:
        prep = {"name": situation_name.replace(CLONE, ""), "matched": False, "activate": [], "hide": [],
                "overrides": {}}
    overrides = prep["overrides"]
    floor = find_first_child_with_name(graph, root, FLOOR_NAME, FLOOR_EXCLUDE)
    renderer = None if floor is None else component_in_children(
        graph, floor, lambda t: _go(graph, t) in mr_by_go, lambda t: active_in_hierarchy(graph, t, overrides))
    shown = {**overrides, root_go: True}
    nodes, mats = glb_order(room.drawn_meshes(mf_by_go, mr_by_go, graph, lambda p: None))
    rows = []
    for p in mats:
        mat = p.read()
        sh = mat.m_Shader.read().object_reader
        rows.append((mat.object_reader.read_typetree(), sh.read_typetree()["m_ParsedForm"]["m_Name"], sh))
    tex_index = glb_textures([(tt, name) for tt, name, _ in rows])
    if len(volumes) > 1:
        raise NotImplementedError(f"{key}: {len(volumes)} Volume components")
    volume = None
    if volumes:
        o, tf = volumes[0]
        volume = {"path": graph.path(tf), "active": active_in_hierarchy(graph, tf, shown),
                  **volume_record(ex.component(o), graph.go[_go(graph, tf)]["m_Layer"])}
    t = graph.tf[root]
    return {
        "roomRoot": {"path": graph.path(root), "localPosition": t["m_LocalPosition"],
                     "localRotation": t["m_LocalRotation"], "localScale": t["m_LocalScale"]},
        "roomNodes": [{"path": graph.path(tf), "placed": under(graph, tf, root),
                       "active": under(graph, tf, root) and active_in_hierarchy(graph, tf, shown),
                       "renderQueue": FLOOR_QUEUE if tf == renderer else None} for tf, _ in nodes],
        "roomMaterials": [room_material(tt, name, tex_index) for tt, name, _ in rows],
        "situation": {"name": prep["name"], "matched": prep["matched"],
                      "activate": [graph.path(x) for x in prep["activate"]],
                      "hide": [graph.path(graph.tf_of_go[g]) if g in graph.tf_of_go else None for g in prep["hide"]]},
        "floor": {"object": graph.path(floor) if floor is not None else None,
                  "renderer": graph.path(renderer) if renderer is not None else None, "renderQueue": FLOOR_QUEUE},
        "volume": volume,
        "shaders": {name: sh for _, name, sh in reversed(rows)},          # the first material's object
        "check": {"nodes": [n for _, n in nodes], "textures": len(tex_index),
                  "mirrored": sum(1 for tf, _ in nodes if under(graph, tf, root)
                                  and np.linalg.det(graph.world(tf)[:3, :3]) < 0)},
    }

def spine_records(env, ex: Exporter) -> tuple[dict, list, dict]:
    """The Spine data of a situation prefab's bundle closure (loaded as spot.extract loads it): the material of
    every atlas page (Exporter.material records by name), per SpotSpineCharacter in spot.json's order its
    SkeletonAnimation's MeshRenderer sorting order / layer and {atlas page: material name} (SpineAtlasAsset
    materials in page order), and the shaders the materials name. -> (materials, characters, {name: shader})."""
    graph = SceneGraph(env)
    by_cls: dict[str, list] = {}
    mr_by_go = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            by_cls.setdefault(script_class(o), []).append(o)
        elif o.type.name == "MeshRenderer":
            mr_by_go[o.read_typetree()["m_GameObject"]["m_PathID"]] = o
    materials, shaders, pages_of = {}, {}, {}

    def pages(sda) -> dict:
        if sda.path_id not in pages_of:
            out = {}
            for ap in sda.read_typetree()["atlasAssets"]:
                a = deref(sda, ap)
                if a is None:
                    continue
                att = a.read_typetree()
                names = spot._atlas_pages(spot._text_bytes(deref(a, att["atlasFile"]).read()).decode("utf-8"))
                mats = [deref(a, m) for m in att["materials"]]
                if len(names) != len(mats):
                    raise RuntimeError(f"atlas {att.get('m_Name')}: {len(names)} pages vs {len(mats)} materials")
                for page, m in zip(names, mats):
                    rec = None if m is None else ex.material(m)
                    if rec is not None:
                        storyfonts._material_textures(rec)
                        materials.setdefault(rec["material"], rec)
                        sh = rec["shader"]["shader"]
                        shaders.setdefault(sh, ex.shaders[sh])
                    out[page] = rec and rec["material"]
            pages_of[sda.path_id] = out
        return pages_of[sda.path_id]
    for o in by_cls.get("SkeletonDataAsset", []):
        pages(o)
    chars = []
    for o in by_cls.get("SpotSpineCharacter", []):
        tt = o.read_typetree()
        rec = {"path": graph.path(graph.tf_of_go[tt["m_GameObject"]["m_PathID"]]), "sortingOrder": None,
               "sortingLayer": None, "pageMaterials": {}}
        anim = deref(o, tt["_animation"])
        if anim is not None:
            at = anim.read_typetree()
            mr = mr_by_go.get(at["m_GameObject"]["m_PathID"])
            if mr is not None:
                mt = mr.read_typetree()
                rec.update(sortingOrder=mt.get("m_SortingOrder"), sortingLayer=mt.get("m_SortingLayerID"))
            sda = deref(anim, at["skeletonDataAsset"])
            rec["pageMaterials"] = dict(pages(sda)) if sda is not None else {}
        chars.append(rec)
    return {k: materials[k] for k in sorted(materials)}, chars, shaders


def scene_root(ex: Exporter, key: str | None) -> dict:
    """sceneRoot: the "Spot" scene's SpotSceneRoot: world TRS of `_objRoot` (the parent SetObject puts the
    background and the situation under), its Light components, its Volumes and the scene camera
    (`_sceneCamera`, advui camera record); null / [] when the scene or the component is not there."""
    out = {"key": key, "objRoot": None, "lights": [], "volumes": [], "sceneCamera": None}
    if key is None:
        return out
    nodes = ex.prefab(key)["nodes"]
    roots = [(n, c) for n in nodes for c in n["components"] if c.get("class") == "SpotSceneRoot"]
    if len(roots) != 1:
        return out
    c = roots[0][1]
    obj, cam = advui._ref_path(c.get("_objRoot")), advui._ref_path(c.get("_sceneCamera"))
    out["objRoot"] = {"path": obj, **world_trs(nodes, obj)} if obj else None
    out["lights"] = [{"path": n["path"], **{k: _plain(v) for k, v in x.items() if k != "type"}}
                     for n in nodes for x in n["components"] if x["type"] == "Light"]
    out["volumes"] = [{"path": n["path"], **volume_record(x, n["layer"])}
                      for n in nodes for x in n["components"] if x.get("class") == "Volume"]
    out["sceneCamera"] = advui._camera(nodes, cam) if cam else None
    return out


def home_host(cat, master_dir, player, host: dict, hdir: Path, keys: dict) -> dict:
    """host.json `home`: spot.extract and room.extract_room into host/spot/, the placed background (background),
    the Spine records, the Spot scene root, the blur, the ambient sound and the shaders (host/shaders/, GLES3
    programs only) with each material's GLES3 keyword set."""
    sdir = hdir / "spot"
    doc = spot.extract(cat, Path(master_dir), host["spotId"], sdir)
    row = doc["master"]
    # A tap talk's character need not be among the spot's SpotCharacters (spot.json `characters`): the game then has
    # no tap target that starts the talk (HomeSpotAfterTalkRequestFactory.TrySelectAdv matches the tapped
    # SpotCharacter's _characterId), and the player plays it without the camera focus
    bg_key, sit_key = row["_backgroundAssetPath"], row["_situationAssetPath"]
    room_doc = room.extract_room(cat, bg_key, sdir / "room.glb")
    ex = Exporter(cat, hdir, player=player, textures="deferred", inline_meshes=False)
    sit_env = UnityPy.load(*[str(p) for p in cat.fetch_key(sit_key)])
    sit_name = container_go(sit_env, cat._entry(sit_key)["internal_id"]).read_typetree()["m_Name"]
    bg = background(cat, bg_key, sit_name + CLONE, ex)
    glb = glb_json(sdir / "room.glb")
    if ([n["name"] for n in glb["nodes"]] != bg["check"]["nodes"] or room_doc["meshCount"] != len(bg["roomNodes"])
            or [m["name"] for m in glb.get("materials", [])] != [m["name"] for m in bg["roomMaterials"]]
            or len(glb.get("images", [])) != bg["check"]["textures"]):
        raise RuntimeError(f"{bg_key}: room.glb nodes / materials / textures differ from the background's order")
    spine_mats, spine_chars, spine_shaders = spine_records(sit_env, ex)
    if [c["path"] for c in spine_chars] != [c["path"] for c in doc["spineCharacters"]]:
        raise RuntimeError(f"{sit_key}: Spine characters differ from spot.json's")
    blur = blur_record(player.graphics())
    blur_obj = [o for o in player.renderer_shaders(blur["renderer"]) if o.read().m_ParsedForm.m_Name == blur["shader"]]
    if len(blur_obj) != 1:
        raise RuntimeError(f"renderer {blur['renderer']}: {len(blur_obj)} shaders {blur['shader']}")
    needed = dict(bg["shaders"])
    for name, o in [*spine_shaders.items(), (blur["shader"], blur_obj[0])]:
        needed.setdefault(name, o)
    index, summary = dump_gles3(needed, hdir / "shaders")
    keywords = {"room": [gles3_keywords(index, m["shader"], m["keywords"], f"room material {m['name']}")
                         for m in bg["roomMaterials"]],
                "spine": {k: gles3_keywords(index, m["shader"]["shader"], m["keywords"], f"Spine material {k}")
                          for k, m in spine_mats.items()}}
    gles3_keywords(index, blur["shader"], [], "the blur pass")
    return {"spotId": host["spotId"], "talk": host["talk"], "characterId": host["characterId"],
            "titleTextId": row.get("_advNameTextId") if host["talk"] == "area" else None,
            "spot": f"{HOST_DIR}/spot/spot.json", "room": f"{HOST_DIR}/spot/room.glb",
            "roomRoot": bg["roomRoot"], "roomNodes": bg["roomNodes"], "roomMaterials": bg["roomMaterials"],
            "situation": bg["situation"], "floor": bg["floor"], "sceneRoot": scene_root(ex, keys.get("spotScene")),
            "volume": bg["volume"], "spineMaterials": spine_mats, "spineCharacters": spine_chars,
            "shaders": {"index": f"{HOST_DIR}/shaders/shaders.json", "names": summary["names"]},
            "materialKeywords": keywords, "blur": blur,
            "ambient": ambient_record(doc["situationSettings"], master_dir)}


def home_ui(ui: UiDoc, keys: dict) -> tuple[str, dict, list]:
    """host/ui nodes of a home talk: UISpotWidget's root, View, View/Fade, its DOTweenSequence_* nodes (and the
    nodes their calls target), the SimpleAdv root (UISpotWidget._afterTalkContents) subtree; UISpotLetterbox.
    The opened sequence is _fadeIn: the spot appears through SpotManager.PlayInAnimation -> SpotPresenter.FadeIn
    (the Fade cover from 1 to 0); a spot change (ChangeSpotAsync) covers with _transitionIn and reveals with
    _transitionOut, which ends with the cover at 0 as well.
    -> (overlay root, layout root, opened sequences)."""
    nodes = ui.prefab(keys["spotWidget"])
    w = advui._component(nodes[0], ("UISpotWidget",))
    overlay = advui._ref_path(w["_afterTalkContents"])
    seqs = [advui._ref_path(w[k]) for k in ("_transitionIn", "_transitionOut", "_fadeIn", "_fadeOut") if w.get(k)]
    fade = f"{_parent(overlay)}/Fade"
    ui.add(keys["spotWidget"], nodes, [overlay, fade, *seqs], _call_targets(nodes, seqs))
    ui.add(keys["spotLetterbox"], ui.prefab(keys["spotLetterbox"]))
    return overlay, layout_root(nodes, overlay), [advui._ref_path(w["_fadeIn"])]


def _call_targets(nodes: list[dict], seqs: list[str]) -> list[str]:
    """The nodes the DOTweenSequence event entries of the nodes at `seqs` call."""
    out = []
    for n in nodes:
        if n["path"] in seqs:
            for c in n["components"]:
                if c.get("class") == "DOTweenSequence":
                    for e in c["_list"]:
                        out += [x["target"] for x in tween_calls(e) if x["target"] and x["target"] not in out]
    return out


def afterlive_ui(ui: UiDoc, keys: dict) -> tuple[str, dict, list, dict]:
    """host/ui nodes of a live result talk: UILiveResultBackgroundWidget (its _backgroundImage showing the
    live_result_background sprite); UILiveResultWidget's chain to the SimpleAdv root (_advSimpleContents) and its
    subtree, the widget's and UISoloLiveMusicResultView's _inAnimation sequences and the nodes their calls target.
    -> (overlay root, layout root, opened sequences, host.json `afterlive`)."""
    bnodes = ui.prefab(keys["resultBackground"])
    bw = advui._component(bnodes[0], ("UILiveResultBackgroundWidget",))
    bg_path = advui._ref_path(bw["_backgroundImage"])
    recs = [r for r in ui.add(keys["resultBackground"], bnodes) if r["path"] == bg_path and r.get("image")]
    if len(recs) != 1:
        raise RuntimeError(f"{bg_path}: {len(recs)} image nodes")
    sprite = ui.sprite(keys["resultBackgroundSprite"])
    recs[0]["image"].update(sprite=sprite["name"], spriteRef=sprite["spriteRef"])
    nodes = ui.prefab(keys["resultWidget"])
    rw = advui._component(nodes[0], ("UILiveResultWidget",))
    overlay = advui._ref_path(rw["_advSimpleContents"])
    view = [(n, c) for n in nodes for c in n["components"] if c.get("class") == "UISoloLiveMusicResultView"
            and overlay.startswith(n["path"] + "/")]
    if len(view) != 1:
        raise RuntimeError(f"{overlay}: {len(view)} UISoloLiveMusicResultView ancestors")
    seqs = [advui._ref_path(bw["_inAnimation"]), advui._ref_path(rw["_inAnimation"]),
            advui._ref_path(view[0][1]["_inAnimation"])]
    ui.add(keys["resultWidget"], nodes, [overlay, *seqs[1:]], _call_targets(nodes, seqs[1:]))
    parts = overlay.split("/")
    reward = next(("/".join(parts[:i + 1]) for i in range(len(parts) - 2, 0, -1) if parts[i] == REWARD_PANEL), None)
    if reward is None:
        raise RuntimeError(f"{overlay}: no {REWARD_PANEL} ancestor")
    return overlay, layout_root(nodes, overlay), seqs, {
        "backgroundNode": bg_path, "backgroundSprite": sprite["name"], "rewardPanel": reward,
        "substitutes": list(SUBSTITUTES)}


# ---------------------------------------------------------------- background (SpotBackground, SpotSceneRoot)
def _go(graph, tf: int) -> int:
    return graph.tf[tf]["m_GameObject"]["m_PathID"]


def _children(graph, tf: int) -> list[int]:
    return [c["m_PathID"] for c in graph.tf[tf]["m_Children"]]


def prepare(graph, background_tf: int, situation_name: str, entries: list) -> dict:
    """SpotBackground.Prepare(situationName) on the background at `background_tf` (entries: its _situationObjects
    as [(SituationName, [HideObjects GameObject path ids | None])]): "(Clone)" removed from the name; the first
    entry of that name activates every child of transform.GetChild(0) and deactivates its HideObjects; no entry of
    that name keeps the prefab state. -> {name, matched, activate [transform ids], hide [GameObject ids | None],
    overrides {GameObject id: activeSelf}}."""
    name = situation_name.replace(CLONE, "")
    kids = _children(graph, background_tf)
    if not kids:
        raise RuntimeError(f"{graph.path(background_tf)}: SpotBackground without a child (GetChild(0))")
    for entry_name, hide in entries:
        if entry_name == name:
            activate = _children(graph, kids[0])
            overrides = {_go(graph, t): True for t in activate}
            overrides.update({g: False for g in hide if g})
            return {"name": name, "matched": True, "activate": activate, "hide": list(hide), "overrides": overrides}
    return {"name": name, "matched": False, "activate": [], "hide": [], "overrides": {}}


def active_in_hierarchy(graph, tf: int, overrides: dict) -> bool:
    """GameObject.activeInHierarchy of the object at `tf`, with `overrides` {GameObject id: activeSelf} over the
    serialized m_IsActive."""
    while tf in graph.tf:
        go = _go(graph, tf)
        if not overrides.get(go, bool(graph.go.get(go, {}).get("m_IsActive", 1))):
            return False
        tf = graph.tf[tf]["m_Father"]["m_PathID"]
    return True


def under(graph, tf: int, root: int) -> bool:
    """The transform `tf` is `root` or below it."""
    while tf in graph.tf:
        if tf == root:
            return True
        tf = graph.tf[tf]["m_Father"]["m_PathID"]
    return False


def find_first_child_with_name(graph, tf: int, contains: str, excludes: str) -> int | None:
    """SpotSceneRoot.SetObject's FindFirstChildWithName: depth first over the descendants of `tf` (itself
    excluded), children in order, the first whose name contains `contains` and not `excludes` (ordinal)."""
    for c in _children(graph, tf):
        name = graph.go.get(_go(graph, c), {}).get("m_Name", "")
        if contains in name and excludes not in name:
            return c
        hit = find_first_child_with_name(graph, c, contains, excludes)
        if hit is not None:
            return hit
    return None


def component_in_children(graph, tf: int, has, active) -> int | None:
    """GameObject.GetComponentInChildren<T>() (inactive objects skipped): depth first from `tf` itself, the first
    transform whose object is active in the hierarchy (`active(tf)`) and has the component (`has(tf)`)."""
    if active(tf) and has(tf):
        return tf
    for c in _children(graph, tf):
        hit = component_in_children(graph, c, has, active)
        if hit is not None:
            return hit
    return None


def glb_order(drawn, arrays_of=mesh_arrays) -> tuple[list, list]:
    """The nodes and materials room.extract_room writes, in its order, from its room.drawn_meshes items
    [(transform id, Mesh, material PPtrs)]: a mesh without vertex data or triangles, or whose submeshes all lack
    a material, is no node; a material is numbered at its first use (room.submesh_primitives order, by path id).
    -> ([(transform id, mesh name)], [material PPtr])."""
    index: dict[int, int] = {}
    mats, nodes = [], []

    def material_for(p) -> int:
        if p.path_id not in index:
            index[p.path_id] = len(mats)
            mats.append(p)
        return index[p.path_id]
    for tf, mesh, pptrs in drawn:
        arrays = arrays_of(mesh)
        if arrays is None:
            continue
        tris = arrays[3]
        if not any(len(t) for t in tris):
            continue
        if len(pptrs) < len(tris):
            raise NotImplementedError(f"{mesh.m_Name}: {len(tris)} submeshes, {len(pptrs)} materials")
        if room.submesh_primitives(tris, pptrs, material_for, lambda i: None):
            nodes.append((tf, mesh.m_Name))
    return nodes, mats


def glb_textures(materials: list) -> dict[int, int]:
    """{texture path id: glb texture index} of room.extract_room for its materials [(typetree, shader name)] in
    glb order: the main texture slot of each (room._main_tex_slot), numbered at first use."""
    index: dict[int, int] = {}
    for tt, shader_name in materials:
        slot = room._main_tex_slot(shader_name)
        for key, env in tt["m_SavedProperties"]["m_TexEnvs"]:
            tid = env["m_Texture"]["m_PathID"]
            if key == slot and tid:
                index.setdefault(tid, len(index))
    return index


def room_material(tt: dict, shader_name: str, tex_index: dict) -> dict:
    """A room material as saved (its shader runs as the game's): name, shader, keywords, float / int / colour
    properties, custom render queue, and per texture slot the glb texture index (null when the texture is not in
    the glb), scale and offset."""
    sp = tt["m_SavedProperties"]
    keywords = tt.get("m_ValidKeywords")
    if keywords is None:
        keywords = (tt.get("m_ShaderKeywords") or "").split()
    return {"name": tt["m_Name"], "shader": shader_name, "keywords": list(keywords),
            "floats": dict(sp.get("m_Floats", [])), "ints": dict(sp.get("m_Ints", [])),
            "colors": dict(sp.get("m_Colors", [])), "renderQueue": tt.get("m_CustomRenderQueue"),
            "texEnvs": {k: {"texture": tex_index.get(v["m_Texture"]["m_PathID"]) if v["m_Texture"]["m_PathID"]
                            else None,
                            "scale": [v["m_Scale"]["x"], v["m_Scale"]["y"]],
                            "offset": [v["m_Offset"]["x"], v["m_Offset"]["y"]]} for k, v in sp["m_TexEnvs"]}}


def glb_json(path) -> dict:
    """The JSON chunk of a binary glTF file."""
    data = Path(path).read_bytes()
    (n,) = struct.unpack_from("<I", data, 12)
    if data[:4] != b"glTF" or data[16:20] != b"JSON":
        raise RuntimeError(f"{path}: not a binary glTF file")
    return json.loads(data[20:20 + n])


def volume_record(comp: dict, layer: int) -> dict:
    """A Volume component (Exporter record: sharedProfile and its components inlined) -> enabled, isGlobal,
    weight, priority, blendDistance, layer, profile name and per profile component its class, active flag and the
    parameters it overrides (m_OverrideState set) with their values."""
    prof = comp.get("sharedProfile") or {}
    comps = []
    for c in prof.get("components") or []:
        if not c:
            continue
        over = {k: _plain(v["m_Value"]) for k, v in c.items()
                if isinstance(v, dict) and "m_OverrideState" in v and v["m_OverrideState"]}
        comps.append({"class": c.get("asset"), "active": bool(c.get("active", 1)), **over})
    return {"enabled": bool(comp.get("m_Enabled", 1)), "isGlobal": bool(comp["m_IsGlobal"]), "weight": comp["weight"],
            "priority": comp["priority"], "blendDistance": comp["blendDistance"], "layer": layer,
            "profile": prof.get("name"), "components": comps}


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _rotate(q, v):
    x, y, z, _ = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return (x, y, z)


def world_trs(nodes: list[dict], path: str) -> dict:
    """World position, rotation and (lossy) scale of the node at `path` from the local TRS of it and its
    ancestors (position through the parents' rotation and scale, rotation the product of the local rotations,
    scale the product of the local scales)."""
    parts = path.split("/")
    pos, rot, scl = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)
    for i in range(1, len(parts) + 1):
        n = _first(nodes, "/".join(parts[:i]))
        p, r, s = n["localPosition"], n["localRotation"], n["localScale"]
        d = _rotate(rot, (scl[0] * p["x"], scl[1] * p["y"], scl[2] * p["z"]))
        pos = (pos[0] + d[0], pos[1] + d[1], pos[2] + d[2])
        rot = _qmul(rot, (r["x"], r["y"], r["z"], r["w"]))
        scl = (scl[0] * s["x"], scl[1] * s["y"], scl[2] * s["z"])
    return {"position": dict(zip("xyz", pos)), "rotation": dict(zip("xyzw", rot)), "scale": dict(zip("xyz", scl))}


def blur_record(graphics: dict) -> dict:
    """blur: the UIRendererFeature of renderer 0 (UIBlurController.ExecBlur -> UIRenderPass CaptureAndBlur): its
    Dual Kawase iterations, offset, downsample, blend rate maximum and shader name, read from the player data
    (renderer 0 of every pipeline must be the same renderer)."""
    first = {p["m_RendererDataList"][0] for p in graphics["pipelines"].values()}
    if len(first) != 1:
        raise NotImplementedError(f"renderer 0 differs between the pipelines: {sorted(first)}")
    name = advscene.camera_renderer(graphics, graphics["defaultPipeline"], 0)
    feats = [f for f in graphics["renderers"][name]["m_RendererFeatures"] if f["class"] == BLUR_FEATURE]
    if len(feats) != 1:
        raise RuntimeError(f"renderer {name}: {len(feats)} {BLUR_FEATURE}")
    f = feats[0]
    return {"renderer": name, "active": bool(f["m_Active"]), "iterations": f["_blurIterations"],
            "offset": f["_blurOffset"], "downsample": f["_blurDownsample"], "blendRateMax": f["_blurBlendRateMax"],
            "shader": f["_dualKawaseBlurShader"]}


def _rows(master_dir, name: str) -> dict:
    try:
        return {r["_id"]: r for r in master.table(Path(master_dir), name)}
    except FileNotFoundError:
        return {}


def ambient_record(settings: dict, master_dir) -> dict | None:
    """ambient: the situation's ambient sound (SpotSituationSettings._soundId, ambientSoundVolume) with its cue
    sheet and cue (MasterSound, MasterSoundCueSheet; null when the master data lacks the row); null without one."""
    sid = settings.get("_soundId") or 0
    if not sid:
        return None
    snd = _rows(master_dir, "MasterSound").get(sid) or {}
    sheet = _rows(master_dir, "MasterSoundCueSheet").get(snd.get("_soundCueSheetID")) or {}
    return {"soundId": sid, "volume": settings.get("ambientSoundVolume"), "cueSheet": sheet.get("_cueSheetName"),
            "cue": snd.get("_cueName")}


# ---------------------------------------------------------------- per language: ui/simple/
def text_bindings(ui: UiDoc, lang: dict, mode: int) -> dict[str, dict]:
    """{node path: text binding} of the text nodes of `ui`, as storyfonts.text_bindings makes them for the story
    UI: the serialized TextMeshPro fields, storyfonts.text_extras and `localized` (LocalizeText in `mode`)."""
    swap = textstyle.font_swap(lang, mode)
    out = {}
    for rec, (_, n) in zip(ui.nodes, ui.sources):
        if "textStyle" not in rec:
            continue
        path = rec["path"]
        if path in out:
            raise RuntimeError(f"{path}: two text nodes")
        c = advui._component(n, advui.TMP_CLASSES)
        t = {k: c[k] for k in advui.TMP_FIELDS if k in c}
        t.update({"class": c["class"], "enabled": c["m_Enabled"], "fontAsset": c["m_fontAsset"]["name"],
                  "material": c["m_sharedMaterial"]["material"]})
        t.update(storyfonts.text_extras(path, n["components"]))
        t["localized"] = textstyle.localize_text(path, t["fontAsset"], t["material"], swap, lang, mode)
        out[path] = t
    return out


def story_text_materials(story_fonts: dict | None, lang: dict, mode: int) -> dict[str, set]:
    """{localized game font asset: its localized game text materials} of the story UI's texts (the `texts` of
    the story's ui/fonts.json, open fonts)."""
    if not story_fonts or story_fonts.get("source") != "open":
        return {}
    swap, out = textstyle.font_swap(lang, mode), {}
    for path, t in story_fonts.get("texts", {}).items():
        loc = textstyle.localize_text(path, t["fontAsset"], t["material"], swap, lang, mode)
        out.setdefault(loc["fontAsset"], set()).add(loc["material"])
    return out


def open_fonts(ui: UiDoc, cat, player, episode: dict, story_ui: dict, language: str, font, master_dir,
               texts: list[str], story_fonts: dict | None = None) -> dict:
    """ui/simple/fonts.json and ui/simple/fonts/*.png from the font file `font` (storyfonts.open_fonts' format
    and generator) for the text nodes of `ui`: the characters are storyfonts.shown_characters over the story and
    its own ui/ui.json (`story_ui`), the MasterText rows of the simple UI's text keys and `texts`. The glyph margin
    also covers the story UI's text materials of the same font (`story_fonts`, the story's ui/fonts.json), so the
    glyph pages equal the story UI's when the characters do."""
    tmpfont.require_extra()
    mode, field = languages.mode(language), languages.column(language)[1:]
    lang = textstyle.language_fonts(player)
    bindings = text_bindings(ui, lang, mode)
    story_mats = story_text_materials(story_fonts, lang, mode)
    chars = sorted(set(storyfonts.shown_characters(episode, story_ui, master_dir, field))
                   | set(storyfonts.shown_characters(NO_EPISODE, ui.doc, master_dir, field))
                   | {ord(ch) for s in texts if isinstance(s, str) for ch in s})
    ex, pages_dir = ui.ex, ui.dir / storyfonts.PAGES_DIR
    by_game: dict[str, list[str]] = {}
    for path, t in bindings.items():
        by_game.setdefault(t["localized"]["fontAsset"], []).append(path)
    fonts, textures, materials, missing = {}, {}, {}, set()
    for game_name in sorted(by_game):
        gobj = ex.key_object(textstyle.font_dir(game_name) + game_name)
        gtt = gobj.read_typetree()
        text_mats = {}
        for path in by_game[game_name]:
            mname = bindings[path]["localized"]["material"]
            if mname not in text_mats:
                key = textstyle.font_dir(game_name) + mname
                if not cat.has(key):
                    raise KeyError(f"material key {key}")
                text_mats[mname] = ex.material(ex.key_object(key))
        game = {"pointSize": gtt["m_FaceInfo"]["m_PointSize"], "padding": gtt["m_AtlasPadding"],
                "renderMode": gtt["m_AtlasRenderMode"], "style": {k: gtt[k] for k in storyfonts.STYLE_KEYS},
                "material": ex.material(ex.deref(gobj, gtt["m_Material"]))}
        name = font.asset_name if len(by_game) == 1 else f"{font.asset_name} ({game_name})"
        margin_mats = dict(text_mats)
        for mname in sorted(story_mats.get(game_name, ())):
            key = textstyle.font_dir(game_name) + mname
            if mname not in margin_mats and cat.has(key):
                margin_mats[mname] = ex.material(ex.key_object(key))
        a = storyfonts.open_asset(font, name, chars, game, margin_mats)
        used = {a["renames"][m] for m in text_mats} | {f"{name} Material"}
        a["materials"] = {k: v for k, v in a["materials"].items() if k in used}
        fonts[name] = a["font"]
        textures.update(a["textures"])
        materials.update(a["materials"])
        missing |= a["missing"]
        pages_dir.mkdir(parents=True, exist_ok=True)
        for pname, px in a["pages"]:
            (ui.dir / a["textures"][pname]["texture"]).write_bytes(packed_png(px))
        for path in by_game[game_name]:
            loc = bindings[path]["localized"]
            bindings[path]["localized"] = {**loc, "fontAsset": name, "material": a["renames"][loc["material"]]}
    keywords = storyfonts.text_shaders(ex, ui.dir, materials)
    doc = {"format": storyfonts.FONTS_FORMAT, "language": language, "source": "open", "fonts": fonts,
           "textures": textures, "materials": materials, "materialKeywords": keywords, "texts": bindings,
           "coverage": {"characters": len(chars), "missing": [chr(u) for u in sorted(missing)]},
           "lineBreaking": storyfonts.line_breaking(player)}
    (ui.dir / storyfonts.FONTS_DOC).write_bytes(
        dumps(doc, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8") + b"\n")
    return {"fonts": sorted(fonts), "characters": len(chars), "missing": len(missing), "pages": len(textures)}


def simple_ui(cat, master_dir, player, episode: dict, ldir: Path, language: str, keys: dict, host: dict,
              title_id: str | None, font) -> dict:
    """<ldir>/ui/simple/: ui.json (UISimpleAdvTalkWindow, the window SimpleAdvPlayer.LoadInitialTalkWindow
    attaches; for an area talk UISystemMessageWidget, which SystemMessageManager.ShowMessageByTextIdAsync shows the
    spot's title with, and `texts.areaTitle`) and its open fonts."""
    sdir = Path(ldir) / "ui" / SIMPLE_DIR
    story_ui = json.loads((Path(ldir) / "ui" / "ui.json").read_text(encoding="utf-8"))
    fonts_doc = Path(ldir) / "ui" / storyfonts.FONTS_DOC
    story_fonts = json.loads(fonts_doc.read_text(encoding="utf-8")) if fonts_doc.is_file() else None
    ui = UiDoc(cat, player, sdir, languages.mode(language))
    ui.add(keys["talkWindow"], ui.prefab(keys["talkWindow"]))
    texts = {}
    if host.get("talk") == "area":
        ui.add(keys["systemMessage"], ui.prefab(keys["systemMessage"]))
        field = languages.column(language)
        row = _rows(master_dir, "MasterText").get(title_id)
        if row is None:
            raise KeyError(f"area talk title {title_id} not in MasterText")
        texts["areaTitle"] = row.get(field) or ""
    ui.write(f"SimpleAdvPlayer talk window UI data, {language}", head={"language": advui.language_doc(language)},
             tail={"texts": texts} if texts else None)
    return open_fonts(ui, cat, player, episode, story_ui, language, font, master_dir, list(texts.values()),
                      story_fonts)


# ---------------------------------------------------------------- entry
def build(cat, master_dir, player, episode: dict, story_dir, language_dirs: dict, groups: list, *,
          fonts: str = "open", font_files: dict | None = None) -> dict | None:
    """The host data of `episode` (episode.json of story.build) when it is an Overlay episode (master
    `_playbackMode` 1; else None and nothing written): <story_dir>/host/ (host.json, ui/, spot/, shaders/) and in
    each language directory of `language_dirs` {language: dir with the story's ui/ui.json} its ui/simple/.
    `groups`: the story groups of the episode (storysite.story_groups), which name the host (host_of); `fonts`:
    "open" with `font_files` {language: storyfonts.FontFile}. -> {kind, doc, ui} (the story manifest's `host`)."""
    if fonts not in FONT_MODES:
        raise ValueError(f"fonts must be one of {FONT_MODES}, not {fonts!r}")
    if (episode.get("master") or {}).get("_playbackMode") != OVERLAY:
        return None
    host = host_of(groups)
    if fonts == "game":
        raise NotImplementedError("the talk window UI of an Overlay story has open fonts only (fonts='open')")
    font_files = dict(font_files or {})
    lost = [lang for lang in language_dirs if lang not in font_files]
    if lost:
        raise ValueError(f"no font file for {lost}")
    keys = {k: emb_key(cat, ADDRESSES[k]) for k in ("cameraTarget", "talkWindow")}
    if host.get("talk") == "area":
        keys["systemMessage"] = emb_key(cat, ADDRESSES["systemMessage"])
    for k in KIND_KEYS[host["kind"]]:
        try:
            keys[k] = emb_key(cat, ADDRESSES[k])
        except KeyError:
            if k != "spotScene":
                raise
            keys[k] = None                            # scene root: null / [] (scene_root)
    hdir = Path(story_dir) / HOST_DIR
    shutil.rmtree(hdir, ignore_errors=True)
    ui = UiDoc(cat, player, hdir / "ui")
    cam = camera_target(ui.prefab(keys["cameraTarget"]))
    home = afterlive = None
    if host["kind"] == "home":
        overlay, layout, opened = home_ui(ui, keys)
        home = home_host(cat, master_dir, player, host, hdir, keys)
    else:
        overlay, layout, opened, afterlive = afterlive_ui(ui, keys)
    ui.write(f"host screen UI data of an Overlay story ({host['kind']}), no texts")
    doc = {"format": FORMAT,
           "about": "Host screen of an Overlay story (SimpleAdvPlayer): the slot capture camera, the SimpleAdv "
                    "root and its layout, the host UI and the home spot or live result screen it is shown over",
           "advId": episode.get("advId"), "kind": host["kind"], "group": host["group"], "keys": keys,
           "cameraTarget": cam, "overlayRoot": overlay, "layoutRoot": layout, "openedSequences": opened,
           "ui": HOST_UI_DOC, "home": home, "afterlive": afterlive}
    write_json(hdir / "host.json", doc)
    for language, ldir in language_dirs.items():
        shutil.rmtree(Path(ldir) / "ui" / SIMPLE_DIR, ignore_errors=True)
        simple_ui(cat, master_dir, player, episode, Path(ldir), language, keys, host,
                  (home or {}).get("titleTextId"), font_files[language])
    return {"kind": host["kind"], "doc": HOST_DOC, "ui": SIMPLE_DOC}
