"""Corrupt catalog and bundle inputs must fail before becoming reusable cache entries."""
import struct

import pytest

import synth
from nnnotes.addressables import (BundleKey, DYNAMIC, NONE, OFFSET_MASK, parse,
                                 parse_header, parse_keys, parse_locations)
from nnnotes.catalog import Catalog


KEY = BundleKey(synth.BUNDLE_KEY, synth.BUNDLE_SEED)


def binary(path_ids=False):
    return synth.CatalogWriter().build([
        ("Char/A", "Assets/Game/A.prefab", [1]),
        ("a_01.bundle", synth.remote("a_01.bundle"), []),
    ], path_ids=path_ids)


@pytest.mark.parametrize("bad_download", ["wrong-key", b"error response", b"", b"UnityFS"])
def test_invalid_bundle_is_not_cached_and_can_be_retried(tmp_path, monkeypatch, bad_download):
    cat = Catalog(binary(), tmp_path / "cache", cdn="https://cdn.invalid", bundle_key=KEY)
    bundle, = cat.resolve("Char/A")
    plain = synth.fake_bundle(bundle.name)
    if bad_download == "wrong-key":
        cat._settings["bundle_key"] = BundleKey(bytes(16), b"")
        data = synth.encrypt_bundle(plain, bundle.name)
    else:
        data = bad_download
    monkeypatch.setattr(cat, "_download", lambda url: data)
    with pytest.raises(ValueError, match="UnityFS"):
        cat.fetch(bundle)
    assert cat.cached(bundle) is None
    assert not list(cat.cache_dir.rglob("*.part"))
    cat._settings["bundle_key"] = KEY
    monkeypatch.setattr(cat, "_download", lambda url: synth.encrypt_bundle(plain, bundle.name))
    assert cat.fetch(bundle).read_bytes() == plain


@pytest.mark.parametrize("damaged", [b"bad cached bundle", b"UnityFS", b""])
def test_damaged_cached_bundle_is_a_miss_and_is_replaced(tmp_path, monkeypatch, damaged):
    cat = Catalog(binary(), tmp_path / "cache", cdn="https://cdn.invalid", bundle_key=KEY)
    bundle, = cat.resolve("Char/A")
    plain = synth.fake_bundle(bundle.name)
    monkeypatch.setattr(cat, "_download", lambda url: synth.encrypt_bundle(plain, bundle.name))
    path = cat.fetch(bundle)
    path.write_bytes(damaged)
    assert cat.cached(bundle) is None
    assert cat.fetch(bundle).read_bytes() == plain


def test_apk_bundle_repairs_damaged_cache(tmp_path):
    import zipfile

    apk = tmp_path / "base.apk"
    local = "local.bundle"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/aa/catalog.bin", synth.CatalogWriter().build([
            ("Local", synth.local(local), []),
        ]))
        archive.writestr("assets/aa/Android/" + local, synth.encrypt_bundle(synth.fake_bundle(local), local))
    cat = Catalog(binary(), tmp_path / "cache", apk=apk, bundle_key=KEY)
    bundle, = cat.resolve("Local")
    path = cat.fetch(bundle)
    path.write_bytes(b"bad cached bundle")
    assert cat.cached(bundle) is None
    assert cat.apk_bundle("local").read_bytes() == synth.fake_bundle(local)
    path.write_bytes(b"bad cached bundle")
    assert cat.fetch(bundle).read_bytes() == synth.fake_bundle(local)


@pytest.mark.parametrize("decode", [parse, parse_header, parse_keys, parse_locations])
@pytest.mark.parametrize("data", [b"", b"short"])
def test_short_catalog_has_a_format_error(decode, data):
    with pytest.raises(ValueError):
        decode(data)


@pytest.mark.parametrize("decode", [parse, parse_locations])
def test_string_cannot_extend_past_catalog(decode):
    data = bytearray(binary())
    location = parse(data)[0]["offset"]
    string_offset = len(data) + 4
    data += struct.pack("<I", 20) + b"Assets/X"
    struct.pack_into("<I", data, location + 4, string_offset)
    with pytest.raises(ValueError, match="past the end"):
        decode(bytes(data))


@pytest.mark.parametrize("decode", [parse, parse_keys, parse_locations])
def test_key_table_requires_complete_records(decode):
    data = bytearray(binary())
    keys, = struct.unpack_from("<I", data, 8)
    struct.pack_into("<I", data, keys - 4, 7)
    with pytest.raises(ValueError, match="multiple of 8"):
        decode(bytes(data))


def test_dependency_array_requires_complete_offsets():
    data = bytearray(binary())
    location = parse(data)[0]["offset"]
    array_offset = len(data) + 4
    data += struct.pack("<I", 5) + struct.pack("<I", NONE) + b"x"
    struct.pack_into("<I", data, location + 12, array_offset)
    with pytest.raises(ValueError, match="multiple of 4"):
        parse(bytes(data))


@pytest.mark.parametrize("decode", [parse, parse_locations])
def test_cyclic_catalog_path_is_rejected(decode):
    data = bytearray(binary(path_ids=True))
    location = parse(data)[0]["offset"]
    internal, = struct.unpack_from("<I", data, location + 4)
    assert internal & DYNAMIC
    struct.pack_into("<I", data, (internal & OFFSET_MASK) + 4, internal)
    with pytest.raises(ValueError, match="loops"):
        decode(bytes(data))
