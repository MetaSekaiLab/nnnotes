"""Voices: every character voice the master data names, joined to its cue in the cue sheet and to the decoded
streams of the export.

Sources (the rules: `voices` in viewrules.json, docs/views.md):

    rows      a master table whose rows name a MasterSound id (the voice collection's CharacterVoiceMasterType
              tables, and MasterHomeSpot's intro voice): per row the character(s), the sound, the text, the row's
              own category enum and the listed conditions (dates, unlock ranks)
    code      MasterTitle: the characters of `_voiceCharacterIds`, each with the cue names the client builds for
              them (InitialVoicePlayer: title call and splash company voices)
    Sound     the MasterSound rows of the voice category that no other source names: their sheet and cue, and
              what the sheet and cue names suggest (`inferred`: a situation, characters by name, the rows of other
              sources in the same sheet), kept apart from the master facts
    Story     the ADV episodes of MasterAdv (`story` rules; storyvoices): per voice id of an episode command, the
              command, its speaker and line texts (the episode's -Text shard), its sound (-Sound) and cue sheet
              (-SoundCueSheet); the characters the speaker names suggest are `inferred`. The episodes, with their
              kind (the table that plays them), title and status, are the document's `episodes`

A row's sound gives its cue sheet (MasterSoundCueSheet) and cue name. The cue is looked up in the sheet's ACB
(acb.cue_streams: cue -> waveforms -> the decoder's stream numbers, from the ACB's tables, no audio decoded) and
each stream in the result of the sheet's cri.audio task (its file, sha256, rate, channels, samples).

Content (source, characters, category, sound, text, conditions) and availability are kept apart: `availability`
says whether the master data resolves the sound to a sheet and cue (`master`), whether the catalog has the sheet's
key (`catalog`) and whether the export decoded the cue's streams (`exported`); `status` is the first that fails:

    no-value      the master data does not resolve the sound (no MasterSound row, no sheet row)
    missing-key   the catalog has no key Cri/Sound/<sheet>
    not-exported  the key exists, the export has no cri.audio result for it
    unsupported   the sheet's cri.audio task could not decode it (its reason in `detail`)
    missing-cue   the sheet is decoded, its ACB has no such cue, or the cue plays none of its streams
    ok            the cue's streams, in `audio`

Gaps are the rows that are missing-key or missing-cue. Reverse coverage: the catalog keys under the rules'
prefixes whose sheet no row names, and per decoded sheet the streams no row reaches. Whether a site shows a voice
is not recorded here: sites are built separately.

The stage `view.voices` (one task per catalog subject, when master data is set and the view selected) reads the
master tables of its rules, the rules, the part of the address table it looks up ("addresses", views.Recorder), the
sheets it resolves ("sheets": sheet -> cri.audio task and ACB content), each decoded sheet's ACB ("acb:<sha256>"),
the episode objects the unity.export results hold ("episodes": asset -> part -> object, artifact and content id;
"adv:<sha256>" each content) and, in its context, the keys of the cri.audio results. Its document
(nnnotes.voices/1) is the artifact "view.voices:<subject>#view"; the `original` layout gets views/voices.json with
the file of every stream.

The command `nnnotes voices` lists, searches and fetches voices from that document or, without an export, from an
index built from the configured master data and catalog (content only; the episodes named by --episode or a row id
are read from the catalog). Nothing here imports UnityPy or numpy before a sheet is decoded or an episode loaded.
"""
from __future__ import annotations

import copy
import functools
import json
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

from . import acb, advcommand, contract, languages, storyvoices, views
from .contract import Cost, Input
from .stages import Output, Pending, Stage

SCHEMA = "nnnotes.voices/1"
VIEW = "voices"
STAGE = views.STAGE_PREFIX + VIEW
AUDIO_STAGE = "cri.audio"
EXPORT_STAGE = "unity.export"
SHEET_PREFIX = "Cri/Sound/"
STATUSES = ("ok", "missing-cue", "missing-key", "not-exported", "unsupported", "no-value")
GAP_STATUSES = ("missing-key", "missing-cue")
SOUND_SOURCE = "Sound"
STORY_SOURCE = "Story"
SPEAKER_RULE = "speaker-name"
LANGUAGES = tuple(languages.LANGUAGES)
_SOURCE_KEYS = {"name", "type", "table", "category", "voices", "names", "conditions", "characters", "sheet", "cues"}
_VOICE_KEYS = {"slot", "character", "characters", "sound", "text"}
_RULE_KEYS = {"version", "key", "prefixes", "sound", "characters", "enums", "sources", "situations", "near", "story"}
_STORY_KEYS = {"table", "asset", "title", "key", "shards", "split", "kinds"}
_KIND_KEYS = {"kind", "table", "adv", "fields", "names"}
# story part -> (the class of its object, the unity.export artifact role that holds its content)
STORY_OBJECTS = {"script": ("MonoBehaviour", "json"), "text": ("TextAsset", "data"), "sound": ("TextAsset", "data"),
                 "sheets": ("TextAsset", "data")}


# ---------------------------------------------------------------- rules
def check_rules(rules: dict) -> None:
    """Raise views.ViewError naming the first problem of the `voices` rules."""
    v = rules.get(VIEW)
    if not isinstance(v, dict):
        raise views.ViewError("view rules: no voices rules")
    if set(v) - _RULE_KEYS:
        raise views.ViewError(f"voices: unknown keys {sorted(set(v) - _RULE_KEYS)}")
    if not isinstance(v.get("version"), int) or v["version"] < 1:
        raise views.ViewError("voices: version is a positive integer")
    if views.fields(v.get("key", "")) != ["sheet"]:
        raise views.ViewError("voices: key is a template of {sheet}")
    for k in ("table", "sheets", "sheet", "sheetName", "cue", "category", "voice"):
        if k not in v.get("sound", {}):
            raise views.ViewError(f"voices.sound: no {k}")
    if "table" not in v.get("characters", {}) or "token" not in v["characters"]:
        raise views.ViewError("voices.characters: a table and a token column")
    enums = v.get("enums", {})
    names = set()
    for s in v.get("sources", []):
        where = f"voices.sources.{s.get('name')}"
        if set(s) - _SOURCE_KEYS or not s.get("name") or not s.get("table"):
            raise views.ViewError(f"{where}: keys are {', '.join(sorted(_SOURCE_KEYS))} (name and table needed)")
        if s["name"] in names or s["name"] in (SOUND_SOURCE, STORY_SOURCE):
            raise views.ViewError(f"{where}: repeated or reserved name")
        names.add(s["name"])
        cat = s.get("category")
        if cat is not None and (set(cat) != {"column", "enum"} or cat["enum"] not in enums):
            raise views.ViewError(f"{where}.category: {{column, enum}} of a known enum")
        if ("voices" in s) == ("cues" in s):
            raise views.ViewError(f"{where}: either voices or cues")
        for x in s.get("voices", []):
            if set(x) - _VOICE_KEYS or "sound" not in x or ("character" in x) == ("characters" in x):
                raise views.ViewError(f"{where}.voices: a sound and a character or characters column")
        if len(s.get("voices", [])) > 1 and len({x.get("slot") for x in s["voices"]}) != len(s["voices"]):
            raise views.ViewError(f"{where}.voices: several voices of a row need distinct slots")
        if "cues" in s:
            if not s.get("characters") or not s.get("sheet"):
                raise views.ViewError(f"{where}: cues need a characters column and a sheet")
            for c in s["cues"]:
                if set(c) != {"category", "cue"} or views.fields(c["cue"]) != ["character"]:
                    raise views.ViewError(f"{where}.cues: {{category, cue}}, the cue a template of {{character}}")
    for n in v.get("near", []):
        if n not in names:
            raise views.ViewError(f"voices.near: {n!r} is not a source")
    for s in v.get("situations", []):
        if set(s) != {"situation", "cue"}:
            raise views.ViewError("voices.situations: {situation, cue}")
        re.compile(s["cue"])
    for p in v.get("prefixes", []):
        if not isinstance(p, str) or not p:
            raise views.ViewError("voices.prefixes: non-empty strings")
    if "story" in v:
        _check_story(v["story"])


