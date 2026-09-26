"""The model stages: the closures of Live2D model keys (as Catalog.resolve gives them), the task catalog the
Live2D extractor runs on, the keys of both stages, and their runs with synthetic extractors, censuses and objects."""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import synth
from nnnotes import census, contract, link, modelstages
from nnnotes.catalog import Catalog
from nnnotes.catalogdb import index
from nnnotes.census import CensusStage
from nnnotes.contract import IncompatibleTask, Input, Task
from nnnotes.modelstages import Closures, Live2DStage, SpineStage, TaskCatalog
from nnnotes.stages import Env, Pending, describe, execute, registry
from nnnotes.store import Store

H = [f"{i:x}" * 32 for i in range(1, 10)]
M1 = "Character/Live2D/g1/m1/model/m1"
M2 = "Character/Live2D/g2/m2/model/m2"
PREFAB = "Assets/AddressableResources/Character/Live2D/g1/m1/model/m1.prefab"


def remote_entries(order=(2, 3, 5), m1_prefab=PREFAB):
    return [
        (M1, "Character/Live2D/g1/m1/model/m1", [3]),                     # an alias location first
        (M1, m1_prefab, list(order), {"type": "UnityEngine.GameObject"}),
        (f"model_m1_{H[0]}.bundle", synth.remote(f"model_m1_{H[0]}.bundle"), [4]),
        (f"shared_{H[1]}.bundle", synth.remote(f"shared_{H[1]}.bundle"), []),
        (f"tex_{H[2]}.bundle", synth.remote(f"tex_{H[2]}.bundle"), [2]),   # a cycle back to the model bundle
        ("monoscripts.bundle", synth.local("monoscripts.bundle"), []),
        (M2, "Assets/AddressableResources/Character/Live2D/g2/m2/model/m2.prefab", [7, 5, 3]),
        (f"model_m2_{H[3]}.bundle", synth.remote(f"model_m2_{H[3]}.bundle"), []),
        ("Character/Live2D/g2/m2/motion/idle", "Assets/Game/idle.anim", [7]),
        ("Character/Live2D/g3/m3/model/m3", "Assets/Game/m3.prefab", []),   # no bundle
    ]


APK = [("Shared/S", "Assets/Game/Shared/S.prefab", [1]),
       ("monoscripts.bundle", synth.local("monoscripts.bundle"), [])]


def build(entries):
    return synth.CatalogWriter().build(entries)


def make_apk(path, entries=APK):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("assets/aa/catalog.bin", build(entries))
    return path


def test_closures_are_those_of_catalog_resolve(tmp_path):
    remote = build(remote_entries())
    cat = Catalog(remote, tmp_path / "cache", apk=make_apk(tmp_path / "base.apk"))
    doc = index(remote, build(APK))
    c = Closures(doc)
    names = {b["stable"]: b["name"] for b in doc["bundles"]}
    assert c.keys() == cat.keys()
    for key in cat.keys():
        assert c.entry(key)["internalId"] == cat._entry(key)["internal_id"]
        assert [names[s] for s in c.closure_bundles(key)] == [b.name for b in cat.resolve(key)], key
    assert c.entry(M1)["internalId"] == PREFAB                              # the location inside a bundle
    assert c.closure_bundles(M1) == ["monoscripts", "shared", "model_m1", "tex"]
    assert c.own_bundle(M1) == "model_m1" and c.own_bundle("Character/Live2D/g3/m3/model/m3") is None
    with pytest.raises(KeyError):
        c.entry("Character/Live2D/none")


# ---------------------------------------------------------------- live2d.model
def contents(tag=b""):
    return {"model_m1": b"UnityFS m1" + tag, "shared": b"UnityFS shared", "tex": b"UnityFS tex",
            "monoscripts": b"UnityFS scripts", "model_m2": b"UnityFS m2"}


def bundles_fact(doc, data, locators=True):
    out = {}
    for b in doc["bundles"]:
        body = data[b["stable"]]
        locs = ({"kind": "cache", "path": f"bundles/{b['name']}"},) if locators else ()
        out[b["stable"]] = Input("bundle", contract.sha256(body), len(body), b["name"], locs)
    return out


