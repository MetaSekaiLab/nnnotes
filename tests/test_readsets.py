"""Read sets by the player's plan mode, checked against the full simulation; ConfigError ends a pipeline run."""
import subprocess
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
    """read_set_cached stand-in: {mode: {chart dir: files}}, calls recorded."""

    def __init__(self, plan, full):
        self.sets, self.calls, self.lock = {"plan": plan, "full": full}, [], threading.Lock()

    def __call__(self, live_dir, player_dir, mode="full", digest=None):
        assert digest == [(live_dir.name, b"")]
        with self.lock:
            self.calls.append((live_dir.name, mode))
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
