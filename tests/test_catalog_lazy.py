"""The CDN base and the bundle key are read from the settings only when something must be downloaded."""
import threading
from pathlib import Path
import zipfile

import pytest

import synth
from nnnotes import catalog, cli
from nnnotes.addressables import BundleKey
from nnnotes.catalog import Catalog
from nnnotes.config import ConfigError

KEY = BundleKey(synth.BUNDLE_KEY, synth.BUNDLE_SEED)
ENTRIES = [
    ("Char/A", "Assets/Game/Char/A.prefab", [2]),
    ("Char/B", "Assets/Game/Char/B.prefab", [2]),
    ("a_01.bundle", synth.remote("a_01.bundle"), []),
]


def cdn_tree(root):
    p = root / "asset" / "Android" / "a_01.bundle"
    p.parent.mkdir(parents=True)
    p.write_bytes(synth.encrypt_bundle(synth.fake_bundle("a_01.bundle"), "a_01.bundle"))
    (root / "asset" / "Android" / "catalog_main_xx.bin").write_bytes(synth.CatalogWriter().build(ENTRIES))
    return root.as_uri()


def never(what):
    def f():
        raise AssertionError(f"{what} was read although nothing is downloaded")
    return f


def missing(setting):
    def f():
        raise ConfigError(f"setting {setting} is not set")
    return f


def test_cached_bundles_read_no_settings(tmp_path):
    cdn = cdn_tree(tmp_path / "cdn")
    first = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=cdn, bundle_key=KEY)
    (p,) = first.fetch_key("Char/A")
    again = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=never("cdn"),
                    bundle_key=never("bundle key"))
    assert again.fetch_key("Char/A") == [p] and again.fetch_key("Char/B") == [p]
    assert p.read_bytes() == synth.fake_bundle("a_01.bundle")


def test_settings_are_read_once_when_a_download_needs_them(tmp_path):
    cdn = cdn_tree(tmp_path / "cdn")
    calls = []
    cat = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache",
                  cdn=lambda: calls.append("cdn") or cdn, bundle_key=lambda: calls.append("key") or KEY)
    assert cat.cdn is None                                   # not read yet
    (p,) = cat.fetch_key("Char/A")
    assert p.read_bytes() == synth.fake_bundle("a_01.bundle")
    p.unlink()
    cat.fetch_key("Char/A")
    assert calls == ["cdn", "key"] and cat.cdn == cdn


def test_missing_setting_names_the_file_and_the_setting(tmp_path):
    cdn = cdn_tree(tmp_path / "cdn")
    cat = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=missing("servers.zz.cdn"),
                  bundle_key=KEY)
    with pytest.raises(ConfigError) as e:
        cat.fetch_key("Char/A")
    assert str(e.value) == "a_01.bundle is not in the cache: setting servers.zz.cdn is not set"
    cat = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=cdn,
                  bundle_key=missing("bundle.key"))
    with pytest.raises(ConfigError, match="^a_01.bundle is not in the cache: setting bundle.key is not set$"):
        cat.fetch_key("Char/A")


def test_missing_key_fails_before_the_download(tmp_path):
    gone = (tmp_path / "no-such-cdn").as_uri()               # a download would fail with URLError
    cat = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=gone,
                  bundle_key=missing("bundle.key"))
    with pytest.raises(ConfigError, match="bundle.key"):
        cat.fetch_key("Char/A")


def test_apk_bundles_read_the_key_only_when_encrypted(tmp_path):
    apk = tmp_path / "base.apk"
    entries = [("Shared/S", "Assets/Shared/S.prefab", [1]),
               ("s_03.bundle", "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/s_03.bundle", []),
               ("Shared/P", "Assets/Shared/P.prefab", [3]),
               ("p_04.bundle", "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/p_04.bundle", [])]
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(entries))
        z.writestr("assets/aa/Android/s_03.bundle", synth.encrypt_bundle(synth.fake_bundle("s_03.bundle"),
                                                                           "s_03.bundle"))
        z.writestr("assets/aa/Android/p_04.bundle", synth.fake_bundle("p_04.bundle"))
    cat = Catalog(synth.CatalogWriter().build(ENTRIES), tmp_path / "cache", cdn=never("cdn"),
                  bundle_key=missing("bundle.key"), apk=apk)
    (p,) = cat.fetch_key("Shared/P")                         # plain: no key
    assert p.read_bytes() == synth.fake_bundle("p_04.bundle")
    assert cat.apk_bundle("p_04").read_bytes() == synth.fake_bundle("p_04.bundle")
    with pytest.raises(ConfigError, match="^s_03.bundle is not in the cache: setting bundle.key"):
        cat.fetch_key("Shared/S")
    with pytest.raises(ConfigError, match="^s_03.bundle is not in the cache"):
        cat.apk_bundle("s_03")


