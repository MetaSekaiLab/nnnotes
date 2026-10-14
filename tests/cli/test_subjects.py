"""The single-subject export commands run their stage through the store, as `export assets` does, and place the
artifacts of the subject's task in the output directory."""

import hashlib
import json
import zipfile

from nnnotes import cli
from nnnotes.cli import assets
from nnnotes.core import jsonio
from nnnotes.formats import shader
from nnnotes.pipeline import contract
from nnnotes.pipeline.stages import Output
from nnnotes.source.catalog import Catalog
from tests.cli.test_assets import FakeExport, bundle_bytes, cdoc, obj
from tests.helpers import synth

M1 = "Character/Live2D/g1/m1/model/m1"
PREFAB = "Assets/AddressableResources/Character/Live2D/g1/m1/model/m1.prefab"
MODEL_BUNDLE = f"model_m1_{'1' * 32}.bundle"
TEX_BUNDLE = f"tex_{'2' * 32}.bundle"
REMOTE = [
    (M1, PREFAB, [1, 2, 3]),
    (MODEL_BUNDLE, synth.remote(MODEL_BUNDLE), []),
    (TEX_BUNDLE, synth.remote(TEX_BUNDLE), []),
    ("monoscripts.bundle", synth.local("monoscripts.bundle"), []),
]
APK = [("monoscripts.bundle", synth.local("monoscripts.bundle"), [])]


def run(argv, capsys):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def settings(tmp_path) -> list[str]:
    """Global options of a catalog whose remote bundles are in the cache and an APK with a local bundle."""
    catalog = tmp_path / "catalog_main_ja.bin"
    catalog.write_bytes(synth.CatalogWriter().build(REMOTE))
    bundles = tmp_path / "cache" / "bundles"
    bundles.mkdir(parents=True)
    for name in (MODEL_BUNDLE, TEX_BUNDLE):
        (bundles / name).write_bytes(b"UnityFS " + name.encode())
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(APK))
        z.writestr("assets/aa/Android/monoscripts.bundle", b"UnityFS scripts")
    return [
        "--catalog",
        str(catalog),
        "--cache",
        str(tmp_path / "cache"),
        "--region",
        "zz",
        "--language",
        "ja",
        "--apk",
        str(apk),
    ]


def fake_extract_model(calls):
    def extract_model(cat, key, out_dir):
        data = b"|".join(p.read_bytes() for p in cat.fetch_key(key))
        calls.append(key)
        (out_dir / "textures").mkdir()
        (out_dir / "m1.moc3").write_bytes(b"MOC3" + data)
        (out_dir / "textures" / "texture_00.png").write_bytes(b"\x89PNG page")
        (out_dir / "m1.model3.json").write_text('{"Version": 3}\n', encoding="utf-8")
        return {"name": "m1", "model3": "m1.model3.json", "textures": ["textures/texture_00.png"]}

    return extract_model


def test_export_live2d_places_the_files_of_the_model_task(tmp_path, capsys, monkeypatch):
    from nnnotes.unity import live2d

    calls = []
    monkeypatch.setattr(live2d, "extract_model", fake_extract_model(calls))
    base = settings(tmp_path)
    out = tmp_path / "out"
    code, stdout, err = run([*base, "--json", "export", "live2d", "m1", "-o", str(out)], capsys)
    assert code == 0, err
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "m1.moc3",
        "m1.model3.json",
        "textures/texture_00.png",
    ]
    # the bundles of the model's closure, in the order the catalog resolves them (as the extractor reads them)
    cat = Catalog((tmp_path / "catalog_main_ja.bin").read_bytes(), tmp_path / "cache", apk=tmp_path / "base.apk")
    data = {MODEL_BUNDLE: b"UnityFS " + MODEL_BUNDLE.encode(), TEX_BUNDLE: b"UnityFS " + TEX_BUNDLE.encode()}
    order = [data.get(b.name, b"UnityFS scripts") for b in cat.resolve(M1)]
    assert len(order) == 3 and (out / "m1.moc3").read_bytes() == b"MOC3" + b"|".join(order)
    assert json.loads(stdout) == {"model3": "m1.model3.json", "name": "m1", "textures": ["textures/texture_00.png"]}
    assert calls == [M1]
    # the second export of the same model is a hit of the store: the extractor does not run again
    code, _, err = run([*base, "export", "live2d", M1, "-o", str(tmp_path / "again")], capsys)
    assert code == 0, err
    assert calls == [M1] and (tmp_path / "again" / "m1.moc3").read_bytes() == (out / "m1.moc3").read_bytes()


