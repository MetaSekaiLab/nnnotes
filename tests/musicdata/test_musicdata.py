"""Music data export (musicdata): metadata, chart facts, BGM length, the deck model's chart statistics and the deck
input on synthetic master data, charts and ACBs. The deck model is a stand-in (FakeDeck) except where the built
extension module is tested itself."""

import gzip
import hashlib
import io
import json
import re
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from nnnotes import cli, musicdata, schemas
from nnnotes.content import score
from nnnotes.musicdata import deckdata
from nnnotes.source.master import MasterKey
from nnnotes.source.session import Session
from tests.helpers import synth
from tests.musicdata.test_deckdata import CHART, SMALL, FakeCatalog, rows_of, run
from tests.pipeline.test_voices import simple_acb

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
ROOT = Path(__file__).resolve().parents[2]


def text(tid, stem):
    return {
        "_id": tid,
        "_japanese": f"{stem}-ja",
        "_english": f"{stem}-en",
        "_traditionalChinese": f"{stem}-tw",
        "_simplifiedChinese": f"{stem}-cn",
        "_korean": f"{stem}-ko",
    }


MUSIC = {
    "_id": 100002,
    "_sortOrder": 2,
    "_startAt": "2026/01/01 0:00:00",
    "_defaultUnlock": True,
    "_titleTextID": "T2",
    "_rubyTitleTextID": "",
    "_phoneticTextID": "P2",
    "_bandIDs": [1],
    "_bandNameTextID": "",
    "_vocalCharacterIDs": [1],
    "_lyricistTextID": "L2",
    "_composerTextID": "C2",
    "_arrangerTextID": "",
    "_musicType": 4,
    "_musicCategories": [1],
    "_bestMusicTagIDs": [1],
    "_jacketAssetName": "jkt_2",
    "_gekisouMission1": 1,
    "_gekisouMission2": 3,
    "_gekisouMission3": 3,
    "_musicSoundID": 13,
    "_easyID": 20,
    "_normalID": 0,
    "_hardID": 0,
    "_expertID": 30,
    "_liveScoreRankGroup": 9,
    "_extra": [7],
}
MUSIC1 = dict(
    MUSIC,
    _id=100001,
    _sortOrder=1,
    _titleTextID="T1",
    _bandNameTextID="BN",
    _easyID=10,
    _expertID=0,
    _liveScoreRankGroup=7,
)


def columns(table, **values):
    """A row of a deck input table: every column 0 (an id list empty), then `values`."""
    cols = dict(deckdata.TABLES)[table]
    return {**{c: [] if c.endswith(("IDs", "Ids", "Categories")) else 0 for c in cols}, **values}


def member_card(i, character, skill, **values):
    return columns(
        "MasterMemberCard",
        _id=i,
        _characterID=character,
        _rarity=3,
        _cardType=1,
        _bestMusicTagIDs=[1],
        _gekisouSkillID=skill,
        _nameTextID=f"Card{i}",
        _subtitleTextID=f"Sub{i}" if i <= 2 else "",
        **values,
    )


def snap(i, character, support, **values):
    return columns(
        "MasterSupportCard",
        _id=i,
        _characterIDs=[character],
        _rarity=2,
        _supportCardRankGroup=1,
        _gekisouSupportSkillId01=support,
        _nameTextID=f"Snap{i}",
        _descriptionTextID="SnapSub41" if i == 41 else "",
        **values,
    )


def effect(table, key, i, skill, level, group=0):
    return columns(
        table,
        _id=i,
        **{key: skill},
        _level=level,
        _skillConditionGroup=group,
        _skillEffectType=2000,
        _effectValue=100 * level,
    )


# Gekisou skills 1 (combo, levels 1..5), 2 (Just count, 1..3), 3 (luck, 1..5); Gekisou support skills 31 and 33 (Just
# count, two rows per level: a member of band 1 (31) or 2 (33), or not) and 32 (combo); member cards 1..8 (card 6
# without a Gekisou skill); snaps 41..45 (43 without a Gekisou support skill), highest rank 5
GEKISOU_ROWS = {
    "MasterMemberCard": [
        member_card(1, 1, 1),
        member_card(2, 2, 2),
        member_card(3, 3, 1),
        member_card(4, 4, 3),
        member_card(5, 5, 2),
        member_card(6, 6, 0),
        member_card(7, 3, 3),
        member_card(8, 1, 2),
    ],
    "MasterGekisouSkill": [
        {
            "_id": 1,
            "_nameTextID": "GS1",
            "_descriptionTextFormatID": "GSD1",
            "_gekisouMissionType": 1,
            "_skillCategories": [1],
        },
        {
            "_id": 2,
            "_nameTextID": "GS2",
            "_descriptionTextFormatID": "GSD2",
            "_gekisouMissionType": 3,
            "_skillCategories": [2],
        },
        {
            "_id": 3,
            "_nameTextID": "GS3",
            "_descriptionTextFormatID": "",
            "_gekisouMissionType": 2,
            "_skillCategories": [3],
        },
    ],
    "MasterGekisouSkillEffect": [
        effect("MasterGekisouSkillEffect", "_gekisouSkillID", 10 * s + lv, s, lv)
        for s, top in ((1, 5), (2, 3), (3, 5))
        for lv in range(1, top + 1)
    ],
    "MasterGekisouSupportSkill": [
        {
            "_id": 31,
            "_nameTextID": "GS31",
            "_descriptionTextFormatID": "GSD31",
            "_gekisouSupportSkillExecTiming": 1,
            "_gekisouMissionType": 3,
        },
        {
            "_id": 32,
            "_nameTextID": "GS32",
            "_descriptionTextFormatID": "",
            "_gekisouSupportSkillExecTiming": 1,
            "_gekisouMissionType": 1,
        },
        {
            "_id": 33,
            "_nameTextID": "GS33",
            "_descriptionTextFormatID": "",
            "_gekisouSupportSkillExecTiming": 1,
            "_gekisouMissionType": 3,
        },
    ],
    "MasterGekisouSupportSkillEffect": [
        effect("MasterGekisouSupportSkillEffect", "_gekisouSupportSkillID", 100 * g + 10 * s + lv, s, lv, g)
        for s, groups in ((31, (100, 101)), (32, (0,)), (33, (102, 103)))
        for lv in range(1, 6)
        for g in groups
    ],
    "MasterSupportCard": [snap(41, 1, 31), snap(42, 2, 32), snap(43, 3, 0), snap(44, 4, 31), snap(45, 5, 33)],
    "MasterSupportCardRank": [
        columns(
            "MasterSupportCardRank",
            _id=r,
            _group=1,
            _rank=r,
            _gekisouSupportSkill01Level=r,
            _gekisouSupportSkill02Level=r,
        )
        for r in range(1, 6)
    ],
    # the band conditions: group 100 (102) a member of band 1 (2), group 101 (103) not
    "MasterSkillConditionSet": [{"_id": i, "_group": 99 + i, "_conditionIds": [i]} for i in (1, 2, 3, 4)],
    "MasterSkillCondition": [
        {
            "_id": i,
            "_conditionType": 5000,
            "_conditionValues": [],
            "_isPositive": i in (1, 3),
            "_conditionTargetIDs": [3 if i <= 2 else 4],
        }
        for i in (1, 2, 3, 4)
    ],
    "MasterSkillTarget": [
        columns("MasterSkillTarget", _id=t, _skillTargetType=3, _bandID=t - 2, _judgement=-1) for t in (3, 4)
    ],
}
TABLE_ROWS = {
    "MasterLiveMusic": [MUSIC, MUSIC1],
    "MasterLiveMusicScore": [
        {
            "_id": 30,
            "_musicScoreTextFileName": "c/c_03",
            "_musicScoreLevel": 20,
            "_fullComboCount": 2,
            "_musicScoreDisplayLevel": 20.5,
        },
        {
            "_id": 10,
            "_musicScoreTextFileName": "c/c_01",
            "_musicScoreLevel": 5,
            "_fullComboCount": 11,
            "_musicScoreDisplayLevel": 5.0,
        },
        {
            "_id": 20,
            "_musicScoreTextFileName": "c/c_02",
            "_musicScoreLevel": 9,
            "_fullComboCount": 2,
            "_musicScoreDisplayLevel": 9.0,
        },
        {
            "_id": 40,
            "_musicScoreTextFileName": "c/c_04",
            "_musicScoreLevel": 1,
            "_fullComboCount": 2,
            "_musicScoreDisplayLevel": 1.0,
        },
    ],  # a chart of no song
    "MasterText": [
        text("T1", "one"),
        text("T2", "two"),
        text("P2", "pho"),
        text("L2", "lyr"),
        text("C2", "com"),
        text("BN", "crychic"),
        text("Band1", "mygo"),
        text("Band2", "mujica"),
        text("Ch1", "tomori"),
        text("Ch1s", "tomo"),
        text("Tag1", "tag"),
        text("Cat1", "original"),
    ]
    + [text(f"Ch{i}", f"char{i}") for i in range(2, 7)]
    + [text(f"Card{i}", f"card{i}") for i in range(1, 9)]
    + [text(f"Sub{i}", f"sub{i}") for i in (1, 2)]
    + [text(f"Snap{i}", f"snap{i}") for i in (41, 42, 43, 44, 45)]
    + [text("SnapSub41", "snapsub41")]
    + [text(f"GS{i}", f"gs{i}") for i in (1, 2, 3, 31, 32, 33)]
    + [text(f"GSD{i}", f"gsd{i}") for i in (1, 2, 31)],
    "MasterBand": [
        {"_id": 1, "_nameTextID": "Band1", "_mainColorCode": "#3388BB", "_subColorCode": "#FFFFFF"},
        {"_id": 2, "_nameTextID": "Band2", "_mainColorCode": "#881122", "_subColorCode": "#000000"},
    ],
    # characters 1..4 of band 1, 5 and 6 of band 2
    "MasterCharacter": [
        {"_id": 1, "_nameTextID": "Ch1", "_shortNameTextID": "Ch1s", "_bandID": 1, "_mainColorCode": "#77BBDD"}
    ]
    + [
        {
            "_id": i,
            "_nameTextID": f"Ch{i}",
            "_shortNameTextID": "",
            "_bandID": 1 if i <= 4 else 2,
            "_mainColorCode": "#000000",
        }
        for i in range(2, 7)
    ],
    **GEKISOU_ROWS,
    "MasterTag": [{"_id": 1, "_nameTextID": "Tag1"}],
    "MasterLiveMusicCategory": [{"_id": 1, "_musicCategories": [1], "_textKey": "Cat1"}],
    "MasterSound": [{"_id": 13, "_soundCueSheetID": 5, "_cueName": "song2"}],
    "MasterSoundCueSheet": [{"_id": 5, "_cueSheetName": "Bgm2"}],
    "MasterLiveScoreRank": [
        {"_id": 3, "_group": 9, "_liveScoreRank": 7, "_requiredScore": 900, "_battleLiveRequiredScore": 1800},
        {"_id": 1, "_group": 9, "_liveScoreRank": 2, "_requiredScore": 0, "_battleLiveRequiredScore": 0},
        {"_id": 2, "_group": 8, "_liveScoreRank": 6, "_requiredScore": 5, "_battleLiveRequiredScore": 6},
    ],
    # the songs' missions [1, 3, 3] are pattern 3; pattern 2 rows are another song's
    "MasterLiveGekisouRankingScoreBonus": [
        {
            "_id": 100 * p + 10 * c + k,
            "_missionPattern": p,
            "_count": c,
            "_rank": k,
            "_scoreBonusPercent": (6 - k) * c + 10 * p,
        }
        for p in (2, 3)
        for c in (1, 2, 3)
        for k in (1, 2, 3, 4, 5)
    ],
}
CHARTS = {
    "c/c_01": gzip.compress(json.dumps(CHART).encode("utf-8"), mtime=0),
    "c/c_02": json.dumps(SMALL).encode("utf-8"),
    "c/c_03": gzip.compress(json.dumps(SMALL).encode("utf-8"), mtime=0),
    "c/c_04": json.dumps(SMALL).encode("utf-8"),
}
ACB = simple_acb({"song2": [1]}, [1], {1: 48000 * 90 + 24})  # 90.0005 s at 48 kHz, cue Length 100
PROV = {
    "region": "xx",
    "client": {"versionName": "9.9.9", "versionCode": 99},
    "catalog": {"resourceVersion": None, "sha256": "ab" * 32},
}


