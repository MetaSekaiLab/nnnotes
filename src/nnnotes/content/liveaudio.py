"""Live sounds: the cue structure of the live's cue sheets, the game's sound settings and the live-audio documents.

What a live plays besides its chart, from the master data (MasterSound, MasterSoundCueSheet, MasterLiveMusic,
MasterLiveNoteSe, MasterLiveSe, MasterOptionDefault), the cue sheets' ACBs and their decoded streams (cri.decode or
the cri.audio stage), the ACF of the APK (ACF_IN_APK) and two MonoBehaviours of the APK's built-in files
(SETTINGS_SCRIPTS). A sound entry (sound_entry):

  {row, sheet, cue, category, categories, volume, busSends, lengthMs, other, layers[{file, stream, awbId,
  sampleRate, channels, samples, loopStart, loopEnd, loopFlag, volume, busSends, other}]}

the cue structure (sequence -> tracks -> synth -> waveform) read from the ACB (acb_cues: command 65 categories, 146
volume, 111 bus send), each layer the decoded stream `audio/<sheet>/<file>`.

music_doc (the live.music product, format MUSIC_FORMAT): `music` {soundId, volume, startTimeSec} the BGM sound, and
`sounds` {its id: its entry}, each layer in the product's audio format.

sounds_doc (the live.sounds product, format SOUNDS_FORMAT), the sounds of the default options (auto play at Perfect,
fresh profile) and of the note sound sets offered:

  sounds        MasterSound id -> entry: the note SE of the default preset and of every offered set, and the live
                SE of LIVE_SE_SOUNDS (the start and finish cheers and the finish direction of every result: the one a
                live ends with depends on how it is played)
  categories    CRI category volumes for a fresh profile (SoundVolumeSettings x AppConfigDefaultData)
  acf, react    the ACF's buses and categories, its REACT (ducking) entries between live categories
  noteSe        LiveNoteSeType -> sound id / volume / mute for the default preset (LiveSettingCreator.CreateSESettings);
                with sets offered also `groups`: set id -> LiveNoteSeType -> sound id (MasterLiveNoteSe)
  liveSe        LiveSeType -> sound id (MasterLiveSe)
  timeline      intro / end timings the runtime needs

A live.sounds subject names the sets offered: `default`, or `NoteSePatternId=<id>,<id>,...` (sounds_subject).

extract() writes the same for one music into a live directory (audio/live-audio.json: the fields of both documents
in one, the BGM sound among the sounds) from a Catalog, decoding each cue sheet with cri.decode into
`<out>/audio/<sheet>/` (a sheet already decoded there, the BGM by score.py, is reused).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.jsonio import write_json
from ..formats.acb import commands as _commands
from ..formats.acb import tables as _tables
from ..formats.acb import u16s as _u16s
from ..formats.encoders import FLAC_LEVEL
from ..source.apkset import ApkSet

if TYPE_CHECKING:
    from ..source.catalog import Catalog
    from ..unity.player import PlayerData
    from .liveoptions import LiveOptions

ACF_IN_APK = "assets/Cri/Sound/Sirius.acf"
MUSIC_FORMAT, SOUNDS_FORMAT = 1, 1  # the "format" of music_doc / sounds_doc
MUSIC_TABLES = ("MasterLiveMusic", "MasterSound", "MasterSoundCueSheet")
SOUND_TABLES = ("MasterOptionDefault", "MasterLiveNoteSe", "MasterLiveSe", "MasterSound", "MasterSoundCueSheet")
# the MonoBehaviours of the sound settings (script classes; the APK's built-in files hold one of each)
SETTINGS_SCRIPTS = ("SoundVolumeSettings", "AppConfigDefaultData")
# the AppConfigDefaultData field of each live category's volume (AppConfig.UpdateLiveSoundVolumeConfig)
LIVE_VOLUMES = {
    "LiveBgm": "LiveBgmSoundVolume",
    "LiveSe": "LiveSeSoundVolume",
    "LiveVoice": "LiveVoiceSoundVolume",
    "LiveNotesSe": "LiveNotesSeSoundVolume",
}
SE_PATTERN = "NoteSePatternId"  # the option of the note sound sets (liveoptions)
# intro / end timings of the live's sounds
TIMELINE = {
    "startCheerDelaySec": 1.0,
    "playVoiceSignalSec": 1.483333,
    "startCheerStopFadeSec": 1.0,
    "finishCheerStopFadeSec": 1.0,
}

# App.Master.LiveNoteSeType / LiveSeType
NOTE_SE_TYPES = {
    1: "InVain",
    2: "Good",
    3: "Great",
    4: "Perfect",
    5: "Flick",
    6: "FlickDirection",
    7: "Slide",
    8: "Just",
    9: "Trace",
    10: "SlideConnect",
    11: "GekisouTap",
    12: "GekisouFlick",
    13: "GekisouFlickDirection",
    14: "GekisouSlide",
}
LIVE_SE_TYPES = {
    1: "GekisouCountDown",
    2: "LuckHit",
    3: "LuckSuperHit",
    4: "LuckCritical",
    5: "LuckMiss",
    6: "LuckRushDouble",
    7: "LuckRushTriple",
    8: "LuckRushMax",
    9: "StartCheers",
    10: "FinishCheers",
    11: "GekisouFirstCheers",
    12: "GekisouOthersCheers",
    13: "LiveClearDirection",
    14: "FullComboDirection",
    15: "AssistFullComboDirection",
    16: "AllPerfectDirection",
}
# SoundSettingsExtensions.ToOptionItemType (its constant table: note SE type 1..14 -> volume OptionItemType)
NOTE_SE_VOLUME_ITEM = [431, 433, 433, 433, 435, 437, 439, 433, 441, 439, 451, 453, 455, 457]
# OptionSoundUtility.GetMuteTypeForVolume: volume item -> mute item (OptionItemType 431..459 -> 460..470)
NOTE_SE_MUTE_ITEM = {
    431: 460,
    433: 461,
    435: 462,
    437: 463,
    439: 464,
    441: 465,
    451: 466,
    453: 467,
    455: 468,
    457: 469,
    459: 470,
}
# LiveSettingCreator.BuildIndividualNoteSeDictionary: the sound set item (OptionItemType) of each note SE type
NOTE_SE_ID_ITEM = {
    1: 430,
    2: 432,
    3: 432,
    4: 432,
    8: 432,
    5: 434,
    6: 436,
    7: 438,
    10: 438,
    9: 440,
    11: 450,
    12: 452,
    13: 454,
    14: 456,
}
# live SE whose sounds a live directory carries: the start / finish cheers (9, 10) and the finish direction of every
# result (LivePlayingStateNodeBase.PlayFinishVoiceAndCheer: after the result's voice and the finish cheer, 16
# AllPerfectDirection, else 14 FullComboDirection, else 15 AssistFullComboDirection, else 13 LiveClearDirection; none
# when the life is below 1), in type order
LIVE_SE_SOUNDS = (9, 10, 13, 14, 15, 16)


def _params(cmds, categories: dict[int, str], buses: list[str]) -> dict:
    """Parameters of one command list. 65 = category ids, 146 = volume, 111 = bus send (index into the ACB's
    StringValueTable bus names, level / 10000). Other codes are kept as hex, uninterpreted."""
    d = {"categories": [], "volume": 1.0, "busSends": {}, "other": {}}
    for code, v in cmds:
        if code == 65:
            d["categories"] += [categories[struct.unpack_from(">I", v, i)[0]] for i in range(0, len(v), 4)]
        elif code == 146:
            d["volume"] *= struct.unpack(">f", v)[0]
        elif code == 111:
            bus, level = struct.unpack(">HH", v)
            d["busSends"][buses[bus]] = level / 10000
        elif code in (0, 2000):
            pass
        else:
            d["other"][str(code)] = v.hex()
    return d


def acb_cues(acb: bytes) -> dict[str, dict]:
    """{cueName: cue data} of an ACB whose cues are sequences of tracks that note-on one synth each (the only shape
    in the live sheets; anything else raises)."""
    top, T = _tables(acb)
    categories = {r["Id"]: r["Name"] for r in T.get("AcfReferenceTable", []) if r["Type"] == 3}
    buses = [r["StringValue"] for r in T.get("StringValueTable", [])]
    names = {r["CueIndex"]: r["CueName"] for r in T["CueNameTable"]}

    def cmd(table, index):
        return [] if index == 65535 else _commands(T[table][index]["Command"])

    out = {}
    for ci, c in enumerate(T["CueTable"]):
        name = names[ci]
        if c["ReferenceType"] != 3:
            raise ValueError(f"{name}: cue reference type {c['ReferenceType']} (only sequences handled)")
        seq = T["SequenceTable"][c["ReferenceIndex"]]
        if seq["Type"] != 0:
            raise ValueError(f"{name}: sequence type {seq['Type']} (only polyphonic handled)")
        sp = _params(cmd("SeqCommandTable", seq["CommandIndex"]), categories, buses)
        layers = []
        for ti in _u16s(seq["TrackIndex"]):
            tr = T["TrackTable"][ti]
            tp = _params(cmd("TrackCommandTable", tr["CommandIndex"]), categories, buses)
            ons = [v for code, v in cmd("TrackEventTable", tr["EventIndex"]) if code == 2000]
            if len(ons) != 1:
                raise ValueError(f"{name}: track {ti} has {len(ons)} note-ons")
            kind, si = struct.unpack(">HH", ons[0][:4])
            if kind != 2:
                raise ValueError(f"{name}: track {ti} note-on kind {kind}")
            syn = T["SynthTable"][si]
            yp = _params(cmd("SynthCommandTable", syn["CommandIndex"]), categories, buses)
            items = [
                struct.unpack_from(">HH", syn["ReferenceItems"], i) for i in range(0, len(syn["ReferenceItems"]), 4)
            ]
            if syn["Type"] != 0 or len(items) != 1 or items[0][0] != 1:
                raise ValueError(f"{name}: synth {si} type {syn['Type']} items {items}")
            w = T["WaveformTable"][items[0][1]]
            if w["Streaming"] != 0:
                raise ValueError(f"{name}: streamed waveform (memory AWB expected)")
            layers.append(
                {
                    "awbId": w["MemoryAwbId"],
                    "samples": w["NumSamples"],
                    "loopFlag": w["LoopFlag"],
                    "volume": tp["volume"] * yp["volume"],
                    "busSends": {**tp["busSends"], **yp["busSends"]},
                    "trackOther": tp["other"],
                    "synthOther": yp["other"],
                }
            )
        out[name] = {
            "cueId": c["CueId"],
            "lengthMs": c["Length"],
            "categories": sp["categories"],
            "volume": sp["volume"],
            "busSends": sp["busSends"],
            "sequenceOther": sp["other"],
            "acbVolume": top.get("AcbVolume", 1.0),
            "layers": layers,
        }
    return out


def acf_info(acf: bytes) -> dict:
    """Categories, buses and REACT entries of the game's ACF (ACF_IN_APK)."""
    top, T = _tables(acf)
    cat_names = {r["Index"]: r["Name"] for r in T["CategoryNameTable"]}
    cats = {}
    for r in T["CategoryTable"]:
        if r["CommandIndex"] != 65535:
            raise ValueError(f"ACF category {cat_names[r['Id']]} has commands (default volume) - not handled")
        cats[cat_names[r["Id"]]] = {"id": r["Id"], "group": r["GroupIndex"]}
    bus_names = [r["StringValue"] for r in T["BusNameTable"]]
    buses = [{"name": bus_names[b["BusNameIndex"]], "volume": b["Volume"], "fx": b["NumFxs"]} for b in T["BusTable"]]
    react = [
        {
            "name": r["REACTName"],
            "src": cat_names[r["Src"]],
            "dest": cat_names[r["Dest"]],
            "level": r["Level"],
            "decrementMs": r["DecrementTime"],
            "incrementMs": r["IncrementTime"],
            "holdType": r["HoldType"],
            "holdMs": r["HoldTime"],
            "startCurve": [r["StartCurveType"], r["StartCurveStrength"]],
            "endCurve": [r["EndCurveType"], r["EndCurveStrength"]],
            "parameterId": r["ParameterId"],
            "targetType": r["TargetType"],
        }
        for r in T.get("ReactTable", [])
    ]
    return {"name": top["Name"], "categories": cats, "buses": buses, "react": react}


