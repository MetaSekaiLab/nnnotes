import io
import json
import os
import re
import tomllib
from pathlib import Path

import pytest

from nnnotes import cli, configfile
from nnnotes.config import ConfigError, template

ROOT = Path(__file__).resolve().parents[1]
KEY = bytes(range(16)).hex()
MKEY = bytes(range(32)).hex()


def run(argv, capsys):
    """(exit code, stdout, stderr) of the command line."""
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def leaves(table, section=""):
    for k, v in table.items():
        if isinstance(v, dict):
            yield from leaves(v, f"{section}.{k}" if section else k)
        else:
            yield f"{section}.{k}" if section else k, v


def test_settings_are_the_documented_ones():
    doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    documented = set()
    for section, key in re.findall(r"^\| `\[([^\]]+)\] ([^`]+)` \|", doc, re.M):
        documented.add(f"{section}.{key}")
    known = set()
    for s in configfile.SETTINGS:
        if s.section == configfile.FONTS:
            known.add("paths.fonts.emoji" if s.key == "emoji" else "paths.fonts.<language>")
        else:
            known.add(f"{s.section}.{s.key}")
    assert documented == known


def test_template_values_are_empty():
    data = tomllib.loads(template().decode("utf-8"))
    assert all(v in ("", []) for _, v in leaves(data))
    secrets = {f"{s.section}.{s.key}" for s in configfile.SETTINGS if s.secret}
    assert secrets == {"bundle.key", "bundle.nonce_seed", "master.key", "master.iv"}


def test_resolve():
    assert configfile.resolve("servers.kr.cdn")[1] == "servers.kr"
    assert configfile.resolve("paths.fonts.zh-Hant")[0].kind == "path"
    for bad in ("bundle.ky", "servers.tw.nope", "servers.a b.cdn", "paths.fonts.xx", "key"):
        with pytest.raises(ConfigError, match="unknown setting"):
            configfile.resolve(bad)


def test_init_with_values(tmp_path, capsys):
    code, out, err = run(["config", "init", "--set", f"bundle.key={KEY}", "--set", "servers.en.cdn=https://cdn.test/",
                          "--set", "servers.en.languages=en, ja", "--set", "catalog.region=en",
                          "--set", "paths.cache=cache"], capsys)
    assert code == 0 and KEY not in out + err
    f = tmp_path / "nnnotes.toml"
    assert out.strip() == str(f)
    data = tomllib.loads(f.read_text(encoding="utf-8"))
    assert data["bundle"]["key"] == KEY and data["catalog"]["region"] == "en"
    assert data["servers"] == {"en": {"name": "", "cdn": "https://cdn.test/", "api": "", "languages": ["en", "ja"],
                                      "master": "", "provider": "", "client_version": "", "apk": "",
                                      "catalog": ""}}                  # the example region table renamed
    assert data["paths"]["cache"] == str((tmp_path / "cache").absolute())
    assert "# CDN base URL of the region" in f.read_text(encoding="utf-8")         # the comments stay
    if os.name != "nt":
        assert f.stat().st_mode & 0o777 == 0o600
    code, _, err = run(["config", "init"], capsys)
    assert code == 2 and "exists" in err
    code, _, _ = run(["config", "init", "--force", "--no-input"], capsys)
    assert code == 0 and f.read_bytes() == template()


def test_init_asks_nothing_outside_a_terminal(tmp_path, capsys):
    code, _, _ = run(["config", "init"], capsys)                        # pytest's stdin is not a terminal
    assert code == 0 and (tmp_path / "nnnotes.toml").read_bytes() == template()


