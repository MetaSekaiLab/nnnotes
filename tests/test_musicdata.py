"""Music data export (musicdata): metadata, chart facts, BGM length, the deck model's chart statistics and the deck
input on synthetic master data, charts and ACBs. The deck model is a stand-in (FakeDeck) except where the built
extension module is tested itself."""
import gzip
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

import synth
from nnnotes import cli, deckdata, musicdata, score
from nnnotes.master import MasterKey
from test_deckdata import CHART, SMALL, FakeCatalog, rows_of, run
from test_voices import simple_acb

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
ROOT = Path(__file__).resolve().parents[1]


def text(tid, stem):
    return {"_id": tid, "_japanese": f"{stem}-ja", "_english": f"{stem}-en", "_traditionalChinese": f"{stem}-tw",
            "_simplifiedChinese": f"{stem}-cn", "_korean": f"{stem}-ko"}


MUSIC = {"_id": 100002, "_sortOrder": 2, "_startAt": "2026/01/01 0:00:00", "_defaultUnlock": True,
         "_titleTextID": "T2", "_rubyTitleTextID": "", "_phoneticTextID": "P2", "_bandIDs": [1],
         "_bandNameTextID": "", "_vocalCharacterIDs": [1], "_lyricistTextID": "L2", "_composerTextID": "C2",
         "_arrangerTextID": "", "_musicType": 4, "_musicCategories": [1], "_bestMusicTagIDs": [1],
         "_jacketAssetName": "jkt_2", "_gekisouMission1": 1, "_gekisouMission2": 3, "_gekisouMission3": 3,
         "_musicSoundID": 13, "_easyID": 20, "_normalID": 0, "_hardID": 0, "_expertID": 30, "_liveScoreRankGroup": 9,
         "_extra": [7]}
MUSIC1 = dict(MUSIC, _id=100001, _sortOrder=1, _titleTextID="T1", _bandNameTextID="BN", _easyID=10, _expertID=0,
              _liveScoreRankGroup=7)
TABLE_ROWS = {
    "MasterLiveMusic": [MUSIC, MUSIC1],
    "MasterLiveMusicScore": [
        {"_id": 30, "_musicScoreTextFileName": "c/c_03", "_musicScoreLevel": 20, "_fullComboCount": 2,
         "_musicScoreDisplayLevel": 20.5},
        {"_id": 10, "_musicScoreTextFileName": "c/c_01", "_musicScoreLevel": 5, "_fullComboCount": 11,
         "_musicScoreDisplayLevel": 5.0},
        {"_id": 20, "_musicScoreTextFileName": "c/c_02", "_musicScoreLevel": 9, "_fullComboCount": 2,
         "_musicScoreDisplayLevel": 9.0},
        {"_id": 40, "_musicScoreTextFileName": "c/c_04", "_musicScoreLevel": 1, "_fullComboCount": 2,
         "_musicScoreDisplayLevel": 1.0}],                  # a chart of no song
    "MasterText": [text("T1", "one"), text("T2", "two"), text("P2", "pho"), text("L2", "lyr"), text("C2", "com"),
                   text("BN", "crychic"), text("Band1", "mygo"), text("Ch1", "tomori"), text("Ch1s", "tomo"),
                   text("Tag1", "tag"), text("Cat1", "original")],
    "MasterBand": [{"_id": 1, "_nameTextID": "Band1", "_mainColorCode": "#3388BB", "_subColorCode": "#FFFFFF"}],
    "MasterCharacter": [{"_id": 1, "_nameTextID": "Ch1", "_shortNameTextID": "Ch1s", "_bandID": 1,
                         "_mainColorCode": "#77BBDD"}],
    "MasterTag": [{"_id": 1, "_nameTextID": "Tag1"}],
    "MasterLiveMusicCategory": [{"_id": 1, "_musicCategories": [1], "_textKey": "Cat1"}],
    "MasterSound": [{"_id": 13, "_soundCueSheetID": 5, "_cueName": "song2"}],
    "MasterSoundCueSheet": [{"_id": 5, "_cueSheetName": "Bgm2"}],
    "MasterLiveScoreRank": [
        {"_id": 3, "_group": 9, "_liveScoreRank": 7, "_requiredScore": 900, "_battleLiveRequiredScore": 1800},
        {"_id": 1, "_group": 9, "_liveScoreRank": 2, "_requiredScore": 0, "_battleLiveRequiredScore": 0},
        {"_id": 2, "_group": 8, "_liveScoreRank": 6, "_requiredScore": 5, "_battleLiveRequiredScore": 6}],
    # the songs' missions [1, 3, 3] are pattern 3; pattern 2 rows are another song's
    "MasterLiveGekisouRankingScoreBonus": [
        {"_id": 100 * p + 10 * c + k, "_missionPattern": p, "_count": c, "_rank": k,
         "_scoreBonusPercent": (6 - k) * c + 10 * p}
        for p in (2, 3) for c in (1, 2, 3) for k in (1, 2, 3, 4, 5)],
}
CHARTS = {"c/c_01": gzip.compress(json.dumps(CHART).encode("utf-8"), mtime=0),
          "c/c_02": json.dumps(SMALL).encode("utf-8"),
          "c/c_03": gzip.compress(json.dumps(SMALL).encode("utf-8"), mtime=0),
          "c/c_04": json.dumps(SMALL).encode("utf-8")}
