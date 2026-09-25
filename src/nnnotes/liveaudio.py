"""Live sounds for one music: SE / cheer / voice cue sheets, their CRI cue data and the live-audio index.

`extract(cat, master, player, music_id, out_dir)` decodes every cue sheet the live plays in the preview slice
(auto play at Perfect, fresh profile, default options) with `cri.decode` into `<out>/audio/<cueSheet>/`
and writes `<out>/audio/live-audio.json`:

  sounds        MasterSound id -> {row, sheet, cue, categories, volume, busSends, layers[{file, sampleRate, samples,
                loopStart, loopEnd, loopFlag, volume, busSends}], lengthMs}; the cue structure (sequence -> tracks ->
                synth -> waveform) is read from the ACB (@UTF tables; command 65 categories, 146 volume, 111 bus send)
  categories    CRI category volumes for a fresh profile (SoundVolumeSettings x AppConfigDefaultData)
  react         the ACF's REACT (ducking) entries between live categories
  music         the BGM sound id and PlayMusic volume
  noteSe        LiveNoteSeType -> sound id / volume / mute for the default preset (LiveSettingCreator.CreateSESettings)
  liveSe        LiveSeType -> sound id (MasterLiveSe)
  timeline      intro / end timings the runtime needs
  voice         the start / finish character voice rule and the voice character the caller chose (none by default)

Cue sheets come through `cri` (any of its layouts; the note SE / live SE / voice sheets are embedded ACBs);
each cue layer maps to its vgmstream stream by `streams.json` (memory AWB order). A sheet already decoded into
`<out>/audio/<sheet>/` (the BGM by score.py) is reused.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from .catalog import Catalog
from .jsonio import write_json
from .player import PlayerData
from . import cri

ACF_IN_APK = "assets/Cri/Sound/Sirius.acf"

# App.Master.LiveNoteSeType / LiveSeType
NOTE_SE_TYPES = {1: "InVain", 2: "Good", 3: "Great", 4: "Perfect", 5: "Flick", 6: "FlickDirection", 7: "Slide",
                 8: "Just", 9: "Trace", 10: "SlideConnect", 11: "GekisouTap", 12: "GekisouFlick",
                 13: "GekisouFlickDirection", 14: "GekisouSlide"}
LIVE_SE_TYPES = {1: "GekisouCountDown", 2: "LuckHit", 3: "LuckSuperHit", 4: "LuckCritical", 5: "LuckMiss",
                 6: "LuckRushDouble", 7: "LuckRushTriple", 8: "LuckRushMax", 9: "StartCheers", 10: "FinishCheers",
                 11: "GekisouFirstCheers", 12: "GekisouOthersCheers", 13: "LiveClearDirection",
                 14: "FullComboDirection", 15: "AssistFullComboDirection", 16: "AllPerfectDirection"}
# SoundSettingsExtensions.ToOptionItemType (its constant table: note SE type 1..14 -> volume OptionItemType)
NOTE_SE_VOLUME_ITEM = [431, 433, 433, 433, 435, 437, 439, 433, 441, 439, 451, 453, 455, 457]
# OptionSoundUtility.GetMuteTypeForVolume: volume item -> mute item (OptionItemType 431..459 -> 460..470)
NOTE_SE_MUTE_ITEM = {431: 460, 433: 461, 435: 462, 437: 463, 439: 464, 441: 465, 451: 466, 453: 467, 455: 468,
                     457: 469, 459: 470}
# live SE a preview of an auto-play (AP) run plays: start / finish cheers, all-perfect direction
SLICE_LIVE_SE = (9, 10, 16)


# --------------------------------------------------------------------------- CRI @UTF tables
def utf_table(buf: bytes) -> list[dict]:
    """Rows of a CRI @UTF table (big endian; column storage 0x10 name flag, 0x30 constant, 0x50 per row)."""
    if buf[:4] != b"@UTF":
        raise ValueError("not an @UTF table")
    size, = struct.unpack_from(">I", buf, 4)
    t = buf[8:8 + size]
    rows_off, = struct.unpack_from(">H", t, 2)
    str_off, data_off = struct.unpack_from(">II", t, 4)
    ncol, row_w, nrow = struct.unpack_from(">HHI", t, 16)

    def cstr(o):
        return t[str_off + o:t.index(b"\0", str_off + o)].decode("utf-8")

    def value(p, typ):
        f = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f"}.get(typ)
        if f:
            return struct.unpack_from(f, t, p)[0], p + struct.calcsize(f)
        if typ == 0xA:
            return cstr(struct.unpack_from(">I", t, p)[0]), p + 4
        if typ == 0xB:
            o, n = struct.unpack_from(">II", t, p)
            return bytes(t[data_off + o:data_off + o + n]), p + 8
        raise ValueError(f"@UTF column type {typ}")

    cols, p = [], 24
    for _ in range(ncol):
        flag = t[p]
        p += 1
        name = None
        if flag & 0x10:
            name = cstr(struct.unpack_from(">I", t, p)[0])
            p += 4
        const = None
        if flag & 0xF0 == 0x30:
            const, p = value(p, flag & 0x0F)
        cols.append((name, flag & 0xF0, flag & 0x0F, const))
    rows = []
    for r in range(nrow):
        q, row = rows_off + r * row_w, {}
        for name, storage, typ, const in cols:
            if storage == 0x50:
                row[name], q = value(q, typ)
            else:
                row[name] = const
        rows.append(row)
    return rows


def _tables(buf: bytes) -> tuple[dict, dict[str, list[dict]]]:
    top = utf_table(buf)[0]
    return top, {k: utf_table(v) for k, v in top.items() if isinstance(v, bytes) and v[:4] == b"@UTF"}


def _commands(b: bytes) -> list[tuple[int, bytes]]:
    """ACB command list: repeated (u16 code, u8 size, payload)."""
    out, p = [], 0
    while p + 3 <= len(b):
        code, = struct.unpack_from(">H", b, p)
        n = b[p + 2]
        out.append((code, b[p + 3:p + 3 + n]))
        p += 3 + n
    return out


def _u16s(b: bytes) -> list[int]:
    return [struct.unpack_from(">H", b, i)[0] for i in range(0, len(b), 2)]


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
            items = [struct.unpack_from(">HH", syn["ReferenceItems"], i) for i in range(0, len(syn["ReferenceItems"]), 4)]
            if syn["Type"] != 0 or len(items) != 1 or items[0][0] != 1:
                raise ValueError(f"{name}: synth {si} type {syn['Type']} items {items}")
            w = T["WaveformTable"][items[0][1]]
            if w["Streaming"] != 0:
                raise ValueError(f"{name}: streamed waveform (memory AWB expected)")
            layers.append({"awbId": w["MemoryAwbId"], "samples": w["NumSamples"], "loopFlag": w["LoopFlag"],
                           "volume": tp["volume"] * yp["volume"], "busSends": {**tp["busSends"], **yp["busSends"]},
                           "trackOther": tp["other"], "synthOther": yp["other"]})
        out[name] = {"cueId": c["CueId"], "lengthMs": c["Length"], "categories": sp["categories"],
                     "volume": sp["volume"], "busSends": sp["busSends"], "sequenceOther": sp["other"],
                     "acbVolume": top.get("AcbVolume", 1.0), "layers": layers}
    return out


def acf_info(apk: Path) -> dict:
    """Categories, buses and REACT entries of the game's ACF (`assets/Cri/Sound/Sirius.acf` in the APK)."""
    with zipfile.ZipFile(apk) as z:
        top, T = _tables(z.read(ACF_IN_APK))
    cat_names = {r["Index"]: r["Name"] for r in T["CategoryNameTable"]}
    cats = {}
    for r in T["CategoryTable"]:
        if r["CommandIndex"] != 65535:
            raise ValueError(f"ACF category {cat_names[r['Id']]} has commands (default volume) - not handled")
        cats[cat_names[r["Id"]]] = {"id": r["Id"], "group": r["GroupIndex"]}
    bus_names = [r["StringValue"] for r in T["BusNameTable"]]
    buses = [{"name": bus_names[b["BusNameIndex"]], "volume": b["Volume"], "fx": b["NumFxs"]} for b in T["BusTable"]]
    react = [{"name": r["REACTName"], "src": cat_names[r["Src"]], "dest": cat_names[r["Dest"]], "level": r["Level"],
              "decrementMs": r["DecrementTime"], "incrementMs": r["IncrementTime"], "holdType": r["HoldType"],
              "holdMs": r["HoldTime"], "startCurve": [r["StartCurveType"], r["StartCurveStrength"]],
              "endCurve": [r["EndCurveType"], r["EndCurveStrength"]], "parameterId": r["ParameterId"],
              "targetType": r["TargetType"]} for r in T.get("ReactTable", [])]
    return {"name": top["Name"], "categories": cats, "buses": buses, "react": react}


