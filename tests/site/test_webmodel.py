import ast
import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from nnnotes import cli
from nnnotes.core import jsonio
from nnnotes.site import sitefiles, siteindex, webmodel
from nnnotes.source.config import Config, ConfigError
from nnnotes.unity import live2d

KEY_A = "Character/Live2D/001_adv/adv_model_a/model/adv_model_a"
KEY_B = "Character/Live2D/sub_x/adv_model_b/model/adv_model_b"


def test_shader_names():
    doc = {
        "nodes": [
            {
                "materials": [
                    {"material": "Lit", "shader": {"shader": "S/Lit"}},
                    {"material": "Mask", "shader": {"shader": "S/Mask"}},
                ]
            }
        ],
        "other": {"shader": "not a reference", "x": 1},
    }
    assert webmodel.shader_names(doc) == {"S/Lit", "S/Mask"}


def drawable(tex, keywords, shader="S/Lit"):
    return {
        "path": "m/Drawables/d",
        "components": [
            {
                "type": "MeshRenderer",
                "m_Materials": [{"material": "Lit", "shader": {"shader": shader}, "keywords": keywords}],
            },
            {"type": "MonoBehaviour", "class": "CubismRenderer", "_mainTexture": {"texture": tex, "name": "page"}},
        ],
    }


def variant(shader, n, keywords, platform="gles3", kind="GLES3", ext="glsl"):
    return {
        "file": f"{shader}/{platform}/s0p0_vertex_{n}.{ext}",
        "platform": platform,
        "subShader": 0,
        "pass": 0,
        "stage": "vertex",
        "type": kind,
        "keywords": keywords,
    }


ALV = "_ADDITIONAL_LIGHTS_VERTEX"
SHADER_INDEX = [
    {
        "name": "S/Lit",
        "source": "x",
        "parsed": "S_Lit.json",
        "variants": [
            variant("S_Lit", 0, []),
            variant("S_Lit", 1, ["CUBISM_MASK_ON"]),
            variant("S_Lit", 2, ["_ADDITIONAL_LIGHTS"]),
            variant("S_Lit", 3, [], kind="GLES31"),
            variant("S_Lit", 4, [], platform="vulkan", kind="SPIRV", ext="vkprog"),
            variant("S_Lit", 5, [ALV]),
            variant("S_Lit", 6, ["CUBISM_MASK_ON", ALV]),
        ],
    },
    {"name": "S/Mask", "source": "y", "parsed": "S_Mask.json", "variants": [variant("S_Mask", 0, [])]},
]
MASKS = {
    "cubismMask": {"material": "Mask", "shader": {"shader": "S/Mask"}, "keywords": [], "floats": {"_Cull": 0.0}},
    "cubismMaskCulling": {
        "material": "MaskCulling",
        "shader": {"shader": "S/Mask"},
        "keywords": [],
        "floats": {"_Cull": 1.0},
    },
}


def fake_model(root, name="m", tex=b"\x89PNG texture a", moc=b"MOC3 bytes", masked=True, key=KEY_A, motion_sync=False):
    """A model directory in the export layout (made-up contents) and its export summary. Two atlas pages, the
    drawables use page 0; drawables masked or not; the root node with the MotionSync components or not."""
    kws = [[], ["CUBISM_MASK_ON"]] if masked else [[], []]
    root_node = {
        "path": name,
        "components": [
            {"type": "MonoBehaviour", "class": c} for c in (webmodel.MOTION_SYNC_COMPONENTS if motion_sync else ())
        ],
    }
    prefab = {
        "key": name,
        "nodes": [root_node, *(drawable("textures/p0-00.png", k) for k in kws)],
        "canvas": {"width": 1.0},
    }
    doc = {
        "format": webmodel.MODEL_FORMAT,
        "name": name,
        "key": key,
        "moc3": f"{name}.moc3",
        "prefab": f"{name}.prefab.json",
        "textures": webmodel.drawable_textures(prefab),
        "canvas": prefab["canvas"],
        "shaders": "shaders/shaders.json",
        "resources": MASKS,
        "motionSync": webmodel.has_motion_sync(prefab),
    }
    files = {
        f"{name}.moc3": moc,
        f"{name}.prefab.json": json.dumps(prefab, indent=1).encode(),
        "textures/p0-00.png": tex,
        "textures/p1-11.png": b"unused page",
        "shaders/shaders.json": json.dumps(SHADER_INDEX).encode(),
        webmodel.MODEL_INDEX: json.dumps(doc).encode(),
        "shaders/S_Lit.json": b'{"name": "S/Lit"}',
        "shaders/S_Mask.json": b'{"name": "S/Mask"}',
    }
    for rec in SHADER_INDEX:
        for v in rec["variants"]:
            files[f"shaders/{v['file']}"] = f"#ifdef VERTEX\n// {v['file']}\n#endif\n".encode()
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    summary = {
        "name": name,
        "textures": doc["textures"],
        "nodes": len(prefab["nodes"]),
        "canvas": prefab["canvas"],
        "files": webmodel.read_files(doc, prefab, SHADER_INDEX),
    }
    return root, summary