ACB = b"@UTF" + b"cue sheet bytes"
RAW = f"cri_assets_cri/sound/s_{'3' * 32}"


def audio_settings(tmp_path) -> list[str]:
    """Global options of a catalog whose key Cri/Sound/s loads a raw ACB in the cache, and an APK with boot data."""
    catalog = tmp_path / "catalog_main_ja.bin"
    entries = [("Cri/Sound/s", "Assets/Cri/Sound/s.acb.bytes", [1]), (RAW, synth.remote(RAW), [])]
    catalog.write_bytes(synth.CatalogWriter().build(entries))
    raw = tmp_path / "cache" / "raw" / "asset" / "Android" / RAW
    raw.parent.mkdir(parents=True)
    raw.write_bytes(ACB)
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(APK))
        z.writestr("assets/bin/Data/data.unity3d", b"UnityFS boot data")
    return [
        "--catalog",
        str(catalog),
        "--cache",
        str(tmp_path / "cache"),
        "--region",
        "zz",
        "--language",
        "ja",
        "--apk",
        str(apk),
    ]


def test_export_audio_decodes_the_cue_sheet_task_in_its_format(tmp_path, capsys):
    from tests.pipeline.test_cristages import fake_tools

    base = audio_settings(tmp_path)
    with fake_tools() as fake:
        code, stdout, err = run([*base, "--json", "export", "audio", "s", "-o", str(tmp_path / "flac")], capsys)
        assert code == 0, err
        assert json.loads(stdout) == {"out": str(tmp_path / "flac"), "cues": 3}
        files = sorted(p.name for p in (tmp_path / "flac").iterdir())
        assert files == ["a.flac", "a_3.flac", "b.flac", "c.flac", "c_5.flac", "cues.json", "streams.json"]
        level = ["-compression_level", "8"]
        assert all(b"|".join(x.encode() for x in level) in (tmp_path / "flac" / f).read_bytes() for f in files[:5])
        calls = len(fake.calls)
        # the same cue sheet again: a hit of the store (no tool runs); another format or level: another task
        code, _, err = run([*base, "export", "audio", "s", "-o", str(tmp_path / "again")], capsys)
        assert code == 0 and len(fake.calls) == calls, err
        code, _, err = run([*base, "export", "audio", "s", "--flac-level", "0", "-o", str(tmp_path / "l0")], capsys)
        assert code == 0, err
        assert b"-compression_level|0" in (tmp_path / "l0" / "a.flac").read_bytes()
        code, _, err = run([*base, "export", "audio", "s", "--format", "wav", "-o", str(tmp_path / "wav")], capsys)
        assert code == 0, err
        assert sorted(p.suffix for p in (tmp_path / "wav").iterdir()) == [".json"] * 2 + [".wav"] * 5


# ---------------------------------------------------------------- shaders: the Shader artifacts of unity.export
class FakeShaders(FakeExport):
    """unity.export of the synthetic bundles: each Shader object as the artifacts the real converter writes (the
    parsed form `json`, one program, the variant `index`)."""

    def run(self, task, store):
        doc = contract.loads(store.input_bytes(task.input("census")))
        assert task.params["classes"] == ["Shader"]
        arts, items = [], []
        for f in doc["files"]:
            for o in f["objects"]:
                if o["class"] != "Shader":
                    continue
                oid = contract.object_id(f["name"], o["pathId"])
                safe, role = shader.safe_name(o["name"]), "program:gles3/s0p0_vertex_0"
                rec = {
                    "file": f"{safe}/gles3/s0p0_vertex_0.glsl",
                    "platform": "gles3",
                    "subShader": 0,
                    "pass": 0,
                    "stage": "vertex",
                    "type": "kShaderGpuProgramGLES3",
                    "keywords": [f["name"]],
                }
                parts = [
                    ("json", json.dumps({"name": o["name"], "file": f["name"]}).encode(), "json"),
                    (role, f"// {o['name']} of {f['name']}".encode(), "glsl"),
                    ("index", contract.encode({"name": o["name"], "variants": [{**rec, "role": role}]}), "json"),
                ]
                ids = []
                for r, data, ext in parts:
                    ids.append(contract.artifact_id(oid, r))
                    content = store.add(data, ext)
                    arts.append(contract.artifact(ids[-1], content, contract.provenance(task), {"kind": "shader"}))
                items.append(contract.item(oid, "exported", artifacts=ids, cls="Shader"))
        return Output(arts, items)


