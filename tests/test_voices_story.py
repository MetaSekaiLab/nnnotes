"""The Story source of the voices index on synthetic episodes (no game data): episode commands, speaker and line
texts, the sounds and cue sheets of the shards, the statuses of episodes and rows, the inferred characters, the
stage reading the unity.export artifacts, and the command."""
import argparse
import copy
import json
import random

import pytest

from nnnotes import advcommand, contract, languages, storyvoices, voices
from nnnotes.contract import Task
from nnnotes.stages import Pending, describe, execute, registry
from nnnotes.store import Store
from nnnotes.views import AddressIndex, Obj, ViewError
from test_views import addresses_doc, write_master
from test_voices import (KEYS, SHEETS, TABLES, Cfg, Paths, audio, audio_task, command, planning, row, text)

RULES = voices.load_rules()
STORY_SHEET = "adv_voice_x_01"
SHEETS2 = {**SHEETS, STORY_SHEET: ({f"{STORY_SHEET}_00{n}": [n - 1] for n in range(1, 5)}, [0, 1, 2, 3],
                                   {n - 1: (f"{STORY_SHEET}_00{n}", 100 * n) for n in range(1, 5)})}


def lines(tid, base):
    """A text row of an episode's -Text shard in the five languages."""
    return {**text(tid, base), "_note": ""}


def cmd(index, command=2, target="", texts=(), line="", voices_=(), ignore=0):
    return {"Index": index, "Command": command, "TargetName": target, "TargetTextIDs": list(texts),
            "TargetTextColors": [], "AdvTextID": line, "VoiceIDs": list(voices_), "IgnoreData": ignore,
            "Parameter1": "", "MotionName": "m"}


def shard(rows):
    return json.dumps({"_header": [{"_name": "ID", "_type": "Int64"}], "_allData": rows},
                      ensure_ascii=False).encode("utf-8")


def sound_row(i, sheet, cue, cat=2):
    return {"_id": i, "_category": cat, "_soundCueSheetID": sheet, "_cueName": cue, "_note": ""}


# ---------------------------------------------------------------- synthetic episodes
EPISODES = {
    # several commands; two voice ids in one command; a cue the ACB lacks; a sound whose sheet has no row; a voice
    # id without a sound; a sheet without a catalog key; a row the player ignores; speakers with and without a match
    "adv_script_x_01": {
        "script": {"m_Name": "adv_script_x_01", "Collection": [
            cmd(0, command=25),
            cmd(3, target="alpha", texts=["adv_alpha"], line="x01_1", voices_=[5001]),
            cmd(4, target="alpha", texts=["adv_alpha"], line="x01_9"),
            cmd(5, target="gamma・Alpha", texts=["adv_gamma", "adv_alpha"], line="x01_2", voices_=[5002, 5003]),
            cmd(7, command=37, target="passerby", texts=["adv_passerby"], line="x01_3", voices_=[5004]),
            cmd(9, command=52, voices_=[5005]),
            cmd(11, target="gamma", texts=["adv_gamma"], line="x01_4", voices_=[5006]),
            cmd(12, target="alpha・passerby", texts=["adv_both"], line="x01_5", voices_=[5007], ignore=1)]},
        "text": [lines(t, t) for t in ("x01_1", "x01_2", "x01_3", "x01_4", "x01_5", "x01_9", "adv_alpha",
                                       "adv_gamma", "adv_passerby", "adv_both", "x01_unused")],
        "sound": [sound_row(5001, 700, f"{STORY_SHEET}_001"), sound_row(5002, 700, f"{STORY_SHEET}_002"),
                  sound_row(5003, 700, f"{STORY_SHEET}_009"), sound_row(5004, 99, "lost_01"),
                  sound_row(5006, 701, "adv_voice_x_gone_001"), sound_row(5007, 700, f"{STORY_SHEET}_003"),
                  sound_row(5008, 1100, "bgm_01", cat=0)],
        "sheets": [{"_id": 700, "_cueSheetName": STORY_SHEET, "_note": ""},
                   {"_id": 701, "_cueSheetName": "adv_voice_x_gone", "_note": ""},
                   {"_id": 1100, "_cueSheetName": "sound_bgm_x", "_note": ""}]},
    # no -Text key; the -SoundCueSheet key is there but not exported
    "adv_script_x_02": {
        "script": {"Collection": [cmd(1, target="gamma", texts=["adv_gamma"], line="x02_1", voices_=[5101])]},
        "sound": [sound_row(5101, 700, f"{STORY_SHEET}_004")]},
    # a home episode naming a cue a master row names too
    "adv_script_home_03": {
        "script": {"Collection": [cmd(2, target="alpha", texts=["adv_alpha"], line="h03_1", voices_=[5201])]},
        "text": [lines(t, t) for t in ("h03_1", "adv_alpha")],
        "sound": [sound_row(5201, 10, "Growth_Alpha_RankUp_01", cat=0)],
        "sheets": [{"_id": 10, "_cueSheetName": "VoiceSystem_01"}]},
    # an episode without voices
    "adv_script_x_08": {"script": {"Collection": [cmd(0, command=25), cmd(1, line="x08_1")]}, "text": [],
                        "sound": [], "sheets": []},
    # exported, but the MonoBehaviour has no command list
    "adv_script_x_07": {"script": {"m_Name": "adv_script_x_07"}, "text": [], "sound": [], "sheets": []},
}
# catalog keys that exist without being exported
NOT_EXPORTED = {"adv_script_x_06": ("script", "text", "sound", "sheets"), "adv_script_x_02": ("sheets",)}

