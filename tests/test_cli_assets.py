"""The asset commands on synthetic catalogs: export, plan, run-stage, catalogs, store verify.

Bundles here are UnityFS-signed files whose body is the census document a real census would find; the census and
export stages of the pipeline are replaced by small stages that read them (the registry is patched), the catalog,
link and layout parts are the real ones."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import synth
from nnnotes import cli, cli_assets, contract
from nnnotes.census import CensusStage
from nnnotes.config import ConfigError
from nnnotes.contract import Cost
from nnnotes.stages import Output, Stage
from nnnotes.store import Store

HASHES = {n: hashlib.md5(n.encode()).hexdigest() for n in ("card_1", "card_2", "shared", "bg_x")}
NAMES = {n: f"{n.replace('card_', 'card_assets_card_').replace('shared', 'shared_assets_all')}_{h}.bundle"
         for n, h in HASHES.items()}
CABS = {n: f"CAB-{hashlib.md5(('cab' + n).encode()).hexdigest()}" for n in HASHES}
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- synthetic data
def obj(pid, cls, name=None, script=None):
    o = {"pathId": pid, "classId": 0, "class": cls, "byteSize": 10, "typeHash": None}
    if name is not None:
        o["name"] = name
    if script is not None:
        o["script"] = script
    return o


def cdoc(file: str, objs: list, container=(), scripts=(), bundle_name=None) -> dict:
    classes = {}
    for o in objs:
        classes[o["class"]] = classes.get(o["class"], 0) + 1
    ab = {"file": file, "pathId": 1, "name": "ab", "assetBundleName": bundle_name, "dependencies": [],
          "isStreamedSceneAssetBundle": False, "sceneHashes": [],
          "container": [{"path": p, "file": file, "pathId": i, "preloadIndex": 0, "preloadSize": 0}
                        for p, i in container]}
    return {"schema": contract.CENSUS, "files": [{"name": file, "unityVersion": "6000.0.0f1", "platform": 13,
                                                  "typetree": True, "externals": [],
                                                  "objects": sorted(objs, key=lambda o: o["pathId"])}],
            "resources": [], "assetBundles": [ab], "scripts": list(scripts), "classes": classes,
            "objects": len(objs), "errors": []}


def censuses(broken: bool = True) -> dict:
    """{bundle: census document}: two cards (a texture with a sub-sprite, a script-driven MonoBehaviour), the
    shared bundle holding the MonoScript, a background bundle with an object that fails (`broken`)."""
    script = {"file": CABS["shared"], "pathId": 42}
    out = {}
    for n in ("card_1", "card_2"):
        i = n[-1]
        out[n] = cdoc(CABS[n], [obj(1, "AssetBundle", "ab"), obj(5, "Texture2D", f"card{i}"),
                                obj(6, "Sprite", f"card{i}"), obj(7, "MonoBehaviour", "", script)],
                      [(f"assets/game/card/{i}/full.png", 5), (f"assets/game/card/{i}/full.png", 6)])
    out["shared"] = cdoc(CABS["shared"], [obj(1, "AssetBundle", "ab"), obj(42, "MonoScript", "Widget")],
                         scripts=[{"file": CABS["shared"], "pathId": 42, "assembly": "Game.dll",
                                   "namespace": "Game", "class": "Widget", "name": "Widget"}])
    out["bg_x"] = cdoc(CABS["bg_x"], [obj(1, "AssetBundle", "ab"), obj(3, "Mesh", "floor")]
                       + ([obj(4, "Broken", "bad")] if broken else []), [("assets/game/bg/x.prefab", 3)])
    return out


def bundle_bytes(doc: dict) -> bytes:
    return b"UnityFS\0" + contract.encode(doc)


def catalog_bytes(extra=()) -> bytes:
    remote = {n: synth.remote(NAMES[n]) for n in NAMES}
    entries = [(NAMES[n], remote[n], []) for n in ("card_1", "card_2", "shared", "bg_x")] + list(extra)
    entries += [("Card/1/full", "Assets/Game/Card/1/full.png", [0, 2], {"type": "UnityEngine.Texture2D"}),
                ("Card/2/full", "Assets/Game/Card/2/full.png", [1, 2], {"type": "UnityEngine.Texture2D"}),
                ("Bg/x", "Assets/Game/Bg/x.prefab", [3], {"type": "UnityEngine.GameObject"})]
    return synth.CatalogWriter().build(entries)


def setup_data(tmp_path, docs=None, cached=None, extra=()) -> dict:
    """A catalog file and a cache with the (decrypted) bundles; -> the global flags."""
    docs = censuses() if docs is None else docs
    cache = tmp_path / "cache"
    (cache / "bundles").mkdir(parents=True, exist_ok=True)
    for n, doc in docs.items():
        if cached is None or n in cached:
            (cache / "bundles" / NAMES[n]).write_bytes(bundle_bytes(doc))
    cat = tmp_path / "catalog.bin"
    cat.write_bytes(catalog_bytes(extra))
    return {"cache": cache, "catalog": cat}


# ---------------------------------------------------------------- stand-in stages
class FakeCensus(CensusStage):
    """unity.census over the synthetic bundles (their body is the census)."""

    def run(self, task, store):
        data = store.input_bytes(task.input("bundle"))
        doc = contract.loads(data[8:])
        content = store.add(contract.encode(doc), "json")
        rec = contract.artifact(contract.artifact_id(task.id, "census"), content, contract.provenance(task),
                                {"kind": "unity.census", "format": "json",
                                 "facts": {"classes": doc["classes"], "objects": doc["objects"],
                                           "scripts": len(doc["scripts"])}})
        return Output([rec], [])


class FakeExport(Stage):
    """unity.export: one JSON artifact per object of the census (MonoBehaviours name their script class from the
    link.scripts table), the AssetBundle object contained in the bundle's first artifact, class `Broken` failed."""
    name = "unity.export"
    version = 1
    after = ("unity.census", "link.scripts")
    PARAMS = {"png": {"encoder": "pillow", "level": 6}, "classes": None}
    ATOMS = {"png.encode": "fake/1"}

    def subjects(self, env):
        return list(env.fact("selected"))

    def _census(self, subject):
        return contract.task_id("unity.census", subject)

    def depends(self, subject, env):
        return [self._census(subject), "link.scripts:all"]

    def inputs(self, subject, env):
        return [env.input_of(self._census(subject), "census"), env.input_of("link.scripts:all", "scripts")]

    def estimate(self, subject, env, inputs):
        return Cost(0.01, 1 << 20)

    def run(self, task, store):
        doc = contract.loads(store.input_bytes(task.input("census")))
        scripts = contract.loads(store.input_bytes(task.input("scripts")))["scripts"]
        if any(o["class"] == "Crash" for f in doc["files"] for o in f["objects"]):
            raise ConfigError("setting paths.tool is not set")
        container = {(e["file"], e["pathId"]): e["path"] for e in doc["assetBundles"][0]["container"]}
        only = task.params["classes"]
        arts, items, first = [], [], None
        for f in doc["files"]:
            for o in f["objects"]:
                oid = contract.object_id(f["name"], o["pathId"])
                if only is not None and o["class"] not in only:
                    continue
                if o["class"] == "AssetBundle":
                    continue
                if o["class"] == "Broken":
                    items.append(contract.item(oid, "failed", why=contract.reason("fake.broken", "broken"),
                                               cls=o["class"]))
                    continue
                body = {"object": oid, "class": o["class"], "level": task.params["png"]["level"]}
                if o.get("script"):
                    body["script"] = scripts.get(contract.object_id(o["script"]["file"], o["script"]["pathId"]))
                facts = {"file": f["name"], "pathId": o["pathId"], "classId": 0, "class": o["class"],
                         "name": o.get("name", "")}
                if (f["name"], o["pathId"]) in container:
                    facts["container"] = container[(f["name"], o["pathId"])]
                aid = contract.artifact_id(oid, "json")
                arts.append(contract.artifact(aid, store.add(contract.encode(body), "json"),
                                              contract.provenance(task, obj=facts), {"kind": "fake.json"}))
                items.append(contract.item(oid, "exported", artifacts=[aid], cls=o["class"]))
                first = first or aid
        for f in doc["files"]:
            for o in f["objects"]:
                if o["class"] == "AssetBundle" and first and (only is None or "AssetBundle" in only):
                    items.append(contract.item(contract.object_id(f["name"], o["pathId"]), "contained",
                                               within=first, cls="AssetBundle"))
        return Output(arts, items)


