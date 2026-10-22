"""Assemble one song's master rows and localized metadata with explicit resource identities."""

from __future__ import annotations

import copy

from ..source.languages import LANGUAGES

DIFFICULTIES = ("easy", "normal", "hard", "expert")


def find(tables: dict, name: str, identity):
    matches = [row for row in tables[name]["_allData"] if row["_id"] == identity]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected one row for ID {identity}")
    return matches[0]


def append(tables: dict, name: str, row: dict) -> None:
    rows = tables[name]["_allData"]
    if any(item["_id"] == row["_id"] for item in rows):
        raise ValueError(f"{name}: new ID conflicts with an existing row")
    rows.append(row)


def assemble(tables: dict, project: dict, chart_stats: dict) -> tuple[dict, dict]:
    tables = copy.deepcopy(tables)
    song = project["song"]
    song_id = song["id"]
    if isinstance(song_id, bool) or not isinstance(song_id, int) or not 0 < song_id <= (2**31 - 101) // 100:
        raise ValueError("song.id must allow its derived IDs to fit in a signed 32-bit integer")
    source = find(tables, "MasterLiveMusic", project["template_song"])
    row = copy.deepcopy(source)
    prefix = f"custom_{song_id}"
    full, preview = f"M_{prefix}", f"M_{prefix}_short"
    fallback = song.get("fallback_language")
    if fallback is not None and fallback not in LANGUAGES:
        raise ValueError("song.fallback_language is not a supported language")

    def text(field: str, value) -> str:
        if isinstance(value, str):
            value = {language: value for language in LANGUAGES}
        if not isinstance(value, dict) or set(value) - set(LANGUAGES):
            raise ValueError(f"song.{field}: expected text or a mapping of supported languages")
        identity = f"Custom_{song_id}_{field}"
        record = {"_id": identity}
        for language, (_, column) in LANGUAGES.items():
            record[column] = value.get(language, value.get(fallback, "") if fallback else "")
            if not isinstance(record[column], str):
                raise ValueError(f"song.{field}: localized text must be a string")
        append(tables, "MasterText", record)
        return identity

    credits = song.get("credits", {})
    display = {
        "title": song["title"],
        "phonetic": song.get("phonetic", song["title"]),
        "rubyTitle": song.get("ruby_title", song["title"]),
        "bandName": song.get("performer", ""),
        "lyricist": credits.get("lyricist", ""),
        "composer": credits.get("composer", ""),
        "arranger": credits.get("arranger", ""),
    }
    fields = {
        "_titleTextID": "title",
        "_phoneticTextID": "phonetic",
        "_rubyTitleTextID": "rubyTitle",
        "_bandNameTextID": "bandName",
        "_lyricistTextID": "lyricist",
        "_composerTextID": "composer",
        "_arrangerTextID": "arranger",
    }
    for field, name in fields.items():
        if field in row:
            row[field] = text(name, display[name])
    for field in row:
        if field.endswith("TextID") and field not in fields:
            raise ValueError(f"unsupported song text field: {field}")
    bands = song.get("bands", [])
    if not isinstance(bands, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in bands):
        raise ValueError("song.bands must be an array of band IDs")
    for band in bands:
        find(tables, "MasterBand", band)
    tags = []
    if not isinstance(song.get("tags", []), list) or len(song.get("tags", [])) > 100:
        raise ValueError("song.tags must be an array with at most 100 entries")
    for index, tag in enumerate(song.get("tags", [])):
        identity = song_id * 100 + index
        append(tables, "MasterTag", {"_id": identity, "_nameTextID": text(f"tag{index}", tag)})
        tags.append(identity)
    row.update(
        _id=song_id,
        _sortOrder=song.get("sort_order", song_id),
        _bandIDs=bands,
        _bestMusicTagIDs=tags,
        _vocalCharacterIDs=[],
        _musicVideoIDs=[],
        _anotherVocalIDs=[],
        _jacketAssetName=prefix,
        _defaultUnlock=True,
        _unlockConditionID=0,
        _startAt=song.get("start_at", "2000/01/01 0:00:00"),
    )
    names = {"jacket": prefix, "full": full, "preview": preview, "charts": {}}
    for i, difficulty in enumerate(DIFFICULTIES):
        spec = project["charts"][difficulty]
        identity = spec["score_id"]
        if isinstance(identity, bool) or not isinstance(identity, int) or not 0 < identity < 2**31:
            raise ValueError("chart score_id must be a positive signed 32-bit integer")
        score = copy.deepcopy(find(tables, "MasterLiveMusicScore", source[f"_{difficulty}ID"]))
        name = f"{prefix}/{prefix}_{i:02}"
        names["charts"][difficulty] = name
        score.update(
            _id=identity,
            _musicScoreTextFileName=name,
            _musicScoreLevel=spec["level"],
            _musicScoreDisplayLevel=spec.get("display_level", float(spec["level"])),
            _fullComboCount=chart_stats[difficulty]["notes"],
        )
        append(tables, "MasterLiveMusicScore", score)
        row[f"_{difficulty}ID"] = identity
    group = song_id
    if any(item["_group"] == group for item in tables["MasterLiveScoreRank"]["_allData"]):
        raise ValueError("new score rank group conflicts with an existing group")
    ranks = [
        item for item in tables["MasterLiveScoreRank"]["_allData"] if item["_group"] == source["_liveScoreRankGroup"]
    ]
    if not ranks:
        raise ValueError("template song has no score rank group")
    for index, item in enumerate(ranks):
        append(tables, "MasterLiveScoreRank", {**item, "_id": song_id * 100 + index, "_group": group})
    row["_liveScoreRankGroup"] = group
    for index, (field, sheet) in enumerate((("_musicSoundID", full), ("_jingleSoundID", preview))):
        sound = copy.deepcopy(find(tables, "MasterSound", source[field]))
        identity = song_id * 100 + 90 + index
        sound.update(_id=identity, _soundCueSheetID=identity, _cueName=sheet)
        for pitch_field, value in (("_isRandomPitch", False), ("_minPitch", 0.0), ("_maxPitch", 0.0)):
            if pitch_field in sound:
                sound[pitch_field] = value
        append(tables, "MasterSound", sound)
        append(tables, "MasterSoundCueSheet", {"_id": identity, "_cueSheetName": sheet})
        row[field] = identity
    for field, value in song.get("fields", {}).items():
        if (
            field not in source
            or field in fields
            or field in ("_id", "_jacketAssetName", "_liveScoreRankGroup")
            or field in row
            and (field.endswith("ID") or field.endswith("IDs"))
        ):
            raise ValueError("song.fields may only override existing presentation/behavior fields")
        row[field] = value
    append(tables, "MasterLiveMusic", row)
    return tables, names