REAL = {s.name: s for s in assets.STAGE_SOURCES}
SHADER_SOURCES = (
    REAL["catalog.index"],
    assets.StageSource("unity.census", "tests.cli.test_assets:FakeCensus"),
    REAL["link.scripts"],
    assets.StageSource("unity.export", "tests.cli.test_subjects:FakeShaders", output=True, root="_bundles/{subject}"),
)
FX = {n: f"fx_{n}_{n * 32}.bundle" for n in ("a", "b")}


def test_export_shader_writes_the_first_shader_of_each_name_from_the_export_results(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(assets, "STAGE_SOURCES", SHADER_SOURCES)
    docs = {
        "a": cdoc("CAB-a", [obj(1, "AssetBundle", "ab"), obj(7, "Shader", "Game/Glow"), obj(2, "Shader", "Game/Rim")]),
        "b": cdoc("CAB-b", [obj(1, "AssetBundle", "ab"), obj(3, "Shader", "Game/Glow"), obj(9, "Mesh", "m")]),
    }
    cache = tmp_path / "cache" / "bundles"
    cache.mkdir(parents=True)
    for n, doc in docs.items():
        (cache / FX[n]).write_bytes(bundle_bytes(doc))
    entries = [("Fx/x", "Assets/Game/Fx/x.mat", [1, 2]), (FX["a"], synth.remote(FX["a"]), [])]
    entries.append((FX["b"], synth.remote(FX["b"]), []))
    catalog = tmp_path / "catalog_main_ja.bin"
    catalog.write_bytes(synth.CatalogWriter().build(entries))
    base = ["--catalog", str(catalog), "--cache", str(tmp_path / "cache"), "--region", "zz", "--language", "ja"]
    out = tmp_path / "out"
    code, stdout, err = run([*base, "--json", "export", "shader", "--key", "Fx/x", "-o", str(out)], capsys)
    assert code == 0, err
    order = [b.name for b in Catalog(catalog.read_bytes(), tmp_path / "cache").resolve("Fx/x")]
    assert order == [FX["b"], FX["a"]]  # the closure as the catalog loads it: b's Glow comes first
    records = json.loads((out / "shaders.json").read_text(encoding="utf-8"))
    assert [(r["name"], r["source"], r["parsed"]) for r in records] == [
        ("Game/Glow", FX["b"], "Game_Glow.json"),
        ("Game/Rim", FX["a"], "Game_Rim.json"),
    ]
    assert records[0]["variants"] == [
        {
            "file": "Game_Glow/gles3/s0p0_vertex_0.glsl",
            "platform": "gles3",
            "subShader": 0,
            "pass": 0,
            "stage": "vertex",
            "type": "kShaderGpuProgramGLES3",
            "keywords": ["CAB-b"],
        }
    ]
    assert json.loads((out / "Game_Glow.json").read_bytes()) == {"name": "Game/Glow", "file": "CAB-b"}
    assert (out / "Game_Rim" / "gles3" / "s0p0_vertex_0.glsl").read_bytes() == b"// Game/Rim of CAB-a"
    assert json.loads(stdout) == {"count": 2, "names": ["Game/Glow", "Game/Rim"], "variants": 2}
    assert (out / "shaders.json").read_text(encoding="utf-8") == jsonio.dumps(records, indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- scenes: room.scene and spot.scene
BG, SITUATION = "Spot/a/Background/b", "Spot/a/Situation/s"
SCENE = {n: f"scene_{n}_{n * 32}.bundle" for n in ("b", "s", "c")}
SCENE_SOURCES = (*SHADER_SOURCES, REAL["room.scene"], REAL["spot.scene"])
SPOT_MASTER = {
    "MasterHomeSpot": [
        {"_id": 1, "_backgroundAssetPath": BG, "_situationAssetPath": SITUATION, "_nameTextId": 5},
        {"_id": 2, "_backgroundAssetPath": "Spot/none", "_situationAssetPath": SITUATION, "_nameTextId": 5},
    ],
    "MasterText": [{"_id": 5, "ja": "Spot A"}],
    "MasterStoryHomeSpotTapTalkEpisode": [],
}


def _digest(paths) -> bytes:
    return hashlib.sha256(b"|".join(p.read_bytes() for p in paths)).hexdigest().encode()


def fake_extract_room(calls):
    def extract_room(cat, key, out_path):
        calls.append(key)
        out_path.write_bytes(b"glTF" + _digest(cat.fetch_key(key)))
        doc = {"backgroundKey": key, "glb": out_path.name, "meshCount": 2, "inactiveMeshes": 1, "cutoff": [0.5, 1e-7]}
        jsonio.write_json(out_path.with_suffix(".json"), doc)
        return doc

    return extract_room


def fake_extract_spot(calls):
    def extract(cat, md, spot_id, out_dir):
        assert cat.apk is not None
        calls.append(spot_id)
        tables = {t: json.loads((md / f"{t}.json").read_bytes())["_allData"] for t in SPOT_MASTER}
        assert tables == SPOT_MASTER
        (sp,) = [r for r in tables["MasterHomeSpot"] if r["_id"] == spot_id]
        (out_dir / "spine").mkdir(parents=True)
        (out_dir / "spine" / "c1.skel").write_bytes(b"SKEL" + _digest(cat.fetch_key(sp["_situationAssetPath"])))
        doc = {
            "spotId": spot_id,
            "name": tables["MasterText"][0]["ja"],
            "characters": [1, 2],
            "skeletons": [{"skeleton": "c1.skel"}],
            "resources": {
                "background": [b.name for b in cat.resolve(sp["_backgroundAssetPath"])],
                "situation": [b.name for b in cat.resolve(sp["_situationAssetPath"])],
            },
        }
        jsonio.write_json(out_dir / "spot.json", doc)
        return doc

    return extract


def scene_settings(tmp_path) -> list[str]:
    """A catalog whose keys BG and SITUATION load synthetic bundles with shaders (a shared one in both closures),
    the master tables of the Spots and an APK."""
    docs = {
        "b": cdoc("CAB-b", [obj(1, "AssetBundle", "ab"), obj(4, "Shader", "Room/Wall")]),
        "s": cdoc(
            "CAB-s", [obj(1, "AssetBundle", "ab"), obj(6, "Shader", "Spine/Skin"), obj(2, "Shader", "Room/Wall")]
        ),
        "c": cdoc("CAB-c", [obj(1, "AssetBundle", "ab"), obj(3, "Shader", "Common/Unlit")]),
    }
    cache = tmp_path / "cache" / "bundles"
    cache.mkdir(parents=True)
    for n, doc in docs.items():
        (cache / SCENE[n]).write_bytes(bundle_bytes(doc))
    entries = [(BG, "Assets/Game/Spot/a/b.prefab", [2, 4]), (SITUATION, "Assets/Game/Spot/a/s.prefab", [3, 4])]
    entries += [(SCENE[n], synth.remote(SCENE[n]), []) for n in ("b", "s", "c")]
    catalog = tmp_path / "catalog_main_ja.bin"
    catalog.write_bytes(synth.CatalogWriter().build(entries))
    md = tmp_path / "master"
    md.mkdir()
    for name, rows in SPOT_MASTER.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(APK))
        z.writestr("assets/aa/Android/monoscripts.bundle", b"UnityFS scripts")
    return [
        "--catalog",
        str(catalog),
        "--cache",
        str(tmp_path / "cache"),
        "--region",
        "zz",
        "--language",
        "ja",
        "--master",
        str(md),
        "--apk",
        str(apk),
    ]


def test_export_spot_places_the_spot_room_and_shader_results(tmp_path, capsys, monkeypatch):
    from nnnotes.content import room, spot

    rooms, spots = [], []
    monkeypatch.setattr(assets, "STAGE_SOURCES", SCENE_SOURCES)
    monkeypatch.setattr(room, "extract_room", fake_extract_room(rooms))
    monkeypatch.setattr(spot, "extract", fake_extract_spot(spots))
    base = scene_settings(tmp_path)
    out = tmp_path / "out"
    code, stdout, err = run([*base, "--json", "export", "spot", "1", "-o", str(out)], capsys)
    assert code == 0, err
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "room.glb",
        "room.json",
        "shaders/Common_Unlit.json",
        "shaders/Common_Unlit/gles3/s0p0_vertex_0.glsl",
        "shaders/Room_Wall.json",
        "shaders/Room_Wall/gles3/s0p0_vertex_0.glsl",
        "shaders/Spine_Skin.json",
        "shaders/Spine_Skin/gles3/s0p0_vertex_0.glsl",
        "shaders/shaders.json",
        "spine/c1.skel",
        "spot.json",
    ]
    cat = Catalog((tmp_path / "catalog_main_ja.bin").read_bytes(), tmp_path / "cache", apk=tmp_path / "base.apk")
    doc = json.loads((out / "spot.json").read_bytes())
    # the closures as the catalog resolves them: the names, and the bundles the extractors read in that order
    names = {"background": [b.name for b in cat.resolve(BG)], "situation": [b.name for b in cat.resolve(SITUATION)]}
    assert doc["resources"] == names
    assert (out / "spine" / "c1.skel").read_bytes() == b"SKEL" + _digest(cat.fetch_key(SITUATION))
    assert (out / "room.glb").read_bytes() == b"glTF" + _digest(cat.fetch_key(BG))
    report = {"backgroundKey": BG, "glb": "room.glb", "meshCount": 2, "inactiveMeshes": 1, "cutoff": [0.5, 1e-7]}
    assert (out / "room.json").read_text(encoding="utf-8") == jsonio.dumps(report, indent=1, ensure_ascii=False)
    # the shaders of the background's closure, then the situation's (by path id): the first of each name
    assert [b.name for b in cat.resolve(BG)] == [SCENE["c"], SCENE["b"]]
    assert [b.name for b in cat.resolve(SITUATION)] == [SCENE["c"], SCENE["s"]]
    records = json.loads((out / "shaders" / "shaders.json").read_bytes())
    assert [(r["name"], r["source"]) for r in records] == [
        ("Common/Unlit", SCENE["c"]),
        ("Room/Wall", SCENE["b"]),
        ("Spine/Skin", SCENE["s"]),
    ]
    assert json.loads(stdout) == {
        "out": str(out),
        "spot": {"skeletons": 1, "tapTargets": 2},
        "room": {"meshes": 2, "inactiveMeshes": 1},
        "shaders": {"shaders": 3, "variants": 3},
    }
    assert rooms == [BG] and spots == [1]
    # the same Spot again and its room alone: hits of the store
    code, _, err = run([*base, "export", "spot", "1", "-o", str(tmp_path / "again")], capsys)
    assert code == 0 and rooms == [BG] and spots == [1], err
    glb = tmp_path / "r" / "scene.glb"
    code, stdout, err = run([*base, "--json", "export", "room", BG, "-o", str(glb)], capsys)
    assert code == 0 and rooms == [BG], err
    assert glb.read_bytes() == (out / "room.glb").read_bytes()
    named = {**report, "glb": "scene.glb"}
    assert glb.with_suffix(".json").read_text(encoding="utf-8") == jsonio.dumps(named, indent=1, ensure_ascii=False)
    assert json.loads(stdout) == named


