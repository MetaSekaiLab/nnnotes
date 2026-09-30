"""Music data: one JSON file with every live song and chart of one master data version (format
`nnnotes.music-data/1`, docs/music-data.md): titles and credits in every language, bands, vocal characters, category,
tags, release time, score ranks, the live BGM's length, the Gekisou catalog (member cards, snaps and their Gekisou
skills), per difficulty the chart facts (level, note counts, BPM, chart times, skill events, fever ranges) and the
chart's deck statistics, what the chart contributes to the live score whatever the deck, with Gekisou on (a Gekisou
live, every rank) and off (a solo live), measured by the deck model ournotes-deck (the extension module
nnnotes._deck).

Master data is read from the files as served (deckdata.master_files / apk_master: SHA-256 checked against the
manifest, decoded with master.decode). Charts are the TextAssets `Live/MusicScore/<file name>` converted by
score.runtime_score; the BGM length is read from the cue sheet's ACB (cue `Length` and the stream's sample count),
without decoding audio. The deck model reads the deck input (deckdata.build: the charts' runtime notes and the master
data tables it needs) in memory; its statistics are checked against the chart facts and the master data. With `full`
the file also carries that deck input (`master`, `charts`), every chart's runtime notes and the tables. With a jackets
directory, every song's jacket (the Texture2D `Image/Jacket/<jacket>`) is written there as `<jacket>.webp`, scaled to
at most JACKET_SIZE pixels on its longer side.

The output is canonical (deckdata.encode): minified UTF-8 with one trailing LF, keys in a fixed order, songs sorted
by id, master data floats as the shortest decimal of their binary32 value, the deck model's numbers as it writes them.
The same inputs and deck model give the same bytes. The file is written only when every table, chart and cue sheet
was read and every chart measured; any missing or unreadable input is a MusicDataError naming it.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path
from typing import Callable

from . import deckdata
from .languages import LANGUAGES

FORMAT = "nnnotes.music-data/1"
DIFFICULTIES = ("easy", "normal", "hard", "expert")
MUSIC_LENGTH_TAIL_MS = 1000         # the live's music length: the last note time + 1000 ms (LiveScore skip path)

# the tables of the song metadata; the deck model's are deckdata.TABLES
SONG_TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterText", "MasterBand", "MasterCharacter",
               "MasterTag", "MasterLiveMusicCategory", "MasterSound", "MasterSoundCueSheet", "MasterLiveScoreRank")
# the tables of the Gekisou catalog (gekisou_catalog): member cards, snaps and their Gekisou (support) skills
CATALOG_TABLES = ("MasterMemberCard", "MasterSupportCard", "MasterSupportCardRank", "MasterGekisouSkill",
                  "MasterGekisouSkillEffect", "MasterGekisouSupportSkill", "MasterGekisouSupportSkillEffect")
SCORE_RANKS = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A", 6: "S", 7: "SS"}   # LiveScoreRank


class MusicDataError(ValueError):
    """An input the music data file cannot be made from (the message names it)."""


# ---------------------------------------------------------------- texts
class Texts:
    """MasterText rows by id -> {language: text} in every language (languages.LANGUAGES)."""

    def __init__(self, rows: list[dict]):
        self.rows = {r.get("_id"): r for r in rows}

    def get(self, text_id) -> dict | None:
        """{language: text} of a text id; None for an empty id; a MusicDataError for an id MasterText does not have."""
        if not text_id:
            return None
        r = self.rows.get(text_id)
        if r is None:
            raise MusicDataError(f"MasterText has no text {text_id!r}")
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
        raise MusicDataError("no BPM change")
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
        raise MusicDataError(f"chart {key}: not a chart (gzip or JSON cannot be read)") from None
    try:
        rs = score.runtime_score(root)
    except Exception as e:                               # the converter's own errors and malformed chart fields
        raise MusicDataError(f"chart {key}: cannot be converted ({type(e).__name__}: {e})") from None
    notes = rs.notes
    if not notes:
        raise MusicDataError(f"chart {key}: no notes")
    judged = sorted(int(n.pos.ms) for n in notes if score.is_judgement_note(n.op))
    if not judged:
        raise MusicDataError(f"chart {key}: no judged note")
    last = max(int(n.pos.ms) for n in notes)
    by_type = Counter(int(n.op) for n in notes)
    try:
        bpm = bpm_facts(rs.bpm_events, judged[0], judged[-1])
    except MusicDataError as e:
        raise MusicDataError(f"chart {key}: {e}") from None
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
        raise MusicDataError(f"{where}: the ACB cannot be read ({type(e).__name__}: {e})") from None
    c = cues.get(cue)
    if c is None:
        raise MusicDataError(f"{where}: no cue {cue!r}")
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
            raise MusicDataError(f"cue sheet {sheet}: no such asset") from None
        except Exception as e:
            raise MusicDataError(f"cue sheet {sheet}: cannot be read ({type(e).__name__}: {e})") from None
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
            raise MusicDataError(f"jacket {name}: no asset {key}") from None
        except Exception as e:
            raise MusicDataError(f"jacket {name}: {key} cannot be read ({type(e).__name__}: {e})") from None
        if o is None or o.type.name != "Texture2D":
            raise MusicDataError(f"jacket {name}: {key} is not a Texture2D")
        return jacket_webp(o.read().image, size)
    return jacket


def _by_id(rows: list[dict], table: str) -> dict:
    out = {}
    for r in rows:
        if r.get("_id") in out:
            raise MusicDataError(f"{table}: _id {r.get('_id')} occurs twice")
        out[r.get("_id")] = r
    return out


# ---------------------------------------------------------------- the Gekisou catalog
MISSION_LUCK, MISSION_JUST, MISSION_ALL = 2, 3, 4   # GekisouMissionType: 1 combo, 2 luck, 3 Just count, 4 all


def _max_levels(rows: list[dict], key: str) -> dict:
    """skill id -> the highest `_level` of its effect rows."""
    out: dict = {}
    for r in rows:
        out[r.get(key)] = max(out.get(r.get(key), 0), r.get("_level") or 0)
    return out


def gekisou_catalog(tables: dict[str, list[dict]], text: Texts) -> dict:
    """The Gekisou catalog `{skills, supportSkills, members, snaps}`, each sorted by id: the Gekisou skills
    (MasterGekisouSkill) and Gekisou support skills (MasterGekisouSupportSkill) with their mission and highest level
    (the highest `_level` of their effect rows, 0 without one), the member cards with their character, band and
    Gekisou skill, the snaps (MasterSupportCard) with their characters, Gekisou support skills and the level those have
    at the snap's highest rank (MasterSupportCardRank of its rank group)."""
    characters = _by_id(tables["MasterCharacter"], "MasterCharacter")
    skill_rows = _by_id(tables["MasterGekisouSkill"], "MasterGekisouSkill")
    support_rows = _by_id(tables["MasterGekisouSupportSkill"], "MasterGekisouSupportSkill")
    levels = _max_levels(tables["MasterGekisouSkillEffect"], "_gekisouSkillID")
    support_levels = _max_levels(tables["MasterGekisouSupportSkillEffect"], "_gekisouSupportSkillID")
    top: dict = {}                                      # rank group -> its highest rank row (a later row on a tie)
    for r in tables["MasterSupportCardRank"]:
        g = r.get("_group")
        if g not in top or (r.get("_rank") or 0) >= (top[g].get("_rank") or 0):
            top[g] = r

    def skill(r: dict, max_level: dict) -> dict:
        return {"id": r["_id"], "mission": r.get("_gekisouMissionType"), "maxLevel": max_level.get(r["_id"], 0),
                "name": text.get(r.get("_nameTextID")), "description": text.get(r.get("_descriptionTextFormatID"))}

    members = []
    for c in sorted(_by_id(tables["MasterMemberCard"], "MasterMemberCard").values(), key=lambda r: r["_id"]):
        where = f"MasterMemberCard {c['_id']}"
        ch = characters.get(c.get("_characterID"))
        if ch is None:
            raise MusicDataError(f"{where}: character {c.get('_characterID')} is not in MasterCharacter")
        sid = c.get("_gekisouSkillID") or None
        if sid is not None and sid not in skill_rows:
            raise MusicDataError(f"{where}: Gekisou skill {sid} is not in MasterGekisouSkill")
        members.append({"id": c["_id"], "characterId": c.get("_characterID"), "bandId": ch.get("_bandID"),
                        "rarity": c.get("_rarity"), "gekisouSkillId": sid, "name": text.get(c.get("_nameTextID")),
                        "subtitle": text.get(c.get("_subtitleTextID"))})
    snaps = []
    for s in sorted(_by_id(tables["MasterSupportCard"], "MasterSupportCard").values(), key=lambda r: r["_id"]):
        where = f"MasterSupportCard {s['_id']}"
        rank = top.get(s.get("_supportCardRankGroup"))
        if rank is None:
            raise MusicDataError(f"{where}: rank group {s.get('_supportCardRankGroup')} has no MasterSupportCardRank "
                                 f"row")
        ids, at = [], []
        for n in (1, 2):
            i = s.get(f"_gekisouSupportSkillId0{n}")
            if i:
                if i not in support_rows:
                    raise MusicDataError(f"{where}: Gekisou support skill {i} is not in MasterGekisouSupportSkill")
                ids.append(i)
                at.append(rank.get(f"_gekisouSupportSkill0{n}Level"))
        if len(set(at)) > 1:
            raise MusicDataError(f"{where}: its Gekisou support skills {ids} have the levels {at} at the highest "
                                 f"rank; the catalog has one level per snap")
        snaps.append({"id": s["_id"], "characterIds": list(s.get("_characterIDs") or []), "rarity": s.get("_rarity"),
                      "gekisouSupportSkillIds": ids,
                      "supportSkillLevel": at[0] if at else rank.get("_gekisouSupportSkill01Level"),
                      "name": text.get(s.get("_nameTextID")), "subtitle": text.get(s.get("_descriptionTextID"))})
    return {"skills": [skill(r, levels) for r in sorted(skill_rows.values(), key=lambda r: r["_id"])],
            "supportSkills": [skill(r, support_levels) for r in sorted(support_rows.values(), key=lambda r: r["_id"])],
            "members": members, "snaps": snaps}