# --------------------------------------------------------------------------- game settings
def category_volumes(svs: dict, acd: dict) -> dict:
    """Fresh-profile CRI category volumes from the SoundVolumeSettings `svs` and AppConfigDefaultData `acd`
    MonoBehaviours (SETTINGS_SCRIPTS): `<Cat>` = SoundVolumeSettings.DefaultVolume x 1.0 (SoundManager.Boot
    ChangeVolume("All", 1)); `Live<X>Config` = LocalCacheData live volume = AppConfigDefaultData
    (AppConfig.UpdateLiveSoundVolumeConfig); Bgm/Se/VoiceConfig = system option 400-402 / 100 (preset 1: 1.0)."""
    out = {e["CategoryName"]: e["DefaultVolume"] for e in svs["_collection"]}
    defaults = acd["_defaultData"]
    for cat, field in LIVE_VOLUMES.items():
        out[cat + "Config"] = defaults[f"<{field}>k__BackingField"]
    return out


def sound_settings(objects) -> tuple[dict, dict]:
    """The SoundVolumeSettings and AppConfigDefaultData MonoBehaviours (SETTINGS_SCRIPTS) among the root objects of a
    product's object table, by their script's class (exactly one of each)."""
    found: dict[str, list] = {c: [] for c in SETTINGS_SCRIPTS}
    for oid in objects.roots:
        if oid not in objects or objects.info(oid).get("class") != "MonoBehaviour":
            continue
        doc = objects.json(oid)
        cls = str(doc.get("$script") or "").rsplit("|", 1)[-1]
        if cls in found:
            found[cls].append(doc)
    for cls, docs in found.items():
        if len(docs) != 1:
            raise ValueError(f"{cls}: {len(docs)} objects among the product's roots")
    svs, acd = (found[c][0] for c in SETTINGS_SCRIPTS)
    return svs, acd


