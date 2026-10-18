"""Site catalog and listing indexes from explicit region-topic records."""

from __future__ import annotations

from collections import defaultdict

from . import contract
from .stages import Output, Stage

INDEX = "site.index"
CATALOG = "nnnotes.site/1"
KINDS = {"live": ("charts", 2), "model": ("models", 2), "story": ("stories", "ournotes.stories/1")}


class IndexStage(Stage):
    name, version = INDEX, 2

    def subjects(self, env):
        return sorted(env.facts.get(self.name, {}))

    def inputs(self, subject, env):
        return env.fact(self.name)[subject]

    def run(self, task, depot):
        catalog = contract.loads(depot.input_bytes(task.input("catalog")))
        if catalog.get("format") != CATALOG:
            raise ValueError("site index requires a site catalog")
        regions = {r["id"]: i for i, r in enumerate(catalog["regions"])}
        topics = sorted(catalog["topics"], key=lambda t: (t["kind"], t["id"], regions[t["region"]]))
        groups = defaultdict(dict)
        for topic in topics:
            sig = contract.digest({"manifest": topic["manifest"], "entry": topic["entry"]})
            group = groups[(topic["kind"], topic["id"])].setdefault(sig, {"topic": topic, "regions": []})
            group["regions"].append(topic["region"])
        rows = {kind: [] for kind in KINDS}
        aliases = {}
        for (kind, tid), versions in sorted(groups.items()):
            folder, _ = KINDS[kind]
            if kind == "model" and len(versions) > 1:
                raise ValueError(f"model {tid}: differing contents require separate sites")
            for index, group in enumerate(versions.values()):
                topic, rs = group["topic"], group["regions"]
                alias = f"{folder}/{'' if index == 0 else rs[0] + '/'}{tid}.json"
                aliases[alias] = topic["manifest"]
                rows[kind].append({**topic["entry"], "id": tid, "manifest": alias, "regions": rs})
        artifacts = []

        def emit(path, doc):
            artifacts.append(
                contract.artifact(
                    contract.artifact_id(task.id, path),
                    depot.add(contract.encode(doc), "json"),
                    contract.provenance(task),
                    {"kind": INDEX},
                )
            )

        for kind, (folder, fmt) in KINDS.items():
            listing = rows[kind]
            languages = sorted(
                {
                    catalog["language"],
                    *(
                        code
                        for entry in listing
                        for code in [*entry.get("languages", []), *entry.get("titles", {}), *entry.get("names", {})]
                    ),
                }
            )
            emit(
                folder + ".json",
                {
                    "format": fmt,
                    "language": catalog["language"],
                    "languages": languages,
                    "regions": catalog["regions"],
                    folder: listing,
                },
            )
        emit("site.json", {**catalog, "topics": topics})
        emit("aliases.json", aliases)
        return Output(artifacts)
