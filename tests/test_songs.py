"""Songs export (songs): metadata, chart facts and BGM length on synthetic master data, charts and ACBs."""
import gzip
import hashlib
import io
import json

import pytest

import synth
from nnnotes import cli, deckdata, score, songs
from nnnotes.master import MasterKey
from test_deckdata import CHART, SMALL, FakeCatalog, run
from test_voices import simple_acb

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)


def text(tid, stem):
    return {"_id": tid, "_japanese": f"{stem}-ja", "_english": f"{stem}-en", "_traditionalChinese": f"{stem}-tw",
            "_simplifiedChinese": f"{stem}-cn", "_korean": f"{stem}-ko"}


MUSIC = {"_id": 100002, "_sortOrder": 2, "_startAt": "2026/01/01 0:00:00", "_defaultUnlock": True,
         "_titleTextID": "T2", "_rubyTitleTextID": "", "_phoneticTextID": "P2", "_bandIDs": [1],
         "_bandNameTextID": "", "_vocalCharacterIDs": [1], "_lyricistTextID": "L2", "_composerTextID": "C2",
         "_arrangerTextID": "", "_musicType": 4, "_musicCategories": [1], "_bestMusicTagIDs": [1],
         "_jacketAssetName": "jkt_2", "_gekisouMission1": 1, "_gekisouMission2": 3, "_gekisouMission3": 3,
         "_musicSoundID": 13, "_easyID": 20, "_normalID": 0, "_hardID": 0, "_expertID": 30, "_extra": [7]}
MUSIC1 = dict(MUSIC, _id=100001, _sortOrder=1, _titleTextID="T1", _bandNameTextID="BN", _easyID=10, _expertID=0)
TABLE_ROWS = {
    "MasterLiveMusic": [MUSIC, MUSIC1],
    "MasterLiveMusicScore": [
        {"_id": 30, "_musicScoreTextFileName": "c/c_03", "_musicScoreLevel": 20, "_fullComboCount": 2,
         "_musicScoreDisplayLevel": 20.5},
        {"_id": 10, "_musicScoreTextFileName": "c/c_01", "_musicScoreLevel": 5, "_fullComboCount": 11,
         "_musicScoreDisplayLevel": 5.0},
        {"_id": 20, "_musicScoreTextFileName": "c/c_02", "_musicScoreLevel": 9, "_fullComboCount": 2,
         "_musicScoreDisplayLevel": 9.0}],
    "MasterText": [text("T1", "one"), text("T2", "two"), text("P2", "pho"), text("L2", "lyr"), text("C2", "com"),
                   text("BN", "crychic"), text("Band1", "mygo"), text("Ch1", "tomori"), text("Ch1s", "tomo"),
                   text("Tag1", "tag"), text("Cat1", "original")],
    "MasterBand": [{"_id": 1, "_nameTextID": "Band1", "_mainColorCode": "#3388BB", "_subColorCode": "#FFFFFF"}],
    "MasterCharacter": [{"_id": 1, "_nameTextID": "Ch1", "_shortNameTextID": "Ch1s", "_bandID": 1,
                         "_mainColorCode": "#77BBDD"}],
    "MasterTag": [{"_id": 1, "_nameTextID": "Tag1"}],
    "MasterLiveMusicCategory": [{"_id": 1, "_musicCategories": [1], "_textKey": "Cat1"}],
    "MasterSound": [{"_id": 13, "_soundCueSheetID": 5, "_cueName": "song2"}],
    "MasterSoundCueSheet": [{"_id": 5, "_cueSheetName": "Bgm2"}],
}
CHARTS = {"c/c_01": gzip.compress(json.dumps(CHART).encode("utf-8"), mtime=0),
          "c/c_02": json.dumps(SMALL).encode("utf-8"),
          "c/c_03": gzip.compress(json.dumps(SMALL).encode("utf-8"), mtime=0)}
ACB = simple_acb({"song2": [1]}, [1], {1: 48000 * 90 + 24})            # 90.0005 s at 48 kHz, cue Length 100
PROV = {"region": "xx", "client": {"versionName": "9.9.9", "versionCode": 99},
        "catalog": {"resourceVersion": None, "sha256": "ab" * 32}}


def master_dir(tmp_path, rows=TABLE_ROWS):
    d = tmp_path / "m"
    d.mkdir()
    files = []
    for t in songs.TABLES:
        data = synth.master_file({"_allData": rows.get(t, [])})
        (d / f"{t}.bin").write_bytes(data)
        files.append({"name": f"{t}.bin", "hash": hashlib.sha256(data).hexdigest(), "size": len(data)})
    (d / "MasterManifest.json").write_text(json.dumps({"version": "v-test", "files": files}), encoding="utf-8")
    return d


