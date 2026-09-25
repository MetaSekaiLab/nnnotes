import json
import zipfile
from pathlib import Path

import pytest

import synth
from nnnotes import cli

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
    p.write_bytes(synth.CatalogWriter().build([
        ("Char/A", "Assets/Game/Char/A.prefab", [2]),
        ("Char/B", "Assets/Game/Char/B.prefab", [2]),
        ("a_01.bundle", synth.remote("a_01.bundle"), []),
    ]))
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
    for cmd in ("catalog", "browse", "pull", "master", "adv", "story", "live2d", "spot", "room", "shader", "audio",
                "crikey", "player", "live", "web"):
        assert cmd in out


@pytest.mark.parametrize("argv", [
    ["adv", "1"], ["story", "1"], ["live2d", "k"], ["spot", "1"], ["room", "k"], ["shader", "--key", "k"],
    ["audio", "s"], ["player"], ["live", "1"], ["master", "decode", "x"], ["master", "download", "--version", "1"],
])
def test_output_is_required(argv, capsys):
    code, _, err = run(argv, capsys)
    assert code == 2 and "-o/--out" in err


def test_web_needs_a_selection(capsys):
    code, _, err = run(["web", "out"], capsys)
    assert code == 2 and "--pair" in err and "--all" in err


def test_parse_pair():
    assert cli.parse_pair("100001:expert") == (100001, "expert")
    assert cli.parse_pair("7_easy") == (7, "easy")
    with pytest.raises(Exception):
        cli.parse_pair("7:extreme")


def test_catalog_listing(catalog_file, tmp_path, capsys):
    code, out, err = run(["--catalog", str(catalog_file), "--cache", str(tmp_path / "cache"),
                          "catalog", "--prefix", "Char/", "--limit", "1"], capsys)
    assert code == 0 and out.splitlines() == ["Char/A"] and "# 2 keys" in err


def test_missing_settings_exit_2_and_name_the_setting(catalog_file, tmp_path, cdn, capsys, monkeypatch):
    base = ["--catalog", str(catalog_file), "--cache", str(tmp_path / "cache")]
    code, out, err = run(base + ["pull", "Char/A"], capsys)
    assert code == 2 and out == ""
    assert err.strip() == ("nnnotes: a_01.bundle is not in the cache: setting catalog.region is not set: give it "
                           "as `region` in the [catalog] table of the config file, the environment variable "
                           "NNNOTES_CATALOG_REGION or --region")
    code, _, err = run(base + ["--region", "zz", "pull", "Char/A"], capsys)
    assert code == 2 and "servers.zz.cdn" in err and "NNNOTES_SERVERS_ZZ_CDN" in err
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    code, _, err = run(base + ["--region", "zz", "pull", "Char/A"], capsys)
    assert code == 2 and "bundle.key" in err and "NNNOTES_BUNDLE_KEY" in err
    assert cdn not in err and len(err.strip().splitlines()) == 1
    code, _, err = run(["pull", "Char/A"], capsys)
    assert code == 2 and "paths.cache" in err and "NNNOTES_PATHS_CACHE" in err and "--cache" in err


def test_malformed_setting_names_setting_not_value(catalog_file, tmp_path, cdn, capsys, monkeypatch):
    bad = "0123456789abcdef"                          # 8 bytes
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", bad)
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", SEED_HEX)
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    code, _, err = run(["--catalog", str(catalog_file), "--cache", str(tmp_path / "cache"), "--region", "zz",
                        "pull", "Char/A"], capsys)
    assert code == 2 and err.strip() == ("nnnotes: a_01.bundle is not in the cache: setting bundle.key: must be "
                                         "16 bytes (32 hex digits)")
    assert bad not in err


def test_missing_path_on_disk(tmp_path, capsys):
    code, _, err = run(["--cache", str(tmp_path / "c"), "--catalog", str(tmp_path / "absent.bin"), "catalog"], capsys)
    assert code == 2 and "setting paths.catalog: file" in err


def test_pull_from_config_file(catalog_file, tmp_path, cdn, capsys):
    conf = tmp_path / "conf" / "nnnotes.toml"
    conf.parent.mkdir()
    conf.write_text(f'[bundle]\nkey = "{KEY_HEX}"\nnonce_seed = "{SEED_HEX}"\n'
                    f'[catalog]\nregion = "zz"\nlanguage = "xx"\n'
                    f'[servers.zz]\ncdn = "{cdn}"\n'
                    f'[paths]\ncatalog = "../{catalog_file.name}"\ncache = "cache"\n', encoding="utf-8")
    code, out, err = run(["--config", str(conf), "pull", "Char/A", "Char/B"], capsys)
    assert code == 0, err
    cached = tmp_path / "conf" / "cache" / "bundles" / "a_01.bundle"
    assert cached.read_bytes() == synth.fake_bundle("a_01.bundle")
    assert [Path(line).resolve() for line in out.splitlines()] == [cached.resolve()] * 2
    assert KEY_HEX not in out + err