def _option(rows: list[dict], preset: int, item: int) -> str:
    hit = [r["_valueString"] for r in rows if r["_presetId"] == preset and r["_optionItemType"] == item]
    if len(hit) != 1:
        raise KeyError(f"MasterOptionDefault preset {preset} item {item}: {len(hit)} rows")
    return hit[0]


def note_se_groups(rows: list[dict]) -> dict[int, dict[int, int]]:
    """MasterLiveNoteSe rows: sound set (_groupID) -> LiveNoteSeType -> sound id."""
    out: dict[int, dict[int, int]] = {}
    for r in rows:
        out.setdefault(r["_groupID"], {})[r["_liveNoteSeType"]] = r["_seId"]
    return out


def _volume(opt: list[dict], preset: int, item: int) -> float:
    """OptionSoundUtility.GetIndividualNoteSeVolume: 0 when the item's mute (GetMuteTypeForVolume) is on, else
    clamp01(value / 100)."""
    if _option(opt, preset, NOTE_SE_MUTE_ITEM[item]).upper() == "TRUE":
        return 0.0
    return min(max(int(_option(opt, preset, item)) / 100.0, 0.0), 1.0)


def note_se_settings(tables: dict, preset: int = 1, groups: tuple = ()) -> dict:
    """LiveSettingCreator.CreateSESettings for a fresh profile (option preset `preset`) from the master tables
    `tables` {name: rows} (MasterOptionDefault, MasterLiveNoteSe): pattern mode takes the MasterLiveNoteSe rows of the
    set NoteSePatternId (420); UseIndividualNoteSe (421) takes, per note SE type, the row of the set its sound item
    names (BuildIndividualNoteSeDictionary; a type whose set has no row for it has no entry). `groups`: sound set ids
    whose tables are written as `groups` as well (the sets a viewer may choose)."""
    opt = tables["MasterOptionDefault"]
    table = note_se_groups(tables["MasterLiveNoteSe"])
    group = int(_option(opt, preset, 420))
    individual = _option(opt, preset, 421).upper() == "TRUE"
    if individual:
        types = {}
        for t, item in NOTE_SE_ID_ITEM.items():
            se = table.get(int(_option(opt, preset, item)), {}).get(t)
            if se is not None:
                types[t] = se
    else:
        types = dict(table.get(group, {}))
    volumes, mutes = {}, {}
    for t in range(1, 15):
        item = NOTE_SE_VOLUME_ITEM[t - 1]
        mute = _option(opt, preset, NOTE_SE_MUTE_ITEM[item]).upper() == "TRUE"
        # GetIndividualNoteSeVolume (mute -> 0) / GetNoteSeMute (GetMuteTypeForVolume)
        volumes[t], mutes[t] = _volume(opt, preset, item), mute
    out = {
        "preset": preset,
        "patternId": group,
        "useIndividualNoteSe": individual,
        "types": {str(k): types[k] for k in sorted(types)},
        "typeNames": NOTE_SE_TYPES,
        "volumes": {str(k): v for k, v in volumes.items()},
        "mutes": {str(k): v for k, v in mutes.items()},
        "gekisouTraceVolume": _volume(opt, preset, 459),
    }
    if groups:
        missing = [g for g in groups if g not in table]
        if missing:
            raise KeyError(f"MasterLiveNoteSe has no rows of sound set(s) {missing}")
        out["groups"] = {str(g): {str(t): table[g][t] for t in sorted(table[g])} for g in groups}
    return out


