"""Objects without a converter of their own (generic.json: the typetree JSON)."""

from __future__ import annotations

from .objects import BundleObjects, encode_object

__all__ = ["generic"]


def generic(ctx: BundleObjects, info) -> str | None:
    """`json`: the object's typetree JSON. Returns why when the typetree could be read only in part (its part is
    the JSON: BundleObjects.partial), else None."""
    key = (info.file, info.path_id)
    doc = ctx.tree(ctx.readers[key], ctx.tt(key, whole=False))
    ctx.emit(key, "json", encode_object(doc), "json", "unity.object", "json", {})
    return ctx.partial.get(key)