REAL = {s.name: s for s in cli_assets.STAGE_SOURCES}
FAKE_SOURCES = (REAL["catalog.index"], cli_assets.StageSource("unity.census", "test_cli_assets:FakeCensus"),
                REAL["link.scripts"], REAL["link.addresses"],
                cli_assets.StageSource("unity.export", "test_cli_assets:FakeExport", output=True,
                                       root="_bundles/{subject}"),
                REAL["link.artifacts"])


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES)


def nn(capsys, *argv):
    """Run an asset command; (exit code, stdout, stderr)."""
    try:
        cli_assets.main([str(a) for a in argv])
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    out, err = capsys.readouterr()
    return code, out, err


def flags(d: dict) -> list:
    return ["--cache", d["cache"], "--catalog", d["catalog"]]


def tree(root: Path, skip=()) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not any(rel.startswith(s) for s in skip):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def export(capsys, tmp_path, d, store, out, *more):
    return nn(capsys, *flags(d), "export", "-o", tmp_path / out, "--store", tmp_path / store, "--workers", "0",
              *more)


# ---------------------------------------------------------------- selection
def test_selection_items_and_groups():
    assert cli_assets.parse_selection([]) == ["all"]
    assert cli_assets.parse_selection(["key:Card/", "group:bg", "key:Card/"]) == ["group:bg", "key:Card/"]
    with pytest.raises(ValueError):
        cli_assets.parse_selection(["card"])
    assert cli_assets.bundle_group("membercard_assets_membercard_100101") == "membercard"
    assert cli_assets.bundle_group("live2d_yomogi_023") == "live2d"


def test_select_adds_the_dependencies_to_the_census():
    from nnnotes.catalogdb import index
    idx = index(catalog_bytes())
    stable = {n: contract.stable_bundle_name(NAMES[n]) for n in NAMES}
    s = cli_assets.select(idx, ["group:card"])
    assert s.selected == sorted([stable["card_1"], stable["card_2"]])
    assert s.census == sorted([stable["card_1"], stable["card_2"], stable["shared"]])
    s = cli_assets.select(idx, ["key:Card/1/"])
    assert s.selected == s.census == sorted([stable["card_1"], stable["shared"]])
    s = cli_assets.select(idx, ["bundle:bg_*"])
    assert s.selected == s.census == [stable["bg_x"]]
    assert cli_assets.select(idx, ["all"]).selected == sorted(stable.values())