def live2d_env(store, entries=None, data=None, selected=None, locators=True):
    doc = index(build(entries or remote_entries()), build(APK))
    data = data or contents()
    bundles = bundles_fact(doc, data, locators)
    return Env(store, {"index": doc, "bundles": bundles, "selected": sorted(selected or bundles)})


def test_live2d_subjects_inputs_and_context(tmp_path):
    stage = Live2DStage()
    env = live2d_env(Store(tmp_path / "s"))
    assert stage.subjects(env) == [M1, M2]                                   # model keys with their bundle
    assert stage.subjects(live2d_env(Store(tmp_path / "s"), selected=["model_m2", "shared"])) == [M2]
    t = describe(stage, M1, None, env)
    assert t.id == "live2d.model:" + M1 and t.params == {} and t.atoms == Live2DStage.ATOMS
    assert [(i.role, i.name) for i in t.inputs] == [("bundle:000", "monoscripts.bundle"),
                                                  ("bundle:001", f"shared_{H[1]}.bundle"),
                                                  ("bundle:002", f"model_m1_{H[0]}.bundle"),
                                                  ("bundle:003", f"tex_{H[2]}.bundle")]
    assert t.inputs[2].sha256 == contract.sha256(contents()["model_m1"])
    assert t.context == {"internalId": PREFAB}
    assert t.inputs[0].locators == ({"kind": "cache", "path": "bundles/monoscripts.bundle"},)
    assert set(Live2DStage.ATOMS) == {"live2d.extract_model"}
    assert all(lib.lower() in Live2DStage.ATOMS["live2d.extract_model"] for lib in modelstages.LIBRARIES)


def test_live2d_keys_follow_the_closure_contents_and_order_only(tmp_path):
    stage, store = Live2DStage(), Store(tmp_path / "s")
    key = describe(stage, M1, None, live2d_env(store)).key
    # other file names, no locators, another catalog with more keys: the same task
    renamed = [(e[0].replace(H[1], H[8]), e[1].replace(H[1], H[8]), *e[2:]) for e in remote_entries()]
    renamed.append(("Other/key", "Assets/Game/other.asset", [3]))
    assert describe(stage, M1, None, live2d_env(store, renamed, locators=False)).key == key
    # a bundle of the closure changed, the closure's order changed, the key's location changed: another task
    assert describe(stage, M1, None, live2d_env(store, data=contents(b"!"))).key != key
    assert describe(stage, M1, None, live2d_env(store, remote_entries(order=(3, 2, 5)))).key != key
    assert describe(stage, M1, None, live2d_env(store, remote_entries(m1_prefab=PREFAB + "x"))).key != key
    # a bundle outside the closure changed: the same task
    other = dict(contents(), model_m2=b"UnityFS m2 changed")
    assert describe(stage, M1, None, live2d_env(store, data=other)).key == key
    bumped = type("Bumped", (Live2DStage,), {"version": 2})()
    assert describe(bumped, M1, None, live2d_env(store)).key != key


def test_live2d_waits_for_unfetched_bundles_and_names_unreadable_ones(tmp_path):
    stage, store = Live2DStage(), Store(tmp_path / "s")
    env = live2d_env(store)
    env.facts["bundles"] = dict(env.facts["bundles"], tex=Pending("fetch:tex"))
    with pytest.raises(Pending, match="fetch:tex"):
        describe(stage, M1, None, env)
    env.facts["bundles"]["tex"] = None
    with pytest.raises(Pending, match="fetch:tex"):
        describe(stage, M1, None, env)
    del env.facts["bundles"]["tex"]
    with pytest.raises(ValueError, match="bundle tex is not readable"):
        describe(stage, M1, None, env)


def stored_task(store, stage=None):
    """The live2d.model task of M1 with every bundle in the store (store locators only)."""
    for body in contents().values():
        store.put(body)
    return describe(stage or Live2DStage(), M1, None, live2d_env(store, locators=False))


