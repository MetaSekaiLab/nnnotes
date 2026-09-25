import struct

import pytest

from nnnotes import cache


@pytest.fixture(autouse=True)
def fresh_cache():
    """Every test starts with the default settings and empty buckets, and leaves them so."""
    before = cache.settings()
    cache.configure(enabled=True, memory_mb=cache.DEFAULT_MEMORY_MB, directory="")
    yield
    cache.configure(enabled=before["enabled"], memory_mb=before["memory"] >> 20,
                    directory=before["directory"] or "", closures=before["closures"],
                    closure_mb=before["closure_bytes"] >> 20)


def test_key_is_unambiguous_and_stable():
    assert cache.key("ab", "c") != cache.key("a", "bc")
    assert cache.key(b"x") != cache.key("x")
    assert cache.key(1) != cache.key("1") != cache.key(1.0)
    assert cache.key((1, 2), [b"a"]) == cache.key([1, 2], (b"a",))          # tuples and lists alike
    assert cache.key(memoryview(b"abc")) == cache.key(b"abc") == cache.key(bytearray(b"abc"))
    assert len(cache.key()) == 64
    with pytest.raises(TypeError):
        cache.key(object())


def test_bucket_lru_and_budget():
    cache.configure(memory_mb=1)
    b = cache.bucket("t-lru", max_item=1 << 20)
    k = [b.key(i) for i in range(5)]
    for i in range(4):
        b.put(k[i], bytes(300 << 10))                 # 4 x 300 KiB > 1 MiB: the oldest goes
    assert b.get(k[0]) is None
    assert all(b.get(x) is not None for x in k[1:4])
    b.get(k[1])                                        # most recently used survives the next insert
    b.put(k[4], bytes(300 << 10))
    assert b.get(k[1]) is not None and b.get(k[2]) is None
    s = b.stats()
    assert s["bytes"] <= 1 << 20 and s["hits"] >= 4 and s["misses"] >= 2


def test_bucket_max_item_and_disabled():
    b = cache.bucket("t-max", max_item=10)
    b.put(b.key("big"), b"x" * 11)
    assert b.get(b.key("big")) is None
    b.put(b.key("small"), b"x" * 10)
    assert b.get(b.key("small")) == b"x" * 10
    cache.configure(enabled=False)
    assert b.get(b.key("small")) is None               # configure clears, and a disabled cache misses
    b.put(b.key("small"), b"y")
    assert b.get(b.key("small")) is None
    cache.configure(enabled=True)
    assert b.get(b.key("small")) is None


def test_non_bytes_values_with_size():
    b = cache.bucket("t-obj")
    b.put(b.key("o"), {"a": 1}, 100)
    assert b.get(b.key("o")) == {"a": 1}


def test_disk_layer_shared_and_checked(tmp_path):
    cache.configure(directory=tmp_path / "c")
    b = cache.bucket("t-disk", salt="s1", disk=True)
    k = b.key("v")
    b.put(k, b"payload")
    files = [p for p in (tmp_path / "c").rglob("*") if p.is_file()]
    assert len(files) == 1 and not files[0].name.endswith(".part")
    cache.clear()                                      # memory gone: the disk answers
    assert b.get(k) == b"payload" and b.stats()["diskHits"] == 1
    other = cache.bucket("t-disk-other-salt", salt="s2", disk=True)
    assert other.get(other.key("v")) is None           # another salt: another key
    files[0].write_bytes(files[0].read_bytes()[:-1])   # a truncated file is a miss
    cache.clear()
    assert b.get(k) is None
    files[0].write_bytes(b"garbage")
    assert b.get(k) is None


def test_pack_round_trip():
    blobs = [b"", b"a", bytes(range(256)) * 3]
    meta, back = cache.unpack(cache.pack(b'{"x":1}', blobs))
    assert meta == b'{"x":1}' and back == blobs
    with pytest.raises(ValueError):
        cache.unpack(cache.pack(b"m", [b"z"]) + b"extra")
    assert cache.unpack(struct.pack("<II", 0, 0)) == (b"", [])


def test_source_salt_follows_the_source(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a = 1\n", encoding="utf-8")
    s1 = cache.source_salt(f, "no-such-distribution-xyz")
    assert s1 == cache.source_salt(f, "no-such-distribution-xyz")
    g = tmp_path / "n.py"
    g.write_text("a = 2\n", encoding="utf-8")
    assert cache.source_salt(g, "no-such-distribution-xyz") != s1


def test_file_id_changes_with_content(tmp_path):
    f = tmp_path / "tool"
    f.write_bytes(b"1")
    a = cache.file_id(f)
    f.write_bytes(b"22")
    assert cache.file_id(f) != a