# ---------------------------------------------------------------- export
def test_export_writes_layouts_reports_and_hits_the_second_time(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    code, out, err = export(capsys, tmp_path, d, "store", "out")
    assert code == 1, err                                            # the broken object is a failed item
    summary = json.loads(out)
    assert summary["items"] == {"exported": 8, "contained": 4, "generic": 0, "unsupported": 0, "failed": 1}
    files = tree(tmp_path / "out")
    assert "Assets/Game/Card/1/full.png.json" in files               # the catalog's case of the container path
    assert "Assets/Game/Card/1/full[card1].png.json" in files         # the sub-sprite
    assert "Assets/Game/Bg/x.prefab.json" in files
    mb = f"_bundles/{contract.stable_bundle_name(NAMES['card_1'])}/MonoBehaviour/~7.json"
    body = contract.loads((tmp_path / "out" / mb).read_bytes())
    assert body["script"] == "Game|Game|Widget"                      # resolved through link.scripts
    reports = tmp_path / "out" / cli_assets.REPORTS
    fails = contract.loads((reports / "failures.json").read_bytes())["failures"]
    assert [(f["code"], f["object"]) for f in fails] == [("fake.broken", f"{CABS['bg_x']}:4")]
    cov = contract.loads((reports / "coverage.json").read_bytes())["classes"]
    assert cov["Texture2D"]["total"] == 2 and cov["Texture2D"]["exported"] == 2
    assert cov["AssetBundle"] == {"total": 4, "missing": 0, "exported": 0, "contained": 4, "generic": 0,
                                  "unsupported": 0, "failed": 0}
    run = contract.loads((reports / "run.json").read_bytes())
    assert Store(tmp_path / "store").run(run["id"]) == run
    assert run["context"]["catalog"]["sha"] == contract.sha256(d["catalog"].read_bytes())
    first = files
    code, out, _ = export(capsys, tmp_path, d, "store", "out")
    again = json.loads(out)
    assert code == 1 and again["tasks"]["ran"] == 0 and again["run"] == summary["run"]
    assert again["layouts"]["original"]["write"] == 0
    same = lambda t: {k: v for k, v in t.items() if k != f"{cli_assets.REPORTS}/run.json"}   # noqa: E731
    assert same(tree(tmp_path / "out")) == same(first)              # the run's statuses are hits now
    code, out, _ = nn(capsys, *flags(d), "plan", "-o", tmp_path / "out", "--store", tmp_path / "store", "--check")
    assert code == 0, out


def test_export_is_the_same_with_worker_processes(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    export(capsys, tmp_path, d, "s0", "o0")
    code, _, err = nn(capsys, *flags(d), "export", "-o", tmp_path / "o2", "--store", tmp_path / "s2",
                      "--workers", "2", "--layout", "original,cas")
    assert code == 1, err
    assert tree(tmp_path / "s0", ["inputs", "runs", "costs"]) == tree(tmp_path / "s2", ["inputs", "runs", "costs"])
    o0, o2 = tree(tmp_path / "o0"), tree(tmp_path / "o2" / "original")
    assert {k: v for k, v in o0.items() if not k.startswith(cli_assets.REPORTS)} == o2
    cas = tree(tmp_path / "o2" / "cas")
    assert all(p.startswith("assets/") or p == "manifest.json" for p in cas)


def test_plan_emit_tasks_and_run_stage_give_the_exported_store(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    export(capsys, tmp_path, d, "orch", "o")
    rounds = 0
    while True:
        tasks = tmp_path / f"round{rounds}"
        code, out, err = nn(capsys, *flags(d), "plan", "--store", tmp_path / "q", "--emit-tasks", tasks, "--json")
        assert code == 0, err
        doc = json.loads(out)
        if not any(n["status"] == "run" for n in doc["nodes"]):
            break
        code, out, err = nn(capsys, *flags(d), "run-stage", tasks, "--store", tmp_path / "q")
        assert code in (0, 1), err
        rounds += 1
    assert rounds == 4                          # index + census; scripts + addresses; export; artifacts
    skip = ["inputs", "runs", "costs", "catalogs"]
    assert tree(tmp_path / "orch", skip) == tree(tmp_path / "q", skip)


def test_plan_explains_and_waits_for_bundles_not_fetched(tmp_path, capsys, fake):
    d = setup_data(tmp_path, cached={"card_1", "shared", "bg_x"})
    code, out, _ = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--json", "--check")
    assert code == 1
    nodes = {n["id"]: n for n in json.loads(out)["nodes"]}
    card2 = contract.stable_bundle_name(NAMES["card_2"])
    assert nodes[f"unity.census:{card2}"]["status"] == "unknown"
    assert nodes[f"unity.census:{card2}"]["reasons"] == [{"code": "pending", "task": f"fetch:{NAMES['card_2']}"}]
    assert nodes["link.scripts:all"]["status"] == "unknown"
    assert nodes["catalog.index:main"]["reasons"] == [{"code": "new-input"}]
    code, out, _ = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s")
    assert "unknown" in out and "tasks:" in out


def test_parameters_change_keys_and_are_explained(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    export(capsys, tmp_path, d, "s", "o")
    base = [*flags(d), "plan", "--store", tmp_path / "s", "--json"]
    code, out, _ = nn(capsys, *base, "--png-level", "3")
    runs = [n for n in json.loads(out)["nodes"] if n["status"] == "run"]
    assert {n["stage"] for n in runs} == {"unity.export"}
    assert runs[0]["reasons"] == [{"code": "params-changed", "path": "/png/level", "old": 6, "new": 3}]
    tid = runs[0]["id"]
    code, out, _ = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--png-level", "3", "--why", tid)
    w = json.loads(out)
    assert w["keyParts"]["old"]["params"]["png"]["level"] == 6 and w["keyParts"]["new"]["params"]["png"]["level"] == 3
    code, out, _ = export(capsys, tmp_path, d, "s", "o2", "--only-class", "Texture2D", "--select", "group:card")
    summary = json.loads(out)
    assert code == 0 and summary["items"]["exported"] == 2 and summary["selection"] == ["group:card"]


def test_usage_errors(tmp_path, capsys, fake, monkeypatch):
    d = setup_data(tmp_path)
    assert export(capsys, tmp_path, d, "s", "o", "--png-level", "12")[0] == 2
    assert export(capsys, tmp_path, d, "s", "o", "--select", "card")[0] == 2
    assert export(capsys, tmp_path, d, "s", "o", "--layout", "flat")[0] == 2
    assert export(capsys, tmp_path, d, "s", "o", "--views", "cards")[0] == 2
    assert export(capsys, tmp_path, d, "s", "o", "--catalog-version", "nothing")[0] == 2
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", tuple(s for s in FAKE_SOURCES if s.name != "unity.export"))
    code, _, err = export(capsys, tmp_path, d, "s", "o", "--png-level", "3")
    assert code == 2 and "--png-level" in err
    code, _, err = export(capsys, tmp_path, d, "s", "o", "--flac-level", "5")
    assert code == 2 and "no stage of the pipeline encodes FLAC" in err
    code, _, err = nn(capsys, "export", "-o", tmp_path / "o")
    assert code == 2 and "paths.store" in err                        # no store and no cache


def test_the_flac_level_is_a_parameter_of_the_stages_that_encode_flac(tmp_path, capsys, fake, monkeypatch):
    monkeypatch.setattr(FakeExport, "PARAMS", {**FakeExport.PARAMS, "flac": {"level": 8}})
    d = setup_data(tmp_path)
    assert export(capsys, tmp_path, d, "s", "o", "--flac-level", "13")[0] == 2
    export(capsys, tmp_path, d, "s", "o")
    base = [*flags(d), "plan", "--store", tmp_path / "s", "--flac-level", "5"]
    code, out, _ = nn(capsys, *base, "--json")
    tid = next(n["id"] for n in json.loads(out)["nodes"] if n["stage"] == "unity.export")
    code, out, _ = nn(capsys, *base, "--why", tid)
    assert json.loads(out)["keyParts"]["new"]["params"]["flac"] == {"level": 5}


def test_the_placement_is_probed_once_and_never_changes_the_files(tmp_path, capsys, fake, monkeypatch):
    import errno
    from nnnotes import layout

    def clone_by_copy(src, dst):
        with open(dst, "xb") as f:
            f.write(Path(src).read_bytes())

    def unsupported(*a, **k):
        raise OSError(errno.EOPNOTSUPP, "not here")
    d = setup_data(tmp_path)
    monkeypatch.setattr(layout, "clone", unsupported)
    code, _, err = export(capsys, tmp_path, d, "s", "o0", "--link", "clone")
    assert code == 2 and "--link clone: clone unsupported" in err
    assert not (tmp_path / "s" / "ac").exists()                                  # nothing ran
    trees, runs = {}, {}
    for how in ("copy", "hard", "clone", "auto"):
        if how == "clone":
            monkeypatch.setattr(layout, "clone", clone_by_copy)
        code, out, err = export(capsys, tmp_path, d, "s", f"o-{how}", *(("--link", how) if how != "auto" else ()))
        summary = json.loads(out)
        assert code == 1, err
        runs[how] = contract.loads((tmp_path / f"o-{how}" / cli_assets.REPORTS / "run.json").read_bytes())
        trees[how] = {k: v for k, v in tree(tmp_path / f"o-{how}").items() if not k.endswith("/run.json")}
        n = summary["layouts"]["original"]
        assert summary["placement"] == runs[how]["placement"] and n["failed"] == 0
        assert n[{"copy": "copied", "hard": "linked", "clone": "cloned", "auto": "cloned"}[how]] == n["write"] > 0
        assert "hard-linked (read-only)" in err
    assert trees["copy"] == trees["hard"] == trees["clone"] == trees["auto"]
    assert {h: r["placement"]["method"] for h, r in runs.items()} == {"copy": "copy", "hard": "hard",
                                                                       "clone": "clone", "auto": "clone"}
    assert len({r["id"] for r in runs.values()}) == 1 and runs["copy"]["placement"]["reason"] == "copy: as asked"
    monkeypatch.setattr(layout.os, "link", lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, "elsewhere")))
    assert export(capsys, tmp_path, d, "s", "o5", "--link", "hard")[0] == 2
    monkeypatch.setenv("NNNOTES_EXPORT_LINK", "copy")
    code, out, _ = export(capsys, tmp_path, d, "s", "o6")
    assert json.loads(out)["placement"] == {"method": "copy", "reason": "copy: as asked"}
    monkeypatch.setenv("NNNOTES_EXPORT_LINK", "symlink")
    code, _, err = export(capsys, tmp_path, d, "s", "o7")
    assert code == 2 and "export.link" in err


def test_an_aborted_run_keeps_its_results(tmp_path, capsys, fake):
    docs = censuses()
    docs["bg_x"]["files"][0]["objects"].append(obj(9, "Crash"))
    d = setup_data(tmp_path, docs)
    code, out, err = export(capsys, tmp_path, d, "s", "o")
    assert code == 3 and "aborted" in err
    run = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "run.json").read_bytes())
    st = {t["id"]: t["status"] for t in run["tasks"]}
    assert st[f"unity.census:{contract.stable_bundle_name(NAMES['bg_x'])}"] == "ran"


