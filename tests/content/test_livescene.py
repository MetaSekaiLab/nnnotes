"""The live scene product assembled from a synthetic object table (content.livescene)."""

import pytest

from nnnotes.content import livescene
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import livescene as ls

LANE = [{"path": "LiveGameLane", "attribute": "m_Color.a"}]


def objects_of(tmp_path, record, objs=None) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(ls.table(depot, record, objs), depot)


def record():
    return livescene.scene_record(ls.master())


def test_the_scene_document(tmp_path):
    doc, keep, roles = livescene.scene(objects_of(tmp_path, record()), record())
    assert doc["format"] == livescene.SCENE_FORMAT
    assert doc["scene"] == [ls.LIVE, ls.GAME, ls.DIRECTOR] and doc["renderSettings"] == ls.RENDER
    assert doc["graphics"] == {"graphics": ls.GRAPHICS, "quality": ls.QUALITY, "player": ls.PLAYER}
    assert doc["laneSkins"] == {"skin001": ls.SKIN}
    assert doc["prefabs"] == {"laneLine": ls.LANE_LINE, "judgeEffectView": ls.JUDGE_EFFECT, "barLineView": ls.BAR_LINE}
    assert doc["spriteAssets"] == {"combo": ls.COMBO_ASSET, "judgement": ls.JUDGEMENT_ASSET}
    assert ls.SCRIPT not in keep and ls.TL in keep and ls.UI_COPY in roles


def test_each_band_with_a_timeline_binds_its_tracks(tmp_path):
    doc, _, _ = livescene.scene(objects_of(tmp_path, record()), record())
    assert list(doc["bands"]) == ["1"]  # band 2 has no start timeline in the catalog
    band = doc["bands"]["1"]
    assert band["timeline"] == ls.TL and band["background"] == ls.BACKGROUND
    assert band["tracks"] == {
        ls.STAGE_TRACK: {
            "name": "LightWeightBackgroundStageTrack",
            "prefab": ls.LIVE,
            "animator": "Live/RenderCanvas/LightWeightBackgroundStageImage",
        },
        ls.LANE_TRACK: {
            "name": "LiveGameLaneEffect",
            "prefab": ls.TL,
            "animator": "live_start_playable_timeline/root",
        },
        ls.VIEW_TRACK: {"name": "LiveGameViewTrack", "prefab": ls.GAME, "animator": "LiveGameView/root"},
    }
    b = doc["bindings"]
    assert b[ls.GAME] == {
        "LiveGameView/root": {ls.VIEW_CLIP: LANE, ls.RECORDED: LANE}
    }  # a group's track, its infinite clip
    assert b[ls.LIVE] == {
        "Live/LiveUICanvas/UILiveCombo": {ls.COMBO_CLIP: [{"path": "count", "attribute": "m_Color.a"}]},
        "Live/RenderCanvas/LightWeightBackgroundStageImage": {ls.STAGE_CLIP: [{"path": "", "attribute": "m_Color.a"}]},
    }
    assert b[ls.TL] == {
        "live_start_playable_timeline/root": {ls.LANE_CLIP: [{"path": "line", "attribute": "m_Color.a"}]}
    }


def test_the_scene_settings():
    s = livescene.scene_settings(record())
    assert s["optionDefaults"] == {"GuidelineCount": "2", "LiveSkinId": "1", "QualitySetting": "0"}
    assert s["optionRanges"] == {"QualitySetting": [0, 2]}
    assert s["laneSkin"] == "skin001" and s["laneSkins"] == {"1": "skin001"}
    assert s["judgementSprites"] == {"1": "judgment_miss", "5": "judgment_perfect"}
    assert s["qualitySettings"] == ls.master()["MasterLiveQualitySettings"]


def test_the_scene_keys_name_every_band_and_lane_skin():
    keys = livescene.scene_keys(record())
    assert livescene.SCENE_KEY in keys and "Live/Lane/skin001/out_side_line" in keys
    assert {"Band/1/timeline/live_start_playable_timeline", "Band/2/live_stage/lightweight_background"} <= set(keys)
    assert keys == sorted(keys) and len(keys) == 1 + 2 + 2 + 3 + 4


def test_a_lookup_by_name_finds_the_always_included_shader(tmp_path):
    doc, _, roles = livescene.scene(objects_of(tmp_path, record()), record())
    assert doc["shaders"]["UI/Default"] == ls.SHADERS["UI/Default"]  # not the bundle's different copy
    assert set(doc["shaders"]) == set(livescene.PLAYER_SHADERS) and len(roles) == len(ls.SHADERS) + 1


def test_a_shader_the_live_screen_finds_by_name_is_required(tmp_path):
    objs = ls.objects()
    del objs[ls.SHADERS["Hidden/CompositeRectBlit"]]
    with pytest.raises(ValueError, match="finds no shader Hidden/CompositeRectBlit"):
        livescene.scene(objects_of(tmp_path, record(), objs), record())


def test_a_track_with_clips_and_no_known_animator_is_an_error(tmp_path):
    objs = ls.objects()
    cls, name, files = objs[ls.LANE_TRACK]
    objs[ls.LANE_TRACK] = (cls, name, {"json": {**files["json"], "m_Name": "Other"}})
    with pytest.raises(ValueError, match="track Other plays clips on no known Animator"):
        livescene.scene(objects_of(tmp_path, record(), objs), record())