def test_init_user_file(tmp_path, capsys):
    code, out, err = run(["config", "init", "--user", "--no-input"], capsys)
    target = tmp_path / "user-config" / "nnnotes" / "nnnotes.toml"
    assert code == 0 and out.strip() == str(target) and target.is_file()
    code, out, _ = run(["config", "path", "--json"], capsys)
    doc = json.loads(out)
    assert doc["schema"] == configfile.PATHS and doc["read"] == str(target.absolute())
    assert [f["state"] for f in doc["files"]] == ["not given", "not given", "not found", "read"]
    run(["config", "init", "--no-input"], capsys)                          # ./nnnotes.toml comes first
    code, _, err = run(["config", "init", "--user", "--no-input", "--force"], capsys)
    assert "the commands read" in err
    doc = json.loads(run(["config", "path", "--json"], capsys)[1])
    assert [f["state"] for f in doc["files"]][2:] == ["read", "found, not read"]


def test_init_value_errors_name_the_setting_only(capsys):
    code, _, err = run(["config", "init", "--set", "master.key=zz" + KEY], capsys)
    assert code == 2 and "master.key" in err and KEY not in err
    code, _, err = run(["config", "init", "--set", "catalog.language=xx"], capsys)
    assert code == 2 and "languages" in err
    code, _, err = run(["config", "init", "--set", "servers.tw.api=https://host/path"], capsys)
    assert code == 2 and "servers.tw.api" in err
    code, _, err = run(["config", "init", "--set", "bundle.key"], capsys)
    assert code == 2 and "SECTION.KEY=VALUE" in err


def test_prompt_asks_every_setting():
    answers = iter([
        "zz",                                               # bundle.key: invalid, asked again
        KEY, "", MKEY, "",                                  # bundle.key, nonce_seed, master.key, master.iv
        "tw,kr",                                            # regions
        "", "zh-Hant",                                      # catalog.region (default tw), language
    ])
    secret_prompts, prompts = [], []

    def ask(p):
        prompts.append(p)
        return next(answers, "")

    def ask_secret(p):
        secret_prompts.append(p)
        return next(answers, "")

    values, regions = configfile.prompt(ask=ask, ask_secret=ask_secret, say=lambda *a: None)
    got = {f"{s}.{k}": v for s, k, v in values}
    assert regions == ["tw", "kr"]
    assert got == {"bundle.key": KEY, "master.key": MKEY, "catalog.region": "tw", "catalog.language": "zh-Hant"}
    assert len(secret_prompts) == 5 and all("(hidden)" in p for p in secret_prompts)
    assert any(p.startswith("[servers.kr] cdn") for p in prompts)
    text = configfile.render(values, regions)
    assert set(tomllib.loads(text)["servers"]) == {"tw", "kr"}


def test_set_and_unset_edit_in_place(tmp_path, capsys, monkeypatch):
    run(["config", "init", "--no-input"], capsys)
    f = tmp_path / "nnnotes.toml"
    before = f.read_text(encoding="utf-8").splitlines()
    code, out, _ = run(["config", "set", "bundle.key", KEY], capsys)
    assert code == 0 and KEY not in out
    monkeypatch.setattr("sys.stdin", io.StringIO(MKEY + "\n"))
    code, out, _ = run(["config", "set", "master.key", "-"], capsys)
    assert code == 0 and MKEY not in out
    run(["config", "set", "servers.kr.languages", "ko"], capsys)
    after = f.read_text(encoding="utf-8").splitlines()
    changed = [line for line in before if line not in after]
    assert changed == ['key = ""', 'key = ""']                          # only the two values' lines
    data = tomllib.loads("\n".join(after))
    assert data["bundle"]["key"] == KEY and data["master"]["key"] == MKEY
    assert data["servers"]["kr"]["languages"] == ["ko"] and "tw" in data["servers"]
    code, _, _ = run(["config", "unset", "servers.kr.languages"], capsys)
    assert code == 0 and tomllib.loads(f.read_text(encoding="utf-8"))["servers"]["kr"]["languages"] == []
    code, _, err = run(["config", "set", "bundle.kye", "1"], capsys)
    assert code == 2 and "unknown setting" in err