def test_export_dry_run_and_explain(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    code, out, err = export(capsys, tmp_path, d, "s", "o", "--dry-run", "--explain")
    assert code == 0 and "to run" in out and not (tmp_path / "o").exists()
    assert "pipeline: catalog.index, unity.census, link.scripts, link.addresses, unity.export" in err
    assert "not installed:" in err and "workers: none" in err


# ---------------------------------------------------------------- run-stage
def test_run_stage_refuses_incompatible_tasks(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--emit-tasks", tmp_path / "t")
    (f,) = [p for p in sorted((tmp_path / "t").glob("*.json"))
            if contract.loads(p.read_bytes())["stage"]["name"] == "catalog.index"]
    doc = contract.loads(f.read_bytes())
    edited = tmp_path / "edited.json"
    edited.write_bytes(contract.encode(dict(doc, params={"x": 1})))
    code, _, err = nn(capsys, "run-stage", f, edited, "--store", tmp_path / "s")
    assert code == 4 and "incompatible task" in err
    assert not (tmp_path / "s" / "ac").exists()                     # nothing of the batch ran
    other = tmp_path / "other.json"
    t = contract.Task.from_json(doc)
    other.write_bytes(contract.encode(contract.Task("catalog.index", 9, t.subject, t.params, t.atoms, t.inputs,
                                                     t.context).to_json()))
    assert nn(capsys, "run-stage", other, "--store", tmp_path / "s")[0] == 4
    code, out, _ = nn(capsys, "run-stage", f, "--store", tmp_path / "s")
    assert code == 0 and json.loads(out)["summary"] == {"tasks": 1, "hit": 0, "ran": 1, "failed": 0}


def test_plan_and_run_stage_do_not_import_unitypy(tmp_path):
    d = setup_data(tmp_path)
    script = f"""
import sys
from nnnotes import cli_assets
args = {[str(a) for a in flags(d)]!r}
from pathlib import Path
from nnnotes import contract
def main(argv):
    try:
        cli_assets.main(argv)
    except SystemExit as e:
        assert not e.code, e.code
main(args + ["plan", "--store", {str(tmp_path / 's')!r}, "--emit-tasks", {str(tmp_path / 't')!r}])
index = [str(p) for p in Path({str(tmp_path / 't')!r}).glob("*.json")
         if contract.loads(p.read_bytes())["stage"]["name"] == "catalog.index"]
assert len(index) == 1, index
main(["run-stage", *index, "--store", {str(tmp_path / 's')!r}])
main(args + ["plan", "--store", {str(tmp_path / 's')!r}, "--json"])
assert "UnityPy" not in sys.modules, "UnityPy imported"
"""
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-3000:]


# ---------------------------------------------------------------- catalogs and store
def test_catalog_versions_import_list_and_diff(tmp_path, capsys):
    a = tmp_path / "a.bin"
    a.write_bytes(catalog_bytes())
    entries = [(NAMES[n], synth.remote(NAMES[n]), []) for n in ("card_1", "shared")]
    entries += [("Card/1/full", "Assets/Game/Card/1/full.png", [0, 1], {"type": "UnityEngine.Texture2D"}),
                ("Card/3/full", "Assets/Game/Card/3/full.png", [0], {"type": "UnityEngine.Texture2D"})]
    b = tmp_path / "b.bin"
    b.write_bytes(synth.CatalogWriter().build(entries))
    s = ["--store", tmp_path / "s"]
    assert nn(capsys, "catalogs", "import", a, "--label", "v1", *s)[0] == 0
    code, out, _ = nn(capsys, "catalogs", "import", b, *s)
    assert code == 0 and contract.sha256(b.read_bytes())[:12] in out
    assert nn(capsys, "catalogs", "import", b, "--label", "v1", *s)[0] == 2     # a label names one version
    code, out, _ = nn(capsys, "catalogs", "list", "--json", *s)
    vs = json.loads(out)["versions"]
    assert [v["labels"] for v in vs] == [["v1"], [contract.sha256(b.read_bytes())[:12]]]
    code, out, _ = nn(capsys, "catalogs", "diff", "v1", vs[1]["id"][:12], "--json", *s)
    diff = json.loads(out)
    assert diff["keys"]["added"] == ["Card/3/full"] and diff["keys"]["removed"] == ["Bg/x", "Card/2/full"]
    code, out, _ = nn(capsys, "catalogs", "diff", "v1", "v1", *s)
    assert code == 0 and out.splitlines()[-1].startswith("# keys +0 -0 ~0")
    assert nn(capsys, "catalogs", "diff", "v1", "v9", *s)[0] == 2


def test_store_verify(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    export(capsys, tmp_path, d, "s", "o")
    code, out, _ = nn(capsys, "store", "verify", "--store", tmp_path / "s")
    r = json.loads(out)
    assert code == 0 and r["problemCount"] == 0 and r["contents"] > 0 and r["results"] > 0
    victim = next(p for p in sorted((tmp_path / "s" / "cas").rglob("*")) if p.is_file())
    assert os.stat(victim).st_mode & 0o222 == 0                          # store objects are read-only
    os.chmod(victim, 0o666)
    victim.write_bytes(victim.read_bytes() + b"x")
    code, out, _ = nn(capsys, "store", "verify", "--store", tmp_path / "s")
    problems = {p["problem"] for p in json.loads(out)["problems"]}
    assert code == 1 and "content differs from its id" in problems
    assert any(p.endswith("bytes of another size") for p in problems)
    code, out, _ = nn(capsys, "store", "verify", "--quick", "--store", tmp_path / "s")
    assert code == 1 and all("another size" in p["problem"] for p in json.loads(out)["problems"])


# ---------------------------------------------------------------- the command line
def test_the_commands_are_part_of_nnnotes(tmp_path, capsys, fake):
    try:
        cli.build_parser().parse_args(["store", "verify"])
    except SystemExit:
        pytest.skip("the command line does not register the asset commands")
    assert ("paths", "store") in cli.FLAG_SETTINGS
    d = setup_data(tmp_path)
    with pytest.raises(SystemExit) as e:
        cli.main([*map(str, flags(d)), "export", "-o", str(tmp_path / "o"), "--store", str(tmp_path / "s"),
                  "--workers", "0"])
    assert e.value.code == 1
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        cli.main([*map(str, flags(d)), "plan", "-o", str(tmp_path / "o"), "--store", str(tmp_path / "s"), "--check"])
    assert e.value.code == 0


class ObjectPaths(Stage):
    """An output stage with a derived document: the layout files of every object of the selected bundles' censuses
    and of one unknown object ({object id: {artifact id: path}}), placed at derived/objects.json."""
    name = "fake.paths"
    version = 1
    after = ("unity.census",)

    def subjects(self, env):
        return ["all"]

    def depends(self, subject, env):
        return [contract.task_id("unity.census", s) for s in env.fact("selected")]

    def inputs(self, subject, env):
        return [env.input_of(contract.task_id("unity.census", s), "census", f"census:{s}")
                for s in env.fact("selected")]

    def estimate(self, subject, env, inputs):
        return Cost(0.01, 1 << 20)

    def run(self, task, store):
        oids = ["CAB-none:9"]
        for inp in task.inputs:
            for f in contract.loads(store.input_bytes(inp))["files"]:
                oids += [contract.object_id(f["name"], o["pathId"]) for o in f["objects"]]
        rec = contract.artifact(contract.artifact_id(task.id, "objects"), store.add(contract.encode(sorted(oids)),
                                                                                    "json"),
                                contract.provenance(task), {"kind": "fake.objects"})
        return Output([rec], [])

    def derived(self, task_id, result, store, paths):
        oids = contract.loads(store.read(result["artifacts"][0]["content"]["sha256"]))
        doc = {o: {a: paths.artifact(a) for a in paths.objects(o)} for o in oids}
        return [("derived/objects.json", contract.encode(doc))]


def test_derived_documents_read_the_paths_of_the_objects_they_name(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (
        cli_assets.StageSource("fake.paths", "test_cli_assets:ObjectPaths", output=True),))
    d = setup_data(tmp_path)
    code, _, err = export(capsys, tmp_path, d, "s", "o")
    assert code == 1, err
    doc = contract.loads((tmp_path / "o" / "derived" / "objects.json").read_bytes())
    card = f"{CABS['card_1']}:5"
    assert doc["CAB-none:9"] == {} and doc[f"{CABS['bg_x']}:4"] == {}                 # unknown; failed
    assert doc[card] == {f"{card}#json": "Assets/Game/Card/1/full.png.json"}
    assert doc[f"{CABS['card_1']}:1"] == doc[card]                                   # contained in it
    assert (tmp_path / "o" / "Assets" / "Game" / "Card" / "1" / "full.png.json").is_file()
    code, out, _ = nn(capsys, *flags(d), "plan", "-o", tmp_path / "o", "--store", tmp_path / "s", "--check")
    assert code == 0, out


# ---------------------------------------------------------------- views and the documents of a run
def test_views_are_placed_with_paths_and_every_document_validates(tmp_path, capsys, monkeypatch):
    from test_contract import validators
    from nnnotes import views
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (REAL["view."],))
    rules = views.load_rules()
    master = tmp_path / "master"
    master.mkdir()
    tables = sorted({t for n in ("cards", "jackets") for t in views.view_tables(rules, n)})
    for t in tables:
        (master / f"{t}.json").write_text('{"_allData": []}', encoding="utf-8")
    (master / "MasterManifest.json").write_text('{"version": "1.2.3", "files": []}', encoding="utf-8")
    d = setup_data(tmp_path)
    code, out, err = nn(capsys, *flags(d), "--master", master, "export", "-o", tmp_path / "o", "--store",
                        tmp_path / "s", "--workers", "0", "--views", "cards,jackets", "--layout", "original,cas")
    assert code == 1, err
    run = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "run.json").read_bytes())
    sha = contract.sha256((master / "MasterText.json").read_bytes())
    assert run["context"]["master"]["version"] == "1.2.3"
    assert sorted(run["context"]["master"]["tables"]) == tables and run["context"]["master"]["tables"]["MasterText"] == sha
    placed = tree(tmp_path / "o" / "original")
    assert "views/cards.json" in placed and "views/jackets.json" in placed
    assert not any(p.startswith("_tasks/view.") for p in placed)          # placed through the derived documents
    doc = contract.loads((tmp_path / "o" / "original" / "views" / "cards.json").read_bytes())
    assert doc["layout"] == "original" and doc["view"] == "cards"
    store = Store(tmp_path / "s")
    run = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "run.json").read_bytes())
    v = validators()
    checked = set()
    for t in run["tasks"]:
        res = store.result(t["key"])
        for a in res["artifacts"]:
            if a["content"]["ext"] != "json":
                continue
            body = contract.loads(store.read(a["content"]["sha256"]))
            name = body.get("schema") if isinstance(body, dict) else None
            if name in v:
                errors = [e.message for e in v[name].iter_errors(body)]
                assert errors == [], (a["id"], errors[:3])
                checked.add(name)
    assert checked >= {contract.CATALOG_INDEX, contract.CENSUS, contract.VIEW, "nnnotes.scripts/1",
                       "nnnotes.addresses/1", "nnnotes.artifacts/1"}, checked
    code, out, _ = nn(capsys, *flags(d), "--master", master, "plan", "--store", tmp_path / "s", "--json",
                      "--views", "cards,jackets", "--emit-tasks", tmp_path / "t")
    docs = [(contract.PLAN, json.loads(out)), (contract.RUN, run),
            ("nnnotes.catalogs/1", contract.loads((tmp_path / "s" / "catalogs" / "index.json").read_bytes()))]
    docs += [(contract.TASK, contract.loads(p.read_bytes())) for p in (tmp_path / "t").glob("*.json")]
    for name in ("coverage", "failures"):
        docs.append((f"nnnotes.{name}/1", contract.loads((tmp_path / "o" / cli_assets.REPORTS
                                                          / f"{name}.json").read_bytes())))
    docs.append((contract.LAYOUT, contract.loads((tmp_path / "o" / "cas" / "manifest.json").read_bytes())))
    for name, body in docs:
        errors = [e.message for e in v[name].iter_errors(body)]
        assert errors == [], (name, errors[:3])
    vid = run["context"]["catalog"]["id"]
    code, out, _ = nn(capsys, "catalogs", "diff", vid[:12], vid[:12], "--json", "--store", tmp_path / "s")
    diff = json.loads(out)
    assert diff["objects"]["summary"]["unchanged"] > 0                   # the address tables of the runs
    assert [e.message for e in v["nnnotes.catalog-diff/1"].iter_errors(diff)] == []


