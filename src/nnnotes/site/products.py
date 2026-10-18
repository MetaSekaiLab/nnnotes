"""Immutable product snapshots assembled through depot tasks.

Input manifests use product-layout paths. A snapshot rewrites declared references to the content-addressed
manifests it produces and collects the stored asset placements. Construction writes only to the depot; callers
choose the public aliases and publish the complete layout after all requested products have succeeded.
"""

from __future__ import annotations

import json
import re

from ..pipeline import contract, siteassets, siteproducts
from ..pipeline.contract import Input
from ..pipeline.orchestrate import Run
from ..pipeline.stages import Env, describe, execute

_ASSET = re.compile(r"assets/([0-9a-f]{64})\.([a-z0-9]+)")
REFERENCE_FIELDS = {
    "live": {"live.json": [["parts"]]},
    "story": {"story.json": [["parts"]]},
    "story.host": {"host.json": [["home", "parts"]]},
}
LANGUAGE_FIELDS = {
    "live": [["parts", "ui"]],
    "story": [["parts", "ui"], ["parts", "fonts"], ["parts", "simpleui"]],
}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


class Snapshot:
    """One coherent group of products; equal inputs share tasks across snapshots and regions."""

    def __init__(self, depot, manifests, *, languages=None, encoding="gzip", split=siteassets.SPLIT_MIN_BYTES):
        self.depot, self.sources = depot, manifests
        self.languages = None if languages is None else sorted(set(languages))
        self.asset_params = siteassets.AssetStage().normalize({"encoding": encoding, "split": split})
        self.run = Run(depot, {}, [], {siteassets.ASSET: self.asset_params}, {})
        self.paths, self.documents, self.records, self.entries = {}, {}, {}, {}
        self._visiting = set()

    def _input(self, role, body):
        return Input(role, self.depot.put(body), len(body), None, ({"kind": "depot"},))

    def _task(self, stage, subject, inputs, params=None):
        env = Env(self.depot, {stage.name: {subject: inputs}})
        if stage.name == siteassets.ASSET:
            env = Env(self.depot, {"site_assets": {subject: inputs[0]}})
        task = describe(stage, subject, params, env)
        if task.id in self.run.tasks:
            if self.run.tasks[task.id]["key"] != task.key:
                raise ValueError(f"site task {task.id}: conflicting inputs")
            return self.depot.committed(task.key, task.id)
        outcome = execute(task, self.depot, {stage.name: stage})
        self.run.stages[stage.name] = stage.version
        self.run.params[stage.name] = task.params
        self.run.tasks[task.id] = {"id": task.id, "key": task.key, "status": outcome.status}
        if outcome.cost:
            self.run.log.append({"task": task.id, **outcome.cost})
        return self.depot.committed(task.key, task.id)

    def _document(self, stage, source, spec):
        inputs = [self._input("source", source), self._input("spec", contract.encode(spec))]
        key = contract.digest([[i.role, i.sha256] for i in inputs])
        result = self._task(stage, key, inputs)
        return result["artifacts"][0]

    def _source(self, entry):
        match = _ASSET.fullmatch(entry.get("asset", ""))
        if not match or "stored" in entry or "parts" in entry:
            raise ValueError("site snapshot requires a decoded product-layout file entry")
        return Input("source", match[1], entry["size"], None, ({"kind": "depot"},)), match[2]

    def _asset(self, inp, ext):
        result = self._task(siteassets.AssetStage(), siteassets.subject(inp, ext), [inp], self.asset_params)
        for placed in siteassets.placements(result):
            self.entries[placed["path"]] = placed
        return siteassets.entry(result, self.depot)

    def _languages(self, product, doc):
        fields = [p for p in LANGUAGE_FIELDS.get(product, []) if siteproducts.at(doc, p) is not None]
        if not fields:
            return [], []
        if self.languages is not None:
            wanted = self.languages
        else:
            groups = []
            for path in fields:
                group = siteproducts.at(doc, path)
                if not isinstance(group, dict):
                    raise ValueError("product language group must be an object")
                groups.append(group)
            wanted = sorted(set.intersection(*[{k for k, v in group.items() if v in self.sources} for group in groups]))
        if not wanted:
            raise ValueError(f"{product}: no available language products")
        return fields, wanted

    def product(self, path):
        if path in self.paths:
            return self.paths[path]
        if path in self._visiting:
            raise ValueError(f"cyclic product references: {path}")
        if path not in self.sources:
            raise ValueError(f"missing product manifest: {path}")
        self._visiting.add(path)
        try:
            source = self.sources[path]
            if source.get("schema") != contract.PRODUCT:
                raise ValueError(f"{path}: expected {contract.PRODUCT}")
            files, objects, language = {}, {}, None
            for logical, entry in sorted(source["files"].items()):
                inp, ext = self._source(entry)
                paths = REFERENCE_FIELDS.get(source["product"], {}).get(logical, [])
                if paths:
                    body = self.depot.input_bytes(inp)
                    doc = json.loads(body)
                    language_paths, wanted = self._languages(source["product"], doc)
                    selected = siteproducts.select_languages(doc, language_paths, wanted)
                    dependencies = sorted({v for p in paths for v in _strings(siteproducts.at(selected, p))})
                    routes = {p: self.product(p) for p in dependencies}
                    art = self._document(
                        siteproducts.ReferencesStage(),
                        body,
                        {
                            "paths": paths,
                            "routes": routes,
                            "languagePaths": language_paths,
                            "languages": wanted,
                        },
                    )
                    c = art["content"]
                    inp = Input("source", c["sha256"], c["size"], None, ({"kind": "depot"},))
                    if wanted:
                        language = source.get("language") if source.get("language") in wanted else wanted[0]
                files[logical] = self._asset(inp, ext)
            for oid, obj in sorted(source["objects"].items()):
                objects[oid] = {role: self._asset(*self._source(entry)) for role, entry in sorted(obj["files"].items())}
            art = self._document(
                siteproducts.ManifestStage(),
                contract.encode(source),
                {
                    "files": files,
                    "objects": objects,
                    "language": language,
                },
            )
            c = art["content"]
            target = f"products/{c['sha256']}.json"
            self.paths[path] = target
            self.documents[path] = json.loads(self.depot.read(c["sha256"]))
            self.records[path] = art
            self.entries[target] = {"path": target, "sha256": c["sha256"], "size": c["size"], "id": art["id"]}
            return target
        finally:
            self._visiting.remove(path)

    def alias(self, source, path):
        """Add a public entry point after its immutable product snapshot has been constructed."""
        target = self.product(source)
        segments = path.split("/")
        if (
            len(segments) < 2
            or any(not s or s in {".", ".."} for s in segments)
            or re.search(r"[\\:?#\x00]", path)
            or not path.endswith(".json")
        ):
            raise ValueError("site product alias requires a relative manifest path")
        if len(segments) == 2:
            self.entries[path] = {**self.entries[target], "path": path}
        else:
            doc = self.documents[source]
            art = self._document(
                siteproducts.ManifestStage(),
                contract.encode(doc),
                {
                    "files": doc["files"],
                    "objects": {oid: obj["files"] for oid, obj in doc["objects"].items()},
                    "root": "../" * (len(segments) - 1),
                },
            )
            c = art["content"]
            self.entries[path] = {"path": path, "sha256": c["sha256"], "size": c["size"], "id": art["id"]}
        return path
