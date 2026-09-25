import hashlib
import json

import pytest

from nnnotes import cli, web, webmodel
from nnnotes.config import ConfigError

KEY_A = "Character/Live2D/001_adv/adv_model_a/model/adv_model_a"
KEY_B = "Character/Live2D/sub_x/adv_model_b/model/adv_model_b"


def test_model_id_is_the_model_name():
    assert webmodel.model_id(KEY_A) == "adv_model_a"
    for bad in ("Character/Live2D/001_adv/a/model/b", "Character/Live2D/a/model/a", "Other/001/a/model/a",
                "Character/Live2D/001_adv/a/model/a/extra"):
        with pytest.raises(ValueError, match="not a Live2D model key"):
            webmodel.model_id(bad)
    with pytest.raises(ValueError, match="not URL-safe"):
        webmodel.model_id("Character/Live2D/g/a b/model/a b")


def test_model_keys_and_select():
    keys = [KEY_B, "Character/Live2D/001_adv/adv_model_a/motion/x", KEY_A, "Live/Score/1"]
    models = webmodel.model_keys(keys)
    assert models == {"adv_model_a": KEY_A, "adv_model_b": KEY_B}
    assert list(models) == ["adv_model_a", "adv_model_b"]
    assert webmodel.select(None, models) == models
    assert webmodel.select([KEY_B, "adv_model_a"], models) == models
    assert webmodel.select(["adv_model_b"], models) == {"adv_model_b": KEY_B}
    with pytest.raises(ValueError, match="nope"):
        webmodel.select(["adv_model_a", "nope"], models)
    with pytest.raises(ValueError, match="model id adv_model_a"):
        webmodel.model_keys([KEY_A, "Character/Live2D/002_adv/adv_model_a/model/adv_model_a"])


def test_shader_names():
    doc = {"nodes": [{"materials": [{"material": "Lit", "shader": {"shader": "S/Lit"}},
                                    {"material": "Mask", "shader": {"shader": "S/Mask"}}]}],
           "other": {"shader": "not a reference", "x": 1}}
    assert webmodel.shader_names(doc) == {"S/Lit", "S/Mask"}


def drawable(tex, keywords, shader="S/Lit"):
    return {"path": "m/Drawables/d", "components": [
        {"type": "MeshRenderer",
         "m_Materials": [{"material": "Lit", "shader": {"shader": shader}, "keywords": keywords}]},
        {"type": "MonoBehaviour", "class": "CubismRenderer", "_mainTexture": {"texture": tex, "name": "page"}}]}


def variant(shader, n, keywords, platform="gles3", kind="GLES3", ext="glsl"):
    return {"file": f"{shader}/{platform}/s0p0_vertex_{n}.{ext}", "platform": platform, "subShader": 0, "pass": 0,
            "stage": "vertex", "type": kind, "keywords": keywords}


SHADER_INDEX = [
    {"name": "S/Lit", "source": "x", "parsed": "S_Lit.json", "variants": [
        variant("S_Lit", 0, []), variant("S_Lit", 1, ["CUBISM_MASK_ON"]), variant("S_Lit", 2, ["_ADDITIONAL_LIGHTS"]),
        variant("S_Lit", 3, [], kind="GLES31"),
        variant("S_Lit", 4, [], platform="vulkan", kind="SPIRV", ext="vkprog")]},
    {"name": "S/Mask", "source": "y", "parsed": "S_Mask.json", "variants": [variant("S_Mask", 0, [])]},
]
MASKS = {"cubismMask": {"material": "Mask", "shader": {"shader": "S/Mask"}, "keywords": [], "floats": {"_Cull": 0.0}},
         "cubismMaskCulling": {"material": "MaskCulling", "shader": {"shader": "S/Mask"}, "keywords": [],
                               "floats": {"_Cull": 1.0}}}


