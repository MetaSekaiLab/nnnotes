import pytest

from nnnotes.pipeline import census, contract
from nnnotes.pipeline.contract import Input
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.stages import Env, Pending, describe, execute, registry
from nnnotes.unity import reader as unity
from nnnotes.unity import scripttypes
from tests.helpers.fakeunity import FakeEnvironment, pptr

SCRIPTS = "CAB-5c1e0000000000000000000000000000"
MAIN = "CAB-aaaa0000000000000000000000000000"
SHARED = MAIN + ".sharedAssets"
HASH = bytes.fromhex("01" * 16)
# a MonoBehaviour's tree in Unity's layout: its header, a texture reference and a float
ROWS = [
    [0, "MonoBehaviour", "Base", 0],
    [1, "PPtr<GameObject>", "m_GameObject", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "UInt8", "m_Enabled", 0x4000],
    [1, "PPtr<MonoScript>", "m_Script", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "string", "m_Name", 0],
    [1, "PPtr<Texture2D>", "target", 0],
    [2, "int", "m_FileID", 0],
    [2, "SInt64", "m_PathID", 0],
    [1, "float", "volume", 0],
]


def tf(go, children=(), father=0):
    return {
        "m_GameObject": pptr(go),
        "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "m_LocalPosition": {"x": 0.0, "y": 0.0, "z": 0.0},
        "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
        "m_Children": [pptr(c) for c in children],
        "m_Father": pptr(father),
    }


def go(name, comps):
    return {"m_Component": [{"component": pptr(c)} for c in comps], "m_Layer": 0, "m_Name": name, "m_IsActive": 1}


def mono(go_pid, script, name="", **fields):
    return {"m_GameObject": pptr(go_pid), "m_Enabled": 1, "m_Script": script, "m_Name": name, **fields}


def asset_bundle():
    def entry(index, size, asset):
        return {"preloadIndex": index, "preloadSize": size, "asset": asset}

    return {
        "m_Name": "scene_x",
        "m_PreloadTable": [],
        "m_Container": [
            ("Assets/Scenes/X.unity", entry(0, 0, pptr(0))),
            ("Assets/Tex/tex.png", entry(0, 1, pptr(-5))),
            ("Assets/Tex/tex.png", entry(1, 1, pptr(3, 2))),
        ],
        "m_MainAsset": {"preloadIndex": 0, "preloadSize": 0, "asset": pptr(0)},
        "m_RuntimeCompatibility": 1,
        "m_AssetBundleName": "0123_abcd.bundle",
        "m_Dependencies": ["dep_1.bundle"],
        "m_IsStreamedSceneAssetBundle": True,
        "m_SceneHashes": [("Assets/Scenes/X.unity", "ffee")],
    }


def scene_bundle() -> FakeEnvironment:
    """A scene bundle: two serialized files sharing path ids, a resource file, a hierarchy (a root GameObject with a
    child and MonoBehaviours, one of them without a script), a texture and the AssetBundle object, an unreadable
    object."""
    env = FakeEnvironment()
    scene = env.file(MAIN, externals=["archive:/" + SCRIPTS + "/" + SCRIPTS, "archive:/" + SHARED + "/" + SHARED])
    scene.add(1, "GameObject", go("root", [2, 7]))
    scene.add(2, "Transform", tf(1, [4]))
    scene.add(3, "GameObject", go("child", [4, 8]))
    scene.add(4, "Transform", tf(3, father=2))
    scene.add(7, "MonoBehaviour", mono(1, pptr(42, 1), "", tex=pptr(-5, 2), big=[0] * 10), type_hash=HASH)
    scene.add(8, "MonoBehaviour", mono(3, pptr(0), "n", other=pptr(9, 7)), type_hash=HASH)
    scene.add(11, "Material", {"m_Name": "x"}, broken=True)
    shared = env.file(
        SHARED,
        externals=[
            ("archive:/" + SCRIPTS + "/" + SCRIPTS, b"", 0),
            ("Library/unity default resources", b"\x0f" * 16, 0),
        ],
    )
    shared.add(-5, "Texture2D", {"m_Name": "tex", "m_Width": 4, "image data": b"\0" * 64}, byte_size=500)
    shared.add(1, "AssetBundle", asset_bundle())
    shared.add(3, "GameObject", go("go", []))
    env.resources[MAIN + ".resS"] = b"\0" * 1234
    return env