def test_export_spot_names_a_key_of_the_spot_the_catalog_lacks(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(assets, "STAGE_SOURCES", SCENE_SOURCES)
    code, out, err = run([*scene_settings(tmp_path), "export", "spot", "2", "-o", str(tmp_path / "o")], capsys)
    assert code == 2 and out == "", err
    assert "nnnotes export spot: error: not a key of the catalog: Spot/none" in err


# ---------------------------------------------------------------- adv.episode
EP = "Adv/Episode/e1/e1"
EP_BUNDLES = {n: f"adv_{n}_{n * 32}.bundle" for n in ("e", "t", "c")}
EP_MASTER = {
    "MasterAdv": [{"_id": 1, "_advEpisodeAsset": "e1", "_titleTextId": ""}],
    "MasterAdvChat": [],
    "MasterText": [{"_id": "t", "_japanese": "title"}],
}


def episode_settings(tmp_path, extra=()) -> list[str]:
    """A catalog with the episode e1 (its script, a -Text shard and a model) and the ADV player settings in the
    APK; `extra`: more (key, bundle letter) entries."""
    cache = tmp_path / "cache" / "bundles"
    cache.mkdir(parents=True, exist_ok=True)
    for name in EP_BUNDLES.values():
        (cache / name).write_bytes(b"UnityFS " + name.encode())
    entries = [
        (EP, "Assets/Adv/e1.asset", [3]),
        (EP + "-Text", "Assets/Adv/e1-Text.json", [4]),
        ("Character/Live2D/g/m/model/m", "Assets/m.prefab", [5]),
    ]
    entries += [(EP_BUNDLES[n], synth.remote(EP_BUNDLES[n]), []) for n in ("e", "t", "c")]
    entries += [(key, f"Assets/{key}.prefab", [5]) for key in extra]
    catalog = tmp_path / "catalog_main_ja.bin"
    catalog.write_bytes(synth.CatalogWriter().build(entries))
    md = tmp_path / "master"
    md.mkdir(exist_ok=True)
    for name, rows in EP_MASTER.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    apk = tmp_path / "base.apk"
    settings = [
        ("EmbCommon/Adv/Settings/AdvPlayerSettings", "Assets/Emb/s.asset", [1]),
        ("emb.bundle", synth.local("emb.bundle"), []),
    ]
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(settings))
        z.writestr("assets/aa/Android/emb.bundle", b"UnityFS player settings")
    return [
        "--catalog",
        str(catalog),
        "--cache",
        str(tmp_path / "cache"),
        "--region",
        "zz",
        "--language",
        "ja",
        "--master",
        str(md),
        "--apk",
        str(apk),
    ]