# ---------------------------------------------------------------- the deck model
DECK_SEEDS = 8                              # seed set size of a chart with a luck range (ournotes-deck's default)


class Deck:
    """The deck model (nnnotes._deck): chart statistics of a deck input document, on `workers` threads (None: the
    available parallelism), `seeds` seeds for a chart with a luck range; with `aptitude` every chart's Gekisou aptitude
    too, a variant on at most `aptitude_max_seeds` seeds and its cross terms on at most `aptitude_cross_seeds` (None:
    the deck model's defaults)."""

    def __init__(self, seeds: int = DECK_SEEDS, workers: int | None = None, module=None, aptitude: bool = True,
                 aptitude_max_seeds: int | None = None, aptitude_cross_seeds: int | None = None,
                 require_convergence: bool = True):
        if module is None:
            try:
                from . import _deck as module
            except ImportError:
                raise MusicDataError("the deck model (nnnotes._deck) is not built into this installation: install "
                                     "nnnotes from a wheel or build it (maturin), or pass --no-deck") from None
        self.module, self.seeds, self.workers = module, seeds, workers
        self.aptitude, self.aptitude_max_seeds, self.aptitude_cross_seeds = \
            aptitude, aptitude_max_seeds, aptitude_cross_seeds
        self.require_convergence = require_convergence

    def info(self) -> dict:
        """{name, version, source, commit, format} of the deck model."""
        i = self.module.info()
        return {k: i[k] for k in ("name", "version", "source", "commit", "format")}

    def stats(self, deck_input: dict) -> dict:
        """The chart statistics document of a deck input document (deckdata.build), its numbers as written."""
        try:
            text = self.module.chart_stats(deckdata.encode(deck_input).decode("utf-8"), self.seeds, self.workers,
                                           aptitude=self.aptitude, aptitude_max_seeds=self.aptitude_max_seeds,
                                           aptitude_cross_seeds=self.aptitude_cross_seeds)
        except ValueError as e:
            raise MusicDataError(f"deck model: {e}") from None
        doc = json.loads(text, parse_float=deckdata._Num)
        if doc.get("format") != self.info()["format"]:
            raise MusicDataError(f"deck model: wrote {doc.get('format')!r}, expected {self.info()['format']!r}")
        if self.require_convergence and self.aptitude:
            unmet = [(c.get("scoreId"), v.get("shape"), v.get("bandMatch"), v.get("seeds"))
                     for c in doc.get("charts", [])
                     for v in (c.get("gekisouAptitude") or {}).get("variants", [])
                     if v.get("seTargetMet") is not True]
            if unmet:
                chart, shape, band, seeds = unmet[0]
                raise MusicDataError(f"deck model: {len(unmet)} aptitude variants did not meet BOTH score SE targets; "
                                     f"chart {chart}, shape {shape}, bandMatch {band}, seeds {seeds}; "
                                     "increase --aptitude-max-seeds; no final artifact written")
        return doc


# the keys of a chart's statistics carried by `deck` (the others are checked against the chart facts)
DECK_CHART_KEYS = ("convertedNoteCount", "skip", "events", "positions", "ranges", "justNotes", "seeds", "offSeeds",
                   "unplayable", "gekisouAptitude")
RANKS = 5                                   # the ranks of a Gekisou range (a Gekisou live has up to five players)
GEKISOU_RANGES = 3                          # the Gekisou ranges of a live (its first three fevers)


def mission_pattern(missions) -> int:
    """The mission pattern of a song's three Gekisou missions, the `_missionPattern` of its rank bonus rows: 0 when a
    mission is missing, 1 all the same, 2 all different, 3 otherwise."""
    a, b, c = missions
    if not (a and b and c):
        return 0
    if a == b:
        return 1 if a == c else 3
    return 2 if b != c and a != c else 3


def rank_bonus_percents(rows: list[dict], missions) -> list[list]:
    """The rank bonus percentages [range][rank - 1] of a song's missions from the MasterLiveGekisouRankingScoreBonus
    rows of its mission pattern (`_count`: the range 1..3, `_rank` 1..5; a later row wins; 0 without a row)."""
    pattern = mission_pattern(missions)
    out = [[0] * RANKS for _ in range(GEKISOU_RANGES)]
    for r in rows:
        c, k = (r.get("_count") or 0) - 1, (r.get("_rank") or 0) - 1
        if r.get("_missionPattern") == pattern and 0 <= c < GEKISOU_RANGES and 0 <= k < RANKS:
            out[c][k] = r.get("_scoreBonusPercent")
    return out