# --------------------------------------------------------------------------- sounds
def sheet_key(sheet: str) -> str:
    """The catalog key of a cue sheet."""
    return f"Cri/Sound/{sheet}"


def music_sound(tables: dict, music_id: int) -> tuple[dict, dict]:
    """(MasterSound row, MasterSoundCueSheet row) of a music's BGM (MasterLiveMusic._musicSoundID)."""
    music = [r for r in tables["MasterLiveMusic"] if r["_id"] == music_id]
    if len(music) != 1:
        raise KeyError(f"MasterLiveMusic {music_id}")
    sound = _row(tables, "MasterSound", music[0]["_musicSoundID"])
    return sound, _row(tables, "MasterSoundCueSheet", sound["_soundCueSheetID"])


def _row(tables: dict, table: str, row_id: int) -> dict:
    hit = [r for r in tables[table] if r["_id"] == row_id]
    if len(hit) != 1:
        raise KeyError(f"{table} {row_id}: {len(hit)} rows")
    return hit[0]


def live_sounds(tables: dict, groups: tuple = ()) -> dict:
    """The live sounds of the default options with the note sound sets `groups` offered as well: {"noteSe":
    note_se_settings, "liveSe": {LiveSeType: sound id}, "ids": the sound ids a live plays (the default note SE, the
    live SE of LIVE_SE_SOUNDS, then the note SE of the other offered sets)}."""
    nse = note_se_settings(tables, groups=groups)
    live_se = {r["_liveSeType"]: r["_seId"] for r in tables["MasterLiveSe"]}
    ids = sorted(set(nse["types"].values())) + [live_se[t] for t in LIVE_SE_SOUNDS]
    for se in sorted({v for g in nse.get("groups", {}).values() for v in g.values()}):
        if se not in ids:
            ids.append(se)
    return {"noteSe": nse, "liveSe": live_se, "ids": ids}


