"""Small synthetic APK sets: expansion reuse, invalidation and reader lifetimes."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from nnnotes import apkset
from nnnotes.catalog import APK_AA_DIR, APK_CATALOG, Catalog
from test_jp import cat_bytes, zip_bytes


@pytest.fixture(autouse=True)
def clear_expansions():
    apkset._expanded_apks.cache_clear()
    yield
    apkset._expanded_apks.cache_clear()


def package(path, payload=b"asset"):
    path.write_bytes(zip_bytes({
        "base.apk": zip_bytes({"AndroidManifest.xml": b"base"}),
        "split_data.apk": zip_bytes({"asset": payload, "AndroidManifest.xml": b"split"}),
    }))
    return path


@pytest.mark.parametrize("suffix", [".apks", ".xapk"])
def test_repeated_and_concurrent_opens_expand_once(tmp_path, monkeypatch, suffix):
    source = package(tmp_path / ("game" + suffix))
    copy = Mock(wraps=apkset.shutil.copyfileobj)
    monkeypatch.setattr(apkset.shutil, "copyfileobj", copy)
    def read(_):
        with apkset.ApkSet(source) as archive:
            assert archive.read("AndroidManifest.xml") == b"base"
            return archive.read("asset")
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(read, range(8))) == [b"asset"] * 8
    assert read(None) == b"asset"
    assert copy.call_count == 2


def test_replaced_archive_uses_new_expansion(tmp_path):
    source = package(tmp_path / "game.apks")
    with apkset.ApkSet(source) as old:
        package(source, b"replacement")
        with apkset.ApkSet(source) as new:
            assert new.read("asset") == b"replacement"
        assert old.read("asset") == b"asset"


def test_same_size_archive_update_invalidates_by_mtime(tmp_path):
    source = package(tmp_path / "game.apks", b"first")
    before = source.stat()
    with apkset.ApkSet(source) as archive:
        assert archive.read("asset") == b"first"
    package(source, b"other")
    assert source.stat().st_size == before.st_size
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    with apkset.ApkSet(source) as archive:
        assert archive.read("asset") == b"other"


def test_eviction_does_not_remove_an_active_readers_files(tmp_path):
    source = package(tmp_path / "first.apks")
    with apkset.ApkSet(source) as first:
        expanded = Path(first._expanded[0].name)
        for name in ("second", "third"):
            with apkset.ApkSet(package(tmp_path / f"{name}.apks")) as other:
                assert other.read("asset") == b"asset"
        assert apkset._expanded_apks.cache_info().currsize == 2
        apkset._expanded_apks.cache_clear()
        assert expanded.is_dir()
        assert first.read("asset") == b"asset"
    assert not expanded.exists()


def test_failed_expansion_is_cleaned_and_can_be_retried(tmp_path, monkeypatch):
    source = package(tmp_path / "game.apks")
    directories = []
    make_dir = apkset.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        directory = make_dir(**kwargs)
        directories.append(Path(directory.name))
        return directory
    monkeypatch.setattr(apkset.tempfile, "TemporaryDirectory", temporary)
    copy = apkset.shutil.copyfileobj
    def fail(stream, target):
        copy(stream, target)
        raise OSError("synthetic copy failure")
    monkeypatch.setattr(apkset.shutil, "copyfileobj", fail)
    with pytest.raises(OSError, match="synthetic copy failure"):
        with apkset.ApkSet(source):
            pytest.fail("partial expansion should not open")
    assert directories and all(not p.exists() for p in directories)
    assert apkset._expanded_apks.cache_info().currsize == 0
    monkeypatch.setattr(apkset.shutil, "copyfileobj", copy)
    with apkset.ApkSet(source) as archive:
        assert archive.read("asset") == b"asset"


def test_cached_apk_bundle_does_not_reexpand_the_archive(tmp_path, monkeypatch):
    source = tmp_path / "game.apks"
    source.write_bytes(zip_bytes({
        "base.apk": zip_bytes({"AndroidManifest.xml": b"base"}),
        "split_data.apk": zip_bytes({APK_CATALOG: cat_bytes(),
                                     APK_AA_DIR + "Android/local.bundle": b"UnityFS\0local"}),
    }))
    cat = Catalog(cat_bytes(), tmp_path / "cache", apk=source)
    copy = Mock(wraps=apkset.shutil.copyfileobj)
    monkeypatch.setattr(apkset.shutil, "copyfileobj", copy)
    assert cat.apk_bundle("local").read_bytes() == b"UnityFS\0local"
    assert cat.apk_bundle("local").read_bytes() == b"UnityFS\0local"
    assert copy.call_count == 0