def test_task_catalog_answers_from_the_task(tmp_path):
    store = Store(tmp_path / "s")
    t = stored_task(store)
    cat = TaskCatalog.from_task(t, store)
    assert cat.apk is not None and cat.has(M1) and not cat.has(M2)
    assert cat.keys("Character/") == [M1] and cat.keys("Other/") == []
    assert cat._entry(M1)["internal_id"] == PREFAB and cat.entries_for(M2) == []
    order = ["monoscripts", "shared", "model_m1", "tex"]
    assert [p.read_bytes() for p in cat.fetch_key(M1)] == [contents()[s] for s in order]
    assert [b.name for b in cat.resolve(M1)] == [i.name for i in t.inputs]
    assert [b.offset for b in cat.resolve(M1)] == [0, 1, 2, 3]
    for f in (cat._entry, cat.fetch_key, cat.resolve):
        with pytest.raises(KeyError):
            f(M2)
    # inputs are ordered by position, not by role text
    many = [Input(f"bundle:{i}", contract.sha256(b"x%d" % i), 2) for i in (10, 2, 1)]
    assert [i.role for i in TaskCatalog("k", "Assets/k", modelstages._ordered(
        Task(Live2DStage.name, 1, "k", inputs=tuple(many))), store).bundles] == ["bundle:1", "bundle:2", "bundle:10"]


def fake_extract(calls):
    def extract_model(cat, key, out_dir):
        assert cat.apk is not None
        data = b"".join(p.read_bytes() for p in cat.fetch_key(key))
        name = key.rsplit("/", 1)[-1]
        calls.append((key, cat._entry(key)["internal_id"]))
        (out_dir / "textures").mkdir()
        (out_dir / "motions").mkdir()
        (out_dir / f"{name}.moc3").write_bytes(b"MOC3" + data)
        (out_dir / "textures" / "texture_00-1a2b3c4d.png").write_bytes(b"\x89PNG page")
        (out_dir / f"{name}.prefab.json").write_text('{"nodes": []}', encoding="utf-8")
        (out_dir / "motions" / "idle.motion3.json").write_text("{}", encoding="utf-8")
        (out_dir / "motions" / "_fades.json").write_text("{}", encoding="utf-8")
        (out_dir / "smile.exp3.json").write_text("{}", encoding="utf-8")
        (out_dir / f"{name}.model3.json").write_text('{"Version": 3}\n', encoding="utf-8")
        return {"name": name, "textures": ["textures/texture_00-1a2b3c4d.png"], "motions": 1, "expressions": 1}
    return extract_model


def test_live2d_run_stores_every_file_of_the_extractor(tmp_path, monkeypatch):
    from nnnotes import live2d
    calls = []
    monkeypatch.setattr(live2d, "extract_model", fake_extract(calls))
    store = Store(tmp_path / "s")
    stage = Live2DStage()
    t = stored_task(store, stage)
    ex = execute(t, store, registry(stage))
    assert ex.status == "ran" and calls == [(M1, PREFAB)]
    doc = store.result(t.key)
    tid = "live2d.model:" + M1
    got = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    assert sorted(got) == ["m1.moc3", "m1.model3.json", "m1.prefab.json", "motions/_fades.json",
                           "motions/idle.motion3.json", "smile.exp3.json", "textures/texture_00-1a2b3c4d.png"]
    assert all(a["id"].startswith(tid + "#") for a in doc["artifacts"]) and doc["items"] == []
    kinds = {r: (a["semantics"]["kind"], a["content"]["ext"]) for r, a in got.items()}
    assert kinds == {"m1.model3.json": ("live2d.model3", "json"), "m1.moc3": ("live2d.moc3", "moc3"),
                     "m1.prefab.json": ("live2d.prefab", "json"), "motions/_fades.json": ("live2d.fades", "json"),
                     "motions/idle.motion3.json": ("live2d.motion", "json"),
                     "smile.exp3.json": ("live2d.expression", "json"),
                     "textures/texture_00-1a2b3c4d.png": ("live2d.texture", "png")}
    order = ["monoscripts", "shared", "model_m1", "tex"]
    assert store.read(got["m1.moc3"]["content"]["sha256"]) == b"MOC3" + b"".join(contents()[s] for s in order)
    assert got["m1.model3.json"]["semantics"]["facts"] == {"expressions": 1, "motions": 1, "name": "m1",
                                                          "textures": ["textures/texture_00-1a2b3c4d.png"]}
    assert execute(t, store, registry(stage)).status == "hit"
    assert execute(t, store, registry(stage), force=True).status == "ran"   # the same result again
    other = Task(t.stage, t.version, t.subject, t.params, {"live2d.extract_model": "unitypy-0/1"}, t.inputs,
                 t.context)
    with pytest.raises(IncompatibleTask):
        execute(other, store, registry(stage))


