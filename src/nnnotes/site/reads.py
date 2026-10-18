"""Player read-set task selection, full-run checks and option-variant unions."""

from __future__ import annotations

import json

from ..pipeline import contract, readsets
from ..pipeline.contract import Input
from ..pipeline.orchestrate import Run
from ..pipeline.stages import Env, describe, execute


class Reader:
    def __init__(self, depot, player):
        self.depot, self.player = depot, readsets.player_inputs(depot, player)
        self.run = Run(depot, {}, [], {}, {})
        self.mode = "plan"
        self.checked = 0
        self.mismatches = []
        self.plan_errors = []

    def _read(self, entries, entry, mode, settings, products=()):
        spec = contract.encode(readsets.request(entry, mode=mode, settings=settings, variants=products))
        inputs = [
            *self.player,
            *readsets.data_inputs(entries),
            Input("request", self.depot.put(spec), len(spec), None, ({"kind": "depot"},)),
        ]
        subject = contract.digest([[i.role, i.sha256] for i in sorted(inputs, key=lambda i: i.role)])
        stage = readsets.ReadSetStage()
        task = describe(stage, subject, None, Env(self.depot, {stage.name: {subject: inputs}}))
        if task.id not in self.run.tasks:
            done = execute(task, self.depot, {stage.name: stage})
            self.run.tasks[task.id] = {"id": task.id, "key": task.key, "status": done.status}
            self.run.stages[stage.name] = stage.version
            self.run.params[stage.name] = task.params
            if done.cost:
                self.run.log.append({"task": task.id, **done.cost})
        result = self.depot.committed(task.key, task.id)
        art = next(a for a in result["artifacts"] if a["id"].endswith("#readset.json"))
        return json.loads(self.depot.read(art["content"]["sha256"]))

    def _preferred(self, entries, entry, settings, products=()):
        if self.mode == "plan":
            try:
                return self._read(entries, entry, "plan", settings, products)
            except readsets.ReadSetError as error:
                self.plan_errors.append({"entry": entry, "settings": settings, "error": str(error)})
        return self._read(entries, entry, "full", settings, products)

    def read(self, entries, entry, *, variants=(), products=(), check=False):
        """Files read under default and requested settings; optional full-run comparison of the default plan."""
        base = self._preferred(entries, entry, {}, products)
        if check and base["mode"] == "plan":
            full = self._read(entries, entry, "full", {}, products)
            self.checked += 1
            if base["files"] != full["files"]:
                self.mismatches.append(
                    {
                        "entry": entry,
                        "missing": sorted(set(full["files"]) - set(base["files"])),
                        "extra": sorted(set(base["files"]) - set(full["files"])),
                    }
                )
                self.mode, base = "full", full
        files = set(base["files"])
        for settings in variants:
            files.update(self._preferred(entries, entry, settings, products)["files"])
        return sorted(files)
