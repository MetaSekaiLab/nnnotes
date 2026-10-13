"""Normalized runtime inputs for the shared Rust/WASM replay; no scoring or frame scheduler here."""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

from . import deckdata
from .cache import write_atomic

FORMAT = "nnnotes.replay-manifest/1"
CHART_FORMAT = "nnnotes.replay-chart/1"
BUILD_INFO = "build-info.json"
LABEL_FORMAT = "nnnotes.replay-labels/1"
LABEL_TABLES = ("MasterSupportSkill", "MasterSupportSkillEffect", "MasterGekisouSupportSkill",
                "MasterGekisouSupportSkillEffect", "MasterText", "MasterSkillConditionSet", "MasterSkillCondition",
                "MasterSkillCumulativeCondition", "MasterSkillTarget", "MasterCharacter", "MasterMemberCard", "MasterSupportCard", "MasterBand")


def _json(value) -> bytes:
    return deckdata.encode(deckdata._value(value, "replay input"))


def labels(tables: dict, hashes: dict, provenance: dict) -> dict:
    """Original names/templates, level effects and artwork IDs from the same served master tables.

    Consumers use their existing localization/description formatter. No formula or inferred growth is exported.
    MasterSkillIcon is deliberately absent until the producer also records its source hash.
    """
    master = provenance.get("master") or {}
    recorded = master.get("tables") or {}
    result = {}
    for name in LABEL_TABLES:
        digest = hashes.get(name)
        if name not in tables or not isinstance(tables[name], list):
            raise deckdata.DeckDataError(f"replay labels: missing source table {name}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or recorded.get(name, {}).get("sha256") != digest:
            raise deckdata.DeckDataError(f"replay labels: {name} hash differs from master provenance")
        result[name] = {"sha256": digest, "rows": tables[name]}
    return {"format": LABEL_FORMAT, "region": provenance["region"], "masterVersion": master.get("version"),
            "tables": result}


def _read_package(source: Path, paths: tuple[str, ...], name: str) -> dict[str, bytes]:
    """Those of the files `paths` that a release package holds: a directory, or a .tar.gz archive holding the package
    directory."""
    if source.is_dir():
        return {path: (source / path).read_bytes() for path in paths if (source / path).is_file()}
    try:
        with tarfile.open(source, "r:gz") as archive:
            return {member.name.partition("/")[2]: archive.extractfile(member).read()
                    for member in archive.getmembers() if member.isfile() and member.name.partition("/")[2] in paths}
    except (OSError, tarfile.TarError):
        raise deckdata.DeckDataError(
            f"{name}: {source} is neither a package directory nor a .tar.gz package") from None


def _package(files: dict[str, bytes], source: Path, model: dict | None, module: str,
             prefix: str) -> tuple[str, str, str]:
    """Copy the web binding of an ournotes-deck WASM release package of `module` (`replay` or `recommend`), the
    `ournotes-<module>-wasm-v<version>` .tar.gz or its directory, into `files` under `prefix`. Its build-info.json must
    describe the WASM package of `module` built from the pinned model's commit, with the SHA-256 of both files.
    Returns the JS, WASM and build-info.json paths."""
    name = f"{module} engine"
    stem = f"web/ournotes_{module}_wasm"
    js, wasm = f"{stem}.js", f"{stem}_bg.wasm"
    package = _read_package(Path(source), (BUILD_INFO, js, wasm), name)
    if not model or not model.get("commit"):
        raise deckdata.DeckDataError(f"{name}: the pinned deck model identity is required")
    if BUILD_INFO not in package:
        raise deckdata.DeckDataError(f"{name}: missing {BUILD_INFO} in {source}")
    try:
        info = json.loads(package[BUILD_INFO])
    except ValueError:
        raise deckdata.DeckDataError(f"{name}: malformed {BUILD_INFO}") from None
    if not isinstance(info, dict) or info.get("kind") != "wasm" or info.get("module") != module:
        raise deckdata.DeckDataError(f"{name}: {BUILD_INFO} does not describe the {module} WASM package")
    if info.get("commit") != model["commit"]:
        raise deckdata.DeckDataError(f"{name}: {BUILD_INFO} commit {info.get('commit')!r} differs from the pinned "
                                     f"deck model {model['commit']}")
    hashes = info.get("files") if isinstance(info.get("files"), dict) else {}
    for path in (js, wasm):
        if path not in package:
            raise deckdata.DeckDataError(f"{name}: missing {path} in {source}")
        if hashes.get(path) != hashlib.sha256(package[path]).hexdigest():
            raise deckdata.DeckDataError(f"{name}: {path} SHA-256 differs from {BUILD_INFO}")
    if not package[wasm].startswith(b"\x00asm\x01\x00\x00\x00"):
        raise deckdata.DeckDataError(f"{name}: not a WASM v1 module")
    paths = tuple(f"{prefix}/{path.rsplit('/', 1)[-1]}" for path in (js, wasm, BUILD_INFO))
    for path, data in zip(paths, (package[js], package[wasm], package[BUILD_INFO])):
        files[path] = data
    return paths


def bundle(music: dict, engine_dir: Path | None = None, recommend_dir: Path | None = None, *,
           label_source: dict | None = None) -> tuple[dict[str, bytes], dict]:
    """Canonical normalized --full inputs and SHA manifest. Actual ACB length is mandatory.

    Runtime arrays retain native enumeration order. ReplaySession.template/run in Rust produce frames and scores.
    """
    if "master" not in music or "charts" not in music:
        raise deckdata.DeckDataError("replay input needs the normalized --full master/charts")
    lengths = {}
    for song in music["songs"]:
        ms = ((song.get("bgm") or {}).get("length") or {}).get("lengthMs")
        if type(ms) is not int or ms <= 0:
            raise deckdata.DeckDataError(f"replay song {song['id']}: a positive actual BGM lengthMs is required")
        for chart in song["charts"]:
            key = str(chart["scoreId"])
            if key in lengths:
                raise deckdata.DeckDataError(f"replay chart {key}: duplicate song chart")
            lengths[key] = ms
    provenance = dict(music["provenance"])
    provenance["replay"] = {"musicLengthsMs": lengths,
                            "musicLengthSource": "ACB CueTable lengthMs; no last-note estimate"}
    source_records = sorted(music["charts"], key=lambda chart: chart["scoreId"])
    records = [c for c in source_records if str(c["scoreId"]) in lengths]
    if len(records) != len(lengths) or {str(c["scoreId"]) for c in records} != set(lengths):
        raise deckdata.DeckDataError("replay runtime charts differ from the songs' charts")
    data = {"format": deckdata.DECK_FORMAT, "provenance": provenance,
            "master": music["master"], "charts": records}
    files = {"deck-data.json": _json(data)}

    def resource(path: str) -> dict:
        return {"url": path, "sha256": hashlib.sha256(files[path]).hexdigest(), "bytes": len(files[path])}

    label_ref = None
    if label_source is not None:
        if (label_source.get("format") != LABEL_FORMAT or label_source.get("region") != provenance.get("region")
                or label_source.get("masterVersion") != (provenance.get("master") or {}).get("version")):
            raise deckdata.DeckDataError("replay labels: snapshot identity differs from runtime inputs")
        source_tables = label_source.get("tables") or {}
        recorded = (provenance.get("master") or {}).get("tables") or {}
        for name in LABEL_TABLES:
            if (not isinstance(source_tables.get(name), dict) or not isinstance(source_tables[name].get("rows"), list)
                    or not re.fullmatch(r"[0-9a-f]{64}", str(source_tables[name].get("sha256")))
                    or source_tables[name].get("sha256") != recorded.get(name, {}).get("sha256")):
                raise deckdata.DeckDataError(f"replay labels: {name} differs from runtime master provenance")
        files["snap-labels.json"] = _json(label_source)
        label_ref = {"format": LABEL_FORMAT, **resource("snap-labels.json")}

    charts = []
    for record in records:
        sid = record["scoreId"]
        path = f"charts/{sid}.json"
        files[path] = _json({"format": CHART_FORMAT, "scoreId": sid,
                             "musicLengthMs": lengths[str(sid)], "chart": record})
        charts.append({"scoreId": sid, **resource(path), "musicLengthMs": lengths[str(sid)],
                       "noteCount": len(record["notes"]["id"]), "assetSha256": record["asset"]["sha256"]})
    model = music["provenance"].get("deck")
    engine = None
    if engine_dir is not None:
        js, wasm, build = _package(files, engine_dir, model, "replay", "engine")
        engine = {"model": model, "requestFormat": "ournotes.replay/1", "class": "ReplaySession",
                  "methods": ["describeChart", "template", "run"],
                  "js": resource(js), "wasm": resource(wasm), "build": resource(build)}
    recommend = None
    if recommend_dir is not None:
        js, wasm, build = _package(files, recommend_dir, model, "recommend", "recommend")
        recommend = {"model": model, "js": resource(js), "wasm": resource(wasm), "build": resource(build)}
    manifest = {"format": FORMAT, "deckData": {"format": deckdata.DECK_FORMAT, **resource("deck-data.json")},
                "charts": charts, "engine": engine,
                **({"snapLabels": label_ref} if label_ref else {}),
                **({"recommendEngine": recommend} if recommend is not None else {}),
                "unlistedScoreIds": [c["scoreId"] for c in source_records if str(c["scoreId"]) not in lengths],
                "clock": "Explicit frames from ReplaySession.template; no Python/JS scoring or scheduling"}
    files["manifest.json"] = _json(manifest)
    return files, manifest


def write(directory: Path, files: dict[str, bytes]) -> None:
    """Write validated normalized artifacts; callers publish the marker last."""
    directory = Path(directory)
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, data)