def fake_model(root, name="m", tex=b"\x89PNG texture a", moc=b"MOC3 bytes", masked=True):
    """A model directory in the export layout (made-up contents) and its export summary. Two atlas pages, the
    drawables use page 0; drawables masked or not."""
    kws = [[], ["CUBISM_MASK_ON"]] if masked else [[], []]
    prefab = {"key": name, "nodes": [drawable("textures/p0-00.png", k) for k in kws], "canvas": {"width": 1.0}}
    doc = {"format": webmodel.MODEL_FORMAT, "name": name, "key": KEY_A, "moc3": f"{name}.moc3",
           "prefab": f"{name}.prefab.json", "textures": webmodel.drawable_textures(prefab), "canvas": prefab["canvas"],
           "shaders": "shaders/shaders.json", "resources": MASKS}
    files = {f"{name}.moc3": moc, f"{name}.prefab.json": json.dumps(prefab, indent=1).encode(),
             "textures/p0-00.png": tex, "textures/p1-11.png": b"unused page",
             "shaders/shaders.json": json.dumps(SHADER_INDEX).encode(), webmodel.MODEL_INDEX: json.dumps(doc).encode(),
             "shaders/S_Lit.json": b'{"name": "S/Lit"}', "shaders/S_Mask.json": b'{"name": "S/Mask"}'}
    for rec in SHADER_INDEX:
        for v in rec["variants"]:
            files[f"shaders/{v['file']}"] = f"#ifdef VERTEX\n// {v['file']}\n#endif\n".encode()
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    summary = {"name": name, "textures": doc["textures"], "nodes": len(prefab["nodes"]), "canvas": prefab["canvas"],
               "files": webmodel.read_files(doc, prefab, SHADER_INDEX)}
    return root, summary


def test_read_rule(tmp_path):
    _, summary = fake_model(tmp_path / "a", masked=True)
    assert summary["files"] == ["m.moc3", "m.prefab.json", "model.json", "shaders/S_Lit.json",
                                "shaders/S_Lit/gles3/s0p0_vertex_0.glsl", "shaders/S_Lit/gles3/s0p0_vertex_1.glsl",
                                "shaders/S_Mask.json", "shaders/S_Mask/gles3/s0p0_vertex_0.glsl",
                                "shaders/shaders.json", "textures/p0-00.png"]
    _, summary = fake_model(tmp_path / "b", masked=False)
    assert summary["files"] == ["m.moc3", "m.prefab.json", "model.json", "shaders/S_Lit.json",
                                "shaders/S_Lit/gles3/s0p0_vertex_0.glsl", "shaders/shaders.json", "textures/p0-00.png"]
    # keywords the shader's programs do not use are ignored
    lit = {"shader": {"shader": "S/Lit"}, "keywords": ["CUBISM_INVERT_ON"]}
    assert webmodel.shader_files(SHADER_INDEX, [lit]) == ["S_Lit.json", "S_Lit/gles3/s0p0_vertex_0.glsl"]
    with pytest.raises(RuntimeError, match="0 GLES3 programs"):
        webmodel.shader_files(SHADER_INDEX, [{"shader": {"shader": "S/Lit"},
                                              "keywords": ["CUBISM_MASK_ON", "_ADDITIONAL_LIGHTS"]}])
    with pytest.raises(RuntimeError, match="not in the shader index"):
        webmodel.shader_files(SHADER_INDEX, [{"shader": {"shader": "S/Other"}, "keywords": []}])