def all_rows(name):
    """The song tables above; the deck model's tables of test_deckdata."""
    return TABLE_ROWS[name] if name in TABLE_ROWS else rows_of(name)


def master_dir(tmp_path, rows=TABLE_ROWS):
    d = tmp_path / "m"
    d.mkdir()
    files = []
    for t in musicdata.tables_of(True, True):
        data = synth.master_file({"_allData": rows[t] if t in rows else rows_of(t)})
        (d / f"{t}.bin").write_bytes(data)
        files.append({"name": f"{t}.bin", "hash": hashlib.sha256(data).hexdigest(), "size": len(data)})
    (d / "MasterManifest.json").write_text(json.dumps({"version": "v-test", "files": files}), encoding="utf-8")
    return d


def bgm(sheet, cue):
    assert sheet == "Bgm2"
    return musicdata.cue_length(ACB, cue, f"cue sheet {sheet}")


# ---------------------------------------------------------------- a stand-in deck model
WEIGHT = "0.30000000000000004"  # a binary64 value: kept as the deck model writes it


def trunc_percent(score, percent):
    return int(score * percent / 100)


def effect_row(level, *band):
    """A Gekisou (support) skill effect row of GEKISOU_ROWS as the shapes write it: score up by 100 * level, with a
    band condition (positive or not) when given."""
    condition = [[{"type": 5000, "values": [], "positive": b, "targetIds": None}] for b in band]
    return {
        "effectType": 2000,
        "triggerType": 0,
        "activationTimeSecond": 0,
        "effectValue": 100 * level,
        "maxEffectValue": 0,
        "effectLimitCount": 0,
        "effectExecuteLimitCount": 0,
        "skillTargetIds": [],
        "trigger": [],
        "condition": condition,
        "release": [],
        "reset": [],
        "cumulative": None,
    }


def shape_skill(i, level, targets=None, bands=None):
    return {"id": i, "level": level, "memberTargetIds": targets, "bandIds": bands}


# the shape table of GEKISOU_ROWS: support skills 31 and 33 differ in their band only
SHAPES = [
    {
        "id": 0,
        "source": "member",
        "mission": 1,
        "bandCondition": False,
        "effects": [effect_row(5)],
        "skills": [shape_skill(1, 5)],
    },
    {
        "id": 1,
        "source": "member",
        "mission": 3,
        "bandCondition": False,
        "effects": [effect_row(3)],
        "skills": [shape_skill(2, 3)],
    },
    {
        "id": 2,
        "source": "member",
        "mission": 2,
        "bandCondition": False,
        "effects": [effect_row(5)],
        "skills": [shape_skill(3, 5)],
    },
    {
        "id": 3,
        "source": "support",
        "mission": 3,
        "bandCondition": True,
        "effects": [effect_row(5, True), effect_row(5, False)],
        "skills": [shape_skill(31, 5, [3], [1]), shape_skill(33, 5, [4], [2])],
    },
    {
        "id": 4,
        "source": "support",
        "mission": 1,
        "bandCondition": False,
        "effects": [effect_row(5)],
        "skills": [shape_skill(32, 5)],
    },
]


def expectation_check(positions, ranks=None):
    return {
        "deck": ([[0, 5000]] + [None] * (positions - 1)) if positions else [],
        "ranks": ranks,
        "expected": [2000, 0.001],
        "predicted": [2000.25, 0.001],
        "bound": 7.0,
    }


def aptitude_of(stats, positions):
    """Independently authored interval fixtures, not native numerical results or sampling estimates."""
    ranges, base = stats["ranges"], stats["expectation"]
    missions = {row["mission"] for row in ranges}
    factors = [
        {
            "judgedNotes": 5,
            "justNotes": 0,
            "perfectNotes": 5 if row["mission"] == 3 else 0,
            "tailNotes": 1,
            "comboAtStart": 0,
            "lotteries": [
                sum(value[0] for value in base["ranges"][j]["lotResults"]),
                sum(value[1] for value in base["ranges"][j]["lotResults"]),
            ],
        }
        for j, row in enumerate(ranges)
    ]
    variants = []
    for shape in SHAPES:
        if shape["mission"] != 4 and shape["mission"] not in missions:
            continue
        for band in (True, False) if shape["bandCondition"] else (None,):
            rs, inside, inside_perfect = [], 0, 0
            for j, (row, baseline) in enumerate(zip(ranges, base["ranges"])):
                gain = 10 * (j + 1)
                bonus = (
                    trunc_percent(baseline["rangeScore"][0] + gain, row["rankBonusPercent"]) - baseline["rankBonus"][0]
                )
                perfect_bonus = (
                    trunc_percent(baseline["rangeScorePerfect"][0] + gain, row["rankBonusPercent"])
                    - baseline["rankBonusPerfect"][0]
                )
                rs.append(
                    {
                        "rangeScore": [gain, 0],
                        "rankBonus": [bonus, 0],
                        "rankBonusPerfect": [perfect_bonus, 0],
                        "rangeScorePerfect": [gain, 0],
                        "maxCombo": [0, 0],
                        "justCount": [1, 0],
                        "luckPoints": [0, 0],
                    }
                )
                inside += gain + bonus
                inside_perfect += gain + perfect_bonus
            linear = base["rangeWeights"] is not None
            variants.append(
                {
                    "shape": shape["id"],
                    "bandMatch": band,
                    "score": [inside + 7, 0],
                    "scorePerfect": [inside_perfect + 7, 0],
                    "tail": [7, 0],
                    "tailPerfect": [7, 0],
                    "converted": [0, 0],
                    "ranges": rs,
                    "weights": [[0.01, 1e-12]] * positions,
                    "rangeWeights": [[[0.001, 1e-12]] * len(ranges)] * positions if linear else None,
                    "check": expectation_check(positions, [2 if linear else 1] * len(ranges)),
                }
            )
    return {"factors": factors, "variants": variants}