MASKED_FILES = [
    "m.moc3",
    "m.prefab.json",
    "model.json",
    "shaders/S_Lit.json",
    "shaders/S_Lit/gles3/s0p0_vertex_0.glsl",
    "shaders/S_Lit/gles3/s0p0_vertex_1.glsl",
    "shaders/S_Lit/gles3/s0p0_vertex_5.glsl",
    "shaders/S_Lit/gles3/s0p0_vertex_6.glsl",
    "shaders/S_Mask.json",
    "shaders/S_Mask/gles3/s0p0_vertex_0.glsl",
    "shaders/shaders.json",
    "textures/p0-00.png",
]


def test_read_rule(tmp_path):
    _, summary = fake_model(tmp_path / "a", masked=True)
    assert summary["files"] == MASKED_FILES
    _, summary = fake_model(tmp_path / "b", masked=False)
    assert summary["files"] == [
        "m.moc3",
        "m.prefab.json",
        "model.json",
        "shaders/S_Lit.json",
        "shaders/S_Lit/gles3/s0p0_vertex_0.glsl",
        "shaders/S_Lit/gles3/s0p0_vertex_5.glsl",
        "shaders/shaders.json",
        "textures/p0-00.png",
    ]
    # keywords the shader's programs do not use are ignored
    lit = {"shader": {"shader": "S/Lit"}, "keywords": ["CUBISM_INVERT_ON"]}
    assert webmodel.shader_files(SHADER_INDEX, [lit]) == ["S_Lit.json", "S_Lit/gles3/s0p0_vertex_0.glsl"]
    with pytest.raises(RuntimeError, match="0 GLES3 programs"):
        webmodel.shader_files(
            SHADER_INDEX, [{"shader": {"shader": "S/Lit"}, "keywords": ["CUBISM_MASK_ON", "_ADDITIONAL_LIGHTS"]}]
        )
    with pytest.raises(RuntimeError, match="not in the shader index"):
        webmodel.shader_files(SHADER_INDEX, [{"shader": {"shader": "S/Other"}, "keywords": []}])


def test_story_keywords_select_the_story_programs_too():
    lit = {"shader": {"shader": "S/Lit"}, "keywords": ["CUBISM_MASK_ON"]}
    mask = MASKS["cubismMask"]
    assert webmodel.STORY_KEYWORDS == (ALV,)
    assert webmodel.shader_files(SHADER_INDEX, [lit], webmodel.STORY_KEYWORDS) == [
        "S_Lit.json",
        "S_Lit/gles3/s0p0_vertex_1.glsl",
        "S_Lit/gles3/s0p0_vertex_6.glsl",
    ]
    # a shader whose programs do not use the keyword: its one program
    assert webmodel.shader_files(SHADER_INDEX, [mask], webmodel.STORY_KEYWORDS) == [
        "S_Mask.json",
        "S_Mask/gles3/s0p0_vertex_0.glsl",
    ]
    # the keyword without a program for a material's set
    index = [{**SHADER_INDEX[0], "variants": [v for v in SHADER_INDEX[0]["variants"] if v["keywords"] != [ALV]]}]
    with pytest.raises(RuntimeError, match=r"0 GLES3 programs with the keywords \['_ADDITIONAL_LIGHTS_VERTEX'\]"):
        webmodel.shader_files(index, [{"shader": {"shader": "S/Lit"}, "keywords": []}], webmodel.STORY_KEYWORDS)