ACB = simple_acb({"song2": [1]}, [1], {1: 48000 * 90 + 24})            # 90.0005 s at 48 kHz, cue Length 100
PROV = {"region": "xx", "client": {"versionName": "9.9.9", "versionCode": 99},
        "catalog": {"resourceVersion": None, "sha256": "ab" * 32}}


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
WEIGHT = "0.30000000000000004"                  # a binary64 value: kept as the deck model writes it


def trunc_percent(score, percent):
    return int(score * percent / 100)


class FakeDeck:
    """The interface of nnnotes._deck: statistics made from the deck input as the model reports them."""
    COMMIT = "7e5d84b5998d28c21541ce3f2e0a3dfb1439f4f6"

    def __init__(self, change=None, fail=None):
        self.change, self.fail, self.inputs = change, fail, []

    def info(self):
        return {"name": "ournotes-deck", "version": "0.0.1", "source": "https://github.com/empty-sekai/ournotes-deck",
                "commit": self.COMMIT, "dataFormat": deckdata.DECK_FORMAT, "format": "ournotes-deck.chart-stats/2"}

    def chart_stats(self, data, seeds, workers):
        if self.fail:
            raise ValueError(self.fail)
        doc = json.loads(data)
        self.inputs.append((doc, seeds, workers))
        assert doc["format"] == deckdata.DECK_FORMAT and list(doc["master"]) == [t for t, _ in deckdata.TABLES]
        m = doc["master"]

        def table(name):
            return [dict(zip(m[name]["columns"], r)) for r in m[name]["rows"]]
        levels = {r["_id"]: r["_musicScoreLevel"] for r in table("MasterLiveMusicScore")}
        bonus = {(r["_missionPattern"], r["_count"], r["_rank"]): r["_scoreBonusPercent"]
                 for r in table("MasterLiveGekisouRankingScoreBonus")}
        songs = {}
        for r in table("MasterLiveMusic"):
            for d in musicdata.DIFFICULTIES:
                if r[f"_{d}ID"]:
                    songs[r[f"_{d}ID"]] = (r["_id"], d, [r["_gekisouMission1"], r["_gekisouMission2"],
                                                         r["_gekisouMission3"]])
        charts = []
        for c in doc["charts"]:
            music, d, missions = songs[c["scoreId"]]
            last = max(c["notes"]["timeMs"])
            fevers = list(zip(c["fevers"]["startMs"], c["fevers"]["endMs"]))[:3]
            positions = min(len(c["skillEvents"]["timeMs"]), 5)
            pattern = 3 if missions == [1, 3, 3] else 0
            percents = [[bonus.get((pattern, i + 1, k), 0) for k in range(1, 6)] for i in range(len(fevers))]
            check = {"deck": [[0, 5000]], "exact": 2000, "predicted": 2000.25, "bound": 7.0}
            s = {"scoreId": c["scoreId"], "musicId": music, "difficulty": d, "level": levels[c["scoreId"]],
                 "judgedNotes": sum(score.is_judgement_note(op) for op in c["notes"]["op"]),
                 "convertedNoteCount": len(c["notes"]["id"]), "lastNoteMs": last, "musicLengthMs": last + 1000,
                 "skip": 1.5, "events": [[i % 5, t] for i, t in enumerate(c["skillEvents"]["timeMs"])],
                 "positions": positions, "missions": missions,
                 "ranges": [{"index": i, "mission": missions[i], "startMs": a, "endMs": b,
                             "rankBonusPercent": percents[i][0], "rankBonusPercents": percents[i]}
                            for i, (a, b) in enumerate(fevers)],
                 "justNotes": 0,
                 "seeds": [{"seed": 0, "score": 1234,
                            "ranges": [{"rangeScore": 101 * (i + 1), "rankBonus": trunc_percent(101 * (i + 1), p[0]),
                                        "maxCombo": 1, "justCount": 0, "lotResults": [0, 0, 0, 0],
                                        "rangeScorePerfect": 101 * (i + 1)} for i, p in enumerate(percents)],
                            "weights": [["W"] * positions], "check": check, "scorePerfect": 1234,
                            "rangeWeights": [[["W"] * len(fevers)] * positions],
                            "rankCheck": {"ranks": [2] * len(fevers), "exact": 2001, "predicted": 2000.5,
                                          "bound": 7.0} if fevers else None}],
                 "offSeeds": [{"seed": 0, "score": 1000, "weights": [["W"] * positions], "check": dict(check)}]}
            if self.change:
                self.change(s)
            charts.append(s)
        out = json.dumps({"format": "ournotes-deck.chart-stats/2", "source": {}, "model": {"power": 300000},
                          "kinds": [{"id": 0, "effectType": 2000, "activationTimeSecond": 7.5}], "charts": charts})
        return out.replace('"W"', WEIGHT)


