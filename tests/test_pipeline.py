"""The web build's stage pipeline and ingest memos (synthetic stages; no game data, no Node)."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from nnnotes import cache, web


def run(musics, extract, read, ingest, window=2, workers=2, readers=3):
    cleaned, peak, active = [], [0], set()
    lock = threading.Lock()

    def ex(m, ds):
        with lock:
            active.add(m)
            peak[0] = max(peak[0], len(active))
        return extract(m, ds)

    def ing(m, charts, root):
        try:
            return ingest(m, charts, root)
        finally:
            with lock:
                active.discard(m)

    with ThreadPoolExecutor(workers) as w, ThreadPoolExecutor(readers) as r:
        out = web.Pipeline(w, r, ex, read, ing, cleaned.append, window, log=lambda m: None).run(musics)
    return out, cleaned, peak[0]


def extract_ok(m, ds):
    time.sleep(0.001 * (m % 3))
    return {"music": m, "root": f"root{m}", "dirs": {d: (f"{m}/{d}", {"s": d}) for d in ds}, "extractSec": 0.1}


def ingest_ok(m, charts, root):
    assert root == f"root{m}"
    return [{"id": f"{m}_{d}", "ok": True, "files": len(files), "dir": pdir} for d, pdir, summary, files in charts]


def test_pipeline_runs_every_chart_within_the_window():
    musics = [(m, ["easy", "hard"]) for m in range(1, 9)]
    out, cleaned, peak = run(musics, extract_ok, lambda pdir, cid: [pdir, "live.json"], ingest_ok, window=3)
    assert sorted(r["id"] for r in out) == sorted(f"{m}_{d}" for m, ds in musics for d in ds)
    assert all(r["ok"] and r["files"] == 2 and r["music"] == int(r["id"].split("_")[0]) for r in out)
    assert sorted(cleaned) == sorted(f"root{m}" for m, _ in musics)
    assert peak <= 3


def test_pipeline_failures_per_stage():
    def extract(m, ds):
        if m == 2:
            raise RuntimeError("no bundle")
        return extract_ok(m, ds)

    def read(pdir, cid):
        assert cid == pdir.replace("/", "_")
        if pdir == "3/hard":
            raise RuntimeError("node failed")
        return [pdir]

    def ingest(m, charts, root):
        if m == 4:
            raise ValueError("disk full")
        return ingest_ok(m, charts, root)

    out, cleaned, _ = run([(m, ["easy", "hard"]) for m in range(1, 5)], extract, read, ingest)
    by = {r["id"]: r for r in out}
    assert len(by) == 8
    assert by["2_easy"]["stage"] == by["2_hard"]["stage"] == "extract" and not by["2_easy"]["ok"]
    assert by["3_easy"]["ok"] and by["3_hard"]["stage"] == "read set"
    assert by["4_easy"]["stage"] == "ingest" and "disk full" in by["4_easy"]["error"]
    assert by["1_easy"]["ok"] and by["1_hard"]["ok"]
    assert "root2" not in cleaned and {"root1", "root3", "root4"} <= set(cleaned)


def test_bgm_options():
    assert web.bgm_options("aac", False) == {"flac_level": 0}
    assert web.bgm_options("flac", True) == {}
    assert web.bgm_options("aac", True) == {"flac_level": 0, "also": web.WEB_AUDIO["aac"]}


def test_stored_text_memo_matches_put_file(tmp_path):
    cache.configure(enabled=True)
    store = web.Store(tmp_path / "site")
    doc = json.dumps({"a": [1, 2.5, "x"], "b": {"c": 1e999}}, indent=1).replace("Infinity", "1e999")
    direct = web.Store(tmp_path / "ref").put_file("x/a.json", web.text_asset("x/a.json", doc))
    e1 = web.stored_text(store, "x/a.json", doc, memo="b1")
    e2 = web.stored_text(web.Store(tmp_path / "site"), "x/a.json", doc, memo="b1")
    assert e1 == e2 == direct and web.stored_text(store, "x/a.json", doc) == direct
    assert (tmp_path / "site" / e1["asset"]).read_bytes() == (tmp_path / "ref" / direct["asset"]).read_bytes()


def test_store_remembers_names(tmp_path):
    s = web.Store(tmp_path)
    a = s.put("a.png", b"123")
    (tmp_path / a["asset"]).unlink()           # a name this store wrote is not looked up again
    assert s.put("b.png", b"123") == a and (s.written, s.reused) == (1, 1)
    assert web.Store(tmp_path).put("c.png", b"123") == a and (tmp_path / a["asset"]).is_file()


def test_read_set_cached(tmp_path, monkeypatch):
    cache.configure(enabled=True, directory=tmp_path / "c")
    live = tmp_path / "live"
    (live / "sub").mkdir(parents=True)
    (live / "live.json").write_text("{}", encoding="utf-8")
    (live / "sub" / "x.png").write_bytes(b"png")
    calls = []
    monkeypatch.setattr(web, "player_id", lambda p: "player-1")
    monkeypatch.setattr(web, "read_set", lambda d, p, mode="full": calls.append(d) or ["live.json"])
    assert web.read_set_cached(live, tmp_path) == ["live.json"]
    cache.clear()
    assert web.read_set_cached(live, tmp_path) == ["live.json"] and len(calls) == 1      # from the disk layer
    (live / "sub" / "x.png").write_bytes(b"png2")
    web.read_set_cached(live, tmp_path)
    assert len(calls) == 2
    monkeypatch.setattr(web, "player_id", lambda p: "player-2")
    web.read_set_cached(live, tmp_path)
    assert len(calls) == 3
    cache.configure(directory="")


def test_web_audio_takes_the_encoding_made_with_the_decode(tmp_path, monkeypatch):
    live = tmp_path / "live"
    (live / "audio" / "S").mkdir(parents=True)
    (live / "audio" / "S" / "m.m4a").write_bytes(b"pre-encoded")
    la = {"music": {"soundId": 5}, "sounds": {"5": {"layers": [{"file": "audio/S/m.flac", "samples": 10}]}}}
    idx = {"liveAudio": "audio/live-audio.json", "audio": {"file": "audio/S/m.flac", "cues": {"m": "audio/S/m.flac"}}}
    text = {"live.json": json.dumps(idx), "audio/live-audio.json": json.dumps(la)}
    binary = {"audio/S/m.flac": b"fLaC..."}
    monkeypatch.setattr(web, "transcode", lambda *a: pytest.fail("transcoded"))
    monkeypatch.setattr(web, "mp4_priming", lambda data: 1024)
    web._web_audio(live, text, binary, "aac", tmp_path, {})
    assert binary == {"audio/S/m.m4a": b"pre-encoded"}
    assert json.loads(text["audio/live-audio.json"])["sounds"]["5"]["layers"][0] == {
        "file": "audio/S/m.m4a", "samples": 10, "encoderDelay": 1024}
    assert json.loads(text["live.json"])["audio"] == {"file": "audio/S/m.m4a", "cues": {"m": "audio/S/m.m4a"}}


def test_catalog_cached(tmp_path):
    from nnnotes.catalog import Bundle, Catalog
    import synth
    cat = Catalog(synth.CatalogWriter().build([("k", synth.remote("b_1.bundle"), [])]), tmp_path)
    b = Bundle(1, synth.remote("b_1.bundle"), "b_1.bundle", True)
    assert cat.cached(b) is None
    (tmp_path / "bundles").mkdir()
    (tmp_path / "bundles" / "b_1.bundle").write_bytes(b"UnityFS\0x")
    assert cat.cached(b) == tmp_path / "bundles" / "b_1.bundle" == cat.fetch(b)
    e = {"internal_id": synth.remote("x/raw_1")}
    assert cat.cached_raw(e) is None and cat.cached_raw({"internal_id": "Assets/x"}) is None
    (tmp_path / "raw" / "asset" / "Android" / "x").mkdir(parents=True)
    (tmp_path / "raw" / "asset" / "Android" / "x" / "raw_1").write_bytes(b"@UTF")
    assert cat.cached_raw(e) == cat.fetch_raw(e)


def test_pipeline_window_keeps_the_read_set_slots_busy():
    assert web.pipeline_window(12, 14) == 16            # 12 extracting + 4 musics (16 charts) waiting for read sets
    assert web.pipeline_window(4, 22) == 10
    assert web.pipeline_window(1, 1) == 3               # at least 2 beyond the extraction processes
    assert web.pipeline_window(8, 16) == 12