def _trunc_percent(score: int, percent: int) -> int:
    """trunc(score * percent / 100), the rank bonus of a range score."""
    p = score * percent
    return p // 100 if p >= 0 else -(-p // 100)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _numbers(v, n: int) -> bool:
    """Whether v is a list of n numbers (as the deck model writes them)."""
    return isinstance(v, list) and len(v) == n and all(
        isinstance(x, (int, deckdata._Num)) and not isinstance(x, bool) for x in v)


def _check_bound(c, what: str, where: str) -> None:
    """A check (or rank check) of the deck model: its exact score within `bound` of the predicted one."""
    if not isinstance(c, dict):
        raise MusicDataError(f"{where}: {what}: no check")
    if not (_int(c.get("exact")) and _number(c.get("predicted")) and _number(c.get("bound"))
            and float(c["bound"]) >= 0):
        raise MusicDataError(f"{where}: {what}: invalid exact, predicted or bound")
    if abs(c["exact"] - float(c["predicted"])) > float(c["bound"]):
        raise MusicDataError(f"{where}: {what} scores {c['exact']}, predicted {c['predicted']} beyond the bound "
                             f"{c['bound']}")


def _check_rank_check(rc: dict, n: int, s: str, where: str) -> None:
    if len(rc["ranks"]) != n or not all(1 <= r <= RANKS for r in rc["ranks"]):
        raise MusicDataError(f"{where}: {s}: rank check ranks {rc['ranks']!r}")
    _check_bound(rc, f"{s}: the check deck at ranks {rc['ranks']!r}", where)


# ournotes-deck's published seed set (live::seeds): candidate k is the low 32 bits (a signed integer) of output k + 1
# of SplitMix64 started at SEED_ORIGIN (the ASCII bytes of `gekisou1`); a candidate is kept when its pair of effective
# stream seeds (|b| and |b ^ 0x9E3779B9|, -2^31 taken as 2^31 - 1) differs from that of every seed kept before it
SEED_ORIGIN = 0x6765_6B69_736F_7531
_GAMMA, _M64, _LUCK_XOR = 0x9E37_79B9_7F4A_7C15, (1 << 64) - 1, 0x9E37_79B9


def _i32(x: int) -> int:
    x &= 0xFFFF_FFFF
    return x - (1 << 32) if x >> 31 else x


def published_seeds(n: int) -> list[int]:
    """The first n seeds of ournotes-deck's published seed set (a smaller set is a prefix of a larger one)."""
    def mix(z: int) -> int:
        z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & _M64
        z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & _M64
        return z ^ (z >> 31)

    def eff(x: int) -> int:
        return (1 << 31) - 1 if x == -(1 << 31) else abs(x)
    keys, out, k = set(), [], 0
    while len(out) < n:
        b = _i32(mix((SEED_ORIGIN + _GAMMA * (k + 1)) & _M64))
        key = (eff(b), eff(_i32(b ^ _LUCK_XOR)))
        if key not in keys:
            keys.add(key)
            out.append(b)
        k += 1
    return out


def chart_seeds(stats: dict, seeds: int) -> list[int]:
    """The seeds of a chart's Gekisou on statistics: none when it is unplayable with Gekisou, seed 0 when no range is
    a luck range (the play draws nothing), else the first `seeds` published seeds."""
    if stats.get("unplayable") is not None:
        return []
    return published_seeds(seeds) if any(r["mission"] == MISSION_LUCK for r in stats["ranges"]) else [0]


def _check_seed_shapes(stats: dict, kinds: int, where: str) -> None:
    """The array shapes and the checks of a chart's statistics: `weights[kind][position]` (a kind null only with
    Gekisou off), `rangeWeights[kind][position][range]` (null, or a kind null), one Gekisou off seed, the seeds'
    ranges one per range with their luck points, and every check (and rank check) within its bound."""
    positions, n = stats["positions"], len(stats["ranges"])
    for seed in stats["seeds"]:
        s = f"seed {seed['seed']}"
        w = seed.get("weights")
        if not (isinstance(w, list) and len(w) == kinds and all(_numbers(x, positions) for x in w)):
            raise MusicDataError(f"{where}: {s}: weights are not [kind][position]")
        if len(seed["ranges"]) != n:
            raise MusicDataError(f"{where}: {s}: {len(seed['ranges'])} range results for {n} ranges")
        for i, r in enumerate(seed["ranges"]):
            if not _int(r.get("luckPoints")):
                raise MusicDataError(f"{where}: {s} range {i}: no luck points (luckPoints)")
        rw = seed.get("rangeWeights")
        if rw is not None and not (isinstance(rw, list) and len(rw) == kinds and all(
                k is None or (isinstance(k, list) and len(k) == positions and all(_numbers(x, n) for x in k))
                for k in rw)):
            raise MusicDataError(f"{where}: {s}: rangeWeights are not [kind][position][range]")
        _check_bound(seed["check"], f"{s}: the check deck", where)
        rc = seed.get("rankCheck")
        if rc is not None:
            _check_rank_check(rc, n, s, where)
    off = stats.get("offSeeds")
    if not isinstance(off, list) or len(off) != 1:
        raise MusicDataError(f"{where}: the deck model gives no Gekisou off statistics (offSeeds)")
    for seed in off:
        w = seed.get("weights")
        if not (isinstance(w, list) and len(w) == kinds and all(x is None or _numbers(x, positions) for x in w)):
            raise MusicDataError(f"{where}: Gekisou off: weights are not [kind][position]")
        _check_bound(seed["check"], "Gekisou off: the check deck", where)


# ---------------------------------------------------------------- the Gekisou aptitude
PLAIN_EFFECT_TYPE, PLAIN_MS = 2000, 5000    # the plain kind: score up on the whole deck for 5 s, nothing else
CONDITION_MEMBER_TARGET = 5000              # the skill condition on the member a snap's support skill is paired with
APTITUDE_KEYS = ("plainKind", "host", "seedRule", "shapes")
SEED_RULE_KEYS = ("deterministicTest", "batches", "relative", "baseline", "crossSeeds")
SHAPE_KEYS = ("id", "source", "mission", "bandCondition", "effects", "skills")
FACTOR_COUNTS = ("judgedNotes", "justNotes", "perfectNotes", "tailNotes", "comboAtStart")
VARIANT_MEANS = ("score", "scorePerfect", "tail", "tailPerfect", "converted")
RANGE_MEANS = ("rangeScore", "rankBonus", "rangeScorePerfect", "maxCombo", "justCount", "luckPoints")
CHECK_KEYS = ("seed", "ranks", "deck", "exact", "predicted", "bound")
CONDITION_GROUPS = (("trigger", "_skillTriggerConditionGroup"), ("condition", "_skillConditionGroup"),
                    ("release", "_skillReleaseConditionGroup"), ("reset", "_effectExecuteLimitResetConditionGroup"))
MEAN_TOLERANCE = 1e-9                       # relative: a mean against its recomputation (binary64 sums)


def plain_kind(kinds: list[dict]) -> int | None:
    """The id of the plain score-up kind of `deck.kinds` (the page's plainKind): effect type 2000 without targets,
    conditions or limits for 5 s (a kind without `durationMs` counts as 5 s); None when there is none."""
    for k in kinds:
        ms = k.get("durationMs")
        if (k.get("effectType") == PLAIN_EFFECT_TYPE and not (k.get("skillTargetIds") or [])
                and not k.get("skillConditionGroup") and not k.get("skillReleaseConditionGroup")
                and not k.get("effectLimitCount") and not k.get("effectExecuteLimitCount")
                and (PLAIN_MS if ms is None else ms) == PLAIN_MS):
            return k["id"]
    return None