class FakeDeck:
    """The /3 extension interface with synthetic expectations and independently constructed outward intervals."""

    COMMIT = "7e5d84b5998d28c21541ce3f2e0a3dfb1439f4f6"
    SOURCE = "5" * 64
    FORMAT = "ournotes-deck.chart-stats/3"

    def __init__(self, change=None, fail=None, header=None):
        self.change, self.fail, self.header, self.inputs, self.options = change, fail, header, [], []

    def info(self):
        return {
            "name": "ournotes-deck",
            "version": "0.0.1",
            "source": "https://github.com/empty-sekai/ournotes-deck",
            "commit": self.COMMIT,
            "sourceSha256": self.SOURCE,
            "dataFormat": deckdata.DECK_FORMAT,
            "format": self.FORMAT,
        }

    def chart_stats(self, data, seeds, workers, aptitude=True):
        if self.fail:
            raise ValueError(self.fail)
        if seeds < 1:
            raise ValueError("an empty seed set")
        doc = json.loads(data)
        self.inputs.append((doc, seeds, workers))
        self.options.append(aptitude)
        assert doc["format"] == deckdata.DECK_FORMAT and list(doc["master"]) == [t for t, _ in deckdata.TABLES]
        master = doc["master"]

        def table(name):
            return [dict(zip(master[name]["columns"], row)) for row in master[name]["rows"]]

        levels = {row["_id"]: row["_musicScoreLevel"] for row in table("MasterLiveMusicScore")}
        bonuses = {
            (row["_missionPattern"], row["_count"], row["_rank"]): row["_scoreBonusPercent"]
            for row in table("MasterLiveGekisouRankingScoreBonus")
        }
        songs = {}
        for row in table("MasterLiveMusic"):
            for difficulty in musicdata.DIFFICULTIES:
                if row[f"_{difficulty}ID"]:
                    songs[row[f"_{difficulty}ID"]] = (
                        row["_id"],
                        difficulty,
                        [row["_gekisouMission1"], row["_gekisouMission2"], row["_gekisouMission3"]],
                    )
        charts = []
        for chart in doc["charts"]:
            music, difficulty, missions = songs[chart["scoreId"]]
            last = max(chart["notes"]["timeMs"])
            fevers = list(zip(chart["fevers"]["startMs"], chart["fevers"]["endMs"]))[:3]
            positions = min(len(chart["skillEvents"]["timeMs"]), 5)
            pattern = musicdata.mission_pattern(missions)
            percents = [[bonuses.get((pattern, i + 1, rank), 0) for rank in range(1, 6)] for i in range(len(fevers))]
            luck = musicdata.MISSION_LUCK in missions[: len(fevers)]
            expected_ranges = [
                {
                    "rangeScore": [101 * (j + 1), 0],
                    "rankBonus": [trunc_percent(101 * (j + 1), percent[0]), 0],
                    "rankBonusPerfect": [trunc_percent(101 * (j + 1), percent[0]), 0],
                    "rangeScorePerfect": [101 * (j + 1), 0],
                    "maxCombo": 1,
                    "justCount": 0,
                    "luckPoints": [10, 0] if missions[j] == 2 else [0, 0],
                    "lotResults": [[1, 0], [1, 0], [0, 0], [0, 0]] if missions[j] == 2 else [[0, 0]] * 4,
                }
                for j, percent in enumerate(percents)
            ]
            expected = {
                "score": [1234, 0],
                "scorePerfect": [1234, 0],
                "ranges": expected_ranges,
                "weights": [[["W", 0]] * positions],
                "rangeWeights": [[[["W", 0]] * len(fevers)] * positions],
                "check": expectation_check(positions),
                "rankCheck": expectation_check(positions, [2] * len(fevers)) if fevers else None,
            }
            off_check = {
                "deck": ([[0, 5000]] + [None] * (positions - 1)) if positions else [],
                "exact": 2000,
                "predicted": 2000.25,
                "bound": 7.0,
            }
            stats = {
                "scoreId": chart["scoreId"],
                "musicId": music,
                "difficulty": difficulty,
                "level": levels[chart["scoreId"]],
                "judgedNotes": sum(score.is_judgement_note(op) for op in chart["notes"]["op"]),
                "convertedNoteCount": len(chart["notes"]["id"]),
                "lastNoteMs": last,
                "musicLengthMs": last + 1000,
                "skip": 1.5,
                "events": [[i % 5, time] for i, time in enumerate(chart["skillEvents"]["timeMs"])],
                "positions": positions,
                "missions": missions,
                "ranges": [
                    {
                        "index": i,
                        "mission": missions[i],
                        "startMs": a,
                        "endMs": b,
                        "rankBonusPercent": percents[i][0],
                        "rankBonusPercents": percents[i],
                    }
                    for i, (a, b) in enumerate(fevers)
                ],
                "justNotes": 0,
                "expectation": expected,
                "replaySeeds": musicdata.published_seeds(seeds) if luck else [0],
                "offSeeds": [{"seed": 0, "score": 1000, "weights": [["W"] * positions], "check": off_check}],
            }
            stats["gekisouAptitude"] = aptitude_of(stats, positions) if aptitude and fevers else None
            if self.change:
                self.change(stats)
            charts.append(stats)
        out = {
            "format": self.FORMAT,
            "source": doc["provenance"],
            "model": {
                "power": 300000,
                "checkPower": 1000003,
                "unitValue": 10000,
                "expectation": "independent nominal probabilities with outward intervals",
                "gekisouAptitude": "each Gekisou (support) skill shape alone",
            },
            "gekisouAptitude": {
                "plainKind": 0,
                "host": "a synthetic member without a Gekisou skill",
                "law": musicdata.EXPECTATION_LAW,
                "shapes": SHAPES,
            }
            if aptitude
            else None,
            "kinds": [{"id": 0, "effectType": 2000, "activationTimeSecond": 5.0, "durationMs": 5000}],
            "charts": charts,
        }
        out = json.loads(json.dumps(out))
        if self.header:
            self.header(out)
        return json.dumps(out).replace('"W"', WEIGHT)


def export(tmp_path, rows=TABLE_ROWS, charts=CHARTS, bgm=bgm, out="music.json", deck=None, **kw):
    d = master_dir(tmp_path, rows)
    return musicdata.export(
        tmp_path / out,
        deckdata.master_files(d),
        KEY,
        charts.__getitem__,
        bgm,
        **PROV,
        deck=musicdata.Deck(module=deck, workers=3) if deck is not None else None,
        **kw,
    )


def schema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    doc = schemas.load("music-data")
    jsonschema.Draft202012Validator.check_schema(doc)
    return jsonschema.Draft202012Validator(doc)


# ---------------------------------------------------------------- the document
def test_document(tmp_path):
    r = export(tmp_path)
    raw = (tmp_path / "music.json").read_bytes()
    assert raw.endswith(b"}\n") and raw.count(b"\n") == 1
    assert (r["songs"], r["charts"], r["deck"], r["full"]) == (2, 3, None, False)
    assert r["sha256"] == hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    assert list(doc) == [
        "format",
        "provenance",
        "languages",
        "bands",
        "characters",
        "tags",
        "categories",
        "gekisouCatalog",
        "deck",
        "songs",
    ]
    assert doc["format"] == "nnnotes.music-data/2" and doc["languages"] == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    p = doc["provenance"]
    assert list(p) == ["region", "client", "catalog", "master", "exporter", "deck"] and p["deck"] is None
    # without the deck model: the song and the Gekisou catalog tables only
    assert list(p["master"]["tables"]) == list(musicdata.SONG_TABLES + musicdata.CATALOG_TABLES)
    assert p["exporter"] == {"name": "nnnotes", "version": cli.__version__, "chartFormat": deckdata.CHART_FORMAT}
    assert doc["deck"] is None
    assert doc["bands"][0] == {
        "id": 1,
        "name": {"ja": "mygo-ja", "en": "mygo-en", "zh-Hant": "mygo-tw", "zh-Hans": "mygo-cn", "ko": "mygo-ko"},
        "mainColor": "#3388BB",
        "subColor": "#FFFFFF",
    }
    assert [b["id"] for b in doc["bands"]] == [1, 2]
    assert doc["characters"][0]["name"]["ja"] == "tomori-ja" and doc["characters"][0]["bandId"] == 1
    assert doc["categories"] == [{"id": 1, "musicCategories": [1], "name": dict(doc["categories"][0]["name"])}]
    one, two = doc["songs"]
    assert (one["id"], two["id"]) == (100001, 100002)  # sorted by id
    assert one["bandName"]["ja"] == "crychic-ja" and two["bandName"] is None
    assert two["title"]["zh-Hans"] == "two-cn" and two["ruby"] is None and two["arranger"] is None
    assert two["lyricist"]["en"] == "lyr-en" and two["gekisouMissions"] == [1, 3, 3]
    assert two["bgm"] == {
        "soundId": 13,
        "cueSheet": "Bgm2",
        "cue": "song2",
        "length": {"lengthMs": 100, "samples": 48000 * 90 + 24, "sampleRate": 48000, "durationMs": 90000},
    }
    assert two["master"]["MasterLiveMusic"]["_extra"] == [7]  # the whole row
    assert two["scoreRanks"] == [
        {"rank": "D", "requiredScore": 0, "battleRequiredScore": 0},  # by required score
        {"rank": "SS", "requiredScore": 900, "battleRequiredScore": 1800},
    ]
    assert [r["_id"] for r in two["master"]["MasterLiveScoreRank"]] == [1, 3]
    assert one["scoreRanks"] == [] and one["master"]["MasterLiveScoreRank"] == []  # a group without rows
    assert [c["difficulty"] for c in two["charts"]] == ["easy", "expert"]  # difficulties without a score are left out
    assert [c["difficulty"] for c in one["charts"]] == ["easy"]
    assert all(c["deck"] is None for s in doc["songs"] for c in s["charts"])
    schema_validator().validate(doc)


