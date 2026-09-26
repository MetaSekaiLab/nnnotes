"""Read sets by the player's plan mode, checked against the full simulation; the plans served by long-lived
processes when the player has the serve mode; ConfigError ends a pipeline run."""
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from nnnotes import web
from nnnotes.config import ConfigError


# ---------------------------------------------------------------- the player's modes
def node_answers(monkeypatch, answers, calls=None):
    """subprocess.run of web as a player script: `answers(args)` -> (return code, stdout)."""
    def run(args, **kw):
        if calls is not None:
            calls.append(args[2:])
        code, out = answers(args[2:])
        return subprocess.CompletedProcess(args, code, out, "usage" if code else "")
    monkeypatch.setattr(web, "tool", lambda name, exe: "node")
    monkeypatch.setattr(web.subprocess, "run", run)


@pytest.fixture
def player(tmp_path, monkeypatch):
    web._features.clear()
    monkeypatch.setattr(web, "player_id", lambda p: "player-code-1")      # a fixed sample of charts
    return tmp_path / "player"


def test_features_of_an_old_script_are_none(player, monkeypatch):
    calls = []
    node_answers(monkeypatch, lambda a: (2, ""), calls)
    assert web.player_features(player) == frozenset() == web.player_features(player)
    assert calls == [["--features"]]                           # asked once per player code


def test_features_listed(player, monkeypatch):
    node_answers(monkeypatch, lambda a: (0, '{"features": ["plan", "later"]}'))
    assert web.player_features(player) == {"plan", "later"}


def test_features_answer_must_be_the_documented_object(player, monkeypatch):
    node_answers(monkeypatch, lambda a: (0, '["live.json"]'))
    with pytest.raises(RuntimeError, match="features list"):
        web.player_features(player)


def test_read_set_plan_mode_arguments_and_answer(player, tmp_path, monkeypatch):
    calls = []
    node_answers(monkeypatch, lambda a: (0, '{"mode": "plan", "state": "planned", "files": ["a", "b"]}'
                                         if "--plan" in a else '{"state": "ended", "files": ["a"]}'), calls)
    assert web.read_set(tmp_path, player, "plan") == ["a", "b"]
    assert web.read_set(tmp_path, player) == ["a"]
    assert calls[0][-1] == "--plan" and calls[1][-1] == str(tmp_path)
    node_answers(monkeypatch, lambda a: (0, '{"state": "ended", "files": ["a"]}'))
    with pytest.raises(RuntimeError, match="plan mode"):             # a script that ignored --plan
        web.read_set(tmp_path, player, "plan")
    with pytest.raises(ValueError):
        web.read_set(tmp_path, player, "fast")


# ---------------------------------------------------------------- ReadSets
class FakeSets:
    """read_set_cached stand-in: {mode: {chart dir: files}}, calls recorded (and the modes read by a `run`)."""

    def __init__(self, plan, full):
        self.sets, self.calls, self.lock = {"plan": plan, "full": full}, [], threading.Lock()
        self.runs = []

    def __call__(self, live_dir, player_dir, mode="full", digest=None, run=None):
        assert digest == [(live_dir.name, b"")]
        with self.lock:
            self.calls.append((live_dir.name, mode))
            if run is not None:
                self.runs.append(mode)
        got = self.sets[mode][live_dir.name]
        if isinstance(got, Exception):
            raise got
        return list(got)


def reads_with(monkeypatch, player, features, plan, full, check_every=3):
    monkeypatch.setattr(web, "player_features", lambda p: frozenset(features))
    fake = FakeSets(plan, full)
    monkeypatch.setattr(web, "read_set_cached", fake)
    monkeypatch.setattr(web, "live_dir_digest", lambda d: [(d.name, b"")])      # hashed once per chart
    logs = []
    return web.ReadSets(player, log=logs.append, check_every=check_every), fake, logs


CHARTS = [f"{m}_expert" for m in range(1, 13)]