def test_ingest_writes_a_manifest_of_the_read_files(tmp_path):
    site = tmp_path / "site"
    (site / sitefiles.MODELS_DIR).mkdir(parents=True)
    mdir, summary = fake_model(tmp_path / "m")
    r = webmodel.ingest(sitefiles.Store(site), site, "adv_model_a", KEY_A, mdir, summary)
    man = json.loads((site / "models" / "adv_model_a.json").read_text(encoding="utf-8"))
    assert r["ok"] and r["files"] == len(man["files"]) == 12 and sorted(man["files"]) == summary["files"]
    assert man["format"] == sitefiles.MANIFEST_FORMAT == 3 and man["id"] == "adv_model_a" and man["key"] == KEY_A
    assert man["model"] == {"group": "001_adv", "canvas": {"width": 1.0}, "textures": 1, "nodes": 3}
    index = json.loads(sitefiles.entry_text(site, man["files"]["shaders/shaders.json"]))
    assert [(r["name"], [v["file"] for v in r["variants"]]) for r in index] == [
        (
            "S/Lit",
            [
                "S_Lit/gles3/s0p0_vertex_0.glsl",
                "S_Lit/gles3/s0p0_vertex_1.glsl",
                "S_Lit/gles3/s0p0_vertex_5.glsl",
                "S_Lit/gles3/s0p0_vertex_6.glsl",
            ],
        ),
        ("S/Mask", ["S_Mask/gles3/s0p0_vertex_0.glsl"]),
    ]
    prefab = sitefiles.entry_text(site, man["files"]["m.prefab.json"])
    assert json.loads(prefab)["canvas"] == {"width": 1.0} and b" " not in prefab
    assert sitefiles.entry_text(site, man["files"]["m.moc3"]) == b"MOC3 bytes"
    doc = json.loads(sitefiles.entry_text(site, man["files"]["model.json"]))
    assert doc["format"] == webmodel.MODEL_FORMAT == 2 and doc["motionSync"] is False


def test_write_index_keeps_model_assets_and_writes_models_json(tmp_path):
    site = tmp_path / "site"
    (site / "charts").mkdir(parents=True)
    (site / sitefiles.MODELS_DIR).mkdir()
    store = sitefiles.Store(site)
    shared = store.put("livescene/shaders/x.glsl", b"#ifdef VERTEX\n#endif\n")
    chart = {"musicId": 1, "difficulty": "easy", "audio": False, "files": {"x.glsl": shared}, "chart": {}}
    (site / "charts" / "1_easy.json").write_text(json.dumps(chart), encoding="utf-8")
    webmodel.ingest(store, site, "adv_model_b", KEY_B, *fake_model(tmp_path / "b", "b", tex=b"tex b", masked=False))
    webmodel.ingest(store, site, "adv_model_a", KEY_A, *fake_model(tmp_path / "a", "a"))
    stale = store.put("old.json", b"[0]")
    r = siteindex.write_index(site)
    assert (r["charts"], r["models"], r["removedAssets"]) == (1, 2, 1)
    assert not (site / stale["asset"]).exists()
    for mf in (site / "models").glob("*.json"):
        for e in json.loads(mf.read_text(encoding="utf-8"))["files"].values():
            assert all((site / a).is_file() for a in sitefiles.entry_assets(e))
    index = json.loads((site / "models.json").read_text(encoding="utf-8"))
    assert index["format"] == sitefiles.SITE_FORMAT
    assert [m["id"] for m in index["models"]] == ["adv_model_a", "adv_model_b"]
    a = index["models"][0]
    assert a["manifest"] == "models/adv_model_a.json" and a["key"] == KEY_A and a["group"] == "001_adv"
    assert a["files"] == 12 and a["bytes"] > 0 and a["textures"] == 1
    # the two models share their shader files: stored once
    shared_glsl = {
        json.loads((site / "models" / f"{i}.json").read_text(encoding="utf-8"))["files"][
            "shaders/S_Lit/gles3/s0p0_vertex_0.glsl"
        ]["asset"]
        for i in ("adv_model_a", "adv_model_b")
    }
    assert len(shared_glsl) == 1
    # removing a model's manifest frees its own assets on the next index
    (site / "models" / "adv_model_b.json").unlink()
    r = siteindex.write_index(site)
    assert r["models"] == 1 and r["removedAssets"] >= 1


