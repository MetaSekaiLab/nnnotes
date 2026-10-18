import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from nnnotes import cli
from nnnotes.cli import common
from tests.helpers import livenotes as notes_helper
from tests.helpers import synth

KEY_HEX = synth.BUNDLE_KEY.hex()
SEED_HEX = synth.BUNDLE_SEED.hex()
MKEY_HEX, MIV_HEX = synth.MASTER_KEY.hex(), synth.MASTER_IV.hex()


def run(argv, capsys):
    """(exit code, stdout, stderr) of the command line."""
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def catalog_file(tmp_path):
    p = tmp_path / "catalog_main_xx.bin"
    p.write_bytes(
        synth.CatalogWriter().build(
            [
                ("Char/A", "Assets/Game/Char/A.prefab", [2]),
                ("Char/B", "Assets/Game/Char/B.prefab", [2]),
                ("a_01.bundle", synth.remote("a_01.bundle"), []),
            ]
        )
    )
    return p


@pytest.fixture
def cdn(tmp_path):
    p = tmp_path / "cdn" / "asset" / "Android" / "a_01.bundle"
    p.parent.mkdir(parents=True)
    p.write_bytes(synth.encrypt_bundle(synth.fake_bundle("a_01.bundle"), "a_01.bundle"))
    return (tmp_path / "cdn").as_uri()


def test_version_and_help(capsys):
    code, out, _ = run(["--version"], capsys)
    assert code == 0 and out.startswith("nnnotes ")
    code, out, _ = run(["--help"], capsys)
    assert code == 0
    for cmd in (
        "config",
        "servers",
        "catalog",
        "bundle",
        "master",
        "export",
        "apk",
        "depot",
        "voices",
        "site",
        "music-data",
    ):
        assert cmd in out


# the command line, then a module that loads numpy; prints OPENBLAS_NUM_THREADS as numpy starts to load (numpy
# reads it then)
NUMPY_IMPORT_ENV = """\
import os, sys
class Probe:
    def find_spec(self, name, path=None, target=None):
        if name == "numpy":
            print(os.environ.get("OPENBLAS_NUM_THREADS"))
sys.meta_path.insert(0, Probe())
import nnnotes.cli
import nnnotes.unity.reader
"""


@pytest.mark.parametrize("given, seen", [(None, "1"), ("4", "4")])
def test_numpy_blas_threads_set_before_numpy_loads(given, seen):
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    if given is not None:
        env["OPENBLAS_NUM_THREADS"] = given
    r = subprocess.run([sys.executable, "-c", NUMPY_IMPORT_ENV], env=env, capture_output=True, text=True, check=True)
    assert r.stdout.split() == [seen]


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "episode", "1"],
        ["export", "story", "1"],
        ["export", "live2d", "k"],
        ["export", "spot", "1"],
        ["export", "room", "k"],
        ["export", "shader", "--key", "k"],
        ["export", "audio", "s"],
        ["apk", "settings"],
        ["export", "live", "1"],
        ["master", "decode", "x"],
        ["master", "download", "--version", "1"],
    ],
)
def test_output_is_required(argv, capsys):
    code, _, err = run(argv, capsys)
    assert code == 2 and "-o/--out" in err


def test_web_needs_a_selection(capsys):
    code, _, err = run(["site", "build", "-o", "out"], capsys)
    assert code == 2 and "--pair" in err and "--all" in err


def test_parse_pair():
    assert common.parse_pair("100001:expert") == (100001, "expert")
    assert common.parse_pair("7_easy") == (7, "easy")
    with pytest.raises(argparse.ArgumentTypeError):
        common.parse_pair("7:extreme")


def test_catalog_listing(catalog_file, tmp_path, capsys):
    code, out, err = run(
        [
            "--catalog",
            str(catalog_file),
            "--depot",
            str(tmp_path / "depot"),
            "catalog",
            "keys",
            "--prefix",
            "Char/",
            "--limit",
            "1",
        ],
        capsys,
    )
    assert code == 0 and out.splitlines() == ["Char/A"] and "# 2 keys" in err


