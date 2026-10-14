"""The unity.census and unity.export results the stages that read unity.export results plan from: a bundle's census
(what pipeline.units reads for the unit table) and the result of one unit's task. Synthetic: one serialized file per
bundle, every object its own unit."""

from __future__ import annotations

from collections import Counter

from nnnotes.pipeline import census, contract, link
from nnnotes.pipeline.contract import Input, Task

CENSUS = "unity.census"
EXPORT = "unity.export"


def census_doc(file: str, objects) -> dict:
    """The census of a bundle of one serialized file `file` holding `objects` [(pathId, class)]."""
    objs = [
        {"pathId": pid, "classId": 0, "class": cls, "byteSize": 10, "typeHash": None} for pid, cls in sorted(objects)
    ]
    return {
        "schema": contract.CENSUS,
        "files": [
            {
                "name": file,
                "unityVersion": "6000.0.0f1",
                "platform": 13,
                "typetree": True,
                "externals": [],
                "objects": objs,
            }
        ],
        "resources": [],
        "assetBundles": [],
        "scripts": [],
        "classes": dict(sorted(Counter(o["class"] for o in objs).items())),
        "objects": len(objs),
        "errors": [],
    }


def add_census(env, bundle: str, file: str, objects) -> None:
    """Commits the census of `bundle` (census_doc) and adds the bundle to the facts "bundles" and "selected"."""
    depot = env.depot
    data = f"bundle {bundle}".encode()
    inp = Input("bundle", depot.put(data), len(data), bundle, ({"kind": "depot"},))
    t = Task(CENSUS, 3, bundle, {}, {}, (inp,))
    doc = census_doc(file, objects)
    rec = contract.artifact(
        contract.artifact_id(t.id, "census"),
        depot.add(contract.encode(doc), "json"),
        contract.provenance(t),
        {"kind": CENSUS, "format": "json", "facts": census.facts(doc)},
    )
    res = contract.result(t, [rec], [])
    depot.commit(res)
    env.done(t.id, res["key"])
    env.facts["bundles"] = {**env.facts.get("bundles", {}), bundle: inp}
    env.facts["selected"] = sorted({*env.facts.get("selected", ()), bundle})


def add_unit(env, bundle: str, file: str, pid: int, artifacts, cls: str = "MonoBehaviour") -> str:
    """Commits the unity.export result of the unit of object `file:pid` of `bundle` (a bundle of add_census):
    artifacts [(role, bytes, extension, semantics)] of that object, one exported item; -> the task id."""
    depot = env.depot
    oid = contract.object_id(file, pid)
    subject = link.path_address(bundle, link.file_tags([file]), file, pid)
    t = Task(EXPORT, 2, subject, {}, {"unit": oid, "bundle": bundle}, ())
    recs = [
        contract.artifact(contract.artifact_id(oid, role), depot.add(data, ext), contract.provenance(t), semantics)
        for role, data, ext, semantics in artifacts
    ]
    res = contract.result(t, recs, [contract.item(oid, "exported", artifacts=[r["id"] for r in recs], cls=cls)])
    depot.commit(res)
    env.done(t.id, res["key"])
    return t.id