def test_census_of_a_scene_bundle():
    doc = census.census_env(scene_bundle())
    assert doc["schema"] == contract.CENSUS == "nnnotes.census/2"
    assert [f["name"] for f in doc["files"]] == [MAIN, SHARED]
    assert doc["resources"] == [{"name": MAIN + ".resS", "size": 1234}]
    scene, shared = doc["files"]
    assert [o["pathId"] for o in scene["objects"]] == [1, 2, 3, 4, 7, 8, 11]
    assert [o["pathId"] for o in shared["objects"]] == [-5, 1, 3]  # path id 3 in both files
    assert scene["externals"][0] == {
        "file": SCRIPTS,
        "path": "archive:/" + SCRIPTS + "/" + SCRIPTS,
        "guid": None,
        "type": None,
    }
    assert shared["externals"][1] == {
        "file": "unity default resources",
        "path": "Library/unity default resources",
        "guid": "0f" * 16,
        "type": 0,
    }
    assert (scene["unityVersion"], scene["platform"], scene["typetree"]) == ("6000.3.12f1", 13, True)
    objs = {o["pathId"]: o for o in scene["objects"]}
    assert objs[7] == {
        "pathId": 7,
        "classId": 114,
        "class": "MonoBehaviour",
        "byteSize": 0,
        "typeHash": "01" * 16,
        "name": "",
        "script": {"file": SCRIPTS, "pathId": 42},
        "refs": sorted([f"{MAIN}:1", f"{SHARED}:-5", f"{SCRIPTS}:42"]),  # its GameObject, the texture, its script
        "unit": f"{MAIN}:1",
    }
    assert objs[8]["script"] is None and objs[8]["name"] == "n"
    assert objs[8]["refs"] == [f"{MAIN}:3"]  # a reference with an unknown file index is left out
    assert "name" not in objs[11] and "refs" not in objs[11]
    assert doc["errors"] == [{"object": MAIN + ":11", "message": "ValueError: cannot read <obj at 0x?>"}]
    tex = shared["objects"][0]
    assert tex["name"] == "tex" and tex["byteSize"] == 500 and "refs" not in tex
    (ab,) = doc["assetBundles"]
    assert ab["file"] == SHARED and ab["pathId"] == 1 and ab["assetBundleName"] == "0123_abcd.bundle"
    assert ab["dependencies"] == ["dep_1.bundle"] and ab["isStreamedSceneAssetBundle"] is True
    assert ab["sceneHashes"] == [["Assets/Scenes/X.unity", "ffee"]]
    assert ab["container"] == [
        {"path": "Assets/Scenes/X.unity", "file": None, "pathId": None, "preloadIndex": 0, "preloadSize": 0},
        {"path": "Assets/Tex/tex.png", "file": SHARED, "pathId": -5, "preloadIndex": 0, "preloadSize": 1},
        {
            "path": "Assets/Tex/tex.png",
            "file": "unity default resources",
            "pathId": 3,
            "preloadIndex": 1,
            "preloadSize": 1,
        },
    ]
    assert shared["objects"][1]["refs"] == [f"{SHARED}:-5", "unity default resources:3"]
    assert census.container(doc) == ab["container"]
    assert doc["classes"] == {
        "AssetBundle": 1,
        "GameObject": 3,
        "Material": 1,
        "MonoBehaviour": 2,
        "Texture2D": 1,
        "Transform": 2,
    }
    assert doc["objects"] == 10 and doc["scripts"] == []
    assert census.script_refs(doc) == [SCRIPTS + ":42"]
    assert [(f, o["pathId"]) for f, o in census.objects(doc)][:2] == [(MAIN, 1), (MAIN, 2)]
    assert census.facts(doc) == {
        "files": 2,
        "objects": 10,
        "classes": doc["classes"],
        "scripts": 0,
        "scriptRefs": 1,
        "errors": 1,
    }
    assert contract.encode(census.census_env(scene_bundle())) == contract.encode(doc)  # deterministic


