"""Persistent site topics and the complete file closure of their product snapshots."""

from __future__ import annotations

import json
import re

from ..formats import compression
from ..pipeline import contract, siteindex, siteproducts
from ..pipeline.contract import Input
from .products import REFERENCE_FIELDS, Snapshot, _strings


def load(depot, name):
    """The last successfully materialized catalog and its layout entries, fetched through the depot."""
    run = depot.latest_run(["site:" + name])
    if not run:
        return None, {}
    sha = run.get("layouts", {}).get("site")
    if not sha:
        raise ValueError(f"site {name}: previous run has no site layout")
    layout = contract.loads(depot.read(sha))
    entries = {e["path"]: e for e in layout["entries"]}
    catalog = contract.loads(depot.read(entries["site.json"]["sha256"]))
    if catalog.get("format") != siteindex.CATALOG or catalog.get("name") != name:
        raise ValueError("site catalog identity differs")
    return catalog, entries


def merge(old, name, language, regions, topics, replace=()):
    """Update named topics; complete selections replace their region-kind group."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("site name must contain letters, digits, underscores or hyphens")
    replace = set(replace)
    known = {
        (t["region"], t["kind"], t["id"]): t
        for t in (old or {}).get("topics", [])
        if (t["region"], t["kind"]) not in replace
    }
    known.update({(t["region"], t["kind"], t["id"]): t for t in topics})
    rs = {r["id"]: r for r in (old or {}).get("regions", [])}
    rs.update({r["id"]: r for r in regions})
    return {
        "format": siteindex.CATALOG,
        "name": name,
        "language": language,
        "regions": list(rs.values()),
        "topics": [known[k] for k in sorted(known)],
    }


def assemble(depot, catalog, entries):
    """Index tasks, aliases and the exact reachable files of the complete catalog."""
    runner = Snapshot(depot, {})
    body = contract.encode(catalog)
    inp = Input("catalog", depot.put(body), len(body), None, ({"kind": "depot"},))
    result = runner._task(siteindex.IndexStage(), catalog["name"], [inp])
    all_entries = dict(entries)
    output = {}
    aliases = {}
    for art in result["artifacts"]:
        role = contract.parse_artifact_id(art["id"])[1]
        c = art["content"]
        if role == "aliases.json":
            aliases = json.loads(depot.read(c["sha256"]))
        else:
            output[role] = {"path": role, "sha256": c["sha256"], "size": c["size"], "id": art["id"]}
    visited = set()

    def keep(path):
        if path not in all_entries:
            raise ValueError(f"site reference has no file: {path}")
        output[path] = all_entries[path]

    def content(file):
        if "parts" in file:
            return (
                b"{"
                + b",".join(json.dumps(k, ensure_ascii=False).encode() + b":" + raw(p) for k, p, *_ in file["parts"])
                + b"}"
            )
        return raw(file["asset"])

    def raw(path):
        keep(path)
        return compression.decode(path, depot.read(all_entries[path]["sha256"]))

    def product(path):
        if path in visited:
            return
        visited.add(path)
        keep(path)
        manifest = json.loads(depot.read(all_entries[path]["sha256"]))
        if manifest.get("schema") != contract.PRODUCT:
            raise ValueError(f"{path}: expected a product manifest")
        for files in [manifest["files"], *(obj["files"] for obj in manifest["objects"].values())]:
            for file in files.values():
                for p in [r[1] for r in file["parts"]] if "parts" in file else [file["asset"]]:
                    keep(p)
        for logical, paths in REFERENCE_FIELDS.get(manifest["product"], {}).items():
            doc = json.loads(content(manifest["files"][logical]))
            for field in paths:
                for reference in _strings(siteproducts.at(doc, field)):
                    product(reference)
        for reference in manifest.get("variants", []):
            product(reference)

    for path in aliases.values():
        product(path)
    for path, target in aliases.items():
        manifest = json.loads(depot.read(all_entries[target]["sha256"]))
        art = runner._document(
            siteproducts.ManifestStage(),
            contract.encode(manifest),
            {
                "files": manifest["files"],
                "objects": {oid: obj["files"] for oid, obj in manifest["objects"].items()},
                "root": "../" * (len(path.split("/")) - 1),
            },
        )
        c = art["content"]
        output[path] = {"path": path, "sha256": c["sha256"], "size": c["size"], "id": art["id"]}
    return output, runner.run
