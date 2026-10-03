"""Deck input: every live chart as the client builds it at runtime and the master data tables the deck model
(ournotes-deck) reads, for one master data version. `nnnotes music-data` hands it to the deck model in memory
(DECK_FORMAT, the reader's format) and writes it into the music data file with `--full` (docs/music-data.md).

Master data is decoded from the files as served: a directory with `MasterManifest.json` and the `.bin` files it
lists (`nnnotes master download`), or the same layout inside the APK (`assets/Master/`). Each file is checked against
the manifest's SHA-256 and decoded with master.decode. Master data decoded elsewhere is read as it is: a directory of
decoded tables (`<Table>.json`) with the `MasterManifest.json` of the files they were decoded from, whose version and
SHA-256 are taken as the manifest lists them (decoded_master). Charts are the TextAssets
`Live/MusicScore/<file name>` of every MasterLiveMusicScore row, read from the catalog and converted by
score.runtime_score; notes are listed in the order the client enumerates them.

The encoding is canonical (encode): minified UTF-8 with one trailing LF, keys in a fixed order, charts sorted by
score id, numbers the master data writes with a fraction or exponent as the shortest decimal that reads back as the
same binary32 value (infinity as `1e999` / `-1e999`; a NaN is an error). The same inputs give the same bytes; a `.gz`
output is gzip with no file name and a zero modification time. Any missing or unreadable input is a DeckDataError
naming it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .apkset import ApkSet
from typing import Callable

DECK_FORMAT = "nnnotes.deck-data/1"         # the deck model's input format (ournotes-sim data::FORMAT)
CHART_FORMAT = "nnnotes.live-score/1"      # score.convert's format: the converter the notes come from
CHART_PREFIX = "Live/MusicScore/"          # + MasterLiveMusicScore._musicScoreTextFileName
MANIFEST = "MasterManifest.json"
APK_MASTER = "assets/Master/"
EMBEDDED = "embedded"                      # provenance region and master source of the APK's master data
API = "api"

_SKILL_EFFECT = ("_level _skillTriggerConditionGroup _skillTriggerType _skillConditionGroup "
                 "_skillReleaseConditionGroup _skillTargetIDs _skillEffectType _activationTimeSecond _effectValue "
                 "_maxEffectValue _effectLimitCount _skillCumulativeConditionID _effectExecuteLimitCount "
                 "_effectExecuteLimitResetConditionGroup")
_WHOLE = None                              # every column of the table, in the order the rows first have them

# (table, columns), in file order. A table with a column list exports those columns; a whole table all of its own.
TABLES: tuple[tuple[str, tuple[str, ...] | None], ...] = tuple(
    (t, None if c is _WHOLE else tuple(c.split())) for t, c in (
    ("MasterMemberCard", "_id _characterID _rarity _cardType _bestMusicTagIDs _performancePowerMax _technicPowerMax "
                         "_visualPowerMax _memberCardLevelGroup _memberCardAwakeGroup _memberCardRankGroup "
                         "_leaderSkillID _liveSkillID _gekisouSkillID"),
    ("MasterMemberCardLevel", "_id _group _level _exp _performanceRate _technicRate _visualRate"),
    ("MasterMemberCardLevelLimit", "_id _rarity _awakeCount _limitLevel"),
    ("MasterMemberCardAwake", "_id _group _awakeCount _performanceRate _technicRate _visualRate"),
    ("MasterMemberCardRank", "_id _group _rank _performanceRate _technicRate _visualRate _leaderSkillLevel "
                             "_musicTypeBonusRate _musicTagBonusRate"),
    ("MasterSupportCard", "_id _characterIDs _rarity _cardType _performancePowerMax _technicPowerMax "
                          "_visualPowerMax _supportCardLevelGroup _supportCardRankGroup _supportSkillId01 "
                          "_supportSkillId02 _gekisouSupportSkillId01 _gekisouSupportSkillId02"),
    ("MasterSupportCardLevel", "_id _group _level _exp _performanceRate _technicRate _visualRate"),
    ("MasterSupportCardRank", "_id _group _rank _limitLevel _cardTypeLinkBonusRate _supportSkill01Level "
                              "_supportSkill02Level _gekisouSupportSkill01Level _gekisouSupportSkill02Level"),
    ("MasterCharacter", "_id _bandID"),
    ("MasterBand", "_id"),
    ("MasterCharacterRank", "_id _rank _exp _bonus"),
    ("MasterCharacterTotalRank", "_id _totalRank _bonus"),
    ("MasterBandItemSkillEffect", "_id _bandItemId _level _skillTargetIDs _skillEffectType _effectValue"),
    ("MasterBandItem", "_id _bandId"),
    ("MasterBandItemLevel", "_id _bandItemId _level _playerRank"),
    ("MasterVip", "_id _vipRank"),
    ("MasterVipRankBonus", "_id _vipRank _vipBonusType _value"),
    ("MasterMemoryMusicGroup", "_id _skillTargetIds"),
    ("MasterMemoryMusic", "_id _groupId"),
    ("MasterMemoryMusicBonus", "_id _groupId _scoreRank _performance _technic _visual"),
    ("MasterMemoryMemberLevel", "_id _point _performance _technic _visual"),
    ("MasterMemorySupportLevel", "_id _point _performance _technic _visual"),
    ("MasterSkillTarget", "_id _skillTargetType _characterID _bandID _cardType _tagID _judgement _liveMusicType "
                          "_gekisouMissionType _liveSkillCategories _gekisouSkillCategories"),
    ("MasterSkillCondition", "_id _conditionType _conditionValues _isPositive _conditionTargetIDs"),
    ("MasterSkillConditionSet", "_id _group _conditionIds"),
    ("MasterSkillCumulativeCondition", "_id _skillCumulativeConditionType _conditionValues _conditionTargetIDs "
                                       "_maxCumulativeCount"),
    ("MasterSkillEffectSetting", "_id _skillEffectType _phase"),
    ("MasterLeaderSkillEffect", "_id _leaderSkillID _level _skillConditionGroup _skillTargetIDs _skillEffectType "
                                "_effectValue _skillCumulativeConditionID"),
    ("MasterLiveSkill", "_id _skillCategories"),
    ("MasterLiveSkillEffect", "_id _liveSkillID _level _skillConditionGroup _skillReleaseConditionGroup "
                              "_skillTargetIDs _skillEffectType _activationTimeSecond _effectValue _maxEffectValue "
                              "_effectLimitCount _skillCumulativeConditionID _effectExecuteLimitCount "
                              "_effectExecuteLimitResetConditionGroup"),
    ("MasterSupportSkill", "_id"),
    ("MasterSupportSkillEffect", "_id _supportSkillID " + _SKILL_EFFECT),
    ("MasterGekisouSkill", "_id _gekisouMissionType _skillCategories"),
    ("MasterGekisouSkillEffect", "_id _gekisouSkillID " + _SKILL_EFFECT),
    ("MasterGekisouSupportSkill", "_id _gekisouSupportSkillExecTiming _gekisouMissionType"),
    ("MasterGekisouSupportSkillEffect", "_id _gekisouSupportSkillID " + _SKILL_EFFECT),
    ("MasterLiveNoteParameter", "_id _noteOperateType _scorePercent"),
    ("MasterLiveJudgementParameter", "_id _noteSimulateJudgement _scorePercent _damage"),
    ("MasterLiveJudgementTiming", "_id _assistLevel _judgementPriority _noteJudgementType _noteSimulateJudgement "
                                  "_beforeMs _afterMs"),
    ("MasterLiveComboScoreBonus", "_id _comboBonusType _requiredComboCount _bonusFactor"),
    ("MasterLiveSettings", _WHOLE),
    ("MasterParameter", _WHOLE),
    ("MasterLiveGekisouLuckBasePoint", "_id _noteCategory _noteSimulateJudgement _weight _basePoint"),
    ("MasterLiveGekisouLuckBonusLot", "_id _chanceLotType _lotResult _weight"),
    ("MasterLiveGekisouRankingScoreBonus", "_id _missionPattern _rank _count _scoreBonusPercent"),
    ("MasterLiveMusic", "_id _musicType _bestMusicTagIDs _liveScoreRankGroup _easyID _normalID _hardID _expertID "
                        "_gekisouMission1 _gekisouMission2 _gekisouMission3"),
    ("MasterLiveMusicScore", "_id _musicScoreTextFileName _musicScoreLevel _fullComboCount"),
    ("MasterArenaMusic", _WHOLE),
    ("MasterChallengeMusic", _WHOLE),
    ("MasterLiveScoreRank", "_id _group _liveScoreRank _requiredScore _battleLiveRequiredScore"),
    ("MasterEvent", _WHOLE),
    ("MasterEventEffect", _WHOLE),
    ("MasterEventAchievementReward", "_id _eventId _eventPoint _rewardIds"),
    ("MasterEventAchievementLoopReward", "_id _eventId _loopStartEventPoint _loopEventPoint _rewardIds"),
    ("MasterLiveEventReward", "_id _group _eventGroup _scoreRank _resourceType _resourceId _resourceCount "
                              "_probability"),
    ("MasterChallengeLiveEventReward", "_id _group _eventGroup _scoreRank _resourceType _resourceId "
                                       "_resourceCount _probability"),
    ("MasterLiveEventPoint", _WHOLE),
    ("MasterChallengeLiveEventPoint", _WHOLE),
    ("MasterLiveChallengePoint", _WHOLE),
    ("MasterLiveMusicBoostBonus", "_id _consumedLiveBoostCount _liveMusicRewardRate _playerExpRate "
                                  "_memberCardExpRate _friendshipExpRate _eventPointRate"),
    ("MasterChallengeMusicBoostBonus", _WHOLE),
))


class DeckDataError(ValueError):
    """An input the deck data cannot be made from (the message names it)."""


# ---------------------------------------------------------------- master data as served
@dataclass(frozen=True)
class MasterSource:
    """Master data files as served: `version` and the SHA-256 of each file name from the manifest, and a reader of
    files by name. `decoded`: the reader has the decoded tables instead (`<Table>.json` for `<Table>.bin`)."""
    source: str                                         # API or EMBEDDED
    where: str                                          # the directory or APK, for messages
    version: str | None
    hashes: dict[str, str]                              # file name -> sha256 (lowercase hex; "" when not listed)
    read: Callable[[list[str]], dict[str, bytes]]       # file names -> {name: bytes}; a missing file raises KeyError
    decoded: bool = False


def _manifest(raw: bytes, where: str) -> tuple[str | None, dict[str, str]]:
    try:
        doc = json.loads(raw.decode("utf-8"))
        files = doc["files"]
        hashes = {f["name"]: str(f.get("hash") or "").lower() for f in files}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DeckDataError(f"{where}: {MANIFEST} is not a master data manifest") from None
    version = doc.get("version")
    return (str(version) if version is not None else None), hashes


def _directory(d: Path, what: str, decoded: bool = False) -> MasterSource:
    m = d / MANIFEST
    if not m.is_file():
        raise DeckDataError(f"{d}: no {MANIFEST} ({what})")
    version, hashes = _manifest(m.read_bytes(), str(d))

    def read(names):
        out = {}
        for n in names:
            try:
                out[n] = (d / n).read_bytes()
            except FileNotFoundError:
                raise KeyError(n) from None
        return out
    return MasterSource(API, str(d), version, hashes, read, decoded)


def master_files(directory) -> MasterSource:
    """The master data files of a directory written by `nnnotes master download` (MasterManifest.json + .bin)."""
    return _directory(Path(directory), "a directory written by `nnnotes master download`")


def decoded_master(directory) -> MasterSource:
    """Decoded master data: a directory of decoded tables, `<Table>.json` with the `_allData` rows (as `nnnotes master
    decode` writes them), and the `MasterManifest.json` of the files they were decoded from (a master data snapshot
    published with its manifest). The version and each file's SHA-256 are the manifest's: the decoded tables cannot
    be checked against the files as served."""
    return _directory(Path(directory), "decoded master data needs the manifest of the files it was decoded from",
                      decoded=True)


def apk_master(apk) -> MasterSource:
    """The master data files the APK ships (`assets/Master/`: MasterManifest.json + .bin)."""
    apk = Path(apk)
    try:
        with ApkSet(apk) as z:
            raw = z.read(APK_MASTER + MANIFEST)
    except KeyError:
        raise DeckDataError(f"{apk}: no {APK_MASTER}{MANIFEST}") from None
    except (zipfile.BadZipFile, OSError):
        raise DeckDataError(f"{apk}: not a readable APK") from None
    version, hashes = _manifest(raw, f"{apk} {APK_MASTER}")

    def read(names):
        with ApkSet(apk) as z:
            have = set(z.namelist())
            missing = [n for n in names if APK_MASTER + n not in have]
            if missing:
                raise KeyError(missing[0])
            return {n: z.read(APK_MASTER + n) for n in names}
    return MasterSource(EMBEDDED, f"{apk} {APK_MASTER}", version, hashes, read)


def read_master(src: MasterSource, key, tables=None) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """The rows (`_allData`) of every table of `tables` (default: TABLES) and the SHA-256 of each file as served.
    `key`: a master.MasterKey (unused for decoded master data: None)."""
    from . import master
    names = {t: f"{t}.bin" for t in (tables if tables is not None else (t for t, _ in TABLES))}
    unlisted = [t for t, n in names.items() if n not in src.hashes]
    if unlisted:
        raise DeckDataError(f"master data {src.where}: {MANIFEST} lists no {', '.join(unlisted)}")
    files = {t: f"{t}.json" if src.decoded else n for t, n in names.items()}
    try:
        data = src.read(list(files.values()))
    except KeyError as e:
        raise DeckDataError(f"master data {src.where}: no file {e.args[0]}") from None
    rk = None if src.decoded else master.round_keys(key.key)
    tables, shas = {}, {}
    for t, n in names.items():
        raw, f = data[files[t]], files[t]
        if src.decoded:                                  # the manifest's SHA-256 of the file as served
            sha = src.hashes[n]
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise DeckDataError(f"master data {src.where}: {MANIFEST} lists no SHA-256 for {n}")
        else:
            sha = hashlib.sha256(raw).hexdigest()
            if src.hashes[n] and sha != src.hashes[n]:
                raise DeckDataError(f"master data {src.where}: {n}: sha256 differs from the manifest")
        try:
            doc = json.loads((raw if src.decoded else master.decode(raw, key, rk)).decode("utf-8"))
        except Exception as e:                           # padding, gzip, UTF-8 or JSON: the file cannot be read
            raise DeckDataError(f"master data {src.where}: {f} cannot be {'read' if src.decoded else 'decoded'} "
                                f"({type(e).__name__}: {str(e)[:120]})") from None
        rows = doc.get("_allData") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise DeckDataError(f"master data {src.where}: {f} has no `_allData` rows")
        tables[t], shas[t] = rows, sha
    return tables, shas


# ---------------------------------------------------------------- canonical values
class _Num(str):
    """A number token, written as it is."""


def f32_token(v: float) -> _Num:
    """The shortest decimal that reads back as binary32(v); +/-infinity as 1e999 / -1e999; NaN raises ValueError."""
    import numpy as np
    with np.errstate(over="ignore"):
        x = np.float32(v)
    if np.isnan(x):
        raise ValueError("NaN (no JSON representation)")
    if np.isinf(x):
        return _Num("1e999" if x > 0 else "-1e999")
    if x == 0 or 1e-4 <= abs(x) < 1e16:
        return _Num(np.format_float_positional(x, unique=True, trim="0"))
    return _Num(np.format_float_scientific(x, unique=True, trim="0"))


def _value(v, where: str):
    if isinstance(v, float):
        try:
            return f32_token(v)
        except ValueError as e:
            raise DeckDataError(f"{where}: {e}") from None
    if isinstance(v, list):
        return [_value(x, where) for x in v]
    if isinstance(v, dict):
        return {k: _value(x, where) for k, x in v.items()}
    return v


def master_subset(tables: dict[str, list[dict]]) -> dict:
    """{table: {"columns", "rows"}} for TABLES, rows in the served order. A table with a column list must have each
    of them in every row; a whole table exports the columns its rows have (none when it has no rows)."""
    out = {}
    for name, cols in TABLES:
        rows = tables[name]
        if cols is None:
            seen: dict[str, None] = {}
            for r in rows:
                seen.update(dict.fromkeys(r))
            cols = tuple(seen)
        for i, r in enumerate(rows):
            missing = [c for c in cols if c not in r]
            if missing:
                raise DeckDataError(f"{name}: row {i} (_id {r.get('_id')}) has no column {missing[0]}")
        out[name] = {"columns": list(cols),
                     "rows": [[_value(r[c], f"{name} row {i} {c}") for c in cols] for i, r in enumerate(rows)]}
    return out


# ---------------------------------------------------------------- charts
def chart_key(file_name: str) -> str:
    return CHART_PREFIX + file_name


def chart_record(score_id: int, key: str, raw: bytes) -> dict:
    """The record of one chart: the TextAsset bytes as shipped -> runtime notes in enumeration order, skill events
    in chart order, fever ranges sorted by start."""
    from . import score
    try:
        root = score.load_bytes(raw)
    except (ValueError, OSError, EOFError):
        raise DeckDataError(f"chart {key}: not a chart (gzip or JSON cannot be read)") from None
    try:
        rs = score.runtime_score(root)
    except Exception as e:                               # the converter's own errors and malformed chart fields
        raise DeckDataError(f"chart {key}: cannot be converted ({type(e).__name__}: {e})") from None
    ids, ops, judgements, times = [], [], [], []
    seen = set()
    for n in rs.notes:
        if n.id in seen:
            raise DeckDataError(f"chart {key}: note id {n.id} occurs twice")
        seen.add(n.id)
        try:
            jt = score.judgement_type(n.op, n.crit)
        except ValueError:
            raise DeckDataError(f"chart {key}: note {n.id} has operate type {n.op}, which has no judgement type") \
                from None
        ids.append(int(n.id))
        ops.append(int(n.op))
        judgements.append(int(jt))
        times.append(int(n.pos.ms))
    return {
        "scoreId": score_id,
        "asset": {"key": key, "sha256": hashlib.sha256(raw).hexdigest()},
        "notes": {"id": ids, "op": ops, "judgementType": judgements, "timeMs": times},
        "skillEvents": {"timeMs": [int(p.ms) for _, p in rs.skills]},
        "fevers": {"startMs": [int(a.ms) for _, a, _ in rs.fevers], "endMs": [int(b.ms) for _, _, b in rs.fevers]},
    }


def charts(score_rows: list[dict], fetch: Callable[[str], bytes]) -> list[dict]:
    """One record per MasterLiveMusicScore row, sorted by `_id`. `fetch(file name)`: the chart TextAsset's bytes;
    KeyError when there is none."""
    dup = sorted(i for i, n in Counter(r["_id"] for r in score_rows).items() if n > 1)
    if dup:
        raise DeckDataError(f"MasterLiveMusicScore: _id {dup[0]} occurs twice")
    out = []
    for r in sorted(score_rows, key=lambda r: r["_id"]):
        key = chart_key(r["_musicScoreTextFileName"])
        try:
            raw = fetch(r["_musicScoreTextFileName"])
        except KeyError:
            raise DeckDataError(f"chart {key} (MasterLiveMusicScore {r['_id']}): no such asset") from None
        out.append(chart_record(r["_id"], key, raw))
    return out


def catalog_fetch(cat) -> Callable[[str], bytes]:
    """fetch() for charts from a catalog: score.fetch_chart, KeyError for a key the catalog does not have."""
    from . import score

    def fetch(file_name: str) -> bytes:
        if not cat.has(chart_key(file_name)):
            raise KeyError(chart_key(file_name))
        return score.fetch_chart(cat, file_name)
    return fetch


# ---------------------------------------------------------------- provenance
def apk_client(apk) -> dict:
    """{versionName, versionCode} of an APK's AndroidManifest.xml (None when absent)."""
    from .player import MANIFEST_IN_APK, manifest_version_code, manifest_version_name
    try:
        with ApkSet(apk) as z:
            data = z.read(MANIFEST_IN_APK) if MANIFEST_IN_APK in z.namelist() else None
    except (zipfile.BadZipFile, OSError):
        raise DeckDataError(f"{apk}: not a readable APK") from None
    if data is None:
        return {"versionName": None, "versionCode": None}
    return {"versionName": manifest_version_name(data), "versionCode": manifest_version_code(data)}