STORY_TABLES = {
    "MasterAdv": [
        {"_id": 900001, "_advEpisodeAsset": "adv_script_x_01", "_titleTextId": "Title_900001", "_playbackMode": 0},
        {"_id": 900002, "_advEpisodeAsset": "adv_script_x_02", "_titleTextId": "Title_900002", "_playbackMode": 0},
        {"_id": 900003, "_advEpisodeAsset": "adv_script_home_03", "_titleTextId": "", "_playbackMode": 1},
        {"_id": 900004, "_advEpisodeAsset": "", "_titleTextId": ""},
        {"_id": 900005, "_advEpisodeAsset": "adv_script_gone", "_titleTextId": ""},
        {"_id": 900006, "_advEpisodeAsset": "adv_script_x_06", "_titleTextId": ""},
        {"_id": 900007, "_advEpisodeAsset": "adv_script_x_07", "_titleTextId": ""},
        {"_id": 900008, "_advEpisodeAsset": "adv_script_x_08", "_titleTextId": ""}],
    "MasterStoryEpisode": [{"_id": 101, "_chapterId": 1, "_episodeNumber": 1, "_characterId": 0,
                            "_isAnotherEpisode": False, "_isExtraEpisode": False, "_advId": 900001}],
    "MasterStoryChapter": [{"_id": 1, "_nameTextId": "Chapter_Name_1"}],
    "MasterStoryFriendshipEpisode": [{"_id": 1, "_characterFriendshipId": 12, "_episodeNumber": 2,
                                      "_advId": 900002}],
    "MasterCharacterFriendship": [{"_id": 12, "_masterCharacterIdA": 1, "_masterCharacterIdB": 2}],
    "MasterStoryHomeSpotTapTalkEpisode": [{"_id": 1000101, "_spotId": 10001, "_characterId": 1, "_advId": 900003}],
    "MasterStoryLiveResultEpisode": [{"_id": 1, "_characterIds": [1], "_advId": 900008}],
}


def tables():
    t = copy.deepcopy(TABLES)
    t.update(copy.deepcopy(STORY_TABLES))
    t["MasterText"] = t["MasterText"] + [text(x, x.lower()) for x in ("Title_900001", "Title_900002",
                                                                     "Chapter_Name_1")]
    return t


def part_bytes(asset: str, part: str) -> bytes:
    data = EPISODES[asset][part]
    return contract.encode(data) if part == "script" else shard(data)


def story_keys(asset):
    return voices.story_keys(RULES, asset)


def catalog_keys() -> list[str]:
    out = list(KEYS)
    for asset, parts in EPISODES.items():
        out += [story_keys(asset)[p] for p in parts]
    for asset, parts in NOT_EXPORTED.items():
        out += [story_keys(asset)[p] for p in parts]
    return sorted(set(out))


def story(parts=None) -> dict:
    """{asset: storyvoices.episode(...)} of the exported episodes."""
    out = {}
    for asset, have in EPISODES.items():
        got = {p: part_bytes(asset, p) for p in have if parts is None or p in parts}
        out[asset] = storyvoices.episode(got.get("script"), got.get("text"), got.get("sound"), got.get("sheets"))
    return out


