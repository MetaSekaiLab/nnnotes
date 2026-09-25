"""PlayerData memos: the same objects and errors as the lookups they replace, each read done once (synthetic)."""
import struct
from types import SimpleNamespace

import pytest

from nnnotes.player import PlayerData


def bare_player() -> PlayerData:
    p = PlayerData.__new__(PlayerData)
    p._by_type, p._nodes = {}, {}
    p._clear_memos()
    return p


class FakeShader:
    def __init__(self, name, file):
        self.name, self.assets_file, self.reads = name, SimpleNamespace(name=file), 0

    def read(self):
        self.reads += 1
        return SimpleNamespace(m_ParsedForm=SimpleNamespace(m_Name=self.name))


SHADERS = [("A", "f1"), ("Dup", "f1"), ("B", "f2"), ("Dup", "f2"), ("A", "f2"), ("C", "f1")]


def old_shader(by_type, name, file=None):
    """PlayerData.shader before the memo: a linear scan reading every Shader."""
    hits = [o for o in by_type.get("Shader", [])
            if o.read().m_ParsedForm.m_Name == name and (file is None or o.assets_file.name == file)]
    if len(hits) != 1:
        raise KeyError(f"shader {name!r}{f' in {file}' if file else ''}: {len(hits)} objects")
    return hits[0]


def outcome(f, *a):
    try:
        return "ok", f(*a)
    except KeyError as e:
        return "KeyError", str(e)


def test_shader_lookup_equals_the_scan_and_reads_each_shader_once():
    p = bare_player()
    objs = [FakeShader(n, f) for n, f in SHADERS]
    ref = [FakeShader(n, f) for n, f in SHADERS]
    p._by_type = {"Shader": objs}
    index = {id(r): i for i, r in enumerate(ref)}
    for _ in range(3):
        for name in ("A", "B", "C", "Dup", "Missing"):
            for file in (None, "f1", "f2", "f3"):
                got, want = outcome(p.shader, name, file), outcome(old_shader, {"Shader": ref}, name, file)
                assert got[0] == want[0]
                if got[0] == "ok":
                    assert objs.index(got[1]) == index[id(want[1])]
                else:
                    assert got[1] == want[1]
    assert [o.reads for o in objs] == [1] * len(objs)


class FakeScript:
    def __init__(self):
        self.reads = 0

    def read(self):
        self.reads += 1
        return SimpleNamespace(m_AssemblyName="Asm", m_Namespace="Ns", m_ClassName="Cls")


class FakeMono:
    def get_raw_data(self):
        return bytes(16) + struct.pack("<iq", 0, 7)


def test_script_is_read_once_per_object():
    p = bare_player()
    ms = FakeScript()
    derefs = []
    p.deref = lambda owner, pptr: derefs.append(pptr) or ms
    a, b = FakeMono(), FakeMono()
    assert p.script(a) == p.script(a) == p.script(b) == ("Asm", "Ns", "Cls")
    assert ms.reads == 2 and derefs == [{"m_FileID": 0, "m_PathID": 7}] * 2
    p.deref = lambda owner, pptr: None                 # a missing MonoScript raises on every call
    c = FakeMono()
    for _ in range(2):
        with pytest.raises(AttributeError):
            p.script(c)


def test_mono_returns_independent_copies_of_one_read():
    p = bare_player()
    reads = []
    p._read_mono = lambda o: reads.append(o) or {"m_Name": "x", "list": [{"v": 1}], "nested": {"a": [1, 2]}}
    o = object()
    first, second = p.mono(o), p.mono(o)
    assert first == second and first is not second
    assert first["list"] is not second["list"] and first["list"][0] is not second["list"][0]
    first["list"].append(9)
    first["nested"]["a"].clear()
    assert p.mono(o) == {"m_Name": "x", "list": [{"v": 1}], "nested": {"a": [1, 2]}}
    assert reads == [o]
    p.mono(object())
    assert len(reads) == 2


def test_graphics_is_built_once():
    p = bare_player()
    builds = []
    p._read_graphics = lambda: builds.append(1) or {"pipelines": {"P": {"m_RendererDataList": ["R"]}}}
    g1, g2 = p.graphics(), p.graphics()
    assert g1 == g2 and g1 is not g2 and g1["pipelines"] is not g2["pipelines"]
    g1["pipelines"]["P"]["m_RendererDataList"].append("X")
    assert p.graphics()["pipelines"]["P"]["m_RendererDataList"] == ["R"] and builds == [1]


class FakeResourceManager:
    def __init__(self, container):
        self.container, self.reads = container, 0

    def read_typetree(self):
        self.reads += 1
        return {"m_Container": self.container}


def test_resource_lookup_is_case_insensitive_and_parses_the_container_once():
    p = bare_player()
    objects = {1: "one", 2: "two", 3: "three"}
    rm = FakeResourceManager([("Foo/Bar", {"m_PathID": 1}), ("foo/BAR", {"m_PathID": 2}),
                              ("Single", {"m_PathID": 3}), ("Null", {"m_PathID": 0})])
    p.one = lambda type_name: rm
    p.deref = lambda owner, pptr: objects.get(pptr["m_PathID"])
    assert p.resource("single") == p.resource("SINGLE") == "three"
    for path, message in (("FOO/bar", "Resources path FOO/bar: 2 entries"), ("none", "Resources path none: 0 entries"),
                          ("null", "Resources path null: null reference")):
        with pytest.raises(KeyError) as e:
            p.resource(path)
        assert e.value.args[0] == message
    assert rm.reads == 1