def test_no_models_directory_no_models_json(tmp_path):
    (tmp_path / "models.json").write_bytes(b"{}")
    r = siteindex.write_index(tmp_path)
    assert (r["charts"], r["models"]) == (0, 0)
    assert not (tmp_path / "models.json").exists()
    (tmp_path / "models").mkdir()
    siteindex.write_index(tmp_path)
    assert json.loads((tmp_path / "models.json").read_text(encoding="utf-8")) == {
        "format": sitefiles.SITE_FORMAT,
        "models": [],
    }


def test_reingest_json_covers_models(tmp_path):
    site = tmp_path / "site"
    (site / sitefiles.MODELS_DIR).mkdir(parents=True)
    store = sitefiles.Store(site)
    loose = store.put("model.json", b'{ "a" : 1 }')
    doc = {"id": "m", "key": KEY_A, "model": {}, "files": {"model.json": loose}}
    (site / "models" / "m.json").write_text(json.dumps(doc), encoding="utf-8")
    assert sitefiles.reingest_json(site) == {"changedEntries": 1}
    man = json.loads((site / "models" / "m.json").read_text(encoding="utf-8"))
    assert sitefiles.entry_text(site, man["files"]["model.json"]) == b'{"a":1}'


LIVE2D_BUNDLE = "/* live2d bundle */\n"


def fake_player(root, live2d=True, live2d_bundle=True):
    files = {
        sitefiles.READ_SET_SCRIPT: "// read set\n",
        sitefiles.PLAYER_BUNDLES[0]: "/* bundle */\n",
        f"{sitefiles.PLAYER_PAGE_DIR}/index.html": '<script type="module" src="./chart-list.js"></script>\n',
        f"{sitefiles.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n',
    }
    if live2d:
        files[f"{sitefiles.LIVE2D_PAGE_DIR}/index.html"] = '<script type="module" src="./live2d.js"></script>\n'
        files[f"{sitefiles.LIVE2D_PAGE_DIR}/live2d.js"] = "import '../../src/live2d/define.js'; // @PLAYER_VERSION@\n"
    if live2d_bundle:
        files[sitefiles.LIVE2D_BUNDLES[0]] = LIVE2D_BUNDLE
        files[sitefiles.LIVE2D_BUNDLES[0] + ".map"] = "{}"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def test_write_player_copies_the_live2d_page(tmp_path):
    site = tmp_path / "site"
    r = sitefiles.write_player(site, fake_player(tmp_path / "p"))
    assert (r["pageFiles"], r["live2dPageFiles"]) == (2, 2)
    version = hashlib.sha256(LIVE2D_BUNDLE.encode()).hexdigest()[:16]
    page = site / sitefiles.LIVE2D_PAGE_SITE_DIR
    assert (page / "live2d.js").read_text(
        encoding="utf-8"
    ) == f"import './ournotes-player.live2d.element.min.js'; // {version}\n"
    assert (page / "ournotes-player.live2d.element.min.js").read_text(encoding="utf-8") == LIVE2D_BUNDLE
    assert (page / "ournotes-player.live2d.element.min.js.map").is_file()
    assert (site / "chart-list.js").read_text(encoding="utf-8") == 'import "./ournotes-player.element.min.js";\n'
    r = sitefiles.write_player(tmp_path / "site2", fake_player(tmp_path / "p2", live2d=False, live2d_bundle=False))
    assert r["live2dPageFiles"] == 0 and not (tmp_path / "site2" / sitefiles.LIVE2D_PAGE_SITE_DIR).exists()
    with pytest.raises(ConfigError, match="has the Live2D page but not"):
        sitefiles.write_player(tmp_path / "site3", fake_player(tmp_path / "p3", live2d_bundle=False))
    player = fake_player(tmp_path / "p4")
    (player / sitefiles.LIVE2D_PAGE_DIR / "extra.js").write_bytes(b'import "../../src/element.js";\n')
    with pytest.raises(RuntimeError, match="beyond LIVE2D_PAGE_IMPORTS"):
        sitefiles.write_player(tmp_path / "site4", player)


def test_player_pages_never_overwrite_site_data(tmp_path):
    player = fake_player(tmp_path / "p")
    (player / sitefiles.PLAYER_PAGE_DIR / "models.json").write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="would overwrite the site data"):
        sitefiles.write_player(tmp_path / "site", player)


