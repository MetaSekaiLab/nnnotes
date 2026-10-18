"""Live products assembled from chart objects, master records and shared part manifests."""

from __future__ import annotations

import gzip

from ..source import languages
from .prefabs import Objects, key_object

LIVE_FORMAT = 1  # live.json "format"
DIFFICULTIES = ("easy", "normal", "hard", "expert")  # _easyID.._expertID -> file suffix _00.._03
# the master tables of a music record (music_record) and of a live record (live_record)
MUSIC_RECORD_TABLES = (
    "MasterLiveMusic",
    "MasterLiveMusicScore",
    "MasterText",
    "MasterBand",
    "MasterSound",
    "MasterSoundCueSheet",
)
LIVE_TABLES = (*MUSIC_RECORD_TABLES, "MasterCharacter")
# Band of the preview when no deck centre is given: the band of the music's first vocal character
# (MasterLiveMusic._vocalCharacterIDs[0] -> MasterCharacter._bandID). The music's own fields are not the game's input.
DEFAULT_BAND_RULE = "firstVocalCharacter"
BAND_NOTE = (
    "the game takes the band from the player's deck centre (SelfMemberList[2]); the preview has no "
    "deck, so the band is the preview's choice"
)
# keys the band selects (livescene.ASSET_KEYS / SPRITE_KEYS); a band without them cannot be previewed
BAND_KEYS = ("Band/{band}/live_stage/lightweight_background", "Band/{band}/timeline/live_start_playable_timeline")


def chart_key(file_name: str) -> str:
    """LiveEntry loads `Live/MusicScore/` + MasterLiveMusicScore._musicScoreTextFileName."""
    return f"Live/MusicScore/{file_name}"


def jacket_key(name: str) -> str:
    """The jacket sprite of MasterLiveMusic._jacketAssetName `name`."""
    return f"Image/Jacket/{name}"


def live_subject(music_id: int, difficulty: str, band: int | None = None) -> str:
    """The subject of the live of a music's chart at `difficulty`, with the band `band` (else the default rule)."""
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"{difficulty}: not a difficulty ({', '.join(DIFFICULTIES)})")
    return f"{music_id}/{difficulty}" + (f"/{band}" if band is not None else "")


def parse_subject(subject: str) -> tuple[int, str, int | None]:
    """(music id, difficulty, band or None) of a live subject (live_subject; ValueError for anything else)."""
    parts = subject.split("/")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts[::2]) or parts[1] not in DIFFICULTIES:
        raise ValueError(f"{subject}: not a live subject, <musicId>/<difficulty>[/<band>]")
    return int(parts[0]), parts[1], int(parts[2]) if len(parts) == 3 else None


def _rows(tables: dict, name: str) -> dict:
    return {r["_id"]: r for r in tables[name]}


def band_choice(tables: dict, music_id: int, band: int | None = None, leader_card: int | None = None) -> dict:
    """The preview's band {"band", "source", "note"} from master tables {name: rows}: the band of a leader card
    (MasterMemberCard, MasterCharacter), the band given, else the band of DEFAULT_BAND_RULE (MasterLiveMusic,
    MasterCharacter)."""
    if band is not None and leader_card is not None:
        raise ValueError("give either band or leader_card, not both")
    chars = _rows(tables, "MasterCharacter")
    if leader_card is not None:
        cards = [r for r in tables["MasterMemberCard"] if r["_id"] == leader_card]
        if len(cards) != 1:
            raise KeyError(f"MasterMemberCard {leader_card}: {len(cards)} rows")
        ch = chars.get(cards[0]["_characterID"])
        if ch is None:  # get_BandID returns -1, LiveResourceBandResolver.Resolve throws: no preview
            raise KeyError(
                f"MasterMemberCard {leader_card}: character {cards[0]['_characterID']} not in MasterCharacter"
            )
        b, source = (
            ch["_bandID"],
            f"leader card {leader_card} (MasterMemberCard -> MasterCharacter "
            f"{ch['_id']}._bandID, MasterMemberCard.get_BandID)",
        )
    elif band is not None:
        b, source = band, "band given"
    else:
        music = [r for r in tables["MasterLiveMusic"] if r["_id"] == music_id]
        if len(music) != 1:
            raise KeyError(f"MasterLiveMusic {music_id}: {len(music)} rows")
        vocals = music[0]["_vocalCharacterIDs"]
        if not vocals:
            raise ValueError(f"music {music_id}: no vocal character; give a band or a leader card")
        if vocals[0] not in chars:
            raise KeyError(f"music {music_id}: vocal character {vocals[0]} not in MasterCharacter")
        b = chars[vocals[0]]["_bandID"]
        source = (
            f"default ({DEFAULT_BAND_RULE}): MasterLiveMusic._vocalCharacterIDs[0] = {vocals[0]} -> "
            f"MasterCharacter._bandID"
        )
    return {"band": int(b), "source": source, "note": BAND_NOTE}


