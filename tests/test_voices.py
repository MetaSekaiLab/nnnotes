"""The voices index on synthetic master tables, synthetic ACBs and synthetic cri.audio results (no game data): every
source, the ACB cue tables, the statuses, content apart from availability, the stage, the layout document and the
command."""
import argparse
import copy
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from nnnotes import acb, contract, languages, voices
from nnnotes.contract import Input, Task
from nnnotes.stages import Env, Pending, describe, execute, registry
from nnnotes.store import Store
from nnnotes.views import AddressIndex, ViewError
from test_views import FakeAddresses, addresses_doc, write_master

ROOT = Path(__file__).resolve().parents[1]
RATE = 48000


# ---------------------------------------------------------------- synthetic CRI tables
def utf(name: str, cols: list, rows: list[dict]) -> bytes:
    """A CRI @UTF table: `cols` [(column, type)] stored per row (types 0 u8, 2 u16, 4 u32, 0xA string, 0xB data)."""
    strings, data = bytearray(b"<NULL>\0"), bytearray()

    def s(x: str) -> int:
        off = len(strings)
        strings.extend(x.encode("utf-8") + b"\0")
        return off

    name_off = s(name)
    head = b"".join(bytes([0x50 | t]) + struct.pack(">I", s(c)) for c, t in cols)
    body = bytearray()
    for r in rows:
        for c, t in cols:
            v = r.get(c)
            if t == 0xA:
                body += struct.pack(">I", s(v or ""))
            elif t == 0xB:
                v = bytes(v or b"")
                body += struct.pack(">II", len(data), len(v))
                data.extend(v)
            else:
                body += struct.pack({0: ">B", 2: ">H", 4: ">I"}[t], v or 0)
    width = sum({0: 1, 2: 2, 4: 4, 0xA: 4, 0xB: 8}[t] for _, t in cols)
    rows_off = 24 + len(head)
    str_off = rows_off + len(body)
    data_off = str_off + len(strings)
    t = struct.pack(">HHIIIHHI", 1, rows_off, str_off, data_off, name_off, len(cols), width, len(rows))
    t += head + bytes(body) + bytes(strings) + bytes(data)
    return b"@UTF" + struct.pack(">I", len(t)) + t


def afs2(ids: list[int]) -> bytes:
    return b"AFS2" + bytes([1, 4, 2, 0]) + struct.pack("<IHH", len(ids), 32, 0) + b"".join(
        struct.pack("<H", i) for i in ids) + bytes(4 * (len(ids) + 1))


def tlv(code: int, payload: bytes) -> bytes:
    return struct.pack(">HB", code, len(payload)) + payload


def acb_bytes(*, names, cues, waves, synths=(), seqs=(), tracks=(), events=(), blockseqs=(), blocks=(), awb=()):
    """An ACB of the given tables (rows as dicts; bytes columns packed by the caller)."""
    tables = {
        "CueNameTable": utf("CueName", [("CueName", 0xA), ("CueIndex", 2)], names),
        "CueTable": utf("Cue", [("CueId", 4), ("ReferenceType", 0), ("ReferenceIndex", 2), ("Length", 4)], cues),
        "WaveformTable": utf("Waveform", [("MemoryAwbId", 2), ("StreamAwbId", 2), ("Streaming", 0),
                                          ("NumChannels", 0), ("SamplingRate", 2), ("NumSamples", 4)], waves),
        "SynthTable": utf("Synth", [("Type", 0), ("ReferenceItems", 0xB)], list(synths)),
        "SequenceTable": utf("Sequence", [("Type", 0), ("TrackIndex", 0xB)], list(seqs)),
        "TrackTable": utf("Track", [("EventIndex", 2)], list(tracks)),
        "TrackEventTable": utf("TrackEvent", [("Command", 0xB)], list(events)),
        "BlockSequenceTable": utf("BlockSequence", [("TrackIndex", 0xB), ("BlockIndex", 0xB)], list(blockseqs)),
        "BlockTable": utf("Block", [("TrackIndex", 0xB)], list(blocks)),
    }
    cols = [(k, 0xB) for k in tables] + [("AwbFile", 0xB), ("Name", 0xA)]
    return utf("Header", cols, [{**tables, "AwbFile": afs2(list(awb)) if awb else b"", "Name": "sheet"}])


def u16(*v) -> bytes:
    return b"".join(struct.pack(">H", x) for x in v)


def wave(awb_id, samples, streaming=0):
    return {"MemoryAwbId": awb_id, "StreamAwbId": awb_id, "Streaming": streaming, "NumChannels": 1,
            "SamplingRate": RATE, "NumSamples": samples}


def simple_acb(cues: dict, order: list[int], samples: dict) -> bytes:
    """An ACB whose cues play waveforms by AWB id ({cue: [awb ids]}: one id a waveform cue, several a synth), the
    memory AWB holding the ids in `order`; `samples` {awb id: samples}."""
    ids = sorted({i for v in cues.values() for i in v})
    widx = {a: n for n, a in enumerate(ids)}
    names, rows, synths = [], [], []
    for n, (name, v) in enumerate(cues.items()):
        names.append({"CueName": name, "CueIndex": n})
        if len(v) == 1:
            rows.append({"CueId": n, "ReferenceType": 1, "ReferenceIndex": widx[v[0]], "Length": 100})
        else:
            rows.append({"CueId": n, "ReferenceType": 2, "ReferenceIndex": len(synths), "Length": 100})
            synths.append({"Type": 1, "ReferenceItems": b"".join(u16(1, widx[a]) for a in v)})
    return acb_bytes(names=names, cues=rows, waves=[wave(a, samples[a]) for a in ids], synths=synths, awb=order)


