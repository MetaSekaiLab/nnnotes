import json
import re
import struct
from importlib import resources

import pytest
from UnityPy.helpers.TypeTreeHelper import read_typetree
from UnityPy.streams.EndianBinaryReader import EndianBinaryReader

from nnnotes import cli
from nnnotes.config import ConfigError
from nnnotes.player import (HEADER, TYPETREE_FORMAT, PlayerData, UnsupportedVersion, dump_typetrees, load_typetrees,
                            manifest_version_name, typetree_key, typetree_node)

TYPETREE_DIR = resources.files("nnnotes").joinpath("typetrees")
EMBEDDED = sorted(f.name[:-len(".json")] for f in TYPETREE_DIR.iterdir() if f.name.endswith(".json"))

# a small MonoBehaviour: header + one float field
ROWS = [[0, "Sample", "Base", 0],
        [1, "PPtr<GameObject>", "m_GameObject", 0], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
        [1, "UInt8", "m_Enabled", 16384],
        [1, "PPtr<MonoScript>", "m_Script", 0], [2, "int", "m_FileID", 0], [2, "SInt64", "m_PathID", 0],
        [1, "string", "m_Name", 16384],
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
        assert rows[0][0] == 0 and rows[0][2] == "Base" and rows[0][1] == key.split("|")[2]
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
    p.script = lambda o: KEY
    return p


def sample_classes(type_hash=HASH):
    return {typetree_key(*KEY): {"typeHash": type_hash, "nodes": ROWS}}


def test_mono_reads_with_the_embedded_node():
    tt = player(sample_classes()).mono(FakeObject(DATA))
    assert tt == {"m_Name": "test", "volume": 0.5}
    assert not set(HEADER) & set(tt)


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
