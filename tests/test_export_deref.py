"""Exporter.deref: references into a bundle file that no catalog bundle provides."""
import pytest

from nnnotes import export


class Obj:
    def __init__(self, path_id):
        self.path_id = path_id


def make(tmp_path, monkeypatch, objs):
    ex = export.Exporter(None, tmp_path)
    for o in objs:
        ex._by_pid.setdefault(o.path_id, []).append(o)

    def missing(owner, pptr):
        raise FileNotFoundError("CAB-1")
    monkeypatch.setattr(export, "deref", missing)
    monkeypatch.setattr(export, "external_path", lambda owner, pptr: "archive:/CAB-1/CAB-1")
    return ex


def test_dangling_reference_resolves_to_the_only_loaded_object(tmp_path, monkeypatch):
    target = Obj(77)
    ex = make(tmp_path, monkeypatch, [target, Obj(78)])
    assert ex.deref(None, {"m_FileID": 3, "m_PathID": 77}) is target
    assert ex.unresolved == {}


def test_dangling_reference_without_a_holder_is_null(tmp_path, monkeypatch):
    ex = make(tmp_path, monkeypatch, [Obj(78)])
    with pytest.warns(UserWarning, match="no bundle of the catalog"):
        assert ex.deref(None, {"m_FileID": 3, "m_PathID": 77}) is None
    assert ex.unresolved == {"archive:/CAB-1/CAB-1": 1}


def test_dangling_reference_held_twice_is_null(tmp_path, monkeypatch):
    ex = make(tmp_path, monkeypatch, [Obj(77), Obj(77)])
    with pytest.warns(UserWarning):
        assert ex.deref(None, {"m_FileID": 3, "m_PathID": 77}) is None
