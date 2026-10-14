"""The voices command on synthetic voices documents, master data and catalogs."""

import json

import pytest

from nnnotes import cli
from nnnotes.content import storyvoices
from nnnotes.formats import cri
from nnnotes.pipeline.store import Store
from nnnotes.source.session import Session
from tests.pipeline import test_voices as tv
from tests.pipeline import test_voices_story as ts
from tests.pipeline.test_views import write_master


def command(argv, cfg=None):
    args = cli.parse_args(argv)
    args.func(args, cfg or tv.Cfg())


@pytest.fixture
def session(monkeypatch):
    """session(master_dir, catalog): the data sources of the commands that read no export."""

    def use(mdir, catalog):
        monkeypatch.setattr(Session, "master_dir", lambda self, region=None: mdir)
        monkeypatch.setattr(Session, "catalog", lambda self, bundles=True, region=None: catalog)

    return use


def fake_decode(sheets=None):
    """A cri.decode that writes every stream of a synthetic sheet; its calls in `.calls`."""

    def decode(cat, sheet, out_dir, key=None, fmt="flac", **kw):
        decode.calls.append((sheet, fmt, kw.get("flac_level")))
        out_dir.mkdir(parents=True, exist_ok=True)
        streams = []
        for n, name, file, _samples in tv.stream_files(sheet, sheets):
            (out_dir / f"{file}.{fmt}").write_bytes(f"decoded {sheet}/{file}".encode())
            streams.append({"stream": n, "name": name, "file": f"{file}.{fmt}"})
        (out_dir / "streams.json").write_text(json.dumps(streams), encoding="utf-8")

    decode.calls = []
    return decode


def test_list_search_summary(tmp_path, capsysbinary):
    out = tv.export_dir(tmp_path, Store(tmp_path / "s"))
    command(["voices", "list", "--from", str(out), "--character", "Alpha", "--category", "Talk.CharacterRankUp"])
    line = capsysbinary.readouterr().out.decode("utf-8")
    assert (
        line == "MasterTalk:561\tcharacter_name_alpha-ja\tTalk.CharacterRankUp\tVoiceSystem_01/"
        "Growth_Alpha_RankUp_01\tok\tテストです\n"
    )
    command(["voices", "search", "HELLO", "--from", str(out), "--search-language", "en", "--json"])
    rows = json.loads(capsysbinary.readouterr().out)
    assert [r["id"] for r in rows] == ["MasterTalk:561"]
    command(["voices", "list", "--from", str(out), "--source", "Sound", "--character", "gamma"])
    assert capsysbinary.readouterr().out.decode().split("\t")[0] == "MasterSound:114"
    command(["voices", "summary", "--from", str(out), "--json"])
    s = json.loads(capsysbinary.readouterr().out)
    assert s["gaps"] == 8 and s["reverse"]["unreferencedStreams"] == {"VoiceSystem_N": 2}
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(out), "--character", "Nobody"])
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(tmp_path / "none")])


def test_without_an_export_the_content_is_built(tmp_path, capsysbinary, session):
    session(write_master(tmp_path / "m", tv.TABLES), tv.Catalog())
    command(["voices", "list", "--character", "1", "--category", "character-rank-up", "--json"])
    rows = json.loads(capsysbinary.readouterr().out)
    assert [(r["id"], r["status"]) for r in rows] == [("MasterTalk:561", "not-exported")]


