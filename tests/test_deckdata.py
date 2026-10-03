"""The deck input (deckdata: master data as served, chart records, canonical encoding), the runtime note order of the
chart converter (score.runtime_score) and the APK version code, on synthetic master data, charts and manifests. The
music data file that carries it is tested in test_musicdata."""
import gzip
import hashlib
import json
import shutil
import struct
import zipfile

import numpy as np
import pytest

import synth
from nnnotes import cli, deckdata, jsonio, master, player, score
from nnnotes.catalogdb import CatalogDB
from nnnotes.master import MasterKey

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)

# a slide 0 -> 1920 with a connection note (t 1000) and a hidden node (t 1440), a tap on one of the slide's eighth
# positions (t 960), a critical tap, a guide started by a tap, a trace and a flick; skill events out of order and
# fever ranges not sorted by start
CHART = {"score": {"events": {"bpm": [{"t": 0, "bpm": 120}], "sig": [{"t": 0, "sig": [4, 4]}],
                              "skill": [2400, 480], "fever": [[1920, 2400], [0, 960]]},
                   "notes": [{"type": "long", "node": [{"t": 0, "pos": 0, "size": 3},
                                                       {"t": 1000, "pos": 2, "size": 3},
                                                       {"t": 1440, "pos": 4, "size": 3, "visible": False},
                                                       {"t": 1920, "pos": 6, "size": 3}]},
                             {"type": "tap", "t": 960, "pos": 12, "size": 3},
                             {"type": "tap", "t": 2400, "pos": 12, "size": 3, "crit": True},
                             {"type": "tap", "t": 2880, "pos": 12, "size": 2},
                             {"type": "guide", "node": [{"t": 2880, "pos": 12, "size": 2},
                                                        {"t": 3840, "pos": 14, "size": 2}]},
                             {"type": "trace", "t": 4320, "pos": 8, "size": 2},
                             {"type": "flick", "t": 4800, "pos": 2, "size": 3, "dir": "left"}]}}
# (id, op) in NoteDictionary enumeration order: the slide's combo ticks on positions of their own come after the
# slide end; the tick on the tap's position (40001, ComboSkip before the connection note) follows the tap
RUNTIME = [(1, 20), (2, 1), (40001, 121), (3, 21), (4, 122), (5, 22), (10001, 120), (20001, 120), (30001, 120),
           (50001, 120), (60001, 120), (70001, 120), (6, 1), (7, 101), (8, 103), (9, 60), (10, 40)]
TIMES = [0, 1000, 1000, 1041, 1500, 2000, 250, 500, 750, 1291, 1541, 1791, 2500, 3000, 4000, 4500, 5000]
JUDGEMENTS = [10, 1, 1, 21, 1, 11, 21, 21, 21, 21, 21, 21, 2, 1, 1, 21, 5]


# ---------------------------------------------------------------- converter order
def test_runtime_notes_in_note_dictionary_order():
    rs = score.runtime_score(CHART)
    assert [(n.id, n.op) for n in rs.notes] == RUNTIME
    assert [n.pos.ms for n in rs.notes] == TIMES
    assert [p.ms for _, p in rs.skills] == [2500, 500]                    # chart order
    assert [(a.ms, b.ms) for _, a, b in rs.fevers] == [(0, 1000), (2000, 2500)]   # sorted by start


def test_combo_tick_on_a_new_position_follows_the_slide_end():
    chart = {"score": {"events": {"bpm": [{"t": 0, "bpm": 120}]},
                       "notes": [{"type": "long", "node": [{"t": 0, "pos": 0, "size": 3},
                                                           {"t": 960, "pos": 6, "size": 3}]},
                                 {"type": "tap", "t": 1920, "pos": 12, "size": 3}]}}
    rs = score.runtime_score(chart)
    assert [(n.id, n.op) for n in rs.notes] == [(1, 20), (2, 22), (10001, 120), (20001, 120), (30001, 120), (3, 1)]
    assert [n["id"] for n in score.convert(chart)["notes"]] == [1, 10001, 20001, 30001, 2, 3]   # position order


