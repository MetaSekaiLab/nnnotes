import pytest

import synth
from nnnotes import census, contract, link
from nnnotes.catalogdb import CatalogDB, IndexStage, index
from nnnotes.contract import Input
from nnnotes.stages import Env, Pending, describe, execute, registry
from nnnotes.store import Store

HA, HB, HC = "a" * 32, "b" * 32, "c" * 32
CAB_A, CAB_B, CAB_S, CAB_M = (f"CAB-{c * 32}" for c in "1234")


def obj(pid, cls, name=None, size=10, script=None):
    o = {"pathId": pid, "classId": 0, "class": cls, "byteSize": size, "typeHash": "00" * 16}
    if name is not None:
        o["name"] = name
    if script is not None:
        o["script"] = script
    return o


def cdoc(files: dict, container=(), name=None, scripts=()):
    """A census document: files {file: [objects]}, container [(path, file, pathId)] on the first file's AssetBundle."""
    fs = [{"name": f, "unityVersion": "6000.0.0f1", "platform": 13, "typetree": True, "externals": [],
           "objects": sorted(objs, key=lambda o: o["pathId"])} for f, objs in sorted(files.items())]
    first = sorted(files)[0]
    ab = {"file": first, "pathId": 1, "name": "x", "assetBundleName": name, "dependencies": [],
          "isStreamedSceneAssetBundle": False, "sceneHashes": [],
          "container": [{"path": p, "file": f, "pathId": i, "preloadIndex": 0, "preloadSize": 0}
                        for p, f, i in container]}
    classes = {}
    for objs in files.values():
        for o in objs:
            classes[o["class"]] = classes.get(o["class"], 0) + 1
    return {"schema": contract.CENSUS, "files": fs, "resources": [], "assetBundles": [ab],
            "scripts": list(scripts), "classes": classes, "objects": sum(classes.values()), "errors": []}


def catalog_entries():
    return [
        ("Card/A", "Assets/Game/Card/A.png", [3, 4], {"type": "UnityEngine.Texture2D"}),
        ("Card/A", "Assets/Game/Card/A.png", [3, 4], {"type": "UnityEngine.Sprite"}),
        ("Card/Gone", "Assets/Game/Card/Gone.png", [3]),
        (f"card_a_{HA}.bundle", synth.remote(f"card_a_{HA}.bundle"), [], {"extra": {"bundleName": "na_1"}}),
        (f"shared_{HB}.bundle", synth.remote(f"shared_{HB}.bundle"), [], {"extra": {"bundleName": "nb_2"}}),
        ("Scene/X", "Assets/Scenes/X.unity", [6]),
        (f"scene_x_{HC}.bundle", synth.remote(f"scene_x_{HC}.bundle"), [], {"extra": {"bundleName": "nc_3"}}),
        ("Sound/S", "Assets/Game/Sound/S.acb", [8]),
        ("cri/s_" + HA, synth.remote("cri/s_" + HA), []),
        ("Other/O", "Assets/Game/Other/O.asset", [9]),
        (f"other_{HB}.bundle", synth.remote(f"other_{HB}.bundle"), []),
    ]


def censuses():
    card = cdoc({CAB_A: [obj(1, "AssetBundle", "x"), obj(5, "Texture2D", "A", 500), obj(6, "Sprite", "A"),
                         obj(7, "Sprite", "A_square"), obj(8, "Sprite", "dup"), obj(9, "Sprite", "DUP"),
                         obj(10, "MonoBehaviour", "", script={"file": CAB_M, "pathId": 42})]},
                [("assets/game/card/a.png", CAB_A, 6), ("assets/game/card/a.png", CAB_A, 5),
                 ("assets/game/card/a.png", CAB_A, 7), ("assets/game/card/a.png", CAB_A, 8),
                 ("assets/game/card/a.png", CAB_A, 9)], name="na_1.bundle")
    shared = cdoc({CAB_B: [obj(1, "AssetBundle", "y")]}, name="wrong.bundle")
    scene = cdoc({CAB_S: [obj(1, "AssetBundle"), obj(3, "GameObject", "root")],
                  CAB_S + ".sharedAssets": [obj(3, "Material", "m")]},
                 [("Assets/Scenes/X.unity", None, None)], name="nc_3.bundle")
    scripts = cdoc({CAB_M: [obj(42, "MonoScript", "Widget")]}, scripts=[
        {"file": CAB_M, "pathId": 42, "assembly": "Game.dll", "namespace": "Game.UI", "class": "Widget",
         "name": "Widget"},
        {"file": CAB_M, "pathId": 43, "assembly": "Assembly-CSharp.dll", "namespace": "", "class": "Top",
         "name": "Top"}])
    return {"card_a": card, "shared": shared, "scene_x": scene, "scripts": scripts}


