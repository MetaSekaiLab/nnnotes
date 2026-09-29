"""Self-test of the music data gates (music_data.py): a synthetic music-data.json passes every gate, and each gate
fails on the defect it is there for. Runs in seconds, without the network:

    python -m pytest -q -p no:cacheprovider .github/scripts/test_music_data.py

The JSON Schema gate uses docs/schema/music-data.schema.json (or $MUSIC_DATA_SCHEMA) when the checkout has it; the
page smoke test runs when $MUSIC_DATA_PAGE names ournotes-player's examples/songs (and Node.js is installed). Real
files, when named: $MUSIC_DATA_SAMPLE (a file with the play scenario fields: the content gates pass; one made before
the ranges' luckPoints and the Gekisou skill aptitude, the deck and aptitude gates stop it on those alone) and
$MUSIC_DATA_OLD_SAMPLE (one without the play scenario fields: the scenario gate stops it).
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import music_data                                   # noqa: E402
from music_data import Context, gates               # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LANGS = ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
TABLES = music_data.SONG_TABLES + ("MasterLiveSkillEffect", "MasterLiveGekisouRankingScoreBonus")
BIN = {t: hashlib.sha256(t.encode()).hexdigest() for t in TABLES}      # the manifest's hashes of the served files
DECK = "d4" * 20
CONTENT = ("deck", "scenarios", "finite", "references", "bgm")


def schema_path():
    p = Path(os.environ.get("MUSIC_DATA_SCHEMA") or ROOT / music_data.SCHEMA)
    if not p.is_file():
        return None
    return p if importlib.util.find_spec("jsonschema") else None


def page_path():
    p = os.environ.get("MUSIC_DATA_PAGE")
    return Path(p) if p and Path(p, "ranking.js").is_file() and shutil.which("node") else None


# ---------------------------------------------------------------- a synthetic file
def text(stem):
    return {lang: f"{stem}-{lang}" for lang in LANGS}


def check_deck(exact):
    return {"deck": [[0, 5000], None], "exact": exact, "predicted": exact + 0.25, "bound": 7.0}


LUCK_SEEDS = [11, 22]                                                   # a luck chart's seeds; else the one seed 0
# the Gekisou skill aptitude's shapes: id, source, mission, band condition
SHAPES = [(0, "member", 1, False), (1, "support", 2, True), (2, "member", 3, False), (3, "support", 4, False)]
SEED_RULE = {"deterministicTest": 4, "batches": [32, 64, 128, 256, 512, 1024], "relative": 0.01, "baseline": 0.001,
             "crossSeeds": 64}


def effect(band=False):
    return {"effectType": 2000, "triggerType": 7010, "activationTimeSecond": 5.0, "effectValue": 1000,
            "maxEffectValue": 0, "effectLimitCount": 0, "effectExecuteLimitCount": 0, "skillTargetIds": [],
            "trigger": [[{"type": 7010, "values": [1], "positive": True, "targetIds": []}]],
            "condition": [[{"type": 5000, "values": [1], "positive": True, "targetIds": None}]] if band else [],
            "release": [], "reset": [], "cumulative": None}


def aptitude_head():
    return {"plainKind": 0, "host": "one performer with a synthetic empty Gekisou skill", "seedRule": SEED_RULE,
            "shapes": [{"id": i, "source": src, "mission": m, "bandCondition": band, "effects": [effect(band)],
                        "skills": [{"id": 10 + i, "level": 5, "memberTargetIds": [41] if band else None,
                                    "bandIds": [1] if band else None}]} for i, src, m, band in SHAPES]}


def variant(deck, shape, match, deterministic):
    """One shape's aptitude on a chart: deterministic on a chart without a luck range, else on 32 seeds."""
    se = 0 if deterministic else 12.5

    def p(m):
        return [m, se]
    ranges = [{"rangeScore": p(300), "rankBonus": p(750), "rangeScorePerfect": p(300), "maxCombo": p(0),
               "justCount": p(0), "luckPoints": p(2)} for _ in deck["ranges"]]
    n = 1 if deterministic else 32
    linear = deck["seeds"][0]["rangeWeights"] is not None
    result = {"shape": shape, "bandMatch": match, "deterministic": deterministic, "seeds": n, "seTargetMet": True,
            "crossSeeds": min(n, SEED_RULE["crossSeeds"]), "score": p(1500), "scorePerfect": p(1500),
            "tail": p(1500 - 1050 * len(ranges)), "tailPerfect": p(1500 - 1050 * len(ranges)),
            "converted": p(0), "ranges": ranges,
            "weights": [p(0.01), p(0.02)],
            "rangeWeights": [[p(0.001)] * len(ranges), [p(0.002)] * len(ranges)] if linear else None,
            "check": {"seed": deck["seeds"][0]["seed"], "ranks": [2] * len(ranges) if linear else [1] * len(ranges),
                      "deck": [[0, 7000], None], "exact": 5000, "predicted": 5000.5, "bound": 3.0}}
    base = deck["seeds"][0]
    score = base["score"] + 1500
    weight = base["weights"][0][0] + 0.01
    if linear:
        for r, info in zip(base["ranges"], deck["ranges"], strict=True):
            percent = info["rankBonusPercents"][1]
            score += int(r["rangeScore"] * percent / 100) - r["rankBonus"] + 300 * (percent - 250) / 100
        weight += (190 - 250) / 100 * (0.1 + 0.001)
    predicted = 1000003 * (score / 300000 + 0.7 * weight)
    result["check"].update(exact=round(predicted), predicted=predicted)
    return result