# ---------------------------------------------------------------- acb
def test_cue_streams_follow_every_reference_kind():
    data = acb_bytes(
        names=[{"CueName": "wave", "CueIndex": 0}, {"CueName": "synth", "CueIndex": 1},
               {"CueName": "seq", "CueIndex": 2}, {"CueName": "block", "CueIndex": 3},
               {"CueName": "streamed", "CueIndex": 4}, {"CueName": "none", "CueIndex": 5},
               {"CueName": "wave", "CueIndex": 1}, {"CueName": "bad", "CueIndex": 9}],
        cues=[{"CueId": 10, "ReferenceType": 1, "ReferenceIndex": 0, "Length": 1500},
              {"CueId": 11, "ReferenceType": 2, "ReferenceIndex": 0},
              {"CueId": 12, "ReferenceType": 3, "ReferenceIndex": 0},
              {"CueId": 13, "ReferenceType": 8, "ReferenceIndex": 0},
              {"CueId": 14, "ReferenceType": 1, "ReferenceIndex": 3},
              {"CueId": 15, "ReferenceType": 0, "ReferenceIndex": 0}],
        waves=[wave(5, 500), wave(3, 300), wave(9, 900), wave(0, 7, streaming=1)],
        synths=[{"ReferenceItems": u16(1, 1, 2, 1, 1, 1)}, {"ReferenceItems": u16(1, 2, 0, 0, 1, 0)}],
        seqs=[{"TrackIndex": u16(0)}],
        tracks=[{"EventIndex": 0}, {"EventIndex": 1}, {"EventIndex": 0xFFFF}],
        events=[{"Command": tlv(0, b"") + tlv(2000, u16(2, 1)) + tlv(146, b"\0\0\0\0")},
                {"Command": tlv(2003, u16(3, 0))}],
        blockseqs=[{"TrackIndex": u16(2), "BlockIndex": u16(0)}],
        blocks=[{"TrackIndex": u16(1)}],
        awb=[3, 5, 9])
    cues = acb.cue_streams(data)
    assert list(cues) == ["wave", "synth", "seq", "block", "streamed", "none"]    # a repeated name keeps the first
    assert cues["wave"] == {"cueId": 10, "lengthMs": 1500, "streams": [
        {"stream": 2, "awbId": 5, "samples": 500, "sampleRate": RATE, "channels": 1}]}
    assert [s["stream"] for s in cues["synth"]["streams"]] == [1, 3]           # nested synth; each waveform once
    assert [s["stream"] for s in cues["seq"]["streams"]] == [3]                # track event note-on -> synth
    assert [s["stream"] for s in cues["block"]["streams"]] == [3]              # block -> track -> sequence
    assert cues["streamed"]["streams"] == [{"streamed": True, "awbId": 0, "samples": 7, "sampleRate": RATE,
                                            "channels": 1}]
    assert cues["none"]["streams"] == []
    assert acb.awb_ids(afs2([7, 2])) == [7, 2]
    with pytest.raises(acb.AcbError):
        acb.cue_streams(b"RIFF....")


def test_utf_tables_and_commands():
    t = utf("T", [("a", 0), ("b", 2), ("c", 4), ("s", 0xA), ("d", 0xB)],
            [{"a": 1, "b": 2, "c": 3, "s": "x", "d": b"\1\2"}, {"a": 4, "s": "", "d": b""}])
    assert acb.utf_table(t) == [{"a": 1, "b": 2, "c": 3, "s": "x", "d": b"\1\2"},
                                {"a": 4, "b": 0, "c": 0, "s": "", "d": b""}]
    assert acb.commands(tlv(2000, u16(2, 7)) + tlv(65, b"\0\0\0\1")) == [(2000, u16(2, 7)), (65, b"\0\0\0\1")]
    assert acb.u16s(u16(1, 2, 3)) == [1, 2, 3]


# ---------------------------------------------------------------- synthetic master data
def text(tid, base):
    return {"_id": tid, **{col: f"{base}-{code}" for code, (_, col) in languages.LANGUAGES.items()}}


def snd(i, sheet, cue, cat=2):
    return {"_id": i, "_category": cat, "_soundCueSheetID": sheet, "_cueName": cue}