def build(audio_=True, story_=True, tables_=None, keys=None):
    return voices.build(RULES, tables_ or tables(), AddressIndex({k: [] for k in keys or catalog_keys()}),
                        audio(sheets=SHEETS2) if audio_ else None, story=story() if story_ else None)


def story_rows(doc):
    return [r for r in doc["rows"] if r["source"]["name"] == "Story"]


# ---------------------------------------------------------------- the episodes as the index reads them
def test_episode_keeps_the_voice_commands_and_the_rows_they_name():
    e = story()["adv_script_x_01"]
    assert [c["Index"] for c in e["commands"]] == [3, 5, 7, 9, 11, 12]
    assert set(e["commands"][0]) == set(storyvoices.COMMAND_FIELDS)                 # other fields dropped
    assert sorted(e["text"]) == ["adv_alpha", "adv_both", "adv_gamma", "adv_passerby", "x01_1", "x01_2", "x01_3",
                                 "x01_4", "x01_5"]                                   # x01_9: a line without voice
    assert sorted(e["sound"]) == [5001, 5002, 5003, 5004, 5006, 5007]               # 5005 has no row, 5008 unused
    assert sorted(e["sheets"]) == [700, 701]
    from_dict = storyvoices.episode(EPISODES["adv_script_x_01"]["script"], *(part_bytes("adv_script_x_01", p)
                                                                           for p in ("text", "sound", "sheets")))
    assert from_dict == e                                   # the script as JSON bytes or as the loaded fields
    assert storyvoices.episode(None, shard([])) == {"commands": None, "text": {}, "sound": None, "sheets": None}
    assert storyvoices.episode({"m_Name": "x"}) == {"why": "the script has no Collection"}
    assert storyvoices.episode(b"{not json")["why"].startswith("the script is not JSON")
    assert storyvoices.episode({"Collection": []}, b"[1, 2]")["why"] == "not a shard: no _allData list"
    with pytest.raises(storyvoices.StoryError):
        storyvoices.shard_rows(b"\xff\xfe")