def test_convert_keeps_position_order_and_its_document():
    c = score.convert(CHART)
    assert [(n["id"], n["op"]) for n in c["notes"]] == [
        (1, 20), (10001, 120), (20001, 120), (30001, 120), (2, 1), (40001, 121), (3, 21), (50001, 120), (4, 122),
        (60001, 120), (70001, 120), (5, 22), (6, 1), (7, 101), (8, 103), (9, 60), (10, 40)]
    assert sorted(n["id"] for n in c["notes"]) == sorted(i for i, _ in RUNTIME)
    text = jsonio.dumps(c, ensure_ascii=False, indent=1).encode("utf-8")
    assert hashlib.sha256(text).hexdigest() == "12b203dd5151c0afd6a26e3a9e4a8d8bb5180b57324019e9290b7f33aebae571"


# ---------------------------------------------------------------- synthetic master data and charts
SCORES = [{"_id": 30, "_musicScoreTextFileName": "c/c_03", "_musicScoreLevel": 20, "_fullComboCount": 99,
           "_musicScoreDisplayLevel": 20.5},
          {"_id": 10, "_musicScoreTextFileName": "c/c_01", "_musicScoreLevel": 5, "_fullComboCount": 11,
           "_musicScoreDisplayLevel": 5.0},
          {"_id": 20, "_musicScoreTextFileName": "c/c_02", "_musicScoreLevel": 9, "_fullComboCount": 4,
           "_musicScoreDisplayLevel": 9.0}]
SMALL = {"score": {"events": {"bpm": [{"t": 0, "bpm": 150}], "skill": [0], "fever": [[0, 480]]},
                   "notes": [{"type": "tap", "t": 480, "pos": 1, "size": 2}, {"type": "flick", "t": 960, "pos": 3,
                                                                              "size": 2}]}}


def rows_of(name: str) -> list[dict]:
    """Rows of the synthetic tables (every other table is empty)."""
    cols = dict(deckdata.TABLES)
    if name == "MasterLiveMusicScore":
        return SCORES
    if name == "MasterLiveComboScoreBonus":
        return [{"_id": 1, "_comboBonusType": 1, "_requiredComboCount": 10, "_bonusFactor": 1.1},
                {"_id": 2, "_comboBonusType": 1, "_requiredComboCount": 20, "_bonusFactor": 16777217.0},
                {"_id": 3, "_comboBonusType": 1, "_requiredComboCount": 30, "_bonusFactor": 2}]
    if name == "MasterLiveSkillEffect":
        row = {c: i for i, c in enumerate(cols[name])}
        return [dict(row, _activationTimeSecond=7.5, _skillTargetIDs=[3, 4]),
                dict(row, _id=9, _activationTimeSecond=0.1)]
    if name == "MasterLiveSettings":                     # a whole table: every column its rows have
        return [{"_id": 1, "_key": "a", "_value": "1000", "_subValue": ""}]
    if name == "MasterMemberCard":
        return [dict({c: i for i, c in enumerate(cols[name])}, _bestMusicTagIDs=[1, 2], _extra="not exported")]
    return []


def master_dir(tmp_path, rows=rows_of, skip=(), bad_hash=(), name="m"):
    d = tmp_path / name
    d.mkdir()
    files = []
    for t, _ in deckdata.TABLES:
        if t in skip:
            continue
        data = synth.master_file({"_allData": rows(t)})
        (d / f"{t}.bin").write_bytes(data)
        sha = hashlib.sha256(b"other" if t in bad_hash else data).hexdigest()
        files.append({"name": f"{t}.bin", "hash": sha, "size": len(data)})
    (d / "MasterManifest.json").write_text(json.dumps({"version": "v-test", "files": files}), encoding="utf-8")
    return d


def fetcher(charts: dict):
    def fetch(name):
        return charts[name]
    return fetch


CHARTS = {"c/c_01": gzip.compress(json.dumps(CHART).encode("utf-8"), mtime=0),
          "c/c_02": json.dumps(SMALL).encode("utf-8"),
          "c/c_03": gzip.compress(json.dumps(SMALL).encode("utf-8"), mtime=0)}
PROV = {"region": "xx", "client": {"versionName": "9.9.9", "versionCode": 99},
        "catalog": {"resourceVersion": None, "sha256": "ab" * 32}}