def music_record(tables: dict, music_id: int) -> dict:
    """MasterLiveMusic row, its 4 MasterLiveMusicScore rows, title / band name texts in every language
    (languages.LANGUAGES) and sound rows, from master tables {name: rows}."""
    music = [r for r in tables["MasterLiveMusic"] if r["_id"] == music_id]
    if len(music) != 1:
        raise KeyError(f"MasterLiveMusic {music_id}: {len(music)} rows")
    music = music[0]
    scores, text, bands = (_rows(tables, n) for n in ("MasterLiveMusicScore", "MasterText", "MasterBand"))
    sounds, sheets = _rows(tables, "MasterSound"), _rows(tables, "MasterSoundCueSheet")

    def tx(i):
        r = text.get(i)
        return None if r is None else {"id": i, **languages.texts(r)}

    def snd(i):
        r = sounds.get(i)
        if r is None:
            return None
        return {"MasterSound": r, "MasterSoundCueSheet": sheets.get(r["_soundCueSheetID"])}

    return {
        "MasterLiveMusic": music,
        "MasterLiveMusicScore": {d: scores.get(music[f"_{d}ID"]) for d in DIFFICULTIES},
        "title": tx(music["_titleTextID"]),
        "bands": [
            {"MasterBand": bands.get(b), "name": tx(bands[b]["_nameTextID"]) if b in bands else None}
            for b in music["_bandIDs"]
        ],
        "music": snd(music["_musicSoundID"]),
        "jingle": snd(music["_jingleSoundID"]),
    }


def live_record(tables: dict, subject: str) -> dict:
    """What `live` reads of the master tables `tables` (LIVE_TABLES) for a live subject: the music's rows
    (music_record), the difficulty and the band (band_choice). A difficulty the music has no chart of raises."""
    music_id, difficulty, band = parse_subject(subject)
    music = music_record(tables, music_id)
    if music["MasterLiveMusicScore"][difficulty] is None:
        raise KeyError(f"music {music_id}: no {difficulty} chart (no MasterLiveMusicScore row)")
    return {"music": music, "difficulty": difficulty, "band": band_choice(tables, music_id, band)}


def live_keys(record: dict) -> list[str]:
    """The catalog keys of a live: its chart and its jacket."""
    music = record["music"]
    score = music["MasterLiveMusicScore"][record["difficulty"]]
    return sorted(
        [chart_key(score["_musicScoreTextFileName"]), jacket_key(music["MasterLiveMusic"]["_jacketAssetName"])]
    )


def live(objects: Objects, record: dict, parts: dict) -> tuple[dict, bytes, dict[str, dict], set[str]]:
    """(live.json, chart.json, {file: JSON document} of the converted charts, the object ids the player reads) of a
    live (module documentation; `record`: live_record, `parts`: {part: manifest path})."""
    from .score import convert, load_bytes

    music, difficulty = record["music"], record["difficulty"]
    row = music["MasterLiveMusic"]
    score = music["MasterLiveMusicScore"][difficulty]
    key = chart_key(score["_musicScoreTextFileName"])
    text = key_object(objects, key, "TextAsset")
    raw = objects.bytes(text, "data")
    root = load_bytes(raw)
    source = {
        "key": key,
        "musicId": row["_id"],
        "difficulty": difficulty,
        "masterLiveMusicScoreId": score["_id"],
        "fullComboCount": score["_fullComboCount"],
    }
    notes, mirror = ({**convert(root, mirror=m), "source": source} for m in (False, True))
    doc = {
        "format": LIVE_FORMAT,
        "musicId": row["_id"],
        "difficulty": difficulty,
        "band": record["band"],
        "chart": "chart.json",
        "notes": "notes.json",
        "notesMirror": "notes.mirror.json",
        "judgementNoteCount": notes["judgementNoteCount"],
        "fullComboCount": score["_fullComboCount"],
        "noteCount": len(notes["notes"]),
        "lastNoteTimeMs": notes["lastNoteTimeMs"],
        "master": music,
        "jacket": key_object(objects, jacket_key(row["_jacketAssetName"]), "Sprite"),
        "parts": dict(sorted(parts.items())),
    }
    chart = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    keep = {o for o in objects.ids() if o != text and objects.info(o).get("class") != "MonoScript"}
    return doc, chart, {"notes.json": notes, "notes.mirror.json": mirror}, keep