def test_the_objects_of_a_hierarchy_name_its_root():
    doc = census.census_env(scene_bundle())
    objs = {o["pathId"]: o for o in doc["files"][0]["objects"]}
    assert "unit" not in objs[1]  # the root GameObject is its own unit
    assert {pid: objs[pid].get("unit") for pid in (2, 3, 4, 7, 8)} == dict.fromkeys((2, 3, 4, 7, 8), f"{MAIN}:1")
    assert objs[2]["refs"] == [f"{MAIN}:1", f"{MAIN}:4"]  # its GameObject and its child
    assert objs[1]["refs"] == [f"{MAIN}:2", f"{MAIN}:7"]
    assert "unit" not in objs[11] and "unit" not in doc["files"][1]["objects"][2]  # outside any hierarchy


def test_scripts_named_by_clip_bindings():
    main = "CAB-cccc0000000000000000000000000000"

    def binding(tid, script):
        return {"path": 1, "attribute": 2, "script": script, "typeID": tid, "customType": 0, "isPPtrCurve": 0}

    env = FakeEnvironment()
    f = env.file(main, externals=["archive:/" + SCRIPTS + "/" + SCRIPTS])
    bindings = [
        binding(114, pptr(42, 1)),
        binding(4, pptr(0)),
        binding(114, pptr(42, 1)),
        binding(114, pptr(6)),
        binding(114, pptr(1, 5)),
    ]
    f.add(
        5,
        "AnimationClip",
        {"m_Name": "fade", "m_ClipBindingConstant": {"genericBindings": bindings, "pptrCurveMapping": []}},
    )
    f.add(6, "AnimationClip", {"m_Name": "move", "m_ClipBindingConstant": {"genericBindings": [binding(4, pptr(0))]}})
    f.add(7, "MonoScript", {"m_Name": "L", "m_ClassName": "L", "m_Namespace": "", "m_AssemblyName": "A.dll"})
    doc = census.census_env(env)
    rec, other, _ = doc["files"][0]["objects"]
    assert rec["bindingScripts"] == [
        {"file": SCRIPTS, "pathId": 42},
        {"file": main, "pathId": 6},
        {"fileId": 5, "pathId": 1},
    ]
    assert rec["refs"] == [f"{SCRIPTS}:42", f"{main}:6"]
    assert rec["name"] == "fade" and "bindingScripts" not in other and "script" not in rec
    assert census.script_refs(doc) == [SCRIPTS + ":42", main + ":6"]
    assert census.facts(doc)["scriptRefs"] == 2


def test_monoscripts_and_unknown_file_index():
    env = FakeEnvironment()
    f = env.file(SCRIPTS)
    widget = {"m_Name": "Widget", "m_ClassName": "Widget", "m_Namespace": "Game.UI", "m_AssemblyName": "Game.dll"}
    f.add(42, "MonoScript", widget)
    top = {"m_Name": "Top", "m_ClassName": "Top", "m_Namespace": "", "m_AssemblyName": "Assembly-CSharp.dll"}
    f.add(43, "MonoScript", top)
    doc = census.census_env(env)
    assert doc["scripts"] == [
        {
            "file": SCRIPTS,
            "pathId": 42,
            "assembly": "Game.dll",
            "namespace": "Game.UI",
            "class": "Widget",
            "name": "Widget",
        },
        {
            "file": SCRIPTS,
            "pathId": 43,
            "assembly": "Assembly-CSharp.dll",
            "namespace": "",
            "class": "Top",
            "name": "Top",
        },
    ]
    refs = census._Refs("CAB-1", [])
    assert refs.ref(pptr(9, 3)) == {"fileId": 3, "pathId": 9} and refs.ref(pptr(0)) is None
    assert refs.ref(None) is None and refs.ref(pptr(5)) == {"file": "CAB-1", "pathId": 5}