def sheets_of(tables: dict, ids) -> dict[str, list[int]]:
    """{cue sheet name: sound ids} of MasterSound ids, in the order of the ids."""
    sheets = {r["_id"]: r["_cueSheetName"] for r in tables["MasterSoundCueSheet"]}
    out: dict[str, list[int]] = {}
    for sid in ids:
        out.setdefault(sheets[_row(tables, "MasterSound", sid)["_soundCueSheetID"]], []).append(sid)
    return out


def sound_record(tables: dict, ids) -> dict:
    """The MasterSound and MasterSoundCueSheet rows of sound ids (sorted by id): what sound_entry reads of them."""
    sounds = sorted((_row(tables, "MasterSound", i) for i in set(ids)), key=lambda r: r["_id"])
    sheets = {r["_soundCueSheetID"] for r in sounds}
    return {
        "MasterSound": sounds,
        "MasterSoundCueSheet": sorted((_row(tables, "MasterSoundCueSheet", i) for i in sheets), key=lambda r: r["_id"]),
    }


def sound_entry(row: dict, sheet: str, cues: dict, streams: list[dict], files=None) -> dict:
    """The live-audio entry of a MasterSound row whose cue is in the sheet `sheet` (`cues`: acb_cues of its ACB,
    `streams`: its decoded streams, streams.json): its categories, volume, bus sends and one layer per track with
    the decoded stream (vgmstream streams follow the memory AWB order) at `audio/<sheet>/<file>` (`files`: {stream
    file: the layer's file name} where it differs)."""
    if row["_isRandomPitch"]:
        raise NotImplementedError(f"sound {row['_id']}: random pitch")
    name = row["_cueName"]
    cue = cues[name]
    layers = []
    for L in cue["layers"]:
        s = streams[L["awbId"]]
        if s["name"] != name or abs(s["samples"] - L["samples"]) > 8:
            raise RuntimeError(f"{sheet}/{name}: AWB {L['awbId']} -> stream {s} does not match")
        layers.append(
            {
                "file": f"audio/{sheet}/{(files or {}).get(s['file'], s['file'])}",
                "stream": s["stream"],
                "awbId": L["awbId"],
                "sampleRate": s["sampleRate"],
                "channels": s["channels"],
                "samples": s["samples"],
                "loopStart": s.get("loopStart"),
                "loopEnd": s.get("loopEnd"),
                "loopFlag": L["loopFlag"],
                "volume": L["volume"],
                "busSends": L["busSends"],
                "other": {"track": L["trackOther"], "synth": L["synthOther"]},
            }
        )
    return {
        "row": row,
        "sheet": sheet,
        "cue": name,
        "category": row["_category"],
        "categories": cue["categories"],
        "volume": cue["volume"] * cue["acbVolume"],
        "busSends": cue["busSends"],
        "lengthMs": cue["lengthMs"],
        "other": cue["sequenceOther"],
        "layers": layers,
    }