def test_export_episode_writes_the_episode_task_and_reruns_on_its_keys(tmp_path, capsys, monkeypatch):
    from nnnotes.content import adv

    calls = []

    def extract(cat, md, adv_id):
        (row,) = [r for r in json.loads((md / "MasterAdv.json").read_bytes())["_allData"] if r["_id"] == adv_id]
        assert row["_advEpisodeAsset"] == "e1" and (md / "MasterText.json").is_file()
        calls.append(adv_id)
        return {
            "script": [p.read_bytes() for p in cat.fetch_key(EP)][0].decode(),
            "text": cat.has(EP + "-Text") and [p.read_bytes() for p in cat.fetch_key(EP + "-Text")][0].decode(),
            "sound": cat.has(EP + "-Sound"),
            "settings": [p.read_bytes() for p in cat.fetch_key(adv.PLAYER_SETTINGS_KEY)][-1].decode(),
            "model": cat.has("Character/Live2D/g/m/model/m"),
            "stage": cat.has("Adv/Stage/s1"),
            "commandCount": 2,
            "resources": [{"kind": "live2d"}],
        }

    monkeypatch.setattr(adv, "extract", extract)
    monkeypatch.setattr(adv, "to_json", lambda x: x)
    base = episode_settings(tmp_path)
    out = tmp_path / "e.json"
    code, stdout, err = run([*base, "--json", "export", "episode", "1", "-o", str(out)], capsys)
    assert code == 0, err
    doc = {
        "script": "UnityFS " + EP_BUNDLES["e"],
        "text": "UnityFS " + EP_BUNDLES["t"],
        "sound": False,
        "settings": "UnityFS player settings",
        "model": True,
        "stage": False,
        "commandCount": 2,
        "resources": [{"kind": "live2d"}],
    }
    assert out.read_text(encoding="utf-8") == jsonio.dumps(doc, indent=1, ensure_ascii=False)
    assert json.loads(stdout) == {"out": str(out), "commands": 2, "resources": 1} and calls == [1]
    # a key outside the prefixes of the episode's resources: a hit; a resource key: the episode again
    base = episode_settings(tmp_path, extra=["Other/k"])
    code, _, err = run([*base, "export", "episode", "1", "-o", str(out)], capsys)
    assert code == 0 and calls == [1], err
    base = episode_settings(tmp_path, extra=["Other/k", "Adv/Stage/s1"])
    code, _, err = run([*base, "export", "episode", "1", "-o", str(out)], capsys)
    assert code == 0 and calls == [1, 1], err
    assert json.loads(out.read_bytes())["stage"] is True