def _number(v) -> bool:
    """Whether v is a finite number as the deck model writes it."""
    if isinstance(v, bool) or not isinstance(v, (int, deckdata._Num)):
        return False
    try:
        return math.isfinite(float(v))
    except (OverflowError, ValueError):
        return False


def _mean_se(v) -> bool:
    """Whether v is `[mean, standard error]`: two finite numbers, the error not negative."""
    return isinstance(v, list) and len(v) == 2 and all(_number(x) for x in v) and float(v[1]) >= 0


def _same(a: float, b: float, scale: float = 1.0) -> bool:
    return abs(a - b) <= MEAN_TOLERANCE * max(1.0, abs(scale), abs(a), abs(b))


def mean_se(xs: list[int]) -> tuple[float, float]:
    """The mean of xs and its standard error (the sample standard deviation / sqrt(n); 0 for one value)."""
    n = len(xs)
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n) if n > 1 else 0.0)


def _f32(v) -> float:
    import numpy as np
    return float(np.float32(float(v)))


class Aptitude:
    """The Gekisou aptitude of the deck model checked against the Gekisou catalog and the master data it was made
    from: the file's shape table (`deck.gekisouAptitude`, check_header) and every chart's variants (chart)."""

    def __init__(self, tables: dict[str, list[dict]], catalog: dict):
        self.skills = {s["id"]: s for s in catalog["skills"]}
        self.supports = {s["id"]: s for s in catalog["supportSkills"]}
        self.conditions = {r["_id"]: r for r in tables["MasterSkillCondition"]}
        self.cumulative = {r["_id"]: r for r in tables["MasterSkillCumulativeCondition"]}
        self.targets = {r["_id"]: r for r in tables["MasterSkillTarget"]}
        self.sets: dict = {}
        for r in tables["MasterSkillConditionSet"]:
            self.sets.setdefault(r.get("_group"), []).append(r)
        self.rows: dict = {}                            # (source, skill id, level) -> effect rows in master order
        for source, table, key in (("member", "MasterGekisouSkillEffect", "_gekisouSkillID"),
                                   ("support", "MasterGekisouSupportSkillEffect", "_gekisouSupportSkillID")):
            for r in tables[table]:
                self.rows.setdefault((source, r.get(key), r.get("_level")), []).append(r)
        # what the shapes cover: every member card's Gekisou skill at its highest level, every snap's Gekisou support
        # skills at the snap's highest rank
        expected = {("member", m["gekisouSkillId"], self.skills[m["gekisouSkillId"]]["maxLevel"])
                    for m in catalog["members"] if m["gekisouSkillId"] is not None}
        expected |= {("support", i, s["supportSkillLevel"]) for s in catalog["snaps"]
                     for i in s["gekisouSupportSkillIds"]}
        self.expected = {k for k in expected if k in self.rows}     # a skill level with effect rows
        self.header = self.shapes = None

    # ------------------------------------------------ the shape table
    def _group(self, g) -> list:
        """A condition group as the shapes write it: its condition sets in master order, each a list of conditions
        `{type, values, positive, targetIds}` (the member target of condition 5000 null); [] for group 0."""
        if not g:
            return []
        out = []
        for s in self.sets.get(g, []):
            conds = []
            for cid in s.get("_conditionIds") or []:
                c = self.conditions.get(cid)
                if c is None:
                    raise MusicDataError(f"MasterSkillConditionSet {s.get('_id')}: condition {cid} is not in "
                                         f"MasterSkillCondition")
                t = c.get("_conditionType")
                conds.append({"type": t, "values": list(c.get("_conditionValues") or []),
                              "positive": bool(c.get("_isPositive")),
                              "targetIds": (None if t == CONDITION_MEMBER_TARGET
                                            else list(c.get("_conditionTargetIDs") or []))})
            out.append(conds)
        return out

    def effects(self, source: str, skill: int, level: int) -> list[dict]:
        """The effect rows of a Gekisou (support) skill at a level as the shapes write them."""
        out = []
        for r in self.rows.get((source, skill, level), []):
            cid = r.get("_skillCumulativeConditionID")
            c = self.cumulative.get(cid) if cid else None
            if cid and c is None:
                raise MusicDataError(f"Gekisou {source} skill {skill} level {level}: cumulative condition {cid} is not "
                                     f"in MasterSkillCumulativeCondition")
            e = {"effectType": r.get("_skillEffectType"), "triggerType": r.get("_skillTriggerType"),
                 "activationTimeSecond": r.get("_activationTimeSecond"), "effectValue": r.get("_effectValue"),
                 "maxEffectValue": r.get("_maxEffectValue"), "effectLimitCount": r.get("_effectLimitCount"),
                 "effectExecuteLimitCount": r.get("_effectExecuteLimitCount"),
                 "skillTargetIds": list(r.get("_skillTargetIDs") or [])}
            e.update({k: self._group(r.get(col)) for k, col in CONDITION_GROUPS})
            e["cumulative"] = None if c is None else {
                "type": c.get("_skillCumulativeConditionType"), "values": list(c.get("_conditionValues") or []),
                "targetIds": list(c.get("_conditionTargetIDs") or []),
                "maxCumulativeCount": c.get("_maxCumulativeCount")}
            out.append(e)
        return out

    def member_targets(self, source: str, skill: int, level: int) -> tuple[list | None, list | None]:
        """(memberTargetIds, bandIds) of a skill at a level: the target ids of every condition 5000 of its effect rows
        (unique, ascending) and their bands; (None, None) without such a condition."""
        ids = set()
        found = False
        for r in self.rows.get((source, skill, level), []):
            for _, col in CONDITION_GROUPS:
                for s in self.sets.get(r.get(col), []) if r.get(col) else []:
                    for cid in s.get("_conditionIds") or []:
                        c = self.conditions.get(cid) or {}
                        if c.get("_conditionType") == CONDITION_MEMBER_TARGET:
                            found = True
                            ids.update(c.get("_conditionTargetIDs") or [])
        if not found:
            return None, None
        bands = sorted({(self.targets.get(t) or {}).get("_bandID") or 0 for t in ids} - {0})
        return sorted(ids), bands

    @staticmethod
    def _effects_equal(got, want) -> bool:
        if not (isinstance(got, list) and len(got) == len(want)):
            return False
        for g, w in zip(got, want):
            if not isinstance(g, dict) or any(k not in g for k in w):
                return False
            for k, v in w.items():
                if k == "activationTimeSecond":
                    if not (_number(g[k]) and _f32(g[k]) == _f32(v)):
                        return False
                elif g[k] != v:
                    return False
        return True

    def check_header(self, info, kinds: list[dict], model: dict) -> dict | None:
        """`deck.gekisouAptitude` after checking it (None when the deck model leaves the aptitude out): the plain kind
        of `kinds`, a description of the host (and `model.gekisouAptitude`), the seed rule, and the shape table:
        ids 0..n-1, each shape's skills in the catalog (a member card's skill at its highest level, a snap's support
        skill at its highest rank level) with the shape's mission, their effect rows as the master data has them, their
        member targets and bands, and every skill and level of the catalog in exactly one shape."""
        if info is None:
            return None
        where = "deck model: gekisouAptitude"
        if not isinstance(info, dict) or any(k not in info for k in APTITUDE_KEYS):
            raise MusicDataError(f"{where}: not {{{', '.join(APTITUDE_KEYS)}}}")
        want = plain_kind(kinds)
        if info["plainKind"] != want:
            raise MusicDataError(f"{where}: plain kind {info['plainKind']!r} is not the plain kind of the kinds, "
                                 f"{want!r}")
        if not (isinstance(info["host"], str) and info["host"]):
            raise MusicDataError(f"{where}: the support skills' host is not described (host)")
        if not (isinstance(model.get("gekisouAptitude"), str) and model["gekisouAptitude"]):
            raise MusicDataError("deck model: the model does not describe the aptitude (model.gekisouAptitude)")
        rule = info["seedRule"]
        if not (isinstance(rule, dict) and all(k in rule for k in SEED_RULE_KEYS)
                and _int(rule["deterministicTest"]) and rule["deterministicTest"] >= 1
                and isinstance(rule["batches"], list) and rule["batches"] and all(_int(b) for b in rule["batches"])
                and rule["batches"][0] >= 2 and all(a < b for a, b in zip(rule["batches"], rule["batches"][1:]))
                and _number(rule["relative"]) and float(rule["relative"]) >= 0
                and _number(rule["baseline"]) and float(rule["baseline"]) >= 0
                and _int(rule["crossSeeds"]) and rule["crossSeeds"] >= 1):
            raise MusicDataError(f"{where}: seed rule {rule!r}")
        shapes = info["shapes"]
        if not isinstance(shapes, list) or [s.get("id") if isinstance(s, dict) else s for s in shapes] != list(
                range(len(shapes))):
            ids = [s.get("id") if isinstance(s, dict) else s for s in shapes] if isinstance(shapes, list) else shapes
            raise MusicDataError(f"{where}: shape ids {ids!r} are not 0, 1, ... in order")
        seen: dict = {}
        for shape in shapes:
            at = f"{where}: shape {shape['id']}"
            if not _int(shape["id"]) or any(k not in shape for k in SHAPE_KEYS) or shape["source"] not in ("member", "support") \
                    or not _int(shape["mission"]) or shape["mission"] not in (1, 2, 3, 4) \
                    or not isinstance(shape["bandCondition"], bool) \
                    or not isinstance(shape["skills"], list) or not shape["skills"]:
                raise MusicDataError(f"{at}: not a shape {{{', '.join(SHAPE_KEYS)}}} of a source, a mission and "
                                     f"skills")
            source = shape["source"]
            catalog = self.skills if source == "member" else self.supports
            for sk in shape["skills"]:
                if not isinstance(sk, dict) or any(k not in sk for k in ("id", "level", "memberTargetIds", "bandIds")) or not (
                        _int(sk.get("id")) and _int(sk.get("level"))):
                    raise MusicDataError(f"{at}: skill {sk!r}")
                key = (source, sk["id"], sk["level"])
                what = f"{at}: Gekisou {'skill' if source == 'member' else 'support skill'} {sk['id']} level " \
                       f"{sk['level']}"
                if key in seen:
                    raise MusicDataError(f"{what} is in shape {seen[key]} too")
                seen[key] = shape["id"]
                if key not in self.expected:
                    raise MusicDataError(f"{what} is not a skill and level of the Gekisou catalog (a member card's "
                                         f"Gekisou skill at its highest level, a snap's support skill at its highest "
                                         f"rank)")
                if catalog[sk["id"]]["mission"] != shape["mission"]:
                    raise MusicDataError(f"{what} has the mission {catalog[sk['id']]['mission']!r}, the shape "
                                         f"{shape['mission']!r}")
                targets = self.member_targets(*key)
                if (sk.get("memberTargetIds"), sk.get("bandIds")) != targets:
                    raise MusicDataError(f"{what}: member targets {sk.get('memberTargetIds')!r} and bands "
                                         f"{sk.get('bandIds')!r}, the master data has {targets[0]!r} and "
                                         f"{targets[1]!r}")
                if shape["bandCondition"] != (targets[0] is not None):
                    raise MusicDataError(f"{what}: band condition {shape['bandCondition']}, the master data has "
                                         f"{'one' if targets[0] is not None else 'none'}")
                if not self._effects_equal(shape["effects"], self.effects(*key)):
                    raise MusicDataError(f"{what}: the shape's effect rows are not the master data's")
        missing = sorted(self.expected - set(seen), key=repr)
        if missing:
            raise MusicDataError(f"{where}: no shape has " + ", ".join(f"the {s} skill {i} level {lv}"
                                                                        for s, i, lv in missing))
        self.header, self.shapes = info, shapes
        return {k: info[k] for k in APTITUDE_KEYS}

    # ------------------------------------------------ a chart's aptitude
    def chart(self, where: str, stats: dict, plain: int | None) -> dict | None:
        """A chart's `gekisouAptitude` after checking it: null exactly when the aptitude is left out, the master data
        has no shape, the chart is unplayable with Gekisou or has no Gekisou range; else a factor per range and a
        variant per shape of the chart's missions (both band match results of a shape with a band condition), each
        with its means shaped and consistent (the tail, a deterministic variant's rank bonuses) and its check within
        its bound."""
        apt = stats.get("gekisouAptitude")
        if self.header is None or not self.shapes or stats.get("unplayable") is not None or not stats["ranges"]:
            if apt is not None:
                why = ("the aptitude left out" if self.header is None else "no shape" if not self.shapes
                       else "a chart unplayable with Gekisou" if stats.get("unplayable") is not None
                       else "a chart without Gekisou ranges")
                raise MusicDataError(f"{where}: the deck model gives a Gekisou aptitude with {why}")
            return None
        where = f"{where}: Gekisou aptitude"
        if not (isinstance(apt, dict) and isinstance(apt.get("factors"), list) and isinstance(apt.get("variants"),
                                                                                                list)):
            raise MusicDataError(f"{where}: none (gekisouAptitude {{factors, variants}})")
        ranges, base = stats["ranges"], stats["seeds"]
        self._factors(where, apt["factors"], stats)
        missions = {r["mission"] for r in ranges}
        want = [(s["id"], b) for s in self.shapes if s["mission"] == MISSION_ALL or s["mission"] in missions
                for b in ((True, False) if s["bandCondition"] else (None,))]
        if any(not isinstance(v, dict) or not _int(v.get("shape"))
               or v.get("bandMatch") is not None and not isinstance(v.get("bandMatch"), bool)
               or "bandMatch" not in v for v in apt["variants"]):
            raise MusicDataError(f"{where}: invalid shape or bandMatch")
        got = [(v.get("shape"), v.get("bandMatch")) if isinstance(v, dict) else v for v in apt["variants"]]
        if got != want:
            raise MusicDataError(f"{where}: variants (shape, bandMatch) {got!r}, the shapes of the chart's missions "
                                 f"{sorted(missions)} give {want!r}")
        for v in apt["variants"]:
            self._variant(f"{where}: shape {v['shape']} band match {v['bandMatch']!r}", v, stats, base, plain)
        return {"factors": apt["factors"], "variants": apt["variants"]}

    def _factors(self, where: str, factors: list, stats: dict) -> None:
        ranges, base = stats["ranges"], stats["seeds"]
        if len(factors) != len(ranges):
            raise MusicDataError(f"{where}: {len(factors)} factors for {len(ranges)} ranges")
        just = 0
        for j, (f, r) in enumerate(zip(factors, ranges)):
            at = f"{where}: range {j} factors"
            if not (isinstance(f, dict) and all(_int(f.get(k)) and f[k] >= 0 for k in FACTOR_COUNTS)
                    and _mean_se(f.get("lotteries"))):
                raise MusicDataError(f"{at}: not {{{', '.join(FACTOR_COUNTS)}, lotteries}}: {f!r}")
            if r["mission"] != MISSION_JUST and (f["justNotes"], f["perfectNotes"]) != (0, 0):
                raise MusicDataError(f"{at}: {f['justNotes']} Just and {f['perfectNotes']} Perfect notes outside a "
                                     f"Just count range")
            if f["justNotes"] + f["perfectNotes"] > f["judgedNotes"] or f["comboAtStart"] > stats["judgedNotes"]:
                raise MusicDataError(f"{at}: note counts {f!r} beyond the range's or the chart's")
            just += f["justNotes"]
            m, se = mean_se([sum(s["ranges"][j]["lotResults"]) for s in base]) if base else (0.0, 0.0)
            if r["mission"] != MISSION_LUCK:
                m, se = 0.0, 0.0
            if not (_same(float(f["lotteries"][0]), m) and _same(float(f["lotteries"][1]), se, m)):
                raise MusicDataError(f"{at}: lotteries {f['lotteries']}, the chart's seeds give [{m!r}, {se!r}]")
        if just > stats["justNotes"]:
            raise MusicDataError(f"{where}: {just} Just notes in the ranges, {stats['justNotes']} on the play")

    def _variant(self, at: str, v: dict, stats: dict, base: list, plain: int | None) -> None:
        rule, n, positions = self.header["seedRule"], len(stats["ranges"]), stats["positions"]
        if not (isinstance(v.get("deterministic"), bool) and isinstance(v.get("seTargetMet"), bool)
                and _int(v.get("seeds")) and _int(v.get("crossSeeds"))):
            raise MusicDataError(f"{at}: deterministic, seeds, seTargetMet, crossSeeds "
                                 f"{[v.get(k) for k in ('deterministic', 'seeds', 'seTargetMet', 'crossSeeds')]!r}")
        det = v["deterministic"]
        effects = self.shapes[v["shape"]]["effects"]
        random = any(r["mission"] == MISSION_LUCK for r in stats["ranges"]) or any(
            11000 <= e["effectType"] <= 11005 or any(
                c["type"] == 4011 for key, _ in CONDITION_GROUPS for group in e[key] for c in group)
            for e in effects)
        if det and random:
            raise MusicDataError(f"{at}: a deterministic variant with a random dependency")
        if det and (v["seeds"], v["seTargetMet"]) != (1, True):
            raise MusicDataError(f"{at}: deterministic on {v['seeds']} seeds (seTargetMet {v['seTargetMet']})")
        if not det and (v["seeds"] not in rule["batches"]
                        or not v["seTargetMet"] and v["seeds"] != rule["batches"][-1]):
            raise MusicDataError(f"{at}: {v['seeds']} seeds (seTargetMet {v['seTargetMet']}), not a batch of the seed "
                                 f"rule {rule['batches']}")
        if v["crossSeeds"] != min(v["seeds"], rule["crossSeeds"]):
            raise MusicDataError(f"{at}: {v['crossSeeds']} cross seeds, not min({v['seeds']}, {rule['crossSeeds']})")
        pairs = [(k, v.get(k)) for k in VARIANT_MEANS]
        rs = v.get("ranges")
        if not (isinstance(rs, list) and len(rs) == n and all(isinstance(r, dict) for r in rs)):
            raise MusicDataError(f"{at}: {len(rs) if isinstance(rs, list) else rs!r} range results for {n} ranges")
        pairs += [(f"ranges[{j}].{k}", r.get(k)) for j, r in enumerate(rs) for k in RANGE_MEANS]
        w, rw = v.get("weights"), v.get("rangeWeights")
        linear = bool(base) and base[0].get("rangeWeights") is not None
        if plain is None:
            if w is not None or rw is not None:
                raise MusicDataError(f"{at}: weights without a plain kind")
        else:
            if not (isinstance(w, list) and len(w) == positions):
                raise MusicDataError(f"{at}: weights are not [position] of [mean, se]")
            pairs += [(f"weights[{k}]", x) for k, x in enumerate(w)]
            if (rw is not None) != linear:
                raise MusicDataError(f"{at}: rangeWeights {'given' if rw is not None else 'null'}, the chart's seeds "
                                     f"have {'them' if linear else 'none'}")
            if rw is not None:
                if not (isinstance(rw, list) and len(rw) == positions and all(isinstance(x, list) and len(x) == n
                                                                               for x in rw)):
                    raise MusicDataError(f"{at}: rangeWeights are not [position][range] of [mean, se]")
                pairs += [(f"rangeWeights[{k}][{j}]", y) for k, x in enumerate(rw) for j, y in enumerate(x)]
        for name, x in pairs:
            if not _mean_se(x):
                raise MusicDataError(f"{at}: {name} {x!r} is not [mean, standard error]")
            if det and float(x[1]) != 0:
                raise MusicDataError(f"{at}: {name} {x!r}: a deterministic variant with a standard error")
        m = {k: float(v[k][0]) for k in VARIANT_MEANS}
        terms = [float(r[k][0]) for r in rs for k in ("rangeScore", "rankBonus")]
        # Every serialized point mean is independently rounded to 0.001 (score, tail and two terms per range).
        if abs(m["tail"] - (m["score"] - sum(terms))) > (len(terms) + 2) * 0.0005 + 1e-8:
            raise MusicDataError(f"{at}: tail {v['tail'][0]} is not score {v['score'][0]} - {sum(terms)!r} (the "
                                 f"ranges' scores and rank bonuses)")
        if det:
            ints = [float(x[0]) for name, x in pairs if not name.startswith(("weights", "rangeWeights"))]
            if not all(x.is_integer() for x in ints):
                raise MusicDataError(f"{at}: a deterministic variant with a fraction of a point")
            perfect = 0                                 # the Perfect play's range scores and rank bonuses
            for j, (r, info, r0) in enumerate(zip(rs, stats["ranges"], base[0]["ranges"])):
                p = info["rankBonusPercent"]
                want = _trunc_percent(r0["rangeScore"] + int(float(r["rangeScore"][0])), p) - r0["rankBonus"]
                if int(float(r["rankBonus"][0])) != want:
                    raise MusicDataError(f"{at}: range {j}: rank bonus {r['rankBonus'][0]} is not trunc(("
                                         f"{r0['rangeScore']} + {r['rangeScore'][0]}) * {p} / 100) - "
                                         f"{r0['rankBonus']} = {want}")
                d, r0p = int(float(r["rangeScorePerfect"][0])), r0["rangeScorePerfect"]
                perfect += d + _trunc_percent(r0p + d, p) - _trunc_percent(r0p, p)
            if m["tailPerfect"] != m["scorePerfect"] - perfect:
                raise MusicDataError(f"{at}: tail on the Perfect play {v['tailPerfect'][0]} is not scorePerfect "
                                     f"{v['scorePerfect'][0]} - {perfect} (the ranges' Perfect play scores and rank "
                                     f"bonuses)")
        c = v.get("check")
        if not (isinstance(c, dict) and all(k in c for k in CHECK_KEYS)):
            raise MusicDataError(f"{at}: no check {{{', '.join(CHECK_KEYS)}}}")
        first = base[0]["seed"] if det else published_seeds(1)[0]
        if not _int(c["seed"]) or c["seed"] != first:
            raise MusicDataError(f"{at}: the check plays seed {c['seed']!r}, not the variant's first {first}")
        if not (isinstance(c["ranks"], list) and len(c["ranks"]) == n and all(_int(r) and 1 <= r <= RANKS
                                                                                for r in c["ranks"])
                and (linear or all(r == 1 for r in c["ranks"]))):
            raise MusicDataError(f"{at}: check ranks {c['ranks']!r}")
        if not (isinstance(c["deck"], list) and len(c["deck"]) == positions and all(
                x is None or (plain is not None and isinstance(x, list) and len(x) == 2 and x[0] == plain
                              and _int(x[1])) for x in c["deck"])):
            raise MusicDataError(f"{at}: check deck {c['deck']!r} is not [plain kind ({plain}), value] or null per "
                                 f"position")
        _check_bound(c, "the check deck", at)


