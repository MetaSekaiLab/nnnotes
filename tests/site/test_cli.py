"""Site CLI routing, region selection and publication using synthetic product outputs."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nnnotes.cli import assets, export, parse_args, site
from nnnotes.content import liveoptions, sitefacts
from nnnotes.pipeline import contract
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.orchestrate import Run, failure
from nnnotes.site import build, pages
from nnnotes.source.config import Config, ConfigError
from nnnotes.source.session import Session
from tests.site.test_products import file, graph, product

MODEL = "Character/Live2D/g/sample/model/sample"


@pytest.fixture
def command(tmp_path, monkeypatch):
    cfg = Config(
        {
            "catalog": {"region": "one", "language": "ja"},
            "paths": {"depot": str(tmp_path / "depot"), "player": str(tmp_path / "player")},
            "servers": {
                r: {"cdn": "https://example.test/", "master": str(tmp_path / r), "apk": str(tmp_path / (r + ".apk"))}
                for r in ["one", "two"]
            },
        },
        environ={},
    )
    depot = Depot(tmp_path / "depot")
    calls = []
    monkeypatch.setattr(pages, "check_player", Path)
    monkeypatch.setattr(pages, "gather", lambda *a, **kw: ({"index.html": b"page"}, {}))
    monkeypatch.setattr(Session, "master_dir", lambda self, region=None: tmp_path / (region or self.cfg.region()))
    monkeypatch.setattr(sitefacts, "all_pairs", lambda md: [(1, "easy"), (1, "expert")])
    monkeypatch.setattr(sitefacts, "story_groups", lambda md: {})
    monkeypatch.setattr(sitefacts, "optional_model_names", lambda *a: {})
    monkeypatch.setattr("nnnotes.source.master.table", lambda md, name: [{"_id": 1}])
    monkeypatch.setattr("nnnotes.unity.tmpfont.require_extra", lambda *a: None)
    monkeypatch.setattr(liveoptions, "resolve", lambda *a: liveoptions.LiveOptions())

    def prepare(args, regional):
        calls.append((args, regional))
        return SimpleNamespace(args=args, products={"live": ["1/" + getattr(args, "difficulty", "easy")]})

    monkeypatch.setattr(export, "prepare_live", prepare)
    monkeypatch.setattr(export, "prepare_story", lambda *a: (prepare(*a), "expand"))
    monkeypatch.setattr(export, "prepare_live2d", prepare)
    monkeypatch.setattr(
        assets,
        "Workspace",
        lambda *a, **kw: SimpleNamespace(run_index=lambda: None, index=lambda: {"locations": [{"primaryKey": MODEL}]}),
    )

    class Reader:
        def __init__(self, depot, player):
            self.run, self.checked = Run(depot, {}, [], {}, {}), 0
            self.mismatches, self.plan_errors = [], []

        def read(self, *args, **kw):
            self.checked += kw["check"]
            return []

    monkeypatch.setattr(build, "Reader", Reader)

    def outputs(args, regional, ws, expand=None):
        if hasattr(args, "adv_id"):
            assert expand == "expand"
            manifests = graph(depot)
            manifests["storyscenes/default.json"] = product(
                depot,
                "story.scene",
                "default",
                {"scene.json": {"settings": {"playerSettings": "example:1"}}},
                {
                    "example:1": {
                        "class": "MonoBehaviour",
                        "files": {
                            "json": file(
                                depot,
                                {
                                    "_initializeEpisodes": [{"Command": 15}],
                                    "_finalizeEpisodes": [{"Command": 5}],
                                },
                            )
                        },
                    }
                },
            )
            manifests["storyui/en.json"] = product(depot, "story.ui", "en", {"ui.json": {}})
            manifests["storyfonts/1_en.json"] = product(depot, "story.fonts", "1/en", {"fonts.json": {}})
            manifests["stories/1.json"]["files"]["episode.json"] = file(
                depot, {"advId": 1, "asset": "a", "master": {"_playbackMode": 0}, "commands": [], "commandCount": 0}
            )
        elif hasattr(args, "music_id"):
            difficulty = args.difficulty
            live = {
                "musicId": 1,
                "difficulty": difficulty,
                "parts": {"music": "music/1.json"},
                "band": {"band": 1},
                "judgementNoteCount": 2,
                "fullComboCount": 2,
                "master": {
                    "MasterLiveMusic": {},
                    "MasterLiveMusicScore": {difficulty: {"_musicScoreLevel": 1, "_musicScoreDisplayLevel": "1"}},
                },
            }
            manifests = {
                f"lives/1_{difficulty}.json": product(depot, "live", "1/" + difficulty, {"live.json": live}),
                "music/1.json": product(
                    depot,
                    "live.music",
                    "1",
                    {
                        "music.json": {
                            "music": {"soundId": 1},
                            "sounds": {"1": {"layers": [{"samples": 48000, "sampleRate": 48000}]}},
                        }
                    },
                ),
            }
        else:
            manifests = {"models/sample.json": product(depot, "live2d.model", MODEL, {"model.json": {}})}
        rows = []
        for path, doc in manifests.items():
            body = contract.encode(doc)
            rows.append({"path": path, "sha256": depot.put(body), "size": len(body), "id": path})
        return rows, {"tasks": 1, "hit": 1, "ran": 0, "failed": 0}

    monkeypatch.setattr(site, "product_entries", outputs)

    def run(flags):
        args = parse_args(["--json", "site", "build", "--name", "example", "-o", str(tmp_path / "site"), *flags])
        site.cmd_build(args, cfg)
        return args

    return run, calls, cfg, depot


def test_cli_requests_products_and_restores_named_site(command, tmp_path, capsys):
    run, calls, _, _ = command
    run(["--pair", "1:expert", "--live2d", "sample", "--compress", "none", "--workers", "2", "--memory", "3"])
    assert len(calls) == 2
    assert calls[0][0].workers == 2 and calls[0][0].memory == 3
    assert calls[1][0].key == MODEL and calls[1][0].player is True
    assert json.loads(capsys.readouterr().out)["topics"] == 2
    run(["--player-only"])
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)["write"] == 0
    assert (tmp_path / "site/charts/1_expert.json").is_file()
    assert (tmp_path / "site/models/sample.json").is_file()
    chart = json.loads((tmp_path / "site/charts/1_expert.json").read_bytes())
    entry = json.loads((tmp_path / "site/charts.json").read_bytes())["charts"][0]
    assert chart["chart"]["title"] == entry["title"]
    assert chart["flows"] == ["direct"] and chart["quality"] == 1
    assert chart["regions"] == entry["regions"] and chart["audioFormat"] == "aac"


def test_silent_chart_has_no_audio_format_in_either_manifest_or_index(command, tmp_path):
    run, _, _, _ = command
    run(["--pair", "1:expert", "--no-audio"])
    manifest = json.loads((tmp_path / "site/charts/1_expert.json").read_bytes())
    entry = json.loads((tmp_path / "site/charts.json").read_bytes())["charts"][0]
    assert manifest["audio"] is entry["audio"] is False
    assert manifest["audioFormat"] is entry["audioFormat"] is None


def test_cli_story_passes_fonts_languages_and_audio_policy(command, tmp_path):
    run, calls, _, _ = command
    run(
        [
            "--story",
            "1",
            "--story-languages",
            "en,ja",
            "--font",
            "en=a.otf",
            "--font",
            "ja=b.otf",
            "--font",
            "emoji=e.ttf",
            "--no-audio",
        ]
    )
    args, _ = calls[0]
    assert args.story_languages == ["en", "ja"]
    assert dict(args.font) == {"en": "a.otf", "ja": "b.otf", "emoji": "e.ttf"}
    assert args.audio_format == "aac" and args.fonts == "open"
    man = json.loads((tmp_path / "site/stories/1.json").read_bytes())
    assert man["audio"] is False and man["language"] == "ja"
    listing = json.loads((tmp_path / "site/stories.json").read_bytes())
    assert listing["stories"][0]["commands"] == ["Bgm", "FadeOut", "TalkWindow"]


def test_player_only_requires_only_depot_and_player_settings(command, tmp_path):
    run, calls, _, _ = command
    run(["--pair", "1:easy"])
    cfg = Config({"paths": {"depot": str(tmp_path / "depot"), "player": str(tmp_path / "player")}}, environ={})
    args = parse_args(
        ["--json", "site", "build", "--name", "example", "-o", str(tmp_path / "restored"), "--player-only"]
    )
    site.cmd_build(args, cfg)
    assert len(calls) == 1
    assert (tmp_path / "restored/charts/1_easy.json").is_file()


@pytest.mark.parametrize("selection", [["--all-live2d"], ["--all-stories"]])
def test_cli_uses_selected_regions_apk(command, selection):
    run, calls, cfg, _ = command
    run([*selection, "--site-region", "two"])
    assert calls[0][1].region() == "two"
    assert calls[0][1].path("paths", "apk") == cfg.path("servers.two", "apk")


@pytest.mark.parametrize(
    "flags, message",
    [
        (["--pair", "9:easy"], "charts not in"),
        (["--story", "9"], "stories not in"),
        (["--live2d", "missing"], "not a Live2D model"),
        (["--player-only"], "no saved catalog"),
        (["--player-only", "--live-option", "all"], "requires --pair"),
    ],
)
def test_cli_rejects_unavailable_requests_before_publication(command, tmp_path, capsys, flags, message):
    run, calls, _, _ = command
    with pytest.raises(SystemExit) as error:
        run(flags)
    assert error.value.code == 2 and message in capsys.readouterr().err
    assert not calls and not (tmp_path / "site").exists()


def test_story_fonts_extra_is_required_before_product_work(command, monkeypatch):
    run, calls, _, _ = command

    def missing(what):
        raise ConfigError(what + ": install nnnotes[fonts]")

    monkeypatch.setattr("nnnotes.unity.tmpfont.require_extra", missing)
    with pytest.raises(ConfigError, match="nnnotes\\[fonts\\]"):
        run(["--story", "1"])
    assert not calls


def test_cli_live_options_reach_the_product_request(command):
    run, calls, _, _ = command
    run(["--pair", "1:easy", "--live-option", "MirrorChart", "--live-option", "NoteDesignId=2"])
    assert calls[0][0].live_option == ["MirrorChart", "NoteDesignId=2"]


def test_failed_product_run_stops_before_layout(tmp_path, monkeypatch, capsys):
    depot = Depot(tmp_path)
    run = Run(depot, {}, [], {}, {})
    run.failures_.append(failure("live:1", "live", "source.error", "missing source"))
    monkeypatch.setattr(assets, "run_request", lambda *a, **kw: (run, None, None))
    args = SimpleNamespace(json=True, name="example")
    with pytest.raises(SystemExit) as error:
        site.product_entries(args, None, None)
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["failures"][0]["message"] == "missing source"


def test_failed_site_product_run_keeps_the_export_selection_pointer(tmp_path):
    from nnnotes.pipeline.orchestrate import Orchestrator
    from nnnotes.pipeline.stages import Stage
    from nnnotes.source.config import ConfigError

    class Stops(Stage):
        name, version = "test.stop", 1

        def subjects(self, env):
            return ["sample"]

        def run(self, task, depot):
            raise ConfigError("source unavailable")

    depot = Depot(tmp_path)
    selection = ["key:source"]
    original = Run(depot, {}, selection, {}, {}).write()
    stage = Stops()
    orch = Orchestrator(depot, {stage.name: stage}, workers=0, log=lambda _: None)
    ws = SimpleNamespace(
        depot=depot,
        selection=selection,
        run_index=lambda: None,
        facts=lambda *a, **kw: {"raw": {}},
        selected=SimpleNamespace(selected=[], census=[], raw=[]),
        log=lambda _: None,
        problems=lambda: [],
        context=lambda: {},
        wait=lambda: None,
        cancel=lambda: None,
    )
    run, _, aborted = assets.run_request(
        SimpleNamespace(), None, ws, prepared=([stage.name], {}, orch), selection_prefix=["site-product:example"]
    )
    assert aborted == "source unavailable"
    assert run.selection == ["site-product:example", *selection]
    assert depot.latest_run(selection) == original
    assert depot.latest_run(run.selection)["tasks"][0]["status"] == "failed"
