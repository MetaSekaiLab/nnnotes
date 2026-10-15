"""The story asset document assembled from synthetic object tables (content.storyasset)."""

import re

import pytest

from nnnotes.content import storyasset
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import livenotes as ln

KEY = "Adv/Stage/bg001"
EMPTY = "Adv/Stage/empty"
PREFAB = ln.prefab_id("flick_note_view")
ARROW = [{"path": "arrow", "attribute": "m_Color.a"}]
# a small table: a prefab whose Animator plays a controller clip and a time sync clip, with its material, shader and a
# MonoScript
SMALL = {
    oid: entry
    for oid, entry in ln.objects().items()
    if oid in {PREFAB, ln.FLICK_CTL, ln.FLICK_CLIP, ln.LOOP_CLIP, ln.MAT, ln.SHADER, ln.SCRIPT}
}


def objects_of(tmp_path, keys, objs=None) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(ln.table(depot, keys, objs), depot)


def test_manifest_name_replaces_slashes():
    assert storyasset.manifest_name(KEY) == "Adv~Stage~bg001"
    assert storyasset.manifest_name("bg001") == "bg001"


@pytest.mark.parametrize("key", ["Adv/Stage bg001", "Adv~Stage/bg001"])
def test_manifest_name_rejects_other_keys(key):
    with pytest.raises(ValueError, match=re.escape(key)):
        storyasset.manifest_name(key)


def test_the_document_of_a_prefab_key(tmp_path):
    keys = {KEY: [PREFAB, "CAB-n:999"]}  # 999 is not in the table: not one of the key's objects
    doc, _, _ = storyasset.asset(objects_of(tmp_path, keys, SMALL), KEY)
    assert doc == {
        "format": storyasset.ASSET_FORMAT,
        "key": KEY,
        "objects": [PREFAB],
        "bindings": {PREFAB: {"flick_note_view/anim_root": {ln.FLICK_CLIP: ARROW, ln.LOOP_CLIP: ARROW}}},
        "shaders": {"Sirius/Sprite": ln.SHADER},
    }
    assert list(doc) == ["format", "key", "objects", "bindings", "shaders"]


def test_keep_excludes_monoscripts_and_roles_hold_the_shader_roles(tmp_path):
    _, keep, roles = storyasset.asset(objects_of(tmp_path, {KEY: [PREFAB]}, SMALL), KEY)
    assert keep == set(SMALL) - {ln.SCRIPT}
    assert roles == {
        ln.SHADER: {"json", "index", "program:gles3/s0p0_progVertex_0", "program:gles3/s0p1_progVertex_1"},
    }


def test_a_key_without_objects_in_the_table_raises(tmp_path):
    keys = {KEY: [PREFAB], EMPTY: ["CAB-n:999"]}
    with pytest.raises(ValueError, match="Adv/Stage/empty: no objects of the key"):
        storyasset.asset(objects_of(tmp_path, keys, SMALL), EMPTY)