TABLES = {
    "MasterCharacter": [
        {"_id": 1, "_nameTextID": "Character_Name_Alpha", "_shortNameTextID": "Short_Alpha",
         "_enDisplayNameTextId": "En_Alpha"},
        {"_id": 2, "_nameTextID": "Character_Name_Beta_Gamma", "_shortNameTextID": "Short_Gamma",
         "_enDisplayNameTextId": "En_Gamma"}],
    "MasterSoundCueSheet": [{"_id": 1, "_cueSheetName": "Bgm"}, {"_id": 10, "_cueSheetName": "VoiceSystem_01"},
                            {"_id": 11, "_cueSheetName": "VoiceSystem_02"},
                            {"_id": 12, "_cueSheetName": "VoiceLive_01"},
                            {"_id": 13, "_cueSheetName": "VoiceGone"}, {"_id": 14, "_cueSheetName": "spot_01_01_01"},
                            {"_id": 15, "_cueSheetName": "VoiceBroken"}],
    "MasterSound": [
        snd(1, 1, "bgm_home", cat=0), snd(101, 10, "Growth_Alpha_RankUp_01"), snd(102, 10, "Shared_01"),
        snd(103, 11, "Shared_01"), snd(104, 10, "Gone_01"), snd(105, 12, "Live_Alpha_Skill_01"),
        snd(106, 13, "Gacha_Alpha_01"), snd(107, 12, "Live_Alpha_Gekisou_01"), snd(108, 12, "Live_Alpha_Combo_01"),
        snd(109, 12, "Live_Alpha_Pair_01"), snd(110, 12, "Live_Gamma_Pair_01"), snd(111, 12, "Live_Alpha_Start_01"),
        snd(112, 14, "IntroRandom"), snd(113, 14, "spot_01_01_01_alpha_01_tap"),
        snd(114, 10, "Result_Alpha_Talk_CP_Gamma_Call_01"), snd(115, 15, "Broken_01"), snd(116, 99, "Orphan_01")],
    "MasterTalk": [
        {"_id": 561, "_characterId": 1, "_costumeId": 1, "_category": 1, "_textId": "Talk_Text_561",
         "_voiceSoundId": 101, "_startAt": "", "_unlockCharacterRank": 1},
        {"_id": 562, "_characterId": 1, "_category": 0, "_textId": "Talk_Text_562", "_voiceSoundId": 102},
        {"_id": 563, "_characterId": 2, "_category": 2, "_textId": "Talk_Text_563", "_voiceSoundId": 104,
         "_seasonStartAt": "4/1"},
        {"_id": 564, "_characterId": 2, "_category": 9, "_textId": "Talk_Missing", "_voiceSoundId": 999},
        {"_id": 565, "_characterId": 1, "_category": 0, "_textId": "", "_voiceSoundId": 115}],
    "MasterCharacterVoice": [{"_id": 1, "_characterId": 2, "_type": 3, "_textId": "CharacterVoice_1",
                              "_soundId": 103, "_scoreRank": 0, "_startAt": "2026/01/01 0:00:00"}],
    "MasterMemberCard": [
        {"_id": 1, "_characterID": 1, "_nameTextID": "Card_1", "_gachaVoiceTextId": "Gacha_1",
         "_gachaVoiceSoundId": 106, "_startAt": "2026-01-01 0:00:00"},
        {"_id": 2, "_characterID": 2, "_nameTextID": "Card_2", "_gachaVoiceTextId": "", "_gachaVoiceSoundId": 0}],
    "MasterLiveCharacter": [{"_id": 1, "_characterID": 1, "_liveSkillVoiceTextID": "LiveSkill_1",
                             "_liveSkillVoiceSoundID": 105}],
    "MasterLiveGekisouVoice": [{"_id": 1, "_characterID": 1, "_gekisouVoiceType": 3, "_voiceTextID": "Gekisou_1",
                                "_voiceID": 107}],
    "MasterLiveDialogueCommon": [{"_id": 1, "_characterID": 1, "_dialogueType": 1, "_comboVoiceTextID": "Common_1",
                                  "_comboVoiceSoundID": 108}],
    "MasterLiveDialogueFixedPair": [{"_id": 1, "_dialogueType": 1, "_characterID01": 1, "_characterID02": 2,
                                     "_character01ComboVoiceTextID": "Pair_1a", "_character01ComboVoiceSoundID": 109,
                                     "_character02ComboVoiceTextID": "Pair_1b", "_character02ComboVoiceSoundID": 110,
                                     "_unlockFriendshipRank": 0}],
    "MasterLiveStartCharacterVoice": [{"_id": 1, "_characterId": 1, "_voiceTextId": "Start_1", "_voiceSoundId": 111,
                                       "_unlockCharacterRank": 0}],
    "MasterHomeSpot": [{"_id": 10001, "_nameTextId": "Spot_Name_10001", "_characterIds": [1, 2],
                        "_introSoundId": 112, "_startAt": "2026/01/01 0:00:00"}],
    "MasterTitle": [{"_id": 1, "_voiceCharacterIds": [1]}, {"_id": 2, "_voiceCharacterIds": [1, 2]}],
}
TABLES["MasterText"] = [text(t, t.lower()) for t in (
    "Character_Name_Alpha", "Short_Alpha", "En_Alpha", "Character_Name_Beta_Gamma", "Short_Gamma", "En_Gamma",
    "Talk_Text_562", "Talk_Text_563", "CharacterVoice_1", "Gacha_1", "Card_1", "LiveSkill_1", "Gekisou_1", "Common_1",
    "Pair_1a", "Pair_1b", "Start_1", "Spot_Name_10001")] + [
    {"_id": "Talk_Text_561", "_japanese": "テストです", "_english": "Hello!", "_traditionalChinese": "測試",
     "_simplifiedChinese": "測試", "_korean": "테스트"}]

KEYS = ["Cri/Sound/Bgm", "Cri/Sound/InitialVoice", "Cri/Sound/VoiceBroken", "Cri/Sound/VoiceLive_01",
        "Cri/Sound/VoiceSystem_01", "Cri/Sound/VoiceSystem_02", "Cri/Sound/adv_voice_x_01", "Cri/Sound/spot_01_01_01"]

# the decoded sheets: {sheet: ({cue: [awb ids]}, AWB order, {awb id: (stream name, samples)})}
SHEETS = {
    "VoiceSystem_01": ({"Growth_Alpha_RankUp_01": [0], "Shared_01": [1], "Extra_01": [2]}, [0, 1, 2],
                       {0: ("Growth_Alpha_RankUp_01", 1000), 1: ("Shared_01", 1100), 2: ("Extra_01", 1200)}),
    "VoiceSystem_02": ({"Shared_01": [4], "Other_01": [3]}, [3, 4], {3: ("Other_01", 900), 4: ("Shared_01", 800)}),
    "spot_01_01_01": ({"IntroRandom": [0, 1], "spot_01_01_01_alpha_01_tap": [2]}, [0, 1, 2],
                      {0: ("IntroRandom", 500), 1: ("IntroRandom", 600), 2: ("spot_01_01_01_alpha_01_tap", 700)}),
    "InitialVoice": ({"Title_1_App_ON_01": [0], "Title_1_Company_BR_01": [1], "Title_2_App_ON_01": [2]}, [0, 1, 2],
                     {0: ("Title_1_App_ON_01", 10), 1: ("Title_1_Company_BR_01", 11), 2: ("Title_2_App_ON_01", 12)}),
}


def sheet_acb(sheet: str) -> bytes:
    cues, order, info = SHEETS[sheet]
    return simple_acb(cues, order, {a: n for a, (_, n) in info.items()})