# ---------------------------------------------------------------- rows
def test_story_rows_facts_texts_and_statuses():
    doc = build()
    rows = story_rows(doc)
    assert [r["id"] for r in rows] == [
        "MasterAdv:900001:3", "MasterAdv:900001:5:1", "MasterAdv:900001:5:2", "MasterAdv:900001:7",
        "MasterAdv:900001:9", "MasterAdv:900001:11", "MasterAdv:900001:12", "MasterAdv:900002:1",
        "MasterAdv:900003:2"]
    r = row(doc, "MasterAdv:900001:3")
    assert r["source"] == {"name": "Story", "table": "MasterAdv", "row": 900001, "command": 3,
                           "type": {"enum": "AdvCommand", "value": 2, "name": "Talk"}}
    assert r["characters"] == [] and r["category"] == {"name": "Main"}
    assert r["sound"] == {"id": 5001, "category": 2, "sheet": STORY_SHEET, "cue": f"{STORY_SHEET}_001"}
    assert r["text"] == {"id": "x01_1", "texts": {c: f"x01_1-{c}" for c in languages.LANGUAGES}}
    assert r["speaker"] == {"name": "alpha", "texts": [
        {"id": "adv_alpha", "texts": {c: f"adv_alpha-{c}" for c in languages.LANGUAGES}}]}
    assert r["inferred"] == {"characters": [1], "rule": "speaker-name",
                             "matches": [{"segment": "alpha", "character": 1}]}
    assert r["status"] == "ok" and r["availability"] == {"master": True, "catalog": True, "exported": True}
    assert [s["file"] for s in r["audio"]["streams"]] == [f"{STORY_SHEET}_001.flac"]
    assert r["audio"]["streams"][0]["artifact"].startswith(audio_task(STORY_SHEET, SHEETS2).id)
    a, b = row(doc, "MasterAdv:900001:5:1"), row(doc, "MasterAdv:900001:5:2")
    assert (a["source"]["slot"], b["source"]["slot"]) == ("1", "2")
    assert a["text"] == b["text"] and a["speaker"]["name"] == "gamma・Alpha"
    assert a["inferred"]["characters"] == [2, 1]                       # in speaker order, case ignored
    assert a["inferred"]["matches"] == [{"segment": "gamma", "character": 2}, {"segment": "Alpha", "character": 1}]
    assert a["status"] == "ok"
    assert b["status"] == "missing-cue" and b["detail"] == f"sheet {STORY_SHEET} has no cue {STORY_SHEET}_009"
    chat = row(doc, "MasterAdv:900001:7")
    assert chat["source"]["type"]["name"] == "ChatTalk" and "inferred" not in chat          # no character match
    assert chat["speaker"]["texts"][0]["texts"]["ko"] == "adv_passerby-ko"
    assert chat["status"] == "no-value" and chat["detail"] == \
        f"{story_keys('adv_script_x_01')['sheets']} has no row 99"
    assert chat["sound"] == {"id": 5004, "category": 2, "sheet": None, "cue": "lost_01"}
    bare = row(doc, "MasterAdv:900001:9")
    assert bare["source"]["type"]["name"] == "Voice" and "speaker" not in bare and "text" not in bare
    assert bare["status"] == "no-value" and bare["sound"] == {"id": 5005}
    assert bare["availability"] == {"master": False, "catalog": None, "exported": False}
    gone = row(doc, "MasterAdv:900001:11")
    assert gone["status"] == "missing-key" and gone["detail"] == "no catalog key Cri/Sound/adv_voice_x_gone"
    ignored = row(doc, "MasterAdv:900001:12")
    assert ignored["source"]["ignoreData"] is True and ignored["status"] == "ok"
    assert ignored["inferred"]["matches"] == [{"segment": "alpha", "character": 1}]
    two = row(doc, "MasterAdv:900002:1")
    assert two["category"] == {"name": "Friendship"} and two["text"] == {"id": "x02_1", "texts": None}
    assert two["speaker"]["texts"] == [{"id": "adv_gamma", "texts": None}]      # the episode has no -Text key
    assert two["status"] == "not-exported" and two["detail"] == \
        f"{story_keys('adv_script_x_02')['sheets']} is not exported"
    assert two["sound"] == {"id": 5101, "category": 2, "sheet": None, "cue": f"{STORY_SHEET}_004"}
    home = row(doc, "MasterAdv:900003:2")
    assert home["category"] == {"name": "HomeSpotTapTalk"} and home["status"] == "ok"
    assert home["audio"]["streams"] == row(doc, "MasterTalk:561")["audio"]["streams"]
    cov = doc["coverage"]["sources"]["Story"]
    assert cov["rows"] == 9 and cov["counts"] == {"missing-cue": 1, "missing-key": 1, "no-value": 2,
                                                   "not-exported": 1, "ok": 4}
    assert cov["gaps"] == ["MasterAdv:900001:5:2", "MasterAdv:900001:11"]


def test_episodes_kinds_and_statuses():
    doc = build()
    eps = {e["id"]: e for e in doc["episodes"]}
    assert list(eps) == sorted(eps)
    main = eps[900001]
    assert main == {"id": 900001, "asset": "adv_script_x_01", "kind": "Main", "table": "MasterStoryEpisode",
                    "row": 101, "title": {"id": "Title_900001",
                                          "texts": {c: f"title_900001-{c}" for c in languages.LANGUAGES}},
                    "fields": {"chapter": 1, "episode": 1, "character": 0, "another": False, "extra": False},
                    "names": {"chapter": {c: f"chapter_name_1-{c}" for c in languages.LANGUAGES}},
                    "status": "ok", "voices": 7}
    assert eps[900002]["fields"] == {"friendship": 12, "episode": 2, "characterA": 1, "characterB": 2}
    assert (eps[900003]["kind"], eps[900003]["fields"]) == ("HomeSpotTapTalk", {"spot": 10001, "character": 1})
    assert "title" not in eps[900003]
    status = {i: (e["status"], e.get("detail")) for i, e in eps.items()}
    assert status[900004] == ("no-value", "MasterAdv row 900004 has no _advEpisodeAsset")
    assert status[900005] == ("missing-key", "no catalog key Adv/Episode/adv_script_gone/adv_script_gone")
    assert status[900006] == ("not-exported", None)
    assert status[900007] == ("unsupported", "the script has no Collection")
    assert status[900008] == ("ok", None) and eps[900008]["voices"] == 0 and eps[900008]["kind"] == "LiveResult"
    assert eps[900004]["kind"] is None and "table" not in eps[900004]
    cov = doc["coverage"]["sources"]["Story"]
    assert cov["episodes"] == {"count": 8, "counts": {"missing-key": 1, "no-value": 1, "not-exported": 1, "ok": 4,
                                                     "unsupported": 1},
                               "gaps": ["MasterAdv:900005"]}
    assert cov["empty"] == 1
    phase1 = voices.gaps(build(story_=False)) - len(build(story_=False)["coverage"]["sources"]["Story"]["episodes"]
                                                        ["gaps"])
    assert voices.gaps(doc) == phase1 + 2 + 1