def test_master_decode_command(tmp_path, capsys, monkeypatch):
    src = tmp_path / "bin"
    src.mkdir()
    (src / "MasterX.bin").write_bytes(synth.master_file({"_allData": [{"_id": 1}]}))
    code, _, err = run(["master", "decode", str(src), "-o", str(tmp_path / "json")], capsys)
    assert code == 2 and "master.key" in err and "NNNOTES_MASTER_KEY" in err
    monkeypatch.setenv("NNNOTES_MASTER_KEY", MKEY_HEX)
    monkeypatch.setenv("NNNOTES_MASTER_IV", MIV_HEX)
    code, out, _ = run(["master", "decode", str(src), "-o", str(tmp_path / "json")], capsys)
    assert code == 0 and json.loads(out)["decoded"] == 1
    assert json.loads((tmp_path / "json" / "MasterX.json").read_text(encoding="utf-8")) == {"_allData": [{"_id": 1}]}
    (src / "MasterY.bin").write_bytes(b"\0" * 96)
    code, out, _ = run(["master", "decode", str(src), "-o", str(tmp_path / "json")], capsys)
    assert code == 1 and json.loads(out)["failed"][0]["file"] == "MasterY.bin"


def test_master_download_command(tmp_path, capsys, monkeypatch):
    d = tmp_path / "cdn" / "master" / "9.9.9"
    d.mkdir(parents=True)
    (d / "MasterManifest.json").write_text(json.dumps({"version": "9.9.9", "files": []}))
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", (tmp_path / "cdn").as_uri())
    code, _, err = run(["master", "download", "--version", "9.9.9", "-o", str(tmp_path / "m")], capsys)
    assert code == 2 and "catalog.region" in err
    code, out, _ = run(["--region", "zz", "master", "download", "--version", "9.9.9", "-o", str(tmp_path / "m")],
                       capsys)
    assert code == 0 and json.loads(out)["version"] == "9.9.9"
    assert (tmp_path / "m" / "MasterManifest.json").is_file()


def test_browse_needs_a_region(capsys):
    code, _, err = run(["browse"], capsys)
    assert code == 2 and "[servers.<region>]" in err


def test_browse_region_needs_languages(capsys, monkeypatch):
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", "file:///nowhere")
    code, _, err = run(["browse"], capsys)
    assert code == 2 and "servers.zz.languages" in err and "NNNOTES_SERVERS_ZZ_LANGUAGES" in err


def test_web_needs_the_player(tmp_path, capsys):
    code, _, err = run(["web", str(tmp_path / "site"), "--player-only"], capsys)
    assert code == 2 and "--player" in err and "NNNOTES_PATHS_PLAYER" in err


def test_catalog_from_the_cache_needs_no_region(catalog_file, tmp_path, capsys):
    cache = tmp_path / "cache"
    code, _, err = run(["--cache", str(cache), "--language", "xx", "catalog"], capsys)
    assert code == 2 and "catalog.region" in err and "NNNOTES_CATALOG_REGION" in err
    cache.mkdir()
    (cache / "catalog_main_xx.bin").write_bytes(catalog_file.read_bytes())
    code, out, err = run(["--cache", str(cache), "--language", "xx", "catalog", "--prefix", "Char/"], capsys)
    assert code == 0, err
    assert out.splitlines() == ["Char/A", "Char/B"]


@pytest.mark.parametrize("argv", [["live2d", "Char/A"], ["spot", "1"]])
def test_apk_is_a_required_setting(argv, catalog_file, tmp_path, cdn, capsys, monkeypatch):
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", KEY_HEX)
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", SEED_HEX)
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    (tmp_path / "master").mkdir()
    code, _, err = run(["--catalog", str(catalog_file), "--cache", str(tmp_path / "cache"), "--region", "zz",
                        "--master", str(tmp_path / "master"), *argv, "-o", str(tmp_path / "out")], capsys)
    assert code == 2 and "paths.apk" in err and "NNNOTES_PATHS_APK" in err and "--apk" in err


