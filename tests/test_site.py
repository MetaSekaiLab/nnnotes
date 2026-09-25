import hashlib
import json

import pytest

from nnnotes import site
from nnnotes.config import ConfigError


def test_text_asset_minifies_and_drops_doc_fields():
    doc = {"spec": "notes for readers", "music": {"soundId": 1}, "voice": {"rule": "r", "x": 1}, "v": 1e999}
    out = site.text_asset("audio/live-audio.json", json.dumps(doc, indent=2).replace("Infinity", "1e999"))
    assert out == b'{"music":{"soundId":1},"voice":{"x":1},"v":1e999}'
    glsl = "#ifdef VERTEX\nvoid main() {}\n#endif\n"
    assert site.text_asset("shaders/a.glsl", glsl) == glsl.encode("utf-8")


def test_split_json_rejoins():
    assert site.split_json(b'{"a":1,"b":2}') is None
    big = site._minify({"a": "x" * (site.SPLIT_MIN_BYTES // 2), "b": ["y"] * 200000, "c": {"d": 1}}).encode()
    parts = site.split_json(big)
    assert [k for k, _ in parts] == ["a", "b", "c"]
    assert site.join_parts(parts) == big
    odd = site._minify({"a b": "x" * site.SPLIT_MIN_BYTES, "c": 1}).encode()
    assert site.split_json(odd) is None


def test_store_is_content_addressed(tmp_path):
    store = site.Store(tmp_path)
    e = store.put("x/data.json", b"{}")
    assert e == {"asset": f"assets/{hashlib.sha256(b'{}').hexdigest()}.json", "size": 2}
    assert store.put("y/other.json", b"{}") == e
    assert (store.written, store.reused) == (1, 1)
    assert store.put("noext", b"z")["asset"].endswith(".bin")


def manifest(site_dir, music_id, difficulty, store, files):
    entries = {p: store.put(p, data) for p, data in files.items()}
    doc = {"musicId": music_id, "difficulty": difficulty, "audio": True, "audioFormat": "aac", "flows": ["direct"],
           "files": entries, "chart": {"title": f"t{music_id}", "level": 1}}
    (site_dir / "charts" / f"{music_id}_{difficulty}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_write_index_orders_charts_and_prunes_assets(tmp_path):
    (tmp_path / "charts").mkdir()
    store = site.Store(tmp_path)
    manifest(tmp_path, 2, "easy", store, {"live.json": b"{}"})
    manifest(tmp_path, 1, "expert", store, {"live.json": b"{}", "score/a.json": b"[1]"})
    manifest(tmp_path, 1, "easy", store, {"live.json": b"{}"})
    stale = store.put("old.json", b"[0]")
    r = site.write_index(tmp_path)
    assert (r["charts"], r["assets"], r["removedAssets"]) == (3, 2, 1)
    assert not (tmp_path / stale["asset"]).exists()
    index = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert index["format"] == site.SITE_FORMAT
    assert [c["id"] for c in index["charts"]] == ["1_easy", "1_expert", "2_easy"]
    assert index["charts"][1]["bytes"] == 5 and index["charts"][1]["title"] == "t1"


def fake_player(root):
    for rel, text in {site.READ_SET_SCRIPT: "// read set\n", site.PLAYER_BUNDLES[0]: "/* bundle */\n",
                      f"{site.PLAYER_PAGE_DIR}/index.html":
                          '<script type="module" src="./chart-list.js"></script><!-- @PLAYER_VERSION@ -->\n',
                      f"{site.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n'}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def test_check_player(tmp_path):
    with pytest.raises(ConfigError, match="NNNOTES_PATHS_PLAYER"):
        site.check_player(None)
    with pytest.raises(ConfigError, match="not a built ournotes-player"):
        site.check_player(tmp_path)
    assert site.check_player(fake_player(tmp_path / "p")) == (tmp_path / "p").resolve()


def test_write_player(tmp_path):
    player = fake_player(tmp_path / "p")
    out = tmp_path / "site"
    r = site.write_player(out, player)
    version = hashlib.sha256(b"/* bundle */\n").hexdigest()[:16]
    assert r["playerVersion"] == version and r["pageFiles"] == 2
    assert (out / "chart-list.js").read_text(encoding="utf-8") == 'import "./ournotes-player.element.min.js";\n'
    assert version in (out / "index.html").read_text(encoding="utf-8")
    assert (out / "ournotes-player.element.min.js").is_file()
    (player / site.PLAYER_PAGE_DIR / "extra.js").write_bytes(b'import "../../src/other.js";\n')
    with pytest.raises(RuntimeError, match="beyond PAGE_IMPORTS"):
        site.write_player(out, player)


def test_player_only_command(tmp_path, capsys):
    from nnnotes import cli
    out = tmp_path / "site"
    (out / "assets").mkdir(parents=True)
    (out / "assets" / "unreferenced.json").write_bytes(b"{}")
    cli.main(["site", str(out), "--player", str(fake_player(tmp_path / "p")), "--player-only"])
    r = json.loads(capsys.readouterr().out)
    assert (r["charts"], r["removedAssets"], r["pageFiles"]) == (0, 1, 2)
    assert json.loads((out / "charts.json").read_text(encoding="utf-8")) == {"charts": [], "format": site.SITE_FORMAT}


def test_player_only_on_a_new_directory(tmp_path, capsys):
    from nnnotes import cli
    out = tmp_path / "new-site"
    cli.main(["site", str(out), "--player", str(fake_player(tmp_path / "p")), "--player-only"])
    r = json.loads(capsys.readouterr().out)
    assert (r["charts"], r["assets"], r["removedAssets"]) == (0, 0, 0)
    assert (out / "index.html").is_file() and (out / "charts.json").is_file()


@pytest.mark.parametrize("failed, code", [([], 0), ([{"id": "1_easy", "stage": "live", "error": "x"}], 1)])
def test_site_exit_status_follows_failed_charts(tmp_path, capsys, monkeypatch, failed, code):
    from nnnotes import cli
    calls = []

    def fake_build(out, pairs, cfg, player, fmt, **kw):
        calls.append((pairs, fmt, kw))
        return {"site": str(out), "built": [], "failed": failed, "skipped": []}

    monkeypatch.setattr(site, "build", fake_build)
    exit_code = 0
    try:
        cli.main(["site", str(tmp_path / "s"), "--player", str(fake_player(tmp_path / "p")),
                  "--pair", "7:hard", "--pair", "7_easy", "--format", "opus", "--no-audio", "--workers", "1"])
    except SystemExit as e:
        exit_code = e.code
    assert exit_code == code
    assert json.loads(capsys.readouterr().out)["failed"] == failed
    ((pairs, fmt, kw),) = calls
    assert pairs == [(7, "hard"), (7, "easy")] and fmt == "opus"
    assert kw["audio"] is False and kw["workers"] == 1 and kw["force"] is False