def export(tmp_path, out="deck.json", rows=rows_of, charts=CHARTS, src=None, key=None, **kw):
    """Read the master data and every chart, build the deck input and write it canonically to `out` (as
    `music-data --full` carries it); the document."""
    d = master_dir(tmp_path, rows) if not (tmp_path / "m").exists() else tmp_path / "m"
    tables, shas = deckdata.read_master(src or deckdata.master_files(d), key or KEY)
    prov = dict(PROV, **kw)
    doc = deckdata.build(tables, deckdata.charts(tables["MasterLiveMusicScore"], fetcher(charts)),
                         {"region": prov["region"], "tables": shas})
    data = deckdata.encode(doc)
    (tmp_path / out).write_bytes(deckdata.file_bytes(data, out.endswith(".gz")))
    return doc


# ---------------------------------------------------------------- the document
def test_deck_input_document(tmp_path):
    doc = export(tmp_path)
    raw = (tmp_path / "deck.json").read_bytes()
    assert raw.endswith(b"}\n") and raw.count(b"\n") == 1 and b"\r" not in raw
    assert json.loads(raw) == json.loads(deckdata.encode(doc))
    assert list(doc) == ["format", "provenance", "master", "charts"] and doc["format"] == "nnnotes.deck-data/1"
    assert list(doc["master"]) == [t for t, _ in deckdata.TABLES]
    assert len(doc["charts"]) == 3 and sum(len(c["notes"]["id"]) for c in doc["charts"]) == 21
    for t, _ in deckdata.TABLES:
        served = (tmp_path / "m" / f"{t}.bin").read_bytes()
        assert doc["provenance"]["tables"][t] == hashlib.sha256(served).hexdigest()


def test_master_columns_and_values(tmp_path):
    export(tmp_path)
    raw = (tmp_path / "deck.json").read_text(encoding="utf-8")
    m = json.loads(raw)["master"]
    card = m["MasterMemberCard"]
    assert card["columns"] == list(dict(deckdata.TABLES)["MasterMemberCard"]) and "_extra" not in card["columns"]
    assert card["rows"][0][card["columns"].index("_bestMusicTagIDs")] == [1, 2]
    assert m["MasterLiveMusicScore"]["columns"] == ["_id", "_musicScoreTextFileName", "_musicScoreLevel",
                                                    "_fullComboCount"]
    assert [r[0] for r in m["MasterLiveMusicScore"]["rows"]] == [30, 10, 20]          # served order
    assert m["MasterEvent"] == {"columns": [], "rows": []}                            # empty whole table
    assert m["MasterMemoryMusic"] == {"columns": ["_id", "_groupId"], "rows": []}     # empty listed table
    # binary32 values: the shortest decimal of the binary32 value
    assert '"MasterLiveComboScoreBonus":{"columns":["_id","_comboBonusType","_requiredComboCount","_bonusFactor"],' \
           '"rows":[[1,1,10,1.1],[2,1,20,16777216.0],[3,1,30,2]]}' in raw
    effect = m["MasterLiveSkillEffect"]
    i = effect["columns"].index("_activationTimeSecond")
    assert [r[i] for r in effect["rows"]] == [7.5, 0.1] and ',0.1,' in raw
    for v in (1.1, 0.1, 16777217.0):
        assert np.float32(float(deckdata.f32_token(v))) == np.float32(v)


def test_whole_table_columns_in_first_seen_order(tmp_path):
    def rows(name):
        if name == "MasterLiveSettings":
            return [{"_id": 1, "_key": "a", "_value": "1000", "_subValue": ""},
                    {"_key": "b", "_id": 2, "_subValue": "y", "_value": "x"}]
        return rows_of(name)
    export(tmp_path, rows=rows)
    s = json.loads((tmp_path / "deck.json").read_bytes())["master"]["MasterLiveSettings"]
    assert s == {"columns": ["_id", "_key", "_value", "_subValue"], "rows": [[1, "a", "1000", ""], [2, "b", "x", "y"]]}

    def partial(name):
        if name == "MasterLiveSettings":
            return [{"_id": 1, "_key": "a"}, {"_id": 2, "_key": "b", "_value": "x"}]
        return rows_of(name)
    failing(tmp_path, r"MasterLiveSettings: row 0 \(_id 1\) has no column _value",
            src=deckdata.master_files(master_dir(tmp_path, partial, name="p")))


