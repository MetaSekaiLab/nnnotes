"""The document of one story asset: a stage, frame, effect, post effect, still, chat window, chat icon, stamp or
transition, anything a story episode loads by catalog key.

A story asset product holds the objects of one catalog key with the files a player reads: `asset.json` names the key's
objects, the clip bindings of the Animators of the table's prefabs (livenotes.animator_bindings) and the shader name
table (prefabs.shader_table). The product's manifest is `storyassets/<name>.json`, where `<name>` is manifest_name(key).
"""

from __future__ import annotations

import re

from .livenotes import animator_bindings
from .prefabs import Objects, mesh_roles, shader_table

__all__ = ["ASSET_FORMAT", "KEY", "asset", "manifest_name"]

ASSET_FORMAT = 1  # asset.json "format"
KEY = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")  # the catalog keys a story asset product takes


def manifest_name(key: str) -> str:
    """The manifest name of catalog key `key`: its "/" replaced by "~" (ValueError for a key KEY does not match)."""
    if not KEY.fullmatch(key):
        raise ValueError(f"{key!r}: not a story asset key")
    return key.replace("/", "~")


def asset(objects: Objects, key: str) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(asset.json, the object ids the player reads, {object id: the roles of its files it reads}) of the story asset of
    catalog key `key` (module documentation)."""
    oids = sorted(o for o in objects.key(key) if o in objects)
    if not oids:
        raise ValueError(f"{key}: no objects of the key in the product's table")
    shaders, roles = shader_table(objects)
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    doc = {
        "format": ASSET_FORMAT,
        "key": key,
        "objects": oids,
        "bindings": animator_bindings(objects),
        "shaders": shaders,
    }
    return doc, keep, roles