def chart_deck(song: dict, chart: dict, stats: dict, kinds: int, percents: list[list], seeds: int,
               aptitude: Aptitude, plain: int | None) -> dict:
    """A chart's `deck` from its statistics, after checking them against the song, the chart facts, the song's rank
    bonus percentages (`percents`: rank_bonus_percents), the seed set (chart_seeds: `seeds` seeds with a luck range)
    and its Gekisou aptitude (Aptitude.chart, `plain` the plain kind), `kinds` the number of score-up kinds."""
    where = f"chart {chart['scoreId']} ({song['id']} {chart['difficulty']})"
    checks = (
        ("music id", stats["musicId"], song["id"]),
        ("difficulty", stats["difficulty"], chart["difficulty"]),
        ("level", stats["level"], chart["level"]),
        ("judged note count", stats["judgedNotes"], chart["notes"]["judged"]),
        ("last note time", stats["lastNoteMs"], chart["lastNoteMs"]),
        ("music length", stats["musicLengthMs"], chart["musicLengthMs"]),
        ("Gekisou missions", stats["missions"], song["gekisouMissions"]),
        ("skill event times", [t for _, t in stats["events"]], chart["skillEventsMs"]),
        ("fevers", [[r["startMs"], r["endMs"]] for r in stats["ranges"]], chart["fevers"][:len(stats["ranges"])]),
    )
    for name, deck, facts in checks:
        if deck != facts:
            raise MusicDataError(f"{where}: the deck model's {name} {deck!r} differs from the chart's {facts!r}")
    ranges = stats["ranges"]
    for i, r in enumerate(ranges):
        want = percents[i] if i < len(percents) else [0] * RANKS
        got = r.get("rankBonusPercents")
        if got != want or r["rankBonusPercent"] != want[0]:
            raise MusicDataError(f"{where}: range {i}: the deck model's rank bonus percentages {got!r} "
                                 f"({r['rankBonusPercent']!r}) differ from MasterLiveGekisouRankingScoreBonus {want!r}")
    want = chart_seeds(stats, seeds)
    if [s["seed"] for s in stats["seeds"]] != want:
        raise MusicDataError(f"{where}: seeds {[s['seed'] for s in stats['seeds']]} are not the chart's seed set "
                             f"{want}")
    _check_seed_shapes(stats, kinds, where)
    for seed in stats["seeds"]:
        for i, (r, info) in enumerate(zip(seed["ranges"], ranges)):
            bonus = _trunc_percent(r["rangeScore"], info["rankBonusPercent"])
            if r["rankBonus"] != bonus:
                raise MusicDataError(f"{where}: seed {seed['seed']} range {i}: rank bonus {r['rankBonus']} is not "
                                     f"trunc({r['rangeScore']} * {info['rankBonusPercent']} / 100) = {bonus}")
        if stats["justNotes"] == 0 and (seed.get("scorePerfect") != seed["score"] or any(
                r.get("rangeScorePerfect") != r["rangeScore"] for r in seed["ranges"])):
            raise MusicDataError(f"{where}: seed {seed['seed']}: a chart without Just notes scores otherwise on the "
                                 f"Perfect play ({seed.get('scorePerfect')} for {seed['score']})")
    return dict({k: stats.get(k) for k in DECK_CHART_KEYS}, gekisouAptitude=aptitude.chart(where, stats, plain))