def put_manifest(site, mid, key, model=None, doc=None):
    """A model manifest with a model.json only (`doc`, default a current one: MODEL_FORMAT, motionSync)."""
    doc = {"format": webmodel.MODEL_FORMAT, "key": key, "motionSync": False} if doc is None else doc
    entry = sitefiles.Store(site).put("model.json", json.dumps(doc).encode())
    (site / "models").mkdir(parents=True, exist_ok=True)
    (site / "models" / f"{mid}.json").write_text(
        json.dumps({"key": key, "model": model or {}, "files": {"model.json": entry}}), encoding="utf-8"
    )


def test_build_skips_existing_models_unless_forced(tmp_path, monkeypatch):
    site = tmp_path / "site"
    put_manifest(site, "adv_model_a", KEY_A)
    done = []

    def fake_task(mid, key, job, data=None):
        done.append(mid)
        put_manifest(site, mid, key)
        return {"id": mid, "ok": True, "files": 0, "bytes": 0}

    monkeypatch.setattr(webmodel, "model_task", fake_task)
    monkeypatch.setattr(webmodel, "_open", lambda cfg: (None, None))
    models = {"adv_model_a": KEY_A, "adv_model_b": KEY_B}
    r = webmodel.build(site, models, Config(), fake_player(tmp_path / "p"), workers=1)
    assert done == ["adv_model_b"] and r["modelsSkipped"] == ["adv_model_a"] and r["models"] == 2
    assert [m["id"] for m in r["modelsBuilt"]] == ["adv_model_b"] and r["live2dPageFiles"] == 2
    assert r["modelNames"] is None  # no master data: no names
    r = webmodel.build(site, models, Config(), fake_player(tmp_path / "p"), force=True, workers=1)
    assert done == ["adv_model_b", "adv_model_a", "adv_model_b"] and r["modelsSkipped"] == []
    assert r["modelsRebuilt"] == []  # force: every model, none of them for a reason
    with pytest.raises(ValueError, match="has the id"):
        webmodel.build(site, {"other": KEY_A}, Config(), fake_player(tmp_path / "p"), workers=1)


def test_build_rebuilds_outdated_manifests(tmp_path, monkeypatch):
    """An existing manifest whose model.json is older than MODEL_FORMAT, has no motionSync or cannot be read is built
    again without force; a current one is skipped."""
    site = tmp_path / "site"
    keys = {f"m{i}": f"Character/Live2D/g/m{i}/model/m{i}" for i in range(5)}
    put_manifest(site, "m0", keys["m0"])  # current
    put_manifest(site, "m1", keys["m1"], doc={"format": 1, "key": keys["m1"]})  # format 1
    put_manifest(site, "m2", keys["m2"], doc={"format": webmodel.MODEL_FORMAT, "key": keys["m2"]})  # no motionSync
    (site / "models" / "m3.json").write_text(
        json.dumps({"key": keys["m3"], "model": {}, "files": {}}), encoding="utf-8"
    )  # no model.json
    done = []

    def fake_task(mid, key, job, data=None):
        done.append(mid)
        put_manifest(site, mid, key)
        return {"id": mid, "ok": True, "files": 1, "bytes": 1}

    monkeypatch.setattr(webmodel, "model_task", fake_task)
    monkeypatch.setattr(webmodel, "_open", lambda cfg: (None, None))
    assert webmodel.outdated(site, "m0") is None and webmodel.outdated(site, "m1") == "outdated"
    r = webmodel.build(site, keys, Config(), fake_player(tmp_path / "p"), workers=1)
    assert done == ["m1", "m2", "m3", "m4"] and r["modelsSkipped"] == ["m0"]
    assert r["modelsRebuilt"] == [
        {"id": "m1", "reason": "outdated"},
        {"id": "m2", "reason": "outdated"},
        {"id": "m3", "reason": "unreadable"},
    ]
    assert [m["id"] for m in r["modelsBuilt"]] == ["m1", "m2", "m3", "m4"]
    r = webmodel.build(site, keys, Config(), fake_player(tmp_path / "p"), workers=1)  # all current now
    assert r["modelsSkipped"] == list(keys) and r["modelsRebuilt"] == [] and len(done) == 4