def test_vip_identity_is_exported_independently_from_effect_rows(tmp_path):
    def rows(name):
        if name == "MasterVip":
            return [{"_id": 101, "_vipRank": 1, "_point": 0}, {"_id": 121, "_vipRank": 21, "_point": 999}]
        if name == "MasterVipRankBonus":
            return [{"_id": 125, "_vipRank": 21, "_vipBonusType": 0, "_value": 3}]
        return rows_of(name)

    document = export(tmp_path, rows=rows)
    vip = document["master"]["MasterVip"]
    assert vip == {"columns": ["_id", "_vipRank"], "rows": [[101, 1], [121, 21]]}
    assert document["master"]["MasterVipRankBonus"]["rows"] == [[125, 21, 0, 3]]
    # Rank 1 remains represented even without an effect row; effect IDs are not ranks.
    assert document["provenance"]["tables"]["MasterVip"] == hashlib.sha256((tmp_path / "m/MasterVip.bin").read_bytes()).hexdigest()


def test_character_rank_carries_its_experience_threshold(tmp_path):
    def rows(name):
        if name == "MasterCharacterRank":
            return [{"_id": 1, "_rank": 1, "_exp": 0, "_bonus": 0}, {"_id": 2, "_rank": 2, "_exp": 3, "_bonus": 5}]
        return rows_of(name)

    ranks = export(tmp_path, rows=rows)["master"]["MasterCharacterRank"]
    # A character's rank is the highest rank whose cumulative experience it has reached.
    assert ranks == {"columns": ["_id", "_rank", "_exp", "_bonus"], "rows": [[1, 1, 0, 0], [2, 2, 3, 5]]}

    def without_exp(name):
        if name == "MasterCharacterRank":
            return [{"_id": 1, "_rank": 1, "_bonus": 0}]
        return rows_of(name)
    failing(tmp_path, r"MasterCharacterRank: row 0 \(_id 1\) has no column _exp",
            src=deckdata.master_files(master_dir(tmp_path, without_exp, name="p")))


def test_charts(tmp_path):
    export(tmp_path)
    charts = json.loads((tmp_path / "deck.json").read_bytes())["charts"]
    assert [c["scoreId"] for c in charts] == [10, 20, 30]
    c = charts[0]
    assert list(c) == ["scoreId", "asset", "notes", "skillEvents", "fevers"]
    assert c["asset"] == {"key": "Live/MusicScore/c/c_01", "sha256": hashlib.sha256(CHARTS["c/c_01"]).hexdigest()}
    assert list(c["notes"]) == ["id", "op", "judgementType", "timeMs"]
    assert list(zip(c["notes"]["id"], c["notes"]["op"])) == RUNTIME
    assert c["notes"]["timeMs"] == TIMES and c["notes"]["judgementType"] == JUDGEMENTS
    assert c["skillEvents"] == {"timeMs": [2500, 500]}
    assert c["fevers"] == {"startMs": [0, 2000], "endMs": [1000, 2500]}
    # the reader's judgement note count is the converter's
    assert sum(score.is_judgement_note(op) for op in c["notes"]["op"]) == score.convert(CHART)["judgementNoteCount"]
    assert charts[1]["notes"] == charts[2]["notes"] and charts[1]["asset"]["sha256"] != charts[2]["asset"]["sha256"]