def stream_files(sheet: str) -> list[tuple[int, str, str, int]]:
    """(stream number, stream name, file, samples) as cri.decode names the files (a repeated name: <name>_<n>)."""
    _, order, info = SHEETS[sheet]
    out, seen = [], set()
    for n, a in enumerate(order, 1):
        name, samples = info[a]
        out.append((n, name, name if name not in seen else f"{name}_{n}", samples))
        seen.add(name)
    return out


def audio_task(sheet: str) -> Task:
    data = sheet_acb(sheet)
    sha = contract.sha256(data)
    return Task("cri.audio", 1, sha, {"flac": {"level": 8}}, {}, (Input("acb", sha, len(data)),))


def audio_result(store, sheet: str, unsupported: bool = False) -> dict:
    """A cri.audio result of a sheet, committed to `store` when given."""
    t = audio_task(sheet) if sheet in SHEETS else Task("cri.audio", 1, contract.sha256(sheet.encode()), {}, {}, ())
    if unsupported:
        return contract.result(t, [], [contract.item(t.id, "unsupported", why=contract.reason(
            "unsupported.cri.awb_external", "streamed"), cls="ACB")])
    arts = []
    for n, name, file, samples in stream_files(sheet):
        data = f"{sheet}/{file}".encode()
        content = store.add(data, "flac") if store is not None else {
            "sha256": contract.sha256(data), "size": len(data), "mediaType": "audio/flac", "ext": "flac"}
        arts.append(contract.artifact(f"{t.id}#{file}.flac", content, contract.provenance(t),
                                      {"kind": "audio.stream", "format": "flac", "facts": {
                                          "stream": n, "name": name, "sampleRate": RATE, "channels": 1,
                                          "samples": samples}}))
    res = contract.result(t, arts, [contract.item(t.id, "exported", artifacts=[a["id"] for a in arts], cls="ACB")])
    if store is not None:
        store.commit(res)
    return res


def audio(store=None) -> dict:
    out = {s: voices.sheet_audio(audio_result(store, s), acb.cue_streams(sheet_acb(s))) for s in SHEETS}
    out["VoiceBroken"] = voices.sheet_audio(audio_result(store, "VoiceBroken", unsupported=True), None)
    return out


def index(keys=KEYS) -> AddressIndex:
    return AddressIndex({k: [] for k in keys})


def build(audio_=None, tables=None, keys=KEYS):
    return voices.build(voices.load_rules(), copy.deepcopy(tables or TABLES), index(keys), audio_)


def row(doc, rid):
    return next(r for r in doc["rows"] if r["id"] == rid)


# ---------------------------------------------------------------- the index
def test_every_source_gives_rows():
    doc = build(audio())
    assert doc["schema"] == voices.SCHEMA and doc["view"] == "voices" and doc["version"] == 1
    by = {}
    for r in doc["rows"]:
        by.setdefault(r["source"]["name"], []).append(r["id"])
    assert by == {
        "Talk": ["MasterTalk:561", "MasterTalk:562", "MasterTalk:563", "MasterTalk:564", "MasterTalk:565"],
        "CharacterVoice": ["MasterCharacterVoice:1"], "MemberCard": ["MasterMemberCard:1"],
        "LiveCharacter": ["MasterLiveCharacter:1"], "LiveGekisouVoice": ["MasterLiveGekisouVoice:1"],
        "LiveDialogueCommon": ["MasterLiveDialogueCommon:1"],
        "LiveDialogueFixedPair": ["MasterLiveDialogueFixedPair:1:1", "MasterLiveDialogueFixedPair:1:2"],
        "LiveStartCharacterVoice": ["MasterLiveStartCharacterVoice:1"], "HomeSpot": ["MasterHomeSpot:10001"],
        "Title": [f"Title:{c}:{k}" for c in (1, 2) for k in ("TitleCall", "SplashCompanyBR", "SplashCompanyFT",
                                                             "SplashCompanyBilibili")],
        "Sound": ["MasterSound:113", "MasterSound:114", "MasterSound:116"]}
    assert doc["coverage"]["sources"]["MemberCard"]["empty"] == 1              # a card without a gacha voice
    assert doc["tables"] == voices.tables_of(voices.load_rules())


def test_the_rank_up_talk_is_found_with_its_file():
    doc = build(audio())
    r = row(doc, "MasterTalk:561")
    assert r["source"] == {"name": "Talk", "table": "MasterTalk", "row": 561, "column": "_voiceSoundId",
                           "type": {"enum": "CharacterVoiceMasterType", "value": 0, "name": "Talk"}}
    assert r["characters"] == [1]
    assert r["category"] == {"name": "CharacterRankUp", "enum": "TalkCategory", "value": 1}
    assert r["sound"] == {"id": 101, "category": 2, "sheet": "VoiceSystem_01", "cue": "Growth_Alpha_RankUp_01"}
    assert r["text"] == {"id": "Talk_Text_561", "texts": {"ja": "テストです", "en": "Hello!", "zh-Hant": "測試",
                                                          "zh-Hans": "測試", "ko": "테스트"}}
    assert r["conditions"] == {"_costumeId": 1, "_unlockCharacterRank": 1}       # the empty _startAt left out
    assert r["availability"] == {"master": True, "catalog": True, "exported": True} and r["status"] == "ok"
    tid = audio_task("VoiceSystem_01").id
    data = b"VoiceSystem_01/Growth_Alpha_RankUp_01"
    assert r["audio"] == {"cueId": 0, "lengthMs": 100, "streams": [{
        "stream": 1, "artifact": f"{tid}#Growth_Alpha_RankUp_01.flac", "file": "Growth_Alpha_RankUp_01.flac",
        "sha256": contract.sha256(data), "size": len(data), "sampleRate": RATE, "channels": 1, "samples": 1000,
        "seconds": round(1000 / RATE, 6)}]}
    for q in (dict(characters={1}, text="hello"), dict(category="character-rank-up"),
              dict(category="Talk.CharacterRankUp", characters={1}), dict(text="テスト", language="ja")):
        assert [x["id"] for x in voices.select(doc, **q)] == ["MasterTalk:561"], q
    assert voices.select(doc, text="テスト", language="en") == []
    assert voices.character_ids(doc, "alpha") == {1} and voices.character_ids(doc, "en_gamma-ko") == {2}
    assert voices.character_ids(doc, "2") == {2}
    assert [x["id"] for x in voices.rows_for(doc, "101")] == ["MasterTalk:561"]