def test_load_reads_the_cdn_only_for_an_uncached_catalog(tmp_path):
    cdn = cdn_tree(tmp_path / "cdn")
    with pytest.raises(ConfigError, match="^catalog_main_xx.bin is not in the cache: setting servers.zz.cdn"):
        Catalog.load("xx", tmp_path / "cache", cdn=missing("servers.zz.cdn"))
    calls = []
    Catalog.load("xx", tmp_path / "cache", cdn=lambda: calls.append(1) or cdn)
    cat = Catalog.load("xx", tmp_path / "cache", cdn=never("cdn"), bundle_key=never("bundle key"))
    assert calls == [1] and cat.keys("Char/") == ["Char/A", "Char/B"]


def test_write_atomic_temporary_name_is_short_and_unique(tmp_path, monkeypatch):
    from nnnotes import cache
    long_name = "b" * 244 + ".bundle"                         # 251 characters: name + pid + thread id + .part > 255
    dst = tmp_path / long_name
    names = [cache.temp_path(dst).name for _ in range(100)]
    assert len(set(names)) == 100 and all(len(n) <= 32 and n.endswith(".part") for n in names)
    assert cache.temp_path(dst).parent == tmp_path
    try:
        dst.write_bytes(b"")                                    # can this file system hold the name at all?
        dst.unlink()
    except OSError:
        seen = []                                              # (Windows without long paths): the rename only
        monkeypatch.setattr(cache.os, "replace", lambda a, b: seen.append((Path(a).name, Path(b).name)))
        catalog._write_atomic(dst, b"data")
        assert seen[0][1] == long_name and len(seen[0][0]) <= 32
        return
    catalog._write_atomic(dst, b"data")
    t = threading.Thread(target=catalog._write_atomic, args=(dst, b"data2"))
    t.start()
    t.join()
    assert dst.read_bytes() == b"data2" and sorted(p.name for p in tmp_path.iterdir()) == [long_name]


def test_write_atomic_removes_the_temporary_file_on_failure(tmp_path, monkeypatch):
    from nnnotes import cache

    def refuse(a, b):
        raise PermissionError("in use")
    monkeypatch.setattr(cache.os, "replace", refuse)
    with pytest.raises(PermissionError):
        cache.write_atomic(tmp_path / "f.bin", b"x")
    assert list(tmp_path.iterdir()) == []


def test_disk_cache_entries_use_the_short_temporary_name(tmp_path):
    from nnnotes import cache
    p = tmp_path / "ab" / ("c" * 64)
    assert cache._disk_write(p, b"payload") and cache._disk_read(p) == b"payload"
    assert sorted(x.name for x in p.parent.iterdir()) == [p.name]


# ---------------------------------------------------------------- the command line
def run(argv, capsys):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def test_pull_of_cached_bundles_needs_no_region_cdn_or_key(tmp_path, capsys, monkeypatch):
    catbin = tmp_path / "catalog_main_xx.bin"
    catbin.write_bytes(synth.CatalogWriter().build(ENTRIES))
    base = ["--catalog", str(catbin), "--cache", str(tmp_path / "cache")]
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", synth.BUNDLE_KEY.hex())
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", synth.BUNDLE_SEED.hex())
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn_tree(tmp_path / "cdn"))
    code, out, err = run(base + ["--region", "zz", "pull", "Char/A"], capsys)
    assert code == 0, err
    for name in ("NNNOTES_BUNDLE_KEY", "NNNOTES_BUNDLE_NONCE_SEED", "NNNOTES_SERVERS_ZZ_CDN"):
        monkeypatch.delenv(name)
    code, again, err = run(base + ["pull", "Char/A", "Char/B"], capsys)
    assert code == 0, err
    assert again.splitlines() == out.splitlines() * 2


def test_pull_that_downloads_names_the_missing_setting_in_one_line(tmp_path, capsys, monkeypatch):
    catbin = tmp_path / "catalog_main_xx.bin"
    catbin.write_bytes(synth.CatalogWriter().build(ENTRIES))
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn_tree(tmp_path / "cdn"))
    code, out, err = run(["--catalog", str(catbin), "--cache", str(tmp_path / "cache"), "--region", "zz",
                          "pull", "Char/A"], capsys)
    assert code == 2 and out == "" and len(err.strip().splitlines()) == 1
    assert err.startswith("nnnotes: a_01.bundle is not in the cache: setting bundle.key")
    assert "NNNOTES_BUNDLE_KEY" in err
