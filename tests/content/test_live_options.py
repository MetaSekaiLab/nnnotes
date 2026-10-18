"""Live option variants (liveoptions) and the exporters' option output, on synthetic master data and charts."""

import json

import pytest

from nnnotes.content import liveaudio, liveoptions, score
from nnnotes.content.liveoptions import LiveOptions, OptionSpecError, parse_specs, resolve
from tests.helpers.live import CHART

SWITCHES = {
    4,
    5,
    7,
    100,
    101,
    102,
    105,
    108,
    109,
    111,
    112,
    113,
    114,
    115,
    117,
    205,
    206,
    207,
    208,
    304,
    309,
    310,
    403,
    404,
    405,
    414,
    415,
    416,
    417,
    421,
    *range(460, 471),
    513,
    514,
    515,
    524,
    525,
    526,
    527,
    601,
    602,
    603,
    604,
}


def _value(item: int) -> str:
    if item in SWITCHES:
        return "FALSE"
    if item in (1,):
        return "6.50"
    if item in (2, 3, 500, 501):
        return "0.00"
    if 410 <= item <= 413 or item in (431, 433, 435, 437, 439, 441, 451, 453, 455, 457, 459):
        return "90"
    return "1"


def write_master(d, individual: bool = False, trace_mute: bool = False):
    """A synthetic master directory with the tables the live option code reads."""
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for preset in (1, 2):
        for item in liveoptions.OPTION_ITEM_TYPES:
            v = _value(item)
            if preset == 1 and item == 421 and individual:
                v = "TRUE"
            if preset == 1 and individual and item in (432, 438):
                v = "3"
            if preset == 1 and item == 470 and trace_mute:
                v = "TRUE"
            rows.append(
                {
                    "_id": len(rows) + 1,
                    "_presetId": preset,
                    "_optionItemType": item,
                    "_valueString": v,
                    "_addedOptionVersion": 1,
                }
            )
    ranges = [
        {"_id": i + 1, "_optionItemType": t, "_minValue": lo, "_maxValue": hi}
        for i, (t, lo, hi) in enumerate(
            [
                (1, 100, 1200),
                (2, -300, 300),
                (3, -300, 300),
                (106, 10, 100),
                (410, 0, 100),
                (431, 0, 100),
                (433, 0, 100),
                (600, 0, 2),
            ]
        )
    ]
    se = []
    for g in (1, 2, 3):
        for t in range(1, 15):
            se.append({"_id": len(se) + 1, "_groupID": g, "_liveNoteSeType": t, "_seId": g * 1000 + t})
    tables = {
        "MasterOptionDefault": rows,
        "MasterOptionRange": ranges,
        "MasterLiveNoteSkin": [{"_id": i, "_assetName": f"skin{i:03d}"} for i in (1, 2, 3)],
        "MasterLiveNoteEffectSkin": [{"_id": 1, "_assetName": "fxA"}, {"_id": 2, "_assetName": "fxASimple"}],
        "MasterLiveQualitySettings": [{"_id": 1, "_quality": 2}, {"_id": 2, "_quality": 1}, {"_id": 3, "_quality": 0}],
        "MasterLiveNoteSe": se,
        "MasterLiveSettings": [
            {"_key": k, "_value": v}
            for k, v in [
                ("note_speed_min", "1"),
                ("note_speed_max", "12"),
                ("note_speed_view_min", "4"),
                ("note_speed_view_max", "0.35"),
            ]
        ],
        "MasterLiveJudgementSprite": [{"_noteSimulateJudgement": 1, "_spriteName": "perfect"}],
    }
    for name, t in tables.items():
        (d / f"{name}.json").write_text(json.dumps({"_allData": t}), encoding="utf-8")
    return d