def test_scripts_table_and_context():
    docs = censuses()
    table = link.scripts(docs.values())
    assert table == {"schema": link.SCRIPTS, "scripts": {CAB_M + ":42": "Game|Game.UI|Widget",
                                                        CAB_M + ":43": "Assembly-CSharp||Top"}}
    refs = census.script_refs(docs["card_a"])
    assert link.script_context(table["scripts"], refs + ["CAB-x:1"]) == {CAB_M + ":42": "Game|Game.UI|Widget"}


def test_addresses():
    idx = index(synth.CatalogWriter().build(catalog_entries()))
    doc = link.addresses(idx, censuses())
    assert doc["schema"] == link.ADDRESSES and doc["catalog"]["apk"] is None
    assert set(doc["bundles"]) == {"card_a", "shared", "scene_x"}           # "scripts" is no bundle of the index
    assert doc["files"] == {CAB_A: "card_a", CAB_B: "shared", CAB_S: "scene_x", CAB_S + ".sharedAssets": "scene_x"}
    card = doc["bundles"]["card_a"]
    assert card["name"] == f"card_a_{HA}.bundle" and card["files"] == [CAB_A]
    assert card["assetBundleName"] == "na_1.bundle"
    assert card["keys"] == ["Card/A", f"card_a_{HA}.bundle"]
    tex, sprite = doc["keys"]["Card/A"]
    assert tex["bundle"] == "card_a" and tex["type"] == "UnityEngine.Texture2D"
    assert sprite["type"] == "UnityEngine.Sprite"
    assert tex["objects"] == [f"{CAB_A}:{i}" for i in (6, 5, 7, 8, 9)]
    # the texture owns the container path, sprites with a name of their own get [name], the others their path id
    objs = doc["objects"]
    assert objs["card_a/assets/game/card/a.png"]["object"] == CAB_A + ":5"
    assert objs["card_a/assets/game/card/a.png"] == {"object": CAB_A + ":5", "class": "Texture2D", "name": "A",
                                                     "byteSize": 500, "typeHash": "00" * 16}
    assert objs["card_a/assets/game/card/a.png[A]"]["object"] == CAB_A + ":6"
    assert objs["card_a/assets/game/card/a.png[A_square]"]["object"] == CAB_A + ":7"
    assert objs["card_a:8"]["object"] == CAB_A + ":8" and objs["card_a:9"]["object"] == CAB_A + ":9"
    assert len(objs) == 5
    gone = doc["keys"]["Card/Gone"]
    assert gone == [{"location": gone[0]["location"], "type": "UnityEngine.Object",
                     "internalId": "Assets/Game/Card/Gone.png", "bundle": "card_a", "objects": []}]
    assert doc["keys"]["Scene/X"][0]["objects"] == [] and doc["keys"]["Scene/X"][0]["bundle"] == "scene_x"
    assert doc["keys"]["Sound/S"][0]["raw"] == ["cri/s"] and doc["keys"]["Sound/S"][0]["bundle"] is None
    assert doc["keys"]["Other/O"][0]["objects"] is None                      # its bundle has no census
    assert doc["keys"][f"shared_{HB}.bundle"][0]["bundle"] == "shared"
    assert doc["keys"]["cri/s_" + HA][0]["raw"] == "cri/s"
    assert doc["problems"] == {"files": [], "bundleNames": [
        {"bundle": "shared", "catalog": "nb_2.bundle", "census": ["wrong.bundle"]}], "keys": ["Card/Gone"]}
    assert contract.encode(link.addresses(idx, censuses())) == contract.encode(doc)      # deterministic
    streamed = link.addresses(idx, ((s, d) for s, d in reversed(sorted(censuses().items()))))
    assert contract.encode(streamed) == contract.encode(doc)                              # pairs, any order
    rev = link.reverse(doc)
    assert link.address_of(doc, CAB_A + ":6", rev) == "card_a/assets/game/card/a.png[A]"
    assert link.address_of(doc, CAB_A + ":10") == "card_a:10"
    assert link.address_of(doc, CAB_S + ":3") == "scene_x:3"
    assert link.address_of(doc, CAB_S + ".sharedAssets:3") == "scene_x.sharedAssets:3"
    assert link.address_of(doc, "CAB-unknown:3") is None


