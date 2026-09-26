"""One live (music + difficulty) -> a self-contained live directory, the data format ournotes-player reads.

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
`resolve_band` takes it from `--leader-card` / `--band`, else from DEFAULT_BAND_RULE.
"""
from __future__ import annotations

from pathlib import Path

from .catalog import Catalog
from .jsonio import write_json
from .liveoptions import LiveOptions
from .player import PlayerData
from .score import master_table
from . import liveaudio, livenotes, livescene, liveui, score

# Band of the preview when no deck centre is given: the band of the music's first vocal character
# (MasterLiveMusic._vocalCharacterIDs[0] -> MasterCharacter._bandID). The music's own fields are not the game's input.
DEFAULT_BAND_RULE = "firstVocalCharacter"
BAND_NOTE = ("the game takes the band from the player's deck centre (SelfMemberList[2]); the preview has no "
             "deck, so the band is the preview's choice")
# keys the band selects (livescene.ASSET_KEYS / SPRITE_KEYS); a band without them cannot be previewed
BAND_KEYS = ("Band/{band}/live_stage/lightweight_background", "Band/{band}/timeline/live_start_playable_timeline")


def resolve_band(cat: Catalog, master: Path, music_id: int, band: int | None = None,
                 leader_card: int | None = None) -> dict:
    """The preview's band: {"band", "source", "note"} (recorded in livescene/scene.json slice.bandChoice)."""
    if band is not None and leader_card is not None:
        raise ValueError("give either band or leader_card, not both")
    chars = {r["_id"]: r for r in master_table(master, "MasterCharacter")}
    if leader_card is not None:
        cards = [r for r in master_table(master, "MasterMemberCard") if r["_id"] == leader_card]
        if len(cards) != 1:
            raise KeyError(f"MasterMemberCard {leader_card}: {len(cards)} rows")
        ch = chars.get(cards[0]["_characterID"])
        if ch is None:          # get_BandID returns -1, LiveResourceBandResolver.Resolve throws: no preview
            raise KeyError(f"MasterMemberCard {leader_card}: character {cards[0]['_characterID']} not in MasterCharacter")
        b, source = ch["_bandID"], f"leader card {leader_card} (MasterMemberCard -> MasterCharacter "\
                                   f"{ch['_id']}._bandID, MasterMemberCard.get_BandID)"
    elif band is not None:
        b, source = band, "band given"
    else:
        music = [r for r in master_table(master, "MasterLiveMusic") if r["_id"] == music_id]
        if len(music) != 1:
            raise KeyError(f"MasterLiveMusic {music_id}: {len(music)} rows")
        vocals = music[0]["_vocalCharacterIDs"]
        if not vocals:
            raise ValueError(f"music {music_id}: no vocal character; give --band or --leader-card")
        if vocals[0] not in chars:
            raise KeyError(f"music {music_id}: vocal character {vocals[0]} not in MasterCharacter")
        b = chars[vocals[0]]["_bandID"]
        source = (f"default ({DEFAULT_BAND_RULE}): MasterLiveMusic._vocalCharacterIDs[0] = {vocals[0]} -> "
                  f"MasterCharacter._bandID")
    b = int(b)
    missing = [k.format(band=b) for k in BAND_KEYS if not cat.has(k.format(band=b))]
    if missing:
        raise KeyError(f"band {b}: {', '.join(missing)} not in the catalog")
    return {"band": b, "source": source, "note": BAND_NOTE}


def index_doc(music_id: int, difficulty: str, score_summary: dict, audio: dict, live_audio: str) -> dict:
    """live.json: `score_summary` from score.extract (its `notesMirror` when the score was also converted
    mirrored), `audio` its BGM entry, `live_audio` the path of the live sounds (liveaudio.extract)."""
    s = score_summary
    return {"musicId": music_id, "difficulty": difficulty, "chart": s["chart"], "notes": s["notes"],
            **({"notesMirror": s["notesMirror"]} if "notesMirror" in s else {}),
            "master": s["master"], "audio": audio, "liveAudio": live_audio, "scene": "livescene/scene.json",
            "noteAssets": "livenotes/notes.json", "liveUi": "liveui/liveui.json"}


def build(cat: Catalog, master: Path, player: PlayerData, music_id: int, difficulty: str,
          out_dir: Path, audio_format: str = "flac", band: int | None = None,
          leader_card: int | None = None, *, language: str, fonts: str = "open",
          options: LiveOptions = LiveOptions()) -> dict:
    """One live directory. `language`: the client language of the start canvas (a languages.LANGUAGES code);
    `fonts`: "open" or "game" (liveui.extract); `options`: the option variants whose files it carries as well
    (liveoptions.resolve)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    choice = resolve_band(cat, Path(master), music_id, band=band, leader_card=leader_card)
    s = score.extract(cat, master, music_id, difficulty, out_dir, audio=True, audio_fmt=audio_format,
                      mirror=options.mirror)
    la = liveaudio.extract(cat, Path(master), player, music_id, out_dir, fmt=audio_format,   # reuses the BGM decode
                           options=options)
    sc = livescene.extract(cat, player, out_dir, master=Path(master), music_id=music_id, band=choice["band"],
                           band_choice=choice)
    lu = liveui.extract(cat, player, out_dir, master=Path(master), music_id=music_id,   # reads livescene's shaders
                        difficulty=difficulty, language=language, fonts=fonts)
    nt = livenotes.extract(cat, player, out_dir, master=Path(master), options=options)
    index = index_doc(music_id, difficulty, s, s["audio"], la["index"])
    write_json(out_dir / "live.json", index)
    return {**index, "band": choice, "sceneSummary": sc, "noteAssetSummary": nt, "liveAudioSummary": la,
            "liveUiSummary": lu, "judgementNoteCount": s["judgementNoteCount"], "fullComboCount": s["fullComboCount"]}