def test_missing_settings_exit_2_and_name_the_setting(catalog_file, tmp_path, cdn, capsys, monkeypatch):
    base = ["--catalog", str(catalog_file), "--depot", str(tmp_path / "depot")]
    code, out, err = run(base + ["bundle", "pull", "Char/A", "-o", str(tmp_path / "b")], capsys)
    assert code == 2 and out == ""
    assert err.strip() == (
        "nnnotes: a_01.bundle is not downloaded: setting catalog.region is not set: give it "
        "as `region` in the [catalog] table of the config file, the environment variable "
        "NNNOTES_CATALOG_REGION or --region (no config file was found: `nnnotes config init` "
        "writes one to fill in)"
    )
    code, _, err = run(base + ["--region", "zz", "bundle", "pull", "Char/A", "-o", str(tmp_path / "b")], capsys)
    assert code == 2 and "servers.zz.cdn" in err and "NNNOTES_SERVERS_ZZ_CDN" in err
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    code, _, err = run(base + ["--region", "zz", "bundle", "pull", "Char/A", "-o", str(tmp_path / "b")], capsys)
    assert code == 2 and "bundle.key" in err and "NNNOTES_BUNDLE_KEY" in err
    assert cdn not in err and len(err.strip().splitlines()) == 1
    code, _, err = run(["bundle", "pull", "Char/A", "-o", str(tmp_path / "b")], capsys)
    assert code == 2 and "paths.depot" in err and "NNNOTES_PATHS_DEPOT" in err and "--depot" in err


def test_malformed_setting_names_setting_not_value(catalog_file, tmp_path, cdn, capsys, monkeypatch):
    bad = "0123456789abcdef"  # 8 bytes
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", bad)
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", SEED_HEX)
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    code, _, err = run(
        [
            "--catalog",
            str(catalog_file),
            "--depot",
            str(tmp_path / "depot"),
            "--region",
            "zz",
            "bundle",
            "pull",
            "Char/A",
            "-o",
            str(tmp_path / "b"),
        ],
        capsys,
    )
    assert code == 2 and err.strip() == (
        "nnnotes: a_01.bundle is not downloaded: setting bundle.key: must be 16 bytes (32 hex digits)"
    )
    assert bad not in err


def test_missing_path_on_disk(tmp_path, capsys):
    code, _, err = run(
        ["--depot", str(tmp_path / "c"), "--catalog", str(tmp_path / "absent.bin"), "catalog", "keys"], capsys
    )
    assert code == 2 and "setting paths.catalog: file" in err


def test_pull_from_config_file(catalog_file, tmp_path, cdn, capsys, monkeypatch):
    monkeypatch.delenv("NNNOTES_DEPOT_DOWNLOADS")  # the default budget: 0
    conf = tmp_path / "conf" / "nnnotes.toml"
    conf.parent.mkdir()
    conf.write_text(
        f'[bundle]\nkey = "{KEY_HEX}"\nnonce_seed = "{SEED_HEX}"\n'
        f'[catalog]\nregion = "zz"\nlanguage = "xx"\n'
        f'[servers.zz]\ncdn = "{cdn}"\n'
        f'[paths]\ncatalog = "../{catalog_file.name}"\ndepot = "depot"\n',
        encoding="utf-8",
    )
    code, out, err = run(
        ["--config", str(conf), "bundle", "pull", "Char/A", "Char/B", "-o", str(tmp_path / "out")], capsys
    )
    assert code == 0, err
    pulled = tmp_path / "out" / "a_01.bundle"
    assert pulled.read_bytes() == synth.fake_bundle("a_01.bundle")
    assert [Path(line).resolve() for line in out.splitlines()] == [pulled.resolve()] * 2
    assert KEY_HEX not in out + err
    downloads = tmp_path / "conf" / "depot" / "downloads"
    assert not (downloads / "bundles").exists()  # [depot] downloads unset: removed when the command ends
    assert not list((tmp_path / "conf" / "depot" / "leases").iterdir())


def test_master_decode_command(tmp_path, capsys, monkeypatch):
    src = tmp_path / "bin"
    src.mkdir()
    (src / "MasterX.bin").write_bytes(synth.master_file({"_allData": [{"_id": 1}]}))
    code, _, err = run(["master", "decode", str(src), "-o", str(tmp_path / "json")], capsys)
    assert code == 2 and "master.key" in err and "NNNOTES_MASTER_KEY" in err
    monkeypatch.setenv("NNNOTES_MASTER_KEY", MKEY_HEX)
    monkeypatch.setenv("NNNOTES_MASTER_IV", MIV_HEX)
    code, out, _ = run(["master", "decode", str(src), "-o", str(tmp_path / "json"), "--json"], capsys)
    assert code == 0 and json.loads(out)["decoded"] == 1
    assert json.loads((tmp_path / "json" / "MasterX.json").read_text(encoding="utf-8")) == {"_allData": [{"_id": 1}]}
    (src / "MasterY.bin").write_bytes(b"\0" * 96)
    code, out, _ = run(["master", "decode", str(src), "-o", str(tmp_path / "json"), "--json"], capsys)
    assert code == 1 and json.loads(out)["failed"][0]["file"] == "MasterY.bin"