def test_characters_are_inferred_from_the_speaker_only():
    doc = build()
    assert [c["id"] for c in doc["characters"]] == [1, 2]
    assert [r["id"] for r in voices.select(doc, characters={2}, source="Story")] == [
        "MasterAdv:900001:5:1", "MasterAdv:900001:5:2", "MasterAdv:900001:11", "MasterAdv:900002:1"]
    assert all(r["characters"] == [] for r in story_rows(doc))
    s = voices.summary(doc)["story"]
    assert s["speakers"]["passerby"] == {"rows": 1, "characters": []}
    assert s["speakers"]["alpha・passerby"] == {"rows": 1, "characters": [1]}
    assert s["speakers"][""] == {"rows": 1, "characters": []}
    # a split key the rules do not use keeps the whole name as one segment
    rules = copy.deepcopy(RULES)
    rules["voices"]["story"]["split"] = ""
    other = voices.build(rules, tables(), AddressIndex({k: [] for k in catalog_keys()}), None, story=story())
    assert "inferred" not in row(other, "MasterAdv:900001:5:1")
    assert row(other, "MasterAdv:900001:3")["inferred"]["characters"] == [1]


def test_speaker_segments_match_the_parts_of_a_name_id():
    assert voices._name_parts({1: "Character_Name_Alpha", 2: "Character_Name_Beta_Gamma",
                               3: "Character_Name_Delta_Alpha"}) == {"beta": 2, "gamma": 2, "delta": 3}
    assert voices._name_parts({1: "Character_Name_Alpha"}) == {"alpha": 1}
    assert voices._name_parts({1: "Alpha", 2: ""}) == {"alpha": 1}
    ep = storyvoices.episode({"Collection": [cmd(1, target="beta", voices_=[5001]),
                                             cmd(2, target="alpha_child", voices_=[5001]),
                                             cmd(3, target="Name・character", voices_=[5001])]}, None,
                             shard([sound_row(5001, 700, f"{STORY_SHEET}_001")]),
                             shard([{"_id": 700, "_cueSheetName": STORY_SHEET}]))
    doc = voices.build(RULES, tables(), AddressIndex({k: [] for k in catalog_keys()}), None,
                       story={"adv_script_x_01": ep})
    assert row(doc, "MasterAdv:900001:1")["inferred"] == {"characters": [2], "rule": "speaker-name",
                                                          "matches": [{"segment": "beta", "character": 2}]}
    assert "inferred" not in row(doc, "MasterAdv:900001:2")          # a part of the segment is not enough
    assert "inferred" not in row(doc, "MasterAdv:900001:3")          # the parts every name id has


def test_reverse_coverage_counts_a_cue_once_and_shared_cues_are_reported():
    doc = build()
    rev = doc["coverage"]["reverse"]
    assert "Cri/Sound/adv_voice_x_01" not in rev["unreferencedKeys"]
    assert rev["unreferencedStreams"][STORY_SHEET] == [[4, f"{STORY_SHEET}_004"]]    # its only row: not exported
    alone = build(story_=False)
    assert alone["coverage"]["reverse"]["unreferencedKeys"] == ["Cri/Sound/adv_voice_x_01"]
    assert alone["coverage"]["reverse"]["unreferencedStreams"]["VoiceSystem_01"] == \
        rev["unreferencedStreams"]["VoiceSystem_01"]
    s = voices.summary(doc)
    assert s["story"]["sharedCues"] == {"rows": 1, "cues": 1, "sources": {"Talk": 1}}
    assert s["story"]["kinds"]["Main"] == {"episodes": 1, "voices": 7}
    assert s["story"]["kinds"]["None"] == {"episodes": 4, "voices": 0}
    assert s["categories"]["Story.Main"] == {"rows": 7, "ok": 3}
    assert s["story"]["gaps"] == ["MasterAdv:900005"]


