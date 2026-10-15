"""The note highway products assembled from synthetic object tables (content.livenotes)."""

import pytest

from nnnotes.content import livenotes, prefabs
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import livenotes as ln

FLICKS = sorted(livenotes.FLICK_SKIN_UNITS)
ARROW = [{"path": "arrow", "attribute": "m_Color.a"}]
GLOW = [{"path": "glow", "attribute": "m_Color.a"}]


def objects_of(tmp_path, keys, objs=None) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(ln.table(depot, keys, objs), depot)


def test_the_notes_document(tmp_path):
    record = livenotes.notes_record(ln.master())
    doc, keep, roles = livenotes.notes(objects_of(tmp_path, ln.NOTES_KEYS), record)
    assert doc["format"] == livenotes.NOTES_FORMAT
    assert doc["prefabs"] == {n: ln.prefab_id(n) for n in livenotes.NOTE_PREFABS}
    assert doc["laneEffect"] == ln.LANE
    # every Animator: its controller's clips (a clip the table lacks left out) and the loop clip of its time sync
    assert doc["bindings"] == {
        **{ln.prefab_id(v): {f"{v}/anim_root": {ln.FLICK_CLIP: ARROW, ln.LOOP_CLIP: ARROW}} for v in FLICKS},
        ln.LANE_FX: {"lane_tap/root": {ln.FX_CLIP: GLOW}},
        ln.EFFECT_FX: {"note_normal/root": {ln.FX_CLIP: GLOW}},  # in the table, so it is bound as well
    }
    assert doc["shaders"] == {"Sirius/Sprite": ln.SHADER}
    assert roles == {ln.SHADER: {"json", "index", "program:gles3/s0p0_progVertex_0", "program:gles3/s0p1_progVertex_1"}}
    assert ln.SCRIPT not in keep and keep == set(ln.objects()) - {ln.SCRIPT}


def test_the_notes_settings(tmp_path):
    record = livenotes.notes_record(ln.master())
    assert all(r["_presetId"] == 1 and r["_optionItemType"] != 999 for r in record["MasterOptionDefault"])
    assert [r["_key"] for r in record["MasterLiveSettings"]] == [
        "note_speed_min",
        "note_speed_max",
        "note_speed_view_min",
        "note_speed_view_max",
        "slide_offset_min_note_lane_width",
    ]
    s = livenotes.notes_settings(record)
    assert s["optionDefaults"] == {
        "LiveMusicVolume": "100",  # an item the player offers
        "LiveQuality": "1",
        "NoteDesignId": "2",
        "NoteEffectId": "1",
        "NoteSpeed": "5.00",
    }
    assert s["optionRanges"] == {"NoteSpeed": [100, 1200]}
    assert (s["skin"], s["effect"], s["laneEffect"]) == ("skin002", "effect001", "effect001")
    assert s["skins"] == {"1": "skin001", "2": "skin002"} and s["effects"] == {"1": "effect001", "2": "effect001Simple"}
    assert s["noteDisplayTimeMs"] == livenotes.display_time_ms(5.0, 1.0, 12.0, 0.4, 7.0)
    assert s["laneCount"] == 24 and "judgementSprites" not in s


def test_a_skin_binds_the_arrow_loop_of_its_flick_units(tmp_path):
    doc, keep, _ = livenotes.noteskin(objects_of(tmp_path, ln.SKIN_KEYS), "skin001")
    assert (doc["format"], doc["name"], doc["skin"]) == (livenotes.NOTES_FORMAT, "skin001", ln.SKIN)
    # the flick unit's loop plays on the flick view's time sync Animator; the left unit has none, the right is unset
    assert doc["bindings"] == {ln.prefab_id("flick_note_view"): {"flick_note_view/anim_root": {ln.SKIN_CLIP: ARROW}}}
    assert ln.SCRIPT not in keep


def test_an_effect_set(tmp_path):
    doc, _, roles = livenotes.noteeffect(objects_of(tmp_path, ln.EFFECT_KEYS), "effect001Light")
    assert (doc["name"], doc["settings"]) == ("effect001Light", ln.EFFECT)
    assert doc["bindings"][ln.EFFECT_FX] == {"note_normal/root": {ln.FX_CLIP: GLOW}}
    assert doc["shaders"] == {"Sirius/Sprite": ln.SHADER} and set(roles) == {ln.SHADER}


def test_a_key_names_its_objects(tmp_path):
    record = livenotes.notes_record(ln.master())
    keys = {**ln.NOTES_KEYS, livenotes.LANE_EFFECT_KEY: [ln.LANE, ln.EFFECT]}  # the settings by their class
    assert livenotes.notes(objects_of(tmp_path, keys), record)[0]["laneEffect"] == ln.LANE
    with pytest.raises(ValueError, match="LiveLaneEffectAssetSettings: 0 LiveLaneEffectAssetSettings"):
        livenotes.notes(objects_of(tmp_path, {**keys, livenotes.LANE_EFFECT_KEY: [ln.EFFECT]}), record)
    tap = livenotes.NOTE_PREFABS["tap_note_view"]
    with pytest.raises(ValueError, match="tap_note_view: 2 prefab GameObjects"):
        livenotes.notes(objects_of(tmp_path, {**keys, tap: [ln.prefab_id("tap_note_view"), ln.LANE_FX]}), record)
    with pytest.raises(KeyError, match="not a key of the product"):
        livenotes.noteeffect(objects_of(tmp_path, ln.EFFECT_KEYS), "effect001")


def test_a_time_sync_plays_on_an_animator(tmp_path):
    objs = ln.objects()
    view = objs[ln.prefab_id("flick_note_view")][2]["json"]
    view["nodes"][1]["components"] = view["nodes"][1]["components"][1:]  # the time sync's Animator removed
    with pytest.raises(ValueError, match="flick_note_view: 0 components"):
        livenotes.noteskin(objects_of(tmp_path, ln.SKIN_KEYS, objs), "skin001")


def test_copies_of_a_shader_are_one_and_different_ones_of_a_name_none(tmp_path):
    objs = ln.objects()
    objs["CAB-z:1"] = objs[ln.SHADER]  # a copy in another bundle
    doc, _, roles = livenotes.noteeffect(objects_of(tmp_path, ln.EFFECT_KEYS, objs), "effect001Light")
    assert doc["shaders"] == {"Sirius/Sprite": ln.SHADER} and roles["CAB-z:1"] == roles[ln.SHADER]
    cls, name, files = objs[ln.SHADER]
    objs["CAB-z:1"] = (cls, name, {**files, "json": {"name": "Sirius/Sprite", "other": 1}})
    objects = objects_of(tmp_path, ln.EFFECT_KEYS, objs)
    doc, _, roles = livenotes.noteeffect(objects, "effect001Light")
    assert doc["shaders"] == {} and set(roles) == {ln.SHADER, "CAB-z:1"}  # a lookup by name finds neither
    assert prefabs.shader_table(objects, found=["CAB-z:1"])[0] == {"Sirius/Sprite": "CAB-z:1"}


def test_skin_and_effect_names_are_url_safe():
    assert livenotes.noteskin_keys("skin001")[-1] == "Live/Note/skin001/LiveNoteSkinAsset"
    with pytest.raises(ValueError, match="not a note skin or effect set name"):
        livenotes.noteskin_keys("../x")