def test_master_download_command(tmp_path, capsys, monkeypatch):
    d = tmp_path / "cdn" / "master" / "9.9.9"
    d.mkdir(parents=True)
    (d / "MasterManifest.json").write_text(json.dumps({"version": "9.9.9", "files": []}))
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", (tmp_path / "cdn").as_uri())
    code, _, err = run(["master", "download", "--version", "9.9.9", "-o", str(tmp_path / "m")], capsys)
    assert code == 2 and "catalog.region" in err
    code, out, _ = run(
        ["--region", "zz", "master", "download", "--version", "9.9.9", "-o", str(tmp_path / "m"), "--json"], capsys
    )
    assert code == 0 and json.loads(out)["version"] == "9.9.9"
    assert (tmp_path / "m" / "MasterManifest.json").is_file()


def test_browse_needs_a_region(capsys):
    code, _, err = run(["catalog", "browse"], capsys)
    assert code == 2 and "[servers.<region>]" in err


def test_browse_region_needs_languages(capsys, monkeypatch):
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", "file:///nowhere")
    code, _, err = run(["catalog", "browse"], capsys)
    assert code == 2 and "servers.zz.languages" in err and "NNNOTES_SERVERS_ZZ_LANGUAGES" in err


def test_web_needs_the_player(tmp_path, capsys):
    code, _, err = run(["site", "build", "-o", str(tmp_path / "site"), "--player-only"], capsys)
    assert code == 2 and "--player" in err and "NNNOTES_PATHS_PLAYER" in err


def test_catalog_from_the_downloads_needs_no_region(catalog_file, tmp_path, capsys):
    depot = tmp_path / "depot"
    downloads = depot / "downloads"
    code, _, err = run(["--depot", str(depot), "--language", "xx", "catalog", "keys"], capsys)
    assert code == 2 and "catalog.region" in err and "NNNOTES_CATALOG_REGION" in err
    downloads.mkdir(parents=True)
    (downloads / "catalog_main_xx.bin").write_bytes(catalog_file.read_bytes())
    code, out, err = run(["--depot", str(depot), "--language", "xx", "catalog", "keys", "--prefix", "Char/"], capsys)
    assert code == 0, err
    assert out.splitlines() == ["Char/A", "Char/B"]


@pytest.mark.parametrize("argv", [["export", "live2d", "Char/A"], ["export", "spot", "1"]])
def test_apk_is_a_required_setting(argv, catalog_file, tmp_path, cdn, capsys, monkeypatch):
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", KEY_HEX)
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", SEED_HEX)
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    (tmp_path / "master").mkdir()
    code, _, err = run(
        [
            "--catalog",
            str(catalog_file),
            "--depot",
            str(tmp_path / "depot"),
            "--region",
            "zz",
            "--master",
            str(tmp_path / "master"),
            *argv,
            "-o",
            str(tmp_path / "out"),
        ],
        capsys,
    )
    assert code == 2 and "paths.apk" in err and "NNNOTES_PATHS_APK" in err and "--apk" in err


def test_crikey_write_creates_the_directory(tmp_path, capsys, monkeypatch):
    from nnnotes.formats import crikey

    apk = tmp_path / "base.apk"
    apk.write_bytes(b"not read")
    monkeypatch.setattr(crikey, "find_key", lambda path: 987654321012)
    target = tmp_path / "keys" / "sub"
    code, out, _ = run(["--apk", str(apk), "apk", "hca-key", "--write", str(target)], capsys)
    assert code == 0
    assert (target / ".hcakey").read_bytes() == (987654321012).to_bytes(8, "big")
    assert "12 digits" in out and "987654321012" not in out
    code, _, err = run(["apk", "hca-key"], capsys)
    assert code == 2 and "paths.apk" in err


# ---------------------------------------------------------------- unknown keys and ids: usage errors
MASTER_TABLES = {
    "MasterAdv": [{"_id": 1}],
    "MasterHomeSpot": [{"_id": 1}],
    "MasterLiveMusic": [
        {
            "_id": 7,
            **{f"_{d}ID": 0 for d in ("easy", "normal", "hard")},
            "_expertID": 70,
            "_vocalCharacterIDs": [1],
            "_titleTextID": 1,
            "_bandIDs": [2],
            "_musicSoundID": 1,
            "_jingleSoundID": 0,
            "_jacketAssetName": "jacket7",
        }
    ],
    "MasterLiveMusicScore": [{"_id": 70, "_musicScoreTextFileName": "m7_03", "_fullComboCount": 1}],
    "MasterMemberCard": [{"_id": 5, "_characterID": 1}],
    "MasterCharacter": [{"_id": 1, "_bandID": 2}],
    **{t: [] for t in ("MasterText", "MasterBand", "MasterSound", "MasterSoundCueSheet")},
    **notes_helper.master(),
}


