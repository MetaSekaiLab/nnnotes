import importlib.util
import json
import re
import struct
import sys
from importlib import resources
from pathlib import Path

import pytest
from UnityPy.helpers.TypeTreeHelper import read_typetree
from UnityPy.streams.EndianBinaryReader import EndianBinaryReader

from nnnotes import cli
from nnnotes.config import ConfigError
from nnnotes.player import (HEADER, TYPETREE_FORMAT, PlayerData, UnsupportedVersion, dump_typetrees, load_typetrees,
                            manifest_version_name, typetree_key, typetree_node)

TYPETREE_DIR = resources.files("nnnotes").joinpath("typetrees")
EMBEDDED = sorted(f.name[:-len(".json")] for f in TYPETREE_DIR.iterdir() if f.name.endswith(".json"))
ALIGN = 0x4000

# a small MonoBehaviour in Unity's layout: header + one float field
ROWS = [[0, "MonoBehaviour", "Base", 0],
        [1, "PPtr<GameObject>", "m_GameObject", 0], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
        [1, "UInt8", "m_Enabled", ALIGN],
        [1, "PPtr<MonoScript>", "m_Script", 0], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
        [1, "string", "m_Name", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "char", "data", 0],
        [1, "float", "volume", 0]]
DATA = (struct.pack("<iq", 0, 0) + b"\x01\x00\x00\x00" + struct.pack("<iq", 0, 5)
        + struct.pack("<i", 4) + b"test" + struct.pack("<f", 0.5))
KEY = ("Game.Runtime", "Game.Sound", "Sample")
HASH = "00112233445566778899aabbccddeeff"


# ---------------------------------------------------------------- the embedded files
def test_embedded_files_exist():
    assert EMBEDDED


@pytest.mark.parametrize("version", EMBEDDED)
def test_embedded_file_is_well_formed(version):
    text = TYPETREE_DIR.joinpath(f"{version}.json").read_bytes().decode("utf-8")
    assert "\r" not in text and text.endswith("\n")
    doc = load_typetrees(version)
    assert doc["format"] == TYPETREE_FORMAT and doc["unityVersion"] == version
    assert isinstance(doc["gameVersion"], str) and doc["gameVersion"]
    assert dump_typetrees(doc) == text                      # deterministic: sorted keys, one class per line
    assert doc["classes"]
    for key, entry in doc["classes"].items():
        assert len(key.split("|")) == 3 and all(key.split("|")[1:])
        assert set(entry) == {"typeHash", "nodes"}
        assert re.fullmatch(r"[0-9a-f]{32}", entry["typeHash"])
        rows = entry["nodes"]
        assert rows[0] == [0, "MonoBehaviour", "Base", 0]
        for prev, row in zip(rows, rows[1:]):
            assert len(row) == 4 and isinstance(row[1], str) and isinstance(row[2], str)
            assert 1 <= row[0] <= prev[0] + 1
        names = [r[2] for r in rows if r[0] == 1]
        assert names[:4] == ["m_GameObject", "m_Enabled", "m_Script", "m_Name"]
        node = typetree_node(rows)
        assert [c.m_Name for c in node.m_Children] == names


def test_load_typetrees_unknown_version():
    assert load_typetrees("1.0.0f1") is None
    assert load_typetrees("../x") is None
    assert load_typetrees("") is None


def test_dump_is_sorted_and_compact():
    doc = {"unityVersion": "1", "format": 1, "gameVersion": "2",
           "classes": {"b|n|B": {"typeHash": HASH, "nodes": [[0, "B", "Base", 0]]},
                       "a|n|A": {"nodes": [[0, "A", "Base", 0]], "typeHash": HASH}}}
    text = dump_typetrees(doc)
    assert text.splitlines() == [
        '{"format":1,"gameVersion":"2","unityVersion":"1","classes":{',
        f'"a|n|A":{{"nodes":[[0,"A","Base",0]],"typeHash":"{HASH}"}},',
        f'"b|n|B":{{"nodes":[[0,"B","Base",0]],"typeHash":"{HASH}"}}',
        "}}"]
    assert json.loads(text) == doc