def test_phase_one_rows_do_not_change():
    with_story, without = build(), build(story_=False, tables_={**tables(), "MasterAdv": []})
    strip = [r for r in with_story["rows"] if r["source"]["name"] != "Story"]
    assert contract.encode(strip) == contract.encode(without["rows"])


def test_content_is_apart_from_availability_and_deterministic():
    content = ("id", "source", "characters", "category", "sound", "text", "speaker", "inferred")
    strip = lambda d: [{k: r[k] for k in content if k in r} for r in story_rows(d)]     # noqa: E731
    exported, bare = build(), build(audio_=False)
    assert strip(exported) == strip(bare)
    assert row(bare, "MasterAdv:900001:3")["status"] == "not-exported"
    assert contract.encode(build()) == contract.encode(exported)
    shuffled = {k: list(reversed(v)) for k, v in tables().items()}
    saved = copy.deepcopy(EPISODES)
    try:
        for e in EPISODES.values():
            for p in ("text", "sound", "sheets"):
                if p in e:
                    random.Random(7).shuffle(e[p])
        assert contract.encode(build(tables_=shuffled)) == contract.encode(exported)
    finally:
        EPISODES.clear()
        EPISODES.update(saved)


def test_story_rules_are_checked():
    for edit, message in [
        (lambda s: s.update(key="Adv/{name}"), "template of {asset}"),
        (lambda s: s.update(colour=1), "keys are"),
        (lambda s: s["shards"].pop("text"), "shards"),
        (lambda s: s["kinds"].append(dict(s["kinds"][0])), "repeated kind"),
        (lambda s: s["kinds"][0]["fields"].update(bad=["_x", "NoDot"]), "Table.column"),
        (lambda s: s.update(split=3), "split"),
    ]:
        r = copy.deepcopy(RULES)
        edit(r["voices"]["story"])
        with pytest.raises(ViewError, match=message):
            voices.check_rules(r)
    r = copy.deepcopy(RULES)
    r["voices"]["sources"][0]["name"] = "Story"
    with pytest.raises(ViewError, match="reserved"):
        voices.check_rules(r)
    assert {"MasterAdv", "MasterStoryChapter", "MasterCharacterFriendship"} <= set(voices.tables_of(RULES))
    assert advcommand.name(2) == "Talk" and advcommand.name(52) == "Voice" and advcommand.name(99) == "Cmd99"


# ---------------------------------------------------------------- the stage
def export_results(store, env) -> dict:
    """unity.export results holding the exported episode parts, one bundle per episode: {asset: {part: oid}}."""
    oids = {}
    for n, (asset, parts) in enumerate(sorted(EPISODES.items())):
        t = Task("unity.export", 1, f"adv_{asset}", {}, {}, ())
        arts, items = [], []
        for m, part in enumerate(sorted(parts), 1):
            oid = contract.object_id(f"CAB-{n:04x}", 10 * n + m)
            role = voices.STORY_OBJECTS[part][1]
            data = part_bytes(asset, part)
            arts.append(contract.artifact(contract.artifact_id(oid, role), store.add(data, "json" if part == "script"
                                                                                        else "txt"),
                                          contract.provenance(t), {"kind": "unity.object", "format": "json"}))
            items.append(contract.item(oid, "exported", artifacts=[arts[-1]["id"]]))
            oids.setdefault(asset, {})[part] = oid
        res = contract.result(t, arts, items)
        store.commit(res)
        env.done(t.id, res["key"])
    return oids


def address_doc() -> dict:
    """The address table: the catalog keys, the episode objects (a script's MonoBehaviour after another object of its
    key) and the bundle of every object's serialized file."""
    entries = {k: [] for k in catalog_keys()}
    files = {}
    for n, (asset, parts) in enumerate(sorted(EPISODES.items())):
        for m, part in enumerate(sorted(parts), 1):
            oid = contract.object_id(f"CAB-{n:04x}", 10 * n + m)
            cls = voices.STORY_OBJECTS[part][0]
            key = story_keys(asset)[part]
            entries[key] = ([Obj(f"CAB-{n:04x}:{10 * n + 9}", "GameObject", "x")] if part == "script" else []) + [
                Obj(oid, cls, asset)]
            files[f"CAB-{n:04x}"] = f"adv_{asset}"
    doc = addresses_doc(entries)
    doc["files"] = files
    return doc