# --------------------------------------------------------------------------- game settings
def _player_mono(player: PlayerData, cls: str) -> dict:
    hits = []
    for o in player.env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            if player.script(o)[2] == cls:
                hits.append(player.mono(o))
        except Exception:        # MonoBehaviours without a resolvable script are not ours
            continue
    if len(hits) != 1:
        raise RuntimeError(f"{cls}: {len(hits)} objects in the player data")
    return hits[0]


def category_volumes(player: PlayerData) -> dict:
    """Fresh-profile CRI category volumes: `<Cat>` = SoundVolumeSettings.DefaultVolume x 1.0
    (SoundManager.Boot ChangeVolume("All", 1)); `Live<X>Config` = LocalCacheData live volume = AppConfigDefaultData
    (AppConfig.UpdateLiveSoundVolumeConfig); Bgm/Se/VoiceConfig = system option 400-402 / 100 (preset 1: 1.0)."""
    svs = {e["CategoryName"]: e["DefaultVolume"] for e in _player_mono(player, "SoundVolumeSettings")["_collection"]}
    acd = _player_mono(player, "AppConfigDefaultData")["_defaultData"]
    live = {"LiveBgm": "LiveBgmSoundVolume", "LiveSe": "LiveSeSoundVolume", "LiveVoice": "LiveVoiceSoundVolume",
            "LiveNotesSe": "LiveNotesSeSoundVolume"}
    out = dict(svs)
    for cat, field in live.items():
        out[cat + "Config"] = acd[f"<{field}>k__BackingField"]
    return out