def test_without_plan_every_chart_is_simulated(player, monkeypatch, tmp_path):
    full = {c: [c, "live.json"] for c in CHARTS}
    rs, fake, _ = reads_with(monkeypatch, player, [], {}, full)
    assert "full simulation of every chart" in rs.describe()
    assert [rs.read(tmp_path / c, c) for c in CHARTS] == [full[c] for c in CHARTS]
    assert {m for _, m in fake.calls} == {"full"}
    assert rs.summary() == {"mode": "full", "plan": 0, "full": 12, "checked": 0, "mismatches": [], "planErrors": [],
                            "unchecked": 0}


def test_plan_with_first_and_sampled_charts_checked(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    plan = {c: [c, "live.json"] for c in CHARTS}               # the same sets (order is free)
    rs, fake, logs = reads_with(monkeypatch, player, ["plan"], plan, full)
    rs.expect(CHARTS)
    got = [rs.read(tmp_path / c, c) for c in CHARTS]
    assert [set(g) for g in got] == [set(full[c]) for c in CHARTS]
    sampled = {c for c in CHARTS if rs.sampled(c)}
    checked = {c for c, m in fake.calls if m == "full"}
    assert checked == sampled | {CHARTS[0]} and 0 < len(sampled) < len(CHARTS)
    s = rs.summary()
    assert s["mode"] == "plan" and s["checked"] == len(checked) and s["plan"] == len(CHARTS) - len(checked)
    assert s["unchecked"] == s["plan"] and s["mismatches"] == [] and logs == []


def test_the_checked_charts_do_not_depend_on_the_order_the_charts_come_in(player, monkeypatch, tmp_path):
    import random
    full = {c: ["live.json", c] for c in CHARTS}
    runs = []
    for seed in range(4):
        rs, fake, logs = reads_with(monkeypatch, player, ["plan"], {c: list(full[c]) for c in CHARTS}, full,
                                    check_every=1000)
        rs.expect(CHARTS)
        rs.expect(["9_easy"])                                  # a later group does not move the first chart
        order = CHARTS[:]
        random.Random(seed).shuffle(order)
        with ThreadPoolExecutor(6) as ex:
            got = dict(zip(order, ex.map(lambda c: rs.read(tmp_path / c, c), order)))
        assert got == {c: full[c] for c in CHARTS}
        runs.append((sorted(fake.calls), rs.summary()))
    assert all(r == runs[0] for r in runs)
    calls, s = runs[0]
    assert [c for c, m in calls if m == "full"] == [CHARTS[0]] and s["checked"] == 1 and s["plan"] == 11


def test_a_mismatch_of_the_first_chart_lists_the_charts_read_before_it(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    plan = dict({c: list(full[c]) for c in CHARTS}, **{CHARTS[0]: ["live.json"]})
    rs, fake, logs = reads_with(monkeypatch, player, ["plan"], plan, full, check_every=1000)
    rs.expect(CHARTS)
    early = [rs.read(tmp_path / c, c) for c in CHARTS[5:8]]            # extracted before the first chart
    with pytest.raises(web.ReadSetMismatch):
        rs.read(tmp_path / CHARTS[0], CHARTS[0])
    late = [rs.read(tmp_path / c, c) for c in CHARTS[1:3]]
    assert early == [full[c] for c in CHARTS[5:8]] and late == [full[c] for c in CHARTS[1:3]]
    s = rs.summary()
    assert s["unchecked"] == sorted(CHARTS[5:8]) and s["full"] == 2 and rs.mode == "full" and len(logs) == 1
    assert [(c, m) for c, m in fake.calls if c in CHARTS[1:3]] == [(CHARTS[1], "full"), (CHARTS[2], "full")]


def test_a_sampled_mismatch_fails_that_chart_and_the_rest_use_the_simulation(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    rs, fake, logs = reads_with(monkeypatch, player, ["plan"], {}, full)
    rs.expect(CHARTS)
    sampled = [c for c in CHARTS[1:] if rs.sampled(c)]
    bad = sampled[0]
    plan = {c: list(full[c]) for c in CHARTS}
    plan[bad] = ["live.json", bad, "extra.png"]
    fake.sets["plan"] = plan
    results = {}
    for c in CHARTS:
        try:
            results[c] = rs.read(tmp_path / c, c)
        except web.ReadSetMismatch as e:
            results[c] = e
    assert isinstance(results[bad], web.ReadSetMismatch) and "extra.png" in str(results[bad])
    after = CHARTS[CHARTS.index(bad) + 1:]
    assert all((c, "plan") not in fake.calls for c in after) and all(results[c] == full[c] for c in after)
    s = rs.summary()
    before_unchecked = [c for c in CHARTS[1:CHARTS.index(bad)] if not rs.sampled(c)]
    assert s["unchecked"] == sorted(before_unchecked) and s["mismatches"][0]["extra"] == ["extra.png"]
    assert s["full"] == len(after) and "differs" in logs[0]


def test_a_chart_the_plan_cannot_list_is_simulated_and_listed(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    plan = {c: list(full[c]) for c in CHARTS}
    rs, fake, logs = reads_with(monkeypatch, player, ["plan"], plan, full)
    unsampled = [c for c in CHARTS[1:] if not rs.sampled(c)]
    sampled = [c for c in CHARTS[1:] if rs.sampled(c)]
    for c in (unsampled[0], sampled[0]):
        plan[c] = RuntimeError(f"read set of {c} failed:\nunknown note kind 99")
    got = {c: rs.read(tmp_path / c, c) for c in CHARTS}
    assert got == full and rs.mode == "plan"
    s = rs.summary()
    assert [e["id"] for e in s["planErrors"]] == sorted([unsampled[0], sampled[0]], key=CHARTS.index)
    assert s["mismatches"] == [] and len(logs) == 2 and "unknown note kind 99" in logs[0]
    assert s["plan"] + s["full"] + s["checked"] == len(CHARTS) and s["full"] == 2


def test_a_first_chart_the_plan_cannot_list_is_simulated(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    plan = dict(full, **{CHARTS[0]: RuntimeError("failed")})
    rs, fake, _ = reads_with(monkeypatch, player, ["plan"], plan, full, check_every=1000)
    rs.expect(CHARTS)
    assert rs.read(tmp_path / CHARTS[0], CHARTS[0]) == full[CHARTS[0]]
    assert rs.read(tmp_path / CHARTS[1], CHARTS[1]) == full[CHARTS[1]] and (CHARTS[1], "full") not in fake.calls
    s = rs.summary()
    assert [e["id"] for e in s["planErrors"]] == [CHARTS[0]] and s["full"] == 1 and s["plan"] == 1


def test_the_read_set_key_includes_the_mode(tmp_path, monkeypatch):
    from nnnotes import cache
    cache.configure(enabled=True)
    live = tmp_path / "live"
    live.mkdir()
    (live / "live.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web, "player_id", lambda p: "p")
    calls = []
    monkeypatch.setattr(web, "read_set", lambda d, p, mode="full": calls.append(mode) or [mode])
    assert web.read_set_cached(live, tmp_path, "plan") == ["plan"]
    assert web.read_set_cached(live, tmp_path, "full") == ["full"]
    assert web.read_set_cached(live, tmp_path, "plan") == ["plan"] and calls == ["plan", "full"]


def test_a_read_set_run_by_a_server_is_stored_under_the_same_key(tmp_path, monkeypatch):
    from nnnotes import cache
    cache.configure(enabled=True)
    live = tmp_path / "live-served"
    live.mkdir()
    (live / "live.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web, "player_id", lambda p: "p")
    monkeypatch.setattr(web, "read_set", lambda d, p, mode="full": pytest.fail("spawned"))
    served = []
    assert web.read_set_cached(live, tmp_path, "plan", run=lambda d, p, mode: served.append(mode) or ["a"]) == ["a"]
    assert web.read_set_cached(live, tmp_path, "plan") == ["a"] and served == ["plan"]


# ---------------------------------------------------------------- the player's serve mode
# a stand-in for `read-set.mjs --serve`: answers {chart name, mode, pid}; the chart name selects a failure
FAKE_SERVER = r'''
import json, os, sys, time
flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crashed-once")
for line in sys.stdin:
    req = json.loads(line)
    name, mode = os.path.basename(req["chart"]), "plan" if req.get("plan") else "full"
    if name == "crash" or (name == "crash-once" and not os.path.exists(flag)):
        open(flag, "w").close()
        sys.stderr.write(f"crashed on {name}\n")
        sys.stderr.flush()
        sys.exit(3)
    if name == "hang":
        time.sleep(60)
    if name == "fail":
        print(json.dumps({"ok": False, "error": "no plan: effects created during the chart", "cpuSeconds": 0}))
    elif name == "garbage":
        print("not an answer")
    else:
        result = {"mode": mode, "state": "planned" if mode == "plan" else "ended", "frames": 0,
                  "files": [name, mode, str(os.getpid())]}
        print(json.dumps({"ok": True, "result": result, "cpuSeconds": 0}))
    sys.stdout.flush()
'''


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A player directory whose read-set script is FAKE_SERVER (run by this Python)."""
    player = tmp_path / "player"
    (player / "scripts").mkdir(parents=True)
    (player / web.READ_SET_SCRIPT).write_text(FAKE_SERVER, encoding="utf-8")
    monkeypatch.setattr(web, "tool", lambda name, exe: sys.executable)
    servers = web.ReadSetServers(player, timeout=5)
    yield servers
    servers.close()


def test_servers_answer_in_order_and_are_kept_for_later_charts(served, tmp_path):
    a = served.read_set(tmp_path / "a", None, "plan")
    b = served.read_set(tmp_path / "b", None, "full")
    assert a[:2] == ["a", "plan"] and b[:2] == ["b", "full"] and a[2] == b[2]         # one process
    assert served.started == 1
    with pytest.raises(RuntimeError, match="no plan: effects created"):
        served.read_set(tmp_path / "fail", None, "plan")
    assert served.read_set(tmp_path / "c", None, "plan")[2] == a[2] and served.started == 1
    with pytest.raises(ValueError):
        served.read_set(tmp_path / "c", None, "fast")


def test_servers_one_per_concurrent_read(served, tmp_path):
    with ThreadPoolExecutor(3) as ex:
        got = list(ex.map(lambda i: served.read_set(tmp_path / f"c{i}", None, "plan"), range(12)))
    assert [g[0] for g in got] == [f"c{i}" for i in range(12)]
    assert 1 <= served.started <= 3 and len({g[2] for g in got}) == served.started


def test_a_server_that_exits_is_replaced_and_the_chart_asked_again_once(served, tmp_path):
    first = served.read_set(tmp_path / "a", None, "plan")[2]
    again = served.read_set(tmp_path / "crash-once", None, "plan")
    assert again[:2] == ["crash-once", "plan"] and again[2] != first and served.started == 2
    with pytest.raises(RuntimeError, match=r"exited \(3\)[\s\S]*crashed on crash"):
        served.read_set(tmp_path / "crash", None, "plan")
    assert served.started == 3
    with pytest.raises(RuntimeError, match="answered 'not an answer"):
        served.read_set(tmp_path / "garbage", None, "plan")
    assert served.read_set(tmp_path / "b", None, "plan")[0] == "b"


def test_a_server_that_does_not_answer_in_time_is_ended_and_the_chart_fails(served, tmp_path):
    served.timeout = 0.5
    with pytest.raises(RuntimeError, match="no answer within"):
        served.read_set(tmp_path / "hang", None, "plan")
    assert served.started == 1 and served.idle == []
    assert served.read_set(tmp_path / "a", None, "plan")[0] == "a" and served.started == 2


def test_closed_servers_have_exited(served, tmp_path):
    served.read_set(tmp_path / "a", None, "plan")
    (s,) = served.idle
    served.close()
    assert s.proc.poll() == 0 and served.idle == []
    with pytest.raises(RuntimeError, match="closed"):
        served.read_set(tmp_path / "b", None, "plan")


def test_with_the_serve_mode_only_plans_are_served(player, monkeypatch, tmp_path):
    full = {c: ["live.json", c] for c in CHARTS}
    rs, fake, _ = reads_with(monkeypatch, player, ["plan", "serve"], {c: list(full[c]) for c in CHARTS}, full)
    rs.expect(CHARTS)
    assert [rs.read(tmp_path / c, c) for c in CHARTS] == [full[c] for c in CHARTS]
    assert "(served)" in rs.describe() and isinstance(rs.servers, web.ReadSetServers)
    assert fake.runs == ["plan"] * len(CHARTS)                  # the full simulations run in processes of their own
    assert {m for _, m in fake.calls} == {"plan", "full"}
    rs.close()
    assert rs.servers is None
    rs2, fake2, _ = reads_with(monkeypatch, player, ["plan"], {c: list(full[c]) for c in CHARTS}, full)
    rs2.read(tmp_path / CHARTS[1], CHARTS[1])
    assert rs2.servers is None and fake2.runs == [] and "(served)" not in rs2.describe()


# ---------------------------------------------------------------- ConfigError ends the run
def test_config_error_stops_the_pipeline_after_the_stages_in_flight():
    started, cleaned = [], []
    lock = threading.Lock()

    def extract(m, ds):
        with lock:
            started.append(m)
        if m == 2:
            raise ConfigError("setting bundle.key is not set")
        return {"music": m, "root": f"root{m}", "dirs": {d: (f"{m}/{d}", {}) for d in ds}, "extractSec": 0}

    def ingest(m, charts, root):
        return [{"id": f"{m}_{d}", "ok": True} for d, *_ in charts]

    with ThreadPoolExecutor(1) as w, ThreadPoolExecutor(2) as r:
        p = web.Pipeline(w, r, extract, lambda pdir, cid: [pdir], ingest, cleaned.append, window=2,
                         log=lambda m: None)
        with pytest.raises(ConfigError, match="bundle.key"):
            p.run([(m, ["easy"]) for m in range(1, 9)])
    assert max(started) <= 3                                   # no music started after the error
    assert set(cleaned) == {f"root{m}" for m in started if m != 2}


def test_config_error_from_a_reader_or_ingest_also_ends_the_run():
    def extract(m, ds):
        return {"music": m, "root": f"root{m}", "dirs": {d: (f"{m}/{d}", {}) for d in ds}, "extractSec": 0}

    def read(pdir, cid):
        raise ConfigError("setting paths.node: not found")

    with ThreadPoolExecutor(2) as w, ThreadPoolExecutor(2) as r:
        with pytest.raises(ConfigError, match="paths.node"):
            web.Pipeline(w, r, extract, read, lambda *a: [], lambda root: None, window=2,
                         log=lambda m: None).run([(m, ["easy"]) for m in range(1, 5)])

    def ingest(m, charts, root):
        raise ConfigError("setting paths.ffmpeg: not found")

    with ThreadPoolExecutor(2) as w, ThreadPoolExecutor(2) as r:
        with pytest.raises(ConfigError, match="paths.ffmpeg"):
            web.Pipeline(w, r, extract, lambda pdir, cid: [pdir], ingest, lambda root: None, window=2,
                         log=lambda m: None).run([(m, ["easy"]) for m in range(1, 5)])


def test_ingest_task_passes_config_errors_through(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ingest", lambda *a, **k: (_ for _ in ()).throw(ConfigError("setting paths.ffmpeg")))
    cfg = {"site": str(tmp_path), "audioFormat": "aac", "audio": True, "language": "en"}
    with pytest.raises(ConfigError):
        web.ingest_task(1, [("easy", str(tmp_path), {}, [])], str(tmp_path), cfg)
    monkeypatch.setattr(web, "ingest", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad file")))
    (r,) = web.ingest_task(1, [("easy", str(tmp_path), {}, [])], str(tmp_path), cfg)
    assert r["stage"] == "ingest" and not r["ok"]


def test_web_read_workers_flag_reaches_the_build(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    calls = []
    monkeypatch.setattr(web, "build", lambda out, pairs, cfg, player, fmt, **kw: calls.append(kw) or
                        {"site": str(out), "built": [], "failed": [], "skipped": []})
    monkeypatch.setattr(web, "unknown_pairs", lambda cfg, pairs, regions=None: [])
    player = tmp_path / "p"
    for rel in (web.READ_SET_SCRIPT, *web.PLAYER_BUNDLES, f"{web.PLAYER_PAGE_DIR}/index.html"):
        (player / rel).parent.mkdir(parents=True, exist_ok=True)
        (player / rel).write_text("x", encoding="utf-8")
    cli.main(["web", str(tmp_path / "s"), "--player", str(player), "--pair", "7:hard", "--read-workers", "24"])
    cli.main(["web", str(tmp_path / "s"), "--player", str(player), "--pair", "7:hard"])
    capsys.readouterr()
    assert [kw["read_workers"] for kw in calls] == [24, None]


def test_web_pair_that_no_region_has_is_a_usage_error(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    from test_regions import fake_player, write_master
    master = write_master(tmp_path / "m")                     # music 1: easy and expert
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", (tmp_path / "cdn").as_uri())
    monkeypatch.setattr(web, "build", lambda *a, **k: pytest.fail("nothing may be built"))
    base = ["--cache", str(tmp_path / "c"), "--region", "zz", "--master", str(master), "web", str(tmp_path / "s"),
            "--player", str(fake_player(tmp_path / "p"))]
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--pair", "1:expert", "--pair", "1:hard", "--pair", "9_easy", "--pair", "1_hard"])
    err = capsys.readouterr().err
    assert e.value.code == 2 and "charts 1:hard, 9:easy: no MasterLiveMusicScore row" in err
    assert "1:expert" not in err
    with pytest.raises(SystemExit) as e:
        cli.main(base + ["--pair", "2:easy"])
    assert e.value.code == 2 and "chart 2:easy: no MasterLiveMusicScore row" in capsys.readouterr().err
    built = []
    monkeypatch.setattr(web, "build", lambda out, pairs, *a, **k: built.append(pairs) or
                        {"site": str(out), "built": [], "failed": [], "skipped": []})
    cli.main(base + ["--pair", "1:expert", "--pair", "1:easy"])
    capsys.readouterr()
    assert built == [[(1, "expert"), (1, "easy")]]


def test_unknown_pairs_over_the_regions_of_the_site(tmp_path):
    from test_regions import config, write_master
    tw = write_master(tmp_path / "tw")
    en = write_master(tmp_path / "en", extra_music={"_hardID": 11})    # en also has 1:hard
    cfg = config(tmp_path, {"tw": {"cdn": "x", "master": str(tw)}, "en": {"cdn": "x", "master": str(en)}},
                 catalog={"region": "tw"})
    assert web.unknown_pairs(cfg, [(1, "hard"), (1, "easy")]) == [(1, "hard")]
    assert web.unknown_pairs(cfg, [(1, "hard"), (1, "normal")], ["tw", "en"]) == [(1, "normal")]
    (tw / "MasterLiveMusic.json").unlink()
    with pytest.raises(web.ConfigError, match="no MasterLiveMusic.json"):
        web.unknown_pairs(cfg, [(1, "easy")])
