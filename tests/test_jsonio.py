import json
import math

import pytest

from nnnotes.jsonio import dumps, write_json


def test_infinity_literals():
    s = dumps({"a": math.inf, "b": [-math.inf, 1.5], "c": "inf"})
    assert s == '{"a": 1e999, "b": [-1e999, 1.5], "c": "inf"}'
    back = json.loads(s)
    assert back["a"] == math.inf and back["b"][0] == -math.inf


def test_nan_raises_with_path():
    with pytest.raises(ValueError, match=r"NaN at /a\[1\]/b"):
        dumps({"a": [0, {"b": math.nan}]})


def test_document_not_modified():
    doc = {"x": [math.inf], "y": {"z": -math.inf}}
    dumps(doc)
    assert doc["x"][0] == math.inf and doc["y"]["z"] == -math.inf


def test_placeholder_collision_rejected():
    with pytest.raises(ValueError, match="collides"):
        dumps({"v": "\x00nnnotes:+inf"})


def test_write_json_utf8_lf_deterministic(tmp_path):
    doc = {"名前": "テスト", "list": [1, 2, {"k": math.inf}], "nested": {"b": 1, "a": 2}}
    a = write_json(tmp_path / "a.json", doc)
    b = write_json(tmp_path / "b.json", doc)
    data = a.read_bytes()
    assert data == b.read_bytes()
    assert b"\r" not in data and "テスト".encode("utf-8") in data
    assert data == dumps(doc, indent=1, ensure_ascii=False).encode("utf-8")
    assert data.splitlines()[1] == b' "\xe5\x90\x8d\xe5\x89\x8d": "\xe3\x83\x86\xe3\x82\xb9\xe3\x83\x88",'
    assert json.loads(data)["list"][2]["k"] == math.inf


def test_dumps_passes_json_options():
    assert dumps({"b": 1, "a": [1, 2]}, sort_keys=True, separators=(",", ":")) == '{"a":[1,2],"b":1}'