def test_the_model_key_and_libraries_are_those_of_the_extractors():
    from nnnotes import export, webmodel
    assert modelstages.MODEL_KEY.pattern == webmodel.MODEL_KEY.pattern
    assert modelstages.LIBRARIES == export._LIBS


def test_importing_the_stages_does_not_import_unitypy():
    code = "import sys, nnnotes.modelstages as m; m.Live2DStage(); m.SpineStage(); " \
           "assert 'UnityPy' not in sys.modules, 'UnityPy imported'"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


# ---------------------------------------------------------------- spine.skeleton
CAB = {k: f"CAB-{c * 32}" for k, c in zip(("a", "t", "s", "d", "e", "m", "f"), "1234567")}


def obj(pid, cls, name=None, script=None):
    o = {"pathId": pid, "classId": 0, "class": cls, "byteSize": 10, "typeHash": None}
    if name is not None:
        o["name"] = name
    if script is not None:
        o["script"] = {"file": CAB["m"], "pathId": script}
    return o


def cdoc(files, scripts=()):
    """A census document: files [(name, externals, objects)]."""
    fs = [{"name": n, "unityVersion": "6000.0.0f1", "platform": 13, "typetree": True,
           "externals": [{"file": x, "path": f"archive:/{x}/{x}", "guid": None, "type": 0} for x in ext],
           "objects": objs} for n, ext, objs in files]
    classes = {}
    for f in fs:
        for o in f["objects"]:
            classes[o["class"]] = classes.get(o["class"], 0) + 1
    return {"schema": contract.CENSUS, "files": fs, "resources": [], "assetBundles": [], "scripts": list(scripts),
            "classes": classes, "objects": sum(classes.values()), "errors": []}


def spine_censuses():
    sda, atlas = 1, 2
    return {
        "spine_a": cdoc([(CAB["a"], [CAB["t"], CAB["m"], "unity default resources"],
                          [obj(10, "MonoBehaviour", "chr_SkeletonData", sda), obj(11, "MonoBehaviour", "chr_Atlas",
                                                                                   atlas), obj(12, "TextAsset", "chr")])]),
        "tex_b": cdoc([(CAB["t"], [CAB["s"]], [obj(1, "Texture2D", "chr"), obj(2, "Material", "chr_Material")])]),
        "shader_c": cdoc([(CAB["s"], [], [obj(1, "Shader", "Spine/Skeleton")])]),
        "other_d": cdoc([(CAB["d"], [], [obj(5, "MonoBehaviour", "x_SkeletonData", sda)])]),
        "scene_e": cdoc([(CAB["e"], [], [obj(7, "GameObject", "root")]),
                         (CAB["e"] + ".sharedAssets", [CAB["t"]], [obj(7, "MonoBehaviour", "y_SkeletonData", sda)])]),
        "scripts": cdoc([(CAB["m"], [], [obj(1, "MonoScript", "SkeletonDataAsset"),
                                         obj(2, "MonoScript", "SpineAtlasAsset")])],
                        scripts=[{"file": CAB["m"], "pathId": 1, "assembly": "spine-unity.dll",
                                  "namespace": "Spine.Unity", "class": "SkeletonDataAsset", "name": "SkeletonDataAsset"},
                                 {"file": CAB["m"], "pathId": 2, "assembly": "spine-unity.dll",
                                  "namespace": "Spine.Unity", "class": "SpineAtlasAsset", "name": "SpineAtlasAsset"}]),
        "unsel_f": cdoc([(CAB["f"], [], [obj(3, "MonoBehaviour", "z_SkeletonData", sda)])]),
    }