def _rows(master: Path, name: str) -> list[dict]:
    import json
    return json.loads((Path(master) / f"{name}.json").read_text(encoding="utf-8"))["_allData"]


def _option(rows: list[dict], preset: int, item: int) -> str:
    hit = [r["_valueString"] for r in rows if r["_presetId"] == preset and r["_optionItemType"] == item]
    if len(hit) != 1:
        raise KeyError(f"MasterOptionDefault preset {preset} item {item}: {len(hit)} rows")
    return hit[0]


def note_se_settings(master: Path, preset: int = 1) -> dict:
    """LiveSettingCreator.CreateSESettings for a fresh profile (option preset `preset`)."""
    opt = _rows(master, "MasterOptionDefault")
    if _option(opt, preset, 421).upper() != "FALSE":
        raise NotImplementedError("UseIndividualNoteSe (421) true: BuildIndividualNoteSeDictionary not implemented")
    group = int(_option(opt, preset, 420))
    types = {r["_liveNoteSeType"]: r["_seId"] for r in _rows(master, "MasterLiveNoteSe") if r["_groupID"] == group}
    volumes, mutes = {}, {}
    for t in range(1, 15):
        item = NOTE_SE_VOLUME_ITEM[t - 1]
        mute = _option(opt, preset, NOTE_SE_MUTE_ITEM[item]).upper() == "TRUE"
        v = min(max(int(_option(opt, preset, item)) / 100.0, 0.0), 1.0)
        # GetIndividualNoteSeVolume (mute -> 0) / GetNoteSeMute (GetMuteTypeForVolume)
        volumes[t], mutes[t] = (0.0 if mute else v), mute
    return {"preset": preset, "patternId": group, "useIndividualNoteSe": False,
            "types": {str(k): types[k] for k in sorted(types)}, "typeNames": NOTE_SE_TYPES,
            "volumes": {str(k): v for k, v in volumes.items()}, "mutes": {str(k): v for k, v in mutes.items()},
            "gekisouTraceVolume": min(max(int(_option(opt, preset, 459)) / 100.0, 0.0), 1.0)}


# --------------------------------------------------------------------------- extraction
def _decode(cat: Catalog, sheet: str, out_dir: Path, fmt: str) -> list[dict]:
    """Decode a cue sheet into <out>/audio/<sheet>/ (reusing an existing decode) and return its stream list."""
    d = out_dir / "audio" / sheet
    if not (d / "streams.json").exists():
        cri.decode(cat, sheet, d, fmt=fmt)
    import json
    return json.loads((d / "streams.json").read_text(encoding="utf-8"))


