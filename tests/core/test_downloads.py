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


def test_foreign_exclusive_wait_is_bounded_without_taking_over(tmp_path):
    leases = tmp_path / "leases"
    leases.mkdir()
    lock = leases / downloads.EXCLUSIVE
    record = json.dumps({"host": socket.gethostname() + "-other", "pid": 1, "started": time.time()})
    lock.write_text(record)
    with pytest.raises(TimeoutError, match="exclusive lease.*still held"):
        with downloads.lease(tmp_path, None, wait_timeout=0):
            pytest.fail("must not enter while another host holds the exclusive lease")
    assert lock.read_text() == record and list(leases.iterdir()) == [lock]


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


def test_release_respects_the_budget_and_protects_active_files(tmp_path):
    root = tmp_path / "downloads"
    old = put(root, "bundles/old.bundle", 10, 1)
    ready = put(root, "bundles/ready.bundle", 10, 2)
    active = put(root, "bundles/active.bundle", 20, 3)
    assert downloads.release(root, [old, ready], 30) == {"removed": 1, "bytesRemoved": 10}
    assert not old.exists() and ready.exists() and active.exists()
    assert downloads.release(root, [ready], 5) == {"removed": 1, "bytesRemoved": 10}
    assert active.exists()


def test_zero_budget_release_examines_only_finished_paths(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    ready = put(root, "bundles/ready.bundle", 10, 1)
    active = put(root, "raw/active.bin", 20, 2)
    partial = put(root, "bundles/fetch.part", 5, 3)

    def scan(_):
        raise AssertionError("zero-budget release does not need a directory inventory")

    monkeypatch.setattr(downloads, "evictable", scan)
    assert downloads.release(root, [ready, ready, partial], 0) == {"removed": 1, "bytesRemoved": 10}
    assert active.exists() and partial.exists()


def test_release_validates_paths_and_keeps_unlimited_downloads(tmp_path):
    root = tmp_path / "downloads"
    file = put(root, "bundles/one.bundle", 10, 1)
    assert downloads.release(root, [file], None) == {"removed": 0, "bytesRemoved": 0}
    for path in [tmp_path / "outside", root / "catalog.bin"]:
        with pytest.raises(ValueError, match="within the downloads"):
            downloads.release(root, [path], 0)
    assert file.exists()


def test_release_keeps_locked_files_and_continues(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    locked = put(root, "bundles/locked.bundle", 10, 1)
    other = put(root, "bundles/other.bundle", 20, 2)
    unlink = type(locked).unlink

    def remove(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("open")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(locked), "unlink", remove)
    assert downloads.release(root, [locked, other], 0) == {"removed": 1, "bytesRemoved": 20}
    assert locked.exists() and not other.exists()


def test_release_rejects_symlinks_to_outside_files(tmp_path):
    root = tmp_path / "downloads"
    target = put(tmp_path, "outside.bin", 10, 1)
    link = root / "bundles/link.bundle"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="within the downloads"):
        downloads.release(root, [link], 0)
    assert target.exists() and link.is_symlink()