def test_statuses_and_gaps():
    doc = build(audio())
    status = {r["id"]: r["status"] for r in doc["rows"]}
    assert status["MasterTalk:562"] == "ok"
    assert status["MasterTalk:563"] == "missing-cue"                          # the ACB has no Gone_01
    assert status["MasterTalk:564"] == "no-value" and row(doc, "MasterTalk:564")["detail"] == \
        "MasterSound has no row 999"
    assert row(doc, "MasterTalk:564")["availability"] == {"master": False, "catalog": None, "exported": False}
    assert status["MasterTalk:565"] == "unsupported" and row(doc, "MasterTalk:565")["detail"] == \
        "unsupported.cri.awb_external"
    assert status["MasterMemberCard:1"] == "missing-key"                      # no key Cri/Sound/VoiceGone
    assert status["MasterLiveCharacter:1"] == "not-exported"                  # a key, no cri.audio result
    assert row(doc, "MasterLiveCharacter:1")["availability"] == {"master": True, "catalog": True, "exported": False}
    assert status["MasterSound:116"] == "no-value"                            # its sheet has no row
    assert status["MasterSound:114"] == "missing-cue"                         # a MasterSound row the ACB lacks
    assert [status[f"Title:{c}:{k}"] for c in (1, 2) for k in ("TitleCall", "SplashCompanyBR")] == \
        ["ok", "ok", "ok", "missing-cue"]
    assert status["Title:1:SplashCompanyBilibili"] == "missing-cue"
    cov = doc["coverage"]["sources"]
    assert cov["Talk"]["gaps"] == ["MasterTalk:563"] and cov["MemberCard"]["gaps"] == ["MasterMemberCard:1"]
    assert cov["Sound"]["gaps"] == ["MasterSound:114"]
    assert cov["Talk"]["counts"] == {"missing-cue": 1, "no-value": 1, "ok": 2, "unsupported": 1}
    assert voices.gaps(doc) == 1 + 1 + 1 + 5
    assert voices.counts(doc)["not-exported"] == 6


def test_a_cue_in_two_sheets_resolves_in_its_own_sheet():
    doc = build(audio())
    a, b = row(doc, "MasterTalk:562"), row(doc, "MasterCharacterVoice:1")
    assert (a["sound"]["cue"], b["sound"]["cue"]) == ("Shared_01", "Shared_01")
    assert [(s["stream"], s["samples"]) for s in a["audio"]["streams"]] == [(2, 1100)]
    assert [(s["stream"], s["samples"]) for s in b["audio"]["streams"]] == [(2, 800)]    # AWB order 3, 4
    assert a["audio"]["streams"][0]["artifact"].startswith(audio_task("VoiceSystem_01").id)
    assert b["audio"]["streams"][0]["artifact"].startswith(audio_task("VoiceSystem_02").id)
    assert b["category"] == {"name": "RankUp", "enum": "CharacterVoiceType", "value": 3}


def test_several_characters_streams_and_inferences():
    doc = build(audio())
    spot = row(doc, "MasterHomeSpot:10001")
    assert spot["characters"] == [1, 2] and spot["category"] == {"name": "HomeSpot"}
    assert [s["file"] for s in spot["audio"]["streams"]] == ["IntroRandom.flac", "IntroRandom_2.flac"]
    assert spot["names"] == {"spot": {c: f"spot_name_10001-{c}" for c in languages.LANGUAGES}}
    pair = [row(doc, f"MasterLiveDialogueFixedPair:1:{s}") for s in (1, 2)]
    assert [(p["characters"], p["source"]["slot"], p["text"]["id"]) for p in pair] == \
        [([1], "1", "Pair_1a"), ([2], "2", "Pair_1b")]
    tap = row(doc, "MasterSound:113")
    assert tap["characters"] == [] and tap["category"] == {"name": None} and tap["status"] == "ok"
    assert tap["inferred"] == {"situation": "SpotTap", "characters": [1], "rows": ["MasterHomeSpot:10001"]}
    call = row(doc, "MasterSound:114")
    assert call["inferred"] == {"situation": "ResultPairTalk", "characters": [1, 2]}
    title = row(doc, "Title:1:TitleCall")
    assert title["source"] == {"name": "Title", "table": "MasterTitle", "rows": [1, 2], "column": "_voiceCharacterIds",
                               "cue": "Title_{character}_App_ON_01"}
    assert title["sound"] == {"id": None, "sheet": "InitialVoice", "cue": "Title_1_App_ON_01"}
    assert row(doc, "Title:2:TitleCall")["source"]["rows"] == [2]
    assert [x["id"] for x in voices.select(doc, characters={2}, source="Sound")] == ["MasterSound:114"]
    assert [c["id"] for c in doc["characters"]] == [1, 2]
    assert doc["characters"][1]["token"] == "Gamma"


def test_content_is_apart_from_availability():
    exported, bare = build(audio()), build()
    content = ("id", "source", "characters", "category", "sound", "text", "names", "conditions", "inferred")
    strip = lambda d: [{k: r[k] for k in content if k in r} for r in d["rows"]]     # noqa: E731
    assert strip(exported) == strip(bare)
    r = row(bare, "MasterTalk:561")
    assert r["status"] == "not-exported" and "audio" not in r
    assert r["availability"] == {"master": True, "catalog": True, "exported": False}
    gone = build(keys=[k for k in KEYS if k != "Cri/Sound/VoiceSystem_01"])
    assert row(gone, "MasterTalk:561")["status"] == "missing-key"
    assert row(gone, "MasterTalk:561")["availability"]["catalog"] is False
    assert strip(gone) == strip(bare)