def aptitude(deck, luck):
    missions = {r["mission"] for r in deck["ranges"]}
    variants = [variant(deck, i, match, deterministic=not luck) for i, _, m, band in SHAPES
                if m == 4 or m in missions for match in ((True, False) if band else (None,))]
    factors = [{"judgedNotes": 12, "justNotes": 0, "perfectNotes": 0, "tailNotes": 2, "comboAtStart": 5,
                "lotteries": [4.0, 0.0] if r["mission"] == 2 else [0, 0]} for r in deck["ranges"]]
    return {"factors": factors, "variants": variants}


def deck_chart(luck=False):
    def one(n):
        return {"seed": n, "score": 120000 + n, "ranges": [{"rangeScore": 4000, "rankBonus": 10000, "maxCombo": 10,
                                                            "justCount": 0, "luckPoints": 3 if luck else 0,
                                                            "lotResults": [1, 2, 0, 1] if luck else [0, 0, 0, 0],
                                                            "rangeScorePerfect": 4000}],
                "weights": [[0.5, 0.25]], "check": check_deck(2000), "scorePerfect": 120000 + n,
                "rangeWeights": [[[0.1], [0.05]]], "rankCheck": dict(check_deck(1900), ranks=[3])}
    d = {"convertedNoteCount": 20, "skip": 0.01, "events": [[0, 1000], [1, 3000]], "positions": 2,
         "ranges": [{"index": 0, "mission": 2 if luck else 1, "startMs": 1000, "endMs": 5000,
                     "rankBonusPercent": 250, "rankBonusPercents": [250, 190, 160, 100, 100]}],
         "justNotes": 0, "seeds": [one(n) for n in (LUCK_SEEDS if luck else [0])],
         "offSeeds": [{"seed": 0, "score": 90000, "weights": [[0.4, 0.2]], "check": check_deck(1500)}],
         "unplayable": None}
    d["gekisouAptitude"] = aptitude(d, luck)
    return d


def chart(difficulty, score_id, last=60000, luck=False):
    return {"difficulty": difficulty, "scoreId": score_id, "level": 10, "displayLevel": 10.5, "fullComboCount": 20,
            "asset": {"key": f"Live/MusicScore/c/c_{score_id}", "sha256": "ab" * 32},
            "notes": {"judged": 20, "total": 22, "byOperateType": {"1": 20, "120": 2}},
            "bpm": {"main": 120.0, "min": 120.0, "max": 120.0, "changes": [{"timeMs": 0, "bpm": 120.0}]},
            "firstNoteMs": 1000, "lastJudgedNoteMs": last, "lastNoteMs": last, "musicLengthMs": last + 1000,
            "skillEventsMs": [1000, 3000], "fevers": [[1000, 5000]], "deck": deck_chart(luck)}


def song(i, charts):
    ranks = ["D", "C", "B", "A", "S", "SS"]
    return {"id": i, "sortOrder": i, "startAt": "2026/01/01 0:00:00", "defaultUnlock": True, "title": text(f"t{i}"),
            "ruby": None, "phonetic": text(f"p{i}"), "bandIds": [1], "bandName": None, "vocalCharacterIds": [1],
            "lyricist": text("l"), "composer": text("c"), "arranger": None, "musicType": 1, "musicCategories": [1],
            "bestMusicTagIds": [1], "jacket": f"jkt_{i}", "gekisouMissions": [1, 2, 3],
            "bgm": {"soundId": i, "cueSheet": f"Bgm{i}", "cue": f"song{i}",
                    "length": {"lengthMs": 90000, "samples": 48000 * 90, "sampleRate": 48000, "durationMs": 90000}},
            "scoreRanks": [{"rank": r, "requiredScore": n * 1000, "battleRequiredScore": n * 2000}
                           for n, r in enumerate(ranks)],
            "charts": charts, "master": {"MasterLiveMusic": {"_id": i, "_rate": 1.5}, "MasterLiveScoreRank": []}}


def sample() -> dict:
    kind = {"id": 0, "effectType": 2000, "activationTimeSecond": 5.0, "durationMs": 5000, "skillTargetIds": [],
            "skillConditionGroup": 0, "skillReleaseConditionGroup": 0, "effectLimitCount": 0,
            "effectExecuteLimitCount": 0, "effectExecuteLimitResetConditionGroup": 0, "rows": [1], "values": [10000]}
    return {
        "format": "nnnotes.music-data/1",
        "provenance": {
            "region": "tw", "client": {"versionName": "1.0.1", "versionCode": 25},
            "catalog": {"resourceVersion": None, "sha256": "cd" * 32},
            "master": {"source": "api", "version": "v-test", "tables": {t: {"sha256": BIN[t]} for t in TABLES}},
            "exporter": {"name": "nnnotes", "version": "0.1.2", "chartFormat": "nnnotes.live-score/1"},
            "deck": {"name": "ournotes-deck", "version": "0.0.1",
                     "source": "https://github.com/empty-sekai/ournotes-deck", "commit": DECK,
                     "format": "ournotes-deck.chart-stats/2"}},
        "languages": LANGS,
        "bands": [{"id": 1, "name": text("band"), "mainColor": "#3388BB", "subColor": "#FFFFFF"}],
        "characters": [{"id": 1, "bandId": 1, "name": text("ch"), "shortName": text("c"), "mainColor": "#77BBDD"}],
        "tags": [{"id": 1, "name": text("tag")}],
        "categories": [{"id": 1, "musicCategories": [1], "name": text("cat")}],
        "deck": {"model": {"power": 300000, "checkPower": 1000003, "gekisouAptitude": "one skill at a time"},
                 "kinds": [kind], "gekisouAptitude": aptitude_head()},
        "songs": [song(100001, [chart("easy", 10), chart("expert", 30, luck=True)]),
                  song(100002, [chart("expert", 40, luck=True)])],
    }


