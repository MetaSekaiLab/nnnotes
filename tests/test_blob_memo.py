"""The typetree blob memo gives the trees a fresh parse gives (synthetic blobs in UnityPy's own blob format)."""
import random

import pytest
from UnityPy.helpers.TypeTreeNode import TypeTreeNode, get_common_strings
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

from nnnotes import cache, unity

ORIGINAL = TypeTreeNode.parse_blob.__func__._original
COMMON = list(get_common_strings().values())


def random_tree(rng: random.Random, version: int) -> TypeTreeNode:
    def node(level):
        n = TypeTreeNode(level, rng.choice(COMMON + ["MyType", "Vector3f", "PPtr<$Foo>"]),
                         rng.choice(COMMON + ["m_Field", "_custom", "data", "x"]), rng.choice([-1, 1, 4, 8, 12]),
                         rng.randint(1, 3), m_TypeFlags=rng.choice([0, 1]), m_Index=rng.randint(0, 999),
                         m_MetaFlag=rng.choice([0, 0x4000, 0x8000]),
                         m_RefTypeHash=rng.getrandbits(64) if version >= 19 else None)
        if level < 4:
            n.m_Children = [node(level + 1) for _ in range(rng.randint(0, 4))]
        return n
    return node(0)


def blob(tree: TypeTreeNode, version: int, endian: str, pad: bytes = b"") -> bytes:
    w = EndianBinaryWriter(endian=endian)
    w.write(pad)
    tree.dump_blob(w, version)
    w.write(b"\x07TAIL")
    return w.bytes


def parse(fn, data: bytes, version: int, endian: str, pad: int):
    r = EndianBinaryReader(data, endian=endian)
    r.Position = pad
    return fn(TypeTreeNode, r, version), r.Position


def same_tree(a: TypeTreeNode, b: TypeTreeNode) -> bool:
    return a.to_dict() == b.to_dict() and len(a.m_Children) == len(b.m_Children) and \
        all(same_tree(x, y) for x, y in zip(a.m_Children, b.m_Children))


@pytest.fixture(autouse=True)
def memo():
    cache.configure(enabled=True)
    unity.clear_blob_memo()
    yield
    unity.clear_blob_memo()


@pytest.mark.parametrize("version", [17, 19, 21, 22])
@pytest.mark.parametrize("endian", ["<", ">"])
def test_memo_parses_as_unitypy_and_reuses_the_tree(version, endian):
    rng = random.Random(version * 2 + (endian == ">"))
    memo_fn = TypeTreeNode.parse_blob.__func__
    for i in range(30):
        tree = random_tree(rng, version)
        data = blob(tree, version, endian, pad=b"\x00" * i)
        want, want_end = parse(ORIGINAL, data, version, endian, i)
        got, end = parse(memo_fn, data, version, endian, i)
        again, end2 = parse(memo_fn, blob(tree, version, endian, pad=b"\x01" * (i + 3)), version, endian, i + 3)
        assert same_tree(got, want) and end == want_end and end2 == end + 3
        assert again is got                                      # the same blob elsewhere: the tree parsed before
    s = unity.blob_memo_stats()
    assert s["installed"] and s["hits"] >= 30 and s["misses"] <= 30


def test_a_different_blob_is_parsed_on_its_own():
    rng = random.Random(7)
    tree = random_tree(rng, 22)
    data = bytearray(blob(tree, 22, "<"))
    a, _ = parse(TypeTreeNode.parse_blob.__func__, bytes(data), 22, "<", 0)
    data[9] ^= 1                                                 # one bit of the first node record
    b, _ = parse(TypeTreeNode.parse_blob.__func__, bytes(data), 22, "<", 0)
    want, _ = parse(ORIGINAL, bytes(data), 22, "<", 0)
    assert b is not a and same_tree(b, want) and not same_tree(a, b)
    c, _ = parse(TypeTreeNode.parse_blob.__func__, blob(tree, 22, "<"), 21, "<", 0)   # other parse arguments
    assert c is not a


def test_disabled_caches_parse_every_time():
    cache.configure(enabled=False)
    tree = random_tree(random.Random(3), 22)
    data = blob(tree, 22, "<")
    a, _ = parse(TypeTreeNode.parse_blob.__func__, data, 22, "<", 0)
    b, _ = parse(TypeTreeNode.parse_blob.__func__, data, 22, "<", 0)
    assert a is not b and same_tree(a, b) and unity.blob_memo_stats()["trees"] == 0
    cache.configure(enabled=True)


def test_least_recent_trees_are_dropped(monkeypatch):
    monkeypatch.setattr(unity, "BLOB_MEMO_MAX", 3)
    rng = random.Random(5)
    blobs = [blob(random_tree(rng, 22), 22, "<") for _ in range(5)]
    first = [parse(TypeTreeNode.parse_blob.__func__, b, 22, "<", 0)[0] for b in blobs]
    assert unity.blob_memo_stats()["trees"] == 3
    assert parse(TypeTreeNode.parse_blob.__func__, blobs[4], 22, "<", 0)[0] is first[4]
    assert parse(TypeTreeNode.parse_blob.__func__, blobs[0], 22, "<", 0)[0] is not first[0]
