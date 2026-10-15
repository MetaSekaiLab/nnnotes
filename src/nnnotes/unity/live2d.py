"""Live2D (Cubism) model: Cubism-for-Unity prefab -> prefab.json + Cubism runtime files.

Components are identified by their MonoScript class (resolved through the APK's
shared_monoscripts bundle, so the Catalog must be opened with `apk=`).

`extract_runtime` writes what a re-implementation of the game's own runtime needs (`export_runtime` writes the
same and also returns the exporter state, for callers that export more of the model's bundles):

  <name>.moc3                 CubismMoc._bytes
  textures/*.png              atlas pages (Texture2D)
  <name>.prefab.json          the whole model prefab exported by export.Exporter:
                              every GameObject with every component (CubismRenderer/
                              MeshRenderer + materials per drawable, controllers,
                              Live2DCharacter, eye blink, harmonic motion, motion
                              sync, anchors ...), with the fade motion list, the
                              expression list and every AnimationClip (Mecanim clip
                              data + resolved bindings) inlined; plus `canvas` (moc3)

The texture each drawable is drawn with is its CubismRenderer._mainTexture in prefab.json, keyed by
CubismDrawable._unmanagedIndex (the Cubism Core drawable index).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.jsonio import write_json
from ..formats.cubism import moc3_canvas
from ..source.catalog import Catalog
from ..source.config import apk_missing
from .export import Exporter
from .reader import SceneGraph, script_class

# the moc is written as <name>.moc3; prefab.json names it only
STUB_ASSETS = ("CubismMoc",)


def _classes(env) -> dict[str | None, list]:
    by_cls: dict[str | None, list] = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            by_cls.setdefault(script_class(o), []).append(o)
    return by_cls


def _only(by_cls, cls: str, name: str) -> dict:
    objs = by_cls.get(cls, [])
    if len(objs) != 1:
        raise RuntimeError(f"{name}: {len(objs)} {cls}")
    return objs[0].read_typetree()


@dataclass
class RuntimeExport:
    """What `export_runtime` wrote and read: `summary` (the result of `extract_runtime`), the Exporter it wrote with
    (its shaders, textures, ...), the loaded bundle environment, its scene graph and the MonoBehaviours by script
    class."""

    summary: dict
    exporter: Exporter
    env: object
    graph: SceneGraph
    classes: dict[str | None, list]


def export_runtime(cat: Catalog, key: str, out_dir: Path) -> RuntimeExport:
    """Write the runtime files of the model `key` into `out_dir` (module docstring) and return them with the
    exporter state."""
    if cat.apk is None:
        raise apk_missing("resolving the Cubism component classes")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = key.rsplit("/", 1)[-1]
    ex = Exporter(cat, out_dir, inline_meshes=False, stub_assets=STUB_ASSETS)
    env, graph = ex.load(key)
    by_cls = _classes(env)
    moc = bytes(_only(by_cls, "CubismMoc", name)["_bytes"])
    (out_dir / f"{name}.moc3").write_bytes(moc)
    prefab = ex.prefab(key, loaded=(env, graph))
    prefab["canvas"] = moc3_canvas(moc)
    write_json(out_dir / f"{name}.prefab.json", prefab, indent=None)
    pages = sorted((o for o in env.objects if o.type.name == "Texture2D"), key=lambda o: o.read().m_Name)
    tex_files = [ex.texture(o)["texture"] for o in pages]
    res = {
        "name": name,
        "moc3": f"{name}.moc3",
        "prefab": f"{name}.prefab.json",
        "moc3Bytes": len(moc),
        "textures": tex_files,
        "nodes": len(prefab["nodes"]),
    }
    return RuntimeExport(res, ex, env, graph, by_cls)


def extract_runtime(cat: Catalog, key: str, out_dir: Path) -> dict:
    """Write the runtime files of the model `key` into `out_dir`; returns the summary: name, moc3, prefab,
    moc3Bytes, textures, nodes."""
    return export_runtime(cat, key, out_dir).summary