def test_output_is_deterministic_and_gzip_has_no_name_or_time(tmp_path):
    export(tmp_path, "a.json")
    export(tmp_path, "b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    export(tmp_path, "a.json.gz")
    export(tmp_path, "b.json.gz")
    gz = (tmp_path / "a.json.gz").read_bytes()
    assert gz == (tmp_path / "b.json.gz").read_bytes()
    assert gz[:3] == b"\x1f\x8b\x08" and gz[3] == 0 and gz[4:8] == b"\0\0\0\0"      # no FNAME flag, mtime 0
    assert gzip.decompress(gz) == (tmp_path / "a.json").read_bytes()


def test_non_finite_values(tmp_path):
    def rows(name):
        if name == "MasterLiveComboScoreBonus":
            return [{"_id": 1, "_comboBonusType": 1, "_requiredComboCount": 10, "_bonusFactor": 1e39},
                    {"_id": 2, "_comboBonusType": 1, "_requiredComboCount": 10, "_bonusFactor": float("-inf")}]
        return rows_of(name)
    export(tmp_path, rows=rows)
    assert b"[[1,1,10,1e999],[2,1,10,-1e999]]" in (tmp_path / "deck.json").read_bytes()

    def nan(name):
        if name == "MasterLiveComboScoreBonus":
            return [{"_id": 1, "_comboBonusType": 1, "_requiredComboCount": 10, "_bonusFactor": float("nan")}]
        return rows_of(name)
    with pytest.raises(deckdata.DeckDataError, match="MasterLiveComboScoreBonus row 0 _bonusFactor: NaN"):
        export(tmp_path, "n.json", src=deckdata.master_files(master_dir(tmp_path, nan, name="n")))
    assert not (tmp_path / "n.json").exists()


# ---------------------------------------------------------------- failures: no file is written
def failing(tmp_path, match, *, src=None, charts=CHARTS, key=KEY):
    if src is None:
        src = deckdata.master_files(tmp_path / "m" if (tmp_path / "m").exists() else master_dir(tmp_path))
    with pytest.raises(deckdata.DeckDataError, match=match):
        export(tmp_path, "out.json", src=src, key=key, charts=charts)
    assert not (tmp_path / "out.json").exists()


def test_missing_chart_asset(tmp_path):
    failing(tmp_path, r"chart Live/MusicScore/c/c_02 \(MasterLiveMusicScore 20\): no such asset",
            charts={k: v for k, v in CHARTS.items() if k != "c/c_02"})


def test_unreadable_chart(tmp_path):
    failing(tmp_path, "chart Live/MusicScore/c/c_03: not a chart", charts=dict(CHARTS, **{"c/c_03": b"\x1f\x8bxx"}))


def test_master_file_checks(tmp_path):
    failing(tmp_path, "MasterBand.bin: sha256 differs from the manifest",
            src=deckdata.master_files(master_dir(tmp_path, bad_hash={"MasterBand"}, name="a")))
    failing(tmp_path, "lists no MasterParameter, MasterEvent",
            src=deckdata.master_files(master_dir(tmp_path, skip={"MasterEvent", "MasterParameter"}, name="b")))
    d = master_dir(tmp_path, name="c")
    (d / "MasterBand.bin").unlink()
    failing(tmp_path, "no file MasterBand.bin", src=deckdata.master_files(d))
    failing(tmp_path, r"MasterMemberCard.bin cannot be decoded \(ValueError: bad PKCS7 padding",
            src=deckdata.master_files(master_dir(tmp_path, name="d")),
            key=MasterKey(bytes(32), bytes(32)))
    with pytest.raises(deckdata.DeckDataError, match="no MasterManifest.json"):
        deckdata.master_files(tmp_path / "nowhere")


def test_missing_column(tmp_path):
    def rows(name):
        if name == "MasterCharacter":
            return [{"_id": 1, "_bandID": 2}, {"_id": 2}]
        return rows_of(name)
    failing(tmp_path, r"MasterCharacter: row 1 \(_id 2\) has no column _bandID",
            src=deckdata.master_files(master_dir(tmp_path, rows)))


def test_duplicate_note_ids_and_operate_type_without_judgement(tmp_path, monkeypatch):
    real = score.runtime_score

    def twice(root, *a, **kw):
        rs = real(root, *a, **kw)
        k = next(iter(rs.by_key))
        rs.by_key[k] = rs.by_key[k] + rs.by_key[k][:1]
        return rs
    monkeypatch.setattr(score, "runtime_score", twice)
    failing(tmp_path, "chart Live/MusicScore/c/c_01: note id 1 occurs twice")

    def invalid(root, *a, **kw):
        rs = real(root, *a, **kw)
        rs.notes[-1].op = 123
        return rs
    monkeypatch.setattr(score, "runtime_score", invalid)
    failing(tmp_path, "chart Live/MusicScore/c/c_01: note 10 has operate type 123")


def test_duplicate_score_ids(tmp_path):
    def rows(name):
        return SCORES + SCORES[:1] if name == "MasterLiveMusicScore" else rows_of(name)
    failing(tmp_path, "MasterLiveMusicScore: _id 30 occurs twice",
            src=deckdata.master_files(master_dir(tmp_path, rows)))


# ---------------------------------------------------------------- APK master data and version
def manifest_xml(attrs: list[tuple[str, int, int, str | None]]) -> bytes:
    """A binary AndroidManifest.xml whose <manifest> has `attrs`: (name, data type, data, string value or None)."""
    strings = ["manifest"] + [a[0] for a in attrs] + [a[3] for a in attrs if a[3] is not None]
    blobs = [struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0" for s in strings]
    offsets, pos = [], 0
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
    body = struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(blobs)
    body += b"\0" * (-len(body) % 4)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, 28 + len(body), len(strings), 0, 0, 28 + 4 * len(strings), 0) + body
    rec = b""
    for name, vtype, data, text in attrs:
        raw = strings.index(text) if text is not None else 0xFFFFFFFF
        rec += struct.pack("<IIIHBBI", 0xFFFFFFFF, strings.index(name), raw, 8, 0, vtype, data)
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, len(attrs), 0, 0, 0)
    elem_body = struct.pack("<II", 1, 0xFFFFFFFF) + ext + rec
    elem = struct.pack("<HHI", 0x0102, 16, 8 + len(elem_body)) + elem_body
    return struct.pack("<HHI", 0x0003, 8, 8 + len(pool + elem)) + pool + elem


