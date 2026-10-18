"""Content-addressed font inputs shared by text products."""

from __future__ import annotations

from ..content.prefabs import script_class
from ..core.versions import PILLOW_ZLIB
from ..source.apkset import MANIFEST_IN_APK, manifest_version_name
from ..unity import fontsettings
from ..unity.ids import parse_object_id
from . import contract, units
from .assemble import Objects, ProductStage
from .contract import Input


def record_input(env, role: str, record: dict) -> Input:
    body = contract.encode(record)
    return Input(role, env.depot.put(body), len(body), None, ({"kind": "depot"},))


def font_files(objects: Objects) -> dict[str, str]:
    """The serialized file holding each TMP font asset, by asset name."""
    return {
        objects.info(o)["name"]: parse_object_id(o)[0]
        for o in objects.ids("MonoBehaviour")
        if script_class(objects, o) == fontsettings.FONT_CLASS
    }


def font_settings(task, objects, lang):
    from ..content import tmpassets

    bundles = contract.loads(objects.depot.input_bytes(task.input("font-bundles")))
    files = font_files(objects)
    version = manifest_version_name(objects.depot.input_bytes(task.input(f"apk:{MANIFEST_IN_APK}")))
    if version is None:
        raise ValueError(f"{task.stage}: the APK manifest has no version name")
    doc = tmpassets.build_font_settings(objects, lang, version, lambda name: bundles[files[name]])
    return fontsettings.FontSettings(doc)


def font_atoms() -> dict:
    from ..unity.atoms import impl_id

    return {"text.glyphs": impl_id(("fonttools", "freetype-py", "scipy", "numpy", "Pillow", PILLOW_ZLIB), 1)}


class FontInputsStage(ProductStage):
    """Font bundle identities and the APK version used by text layout and glyph generation."""

    APK = (MANIFEST_IN_APK,)

    def font_bundles(self, subject: str, env) -> Input:
        objects = Objects(self.reach(subject, env).table, env.depot)
        files = set(font_files(objects).values())
        bundles = {}
        for catalog in sorted(env.fact("catalogs")):
            addresses = units.address_doc(env, catalog)
            bundles.update({f: addresses["bundles"][b]["name"] for f, b in addresses["files"].items() if f in files})
        return record_input(env, "font-bundles", bundles)