def story_env(store, tmp_path):
    return planning(store, tmp_path, keys=catalog_keys(), sheets=SHEETS2, tables=tables(), doc=address_doc(),
                    results=lambda env: export_results(store, env))


def test_stage_reads_the_exported_episodes(tmp_path):
    store = Store(tmp_path / "s")
    env = story_env(store, tmp_path)
    stage = voices.VoiceStage()
    task = describe(stage, "main", None, env)
    eps = contract.loads(store.read(task.input("episodes").sha256))
    assert sorted(eps) == sorted(EPISODES)
    assert sorted(eps["adv_script_x_02"]) == ["script", "sound"]
    x01 = eps["adv_script_x_01"]["script"]
    assert x01 == {"object": "CAB-0001:11", "artifact": "CAB-0001:11#json",                # the MonoBehaviour
                   "sha256": contract.sha256(part_bytes("adv_script_x_01", "script"))}
    shas = {p["sha256"] for parts in eps.values() for p in parts.values()}
    assert sorted(i.role for i in task.inputs if i.role.startswith("adv:")) == sorted(f"adv:{s}" for s in shas)
    deps = stage.depends("main", env)
    assert sorted(d for d in deps if d.startswith("unity.export:")) == sorted(f"unity.export:adv_{a}"
                                                                             for a in EPISODES)
    ex = execute(task, store, registry(stage))
    assert (ex.status, ex.result_status) == ("ran", "ok")
    res = store.result(task.key)
    doc = contract.loads(store.read(res["artifacts"][0]["content"]["sha256"]))
    want = voices.build(RULES, tables(), AddressIndex({k: [] for k in catalog_keys()}), audio(store, SHEETS2),
                        story=story())
    assert doc["rows"] == want["rows"] and doc["episodes"] == want["episodes"]
    assert doc["coverage"] == want["coverage"]
    assert doc["snapshot"]["episodes"] == task.input("episodes").sha256
    assert STORY_SHEET in doc["snapshot"]["sheets"]
    assert describe(voices.VoiceStage(), "main", None, env).key == task.key
    again = execute(task, store, registry(stage), force=True)
    assert again.status == "ran" and contract.encode(store.result(task.key)) == contract.encode(res)


def test_stage_waits_for_the_exports(tmp_path):
    store = Store(tmp_path / "s")
    env = story_env(store, tmp_path)
    env.waiting("unity.export:adv_adv_script_x_01")
    with pytest.raises(Pending, match="unity.export"):
        describe(voices.VoiceStage(), "main", None, env)


def test_stage_without_exports_has_no_story_rows(tmp_path):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path, keys=catalog_keys(), sheets=SHEETS2, tables=tables(), doc=address_doc())
    stage = voices.VoiceStage()
    task = describe(stage, "main", None, env)
    assert not [i for i in task.inputs if i.role.startswith("adv:")]
    execute(task, store, registry(stage))
    doc = contract.loads(store.read(store.result(task.key)["artifacts"][0]["content"]["sha256"]))
    assert story_rows(doc) == []
    assert {e["status"] for e in doc["episodes"] if e["asset"] in EPISODES} == {"not-exported"}


# ---------------------------------------------------------------- the command
def export_dir(tmp_path, store):
    out = tmp_path / "out"
    doc = build()
    placed = {}
    for r in doc["rows"]:
        for s in (r.get("audio") or {}).get("streams", []):
            placed[s["artifact"]] = f"Cri/Sound/{r['sound']['sheet']}/{s['file']}"
            f = out / placed[s["artifact"]]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(f"{r['sound']['sheet']}/{s['file']}".encode())
    (out / "views").mkdir(parents=True)
    (out / "views" / "voices.json").write_bytes(contract.encode(voices.with_paths(doc, Paths(placed))))
    return out