def test_reverse_coverage():
    doc = build(audio())
    rev = doc["coverage"]["reverse"]
    assert rev["prefixes"] == voices.load_rules()["voices"]["prefixes"]
    assert rev["unreferencedKeys"] == ["Cri/Sound/adv_voice_x_01"]           # Bgm is under no prefix
    assert rev["unreferencedStreams"] == {"VoiceSystem_01": [[3, "Extra_01"]], "VoiceSystem_02": [[1, "Other_01"]]}
    assert rev["streams"] == 3 + 2 + 3 + 3
    assert voices.unreferenced_streams(doc) == 2
    s = voices.summary(doc)
    assert s["reverse"]["unreferencedStreams"] == {"VoiceSystem_N": 2}
    assert s["forward"]["missing-key"] == ["MasterMemberCard:1"]
    assert s["categories"]["Talk.CharacterRankUp"] == {"rows": 1, "ok": 1}


def test_documents_are_deterministic_and_canonical():
    a, b = build(audio()), build(audio())
    assert contract.encode(a) == contract.encode(b)
    shuffled = {k: list(reversed(v)) if isinstance(v, list) else v for k, v in TABLES.items()}
    assert contract.encode(build(audio(), shuffled)) == contract.encode(a)


def test_rules_are_checked():
    rules = voices.load_rules()
    for edit, message in [
        (lambda v: v.update(version=0), "version"),
        (lambda v: v.update(colour=1), "unknown keys"),
        (lambda v: v["sources"].append(dict(v["sources"][0])), "repeated"),
        (lambda v: v["sources"][0].update(category={"column": "_c", "enum": "Nope"}), "known enum"),
        (lambda v: v["sources"][0]["voices"][0].pop("sound"), "a sound"),
        (lambda v: v["sources"][-1]["cues"][0].update(cue="Title_{x}"), "template"),
        (lambda v: v.update(near=["Nope"]), "not a source"),
        (lambda v: v.update(key="Cri/Sound/{name}"), "key"),
    ]:
        r = copy.deepcopy(rules)
        edit(r["voices"])
        with pytest.raises(ViewError, match=message):
            voices.check_rules(r)
    with pytest.raises(ViewError, match="MasterTitle is not in the master data"):
        voices.build(rules, {k: v for k, v in TABLES.items() if k != "MasterTitle"}, index())


# ---------------------------------------------------------------- the stage
def planning(store, tmp: Path, keys=KEYS, skip=()) -> Env:
    """An environment where link.addresses:main has run (the catalog keys `keys`) and the cri.audio tasks of the
    decoded sheets have results; the raw ACBs are files under their catalog keys."""
    mdir = write_master(tmp / "m", TABLES)
    raw, deps = {}, {}
    for sheet in list(SHEETS) + ["VoiceBroken"]:
        data = sheet_acb(sheet) if sheet in SHEETS else b"@UTF broken"
        stable = f"cri_assets_cri/sound/{sheet.lower()}"
        p = tmp / "raw" / stable.replace("/", "_")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        sha, size = store.identify(p, "raw", stable)
        raw[stable] = Input("raw", sha, size, f"{stable}_{'0' * 32}", ({"kind": "file", "path": str(p)},))
        deps[f"Cri/Sound/{sheet}"] = [stable]
    lid = {s: f"remote:{i}" for i, s in enumerate(sorted(raw))}
    locations = [{"id": lid[s], "primaryKey": s, "kind": "raw", "dependencies": []} for s in sorted(raw)]
    locations += [{"id": f"remote:{100 + i}", "primaryKey": k, "kind": "asset", "dependencies": [lid[s] for s in d]}
                  for i, (k, d) in enumerate(sorted(deps.items()))]
    cat = {"rawFiles": [{"stable": s, "locations": [lid[s]]} for s in sorted(raw)], "locations": locations}
    link = FakeAddresses(addresses_doc({k: [] for k in keys}))
    env = Env(store, {"catalogs": {"main": []}, "master": mdir, "views": ["voices"], "raw": raw, "index": cat})
    t = describe(link, "main", None, env)
    execute(t, store, {link.name: link})
    env.done(t.id, t.key)
    for sheet in SHEETS:
        if sheet not in skip:
            res = audio_result(store, sheet)
            env.done(audio_task(sheet).id, res["key"])
    b = Task("cri.audio", 1, raw["cri_assets_cri/sound/voicebroken"].sha256, {}, {}, ())
    broken = contract.result(b, [], [contract.item(b.id, "unsupported", why=contract.reason(
        "unsupported.cri.awb_external", "streamed"), cls="ACB")])
    store.commit(broken)
    env.done(b.id, broken["key"])
    return env


def run_stage(env):
    stage = voices.VoiceStage()
    task = describe(stage, "main", None, env)
    ex = execute(task, env.store, registry(stage))
    res = env.store.result(task.key)
    return stage, task, ex, res, contract.loads(env.store.read(res["artifacts"][0]["content"]["sha256"]))