# ---------------------------------------------------------------- cost calibration
def test_calibration_scales_estimates_from_measured_costs():
    log = []
    for i in range(12):
        t = f"unity.export:b{i}"
        log.append({"event": "start", "task": t, "estimate": {"cpuSeconds": 1.0, "peakBytes": 100 << 20}})
        log.append({"event": "end", "task": t, "ok": True,
                    "cost": {"cpuSeconds": 2.0, "wallSeconds": 2.0, "peakRssBytes": (70 << 20) + (50 << 20)}})
    log.append({"event": "start", "task": "link.scripts:all", "estimate": {"cpuSeconds": 1.0, "peakBytes": 1}})
    log.append({"event": "end", "task": "link.scripts:all", "ok": True, "cost": {"cpuSeconds": 9.0}})
    f = cli_assets.calibrate(log, {"unity.export": {"cpu": 1.5, "peak": 1.0, "samples": 3}}, 70 << 20)
    assert f == {"unity.export": {"cpu": 3.0, "peak": 0.5, "samples": 12}}           # too few samples: no factor
    stage = cli_assets.Calibrated(FakeExport(), 3.0, 0.5)
    assert stage.estimate("x", None, []) == Cost(0.03, 1 << 19)
    assert stage.name == "unity.export" and stage.PARAMS is FakeExport.PARAMS
    import pickle
    again = pickle.loads(pickle.dumps(stage))
    assert again.estimate("x", None, []) == Cost(0.03, 1 << 19)