# ---------------------------------------------------------------- command line
def run(argv, capsys):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def test_web_argument_shape():
    p = cli.build_parser()
    a = p.parse_args(["--json", "site", "build", "-o", "s", "--pair", "1:easy", "--live2d", "m1", "--live2d", KEY_B])
    assert a.pair == [(1, "easy")] and a.live2d == ["m1", KEY_B] and not a.all_live2d
    a = p.parse_args(["--json", "site", "build", "-o", "s", "--all", "--all-live2d", "--name", "example"])
    assert a.all and a.all_live2d and a.live2d is None and a.name == "example"
    for bad in (["--live2d", "m", "--all-live2d"], ["--pair", "1:easy", "--all"], ["--all", "--player-only"]):
        with pytest.raises(SystemExit):
            p.parse_args(["--json", "site", "build", "-o", "s", *bad])


@pytest.mark.parametrize(
    "argv, message",
    [
        ([], "--all-live2d"),
        (["--player-only", "--live2d", "m"], "cannot be combined"),
    ],
)
def test_web_selection_errors(tmp_path, capsys, argv, message):
    code, _, err = run(["--json", "site", "build", "-o", str(tmp_path / "s"), *argv], capsys)
    assert code == 2 and message in err


class FakeCatalog:
    def keys(self, prefix=""):
        return [k for k in (KEY_A, KEY_B, "Live/Score/1") if k.startswith(prefix)]


# ---------------------------------------------------------------- the runtime export
def test_webmodel_uses_the_public_runtime_export(tmp_path, monkeypatch):
    tree = ast.parse(inspect.getsource(webmodel))
    used = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "live2d"
    }
    assert "export_runtime" in used and not [a for a in used if a.startswith("_")]
    assert not hasattr(live2d, "_extract_runtime")
    rt = live2d.RuntimeExport({"name": "m", "moc3": "m.moc3"}, exporter=None, env=None, graph=None, classes={})
    monkeypatch.setattr(live2d, "export_runtime", lambda cat, key, out_dir: rt)
    assert live2d.extract_runtime(None, KEY_A, tmp_path) == {"name": "m", "moc3": "m.moc3"}


def test_runtime_export_needs_the_apk(tmp_path):
    with pytest.raises(ConfigError, match="paths.apk|NNNOTES_PATHS_APK"):
        live2d.export_runtime(SimpleNamespace(apk=None), KEY_A, tmp_path)


# ---------------------------------------------------------------- names from the master data
def name_row(tid, ja, en, zh=None):
    return {
        "_id": tid,
        "_japanese": ja,
        "_english": en,
        "_traditionalChinese": zh or ja,
        "_simplifiedChinese": zh or ja,
        "_korean": "",
    }


def write_master(md, costumes, characters=None, texts=None):
    md.mkdir(parents=True, exist_ok=True)
    tables = {
        "MasterCharacterCostume": costumes,
        "MasterCharacter": characters
        if characters is not None
        else [
            {"_id": 1, "_nameTextID": "Name_A"},
            {"_id": 2, "_nameTextID": "Name_B"},
            {"_id": 3, "_nameTextID": "Gone"},
        ],
        "MasterText": texts
        if texts is not None
        else [name_row("Name_A", "エー", "Ay", "艾"), name_row("Name_B", "ビー", "Bee")],
    }
    for name, rows in tables.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}, ensure_ascii=False), encoding="utf-8")
    return md


def costume(cid, path, char):
    return {"_id": cid, "_characterID": char, "_costumeID": cid % 1000, "_isDefault": False, "_live2dPath": path}


PATH_A, PATH_B = KEY_A.removeprefix(webmodel.LIVE2D_PREFIX), KEY_B.removeprefix(webmodel.LIVE2D_PREFIX)


def test_model_names_from_the_costume_rows(tmp_path):
    md = write_master(
        tmp_path / "m",
        [
            costume(1001, PATH_A, 1),
            costume(1002, PATH_A, 1),  # two costumes, one model
            costume(2001, PATH_B, 2),
            costume(2002, "002_adv/shared/model/shared", 2),
            costume(3001, "002_adv/shared/model/shared", 1),
            costume(3002, "003_adv/c/model/c", 3),  # no name text
            {"_id": 9, "_characterID": 1, "_live2dPath": ""},
        ],
    )
    names = webmodel.model_names(md, "zh-Hant")
    assert names == {
        KEY_A: {"character": 1, "names": {"ja": "エー", "en": "Ay", "zh-Hant": "艾", "zh-Hans": "艾"}, "label": "艾"},
        KEY_B: {
            "character": 2,
            "names": {"ja": "ビー", "en": "Bee", "zh-Hant": "ビー", "zh-Hans": "ビー"},
            "label": "ビー",
        },
    }
    assert "label" not in webmodel.model_names(md)[KEY_A]
    assert "label" not in webmodel.model_names(md, "ko")[KEY_A]  # no Korean text
    assert webmodel.model_facts(KEY_A, {"canvas": {}, "textures": [], "nodes": 0}, names[KEY_A]) == {
        "group": "001_adv",
        "canvas": {},
        "textures": 0,
        "nodes": 0,
        **names[KEY_A],
    }


