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
MISSION_LUCK = 2                            # GekisouMissionType: 1 combo, 2 luck, 3 Just count, 4 all


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
    available parallelism), `seeds` seeds for a chart with a luck range."""

    def __init__(self, seeds: int = DECK_SEEDS, workers: int | None = None, module=None):
        if module is None:
            try:
                from . import _deck as module
            except ImportError:
                raise MusicDataError("the deck model (nnnotes._deck) is not built into this installation: install "
                                     "nnnotes from a wheel or build it (maturin), or pass --no-deck") from None
        self.module, self.seeds, self.workers = module, seeds, workers

    def info(self) -> dict:
        """{name, version, source, commit, format} of the deck model."""
        i = self.module.info()
        return {k: i[k] for k in ("name", "version", "source", "commit", "format")}

    def stats(self, deck_input: dict) -> dict:
        """The chart statistics document of a deck input document (deckdata.build), its numbers as written."""
        try:
            text = self.module.chart_stats(deckdata.encode(deck_input).decode("utf-8"), self.seeds, self.workers)
        except ValueError as e:
            raise MusicDataError(f"deck model: {e}") from None
        doc = json.loads(text, parse_float=deckdata._Num)
        if doc.get("format") != self.info()["format"]:
            raise MusicDataError(f"deck model: wrote {doc.get('format')!r}, expected {self.info()['format']!r}")
        return doc


# the keys of a chart's statistics carried by `deck` (the others are checked against the chart facts)
DECK_CHART_KEYS = ("convertedNoteCount", "skip", "events", "positions", "ranges", "justNotes", "seeds", "offSeeds",
                   "unplayable")
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


def chart_deck(song: dict, chart: dict, stats: dict, kinds: int, percents: list[list],
               seeds: int = DECK_SEEDS) -> dict:
    """A chart's `deck` from its statistics, after checking them against the song, the chart facts, the song's rank
    bonus percentages (`percents`: rank_bonus_percents) and the seed set (chart_seeds: `seeds` seeds with a luck
    range), `kinds` the number of score-up kinds."""
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
    return {k: stats.get(k) for k in DECK_CHART_KEYS}


# ---------------------------------------------------------------- document
def build(tables: dict[str, list[dict]], table_sha: dict[str, str], fetch: Callable[[str], bytes],
          bgm: Callable[[str, str], dict] | None, *, region: str, client: dict, catalog: dict, master_source: str,
          master_version: str | None, deck: Deck | None = None, full: bool = False) -> dict:
    """The music data document. `fetch(file name)`: a chart TextAsset's bytes (KeyError when there is none);
    `bgm(cue sheet, cue)`: the BGM length (catalog_bgm), None to leave every song's `bgm.length` null; `deck`: the
    deck model measuring the songs' charts, None to leave every chart's `deck` null; `full`: add the deck input
    (`master`, `charts`: deckdata.TABLES and every chart's runtime notes). `tables`: tables_of(deck, full)."""
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
        bonus_rows = tables.get("MasterLiveGekisouRankingScoreBonus", [])
        for s in songs:
            percents = rank_bonus_percents(bonus_rows, s["gekisouMissions"])
            for c in s["charts"]:
                c["deck"] = chart_deck(s, c, by_score[c["scoreId"]], len(stats["kinds"]), percents, deck.seeds)
        deck_doc = {"model": stats["model"], "kinds": stats["kinds"]}

    read = [t for t in tables_of(deck is not None, full) if t in tables]
    doc = {
        "format": FORMAT,
        "provenance": {
            "region": region,
            "client": {"versionName": client.get("versionName"), "versionCode": client.get("versionCode")},
            "catalog": {"resourceVersion": catalog.get("resourceVersion"), "sha256": catalog.get("sha256")},
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
           jackets_dir=None) -> dict:
    """Read the master data, every chart and every BGM cue sheet, measure the charts with `deck`, then write the file
    `out` (gzip when it ends in `.gz`) through a temporary file and a rename; with `jacket` and `jackets_dir`, first
    every song's jacket as `<jackets_dir>/<jacket>.webp`. Returns the summary."""
    from .cache import write_atomic
    out = Path(out)
    try:
        tables, shas = deckdata.read_master(src, key, tables_of(deck is not None, full))
        doc = build(tables, shas, fetch, bgm, region=region, client=client, catalog=catalog,
                    master_source=src.source, master_version=src.version, deck=deck, full=full)
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
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, written)
    charts = [c for s in doc["songs"] for c in s["charts"]]
    return {"out": str(out), "format": FORMAT, "region": region, "masterSource": src.source,
            "masterVersion": src.version, "songs": len(doc["songs"]), "charts": len(charts),
            "deck": doc["provenance"]["deck"]["commit"] if deck is not None else None,
            "unplayable": sum(1 for c in charts if c["deck"] and c["deck"]["unplayable"]),
            "full": full, "bgm": bgm is not None, "jackets": len(jackets),
            "bytes": len(data), "fileBytes": len(written), "sha256": hashlib.sha256(written).hexdigest()}