def test_chart_facts(tmp_path):
    export(tmp_path)
    doc = json.loads((tmp_path / "music.json").read_bytes())
    c = doc["songs"][0]["charts"][0]  # CHART at 120 BPM
    assert (c["scoreId"], c["level"], c["displayLevel"], c["fullComboCount"]) == (10, 5, 5.0, 11)
    assert c["asset"] == {"key": "Live/MusicScore/c/c_01", "sha256": hashlib.sha256(CHARTS["c/c_01"]).hexdigest()}
    rs = score.runtime_score(CHART)
    judged = sorted(n.pos.ms for n in rs.notes if score.is_judgement_note(n.op))
    assert c["notes"]["judged"] == len(judged) and c["notes"]["total"] == len(rs.notes) == 17
    assert sum(c["notes"]["byOperateType"].values()) == 17 and list(c["notes"]["byOperateType"])[0] == "1"
    assert c["bpm"] == {"main": 120, "min": 120, "max": 120, "changes": [{"timeMs": 0, "bpm": 120}]}
    assert (c["firstNoteMs"], c["lastJudgedNoteMs"], c["lastNoteMs"], c["musicLengthMs"]) == (0, 5000, 5000, 6000)
    assert c["skillEventsMs"] == [2500, 500] and c["fevers"] == [[0, 1000], [2000, 2500]]
    assert list(c)[-1] == "deck"


def test_bpm_facts():
    class P:
        def __init__(self, ms):
            self.ms = ms

    ev = [(100.0, P(0)), (200.0, P(1000)), (150.0, P(1500)), (300.0, P(9000))]
    f = musicdata.bpm_facts(ev, 500, 4000)  # 100 for 500 ms, 200 for 500, 150 for 2500; 300 after
    assert (f["main"], f["min"], f["max"]) == (150.0, 100.0, 200.0)
    assert [x["timeMs"] for x in f["changes"]] == [0, 1000, 1500, 9000]
    assert musicdata.bpm_facts(ev, 1200, 1200)["main"] == 200.0  # one instant: the BPM at that time
    tie = [(120.0, P(0)), (180.0, P(1000))]
    assert musicdata.bpm_facts(tie, 0, 2000)["main"] == 120.0  # equal time: the earliest
    with pytest.raises(musicdata.MusicDataError):
        musicdata.bpm_facts([], 0, 1)


# ---------------------------------------------------------------- the deck model
def test_deck(tmp_path):
    fake = FakeDeck()
    r = export(tmp_path, deck=fake)
    raw = (tmp_path / "music.json").read_bytes()
    doc = json.loads(raw)
    assert r["deck"] == FakeDeck.COMMIT and r["unplayable"] == 0
    ((deck_input, seeds, workers),) = fake.inputs
    assert (seeds, workers) == (musicdata.DECK_SEEDS, 3)
    assert [c["scoreId"] for c in deck_input["charts"]] == [10, 20, 30]  # the songs' charts, not chart 40
    assert deck_input["provenance"]["master"] == {"source": "api", "version": "v-test"}
    p = doc["provenance"]
    assert p["deck"] == {k: fake.info()[k] for k in ("name", "version", "source", "commit", "sourceSha256", "format")}
    assert list(p["master"]["tables"]) == list(musicdata.tables_of(True, False))
    assert doc["deck"]["model"]["power"] == 300000
    assert doc["deck"]["gekisouAptitude"]["shapes"] == SHAPES
    c = doc["songs"][0]["charts"][0]
    assert list(c["deck"]) == list(musicdata.DECK_CHART_KEYS)
    assert c["deck"]["events"] == [[0, 2500], [1, 500]] and c["deck"]["ranges"][1]["startMs"] == 2000
    assert c["deck"]["unplayable"] is None and c["deck"]["skip"] == 1.5
    # the ranks: the song's mission pattern (3) rows of every range
    assert [r["rankBonusPercents"] for r in c["deck"]["ranges"]] == [[35, 34, 33, 32, 31], [40, 38, 36, 34, 32]]
    expected = c["deck"]["expectation"]
    assert expected["rankCheck"]["ranks"] == [2, 2] and expected["scorePerfect"] == expected["score"]
    assert [r["rankBonus"] for r in expected["ranges"]] == [[35, 0], [80, 0]]
    assert [r["luckPoints"] for r in expected["ranges"]] == [[0, 0], [0, 0]]
    assert c["deck"]["offSeeds"][0]["score"] == 1000
    # the deck model's numbers as it writes them: a binary64 value is not narrowed to binary32
    assert b'"weights":[[' + WEIGHT.encode() + b"," + WEIGHT.encode() + b"]]" in raw and b'"predicted":2000.25' in raw
    assert "master" not in doc and "charts" not in doc
    schema_validator().validate(doc)
    (tmp_path / "m").rename(tmp_path / "m0")
    export(tmp_path, deck=FakeDeck(), out="again.json")
    assert (tmp_path / "again.json").read_bytes() == raw


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda s: s.update(judgedNotes=s["judgedNotes"] + 1), "chart 10 .*judged note count"),
        (lambda s: s.update(musicLengthMs=0), "music length 0 differs"),
        (lambda s: s.update(level=99), "level 99 differs"),
        (lambda s: s["missions"].reverse(), "Gekisou missions"),
        (lambda s: s["events"].pop(), "skill event times"),
        (lambda s: s["ranges"] and s["ranges"][0].update(endMs=1), "fevers"),
        (lambda s: s.update(difficulty="hard"), "difficulty 'hard' differs"),
        (lambda s: s["ranges"] and s["ranges"][0]["rankBonusPercents"].__setitem__(2, 99), "range 0: .*percentages"),
        (lambda s: s["ranges"] and s["ranges"][0].update(rankBonusPercent=1), "range 0: .*percentages"),
        (lambda s: s["ranges"] and s["expectation"]["ranges"][0].update(rankBonus=-1), "range score or luck points"),
        (lambda s: s["expectation"]["ranges"].pop(), "expectation ranges do not match"),
        (lambda s: s.pop("offSeeds"), "no Gekisou off statistics"),
        (lambda s: s["offSeeds"].append(s["offSeeds"][0]), "no Gekisou off statistics"),
        (lambda s: s["offSeeds"][0]["weights"].append(None), "Gekisou off: weights"),
        (lambda s: s["offSeeds"][0]["check"].update(exact=0), "Gekisou off: the check deck scores 0"),
        (lambda s: s["expectation"].update(scorePerfect=[1, 0]), "without Just notes differs"),
        (lambda s: s["expectation"]["weights"][0].pop(), "weights are not"),
        (lambda s: s["expectation"]["weights"].append(None), "weights are not"),
        (lambda s: s["expectation"].update(rangeWeights=[[]]), "rangeWeights are not"),
        (lambda s: s["expectation"]["check"].update(bound=0.1), "expectation check exceeds"),
        (lambda s: s["ranges"] and s["expectation"]["rankCheck"].update(expected=[0, 0]), "expectation check exceeds"),
        (lambda s: s["ranges"] and s["expectation"]["rankCheck"].update(ranks=[6, 1]), "check ranks"),
        (
            lambda s: s["expectation"]["ranges"][0].pop("luckPoints"),
            "expectation range 0: a range score or luck points",
        ),
        (lambda s: s.update(replaySeeds=[3]), r"chart 10 .*seeds \[3\] are not the chart's seed set \[0\]"),
        (lambda s: s["replaySeeds"].append(5), r"chart 10 .*seeds \[0, 5\] are not the chart's seed"),
    ],
)
def test_deck_checks(tmp_path, change, match):
    with pytest.raises(musicdata.MusicDataError, match=match):
        export(tmp_path, deck=FakeDeck(change))
    assert not (tmp_path / "music.json").exists()