def test_master_names_follow_the_settings(tmp_path):
    md = write_master(tmp_path / "m", [costume(1001, PATH_A, 1)])
    assert webmodel.master_names(Config()) is None
    cfg = Config(overrides={("paths", "master"): str(md), ("catalog", "language"): "en"})
    assert webmodel.master_names(cfg) == {
        KEY_A: {"character": 1, "names": {"ja": "エー", "en": "Ay", "zh-Hant": "艾", "zh-Hans": "艾"}, "label": "Ay"}
    }
    (md / "MasterCharacterCostume.json").unlink()
    logged = []
    assert webmodel.master_names(cfg, log=logged.append) is None
    assert logged == ["model names left out: MasterCharacterCostume.json not in the master data"]
    with pytest.raises(ConfigError, match="paths.master"):
        webmodel.master_names(Config(overrides={("paths", "master"): str(tmp_path / "absent")}))


def test_build_adds_names_and_refreshes_skipped_manifests(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "models").mkdir(parents=True)
    put_manifest(site, "adv_model_a", KEY_A, model={"group": "001_adv", "character": 9, "label": "old"})
    jobs = []

    def fake_task(mid, key, job, data=None):
        jobs.append((mid, job["names"].get(key)))
        put_manifest(site, mid, key)
        return {"id": mid, "ok": True, "files": 0, "bytes": 0}

    monkeypatch.setattr(webmodel, "model_task", fake_task)
    monkeypatch.setattr(webmodel, "_open", lambda cfg: (None, None))
    md = write_master(tmp_path / "m", [costume(1001, PATH_A, 1), costume(2001, PATH_B, 2)])
    cfg = Config(overrides={("paths", "master"): str(md), ("catalog", "language"): "ja"})
    r = webmodel.build(site, {"adv_model_a": KEY_A, "adv_model_b": KEY_B}, cfg, fake_player(tmp_path / "p"), workers=1)
    assert r["modelNames"] == 2 and r["modelsSkipped"] == ["adv_model_a"]
    assert jobs == [
        (
            "adv_model_b",
            {
                "character": 2,
                "names": {"ja": "ビー", "en": "Bee", "zh-Hant": "ビー", "zh-Hans": "ビー"},
                "label": "ビー",
            },
        )
    ]
    man = json.loads((site / "models" / "adv_model_a.json").read_text(encoding="utf-8"))
    assert man["model"] == {
        "group": "001_adv",
        "character": 1,
        "names": {"ja": "エー", "en": "Ay", "zh-Hant": "艾", "zh-Hans": "艾"},
        "label": "エー",
    }
    index = json.loads((site / "models.json").read_text(encoding="utf-8"))
    assert [(m["id"], m.get("label")) for m in index["models"]] == [("adv_model_a", "エー"), ("adv_model_b", None)]
    # the same names again: the skipped manifest is not rewritten
    before = (site / "models" / "adv_model_a.json").read_bytes()
    assert webmodel.refresh_names(site, "adv_model_a", webmodel.model_names(md, "ja")[KEY_A]) is False
    assert (site / "models" / "adv_model_a.json").read_bytes() == before
    # a key the master data no longer maps loses the fields
    assert webmodel.refresh_names(site, "adv_model_a", None) is True
    man = json.loads((site / "models" / "adv_model_a.json").read_text(encoding="utf-8"))
    assert man["model"] == {"group": "001_adv"}


# ---------------------------------------------------------------- model.json and the model sources of story.build
class FakeShader:
    def __init__(self, name):
        self.name = name
        self.assets_file = SimpleNamespace(name=f"CAB-{name}")