# ---------------------------------------------------------------- reading with the embedded typetrees
class _Type:
    def __init__(self, type_hash):
        self.old_type_hash = bytes.fromhex(type_hash)


class FakeObject:
    """A MonoBehaviour object reader over raw bytes (the typetree read and size check are UnityPy's)."""

    def __init__(self, data: bytes, type_hash: str = HASH):
        self.data = data
        self.serialized_type = _Type(type_hash)

    def read_typetree(self, node, check_read=True):
        return read_typetree(node, EndianBinaryReader(self.data, "<"), as_dict=True, byte_size=len(self.data),
                             check_read=check_read)


def player(classes=None, game_version="9.9.9"):
    p = PlayerData.__new__(PlayerData)
    p.unity_version = "6000.0.0f1"
    p.game_version = game_version
    p.typetrees = None if classes is None else {"format": 1, "unityVersion": p.unity_version,
                                                "gameVersion": "1.0.0", "classes": classes}
    p._nodes = {}
    p._clear_memos()
    p.script = lambda o: KEY
    return p


def sample_classes(type_hash=HASH):
    return {typetree_key(*KEY): {"typeHash": type_hash, "nodes": ROWS}}


def test_mono_reads_with_the_embedded_node():
    tt = player(sample_classes()).mono(FakeObject(DATA))
    assert tt == {"m_Name": "test", "volume": 0.5}
    assert not set(HEADER) & set(tt)


def test_the_header_is_read_at_unitys_offsets():
    tt = FakeObject(DATA).read_typetree(typetree_node(ROWS))
    assert tt["m_Enabled"] == 1 and tt["m_Script"] == {"m_FileID": 0, "m_PathID": 5} and tt["m_Name"] == "test"


# ---------------------------------------------------------------- Unity's node layout
CSHARP_NAMES = ("Bounds", "Color", "Color32", "LayerMask", "Quaternion", "Rect", "Vector2", "Vector2Int", "Vector3",
                "Vector4")


def nodes(rows):
    """[(row, parent row or None, child rows)] of [level, type, name, meta flag] rows."""
    stack, parents, kids = [], [], [[] for _ in rows]
    for i, r in enumerate(rows):
        del stack[r[0]:]
        parents.append(rows[stack[-1]] if stack else None)
        if stack:
            kids[stack[-1]].append(r)
        stack.append(i)
    return list(zip(rows, parents, kids))


def unity_layout_errors(rows) -> list:
    """Where the rows leave the layout Unity serializes MonoBehaviour typetrees with (as in bundles built with
    typetrees): root `MonoBehaviour`; every string in full form (string / Array / int size / char data); built-in
    structs under their native names; and the align flag (0x4000) exactly where Unity puts it: on m_Enabled, on the
    Array of a string, on the Array of a vector unless its elements are PPtrs, on a vector of bytes, and on fields
    smaller than 4 bytes; not on the root, the rest of the header, strings, classes, PPtrs, the Array of an array of
    a serializable class, or an array's size and data."""
    errors = []
    header = {"m_GameObject": False, "m_Enabled": True, "m_Script": False, "m_Name": False}
    ns = nodes(rows)
    kids_of = {id(r): k for r, _, k in ns}
    for (lv, ty, nm, mf), parent, kids in ns:
        align = bool(mf & ALIGN)
        if ty in CSHARP_NAMES:
            errors.append(f"{nm}: C# type name {ty}")
        if lv == 0:
            want = False
            if (ty, nm) != ("MonoBehaviour", "Base"):
                errors.append(f"root {ty} {nm}")
        elif ty == "string":
            want = False
            if [k[1:3] for k in kids] != [["Array", "Array"]] or                     [k[1:3] for k in kids_of[id(kids[0])]] != [["int", "size"], ["char", "data"]]:
                errors.append(f"{nm}: string not in full form")
        elif ty == "Array":
            if [k[2] for k in kids] != ["size", "data"]:
                errors.append(f"{parent[2]}: Array children {[k[2] for k in kids]}")
                continue
            elem = kids[1][1]
            if parent[1] == "string":
                want = True
            elif parent[1] == "vector":
                want = not elem.startswith("PPtr<")
            else:                                        # an array of a serializable class keeps the class name
                want = False
                if parent[1] != elem:
                    errors.append(f"{parent[2]}: array container {parent[1]} of {elem}")
        elif parent[1] == "Array":                       # size, data
            want = False
        elif ty == "vector":
            arr = kids[0] if len(kids) == 1 and kids[0][1] == "Array" else None
            if arr is None:
                errors.append(f"{nm}: vector without an Array")
                continue
            want = kids_of[id(arr)][1][1] in ("UInt8", "SInt8")
        elif lv == 1 and nm in header and parent[0] == 0:
            want = header[nm]
        elif not kids:
            want = ty in ("UInt8", "SInt8", "UInt16", "SInt16")
        else:                                            # classes, PPtrs
            want = False
        if align != want:
            label = f"{parent[2]}/Array" if ty == "Array" else nm
            errors.append(f"{label} ({ty}): align {align}, Unity {want}")
    return errors


