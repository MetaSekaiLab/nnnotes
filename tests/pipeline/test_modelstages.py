"""The Spine skeleton stage: its subjects, closures, keys and runs with synthetic censuses and objects."""

import subprocess
import sys
from pathlib import Path

import pytest

from nnnotes.pipeline import census, contract, link, modelstages
from nnnotes.pipeline.census import CensusStage
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.modelstages import SpineStage
from nnnotes.pipeline.stages import Env, Pending, describe, execute, registry


def test_the_libraries_are_those_of_the_extractor():
    from nnnotes.unity import export

    assert modelstages.LIBRARIES == export._LIBS


def test_importing_the_stages_does_not_import_unitypy():
    code = (
        "import sys, nnnotes.pipeline.modelstages as m; m.SpineStage(); "
        "assert 'UnityPy' not in sys.modules, 'UnityPy imported'"
    )
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
    fs = [
        {
            "name": n,
            "unityVersion": "6000.0.0f1",
            "platform": 13,
            "typetree": True,
            "externals": [{"file": x, "path": f"archive:/{x}/{x}", "guid": None, "type": 0} for x in ext],
            "objects": objs,
        }
        for n, ext, objs in files
    ]
    classes = {}
    for f in fs:
        for o in f["objects"]:
            classes[o["class"]] = classes.get(o["class"], 0) + 1
    return {
        "schema": contract.CENSUS,
        "files": fs,
        "resources": [],
        "assetBundles": [],
        "resourcePaths": [],
        "scripts": list(scripts),
        "classes": classes,
        "objects": sum(classes.values()),
        "errors": [],
    }


def spine_censuses():
    sda, atlas = 1, 2
    return {
        "spine_a": cdoc(
            [
                (
                    CAB["a"],
                    [CAB["t"], CAB["m"], "unity default resources"],
                    [
                        obj(10, "MonoBehaviour", "chr_SkeletonData", sda),
                        obj(11, "MonoBehaviour", "chr_Atlas", atlas),
                        obj(12, "TextAsset", "chr"),
                    ],
                )
            ]
        ),
        "tex_b": cdoc([(CAB["t"], [CAB["s"]], [obj(1, "Texture2D", "chr"), obj(2, "Material", "chr_Material")])]),
        "shader_c": cdoc([(CAB["s"], [], [obj(1, "Shader", "Spine/Skeleton")])]),
        "other_d": cdoc([(CAB["d"], [], [obj(5, "MonoBehaviour", "x_SkeletonData", sda)])]),
        "scene_e": cdoc(
            [
                (CAB["e"], [], [obj(7, "GameObject", "root")]),
                (CAB["e"] + ".sharedAssets", [CAB["t"]], [obj(7, "MonoBehaviour", "y_SkeletonData", sda)]),
            ]
        ),
        "scripts": cdoc(
            [(CAB["m"], [], [obj(1, "MonoScript", "SkeletonDataAsset"), obj(2, "MonoScript", "SpineAtlasAsset")])],
            scripts=[
                {
                    "file": CAB["m"],
                    "pathId": 1,
                    "assembly": "spine-unity.dll",
                    "namespace": "Spine.Unity",
                    "class": "SkeletonDataAsset",
                    "name": "SkeletonDataAsset",
                },
                {
                    "file": CAB["m"],
                    "pathId": 2,
                    "assembly": "spine-unity.dll",
                    "namespace": "Spine.Unity",
                    "class": "SpineAtlasAsset",
                    "name": "SpineAtlasAsset",
                },
            ],
        ),
        "unsel_f": cdoc([(CAB["f"], [], [obj(3, "MonoBehaviour", "z_SkeletonData", sda)])]),
    }


def census_result(depot, env, subject, doc, body):
    inp = Input("bundle", contract.sha256(body), len(body), subject + ".bundle", ())
    t = describe(CensusStage(), subject, None, Env(depot, {"bundles": {subject: inp}}))
    rec = contract.artifact(
        contract.artifact_id(t.id, "census"),
        depot.add(contract.encode(doc), "json"),
        contract.provenance(t),
        {"kind": "unity.census", "format": "json", "facts": census.facts(doc)},
    )
    depot.commit(contract.result(t, [rec], []))
    env.done(t.id, t.key)
    return inp


def spine_env(depot, docs=None, data=None, selected=("other_d", "scene_e", "spine_a")):
    docs = docs or spine_censuses()
    data = data or {}
    env = Env(depot, {"selected": sorted(selected)})
    env.facts["bundles"] = {
        s: census_result(depot, env, s, d, data.get(s, b"UnityFS " + s.encode())) for s, d in sorted(docs.items())
    }
    st = describe(link.ScriptsStage(), "all", None, env)
    execute(st, depot, registry(link.ScriptsStage()))
    env.done(st.id, st.key)
    return env