def test_ingest_writes_a_manifest_of_the_read_files(tmp_path):
    site = tmp_path / "site"
    (site / web.MODELS_DIR).mkdir(parents=True)
    mdir, summary = fake_model(tmp_path / "m")
    r = webmodel.ingest(web.Store(site), site, "adv_model_a", KEY_A, mdir, summary)
    man = json.loads((site / "models" / "adv_model_a.json").read_text(encoding="utf-8"))
    assert r["ok"] and r["files"] == len(man["files"]) == 10 and sorted(man["files"]) == summary["files"]
    assert man["format"] == web.SITE_FORMAT and man["id"] == "adv_model_a" and man["key"] == KEY_A
    assert man["model"] == {"group": "001_adv", "canvas": {"width": 1.0}, "textures": 1, "nodes": 2}
    index = json.loads(web.entry_text(site, man["files"]["shaders/shaders.json"]))
    assert [(r["name"], [v["file"] for v in r["variants"]]) for r in index] == [
        ("S/Lit", ["S_Lit/gles3/s0p0_vertex_0.glsl", "S_Lit/gles3/s0p0_vertex_1.glsl"]),
        ("S/Mask", ["S_Mask/gles3/s0p0_vertex_0.glsl"])]
    prefab = web.entry_text(site, man["files"]["m.prefab.json"])
    assert json.loads(prefab)["canvas"] == {"width": 1.0} and b" " not in prefab
    assert web.entry_text(site, man["files"]["m.moc3"]) == b"MOC3 bytes"
    assert json.loads(web.entry_text(site, man["files"]["model.json"]))["format"] == 1


def test_write_index_keeps_model_assets_and_writes_models_json(tmp_path):
    site = tmp_path / "site"
    (site / "charts").mkdir(parents=True)
    (site / web.MODELS_DIR).mkdir()
    store = web.Store(site)
    shared = store.put("livescene/shaders/x.glsl", b"#ifdef VERTEX\n#endif\n")
    chart = {"musicId": 1, "difficulty": "easy", "audio": False, "files": {"x.glsl": shared}, "chart": {}}
    (site / "charts" / "1_easy.json").write_text(json.dumps(chart), encoding="utf-8")
    webmodel.ingest(store, site, "adv_model_b", KEY_B, *fake_model(tmp_path / "b", "b", tex=b"tex b", masked=False))
    webmodel.ingest(store, site, "adv_model_a", KEY_A, *fake_model(tmp_path / "a", "a"))
    stale = store.put("old.json", b"[0]")
    r = web.write_index(site)
    assert (r["charts"], r["models"], r["removedAssets"]) == (1, 2, 1)
    assert not (site / stale["asset"]).exists()
    for mf in (site / "models").glob("*.json"):
        for e in json.loads(mf.read_text(encoding="utf-8"))["files"].values():
            assert all((site / a).is_file() for a in web.entry_assets(e))
    index = json.loads((site / "models.json").read_text(encoding="utf-8"))
    assert index["format"] == web.SITE_FORMAT
    assert [m["id"] for m in index["models"]] == ["adv_model_a", "adv_model_b"]
    a = index["models"][0]
    assert a["manifest"] == "models/adv_model_a.json" and a["key"] == KEY_A and a["group"] == "001_adv"
    assert a["files"] == 10 and a["bytes"] > 0 and a["textures"] == 1
    # the two models share their shader files: stored once
    shared_glsl = {json.loads((site / "models" / f"{i}.json").read_text(encoding="utf-8"))["files"]
                   ["shaders/S_Lit/gles3/s0p0_vertex_0.glsl"]["asset"] for i in ("adv_model_a", "adv_model_b")}
    assert len(shared_glsl) == 1
    # removing a model's manifest frees its own assets on the next index
    (site / "models" / "adv_model_b.json").unlink()
    r = web.write_index(site)
    assert r["models"] == 1 and r["removedAssets"] >= 1


def test_no_models_directory_no_models_json(tmp_path):
    (tmp_path / "models.json").write_bytes(b"{}")
    r = web.write_index(tmp_path)
    assert (r["charts"], r["models"]) == (0, 0)
    assert not (tmp_path / "models.json").exists()
    (tmp_path / "models").mkdir()
    web.write_index(tmp_path)
    assert json.loads((tmp_path / "models.json").read_text(encoding="utf-8")) == {"format": web.SITE_FORMAT,
                                                                                   "models": []}


def test_reingest_json_covers_models(tmp_path):
    site = tmp_path / "site"
    (site / web.MODELS_DIR).mkdir(parents=True)
    store = web.Store(site)
    loose = store.put("model.json", b'{ "a" : 1 }')
    doc = {"id": "m", "key": KEY_A, "model": {}, "files": {"model.json": loose}}
    (site / "models" / "m.json").write_text(json.dumps(doc), encoding="utf-8")
    assert web.reingest_json(site) == {"changedEntries": 1}
    man = json.loads((site / "models" / "m.json").read_text(encoding="utf-8"))
    assert web.entry_text(site, man["files"]["model.json"]) == b'{"a":1}'


