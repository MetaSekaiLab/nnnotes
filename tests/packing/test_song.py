import copy

import pytest

from nnnotes.packing.song import assemble, find
from tests.helpers.packing import project_document, song_tables


def test_song_owns_display_metadata_audio_and_chart_references():
    baseline, project = song_tables(), project_document()
    original = copy.deepcopy(baseline)
    stats = {name: {"notes": 42 + i} for i, name in enumerate(project["charts"])}
    result, names = assemble(baseline, project, stats)
    song = find(result, "MasterLiveMusic", 7)
    assert song["_bandIDs"] == []
    assert song["_bestMusicTagIDs"] == [700]
    assert song["_jacketAssetName"] == names["jacket"]
    assert song["_musicSoundID"] != baseline["MasterLiveMusic"]["_allData"][0]["_musicSoundID"]
    assert find(result, "MasterText", song["_titleTextID"])["_english"] == "Example"
    assert find(result, "MasterSound", song["_musicSoundID"])["_cueName"] == names["full"]
    for index, difficulty in enumerate(project["charts"]):
        row = find(result, "MasterLiveMusicScore", song[f"_{difficulty}ID"])
        assert row["_musicScoreTextFileName"] == names["charts"][difficulty]
        assert row["_fullComboCount"] == 42 + index
    assert baseline == original
    for name in baseline:
        assert result[name]["_allData"][: len(baseline[name]["_allData"])] == baseline[name]["_allData"]


def test_duplicate_song_or_score_ids_stop_assembly():
    project = project_document()
    stats = {name: {"notes": 1} for name in project["charts"]}
    project["song"]["id"] = 1
    with pytest.raises(ValueError, match="conflict"):
        assemble(song_tables(), project, stats)
    project["song"]["id"] = 7
    project["charts"]["hard"]["score_id"] = 700
    with pytest.raises(ValueError, match="conflict"):
        assemble(song_tables(), project, stats)