@pytest.fixture
def expectation_fixture():
    """One complete /3 chart from the controlled interface, decoded without narrowing native numbers."""
    tables = {name: all_rows(name) for name, _ in deckdata.TABLES}
    charts = deckdata.charts([tables["MasterLiveMusicScore"][1]], CHARTS.__getitem__)
    source = deckdata.build(tables, charts, {})
    document = FakeDeck().chart_stats(deckdata.encode(source).decode("utf-8"), 8, 1)
    return deckdata.loads(document)["charts"][0]


@pytest.mark.parametrize(
    "estimate",
    [
        100,
        [100],
        [100, 0, 0],
        [True, 0],
        [100, False],
        [100, -1],
        [deckdata.Num("NaN"), 0],
        [100, deckdata.Num("Infinity")],
        [deckdata.Num("1e308"), deckdata.Num("1e308")],
    ],
)
def test_expectation_rejects_invalid_enclosures(expectation_fixture, estimate):
    expectation_fixture["expectation"]["score"] = estimate
    with pytest.raises(musicdata.MusicDataError, match="outward radius"):
        musicdata.validate_expectation(expectation_fixture, 1, "synthetic")


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda c: c.update(justNotes=True), "judgement counts"),
        (lambda c: c.update(justNotes=c["judgedNotes"] + 1), "judgement counts"),
        (lambda c: c.pop("expectation"), "expectation field"),
        (lambda c: c["expectation"].pop("rangeWeights"), "rangeWeights or rankCheck field"),
        (lambda c: c["expectation"].pop("rankCheck"), "rangeWeights or rankCheck field"),
        (lambda c: c["expectation"]["ranges"][0].pop("rankBonusPerfect"), "range score"),
        (lambda c: c["expectation"]["ranges"][0].update(maxCombo=True), "judgement counters"),
        (lambda c: c["expectation"]["ranges"][0].update(lotResults=[[1, 0]] * 4), "outside a luck range"),
        (lambda c: c["expectation"]["check"].update(ranks=[]), "unranked expectation check"),
        (lambda c: c["expectation"]["rankCheck"].update(deck=[None, None]), "check deck"),
    ],
)
def test_expectation_requires_the_new_contract(expectation_fixture, change, message):
    change(expectation_fixture)
    with pytest.raises(musicdata.MusicDataError, match=message):
        musicdata.validate_expectation(expectation_fixture, 1, "synthetic")


def test_expected_bonus_is_not_truncation_of_the_expected_score(expectation_fixture):
    # An equally likely score of 99 or 100 has E[score] = 99.5 and E[trunc(score * .35)] = 34.5.
    # Truncating the mean would incorrectly produce 34. Native /3 explicitly measures both bonuses.
    row = expectation_fixture["expectation"]["ranges"][0]
    row.update(
        rangeScore=[deckdata.Num("99.5"), 0],
        rangeScorePerfect=[deckdata.Num("99.5"), 0],
        rankBonus=[deckdata.Num("34.5"), 0],
        rankBonusPerfect=[deckdata.Num("34.5"), 0],
    )
    musicdata.validate_expectation(expectation_fixture, 1, "synthetic")
    assert trunc_percent(float(row["rangeScore"][0]), 35) != float(row["rankBonus"][0])


def test_expectation_check_bounds_both_interval_endpoints(expectation_fixture):
    check = expectation_fixture["expectation"]["check"]
    check.update(expected=[1000, 1], predicted=[1000, 1], bound=1)
    # Identical centers alone cannot certify a bound smaller than the farthest endpoint separation.
    with pytest.raises(musicdata.MusicDataError, match="expectation check exceeds"):
        musicdata.validate_expectation(expectation_fixture, 1, "synthetic")
    check["bound"] = 2
    musicdata.validate_expectation(expectation_fixture, 1, "synthetic")


def test_unplayable_keeps_only_free_live_statistics(expectation_fixture):
    original = deepcopy(expectation_fixture["offSeeds"])
    expectation_fixture.update(
        unplayable="synthetic unsupported fourth fever", expectation=None, replaySeeds=[], gekisouAptitude=None
    )
    musicdata.validate_expectation(expectation_fixture, 1, "synthetic")
    assert musicdata.validate_aptitude_statistics(None, expectation_fixture, "synthetic") is None
    assert expectation_fixture["offSeeds"] == original
    expectation_fixture.pop("expectation")
    with pytest.raises(musicdata.MusicDataError, match="expectation field"):
        musicdata.validate_expectation(expectation_fixture, 1, "synthetic")


def test_deck_without_range_weights(tmp_path):
    """A chart without range weights, a kind without them and a kind without Gekisou off weights are carried."""

    def change(s):
        s["expectation"]["rangeWeights"] = None if s["scoreId"] == 10 else [None]
        s["expectation"]["rankCheck"] = None
        s["offSeeds"][0]["weights"] = [None]
        s["offSeeds"][0]["check"]["deck"] = [None] * s["positions"]
        if s["scoreId"] == 10:
            for v in s["gekisouAptitude"]["variants"]:
                v["rangeWeights"] = None
                v["check"]["ranks"] = [1] * len(s["ranges"])

    export(tmp_path, deck=FakeDeck(change))
    doc = json.loads((tmp_path / "music.json").read_bytes())
    decks = {c["scoreId"]: c["deck"] for s in doc["songs"] for c in s["charts"]}
    assert decks[10]["expectation"]["rangeWeights"] is None and decks[20]["expectation"]["rangeWeights"] == [None]
    assert decks[30]["offSeeds"][0]["weights"] == [None]
    schema_validator().validate(doc)


def test_gekisou_catalog(tmp_path):
    export(tmp_path)  # without the deck model as with it
    cat = json.loads((tmp_path / "music.json").read_bytes())["gekisouCatalog"]
    assert list(cat) == ["skills", "supportSkills", "members", "snaps"]
    assert cat["skills"][0] == {
        "id": 1,
        "mission": 1,
        "maxLevel": 5,
        "name": {"ja": "gs1-ja", "en": "gs1-en", "zh-Hant": "gs1-tw", "zh-Hans": "gs1-cn", "ko": "gs1-ko"},
        "description": {"ja": "gsd1-ja", "en": "gsd1-en", "zh-Hant": "gsd1-tw", "zh-Hans": "gsd1-cn", "ko": "gsd1-ko"},
    }
    assert [(s["id"], s["mission"], s["maxLevel"]) for s in cat["skills"]] == [(1, 1, 5), (2, 3, 3), (3, 2, 5)]
    assert cat["skills"][2]["description"] is None  # no text id
    assert [(s["id"], s["mission"], s["maxLevel"]) for s in cat["supportSkills"]] == [
        (31, 3, 5),
        (32, 1, 5),
        (33, 3, 5),
    ]
    assert cat["supportSkills"][0]["name"]["en"] == "gs31-en" and cat["supportSkills"][1]["description"] is None
    assert cat["members"][0] == {
        "id": 1,
        "characterId": 1,
        "bandId": 1,
        "rarity": 3,
        "gekisouSkillId": 1,
        "name": {"ja": "card1-ja", "en": "card1-en", "zh-Hant": "card1-tw", "zh-Hans": "card1-cn", "ko": "card1-ko"},
        "subtitle": {"ja": "sub1-ja", "en": "sub1-en", "zh-Hant": "sub1-tw", "zh-Hans": "sub1-cn", "ko": "sub1-ko"},
    }
    assert [(m["id"], m["characterId"], m["bandId"], m["gekisouSkillId"]) for m in cat["members"]] == [
        (1, 1, 1, 1),
        (2, 2, 1, 2),
        (3, 3, 1, 1),
        (4, 4, 1, 3),
        (5, 5, 2, 2),
        (6, 6, 2, None),
        (7, 3, 1, 3),
        (8, 1, 1, 2),
    ]
    assert cat["members"][2]["subtitle"] is None
    assert [
        (s["id"], s["characterIds"], s["rarity"], s["gekisouSupportSkillIds"], s["supportSkillLevel"])
        for s in cat["snaps"]
    ] == [
        (41, [1], 2, [31], 5),
        (42, [2], 2, [32], 5),
        (43, [3], 2, [], 5),
        (44, [4], 2, [31], 5),
        (45, [5], 2, [33], 5),
    ]
    assert cat["snaps"][0]["name"]["ja"] == "snap41-ja" and cat["snaps"][0]["subtitle"]["ko"] == "snapsub41-ko"
    assert cat["snaps"][1]["subtitle"] is None
    schema_validator().validate(json.loads((tmp_path / "music.json").read_bytes()))


