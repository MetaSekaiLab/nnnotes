"""Live options in the web build: read sets with settings and their union, the manifest's options, the CLI flag."""
import json
import subprocess
import sys
import threading

import pytest

from nnnotes import web
from nnnotes.config import ConfigError
from nnnotes.liveoptions import LiveOptions, parse_specs, resolve


# ---------------------------------------------------------------- read sets with settings
def test_read_set_passes_the_settings(tmp_path, monkeypatch):
    calls = []

    def run(args, **kw):
        calls.append(args[2:])
        return subprocess.CompletedProcess(args, 0, '{"mode": "plan", "state": "planned", "files": ["a"]}', "")
    monkeypatch.setattr(web, "tool", lambda name, exe: "node")
    monkeypatch.setattr(web.subprocess, "run", run)
    web.read_set(tmp_path, tmp_path, "plan", settings={"NoteDesignId": 2, "MirrorChart": True})
    web.read_set(tmp_path, tmp_path, "plan")
    assert calls[0][-1] == '--settings={"MirrorChart": true, "NoteDesignId": 2}' and calls[0][-2] == "--plan"
    assert calls[1][-1] == "--plan"


def test_the_read_set_key_includes_the_settings(tmp_path, monkeypatch):
    from nnnotes import cache
    cache.configure(enabled=True)
    live = tmp_path / "live-settings"
    live.mkdir()
    (live / "live.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web, "player_id", lambda p: "p")
    calls = []
    monkeypatch.setattr(web, "read_set", lambda d, p, mode="full", settings=None:
                        calls.append(settings) or [json.dumps(settings)])
    assert web.read_set_cached(live, tmp_path, "plan") == ["null"]
    assert web.read_set_cached(live, tmp_path, "plan", settings={"LiveQuality": 2}) == ['{"LiveQuality": 2}']
    assert web.read_set_cached(live, tmp_path, "plan", settings={"LiveQuality": 2}) == ['{"LiveQuality": 2}']
    assert web.read_set_cached(live, tmp_path, "plan", settings={"LiveQuality": 0}) == ['{"LiveQuality": 0}']
    assert calls == [None, {"LiveQuality": 2}, {"LiveQuality": 0}]


ECHO_SERVER = r'''
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    result = {"mode": "plan", "state": "planned", "frames": 0, "files": [json.dumps(req.get("settings"))]}
    print(json.dumps({"ok": True, "result": result, "cpuSeconds": 0}))
    sys.stdout.flush()
'''


def test_servers_pass_the_settings(tmp_path, monkeypatch):
    player = tmp_path / "player"
    (player / "scripts").mkdir(parents=True)
    (player / web.READ_SET_SCRIPT).write_text(ECHO_SERVER, encoding="utf-8")
    monkeypatch.setattr(web, "tool", lambda name, exe: sys.executable)
    servers = web.ReadSetServers(player, timeout=5)
    try:
        assert servers.read_set(tmp_path / "a", None, "plan") == ["null"]
        assert servers.read_set(tmp_path / "a", None, "plan", settings={"NoteSePatternId": 3}) == \
            ['{"NoteSePatternId": 3}']
    finally:
        servers.close()


class FakeSets:
    """read_set_cached stand-in: (mode, settings as JSON or None) -> files; a value that is an exception raises."""

    def __init__(self, sets):
        self.sets, self.calls, self.lock = sets, [], threading.Lock()

    def __call__(self, live_dir, player_dir, mode="full", digest=None, run=None, settings=None):
        key = (mode, json.dumps(settings, sort_keys=True) if settings else None)
        with self.lock:
            self.calls.append(key)
        got = self.sets[key]
        if isinstance(got, Exception):
            raise got
        return list(got)


def reads_with(monkeypatch, player, features, sets):
    monkeypatch.setattr(web, "player_features", lambda p: frozenset(features))
    monkeypatch.setattr(web, "player_id", lambda p: "player-code-1")
    fake = FakeSets(sets)
    monkeypatch.setattr(web, "read_set_cached", fake)
    monkeypatch.setattr(web, "live_dir_digest", lambda d: [(d.name, b"")])
    logs = []
    return web.ReadSets(player, log=logs.append, check_every=1000), fake, logs


def test_read_with_variants_is_the_union(tmp_path, monkeypatch):
    m, q = '{"MirrorChart": true}', '{"LiveQuality": 2}'
    sets = {("plan", None): ["live.json", "a.png"], ("full", None): ["live.json", "a.png"],
            ("plan", m): ["live.json", "b.png"], ("plan", q): RuntimeError("no plan"),
            ("full", q): ["live.json", "c.png", "a.png"]}
    rs, fake, logs = reads_with(monkeypatch, tmp_path / "p", ["plan", "settings"], sets)
    rs.expect(["x"])
    rs.need_settings()
    got = rs.read(tmp_path / "c", "c", [{"MirrorChart": True}, {"LiveQuality": 2}])
    assert got == ["live.json", "a.png", "b.png", "c.png"]                 # the default set first, as it was
    assert ("full", m) not in fake.calls and ("full", q) in fake.calls      # a failed variant plan: simulated
    s = rs.summary()
    assert s["variants"] == 2 and s["planErrors"][0]["settings"] == {"LiveQuality": 2} and len(logs) == 1
    assert rs.read(tmp_path / "c", "c") == ["live.json", "a.png"]


def test_variants_need_the_settings_feature(tmp_path, monkeypatch):
    rs, _, _ = reads_with(monkeypatch, tmp_path / "p", ["plan", "serve"], {})
    with pytest.raises(ConfigError, match="feature \"settings\""):
        rs.need_settings()
    rs.close()
    rs, _, _ = reads_with(monkeypatch, tmp_path / "p", [], {("full", None): ["live.json"]})
    assert "variants" not in rs.summary() and rs.read(tmp_path / "c", "c", ()) == ["live.json"]


# ---------------------------------------------------------------- manifests
def test_region_manifests_with_other_options_stay(tmp_path):
    site = tmp_path / "site"
    (site / "charts" / "en").mkdir(parents=True)
    files = {"live.json": {"asset": "assets/x.json", "size": 2}}
    shared = site / "charts" / "1_easy.json"
    shared.write_text(json.dumps({"files": files, "options": {"LiveQuality": [1, 2]}}), encoding="utf-8")
    var = site / "charts" / "en" / "1_easy.json"
    var.write_text(json.dumps({"files": files}), encoding="utf-8")
    assert not web.fold_variant(site, "1_easy", "en/") and var.is_file()
    var.write_text(json.dumps({"files": files, "options": {"LiveQuality": [1, 2]}, "regions": ["en"]}),
                   encoding="utf-8")
    assert web.fold_variant(site, "1_easy", "en/") and not var.exists()


def test_manifest_options(tmp_path):
    from test_live_options import write_master
    m = write_master(tmp_path / "m")
    assert resolve(parse_specs(["MirrorChart", "NoteDesignId"]), m).manifest_options() is None
    assert resolve(parse_specs(["LiveQuality=2"]), m).manifest_options() == {"LiveQuality": [1, 2]}
    assert LiveOptions().manifest_options() is None


# ---------------------------------------------------------------- the flag
def test_live_option_flag(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    from test_live_options import write_master as option_tables
    from test_regions import fake_player, write_master
    master = option_tables(write_master(tmp_path / "m"))
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", (tmp_path / "cdn").as_uri())
    base = ["--cache", str(tmp_path / "c"), "--region", "zz", "--master", str(master), "web", str(tmp_path / "s"),
            "--player", str(fake_player(tmp_path / "p"))]
    monkeypatch.setattr(web, "build", lambda *a, **k: pytest.fail("nothing may be built"))
    for extra, msg in ((["--pair", "1:expert", "--live-option", "Mirror"], "--live-option 'Mirror'"),
                       (["--pair", "1:expert", "--live-option", "NoteDesignId=9"], "NoteDesignId: 9 not in"),
                       (["--player-only", "--live-option", "all"], "applies to charts")):
        with pytest.raises(SystemExit) as e:
            cli.main(base + extra)
        assert e.value.code == 2 and msg in capsys.readouterr().err
    built = []
    monkeypatch.setattr(web, "build", lambda out, pairs, *a, **k: built.append(k["live_options"]) or
                        {"site": str(out), "built": [], "failed": [], "skipped": []})
    cli.main(base + ["--pair", "1:expert", "--live-option", "MirrorChart", "--live-option", "NoteDesignId=2"])
    cli.main(base + ["--pair", "1:expert"])
    capsys.readouterr()
    assert built == [{"MirrorChart": True, "NoteDesignId": [2]}, {}]
