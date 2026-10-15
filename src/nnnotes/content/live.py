"""A live (a music's chart at a difficulty): the `live` product, and the self-contained live directory of `build`.

The `live` product (live.json format LIVE_FORMAT), read from a product's object table (`Objects`,
pipeline.assemble), never from a bundle, holds what belongs to one chart and names the products it plays with by
their manifest paths:

    chart.json         the chart as shipped (the TextAsset of chart_key, un-gzipped)
    notes.json         the runtime notes the game converts it to (score.convert), with `source`
    notes.mirror.json  the same with the MirrorChart option on
    live.json          musicId, difficulty, `band` (band_choice: the band whose start timeline and background the
                       live plays), the files above, the counts of the converted chart (judgementNoteCount,
                       fullComboCount, noteCount, lastNoteTimeMs), `master` (music_record: the music's rows, title
                       and band names), `jacket` (the Sprite of jacket_key) and `parts` {part: manifest path}: the
                       scene (live.scene), the note highway (live.notes) with the default note skin and effect set
                       (live.noteskin, live.noteeffect), the default sounds (live.sounds) and the music
                       (live.music)

A subject names a chart and optionally the band: `<musicId>/<difficulty>[/<band>]` (live_subject). Without a band
the live takes the band of DEFAULT_BAND_RULE.

`build` writes the live directory instead, the data format the player reads from a directory:

    <out>/score/                chart as shipped, converted runtime notes, master rows (score.py)
    <out>/audio/<cueSheet>/     the live BGM cue sheet decoded per cue + cues.json + streams.json (score.py)
    <out>/audio/live-audio.json the live's sounds (BGM, note SE, live SE) with their CRI routing, and the
                                note SE / live SE cue sheets decoded next to the BGM (liveaudio.py)
    <out>/livescene/            scene graph, cameras, lane, LightWeight background, start timeline,
                                textures and shaders (livescene.py)
    <out>/livenotes/            note / line / effect prefabs, skins, clips, particle systems,
                                textures and shaders (livenotes.py)
    <out>/liveui/               the start canvas (music title, credits, difficulty) in the client language:
                                strings, text records or the game's fonts, sprites (liveui.py)
    <out>/live.json             index of the above

`options` (liveoptions.LiveOptions, from `--live-option`) adds the files of the option variants it offers: the
mirrored score (score.py, live.json `notesMirror`), the other note skins and effect sets, the bar line view and the
option tables (livenotes.py), the other note sound sets (liveaudio.py). Without it the directory holds what the
default options read.

The band of the LightWeight background and of the start timeline is the band of the player's deck centre
(`SelfMemberList[2]`: LightWeightBackgroundLoadStep.<LoadAsync>d__3 reads its BandID, LiveResourceBandResolver.Resolve
its MemberCard's Character.Band; MasterMemberCard.get_BandID = Character._bandID). The preview has no deck:
`band_choice` takes it from a leader card or a band given, else from DEFAULT_BAND_RULE.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.jsonio import write_json
from ..formats.encoders import FLAC_LEVEL
from ..source import languages
from .liveoptions import LiveOptions
from .prefabs import Objects, key_object

if TYPE_CHECKING:
    from ..source.catalog import Catalog
    from ..unity.player import PlayerData

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


def resolve_band(
    cat: Catalog, master: Path, music_id: int, band: int | None = None, leader_card: int | None = None
) -> dict:
    """The preview's band (band_choice; recorded in livescene/scene.json slice.bandChoice) of a band whose start
    timeline and background the catalog holds (BAND_KEYS)."""
    from .score import master_table

    names = ("MasterCharacter", "MasterLiveMusic") + (("MasterMemberCard",) if leader_card is not None else ())
    choice = band_choice({n: master_table(master, n) for n in names}, music_id, band, leader_card)
    b = choice["band"]
    missing = [k.format(band=b) for k in BAND_KEYS if not cat.has(k.format(band=b))]
    if missing:
        raise KeyError(f"band {b}: {', '.join(missing)} not in the catalog")
    return choice


def index_doc(music_id: int, difficulty: str, score_summary: dict, audio: dict, live_audio: str) -> dict:
    """live.json: `score_summary` from score.extract (its `notesMirror` when the score was also converted
    mirrored), `audio` its BGM entry, `live_audio` the path of the live sounds (liveaudio.extract)."""
    s = score_summary
    return {
        "musicId": music_id,
        "difficulty": difficulty,
        "chart": s["chart"],
        "notes": s["notes"],
        **({"notesMirror": s["notesMirror"]} if "notesMirror" in s else {}),
        "master": s["master"],
        "audio": audio,
        "liveAudio": live_audio,
        "scene": "livescene/scene.json",
        "noteAssets": "livenotes/notes.json",
        "liveUi": "liveui/liveui.json",
    }


def build(
    cat: Catalog,
    master: Path,
    player: PlayerData,
    music_id: int,
    difficulty: str,
    out_dir: Path,
    audio_format: str = "flac",
    band: int | None = None,
    leader_card: int | None = None,
    *,
    language: str,
    fonts: str = "open",
    options: LiveOptions = LiveOptions(),
    flac_level: int = FLAC_LEVEL,
) -> dict:
    """One live directory. `language`: the client language of the start canvas (a languages.LANGUAGES code);
    `fonts`: "open" or "game" (liveui.extract); `options`: the option variants whose files it carries as well
    (liveoptions.resolve); `flac_level`: ffmpeg's compression level of the FLAC audio (cri.decode)."""
    from . import liveaudio, livenotes, livescene, liveui, score

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    choice = resolve_band(cat, Path(master), music_id, band=band, leader_card=leader_card)
    s = score.extract(
        cat,
        master,
        music_id,
        difficulty,
        out_dir,
        audio=True,
        audio_fmt=audio_format,
        flac_level=flac_level,
        mirror=options.mirror,
    )
    la = liveaudio.extract(
        cat,
        Path(master),
        player,
        music_id,
        out_dir,
        fmt=audio_format,  # reuses the BGM decode
        options=options,
        flac_level=flac_level,
    )
    sc = livescene.extract(
        cat, player, out_dir, master=Path(master), music_id=music_id, band=choice["band"], band_choice=choice
    )
    lu = liveui.extract(
        cat,
        player,
        out_dir,
        master=Path(master),
        music_id=music_id,  # reads livescene's shaders
        difficulty=difficulty,
        language=language,
        fonts=fonts,
    )
    nt = livenotes.extract(cat, player, out_dir, master=Path(master), options=options)
    index = index_doc(music_id, difficulty, s, s["audio"], la["index"])
    write_json(out_dir / "live.json", index)
    return {
        **index,
        "band": choice,
        "sceneSummary": sc,
        "noteAssetSummary": nt,
        "liveAudioSummary": la,
        "liveUiSummary": lu,
        "judgementNoteCount": s["judgementNoteCount"],
        "fullComboCount": s["fullComboCount"],
    }