def _check_story(st) -> None:
    if not isinstance(st, dict) or set(st) - _STORY_KEYS or not all(st.get(k) for k in ("table", "asset", "key")):
        raise views.ViewError(f"voices.story: keys are {', '.join(sorted(_STORY_KEYS))} (table, asset and key "
                              f"needed)")
    if set(views.fields(st["key"])) != {"asset"}:
        raise views.ViewError("voices.story: key is a template of {asset}")
    shards = st.get("shards")
    if not isinstance(shards, dict) or set(shards) != {"text", "sound", "sheets"} or not all(
            isinstance(x, str) and x for x in shards.values()):
        raise views.ViewError("voices.story.shards: the key suffixes of text, sound and sheets")
    if not isinstance(st.get("split", ""), str):
        raise views.ViewError("voices.story.split: a string")
    kinds = set()
    for k in st.get("kinds", []):
        where = f"voices.story.kinds.{k.get('kind') if isinstance(k, dict) else k}"
        if not isinstance(k, dict) or set(k) - _KIND_KEYS or not all(k.get(x) for x in ("kind", "table", "adv")):
            raise views.ViewError(f"{where}: keys are {', '.join(sorted(_KIND_KEYS))} (kind, table and adv needed)")
        if k["kind"] in kinds:
            raise views.ViewError(f"{where}: repeated kind")
        kinds.add(k["kind"])
        for part in ("fields", "names"):
            for label, spec in (k.get(part) or {}).items():
                views._check_var(f"{where}.{part}.{label}", spec)


def load_rules(path=None) -> dict:
    """{"format", "voices"} of the view rules (the package's viewrules.json by default), checked."""
    rules = views.load_rules(path)
    out = {"format": rules["format"], VIEW: rules.get(VIEW)}
    check_rules(out)
    return out


def rules_digest(rules: dict) -> str:
    return contract.digest({"format": rules["format"], VIEW: rules[VIEW]})


def tables_of(rules: dict) -> list[str]:
    """Every master table the voices rules read, sorted."""
    v = rules[VIEW]
    out = {v["sound"]["table"], v["sound"]["sheets"], v["characters"]["table"], views.TEXT_TABLE}
    out.update(s["table"] for s in v["sources"])
    st = v.get("story")
    if st:
        out.add(st["table"])
        for k in st.get("kinds", []):
            out.add(k["table"])
            for spec in list((k.get("fields") or {}).values()) + list((k.get("names") or {}).values()):
                out.update(step.partition(".")[0] for step in views._chain(spec)[1:])
    return sorted(out)


def story_keys(rules: dict, asset: str) -> dict:
    """{part: catalog key} of an episode asset (storyvoices.PARTS)."""
    return _story_keys(rules[VIEW]["story"], asset)


def _story_keys(st: dict, asset: str) -> dict:
    key = st["key"].format(asset=asset)
    return {"script": key, **{p: key + st["shards"][p] for p in ("text", "sound", "sheets")}}


def story_assets(rules: dict, tables) -> list[str]:
    """The episode assets of the story table's rows (sorted by row id; an empty asset names none)."""
    st = rules[VIEW].get("story")
    if not st:
        return []
    rows = views._Lookup(tables).rows(st["table"])
    out = []
    for r in sorted(rows, key=lambda r: views._sort_key(r.get("_id"))):
        a = r.get(st["asset"])
        if isinstance(a, str) and a and a not in out:
            out.append(a)
    return out


# ---------------------------------------------------------------- audio of the decoded sheets
def sheet_audio(result: dict | None, cues: dict | None) -> dict:
    """What the index reads of one decoded sheet: its cri.audio result (streams by number: artifact id, file, sha256,
    size and the stream facts; status and reason) and its ACB's cue table (acb.cue_streams)."""
    if result is None:
        return {"status": "not-exported", "streams": {}, "cues": {}}
    streams = {}
    for a in result["artifacts"]:
        if a["semantics"].get("kind") != "audio.stream":
            continue
        f = dict(a["semantics"].get("facts") or {})
        n = f.pop("stream", None)
        if n is None:
            continue
        streams[int(n)] = {"artifact": a["id"], "file": contract.parse_artifact_id(a["id"])[1],
                           "sha256": a["content"]["sha256"], "size": a["content"]["size"], **f}
    item = next(iter(result["items"]), {})
    out = {"status": "unsupported" if item.get("status") == "unsupported" else "exported",
           "streams": streams, "cues": cues or {}}
    if out["status"] == "unsupported":
        out["why"] = (item.get("reason") or {}).get("code")
    return out


def _stream_entry(s: dict) -> dict:
    e = {k: s[k] for k in ("artifact", "file", "sha256", "size", "sampleRate", "channels", "samples", "loopStart",
                           "loopEnd") if k in s}
    if s.get("samples") and s.get("sampleRate"):
        e["seconds"] = round(s["samples"] / s["sampleRate"], 6)
    return e


# ---------------------------------------------------------------- the index
class _Master:
    def __init__(self, rules: dict, tables):
        self.v = rules[VIEW]
        self.look = views._Lookup(tables)
        sd = self.v["sound"]
        self.sound = {r.get("_id"): r for r in self.look.rows(sd["table"])}
        self.sheets = {r.get("_id"): r.get(sd["sheetName"]) for r in self.look.rows(sd["sheets"])}
        cd = self.v["characters"]
        self.characters = {}
        ids = {}
        for r in sorted(self.look.rows(cd["table"]), key=lambda r: views._sort_key(r.get("_id"))):
            names = {k: self.look.text(r.get(col)) for k, col in cd.get("names", {}).items()
                     if not views._empty(r.get(col))}
            ids[r.get("_id")] = str(r.get(cd["token"]) or "")
            token = ids[r.get("_id")].rsplit("_", 1)[-1]
            self.characters[r.get("_id")] = {"id": r.get("_id"), "names": names, "token": token}
        self.name_parts = _name_parts(ids)

    def enum(self, name: str, value):
        return self.v["enums"].get(name, {}).get(str(value))

    def text(self, tid):
        if views._empty(tid):
            return None
        return {"id": tid, "texts": self.look.text(tid)}


def _name_parts(ids: dict) -> dict:
    """{name part, case folded: character}: the `_` parts of each character's token column (`_nameTextID`) after
    the leading parts every character's value shares (its last part always kept); a part of several characters is
    left out."""
    split = {c: [p for p in v.split("_") if p] for c, v in ids.items()}
    lists = [v for v in split.values() if v]
    head = 0
    while lists and head < min(len(v) for v in lists) - 1 and len({v[head] for v in lists}) == 1:
        head += 1
    owners: dict = {}
    for c, parts in split.items():
        for p in parts[head:]:
            owners.setdefault(p.casefold(), set()).add(c)
    return {p: next(iter(cs)) for p, cs in owners.items() if len(cs) == 1}


class _Resolver:
    """The status, availability and audio of a (sheet, cue); records the catalog keys it looks up."""

    def __init__(self, rules: dict, index, audio: dict):
        self.key = rules[VIEW]["key"]
        self.index, self.audio = index, audio
        self.reached: dict[str, set] = {}

    def __call__(self, sheet, cue) -> dict:
        out = {"availability": {"master": sheet is not None and cue is not None, "catalog": None,
                                "exported": False}}
        if sheet is None or cue is None:
            return out
        present = self.index.objects(self.key.format(sheet=sheet)) is not None
        out["availability"]["catalog"] = present
        if not present:
            out.update(status="missing-key", detail=f"no catalog key {self.key.format(sheet=sheet)}")
            return out
        a = self.audio.get(sheet)
        if a is None or a["status"] == "not-exported":
            out["status"] = "not-exported"
            return out
        if a["status"] == "unsupported":
            out.update(status="unsupported", detail=a.get("why"))
            return out
        c = a["cues"].get(cue)
        if c is None:
            out.update(status="missing-cue", detail=f"sheet {sheet} has no cue {cue}")
            return out
        streams, problems = [], []
        for w in c["streams"]:
            s = a["streams"].get(w.get("stream"))
            if s is None:
                problems.append(f"waveform {w.get('awbId')} is not a decoded stream")
                continue
            if abs((s.get("samples") or 0) - (w.get("samples") or 0)) > 8 or s.get("sampleRate") != w.get(
                    "sampleRate") or s.get("channels") != w.get("channels"):
                problems.append(f"stream {w['stream']}: the ACB's waveform differs from the decoded stream")
            streams.append({"stream": w["stream"], **_stream_entry(s)})
            self.reached.setdefault(sheet, set()).add(w["stream"])
        if not streams:
            out.update(status="missing-cue", detail=f"cue {cue} plays no stream of sheet {sheet}")
            return out
        out["availability"]["exported"] = True
        out.update(status="ok", audio={"cueId": c.get("cueId"), "lengthMs": c.get("lengthMs"), "streams": streams})
        if problems:
            out["detail"] = "; ".join(problems)
        return out


