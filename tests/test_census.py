from types import SimpleNamespace

import pytest
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from nnnotes import census, contract, unity
from nnnotes.contract import Input
from nnnotes.stages import Env, Pending, describe, execute, registry
from nnnotes.store import Store

SCRIPTS = "CAB-5c1e0000000000000000000000000000"


def node(cls: str, *fields: str) -> TypeTreeNode:
    return TypeTreeNode(0, cls, "Base", -1, 1, m_Children=[TypeTreeNode(1, "x", f, -1, 0) for f in fields])


class Obj:
    """An object as UnityPy reads it: header reads get the fields of the node they are given."""

    def __init__(self, pid, class_id, cls, fields: dict, *, size=100, type_hash=b"\x01" * 16, broken=False):
        self.path_id, self.class_id, self.byte_size = pid, class_id, size
        self.type = SimpleNamespace(name=cls)
        self.serialized_type = SimpleNamespace(old_type_hash=type_hash)
        self.fields, self.broken, self.reads = fields, broken, []
        self.node = node(cls, *fields)

    def _get_typetree_node(self):
        return self.node

    def read_typetree(self, nodes=None, wrap=False, check_read=True):
        if self.broken:
            raise ValueError(f"cannot read <obj at 0x{id(self):x}>")
        names = [c.m_Name for c in (nodes or self.node).m_Children]
        self.reads.append(names)
        return {k: self.fields[k] for k in names}


def ext(path, guid=b"", typ=0):
    return SimpleNamespace(path=path, guid=guid, type=typ)


def sfile(name, objs, externals=()):
    return SimpleNamespace(name=name, objects={o.path_id: o for o in objs}, externals=list(externals),
                           unity_version="6000.0.0f1", _m_target_platform=13, _enable_type_tree=True)


def ptr(fid, pid):
    return {"m_FileID": fid, "m_PathID": pid}


def scene_bundle():
    """A bundle with two serialized files sharing path ids, a resource file, MonoBehaviours with and without a
    script, an AssetBundle object and an unreadable object."""
    main = "CAB-aaaa0000000000000000000000000000"
    mb = Obj(7, 114, "MonoBehaviour", {"m_GameObject": ptr(0, 3), "m_Enabled": 1, "m_Script": ptr(1, 42),
                                       "m_Name": "", "big": [0] * 10})
    null_mb = Obj(8, 114, "MonoBehaviour", {"m_GameObject": ptr(0, 3), "m_Enabled": 1, "m_Script": ptr(0, 0),
                                            "m_Name": "n"})
    tex = Obj(-5, 28, "Texture2D", {"m_Name": "tex", "m_Width": 4, "image data": b"\0" * 64}, size=500)
    ab = Obj(1, 142, "AssetBundle", {
        "m_Name": "scene_x", "m_PreloadTable": [],
        "m_Container": [("Assets/Scenes/X.unity", {"preloadIndex": 0, "preloadSize": 0, "asset": ptr(0, 0)}),
                        ("Assets/Tex/tex.png", {"preloadIndex": 0, "preloadSize": 1, "asset": ptr(0, -5)}),
                        ("Assets/Tex/tex.png", {"preloadIndex": 1, "preloadSize": 1, "asset": ptr(2, 3)})],
        "m_MainAsset": {}, "m_RuntimeCompatibility": 1, "m_AssetBundleName": "0123_abcd.bundle",
        "m_Dependencies": ["dep_1.bundle"], "m_IsStreamedSceneAssetBundle": True,
        "m_SceneHashes": [("Assets/Scenes/X.unity", "ffee")]})
    tf = Obj(3, 4, "Transform", {"m_GameObject": ptr(0, 9), "m_LocalPosition": {}}, type_hash=None)
    bad = Obj(11, 1, "GameObject", {"m_Component": [], "m_Name": "x"}, broken=True)
    shared = sfile(main + ".sharedAssets", [tex, ab, Obj(3, 1, "GameObject", {"m_Component": [], "m_Layer": 0,
                                                                              "m_Name": "go", "m_IsActive": 1})],
                   [ext("archive:/" + SCRIPTS + "/" + SCRIPTS), ext("Library/unity default resources", b"\x0f" * 16)])
    scene = sfile(main, [tf, mb, null_mb, bad], [ext("archive:/" + SCRIPTS + "/" + SCRIPTS)])
    res = SimpleNamespace(Length=1234)
    bundle = SimpleNamespace(files={scene.name: scene, main + ".resS": res, shared.name: shared})
    return SimpleNamespace(files={"x.bundle": bundle}), {"mb": mb, "tex": tex, "tf": tf}