def sound_entries(tables: dict, ids, sheet) -> tuple[dict, dict]:
    """({sound id: sound_entry}, {cue sheet: (cues, streams)}) of sound ids; `sheet(name)` gives a cue sheet's
    (ACB bytes, streams)."""
    sounds = {r["_id"]: r for r in tables["MasterSound"]}
    entries, read = {}, {}
    for name, sids in sheets_of(tables, ids).items():
        acb, streams = sheet(name)
        cues = acb_cues(acb)
        read[name] = (cues, streams)
        for sid in sids:
            entries[str(sid)] = sound_entry(sounds[sid], name, cues, streams)
    return entries, read


def music_doc(tables: dict, music_id: int, acb: bytes, streams: list[dict], ext: str) -> dict:
    """The live.music document of a music (`tables`: music_record): its BGM sound (`music`: sound id, volume, start
    time) and that sound's entry (`sounds`), each layer's file with the extension `ext` (".m4a": the web format)."""
    row, sheet = music_sound(tables, music_id)
    name = sheet["_cueSheetName"]
    files = {s["file"]: s["file"].rsplit(".", 1)[0] + ext for s in streams}
    return {
        "format": MUSIC_FORMAT,
        "musicId": music_id,
        "music": {"soundId": row["_id"], "volume": 1.0, "startTimeSec": 0.0},
        "sounds": {str(row["_id"]): sound_entry(row, name, acb_cues(acb), streams, files)},
    }