def context(tmp_path: Path, doc: dict, **kw) -> tuple[bytes, Context]:
    """The file's bytes and what check reads next to it: the decoded master data and its snapshot, the jackets (the
    page smoke test only with page=page_path(): it starts Node.js)."""
    master = tmp_path / "master"
    master.mkdir(exist_ok=True)
    files = {}
    for t in TABLES:
        data = json.dumps({"_allData": []}).encode()
        (master / f"{t}.json").write_bytes(data)
        files[f"{t}.json"] = hashlib.sha256(data).hexdigest()
    listed = [{"name": f"{t}.bin", "hash": BIN[t], "size": 1} for t in TABLES]
    (master / music_data.MANIFEST).write_text(json.dumps({"version": "v-test", "files": listed}), encoding="utf-8")
    jackets = tmp_path / "jackets"
    jackets.mkdir(exist_ok=True)
    for s in doc.get("songs") or []:
        if s.get("jacket"):
            (jackets / f"{s['jacket']}.webp").write_bytes(b"RIFF....WEBP")
    # an infinity as nnnotes writes it (1e999: JSON that JavaScript reads too); a NaN stays NaN (nnnotes writes none)
    raw = json.dumps(doc, ensure_ascii=False).replace("Infinity", "1e999").encode("utf-8")
    (tmp_path / "music-data.json").write_bytes(raw)
    ctx = Context(region="tw", language="zh-Hant", master=master,
                  snapshot={"entry": {"version": "v-test", "client_version": "1.0.1"}, "files": files},
                  deck_commit=DECK, nnnotes_version="0.1.2", jackets=jackets, schema=schema_path(), published=None,
                  page=None, file=tmp_path / "music-data.json")
    for k, v in kw.items():
        setattr(ctx, k, v)
    return raw, ctx


def run(tmp_path, doc, **kw) -> dict:
    return gates(*context(tmp_path, doc, **kw))


def gate(report: dict, name: str) -> dict:
    return next(g for g in report["gates"] if g["gate"] == name)


def failures(report: dict) -> list:
    return [(g["gate"], g["failures"]) for g in report["gates"] if not g["passed"]]


def seed(doc, song=0, chart=0):
    return doc["songs"][song]["charts"][chart]["deck"]["seeds"][0]


def apt(doc, song=0, chart=0):
    return doc["songs"][song]["charts"][chart]["deck"]["gekisouAptitude"]


def var(doc, i=0, song=0, chart=0):
    return apt(doc, song, chart)["variants"][i]


def shape(doc, i):
    return doc["deck"]["gekisouAptitude"]["shapes"][i]


def nonlinear(doc, song=0, chart=0):
    """A chart whose ranks are not linear (overlapping ranges): no rangeWeights, the checks at rank 1."""
    d = doc["songs"][song]["charts"][chart]["deck"]
    for s in d["seeds"]:
        s.update(rangeWeights=None, rankCheck=None)
    for v in d["gekisouAptitude"]["variants"]:
        v["rangeWeights"] = None
        v["check"]["ranks"] = [1] * len(d["ranges"])
        predicted = 1000003 * ((d["seeds"][0]["score"] + v["score"][0]) / 300000 + 0.7 * 0.51)
        v["check"].update(exact=round(predicted), predicted=predicted)


# ---------------------------------------------------------------- the gates
def test_the_sample_passes_every_gate(tmp_path):
    r = run(tmp_path, sample(), page=page_path())
    assert r["passed"], failures(r)
    assert [g["gate"] for g in r["gates"]] == [name for name, _ in music_data.GATES]
    assert not [g for g in r["gates"] if g["warningCount"]]
    assert r["sha256"] == hashlib.sha256((tmp_path / "music-data.json").read_bytes()).hexdigest()


def test_schema(tmp_path):
    if schema_path() is None:
        pytest.skip("no docs/schema/music-data.schema.json (a fork not synced with upstream) or no jsonschema")
    doc = sample()
    doc["songs"][0]["charts"][0]["level"] = "10"
    g = gate(run(tmp_path, doc), "schema")
    assert not g["passed"] and any("songs/0/charts/0/level" in f for f in g["failures"])


def test_page_smoke(tmp_path):
    if page_path() is None:
        pytest.skip("set MUSIC_DATA_PAGE to ournotes-player's examples/songs (and install Node.js)")
    assert gate(run(tmp_path, sample(), page=page_path()), "page")["passed"]
    doc = sample()
    for s in doc["songs"]:
        for c in s["charts"]:
            c["deck"].pop("offSeeds")
    g = gate(run(tmp_path, doc, page=page_path()), "page")
    assert not g["passed"] and any("free scenario" in f for f in g["failures"])


def test_page_aptitude_check_reconstruction(tmp_path):
    page = page_path()
    if page is None or "aptitudeFigures" not in (page / "ranking.js").read_text(encoding="utf-8"):
        pytest.skip("page does not yet expose the aptitude API")
    doc = sample()
    # Stochastic check values are not reconstructible from means; changing them must not trigger reconstruction.
    var(doc, 0, 0, 1)["check"].update(exact=99999999, predicted=99999999)
    assert gate(run(tmp_path, doc, page=page), "page")["passed"]
    # Even a self-consistent exported check is independently rejected when deterministic deltas disagree.
    var(doc)["check"].update(exact=99999999, predicted=99999999)
    g = gate(run(tmp_path, doc, page=page), "page")
    assert any("deterministic check reconstruction" in f for f in g["failures"]), g


def unplayable(doc):
    doc["songs"][1]["charts"][0]["deck"]["unplayable"] = "more than three fevers"      # its seeds kept