def test_set_needs_a_file(tmp_path, capsys):
    code, _, err = run(["config", "set", "bundle.key", KEY], capsys)
    assert code == 2 and "config init" in err
    code, _, _ = run(["config", "set", "--file", str(tmp_path / "x.toml"), "bundle.key", KEY], capsys)
    assert code == 0 and tomllib.loads((tmp_path / "x.toml").read_text(encoding="utf-8"))["bundle"]["key"] == KEY


def test_edit_refuses_what_it_cannot_edit():
    text = '[servers.tw]\nlanguages = [\n  "ja",\n]\n'
    with pytest.raises(ConfigError, match="several lines"):
        configfile.edit(text, [("servers.tw", "languages", ["en"])], "f")
    with pytest.raises(ConfigError, match="edit the file itself"):
        configfile.edit('[paths]\nfonts.ja = "a"\n', [("paths.fonts", "ja", "b")], "f")


@pytest.mark.parametrize("provider", ['"jpp"', '42'])
def test_check_reports_invalid_provider_as_json(tmp_path, capsys, provider):
    conf = tmp_path / "nnnotes.toml"
    conf.write_text(f'[catalog]\nregion="jp"\n[servers.jp]\nprovider={provider}\n', encoding="utf-8")
    code, out, err = run(["--config", str(conf), "config", "check", "--json"], capsys)
    assert code == 1 and not err
    report = json.loads(out)
    assert report["problems"] == 1
    item = next(s for s in report["settings"] if s["name"] == "servers.jp.provider")
    assert item["status"] == "invalid"


def test_check_does_not_turn_jp_language_defaults_into_flags(tmp_path, capsys):
    conf = tmp_path / "nnnotes.toml"
    conf.write_text('[catalog]\nregion="jp"\n[servers.jp]\nprovider="jp"\n', encoding="utf-8")
    code, out, _ = run(["--config", str(conf), "config", "check", "--json"], capsys)
    assert code == 0
    settings = {s["name"]: s for s in json.loads(out)["settings"]}
    assert settings["catalog.region"]["origin"] == "file"
    assert settings["catalog.language"]["status"] == "unset"
    cfg = cli.load_config(cli.build_parser().parse_args(["--config", str(conf), "master", "version"]))
    assert cfg.require("catalog", "language") == "ja"  # normal commands still apply the JP default


def test_check_reports_states_not_values(tmp_path, capsys, monkeypatch):
    (tmp_path / "nnnotes.toml").write_text(
        f'[bundle]\nkey = "{KEY}"\n[master]\niv = "00"\n[catalog]\nregion = "xx"\n[servers.tw]\ncdn = "ftp://h"\n'
        f'[paths]\napk = "missing.apk"\ncache = "c"\n[typo]\nkey = "1"\n', encoding="utf-8")
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", "ab")
    monkeypatch.setenv("NNNOTES_BUNDLE_KY", "ab")
    code, out, _ = run(["config", "check", "--json"], capsys)
    assert code == 1 and KEY not in out and '"ab"' not in out
    doc = json.loads(out)
    by = {s["name"]: s for s in doc["settings"]}
    assert doc["schema"] == configfile.CHECK and doc["file"] == str((tmp_path / "nnnotes.toml").absolute())
    assert by["bundle.key"]["status"] == "ok" and by["bundle.key"]["secret"]
    assert (by["bundle.nonce_seed"]["origin"], by["bundle.nonce_seed"]["status"]) == ("env", "ok")
    assert by["master.iv"]["status"] == "invalid" and "32 bytes" in by["master.iv"]["reason"]
    assert by["master.key"]["status"] == "unset"
    assert by["catalog.region"]["status"] == "invalid"
    assert by["servers.tw.cdn"]["status"] == "invalid"
    assert by["paths.apk"]["status"] == "not found" and by["paths.cache"]["status"] == "ok"
    assert by["typo.key"]["status"] == "unknown" and by["NNNOTES_BUNDLE_KY"]["status"] == "unknown"
    assert doc["problems"] == 6
    code, out, _ = run(["--region", "tw", "config", "check"], capsys)
    assert "catalog.region\tflag\tok" in out and KEY not in out