def music_record(tables: dict, music_id: int) -> dict:
    """What music_doc reads of the master tables for one music: its MasterLiveMusic row, its BGM's MasterSound and
    MasterSoundCueSheet rows."""
    music = [r for r in tables["MasterLiveMusic"] if r["_id"] == music_id]
    if len(music) != 1:
        raise KeyError(f"MasterLiveMusic {music_id}")
    row, sheet = music_sound(tables, music_id)
    return {"MasterLiveMusic": music, "MasterSound": [row], "MasterSoundCueSheet": [sheet]}


def sounds_subject(groups: tuple = ()) -> str:
    """The live.sounds subject of the note sound sets offered (LiveOptions.se_patterns: () for the default alone)."""
    return f"{SE_PATTERN}={','.join(map(str, sorted(groups)))}" if groups else "default"


def sounds_groups(subject: str) -> tuple:
    """The note sound sets of a live.sounds subject (sounds_subject)."""
    if subject == "default":
        return ()
    name, eq, values = subject.partition("=")
    try:
        groups = tuple(int(v) for v in values.split(",")) if name == SE_PATTERN and eq else ()
    except ValueError:
        groups = ()
    if not groups or list(groups) != sorted(set(groups)):
        raise ValueError(f"{subject}: not a live sounds subject (default, {SE_PATTERN}=<ids>)")
    return groups


def sounds_record(tables: dict, subject: str) -> dict:
    """What sounds_doc reads of the master tables for a live.sounds subject: the option defaults of preset 1, the
    note SE and live SE tables, and the MasterSound / MasterSoundCueSheet rows of its sounds."""
    sounds = live_sounds(tables, sounds_groups(subject))
    return {
        "MasterOptionDefault": [r for r in tables["MasterOptionDefault"] if r["_presetId"] == 1],
        "MasterLiveNoteSe": tables["MasterLiveNoteSe"],
        "MasterLiveSe": tables["MasterLiveSe"],
        **sound_record(tables, sounds["ids"]),
    }


def sounds_doc(tables: dict, subject: str, sheet, svs: dict, acd: dict, acf: bytes) -> dict:
    """The live.sounds document (`tables`: sounds_record; `sheet(name)`: a cue sheet's (ACB bytes, streams); `svs`,
    `acd`: the SETTINGS_SCRIPTS MonoBehaviours; `acf`: the ACF): every sound a live plays besides the BGM, the
    category volumes, the ACF's buses and categories, REACT, the note SE and live SE tables and the intro / end
    timings."""
    live = live_sounds(tables, sounds_groups(subject))
    entries, _ = sound_entries(tables, live["ids"], sheet)
    info = acf_info(acf)
    return {
        "format": SOUNDS_FORMAT,
        "sounds": entries,
        "categories": category_volumes(svs, acd),
        "acf": {"name": info["name"], "buses": info["buses"], "categories": info["categories"]},
        "react": info["react"],
        "noteSe": live["noteSe"],
        "liveSe": {str(k): v for k, v in sorted(live["liveSe"].items())},
        "liveSeNames": LIVE_SE_TYPES,
        "timeline": TIMELINE,
    }