@pytest.mark.parametrize("change, name, match", [
    # the play scenario fields
    (lambda d: d["songs"][0]["charts"][0]["deck"].pop("offSeeds"), "scenarios", "offSeeds missing"),
    (lambda d: d["songs"][0]["charts"][1]["deck"]["offSeeds"].append({}), "scenarios", "offSeeds has 2 entries"),
    (lambda d: d["songs"][0]["charts"][0]["deck"]["ranges"][0].pop("rankBonusPercents"), "scenarios",
     "rankBonusPercents missing"),
    (lambda d: d["songs"][0]["charts"][0]["deck"]["ranges"][0].update(rankBonusPercents=[250, 190, 160, 100]),
     "scenarios", "rankBonusPercents not five ints"),
    (lambda d: d["songs"][0]["charts"][0]["deck"]["ranges"][0].update(rankBonusPercents=[251, 190, 160, 100, 100]),
     "scenarios", "is not rankBonusPercent"),
    (lambda d: seed(d).pop("scorePerfect"), "scenarios", "no scorePerfect"),
    (lambda d: seed(d).pop("rangeWeights"), "scenarios", "no rangeWeights"),
    (lambda d: seed(d).pop("rankCheck"), "scenarios", "no rankCheck"),
    (lambda d: seed(d)["ranges"][0].pop("rangeScorePerfect"), "scenarios", "rangeScorePerfect missing"),
    (lambda d: seed(d).update(rangeWeights=[[[0.1]]]), "scenarios", "rangeWeights are not"),
    (lambda d: seed(d)["rankCheck"].update(exact=99999), "scenarios", "rank check deck is not within"),
    # the Gekisou skill aptitude
    (lambda d: d["deck"].pop("gekisouAptitude"), "aptitude", "deck.gekisouAptitude missing"),
    (lambda d: d["deck"]["model"].pop("gekisouAptitude"), "aptitude", "deck.model.gekisouAptitude: no text"),
    (lambda d: d["deck"]["gekisouAptitude"].update(plainKind=1), "aptitude", "the page's plain kind is 0"),
    (lambda d: d["deck"]["kinds"][0].update(durationMs=6000), "aptitude", "plainKind 0, the page's plain kind is None"),
    (lambda d: d["deck"]["kinds"][0].update(durationMs=6000), "aptitude", "weights or rangeWeights without a plain"),
    (lambda d: d["deck"]["gekisouAptitude"].pop("host"), "aptitude", "deck.gekisouAptitude: no host"),
    (lambda d: d["deck"]["gekisouAptitude"].update(seedRule=dict(SEED_RULE, batches=[64, 32])), "aptitude",
     "deck.gekisouAptitude.seedRule"),
    (lambda d: shape(d, 1).update(id=5), "aptitude", "ids are not 0, 1, 2, ... in order"),
    (lambda d: shape(d, 0).update(source="card"), "aptitude", "shape 0: source 'card'"),
    (lambda d: shape(d, 2).update(mission=5), "aptitude", "shape 2: mission 5"),
    (lambda d: shape(d, 0).update(bandCondition=True), "aptitude", "bandCondition True (a support skill's alone)"),
    (lambda d: shape(d, 3).update(bandCondition=True), "aptitude",
     "shape 3: bandCondition True, its effects have 0 condition 5000"),
    (lambda d: shape(d, 1)["effects"][0]["condition"][0][0].update(targetIds=[41]), "aptitude",
     "condition 5000 targetIds not null"),
    (lambda d: shape(d, 0)["effects"][0].pop("cumulative"), "aptitude", "effect 0: cumulative missing or malformed"),
    (lambda d: shape(d, 0)["effects"][0].update(effectValue=1.5), "aptitude", "effectValue missing or malformed"),
    (lambda d: shape(d, 0)["effects"][0].update(reset=None), "aptitude", "reset missing or malformed"),
    (lambda d: shape(d, 1)["skills"][0].update(memberTargetIds=None), "aptitude", "is not an id, a level"),
    (lambda d: shape(d, 0)["skills"][0].update(bandIds=[1]), "aptitude", "is not an id, a level"),
    (lambda d: shape(d, 0).update(skills=[]), "aptitude", "shape 0: no skills"),
    (lambda d: d["songs"][0]["charts"][0]["deck"].pop("gekisouAptitude"), "aptitude", "no deck.gekisouAptitude"),
    (lambda d: d["songs"][0]["charts"][0]["deck"].update(gekisouAptitude=None), "aptitude",
     "deck.gekisouAptitude is null, but the chart is playable"),
    (unplayable, "aptitude", "a Gekisou skill aptitude on a chart unplayable with Gekisou on"),
    (lambda d: apt(d)["factors"].pop(), "aptitude", "0 factors for 1 ranges"),
    (lambda d: apt(d)["factors"][0].update(justNotes=3), "aptitude", "Just or Perfect notes in a range without"),
    (lambda d: apt(d)["factors"][0].update(tailNotes=-1), "aptitude", "tailNotes missing or not counts"),
    (lambda d: apt(d)["factors"][0].update(lotteries=[1, 0]), "aptitude", "in a range without the luck mission"),
    (lambda d: apt(d, 0, 1)["factors"][0].update(lotteries=[3.0, 0]), "aptitude", "deck.seeds' lotResults give 4.0"),
    (lambda d: apt(d)["variants"].pop(0), "aptitude", "variants lack [(0, None)]"),
    (lambda d: apt(d)["variants"].reverse(), "aptitude", "not in shape order, true first"),
    (lambda d: apt(d, 0, 1)["variants"].pop(1), "aptitude", "variants lack [(1, False)]"),
    (lambda d: var(d).update(shape=9), "aptitude", "variants of shapes ['9'] not in deck.gekisouAptitude.shapes"),
    (lambda d: var(d).update(shape=2), "aptitude", "variants of shapes [2] of a mission the chart does not play"),
    (lambda d: var(d, 0, 0, 1).update(bandMatch=None), "aptitude", "bandMatch None for a shape with a band"),
    (lambda d: var(d).update(bandMatch=True), "aptitude", "bandMatch True for a shape without a band"),
    (lambda d: var(d).pop("tail"), "aptitude", "shape 0: no tail"),
    (lambda d: var(d).update(score=[1500]), "aptitude", "score not [mean, se]"),
    (lambda d: var(d).update(score=[1500, -1]), "aptitude", "score not [mean, se]"),
    (lambda d: var(d).update(converted=[float("nan"), 0]), "aptitude", "converted not [mean, se]"),
    (lambda d: var(d)["ranges"][0].update(luckPoints=None), "aptitude", "ranges[0].luckPoints not [mean, se]"),
    (lambda d: var(d).update(score=[1500, 1]), "aptitude", "deterministic, but a standard error is not 0: score"),
    (lambda d: var(d).update(seeds=2), "aptitude", "deterministic, but 2 seeds"),
    (lambda d: var(d, 0, 0, 1).update(seeds=33), "aptitude", "33 seeds (seTargetMet True), not a batch"),
    (lambda d: var(d, 0, 0, 1).update(seTargetMet=False), "aptitude", "32 seeds (seTargetMet False), not a batch"),
    (lambda d: var(d, 0, 0, 1).update(crossSeeds=64), "aptitude", "crossSeeds 64, expected min(32, 64)"),
    (lambda d: var(d)["ranges"].append(dict(var(d)["ranges"][0])), "aptitude", "2 range results for 1 ranges"),
    (lambda d: var(d).update(tail=[451, 0]), "aptitude", "tail 451 is not score 1500 less the ranges' rangeScore"),
    (lambda d: var(d)["weights"].append([0, 0]), "aptitude", "weights are not one [mean, se] per position"),
    (lambda d: seed(d).update(rangeWeights=None, rankCheck=None), "aptitude",
     "rangeWeights, but deck.seeds[0].rangeWeights is null"),
    (lambda d: var(d).update(rangeWeights=[[[0, 0]]]), "aptitude", "rangeWeights are not [position][range]"),
    (lambda d: (nonlinear(d), var(d)["check"].update(ranks=[2])), "aptitude", "ranks are not linear (rank 1 alone)"),
    (lambda d: var(d)["check"].update(seed=5), "aptitude", "the check's seed 5 is not deck.seeds[0]'s 0"),
    (lambda d: var(d, 0, 0, 1)["check"].update(seed=22), "aptitude", "the check's seed 22 is not deck.seeds[0]'s 11"),
    (lambda d: var(d)["check"].update(ranks=[6]), "aptitude", "the check's ranks [6] are not one rank per range"),
    (lambda d: var(d)["check"].update(deck=[[1, 7000], None]), "aptitude", "not a [plain kind, value] or null"),
    (lambda d: var(d)["check"].update(exact=99999999), "aptitude", "the check is not within its bound"),
    (lambda d: var(d)["check"].pop("bound"), "aptitude", "the check has no seed, ranks"),
    (lambda d: var(d).update(tailPerfect=[449, 0]), "aptitude", "tailPerfect is not scorePerfect"),
    (lambda d: var(d).update(converted=[0.5, 0]), "aptitude", "a point delta is not an integer"),
    # the deck statistics
    (lambda d: d.update(deck=None), "deck", "deck is null"),
    (lambda d: d["songs"][0]["charts"][0].update(deck=None), "deck", "no deck statistics"),
    (lambda d: seed(d)["check"].update(exact=99999), "deck", "check deck is not within"),
    (lambda d: seed(d).update(weights=[[0.5]]), "deck", "weights are not"),
    (lambda d: d["songs"][0]["charts"][0]["deck"].update(seeds=[]), "deck", "no seeds"),
    (unplayable, "deck", "unplayable, but has Gekisou on seeds"),
    (lambda d: d["songs"][0]["charts"][0]["deck"].update(events=[[0, 1000]]), "deck", "1 skill events"),
    (lambda d: seed(d).update(seed=5), "deck", "seeds [5] without a luck range, expected the one seed 0"),
    (lambda d: d["songs"][0]["charts"][1]["deck"]["seeds"].pop(), "deck", "1 seeds on a luck chart"),
    (lambda d: d["songs"][0]["charts"][1]["deck"]["seeds"][1].update(seed=11), "deck", "2 seeds on a luck chart"),
    (lambda d: d["songs"][1]["charts"][0]["deck"]["seeds"][1].update(seed=33), "deck",
     "its 2 seeds are not the 2 of the first luck chart"),
    (lambda d: seed(d)["ranges"][0].update(rankBonus=9999), "deck", "rankBonus 9999 is not trunc(4000 * 250 / 100)"),
    (lambda d: seed(d)["ranges"][0].pop("luckPoints"), "deck", "seed 0 range 0: luckPoints missing"),
    (lambda d: seed(d, 0, 1)["ranges"][0].update(luckPoints=2.5), "deck", "seed 11 range 0: luckPoints not an int"),
    # numbers
    (lambda d: seed(d)["weights"][0].__setitem__(1, float("nan")), "finite", "weights[0][1]: not finite"),
    (lambda d: d["songs"][1]["charts"][0]["bpm"].update(main=float("inf")), "finite", "bpm.main: not finite"),
    # references and texts
    (lambda d: d["songs"][1]["bandIds"].append(9), "references", "band 9 not in"),
    (lambda d: d["songs"][1]["vocalCharacterIds"].append(7), "references", "character 7 not in"),
    (lambda d: d["songs"][0].update(title=None), "references", "title: no text"),
    (lambda d: d["songs"][0].update(title={lang: "" for lang in LANGS}), "references", "empty in every language"),
    (lambda d: d["songs"][0]["composer"].pop("ko"), "references", "composer: not a text"),
    (lambda d: d["songs"][0].update(jacket=None), "references", "no jacket"),
    (lambda d: d["songs"].reverse(), "references", "not sorted"),
    (lambda d: d["songs"][1]["charts"][0].update(scoreId=10), "references", "occurs twice"),
    (lambda d: d["songs"][0]["charts"].reverse(), "references", "charts ['expert', 'easy']"),
    (lambda d: d["songs"][0].update(scoreRanks=[]), "references", "score ranks"),
    (lambda d: d["songs"][0].update(bandIds=[]), "references", "neither a band nor a band name"),
    # BGM
    (lambda d: d["songs"][0]["bgm"].update(length=None), "bgm", "no BGM length"),
    (lambda d: d["songs"][0]["bgm"]["length"].update(durationMs=50000, samples=48000 * 50), "bgm",
     "ends before the last note"),
    (lambda d: d["songs"][0]["bgm"]["length"].update(durationMs=90500), "bgm", "is not samples"),
    (lambda d: d["songs"][0]["bgm"]["length"].update(lengthMs=95000), "bgm", "cue length"),
    (lambda d: d["songs"][0]["bgm"]["length"].update(durationMs=1200000, samples=48000 * 1200), "bgm",
     "bounds"),
    # provenance
    (lambda d: d.update(format="nnnotes.music-data/2"), "provenance", "format"),
    (lambda d: d["provenance"].update(region="en"), "provenance", "region 'en'"),
    (lambda d: d["provenance"]["master"].update(version="other"), "provenance", "the snapshot's 'v-test'"),
    (lambda d: d["provenance"]["master"].update(source="embedded"), "provenance", "master.source"),
    (lambda d: d["provenance"]["master"]["tables"]["MasterText"].update(sha256="00" * 32), "provenance",
     "MasterText.sha256 is not"),
    (lambda d: d["provenance"]["master"]["tables"].pop("MasterBand"), "provenance", "lacks MasterBand"),
    (lambda d: d["provenance"]["deck"].update(commit="ee" * 20), "provenance", "rust/Cargo.lock pins"),
    (lambda d: d["provenance"]["exporter"].update(version="0.0.9"), "provenance", "installed nnnotes"),
    (lambda d: d["provenance"]["client"].update(versionName=None), "provenance", "no APK version"),
])
def test_a_gate_fails(tmp_path, change, name, match):
    doc = sample()
    change(doc)
    r = run(tmp_path, doc)
    g = gate(r, name)
    assert not r["passed"] and not g["passed"] and any(match in f for f in g["failures"]), g