def export(tmp_path, rows=TABLE_ROWS, charts=CHARTS, bgm=bgm, out="music.json", deck=None, **kw):
    d = master_dir(tmp_path, rows)
    return musicdata.export(tmp_path / out, deckdata.master_files(d), KEY, charts.__getitem__, bgm, **PROV,
                            deck=musicdata.Deck(module=deck, workers=3) if deck is not None else None, **kw)


def schema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    doc = json.loads((ROOT / "docs" / "schema" / "music-data.schema.json").read_text(encoding="utf-8"))
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
    assert list(doc) == ["format", "provenance", "languages", "bands", "characters", "tags", "categories", "deck",
                         "songs"]
    assert doc["format"] == "nnnotes.music-data/1" and doc["languages"] == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    p = doc["provenance"]
    assert list(p) == ["region", "client", "catalog", "master", "exporter", "deck"] and p["deck"] is None
    assert list(p["master"]["tables"]) == list(musicdata.SONG_TABLES)      # without the deck model: its tables only
    assert p["exporter"] == {"name": "nnnotes", "version": cli.__version__, "chartFormat": deckdata.CHART_FORMAT}
    assert doc["deck"] is None
    assert doc["bands"] == [{"id": 1, "name": {"ja": "mygo-ja", "en": "mygo-en", "zh-Hant": "mygo-tw",
                                               "zh-Hans": "mygo-cn", "ko": "mygo-ko"},
                             "mainColor": "#3388BB", "subColor": "#FFFFFF"}]
    assert doc["characters"][0]["name"]["ja"] == "tomori-ja" and doc["characters"][0]["bandId"] == 1
    assert doc["categories"] == [{"id": 1, "musicCategories": [1], "name": dict(doc["categories"][0]["name"])}]
    one, two = doc["songs"]
    assert (one["id"], two["id"]) == (100001, 100002)                     # sorted by id
    assert one["bandName"]["ja"] == "crychic-ja" and two["bandName"] is None
    assert two["title"]["zh-Hans"] == "two-cn" and two["ruby"] is None and two["arranger"] is None
    assert two["lyricist"]["en"] == "lyr-en" and two["gekisouMissions"] == [1, 3, 3]
    assert two["bgm"] == {"soundId": 13, "cueSheet": "Bgm2", "cue": "song2",
                          "length": {"lengthMs": 100, "samples": 48000 * 90 + 24, "sampleRate": 48000,
                                     "durationMs": 90000}}
    assert two["master"]["MasterLiveMusic"]["_extra"] == [7]              # the whole row
    assert two["scoreRanks"] == [{"rank": "D", "requiredScore": 0, "battleRequiredScore": 0},   # by required score
                                 {"rank": "SS", "requiredScore": 900, "battleRequiredScore": 1800}]
    assert [r["_id"] for r in two["master"]["MasterLiveScoreRank"]] == [1, 3]
    assert one["scoreRanks"] == [] and one["master"]["MasterLiveScoreRank"] == []   # a group without rows
    assert [c["difficulty"] for c in two["charts"]] == ["easy", "expert"]  # difficulties without a score are left out
    assert [c["difficulty"] for c in one["charts"]] == ["easy"]
    assert all(c["deck"] is None for s in doc["songs"] for c in s["charts"])
    schema_validator().validate(doc)