def _sound_fields(m: _Master, sid) -> tuple[dict, str | None, str | None, str | None]:
    """(sound entry, sheet, cue, why the master data does not resolve it)."""
    sd = m.v["sound"]
    snd = m.sound.get(sid)
    if snd is None:
        return {"id": sid}, None, None, f"{sd['table']} has no row {sid}"
    sheet = m.sheets.get(snd.get(sd["sheet"]))
    entry = {"id": sid, "category": snd.get(sd["category"]), "sheet": sheet, "cue": snd.get(sd["cue"])}
    if sheet is None:
        return entry, None, None, f"{sd['sheets']} has no row {snd.get(sd['sheet'])}"
    return entry, sheet, snd.get(sd["cue"]), None


def _finish(row: dict, res: dict, why: str | None) -> dict:
    row["availability"] = res["availability"]
    if why is not None:
        row.update(status="no-value", detail=why)
        return row
    row["status"] = res["status"]
    for k in ("detail", "audio"):
        if k in res:
            row[k] = res[k]
    return row


def _category(m: _Master, s: dict, raw: dict) -> dict:
    cat = s.get("category")
    if cat is None:
        return {"name": s["name"]}
    value = raw.get(cat["column"])
    return {"name": m.enum(cat["enum"], value), "enum": cat["enum"], "value": value}


def _characters(value) -> list:
    vals = value if isinstance(value, list) else ([] if views._empty(value) else [value])
    return [v for v in vals if not views._empty(v) and v != 0]


def _row_sources(m: _Master, resolve: _Resolver, named: set) -> list[dict]:
    out = []
    for s in m.v["sources"]:
        if "voices" not in s:
            continue
        stype = s.get("type")
        for raw in sorted(m.look.rows(s["table"]), key=lambda r: views._sort_key(r.get("_id"))):
            for x in s["voices"]:
                sid = raw.get(x["sound"])
                if views._empty(sid) or sid == 0:
                    continue
                named.add(sid)
                rid = f"{s['table']}:{raw.get('_id')}" + (f":{x['slot']}" if len(s["voices"]) > 1 else "")
                source = {"name": s["name"], "table": s["table"], "row": raw.get("_id"), "column": x["sound"]}
                if stype is not None:
                    source["type"] = {"enum": "CharacterVoiceMasterType", "value": stype,
                                      "name": m.enum("CharacterVoiceMasterType", stype)}
                if len(s["voices"]) > 1:
                    source["slot"] = x["slot"]
                chars = _characters(raw.get(x["character"] if "character" in x else x["characters"]))
                sound, sheet, cue, why = _sound_fields(m, sid)
                row = {"id": rid, "source": source, "characters": chars, "category": _category(m, s, raw),
                       "sound": sound}
                text = m.text(raw.get(x["text"])) if "text" in x else None
                if text is not None:
                    row["text"] = text
                names = {k: m.look.text(raw.get(col)) for k, col in s.get("names", {}).items()
                         if not views._empty(raw.get(col))}
                if names:
                    row["names"] = names
                cond = {c: raw[c] for c in s.get("conditions", []) if c in raw and not views._empty(raw[c])}
                if cond:
                    row["conditions"] = cond
                out.append(_finish(row, resolve(sheet, cue), why))
    return out


def _code_sources(m: _Master, resolve: _Resolver) -> list[dict]:
    out = []
    for s in m.v["sources"]:
        if "cues" not in s:
            continue
        by_char: dict = {}
        for raw in sorted(m.look.rows(s["table"]), key=lambda r: views._sort_key(r.get("_id"))):
            for c in _characters(raw.get(s["characters"])):
                by_char.setdefault(c, []).append(raw.get("_id"))
        for c in sorted(by_char, key=views._sort_key):
            for pat in s["cues"]:
                cue = pat["cue"].format(character=c)
                row = {"id": f"{s['name']}:{c}:{pat['category']}",
                       "source": {"name": s["name"], "table": s["table"], "rows": by_char[c], "column": s["characters"],
                                  "cue": pat["cue"]},
                       "characters": [c], "category": {"name": pat["category"]},
                       "sound": {"id": None, "sheet": s["sheet"], "cue": cue}}
                out.append(_finish(row, resolve(s["sheet"], cue), None))
    return out


def _inferred(m: _Master, sheet: str | None, cue: str, near: dict) -> dict:
    out = {}
    for s in m.v.get("situations", []):
        if re.fullmatch(s["cue"], cue or ""):
            out["situation"] = s["situation"]
            break
    tokens = [t.casefold() for t in re.split(r"[_\W]+", cue or "") if t]
    chars = []
    for t in tokens:
        for c in m.characters.values():
            if c["token"] and c["token"].casefold() == t and c["id"] not in chars:
                chars.append(c["id"])
    if chars:
        out["characters"] = chars
    rows = near.get(sheet)
    if rows:
        out["rows"] = sorted(rows)
    return out


def _sound_rows(m: _Master, resolve: _Resolver, named: set, rows: list[dict]) -> list[dict]:
    sd = m.v["sound"]
    near: dict[str, set] = {}
    wanted = set(m.v.get("near", []))
    for r in rows:
        if r["source"]["name"] in wanted and r["sound"].get("sheet"):
            near.setdefault(r["sound"]["sheet"], set()).add(r["id"])
    out = []
    for sid in sorted((k for k, r in m.sound.items() if r.get(sd["category"]) == sd["voice"] and k not in named),
                      key=views._sort_key):
        sound, sheet, cue, why = _sound_fields(m, sid)
        row = {"id": f"{sd['table']}:{sid}", "source": {"name": SOUND_SOURCE, "table": sd["table"], "row": sid},
               "characters": [], "category": {"name": None}, "sound": sound}
        inf = _inferred(m, sheet, sound.get("cue"), near)
        if inf:
            row["inferred"] = inf
        out.append(_finish(row, resolve(sheet, cue), why))
    return out


def _kinds(m: _Master) -> dict:
    """{adv id: (kind rule, row)}: the first row (kinds in rules order, rows by id) that plays the episode."""
    out: dict = {}
    for k in m.v["story"].get("kinds", []):
        for raw in sorted(m.look.rows(k["table"]), key=lambda r: views._sort_key(r.get("_id"))):
            aid = raw.get(k["adv"])
            if not views._empty(aid) and aid != 0:
                out.setdefault(aid, (k, raw))
    return out


def _episode(m: _Master, raw: dict, kinds: dict) -> dict:
    st = m.v["story"]
    ep = {"id": raw.get("_id"), "asset": raw.get(st["asset"])}
    title = m.text(raw.get(st["title"])) if st.get("title") else None
    if title is not None:
        ep["title"] = title
    rule, row = kinds.get(ep["id"], (None, None))
    ep["kind"] = rule["kind"] if rule else None
    if rule:
        ep["table"], ep["row"] = rule["table"], row.get("_id")
        r = views._Row(row, m.look)
        fields = {}
        for label, spec in (rule.get("fields") or {}).items():
            value, _ = r.value(spec)
            if not views._empty(value):
                fields[label] = value
        if fields:
            ep["fields"] = fields
        names = views._names(r, rule.get("names") or {}, m.look)
        if names:
            ep["names"] = names
    return ep