def test_file_tags_and_conflicts():
    assert link.file_tags(["CAB-1"]) == {"CAB-1": ""}
    assert link.file_tags(["CAB-1", "CAB-1.sharedAssets"]) == {"CAB-1": "", "CAB-1.sharedAssets": ".sharedAssets"}
    assert link.file_tags(["CAB-1", "CAB-2"]) == {"CAB-1": "|CAB-1", "CAB-2": "|CAB-2"}
    idx = index(synth.CatalogWriter().build(catalog_entries()))
    docs = censuses()
    docs["shared"] = cdoc({CAB_A: [obj(1, "AssetBundle")]})
    doc = link.addresses(idx, docs)
    assert doc["problems"]["files"] == [{"file": CAB_A, "bundles": ["card_a", "shared"]}]
    assert doc["files"][CAB_A] == "card_a"


def test_diff_addresses():
    idx = index(synth.CatalogWriter().build(catalog_entries()))
    old = link.addresses(idx, censuses())
    docs = censuses()
    card = docs["card_a"]
    card["files"][0]["name"] = CAB_B.replace("2", "9")
    new_cab = card["files"][0]["name"]
    for e in card["assetBundles"][0]["container"]:
        e["file"] = new_cab
    card["assetBundles"][0]["container"] = card["assetBundles"][0]["container"][:4]      # pathId 9 gone
    card["files"][0]["objects"][1]["byteSize"] = 999                                   # the texture changed
    new = link.addresses(idx, docs)
    d = link.diff_addresses(old, new)
    # pathId 9 is gone, so the name of 8 is no longer shared: 8 is addressed by its name now
    assert d["removed"] == ["card_a:8", "card_a:9"] and d["added"] == ["card_a/assets/game/card/a.png[dup]"]
    (ch,) = d["changed"]
    assert ch["address"] == "card_a/assets/game/card/a.png" and ch["fields"] == ["object", "byteSize"]
    assert [r["address"] for r in d["rebuilt"]] == ["card_a/assets/game/card/a.png[A]",
                                                    "card_a/assets/game/card/a.png[A_square]"]
    assert d["summary"] == {"added": 1, "removed": 2, "changed": 1, "rebuilt": 2, "unchanged": 0}
    assert link.diff_addresses(old, old)["summary"]["unchanged"] == 5


# ---------------------------------------------------------------- stages
def census_task(store, subject, doc):
    """A census task with a stored result made of `doc` (no bundle read)."""
    data = contract.encode(doc)
    inp = Input("bundle", contract.sha256(subject.encode()), 1, subject + ".bundle")
    task = describe(census.CensusStage(), subject, None, Env(store, {"bundles": {subject: inp}}))
    content = store.add(data, "json")
    rec = contract.artifact(contract.artifact_id(task.id, "census"), content, contract.provenance(task),
                            {"kind": "unity.census", "format": "json", "facts": census.facts(doc)})
    store.commit(contract.result(task, [rec], []))
    return task