def _rows(change):
    rows = json.loads(json.dumps(TABLE_ROWS))
    change(rows)
    return rows


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda r: r["MasterMemberCard"][0].update(_characterID=9), "MasterMemberCard 1: character 9 is not in"),
        (lambda r: r["MasterMemberCard"][0].update(_gekisouSkillID=9), "MasterMemberCard 1: Gekisou skill 9 is not in"),
        (lambda r: r["MasterMemberCard"].append(r["MasterMemberCard"][0]), "MasterMemberCard: _id 1 occurs twice"),
        (
            lambda r: r["MasterSupportCard"][0].update(_gekisouSupportSkillId01=99),
            "MasterSupportCard 41: Gekisou support skill 99 is not in",
        ),
        (
            lambda r: r["MasterSupportCard"][0].update(_supportCardRankGroup=7),
            "MasterSupportCard 41: rank group 7 has no MasterSupportCardRank row",
        ),
        (
            lambda r: (
                r["MasterSupportCard"][0].update(_gekisouSupportSkillId02=32),
                r["MasterSupportCardRank"][4].update(_gekisouSupportSkill02Level=4),
            ),
            r"MasterSupportCard 41: its Gekisou support skills \[31, 32\] have the levels \[5, 4\]",
        ),
        (lambda r: r["MasterGekisouSkill"][0].update(_nameTextID="GSX"), "MasterText has no text 'GSX'"),
        (
            lambda r: r["MasterGekisouSupportSkill"].append(r["MasterGekisouSupportSkill"][0]),
            "MasterGekisouSupportSkill: _id 31 occurs twice",
        ),
    ],
)
def test_gekisou_catalog_errors(tmp_path, change, match):
    with pytest.raises(musicdata.MusicDataError, match=match):
        export(tmp_path, rows=_rows(change))
    assert not (tmp_path / "music.json").exists()


def test_gekisou_catalog_levels(tmp_path):
    """A snap's level is its rank group's highest rank's; two Gekisou support skills of one level are one snap."""

    def change(r):
        r["MasterSupportCard"][0]["_gekisouSupportSkillId02"] = 32
        r["MasterSupportCardRank"].append(
            columns(
                "MasterSupportCardRank",
                _id=9,
                _group=1,
                _rank=6,
                _gekisouSupportSkill01Level=3,
                _gekisouSupportSkill02Level=3,
            )
        )

    export(tmp_path, rows=_rows(change))
    snaps = json.loads((tmp_path / "music.json").read_bytes())["gekisouCatalog"]["snaps"]
    assert (snaps[0]["gekisouSupportSkillIds"], snaps[0]["supportSkillLevel"]) == ([31, 32], 3)


def test_published_seeds():
    # ournotes-deck crates/ournotes-sim/tests/gekisou_play.rs: published_seeds_are_fixed_and_nested
    assert musicdata.published_seeds(8) == [
        -70152769,
        -452351740,
        156766337,
        -1696681451,
        -1283484205,
        -895566507,
        322491774,
        2099122494,
    ]
    assert musicdata.published_seeds(64)[:8] == musicdata.published_seeds(8)


def on(score_id, change):
    """A FakeDeck change of one chart."""
    return lambda s: change(s) if s["scoreId"] == score_id else None


def test_luck_chart(tmp_path):
    """A chart with a luck range: its seeds are the first published seeds."""
    rows = _rows(lambda r: r["MasterLiveMusic"][1].update(_gekisouMission1=2))  # song 100001: [2, 3, 3]
    export(tmp_path, rows=rows, deck=FakeDeck())
    doc = json.loads((tmp_path / "music.json").read_bytes())
    deck = doc["songs"][0]["charts"][0]["deck"]
    assert deck["replaySeeds"] == musicdata.published_seeds(8)
    schema_validator().validate(doc)
    (tmp_path / "m").rename(tmp_path / "m1")
    with pytest.raises(musicdata.MusicDataError, match=r"chart 10 .*seeds .* are not the chart's seed set"):
        export(tmp_path, rows=rows, deck=FakeDeck(on(10, lambda s: s["replaySeeds"].pop())))


def test_rank_bonus_percents():
    assert [
        musicdata.mission_pattern(m) for m in ([1, 2, 0], [2, 2, 2], [1, 2, 3], [1, 1, 3], [1, 3, 3], [3, 1, 3])
    ] == [0, 1, 2, 3, 3, 3]
    rows = [
        {"_missionPattern": 2, "_count": 1, "_rank": 1, "_scoreBonusPercent": 30},
        {"_missionPattern": 2, "_count": 3, "_rank": 5, "_scoreBonusPercent": 4},
        {"_missionPattern": 2, "_count": 3, "_rank": 5, "_scoreBonusPercent": 5},  # a later row wins
        {"_missionPattern": 2, "_count": 4, "_rank": 1, "_scoreBonusPercent": 9},  # no fourth range
        {"_missionPattern": 2, "_count": 1, "_rank": 6, "_scoreBonusPercent": 9},  # no sixth rank
        {"_missionPattern": 1, "_count": 2, "_rank": 1, "_scoreBonusPercent": 9},
    ]  # another pattern
    assert musicdata.rank_bonus_percents(rows, [1, 2, 3]) == [[30, 0, 0, 0, 0], [0] * 5, [0, 0, 0, 0, 5]]
    assert musicdata.rank_bonus_percents(rows, [0, 0, 0]) == [[0] * 5] * 3


def test_deck_errors(tmp_path, monkeypatch):
    with pytest.raises(musicdata.MusicDataError, match="deck model: chart 30: the check deck scores"):
        export(tmp_path, deck=FakeDeck(fail="chart 30: the check deck scores 1, the chart statistics predict 2"))
    assert not (tmp_path / "music.json").exists()
    monkeypatch.setitem(sys.modules, "ournotes_sim", None)  # an installation without the package
    with pytest.raises(musicdata.MusicDataError, match=r"not installed.*nnnotes\[deck\].*--no-deck"):
        musicdata.Deck()