def test_calibration_counts_the_memory_a_task_adds_to_its_worker():
    log = []
    for i in range(10):
        t = f"unity.export:b{i}"
        log.append({"event": "start", "task": t, "estimate": {"cpuSeconds": 1.0, "peakBytes": 100 << 20}})
        log.append({"event": "end", "task": t, "ok": True, "cost": {
            "cpuSeconds": 1.0, "wallSeconds": 1.0, "peakRssBytes": (400 << 20) + (25 << 20),
            "startRssBytes": 400 << 20}})                    # a worker holding 400 MiB from earlier tasks
    assert cli_assets.calibrate(log, {}, 70 << 20)["unity.export"]["peak"] == 0.25


def test_the_worker_recycling_threshold_follows_the_budget():
    gib = cli_assets.GIB
    assert cli_assets.recycle_rss(None, 8) == cli_assets.RECYCLE_RSS == 3 * gib
    assert cli_assets.recycle_rss(100 * gib, 12) == 3 * gib
    assert cli_assets.recycle_rss(gib, 1) == int(0.4 * gib)
    assert cli_assets.recycle_rss(16 * gib, 12) == int(0.4 * 16 * gib / 12)
    assert cli_assets.recycle_rss(gib, 0) == int(0.4 * gib)                           # tasks in this process
    assert cli_assets.recycle_rss(gib, 64) == 256 << 20