def test_link_stages(tmp_path):
    store = Store(tmp_path / "store")
    db = CatalogDB(tmp_path / "store")
    v = db.add(synth.CatalogWriter().build(catalog_entries()))
    env = Env(store, {"catalogs": {"main": db.inputs(v)}})
    itask = describe(IndexStage(), "main", None, env)
    execute(itask, store, registry(IndexStage()))
    env.done(itask.id, itask.key)
    docs = censuses()
    tasks = {s: census_task(store, s, d) for s, d in docs.items()}
    env.waiting(tasks["card_a"].id)                                    # not run yet
    for s, t in tasks.items():
        if s != "card_a":
            env.done(t.id, t.key)
    stages = registry(link.ScriptsStage(), link.AddressesStage())
    with pytest.raises(Pending):
        describe(stages["link.scripts"], "all", None, env)
    with pytest.raises(Pending):
        describe(stages["link.addresses"], "main", None, env)
    env.waiting(tasks["card_a"].id, failed=True)                       # a failed census is skipped
    st = describe(stages["link.scripts"], "all", None, env)
    assert [i.role for i in st.inputs] == ["census:scripts"]
    env.done(tasks["card_a"].id, tasks["card_a"].key)
    st = describe(stages["link.scripts"], "all", None, env)
    assert st.id == "link.scripts:all" and [i.role for i in st.inputs] == ["census:scripts"]
    execute(st, store, stages)
    (rec,) = store.result(st.key)["artifacts"]
    assert rec["id"] == "link.scripts:all#scripts" and rec["semantics"]["facts"] == {"scripts": 2}
    assert contract.loads(store.read(rec["content"]["sha256"])) == link.scripts([docs["scripts"]])
    at = describe(stages["link.addresses"], "main", None, env)
    assert [i.role for i in at.inputs] == ["census:card_a", "census:scene_x", "census:shared", "index"]
    execute(at, store, stages)
    (rec,) = store.result(at.key)["artifacts"]
    assert rec["id"] == "link.addresses:main#addresses" and rec["semantics"]["facts"]["objects"] == 5
    expected = link.addresses(index(*db.catalog_bytes(v)), {s: docs[s] for s in ("card_a", "scene_x", "shared")})
    assert contract.loads(store.read(rec["content"]["sha256"])) == expected
    assert execute(at, store, stages).status == "hit"


def test_artifacts_table_and_stage(tmp_path):
    store = Store(tmp_path / "store")
    arts, items = [], []
    for pid, status in ((1, "exported"), (2, "contained"), (3, "failed")):
        oid = contract.object_id(CAB_A, pid)
        if status == "exported":
            c = store.add(b"{}\n", "json")
            aid = contract.artifact_id(oid, "json")
            arts.append({"id": aid, "content": c})
            items.append(contract.item(oid, "exported", artifacts=[aid]))
        elif status == "contained":
            items.append(contract.item(oid, "contained", within=contract.artifact_id(CAB_A + ":1", "json")))
        else:
            items.append(contract.item(oid, "failed", why=contract.reason("x", "y")))
    table = link.artifacts([("unity.export:card_a", {"artifacts": arts, "items": items})])
    assert table["objects"] == {f"{CAB_A}:1": [f"{CAB_A}:1#json"]}
    assert table["contained"] == {f"{CAB_A}:2": f"{CAB_A}:1#json"}
    assert table["artifacts"][f"{CAB_A}:1#json"]["ext"] == "json"
    stage = link.ArtifactsStage()
    env = Env(store, {})
    export = contract.Task("unity.export", 1, "card_a")
    store.commit(contract.result(export, [contract.artifact(a["id"], a["content"], contract.provenance(export),
                                                             {"kind": "x"}) for a in arts], items))
    env.waiting(export.id)
    with pytest.raises(Pending):
        describe(stage, "all", None, env)
    env.done(export.id, export.key)
    env.waiting("sprite.crop:atlas", failed=True)                     # failed tasks are skipped
    t = describe(stage, "all", None, env)
    assert t.context == {"results": {export.id: export.key}} and t.inputs == ()
    execute(t, store, registry(stage))
    (rec,) = store.result(t.key)["artifacts"]
    assert rec["id"] == "link.artifacts:all#artifacts"
    assert contract.loads(store.read(rec["content"]["sha256"]))["objects"] == table["objects"]