def deck_pin() -> str:
    """The ournotes-sim version the extra `deck` pins."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    (pin,) = extras["deck"]
    return re.fullmatch(r"ournotes-sim==(\d+\.\d+\.\d+)", pin).group(1)


def test_deck_module():
    deck = pytest.importorskip("ournotes_sim")
    info = deck.info()
    assert info["name"] == "ournotes-deck" and info["format"] == "ournotes-deck.chart-stats/3"
    assert info["dataFormat"] == deckdata.DECK_FORMAT and re.fullmatch(r"[0-9a-f]{40}", info["commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", info["sourceSha256"])
    assert info["version"] == deck_pin()  # the release the extra pins
    with pytest.raises(ValueError, match="not a deck data file"):
        deck.chart_stats('{"format": "x"}')
    assert musicdata.Deck().info()["commit"] == info["commit"]


# ---------------------------------------------------------------- the deck input (--full)
def test_full(tmp_path):
    asked = []

    def fetch(name):
        asked.append(name)
        return CHARTS[name]

    r = export(tmp_path, charts=type("C", (), {"__getitem__": staticmethod(fetch)})(), full=True, deck=FakeDeck())
    doc = json.loads((tmp_path / "music.json").read_bytes())
    assert r["full"] is True and sorted(asked) == sorted(CHARTS)  # every chart asset read once
    assert list(doc)[-2:] == ["master", "charts"]
    assert list(doc["master"]) == [t for t, _ in deckdata.TABLES]
    assert [c["scoreId"] for c in doc["charts"]] == [10, 20, 30, 40]  # every chart, songs or not
    tables, _ = deckdata.read_master(deckdata.master_files(tmp_path / "m"), KEY)
    assert doc["master"] == json.loads(deckdata.encode({"m": deckdata.master_subset(tables)}))["m"]
    assert doc["charts"][0] == deckdata.chart_record(10, "Live/MusicScore/c/c_01", CHARTS["c/c_01"])
    schema_validator().validate(doc)
    (tmp_path / "x").mkdir()
    export(tmp_path / "x", full=True)  # the deck input without the deck model
    assert json.loads((tmp_path / "x" / "music.json").read_bytes())["charts"] == doc["charts"]


# ---------------------------------------------------------------- output, jackets and failures
def test_deterministic_and_no_bgm(tmp_path):
    export(tmp_path, out="a.json")
    (tmp_path / "m").rename(tmp_path / "m0")
    export(tmp_path, out="b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    (tmp_path / "m").rename(tmp_path / "m1")
    r = export(tmp_path, bgm=None, out="c.json.gz")
    doc = json.loads(gzip.decompress((tmp_path / "c.json.gz").read_bytes()))
    assert r["bgm"] is False and all(s["bgm"]["length"] is None for s in doc["songs"])


def test_jacket_webp():
    from PIL import Image

    img = Image.new("RGBA", (1024, 512), (10, 200, 30, 255))
    out = Image.open(io.BytesIO(musicdata.jacket_webp(img)))
    assert (out.format, out.size, out.mode) == ("WEBP", (320, 160), "RGB")
    img.putpixel((0, 0), (0, 0, 0, 0))
    out = Image.open(io.BytesIO(musicdata.jacket_webp(img, size=2000)))
    assert (out.size, out.mode) == ((1024, 512), "RGBA")


def test_jackets(tmp_path):
    from PIL import Image

    asked = []

    def jacket(name):
        asked.append(name)
        return musicdata.jacket_webp(Image.new("RGB", (64, 64), (1, 2, 3)))

    r = export(tmp_path, jacket=jacket, jackets_dir=tmp_path / "j")
    assert r["jackets"] == 1 and asked == ["jkt_2"]  # the two songs share one jacket
    assert Image.open(tmp_path / "j" / "jkt_2.webp").size == (64, 64)
    (tmp_path / "x").mkdir()
    assert export(tmp_path / "x", out="b.json")["jackets"] == 0

    def missing(name):
        raise musicdata.MusicDataError(f"jacket {name}: no asset")

    (tmp_path / "y").mkdir()
    with pytest.raises(musicdata.MusicDataError, match="jacket jkt_2"):
        export(tmp_path / "y", jacket=missing, jackets_dir=tmp_path / "y" / "j")
    assert not (tmp_path / "y" / "music.json").exists()


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda r: r["MasterText"].pop(0), "MasterText has no text 'T1'"),
        (lambda r: r["MasterLiveMusicScore"].pop(0), "expert score 30 is not in MasterLiveMusicScore"),
        (lambda r: r["MasterSound"].clear(), "sound 13 is not in MasterSound"),
        (lambda r: r["MasterLiveMusic"].append(dict(MUSIC)), "MasterLiveMusic: _id 100002 occurs twice"),
        (lambda r: r["MasterLiveScoreRank"][0].update(_liveScoreRank=8), "MasterLiveScoreRank 3: unknown rank 8"),
    ],
)
def test_master_errors(tmp_path, change, match):
    rows = json.loads(json.dumps(TABLE_ROWS))
    change(rows)
    with pytest.raises(musicdata.MusicDataError, match=match):
        export(tmp_path, rows=rows)
    assert not (tmp_path / "music.json").exists()


def test_input_errors(tmp_path):
    with pytest.raises(musicdata.MusicDataError, match="c/c_03 .*no such asset"):
        export(tmp_path, charts={k: v for k, v in CHARTS.items() if k != "c/c_03"})
    (tmp_path / "m").rename(tmp_path / "m0")
    with pytest.raises(musicdata.MusicDataError, match="not a chart"):
        export(tmp_path, charts=dict(CHARTS, **{"c/c_02": b"\x1f\x8bnot"}))
    (tmp_path / "m").rename(tmp_path / "m1")
    with pytest.raises(musicdata.MusicDataError, match="no cue 'song2'"):
        export(tmp_path, bgm=lambda s, c: musicdata.cue_length(simple_acb({"x": [1]}, [1], {1: 5}), c, s))
    (tmp_path / "m").rename(tmp_path / "m2")
    with pytest.raises(musicdata.MusicDataError, match=r"c/c_04 \(MasterLiveMusicScore 40\): no such asset"):
        export(tmp_path, charts={k: v for k, v in CHARTS.items() if k != "c/c_04"}, full=True)


# ---------------------------------------------------------------- command line
def test_command(tmp_path, capsys, monkeypatch):
    d = master_dir(tmp_path)
    out = tmp_path / "o" / "music.json"
    code, _, err = run(["music-data", "-o", str(out)], capsys)
    assert code == 2 and "--master-files" in err and "--apk-master" in err
    code, _, err = run(["music-data", "--master-files", str(d), "--no-deck", "-o", str(out)], capsys)
    assert code == 2 and "catalog.region" in err
    code, _, err = run(["--region", "xx", "music-data", "--master-files", str(d), "--no-deck", "-o", str(out)], capsys)
    assert code == 2 and "master.key" in err
    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    monkeypatch.setattr(Session, "catalog", lambda self, **kw: FakeCatalog(CHARTS))
    monkeypatch.setattr(score, "fetch_chart", lambda cat, name: cat.charts[name])
    base = ["--region", "xx", "--depot", str(tmp_path / "depot"), "music-data", "--master-files", str(d), "--no-bgm"]
    code, stdout, err = run(base + ["--no-deck", "-o", str(out)], capsys)
    assert code == 0, err
    r = json.loads(stdout)
    assert (r["songs"], r["charts"], r["bgm"], r["region"], r["deck"]) == (2, 3, False, "xx", None)
    assert synth.MASTER_KEY.hex() not in stdout + err
    fake = FakeDeck()
    real = musicdata.Deck
    monkeypatch.setattr(musicdata, "Deck", lambda **kw: real(module=fake, **kw))
    code, stdout, err = run(base + ["--full", "--seeds", "2", "--workers", "4", "-o", str(out) + ".gz"], capsys)
    assert code == 0, err
    r = json.loads(stdout)
    assert (r["deck"], r["full"]) == (FakeDeck.COMMIT, True) and fake.inputs[0][1:] == (2, 4)
    doc = json.loads(gzip.decompress(Path(str(out) + ".gz").read_bytes()))
    assert doc["provenance"]["catalog"] == {
        "resourceVersion": None,
        "sha256": hashlib.sha256(b"remote catalog").hexdigest(),
    }
    assert len(doc["charts"]) == 4
    monkeypatch.setattr(musicdata, "Deck", lambda **kw: real(module=FakeDeck(fail="boom"), **kw))
    code, _, err = run(base + ["-o", str(tmp_path / "x.json")], capsys)
    assert code == 1 and "deck model: boom" in err and not (tmp_path / "x.json").exists()


def test_command_decoded_master(tmp_path, capsys, monkeypatch):
    from tests.musicdata.test_deckdata import decoded_dir

    d = master_dir(tmp_path)
    dec = decoded_dir(tmp_path, d)
    out = tmp_path / "o" / "music.json"
    code, _, err = run(["--region", "xx", "music-data", "--decoded-master", "--no-deck", "-o", str(out)], capsys)
    assert code == 2 and "paths.master" in err
    monkeypatch.setattr(Session, "catalog", lambda self, **kw: FakeCatalog(CHARTS))
    monkeypatch.setattr(score, "fetch_chart", lambda cat, name: cat.charts[name])
    fake = FakeDeck()
    real = musicdata.Deck
    monkeypatch.setattr(musicdata, "Deck", lambda **kw: real(module=fake, **kw))
    common = ["--region", "xx", "--depot", str(tmp_path / "depot")]
    options = ["--no-bgm", "--full", "-o"]
    code, stdout, err = run(
        common + ["--master", str(dec), "music-data", "--decoded-master"] + options + [str(out)], capsys
    )  # no master key is set
    assert code == 0, err
    r = json.loads(stdout)
    assert (r["masterSource"], r["masterVersion"], r["region"], r["songs"]) == ("api", "v-test", "xx", 2)
    # the file of the master data files as served, byte for byte
    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    code, _, err = run(common + ["music-data", "--master-files", str(d)] + options + [str(tmp_path / "f.json")], capsys)
    assert code == 0, err
    assert out.read_bytes() == (tmp_path / "f.json").read_bytes()


# ---------------------------------------------------------------- Gekisou aptitude gates
@pytest.fixture
def aptitude_fixture():
    """A header and a two-range chart, independently authored above, through JSON as the extension returns it."""
    tables = deepcopy({t: TABLE_ROWS.get(t, rows_of(t)) for t in musicdata.tables_of(True, False)})
    a = musicdata.Aptitude(tables, musicdata.gekisou_catalog(tables, musicdata.Texts(tables["MasterText"])))
    header = deckdata.loads(
        json.dumps({"plainKind": 0, "host": "synthetic host", "law": musicdata.EXPECTATION_LAW, "shapes": SHAPES}),
    )
    chart = {
        "positions": 2,
        "judgedNotes": 20,
        "justNotes": 0,
        "ranges": [{"mission": 1, "rankBonusPercent": 35}, {"mission": 3, "rankBonusPercent": 40}],
        "expectation": {
            "rangeWeights": [],
            "ranges": [
                {
                    "rangeScore": [101, 0],
                    "rangeScorePerfect": [101, 0],
                    "rankBonus": [35, 0],
                    "rankBonusPerfect": [35, 0],
                    "lotResults": [[0, 0]] * 4,
                },
                {
                    "rangeScore": [202, 0],
                    "rangeScorePerfect": [202, 0],
                    "rankBonus": [80, 0],
                    "rankBonusPerfect": [80, 0],
                    "lotResults": [[0, 0]] * 4,
                },
            ],
        },
    }
    chart["gekisouAptitude"] = aptitude_of(chart, 2)
    chart = deckdata.loads(json.dumps(chart))
    return a, header, chart


def aptitude_header(a, h):
    return a.check_header(h, [{"id": 0, "effectType": 2000}], {"gekisouAptitude": "single shape"})


def test_aptitude_fixture(aptitude_fixture):
    a, h, c = aptitude_fixture
    aptitude_header(a, h)
    assert a.chart("synthetic", c, 0) == c["gekisouAptitude"]
    assert [(v["shape"], v["bandMatch"]) for v in c["gekisouAptitude"]["variants"]] == [
        (0, None),
        (1, None),
        (3, True),
        (3, False),
        (4, None),
    ]
    assert h["shapes"][3]["skills"][1]["bandIds"] == [2]


@pytest.mark.parametrize(
    "change",
    [
        lambda h: h["shapes"][0].update(id=8),
        lambda h: h["shapes"][0].update(id=False),
        lambda h: h["shapes"][0].update(mission=4),
        lambda h: h["shapes"][0].update(mission=8),
        lambda h: h["shapes"][0].update(source="unknown"),
        lambda h: h["shapes"][0].update(bandCondition=True),
        lambda h: h["shapes"][0]["skills"][0].update(level=4),
        lambda h: h["shapes"][0]["skills"][0].update(id=99),
        lambda h: h["shapes"][0]["skills"][0].pop("bandIds"),
        lambda h: h["shapes"][0]["skills"].append(h["shapes"][0]["skills"][0]),
        lambda h: h["shapes"][3]["skills"][0].update(bandIds=[2]),
        lambda h: h["shapes"][3]["skills"][0].update(memberTargetIds=[4]),
        lambda h: h["shapes"][0]["effects"][0].update(effectValue=999),
        lambda h: h["shapes"].pop(),
        lambda h: h.update(plainKind=1),
        lambda h: h.update(host=""),
        lambda h: h.update(law="sampled means"),
        lambda h: h.update(law=None),
        lambda h: h.pop("law"),
    ],
)
def test_aptitude_header_rejects(aptitude_fixture, change):
    a, h, _ = aptitude_fixture
    change(h)
    with pytest.raises(musicdata.MusicDataError):
        aptitude_header(a, h)


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(gekisouAptitude=None),
        lambda c: c.update(unplayable="no table"),
        lambda c: c["gekisouAptitude"]["factors"].pop(),
        lambda c: c["gekisouAptitude"]["factors"][0].update(judgedNotes=-1),
        lambda c: c["gekisouAptitude"]["factors"][0].update(lotteries=[1, 0]),
        lambda c: c["gekisouAptitude"]["variants"].pop(),
        lambda c: c["gekisouAptitude"]["variants"][0].update(shape=99),
        lambda c: c["gekisouAptitude"]["variants"][0].update(shape=False),
        lambda c: c["gekisouAptitude"]["variants"][2].update(bandMatch=False),
        lambda c: c["gekisouAptitude"]["variants"][0].update(score=[1]),
        lambda c: c["gekisouAptitude"]["variants"][0].update(score=[deckdata.Num("NaN"), 0]),
        lambda c: c["gekisouAptitude"]["variants"][0].update(score=[10**400, 0]),
        lambda c: c["gekisouAptitude"]["variants"][0].update(score=[1, -1]),
        lambda c: c["gekisouAptitude"]["variants"][0].update(tail=[7, deckdata.Num("Infinity")]),
        lambda c: c["gekisouAptitude"]["variants"][0].update(tail=[8, 0]),
        lambda c: c["gekisouAptitude"]["variants"][0]["ranges"].pop(),
        lambda c: c["gekisouAptitude"]["variants"][0].update(converted=[deckdata.Num("0.5"), 0]),
        lambda c: c["gekisouAptitude"]["variants"][0]["ranges"][0].pop("rankBonusPerfect"),
        lambda c: c["gekisouAptitude"]["variants"][2].update(tailPerfect=[9, 0]),
        lambda c: c["gekisouAptitude"]["variants"][2]["weights"].__setitem__(0, [0, -1]),
        lambda c: c["gekisouAptitude"]["variants"][0]["check"].update(bound=-1),
        lambda c: c["gekisouAptitude"]["variants"][0]["check"].update(predicted=deckdata.Num("NaN")),
        lambda c: c["gekisouAptitude"]["variants"][0]["check"].update(expected=[0, 0]),
        lambda c: c["gekisouAptitude"]["variants"][0]["check"].update(ranks=[0, 2]),
        lambda c: c["gekisouAptitude"]["variants"][0]["check"].update(deck=[[9, 100], None]),
    ],
)
def test_aptitude_chart_rejects(aptitude_fixture, change):
    a, h, c = aptitude_fixture
    aptitude_header(a, h)
    change(c)
    with pytest.raises(musicdata.MusicDataError):
        a.chart("synthetic", c, 0)


def test_aptitude_rounding(aptitude_fixture):
    a, h, c = aptitude_fixture
    aptitude_header(a, h)
    v = c["gekisouAptitude"]["variants"][2]
    v["tail"] = [deckdata.Num("7.002"), deckdata.Num("0.002")]
    a.chart("synthetic", c, 0)  # outward radius, not a standard error or an implicit rounding tolerance
    v["tail"][1] = deckdata.Num("0.001")
    with pytest.raises(musicdata.MusicDataError, match="tail"):
        a.chart("synthetic", c, 0)


def test_aptitude_perfect_tail_uses_the_measured_perfect_bonus(aptitude_fixture):
    a, h, c = aptitude_fixture
    header = aptitude_header(a, h)
    v = c["gekisouAptitude"]["variants"][0]
    c["justNotes"] = 1
    v["ranges"][0]["rankBonusPerfect"][0] = deckdata.Num("4.25")
    v["scorePerfect"][0] = deckdata.Num("49.25")
    assert v["ranges"][0]["rankBonus"][0] == 3
    # The Perfect and best channels have different independently measured bonuses.
    # Their separate interval identities must both hold, without a seed or standard-error field.
    assert musicdata.validate_aptitude_statistics(header, c, "synthetic") == a.chart("synthetic", c, 0)
    v["ranges"][0]["rankBonusPerfect"][0] = v["ranges"][0]["rankBonus"][0]
    with pytest.raises(musicdata.MusicDataError, match="tailPerfect"):
        musicdata.validate_aptitude_statistics(header, c, "synthetic")


@pytest.mark.parametrize("group", ["trigger", "condition", "release", "reset", "cumulative"])
def test_confirmed_rank_shape_has_no_linear_rank_cross_terms(aptitude_fixture, group):
    a, h, c = aptitude_fixture
    source = a.rows[("member", 1, 5)][0]
    effect = h["shapes"][0]["effects"][0]
    if group == "cumulative":
        source["_skillCumulativeConditionID"] = 77
        a.cumulative[77] = {
            "_skillCumulativeConditionType": 7012,
            "_conditionValues": [1],
            "_conditionTargetIDs": [],
            "_maxCumulativeCount": 1,
        }
        effect[group] = {"type": 7012, "values": [1], "targetIds": [], "maxCumulativeCount": 1}
    else:
        column = dict(musicdata.CONDITION_GROUPS)[group]
        source[column] = 77
        a.sets[77] = [{"_id": 77, "_conditionIds": [77]}]
        a.conditions[77] = {
            "_conditionType": 7012,
            "_conditionValues": [1],
            "_conditionTargetIDs": [],
            "_isPositive": True,
        }
        effect[group] = [[{"type": 7012, "values": [1], "targetIds": [], "positive": True}]]
    header = aptitude_header(a, h)  # Still checked against independent master rows.
    v = c["gekisouAptitude"]["variants"][0]
    old_weights = v["rangeWeights"]
    v["rangeWeights"] = None
    v["check"]["ranks"] = [1, 1]
    assert c["expectation"]["rangeWeights"] is not None
    assert musicdata.validate_aptitude_statistics(header, c, "synthetic") == c["gekisouAptitude"]
    v["check"]["ranks"][0] = 2
    with pytest.raises(musicdata.MusicDataError, match="check ranks"):
        a.chart("synthetic", c, 0)
    v["check"]["ranks"][0] = 1
    v["rangeWeights"] = old_weights
    with pytest.raises(musicdata.MusicDataError, match="rank-linear domain"):
        musicdata.validate_aptitude_statistics(header, c, "synthetic")


def test_aptitude_no_plain(aptitude_fixture):
    a, h, c = aptitude_fixture
    h["plainKind"] = None
    a.check_header(h, [], {"gekisouAptitude": "single shape"})
    for v in c["gekisouAptitude"]["variants"]:
        v.update(weights=None, rangeWeights=None)
        v["check"]["deck"] = [None, None]
    a.chart("synthetic", c, None)


def test_aptitude_disabled(tmp_path):
    fake = FakeDeck()
    d = master_dir(tmp_path)
    musicdata.export(
        tmp_path / "music.json",
        deckdata.master_files(d),
        KEY,
        CHARTS.__getitem__,
        bgm,
        **PROV,
        deck=musicdata.Deck(module=fake, aptitude=False),
    )
    doc = json.loads((tmp_path / "music.json").read_bytes())
    assert fake.options == [False]
    assert doc["deck"]["gekisouAptitude"] is None
    assert all(c["deck"]["gekisouAptitude"] is None for s in doc["songs"] for c in s["charts"])
    schema_validator().validate(doc)


def test_master_data_without_the_arena_music_table(tmp_path):
    d = master_dir(tmp_path)
    (d / "MasterArenaMusic.bin").unlink()
    manifest = json.loads((d / "MasterManifest.json").read_text(encoding="utf-8"))
    manifest["files"] = [f for f in manifest["files"] if f["name"] != "MasterArenaMusic.bin"]
    (d / "MasterManifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    musicdata.export(
        tmp_path / "music.json",
        deckdata.master_files(d),
        KEY,
        CHARTS.__getitem__,
        bgm,
        **PROV,
        deck=musicdata.Deck(module=FakeDeck(), workers=3),
    )
    doc = json.loads((tmp_path / "music.json").read_bytes())
    assert "MasterArenaMusic" not in doc["provenance"]["master"]["tables"]
    assert "MasterBand" in doc["provenance"]["master"]["tables"]