def test_get_copies_the_exported_files(tmp_path, capsysbinary):
    store = Store(tmp_path / "s")
    out = tv.export_dir(tmp_path, store)
    dst = tmp_path / "got"
    command(["voices", "get", "MasterHomeSpot:10001", "-o", str(dst), "--from", str(out)])
    assert sorted(p.name for p in dst.iterdir()) == ["IntroRandom.flac", "IntroRandom_2.flac", "voice.json"]
    assert (dst / "IntroRandom_2.flac").read_bytes() == b"spot_01_01_01/IntroRandom_2"
    meta = json.loads((dst / "voice.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in meta["rows"]] == ["MasterHomeSpot:10001"]
    assert meta["files"] == ["IntroRandom.flac", "IntroRandom_2.flac"]
    command(["voices", "get", "101", "-o", str(tmp_path / "g2"), "--from", str(out)])
    assert (tmp_path / "g2" / "Growth_Alpha_RankUp_01.flac").is_file()
    with pytest.raises(SystemExit) as e:
        command(["voices", "get", "MasterTalk:563", "-o", str(tmp_path / "g3"), "--from", str(out)])
    assert e.value.code == 1
    with pytest.raises(SystemExit) as e:
        command(["voices", "get", "MasterTalk:1", "-o", str(tmp_path / "g4"), "--from", str(out)])
    assert e.value.code == 2


def test_get_without_an_export_decodes_the_one_sheet(tmp_path, capsysbinary, monkeypatch, session):
    decode = fake_decode()
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": tv.sheet_acb(sheet)}, cri.RAW_ACB))
    monkeypatch.setattr(cri, "decode", decode)
    session(write_master(tmp_path / "m", tv.TABLES), tv.Catalog())
    dst = tmp_path / "got"
    command(["voices", "get", "MasterCharacterVoice:1", "-o", str(dst)])
    assert decode.calls == [("VoiceSystem_02", "flac", 8)]
    assert sorted(p.name for p in dst.iterdir()) == ["Shared_01.flac", "voice.json"]
    assert (dst / "Shared_01.flac").read_bytes() == b"decoded VoiceSystem_02/Shared_01"
    command(["voices", "get", "MasterTalk:561", "-o", str(tmp_path / "ogg"), "--format", "ogg"])
    assert (tmp_path / "ogg" / "Growth_Alpha_RankUp_01.ogg").is_file()


def test_get_decodes_at_the_flac_level_given(tmp_path, capsysbinary, monkeypatch):
    decode = fake_decode()
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": tv.sheet_acb(sheet)}, cri.RAW_ACB))
    monkeypatch.setattr(cri, "decode", decode)
    monkeypatch.setattr(Session, "catalog", lambda self, bundles=True, region=None: tv.Catalog())
    out = tv.export_dir(tmp_path, Store(tmp_path / "s"))
    command(["voices", "get", "101", "-o", str(tmp_path / "g"), "--from", str(out), "--flac-level", "5"])
    assert decode.calls == [("VoiceSystem_01", "flac", 5)]
    for argv, message in (
        (["--flac-level", "13"], "--flac-level 13: expected 0 to 12"),
        (["--format", "ogg", "--flac-level", "5"], "--flac-level: --format ogg writes no FLAC"),
    ):
        with pytest.raises(SystemExit) as e:
            command(["voices", "get", "101", "-o", str(tmp_path / "x"), "--from", str(out), *argv])
        assert e.value.code == 2 and message in capsysbinary.readouterr().err.decode()


def test_story_rows_are_filtered(tmp_path, capsysbinary):
    out = ts.export_dir(tmp_path, Store(tmp_path / "s"))
    command(
        ["voices", "list", "--from", str(out), "--source", "story", "--episode", "900001", "--status", "ok"],
        tv.Cfg("en"),
    )
    got = capsysbinary.readouterr().out.decode("utf-8").splitlines()
    sheet = ts.STORY_SHEET
    assert got == [
        f"MasterAdv:900001:3\tadv_alpha-en\tStory.Main\t{sheet}/{sheet}_001\tok\tx01_1-en",
        f"MasterAdv:900001:5:1\tadv_gamma-en,adv_alpha-en\tStory.Main\t{sheet}/{sheet}_002\tok\tx01_2-en",
        f"MasterAdv:900001:12\tadv_both-en\tStory.Main\t{sheet}/{sheet}_003\tok\tx01_5-en",
    ]
    command(["voices", "search", "x01_3-ZH-HANS", "--from", str(out), "--search-language", "zh-Hans", "--json"])
    assert [r["id"] for r in json.loads(capsysbinary.readouterr().out)] == ["MasterAdv:900001:7"]
    command(["voices", "list", "--from", str(out), "--episode", "adv_script_x_02", "--json"])
    assert [r["id"] for r in json.loads(capsysbinary.readouterr().out)] == ["MasterAdv:900002:1"]
    command(["voices", "list", "--from", str(out), "--category", "Story.HomeSpotTapTalk", "--character", "alpha"])
    assert capsysbinary.readouterr().out.decode().split("\t")[0] == "MasterAdv:900003:2"
    with pytest.raises(SystemExit):
        command(["voices", "list", "--from", str(out), "--episode", "123"])
    command(["voices", "summary", "--from", str(out)])
    assert "story: 8 episodes" in capsysbinary.readouterr().out.decode()
    dst = tmp_path / "got"
    command(["voices", "get", "MasterAdv:900001:5:1", "-o", str(dst), "--from", str(out)])
    assert (dst / f"{sheet}_002.flac").read_bytes() == f"{sheet}/{sheet}_002.flac".encode()
    assert [r["id"] for r in json.loads((dst / "voice.json").read_text(encoding="utf-8"))["rows"]] == [
        "MasterAdv:900001:5:1"
    ]
    command(["voices", "get", "5201", "-o", str(tmp_path / "g2"), "--from", str(out)])
    assert (tmp_path / "g2" / "Growth_Alpha_RankUp_01.flac").is_file()


def test_without_an_export_one_episode_is_read(tmp_path, capsysbinary, monkeypatch, session):
    loaded = []

    def load(cat, keys):
        asset = keys["script"].rsplit("/", 1)[-1]
        loaded.append(asset)
        return {
            p: ts.EPISODES[asset][p] if p == "script" else ts.shard(ts.EPISODES[asset][p]) for p in ts.EPISODES[asset]
        }

    monkeypatch.setattr(storyvoices, "load_episode", load)
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": tv.sheet_acb(sheet, ts.SHEETS2)}, cri.RAW_ACB))
    monkeypatch.setattr(cri, "decode", fake_decode(ts.SHEETS2))
    session(write_master(tmp_path / "m", ts.tables()), ts.Catalog())
    command(["voices", "list", "--episode", "900001", "--json"])
    rows = json.loads(capsysbinary.readouterr().out)
    assert loaded == ["adv_script_x_01"]
    assert [(r["id"], r["status"]) for r in rows][:2] == [
        ("MasterAdv:900001:3", "not-exported"),
        ("MasterAdv:900001:5:1", "not-exported"),
    ]
    command(["voices", "list", "--source", "Story", "--json"])
    assert json.loads(capsysbinary.readouterr().out) == []  # no episode read without --episode
    dst = tmp_path / "got"
    command(["voices", "get", "MasterAdv:900001:3", "-o", str(dst)])
    assert loaded[-1] == "adv_script_x_01"
    sheet = ts.STORY_SHEET
    assert (dst / f"{sheet}_001.flac").read_bytes() == f"decoded {sheet}/{sheet}_001".encode()
