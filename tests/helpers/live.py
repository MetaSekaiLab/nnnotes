"""A synthetic live: the master tables of music 7 and the object table of its chart and jacket (content.live)."""

import gzip

from nnnotes.content import live
from nnnotes.pipeline import contract
from tests.helpers import livenotes as ln

CHART = {
    "score": {
        "events": {"bpm": [{"t": 0, "bpm": 120}], "sig": [{"t": 0, "sig": [4, 4]}]},
        "notes": [
            {"type": "tap", "t": 480, "pos": 2, "size": 3},
            {"type": "flick", "t": 960, "pos": 10, "size": 4, "dir": "left"},
            {"type": "long", "node": [{"t": 1440, "pos": 0, "size": 2}, {"t": 1920, "pos": 5.5, "size": 2}]},
        ],
    }
}
TEXT, JACKET, TEXTURE, SCRIPT = "cab-chart:1", "cab-jacket:2", "cab-jacket:3", "cab-chart:9"
EXPERT, NORMAL = "m7_03", "m7_01"


def master() -> dict:
    """Master tables {name: rows} of music 7: a normal and an expert chart, vocal characters 1 (band 2) and 3 (band
    1), member card 5 of character 3."""
    return {
        "MasterLiveMusic": [
            {
                "_id": 7,
                "_easyID": 0,
                "_normalID": 71,
                "_hardID": 0,
                "_expertID": 73,
                "_vocalCharacterIDs": [1, 3],
                "_titleTextID": 100,
                "_bandIDs": [2],
                "_musicSoundID": 900,
                "_jingleSoundID": 0,
                "_jacketAssetName": "jacket7",
            }
        ],
        "MasterLiveMusicScore": [
            {"_id": 71, "_musicScoreTextFileName": NORMAL, "_fullComboCount": 9},
            {"_id": 73, "_musicScoreTextFileName": EXPERT, "_fullComboCount": 5},
        ],
        "MasterText": [{"_id": 100, "_japanese": "曲", "_english": "Song"}, {"_id": 200, "_japanese": "バンド"}],
        "MasterBand": [{"_id": 1, "_nameTextID": 0}, {"_id": 2, "_nameTextID": 200}],
        "MasterSound": [{"_id": 900, "_soundCueSheetID": 10, "_cueName": "bgm_7"}],
        "MasterSoundCueSheet": [{"_id": 10, "_cueSheetName": "bgm_7"}],
        "MasterCharacter": [{"_id": 1, "_bandID": 2}, {"_id": 3, "_bandID": 1}],
        "MasterMemberCard": [{"_id": 5, "_characterID": 3}, {"_id": 6, "_characterID": 8}],
    }


def table(depot, chart: str = EXPERT, gz: bool = True) -> dict:
    """The object table of the live of chart file `chart`: its TextAsset (the chart JSON, gzipped with `gz`) and the
    jacket Sprite with its texture."""
    data = contract.encode(CHART)
    objs = {
        TEXT: ("TextAsset", chart, {"json": {"m_Name": chart}, "data": gzip.compress(data) if gz else data}),
        JACKET: ("Sprite", "jacket7", {"json": {"m_RD": {"texture": {"$ref": {"file": "cab-jacket", "pathId": 3}}}}}),
        TEXTURE: ("Texture2D", "jacket7", {"json": {"m_Name": "jacket7"}, "image": b"png"}),
        SCRIPT: ("MonoScript", "TextAsset", {"json": {}}),
    }
    keys = {live.chart_key(chart): [TEXT], live.jacket_key("jacket7"): [JACKET]}
    return ln.table(depot, keys, objs)
