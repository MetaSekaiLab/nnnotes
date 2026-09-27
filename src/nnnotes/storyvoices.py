"""The story episodes as the voices index reads them: an episode's voice commands and the rows of its shards they
name, from the exported objects (the script's MonoBehaviour JSON, the shards' TextAsset bytes) or, for one episode
at the command line, from the catalog.

An episode (catalog key `Adv/Episode/<asset>/<asset>`, the voices rules' `story.key`) is an AdvEpisodeCollection
MonoBehaviour whose `Collection` is the command list; its shards are TextAssets of JSON `{"_header", "_allData"}`
at the same key with a suffix: `-Text` (text rows in the five languages, MasterText columns), `-Sound` (sound
rows, MasterSound columns) and `-SoundCueSheet` (cue sheet rows, MasterSoundCueSheet columns). `episode` keeps of
them what the voices index uses: the commands with voice ids and the rows those name, so an index of every episode
holds these parts only. Nothing here imports UnityPy before an episode is loaded from the catalog.
"""
from __future__ import annotations

import json

PARTS = ("script", "text", "sound", "sheets")
COMMAND_FIELDS = ("Index", "Command", "TargetName", "TargetTextIDs", "AdvTextID", "VoiceIDs", "IgnoreData")


class StoryError(ValueError):
    """Episode data that is not of the form above."""


def shard_rows(data: bytes) -> list[dict]:
    """The rows (`_allData`) of a shard's bytes."""
    try:
        doc = json.loads(bytes(data).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise StoryError(f"not a shard: {e}") from None
    rows = doc.get("_allData") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise StoryError("not a shard: no _allData list")
    return rows


def voice_commands(script: dict) -> list[dict]:
    """The commands of an episode script (its MonoBehaviour's fields) that carry voice ids, in the stored order,
    with the fields the index reads."""
    rows = script.get("Collection")
    if not isinstance(rows, list):
        raise StoryError("the script has no Collection")
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("VoiceIDs"):
            out.append({k: r[k] for k in COMMAND_FIELDS if k in r})
    return out


def _by_id(rows: list[dict], ids) -> dict:
    want = set(ids)
    return {r["_id"]: r for r in rows if isinstance(r, dict) and r.get("_id") in want}


def episode(script=None, text=None, sound=None, sheets=None, *, sheet_column: str = "_soundCueSheetID") -> dict:
    """What the index reads of one episode: {"commands", "text", "sound", "sheets"}, each None when that part is
    not given, or {"why": ...} when the script is not an episode script. `script`: the MonoBehaviour's fields (a
    dict) or its JSON bytes; the shards: TextAsset bytes. Of the shards only the rows the voice commands name are
    kept (texts of their lines and speakers, their sounds, the sheets of those sounds)."""
    if isinstance(script, (bytes, bytearray, memoryview)):
        try:
            script = json.loads(bytes(script).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return {"why": f"the script is not JSON: {e}"}
    out: dict = {"commands": None, "text": None, "sound": None, "sheets": None}
    try:
        if script is not None:
            out["commands"] = voice_commands(script)
        cmds = out["commands"] or []
        if text is not None:
            ids = [c.get("AdvTextID") for c in cmds] + [t for c in cmds for t in c.get("TargetTextIDs") or ()]
            out["text"] = _by_id(shard_rows(text), ids)
        if sound is not None:
            out["sound"] = _by_id(shard_rows(sound), [v for c in cmds for v in c.get("VoiceIDs") or ()])
        if sheets is not None:
            used = [r.get(sheet_column) for r in (out["sound"] or {}).values()]
            out["sheets"] = _by_id(shard_rows(sheets), used)
    except StoryError as e:
        return {"why": str(e)}
    return out


def load_episode(cat, keys: dict) -> dict:
    """The parts of one episode read from the catalog (`keys`: {part: catalog key}, PARTS; a key the catalog lacks
    gives no part), as `episode` takes them."""
    from . import unity
    got: dict = {}
    for part, key in keys.items():
        if not cat.has(key):
            continue
        for p in cat.fetch_key(key):
            env = unity.load(p)
            if part == "script":
                tt = unity.first_mono(env, must_have="Collection")
                if tt is not None:
                    got[part] = tt
                    break
            else:
                ta = unity.textassets(env)
                if ta:
                    got[part] = next(iter(ta.values())).encode("utf-8", "surrogateescape")
                    break
    return got