def test_chart_facts(tmp_path):
    export(tmp_path)
    doc = json.loads((tmp_path / "music.json").read_bytes())
    c = doc["songs"][0]["charts"][0]                                      # CHART at 120 BPM
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
    f = musicdata.bpm_facts(ev, 500, 4000)              # 100 for 500 ms, 200 for 500, 150 for 2500; 300 after
    assert (f["main"], f["min"], f["max"]) == (150.0, 100.0, 200.0)
    assert [x["timeMs"] for x in f["changes"]] == [0, 1000, 1500, 9000]
    assert musicdata.bpm_facts(ev, 1200, 1200)["main"] == 200.0   # one instant: the BPM at that time
    tie = [(120.0, P(0)), (180.0, P(1000))]
    assert musicdata.bpm_facts(tie, 0, 2000)["main"] == 120.0     # equal time: the earliest
    with pytest.raises(musicdata.MusicDataError):
        musicdata.bpm_facts([], 0, 1)


# ---------------------------------------------------------------- the deck model
def test_deck(tmp_path):
    fake = FakeDeck()
    r = export(tmp_path, deck=fake)
    raw = (tmp_path / "music.json").read_bytes()
    doc = json.loads(raw)
    assert r["deck"] == FakeDeck.COMMIT and r["unplayable"] == 0
    (deck_input, seeds, workers), = fake.inputs
    assert (seeds, workers) == (musicdata.DECK_SEEDS, 3)
    assert [c["scoreId"] for c in deck_input["charts"]] == [10, 20, 30]  # the songs' charts, not chart 40
    assert deck_input["provenance"]["master"] == {"source": "api", "version": "v-test"}
    p = doc["provenance"]
    assert p["deck"] == {k: fake.info()[k] for k in ("name", "version", "source", "commit", "format")}
    assert list(p["master"]["tables"]) == list(musicdata.tables_of(True, False))
    assert doc["deck"] == {"model": {"power": 300000},
                           "kinds": [{"id": 0, "effectType": 2000, "activationTimeSecond": 7.5}]}
    c = doc["songs"][0]["charts"][0]
    assert list(c["deck"]) == list(musicdata.DECK_CHART_KEYS)
    assert c["deck"]["events"] == [[0, 2500], [1, 500]] and c["deck"]["ranges"][1]["startMs"] == 2000
    assert c["deck"]["unplayable"] is None and c["deck"]["skip"] == 1.5
    # the ranks: the song's mission pattern (3) rows of every range
    assert [r["rankBonusPercents"] for r in c["deck"]["ranges"]] == [[35, 34, 33, 32, 31], [40, 38, 36, 34, 32]]
    seed = c["deck"]["seeds"][0]
    assert seed["rankCheck"]["ranks"] == [2, 2] and seed["scorePerfect"] == seed["score"]
    assert [r["rankBonus"] for r in seed["ranges"]] == [35, 80]
    assert c["deck"]["offSeeds"][0]["score"] == 1000
    # the deck model's numbers as it writes them: a binary64 value is not narrowed to binary32
    assert b'"weights":[[' + WEIGHT.encode() + b',' + WEIGHT.encode() + b']]' in raw and b'"predicted":2000.25' in raw
    assert "master" not in doc and "charts" not in doc
    schema_validator().validate(doc)
    (tmp_path / "m").rename(tmp_path / "m0")
    export(tmp_path, deck=FakeDeck(), out="again.json")
    assert (tmp_path / "again.json").read_bytes() == raw


