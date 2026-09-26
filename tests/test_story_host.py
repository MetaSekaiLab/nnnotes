"""The host data of Overlay stories (storyhost.py) on synthetic prefab nodes, hierarchies and master rows: the host
of a story group, the slot capture camera and layout root records, SpotBackground.Prepare, the floor render queue
rule, the room node and material order, the build's entry and return value. Nothing here comes from game data."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from nnnotes import storyhost
from nnnotes.unity import SceneGraph

V0 = {"x": 0.0, "y": 0.0, "z": 0.0}
V1 = {"x": 1.0, "y": 1.0, "z": 1.0}
Q0 = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}


# ---------------------------------------------------------------- synthetic hierarchies
def obj(kind, pid, tt):
    return SimpleNamespace(type=SimpleNamespace(name=kind), path_id=pid, read_typetree=lambda: tt)


def graph(tree, inactive=()):
    """A SceneGraph of `tree` {name: [children as (name, subtree) pairs]} given as nested lists [(name, [...])]:
    GameObject ids 1000 + n, transform ids n, in depth-first order; names in `inactive` are saved inactive."""
    objs, ids = [], {}

    def walk(name, kids, father):
        tf = len(ids) + 1
        ids[name] = tf
        objs.append(obj("GameObject", 1000 + tf, {"m_Name": name.split("#")[0], "m_IsActive": name not in inactive,
                                                   "m_Layer": 0, "m_Component": []}))
        entry = {"m_GameObject": {"m_FileID": 0, "m_PathID": 1000 + tf},
                 "m_Father": {"m_FileID": 0, "m_PathID": father}, "m_Children": [],
                 "m_LocalPosition": V0, "m_LocalRotation": Q0, "m_LocalScale": V1}
        objs.append(obj("Transform", tf, entry))
        for child, sub in kids:
            entry["m_Children"].append({"m_FileID": 0, "m_PathID": walk(child, sub, tf)})
        return tf
    for name, kids in tree:
        walk(name, kids, 0)
    return SceneGraph(SimpleNamespace(objects=objs)), ids


BACKGROUND = [("bg", [("light", [("table", []), ("chair", []), ("wall_floor_shadow", [("floor_mesh", [])]),
                                 ("obj_floor", [("mesh_a", []), ("mesh_b", [])])])]),
              ("model", [("table#m", [])])]


def test_prepare_matches_the_situation_without_clone_suffix():
    g, ids = graph(BACKGROUND, inactive=("chair",))
    entries = [("other", [1000 + ids["table"]]), ("sit_01", [1000 + ids["table"], None])]
    got = storyhost.prepare(g, ids["bg"], "sit_01(Clone)", entries)
    assert got["name"] == "sit_01" and got["matched"]
    assert got["activate"] == [ids["table"], ids["chair"], ids["wall_floor_shadow"], ids["obj_floor"]]
    assert got["hide"] == [1000 + ids["table"], None]
    ov = got["overrides"]
    assert ov[1000 + ids["chair"]] is True and ov[1000 + ids["table"]] is False     # hidden after activation
    assert not storyhost.active_in_hierarchy(g, ids["table"], ov)
    assert storyhost.active_in_hierarchy(g, ids["chair"], ov)
    assert not storyhost.active_in_hierarchy(g, ids["chair"], {})                  # saved state


def test_prepare_without_a_matching_entry_keeps_the_prefab_state():
    g, ids = graph(BACKGROUND)
    got = storyhost.prepare(g, ids["bg"], "sit_09(Clone)", [("sit_01", [1000 + ids["table"]])])
    assert got == {"name": "sit_09", "matched": False, "activate": [], "hide": [], "overrides": {}}
    g2, ids2 = graph([("bg", [])])
    with pytest.raises(RuntimeError, match="GetChild"):
        storyhost.prepare(g2, ids2["bg"], "x", [])


def test_floor_rule_skips_shadow_names_and_inactive_renderers():
    g, ids = graph(BACKGROUND, inactive=("mesh_a",))
    floor = storyhost.find_first_child_with_name(g, ids["bg"], "floor", "shadow")
    assert floor == ids["floor_mesh"]              # below the excluded "wall_floor_shadow", depth first
    g2, ids2 = graph([("bg", [("a_shadow_floor", []), ("obj_floor", [("mesh_a", []), ("mesh_b", [])])])],
                     inactive=("mesh_a",))
    floor = storyhost.find_first_child_with_name(g2, ids2["bg"], "floor", "shadow")
    assert floor == ids2["obj_floor"]
    renderers = {ids2["mesh_a"], ids2["mesh_b"]}
    hit = storyhost.component_in_children(g2, floor, renderers.__contains__,
                                          lambda t: storyhost.active_in_hierarchy(g2, t, {}))
    assert hit == ids2["mesh_b"]                   # GetComponentInChildren skips the inactive mesh_a
    assert storyhost.component_in_children(g2, floor, set().__contains__, lambda t: True) is None
    assert storyhost.find_first_child_with_name(g2, ids2["mesh_b"], "floor", "shadow") is None


def test_under_tells_the_placed_prefab_from_other_roots():
    g, ids = graph(BACKGROUND)
    assert storyhost.under(g, ids["mesh_a"], ids["bg"]) and storyhost.under(g, ids["bg"], ids["bg"])
    assert not storyhost.under(g, ids["table#m"], ids["bg"])


# ---------------------------------------------------------------- room order
class Mesh:
    def __init__(self, name, tris):
        self.m_Name, self.tris = name, [np.asarray(t, np.int64).reshape(-1, 3) for t in tris]


def pp(pid):
    return SimpleNamespace(path_id=pid)


def arrays(mesh):
    return None if mesh.m_Name == "empty" else (None, None, None, mesh.tris)


def test_glb_json_reads_the_json_chunk(tmp_path):
    import struct
    js = json.dumps({"nodes": [{"name": "a"}]}).encode("utf-8")
    js += b" " * (-len(js) % 4)
    (tmp_path / "r.glb").write_bytes(b"glTF" + struct.pack("<II", 2, 20 + len(js)) + struct.pack("<I", len(js))
                                     + b"JSON" + js)
    assert storyhost.glb_json(tmp_path / "r.glb") == {"nodes": [{"name": "a"}]}
    (tmp_path / "x.glb").write_bytes(b"XXXX" + bytes(20))
    with pytest.raises(RuntimeError, match="not a binary glTF"):
        storyhost.glb_json(tmp_path / "x.glb")


def test_glb_order_follows_room_extract_room():
    tri = [[0, 1, 2]]
    drawn = [(1, Mesh("a", [tri, tri]), [pp(20), pp(10)]),
             (2, Mesh("empty", [tri]), [pp(30)]),              # no vertex data: no node
             (3, Mesh("none", [[]]), [pp(40)]),                # no triangles
             (4, Mesh("nomat", [tri]), [pp(0)]),               # its only submesh has no material
             (5, Mesh("b", [[], tri]), [pp(50), pp(10)])]      # the empty submesh does not number pp(50)
    nodes, mats = storyhost.glb_order(drawn, arrays)
    assert nodes == [(1, "a"), (5, "b")]
    assert [m.path_id for m in mats] == [20, 10]
    with pytest.raises(NotImplementedError, match="2 submeshes, 1 materials"):
        storyhost.glb_order([(1, Mesh("c", [tri, tri]), [pp(1)])], arrays)


def tex(pid, sx=1.0):
    return {"m_Texture": {"m_FileID": 0, "m_PathID": pid}, "m_Scale": {"x": sx, "y": 1.0},
            "m_Offset": {"x": 0.0, "y": 0.5}}


def mat_tt(name, envs, keywords=("K",)):
    return {"m_Name": name, "m_ValidKeywords": list(keywords), "m_CustomRenderQueue": -1,
            "m_SavedProperties": {"m_TexEnvs": envs, "m_Floats": [["_Cutoff", 0.5]], "m_Ints": [],
                                  "m_Colors": [["_Color", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}]]}}


def test_glb_textures_and_room_materials_keep_the_glb_order():
    a = mat_tt("a", [["_MainTex", tex(7)], ["_Mask", tex(9)]])
    b = mat_tt("b", [["_MainTex", tex(9)], ["_Extra", tex(7)]])
    c = mat_tt("c", [["_BaseMap", tex(8)], ["_MainTex", tex(6)]])
    index = storyhost.glb_textures([(a, "Unlit/Transparent"), (b, "Unlit/Transparent Cutout"),
                                    (c, "Universal Render Pipeline/Unlit")])
    assert index == {7: 0, 9: 1, 8: 2}                 # main slot only, first use; URP shaders use _BaseMap
    rec = storyhost.room_material(a, "Unlit/Transparent", index)
    assert rec["texEnvs"]["_Mask"] == {"texture": 1, "scale": [1.0, 1.0], "offset": [0.0, 0.5]}
    assert storyhost.room_material(c, "U", index)["texEnvs"]["_MainTex"]["texture"] is None
    assert rec["floats"] == {"_Cutoff": 0.5} and rec["keywords"] == ["K"] and rec["renderQueue"] == -1
    empty = mat_tt("d", [["_MainTex", tex(0)]])
    assert storyhost.room_material(empty, "U", index)["texEnvs"]["_MainTex"]["texture"] is None


# ---------------------------------------------------------------- hosts, records
def group(kind, **kw):
    return {"kind": kind, "id": 7, **kw}


def test_host_of_maps_the_story_group():
    spot = {"id": 10, "names": {}}
    char = [{"id": 3, "names": {}}]
    assert storyhost.host_of([group("chapter"), group("spotTalk", spot=spot, characters=char)]) == {
        "kind": "home", "group": {"kind": "spotTalk", "id": 7}, "spotId": 10, "talk": "tap", "characterId": 3}
    assert storyhost.host_of([group("spot", spot=spot, characters=[])])["talk"] == "area"
    assert storyhost.host_of([group("spot", spot=spot, characters=char)])["characterId"] is None
    assert storyhost.host_of([group("liveResult", characters=char)]) == {
        "kind": "afterlive", "group": {"kind": "liveResult", "id": 7}}
    with pytest.raises(ValueError, match="not 0"):
        storyhost.host_of([group("chapter")])
    with pytest.raises(ValueError, match="not 2"):
        storyhost.host_of([group("liveResult"), group("spot", spot=spot)])


def node(path, *components, **kw):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "active": True, "layer": 5, "localPosition": V0,
            "localRotation": Q0, "localScale": V1, "components": list(components), **kw}


LAYOUT = {"type": "MonoBehaviour", "class": "SimpleAdvLayoutRoot", "m_Enabled": 1, "m_Name": "",
          "_layoutSurfaceReferenceSize": {"x": 1220.0, "y": 1020.0}, "_characterSlotCount": 3}


def test_layout_root_record():
    nodes = [node("W"), node("W/Root", LAYOUT)]
    assert storyhost.layout_root(nodes, "W/Root") == {"m_Enabled": 1, "_layoutSurfaceReferenceSize":
                                                      {"x": 1220.0, "y": 1020.0}, "_characterSlotCount": 3}
    with pytest.raises(RuntimeError, match="no SimpleAdvLayoutRoot"):
        storyhost.layout_root(nodes, "W")
    rec = storyhost.node_record(nodes[1])
    assert rec["simpleAdvLayoutRoot"]["_characterSlotCount"] == 3 and rec["path"] == "W/Root"


def camera_nodes():
    crt = {"type": "MonoBehaviour", "class": "CameraTargetRenderer", "m_Enabled": 1, "m_Name": "",
           "_stage": {"transform": "C/Container/Stage"},
           "_captureCamera": {"component": "Camera", "gameObject": "C/Container/CaptureCamera"}}
    cam = {"type": "Camera", "m_ClearFlags": 2, "m_BackGroundColor": {"r": 0.2, "g": 0.3, "b": 0.5, "a": 0.0},
           "near clip plane": 0.3, "far clip plane": 1000.0, "field of view": 60.0, "orthographic": False,
           "orthographic size": 0.6, "m_HDR": True, "m_AllowMSAA": True}
    data = {"type": "MonoBehaviour", "class": "UniversalAdditionalCameraData", "m_RendererIndex": -1,
            "m_RenderPostProcessing": 0, "m_Antialiasing": 0}
    return [node("C", crt), node("C/Container"),
            node("C/Container/CaptureCamera", cam, data, localPosition={"x": 0.0, "y": 0.0, "z": -1.0}),
            node("C/Container/Stage")]


def test_camera_target_record():
    got = storyhost.camera_target(camera_nodes())
    assert got["container"] == {"path": "C/Container", "localPosition": V0, "localRotation": Q0, "localScale": V1}
    assert got["stage"]["path"] == "C/Container/Stage"
    cam = got["camera"]
    assert cam["localPosition"]["z"] == -1.0 and cam["fieldOfView"] == 60.0 and cam["clearFlags"] == 2
    assert cam["backgroundColor"]["a"] == 0.0 and cam["rendererIndex"] == -1 and cam["renderPostProcessing"] is False
    assert set(cam) == {"path", "localPosition", "localRotation", "fieldOfView", "near", "far", "orthographic",
                        "orthographicSize", "clearFlags", "backgroundColor", "hdr", "allowMSAA", "rendererIndex",
                        "renderPostProcessing", "antialiasing"}
    bad = camera_nodes()
    bad[0]["components"][0]["_stage"] = {"transform": "C/Stage"}
    with pytest.raises(RuntimeError, match="one parent"):
        storyhost.camera_target(bad)


def test_node_record_adds_fitters_widgets_and_sequence_calls():
    fitter = {"type": "MonoBehaviour", "class": "AspectRatioFitter", "m_Enabled": 1, "m_AspectMode": 4,
              "m_AspectRatio": 1.5}
    widget = {"type": "MonoBehaviour", "class": "UIThingWidget", "m_Enabled": 1, "_canvasSortOrder": 100,
              "_useBlur": 0}
    call = {"m_Target": {"component": "MonoBehaviour", "gameObject": "W/View", "class": "SimpleAnimationTrigger"},
            "m_MethodName": "PlayAnimation", "m_Mode": 5,
            "m_Arguments": {"m_StringArgument": "Show", "m_FloatArgument": 0.0, "m_IntArgument": 0,
                            "m_BoolArgument": 0}}
    seq = {"type": "MonoBehaviour", "class": "DOTweenSequence", "updateType": 0, "isSpeedBased": 0,
           "_list": [{"_commandType": 1, "_duration": 2.0, "_tweenEvent": {"m_PersistentCalls": {"m_Calls": [call]}}},
                     {"_commandType": 0, "_duration": 0.5}]}
    rec = storyhost.node_record(node("W", fitter, widget, seq))
    assert rec["aspectRatioFitter"] == {"m_Enabled": 1, "m_AspectMode": 4, "m_AspectRatio": 1.5}
    assert rec["widget"] == {"class": "UIThingWidget", "_canvasSortOrder": 100, "_useBlur": 0}
    first, second = rec["tweenSequence"]["list"]
    assert first["calls"] == [{"target": "W/View", "class": "SimpleAnimationTrigger", "method": "PlayAnimation",
                               "string": "Show", "float": 0.0, "int": 0, "bool": 0, "mode": 5}]
    assert "calls" not in second
    assert storyhost._call_targets([node("W", seq), node("X", seq)], ["W"]) == ["W/View"]


def test_kept_keeps_subtrees_singles_and_their_ancestors():
    keep = dict(subtrees=["W/View/Root"], singles=["W/Seq"])
    assert [p for p in ("W", "W/View", "W/View/Root", "W/View/Root/Talk", "W/View/Other", "W/Seq", "W/Seq/Child")
            if storyhost.kept(p, **keep)] == ["W", "W/View", "W/View/Root", "W/View/Root/Talk", "W/Seq"]


# ---------------------------------------------------------------- scene, volume, blur, ambient
def test_volume_record_keeps_overrides_only():
    comp = {"type": "MonoBehaviour", "class": "Volume", "m_Enabled": 1, "m_IsGlobal": 1, "priority": 0.0,
            "blendDistance": 0.0, "weight": 0.05,
            "sharedProfile": {"asset": "VolumeProfile", "name": "p", "components": [
                {"asset": "Bloom", "name": "Bloom", "active": 1, "threshold": {"m_OverrideState": 1, "m_Value": 0.5},
                 "scatter": {"m_OverrideState": 0, "m_Value": 1.0}}, None]}}
    assert storyhost.volume_record(comp, 3) == {
        "enabled": True, "isGlobal": True, "weight": 0.05, "priority": 0.0, "blendDistance": 0.0, "layer": 3,
        "profile": "p", "components": [{"class": "Bloom", "active": True, "threshold": 0.5}]}


def test_world_trs_composes_the_parents():
    turn = {"x": 0.0, "y": 1.0, "z": 0.0, "w": 0.0}                     # 180 degrees about y
    nodes = [node("R", localPosition={"x": 1.0, "y": 0.0, "z": 0.0}, localRotation=turn,
                  localScale={"x": 2.0, "y": 2.0, "z": 2.0}),
             node("R/O", localPosition={"x": 1.0, "y": 0.0, "z": 3.0})]
    got = storyhost.world_trs(nodes, "R/O")
    assert [got["position"][a] for a in "xyz"] == pytest.approx([-1.0, 0.0, -6.0])
    assert got["rotation"] == {"x": 0.0, "y": 1.0, "z": 0.0, "w": 0.0} and got["scale"]["y"] == 2.0


def test_blur_record_reads_renderer_zero():
    feat = {"class": storyhost.BLUR_FEATURE, "m_Active": 1, "_blurIterations": 3, "_blurOffset": 1.0,
            "_blurDownsample": 1, "_blurBlendRateMax": 0.3, "_dualKawaseBlurShader": "Hidden/Blur"}
    g = {"defaultPipeline": "P", "pipelines": {"P": {"m_RendererDataList": ["R0", "R1"], "m_DefaultRendererIndex": 1},
                                               "Q": {"m_RendererDataList": ["R0"], "m_DefaultRendererIndex": 0}},
         "renderers": {"R0": {"m_RendererFeatures": [{"class": "Other"}, feat]}, "R1": {"m_RendererFeatures": []}}}
    assert storyhost.blur_record(g) == {"renderer": "R0", "active": True, "iterations": 3, "offset": 1.0,
                                        "downsample": 1, "blendRateMax": 0.3, "shader": "Hidden/Blur"}
    g["pipelines"]["Q"]["m_RendererDataList"] = ["R1"]
    with pytest.raises(NotImplementedError):
        storyhost.blur_record(g)


def write_table(d, name, rows):
    (d / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")


def test_ambient_record(tmp_path):
    write_table(tmp_path, "MasterSound", [{"_id": 31, "_soundCueSheetID": 4, "_cueName": "amb_cafe"}])
    write_table(tmp_path, "MasterSoundCueSheet", [{"_id": 4, "_cueSheetName": "se_amb"}])
    assert storyhost.ambient_record({"_soundId": 31, "ambientSoundVolume": 0.5}, tmp_path) == {
        "soundId": 31, "volume": 0.5, "cueSheet": "se_amb", "cue": "amb_cafe"}
    assert storyhost.ambient_record({"_soundId": 0}, tmp_path) is None
    assert storyhost.ambient_record({"_soundId": 99}, tmp_path)["cue"] is None


# ---------------------------------------------------------------- build
def test_build_writes_nothing_for_a_full_adv_episode(tmp_path):
    ep = {"advId": 1, "master": {"_playbackMode": 0}}
    lang = tmp_path / "lang" / "ja"
    assert storyhost.build(None, tmp_path, None, ep, tmp_path / "story", {"ja": lang}, [group("liveResult")]) is None
    assert not (tmp_path / "story").exists() and not lang.exists()
    with pytest.raises(ValueError, match="fonts"):
        storyhost.build(None, tmp_path, None, ep, tmp_path / "story", {}, [], fonts="bitmap")
    ep1 = {"advId": 1, "master": {"_playbackMode": 1}}
    with pytest.raises(NotImplementedError, match="open fonts"):
        storyhost.build(None, tmp_path, None, ep1, tmp_path / "story", {}, [group("liveResult")], fonts="game")
    with pytest.raises(ValueError, match="no font file"):
        storyhost.build(None, tmp_path, None, ep1, tmp_path / "story", {"ja": lang}, [group("liveResult")])


class FakeUi:
    written = []

    def __init__(self, cat, player, out_dir, mode=None):
        self.dir = out_dir

    def prefab(self, key):
        return camera_nodes()

    def write(self, about, head=None, tail=None):
        self.dir.mkdir(parents=True, exist_ok=True)
        FakeUi.written.append(self.dir)


def test_build_returns_the_manifest_entry(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storyhost, "emb_key", lambda cat, a: "Emb" + a)
    monkeypatch.setattr(storyhost, "UiDoc", FakeUi)
    monkeypatch.setattr(storyhost, "afterlive_ui", lambda ui, keys: (
        "W/Root", {"_characterSlotCount": 3}, ["B/In", "W/In", "W/View/In"],
        {"backgroundNode": "B/Bg", "backgroundSprite": "bg", "rewardPanel": "W/Reward", "substitutes": []}))
    monkeypatch.setattr(storyhost, "simple_ui", lambda *a: calls.append((a[4], a[5], a[7], a[8], a[9])))
    story = tmp_path / "story"
    (story / "host" / "stale").mkdir(parents=True)
    ep = {"advId": 5, "master": {"_playbackMode": 1}}
    langs = {"ja": tmp_path / "ja", "en": tmp_path / "en"}
    got = storyhost.build("cat", tmp_path, "player", ep, story, langs, [group("liveResult", characters=[])],
                          font_files={"ja": "font-ja", "en": "font-en"})
    assert got == {"kind": "afterlive", "doc": "host/host.json", "ui": "ui/simple/ui.json"}
    assert not (story / "host" / "stale").exists()
    doc = json.loads((story / "host" / "host.json").read_text(encoding="utf-8"))
    assert doc["format"] == storyhost.FORMAT and doc["kind"] == "afterlive" and doc["home"] is None
    assert doc["overlayRoot"] == "W/Root" and doc["openedSequences"] == ["B/In", "W/In", "W/View/In"]
    assert doc["keys"]["resultWidget"] == "EmbUI/Prefab/UILiveResultWidget" and "systemMessage" not in doc["keys"]
    assert doc["cameraTarget"]["stage"]["path"] == "C/Container/Stage" and doc["ui"] == "host/ui/ui.json"
    assert [(c[0], c[1], c[3], c[4]) for c in calls] == [(tmp_path / "ja", "ja", None, "font-ja"),
                                                        (tmp_path / "en", "en", None, "font-en")]
    again = story / "host" / "host.json"
    first = again.read_bytes()
    storyhost.build("cat", tmp_path, "player", ep, story, langs, [group("liveResult", characters=[])],
                    font_files={"ja": "font-ja", "en": "font-en"})
    assert again.read_bytes() == first


def test_story_text_materials_localize_the_story_ui_texts():
    lang = {"languages": {0: {"fontNames": ["Game-R", "Num-B"], "additionalFontNames": []},
                          2: {"fontNames": ["Game-TC", "Num-TC"], "additionalFontNames": []}},
            "materialTypes": ["Default", "OutlineAdvCommon"]}
    fonts = {"source": "open", "texts": {
        "W/Talk": {"fontAsset": "Game-R SDF", "material": "Game-R SDF Material"},
        "W/Name": {"fontAsset": "Game-R SDF", "material": "Game-R - OutlineAdvCommon"},
        "W/Num": {"fontAsset": "Num-B SDF", "material": "Num-B - Default"}}}
    assert storyhost.story_text_materials(fonts, lang, 2) == {
        "Game-TC SDF": {"Game-TC - Default", "Game-TC - OutlineAdvCommon"}, "Num-TC SDF": {"Num-TC - Default"}}
    assert storyhost.story_text_materials({**fonts, "source": "game"}, lang, 2) == {}
    assert storyhost.story_text_materials(None, lang, 2) == {}


# ---------------------------------------------------------------- clips
def stream(frames):
    """StreamedClip words of [(time, [(curve, c0, c1, c2, c3)])], closed by the empty +inf frame."""
    import struct
    buf = b""
    for t, keys in [*frames, (float("inf"), [])]:
        buf += struct.pack("<fi", t, len(keys)) + b"".join(struct.pack("<i4f", *k) for k in keys)
    return list(struct.unpack(f"<{len(buf) // 4}I", buf))


def binding(path, tid, attr, pptr=False, script=None, crc=0):
    return {"path": path, "pathCrc": crc, "typeID": tid, "attribute": attr, "attributeCrc": 0, "pptr": pptr,
            "int": False, "serializeReference": False, "transformAttr": False, "script": script, "raw": attr}


class FakeClipExporter:
    def __init__(self, rec):
        self.rec = rec

    def clip_record(self, o):
        return self.rec

    def bind_clip(self, o, graph, root):
        self.bound = root

    def deref(self, owner, p):
        return SimpleNamespace(read_typetree=lambda: {"m_Name": f"sprite{p['m_PathID']}"})


def test_clip_view_keys_child_bindings_discrete_dense_and_lost_paths():
    rec = {"name": "In", "tt": {"m_SampleRate": 60.0, "m_Events": []},
           "muscle": {"m_StartTime": 0.0, "m_StopTime": 1.0, "m_LoopTime": 0},
           "streamed": {"curveCount": 1, "discreteCurveCount": 1,
                        "data": stream([(-1.0, [(0, 0.0, 0.0, 0.0, 1.0), (1, 0.0, 0.0, 0.0, 0.0)]),
                                        (0.5, [(0, 0.0, 0.0, -2.0, 1.0), (1, 0.0, 0.0, 0.0, 1.0)])])},
           "dense": {"m_CurveCount": 0, "m_FrameCount": 0, "m_SampleRate": 0.0, "m_BeginTime": 0.0,
                     "m_SampleArray": []},
           "constant": [0.25, 1.0],
           "bindings": [binding("", 225, "m_Alpha"), binding("Logo", 114, "m_Sprite", pptr=True, script=("", "Image")),
                        binding("BgBlack", 225, "m_Alpha"), binding(None, 1, "m_IsActive", crc=77)],
           "pptrCurveMapping": [{"m_FileID": 0, "m_PathID": 5}, {"m_FileID": 0, "m_PathID": 6}]}
    ex = FakeClipExporter(rec)
    got = storyhost.clip_view(ex, None, None, "W/Root")
    assert ex.bound == "W/Root"
    assert got["curves"]["CanvasGroup.m_Alpha"]["keys"][1] == [0.5, 0.0, 0.0, -2.0, 1.0]
    assert got["curves"]["Logo:Image.m_Sprite"] == {"discrete": [[-1.0, 0.0], [0.5, 1.0]]}
    assert got["curves"]["BgBlack:CanvasGroup.m_Alpha"] == {"constant": 0.25}
    assert got["curves"]["#77:GameObject.m_IsActive"] == {"constant": 1.0}
    assert got["pptrCurveMapping"] == ["sprite5", "sprite6"] and got["unresolvedPaths"] == [77]
    rec["bindings"][0]["pptr"] = True                    # a reference curve among the float curves
    with pytest.raises(NotImplementedError, match="outside the discrete curves"):
        storyhost.clip_view(ex, None, None, "W/Root")


def test_ui_only_characters():
    """The characters of a UI alone: storyfonts.shown_characters over the empty episode storyhost passes."""
    from nnnotes import storyfonts
    ui_doc = {"nodes": [], "masterIdTexts": {"a": {"text": "Ab"}}}
    assert storyfonts.shown_characters(storyhost.NO_EPISODE, ui_doc, None, "japanese") == [ord("A"), ord("b")]