def _sound_entry(row: dict, sheet: str, cue: dict, streams: list[dict], cue_name: str) -> dict:
    layers = []
    for L in cue["layers"]:
        s = streams[L["awbId"]]                     # vgmstream streams follow the memory AWB order
        if s["name"] != cue_name or abs(s["samples"] - L["samples"]) > 8:
            raise RuntimeError(f"{sheet}/{cue_name}: AWB {L['awbId']} -> stream {s} does not match")
        layers.append({"file": f"audio/{sheet}/{s['file']}", "stream": s["stream"], "awbId": L["awbId"],
                       "sampleRate": s["sampleRate"], "channels": s["channels"], "samples": s["samples"],
                       "loopStart": s.get("loopStart"), "loopEnd": s.get("loopEnd"), "loopFlag": L["loopFlag"],
                       "volume": L["volume"], "busSends": L["busSends"],
                       "other": {"track": L["trackOther"], "synth": L["synthOther"]}})
    return {"row": row, "sheet": sheet, "cue": cue_name, "category": row["_category"],
            "categories": cue["categories"], "volume": cue["volume"] * cue["acbVolume"], "busSends": cue["busSends"],
            "lengthMs": cue["lengthMs"], "other": cue["sequenceOther"], "layers": layers}


def extract(cat: Catalog, master: Path, player: PlayerData, music_id: int, out_dir: Path,
            fmt: str = "flac", voice_character: int | None = None) -> dict:
    """Decode the live's sound cue sheets and write <out>/audio/live-audio.json. Returns a summary.
    voice_character: None = no character voice (the game picks the voice from the player's deck; there is no rule
    without a deck)."""
    out_dir = Path(out_dir)
    if cat.apk is None:
        raise RuntimeError("liveaudio needs the Catalog opened with apk= (ACF, HCA key)")
    sounds = {str(r["_id"]): r for r in _rows(master, "MasterSound")}
    sheets = {r["_id"]: r["_cueSheetName"] for r in _rows(master, "MasterSoundCueSheet")}
    music = [r for r in _rows(master, "MasterLiveMusic") if r["_id"] == music_id]
    if len(music) != 1:
        raise KeyError(f"MasterLiveMusic {music_id}")
    music_sound = music[0]["_musicSoundID"]
    nse = note_se_settings(master)
    live_se = {r["_liveSeType"]: r["_seId"] for r in _rows(master, "MasterLiveSe")}
    want = [music_sound] + sorted(set(nse["types"].values())) + [live_se[t] for t in SLICE_LIVE_SE]
    voice = {"decision": "caller", "character": voice_character,
             "rule": "LotteryStartVoice: random deck member, random MasterLiveStartCharacterVoice row of that "
                     "character; finish voice from LotteryFinishVoice; no deck -> undefined",
             "startVoiceSoundIds": []}
    if voice_character is not None:
        rows = [r for r in _rows(master, "MasterLiveStartCharacterVoice") if r["_characterId"] == voice_character]
        voice["startVoiceSoundIds"] = [r["_voiceSoundId"] for r in rows]
        want += voice["startVoiceSoundIds"]

    by_sheet: dict[str, list[int]] = {}
    for sid in want:
        r = sounds[str(sid)]
        by_sheet.setdefault(sheets[r["_soundCueSheetID"]], []).append(sid)
    entries, decoded = {}, {}
    for sheet, ids in by_sheet.items():
        acb, layout = cri.acb_data(cat, sheet)
        cues = acb_cues(acb["acb"])
        streams = _decode(cat, sheet, out_dir, fmt)
        decoded[sheet] = {"layout": layout, "cues": len(cues), "streams": len(streams)}
        for sid in ids:
            r = sounds[str(sid)]
            if r["_isRandomPitch"]:
                raise NotImplementedError(f"sound {sid}: random pitch")
            entries[str(sid)] = _sound_entry(r, sheet, cues[r["_cueName"]], streams, r["_cueName"])

    acf = acf_info(cat.apk)
    doc = {
        "musicId": music_id,
        "spec": "nnnotes.liveaudio",
        "sounds": entries,
        "categories": category_volumes(player),
        "acf": {"name": acf["name"], "buses": acf["buses"], "categories": acf["categories"]},
        "react": acf["react"],
        "music": {"soundId": music_sound, "volume": 1.0, "startTimeSec": 0.0},
        "noteSe": nse,
        "liveSe": {str(k): v for k, v in sorted(live_se.items())}, "liveSeNames": LIVE_SE_TYPES,
        "timeline": {"startCheerDelaySec": 1.0, "playVoiceSignalSec": 1.483333, "startCheerStopFadeSec": 1.0,
                     "finishCheerStopFadeSec": 1.0},
        "voice": voice,
        "decoded": decoded,
    }
    write_json(out_dir / "audio" / "live-audio.json", doc)
    return {"index": "audio/live-audio.json", "sheets": sorted(by_sheet), "sounds": len(entries),
            "musicSoundId": music_sound}