# --------------------------------------------------------------------------- extraction
def _decode(cat: Catalog, sheet: str, out_dir: Path, fmt: str, flac_level: int) -> list[dict]:
    """Decode a cue sheet into <out>/audio/<sheet>/ (reusing an existing decode) and return its stream list."""
    from ..formats import cri

    d = out_dir / "audio" / sheet
    if not (d / "streams.json").exists():
        cri.decode(cat, sheet, d, fmt=fmt, flac_level=flac_level)
    import json

    return json.loads((d / "streams.json").read_text(encoding="utf-8"))


def _player_mono(player: PlayerData, cls: str) -> dict:
    hits = []
    for o in player.env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            if player.script(o)[2] != cls:
                continue
        except Exception:  # MonoBehaviours without a resolvable script are not ours
            continue
        hits.append(player.mono(o))
    if len(hits) != 1:
        raise RuntimeError(f"{cls}: {len(hits)} objects in the player data")
    return hits[0]


def extract(
    cat: Catalog,
    master: Path,
    player: PlayerData,
    music_id: int,
    out_dir: Path,
    fmt: str = "flac",
    options: LiveOptions | None = None,
    *,
    flac_level: int = FLAC_LEVEL,
) -> dict:
    """Decode the live's sound cue sheets and write <out>/audio/live-audio.json (module documentation). Returns a
    summary. `options`: the note sound sets it offers are written as noteSe.groups, their sounds decoded after the
    default ones. `flac_level`: ffmpeg's compression level of the FLAC files (cri.decode)."""
    from ..formats import cri
    from .score import master_table

    out_dir = Path(out_dir)
    if cat.apk is None:
        raise RuntimeError("liveaudio needs the Catalog opened with apk= (ACF, HCA key)")
    tables = {t: master_table(master, t) for t in dict.fromkeys((*MUSIC_TABLES, *SOUND_TABLES))}
    music_row, _ = music_sound(tables, music_id)
    live = live_sounds(tables, options.se_patterns if options is not None else ())
    layouts = {}

    def sheet(name: str):
        acb, layouts[name] = cri.acb_data(cat, name)
        return acb["acb"], _decode(cat, name, out_dir, fmt, flac_level)

    entries, read = sound_entries(tables, list(dict.fromkeys([music_row["_id"], *live["ids"]])), sheet)
    with ApkSet(cat.apk) as z:
        acf = acf_info(z.read(ACF_IN_APK))
    svs, acd = (_player_mono(player, s) for s in SETTINGS_SCRIPTS)
    doc = {
        "musicId": music_id,
        "spec": "nnnotes.content.liveaudio",
        "sounds": entries,
        "categories": category_volumes(svs, acd),
        "acf": {"name": acf["name"], "buses": acf["buses"], "categories": acf["categories"]},
        "react": acf["react"],
        "music": {"soundId": music_row["_id"], "volume": 1.0, "startTimeSec": 0.0},
        "noteSe": live["noteSe"],
        "liveSe": {str(k): v for k, v in sorted(live["liveSe"].items())},
        "liveSeNames": LIVE_SE_TYPES,
        "timeline": TIMELINE,
        "voice": {
            "decision": "caller",
            "character": None,
            "rule": "LotteryStartVoice: random deck member, random MasterLiveStartCharacterVoice row of that "
            "character; finish voice from LotteryFinishVoice; no deck -> undefined",
            "startVoiceSoundIds": [],
        },
        "decoded": {
            n: {"layout": layouts[n], "cues": len(cues), "streams": len(streams)} for n, (cues, streams) in read.items()
        },
    }
    write_json(out_dir / "audio" / "live-audio.json", doc)
    return {
        "index": "audio/live-audio.json",
        "sheets": sorted(read),
        "sounds": len(entries),
        "musicSoundId": music_row["_id"],
    }
