"""The ADV stages: an episode's script and shards as `nnnotes export episode` writes them.

adv.episode, one task per MasterAdv id a command asks for (the fact "requests"), with master data (the fact "master"):
    inputs     "master:<Table>": MasterAdv, MasterAdvChat and MasterText (the tables adv.extract reads);
               "bundle:<stable name>": the bundles of the closures of the episode's keys the catalog has (the
               episode asset Adv/Episode/<asset>/<asset>, its -Text, -Sound, -SoundCueSheet and -Video shards, and
               the ADV player settings, which name the default transition); "keys": the keys of the catalog that
               start with a prefix of the episode's resources (key_prefixes: whether the catalog has a resource is
               part of episode.json)
    context    {"closures": {key: [stable names of its bundles in load order]}}
    artifacts  "adv.episode:<id>#episode.json": the episode as `nnnotes export episode` writes it (facts: commands,
               resources)

The task runs adv.extract through TaskCatalog, its one atom ("adv.extract"); this module imports the extractor (and
UnityPy) only when it plans or runs a task.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import contract
from .atoms import impl_id
from .contract import Cost, Input
from .modelstages import LIBRARIES, ClosureStage, TaskCatalog, check_atoms, file_artifact, requested, write_master
from .stages import Output

EPISODE = "adv.episode"
EPISODE_TABLES = ("MasterAdv", "MasterAdvChat", "MasterText")
SHARDS = ("", "-Text", "-Sound", "-SoundCueSheet", "-Video")


def key_prefixes() -> list[str]:
    """The prefixes of the addresses adv.closure asks the catalog about (adv.RESOURCE_PREFIX and the resources
    derived from other fields), sorted."""
    from ..content import adv

    talk = adv.TALK_WINDOW_PREFIX
    return sorted(
        {
            *(prefix for _kind, prefix in adv.RESOURCE_PREFIX.values()),
            adv.TRANSITION_PREFIX,
            talk,
            adv.EMBEDDED_PREFIX + talk,
            adv.CHAT_WINDOW_PREFIX,
            adv.CHAT_ICON_PREFIX,
            adv.VIDEO_ADDRESS.format(""),
        }
    )


def episode_keys(asset: str) -> list[str]:
    """The keys adv.extract reads for the episode asset `asset` (its script and shards, the player settings)."""
    from ..content import adv

    base = f"Adv/Episode/{asset}/{asset}"
    return [base + s for s in SHARDS] + [adv.PLAYER_SETTINGS_KEY]


def _rows(path: Path) -> list[dict]:
    return json.loads(path.read_bytes())["_allData"]


def _stored(store, role: str, data: bytes) -> Input:
    return Input(role, store.put(data), len(data), None, ({"kind": "store"},))


class EpisodeStage(ClosureStage):
    """adv.episode: a MasterAdv id -> episode.json of `nnnotes export episode` (module documentation). Subjects:
    the ids a command asks for (the fact "requests"), when the fact "master" names the master data."""

    name = EPISODE
    version = 1
    ATOMS = {"adv.extract": impl_id(LIBRARIES, 1)}

    def subjects(self, env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        return requested(env, self.name)

    def row(self, subject: str, env) -> dict:
        p = Path(env.fact("master")) / f"{EPISODE_TABLES[0]}.json"
        for r in _rows(p):
            if r.get("_id") == int(subject):
                return r
        raise ValueError(f"{self.name}: no MasterAdv row {subject}")

    def keys(self, subject: str, env) -> list[str]:
        """The keys of the episode the catalog has."""
        c = self.closures(env)
        return [k for k in episode_keys(self.row(subject, env)["_advEpisodeAsset"]) if k in c.by_key]

    def inputs(self, subject: str, env) -> list[Input]:
        what = f"{self.name} {subject}"
        bundles, _ = self.closure_context(self.keys(subject, env), env, what)
        prefixes = tuple(key_prefixes())
        known = [k for k in self.closures(env).keys() if k.startswith(prefixes)]
        keys = _stored(env.store, "keys", contract.encode({"prefixes": list(prefixes), "keys": known}))
        return self.master_inputs(EPISODE_TABLES, env, what) + bundles + [keys]

    def context(self, subject: str, env) -> dict:
        return self.closure_context(self.keys(subject, env), env, f"{self.name} {subject}")[1]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.3 + size * 1e-7, (320 << 20) + 8 * size)

    def run(self, task, store) -> Output:
        check_atoms(task, self.ATOMS)
        from ..content import adv
        from ..core.jsonio import write_json

        known = contract.loads(store.input_bytes(task.input("keys")))["keys"]
        cat = TaskCatalog.of_closures(task, store, known=known)
        with tempfile.TemporaryDirectory(prefix="nnnotes-adv-") as d:
            mdir = write_master(task, store, Path(d, "master"))
            doc = adv.to_json(adv.extract(cat, mdir, int(task.subject)))  # pyright: ignore[reportArgumentType]
            path = write_json(Path(d, "episode.json"), doc)
            facts = {"commands": doc["commandCount"], "resources": len(doc["resources"])}
            arts = [file_artifact(task, store, "episode.json", path.read_bytes(), "adv.episode", "json", facts)]
        return Output(arts, [])
