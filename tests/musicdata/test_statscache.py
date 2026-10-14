"""Python/native cache boundary tests, using controlled /3 documents rather than a cache implementation.

The Rust tests exercise program/run reuse, persistence and concurrency. These tests establish that Python passes
complete inputs and options to that implementation, preserves its reported counters and publishes identical data.
"""

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from nnnotes import musicdata
from nnnotes.musicdata import deckdata
from tests.musicdata.test_musicdata import CHARTS, KEY, PROV, FakeDeck, all_rows, bgm, master_dir, run


@pytest.fixture
def deck_input():
    tables = {name: all_rows(name) for name, _ in deckdata.TABLES}
    scores = [row for row in tables["MasterLiveMusicScore"] if row["_id"] != 40]
    return deckdata.build(tables, deckdata.charts(scores, CHARTS.__getitem__), {"region": "xx", "tables": {}})


class CacheBridge(FakeDeck):
    """An instrumented native interface, not a simulation of native cache hits or performance."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cache_calls = []
        self.counters = {"requests": 29, "hits": 19, "computed": 10, "writes": 10, "invalid": 2, "bytes": 5000}

    def chart_stats_cached(self, data, seeds, workers, aptitude=True, cache_dir=None):
        self.cache_calls.append((json.loads(data), seeds, workers, aptitude, cache_dir))
        return self.chart_stats(data, seeds, workers, aptitude=aptitude), dict(self.counters)


def test_cached_calls_pass_the_whole_current_input_and_report_native_counters(tmp_path, deck_input):
    module = CacheBridge()
    directory = tmp_path / "native cache"
    directory.mkdir()
    retained = directory / "old-entry.json"
    retained.write_bytes(b"native-owned entry")
    before = deckdata.encode(deck_input)
    deck = musicdata.Deck(module=module, seeds=4, workers=3, aptitude=False, cache=directory)
    result = deck.stats(deck_input)
    assert module.cache_calls == [(json.loads(before), 4, 3, False, str(directory))]
    assert deck.counts == module.counters
    assert result["format"] == "ournotes-deck.chart-stats/3"
    assert deckdata.encode(deck_input) == before and retained.read_bytes() == b"native-owned entry"
    assert list(directory.iterdir()) == [retained]  # Python neither replaces nor garbage-collects native records.
    current = deepcopy(deck_input)
    current["provenance"]["revision"] = "next"
    current["master"]["MasterEvent"] = {"columns": ["_id"], "rows": [[99]]}
    result = deck.stats(current)
    assert module.cache_calls[-1][0] == json.loads(deckdata.encode(current))
    assert result["source"] == current["provenance"]


def test_uncached_calls_do_not_invent_native_cache_counters(deck_input):
    module = CacheBridge()
    deck = musicdata.Deck(module=module, seeds=2, workers=1)
    deck.stats(deck_input)
    assert module.cache_calls == [] and len(module.inputs) == 1
    assert module.inputs[0][1:] == (2, 1) and module.options == [True]
    assert deck.counts is None


@pytest.mark.parametrize(
    "options, flag",
    [
        ({"seeds": -1}, "--seeds"),
        ({"seeds": 0}, "--seeds"),
        ({"seeds": True}, "--seeds"),
        ({"seeds": 1 << 100}, "--seeds"),
        ({"workers": -1}, "--workers"),
        ({"workers": False}, "--workers"),
        ({"workers": 1 << 100}, "--workers"),
    ],
)
def test_invalid_options_are_user_errors_before_native_or_export_work(tmp_path, capsys, options, flag):
    with pytest.raises(musicdata.MusicDataError, match=flag):
        musicdata.Deck(**options)
    value = next(iter(options.values()))
    if not isinstance(value, bool):
        output = tmp_path / "music.json"
        code, out, error = run(["music-data", "--decoded-master", "-o", str(output), flag, str(value)], capsys)
        assert code == 1 and not out and flag in error and "Traceback" not in error
        assert not output.exists()


def test_zero_workers_selects_native_automatic_parallelism(deck_input):
    module = CacheBridge()
    deck = musicdata.Deck(module=module, seeds=1, workers=0)
    deck.stats(deck_input)
    assert module.inputs[0][1:] == (1, None)


def test_sampling_format_is_rejected_before_calling_the_model(deck_input):
    module = FakeDeck()
    module.FORMAT = "ournotes-deck.chart-stats/2"
    with pytest.raises(musicdata.MusicDataError, match="format .*chart-stats/2.* is not supported"):
        musicdata.Deck(module=module).stats(deck_input)
    assert module.inputs == []


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.update(format="ournotes-deck.chart-stats/2"),
        lambda doc: doc["charts"].reverse(),
        lambda doc: doc["charts"].append(doc["charts"][0]),
        lambda doc: doc["charts"][0].update(scoreId=True),
    ],
)
def test_model_output_format_and_chart_identity_are_checked(deck_input, change):
    with pytest.raises(musicdata.MusicDataError):
        musicdata.Deck(module=FakeDeck(header=change)).stats(deck_input)


@pytest.mark.parametrize("response", ["{", "[]", "null", "{}"])
def test_malformed_native_documents_raise_a_domain_error(deck_input, response):
    class BadDocument(FakeDeck):
        def chart_stats(self, *args, **kwargs):
            return response

    with pytest.raises(musicdata.MusicDataError, match="deck model:"):
        musicdata.Deck(module=BadDocument()).stats(deck_input)


def test_cache_interface_failure_does_not_replace_an_existing_export(tmp_path):
    source = master_dir(tmp_path)
    out = tmp_path / "music.json"
    out.write_bytes(b"previous published artifact\n")
    with pytest.raises(musicdata.MusicDataError, match="interrupted native run"):
        musicdata.export(
            out,
            deckdata.master_files(source),
            KEY,
            CHARTS.__getitem__,
            bgm,
            **PROV,
            deck=musicdata.Deck(module=CacheBridge(fail="interrupted native run"), cache=tmp_path / "stats"),
        )
    assert out.read_bytes() == b"previous published artifact\n"


@pytest.mark.parametrize("compressed", [False, True])
def test_native_cache_transport_does_not_change_exported_bytes(tmp_path, compressed):
    source = master_dir(tmp_path)
    sources = {path.name: path.read_bytes() for path in source.iterdir()}
    outputs, summaries = [], []
    for name, cache in (("uncached", None), ("cached", tmp_path / "stats")):
        out = tmp_path / (name + (".json.gz" if compressed else ".json"))
        summaries.append(
            musicdata.export(
                out,
                deckdata.master_files(source),
                KEY,
                CHARTS.__getitem__,
                bgm,
                **PROV,
                full=True,
                deck=musicdata.Deck(module=CacheBridge(), cache=cache, workers=2),
            )
        )
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1] and summaries[0]["sha256"] == summaries[1]["sha256"]
    assert summaries[0]["deckStats"] is None
    assert summaries[1]["deckStats"] == CacheBridge().counters
    doc = json.loads(gzip.decompress(outputs[0]) if compressed else outputs[0])
    assert doc["format"] == "nnnotes.music-data/2"
    assert doc["provenance"]["deck"]["format"] == "ournotes-deck.chart-stats/3"
    assert "deckStats" not in doc
    assert {path.name: path.read_bytes() for path in source.iterdir()} == sources


@pytest.mark.parametrize("option", ["--aptitude-max-seeds", "--aptitude-cross-seeds", "--allow-unconverged-aptitude"])
def test_removed_sampling_options_are_not_silently_accepted(capsys, option):
    argv = ["music-data", "--decoded-master", "-o", "out.json", option]
    argv += [] if option == "--allow-unconverged-aptitude" else ["32"]
    code, _, error = run(argv, capsys)
    assert code == 2 and "unrecognized arguments" in error


def test_the_native_cached_entry_point_rejects_bad_input(tmp_path):
    native = pytest.importorskip("ournotes_sim")
    with pytest.raises(ValueError, match="not a deck data file"):
        native.chart_stats_cached('{"format":"invalid"}', cache_dir=str(tmp_path / "stats"))
    assert not list(Path(tmp_path).glob("stats/**/*.json"))