def scripts_bundle():
    s = [Obj(42, 115, "MonoScript", {"m_Name": "Widget", "m_ExecutionOrder": 0, "m_ClassName": "Widget",
                                     "m_Namespace": "Game.UI", "m_AssemblyName": "Game.dll"}),
         Obj(43, 115, "MonoScript", {"m_Name": "Top", "m_ExecutionOrder": 0, "m_ClassName": "Top",
                                     "m_Namespace": "", "m_AssemblyName": "Assembly-CSharp.dll"})]
    return SimpleNamespace(files={"s.bundle": SimpleNamespace(files={SCRIPTS: sfile(SCRIPTS, s)})})


def test_census_of_a_scene_bundle():
    env, objs = scene_bundle()
    doc = census.census_env(env)
    main = "CAB-aaaa0000000000000000000000000000"
    assert doc["schema"] == contract.CENSUS
    assert [f["name"] for f in doc["files"]] == [main, main + ".sharedAssets"]
    assert doc["resources"] == [{"name": main + ".resS", "size": 1234}]
    scene, shared = doc["files"]
    assert [o["pathId"] for o in scene["objects"]] == [3, 7, 8, 11]
    assert [o["pathId"] for o in shared["objects"]] == [-5, 1, 3]             # path id 3 in both files
    assert scene["externals"] == [{"file": SCRIPTS, "path": "archive:/" + SCRIPTS + "/" + SCRIPTS, "guid": None,
                                   "type": 0}]
    assert shared["externals"][1] == {"file": "unity default resources", "path": "Library/unity default resources",
                                      "guid": "0f" * 16, "type": 0}
    assert (scene["unityVersion"], scene["platform"], scene["typetree"]) == ("6000.0.0f1", 13, True)
    tf, mb, null_mb, bad = scene["objects"]
    assert tf == {"pathId": 3, "classId": 4, "class": "Transform", "byteSize": 100, "typeHash": None}
    assert mb == {"pathId": 7, "classId": 114, "class": "MonoBehaviour", "byteSize": 100, "typeHash": "01" * 16,
                  "name": "", "script": {"file": SCRIPTS, "pathId": 42}}
    assert null_mb["script"] is None and null_mb["name"] == "n"
    assert "name" not in bad and doc["errors"] == [{"object": main + ":11",
                                                    "message": "ValueError: cannot read <obj at 0x?>"}]
    tex = shared["objects"][0]
    assert tex["name"] == "tex" and tex["byteSize"] == 500
    # headers only: a MonoBehaviour is read up to m_Name, a texture up to m_Name, a transform not at all
    assert objs["mb"].reads == [["m_GameObject", "m_Enabled", "m_Script", "m_Name"]]
    assert objs["tex"].reads == [["m_Name"]] and objs["tf"].reads == []
    (ab,) = doc["assetBundles"]
    assert ab["file"] == main + ".sharedAssets" and ab["pathId"] == 1 and ab["assetBundleName"] == "0123_abcd.bundle"
    assert ab["dependencies"] == ["dep_1.bundle"] and ab["isStreamedSceneAssetBundle"] is True
    assert ab["sceneHashes"] == [["Assets/Scenes/X.unity", "ffee"]]
    assert ab["container"] == [
        {"path": "Assets/Scenes/X.unity", "file": None, "pathId": None, "preloadIndex": 0, "preloadSize": 0},
        {"path": "Assets/Tex/tex.png", "file": main + ".sharedAssets", "pathId": -5, "preloadIndex": 0,
         "preloadSize": 1},
        {"path": "Assets/Tex/tex.png", "file": "unity default resources", "pathId": 3, "preloadIndex": 1,
         "preloadSize": 1}]
    assert census.container(doc) == ab["container"]
    assert doc["classes"] == {"AssetBundle": 1, "GameObject": 2, "MonoBehaviour": 2, "Texture2D": 1, "Transform": 1}
    assert doc["objects"] == 7 and doc["scripts"] == []
    assert census.script_refs(doc) == [SCRIPTS + ":42"]
    assert [(f, o["pathId"]) for f, o in census.objects(doc)][:2] == [(main, 3), (main, 7)]
    assert census.facts(doc) == {"files": 2, "objects": 7, "classes": doc["classes"], "scripts": 0, "scriptRefs": 1,
                                 "errors": 1}
    again, _ = scene_bundle()
    assert contract.encode(census.census_env(again)) == contract.encode(doc)    # deterministic


