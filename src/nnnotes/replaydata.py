"""Normalized runtime inputs for the shared Rust/WASM replay; no scoring or frame scheduler here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import deckdata
from .cache import write_atomic

FORMAT = "nnnotes.replay-manifest/1"
CHART_FORMAT = "nnnotes.replay-chart/1"
JS = "ournotes_replay.js"
WASM = "ournotes_replay_bg.wasm"
RECOMMEND_JS = "ournotes_recommend.js"
RECOMMEND_WASM = "ournotes_recommend_bg.wasm"


def _json(value) -> bytes:
    return deckdata.encode(deckdata._value(value, "replay input"))


def _package(files: dict[str, bytes], source: Path, model: dict | None, name: str, build_format: str, prefix: str,
             js: str, wasm: str) -> tuple[str, str, str]:
    """Copy a wasm-bindgen package of the pinned deck model (`js`, `wasm`, build.json) into `files` under `prefix`;
    build.json must name the model's commit and both file hashes. Returns the JS, WASM and build.json paths."""
    source = Path(source)
    try:
        built = json.loads((source / "build.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise deckdata.DeckDataError(f"{name}: missing or malformed build.json") from None
    if not model or not model.get("commit"):
        raise deckdata.DeckDataError(f"{name}: the pinned deck model identity is required")
    if built.get("format") != build_format:
        raise deckdata.DeckDataError(f"{name}: build.json format is {built.get('format')!r}, not {build_format}")
    if built.get("commit") != model["commit"]:
        raise deckdata.DeckDataError(f"{name}: build.json commit {built.get('commit')!r} differs from the pinned "
                                     f"deck model {model['commit']}")
    js_path, wasm_path, build_path = f"{prefix}/{js}", f"{prefix}/{wasm}", f"{prefix}/build.json"
    for file, path in ((js, js_path), (wasm, wasm_path)):
        if not (source / file).is_file():
            raise deckdata.DeckDataError(f"{name}: missing {file} in {source}")
        files[path] = (source / file).read_bytes()
    if not files[wasm_path].startswith(b"\x00asm\x01\x00\x00\x00"):
        raise deckdata.DeckDataError(f"{name}: not a WASM v1 module")
    if built.get("jsSha256") != hashlib.sha256(files[js_path]).hexdigest() or \
            built.get("wasmSha256") != hashlib.sha256(files[wasm_path]).hexdigest():
        raise deckdata.DeckDataError(f"{name}: JS/WASM SHA differs from build.json")
    files[build_path] = _json(built)
    return js_path, wasm_path, build_path


def bundle(music: dict, engine_dir: Path | None = None,
           recommend_dir: Path | None = None) -> tuple[dict[str, bytes], dict]:
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
        js, wasm, build = _package(files, engine_dir, model, "replay engine", "ournotes.replay-engine/1", "engine",
                                   JS, WASM)
        engine = {"model": model, "requestFormat": "ournotes.replay/1", "class": "ReplaySession",
                  "methods": ["describeChart", "template", "run"],
                  "js": resource(js), "wasm": resource(wasm), "build": resource(build)}
    recommend = None
    if recommend_dir is not None:
        js, wasm, build = _package(files, recommend_dir, model, "recommend engine", "ournotes.recommend-engine/1",
                                   "recommend", RECOMMEND_JS, RECOMMEND_WASM)
        recommend = {"model": model, "js": resource(js), "wasm": resource(wasm), "build": resource(build)}
    manifest = {"format": FORMAT, "deckData": {"format": deckdata.DECK_FORMAT, **resource("deck-data.json")},
                "charts": charts, "engine": engine,
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
