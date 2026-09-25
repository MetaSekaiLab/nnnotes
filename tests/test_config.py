from pathlib import Path

import pytest

from nnnotes import config
from nnnotes.config import Config, ConfigError, env_name

KEY_HEX = bytes(range(16)).hex()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_env_name():
    assert env_name("bundle", "key") == "NNNOTES_BUNDLE_KEY"
    assert env_name("bundle", "nonce_seed") == "NNNOTES_BUNDLE_NONCE_SEED"
    assert env_name("servers.tw", "cdn") == "NNNOTES_SERVERS_TW_CDN"
    assert env_name("servers.a-b", "cdn") == "NNNOTES_SERVERS_A_B_CDN"


def test_precedence_file_env_flag(tmp_path):
    f = write(tmp_path / "c.toml", '[catalog]\nregion = "file"\nlanguage = "file-lang"\n')
    assert Config.load(f, environ={}).get("catalog", "region") == "file"
    env = {"NNNOTES_CATALOG_REGION": "env"}
    assert Config.load(f, environ=env).get("catalog", "region") == "env"
    cfg = Config.load(f, environ=env, overrides={("catalog", "region"): "flag"})
    assert cfg.get("catalog", "region") == "flag"
    assert cfg.get("catalog", "language") == "file-lang"


def test_empty_values_count_as_unset(tmp_path):
    f = write(tmp_path / "c.toml", '[catalog]\nregion = "file"\nlanguage = ""\n[servers.x]\nlanguages = []\n')
    cfg = Config.load(f, environ={"NNNOTES_CATALOG_REGION": ""}, overrides={("catalog", "region"): ""})
    assert cfg.get("catalog", "region") == "file"
    assert not cfg.has("catalog", "language")
    assert cfg.get_list("servers.x", "languages") == []


def test_config_file_lookup(tmp_path, monkeypatch):
    write(tmp_path / "nnnotes.toml", '[catalog]\nregion = "cwd"\n')
    named = write(tmp_path / "named.toml", '[catalog]\nregion = "named"\n')
    flag = write(tmp_path / "flag.toml", '[catalog]\nregion = "flag"\n')
    assert Config.load(environ={}).get("catalog", "region") == "cwd"
    env = {"NNNOTES_CONFIG": str(named)}
    assert Config.load(environ=env).get("catalog", "region") == "named"
    assert Config.load(flag, environ=env).get("catalog", "region") == "flag"
    # NNNOTES_CONFIG names the file; it is not a setting itself
    assert Config.load(environ=env).source == str(named)


def test_no_file_is_fine(tmp_path):
    cfg = Config.load(environ={})
    assert cfg.source is None and not cfg.has("bundle", "key")


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="--config"):
        Config.load(tmp_path / "absent.toml", environ={})
    with pytest.raises(ConfigError, match="NNNOTES_CONFIG"):
        Config.load(environ={"NNNOTES_CONFIG": str(tmp_path / "absent.toml")})


def test_invalid_toml_names_file_not_value(tmp_path):
    f = write(tmp_path / "bad.toml", f'[bundle]\nkey = "{KEY_HEX}\n')
    with pytest.raises(ConfigError) as e:
        Config.load(f, environ={})
    assert "invalid TOML" in str(e.value) and KEY_HEX not in str(e.value)


def test_missing_setting_message():
    cfg = Config.load(environ={}, flags={("catalog", "region"): "--region"})
    with pytest.raises(ConfigError) as e:
        cfg.require("catalog", "region")
    msg = str(e.value)
    assert "catalog.region" in msg and "`region` in the [catalog] table" in msg
    assert "NNNOTES_CATALOG_REGION" in msg and "--region" in msg
    with pytest.raises(ConfigError) as e:
        cfg.cdn("tw")
    assert "servers.tw.cdn" in str(e.value) and "NNNOTES_SERVERS_TW_CDN" in str(e.value)


@pytest.mark.parametrize("value, what", [
    ("zz" * 16, "must be a hex string"),
    ("00ff", "must be 16 bytes (32 hex digits)"),
    (bytes(range(17)).hex(), "must be 16 bytes (32 hex digits)"),
])
def test_malformed_hex(value, what):
    cfg = Config.load(environ={"NNNOTES_BUNDLE_KEY": value})
    with pytest.raises(ConfigError) as e:
        cfg.hex("bundle", "key", 16)
    assert str(e.value) == f"setting bundle.key: {what}"
    assert value not in str(e.value)