def test_command_filters_story_rows(tmp_path, capsysbinary):
    out = export_dir(tmp_path, Store(tmp_path / "s"))
    common = argparse.Namespace()
    command(["voices", "list", "--from", str(out), "--source", "story", "--episode", "900001", "--status", "ok"],
            common, Cfg("en"))
    got = capsysbinary.readouterr().out.decode("utf-8").splitlines()
    assert got == [f"MasterAdv:900001:3\tadv_alpha-en\tStory.Main\t{STORY_SHEET}/{STORY_SHEET}_001\tok\tx01_1-en",
                   f"MasterAdv:900001:5:1\tadv_gamma-en,adv_alpha-en\tStory.Main\t{STORY_SHEET}/{STORY_SHEET}_002\t"
                   f"ok\tx01_2-en",
                   f"MasterAdv:900001:12\tadv_both-en\tStory.Main\t{STORY_SHEET}/{STORY_SHEET}_003\tok\tx01_5-en"]
    command(["voices", "search", "x01_3-ZH-HANS", "--from", str(out), "--language", "zh-Hans", "--json"], common)
    assert [r["id"] for r in json.loads(capsysbinary.readouterr().out)] == ["MasterAdv:900001:7"]
    command(["voices", "list", "--from", str(out), "--episode", "adv_script_x_02", "--json"], common)
    assert [r["id"] for r in json.loads(capsysbinary.readouterr().out)] == ["MasterAdv:900002:1"]
    command(["voices", "list", "--from", str(out), "--category", "Story.HomeSpotTapTalk", "--character", "alpha"],
            common)
    assert capsysbinary.readouterr().out.decode().split("\t")[0] == "MasterAdv:900003:2"
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(out), "--episode", "123"], common)
    command(["voices", "summary", "--from", str(out)], common)
    assert "story: 8 episodes" in capsysbinary.readouterr().out.decode()
    dst = tmp_path / "got"
    command(["voices", "get", "MasterAdv:900001:5:1", "-o", str(dst), "--from", str(out)], common)
    assert (dst / f"{STORY_SHEET}_002.flac").read_bytes() == f"{STORY_SHEET}/{STORY_SHEET}_002.flac".encode()
    assert [r["id"] for r in json.loads((dst / "voice.json").read_text(encoding="utf-8"))["rows"]] == [
        "MasterAdv:900001:5:1"]
    command(["voices", "get", "5201", "-o", str(tmp_path / "g2"), "--from", str(out)], common)
    assert (tmp_path / "g2" / "Growth_Alpha_RankUp_01.flac").is_file()


class Catalog:
    def keys(self, prefix=""):
        return [k for k in catalog_keys() if k.startswith(prefix)]


def test_command_without_an_export_reads_one_episode(tmp_path, capsysbinary, monkeypatch):
    from nnnotes import cri
    from test_voices import sheet_acb, stream_files
    mdir = write_master(tmp_path / "m", tables())
    loaded = []

    def load(cat, keys):
        asset = keys["script"].rsplit("/", 1)[-1]
        loaded.append(asset)
        return {p: EPISODES[asset][p] if p == "script" else shard(EPISODES[asset][p]) for p in EPISODES[asset]}

    def decode(cat, sheet, out_dir, key=None, fmt="flac", **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        streams = []
        for n, name, file, samples in stream_files(sheet, SHEETS2):
            (out_dir / f"{file}.{fmt}").write_bytes(f"decoded {sheet}/{file}".encode())
            streams.append({"stream": n, "name": name, "file": f"{file}.{fmt}"})
        (out_dir / "streams.json").write_text(json.dumps(streams), encoding="utf-8")

    monkeypatch.setattr(storyvoices, "load_episode", load)
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": sheet_acb(sheet, SHEETS2)}, cri.RAW_ACB))
    monkeypatch.setattr(cri, "decode", decode)
    common = argparse.Namespace(master_dir=lambda cfg: mdir, open_catalog=lambda cfg, bundles=True: Catalog())
    command(["voices", "list", "--episode", "900001", "--json"], common)
    rows = json.loads(capsysbinary.readouterr().out)
    assert loaded == ["adv_script_x_01"]
    assert [(r["id"], r["status"]) for r in rows][:2] == [("MasterAdv:900001:3", "not-exported"),
                                                          ("MasterAdv:900001:5:1", "not-exported")]
    command(["voices", "list", "--source", "Story", "--json"], common)
    assert json.loads(capsysbinary.readouterr().out) == []            # no episode read without --episode
    dst = tmp_path / "got"
    command(["voices", "get", "MasterAdv:900001:3", "-o", str(dst)], common)
    assert loaded[-1] == "adv_script_x_01"
    assert (dst / f"{STORY_SHEET}_001.flac").read_bytes() == f"decoded {STORY_SHEET}/{STORY_SHEET}_001".encode()