def bgm(sheet, cue):
    assert sheet == "Bgm2"
    return songs.cue_length(ACB, cue, f"cue sheet {sheet}")


def export(tmp_path, rows=TABLE_ROWS, charts=CHARTS, bgm=bgm, out="songs.json", **kw):
    d = master_dir(tmp_path, rows)
    return songs.export(tmp_path / out, deckdata.master_files(d), KEY, charts.__getitem__, bgm, **PROV, **kw)


def test_document(tmp_path):
    r = export(tmp_path)
    raw = (tmp_path / "songs.json").read_bytes()
    assert raw.endswith(b"}\n") and raw.count(b"\n") == 1
    assert (r["songs"], r["charts"], r["sha256"]) == (2, 3, hashlib.sha256(raw).hexdigest())
    doc = json.loads(raw)
    assert list(doc) == ["format", "provenance", "languages", "bands", "characters", "tags", "categories", "songs"]
    assert doc["format"] == "nnnotes.songs/1" and doc["languages"] == ["ja", "en", "zh-Hant", "zh-Hans", "ko"]
    assert list(doc["provenance"]["master"]["tables"]) == list(songs.TABLES)
    assert doc["bands"] == [{"id": 1, "name": {"ja": "mygo-ja", "en": "mygo-en", "zh-Hant": "mygo-tw",
                                               "zh-Hans": "mygo-cn", "ko": "mygo-ko"},
                             "mainColor": "#3388BB", "subColor": "#FFFFFF"}]
    assert doc["characters"][0]["name"]["ja"] == "tomori-ja" and doc["characters"][0]["bandId"] == 1
    assert doc["categories"] == [{"id": 1, "musicCategories": [1], "name": dict(doc["categories"][0]["name"])}]
    one, two = doc["songs"]
    assert (one["id"], two["id"]) == (100001, 100002)                     # sorted by id
    assert one["bandName"]["ja"] == "crychic-ja" and two["bandName"] is None
    assert two["title"]["zh-Hans"] == "two-cn" and two["ruby"] is None and two["arranger"] is None
    assert two["lyricist"]["en"] == "lyr-en" and two["gekisouMissions"] == [1, 3, 3]
    assert two["bgm"] == {"soundId": 13, "cueSheet": "Bgm2", "cue": "song2",
                          "length": {"lengthMs": 100, "samples": 48000 * 90 + 24, "sampleRate": 48000,
                                     "durationMs": 90000}}
    assert two["master"]["MasterLiveMusic"]["_extra"] == [7]              # the whole row
    assert [c["difficulty"] for c in two["charts"]] == ["easy", "expert"]  # difficulties without a score are left out
    assert [c["difficulty"] for c in one["charts"]] == ["easy"]


def test_chart_facts(tmp_path):
    export(tmp_path)
    doc = json.loads((tmp_path / "songs.json").read_bytes())
    c = doc["songs"][0]["charts"][0]                                      # CHART at 120 BPM
    assert (c["scoreId"], c["level"], c["displayLevel"], c["fullComboCount"]) == (10, 5, 5.0, 11)
    assert c["asset"] == {"key": "Live/MusicScore/c/c_01", "sha256": hashlib.sha256(CHARTS["c/c_01"]).hexdigest()}
    rs = score.runtime_score(CHART)
    judged = sorted(n.pos.ms for n in rs.notes if score.is_judgement_note(n.op))
    assert c["notes"]["judged"] == len(judged) and c["notes"]["total"] == len(rs.notes) == 17
    assert sum(c["notes"]["byOperateType"].values()) == 17 and list(c["notes"]["byOperateType"])[0] == "1"
    assert c["bpm"] == {"main": 120, "min": 120, "max": 120, "changes": [{"timeMs": 0, "bpm": 120}]}
    assert (c["firstNoteMs"], c["lastJudgedNoteMs"], c["lastNoteMs"], c["musicLengthMs"]) == (0, 5000, 5000, 6000)
    assert c["skillEventsMs"] == [2500, 500] and c["fevers"] == [[0, 1000], [2000, 2500]]


def test_bpm_facts():
    class P:
        def __init__(self, ms):
            self.ms = ms
    ev = [(100.0, P(0)), (200.0, P(1000)), (150.0, P(1500)), (300.0, P(9000))]
    f = songs.bpm_facts(ev, 500, 4000)                  # 100 for 500 ms, 200 for 500, 150 for 2500; 300 after
    assert (f["main"], f["min"], f["max"]) == (150.0, 100.0, 200.0)
    assert [x["timeMs"] for x in f["changes"]] == [0, 1000, 1500, 9000]
    assert songs.bpm_facts(ev, 1200, 1200)["main"] == 200.0       # one instant: the BPM at that time
    tie = [(120.0, P(0)), (180.0, P(1000))]
    assert songs.bpm_facts(tie, 0, 2000)["main"] == 120.0         # equal time: the earliest
    with pytest.raises(songs.SongsError):
        songs.bpm_facts([], 0, 1)