def test_manifest_version_code():
    both = manifest_xml([("versionCode", 0x10, 25, None), ("versionName", 0x03, 3, "1.2.3")])
    assert player.manifest_version_code(both) == 25 and player.manifest_version_name(both) == "1.2.3"
    assert player.manifest_version_code(manifest_xml([("versionCode", 0x11, 0x1F, None)])) == 31
    assert player.manifest_version_code(manifest_xml([("versionCode", 0x03, 0, "42")])) == 42
    none = manifest_xml([("versionName", 0x03, 0, "1.0")])
    assert player.manifest_version_code(none) is None and player.manifest_version_name(none) == "1.0"
    assert player.manifest_version_code(b"\0" * 4) is None
    old = synth.axml(["manifest", "versionName", "7.1"], 2)          # a string attribute only
    assert player.manifest_version_name(old) == "7.1" and player.manifest_version_code(old) is None


def test_apk_master_and_client(tmp_path):
    d = master_dir(tmp_path)
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("AndroidManifest.xml", manifest_xml([("versionName", 0x03, 0, "9.9.9"),
                                                         ("versionCode", 0x10, 99, None)]))
        for f in d.iterdir():
            z.writestr(f"assets/Master/{f.name}", f.read_bytes())
    src = deckdata.apk_master(apk)
    assert (src.source, src.version) == ("embedded", "v-test")
    client = deckdata.apk_client(apk)
    assert client == {"versionName": "9.9.9", "versionCode": 99}
    e = export(tmp_path, "e.json", src=src)
    f = export(tmp_path, "f.json")
    assert e["master"] == f["master"] and e["charts"] == f["charts"]
    with zipfile.ZipFile(tmp_path / "empty.apk", "w") as z:
        z.writestr("x", b"")
    with pytest.raises(deckdata.DeckDataError, match="no assets/Master/MasterManifest.json"):
        deckdata.apk_master(tmp_path / "empty.apk")
    with zipfile.ZipFile(tmp_path / "partial.apk", "w") as z:
        z.writestr("assets/Master/MasterManifest.json", (d / "MasterManifest.json").read_bytes())
    with pytest.raises(deckdata.DeckDataError, match="no file MasterMemberCard.bin"):
        deckdata.read_master(deckdata.apk_master(tmp_path / "partial.apk"), KEY)
    assert deckdata.apk_client(tmp_path / "empty.apk") == {"versionName": None, "versionCode": None}


# ---------------------------------------------------------------- decoded master data
def decoded_dir(tmp_path, d, name="dec"):
    """The decoded tables of a master_dir (`master decode`) with its manifest, as a published snapshot carries them."""
    out = tmp_path / name
    assert not master.decode_files(sorted(d.glob("*.bin")), out, KEY)["failed"]
    shutil.copy(d / "MasterManifest.json", out / "MasterManifest.json")
    return out