def test_stage_subjects_inputs_and_waits(tmp_path):
    store = Store(tmp_path / "s")
    stage = voices.VoiceStage()
    assert stage.name == "view.voices" and stage.after == ("link.addresses", "cri.audio") and stage.version == 1
    assert stage.subjects(Env(store, {"catalogs": {"main": []}, "master": None})) == []
    assert stage.subjects(Env(store, {"catalogs": {"main": []}, "master": tmp_path, "views": ["cards"]})) == []
    env = planning(store, tmp_path)
    assert stage.subjects(env) == ["main"]
    task = describe(stage, "main", None, env)
    roles = sorted(i.role for i in task.inputs)
    assert [r for r in roles if r.startswith("acb:")] == sorted(f"acb:{contract.sha256(sheet_acb(s))}"
                                                               for s in SHEETS)   # not the unsupported sheet's
    assert [r for r in roles if not r.startswith("acb:")] == [
        "addresses", *sorted(f"master:{t}" for t in voices.tables_of(stage.rules)), "rules", "sheets"]
    sheets = contract.loads(store.read(task.input("sheets").sha256))
    assert sorted(sheets) == sorted([*SHEETS, "VoiceBroken"])
    assert sheets["VoiceSystem_01"] == {"task": audio_task("VoiceSystem_01").id,
                                        "acb": contract.sha256(sheet_acb("VoiceSystem_01"))}
    assert sorted(task.context["audio"]) == sorted(stage.depends("main", env)[1:])
    assert stage.depends("main", env)[0] == "link.addresses:main"
    waiting = Env(store, env.facts)
    for tid in env.tasks("link.addresses"):
        waiting.done(tid, env.key(tid))
    waiting.waiting(audio_task("VoiceSystem_01").id)
    with pytest.raises(Pending, match="cri.audio"):
        describe(stage, "main", None, waiting)


def test_stage_document_equals_the_index_of_its_inputs(tmp_path):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path)
    stage, task, ex, res, doc = run_stage(env)
    assert (ex.status, ex.result_status) == ("ran", "ok") and res["items"] == []
    assert [a["id"] for a in res["artifacts"]] == ["view.voices:main#view"]
    want = voices.build(voices.load_rules(), TABLES, index(), audio(store))
    assert doc["rows"] == want["rows"] and doc["coverage"] == want["coverage"]
    assert sorted(doc["snapshot"]["tables"]) == voices.tables_of(stage.rules)
    assert doc["snapshot"]["sheets"]["VoiceBroken"]["status"] == "unsupported"
    facts = res["artifacts"][0]["semantics"]["facts"]
    assert facts == {"rows": len(doc["rows"]), "entries": voices.counts(doc), "gaps": voices.gaps(doc),
                     "unreferenced": 2}
    # described again: the same key; run again: the same bytes
    assert describe(voices.VoiceStage(), "main", None, env).key == task.key
    again = execute(task, store, registry(stage), force=True)
    assert again.status == "ran" and contract.encode(store.result(task.key)) == contract.encode(res)
    # the task crosses to another process as JSON
    same = contract.Task.from_json(json.loads(json.dumps(task.to_json())))
    assert same.key == task.key


def test_stage_key_follows_the_audio_and_the_catalog(tmp_path):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path)
    key = describe(voices.VoiceStage(), "main", None, env).key
    other = planning(Store(tmp_path / "t"), tmp_path / "t", keys=KEYS + ["Cri/Sound/VoiceGone"])
    assert describe(voices.VoiceStage(), "main", None, other).key != key
    fewer = planning(Store(tmp_path / "u"), tmp_path / "u", skip=("InitialVoice",))
    k2 = describe(voices.VoiceStage(), "main", None, fewer).key
    assert k2 != key
    _, _, _, _, doc = run_stage(fewer)
    assert row(doc, "Title:1:TitleCall")["status"] == "not-exported"


class Paths:
    def __init__(self, placed):
        self.placed = placed

    def artifact(self, aid):
        return self.placed.get(aid)

    def objects(self, oid):
        return []


def test_derived_document_has_the_stream_paths(tmp_path):
    store = Store(tmp_path / "s")
    env = planning(store, tmp_path)
    stage, task, _, res, doc = run_stage(env)
    tid = audio_task("VoiceSystem_01").id
    placed = {f"{tid}#Growth_Alpha_RankUp_01.flac": "Cri/Sound/VoiceSystem_01/Growth_Alpha_RankUp_01.flac"}
    out = stage.derived(task.id, res, store, Paths(placed))
    assert [p for p, _ in out] == ["views/voices.json"]
    d = contract.loads(out[0][1])
    assert d["layout"] == "original"
    assert row(d, "MasterTalk:561")["audio"]["streams"][0]["path"] == placed[f"{tid}#Growth_Alpha_RankUp_01.flac"]
    assert row(d, "MasterTalk:562")["audio"]["streams"][0]["path"] is None
    for r in d["rows"]:
        for s in (r.get("audio") or {}).get("streams", []):
            s.pop("path")
    assert {k: v for k, v in d.items() if k != "layout"} == doc


# ---------------------------------------------------------------- the command
class Cfg:
    def __init__(self, language="ja"):
        self.language = language

    def get(self, section, key):
        return self.language if (section, key) == ("catalog", "language") else None

    def require_path(self, section, key):
        return Path("apk")


class Catalog:
    def keys(self, prefix=""):
        return [k for k in KEYS if k.startswith(prefix)]


def command(argv, common, cfg=None):
    p = argparse.ArgumentParser(prog="nnnotes")
    voices.register(p.add_subparsers(dest="cmd", required=True), common)
    args = p.parse_args(argv)
    args.func(args, cfg or Cfg())


def export_dir(tmp: Path, store) -> Path:
    """An export directory: views/voices.json with paths and the stream files at them."""
    out = tmp / "out"
    placed = {}
    doc = voices.build(voices.load_rules(), TABLES, index(), audio(store))
    for r in doc["rows"]:
        for s in (r.get("audio") or {}).get("streams", []):
            sheet = r["sound"]["sheet"]
            placed[s["artifact"]] = f"Cri/Sound/{sheet}/{s['file']}"
            f = out / placed[s["artifact"]]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(store.read(s["sha256"]))
    d = voices.with_paths(doc, Paths(placed))
    (out / "views").mkdir(parents=True)
    (out / "views" / "voices.json").write_bytes(contract.encode(d))
    return out