def _speaker_matches(m: _Master, target: str) -> list[dict]:
    """The segments of a command's TargetName (split at the rules' `split`) that are, ignoring case, a name part of
    one character (_name_parts: its token or another part of its name id), with that character (rule SPEAKER_RULE),
    in order, each character once."""
    split = m.v["story"].get("split") or ""
    out = []
    for part in (target.split(split) if split else [target]):
        seg = part.strip()
        c = m.name_parts.get(seg.casefold()) if seg else None
        if c is not None and all(x["character"] != c for x in out):
            out.append({"segment": seg, "character": c})
    return out


def _unresolved(status: str, detail: str) -> dict:
    return {"availability": {"master": False, "catalog": None, "exported": False}, "status": status,
            "detail": detail}


def _story_rows(m: _Master, resolve: _Resolver, index, story: dict) -> tuple[list[dict], list[dict]]:
    """(episodes, rows) of the story source: every row of the story table, and a row per voice id of each command
    of the exported episodes (`story`: {asset: storyvoices.episode(...)})."""
    st, sd = m.v["story"], m.v["sound"]
    kinds = _kinds(m)
    episodes, rows = [], []
    for raw in sorted(m.look.rows(st["table"]), key=lambda r: views._sort_key(r.get("_id"))):
        ep = _episode(m, raw, kinds)
        episodes.append(ep)
        aid, asset = ep["id"], ep["asset"]
        ep["voices"] = 0
        if not isinstance(asset, str) or not asset:
            ep.update(status="no-value", detail=f"{st['table']} row {aid} has no {st['asset']}")
            continue
        keys = _story_keys(st, asset)
        present = {p: index.objects(k) is not None for p, k in keys.items()}
        data = story.get(asset) or {}
        if not present["script"]:
            ep.update(status="missing-key", detail=f"no catalog key {keys['script']}")
            continue
        if "why" in data:
            ep.update(status="unsupported", detail=data["why"])
            continue
        if data.get("commands") is None:
            ep["status"] = "not-exported"
            continue
        ep["status"] = "ok"

        def shard(part):
            if data.get(part) is not None:
                return data[part], None
            if not present[part]:
                return None, _unresolved("missing-key", f"no catalog key {keys[part]}")
            return None, _unresolved("not-exported", f"{keys[part]} is not exported")

        texts, _ = shard("text")
        sounds, sound_gap = shard("sound")
        sheets, sheet_gap = shard("sheets")

        def text_of(tid):
            return {"id": tid, "texts": languages.texts(texts[tid]) if texts is not None and tid in texts else None}

        for c in data["commands"]:
            vids = [v for v in c.get("VoiceIDs") or () if not views._empty(v)]
            for n, vid in enumerate(vids, 1):
                source = {"name": STORY_SOURCE, "table": st["table"], "row": aid, "command": c.get("Index"),
                          "type": {"enum": "AdvCommand", "value": c.get("Command"),
                                   "name": advcommand.name(c.get("Command"))}}
                if len(vids) > 1:
                    source["slot"] = str(n)
                if c.get("IgnoreData"):
                    source["ignoreData"] = True
                row = {"id": f"{st['table']}:{aid}:{c.get('Index')}" + (f":{n}" if len(vids) > 1 else ""),
                       "source": source, "characters": [], "category": {"name": ep["kind"]}, "sound": {"id": vid}}
                if not views._empty(c.get("AdvTextID")):
                    row["text"] = text_of(c["AdvTextID"])
                target, tids = c.get("TargetName") or "", list(c.get("TargetTextIDs") or ())
                if target or tids:
                    row["speaker"] = {"name": target, "texts": [text_of(t) for t in tids]}
                matches = _speaker_matches(m, target)
                if matches:
                    row["inferred"] = {"characters": [x["character"] for x in matches], "rule": SPEAKER_RULE,
                                       "matches": matches}
                res, why = sound_gap, None
                if res is None:
                    s = sounds.get(vid)
                    if s is None:
                        why = f"{keys['sound']} has no row {vid}"
                    else:
                        cue = s.get(sd["cue"]) or None
                        row["sound"].update(category=s.get(sd["category"]), sheet=None, cue=cue)
                        res = sheet_gap
                        if res is None:
                            sh = sheets.get(s.get(sd["sheet"]))
                            if sh is None:
                                why = f"{keys['sheets']} has no row {s.get(sd['sheet'])}"
                            elif not sh.get(sd["sheetName"]) or cue is None:
                                why = f"{keys['sound']} row {vid}: no cue sheet or cue name"
                            else:
                                row["sound"]["sheet"] = sh[sd["sheetName"]]
                                res = resolve(row["sound"]["sheet"], cue)
                rows.append(_finish(row, res if res is not None else resolve(None, None), why))
                ep["voices"] += 1
    return episodes, rows


def _coverage(v: dict, rows: list[dict], index, audio: dict, reached: dict, empty: dict,
              episodes: list[dict] | None = None) -> dict:
    sources = {}
    for s in [x["name"] for x in v["sources"]] + [SOUND_SOURCE] + ([STORY_SOURCE] if "story" in v else []):
        sources[s] = {"rows": 0, "counts": {}, "gaps": []}
        if s in empty:
            sources[s]["empty"] = empty[s]
    if "story" in v:
        eps = episodes or []
        counts: dict = {}
        for e in eps:
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        sources[STORY_SOURCE]["empty"] = sum(1 for e in eps if e["status"] == "ok" and not e["voices"])
        sources[STORY_SOURCE]["episodes"] = {
            "count": len(eps), "counts": dict(sorted(counts.items())),
            "gaps": [f"{v['story']['table']}:{e['id']}" for e in eps if e["status"] in GAP_STATUSES]}
    for r in rows:
        c = sources[r["source"]["name"]]
        c["rows"] += 1
        c["counts"][r["status"]] = c["counts"].get(r["status"], 0) + 1
        if r["status"] in GAP_STATUSES:
            c["gaps"].append(r["id"])
    for c in sources.values():
        c["counts"] = dict(sorted(c["counts"].items()))
    key = v["key"]
    head = key.split("{", 1)[0]
    keys = sorted({k for p in v.get("prefixes", []) for k in index.keys(p)})
    named = {r["sound"]["sheet"] for r in rows if r["sound"].get("sheet")}
    loose = [k for k in keys if k.startswith(head) and k[len(head):] not in named]
    sheets = sorted({k[len(head):] for k in keys if k.startswith(head)} | named)
    streams, total = {}, 0
    for sh in sheets:
        a = audio.get(sh)
        if a is None or a["status"] != "exported":
            continue
        total += len(a["streams"])
        left = [[n, a["streams"][n].get("name")] for n in sorted(a["streams"]) if n not in reached.get(sh, ())]
        if left:
            streams[sh] = left
    return {"rows": len(rows), "sources": sources,
            "reverse": {"prefixes": list(v.get("prefixes", [])), "keys": len(keys), "unreferencedKeys": loose,
                        "streams": total, "unreferencedStreams": streams}}