def fake_export(monkeypatch, motion_sync=False):
    """export_model's inputs made up: the runtime export writes fake_model's files, the player's mask materials are
    MASKS and the shader dump indexes SHADER_INDEX (fake_model wrote its files); -> the player data."""

    def export_runtime(cat, key, out_dir):
        name = webmodel.model_id(key)
        fake_model(out_dir, name, key=key, motion_sync=motion_sync)
        prefab = json.loads((out_dir / f"{name}.prefab.json").read_text(encoding="utf-8"))
        summary = {
            "name": name,
            "moc3": f"{name}.moc3",
            "prefab": f"{name}.prefab.json",
            "moc3Bytes": 10,
            "textures": ["textures/p0-00.png", "textures/p1-11.png"],
            "nodes": len(prefab["nodes"]),
        }
        return live2d.RuntimeExport(summary, SimpleNamespace(shaders={"S/Lit": FakeShader("S/Lit")}), None, None, {})

    class FakeExporter:
        def __init__(self, cat, out_dir, player=None):
            self.shaders = {"S/Mask": FakeShader("S/Mask")}

        def material(self, path):
            return MASKS[{v: k for k, v in webmodel.RESOURCES.items()}[path]]

    def dump_objects(objs, sdir, name, index):
        index.extend(r for r in SHADER_INDEX if r["name"] == objs[0].name)

    def write_index(index, sdir):
        jsonio.write_json(sdir / "shaders.json", index)

    monkeypatch.setattr(live2d, "export_runtime", export_runtime)
    monkeypatch.setattr(webmodel, "Exporter", FakeExporter)
    monkeypatch.setattr(webmodel.shader_mod, "dump_objects", dump_objects)
    monkeypatch.setattr(webmodel.shader_mod, "write_index", write_index)
    return SimpleNamespace(resource=lambda path: path)


def model_files(name):
    return [f.replace("m.", f"{name}.") for f in MASKED_FILES]


@pytest.mark.parametrize("motion_sync", [False, True])
def test_export_model_writes_model_json(tmp_path, monkeypatch, motion_sync):
    player = fake_export(monkeypatch, motion_sync)
    r = webmodel.export_model(None, player, KEY_A, tmp_path / "x")
    doc = json.loads((tmp_path / "x" / webmodel.MODEL_INDEX).read_text(encoding="utf-8"))
    assert doc == {
        "format": 2,
        "name": "adv_model_a",
        "key": KEY_A,
        "moc3": "adv_model_a.moc3",
        "prefab": "adv_model_a.prefab.json",
        "textures": ["textures/p0-00.png"],
        "canvas": {"width": 1.0},
        "shaders": "shaders/shaders.json",
        "resources": MASKS,
        "motionSync": motion_sync,
    }
    assert r["files"] == model_files("adv_model_a")


def site_with(tmp_path, *models):
    """A site with the model manifests of fake_model for each (key, motion_sync)."""
    site = tmp_path / "site"
    (site / sitefiles.MODELS_DIR).mkdir(parents=True, exist_ok=True)
    for key, ms in models:
        mid = webmodel.model_id(key)
        webmodel.ingest(
            sitefiles.Store(site), site, mid, key, *fake_model(tmp_path / mid, mid, key=key, motion_sync=ms)
        )
    return site


def test_site_models_read_model_json(tmp_path):
    site = site_with(tmp_path, (KEY_A, True), (KEY_B, False))
    src = webmodel.SiteModels(site)
    assert src.ensure(KEY_A) == {"id": "adv_model_a", "motionSync": True}
    assert src.ensure(KEY_B) == {"id": "adv_model_b", "motionSync": False}
    assert src.story_fields(tmp_path / "story") == {}
    with pytest.raises(RuntimeError, match="no model manifest models/c.json in the site"):
        src.ensure("Character/Live2D/g/c/model/c")
    # a model.json without motionSync (a model built before it had one)
    man = json.loads((site / "models" / "adv_model_b.json").read_text(encoding="utf-8"))
    doc = {
        k: v for k, v in json.loads(sitefiles.entry_text(site, man["files"]["model.json"])).items() if k != "motionSync"
    }
    man["files"]["model.json"] = sitefiles.Store(site).put("model.json", json.dumps({**doc, "format": 1}).encode())
    (site / "models" / "adv_model_b.json").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(RuntimeError, match="format 1 has no motionSync; export the model again"):
        src.ensure(KEY_B)
    # the manifest of another key with the same id
    with pytest.raises(RuntimeError, match="not of Character/Live2D/other/adv_model_a"):
        src.ensure("Character/Live2D/other/adv_model_a/model/adv_model_a")