def test_deterministic_and_no_bgm(tmp_path):
    export(tmp_path, out="a.json")
    (tmp_path / "m").rename(tmp_path / "m0")
    export(tmp_path, out="b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    (tmp_path / "m").rename(tmp_path / "m1")
    r = export(tmp_path, bgm=None, out="c.json.gz")
    doc = json.loads(gzip.decompress((tmp_path / "c.json.gz").read_bytes()))
    assert r["bgm"] is False and all(s["bgm"]["length"] is None for s in doc["songs"])


def test_jacket_webp():
    from PIL import Image
    img = Image.new("RGBA", (1024, 512), (10, 200, 30, 255))
    out = Image.open(io.BytesIO(songs.jacket_webp(img)))
    assert (out.format, out.size, out.mode) == ("WEBP", (320, 160), "RGB")
    img.putpixel((0, 0), (0, 0, 0, 0))
    out = Image.open(io.BytesIO(songs.jacket_webp(img, size=2000)))
    assert (out.size, out.mode) == ((1024, 512), "RGBA")


def test_jackets(tmp_path):
    from PIL import Image
    asked = []

    def jacket(name):
        asked.append(name)
        return songs.jacket_webp(Image.new("RGB", (64, 64), (1, 2, 3)))
    r = export(tmp_path, jacket=jacket, jackets_dir=tmp_path / "j")
    assert r["jackets"] == 1 and asked == ["jkt_2"]           # the two songs share one jacket
    assert Image.open(tmp_path / "j" / "jkt_2.webp").size == (64, 64)
    (tmp_path / "x").mkdir()
    assert export(tmp_path / "x", out="b.json")["jackets"] == 0

    def missing(name):
        raise songs.SongsError(f"jacket {name}: no asset")
    (tmp_path / "y").mkdir()
    with pytest.raises(songs.SongsError, match="jacket jkt_2"):
        export(tmp_path / "y", jacket=missing, jackets_dir=tmp_path / "y" / "j")
    assert not (tmp_path / "y" / "songs.json").exists()


@pytest.mark.parametrize("change, match", [
    (lambda r: r["MasterText"].pop(0), "MasterText has no text 'T1'"),
    (lambda r: r["MasterLiveMusicScore"].pop(0), "expert score 30 is not in MasterLiveMusicScore"),
    (lambda r: r["MasterSound"].clear(), "sound 13 is not in MasterSound"),
    (lambda r: r["MasterLiveMusic"].append(dict(MUSIC)), "MasterLiveMusic: _id 100002 occurs twice"),
])
def test_master_errors(tmp_path, change, match):
    rows = json.loads(json.dumps(TABLE_ROWS))
    change(rows)
    with pytest.raises(songs.SongsError, match=match):
        export(tmp_path, rows=rows)
    assert not (tmp_path / "songs.json").exists()


def test_input_errors(tmp_path):
    with pytest.raises(songs.SongsError, match="c/c_03 .*no such asset"):
        export(tmp_path, charts={k: v for k, v in CHARTS.items() if k != "c/c_03"})
    (tmp_path / "m").rename(tmp_path / "m0")
    with pytest.raises(songs.SongsError, match="not a chart"):
        export(tmp_path, charts=dict(CHARTS, **{"c/c_02": b"\x1f\x8bnot"}))
    (tmp_path / "m").rename(tmp_path / "m1")
    with pytest.raises(songs.SongsError, match="no cue 'song2'"):
        export(tmp_path, bgm=lambda s, c: songs.cue_length(simple_acb({"x": [1]}, [1], {1: 5}), c, s))


def test_command(tmp_path, capsys, monkeypatch):
    d = master_dir(tmp_path)
    out = tmp_path / "o" / "songs.json"
    code, _, err = run(["songs", "-o", str(out)], capsys)
    assert code == 2 and "--master-files" in err
    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    monkeypatch.setattr(cli, "open_catalog", lambda cfg, **kw: FakeCatalog(CHARTS))
    monkeypatch.setattr(score, "fetch_chart", lambda cat, name: cat.charts[name])
    code, stdout, err = run(["--region", "xx", "--cache", str(tmp_path / "cache"), "songs", "--master-files",
                             str(d), "--no-bgm", "-o", str(out)], capsys)
    assert code == 0, err
    r = json.loads(stdout)
    assert (r["songs"], r["charts"], r["bgm"], r["region"]) == (2, 3, False, "xx")
    assert synth.MASTER_KEY.hex() not in stdout + err