LIVE2D_BUNDLE = "/* live2d bundle */\n"


def fake_player(root, live2d=True, live2d_bundle=True):
    files = {web.READ_SET_SCRIPT: "// read set\n", web.PLAYER_BUNDLES[0]: "/* bundle */\n",
             f"{web.PLAYER_PAGE_DIR}/index.html": '<script type="module" src="./chart-list.js"></script>\n',
             f"{web.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n'}
    if live2d:
        files[f"{web.LIVE2D_PAGE_DIR}/index.html"] = '<script type="module" src="./live2d.js"></script>\n'
        files[f"{web.LIVE2D_PAGE_DIR}/live2d.js"] = "import '../../src/live2d/define.js'; // @PLAYER_VERSION@\n"
    if live2d_bundle:
        files[web.LIVE2D_BUNDLES[0]] = LIVE2D_BUNDLE
        files[web.LIVE2D_BUNDLES[0] + ".map"] = "{}"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def test_write_player_copies_the_live2d_page(tmp_path):
    site = tmp_path / "site"
    r = web.write_player(site, fake_player(tmp_path / "p"))
    assert (r["pageFiles"], r["live2dPageFiles"]) == (2, 2)
    version = hashlib.sha256(LIVE2D_BUNDLE.encode()).hexdigest()[:16]
    page = site / web.LIVE2D_PAGE_SITE_DIR
    assert (page / "live2d.js").read_text(encoding="utf-8") == \
        f"import './ournotes-player.live2d.element.min.js'; // {version}\n"
    assert (page / "ournotes-player.live2d.element.min.js").read_text(encoding="utf-8") == LIVE2D_BUNDLE
    assert (page / "ournotes-player.live2d.element.min.js.map").is_file()
    assert (site / "chart-list.js").read_text(encoding="utf-8") == 'import "./ournotes-player.element.min.js";\n'
    r = web.write_player(tmp_path / "site2", fake_player(tmp_path / "p2", live2d=False, live2d_bundle=False))
    assert r["live2dPageFiles"] == 0 and not (tmp_path / "site2" / web.LIVE2D_PAGE_SITE_DIR).exists()
    with pytest.raises(ConfigError, match="has the Live2D page but not"):
        web.write_player(tmp_path / "site3", fake_player(tmp_path / "p3", live2d_bundle=False))
    player = fake_player(tmp_path / "p4")
    (player / web.LIVE2D_PAGE_DIR / "extra.js").write_bytes(b'import "../../src/element.js";\n')
    with pytest.raises(RuntimeError, match="beyond LIVE2D_PAGE_IMPORTS"):
        web.write_player(tmp_path / "site4", player)


def test_player_pages_never_overwrite_site_data(tmp_path):
    player = fake_player(tmp_path / "p")
    (player / web.PLAYER_PAGE_DIR / "models.json").write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="would overwrite the site data"):
        web.write_player(tmp_path / "site", player)


