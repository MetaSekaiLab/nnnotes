"""The downloads of a depot as a bounded cache: budgets, least-recently-used trimming, leases of running commands."""

import json
import os
import socket
import subprocess
import sys
import time

import pytest

from nnnotes.core import downloads


def put(root, rel, size, mtime):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    os.utime(p, ns=(mtime, mtime))
    return p


@pytest.mark.parametrize(
    "text, size",
    [
        (None, 0),
        ("", 0),
        ("0", 0),
        ("all", None),
        ("ALL", None),
        ("123", 123),
        ("500MB", 500_000_000),
        ("1.5 gb", 1_500_000_000),
        ("20GiB", 20 << 30),
        ("2kib", 2048),
    ],
)
def test_parse_budget(text, size):
    assert downloads.parse_budget(text) == size


@pytest.mark.parametrize("text", ["-1", "lots", "5 parsecs", "1e9"])
def test_parse_budget_names_the_forms(text):
    with pytest.raises(ValueError, match="not a size"):
        downloads.parse_budget(text)


def test_trim_removes_the_least_recently_used_bundles_and_raw_files(tmp_path):
    d = tmp_path / "downloads"
    old = put(d, "bundles/old.bundle", 100, 1_000_000_000)
    mid = put(d, "international/abc/1.0/raw/cri/x.awb", 100, 2_000_000_000)
    new = put(d, "jp/site/v/h/bundles/new.bundle", 100, 3_000_000_000)
    catalog = put(d, "catalog_main_xx.bin", 1000, 0)
    put(d, "bundles/torn.part", 10, 0)

    assert downloads.trim(d, 150, dry_run=True) == {"kept": 1, "removed": 2, "bytesKept": 100, "bytesRemoved": 200}
    assert old.exists() and mid.exists()
    assert downloads.trim(d, 150)["removed"] == 2
    assert not old.exists() and not mid.exists() and new.exists() and catalog.exists()
    assert not (d / "international").exists()  # emptied directories go too
    assert downloads.trim(d, None) == {"kept": 1, "removed": 0, "bytesKept": 100, "bytesRemoved": 0}
    assert downloads.trim(d, 0)["removed"] == 1 and catalog.exists()


def test_using_a_file_keeps_it_longest(tmp_path):
    d = tmp_path / "downloads"
    first = put(d, "bundles/a.bundle", 10, 1_000_000_000)
    second = put(d, "bundles/b.bundle", 10, 2_000_000_000)
    downloads.touch(first)
    downloads.trim(d, 10)
    assert first.exists() and not second.exists()


def test_the_last_command_to_end_trims(tmp_path):
    f = put(tmp_path / "downloads", "bundles/a.bundle", 10, 1_000_000_000)
    with downloads.lease(tmp_path, 0) as outer:
        with downloads.lease(tmp_path, 0):
            pass
        assert f.exists()  # the outer command still runs
        assert [p.name for p in (tmp_path / "leases").iterdir()] == [outer.name]
    assert not f.exists() and not list((tmp_path / "leases").iterdir())


def test_no_budget_keeps_everything(tmp_path):
    f = put(tmp_path / "downloads", "bundles/a.bundle", 10, 1_000_000_000)
    with downloads.lease(tmp_path, None):
        pass
    assert f.exists()


def test_leases_of_ended_processes_are_dropped(tmp_path):
    ended = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    leases = tmp_path / "leases"
    leases.mkdir()
    host = socket.gethostname()
    stale = leases / "stale.json"
    stale.write_text(json.dumps({"host": host, "pid": int(ended.stdout), "started": time.time()}))
    old = leases / "old.json"
    old.write_text(json.dumps({"host": host + "-other", "pid": 1, "started": time.time() - 2 * 24 * 3600}))
    f = put(tmp_path / "downloads", "bundles/a.bundle", 10, 1_000_000_000)
    with downloads.lease(tmp_path, 0):
        pass
    assert not f.exists() and not list(leases.iterdir())
    foreign = leases / "foreign.json"
    foreign.write_text(json.dumps({"host": host + "-other", "pid": 1, "started": time.time()}))
    assert downloads.others_live(tmp_path)  # a recent lease of another host counts


def test_a_command_waits_for_a_trim_under_way_and_drops_a_stale_one(tmp_path, monkeypatch):
    leases = tmp_path / "leases"
    leases.mkdir()
    lock = leases / downloads.EXCLUSIVE
    lock.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "started": time.time()}))
    waited = []
    monkeypatch.setattr(downloads.time, "sleep", lambda s: waited.append(s) or lock.unlink())
    with downloads.lease(tmp_path, None):
        assert waited and not lock.exists()  # the trimming command ended
    lock.write_text(json.dumps({"host": socket.gethostname(), "pid": 0, "started": time.time()}))
    with downloads.lease(tmp_path, None):
        assert not lock.exists() and len(waited) == 1  # its holder is gone


def test_a_trim_under_way_elsewhere_is_left_to_its_command(tmp_path):
    f = put(tmp_path / "downloads", "bundles/a.bundle", 10, 1_000_000_000)
    with downloads.lease(tmp_path, 0):
        (tmp_path / "leases" / downloads.EXCLUSIVE).write_text("{")  # another command runs alone
    assert f.exists()


def test_exclusive_is_granted_to_the_only_command(tmp_path):
    with downloads.lease(tmp_path, None) as own:
        with downloads.exclusive(tmp_path, own) as alone:
            assert alone and (tmp_path / "leases" / downloads.EXCLUSIVE).exists()
            with downloads.exclusive(tmp_path, own) as again:
                assert not again  # held
        with downloads.lease(tmp_path, None):
            with downloads.exclusive(tmp_path, own) as alone:
                assert not alone  # another lease is live
    assert not list((tmp_path / "leases").iterdir())