def build(rules: dict, tables, index, audio: dict | None = None, snapshot: dict | None = None,
          story: dict | None = None) -> dict:
    """The voices document (nnnotes.voices/1) of master `tables` (views.Tables), the address `index` (catalog
    keys; wrap it in a views.Recorder to learn what it read), `audio` ({sheet: sheet_audio(...)} of the decoded
    sheets; none: nothing exported) and `story` ({episode asset: storyvoices.episode(...)} of the exported
    episodes). `snapshot`: {"tables": {table: sha256}, "sheets": {...}, ...} to record."""
    check_rules(rules)
    audio = audio or {}
    m = _Master(rules, tables)
    resolve = _Resolver(rules, index, audio)
    named: set = set()
    rows = _row_sources(m, resolve, named)
    rows += _code_sources(m, resolve)
    empty = {}
    for s in m.v["sources"]:
        if "voices" in s:
            empty[s["name"]] = sum(1 for raw in m.look.rows(s["table"]) for x in s["voices"]
                                   if views._empty(raw.get(x["sound"])) or raw.get(x["sound"]) == 0)
    rows += _sound_rows(m, resolve, named, rows)
    episodes = None
    if "story" in m.v:
        episodes, story_rows = _story_rows(m, resolve, index, story or {})
        rows += story_rows
    used = sorted({c for r in rows for c in r["characters"] + r.get("inferred", {}).get("characters", [])},
                  key=views._sort_key)
    doc = {"schema": SCHEMA, "view": VIEW, "version": rules[VIEW]["version"], "rules": rules_digest(rules),
           "tables": tables_of(rules),
           "characters": [m.characters[c] for c in used if c in m.characters],
           "rows": rows}
    if episodes is not None:
        doc["episodes"] = episodes
    if snapshot:
        doc["snapshot"] = snapshot
    doc["coverage"] = _coverage(m.v, rows, index, audio, resolve.reached, empty, episodes)
    return doc


def gaps(doc: dict) -> int:
    """The gap rows of every source and the story episodes whose key is missing."""
    return sum(len(c["gaps"]) + len(c.get("episodes", {}).get("gaps", ())) for c in doc["coverage"]["sources"].values())


def counts(doc: dict) -> dict:
    out: dict = {}
    for r in doc["rows"]:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return dict(sorted(out.items()))


def unreferenced_streams(doc: dict) -> int:
    return sum(len(v) for v in doc["coverage"]["reverse"]["unreferencedStreams"].values())


def with_paths(doc: dict, paths) -> dict:
    """A copy of a voices document with the layout path of every stream (`paths.artifact(artifact id)`; null when
    the layout places none)."""
    out = copy.deepcopy(doc)
    for r in out["rows"]:
        for s in (r.get("audio") or {}).get("streams", []):
            s["path"] = paths.artifact(s["artifact"])
    out["layout"] = "original"
    return out


# ---------------------------------------------------------------- the stage
def _sheet_name(name: str) -> str | None:
    if name.startswith(SHEET_PREFIX):
        return name[len(SHEET_PREFIX):] or None
    return name if "/" not in name else None


_address_cache: dict = {}          # content id of an address table -> (AddressIndex, files), the latest one


def _address_parts(env, tid: str) -> tuple:
    """(AddressIndex, {serialized file: stable bundle name}) of the result of the link.addresses task `tid`."""
    sha = env.artifact(tid, "addresses")["content"]["sha256"]
    hit = _address_cache.get(sha)
    if hit is None:
        doc = contract.loads(env.store.read(sha))
        hit = (views.AddressIndex.from_addresses(doc), dict(doc.get("files") or {}))
        _address_cache.clear()
        _address_cache[sha] = hit
    return hit


