"""Songs: one JSON file with every live song's metadata for one master data version (format `nnnotes.songs/1`,
docs/songs.md): titles and credits in every language, bands, vocal characters, category, tags, release time, the
live BGM's length, and per difficulty the chart facts a song listing shows (level, note counts, BPM, chart times,
skill events, fever ranges).

Master data is read from the files as served, like deck data (deckdata.master_files / apk_master: SHA-256 checked
against the manifest, decoded with master.decode), so a songs file and a deck data file of the same master data
version join by `scoreId`. Charts are the TextAssets `Live/MusicScore/<file name>` converted by score.runtime_score;
the BGM length is read from the cue sheet's ACB (cue `Length` and the stream's sample count), without decoding
audio. With a jackets directory, every song's jacket (the Texture2D `Image/Jacket/<jacket>`) is written there as
`<jacket>.webp`, scaled to at most JACKET_SIZE pixels on its longer side.

The output is canonical in the same way as deck data (deckdata.encode): minified UTF-8 with one trailing LF, keys
in a fixed order, songs sorted by id, floats as the shortest decimal of their binary32 value. The file is written
only when every table, chart and cue sheet was read; any missing or unreadable input is a SongsError naming it.
"""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from pathlib import Path
from typing import Callable

from . import deckdata
from .languages import LANGUAGES

FORMAT = "nnnotes.songs/1"
DIFFICULTIES = ("easy", "normal", "hard", "expert")
MUSIC_LENGTH_TAIL_MS = 1000         # the live's music length: the last note time + 1000 ms (LiveScore skip path)

TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterText", "MasterBand", "MasterCharacter", "MasterTag",
          "MasterLiveMusicCategory", "MasterSound", "MasterSoundCueSheet")


class SongsError(ValueError):
    """An input the songs file cannot be made from (the message names it)."""


# ---------------------------------------------------------------- texts
class Texts:
    """MasterText rows by id -> {language: text} in every language (languages.LANGUAGES)."""

    def __init__(self, rows: list[dict]):
        self.rows = {r.get("_id"): r for r in rows}

    def get(self, text_id) -> dict | None:
        """{language: text} of a text id; None for an empty id; a SongsError for an id MasterText does not have."""
        if not text_id:
            return None
        r = self.rows.get(text_id)
        if r is None:
            raise SongsError(f"MasterText has no text {text_id!r}")
        return {code: r.get(col) for code, (_, col) in LANGUAGES.items()}


# ---------------------------------------------------------------- charts
def _f(v) -> float:
    return float(v)


def bpm_facts(bpm_events: list, first_ms: int, last_ms: int) -> dict:
    """{main, min, max, changes} of a chart's BPM changes [(bpm, Pos)] (tick order). `changes` lists every change
    as {timeMs, bpm}; main / min / max are taken over the played span [first_ms, last_ms] (the first and the last
    judged note): main is the BPM that holds longest in the span (the earliest on a tie), min / max the lowest and
    the highest that hold in it. A span of one instant takes the BPM at that time."""
    changes = sorted(((int(p.ms), _f(b)) for b, p in bpm_events), key=lambda x: x[0])
    if not changes:
        raise SongsError("no BPM change")
    held: dict[float, int] = {}
    order: list[float] = []
    for i, (t, b) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else None
        lo = max(t, first_ms)
        hi = last_ms if end is None else min(end, last_ms)
        at_span = (end is None or end > first_ms) and t <= last_ms
        if not at_span:
            continue
        if b not in held:
            order.append(b)
            held[b] = 0
        held[b] += max(0, hi - lo)
    if not order:                                    # every change after the span: the first one holds before it
        order, held = [changes[0][1]], {changes[0][1]: 0}
    main = max(order, key=lambda b: (held[b], -order.index(b)))
    return {"main": main, "min": min(order), "max": max(order),
            "changes": [{"timeMs": t, "bpm": b} for t, b in changes]}