def test_a_table_read_is_not_the_listed_one(tmp_path):
    raw, ctx = context(tmp_path, sample())
    (ctx.master / "MasterText.json").write_text('{"_allData": [{}]}', encoding="utf-8")
    g = gate(gates(raw, ctx), "provenance")
    assert not g["passed"] and g["failures"] == ["MasterText.json read is not the one index.json lists"]


def test_a_jacket_file_is_missing(tmp_path):
    raw, ctx = context(tmp_path, sample())
    (ctx.jackets / "jkt_100002.webp").unlink()
    g = gate(gates(raw, ctx), "references")
    assert not g["passed"] and g["failures"] == ["song 100002: no jacket file jackets/jkt_100002.webp"]


def test_warnings_do_not_fail(tmp_path):
    doc = sample()
    nonlinear(doc)                                                        # overlapping ranges
    seed(doc, 0, 1)["rangeWeights"][0] = None                             # a kind reading the confirmed rank
    doc["songs"][1]["charts"][0]["deck"]["offSeeds"][0]["weights"][0] = None
    doc["songs"][0]["master"]["MasterLiveMusic"]["_rate"] = float("inf")  # 1e999 in master data as served
    doc["songs"][1]["title"]["zh-Hant"] = ""
    r = run(tmp_path, doc, page=page_path())
    assert r["passed"], failures(r)
    assert gate(r, "scenarios")["warningCount"] == 3 and gate(r, "finite")["warningCount"] == 1
    assert gate(r, "references")["warnings"] == ["songs without a zh-Hant title: 100002"]


