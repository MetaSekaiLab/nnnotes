"""Localized listing facts from master tables and product documents."""

from pathlib import Path

from ..source import languages, master
from .advcommand import name as command_name
from .live2d import LIVE2D_PREFIX

COSTUME_TABLE = "MasterCharacterCostume"


def optional_model_names(master_dir, language):
    if master_dir is None:
        return {}
    try:
        return model_names(master_dir, language)
    except FileNotFoundError:
        return {}


def all_pairs(master_dir):
    from .score import DIFFICULTIES

    scores = {r["_id"] for r in master.table(master_dir, "MasterLiveMusicScore")}
    return [
        (m["_id"], d)
        for m in sorted(master.table(master_dir, "MasterLiveMusic"), key=lambda r: r["_id"])
        for d in DIFFICULTIES
        if m.get(f"_{d}ID") in scores
    ]


def chart(live, music, language):
    rows, difficulty = live["master"], live["difficulty"]
    row, score = rows["MasterLiveMusic"], rows["MasterLiveMusicScore"][difficulty]
    title = rows.get("title") or {}
    bands = [b.get("name") or {} for b in rows.get("bands") or []]
    layer = music["sounds"][str(music["music"]["soundId"])]["layers"][0]
    return {
        "musicId": live["musicId"],
        "difficulty": difficulty,
        "title": title.get(language),
        "bands": [b.get(language) for b in bands],
        "language": language,
        "titles": {c: title[c] for c in languages.LANGUAGES if isinstance(title.get(c), str)},
        "bandNames": {
            c: [b[c] for b in bands] for c in languages.LANGUAGES if all(isinstance(b.get(c), str) for b in bands)
        },
        "bandIds": list(row.get("_bandIDs") or []),
        "stageBand": live["band"]["band"],
        "level": score["_musicScoreLevel"],
        "displayLevel": score["_musicScoreDisplayLevel"],
        "notes": live["judgementNoteCount"],
        "fullComboCount": live["fullComboCount"],
        "durationMs": layer["samples"] * 1000 // layer["sampleRate"],
        "sortOrder": row.get("_sortOrder"),
    }


def story(episode, groups, selected_languages, language, player_settings):
    title, row = episode.get("title") or {}, episode["master"]
    commands = {"TalkWindow", *(c["cmd"] for c in episode["commands"] if not c.get("IgnoreData"))}
    for field in ["_initializeEpisodes", "_finalizeEpisodes"]:
        commands.update(command_name(r["Command"]) for r in player_settings[field])
    return {
        "advId": episode["advId"],
        "asset": episode["asset"],
        "sheetName": row.get("_sheetName", ""),
        "playbackMode": row["_playbackMode"],
        "titles": {
            c: title[languages.column(c)[1:]]
            for c in languages.LANGUAGES
            if isinstance(title.get(languages.column(c)[1:]), str) and title[languages.column(c)[1:]]
        },
        "groups": groups,
        "commands": sorted(commands),
        "commandCount": episode["commandCount"],
        "language": language,
        "languages": list(selected_languages),
    }