def chart_facts(score_row: dict, key: str, raw: bytes) -> dict:
    """The facts of one chart: level, note counts, BPM, chart times, skill events and fevers."""
    from . import score
    try:
        root = score.load_bytes(raw)
    except (ValueError, OSError, EOFError):
        raise SongsError(f"chart {key}: not a chart (gzip or JSON cannot be read)") from None
    try:
        rs = score.runtime_score(root)
    except Exception as e:                               # the converter's own errors and malformed chart fields
        raise SongsError(f"chart {key}: cannot be converted ({type(e).__name__}: {e})") from None
    notes = rs.notes
    if not notes:
        raise SongsError(f"chart {key}: no notes")
    judged = sorted(int(n.pos.ms) for n in notes if score.is_judgement_note(n.op))
    if not judged:
        raise SongsError(f"chart {key}: no judged note")
    last = max(int(n.pos.ms) for n in notes)
    by_type = Counter(int(n.op) for n in notes)
    try:
        bpm = bpm_facts(rs.bpm_events, judged[0], judged[-1])
    except SongsError as e:
        raise SongsError(f"chart {key}: {e}") from None
    return {
        "scoreId": score_row["_id"],
        "level": score_row["_musicScoreLevel"],
        "displayLevel": score_row.get("_musicScoreDisplayLevel"),
        "fullComboCount": score_row["_fullComboCount"],
        "asset": {"key": key, "sha256": hashlib.sha256(raw).hexdigest()},
        "notes": {"judged": len(judged), "total": len(notes),
                  "byOperateType": {str(op): by_type[op] for op in sorted(by_type)}},
        "bpm": bpm,
        "firstNoteMs": judged[0],
        "lastJudgedNoteMs": judged[-1],
        "lastNoteMs": last,
        "musicLengthMs": last + MUSIC_LENGTH_TAIL_MS,
        "skillEventsMs": [int(p.ms) for _, p in rs.skills],
        "fevers": [[int(a.ms), int(b.ms)] for _, a, b in rs.fevers],
    }


# ---------------------------------------------------------------- BGM
def cue_length(acb: bytes, cue: str, where: str) -> dict:
    """{lengthMs, samples, sampleRate, durationMs} of a cue of an ACB: the CueTable `Length` and the first stream's
    sample count and rate (durationMs = samples * 1000 // sampleRate)."""
    from . import acb as acbmod
    try:
        cues = acbmod.cue_streams(acb)
    except Exception as e:
        raise SongsError(f"{where}: the ACB cannot be read ({type(e).__name__}: {e})") from None
    c = cues.get(cue)
    if c is None:
        raise SongsError(f"{where}: no cue {cue!r}")
    s = (c.get("streams") or [None])[0] or {}
    samples, rate = s.get("samples"), s.get("sampleRate")
    return {"lengthMs": c.get("lengthMs"), "samples": samples, "sampleRate": rate,
            "durationMs": samples * 1000 // rate if isinstance(samples, int) and isinstance(rate, int) and rate
            else None}


def catalog_bgm(cat) -> Callable[[str, str], dict]:
    """bgm(cue sheet, cue) for a catalog: the cue sheet's ACB (cri.acb_data) -> cue_length."""
    from . import cri

    def bgm(sheet: str, cue: str) -> dict:
        try:
            files, _ = cri.acb_data(cat, sheet)
        except KeyError:
            raise SongsError(f"cue sheet {sheet}: no such asset") from None
        except Exception as e:
            raise SongsError(f"cue sheet {sheet}: cannot be read ({type(e).__name__}: {e})") from None
        return cue_length(files["acb"], cue, f"cue sheet {sheet}")
    return bgm


# ---------------------------------------------------------------- document
JACKET_KEY = "Image/Jacket/{jacket}"
JACKET_SIZE = 320                           # longer side of a written jacket, pixels
JACKET_QUALITY = 88                         # WebP quality