# ---------------------------------------------------------------- specs and resolution
def test_parse_specs():
    assert parse_specs(None) == {}
    assert parse_specs(["MirrorChart", "NoteDesignId", "NoteEffectId=2", "NoteEffectId=1, 2", "defaults"]) == {
        "MirrorChart": True,
        "NoteDesignId": "all",
        "NoteEffectId": [1, 2],
        "defaults": True,
    }
    assert parse_specs(["LiveQuality=2", "LiveQuality"]) == {"LiveQuality": "all"}
    assert parse_specs(["LiveQuality", "LiveQuality=2"]) == {"LiveQuality": "all"}
    assert parse_specs(["all"]) == {"all": True} and parse_specs(["MeasureLineDisplay"]) == {"MeasureLineDisplay": True}
    for bad in (
        ["MirrorChart=1"],
        ["MeasureLineDisplay=1"],
        ["NoteSpeed"],
        ["NoteDesignId=a"],
        ["NoteDesignId="],
        ["all=1"],
        ["defaults=x"],
    ):
        with pytest.raises(OptionSpecError):
            parse_specs(bad)


def test_resolve_empty_is_the_default_output(tmp_path):
    m = write_master(tmp_path / "m")
    assert resolve({}, m) == LiveOptions() and not resolve(None, None)
    assert not LiveOptions() and LiveOptions().read_variants() == [] and LiveOptions().manifest_options() is None
    with pytest.raises(OptionSpecError):
        resolve({"MirrorChart": True}, None)


def test_resolve_values_and_defaults(tmp_path):
    m = write_master(tmp_path / "m")
    o = resolve(parse_specs(["defaults"]), m)
    assert o.tables and not (o.mirror or o.bar_lines or o.designs or o.effects or o.qualities or o.se_patterns)
    assert o.read_variants() == [] and o.manifest_options() is None
    o = resolve(parse_specs(["MeasureLineDisplay"]), m)
    assert o and o.tables and o.bar_lines and not o.mirror and o.values("MeasureLineDisplay") == [False, True]
    assert o.values("MirrorChart") == [False] and o.read_variants() == [{"MeasureLineDisplay": True}]
    assert o.manifest_options() is None and o.effect_sets({1: "fxA"}) == []
    o = resolve(parse_specs(["NoteDesignId=3", "NoteSePatternId=1"]), m)
    assert o.tables and o.designs == (1, 3) and o.se_patterns == ()  # the default alone offers nothing
    o = resolve(parse_specs(["all"]), m)
    assert (o.mirror, o.bar_lines, o.designs, o.effects, o.qualities, o.se_patterns) == (
        True,
        True,
        (1, 2, 3),
        (1, 2),
        (0, 1, 2),
        (1, 2, 3),
    )
    assert o.values("MirrorChart") == [False, True] and o.values("LiveQuality") == [0, 1, 2]
    assert o.manifest_options() == {"LiveQuality": [0, 1, 2]}
    with pytest.raises(OptionSpecError, match="NoteDesignId: 4 not in MasterLiveNoteSkin"):
        resolve(parse_specs(["NoteDesignId=4"]), m)


def test_read_variants(tmp_path):
    m = write_master(tmp_path / "m")
    o = resolve(parse_specs(["all"]), m)
    v = o.read_variants()
    # every combination of the picture options (2 x 3 x 2 x 3) but the default one, the bar lines, each other sound
    # set
    assert len(v) == 2 * 3 * 2 * 3 - 1 + 1 + 2
    assert v[0] == {"LiveQuality": 0} and v[-3:] == [
        {"MeasureLineDisplay": True},
        {"NoteSePatternId": 2},
        {"NoteSePatternId": 3},
    ]
    assert all("MeasureLineDisplay" not in s or len(s) == 1 for s in v)
    assert {"MirrorChart": True, "NoteDesignId": 3, "NoteEffectId": 2, "LiveQuality": 2} in v
    assert len({json.dumps(s, sort_keys=True) for s in v}) == len(v) and {} not in v
    assert all("NoteSePatternId" not in s or len(s) == 1 for s in v)
    o = resolve(parse_specs(["MirrorChart", "NoteSePatternId=2"]), m)
    assert o.read_variants() == [{"MirrorChart": True}, {"NoteSePatternId": 2}] and not o.bar_lines
    o = resolve(parse_specs(["NoteSePatternId=2", "MeasureLineDisplay", "MirrorChart"]), m)
    assert o.read_variants() == [{"MirrorChart": True}, {"MeasureLineDisplay": True}, {"NoteSePatternId": 2}]