class VoiceStage(Stage):
    """view.voices (module documentation). Artifact "<task id>#view" (nnnotes.voices/1); facts rows, entries (rows
    per status), gaps, unreferenced (streams of the decoded sheets no row reaches). No items."""
    name = STAGE
    after = (views.ADDRESSES_STAGE, EXPORT_STAGE, AUDIO_STAGE)

    def __init__(self, rules: dict | None = None):
        self.rules = load_rules() if rules is None else {"format": rules["format"], VIEW: rules[VIEW]}
        check_rules(self.rules)
        self.version = self.rules[VIEW]["version"]
        self.rules_bytes = contract.encode(self.rules)
        self.tables = tables_of(self.rules)
        self._memo = None

    def __getstate__(self):
        return {"rules": self.rules}

    def __setstate__(self, state):
        self.__init__(state["rules"])

    def subjects(self, env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        selected = env.facts.get("views")
        if selected is not None and VIEW not in selected:
            return []
        return sorted(env.fact("catalogs"))

    def _audio_tasks(self, env) -> list[str]:
        pending = env.pending(AUDIO_STAGE)
        if pending:
            raise Pending(pending[0])
        return env.tasks(AUDIO_STAGE)

    def _export_tasks(self, env) -> list[str]:
        pending = env.pending(EXPORT_STAGE)
        if pending:
            raise Pending(pending[0])
        return env.tasks(EXPORT_STAGE)

    def depends(self, subject: str, env) -> list[str]:
        return ([contract.task_id(views.ADDRESSES_STAGE, subject)] + self._audio_tasks(env)
                + self._plan(subject, env)[6])

    def context(self, subject: str, env) -> dict:
        sheets = self._plan(subject, env)[2]
        return {"audio": {t: env.key(t) for t in sorted({s["task"] for s in sheets.values()})}}

    def _sheets(self, env) -> tuple[dict, dict]:
        """({sheet: {"task", "acb"}}, {acb sha256: Input}) of the cri.audio results: the sheets their contents are
        known by (cristages.AudioStage.index: the catalog keys and the cue sheet names of the ACB artifacts), and
        the ACBs of the decoded ones (an unsupported sheet's ACB is not read)."""
        tids = self._audio_tasks(env)
        if not tids:
            return {}, {}
        from .cristages import AudioStage
        index = AudioStage().index(env)
        sheets, acbs = {}, {}
        for tid in tids:
            sha = contract.parse_task_id(tid, AUDIO_STAGE)[1]
            e = index.get(sha)
            if e is None:
                continue
            decoded = any(i.get("status") == "exported" for i in env.result(tid)["items"])
            for n in sorted(e["names"]):
                sheet = _sheet_name(n)
                if sheet is not None and sheet not in sheets:
                    sheets[sheet] = {"task": tid, "acb": sha}
                    if decoded:
                        a = e["acb"]
                        acbs[sha] = Input(f"acb:{sha}", a.sha256, a.size, a.name, a.locators)
        return dict(sorted(sheets.items())), acbs

    def _plan(self, subject: str, env) -> tuple:
        """(master inputs, the recorded part of the address table, {sheet: {task, acb}}, ACB inputs, {episode asset:
        {part: {object, artifact, sha256}}}, episode content inputs, the unity.export tasks read) for a task: of the
        decoded sheets, those a row names or the prefixes list (a music or effect sheet is not read); of the story
        episodes, the parts the unity.export results hold. Kept for the planning state it was made in (context,
        inputs and depends ask for it in turn)."""
        link = contract.task_id(views.ADDRESSES_STAGE, subject)
        tids = self._audio_tasks(env)
        exports = self._export_tasks(env)
        masters = self._masters(env)
        sig = (subject, env.key(link), tuple((t, env.key(t)) for t in tids), tuple((t, env.key(t)) for t in exports),
               tuple(i.sha256 for i in masters), id(env.store), id(env.facts.get("index")))
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        index, files = _address_parts(env, link)
        tables = {i.role[len(views.MASTER_ROLE):]: views._cached_rows(i.sha256, Path(i.locators[0]["path"]).read_bytes)
                  for i in masters}
        found, acbs = self._sheets(env)
        rec = views.Recorder(index)
        stub = build(self.rules, tables, rec, {s: {"status": "not-exported", "streams": {}, "cues": {}}
                                               for s in found})
        head = self.rules[VIEW]["key"].split("{", 1)[0]
        wanted = {r["sound"]["sheet"] for r in stub["rows"] if r["sound"].get("sheet")}
        wanted |= {k[len(head):] for p in self.rules[VIEW].get("prefixes", []) for k in rec.keys(p)
                   if k.startswith(head)}
        sheets = {s: v for s, v in found.items() if s in wanted}
        used = {v["acb"] for v in sheets.values()}
        episodes, contents, read = self._episodes(env, rec, tables, files, set(exports))
        plan = (masters, rec.subset(), sheets, [acbs[a] for a in sorted(acbs) if a in used], episodes, contents,
                read)
        self._memo = (sig, plan)
        return plan

    def _episodes(self, env, index, tables, files: dict, exports: set) -> tuple[dict, list[Input], list[str]]:
        """The story parts the unity.export results hold: the object a part's key names (the first of its class,
        STORY_OBJECTS) in the bundle of its serialized file (the address table's `files`), and the artifact with
        its content. ({asset: {part: {object, artifact, sha256}}}, the content inputs, the tasks read.)"""
        arts: dict[str, dict] = {}
        out: dict = {}
        contents: dict[str, Input] = {}
        read = set()
        for asset in story_assets(self.rules, tables):
            for part, key in story_keys(self.rules, asset).items():
                cls, role = STORY_OBJECTS[part]
                pick = views.choose(index.objects(key) or [], None, cls)
                if not pick:
                    continue
                oid = pick[0].id
                stable = files.get(contract.parse_object_id(oid)[0])
                tid = contract.task_id(EXPORT_STAGE, stable) if stable is not None else None
                if tid not in exports:
                    continue
                if tid not in arts:
                    arts[tid] = {a["id"]: a for a in env.result(tid)["artifacts"]}
                aid = contract.artifact_id(oid, role)
                a = arts[tid].get(aid)
                if a is None:
                    continue
                c = a["content"]
                out.setdefault(asset, {})[part] = {"object": oid, "artifact": aid, "sha256": c["sha256"]}
                contents.setdefault(c["sha256"], Input(f"adv:{c['sha256']}", c["sha256"], c["size"], None,
                                                       ({"kind": "store"},)))
                read.add(tid)
        return dict(sorted(out.items())), [contents[k] for k in sorted(contents)], sorted(read)

    def inputs(self, subject: str, env) -> list[Input]:
        masters, subset, sheets, acbs, episodes, contents, _ = self._plan(subject, env)
        store = env.store
        return (masters + [views._stored(store, "addresses", contract.encode(subset)),
                           views._stored(store, "rules", self.rules_bytes),
                           views._stored(store, "sheets", contract.encode(sheets)),
                           views._stored(store, "episodes", contract.encode(episodes))] + acbs + contents)

    def _masters(self, env) -> list[Input]:
        mdir = Path(env.fact("master"))
        out = []
        for t in self.tables:
            p = mdir / f"{t}.json"
            if not p.is_file():
                raise views.ViewError(f"view {VIEW}: master table {t} is not in the master data")
            sha, size = env.store.identify(p)
            out.append(Input(views.MASTER_ROLE + t, sha, size, p.name, ({"kind": "file", "path": str(p)},)))
        return out

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        big = max((i.size for i in inputs if i.role.startswith("acb:")), default=0)
        story = [i.size for i in inputs if i.role.startswith("adv:")]
        return Cost(0.2 + size / 300e6 + sum(story) / 80e6,
                    (96 << 20) + 4 * big + 16 * sum(i.size for i in inputs if i.role.startswith(views.MASTER_ROLE))
                    + 2 * sum(story) + 12 * max(story, default=0))

    def run(self, task, store) -> Output:
        rules = contract.loads(store.input_bytes(task.input("rules")))
        check_rules(rules)
        if rules[VIEW]["version"] != task.version:
            raise views.ViewError(f"task {task.id}: its rules are not those of view {VIEW} version {task.version}")
        masters = [i for i in task.inputs if i.role.startswith(views.MASTER_ROLE)]
        tables = {i.role[len(views.MASTER_ROLE):]: views._cached_rows(i.sha256, functools.partial(store.input_bytes, i))
                  for i in masters}
        index = views.AddressIndex.from_subset(contract.loads(store.input_bytes(task.input("addresses"))))
        sheets = contract.loads(store.input_bytes(task.input("sheets")))
        results = task.context.get("audio", {})
        roles = {i.role for i in task.inputs}
        cues: dict[str, dict] = {}
        audio = {}
        for sheet, s in sorted(sheets.items()):
            key = results.get(s["task"])
            res = store.result(key) if key is not None else None
            if res is None:
                raise views.ViewError(f"task {task.id}: no result of {s['task']}")
            role = f"acb:{s['acb']}"
            if role in roles and s["acb"] not in cues:
                cues[s["acb"]] = acb.cue_streams(store.input_bytes(task.input(role)))
            audio[sheet] = sheet_audio(res, cues.get(s["acb"]))
        snapshot = {"tables": {i.role[len(views.MASTER_ROLE):]: i.sha256 for i in masters},
                    "sheets": {k: {"task": v["task"], "acb": v["acb"], "status": audio[k]["status"]}
                               for k, v in sorted(sheets.items())}}
        story = {}
        if "episodes" in roles:
            snapshot["episodes"] = task.input("episodes").sha256
            column = rules[VIEW]["sound"]["sheet"]
            for asset, parts in sorted(contract.loads(store.input_bytes(task.input("episodes"))).items()):
                got = {p: store.input_bytes(task.input(f"adv:{x['sha256']}")) for p, x in parts.items()}
                story[asset] = storyvoices.episode(got.get("script"), got.get("text"), got.get("sound"),
                                                   got.get("sheets"), sheet_column=column)
        doc = build(rules, tables, index, audio, snapshot, story)
        content = store.add(contract.encode(doc), "json")
        facts = {"rows": len(doc["rows"]), "entries": counts(doc), "gaps": gaps(doc),
                 "unreferenced": unreferenced_streams(doc)}
        rec = contract.artifact(contract.artifact_id(task.id, views.VIEW_ROLE), content, contract.provenance(task),
                                {"kind": "view", "format": "json", "facts": facts})
        return Output([rec], [])

    def derived(self, task_id: str, result: dict, store, paths) -> list[tuple[str, bytes]]:
        """The voices document with the layout path of every stream, at views/voices.json."""
        aid = contract.artifact_id(task_id, views.VIEW_ROLE)
        rec = next((a for a in result["artifacts"] if a["id"] == aid), None)
        if rec is None:
            raise ValueError(f"result of {task_id} has no artifact {aid}")
        doc = contract.loads(store.read(rec["content"]["sha256"]))
        subject = contract.parse_task_id(task_id, self.name)[1]
        return [(views.view_path(VIEW, subject), contract.encode(with_paths(doc, paths)))]


# ---------------------------------------------------------------- queries
def _norm(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).casefold()


def _flat(s) -> str:
    return re.sub(r"[\s_\-.]+", "", _norm(s))


def character_ids(doc: dict, spec: str) -> set:
    """The ids of the document's characters that `spec` names: an id, or a part of a name in any language."""
    if spec.strip().isdigit():
        return {int(spec)}
    want = _norm(spec).strip()
    out = set()
    for c in doc["characters"]:
        texts = [c.get("token")] + [t for n in c["names"].values() for t in (n or {}).values()]
        if any(want and want in _norm(t) for t in texts if t):
            out.add(c["id"])
    return out


def _category_match(row: dict, spec: str) -> bool:
    want = _flat(spec)
    src, cat = row["source"]["name"], row["category"].get("name")
    return want in {_flat(src), _flat(cat), _flat(f"{src}.{cat}")}


def episode_ids(doc: dict, spec: str) -> set:
    """The ids of the document's episodes that `spec` names: an id, or an episode asset."""
    spec = spec.strip()
    return {e["id"] for e in doc.get("episodes", []) if str(e["id"]) == spec or e.get("asset") == spec}


def select(doc: dict, *, characters=None, category=None, source=None, status=None, text=None,
           language=None, episodes=None) -> list[dict]:
    """The rows of a document matching every given filter (characters: a set of ids, a row matches by its
    characters or, for Sound and Story rows, the inferred ones; text: a part of the row's text in `language`, every
    language when None, or of its cue name; episodes: a set of episode ids, the Story rows of those)."""
    out = []
    want = _norm(text) if text else None
    for r in doc["rows"]:
        if episodes is not None and (r["source"]["name"] != STORY_SOURCE or r["source"].get("row") not in episodes):
            continue
        if source and _flat(r["source"]["name"]) != _flat(source):
            continue
        if status and r["status"] != status:
            continue
        if category and not _category_match(r, category):
            continue
        if characters is not None:
            chars = set(r["characters"]) | set(r.get("inferred", {}).get("characters", []))
            if not chars & characters:
                continue
        if want is not None:
            texts = (r.get("text") or {}).get("texts") or {}
            pool = [texts.get(language)] if language else list(texts.values())
            pool += [r["sound"].get("cue")]
            if not any(t and want in _norm(t) for t in pool):
                continue
        out.append(r)
    return out


def rows_for(doc: dict, ident: str) -> list[dict]:
    """The rows of a row id, or of a MasterSound id (every row naming the sound)."""
    by_id = [r for r in doc["rows"] if r["id"] == ident]
    if by_id:
        return by_id
    if ident.strip().isdigit():
        n = int(ident)
        return [r for r in doc["rows"] if r["sound"].get("id") == n]
    return []


def summary(doc: dict) -> dict:
    """Rows per status, per source and category, per character; gaps both ways."""
    by_cat: dict = {}
    by_char: dict = {}
    for r in doc["rows"]:
        k = f"{r['source']['name']}.{r['category'].get('name')}"
        c = by_cat.setdefault(k, {"rows": 0, "ok": 0})
        c["rows"] += 1
        c["ok"] += r["status"] == "ok"
        for ch in r["characters"] or r.get("inferred", {}).get("characters", []) or [None]:
            x = by_char.setdefault(str(ch), {"rows": 0, "ok": 0})
            x["rows"] += 1
            x["ok"] += r["status"] == "ok"
    rev = doc["coverage"]["reverse"]
    fam: dict = {}
    for sheet, left in rev["unreferencedStreams"].items():
        f = re.sub(r"\d+", "N", sheet)
        fam[f] = fam.get(f, 0) + len(left)
    keys: dict = {}
    for k in rev["unreferencedKeys"]:
        f = re.sub(r"\d+", "N", k)
        keys[f] = keys.get(f, 0) + 1
    out = {"rows": len(doc["rows"]), "statuses": counts(doc), "gaps": gaps(doc),
           "categories": dict(sorted(by_cat.items())), "characters": dict(sorted(by_char.items())),
           "forward": {s: [r["id"] for r in doc["rows"] if r["status"] == s] for s in GAP_STATUSES},
           "reverse": {"keys": rev["keys"], "streams": rev["streams"],
                       "unreferencedKeys": dict(sorted(keys.items())),
                       "unreferencedStreams": dict(sorted(fam.items()))}}
    if "episodes" in doc:
        out["story"] = _story_summary(doc)
    return out


def _story_summary(doc: dict) -> dict:
    """Episodes per status and kind; per speaker name its rows and inferred characters; the story voices whose cue
    (sheet and cue name) another source's row also names, per source."""
    eps = doc["episodes"]
    statuses: dict = {}
    kinds: dict = {}
    for e in eps:
        statuses[e["status"]] = statuses.get(e["status"], 0) + 1
        k = kinds.setdefault(str(e.get("kind")), {"episodes": 0, "voices": 0})
        k["episodes"] += 1
        k["voices"] += e.get("voices", 0)
    others: dict = {}
    for r in doc["rows"]:
        if r["source"]["name"] != STORY_SOURCE and r["sound"].get("sheet") and r["sound"].get("cue"):
            others.setdefault((r["sound"]["sheet"], r["sound"]["cue"]), set()).add(r["source"]["name"])
    speakers: dict = {}
    shared = {"rows": 0, "cues": set(), "sources": {}}
    for r in doc["rows"]:
        if r["source"]["name"] != STORY_SOURCE:
            continue
        name = (r.get("speaker") or {}).get("name") or ""
        s = speakers.setdefault(name, {"rows": 0, "characters": []})
        s["rows"] += 1
        for c in r.get("inferred", {}).get("characters", []):
            if c not in s["characters"]:
                s["characters"].append(c)
        cue = (r["sound"].get("sheet"), r["sound"].get("cue"))
        if cue in others:
            shared["rows"] += 1
            shared["cues"].add(cue)
            for src in others[cue]:
                shared["sources"][src] = shared["sources"].get(src, 0) + 1
    shared["cues"] = len(shared["cues"])
    shared["sources"] = dict(sorted(shared["sources"].items()))
    return {"episodes": len(eps), "statuses": dict(sorted(statuses.items())), "kinds": dict(sorted(kinds.items())),
            "gaps": doc["coverage"]["sources"].get(STORY_SOURCE, {}).get("episodes", {}).get("gaps", []),
            "speakers": dict(sorted(speakers.items())), "sharedCues": shared}


# ---------------------------------------------------------------- command line
def _out(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))