@pytest.mark.parametrize("change, match", [
    (lambda s: s.update(judgedNotes=s["judgedNotes"] + 1), "chart 10 .*judged note count"),
    (lambda s: s.update(musicLengthMs=0), "music length 0 differs"),
    (lambda s: s.update(level=99), "level 99 differs"),
    (lambda s: s["missions"].reverse(), "Gekisou missions"),
    (lambda s: s["events"].pop(), "skill event times"),
    (lambda s: s["ranges"] and s["ranges"][0].update(endMs=1), "fevers"),
    (lambda s: s.update(difficulty="hard"), "difficulty 'hard' differs"),
    (lambda s: s["ranges"] and s["ranges"][0]["rankBonusPercents"].__setitem__(2, 99), "range 0: .*percentages"),
    (lambda s: s["ranges"] and s["ranges"][0].update(rankBonusPercent=1), "range 0: .*percentages"),
    (lambda s: s["ranges"] and s["seeds"][0]["ranges"][0].update(rankBonus=-1), "rank bonus -1 is not trunc"),
    (lambda s: s["seeds"][0]["ranges"].pop(), "range results for"),
    (lambda s: s.pop("offSeeds"), "no Gekisou off statistics"),
    (lambda s: s["offSeeds"].append(s["offSeeds"][0]), "no Gekisou off statistics"),
    (lambda s: s["offSeeds"][0]["weights"].append(None), "Gekisou off: weights"),
    (lambda s: s["offSeeds"][0]["check"].update(exact=0), "Gekisou off: the check deck scores 0"),
    (lambda s: s["seeds"][0].update(scorePerfect=1), "otherwise on the Perfect play"),
    (lambda s: s["seeds"][0]["weights"][0].pop(), "weights are not"),
    (lambda s: s["seeds"][0]["weights"].append(None), "weights are not"),
    (lambda s: s["seeds"][0].update(rangeWeights=[[]]), "rangeWeights are not"),
    (lambda s: s["seeds"][0]["check"].update(bound=0.1), "the check deck scores 2000"),
    (lambda s: s["ranges"] and s["seeds"][0]["rankCheck"].update(exact=0), "at ranks"),
    (lambda s: s["ranges"] and s["seeds"][0]["rankCheck"].update(ranks=[6, 1]), "rank check ranks"),
])
def test_deck_checks(tmp_path, change, match):
    with pytest.raises(musicdata.MusicDataError, match=match):
        export(tmp_path, deck=FakeDeck(change))
    assert not (tmp_path / "music.json").exists()


def test_deck_without_range_weights(tmp_path):
    """A chart without range weights, a kind without them and a kind without Gekisou off weights are carried."""
    def change(s):
        s["seeds"][0]["rangeWeights"] = None if s["scoreId"] == 10 else [None]
        s["seeds"][0]["rankCheck"] = None
        s["offSeeds"][0]["weights"] = [None]
    export(tmp_path, deck=FakeDeck(change))
    doc = json.loads((tmp_path / "music.json").read_bytes())
    decks = {c["scoreId"]: c["deck"] for s in doc["songs"] for c in s["charts"]}
    assert decks[10]["seeds"][0]["rangeWeights"] is None and decks[20]["seeds"][0]["rangeWeights"] == [None]
    assert decks[30]["offSeeds"][0]["weights"] == [None]
    schema_validator().validate(doc)


def test_rank_bonus_percents():
    assert [musicdata.mission_pattern(m) for m in ([1, 2, 0], [2, 2, 2], [1, 2, 3], [1, 1, 3], [1, 3, 3],
                                                    [3, 1, 3])] == [0, 1, 2, 3, 3, 3]
    rows = [{"_missionPattern": 2, "_count": 1, "_rank": 1, "_scoreBonusPercent": 30},
            {"_missionPattern": 2, "_count": 3, "_rank": 5, "_scoreBonusPercent": 4},
            {"_missionPattern": 2, "_count": 3, "_rank": 5, "_scoreBonusPercent": 5},     # a later row wins
            {"_missionPattern": 2, "_count": 4, "_rank": 1, "_scoreBonusPercent": 9},     # no fourth range
            {"_missionPattern": 2, "_count": 1, "_rank": 6, "_scoreBonusPercent": 9},     # no sixth rank
            {"_missionPattern": 1, "_count": 2, "_rank": 1, "_scoreBonusPercent": 9}]     # another pattern
    assert musicdata.rank_bonus_percents(rows, [1, 2, 3]) == [[30, 0, 0, 0, 0], [0] * 5, [0, 0, 0, 0, 5]]
    assert musicdata.rank_bonus_percents(rows, [0, 0, 0]) == [[0] * 5] * 3


def test_deck_errors(tmp_path, monkeypatch):
    with pytest.raises(musicdata.MusicDataError, match="deck model: chart 30: the check deck scores"):
        export(tmp_path, deck=FakeDeck(fail="chart 30: the check deck scores 1, the chart statistics predict 2"))
    assert not (tmp_path / "music.json").exists()
    monkeypatch.setitem(sys.modules, "nnnotes._deck", None)              # an installation without the module
    with pytest.raises(musicdata.MusicDataError, match="not built into this installation.*--no-deck"):
        musicdata.Deck()


