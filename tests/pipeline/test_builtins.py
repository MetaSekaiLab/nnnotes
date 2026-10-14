import zipfile
from unittest import mock

from nnnotes.pipeline import builtins, contract
from nnnotes.pipeline.contract import Input
from nnnotes.pipeline.depot import Depot

DATA = b"UnityFS data"
DEFAULTS = b"\0\0\0\0 a serialized file"


def apk(path, members):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("classes.dex", b"dex")
        for m, data in members.items():
            z.writestr(builtins.DATA_DIR + m, data)
    return path


def test_the_built_in_files_of_an_apk_are_subjects(tmp_path):
    depot = Depot(tmp_path / "s")
    a = apk(tmp_path / "base.apk", {"data.unity3d": DATA, "Resources/unity default resources": DEFAULTS})
    got = builtins.inputs(depot, a)
    assert got == {
        "apk/Resources/unity default resources": Input(
            "bundle", contract.sha256(DEFAULTS), len(DEFAULTS), "unity default resources", ({"kind": "depot"},)
        ),
        "apk/data.unity3d": Input("bundle", contract.sha256(DATA), len(DATA), "data.unity3d", ({"kind": "depot"},)),
    }
    assert depot.read(contract.sha256(DEFAULTS)) == DEFAULTS
    with mock.patch.object(builtins, "ApkSet", side_effect=AssertionError("read again")):
        assert builtins.inputs(Depot(tmp_path / "s"), a) == got  # remembered by the APK's content
    assert builtins.inputs(depot, None) == {}


def test_an_apk_directory_and_its_members(tmp_path):
    d = tmp_path / "apk"
    d.mkdir()
    apk(d / "base.apk", {"data.unity3d": DATA, "datapack.unity3d": b"UnityFS pack"})
    assert sorted(builtins.inputs(Depot(tmp_path / "s"), d)) == ["apk/data.unity3d", "apk/datapack.unity3d"]


def test_serialized_names():
    assert builtins.serialized_name("apk/Resources/unity default resources") == "unity default resources"
    assert builtins.serialized_name("apk/data.unity3d") == "data.unity3d"
    assert builtins.serialized_name("card_1") is None
    assert builtins.is_builtin("apk/data.unity3d") and not builtins.is_builtin("card_1")