def _print_json(obj) -> None:
    from .jsonio import dumps
    _out(dumps(obj, ensure_ascii=False, indent=1) + "\n")


def _label(doc: dict, ids, language: str) -> str:
    names = {c["id"]: c for c in doc["characters"]}
    out = []
    for i in ids:
        c = names.get(i)
        n = ((c or {}).get("names", {}).get("name") or {}).get(language) if c else None
        out.append(n or (c or {}).get("token") or str(i))
    return ",".join(out) or "-"


def _line(doc: dict, r: dict, language: str) -> str:
    chars = r["characters"] or r.get("inferred", {}).get("characters", [])
    text = ((r.get("text") or {}).get("texts") or {}).get(language) or ""
    cat = r["category"].get("name") or r.get("inferred", {}).get("situation") or "-"
    where = f"{r['sound'].get('sheet')}/{r['sound'].get('cue')}"
    who = _label(doc, chars, language)
    if "speaker" in r:
        names = [(t.get("texts") or {}).get(language) for t in r["speaker"]["texts"]]
        who = ",".join(n for n in names if n) or r["speaker"]["name"] or who
    return "\t".join([r["id"], who, f"{r['source']['name']}.{cat}", where, r["status"],
                      text.replace("\n", " ")]) + "\n"


def _story_ids(tables, rules: dict, specs) -> dict:
    """{episode asset: adv id} of the story table's rows that `specs` name (an id or an asset each)."""
    st = rules[VIEW]["story"]
    want = {str(s).strip() for s in specs}
    out = {}
    for r in views._Lookup(tables).rows(st["table"]):
        a = r.get(st["asset"])
        if isinstance(a, str) and a and (str(r.get("_id")) in want or a in want):
            out[a] = r.get("_id")
    return out


def _document(args, cfg, common, episodes=()) -> tuple[dict, Path | None]:
    """The document of --from, else the index of the configured master data and catalog, with the story episodes
    `episodes` (ids or assets) read from the catalog."""
    if args.source_dir:
        p = Path(args.source_dir)
        f = p if p.is_file() else p / views.view_path(VIEW, views.MAIN_SUBJECT)
        if not f.is_file():
            args.usage(f"--from {p}: no {views.view_path(VIEW, views.MAIN_SUBJECT)} (export --views voices "
                       f"writes it)")
        doc = contract.loads(f.read_bytes())
        if doc.get("schema") != SCHEMA:
            args.usage(f"{f}: not a voices document")
        return doc, (f.parent.parent if not p.is_file() else None)
    rules = load_rules()
    tables = views.MasterTables(common.master_dir(cfg))
    cat = common.open_catalog(cfg, bundles=False)
    prefixes = [SHEET_PREFIX] + ([rules[VIEW]["story"]["key"].split("{", 1)[0]] if "story" in rules[VIEW] else [])
    index = views.AddressIndex({k: [] for p in prefixes for k in cat.keys(p)})
    story = {}
    assets = _story_ids(tables, rules, episodes) if episodes and "story" in rules[VIEW] else {}
    if assets:
        full = common.open_catalog(cfg)
        column = rules[VIEW]["sound"]["sheet"]
        for asset in sorted(assets):
            got = storyvoices.load_episode(full, story_keys(rules, asset))
            story[asset] = storyvoices.episode(got.get("script"), got.get("text"), got.get("sound"),
                                               got.get("sheets"), sheet_column=column)
    return build(rules, tables, index, story=story), None


def _language(args, cfg) -> str:
    lang = getattr(args, "language", None) or cfg.get("catalog", "language") or "ja"
    if lang not in LANGUAGES:
        args.usage(f"--language {lang}: one of {', '.join(LANGUAGES)}")
    return lang