def test_aptitude_warnings(tmp_path):
    doc = sample()
    var(doc, 0, 0, 1).update(seeds=1024, seTargetMet=False, crossSeeds=64)   # the last batch, not the target
    r = run(tmp_path, doc)
    assert r["passed"], failures(r)
    assert gate(r, "aptitude")["warnings"] == [
        "1 variants missed the seed rule's standard error target: 30 shape 1"]
    assert gate(r, "aptitude")["note"] == ("4 shapes; 3 charts with an aptitude, 0 null; 8 variants, "
                                           "2 deterministic; plain kind 0")


def test_no_gekisou_skill_shapes(tmp_path):
    doc = sample()
    doc["deck"]["gekisouAptitude"]["shapes"] = []
    kept = apt(doc)
    for _, c in music_data.charts_of(doc):
        c["deck"]["gekisouAptitude"] = None
    r = run(tmp_path, doc, page=page_path())
    assert r["passed"], failures(r)
    doc["songs"][0]["charts"][0]["deck"]["gekisouAptitude"] = kept
    g = gate(run(tmp_path, doc), "aptitude")
    assert g["failures"] == ["chart 10 (100001 easy): a Gekisou skill aptitude on a chart without a Gekisou skill "
                             "shape"]


def test_an_unplayable_chart_has_no_aptitude(tmp_path):
    doc = sample()
    doc["songs"][1]["charts"][0]["deck"].update(unplayable="more than three fevers", seeds=[], gekisouAptitude=None)
    r = run(tmp_path, doc, page=page_path())
    assert r["passed"], failures(r)
    assert gate(r, "aptitude")["note"].startswith("4 shapes; 2 charts with an aptitude, 1 null")