def test_export_records_the_calibration_and_plan_uses_it(tmp_path, capsys, fake):
    d = setup_data(tmp_path)
    export(capsys, tmp_path, d, "s", "o")
    cal = tmp_path / "s" / "costs" / cli_assets.CALIBRATION
    assert contract.loads(cal.read_bytes()) == {"stages": {}}                        # four bundles: too few
    cli_assets.write_calibration(tmp_path / "s", {"unity.export": {"cpu": 2.0, "peak": 3.0, "samples": 50}})
    code, out, err = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--json", "--png-level", "1",
                        "--explain")
    assert "cost calibration unity.export: CPU x2.0, peak memory x3.0 (50 measured tasks)" in err
    runs = [n for n in json.loads(out)["nodes"] if n["stage"] == "unity.export"]
    assert runs and all(n["cost"] == {"cpuSeconds": 0.02, "peakBytes": 3 << 20} for n in runs)
    code, out, _ = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--check")
    assert code == 0                                                                # keys do not change


def test_files_that_cannot_be_read_are_reported_not_failed(tmp_path, capsys, fake):
    d = setup_data(tmp_path, censuses(broken=False), extra=[("apkonly.bundle", synth.local("apkonly.bundle"), [])])
    code, out, err = export(capsys, tmp_path, d, "s", "o")
    summary = json.loads(out)
    assert code == 0, err
    assert summary["sources"] == {"source.absent": 1} and summary["failures"] == 0
    (p,) = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "sources.json").read_bytes())["problems"]
    assert p["stable"] == "apkonly" and "[paths] apk is not set" in p["message"]


# ---------------------------------------------------------------- stages that name files, stages that name tools
class Named(Stage):
    """Artifacts of no object that the layout roots at the catalog names of their subject."""
    name, version = "toy.named", 1

    def subjects(self, env):
        return ["s1", "s2"]

    def names(self, env):
        return {"s1": ["Cri/Sound/y", "Cri/Sound/x"]}

    def run(self, task, store):
        arts = [contract.artifact(contract.artifact_id(task.id, role), store.add(data, ext),
                                  contract.provenance(task), {"kind": "toy"})
                for role, data, ext in (("a.flac", b"fLaC" + task.subject.encode(), "flac"),
                                        ("cues.json", b"[]", "json"))]
        return Output(arts, [])


class Tooled(Stage):
    """A stage whose atom id names a configured tool (as the CRI stages name ffmpeg)."""
    name, version = "toy.tool", 1

    def subjects(self, env):
        return ["s"]

    def uses(self, subject, env):
        from nnnotes.config import active
        cfg = active()
        return {"tool": str(cfg.path("paths", "ffmpeg")) if cfg is not None else "unset"}

    def run(self, task, store):
        return Output([], [])


def test_named_contents_are_placed_under_each_name(tmp_path, capsys, monkeypatch):
    from nnnotes import layout
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (
        cli_assets.StageSource("toy.named", "test_cli_assets:Named", output=True),))
    d = setup_data(tmp_path, censuses(broken=False))
    code, out, err = export(capsys, tmp_path, d, "s", "o", "--layout", "original,cas")
    assert code == 0, err
    orig = tree(tmp_path / "o" / "original")
    assert {"Cri/Sound/x/a.flac", "Cri/Sound/x/cues.json", "Cri/Sound/y/a.flac", "Cri/Sound/y/cues.json",
            "_tasks/toy.named/s2/a.flac"} <= set(orig)
    assert orig["Cri/Sound/x/a.flac"] == orig["Cri/Sound/y/a.flac"]
    report = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "layout-original.json").read_bytes())
    assert report["names"] == [{"task": "toy.named:s1", "names": ["Cri/Sound/x", "Cri/Sound/y"]}]
    mo = contract.loads((tmp_path / "o" / "original" / "manifest.json").read_bytes())
    mc = contract.loads((tmp_path / "o" / "cas" / "manifest.json").read_bytes())
    derived = {e["id"] for e in mo["entries"]} - {e["id"] for e in mc["entries"]}
    assert not derived and layout.cas_from_manifest(mo) == mc["entries"]


def test_plan_and_export_describe_with_the_same_settings(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (
        cli_assets.StageSource("toy.tool", "test_cli_assets:Tooled"),))
    d = setup_data(tmp_path, censuses(broken=False))
    tool = tmp_path / "ffmpeg-bin"
    code, _, err = nn(capsys, *flags(d), "--ffmpeg", tool, "plan", "--store", tmp_path / "s", "--emit-tasks",
                      tmp_path / "t")
    assert code == 0, err
    (planned,) = [contract.loads(p.read_bytes()) for p in (tmp_path / "t").glob("*.json")
                  if contract.loads(p.read_bytes())["stage"]["name"] == "toy.tool"]
    assert planned["atoms"] == {"tool": str(tool)}
    code, _, err = nn(capsys, *flags(d), "--ffmpeg", tool, "export", "-o", tmp_path / "o", "--store",
                      tmp_path / "s", "--workers", "0")
    run = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "run.json").read_bytes())
    assert {t["id"]: t["key"] for t in run["tasks"]}["toy.tool:s"] == planned["key"]


def test_plan_knows_the_files_the_apk_does_not_have(tmp_path, capsys, fake, monkeypatch):
    import zipfile
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build([("apkonly.bundle",
                                                                          synth.local("apkonly.bundle"), [])]))
    d = setup_data(tmp_path, censuses(broken=False), extra=[("apkonly.bundle", synth.local("apkonly.bundle"), [])])
    monkeypatch.setattr(cli_assets, "_apk_version", lambda apk: None)
    code, out, err = nn(capsys, *flags(d), "--apk", apk, "plan", "--store", tmp_path / "s", "--json")
    nodes = {n["id"]: n for n in json.loads(out)["nodes"]}
    assert "unity.census:apkonly" not in nodes                      # not pending on a fetch that cannot happen
    code, out, err = nn(capsys, *flags(d), "--apk", apk, "plan", "--store", tmp_path / "s", "--emit-tasks",
                        tmp_path / "t")
    assert nn(capsys, *flags(d), "run-stage", tmp_path / "t", "--store", tmp_path / "s")[0] == 0
    code, out, _ = nn(capsys, *flags(d), "--apk", apk, "plan", "--store", tmp_path / "s", "--json")
    nodes = {n["id"]: n for n in json.loads(out)["nodes"]}
    assert nodes["link.scripts:all"]["status"] == "run"