# ---------------------------------------------------------------- document
def build(tables: dict[str, list[dict]], table_sha: dict[str, str], fetch: Callable[[str], bytes],
          bgm: Callable[[str, str], dict] | None, *, region: str, client: dict, catalog: dict, master_source: str,
          master_version: str | None, deck: Deck | None = None, full: bool = False) -> dict:
    """The music data document. `fetch(file name)`: a chart TextAsset's bytes (KeyError when there is none);
    `bgm(cue sheet, cue)`: the BGM length (catalog_bgm), None to leave every song's `bgm.length` null; `deck`: the
    deck model measuring the songs' charts (and their Gekisou aptitude), None to leave every chart's `deck` null;
    `full`: add the deck input (`master`, `charts`: deckdata.TABLES and every chart's runtime notes). `tables`:
    tables_of(deck, full)."""
    from . import __version__
    raws: dict[str, bytes] = {}

    def raw_chart(row: dict) -> tuple[str, bytes]:      # (key, bytes); each chart asset is read once
        name = row["_musicScoreTextFileName"]
        key = deckdata.chart_key(name)
        if name not in raws:
            try:
                raws[name] = fetch(name)
            except KeyError:
                raise MusicDataError(f"chart {key} (MasterLiveMusicScore {row['_id']}): no such asset") from None
        return key, raws[name]

    text = Texts(tables["MasterText"])
    scores = _by_id(tables["MasterLiveMusicScore"], "MasterLiveMusicScore")
    sounds = _by_id(tables["MasterSound"], "MasterSound")
    sheets = _by_id(tables["MasterSoundCueSheet"], "MasterSoundCueSheet")
    musics = sorted(_by_id(tables["MasterLiveMusic"], "MasterLiveMusic").values(), key=lambda r: r["_id"])
    rank_groups: dict = {}                              # _group -> rows in required-score order, as the client reads
    for r in sorted(tables["MasterLiveScoreRank"], key=lambda r: (r.get("_requiredScore") or 0, r.get("_id") or 0)):
        if r.get("_liveScoreRank") not in SCORE_RANKS:
            raise MusicDataError(f"MasterLiveScoreRank {r.get('_id')}: unknown rank {r.get('_liveScoreRank')!r}")
        rank_groups.setdefault(r.get("_group"), []).append(r)

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
    gk_catalog = gekisou_catalog(tables, text)

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
                raise MusicDataError(f"{where}: {d} score {sid} is not in MasterLiveMusicScore")
            charts.append({"difficulty": d, **chart_facts(row, *raw_chart(row)), "deck": None})
        snd = sounds.get(m.get("_musicSoundID"))
        if snd is None:
            raise MusicDataError(f"{where}: sound {m.get('_musicSoundID')} is not in MasterSound")
        sheet = sheets.get(snd.get("_soundCueSheetID"))
        if sheet is None:
            raise MusicDataError(f"{where}: cue sheet {snd.get('_soundCueSheetID')} is not in MasterSoundCueSheet")
        length = bgm(sheet["_cueSheetName"], snd["_cueName"]) if bgm is not None else None
        rank_rows = rank_groups.get(m.get("_liveScoreRankGroup"), [])
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
            "scoreRanks": [{"rank": SCORE_RANKS[r["_liveScoreRank"]], "requiredScore": r.get("_requiredScore"),
                            "battleRequiredScore": r.get("_battleLiveRequiredScore")} for r in rank_rows],
            "charts": charts,
            "master": {"MasterLiveMusic": m, "MasterLiveScoreRank": rank_rows},
        })

    exporter = {"name": "nnnotes", "version": __version__, "chartFormat": deckdata.CHART_FORMAT}
    records: dict[int, dict] = {}                       # score id -> the chart's deck input record

    def record(sid: int) -> dict:
        if sid not in records:
            records[sid] = deckdata.chart_record(sid, *raw_chart(scores[sid]))
        return records[sid]

    deck_doc = None
    if deck is not None:
        measured = sorted({c["scoreId"] for s in songs for c in s["charts"]})
        stats = deck.stats(deckdata.build(
            tables, [record(i) for i in measured],
            {"region": region, "master": {"source": master_source, "version": master_version},
             "exporter": exporter}))
        by_score = {c["scoreId"]: c for c in stats["charts"]}
        if sorted(by_score) != measured:
            raise MusicDataError("deck model: the charts measured differ from the songs' charts")
        aptitude = Aptitude(tables, gk_catalog)
        header = aptitude.check_header(stats.get("gekisouAptitude"), stats["kinds"], stats["model"])
        if (header is not None) != deck.aptitude:
            raise MusicDataError(f"deck model: {'no' if deck.aptitude else 'a'} Gekisou aptitude (gekisouAptitude), "
                                 f"asked for {'it' if deck.aptitude else 'none'}")
        plain = header["plainKind"] if header is not None else None
        bonus_rows = tables.get("MasterLiveGekisouRankingScoreBonus", [])
        for s in songs:
            percents = rank_bonus_percents(bonus_rows, s["gekisouMissions"])
            for c in s["charts"]:
                c["deck"] = chart_deck(s, c, by_score[c["scoreId"]], len(stats["kinds"]), percents, deck.seeds,
                                       aptitude, plain)
        deck_doc = {"model": stats["model"], "kinds": stats["kinds"], "gekisouAptitude": header}

    read = [t for t in tables_of(deck is not None, full) if t in tables]
    doc = {
        "format": FORMAT,
        "provenance": {
            "region": region,
            "client": {"versionName": client.get("versionName"), "versionCode": client.get("versionCode")},
            "catalog": {"resourceVersion": catalog.get("resourceVersion"), "sha256": catalog.get("sha256"),
                        **({"resourceHash": catalog["resourceHash"]} if catalog.get("resourceHash") else {})},
            "master": {"source": master_source, "version": master_version,
                       "tables": {t: {"sha256": table_sha[t]} for t in read}},
            "exporter": exporter,
            "deck": deck.info() if deck is not None else None,
        },
        "languages": list(LANGUAGES),
        "bands": bands,
        "characters": characters,
        "tags": tags,
        "categories": categories,
        "gekisouCatalog": gk_catalog,
        "deck": deck_doc,
        "songs": songs,
    }
    if full:
        doc["master"] = deckdata.master_subset(tables)
        doc["charts"] = [record(sid) for sid in sorted(scores)]
    return doc


