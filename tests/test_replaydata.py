"""Runtime inputs retain normalized note order/identities and bind the engine to the pinned model."""
import hashlib
import json
import tarfile

import pytest

from nnnotes import deckdata, musicdata, replaydata
from test_musicdata import CHARTS, KEY, PROV, FakeDeck, bgm, export


def test_export_replay_keeps_main_file_small_and_preserves_runtime_input(tmp_path):
    result = export(tmp_path, replay_dir=tmp_path / "replay")
    doc = json.loads((tmp_path / "music.json").read_text())
    manifest_path = tmp_path / doc["replay"]["manifestUrl"]
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    data = json.loads((manifest_path.parent / "deck-data.json").read_text())
    assert "master" not in doc and "charts" not in doc
    assert result["replay"]["sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_path.parent.name == result["replay"]["sha256"]
    assert data["format"] == deckdata.DECK_FORMAT
    assert [c["scoreId"] for c in data["charts"]] == [10, 20, 30]
    assert len(data["master"]) == len(deckdata.TABLES)
    for item, record in zip(manifest["charts"], data["charts"]):
        payload = (manifest_path.parent / item["url"]).read_bytes()
        chart = json.loads(payload)
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert chart["chart"] == record
        assert chart["musicLengthMs"] == data["provenance"]["replay"]["musicLengthsMs"][str(record["scoreId"])]
    assert manifest["engine"] is None
    labels = json.loads((manifest_path.parent / manifest["snapLabels"]["url"]).read_bytes())
    assert labels["format"] == replaydata.LABEL_FORMAT
    assert labels["region"] == data["provenance"]["region"]
    assert labels["masterVersion"] == data["provenance"]["master"]["version"]
    assert set(labels["tables"]) == set(replaydata.LABEL_TABLES)
    assert "MasterSkillIcon" not in labels["tables"]
    for name, table in labels["tables"].items():
        assert table["sha256"] == data["provenance"]["master"]["tables"][name]["sha256"]
    assert hashlib.sha256((manifest_path.parent / manifest["snapLabels"]["url"]).read_bytes()).hexdigest() == manifest["snapLabels"]["sha256"]


def test_replay_requires_actual_audio_length_and_contains_no_guess(tmp_path):
    with pytest.raises(musicdata.MusicDataError, match="actual BGM lengths"):
        export(tmp_path, bgm=None, replay_dir=tmp_path / "replay")
    assert not (tmp_path / "music.json").exists()


@pytest.mark.parametrize("change", ["region", "version", "table_hash", "missing_table"])
def test_replay_label_binding_rejects_other_snapshots(tmp_path, change):
    import copy
    from test_musicdata import TABLE_ROWS
    from nnnotes import deckdata
    hashes = {name: hashlib.sha256(name.encode()).hexdigest() for name in replaydata.LABEL_TABLES}
    provenance = {"region": "jp", "master": {"version": "v", "tables": {name: {"sha256": digest} for name, digest in hashes.items()}}}
    rows = {name: TABLE_ROWS.get(name, []) for name in replaydata.LABEL_TABLES}
    label_source = replaydata.labels(rows, hashes, provenance)
    export(tmp_path, replay_dir=tmp_path / "replay")
    doc = json.loads((tmp_path / "music.json").read_bytes())
    runtime = json.loads((tmp_path / doc["replay"]["manifestUrl"]).parent.joinpath("deck-data.json").read_bytes())
    music = {**doc, "master": runtime["master"], "charts": runtime["charts"]}
    music["provenance"] = {**copy.deepcopy(doc["provenance"]), **provenance}
    if change == "region":
        label_source["region"] = "tw"
    elif change == "version":
        label_source["masterVersion"] = "other"
    elif change == "table_hash":
        label_source["tables"]["MasterText"]["sha256"] = "f" * 64
    else:
        del label_source["tables"]["MasterText"]
    with pytest.raises(deckdata.DeckDataError, match="replay labels"):
        replaydata.bundle(music, label_source=label_source)


def test_raw_label_rows_preserve_names_levels_and_bind_artwork(tmp_path):
    from test_musicdata import TABLE_ROWS
    import copy
    rows = {name: copy.deepcopy(TABLE_ROWS.get(name, [])) for name in replaydata.LABEL_TABLES}
    rows["MasterSupportSkill"] = [{"_id": 1, "_nameTextID": "first", "_descriptionTextFormatID": "duration"}]
    rows["MasterSupportSkillEffect"] = [{"_id": 1, "_supportSkillID": 1, "_level": 1, "_effectValue": 250},
                                       {"_id": 2, "_supportSkillID": 1, "_level": 2, "_effectValue": 500}]
    hashes = {name: hashlib.sha256(name.encode()).hexdigest() for name in replaydata.LABEL_TABLES}
    provenance = {"region": "tw", "master": {"version": "v", "tables": {name: {"sha256": digest} for name, digest in hashes.items()}}}
    labels = replaydata.labels(rows, hashes, provenance)
    assert labels["tables"]["MasterSupportSkill"]["rows"] == rows["MasterSupportSkill"]
    assert [row["_level"] for row in labels["tables"]["MasterSupportSkillEffect"]["rows"]] == [1, 2]
    assert labels["tables"]["MasterSupportCard"]["rows"] == rows["MasterSupportCard"]
    hashes["MasterText"] = "f" * 64
    with pytest.raises(deckdata.DeckDataError, match="hash differs"):
        replaydata.labels(rows, hashes, provenance)


WASM_MAGIC = b"\x00asm\x01\x00\x00\x00"


def release_package(directory, module, commit=FakeDeck.COMMIT, js=None):
    """A synthetic ournotes-deck WASM release package of `module`: its web binding and build-info.json."""
    js = js if js is not None else f"export class {module.title()}Session {{}}".encode()
    web = directory / "web"
    web.mkdir(parents=True, exist_ok=True)
    stem = f"ournotes_{module}_wasm"
    (web / f"{stem}.js").write_bytes(js)
    (web / f"{stem}_bg.wasm").write_bytes(WASM_MAGIC)
    info = {"version": "0.0.1", "tag": "v0.0.1", "commit": commit, "kind": "wasm", "module": module,
            "target": "wasm32-unknown-unknown",
            "files": {f"web/{stem}.js": hashlib.sha256(js).hexdigest(),
                      f"web/{stem}_bg.wasm": hashlib.sha256(WASM_MAGIC).hexdigest()}}
    (directory / replaydata.BUILD_INFO).write_text(json.dumps(info, indent=2) + "\n")
    return info


def archive(directory):
    """The release archive of a package directory: the directory itself at the archive root."""
    path = directory.parent / f"{directory.name}.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        output.add(directory, arcname=directory.name)
    return path


def test_engine_manifest_rejects_stale_pin_and_changed_wasm(tmp_path):
    engine = tmp_path / "ournotes-replay-wasm-v0.0.1"
    release_package(engine, "replay", commit="0" * 40)
    stale = tmp_path / "stale"
    stale.mkdir()
    stale_pin = f"build-info.json commit '0{{40}}' differs from the pinned deck model {FakeDeck.COMMIT}"
    with pytest.raises(musicdata.MusicDataError, match=stale_pin):
        export(stale, deck=FakeDeck(), replay_dir=stale / "replay", replay_engine=engine)
    assert not (stale / "music.json").exists()
    release_package(engine, "replay")
    valid = tmp_path / "valid"
    valid.mkdir()
    result = export(valid, deck=FakeDeck(), replay_dir=valid / "replay", replay_engine=engine)
    old_manifest_path = valid / result["replay"]["manifestUrl"]
    old_bytes = old_manifest_path.read_bytes()
    manifest = json.loads(old_bytes)
    assert manifest["engine"]["model"]["commit"] == FakeDeck.COMMIT
    release_package(engine, "replay", js=b"export class ReplaySession {}\n// updated engine")
    result = musicdata.export(valid / "music.json", deckdata.master_files(valid / "m"), KEY,
                              CHARTS.__getitem__, bgm, **PROV, deck=musicdata.Deck(module=FakeDeck()),
                              replay_dir=valid / "replay", replay_engine=engine)
    assert valid / result["replay"]["manifestUrl"] != old_manifest_path
    assert old_manifest_path.read_bytes() == old_bytes  # readers of the preceding document keep its resource tree
    (engine / "web" / "ournotes_replay_wasm_bg.wasm").write_bytes(WASM_MAGIC + b"changed")
    changed = tmp_path / "changed"
    changed.mkdir()
    with pytest.raises(musicdata.MusicDataError, match="ournotes_replay_wasm_bg.wasm SHA-256 differs"):
        export(changed, deck=FakeDeck(), replay_dir=changed / "replay", replay_engine=engine)


@pytest.mark.parametrize("packed", [False, True])
def test_recommend_engine_shares_the_model_of_the_deck_data(tmp_path, packed):
    replay = tmp_path / "ournotes-replay-wasm-v0.0.1"
    recommend = tmp_path / "ournotes-recommend-wasm-v0.0.1"
    release_package(replay, "replay")
    info = release_package(recommend, "recommend")
    replay_source, recommend_source = (archive(replay), archive(recommend)) if packed else (replay, recommend)
    plain = tmp_path / "plain"
    plain.mkdir()
    result = export(plain, deck=FakeDeck(), replay_dir=plain / "replay", replay_engine=replay_source)
    assert "recommendEngine" not in json.loads((plain / result["replay"]["manifestUrl"]).read_bytes())
    out = tmp_path / "out"
    out.mkdir()
    result = export(out, deck=FakeDeck(), replay_dir=out / "replay", replay_engine=replay_source,
                    recommend_engine=recommend_source)
    manifest_path = out / result["replay"]["manifestUrl"]
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest["recommendEngine"]
    data = json.loads((manifest_path.parent / manifest["deckData"]["url"]).read_bytes())
    assert list(entry) == ["model", "js", "wasm", "build"]
    assert entry["model"] == manifest["engine"]["model"] == data["provenance"]["deck"]
    assert entry["model"]["commit"] == FakeDeck.COMMIT
    assert [entry[k]["url"] for k in ("js", "wasm", "build")] == [
        "recommend/ournotes_recommend_wasm.js", "recommend/ournotes_recommend_wasm_bg.wasm", "recommend/build-info.json"]
    assert [manifest["engine"][k]["url"] for k in ("js", "wasm", "build")] == [
        "engine/ournotes_replay_wasm.js", "engine/ournotes_replay_wasm_bg.wasm", "engine/build-info.json"]
    for key, name in (("js", "ournotes_recommend_wasm.js"), ("wasm", "ournotes_recommend_wasm_bg.wasm")):
        payload = (manifest_path.parent / entry[key]["url"]).read_bytes()
        assert payload == (recommend / "web" / name).read_bytes()
        assert (entry[key]["sha256"], entry[key]["bytes"]) == (hashlib.sha256(payload).hexdigest(), len(payload))
        assert entry[key]["sha256"] == info["files"][f"web/{name}"]
    build = (manifest_path.parent / entry["build"]["url"]).read_bytes()
    assert build == (recommend / replaydata.BUILD_INFO).read_bytes()
    assert (entry["build"]["sha256"], entry["build"]["bytes"]) == (hashlib.sha256(build).hexdigest(), len(build))


@pytest.mark.parametrize("change,message", [
    ("commit", "recommend engine: build-info.json commit '0{40}' differs from the pinned deck model"),
    ("module", "recommend engine: build-info.json does not describe the recommend WASM package"),
    ("wasm", "recommend engine: web/ournotes_recommend_wasm_bg.wasm SHA-256 differs from build-info.json"),
    ("missing", "recommend engine: missing web/ournotes_recommend_wasm.js"),
    ("not-a-package", "recommend engine: .* is neither a package directory nor a .tar.gz package"),
    ("no-replay-dir", "--recommend-engine needs --replay-dir"),
])
def test_recommend_engine_rejects_another_build(tmp_path, change, message):
    replay = tmp_path / "replay-pkg"
    release_package(replay, "replay")
    pkg = tmp_path / "recommend-pkg"
    release_package(pkg, "recommend", commit="0" * 40 if change == "commit" else FakeDeck.COMMIT)
    if change == "module":
        pkg = tmp_path / "other-pkg"
        release_package(pkg, "replay")
    if change == "wasm":
        (pkg / "web" / "ournotes_recommend_wasm_bg.wasm").write_bytes(WASM_MAGIC + b"changed")
    if change == "missing":
        (pkg / "web" / "ournotes_recommend_wasm.js").unlink()
    if change == "not-a-package":
        pkg = tmp_path / "recommend.tar.gz"
        pkg.write_bytes(b"not an archive")
    options = {} if change == "no-replay-dir" else {"replay_dir": tmp_path / "replay", "replay_engine": replay}
    with pytest.raises(musicdata.MusicDataError, match=message):
        export(tmp_path, deck=FakeDeck(), recommend_engine=pkg, **options)
    assert not (tmp_path / "music.json").exists() and not (tmp_path / "replay").exists()


def test_final_export_rejects_an_invalid_expectation_before_publishing(tmp_path):
    def invalid(chart):
        apt = chart.get("gekisouAptitude")
        if apt and apt["variants"]:
            apt["variants"][0]["check"]["expected"] = [0, 0]
    with pytest.raises(musicdata.MusicDataError, match="expectation check exceeds"):
        export(tmp_path, deck=FakeDeck(change=invalid))
    assert not (tmp_path / "music.json").exists()