def test_decoded_master(tmp_path):
    d = master_dir(tmp_path)
    src = deckdata.decoded_master(decoded_dir(tmp_path, d))
    assert (src.source, src.version, src.decoded) == ("api", "v-test", True)
    # the same rows and the manifest's SHA-256 of the files as served, without the key
    assert deckdata.read_master(src, None) == deckdata.read_master(deckdata.master_files(d), KEY)
    export(tmp_path, "e.json", src=src)
    export(tmp_path, "f.json")
    assert (tmp_path / "e.json").read_bytes() == (tmp_path / "f.json").read_bytes()


def test_decoded_master_checks(tmp_path):
    d = master_dir(tmp_path)
    with pytest.raises(deckdata.DeckDataError, match="no MasterManifest.json .decoded master data needs the manifest"):
        deckdata.decoded_master(tmp_path)
    dec = decoded_dir(tmp_path, d, "a")
    (dec / "MasterBand.json").unlink()
    failing(tmp_path, "no file MasterBand.json", src=deckdata.decoded_master(dec), key=None)
    dec = decoded_dir(tmp_path, d, "b")
    (dec / "MasterBand.json").write_text("{", encoding="utf-8")
    failing(tmp_path, r"MasterBand.json cannot be read \(JSONDecodeError", src=deckdata.decoded_master(dec), key=None)
    (dec / "MasterBand.json").write_text('{"x": 1}', encoding="utf-8")
    failing(tmp_path, "MasterBand.json has no `_allData` rows", src=deckdata.decoded_master(dec), key=None)
    dec = decoded_dir(tmp_path, d, "c")
    m = json.loads((dec / "MasterManifest.json").read_text(encoding="utf-8"))
    for f in m["files"]:
        if f["name"] == "MasterBand.bin":
            f["hash"] = ""
    (dec / "MasterManifest.json").write_text(json.dumps(m), encoding="utf-8")
    failing(tmp_path, "MasterManifest.json lists no SHA-256 for MasterBand.bin", src=deckdata.decoded_master(dec),
            key=None)
    dec = decoded_dir(tmp_path, master_dir(tmp_path, skip={"MasterEvent"}, name="m2"), "e")
    failing(tmp_path, "lists no MasterEvent", src=deckdata.decoded_master(dec), key=None)


def test_resource_version_from_the_catalog_store(tmp_path):
    remote = synth.CatalogWriter().build([("a_01.bundle", synth.remote("a_01.bundle"), [])])
    sha = hashlib.sha256(remote).hexdigest()
    assert deckdata.resource_version(tmp_path / "store", sha) is None
    CatalogDB(tmp_path / "store").add(remote, None, region="xx", language="en", resource_version="1.2.3")
    assert deckdata.resource_version(tmp_path / "store", sha) == "1.2.3"
    assert deckdata.resource_version(tmp_path / "store", "0" * 64) is None
    assert deckdata.resource_version(None, sha) is None


# ---------------------------------------------------------------- command line
class FakeCatalog:
    def __init__(self, charts):
        self.charts = charts

    def sources(self):
        return {"remote": b"remote catalog"}

    def has(self, key):
        return key.startswith(deckdata.CHART_PREFIX) and key[len(deckdata.CHART_PREFIX):] in self.charts


def run(argv, capsys):
    """(exit code, stdout, stderr) of the command line; the message of sys.exit(message) is part of stderr."""
    code, message = 0, ""
    try:
        cli.main(argv)
    except SystemExit as e:
        code, message = (e.code, "") if isinstance(e.code, int) else (1, str(e.code))
    out, err = capsys.readouterr()
    return code, out, err + message


def test_the_tables_are_documented():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "docs" / "music-data.md").read_text(encoding="utf-8")
    documented = []
    for line in text.splitlines():
        if line.startswith("| `Master"):
            name, cols = (c.strip() for c in line.strip("|").split("|"))
            documented.append((name.strip("`"), None if cols == "all" else tuple(c.strip("`") for c in cols.split())))
    assert documented == list(deckdata.TABLES)