def census_result(store, env, subject, doc, body):
    inp = Input("bundle", contract.sha256(body), len(body), subject + ".bundle", ())
    t = describe(CensusStage(), subject, None, Env(store, {"bundles": {subject: inp}}))
    rec = contract.artifact(contract.artifact_id(t.id, "census"), store.add(contract.encode(doc), "json"),
                            contract.provenance(t), {"kind": "unity.census", "format": "json", "facts": census.facts(doc)})
    store.commit(contract.result(t, [rec], []))
    env.done(t.id, t.key)
    return inp


def spine_env(store, docs=None, data=None, selected=("other_d", "scene_e", "spine_a")):
    docs = docs or spine_censuses()
    data = data or {}
    env = Env(store, {"selected": sorted(selected)})
    env.facts["bundles"] = {s: census_result(store, env, s, d, data.get(s, b"UnityFS " + s.encode()))
                            for s, d in sorted(docs.items())}
    st = describe(link.ScriptsStage(), "all", None, env)
    execute(st, store, registry(link.ScriptsStage()))
    env.done(st.id, st.key)
    return env


def test_spine_subjects_closures_and_context(tmp_path):
    stage, store = SpineStage(), Store(tmp_path / "s")
    env = spine_env(store)
    assert stage.subjects(env) == ["other_d:5", "scene_e.sharedAssets:7", "spine_a:10"]
    t = describe(stage, "spine_a:10", None, env)
    assert t.id == "spine.skeleton:spine_a:10" and t.atoms == SpineStage.ATOMS and t.params == {}
    assert [i.role for i in t.inputs] == ["bundle:scripts", "bundle:shader_c", "bundle:spine_a", "bundle:tex_b"]
    assert t.inputs[2].sha256 == contract.sha256(b"UnityFS spine_a")
    assert t.context == {"object": CAB["a"] + ":10"}
    assert stage.depends("spine_a:10", env) == ["link.scripts:all", "unity.census:scripts",
                                                "unity.census:shader_c", "unity.census:spine_a",
                                                "unity.census:tex_b"]
    e = describe(stage, "scene_e.sharedAssets:7", None, env)
    assert [i.role for i in e.inputs] == ["bundle:scene_e", "bundle:shader_c", "bundle:tex_b"]
    assert e.context == {"object": CAB["e"] + ".sharedAssets:7"}
    assert [i.role for i in describe(stage, "other_d:5", None, env).inputs] == ["bundle:other_d"]
    with pytest.raises(ValueError, match="no SkeletonDataAsset"):
        describe(stage, "unsel_f:3", None, env)


def test_spine_keys_follow_the_closure_contents_only(tmp_path):
    stage = SpineStage()
    key = describe(stage, "spine_a:10", None, spine_env(Store(tmp_path / "a"))).key
    env = spine_env(Store(tmp_path / "b"), data={"other_d": b"UnityFS other, changed"})
    assert describe(stage, "spine_a:10", None, env).key == key
    env = spine_env(Store(tmp_path / "c"), data={"shader_c": b"UnityFS shader, changed"})
    assert describe(stage, "spine_a:10", None, env).key != key


def test_spine_waits_for_censuses_and_the_script_table(tmp_path):
    stage, store = SpineStage(), Store(tmp_path / "s")
    env = spine_env(store)
    env.waiting("unity.census:tex_b")
    with pytest.raises(Pending, match="unity.census:tex_b"):
        stage.subjects(env)
    env.waiting("unity.census:tex_b", failed=True)            # a failed census is left out of the closures
    assert [i.role for i in describe(stage, "spine_a:10", None, env).inputs] == ["bundle:scripts", "bundle:spine_a"]
    env.waiting("link.scripts:all")
    with pytest.raises(Pending, match="link.scripts:all"):
        stage.subjects(env)