def test_gzip_caps(tmp_path, monkeypatch):
    raw, ctx = context(tmp_path, sample())
    g = gate(gates(raw, ctx, only=("gzip",)), "gzip")
    assert g["passed"] and g["note"].startswith(f"{len(music_data.gzip.compress(raw, 6))} bytes gzipped")
    monkeypatch.setattr(music_data, "APTITUDE_GZIP_MAX", 100)
    g = gate(gates(raw, ctx, only=("gzip",)), "gzip")
    assert not g["passed"] and g["failures"][0].startswith("the Gekisou skill aptitude: ")
    monkeypatch.setattr(music_data, "FILE_GZIP_MAX", 100)
    assert len(gate(gates(raw, ctx, only=("gzip",)), "gzip")["failures"]) == 2


def test_against_the_published_file(tmp_path):
    doc = sample()
    raw, ctx = context(tmp_path, doc)
    more = copy.deepcopy(doc)
    more["songs"].append(song(100003, [chart("expert", 50)]))
    ctx.published = json.dumps(more).encode()
    r = gates(raw, ctx)
    assert not gate(r, "counts")["passed"] and gate(r, "counts")["failures"] == [
        "2 songs, the published file has 3", "3 charts, the published file has 4"]
    assert gate(r, "counts")["warnings"] == ["songs no longer in the file: 100003", "charts no longer in the file: 50"]
    ctx.published = raw + b" " * (len(raw) * 2)                           # the same songs in a file three times as big
    r = gates(raw, ctx)
    assert gate(r, "counts")["passed"] and not gate(r, "size")["passed"]
    ctx.published = raw[:len(raw) // 3]                                   # a third: not JSON, and twice exceeded
    r = gates(raw, ctx)
    assert gate(r, "counts")["warnings"] == ["the published music-data.json is not JSON: skipped"]
    assert not gate(r, "size")["passed"]
    ctx.published = raw
    assert gates(raw, ctx)["passed"]


def test_not_json(tmp_path):
    r = gates(b"{", Context())
    assert not r["passed"] and r["gates"][0]["gate"] == "json"


def test_report_markdown(tmp_path):
    doc = sample()
    doc["songs"][0]["charts"][0]["deck"].pop("offSeeds")
    text_ = music_data.report_markdown(run(tmp_path, doc))
    assert "| scenarios | **failed** (1) |" in text_
    assert "- scenarios failure: chart 10 (100001 easy): offSeeds missing, expected exactly one" in text_


def test_deck_commit_from_cargo_lock(tmp_path):
    lock = tmp_path / "rust" / "Cargo.lock"
    lock.parent.mkdir()
    lock.write_text('[[package]]\nname = "pyo3"\nversion = "0.29.0"\n\n[[package]]\nname = "ournotes-deck"\n'
                    'version = "0.0.1"\nsource = "git+https://github.com/empty-sekai/ournotes-deck?rev=' + "a" * 40
                    + "#" + "b" * 40 + '"\n', encoding="utf-8")
    assert music_data.deck_commit(tmp_path) == "b" * 40
    with pytest.raises(SystemExit, match="sync the fork"):
        music_data.deck_commit(tmp_path / "nowhere")


# ---------------------------------------------------------------- real files (when named)
def real(name):
    p = os.environ.get(name)
    if not p or not Path(p).is_file():
        pytest.skip(f"set {name} to a music-data.json")
    return Path(p).read_bytes()


def luck_points(raw: bytes) -> bool:
    """Whether a file's deck.seeds ranges have luckPoints (a file made before them has none)."""
    return any("luckPoints" in r for _, c in music_data.charts_of(json.loads(raw))
               for s in (c.get("deck") or {}).get("seeds") or [] for r in s.get("ranges") or [])


def before_luck_points(r: dict) -> bool:
    """The deck gate stopped only on the ranges' missing luckPoints."""
    g = gate(r, "deck")
    return not g["passed"] and all(f.endswith("luckPoints missing") for f in g["failures"])


def test_a_real_file_without_the_scenario_fields():
    raw = real("MUSIC_DATA_OLD_SAMPLE")
    r = gates(raw, Context(language="zh-Hant"), only=CONTENT)
    assert [n for n, _ in failures(r)] == (["scenarios"] if luck_points(raw) else ["deck", "scenarios"])
    assert luck_points(raw) or before_luck_points(r)
    g = gate(r, "scenarios")
    charts = sum(len(s["charts"]) for s in json.loads(raw)["songs"])
    assert g["failureCount"] >= charts and all("offSeeds missing" in f or "rankBonusPercents missing" in f
                                               or "no scorePerfect" in f for f in g["failures"])


def test_a_real_file_with_the_scenario_fields(tmp_path):
    raw = real("MUSIC_DATA_SAMPLE")
    (tmp_path / "music-data.json").write_bytes(raw)
    ctx = Context(language="zh-Hant", page=page_path(), file=tmp_path / "music-data.json", published=raw)
    r = gates(raw, ctx, only=CONTENT + ("aptitude", "counts", "size", "gzip", "page"))
    new = [n for n, ok in (("deck", luck_points(raw)), ("aptitude", "gekisouAptitude" in json.loads(raw)["deck"]))
           if not ok]                                                  # the gates of fields made after the file
    assert [n for n, _ in failures(r)] == new, failures(r)
    assert "deck" not in new or before_luck_points(r)
    g = gate(r, "aptitude")
    assert "aptitude" not in new or all(f.endswith(("gekisouAptitude missing", "gekisouAptitude: no text",
                                                    ": no deck.gekisouAptitude")) for f in g["failures"]), g
    assert gate(r, "scenarios")["warningCount"] == 0
    print(f"aptitude: {g['note']}; warnings: {g['warnings']}; gzip: {gate(r, 'gzip')['note']}")


def test_real_aptitude_deck_sample():
    """Optional real chart-stats output, wrapped without modifying its statistics or touching the source file."""
    stats = json.loads(real("MUSIC_DATA_APTITUDE_DECK_SAMPLE"))
    doc = {"deck": {k: stats[k] for k in ("model", "kinds", "gekisouAptitude")},
           "songs": [{"id": c["musicId"], "charts": [{"scoreId": c["scoreId"], "difficulty": c["difficulty"],
                       "skillEventsMs": [e[1] for e in c["events"]], "deck": c}]} for c in stats["charts"]]}
    report = gates(json.dumps(doc).encode(), Context(), only=("deck", "aptitude", "gzip"))
    assert report["passed"], failures(report)


# ---------------------------------------------------------------- publish (a stand-in bucket)
class FakeS3:
    def __init__(self, corrupt=None):
        self.store, self.log, self.corrupt = {}, [], corrupt

    def upload_file(self, src, bucket, key, ExtraArgs):
        self.store[key] = b"not it" if key == self.corrupt else Path(src).read_bytes()
        self.log.append((key, ExtraArgs["CacheControl"], ExtraArgs["ContentType"]))

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.store[Key])}