def test_crikey_write_creates_the_directory(tmp_path, capsys, monkeypatch):
    from nnnotes import crikey
    apk = tmp_path / "base.apk"
    apk.write_bytes(b"not read")
    monkeypatch.setattr(crikey, "find_key", lambda path: 987654321012)
    target = tmp_path / "keys" / "sub"
    code, out, _ = run(["--apk", str(apk), "crikey", "--write", str(target)], capsys)
    assert code == 0
    assert (target / ".hcakey").read_bytes() == (987654321012).to_bytes(8, "big")
    assert "12 digits" in out and "987654321012" not in out
    code, _, err = run(["crikey"], capsys)
    assert code == 2 and "paths.apk" in err


# ---------------------------------------------------------------- unknown keys and ids: usage errors
MASTER_TABLES = {
    "MasterAdv": [{"_id": 1}],
    "MasterHomeSpot": [{"_id": 1}],
    "MasterLiveMusic": [{"_id": 7, "_expertID": 70, "_easyID": 0, "_vocalCharacterIDs": [1]}],
    "MasterLiveMusicScore": [{"_id": 70}],
    "MasterMemberCard": [{"_id": 5, "_characterID": 1}],
    "MasterCharacter": [{"_id": 1, "_bandID": 2}],
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
    return ["--catalog", str(catalog_file), "--cache", str(tmp_path / "cache"), "--region", "zz", "--language", "ja",
            "--master", str(md), "--apk", str(apk)]


@pytest.mark.parametrize("argv, message", [
    (["pull", "Char/A", "Nope/1", "Nope/2"], "not a key of the catalog: Nope/1, Nope/2"),
    (["room", "Nope/1", "-o", "r.glb"], "not a key of the catalog: Nope/1"),
    (["shader", "--key", "Nope/1", "-o", "s"], "not a key of the catalog: Nope/1"),
    (["shader", "--apk-bundle", "zz", "-o", "s"], "--apk-bundle 'zz': 0 APK bundles match"),
    (["audio", "Nope", "-o", "a"], "cue sheet Nope: no key Cri/Sound/Nope in the catalog"),
    (["live2d", "nope", "-o", "m"], "not a Live2D model of the catalog: nope"),
    (["live2d", "Character/Live2D/g/x/model/x", "-o", "m"], "not a Live2D model of the catalog: Character/Live2D/g/x"),
    (["live2d", "Char/A", "-o", "m"], "not a Live2D model of the catalog: Char/A"),
    (["adv", "99", "-o", "e.json"], "episode 99: no MasterAdv row with this id"),
    (["story", "99", "-o", "s"], "episode 99: no MasterAdv row with this id"),
    (["spot", "99", "-o", "s"], "spot 99: no MasterHomeSpot row with this id"),
    (["live", "99", "-o", "l"], "music 99: no MasterLiveMusic row with this id"),
    (["live", "7", "--difficulty", "easy", "-o", "l"], "music 7: no easy chart"),
    (["live", "7", "--leader-card", "9", "-o", "l"], "MasterMemberCard 9: 0 rows"),
    (["live", "7", "--band", "4", "-o", "l"], "band 4: Band/4/live_stage/lightweight_background"),
])
def test_unknown_key_or_id_is_a_usage_error(data, argv, message, capsys):
    code, out, err = run(data + argv, capsys)
    assert code == 2 and out == "", err
    assert f"nnnotes {argv[0]}: error: {message}" in err and "Traceback" not in err
    assert KEY_HEX not in err


def test_known_id_reaches_the_extractor(data, tmp_path, capsys, monkeypatch):
    from nnnotes import adv
    seen = []
    monkeypatch.setattr(adv, "extract", lambda cat, md, adv_id: seen.append(adv_id) or "x")
    monkeypatch.setattr(adv, "to_json", lambda x: {"commandCount": 0, "resources": []})
    code, out, err = run(data + ["adv", "1", "-o", str(tmp_path / "e.json")], capsys)
    assert code == 0 and seen == [1], err


def test_missing_master_table_names_it(data, tmp_path, capsys):
    (tmp_path / "master" / "MasterHomeSpot.json").unlink()
    code, _, err = run(data + ["spot", "1", "-o", "s"], capsys)
    assert code == 2 and "no MasterHomeSpot.json" in err and len(err.strip().splitlines()) == 1


def test_story_needs_the_language(data, capsys):
    i = data.index("--language")
    code, _, err = run(data[:i] + data[i + 2:] + ["story", "1", "-o", "s"], capsys)
    assert code == 2 and "catalog.language" in err and "NNNOTES_CATALOG_LANGUAGE" in err
