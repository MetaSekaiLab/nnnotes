"""Player page collection, bundled imports and reserved site paths."""

import hashlib

import pytest

from nnnotes.site import pages
from nnnotes.source.config import ConfigError


def player(root, *, live2d=False, story=False, bundles=True):
    files = {
        pages.READ_SET_SCRIPT: "// read set\n",
        pages.PLAYER_BUNDLES[0]: "/* bundle */\n",
        f"{pages.PLAYER_PAGE_DIR}/index.html": "<!-- @PLAYER_VERSION@ -->\n",
        f"{pages.PLAYER_PAGE_DIR}/chart-list.js": 'import "../../src/element.js";\n',
    }
    for enabled, directory, bundle, script in [
        (live2d, pages.LIVE2D_PAGE_DIR, pages.LIVE2D_BUNDLES[0], "live2d/define.js"),
        (story, pages.STORY_PAGE_DIR, pages.STORY_BUNDLES[0], "story/define.js"),
    ]:
        if enabled:
            files[f"{directory}/index.html"] = "<!-- @PLAYER_VERSION@ -->"
            files[f"{directory}/main.js"] = f'import "../../src/{script}";\n'
            if bundles:
                files[bundle] = f"/* {script} */"
                files[bundle + ".map"] = "{}"
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))
    return root


def test_check_player(tmp_path):
    with pytest.raises(ConfigError, match="NNNOTES_PATHS_PLAYER"):
        pages.check_player(None)
    with pytest.raises(ConfigError, match="not a built ournotes-player"):
        pages.check_player(tmp_path)
    assert pages.check_player(player(tmp_path / "p")) == (tmp_path / "p").resolve()


def test_pages_bind_imports_and_versions_to_bundles(tmp_path):
    root = player(tmp_path, live2d=True, story=True)
    files, stats = pages.gather(root, stories=True)
    version = hashlib.sha256(b"/* bundle */\n").hexdigest()[:16]
    assert stats["playerVersion"] == version
    assert stats["pageFiles"] == stats["live2dPageFiles"] == stats["storyPageFiles"] == 2
    assert version.encode() in files["index.html"]
    assert files["chart-list.js"] == b'import "./ournotes-player.element.min.js";\n'
    for name in ["live2d", "story"]:
        assert files[f"{name}/main.js"] == f'import "./ournotes-player.{name}.element.min.js";\n'.encode()
        assert f"{name}/ournotes-player.{name}.element.min.js.map" in files


def test_missing_optional_bundles(tmp_path):
    with pytest.raises(ConfigError, match="has the Live2D page but not"):
        pages.gather(player(tmp_path / "l", live2d=True, bundles=False))
    root = player(tmp_path / "s", story=True, bundles=False)
    files, stats = pages.gather(root)
    assert "storyPageFiles" not in stats
    assert not any(p.startswith("story/") for p in files)
    with pytest.raises(ConfigError, match="has the story page but not"):
        pages.gather(root, stories=True)


def test_songs_include_nested_module_dependencies(tmp_path):
    root = player(tmp_path)
    songs = {"index.html": b"songs", "songs.js": b'import "./nested/data.json";', "nested/data.json": b"{}"}
    for name, data in songs.items():
        path = root / pages.SONGS_PAGE_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    files, stats = pages.gather(root)
    assert stats["songsPageFiles"] == len(songs)
    assert {p.removeprefix("songs/"): b for p, b in files.items() if p.startswith("songs/")} == songs


@pytest.mark.parametrize("name", ["models.json", "site.json", "products/example.json"])
def test_reserved_product_and_index_paths(tmp_path, name):
    root = player(tmp_path)
    path = root / pages.PLAYER_PAGE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="would overwrite the site data"):
        pages.gather(root)


def test_unbundled_source_import_is_rejected(tmp_path):
    root = player(tmp_path)
    (root / pages.PLAYER_PAGE_DIR / "extra.js").write_text('import "../../src/other.js";', encoding="utf-8")
    with pytest.raises(RuntimeError, match="beyond PAGE_IMPORTS"):
        pages.gather(root)