class FakeRef:
    def __init__(self, path_id, target):
        self.path_id, self.target = path_id, target

    def read(self):
        return self.target


class FakeObj:
    def __init__(self, tt, **fields):
        self.tt, self.fields = tt, fields

    def read_typetree(self):
        return self.tt

    def read(self):
        return type("Read", (), self.fields)()


class FakeFile:
    def __init__(self, objects):
        self.objects = objects


class FakeEnv:
    def __init__(self, files):
        self.files = files

    def get_cab(self, name):
        return self.files.get(name.lower())


def fake_skeleton(pages=1):
    from PIL import Image
    text = lambda name, s: type("T", (), {"m_Name": name, "m_Script": s})()        # noqa: E731
    tex = type("Tex", (), {"m_Name": "chr", "m_TextureFormat": 4, "image": Image.new("RGBA", (1, 1))})()
    mat = type("Mat", (), {"m_SavedProperties": type("P", (), {"m_TexEnvs": [
        ("_MainTex", type("E", (), {"m_Texture": FakeRef(1, tex)})())]})()})()
    atlas = type("A", (), {"atlasFile": FakeRef(2, text("chr", "\nchr.png\nsize: 1,1\n")),
                           "materials": [FakeRef(3, mat)] * pages})()
    return FakeObj({"m_Name": "chr_SkeletonData", "scale": 1.0, "defaultMix": 0.0},
                   skeletonJSON=FakeRef(4, text("chr", b"\x01skel")), atlasAssets=[FakeRef(11, atlas)])


def test_spine_run_stores_the_files_of_the_skeleton(tmp_path, monkeypatch):
    from nnnotes import unity
    stage, store = SpineStage(), Store(tmp_path / "s")
    env = spine_env(store)
    for s in ("scripts", "shader_c", "spine_a", "tex_b"):
        store.put(b"UnityFS " + s.encode())
    loaded = []

    def load_closure(paths):
        loaded.append([Path(p).read_bytes() for p in paths])
        return FakeEnv({CAB["a"].lower(): FakeFile({10: sda})})

    monkeypatch.setattr(unity, "load_closure", load_closure)
    sda = fake_skeleton()
    t = describe(stage, "spine_a:10", None, env)
    assert execute(t, store, registry(stage)).status == "ran"
    assert loaded == [[b"UnityFS scripts", b"UnityFS shader_c", b"UnityFS spine_a", b"UnityFS tex_b"]]
    doc = store.result(t.key)
    got = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    assert {r: (a["semantics"]["kind"], a["content"]["ext"]) for r, a in got.items()} == {
        "chr.skel": ("spine.skeleton", "skel"), "chr.atlas": ("spine.atlas", "atlas"), "chr.png": ("spine.page", "png")}
    assert store.read(got["chr.skel"]["content"]["sha256"]) == b"\x01skel"
    assert got["chr.skel"]["semantics"]["facts"]["atlases"] == ["chr.atlas"]
    (item,) = doc["items"]
    assert item == {"object": CAB["a"] + ":10", "status": "exported", "class": "MonoBehaviour",
                    "artifacts": sorted(a["id"] for a in doc["artifacts"])}
    sda = fake_skeleton(pages=2)                               # a writer error: a failed item, cached
    t2 = Task(t.stage, t.version, t.subject, {}, t.atoms, t.inputs[:3], t.context)     # another key
    assert execute(t2, store, registry(stage)).result_status == "partial"
    doc2 = store.result(t2.key)
    assert doc2["artifacts"] == [] and doc2["items"][0]["reason"]["code"] == "failed.export"
    assert "1 pages vs 2 materials" in doc2["items"][0]["reason"]["message"]