class FakeBucket:
    name, prefix, writable = "moenotes", "music-data/", True

    def __init__(self, s3):
        self.s3 = s3

    def keys(self, sub=""):
        return {k[len(self.prefix):]: len(v) for k, v in self.s3.store.items() if k.startswith(self.prefix + sub)}


def published_out(tmp_path, monkeypatch, s3):
    out = tmp_path / "out"
    out.mkdir()
    raw, ctx = context(out, sample())
    report = gates(raw, ctx)
    (out / "check.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "build.json").write_text(json.dumps({"sha256": report["sha256"],
                                                "archive": f"archive/v-test/{report['sha256']}.json"}),
                                    encoding="utf-8")
    monkeypatch.setattr(music_data, "bucket", lambda: FakeBucket(s3))
    monkeypatch.setattr(music_data.time, "sleep", lambda s: None)
    monkeypatch.setenv("STORY_S3_ENDPOINT", "https://storage.example")
    monkeypatch.setenv("STORY_S3_BUCKET", "moenotes")
    monkeypatch.delenv("FORCE", raising=False)
    monkeypatch.setenv("MUSIC_DATA_PUBLISH", "true")
    return out, report


def test_publish_order_and_read_back(tmp_path, monkeypatch):
    s3 = FakeS3()
    out, report = published_out(tmp_path, monkeypatch, s3)
    music_data.cmd_publish(str(out))
    keys = [k for k, _, _ in s3.log]
    archive = f"music-data/archive/v-test/{report['sha256']}.json"
    assert sorted(keys[:2]) == ["music-data/jackets/jkt_100001.webp", "music-data/jackets/jkt_100002.webp"]
    assert keys[2:] == [archive, "music-data/music-data.json", "music-data/build.json"]
    caches = {k: c for k, c, _ in s3.log}
    assert caches[archive].endswith("immutable") and caches["music-data/music-data.json"] == "no-cache"
    assert s3.store["music-data/music-data.json"] == (out / "music-data.json").read_bytes()
    s3.log.clear()
    music_data.cmd_publish(str(out))                          # again: the jackets and the archive copy are there
    assert [k for k, _, _ in s3.log] == ["music-data/music-data.json", "music-data/build.json"]
    monkeypatch.setenv("FORCE", "true")
    s3.log.clear()
    music_data.cmd_publish(str(out), dry_run=True)
    assert s3.log == []


def test_publish_stops_before_the_marker_when_the_file_does_not_read_back(tmp_path, monkeypatch):
    s3 = FakeS3(corrupt="music-data/music-data.json")
    out, _ = published_out(tmp_path, monkeypatch, s3)

    def offline(*a, **kw):
        raise music_data.urllib.error.URLError("offline")
    monkeypatch.setattr(music_data, "get", offline)
    with pytest.raises(SystemExit, match="music-data.json: the bucket does not serve what was uploaded"):
        music_data.cmd_publish(str(out))
    assert "music-data/build.json" not in s3.store


@pytest.mark.parametrize("switch", [None, "", "false", "True", "1"])
def test_publishing_is_off_unless_the_switch_is_true(tmp_path, monkeypatch, switch):
    s3 = FakeS3()
    out, _ = published_out(tmp_path, monkeypatch, s3)
    if switch is None:
        monkeypatch.delenv("MUSIC_DATA_PUBLISH")
    else:
        monkeypatch.setenv("MUSIC_DATA_PUBLISH", switch)
    music_data.cmd_publish(str(out))                          # a dry run, whatever the command line says
    assert s3.log == [] and s3.store == {}


def test_publish_needs_passed_gates(tmp_path, monkeypatch):
    s3 = FakeS3()
    out, report = published_out(tmp_path, monkeypatch, s3)
    (out / "check.json").write_text(json.dumps(dict(report, passed=False)), encoding="utf-8")
    with pytest.raises(SystemExit, match="has not passed the gates"):
        music_data.cmd_publish(str(out))
    (out / "check.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "music-data.json").write_bytes(b"{}")                         # not the file that was checked
    with pytest.raises(SystemExit, match="has not passed the gates"):
        music_data.cmd_publish(str(out))
    assert s3.store == {}