def test_scripts_named_by_clip_bindings():
    main = "CAB-cccc0000000000000000000000000000"

    def binding(tid, script):
        return {"path": 1, "attribute": 2, "script": script, "typeID": tid, "customType": 0, "isPPtrCurve": 0}
    clip = Obj(5, 74, "AnimationClip", {
        "m_Name": "fade", "m_Legacy": False, "m_MuscleClip": {},
        "m_ClipBindingConstant": {"genericBindings": [binding(114, ptr(1, 42)), binding(4, ptr(0, 0)),
                                                      binding(114, ptr(1, 42)), binding(114, ptr(0, 6)),
                                                      binding(114, ptr(5, 1))], "pptrCurveMapping": []},
        "m_Events": []})
    plain = Obj(6, 74, "AnimationClip", {"m_Name": "move", "m_ClipBindingConstant": {"genericBindings": [
        binding(4, ptr(0, 0))]}, "m_Events": []})
    local = Obj(7, 115, "MonoScript", {"m_Name": "L", "m_ClassName": "L", "m_Namespace": "", "m_AssemblyName": "A.dll"})
    f = sfile(main, [clip, plain, local], [ext("archive:/" + SCRIPTS + "/" + SCRIPTS)])
    doc = census.census_env(SimpleNamespace(files={"c.bundle": SimpleNamespace(files={main: f})}))
    rec, other, _ = doc["files"][0]["objects"]
    assert rec["bindingScripts"] == [{"file": SCRIPTS, "pathId": 42}, {"file": main, "pathId": 6},
                                     {"fileId": 5, "pathId": 1}]
    assert rec["name"] == "fade" and "bindingScripts" not in other and "script" not in rec
    assert clip.reads == [["m_Name", "m_Legacy", "m_MuscleClip", "m_ClipBindingConstant"]]    # not m_Events
    assert census.script_refs(doc) == [SCRIPTS + ":42", main + ":6"]
    assert census.facts(doc)["scriptRefs"] == 2
    assert census.CensusStage.version == 2


def test_monoscripts_and_unknown_file_index():
    doc = census.census_env(scripts_bundle())
    assert doc["scripts"] == [
        {"file": SCRIPTS, "pathId": 42, "assembly": "Game.dll", "namespace": "Game.UI", "class": "Widget",
         "name": "Widget"},
        {"file": SCRIPTS, "pathId": 43, "assembly": "Assembly-CSharp.dll", "namespace": "", "class": "Top",
         "name": "Top"}]
    refs = census._Refs(sfile("CAB-1", []))
    assert refs.ref(ptr(3, 9)) == {"fileId": 3, "pathId": 9} and refs.ref(ptr(0, 0)) is None
    assert refs.ref(None) is None and refs.ref(ptr(0, 5)) == {"file": "CAB-1", "pathId": 5}


def test_bundle_subjects():
    idx = {"bundles": [{"stable": "a", "name": "a_1.bundle"}, {"stable": "b_2.bundle", "name": "b_2.bundle"}]}
    assert census.bundle_subjects(idx) == {"a": idx["bundles"][0], "b_2.bundle": idx["bundles"][1]}


def test_census_stage(tmp_path, monkeypatch):
    bundle = tmp_path / "x_1.bundle"
    bundle.write_bytes(b"UnityFS\0 not really")
    loaded = []

    def load(path):
        loaded.append(str(path))
        return scene_bundle()[0]

    monkeypatch.setattr(unity, "load", load)
    store = Store(tmp_path / "store")
    sha, size = store.identify(bundle, "bundles", bundle.name)
    inp = Input("bundle", sha, size, bundle.name, ({"kind": "file", "path": str(bundle)},))
    stage = census.CensusStage()
    env = Env(store, {"bundles": {"x": inp}})
    assert stage.subjects(env) == ["x"]
    task = describe(stage, "x", None, env)
    assert task.id == "unity.census:x" and task.atoms == census.CensusStage.ATOMS
    assert task.atoms["reader"].startswith("unitypy-")
    assert task.cost.peak_bytes > size
    assert execute(task, store, registry(stage)).status == "ran"
    doc = store.result(task.key)
    (rec,) = doc["artifacts"]
    assert rec["id"] == "unity.census:x#census" and rec["semantics"]["facts"]["objects"] == 7
    assert rec["provenance"]["inputs"] == [{"role": "bundle", "sha256": sha}]
    assert contract.loads(store.read(rec["content"]["sha256"])) == census.census_env(scene_bundle()[0])
    assert loaded == [str(bundle)]
    assert execute(task, store, registry(stage)).status == "hit" and len(loaded) == 1
    with pytest.raises(ValueError, match="expected 'bundle'"):
        describe(stage, "x", None, Env(store, {"bundles": {"x": Input("other", sha, size)}}))
    for waiting in (None, Pending("fetch:x_1.bundle")):
        with pytest.raises(Pending, match="fetch:x"):
            describe(stage, "x", None, Env(store, {"bundles": {"x": waiting}}))