@pytest.mark.parametrize("version", EMBEDDED)
def test_embedded_rows_have_unitys_layout(version):
    doc = load_typetrees(version)
    for key, entry in doc["classes"].items():
        assert unity_layout_errors(entry["nodes"]) == [], key
    strings = [r for e in doc["classes"].values() for r in e["nodes"] if r[1] == "string"]
    assert len(strings) >= len(doc["classes"])           # m_Name at least


def test_the_layout_check_catches_the_generators_forms():
    assert unity_layout_errors(ROWS) == []
    leaf = ROWS[:9] + [[1, "string", "m_Name", ALIGN]] + ROWS[12:]
    assert "m_Name: string not in full form" in unity_layout_errors(leaf)
    assert "m_Name (string): align True, Unity False" in unity_layout_errors(leaf)
    shifted = [r[:3] + [ALIGN if r[2] in ("m_GameObject", "m_Script") else 0 if r[2] == "m_Enabled" else r[3]]
               for r in ROWS]
    assert unity_layout_errors(shifted) == ["m_GameObject (PPtr<GameObject>): align True, Unity False",
                                            "m_Enabled (UInt8): align False, Unity True",
                                            "m_Script (PPtr<MonoScript>): align True, Unity False"]
    classes = ROWS + [[1, "Item", "_items", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0],
                      [3, "Item", "data", 0], [4, "UInt8", "on", ALIGN],
                      [1, "vector", "_refs", 0], [2, "Array", "Array", 0], [3, "int", "size", 0],
                      [3, "PPtr<$Mesh>", "data", 0], [4, "int", "m_FileID", 0], [4, "SInt64", "m_PathID", 0],
                      [1, "vector", "_bytes", ALIGN], [2, "Array", "Array", ALIGN], [3, "int", "size", 0],
                      [3, "UInt8", "data", 0],
                      [1, "Vector2", "_size", 0], [2, "float", "x", 0], [2, "float", "y", 0]]
    assert unity_layout_errors(classes) == ["_items/Array (Array): align True, Unity False",
                                            "_size: C# type name Vector2"]


def test_unsupported_is_a_config_error():
    assert issubclass(UnsupportedVersion, ConfigError)


def check_unsupported(e, *parts):
    msg = str(e.value)
    for part in ("MonoBehaviour Game.Sound.Sample (Game.Runtime)", "Unity 6000.0.0f1", "is not supported yet",
                 *parts):
        assert part in msg, (part, msg)


def test_missing_class():
    with pytest.raises(UnsupportedVersion) as e:
        player({}).mono(FakeObject(DATA))
    check_unsupported(e, "no embedded typetree for this class", "Game version 9.9.9",
                      "embedded typetrees from game version 1.0.0")


def test_missing_unity_version():
    with pytest.raises(UnsupportedVersion) as e:
        player(None, game_version=None).mono(FakeObject(DATA))
    check_unsupported(e, "no embedded typetrees for Unity 6000.0.0f1", "Game version unknown")