def test_spine_subjects_closures_and_context(tmp_path):
    stage, depot = SpineStage(), Depot(tmp_path / "s")
    env = spine_env(depot)
    assert stage.subjects(env) == ["other_d:5", "scene_e.sharedAssets:7", "spine_a:10"]
    t = describe(stage, "spine_a:10", None, env)
    assert t.id == "spine.skeleton:spine_a:10" and t.atoms == SpineStage.ATOMS and t.params == {}
    assert [i.role for i in t.inputs] == ["bundle:scripts", "bundle:shader_c", "bundle:spine_a", "bundle:tex_b"]
    assert t.inputs[2].sha256 == contract.sha256(b"UnityFS spine_a")
    assert t.context == {"object": CAB["a"] + ":10"}
    assert stage.depends("spine_a:10", env) == [
        "link.scripts:all",
        "unity.census:scripts",
        "unity.census:shader_c",
        "unity.census:spine_a",
        "unity.census:tex_b",
    ]
    e = describe(stage, "scene_e.sharedAssets:7", None, env)
    assert [i.role for i in e.inputs] == ["bundle:scene_e", "bundle:shader_c", "bundle:tex_b"]
    assert e.context == {"object": CAB["e"] + ".sharedAssets:7"}
    assert [i.role for i in describe(stage, "other_d:5", None, env).inputs] == ["bundle:other_d"]
    with pytest.raises(ValueError, match="no SkeletonDataAsset"):
        describe(stage, "unsel_f:3", None, env)


def test_spine_keys_follow_the_closure_contents_only(tmp_path):
    stage = SpineStage()
    key = describe(stage, "spine_a:10", None, spine_env(Depot(tmp_path / "a"))).key
    env = spine_env(Depot(tmp_path / "b"), data={"other_d": b"UnityFS other, changed"})
    assert describe(stage, "spine_a:10", None, env).key == key
    env = spine_env(Depot(tmp_path / "c"), data={"shader_c": b"UnityFS shader, changed"})
    assert describe(stage, "spine_a:10", None, env).key != key


def test_spine_waits_for_censuses_and_the_script_table(tmp_path):
    stage, depot = SpineStage(), Depot(tmp_path / "s")
    env = spine_env(depot)
    env.waiting("unity.census:tex_b")
    with pytest.raises(Pending, match="unity.census:tex_b"):
        stage.subjects(env)
    env.waiting("unity.census:tex_b", failed=True)  # a failed census is left out of the closures
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

    text = lambda name, s: type("T", (), {"m_Name": name, "m_Script": s})()  # noqa: E731
    tex = type("Tex", (), {"m_Name": "chr", "m_TextureFormat": 4, "image": Image.new("RGBA", (1, 1))})()
    mat = type(
        "Mat",
        (),
        {
            "m_SavedProperties": type(
                "P", (), {"m_TexEnvs": [("_MainTex", type("E", (), {"m_Texture": FakeRef(1, tex)})())]}
            )()
        },
    )()
    atlas = type(
        "A",
        (),
        {"atlasFile": FakeRef(2, text("chr", "\nchr.png\nsize: 1,1\n")), "materials": [FakeRef(3, mat)] * pages},
    )()
    return FakeObj(
        {"m_Name": "chr_SkeletonData", "scale": 1.0, "defaultMix": 0.0},
        skeletonJSON=FakeRef(4, text("chr", b"\x01skel")),
        atlasAssets=[FakeRef(11, atlas)],
    )


def test_spine_run_stores_the_files_of_the_skeleton(tmp_path, monkeypatch):
    from nnnotes.unity import reader as unity

    stage, depot = SpineStage(), Depot(tmp_path / "s")
    env = spine_env(depot)
    for s in ("scripts", "shader_c", "spine_a", "tex_b"):
        depot.put(b"UnityFS " + s.encode())
    loaded = []

    def load_closure(paths):
        loaded.append([Path(p).read_bytes() for p in paths])
        return FakeEnv({CAB["a"].lower(): FakeFile({10: sda})})

    monkeypatch.setattr(unity, "load_closure", load_closure)
    sda = fake_skeleton()
    t = describe(stage, "spine_a:10", None, env)
    assert execute(t, depot, registry(stage)).status == "ran"
    assert loaded == [[b"UnityFS scripts", b"UnityFS shader_c", b"UnityFS spine_a", b"UnityFS tex_b"]]
    doc = depot.result(t.key)
    got = {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}
    assert {r: (a["semantics"]["kind"], a["content"]["ext"]) for r, a in got.items()} == {
        "chr.skel": ("spine.skeleton", "skel"),
        "chr.atlas": ("spine.atlas", "atlas"),
        "chr.png": ("spine.page", "png"),
    }
    assert depot.read(got["chr.skel"]["content"]["sha256"]) == b"\x01skel"
    assert got["chr.skel"]["semantics"]["facts"]["atlases"] == ["chr.atlas"]
    (item,) = doc["items"]
    assert item == {
        "object": CAB["a"] + ":10",
        "status": "exported",
        "class": "MonoBehaviour",
        "artifacts": sorted(a["id"] for a in doc["artifacts"]),
    }
    sda = fake_skeleton(pages=2)  # a writer error: a failed item, cached
    t2 = Task(t.stage, t.version, t.subject, {}, t.atoms, t.inputs[:3], t.context)  # another key
    assert execute(t2, depot, registry(stage)).result_status == "partial"
    doc2 = depot.result(t2.key)
    assert doc2["artifacts"] == [] and doc2["items"][0]["reason"]["code"] == "failed.export"
    assert "1 pages vs 2 materials" in doc2["items"][0]["reason"]["message"]