def catalog_info(cat, store_root=None) -> dict:
    """{resourceVersion, sha256} of a catalog's remote catalog file (resource_version)."""
    sha = hashlib.sha256(cat.sources()["remote"]).hexdigest()
    source = getattr(cat, "source", None)
    return {"resourceVersion": source.version if source else (getattr(cat, "resource_version", None) or resource_version(store_root, sha)), "sha256": sha,
            **({"resourceHash": source.hash} if source else {})}


def resource_version(store_root, remote_sha: str) -> str | None:
    """The resource version recorded for the remote catalog `remote_sha` by the catalog versions of a store
    (the latest import that has one), None when none is recorded. Read only."""
    if store_root is None:
        return None
    from .catalogdb import CatalogDB
    try:
        versions = CatalogDB(store_root).versions()
    except (OSError, ValueError):
        return None
    for v in reversed(versions):
        if v.get("remote", {}).get("sha256") == remote_sha and v.get("resourceVersion"):
            return v["resourceVersion"]
    return None


# ---------------------------------------------------------------- the deck model's input
def build(tables: dict[str, list[dict]], chart_records: list[dict], provenance: dict) -> dict:
    """The deck model's input document (DECK_FORMAT): the TABLES subset of `tables` and chart records (charts)."""
    return {"format": DECK_FORMAT, "provenance": provenance, "master": master_subset(tables),
            "charts": chart_records}


def _emit(v, out: list) -> None:
    if isinstance(v, _Num):
        out.append(v)
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int):
        out.append(str(int(v)))
    elif isinstance(v, dict):
        out.append("{")
        for i, (k, x) in enumerate(v.items()):
            if not isinstance(k, str):
                raise TypeError(f"key {k!r} is not a string")
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _emit(x, out)
        out.append("}")
    elif isinstance(v, (list, tuple)):
        if all(type(x) is int for x in v):
            out.append("[" + ",".join(map(str, v)) + "]")
            return
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _emit(x, out)
        out.append("]")
    else:                                            # a float must have been made a _Num (f32_token)
        raise TypeError(f"no canonical form for {type(v).__name__}")


def encode(doc: dict) -> bytes:
    """The canonical bytes of a document: minified UTF-8, keys in their order, one trailing LF."""
    out: list[str] = []
    _emit(doc, out)
    out.append("\n")
    return "".join(out).encode("utf-8")


def file_bytes(data: bytes, gz: bool) -> bytes:
    """The bytes written: `data`, or with `gz` gzip with no file name and modification time 0."""
    return gzip.compress(data, compresslevel=9, mtime=0) if gz else data
