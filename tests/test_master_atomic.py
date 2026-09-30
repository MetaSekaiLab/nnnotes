"""Master downloads publish verified bytes using independent temporary files."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import synth
from nnnotes import cache, master


def test_concurrent_downloads_use_independent_temporary_files(tmp_path, monkeypatch):
    cdn = synth.serve_master_version(tmp_path / "cdn", "v", {"MasterA.bin": b"table"})
    out = tmp_path / "out"
    barrier = threading.Barrier(2)
    replace = cache.os.replace
    entered = set()
    lock = threading.Lock()

    def synchronized_replace(src, dst):
        ident = threading.get_ident()
        with lock:
            synchronize = dst.name == "MasterA.bin" and ident not in entered
            if synchronize:
                entered.add(ident)
        if synchronize:
            barrier.wait(timeout=5)
        return replace(src, dst)

    monkeypatch.setattr(cache.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: master.download(cdn, "v", out, workers=1), range(2)))
    assert [r["downloaded"] for r in results] == [1, 1]
    assert (out / "MasterA.bin").read_bytes() == b"table"
    assert sorted(p.name for p in out.iterdir()) == ["MasterA.bin", "MasterManifest.json"]


def test_failed_download_write_preserves_destination_and_removes_temp(tmp_path, monkeypatch):
    cdn = synth.serve_master_version(tmp_path / "cdn", "v", {"MasterA.bin": b"new table"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "MasterA.bin").write_bytes(b"previous table")
    replace = cache.os.replace

    def fail_table(src, dst):
        if dst.name == "MasterA.bin":
            raise OSError("simulated write failure")
        return replace(src, dst)

    monkeypatch.setattr(cache.os, "replace", fail_table)
    with pytest.raises(OSError, match="simulated write failure"):
        master.download(cdn, "v", out, workers=1)
    assert (out / "MasterA.bin").read_bytes() == b"previous table"
    assert sorted(p.name for p in out.iterdir()) == ["MasterA.bin", "MasterManifest.json"]

def test_transient_windows_rename_failure_is_retried(tmp_path, monkeypatch):
    cdn = synth.serve_master_version(tmp_path / "cdn", "v", {"MasterA.bin": b"table"})
    replace = cache.os.replace
    attempts = []

    def temporarily_busy(src, dst):
        if dst.name == "MasterA.bin":
            attempts.append(src)
            if len(attempts) == 1:
                raise PermissionError("temporarily busy")
        return replace(src, dst)

    monkeypatch.setattr(cache.os, "replace", temporarily_busy)
    out = tmp_path / "out"
    assert master.download(cdn, "v", out, workers=1)["downloaded"] == 1
    assert len(attempts) == 2
    assert (out / "MasterA.bin").read_bytes() == b"table"
    assert not list(out.glob("*.part"))
