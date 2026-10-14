"""canon/1, the canonical JSON of nnnotes' documents, and the hashes taken of JSON values and bytes.

canon/1        UTF-8, LF, one trailing newline, non-ASCII as is, indent 1. Contract documents: keys sorted.
               Object documents: the object's own field order. Non-finite floats as `1e999` / `-1e999`
               (jsonio), NaN as {"$float": "nan"}.
content id     sha256 of the bytes (hex).
key hash       sha256 of the compact canonical text (ASCII only, keys sorted) of a JSON value.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any, cast

from . import jsonio

__all__ = [
    "CANON",
    "NAN",
    "digest",
    "dumps",
    "encode",
    "encode_chunks",
    "is_sha256",
    "key_text",
    "loads",
    "sha256",
]

CANON = "canon/1"
NAN = {"$float": "nan"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _tag_nan(v: Any) -> Any:
    """`v` with every NaN replaced by {"$float": "nan"}; containers are copied only where something changed."""
    if isinstance(v, float):
        return dict(NAN) if v != v else v
    if isinstance(v, dict):
        d = cast(dict[Any, Any], v)
        out: dict[Any, Any] | None = None
        for k, x in d.items():
            y = _tag_nan(x)
            if y is not x:
                if out is None:
                    out = dict(d)
                out[k] = y
        return d if out is None else out
    if isinstance(v, (list, tuple)):
        s = cast(list[Any] | tuple[Any, ...], v)
        items: list[Any] | None = None
        for i, x in enumerate(s):
            y = _tag_nan(x)
            if y is not x:
                if items is None:
                    items = list(s)
                items[i] = y
        return s if items is None else items
    return v


def _text(obj: Any, **kw: Any) -> str:
    try:
        return jsonio.dumps(obj, **kw)
    except ValueError:
        tagged = _tag_nan(obj)
        if tagged is obj:
            raise  # not a NaN: the writer's own error
    return jsonio.dumps(tagged, **kw)


def dumps(obj: Any, *, sort_keys: bool = True) -> str:
    """The canon/1 text of a document: keys sorted (`sort_keys=False` keeps the document's order, for object
    documents), indent 1, non-ASCII as is, one trailing newline."""
    return _text(obj, indent=1, ensure_ascii=False, sort_keys=sort_keys) + "\n"


def encode(obj: Any, *, sort_keys: bool = True) -> bytes:
    """dumps() as UTF-8 bytes."""
    return dumps(obj, sort_keys=sort_keys).encode("utf-8")


def encode_chunks(obj: Any, size: int = 1 << 20) -> Iterator[bytes]:
    """encode(obj) as byte strings of about `size` bytes, made as they are written (a large document's text is
    never whole in memory, nor the encoder's list of its pieces). Raises ValueError, possibly after some chunks,
    for a document with a non-finite number: encode() writes those."""
    enc = json.JSONEncoder(indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False)
    buf: list[str] = []
    n = 0
    for piece in enc.iterencode(obj):
        buf.append(piece)
        n += len(piece)
        if n >= size:
            yield "".join(buf).encode("utf-8")
            buf, n = [], 0
    buf.append("\n")
    yield "".join(buf).encode("utf-8")


def _untag(d: dict[str, Any]) -> Any:
    return float("nan") if d == NAN else d


def loads(text: str | bytes | bytearray) -> Any:
    """A canon/1 (or any JSON) document: {"$float": "nan"} reads back as NaN, 1e999 as infinity."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    return json.loads(text, object_hook=_untag)


def key_text(value: Any) -> str:
    """The compact canonical text a key hash is taken of: keys sorted, ASCII only, no whitespace."""
    return _text(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def digest(value: Any) -> str:
    """The key hash of a JSON value (sha256 hex of key_text)."""
    return hashlib.sha256(key_text(value).encode("ascii")).hexdigest()


def sha256(data: bytes | bytearray | memoryview) -> str:
    """The content id of bytes."""
    return hashlib.sha256(data).hexdigest()


def is_sha256(s: object) -> bool:
    return isinstance(s, str) and _SHA256.fullmatch(s) is not None