def test_build_skips_existing_models_unless_forced(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "models").mkdir(parents=True)
    (site / "models" / "adv_model_a.json").write_text(json.dumps({"key": KEY_A, "model": {}, "files": {}}),
                                                      encoding="utf-8")
    done = []

    def fake_task(mid, key, job, data=None):
        done.append(mid)
        (site / "models" / f"{mid}.json").write_text(json.dumps({"key": key, "model": {}, "files": {}}),
                                                     encoding="utf-8")
        return {"id": mid, "ok": True, "files": 0, "bytes": 0}

    monkeypatch.setattr(webmodel, "model_task", fake_task)
    monkeypatch.setattr(webmodel, "_open", lambda cfg: (None, None))
    models = {"adv_model_a": KEY_A, "adv_model_b": KEY_B}
    r = webmodel.build(site, models, None, fake_player(tmp_path / "p"), workers=1)
    assert done == ["adv_model_b"] and r["modelsSkipped"] == ["adv_model_a"] and r["models"] == 2
    assert [m["id"] for m in r["modelsBuilt"]] == ["adv_model_b"] and r["live2dPageFiles"] == 2
    r = webmodel.build(site, models, None, fake_player(tmp_path / "p"), force=True, workers=1)
    assert done == ["adv_model_b", "adv_model_a", "adv_model_b"] and r["modelsSkipped"] == []
    with pytest.raises(ValueError, match="has the id"):
        webmodel.build(site, {"other": KEY_A}, None, fake_player(tmp_path / "p"), workers=1)


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
    a = p.parse_args(["web", "s", "--pair", "1:easy", "--live2d", "m1", "--live2d", KEY_B])
    assert a.pair == [(1, "easy")] and a.live2d == ["m1", KEY_B] and not a.all_live2d
    a = p.parse_args(["web", "s", "--all", "--all-live2d", "--force"])
    assert a.all and a.all_live2d and a.live2d is None and a.force
    for bad in (["--live2d", "m", "--all-live2d"], ["--pair", "1:easy", "--all"], ["--all", "--player-only"]):
        with pytest.raises(SystemExit):
            p.parse_args(["web", "s", *bad])


@pytest.mark.parametrize("argv, message", [
    ([], "--all-live2d"),
    (["--player-only", "--live2d", "m"], "leave out --live2d"),
    (["--reingest-json", "--all-live2d"], "leave out --live2d"),
])
def test_web_selection_errors(tmp_path, capsys, argv, message):
    code, _, err = run(["web", str(tmp_path / "s"), *argv], capsys)
    assert code == 2 and message in err


class FakeCatalog:
    def keys(self, prefix=""):
        return [k for k in (KEY_A, KEY_B, "Live/Score/1") if k.startswith(prefix)]


@pytest.mark.parametrize("failed, code", [([], 0), ([{"id": "adv_model_b", "stage": "export", "error": "x"}], 1)])
def test_web_builds_models_then_charts(tmp_path, capsys, monkeypatch, failed, code):
    calls = []
    monkeypatch.setattr(cli, "open_catalog", lambda cfg, bundles=True: FakeCatalog())

    def fake_models(out, models, cfg, player, **kw):
        calls.append(("models", models, kw))
        return {"site": str(out), "modelsBuilt": [], "modelsFailed": failed, "modelsSkipped": []}

    def fake_charts(out, pairs, cfg, player, fmt, **kw):
        calls.append(("charts", pairs, kw))
        return {"site": str(out), "built": [], "failed": [], "skipped": []}

    monkeypatch.setattr(webmodel, "build", fake_models)
    monkeypatch.setattr(web, "build", fake_charts)
    (tmp_path / "base.apk").write_bytes(b"")
    code_, out, _ = run(["--apk", str(tmp_path / "base.apk"), "web", str(tmp_path / "s"),
                         "--player", str(fake_player(tmp_path / "p")), "--pair", "7:hard", "--live2d", "adv_model_b",
                         "--live2d", KEY_A, "--force"], capsys)
    assert code_ == code
    assert json.loads(out)["modelsFailed"] == failed
    (m, models, mkw), (c, pairs, ckw) = calls
    assert (m, c) == ("models", "charts")
    assert models == {"adv_model_a": KEY_A, "adv_model_b": KEY_B} and mkw["force"] is True
    assert pairs == [(7, "hard")] and ckw["force"] is True


def test_web_unknown_model_is_a_usage_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "open_catalog", lambda cfg, bundles=True: FakeCatalog())
    (tmp_path / "base.apk").write_bytes(b"")
    code, _, err = run(["--apk", str(tmp_path / "base.apk"), "web", str(tmp_path / "s"),
                        "--player", str(fake_player(tmp_path / "p")), "--live2d", "nope"], capsys)
    assert code == 2 and "not a Live2D model of the catalog: nope" in err
