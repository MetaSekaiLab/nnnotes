"""The one JSON writer of the extractors.

Every JSON file the extractors write goes through `write_json` (or `dumps` for JSON embedded
elsewhere): UTF-8, LF line endings, and non-finite floats as the out-of-range literals
`1e999` / `-1e999`, which are valid JSON and read back as +/-Infinity by JSON.parse and
Python's json.loads (the bare `Infinity` token Python writes by default is not JSON).
NaN has no such literal and raises, naming where it occurs.

Most documents have no non-finite float: they are encoded by json.dumps directly, and only a document that
json.dumps refuses (or whose text holds a placeholder) takes the walk that finds and replaces them.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

_POS, _NEG = "\x00nnnotes:+inf", "\x00nnnotes:-inf"        # placeholders, replaced after encoding
_TOKENS = ((json.dumps(_POS), "1e999"), (json.dumps(_NEG), "-1e999"))
_MARK = json.dumps(_POS)[1:-len("+inf") - 1]                 # the placeholders' common prefix as JSON text


class _NaN(ValueError):
    def __init__(self):
        super().__init__()
        self.path: list[str] = []


def _finite(v, found: list):
    """`v` with every non-finite float replaced by a placeholder string; containers are copied only
    where something changed, so the caller's document is never modified."""
    if isinstance(v, float):
        if math.isfinite(v):
            return v
        if v != v:
            raise _NaN()
        found.append(True)
        return _POS if v > 0 else _NEG
    if isinstance(v, dict):
        out = None
        for k, x in v.items():
            if k == _POS or k == _NEG:
                raise ValueError(f"key {k!r} collides with the non-finite placeholder")
            try:
                y = _finite(x, found)
            except _NaN as e:
                e.path.append(f"/{k}")
                raise
            if y is not x:
                if out is None:
                    out = dict(v)
                out[k] = y
        return v if out is None else out
    if isinstance(v, (list, tuple)):
        out = None
        for i, x in enumerate(v):
            try:
                y = _finite(x, found)
            except _NaN as e:
                e.path.append(f"[{i}]")
                raise
            if y is not x:
                if out is None:
                    out = list(v)
                out[i] = y
        return v if out is None else out
    if isinstance(v, str) and (v == _POS or v == _NEG):
        raise ValueError(f"string {v!r} collides with the non-finite placeholder")
    return v


def dumps(obj, **kw) -> str:
    """json.dumps with non-finite floats as 1e999 / -1e999; raises ValueError on NaN (with its path).
    Keyword arguments are json.dumps' (indent, ensure_ascii, separators, default, ...)."""
    try:
        s = json.dumps(obj, allow_nan=False, **kw)
    except ValueError:
        pass                                   # a non-finite float (or NaN): the walk below
    else:
        if _MARK not in s:
            return s                           # (a placeholder string in the data: the walk rejects it)
    found: list = []
    try:
        clean = _finite(obj, found)
    except _NaN as e:
        raise ValueError(f"NaN at {''.join(reversed(e.path)) or '/'} (no JSON representation)") from None
    s = json.dumps(clean, allow_nan=False, **kw)
    if found:
        for token, literal in _TOKENS:
            s = s.replace(token, literal)
    return s


def write_json(path, obj, *, indent: int | None = 1, ensure_ascii: bool = False, **kw) -> Path:
    """Write `obj` as JSON to `path` (UTF-8, LF); see `dumps`. Returns the path."""
    path = Path(path)
    path.write_text(dumps(obj, indent=indent, ensure_ascii=ensure_ascii, **kw), encoding="utf-8", newline="\n")
    return path
