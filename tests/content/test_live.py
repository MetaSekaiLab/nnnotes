"""The live product assembled from a synthetic object table, its subjects and its master record (content.live)."""

import pytest

from nnnotes.content import live, score
from nnnotes.pipeline import contract
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import live as lh
from tests.helpers.schemas import validate

PARTS = {
    "scene": "scenes/default.json",
    "music": "music/7.json",
    "notes": "notes/default.json",
    "noteskin": "noteskins/skin001.json",
    "noteeffect": "noteeffects/effect001.json",
    "sounds": "sounds/default.json",
}


def objects_of(tmp_path, **kw) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(lh.table(depot, **kw), depot)


def test_a_subject_names_a_chart_and_optionally_a_band():
    assert live.live_subject(7, "expert") == "7/expert" and live.live_subject(7, "easy", 2) == "7/easy/2"
    assert live.parse_subject("7/expert") == (7, "expert", None)
    assert live.parse_subject("7/easy/2") == (7, "easy", 2)
    for bad in ("7", "7/extreme", "x/expert", "7/expert/b", "7/expert/2/1", "7//2"):
        with pytest.raises(ValueError, match="not a live subject"):
            live.parse_subject(bad)
    with pytest.raises(ValueError, match="not a difficulty"):
        live.live_subject(7, "extreme")


def test_the_band_is_the_leader_cards_the_given_or_the_first_vocal_characters():
    t = lh.master()
    assert live.band_choice(t, 7)["band"] == 2  # character 1, the first vocal character
    assert live.band_choice(t, 7, band=4) == {"band": 4, "source": "band given", "note": live.BAND_NOTE}
    assert live.band_choice(t, 7, leader_card=5)["band"] == 1  # card 5 -> character 3
    with pytest.raises(ValueError, match="either band or leader_card"):
        live.band_choice(t, 7, band=1, leader_card=5)
    with pytest.raises(KeyError, match="MasterMemberCard 9: 0 rows"):
        live.band_choice(t, 7, leader_card=9)
    with pytest.raises(KeyError, match="character 8 not in MasterCharacter"):
        live.band_choice(t, 7, leader_card=6)
    t["MasterLiveMusic"][0]["_vocalCharacterIDs"] = []
    with pytest.raises(ValueError, match="no vocal character; give a band or a leader card"):
        live.band_choice(t, 7)


def test_the_record_holds_the_musics_rows_the_difficulty_and_the_band():
    record = live.live_record(lh.master(), "7/expert/1")
    music = record["music"]
    assert record["difficulty"] == "expert" and record["band"]["band"] == 1
    assert music["MasterLiveMusicScore"]["normal"]["_id"] == 71 and music["MasterLiveMusicScore"]["easy"] is None
    assert music["title"]["ja"] == "曲" and music["bands"][0]["name"]["ja"] == "バンド"
    assert music["music"]["MasterSoundCueSheet"]["_cueSheetName"] == "bgm_7" and music["jingle"] is None
    assert live.live_keys(record) == ["Image/Jacket/jacket7", f"Live/MusicScore/{lh.EXPERT}"]
    with pytest.raises(KeyError, match="music 7: no easy chart"):
        live.live_record(lh.master(), "7/easy")
    with pytest.raises(KeyError, match="MasterLiveMusic 8: 0 rows"):
        live.live_record(lh.master(), "8/expert")


@pytest.mark.parametrize("gz", [True, False])
def test_the_live_holds_its_chart_converted_and_names_its_parts(tmp_path, gz):
    record = live.live_record(lh.master(), "7/expert")
    doc, chart, converted, keep = live.live(objects_of(tmp_path, gz=gz), record, PARTS)
    validate("live", doc)
    notes, mirror = score.convert(lh.CHART), score.convert(lh.CHART, mirror=True)
    assert doc == {
        "format": live.LIVE_FORMAT,
        "musicId": 7,
        "difficulty": "expert",
        "band": record["band"],
        "chart": "chart.json",
        "notes": "notes.json",
        "notesMirror": "notes.mirror.json",
        "judgementNoteCount": notes["judgementNoteCount"],
        "fullComboCount": 5,
        "noteCount": len(notes["notes"]),
        "lastNoteTimeMs": notes["lastNoteTimeMs"],
        "master": record["music"],
        "jacket": lh.JACKET,
        "parts": PARTS,
    }
    assert chart == contract.encode(lh.CHART)  # as shipped, un-gzipped
    source = {
        "key": f"Live/MusicScore/{lh.EXPERT}",
        "musicId": 7,
        "difficulty": "expert",
        "masterLiveMusicScoreId": 73,
        "fullComboCount": 5,
    }
    assert converted == {"notes.json": {**notes, "source": source}, "notes.mirror.json": {**mirror, "source": source}}
    assert mirror["mirror"] is True
    assert keep == {lh.JACKET, lh.TEXTURE}  # the chart is in the product's files


def test_a_chart_key_without_a_text_asset_is_an_error(tmp_path):
    depot = Depot(tmp_path / "d")
    doc = lh.table(depot)
    doc["objects"][lh.TEXT]["class"] = "Texture2D"
    record = live.live_record(lh.master(), "7/expert")
    with pytest.raises(ValueError, match=f"Live/MusicScore/{lh.EXPERT}: 0 TextAsset objects"):
        live.live(Objects(doc, depot), record, PARTS)