def story_groups(master_dir: Path) -> dict[int, list[dict]]:
    """{advId: [group]}: the rows of the story tables that name an episode (docs/story-data-format.md, story
    facts), in table order (MasterStoryEpisode, MasterStoryFriendshipEpisode, MasterStoryHomeSpotTapTalkEpisode,
    MasterStoryLiveResultEpisode, MasterHomeSpot), rows of a table by id. Names in every language with a text;
    a table the master data lacks gives no groups."""
    md = Path(master_dir)

    def table(name: str) -> list[dict]:
        try:
            return sorted(master.table(md, name), key=lambda r: r["_id"])
        except FileNotFoundError:
            return []

    texts = {r["_id"]: r for r in table("MasterText")}

    def names(text_id) -> dict:
        row = texts.get(text_id)
        return {c: t for c, t in languages.texts(row).items() if isinstance(t, str) and t} if row else {}

    chars = {r["_id"]: r for r in table("MasterCharacter")}

    def character(cid: int) -> dict:
        return {"id": cid, "names": names((chars.get(cid) or {}).get("_nameTextID"))}

    chapters = {r["_id"]: r for r in table("MasterStoryChapter")}
    friendships = {r["_id"]: r for r in table("MasterCharacterFriendship")}
    spots = {r["_id"]: r for r in table("MasterHomeSpot")}

    def spot(sid: int) -> dict:
        return {"id": sid, "names": names((spots.get(sid) or {}).get("_nameTextId"))}

    out: dict[int, list[dict]] = {}
    for r in table("MasterStoryEpisode"):
        ch = chapters.get(r["_chapterId"]) or {}
        g = {
            "kind": "chapter",
            "id": r["_id"],
            "episodeNumber": r["_episodeNumber"],
            "chapter": {
                "id": r["_chapterId"],
                "names": names(ch.get("_nameTextId")),
                "bandId": ch.get("_bandId", 0),
                "special": bool(ch.get("_isSpecialStory")),
            },
            "characters": [character(r["_characterId"])] if r.get("_characterId") else [],
        }
        out.setdefault(r["_advId"], []).append(g)
    for r in table("MasterStoryFriendshipEpisode"):
        f = friendships.get(r["_characterFriendshipId"]) or {}
        ids = [f[k] for k in ("_masterCharacterIdA", "_masterCharacterIdB") if f.get(k)]
        out.setdefault(r["_advId"], []).append(
            {
                "kind": "friendship",
                "id": r["_id"],
                "episodeNumber": r["_episodeNumber"],
                "characters": [character(c) for c in ids],
            }
        )
    for r in table("MasterStoryHomeSpotTapTalkEpisode"):
        out.setdefault(r["_advId"], []).append(
            {
                "kind": "spotTalk",
                "id": r["_id"],
                "spot": spot(r["_spotId"]),
                "characters": [character(r["_characterId"])] if r.get("_characterId") else [],
            }
        )
    for r in table("MasterStoryLiveResultEpisode"):
        out.setdefault(r["_advId"], []).append(
            {"kind": "liveResult", "id": r["_id"], "characters": [character(c) for c in r.get("_characterIds") or []]}
        )
    for r in table("MasterHomeSpot"):
        if r.get("_advId"):
            out.setdefault(r["_advId"], []).append(
                {"kind": "spot", "id": r["_id"], "spot": spot(r["_id"]), "characters": []}
            )
    return out


def model_names(master_dir: Path, language: str | None = None) -> dict[str, dict]:
    """{model key: {"character", "names", "label"}} of the models the master data maps to one character: every
    MasterCharacterCostume row maps the key LIVE2D_PREFIX + `_live2dPath` to `_characterID` (the MasterCharacter id,
    `character`); `names`: {language code: the MasterText of the character's `_nameTextID`}, the languages with a
    text; `label`: the name in `language` (left out without `language` or a text in it). A key the rows map to two
    characters, or a character without a name text, is left out."""
    chars_of: dict[str, set] = {}
    for r in master.table(master_dir, COSTUME_TABLE):
        if r.get("_live2dPath") and r.get("_characterID") is not None:
            chars_of.setdefault(LIVE2D_PREFIX + r["_live2dPath"], set()).add(r["_characterID"])
    chars = {r["_id"]: r for r in master.table(master_dir, "MasterCharacter")}
    texts = {r["_id"]: r for r in master.table(master_dir, "MasterText")}
    out = {}
    for key, ids in sorted(chars_of.items()):
        if len(ids) != 1:
            continue
        (cid,) = ids
        row = texts.get((chars.get(cid) or {}).get("_nameTextID"))
        names = {c: t for c, t in languages.texts(row).items() if isinstance(t, str) and t} if row else {}
        if not names:
            continue
        entry = {"character": cid, "names": names}
        if language in names:
            entry["label"] = names[language]
        out[key] = entry
    return out