def test_plan_census_takes_the_censuses_first(tmp_path, capsys, fake):
    d = setup_data(tmp_path, censuses(broken=False))
    code, out, err = nn(capsys, *flags(d), "plan", "--store", tmp_path / "s", "--census", "--workers", "0",
                        "--json")
    assert code == 0, err
    nodes = json.loads(out)["nodes"]
    census = [n for n in nodes if n["stage"] == "unity.census"]
    assert census and all(n["status"] == "hit" for n in census)
    assert {n["status"] for n in nodes if n["stage"] == "link.scripts"} == {"run"}


class NamedByExport(Named):
    """Names that read the unity.export results (as bundle-held CRI content does)."""
    name = "toy.byexport"

    def names(self, env):
        return {"s1": [f"Named/{len(env.tasks('unity.export'))}"]}


def test_names_see_the_tasks_that_ran(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (
        cli_assets.StageSource("toy.byexport", "test_cli_assets:NamedByExport", output=True),))
    d = setup_data(tmp_path, censuses(broken=False))
    code, out, err = export(capsys, tmp_path, d, "s", "o")
    assert code == 0, err
    placed = tree(tmp_path / "o")
    assert "Named/4/a.flac" in placed and "Named/0/a.flac" not in placed        # four bundles were exported
    code, out, _ = nn(capsys, *flags(d), "plan", "-o", tmp_path / "o", "--store", tmp_path / "s", "--check")
    assert code == 0, out                                                         # plan places them the same way


def test_calibration_keeps_the_cpu_factor_without_child_measurements():
    log = []
    for i in range(12):
        t = f"cri.audio:{i}"
        log.append({"event": "start", "task": t, "estimate": {"cpuSeconds": 1.0, "peakBytes": 100 << 20}})
        log.append({"event": "end", "task": t, "ok": True,
                    "cost": {"cpuSeconds": 0.1, "childCpuSeconds": None, "peakRssBytes": (70 << 20) + (100 << 20)}})
    f = cli_assets.calibrate(log, {"cri.audio": {"cpu": 2.0, "peak": 1.0, "samples": 20}}, 70 << 20)
    assert f == {"cri.audio": {"cpu": 2.0, "peak": 1.0, "samples": 12}}


def test_census_documents_with_binding_scripts_validate():
    from test_contract import validators
    doc = cdoc(CABS["card_1"], [obj(1, "AssetBundle", "ab"), obj(9, "AnimationClip", "clip")])
    doc["files"][0]["objects"][1]["bindingScripts"] = [{"file": CABS["shared"], "pathId": 42},
                                                       {"fileId": 3, "pathId": 7}]
    v = validators()[contract.CENSUS]
    assert [e.message for e in v.iter_errors(doc)] == []
    doc["files"][0]["objects"][1]["bindingScripts"] = [{"pathId": 7}]
    assert list(v.iter_errors(doc))


class RawCopy(Stage):
    """The raw files of the selection (the fact the CRI stages read), each stored as its artifact."""
    name, version = "cri.audio", 1

    def subjects(self, env):
        return sorted(env.fact("raw"))

    def inputs(self, subject, env):
        return [env.fact("raw")[subject]]

    def run(self, task, store):
        content = store.add(store.input_bytes(task.input("raw")), "acb")
        return Output([contract.artifact(contract.artifact_id(task.id, "sheet.acb"), content,
                                         contract.provenance(task), {"kind": "toy"})], [])


def test_raw_files_of_the_apk_are_read_from_it(tmp_path, capsys, monkeypatch):
    import zipfile
    monkeypatch.setattr(cli_assets, "STAGE_SOURCES", FAKE_SOURCES + (
        cli_assets.StageSource("cri.audio", "test_cli_assets:RawCopy"),))
    monkeypatch.setattr(cli_assets, "_apk_version", lambda apk: None)
    name = "cri_assets_embcri/sound/initialse_" + "ab" * 16
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build([("Cri/Initial/se", synth.local(name), [])]))
        z.writestr("assets/aa/Android/" + name, b"@UTF sheet")
        z.writestr("assets/bin/Data/data.unity3d", b"boot")
    d = setup_data(tmp_path, censuses(broken=False))
    code, out, err = nn(capsys, *flags(d), "--apk", apk, "plan", "--store", tmp_path / "s", "--json")
    nodes = {n["id"]: n for n in json.loads(out)["nodes"]}
    assert nodes["cri.audio:cri_assets_embcri/sound/initialse"]["reasons"] == [{"code": "pending", "task": f"fetch:{name}"}]
    code, out, err = nn(capsys, *flags(d), "--apk", apk, "export", "-o", tmp_path / "o", "--store", tmp_path / "s",
                        "--workers", "0")
    assert code == 0, err
    assert json.loads(out)["sources"] == {}
    assert (d["cache"] / "raw" / "Android" / name).read_bytes() == b"@UTF sheet"      # where a CDN file would be
    run = contract.loads((tmp_path / "o" / cli_assets.REPORTS / "run.json").read_bytes())
    assert {t["id"]: t["status"] for t in run["tasks"]}["cri.audio:cri_assets_embcri/sound/initialse"] == "ran"
    code, out, _ = nn(capsys, *flags(d), "--apk", apk, "plan", "-o", tmp_path / "o", "--store", tmp_path / "s",
                      "--check")
    assert code == 0, out                                                              # read from the cache now


def test_worker_counts_follow_the_cpus_this_process_may_use(monkeypatch):
    import os
    from types import SimpleNamespace
    from nnnotes import config, cri
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    monkeypatch.setattr(os, "process_cpu_count", lambda: 6, raising=False)
    assert config.usable_cpus() == 6                                   # Python 3.13: the affinity mask
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {3, 4, 5, 6, 7, 8, 9, 10}, raising=False)
    assert config.usable_cpus() == 8                                   # older Pythons: the mask where there is one
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    assert config.usable_cpus() == 64                                  # no mask: the machine's count
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert config.usable_cpus() == 1
    monkeypatch.setattr(config, "usable_cpus", lambda: 12)
    monkeypatch.setattr(cli_assets, "usable_cpus", lambda: 12)
    monkeypatch.setattr(cri, "usable_cpus", lambda: 12)
    ws = cli_assets.Workspace.__new__(cli_assets.Workspace)
    ws.args = SimpleNamespace(workers=None)
    assert ws.workers() == 12 and cri.default_workers() == 3
    ws.args.workers = 0
    assert ws.workers() == 0