def test_effect_sets(tmp_path):
    m = write_master(tmp_path / "m")
    names = {1: "fxA", 2: "fxASimple"}
    assert resolve(parse_specs(["NoteEffectId"]), m).effect_sets(names) == [("fxASimple", False)]
    assert resolve(parse_specs(["LiveQuality=2"]), m).effect_sets(names) == [("fxALight", True)]
    assert resolve(parse_specs(["LiveQuality=0"]), m).effect_sets(names) == []
    assert resolve(parse_specs(["NoteEffectId", "LiveQuality"]), m).effect_sets(names) == [
        ("fxALight", True),
        ("fxASimple", False),
        ("fxASimpleLight", True),
    ]
    assert LiveOptions().effect_sets(names) == []


# ---------------------------------------------------------------- note assets settings


# ---------------------------------------------------------------- bar line view


# ---------------------------------------------------------------- note sounds
def test_live_se_sounds_cover_every_result():
    # the cheers and the finish direction of every result (all perfect, full combo, assist full combo, clear)
    names = [liveaudio.LIVE_SE_TYPES[t] for t in liveaudio.LIVE_SE_SOUNDS]
    assert names == [
        "StartCheers",
        "FinishCheers",
        "LiveClearDirection",
        "FullComboDirection",
        "AssistFullComboDirection",
        "AllPerfectDirection",
    ]
    assert list(liveaudio.LIVE_SE_SOUNDS) == sorted(liveaudio.LIVE_SE_SOUNDS)


def note_se_tables(m) -> dict:
    return {t: score.master_table(m, t) for t in ("MasterOptionDefault", "MasterLiveNoteSe")}


def test_note_se_settings_default_and_groups(tmp_path):
    m = note_se_tables(write_master(tmp_path / "m"))
    d = liveaudio.note_se_settings(m)
    assert d["types"] == {str(t): 1000 + t for t in range(1, 15)} and d["patternId"] == 1
    assert not d["useIndividualNoteSe"] and "groups" not in d
    assert d["volumes"]["4"] == 0.9 and d["gekisouTraceVolume"] == 0.9
    g = liveaudio.note_se_settings(m, groups=(1, 3))
    assert {k: v for k, v in g.items() if k != "groups"} == d
    assert list(g["groups"]) == ["1", "3"] and g["groups"]["3"]["8"] == 3008
    with pytest.raises(KeyError):
        liveaudio.note_se_settings(m, groups=(9,))


def test_note_se_individual_and_trace_mute(tmp_path):
    m = note_se_tables(write_master(tmp_path / "m", individual=True, trace_mute=True))
    d = liveaudio.note_se_settings(m)
    assert d["useIndividualNoteSe"]
    # TapSeId (432) 3 -> types 2, 3, 4, 8; SlideSeId (438) 3 -> 7, 10; the others set 1
    assert {t: d["types"][str(t)] // 1000 for t in range(1, 15)} == {
        1: 1,
        2: 3,
        3: 3,
        4: 3,
        5: 1,
        6: 1,
        7: 3,
        8: 3,
        9: 1,
        10: 3,
        11: 1,
        12: 1,
        13: 1,
        14: 1,
    }
    assert d["gekisouTraceVolume"] == 0.0  # GekisouTraceSeMute (470)


# ---------------------------------------------------------------- mirrored score
def test_mirrored_conversion():
    a, b = score.convert(CHART), score.convert(CHART, mirror=True)
    assert (a["mirror"], b["mirror"]) == (False, True)
    pairs = [(x["opName"], y["opName"]) for x, y in zip(a["notes"], b["notes"])]
    assert all(p == q for p, q in pairs) and len(a["notes"]) == len(b["notes"]) == 5
    for x, y in zip(a["notes"], b["notes"]):
        if "laneIndexFloat" in x:  # ApplyMirror: 24 - lane - width
            assert y["laneIndexFloat"] == 24 - x["laneIndexFloat"] - x["widthFloat"]
    assert [n["direction"] for n in b["notes"]][:2] == ["Normal", "Right"]  # ApplyMirrorDirection
    assert b["notes"][4]["laneIndex"] == 16  # 16.5 rounds to even