def player_file() -> FakeEnvironment:
    """A player's own file without type trees: a MonoBehaviour that references a built-in texture."""
    env = FakeEnvironment()
    f = env.file("globalgamemanagers.assets", externals=["Library/unity default resources"], typetrees=False)
    f.add(50, "MonoScript", {"m_Name": "S", "m_ClassName": "S", "m_Namespace": "", "m_AssemblyName": "A.dll"})
    f.add(2, "MonoBehaviour", mono(0, pptr(50), "volume", target=pptr(10, 1), volume=0.5), type_hash=HASH)
    return env


def test_files_without_type_trees_read_with_the_shipped_ones(monkeypatch):
    header = ["globalgamemanagers.assets:50"]
    for trees, refs in (({HASH.hex(): ROWS}, [*header, "unity default resources:10"]), ({}, header)):
        monkeypatch.setattr(scripttypes, "script_types", lambda version, trees=trees: trees)
        rec = census.census_env(player_file(), shipped=True)["files"][0]["objects"][0]
        assert rec["name"] == "volume" and rec["script"] == {"file": "globalgamemanagers.assets", "pathId": 50}
        assert rec["refs"] == refs  # without a shipped tree, the header's
    monkeypatch.setattr(scripttypes, "script_types", lambda version: {HASH.hex(): ROWS})
    assert census.census_env(player_file())["files"][0]["objects"][0]["refs"] == header  # not asked to ship


def test_bundle_subjects():
    idx = {"bundles": [{"stable": "a", "name": "a_1.bundle"}, {"stable": "b_2.bundle", "name": "b_2.bundle"}]}
    assert census.bundle_subjects(idx) == {"a": idx["bundles"][0], "b_2.bundle": idx["bundles"][1]}


def test_census_stage(tmp_path, monkeypatch):
    bundle = tmp_path / "x_1.bundle"
    bundle.write_bytes(b"UnityFS\0 not really")
    loaded = []

    def load(path, name=None):
        loaded.append((str(path), name))
        return scene_bundle()

    monkeypatch.setattr(unity, "load", load)
    depot = Depot(tmp_path / "depot")
    sha, size = depot.identify(bundle, "bundles", bundle.name)
    inp = Input("bundle", sha, size, bundle.name, ({"kind": "file", "path": str(bundle)},))
    stage = census.CensusStage()
    env = Env(depot, {"bundles": {"x": inp}})
    assert stage.subjects(env) == ["x"]
    task = describe(stage, "x", None, env)
    assert task.id == "unity.census:x" and task.atoms == census.CensusStage.ATOMS and task.version == 3
    assert task.atoms["reader"].startswith("unitypy-")
    assert task.cost.peak_bytes > size
    assert execute(task, depot, registry(stage)).status == "ran"
    doc = depot.result(task.key)
    (rec,) = doc["artifacts"]
    assert rec["id"] == "unity.census:x#census" and rec["semantics"]["facts"]["objects"] == 10
    assert rec["provenance"]["inputs"] == [{"role": "bundle", "sha256": sha}]
    assert contract.loads(depot.read(rec["content"]["sha256"])) == census.census_env(scene_bundle())
    assert loaded == [(str(bundle), None)]  # a catalog bundle: its files keep their names
    assert execute(task, depot, registry(stage)).status == "hit" and len(loaded) == 1
    with pytest.raises(ValueError, match="expected 'bundle'"):
        describe(stage, "x", None, Env(depot, {"bundles": {"x": Input("other", sha, size)}}))
    for waiting in (None, Pending("fetch:x_1.bundle")):
        with pytest.raises(Pending, match="fetch:x"):
            describe(stage, "x", None, Env(depot, {"bundles": {"x": waiting}}))
    # a serialized file of the APK outside a bundle is loaded under the name references into it use, and read with the
    # shipped type trees, which its key names
    own = "apk/Resources/unity default resources"
    task = describe(stage, own, None, Env(depot, {"bundles": {own: inp}}))
    assert task.atoms == {**census.CensusStage.ATOMS, "typetrees": scripttypes.types_id()}
    assert execute(task, depot, registry(stage)).status == "ran"
    assert loaded[-1] == (str(bundle), "unity default resources")