def test_command_list_search_summary(tmp_path, capsysbinary):
    out = export_dir(tmp_path, Store(tmp_path / "s"))
    common = argparse.Namespace()
    command(["voices", "list", "--from", str(out), "--character", "Alpha", "--category", "Talk.CharacterRankUp"],
            common)
    line = capsysbinary.readouterr().out.decode("utf-8")
    assert line == "MasterTalk:561\tcharacter_name_alpha-ja\tTalk.CharacterRankUp\tVoiceSystem_01/" \
                   "Growth_Alpha_RankUp_01\tok\tテストです\n"
    command(["voices", "search", "HELLO", "--from", str(out), "--language", "en", "--json"], common)
    rows = json.loads(capsysbinary.readouterr().out)
    assert [r["id"] for r in rows] == ["MasterTalk:561"]
    command(["voices", "list", "--from", str(out), "--source", "Sound", "--character", "gamma"], common)
    assert capsysbinary.readouterr().out.decode().split("\t")[0] == "MasterSound:114"
    command(["voices", "summary", "--from", str(out), "--json"], common)
    s = json.loads(capsysbinary.readouterr().out)
    assert s["gaps"] == 8 and s["reverse"]["unreferencedStreams"] == {"VoiceSystem_N": 2}
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(out), "--character", "Nobody"], common)
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(tmp_path / "none")], common)


def test_command_without_an_export_builds_the_content(tmp_path, capsysbinary):
    mdir = write_master(tmp_path / "m", TABLES)
    common = argparse.Namespace(master_dir=lambda cfg: mdir, open_catalog=lambda cfg, bundles=True: Catalog())
    command(["voices", "list", "--character", "1", "--category", "character-rank-up", "--json"], common)
    rows = json.loads(capsysbinary.readouterr().out)
    assert [(r["id"], r["status"]) for r in rows] == [("MasterTalk:561", "not-exported")]


def test_get_copies_the_exported_files(tmp_path, capsysbinary):
    store = Store(tmp_path / "s")
    out = export_dir(tmp_path, store)
    dst = tmp_path / "got"
    command(["voices", "get", "MasterHomeSpot:10001", "-o", str(dst), "--from", str(out)], argparse.Namespace())
    assert sorted(p.name for p in dst.iterdir()) == ["IntroRandom.flac", "IntroRandom_2.flac", "voice.json"]
    assert (dst / "IntroRandom_2.flac").read_bytes() == b"spot_01_01_01/IntroRandom_2"
    meta = json.loads((dst / "voice.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in meta["rows"]] == ["MasterHomeSpot:10001"]
    assert meta["files"] == ["IntroRandom.flac", "IntroRandom_2.flac"]
    command(["voices", "get", "101", "-o", str(tmp_path / "g2"), "--from", str(out)], argparse.Namespace())
    assert (tmp_path / "g2" / "Growth_Alpha_RankUp_01.flac").is_file()
    with pytest.raises(SystemExit) as e:
        command(["voices", "get", "MasterTalk:563", "-o", str(tmp_path / "g3"), "--from", str(out)],
                argparse.Namespace())
    assert e.value.code == 1
    with pytest.raises(SystemExit) as e:
        command(["voices", "get", "MasterTalk:1", "-o", str(tmp_path / "g4"), "--from", str(out)],
                argparse.Namespace())
    assert e.value.code == 2


def test_get_without_an_export_decodes_the_one_sheet(tmp_path, capsysbinary, monkeypatch):
    from nnnotes import cri
    mdir = write_master(tmp_path / "m", TABLES)
    calls = []

    def decode(cat, sheet, out_dir, key=None, fmt="flac", **kw):
        calls.append((sheet, fmt, kw.get("flac_level")))
        out_dir.mkdir(parents=True, exist_ok=True)
        streams = []
        for n, name, file, samples in stream_files(sheet):
            (out_dir / f"{file}.{fmt}").write_bytes(f"decoded {sheet}/{file}".encode())
            streams.append({"stream": n, "name": name, "file": f"{file}.{fmt}"})
        (out_dir / "streams.json").write_text(json.dumps(streams), encoding="utf-8")

    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": sheet_acb(sheet)}, cri.RAW_ACB))
    monkeypatch.setattr(cri, "decode", decode)
    common = argparse.Namespace(master_dir=lambda cfg: mdir, open_catalog=lambda cfg, bundles=True: Catalog())
    dst = tmp_path / "got"
    command(["voices", "get", "MasterCharacterVoice:1", "-o", str(dst)], common)
    assert calls == [("VoiceSystem_02", "flac", 8)]
    assert sorted(p.name for p in dst.iterdir()) == ["Shared_01.flac", "voice.json"]
    assert (dst / "Shared_01.flac").read_bytes() == b"decoded VoiceSystem_02/Shared_01"
    command(["voices", "get", "MasterTalk:561", "-o", str(tmp_path / "ogg"), "--format", "ogg"], common)
    assert (tmp_path / "ogg" / "Growth_Alpha_RankUp_01.ogg").is_file()


# ---------------------------------------------------------------- laziness and documentation
def test_voices_import_nothing_heavy():
    code = ("import sys, nnnotes.voices, nnnotes.acb; "
            "print(sorted(m for m in ('UnityPy', 'numpy', 'PIL', 'nnnotes.cri') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    assert out.strip() == "[]"


def test_sources_statuses_and_situations_are_documented():
    doc = (ROOT / "docs" / "views.md").read_text(encoding="utf-8")
    section = re.search(r"^## Voices\n(.*?)(?=^## |\Z)", doc, re.S | re.M)
    assert section, "docs/views.md has no Voices section"
    text_ = section.group(1)
    v = voices.load_rules()["voices"]
    for s in v["sources"]:
        assert f"`{s['name']}`" in text_ and f"`{s['table']}`" in text_, s["name"]
        for c in s.get("cues", []):
            assert f"`{c['cue']}`" in text_ and f"`{c['category']}`" in text_
    assert f"`{voices.SOUND_SOURCE}`" in text_
    for s in voices.STATUSES:
        assert f"`{s}`" in text_, s
    for s in v["situations"]:
        assert f"`{s['situation']}`" in text_, s["situation"]