@pytest.fixture
def data(catalog_file, tmp_path, cdn, monkeypatch):
    """Global options of a synthetic catalog, APK (one local bundle) and master data directory."""
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", KEY_HEX)
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", SEED_HEX)
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build([("EmbX", synth.local("x_01.bundle"), [])]))
        z.writestr("assets/aa/Android/x_01.bundle", synth.fake_bundle("x_01.bundle"))
    md = tmp_path / "master"
    md.mkdir()
    for name, rows in MASTER_TABLES.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return [
        "--catalog",
        str(catalog_file),
        "--depot",
        str(tmp_path / "depot"),
        "--region",
        "zz",
        "--language",
        "ja",
        "--master",
        str(md),
        "--apk",
        str(apk),
    ]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["bundle", "pull", "Char/A", "Nope/1", "Nope/2", "-o", "b"], "not a key of the catalog: Nope/1, Nope/2"),
        (["export", "room", "Nope/1", "-o", "r"], "Nope/1: not a spot background key"),
        (["export", "room", "Spot/a/Background/a", "-o", "r"], "Spot/a/Background/a: not a key of the catalog"),
        (["export", "shader", "--key", "Nope/1", "-o", "s"], "not a key of the catalog: Nope/1"),
        (["export", "shader", "--apk-bundle", "zz", "-o", "s"], "--apk-bundle 'zz': 0 APK bundles match"),
        (["export", "audio", "Nope", "-o", "a"], "cue sheet Nope: no key Cri/Sound/Nope in the catalog"),
        (["export", "live2d", "nope", "-o", "m"], "not a Live2D model of the catalog: nope"),
        (
            ["export", "live2d", "Character/Live2D/g/x/model/x", "-o", "m"],
            "not a Live2D model of the catalog: Character/Live2D/g/x",
        ),
        (["export", "live2d", "Char/A", "-o", "m"], "not a Live2D model of the catalog: Char/A"),
        (["export", "episode", "99", "-o", "e.json"], "episode 99: no MasterAdv row with this id"),
        (["export", "story", "99", "-o", "s"], "episode 99: no MasterAdv row with this id"),
        (["export", "spot", "99", "-o", "s"], "spot 99: no MasterHomeSpot row with this id"),
        (["export", "live", "99", "-o", "l"], "music 99: no MasterLiveMusic row with this id"),
        (["export", "live", "7", "--difficulty", "easy", "-o", "l"], "music 7: no easy chart"),
        (["export", "live", "7", "--leader-card", "9", "-o", "l"], "MasterMemberCard 9: 0 rows"),
        (["export", "live", "7", "--band", "4", "-o", "l"], "band 4: Band/4/live_stage/lightweight_background"),
    ],
)
def test_unknown_key_or_id_is_a_usage_error(data, argv, message, capsys):
    code, out, err = run(data + argv, capsys)
    assert code == 2 and out == "", err
    assert f"nnnotes {argv[0]} {argv[1]}: error: {message}" in err and "Traceback" not in err
    assert KEY_HEX not in err


def test_known_id_reaches_the_extractor(data, tmp_path, capsys, monkeypatch):
    from nnnotes.cli import export

    seen = []
    monkeypatch.setattr(
        export, "_file_product", lambda args, cfg, stage, subject: seen.append((stage.name, subject)) or (None, None)
    )
    monkeypatch.setattr(export, "_product_document", lambda *a: {"commandCount": 0, "resources": []})
    monkeypatch.setattr(export, "_place_files", lambda *a, **kw: None)
    code, out, err = run(data + ["export", "episode", "1", "-o", str(tmp_path / "e.json")], capsys)
    assert code == 0 and seen == [("export.episode", "1")], err


def test_missing_master_table_names_it(data, tmp_path, capsys):
    (tmp_path / "master" / "MasterHomeSpot.json").unlink()
    code, _, err = run(data + ["export", "spot", "1", "-o", "s"], capsys)
    assert code == 2 and "no MasterHomeSpot.json" in err and len(err.strip().splitlines()) == 1