def test_hex_forms():
    cfg = Config.load(environ={"NNNOTES_BUNDLE_KEY": f" 0x{KEY_HEX.upper()} ", "NNNOTES_BUNDLE_NONCE_SEED": "abcd"})
    assert cfg.hex("bundle", "key", 16) == bytes(range(16))
    assert cfg.hex("bundle", "nonce_seed") == b"\xab\xcd"


def test_wrong_types(tmp_path):
    f = write(tmp_path / "c.toml", '[bundle]\nkey = 12345\n[paths]\ncache = 1\n[servers.x]\nlanguages = [1, 2]\n')
    cfg = Config.load(f, environ={})
    for call, what in ((lambda: cfg.get("bundle", "key"), "must be a string"),
                       (lambda: cfg.path("paths", "cache"), "must be a path string"),
                       (lambda: cfg.get_list("servers.x", "languages"), "must be a list of strings")):
        with pytest.raises(ConfigError) as e:
            call()
        assert what in str(e.value) and "12345" not in str(e.value)


def test_paths_relative_to_their_source(tmp_path):
    f = write(tmp_path / "conf" / "c.toml", '[paths]\ncache = "cache"\nmaster = "m"\n')
    cfg = Config.load(f, environ={"NNNOTES_PATHS_MASTER": "env-m"}, overrides={("paths", "apk"): "x.apk"})
    assert cfg.path("paths", "cache") == (tmp_path / "conf").resolve() / "cache"
    assert cfg.path("paths", "master") == Path("env-m")
    assert cfg.path("paths", "apk") == Path("x.apk")
    assert cfg.path("paths", "ffmpeg") is None
    with pytest.raises(ConfigError, match="NNNOTES_PATHS_VGMSTREAM"):
        cfg.require_path("paths", "vgmstream")


def test_lists():
    cfg = Config.load(environ={"NNNOTES_SERVERS_TW_LANGUAGES": "zh-Hant, en ,"})
    assert cfg.get_list("servers.tw", "languages") == ["zh-Hant", "en"]


def test_regions_from_tables_and_env(tmp_path):
    f = write(tmp_path / "c.toml", '[servers.aa]\nname = "A"\n[servers.bb]\ncdn = "x"\n')
    cfg = Config.load(f, environ={"NNNOTES_SERVERS_CC_CDN": "y", "NNNOTES_SERVERS_AA_CDN": "z"})
    assert cfg.regions() == ["aa", "bb", "cc"]


def test_cdn_strips_trailing_slash():
    cfg = Config.load(environ={"NNNOTES_SERVERS_T_CDN": "file:///base/", "NNNOTES_CATALOG_REGION": "t"})
    assert cfg.cdn(cfg.region()) == "file:///base"


def test_repr_shows_no_values(tmp_path):
    f = write(tmp_path / "c.toml", f'[bundle]\nkey = "{KEY_HEX}"\n[servers.t]\ncdn = "file:///secret-base"\n')
    cfg = Config.load(f, environ={"NNNOTES_MASTER_KEY": "feedface" * 8})
    text = repr(cfg) + str(cfg)
    assert KEY_HEX not in text and "secret-base" not in text and "feedface" not in text
    assert repr(str(f)) in repr(cfg)


def test_tool_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda exe: None)
    config.use(Config.load(environ={}))
    with pytest.raises(ConfigError) as e:
        config.tool("ffmpeg", "ffmpeg")
    msg = str(e.value)
    assert "ffmpeg not found on PATH" in msg
    assert "paths" in msg and "NNNOTES_PATHS_FFMPEG" in msg and "--ffmpeg" in msg
    exe = tmp_path / "bin" / "ffmpeg"
    config.use(Config.load(environ={"NNNOTES_PATHS_FFMPEG": str(exe)}))
    assert config.tool("ffmpeg", "ffmpeg") == str(exe)
    monkeypatch.setattr(config.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    config.use(Config.load(environ={}))
    assert config.tool("vgmstream", "vgmstream-cli") == "/usr/bin/vgmstream-cli"
