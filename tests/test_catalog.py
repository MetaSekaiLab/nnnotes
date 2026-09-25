import zipfile

import pytest

import synth
from nnnotes.addressables import BundleKey, browse, decrypt, parse, remote_path
from nnnotes.catalog import APK_OFFSET_BASE, Catalog

KEY = BundleKey(synth.BUNDLE_KEY, synth.BUNDLE_SEED)


# ---------------------------------------------------------------- bundle decryption
def test_decrypt_matches_independent_ctr():
    name = "chars_a_0123abcd.bundle"
    plain = synth.fake_bundle(name, 20000)
    stored = synth.encrypt_bundle(plain, name)
    assert stored[:7] != b"UnityFS" and stored[16384:] == plain[16384:]
    assert decrypt(stored, name, KEY) == plain


def test_decrypt_nonce_depends_on_file_name():
    plain = synth.fake_bundle("x", 1000)
    stored = synth.encrypt_bundle(plain, "a.bundle")
    assert decrypt(stored, "b.bundle", KEY) != plain


def test_decrypt_leaves_plain_bundles():
    plain = synth.fake_bundle("plain", 500)
    assert decrypt(plain, "plain.bundle", KEY) is plain


def test_bundle_key_checks_and_hides_values():
    with pytest.raises(ValueError):
        BundleKey(bytes(15), b"")
    text = repr(KEY)
    assert synth.BUNDLE_KEY.hex() not in text and str(list(synth.BUNDLE_KEY)[:4])[1:-1] not in text
    assert repr(synth.BUNDLE_KEY) not in text and repr(synth.BUNDLE_SEED) not in text


def test_remote_path():
    assert remote_path("https://example.invalid/asset/Android/x.bundle") == "/asset/Android/x.bundle"
    assert remote_path("Assets/Game/x.prefab") is None
    assert remote_path(synth.local("x.bundle")) is None


# ---------------------------------------------------------------- catalog
REMOTE_ENTRIES = [
    ("Char/A", "Assets/Game/Char/A.prefab", [2]),
    ("Char/B", "Assets/Game/Char/B.prefab", [3]),
    ("a_01.bundle", synth.remote("a_01.bundle"), [3]),
    ("b_02.bundle", synth.remote("b_02.bundle"), []),
    ("Story/物語", "Assets/Game/Story/物語.asset", [3]),
]
APK_ENTRIES = [
    ("Shared/S", "Assets/Game/Shared/S.prefab", [1]),
    ("s_03.bundle", synth.local("s_03.bundle"), []),
]


def remote_catalog(path_ids=False) -> bytes:
    return synth.CatalogWriter().build(REMOTE_ENTRIES, path_ids=path_ids)


@pytest.mark.parametrize("path_ids", [False, True])
def test_parse(path_ids):
    entries = parse(remote_catalog(path_ids))
    assert [(e["primary_key"], e["internal_id"]) for e in entries] == [(k, i) for k, i, _ in REMOTE_ENTRIES]
    by_key = {e["primary_key"]: e for e in entries}
    assert by_key["Char/A"]["dependencies"] == (by_key["a_01.bundle"]["offset"],)
    assert list(by_key["b_02.bundle"]["dependencies"]) == []


def test_parse_rejects_other_formats():
    data = bytearray(remote_catalog())
    data[4] = 3
    with pytest.raises(ValueError, match="unsupported catalog format"):
        parse(bytes(data))


def test_browse_index():
    bundles, files = browse(parse(remote_catalog()))
    assert sorted(e["primary_key"] for e in bundles.values()) == ["a_01.bundle", "b_02.bundle"]
    assert set(files) == {"Char/A", "Char/B", "Story/物語", "bundles/a_01.bundle", "bundles/b_02.bundle"}


def make_cdn(root):
    """A CDN tree on disk (file:// URL): the two remote bundles, encrypted."""
    for name in ("a_01.bundle", "b_02.bundle"):
        p = root / "asset" / "Android" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(synth.encrypt_bundle(synth.fake_bundle(name), name))
    return root.as_uri()


def make_apk(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build(APK_ENTRIES))
        z.writestr("assets/aa/Android/s_03.bundle", synth.encrypt_bundle(synth.fake_bundle("s_03.bundle"),
                                                                           "s_03.bundle"))
    return path


def test_closure_fetch_and_cache(tmp_path):
    cdn = make_cdn(tmp_path / "cdn")
    cat = Catalog(remote_catalog(), tmp_path / "cache", cdn=cdn + "/", bundle_key=KEY)
    assert cat.keys("Char/") == ["Char/A", "Char/B"]
    assert cat.has("Story/物語") and not cat.has("Char/C")
    assert sorted(b.name for b in cat.resolve("Char/A")) == ["a_01.bundle", "b_02.bundle"]
    paths = cat.fetch_key("Char/A")
    assert sorted(p.name for p in paths) == ["a_01.bundle", "b_02.bundle"]
    for p in paths:
        assert p.parent == tmp_path / "cache" / "bundles"
        assert p.read_bytes() == synth.fake_bundle(p.name)
    # cached bundles are not fetched again
    for p in (tmp_path / "cdn" / "asset" / "Android").iterdir():
        p.unlink()
    assert cat.fetch_key("Char/B") == [tmp_path / "cache" / "bundles" / "b_02.bundle"]
    with pytest.raises(KeyError):
        cat.resolve("Char/C")


def test_encrypted_bundle_needs_key(tmp_path):
    cat = Catalog(remote_catalog(), tmp_path / "cache", cdn=make_cdn(tmp_path / "cdn"))
    with pytest.raises(RuntimeError, match="no bundle key"):
        cat.fetch_key("Char/B")


def test_apk_catalog_is_merged(tmp_path):
    apk = make_apk(tmp_path / "base.apk")
    cat = Catalog(remote_catalog(), tmp_path / "cache", cdn=make_cdn(tmp_path / "cdn"), bundle_key=KEY, apk=apk)
    assert "Shared/S" in cat.keys()
    (b,) = cat.resolve("Shared/S")
    assert not b.remote and b.offset >= APK_OFFSET_BASE
    (p,) = cat.fetch_key("Shared/S")
    assert p.read_bytes() == synth.fake_bundle("s_03.bundle")
    assert cat.apk_bundle("s_03").read_bytes() == synth.fake_bundle("s_03.bundle")
    with pytest.raises(KeyError):
        cat.apk_bundle("nothing-like-this")


def test_apk_bundles_skipped_without_apk(tmp_path):
    cat = Catalog(synth.CatalogWriter().build(APK_ENTRIES), tmp_path / "cache", bundle_key=KEY)
    assert cat.fetch_key("Shared/S") == []


def test_load_downloads_catalog_once(tmp_path):
    cdn_root = tmp_path / "cdn"
    (cdn_root / "asset" / "Android").mkdir(parents=True)
    (cdn_root / "asset" / "Android" / "catalog_main_xx.bin").write_bytes(remote_catalog())
    cat = Catalog.load("xx", tmp_path / "cache", cdn=cdn_root.as_uri())
    assert (tmp_path / "cache" / "catalog_main_xx.bin").read_bytes() == remote_catalog()
    assert cat.keys("Char/") == ["Char/A", "Char/B"]
    (cdn_root / "asset" / "Android" / "catalog_main_xx.bin").unlink()
    assert Catalog.load("xx", tmp_path / "cache").keys("Story/") == ["Story/物語"]
    with pytest.raises(FileNotFoundError):
        Catalog.load("yy", tmp_path / "cache")