def tables_of(deck: bool, full: bool) -> tuple[str, ...]:
    """The master data tables read: SONG_TABLES and CATALOG_TABLES, and deckdata.TABLES with the deck model or
    `full`."""
    base = SONG_TABLES + tuple(t for t in CATALOG_TABLES if t not in SONG_TABLES)
    if not (deck or full):
        return base
    return base + tuple(t for t, _ in deckdata.TABLES if t not in base)


def export(out, src: deckdata.MasterSource, key, fetch: Callable[[str], bytes],
           bgm: Callable[[str, str], dict] | None, *, region: str, client: dict, catalog: dict,
           deck: Deck | None = None, full: bool = False, jacket: Callable[[str], bytes] | None = None,
           jackets_dir=None, replay_dir=None, replay_engine=None) -> dict:
    """Read the master data, every chart and every BGM cue sheet, measure the charts with `deck`, then write the file
    `out` (gzip when it ends in `.gz`) through a temporary file and a rename; with `jacket` and `jackets_dir`, first
    every song's jacket as `<jackets_dir>/<jacket>.webp`. Returns the summary."""
    from .cache import write_atomic
    out = Path(out)
    try:
        if replay_engine is not None and replay_dir is None:
            raise deckdata.DeckDataError("--replay-engine needs --replay-dir")
        if replay_dir is not None and bgm is None:
            raise deckdata.DeckDataError("replay export requires actual BGM lengths; --no-bgm is incompatible")
        tables, shas = deckdata.read_master(src, key, tables_of(deck is not None, full or replay_dir is not None))
        doc = build(tables, shas, fetch, bgm, region=region, client=client, catalog=catalog,
                    master_source=src.source, master_version=src.version, deck=deck, full=full or replay_dir is not None)
        replay_files = None
        if replay_dir is not None:
            from . import replaydata
            replay_files, replay_manifest = replaydata.bundle(doc, replay_engine)
            manifest_sha = hashlib.sha256(replay_files["manifest.json"]).hexdigest()
            replay_target = Path(replay_dir) / manifest_sha
            try:
                relative = replay_target.resolve().relative_to(out.parent.resolve()).as_posix()
            except ValueError:
                raise deckdata.DeckDataError("replay directory must be under the music-data output directory") from None
            doc["replay"] = {"format": replaydata.FORMAT, "manifestUrl": relative + "/manifest.json",
                             "sha256": manifest_sha,
                             "charts": len(replay_manifest["charts"])}
            if not full:
                del doc["master"], doc["charts"]
        jackets = sorted({s["jacket"] for s in doc["songs"] if s["jacket"]}) if jacket is not None else []
        images = {name: jacket(name) for name in jackets}
        data = deckdata.encode(deckdata._value(doc, "music data"))
    except deckdata.DeckDataError as e:
        raise MusicDataError(str(e)) from None
    if jackets:
        d = Path(jackets_dir)
        d.mkdir(parents=True, exist_ok=True)
        for name, image in images.items():
            write_atomic(d / f"{name}.webp", image)
    written = deckdata.file_bytes(data, out.name.endswith(".gz"))
    if replay_files is not None:
        replaydata.write(replay_target, replay_files)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, written)
    charts = [c for s in doc["songs"] for c in s["charts"]]
    return {"out": str(out), "format": FORMAT, "region": region, "masterSource": src.source,
            "masterVersion": src.version, "songs": len(doc["songs"]), "charts": len(charts),
            "deck": doc["provenance"]["deck"]["commit"] if deck is not None else None,
            "unplayable": sum(1 for c in charts if c["deck"] and c["deck"]["unplayable"]),
            "full": full, "bgm": bgm is not None, "jackets": len(jackets),
            "bytes": len(data), "fileBytes": len(written), "sha256": hashlib.sha256(written).hexdigest(),
            **({"replay": doc["replay"]} if replay_files is not None else {})}