def test_type_hash_differs():
    with pytest.raises(UnsupportedVersion) as e:
        player(sample_classes()).mono(FakeObject(DATA, type_hash="ff" * 16))
    check_unsupported(e, "type hash differs")


@pytest.mark.parametrize("data", [DATA + b"\x00" * 4, DATA[:-4]])
def test_read_must_consume_the_serialized_size(data):
    with pytest.raises(UnsupportedVersion) as e:
        player(sample_classes()).mono(FakeObject(data))
    check_unsupported(e, "does not fit the serialized data")


# ---------------------------------------------------------------- game version from the manifest
def axml(strings: list[str], version_index: int | None, utf8: bool = False) -> bytes:
    """A binary AndroidManifest.xml with a string pool and a <manifest> start element."""
    blobs = []
    for s in strings:
        if utf8:
            b = s.encode("utf-8")
            blobs.append(bytes([len(s), len(b)]) + b + b"\x00")
        else:
            blobs.append(struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00")
    offsets, pos = [], 0
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
    body = struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(blobs)
    body += b"\x00" * (-len(body) % 4)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, 28 + len(body), len(strings), 0, 0x100 if utf8 else 0,
                       28 + 4 * len(strings), 0) + body
    attrs = b""
    if version_index is not None:
        attrs = struct.pack("<IIIHBBI", 0xFFFFFFFF, strings.index("versionName"), version_index, 8, 0, 3,
                            version_index)
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, strings.index("manifest"), 20, 20, 1 if attrs else 0, 0, 0, 0)
    elem_body = struct.pack("<II", 1, 0xFFFFFFFF) + ext + attrs
    elem = struct.pack("<HHI", 0x0102, 16, 8 + len(elem_body)) + elem_body
    chunks = pool + elem
    return struct.pack("<HHI", 0x0003, 8, 8 + len(chunks)) + chunks


@pytest.mark.parametrize("utf8", [False, True])
def test_manifest_version_name(utf8):
    assert manifest_version_name(axml(["versionName", "manifest", "1.2.3"], 2, utf8)) == "1.2.3"
    assert manifest_version_name(axml(["versionName", "manifest"], None, utf8)) is None


def test_manifest_version_name_garbage():
    assert manifest_version_name(b"") is None
    assert manifest_version_name(b"\x03\x00\x08\x00" + b"\xff" * 40) is None


# ---------------------------------------------------------------- the removed DummyDll setting
def run(argv, capsys):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def test_dummy_dll_flag_is_gone(capsys, tmp_path):
    code, _, _ = run(["--dummy-dll", str(tmp_path), "player", "-o", str(tmp_path / "p.json")], capsys)
    assert code == 2
    code, out, _ = run(["--help"], capsys)
    assert code == 0 and "dummy" not in out.lower()


def test_leftover_dummy_dll_key_is_ignored(capsys, tmp_path):
    conf = tmp_path / "c.toml"
    conf.write_text('[paths]\ndummy_dll = "somewhere"\n', encoding="utf-8")
    code, _, err = run(["--config", str(conf), "player", "-o", str(tmp_path / "p.json")], capsys)
    assert code == 2 and "paths.apk" in err and "dummy" not in err


# ---------------------------------------------------------------- the generator's rows -> Unity's layout
def gen_typetrees():
    """scripts/gen_typetrees.py (a maintainer tool, not part of the package)."""
    if "gen_typetrees" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "gen_typetrees", Path(__file__).resolve().parents[1] / "scripts" / "gen_typetrees.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gen_typetrees"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["gen_typetrees"]


# the header as TypeTreeGeneratorAPI writes it
GEN_HEADER = [[0, "Sample", "Base", 0],
              [1, "PPtr<GameObject>", "m_GameObject", ALIGN], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
              [1, "UInt8", "m_Enabled", 0],
              [1, "PPtr<MonoScript>", "m_Script", ALIGN], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
              [1, "string", "m_Name", ALIGN]]