def test_deck_module():
    deck = pytest.importorskip("nnnotes._deck")
    info = deck.info()
    assert info["name"] == "ournotes-deck" and info["format"] == "ournotes-deck.chart-stats/2"
    assert info["dataFormat"] == deckdata.DECK_FORMAT and re.fullmatch(r"[0-9a-f]{40}", info["commit"])
    lock = (ROOT / "rust" / "Cargo.lock").read_text(encoding="utf-8")
    assert f"#{info['commit']}\"" in lock                               # the commit Cargo.lock pins
    with pytest.raises(ValueError, match="not a deck data file"):
        deck.chart_stats('{"format": "x"}')
    assert musicdata.Deck().info()["commit"] == info["commit"]


def test_the_extension_version_is_the_package_version():
    from nnnotes import __version__
    cargo = (ROOT / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', cargo, re.M).group(1) == __version__


# ---------------------------------------------------------------- the deck input (--full)
def test_full(tmp_path):
    asked = []

    def fetch(name):
        asked.append(name)
        return CHARTS[name]
    r = export(tmp_path, charts=type("C", (), {"__getitem__": staticmethod(fetch)})(), full=True, deck=FakeDeck())
    doc = json.loads((tmp_path / "music.json").read_bytes())
    assert r["full"] is True and sorted(asked) == sorted(CHARTS)          # every chart asset read once
    assert list(doc)[-2:] == ["master", "charts"]
    assert list(doc["master"]) == [t for t, _ in deckdata.TABLES]
    assert [c["scoreId"] for c in doc["charts"]] == [10, 20, 30, 40]      # every chart, songs or not
    tables, _ = deckdata.read_master(deckdata.master_files(tmp_path / "m"), KEY)
    assert doc["master"] == json.loads(deckdata.encode({"m": deckdata.master_subset(tables)}))["m"]
    assert doc["charts"][0] == deckdata.chart_record(10, "Live/MusicScore/c/c_01", CHARTS["c/c_01"])
    schema_validator().validate(doc)
    (tmp_path / "x").mkdir()
    export(tmp_path / "x", full=True)                                     # the deck input without the deck model
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
    assert r["jackets"] == 1 and asked == ["jkt_2"]           # the two songs share one jacket
    assert Image.open(tmp_path / "j" / "jkt_2.webp").size == (64, 64)
    (tmp_path / "x").mkdir()
    assert export(tmp_path / "x", out="b.json")["jackets"] == 0

    def missing(name):
        raise musicdata.MusicDataError(f"jacket {name}: no asset")
    (tmp_path / "y").mkdir()
    with pytest.raises(musicdata.MusicDataError, match="jacket jkt_2"):
        export(tmp_path / "y", jacket=missing, jackets_dir=tmp_path / "y" / "j")
    assert not (tmp_path / "y" / "music.json").exists()


@pytest.mark.parametrize("change, match", [
    (lambda r: r["MasterText"].pop(0), "MasterText has no text 'T1'"),
    (lambda r: r["MasterLiveMusicScore"].pop(0), "expert score 30 is not in MasterLiveMusicScore"),
    (lambda r: r["MasterSound"].clear(), "sound 13 is not in MasterSound"),
    (lambda r: r["MasterLiveMusic"].append(dict(MUSIC)), "MasterLiveMusic: _id 100002 occurs twice"),
    (lambda r: r["MasterLiveScoreRank"][0].update(_liveScoreRank=8), "MasterLiveScoreRank 3: unknown rank 8"),
])
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
    code, _, err = run(["--region", "xx", "music-data", "--master-files", str(d), "--no-deck", "-o", str(out)],
                       capsys)
    assert code == 2 and "master.key" in err
    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    monkeypatch.setattr(cli, "open_catalog", lambda cfg, **kw: FakeCatalog(CHARTS))
    monkeypatch.setattr(score, "fetch_chart", lambda cat, name: cat.charts[name])
    base = ["--region", "xx", "--cache", str(tmp_path / "cache"), "music-data", "--master-files", str(d), "--no-bgm"]
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
    assert doc["provenance"]["catalog"] == {"resourceVersion": None,
                                            "sha256": hashlib.sha256(b"remote catalog").hexdigest()}
    assert len(doc["charts"]) == 4
    monkeypatch.setattr(musicdata, "Deck", lambda **kw: real(module=FakeDeck(fail="boom"), **kw))
    code, _, err = run(base + ["-o", str(tmp_path / "x.json")], capsys)
    assert code == 1 and "deck model: boom" in err and not (tmp_path / "x.json").exists()
