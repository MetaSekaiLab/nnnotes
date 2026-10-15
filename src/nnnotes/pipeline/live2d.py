"""The Live2D products: a model for the player (live2d.model) and its Cubism SDK files (live2d.cubism).

Both are product stages (pipeline.assemble) whose subjects are model keys (Character/Live2D/<group>/<name>/model/
<name>, the fact "products") and whose roots are the objects of the key; the player's model also starts from the
materials the Cubism mask pass loads by Resources.Load path (content.live2d.RESOURCES), which the APK's built-in
files hold. They assemble with content.live2d:

live2d.model   "<task id>#model.json" (kind live2d.model: content.live2d.model) and the object table of what the
               player reads; its manifest is models/<name>.json
live2d.cubism  "<task id>#<path>" for every Cubism SDK file (content.live2d.cubism; kinds by file: CUBISM_KINDS) and
               no objects; its manifest is cubism/<name>.json, and its files are the model's directory
"""

from __future__ import annotations

from ..content import live2d
from . import contract
from .assemble import Assembly, ProductStage

__all__ = ["CUBISM", "CUBISM_KINDS", "MODEL", "CubismStage", "ModelStage", "cubism_kind"]

MODEL, CUBISM = "live2d.model", "live2d.cubism"
# Cubism SDK files -> artifact kinds (by suffix, the first that matches)
CUBISM_KINDS = (
    (".model3.json", "live2d.model3"),
    (".physics3.json", "live2d.physics3"),
    (".exp3.json", "live2d.expression"),
    (".motion3.json", "live2d.motion"),
    (".moc3", "live2d.moc3"),
    ("textures/", "live2d.texture"),
)


def cubism_kind(path: str) -> str:
    for suffix, kind in CUBISM_KINDS:
        if path.endswith(suffix) or path.startswith(suffix):
            return kind
    raise ValueError(f"{path}: not a Cubism SDK file")


class ModelStage(ProductStage):
    """live2d.model: a model key -> model.json and the object table the player reads (module documentation)."""

    name = MODEL
    version = 2
    KIND = "Live2DModel"

    def select(self, keys: list[str]) -> list[str]:
        return sorted(live2d.model_keys(keys).values())

    def keys(self, subject: str, env) -> list[str]:
        live2d.model_id(subject)
        return [subject]

    def resources(self, subject: str, env) -> list[str]:
        return sorted(live2d.RESOURCES.values())

    def manifest(self, subject: str) -> str:
        return f"models/{live2d.model_id(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = live2d.model(objects, task.subject)
        return Assembly({"model.json": (contract.encode(doc), "json", "live2d.model")}, keep, roles)


class CubismStage(ProductStage):
    """live2d.cubism: a model key -> its Cubism SDK files (module documentation)."""

    name = CUBISM
    version = 1
    KIND = "CubismModel"

    def select(self, keys: list[str]) -> list[str]:
        return sorted(live2d.model_keys(keys).values())

    def keys(self, subject: str, env) -> list[str]:
        live2d.model_id(subject)
        return [subject]

    def manifest(self, subject: str) -> str:
        return f"cubism/{live2d.model_id(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        files = {}
        for path, data in sorted(live2d.cubism(objects, task.subject).items()):
            files[path] = (data, path.rsplit(".", 1)[-1], cubism_kind(path))
        return Assembly(files, set())
