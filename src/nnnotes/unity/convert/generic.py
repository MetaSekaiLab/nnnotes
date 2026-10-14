"""Objects without a converter of their own (generic.json: the typetree JSON), and the bundle's AssetBundle objects
(bundle.json) and MonoScripts (scripts.json), which go into one document each."""

from __future__ import annotations

from .objects import BundleObjects, encode_object

__all__ = ["LOOSE", "generic", "loose_doc"]

# converter of the objects that go into one document -> (class, kind of the document)
LOOSE = {"bundle.json": ("AssetBundle", "unity.bundle"), "scripts.json": ("MonoScript", "unity.scripts")}


def generic(ctx: BundleObjects, info) -> None:
    """`json`: the object's typetree JSON."""
    key = (info.file, info.path_id)
    doc = ctx.tree(ctx.readers[key], ctx.tt(key))
    ctx.emit(key, "json", encode_object(doc), "json", "unity.object", "json", {})


def loose_doc(ctx: BundleObjects, conv: str, entries: dict) -> dict:
    """The document of converter `conv` (LOOSE) for `entries` ({object id: typetree JSON}): bundle.json also lists
    the bundle's serialized files with their Unity versions and externals."""
    if conv == "bundle.json":
        files = [
            {"name": f, "unityVersion": ctx.versions.get(f), "externals": list(ctx.view.externals(f))}
            for f in ctx.files
        ]
        return {"files": files, "assetBundles": entries}
    return {"scripts": entries}