def _filters(args, doc: dict) -> dict:
    chars = None
    if getattr(args, "character", None):
        chars = character_ids(doc, args.character)
        if not chars:
            args.usage(f"--character {args.character}: no character of the master data has this id or name")
    eps = None
    if getattr(args, "episode", None):
        eps = episode_ids(doc, args.episode)
        if not eps:
            args.usage(f"--episode {args.episode}: no episode of the master data has this id or asset")
    return {"characters": chars, "category": getattr(args, "category", None), "source": getattr(args, "source", None),
            "status": getattr(args, "status", None), "episodes": eps}


def _emit(args, doc: dict, rows: list[dict], language: str) -> None:
    rows = rows[: args.limit] if args.limit else rows
    if args.json:
        _print_json(rows)
        return
    for r in rows:
        _out(_line(doc, r, language))


def _episode_args(args) -> tuple:
    return (args.episode,) if getattr(args, "episode", None) else ()


def cmd_list(args, cfg, common) -> None:
    doc, _ = _document(args, cfg, common, _episode_args(args))
    lang = _language(args, cfg)
    _emit(args, doc, select(doc, **_filters(args, doc)), lang)


def cmd_search(args, cfg, common) -> None:
    doc, _ = _document(args, cfg, common, _episode_args(args))
    lang = _language(args, cfg)
    only = args.language if args.language else None
    _emit(args, doc, select(doc, **_filters(args, doc), text=args.text, language=only), lang)


def cmd_summary(args, cfg, common) -> None:
    doc, _ = _document(args, cfg, common)
    s = summary(doc)
    if args.json:
        _print_json(s)
        return
    _out(f"rows {s['rows']}  gaps {s['gaps']}  " + "  ".join(f"{k} {v}" for k, v in s["statuses"].items()) + "\n")
    for k, v in s["categories"].items():
        _out(f"  {k}\t{v['rows']}\t{v['ok']} ok\n")
    rev = s["reverse"]
    _out(f"reverse: {rev['keys']} keys, {sum(rev['unreferencedKeys'].values())} named by no row; {rev['streams']} "
         f"decoded streams, {sum(rev['unreferencedStreams'].values())} reached by no row\n")
    if "story" in s:
        st = s["story"]
        _out(f"story: {st['episodes']} episodes (" + ", ".join(f"{k} {v}" for k, v in st["statuses"].items())
             + f"); {st['sharedCues']['rows']} voices share a cue with another source\n")
        for k, v in st["kinds"].items():
            _out(f"  {k}\t{v['episodes']} episodes\t{v['voices']} voices\n")


def _decode_one(cfg, common, sheet: str, cue: str, fmt: str, level: int, dst: Path) -> list[Path]:
    """The files of a cue's streams, decoded from its sheet alone (cri.decode, as the export decodes a sheet)."""
    from . import cri
    cfg.require_path("paths", "apk")
    cat = common.open_catalog(cfg)
    files, _ = cri.acb_data(cat, sheet)
    c = acb.cue_streams(files["acb"]).get(cue)
    if c is None:
        raise LookupError(f"sheet {sheet} has no cue {cue}")
    with tempfile.TemporaryDirectory(prefix="nnnotes-voice-") as tmp:
        cri.decode(cat, sheet, Path(tmp), fmt=fmt, flac_level=level)
        streams = {s["stream"]: s for s in json.loads((Path(tmp) / "streams.json").read_text(encoding="utf-8"))}
        out = []
        for w in c["streams"]:
            s = streams.get(w.get("stream"))
            if s is None:
                continue
            out.append(dst / s["file"])
            shutil.copyfile(Path(tmp) / s["file"], out[-1])
    return out


def cmd_get(args, cfg, common) -> None:
    head = f"{load_rules()[VIEW].get('story', {}).get('table')}:"
    story = (args.id[len(head):].split(":", 1)[0],) if args.id.startswith(head) else ()
    doc, base = _document(args, cfg, common, story)
    rows = rows_for(doc, args.id)
    if not rows:
        args.usage(f"{args.id}: no voice row or sound id of that number")
    r = next((x for x in rows if x["status"] == "ok"), rows[0])
    sheet, cue = r["sound"].get("sheet"), r["sound"].get("cue")
    dst = Path(args.out)
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    streams = (r.get("audio") or {}).get("streams", [])
    if base is not None and args.format == "flac" and streams and all(s.get("path") for s in streams):
        for s in streams:
            written.append(dst / s["file"])
            shutil.copyfile(base / s["path"], written[-1])
    elif sheet is None or cue is None or r["status"] in ("no-value", "missing-key", "missing-cue", "unsupported"):
        print(f"nnnotes: {r['id']}: {r['status']}" + (f" ({r['detail']})" if r.get("detail") else ""),
              file=sys.stderr)
        sys.exit(1)
    else:
        from .cristages import FLAC_LEVEL
        try:
            written = _decode_one(cfg, common, sheet, cue, args.format, FLAC_LEVEL, dst)
        except LookupError as e:
            print(f"nnnotes: {r['id']}: {e}", file=sys.stderr)
            sys.exit(1)
    from .jsonio import dumps
    (dst / "voice.json").write_text(dumps({"rows": rows, "files": [p.name for p in written]}, ensure_ascii=False,
                                          indent=1) + "\n", encoding="utf-8", newline="\n")
    for p in written:
        _out(f"{p}\n")
    if not written:
        sys.exit(1)


def register(sub, common) -> None:
    """The `voices` command (common: open_catalog(cfg, bundles=...), master_dir(cfg))."""
    c = sub.add_parser("voices", help="character voices of the master data and the story episodes: list, search, "
                                      "summary, get one")
    vs = c.add_subparsers(dest="voices_cmd", required=True, metavar="<voices command>")

    def base(p, text=True):
        p.add_argument("--from", dest="source_dir", metavar="OUT",
                       help="an export directory with views/voices.json (export --views voices); else the index is "
                            "built from the configured master data and catalog (no files)")
        if text:
            p.add_argument("--character", help="a MasterCharacter id or a part of a name in any language")
            p.add_argument("--category", help="a category (CharacterRankUp or character-rank-up), Source.Category "
                                              "or a source")
            p.add_argument("--source", help="a source (Talk, CharacterVoice, ..., HomeSpot, Title, Sound, Story)")
            p.add_argument("--episode", metavar="ADV", help="the Story voices of one episode: a MasterAdv id or an "
                                                            "episode asset (without --from: read from the catalog)")
            p.add_argument("--status", choices=STATUSES, help="only the rows with this status")
            p.add_argument("--limit", type=int, default=0, help="at most N rows (0: all)")
            p.add_argument("--json", action="store_true", help="the rows as JSON")

    def done(p, fn):
        p.set_defaults(func=lambda args, cfg: fn(args, cfg, common), usage=p.error)

    p = vs.add_parser("list", help="the voices matching the filters")
    base(p)
    p.add_argument("--language", choices=LANGUAGES, help="the language of the text column ([catalog] language)")
    done(p, cmd_list)
    p = vs.add_parser("search", help="the voices whose text (or cue name) contains TEXT")
    p.add_argument("text", help="text to look for")
    base(p)
    p.add_argument("--language", choices=LANGUAGES, help="search this language only (default: all five)")
    done(p, cmd_search)
    p = vs.add_parser("summary", help="rows per status, category and character; gaps both ways")
    base(p, text=False)
    p.add_argument("--json", action="store_true", help="the summary as JSON")
    done(p, cmd_summary)
    p = vs.add_parser("get", help="the files of one voice: a row id (MasterTalk:561, MasterAdv:<id>:<command>) or a "
                                  "sound id")
    p.add_argument("id", help="row id or sound id")
    p.add_argument("-o", "--out", required=True, help="output directory")
    base(p, text=False)
    p.add_argument("--format", choices=("flac", "ogg", "wav"), default="flac",
                   help="flac (default: the exported files when --from has them), ogg or wav (decodes the sheet)")
    done(p, cmd_get)