def test_the_command_line_starts_without_the_exporters():
    probe = (
        "import sys, nnnotes.cli as c; c.build_parser(); "
        "print([m for m in ('UnityPy', 'nnnotes.site.build') if m in sys.modules])"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert r.stdout.split() == ["[]"]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["export", "audio", "s", "--flac-level", "13"], "--flac-level 13: expected 0 to 12"),
        (["export", "live", "7", "--flac-level", "-1"], "--flac-level -1: expected 0 to 12"),
        (["export", "audio", "s", "--format", "ogg", "--flac-level", "5"], "--flac-level: --format ogg writes no FLAC"),
    ],
)
def test_flac_level_is_checked(data, argv, message, capsys):
    code, out, err = run(data + argv + ["-o", "o"], capsys)
    assert code == 2 and out == "", err
    assert f"nnnotes {argv[0]} {argv[1]}: error: {message}" in err


def test_flac_level_reaches_the_decode(data, tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace

    from nnnotes.cli import export
    from nnnotes.source.session import Session

    seen = []
    monkeypatch.setattr(Session, "catalog", lambda self: SimpleNamespace(has=lambda key: True))
    monkeypatch.setattr(
        export, "_file_product", lambda args, cfg, stage, subject: seen.append(args.flac_level) or (None, None)
    )
    monkeypatch.setattr(export, "_product_document", lambda *a: {})
    monkeypatch.setattr(export, "_place_files", lambda *a, **kw: None)
    for argv in (["export", "audio", "s"],):
        for flag, level in (([], None), (["--flac-level", "5"], 5)):
            seen.clear()
            code, _, err = run(data + argv + flag + ["-o", str(tmp_path / "o")], capsys)
            assert code == 0 and seen == [level], (argv, err)


def test_export_live_asks_for_the_live_and_the_products_it_plays_with(data, tmp_path, capsys, monkeypatch):
    from nnnotes.cli import assets
    from nnnotes.content import livenotes
    from tests.pipeline.test_live import live_master_dir

    md = live_master_dir(tmp_path / "live")
    qualities = [{"_id": q, "_quality": q, "_effectRenderingScale": 0.5} for q in (1, 2)]  # 2: Low
    (md / "MasterLiveQualitySettings.json").write_text(json.dumps({"_allData": qualities}), encoding="utf-8")
    argv = list(data)
    argv[argv.index("--master") + 1] = str(md)
    held = [
        "Band/1/live_stage/lightweight_background",
        "Band/1/timeline/live_start_playable_timeline",
        livenotes.effect_settings_key("fxALight"),
    ]
    seen = {}
    monkeypatch.setattr(assets.Workspace, "run_index", lambda self: None)
    monkeypatch.setattr(assets.Workspace, "index", lambda self: {"locations": [{"primaryKey": k} for k in held]})

    def export(args, cfg, ws):
        seen.update(products=ws.products, selection=ws.selection, params=ws.params(ws.pipeline()))

    monkeypatch.setattr(assets, "export", export)
    opts = ["NoteDesignId=2", "NoteEffectId", "LiveQuality", "NoteSePatternId=1,3"]
    flags = [
        *(x for o in opts for x in ("--live-option", o)),
        "--format",
        "flac",
        "--flac-level",
        "5",
        "--fonts",
        "game",
        "--live-languages",
        "ja,zh-Hant",
    ]
    code, _, err = run(argv + ["export", "live", "7", "--leader-card", "5", *flags, "-o", str(tmp_path / "o")], capsys)
    assert code == 0, err
    assert seen["products"] == {
        "live": ["7/expert/1"],  # the band of the leader card
        "live.scene": ["default"],
        "live.notes": ["default"],
        "live.noteskin": ["skin001", "skin002"],
        "live.noteeffect": ["fxA", "fxALight", "fxASimple"],  # no Light variant of fxASimple in the catalog
        "live.sounds": ["NoteSePatternId=1,3", "default"],
        "live.music": ["7"],
        "live.ui": ["7/expert/ja", "7/expert/zh-Hant"],
    }
    keys = {s[4:] for s in seen["selection"]}
    assert {"EmbScene/Live", "Image/Jacket/jacket7", "Live/MusicScore/m7_03", "Cri/Sound/se3"} <= keys
    assert livenotes.effect_settings_key("fxALight") in keys and livenotes.skin_key("skin002") in keys
    assert seen["params"]["live.music"] == {"audio": "flac"}
    assert seen["params"]["cri.audio"] == {"flac": {"level": 5}}