def jacket_webp(image, size: int = JACKET_SIZE) -> bytes:
    """WebP bytes of a jacket image (a Pillow image), scaled down (Lanczos) to at most `size` pixels on its longer
    side; opaque images are written without alpha."""
    from PIL import Image
    img = image.convert("RGBA")
    if img.getextrema()[3][0] == 255:
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > size:
        img = img.resize((max(1, round(w * size / max(w, h))), max(1, round(h * size / max(w, h)))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=JACKET_QUALITY, method=6)
    return buf.getvalue()


def catalog_jacket(cat, size: int = JACKET_SIZE) -> Callable[[str], bytes]:
    """jacket(name) for a catalog: the WebP bytes (jacket_webp) of the Texture2D `Image/Jacket/<name>`."""
    import tempfile
    from .export import Exporter
    ex = Exporter(cat, Path(tempfile.gettempdir()), textures="deferred")

    def jacket(name: str) -> bytes:
        key = JACKET_KEY.format(jacket=name)
        try:
            o = ex.key_object(key)
        except KeyError:
            raise SongsError(f"jacket {name}: no asset {key}") from None
        except Exception as e:
            raise SongsError(f"jacket {name}: {key} cannot be read ({type(e).__name__}: {e})") from None
        if o is None or o.type.name != "Texture2D":
            raise SongsError(f"jacket {name}: {key} is not a Texture2D")
        return jacket_webp(o.read().image, size)
    return jacket


def _by_id(rows: list[dict], table: str) -> dict:
    out = {}
    for r in rows:
        if r.get("_id") in out:
            raise SongsError(f"{table}: _id {r.get('_id')} occurs twice")
        out[r.get("_id")] = r
    return out


def build(tables: dict[str, list[dict]], table_sha: dict[str, str], fetch: Callable[[str], bytes],
          bgm: Callable[[str, str], dict] | None, *, region: str, client: dict, catalog: dict, master_source: str,
          master_version: str | None) -> dict:
    """The songs document. `fetch(file name)`: a chart TextAsset's bytes (KeyError when there is none); `bgm(cue
    sheet, cue)`: the BGM length (catalog_bgm), None to leave every song's `bgm.length` null."""
    from . import __version__
    text = Texts(tables["MasterText"])
    scores = _by_id(tables["MasterLiveMusicScore"], "MasterLiveMusicScore")
    sounds = _by_id(tables["MasterSound"], "MasterSound")
    sheets = _by_id(tables["MasterSoundCueSheet"], "MasterSoundCueSheet")
    musics = sorted(_by_id(tables["MasterLiveMusic"], "MasterLiveMusic").values(), key=lambda r: r["_id"])

    bands = [{"id": b["_id"], "name": text.get(b.get("_nameTextID")), "mainColor": b.get("_mainColorCode"),
              "subColor": b.get("_subColorCode")} for b in sorted(tables["MasterBand"], key=lambda r: r["_id"])]
    characters = [{"id": c["_id"], "bandId": c.get("_bandID"), "name": text.get(c.get("_nameTextID")),
                   "shortName": text.get(c.get("_shortNameTextID")), "mainColor": c.get("_mainColorCode")}
                  for c in sorted(tables["MasterCharacter"], key=lambda r: r["_id"])]
    tags = [{"id": t["_id"], "name": text.get(t.get("_nameTextID"))}
            for t in sorted(tables["MasterTag"], key=lambda r: r["_id"])]
    categories = [{"id": c["_id"], "musicCategories": list(c.get("_musicCategories") or []),
                   "name": text.get(c.get("_textKey"))}
                  for c in sorted(tables["MasterLiveMusicCategory"], key=lambda r: r["_id"])]

    songs = []
    for m in musics:
        where = f"MasterLiveMusic {m['_id']}"
        charts = []
        for d in DIFFICULTIES:
            sid = m.get(f"_{d}ID")
            if not sid:
                continue
            row = scores.get(sid)
            if row is None:
                raise SongsError(f"{where}: {d} score {sid} is not in MasterLiveMusicScore")
            key = deckdata.chart_key(row["_musicScoreTextFileName"])
            try:
                raw = fetch(row["_musicScoreTextFileName"])
            except KeyError:
                raise SongsError(f"chart {key} (MasterLiveMusicScore {sid}): no such asset") from None
            charts.append({"difficulty": d, **chart_facts(row, key, raw)})
        snd = sounds.get(m.get("_musicSoundID"))
        if snd is None:
            raise SongsError(f"{where}: sound {m.get('_musicSoundID')} is not in MasterSound")
        sheet = sheets.get(snd.get("_soundCueSheetID"))
        if sheet is None:
            raise SongsError(f"{where}: cue sheet {snd.get('_soundCueSheetID')} is not in MasterSoundCueSheet")
        length = bgm(sheet["_cueSheetName"], snd["_cueName"]) if bgm is not None else None
        songs.append({
            "id": m["_id"],
            "sortOrder": m.get("_sortOrder"),
            "startAt": m.get("_startAt"),
            "defaultUnlock": m.get("_defaultUnlock"),
            "title": text.get(m.get("_titleTextID")),
            "ruby": text.get(m.get("_rubyTitleTextID")),
            "phonetic": text.get(m.get("_phoneticTextID")),
            "bandIds": list(m.get("_bandIDs") or []),
            "bandName": text.get(m.get("_bandNameTextID")),
            "vocalCharacterIds": list(m.get("_vocalCharacterIDs") or []),
            "lyricist": text.get(m.get("_lyricistTextID")),
            "composer": text.get(m.get("_composerTextID")),
            "arranger": text.get(m.get("_arrangerTextID")),
            "musicType": m.get("_musicType"),
            "musicCategories": list(m.get("_musicCategories") or []),
            "bestMusicTagIds": list(m.get("_bestMusicTagIDs") or []),
            "jacket": m.get("_jacketAssetName"),
            "gekisouMissions": [m.get("_gekisouMission1"), m.get("_gekisouMission2"), m.get("_gekisouMission3")],
            "bgm": {"soundId": snd["_id"], "cueSheet": sheet["_cueSheetName"], "cue": snd["_cueName"],
                    "length": length},
            "charts": charts,
            "master": {"MasterLiveMusic": m},
        })
    return {
        "format": FORMAT,
        "provenance": {
            "region": region,
            "client": {"versionName": client.get("versionName"), "versionCode": client.get("versionCode")},
            "catalog": {"resourceVersion": catalog.get("resourceVersion"), "sha256": catalog.get("sha256")},
            "master": {"source": master_source, "version": master_version,
                       "tables": {t: {"sha256": table_sha[t]} for t in TABLES}},
            "exporter": {"name": "nnnotes", "version": __version__, "chartFormat": deckdata.CHART_FORMAT},
        },
        "languages": list(LANGUAGES),
        "bands": bands,
        "characters": characters,
        "tags": tags,
        "categories": categories,
        "songs": songs,
    }


def export(out, src: deckdata.MasterSource, key, fetch: Callable[[str], bytes],
           bgm: Callable[[str, str], dict] | None, *, region: str, client: dict, catalog: dict,
           jacket: Callable[[str], bytes] | None = None, jackets_dir=None) -> dict:
    """Read the master data, every chart and every BGM cue sheet, then write the file `out` (gzip when it ends in
    `.gz`) through a temporary file and a rename; with `jacket` and `jackets_dir`, first every song's jacket as
    `<jackets_dir>/<jacket>.webp`. Returns the summary."""
    from .cache import write_atomic
    out = Path(out)
    try:
        tables, shas = deckdata.read_master(src, key, TABLES)
    except deckdata.DeckDataError as e:
        raise SongsError(str(e)) from None
    doc = build(tables, shas, fetch, bgm, region=region, client=client, catalog=catalog, master_source=src.source,
                master_version=src.version)
    jackets = sorted({s["jacket"] for s in doc["songs"] if s["jacket"]}) if jacket is not None else []
    images = {name: jacket(name) for name in jackets}
    if jackets:
        d = Path(jackets_dir)
        d.mkdir(parents=True, exist_ok=True)
        for name, data in images.items():
            write_atomic(d / f"{name}.webp", data)
    try:
        data = deckdata.encode(deckdata._value(doc, "songs"))
    except deckdata.DeckDataError as e:
        raise SongsError(str(e)) from None
    written = deckdata.file_bytes(data, out.name.endswith(".gz"))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, written)
    return {"out": str(out), "format": FORMAT, "region": region, "masterSource": src.source,
            "masterVersion": src.version, "songs": len(doc["songs"]),
            "charts": sum(len(s["charts"]) for s in doc["songs"]), "bgm": bgm is not None,
            "jackets": len(jackets),
            "bytes": len(data), "fileBytes": len(written), "sha256": hashlib.sha256(written).hexdigest()}
