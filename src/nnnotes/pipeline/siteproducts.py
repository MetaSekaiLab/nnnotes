"""Task-backed product documents with immutable references for a static site."""

from __future__ import annotations

import copy
import json

from ..core import jsonio
from . import contract
from .stages import Output, Stage

REFERENCES, MANIFEST = "site.references", "site.manifest"


def at(document, path):
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def select_languages(document, paths, languages):
    document = copy.deepcopy(document)
    for path in paths:
        values = at(document, path)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError(f"{'.'.join(path)}: language group must be an object")
        missing = set(languages) - set(values)
        if missing:
            raise ValueError(f"{'.'.join(path)}: missing languages {', '.join(sorted(missing))}")
        parent = at(document, path[:-1])
        if not isinstance(parent, dict):
            raise ValueError("language group parent must be an object")
        parent[path[-1]] = {code: values[code] for code in languages}
    return document


class _DocumentStage(Stage):
    def subjects(self, env):
        return sorted(env.facts.get(self.name, {}))

    def inputs(self, subject, env):
        return env.fact(self.name)[subject]

    def run(self, task, depot):
        source = json.loads(depot.input_bytes(task.input("source")))
        spec = json.loads(depot.input_bytes(task.input("spec")))
        doc = self.transform(source, spec)
        content = depot.add(
            jsonio.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(), "json"
        )
        art = contract.artifact(
            contract.artifact_id(task.id, "document.json"), content, contract.provenance(task), {"kind": self.name}
        )
        return Output([art])

    def transform(self, source, spec):
        raise NotImplementedError


class ReferencesStage(_DocumentStage):
    """Rewrite only declared product-reference fields and language groups."""

    name, version = REFERENCES, 1

    def transform(self, source, spec):
        doc = select_languages(source, spec["languagePaths"], spec["languages"])

        def rewrite(value):
            if isinstance(value, str):
                if value not in spec["routes"]:
                    raise ValueError(f"product reference has no manifest: {value}")
                return spec["routes"][value]
            if isinstance(value, dict):
                return {key: rewrite(v) for key, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            if value is not None:
                raise ValueError("product reference must be a path, group or null")
            return None

        for path in spec["paths"]:
            value = at(doc, path)
            if value is not None:
                parent = at(doc, path[:-1])
                if not isinstance(parent, dict):
                    raise ValueError("product reference parent must be an object")
                parent[path[-1]] = rewrite(value)
        return doc


class ManifestStage(_DocumentStage):
    """Apply transformed file entries while preserving product and object identity."""

    name, version = MANIFEST, 1

    def transform(self, source, spec):
        if source.get("schema") != contract.PRODUCT:
            raise ValueError("site manifest requires a product")
        doc = copy.deepcopy(source)
        if set(doc["files"]) != set(spec["files"]) or set(doc["objects"]) != set(spec["objects"]):
            raise ValueError("site manifest file or object identity differs")
        doc["files"] = spec["files"]
        for oid, entry in doc["objects"].items():
            if set(entry["files"]) != set(spec["objects"][oid]):
                raise ValueError(f"site manifest object {oid}: file roles differ")
            entry["files"] = spec["objects"][oid]
        doc["root"] = spec.get("root", "../")
        if spec.get("language"):
            doc["language"] = spec["language"]
        return doc