def test_unity_rows_header_and_strings():
    g = gen_typetrees()
    assert g.unity_rows(GEN_HEADER + [[1, "float", "volume", 0]]) == ROWS
    rows = g.unity_rows(GEN_HEADER + [[1, "string", "label", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0],
                                      [3, "char", "data", 0], [1, "UInt8", "on", ALIGN]])
    assert rows[12:] == [[1, "string", "label", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0],
                         [3, "char", "data", 0], [1, "UInt8", "on", ALIGN]]
    assert unity_layout_errors(rows) == []


def test_unity_rows_arrays_and_builtin_structs():
    g = gen_typetrees()
    fields = [
        [1, "string", "_names", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "string", "data", 0],
        [1, "Color", "_colors", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "Color", "data", 0],
        [4, "float", "r", 0], [4, "float", "g", 0], [4, "float", "b", 0], [4, "float", "a", 0],
        [1, "Item", "_items", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "Item", "data", 0],
        [4, "int", "id", 0],
        [1, "vector", "_meshes", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0],
        [3, "PPtr<$Mesh>", "data", 0], [4, "int", "m_FileID", 0], [4, "SInt64", "m_PathID", 0],
        [1, "vector", "_flags", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "UInt8", "data", 0],
        [1, "Vector3", "_offset", 0], [2, "float", "x", 0], [2, "float", "y", 0], [2, "float", "z", 0],
        [1, "RenderingLayerMask", "_layers", 0],
        [1, "PropertyName", "_exposed", 0], [2, "string", "id", 0], [3, "Array", "Array", ALIGN],
        [4, "int", "size", 0], [4, "char", "data", 0],
    ]
    rows = g.unity_rows(GEN_HEADER + fields)
    assert rows[12:] == [
        [1, "vector", "_names", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "string", "data", 0],
        [4, "Array", "Array", ALIGN], [5, "int", "size", 0], [5, "char", "data", 0],
        [1, "vector", "_colors", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "ColorRGBA", "data", 0],
        [4, "float", "r", 0], [4, "float", "g", 0], [4, "float", "b", 0], [4, "float", "a", 0],
        [1, "Item", "_items", 0], [2, "Array", "Array", 0], [3, "int", "size", 0], [3, "Item", "data", 0],
        [4, "int", "id", 0],
        [1, "vector", "_meshes", 0], [2, "Array", "Array", 0], [3, "int", "size", 0],
        [3, "PPtr<$Mesh>", "data", 0], [4, "int", "m_FileID", 0], [4, "SInt64", "m_PathID", 0],
        [1, "vector", "_flags", ALIGN], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "UInt8", "data", 0],
        [1, "Vector3f", "_offset", 0], [2, "float", "x", 0], [2, "float", "y", 0], [2, "float", "z", 0],
        [1, "RenderingLayerMask", "_layers", 0], [2, "unsigned int", "m_Bits", 0],
        [1, "string", "_exposed", 0], [2, "string", "id", ALIGN], [3, "Array", "Array", ALIGN],
        [4, "int", "size", 0], [4, "char", "data", 0],
    ]
    # the checker's rules hold except for the ExposedReference name (a string holding a string)
    assert unity_layout_errors(rows) == ["_exposed: string not in full form", "id (string): align True, Unity False"]


@pytest.mark.parametrize("fields, message", [
    ([[1, "managedReference", "_ref", 0]], "[SerializeReference]"),
    ([[1, "Item", "_items", 0], [2, "Array", "Array", ALIGN], [3, "int", "size", 0], [3, "Other", "data", 0]],
     "array container Item of Other"),
    ([[1, "vector", "_v", 0], [2, "Array", "Array", ALIGN], [3, "int", "count", 0]], "unexpected Array children"),
])
def test_unity_rows_refuses_what_it_has_no_layout_for(fields, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        gen_typetrees().unity_rows(GEN_HEADER + fields)
    with pytest.raises(ValueError, match="unexpected MonoBehaviour header"):
        gen_typetrees().unity_rows(GEN_HEADER[:1] + GEN_HEADER[5:])
