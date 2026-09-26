"""Golden records: a stage whose output changes must change its version.

tests/golden/<stage>.json records, for a stage version, the atoms its fixtures use, the versions of the libraries
that shape its bytes, and per fixture the task key and the sha256 of the canonical result:

    {"stage": "unity.export", "version": 1, "atoms": {...}, "libraries": {"Pillow": "12.3.0", ...},
     "provider": "<test module>:<function>", "fixtures": {"<name>": {"key": "...", "result": "..."}}}

The provider (a function of a test module) returns {"stage": Stage, "fixtures": {name: callable(store) -> Task},
"libraries": [distribution names]}; each fixture builds its synthetic inputs into the store. Every atom the stage
declares must be used by a fixture. A record made with other library versions is skipped (another encoder version
may write other bytes for the same content) unless GOLDENS_STRICT=1, as in the pinned CI job, where it fails.
GOLDENS_UPDATE=1 rewrites the records from the current code (after a version bump).
"""
import importlib
import os
from pathlib import Path

import pytest

from nnnotes import contract
from nnnotes.stages import Output, golden, golden_problems, library_versions
from nnnotes.store import Store
from test_stages import Src

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def provided(spec: str) -> dict:
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)()


def record(spec: str, store: Store) -> dict:
    p = provided(spec)
    doc = golden(p["stage"], p["fixtures"], store, p.get("libraries", ()))
    return dict(doc, provider=spec)


@pytest.mark.parametrize("path", sorted(GOLDEN_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_golden_record(path, tmp_path):
    expected = contract.loads(path.read_bytes())
    p = provided(expected["provider"])
    if os.environ.get("GOLDENS_UPDATE") == "1":
        path.write_bytes(contract.encode(record(expected["provider"], Store(tmp_path / "store"))))
        return
    installed = library_versions(expected["libraries"])
    if installed != expected["libraries"]:
        if os.environ.get("GOLDENS_STRICT") != "1":
            pytest.skip(f"recorded with {expected['libraries']}, installed {installed}")
        pytest.fail(f"the pinned job has {installed}, the record needs {expected['libraries']}")
    actual = record(expected["provider"], Store(tmp_path / "store"))
    assert golden_problems(expected, actual, p["stage"]) == []


# ---------------------------------------------------------------- the machinery itself (toy stage)
def text_fixture(text: str):
    def build(store):
        from nnnotes.contract import Input, Task
        data = text.encode()
        sha = store.put(data)
        return Task("toy.src", 1, text[:4], {"suffix": ""}, dict(Src.ATOMS),
                    (Input("file", sha, len(data), None, ({"kind": "store"},)),))
    return build


def toy_provider():
    return {"stage": Src(), "fixtures": {"plain": text_fixture("one\ntwo"), "items": text_fixture("x\n\nFAIL\n?q")},
            "libraries": ["pytest"]}


def test_a_regenerated_record_matches(tmp_path):
    a = record("test_goldens:toy_provider", Store(tmp_path / "a"))
    b = record("test_goldens:toy_provider", Store(tmp_path / "b"))
    assert a == b and golden_problems(a, b, Src()) == []
    assert a["libraries"] == library_versions(["pytest"]) and set(a["fixtures"]) == {"plain", "items"}
    assert a["atoms"] == Src.ATOMS and a["version"] == 1


class Changed(Src):
    def run(self, task, store):
        out = super().run(task, store)
        return Output(out.artifacts, [dict(i, reason=contract.reason("new", "x")) if i["status"] == "unsupported"
                                      else i for i in out.items])


def test_an_output_change_without_a_version_bump_fails(tmp_path):
    expected = record("test_goldens:toy_provider", Store(tmp_path / "a"))
    fixtures = toy_provider()["fixtures"]
    actual = golden(Changed(), fixtures, Store(tmp_path / "b"), ["pytest"])
    assert golden_problems(expected, actual) == ["toy.src: fixture items: output changed without a version bump"]
    bumped = Changed()
    bumped.version = 2
    actual = golden(bumped, {n: _versioned(f, 2) for n, f in fixtures.items()}, Store(tmp_path / "c"), ["pytest"])
    problems = golden_problems(expected, actual)
    assert problems[0] == "toy.src: version 2, golden record has 1: regenerate the golden record"
    assert "toy.src: fixture plain: task key changed" in problems


def _versioned(build, version):
    def b(store):
        t = build(store)
        return contract.Task(t.stage, version, t.subject, t.params, t.atoms, t.inputs, t.context)
    return b


def test_every_declared_atom_needs_a_fixture(tmp_path):
    class More(Src):
        ATOMS = {**Src.ATOMS, "mesh.glb": "toy-glb/1"}

    doc = record("test_goldens:toy_provider", Store(tmp_path / "a"))
    assert golden_problems(doc, doc, More()) == ["toy.src: no fixture uses mesh.glb"]
    missing = dict(doc, fixtures={"plain": doc["fixtures"]["plain"]})
    assert golden_problems(missing, doc) == ["toy.src: fixture items has no golden entry"]


def test_records_are_canonical_documents(tmp_path):
    doc = record("test_goldens:toy_provider", Store(tmp_path / "a"))
    path = tmp_path / "toy.src.json"
    path.write_bytes(contract.encode(doc))
    assert contract.loads(path.read_bytes()) == doc


def test_the_export_stages_have_golden_records():
    assert {"unity.export", "sprite.crop"} <= {p.stem for p in GOLDEN_DIR.glob("*.json")}
