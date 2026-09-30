import gzip
import hashlib
import json

import pytest

from nnnotes import compress, web
from nnnotes.config import ConfigError

DOC = web._minify({"rows": [{"id": i, "name": f"row {i}", "tags": ["a", "b"]} for i in range(200)]}).encode()


def test_text_asset_minifies_and_drops_doc_fields():
    doc = {"spec": "notes for readers", "music": {"soundId": 1}, "voice": {"rule": "r", "x": 1}, "v": 1e999}
    out = web.text_asset("audio/live-audio.json", json.dumps(doc, indent=2).replace("Infinity", "1e999"))
    assert out == b'{"music":{"soundId":1},"voice":{"x":1},"v":1e999}'
    glsl = "#ifdef VERTEX\nvoid main() {}\n#endif\n"
    assert web.text_asset("shaders/a.glsl", glsl) == glsl.encode("utf-8")


def test_split_json_rejoins():
    assert web.split_json(b'{"a":1,"b":2}') is None
    big = web._minify({"a": "x" * (web.SPLIT_MIN_BYTES // 2), "b": ["y"] * 200000, "c": {"d": 1}}).encode()
    parts = web.split_json(big)
    assert [k for k, _ in parts] == ["a", "b", "c"]
    assert web.join_parts(parts) == big
    odd = web._minify({"a b": "x" * web.SPLIT_MIN_BYTES, "c": 1}).encode()
    assert web.split_json(odd) is None


def test_store_is_content_addressed(tmp_path):
    store = web.Store(tmp_path)
    e = store.put("x/data.json", b"{}")
    assert e == {"asset": f"assets/{hashlib.sha256(b'{}').hexdigest()}.json", "size": 2}
    assert store.put("y/other.json", b"{}") == e
    assert (store.written, store.reused) == (1, 1)
    assert store.put("noext", b"z")["asset"].endswith(".bin")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_store_encodes_compressible_files_when_smaller(tmp_path):
    store = web.Store(tmp_path)
    e = store.put("x/doc.json", DOC)
    assert e == {"asset": f"assets/{sha(DOC)}.json.gz", "size": len(DOC), "stored": len(compress.gzip_bytes(DOC))}
    assert e["stored"] < e["size"]
    assert (tmp_path / e["asset"]).read_bytes() == compress.gzip_bytes(DOC)
    assert web.read_asset(tmp_path, e["asset"]) == DOC
    shader = b"void main() { gl_FragColor = vec4(1.0); }\n" * 20
    assert store.put("s/a.glsl", shader)["asset"] == f"assets/{sha(shader)}.glsl.gz"
    assert store.put("x/tiny.json", b"{}") == {"asset": f"assets/{sha(b'{}')}.json", "size": 2}   # gzip not smaller
    assert store.put("t/a.png", DOC) == {"asset": f"assets/{sha(DOC)}.png", "size": len(DOC)}   # not compressible
    with pytest.raises(ValueError, match="asset encoding"):
        web.Store(tmp_path, "zstd")


def test_store_brotli(tmp_path):
    pytest.importorskip("brotli")
    e = web.site_store(tmp_path, "br").put("m/model.moc3", DOC)
    assert e == {"asset": f"assets/{sha(DOC)}.moc3.br", "size": len(DOC), "stored": len(compress.brotli_bytes(DOC))}
    assert web.read_asset(tmp_path, e["asset"]) == DOC


def test_store_none_keeps_todays_names_and_bytes(tmp_path):
    store = web.site_store(tmp_path, "none")
    assert store.put("x/doc.json", DOC) == {"asset": f"assets/{sha(DOC)}.json", "size": len(DOC)}
    assert [p.name for p in (tmp_path / "assets").iterdir()] == [f"{sha(DOC)}.json"]
    assert (tmp_path / "assets" / f"{sha(DOC)}.json").read_bytes() == DOC


def test_store_reuses_encoded_names(tmp_path):
    store = web.Store(tmp_path)
    e = store.put("a.json", DOC)
    assert store.put("b.json", DOC) == e and (store.written, store.reused) == (1, 1)
    again = web.Store(tmp_path)
    assert again.put("c.json", DOC) == e and (again.written, again.reused) == (0, 1)
    # a file of that name that holds the bytes is kept whatever encoder wrote it; its length is the entry's `stored`
    other = gzip.compress(DOC, 1, mtime=0)
    (tmp_path / e["asset"]).write_bytes(other)
    kept = web.Store(tmp_path).put("d.json", DOC)
    assert kept == {**e, "stored": len(other)} and (tmp_path / e["asset"]).read_bytes() == other
    (tmp_path / e["asset"]).write_bytes(other[:-5])                  # damaged: written again
    fresh = web.Store(tmp_path)
    assert fresh.put("e.json", DOC) == e and fresh.written == 1
    assert (tmp_path / e["asset"]).read_bytes() == compress.gzip_bytes(DOC)


def test_split_parts_carry_stored(tmp_path):
    big = web._minify({"a": "x" * (web.SPLIT_MIN_BYTES // 2), "b": ["y"] * 200000, "c": {"d": 1}}).encode()
    e = web.Store(tmp_path).put_file("scene.json", big)
    assert e["size"] == len(big) and [p[0] for p in e["parts"]] == ["a", "b", "c"]
    a, b, c = e["parts"]
    small = b'{"d":1}'
    assert len(a) == len(b) == 4 and a[1].endswith(".json.gz") and a[3] < a[2]
    assert c == ["c", f"assets/{sha(small)}.json", len(small)]        # a part gzip does not make smaller
    assert web.entry_text(tmp_path, e) == big
    raw = web.Store(tmp_path / "raw", "none").put_file("scene.json", big)
    assert all(len(p) == 3 for p in raw["parts"]) and web.entry_text(tmp_path / "raw", raw) == big


def test_stored_text_memo_follows_the_encoding(tmp_path):
    doc = json.dumps({"k": list(range(300))})
    a = web.stored_text(web.Store(tmp_path), "x/a.json", doc, memo="m")
    b = web.stored_text(web.Store(tmp_path, "none"), "x/a.json", doc, memo="m")
    assert a["asset"].endswith(".json.gz") and b == {"asset": a["asset"][:-3], "size": a["size"]}


def test_ingest_task_stores_in_the_jobs_encoding(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "ingest", lambda store, *a, **k: seen.append(store.encoding) or {"id": "1_easy"})
    cfg = {"site": str(tmp_path), "audioFormat": "aac", "audio": True, "language": "en"}
    web.ingest_task(1, [("easy", str(tmp_path), {}, [])], str(tmp_path), {**cfg, "encoding": "none"})
    web.ingest_task(1, [("easy", str(tmp_path), {}, [])], str(tmp_path), cfg)
    assert seen == ["none", "gzip"]


def manifest(site_dir, music_id, difficulty, store, files):
    entries = {p: store.put(p, data) for p, data in files.items()}
    doc = {"musicId": music_id, "difficulty": difficulty, "audio": True, "audioFormat": "aac", "flows": ["direct"],
           "files": entries, "chart": {"title": f"t{music_id}", "level": 1}}
    (site_dir / "charts" / f"{music_id}_{difficulty}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_write_index_orders_charts_and_prunes_assets(tmp_path):
    (tmp_path / "charts").mkdir()
    store = web.Store(tmp_path)
    manifest(tmp_path, 2, "easy", store, {"live.json": b"{}"})
    manifest(tmp_path, 1, "expert", store, {"live.json": b"{}", "score/a.json": b"[1]"})
    manifest(tmp_path, 1, "easy", store, {"live.json": b"{}"})
    stale = store.put("old.json", b"[0]")
    r = web.write_index(tmp_path)
    assert (r["charts"], r["assets"], r["removedAssets"], r["assetBytes"], r["bytes"]) == (3, 2, 1, 5, 5)
    assert not (tmp_path / stale["asset"]).exists()
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert index["format"] == web.SITE_FORMAT
    assert [c["id"] for c in index["charts"]] == ["1_easy", "1_expert", "2_easy"]
    assert index["charts"][1]["bytes"] == 5 and index["charts"][1]["title"] == "t1"


def encoded_site(site):
    """Two charts sharing an encoded file, a raw file each, and a stale encoded asset."""
    (site / "charts").mkdir(parents=True)
    store = web.Store(site)
    manifest(site, 1, "easy", store, {"live.json": b"{}", "scene/a.json": DOC})
    manifest(site, 1, "hard", store, {"live.json": b"[1]", "scene/a.json": DOC})
    return store.put("old.json", DOC + b" ")


def test_write_index_prunes_encoded_names(tmp_path):
    stale = encoded_site(tmp_path)
    assert stale["asset"].endswith(".json.gz")
    r = web.write_index(tmp_path)
    assert (r["charts"], r["assets"], r["removedAssets"]) == (2, 3, 1)
    assert not (tmp_path / stale["asset"]).exists()
    names = sorted(p.name for p in (tmp_path / "assets").iterdir())
    assert names == sorted([f"{sha(DOC)}.json.gz", f"{sha(b'{}')}.json", f"{sha(b'[1]')}.json"])
    assert (r["assetBytes"], r["bytes"]) == (len(compress.gzip_bytes(DOC)) + 2 + 3, len(DOC) + 2 + 3)
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert [c["bytes"] for c in index["charts"]] == [len(DOC) + 2, len(DOC) + 3]     # decoded sizes


def fake_player(root):
    for rel, text in {web.READ_SET_SCRIPT: "// read set\n", web.PLAYER_BUNDLES[0]: "/* bundle */\n",
                      f"{web.PLAYER_PAGE_DIR}/index.html":
                          '<script type="module" src="./chart-list.js"></script><!-- @PLAYER_VERSION@ -->\n',
                      f"{web.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n'}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def test_check_player(tmp_path):
    with pytest.raises(ConfigError, match="NNNOTES_PATHS_PLAYER"):
        web.check_player(None)
    with pytest.raises(ConfigError, match="not a built ournotes-player"):
        web.check_player(tmp_path)
    assert web.check_player(fake_player(tmp_path / "p")) == (tmp_path / "p").resolve()


def test_write_player(tmp_path):
    player = fake_player(tmp_path / "p")
    out = tmp_path / "site"
    r = web.write_player(out, player)
    version = hashlib.sha256(b"/* bundle */\n").hexdigest()[:16]
    assert r["playerVersion"] == version and r["pageFiles"] == 2
    assert (out / "chart-list.js").read_text(encoding="utf-8") == 'import "./ournotes-player.element.min.js";\n'
    assert version in (out / "index.html").read_text(encoding="utf-8")
    assert (out / "ournotes-player.element.min.js").is_file()
    (player / web.PLAYER_PAGE_DIR / "extra.js").write_bytes(b'import "../../src/other.js";\n')
    with pytest.raises(RuntimeError, match="beyond PAGE_IMPORTS"):
        web.write_player(out, player)


def test_write_player_copies_the_complete_songs_module_tree(tmp_path):
    player = fake_player(tmp_path / "p")
    files = {"index.html": b'<script type="module" src="./songs.js"></script>',
             "songs.js": b'import "./catalog.js"; import "./replay-panel.js";',
             "catalog.js": b"export const catalog = {};", "text.js": b"export const text = {};",
             "replay-panel.js": b'import "./replay-preset.js";',
             "replay-worker.js": b"// worker", "replay-preset.js": b"// preset",
             "nested/future-dependency.json": b"{}"}
    for name, data in files.items():
        path = player / web.SONGS_PAGE_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    site = tmp_path / "site"
    result = web.write_player(site, player)
    assert result["songsPageFiles"] == len(files)
    assert {path.relative_to(site / "songs").as_posix(): path.read_bytes()
            for path in (site / "songs").rglob("*") if path.is_file()} == files


def test_player_only_command(tmp_path, capsys):
    from nnnotes import cli
    out = tmp_path / "site"
    (out / "assets").mkdir(parents=True)
    (out / "assets" / "unreferenced.json").write_bytes(b"{}")
    cli.main(["web", str(out), "--player", str(fake_player(tmp_path / "p")), "--player-only"])
    r = json.loads(capsys.readouterr().out)
    assert (r["charts"], r["removedAssets"], r["pageFiles"]) == (0, 1, 2)
    assert json.loads((out / "charts.json").read_text(encoding="utf-8")) == {"charts": [], "format": web.SITE_FORMAT}


def test_player_only_and_reingest_over_an_encoded_site(tmp_path, capsys):
    from nnnotes import cli
    site, player = tmp_path / "site", str(fake_player(tmp_path / "p"))
    encoded_site(site)
    before = (site / "charts" / "1_easy.json").read_bytes()
    cli.main(["web", str(site), "--player", player, "--player-only"])
    r = json.loads(capsys.readouterr().out)
    assert (r["charts"], r["assets"], r["removedAssets"]) == (2, 3, 1)
    cli.main(["web", str(site), "--player", player, "--reingest-json"])
    r = json.loads(capsys.readouterr().out)
    assert (r["changedEntries"], r["assets"], r["removedAssets"]) == (0, 3, 0)
    assert json.loads((site / "charts" / "1_easy.json").read_bytes()) == json.loads(before)
    cli.main(["web", str(site), "--player", player, "--reingest-json", "--compress", "none"])
    r = json.loads(capsys.readouterr().out)
    assert (r["changedEntries"], r["assets"], r["removedAssets"]) == (2, 3, 1)
    man = json.loads((site / "charts" / "1_easy.json").read_text(encoding="utf-8"))
    assert man["files"]["scene/a.json"] == {"asset": f"assets/{sha(DOC)}.json", "size": len(DOC)}
    assert r["assetBytes"] == r["bytes"] == len(DOC) + 2 + 3


def test_player_only_on_a_new_directory(tmp_path, capsys):
    from nnnotes import cli
    out = tmp_path / "new-site"
    cli.main(["web", str(out), "--player", str(fake_player(tmp_path / "p")), "--player-only"])
    r = json.loads(capsys.readouterr().out)
    assert (r["charts"], r["assets"], r["removedAssets"]) == (0, 0, 0)
    assert (out / "index.html").is_file() and (out / "charts.json").is_file()


@pytest.mark.parametrize("failed, code", [([], 0), ([{"id": "1_easy", "stage": "live", "error": "x"}], 1)])
def test_web_exit_status_follows_failed_charts(tmp_path, capsys, monkeypatch, failed, code):
    from nnnotes import cli
    calls = []

    def fake_build(out, pairs, cfg, player, fmt, **kw):
        calls.append((pairs, fmt, kw))
        return {"site": str(out), "built": [], "failed": failed, "skipped": []}

    monkeypatch.setattr(web, "build", fake_build)
    monkeypatch.setattr(web, "unknown_pairs", lambda cfg, pairs, regions=None: [])   # no master data here
    exit_code = 0
    try:
        cli.main(["web", str(tmp_path / "s"), "--player", str(fake_player(tmp_path / "p")),
                  "--pair", "7:hard", "--pair", "7_easy", "--format", "opus", "--no-audio", "--workers", "1"])
    except SystemExit as e:
        exit_code = e.code
    assert exit_code == code
    assert json.loads(capsys.readouterr().out)["failed"] == failed
    ((pairs, fmt, kw),) = calls
    assert pairs == [(7, "hard"), (7, "easy")] and fmt == "opus"
    assert kw["audio"] is False and kw["workers"] == 1 and kw["force"] is False and kw["encoding"] == "gzip"


def test_web_compress_flag_reaches_the_build(tmp_path, capsys, monkeypatch):
    from nnnotes import cli
    calls = []

    def fake_build(out, pairs, cfg, player, fmt, **kw):
        calls.append(kw["encoding"])
        return {"site": str(out), "built": [], "failed": [], "skipped": []}

    monkeypatch.setattr(web, "build", fake_build)
    monkeypatch.setattr(web, "unknown_pairs", lambda cfg, pairs, regions=None: [])
    player = str(fake_player(tmp_path / "p"))
    for flag in ("br", "none"):
        cli.main(["web", str(tmp_path / "s"), "--player", player, "--pair", "7:hard", "--compress", flag])
    assert calls == ["br", "none"]
    with pytest.raises(SystemExit):
        cli.main(["web", str(tmp_path / "s"), "--player", player, "--pair", "7:hard", "--compress", "zstd"])
    capsys.readouterr()
