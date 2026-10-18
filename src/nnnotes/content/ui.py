"""UI prefab libraries assembled from serialized object products."""

from __future__ import annotations

import hashlib
from collections import Counter

from .prefabs import clip_bindings, component_class, controller_clips, has_json, prefab_ids, shader_table

FORMAT = "nnnotes.ui-pack/1"
LIBRARY = "nnnotes.ui-library/1"


def identity(key: str) -> str:
    """A stable file name for a catalog key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def pack(objects, key: str) -> tuple[dict, dict]:
    """The UI roots, reachable prefabs and controllers, with per-prefab animation bindings."""
    roots = sorted(objects.key(key))
    if not roots:
        raise ValueError(f"{key}: no UI objects")
    prefabs = prefab_ids(objects)
    controllers = [oid for oid in objects.ids("AnimatorController") if has_json(objects, oid)]
    bindings = {}
    for oid in prefabs:
        document = objects.json(oid)
        bindings[oid] = clip_bindings(objects, document, controller_clips(objects, document))
    shaders, roles = shader_table(objects)
    entries = []
    for oid in sorted(set(roots) | set(prefabs) | set(controllers)):
        info = objects.info(oid)
        entry = {"id": oid, "name": info.get("name", oid), "class": info["class"]}
        if oid in prefabs:
            nodes = objects.json(oid)["nodes"]
            entry.update(
                nodeCount=len(nodes),
                classes=dict(sorted(Counter(component_class(c) for n in nodes for c in n["components"]).items())),
            )
        entries.append(entry)
    return {
        "format": FORMAT,
        "key": key,
        "roots": roots,
        "prefabs": prefabs,
        "controllers": controllers,
        "entries": entries,
        "bindings": bindings,
        "shaders": shaders,
    }, roles


def library(packs) -> dict:
    """A library index of (manifest path, UI document) pairs."""
    assets, embedded, controllers = [], {}, {}
    for manifest, doc in sorted(packs):
        rows = {e["id"]: e for e in doc["entries"]}
        primary = rows[doc["roots"][0]]
        assets.append(
            {
                "id": identity(doc["key"]),
                "key": doc["key"],
                "name": doc["key"].rsplit("/", 1)[-1],
                "kind": "prefab" if primary["class"] == "GameObject" else primary["class"],
                "manifest": manifest,
                "roots": doc["roots"],
                "nodeCount": sum(rows[oid].get("nodeCount", 0) for oid in doc["roots"]),
            }
        )
        for oid in doc["prefabs"]:
            if oid not in doc["roots"]:
                embedded.setdefault(oid, {**rows[oid], "manifest": manifest, "roots": [oid], "kind": "prefab"})
        for oid in doc["controllers"]:
            controllers.setdefault(oid, {**rows[oid], "manifest": manifest})
    return {
        "format": LIBRARY,
        "assets": assets,
        "embedded": [embedded[k] for k in sorted(embedded)],
        "controllers": [controllers[k] for k in sorted(controllers)],
    }
