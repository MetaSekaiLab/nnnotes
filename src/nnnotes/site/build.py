"""Build and publish a named site's complete product and page layout."""

from __future__ import annotations

import hashlib
import json

from ..pipeline import contract
from ..pipeline.orchestrate import Run
from ..pipeline.siteproducts import at
from . import catalog, pages, publish
from .products import REFERENCE_FIELDS, Snapshot, _strings
from .reads import Reader


def combine(run, other):
    """Combine content-addressed site tasks, counting repeated tasks once."""
    for tid, task in other.tasks.items():
        old = run.tasks.get(tid)
        if old and old["key"] != task["key"]:
            raise ValueError(f"site task {tid} has conflicting keys")
        if not old or task["status"] != "hit":
            run.tasks[tid] = task
    run.stages.update(other.stages)
    run.params.update(other.params)
    run.log.extend(other.log)
    run.failures_.extend(other.failures_)


def documents(depot, entries):
    """Decoded product manifests from an unencoded product layout."""
    out = {}
    for e in entries:
        if e["path"].startswith("assets/") or not e["path"].endswith(".json"):
            continue
        doc = json.loads(depot.read(e["sha256"]))
        if doc.get("schema") == contract.PRODUCT:
            out[e["path"]] = doc
    return out


def document(depot, manifests, path, logical):
    entry = manifests[path]["files"][logical]
    return json.loads(depot.read(entry["asset"].split("/")[-1].split(".")[0]))


def model_stats(snapshot, source):
    manifest = snapshot.documents[source]
    files = [*manifest["files"].values(), *(e for o in manifest["objects"].values() for e in o["files"].values())]
    return {"bytes": sum(e["size"] for e in files), "files": len(files)}


def size(snapshot, source, language=None):
    """Unique decoded asset bytes in a product's closure for one optional language."""
    from ..formats import compression

    by_target = {snapshot.paths[p]: m for p, m in snapshot.documents.items()}
    visited, files = set(), {}

    def raw(path):
        return compression.decode(path, snapshot.depot.read(snapshot.entries[path]["sha256"]))

    def visit(path):
        if path in visited:
            return
        visited.add(path)
        man = by_target[path]
        for group in [man["files"], *(o["files"] for o in man["objects"].values())]:
            for e in group.values():
                if "parts" in e:
                    files.update({p: n for _, p, n, *_ in e["parts"]})
                else:
                    files[e["asset"]] = e["size"]
        for logical, paths in REFERENCE_FIELDS.get(man["product"], {}).items():
            e = man["files"][logical]
            doc = {k: json.loads(raw(p)) for k, p, *_ in e["parts"]} if "parts" in e else json.loads(raw(e["asset"]))
            for field in paths:
                refs = at(doc, field)
                if language is not None and isinstance(refs, dict):
                    refs = {
                        k: ({language: v[language]} if isinstance(v, dict) and language in v else v)
                        for k, v in refs.items()
                    }
                for ref in _strings(refs):
                    visit(ref)
        for ref in man.get("variants", []):
            visit(ref)

    visit(snapshot.paths[source])
    return sum(files.values())


class Builder:
    def __init__(self, depot, name, language, regions, player, *, encoding="gzip"):
        self.depot, self.name, self.language = depot, name, language
        self.old, self.entries = catalog.load(depot, name)
        self.regions, self.player, self.encoding = regions, player, encoding
        self.topics, self.replace = [], set()
        self.run = Run(depot, {}, ["site:" + name], {}, {})
        self.reader = None
        self.live_count = 0

    def add(self, entries, source, kind, tid, region, facts, *, languages=None, options=None, audio=True):
        manifests = documents(self.depot, entries)
        if languages and self.language in languages:
            manifests[source]["language"] = self.language
        variants, reads = [], None
        if kind == "live":
            variants = sorted(
                p for p, m in manifests.items() if m["product"] in {"live.noteskin", "live.noteeffect", "live.sounds"}
            )
            if self.reader is None:
                self.reader = Reader(self.depot, self.player)
            sample = int.from_bytes(hashlib.sha256(source.encode()).digest()[:4], "big") % 10 == 0
            reads = self.reader.read(
                entries,
                source,
                products=variants,
                variants=options.read_variants() if options else (),
                check=self.live_count == 0 or sample,
            )
            self.live_count += 1
            combine(self.run, self.reader.run)
        snapshot = Snapshot(
            self.depot,
            manifests,
            languages=languages,
            encoding=self.encoding,
            variants=variants,
            live_options=options.manifest_options() if options else None,
            read_files=reads,
            audio=audio,
        )
        target = snapshot.product(source)
        combine(self.run, snapshot.run)
        self.entries.update(snapshot.entries)
        self.topics.append({"kind": kind, "id": str(tid), "region": region, "manifest": target, "entry": facts})
        return snapshot

    def finish(self, directory):
        doc = catalog.merge(self.old, self.name, self.language, self.regions, self.topics, self.replace)
        entries, run = catalog.assemble(self.depot, doc, self.entries)
        combine(self.run, run)
        files, stats = pages.gather(self.player, stories=any(t["kind"] == "story" for t in doc["topics"]))
        for path, body in files.items():
            entries[path] = {
                "path": path,
                "sha256": self.depot.put(body),
                "size": len(body),
                "id": contract.artifact_id(contract.task_id("site.page", path), "file"),
            }
        placed = publish.materialize(directory, list(entries.values()), self.depot, params={"name": self.name})
        sha = self.depot.put(contract.encode(placed["manifest"]))
        if sha != placed["sha256"]:
            raise ValueError("published layout digest differs")
        manifest = self.run.write({"site": sha})
        return {
            **stats,
            "name": self.name,
            "run": manifest["id"],
            "tasks": manifest["summary"],
            "topics": len(doc["topics"]),
            "write": placed["write"],
            "keep": placed["keep"],
            "removed": placed["remove"],
            "files": len(entries),
            "readSets": {
                "checked": self.reader.checked,
                "mismatches": self.reader.mismatches,
                "planErrors": self.reader.plan_errors,
            }
            if self.reader
            else None,
        }
