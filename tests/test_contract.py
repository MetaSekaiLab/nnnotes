"""The contract: canonical JSON, keys, identities, the documents and their JSON Schemas, and their documentation."""
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from nnnotes import contract, layout, plan as plan_mod
from nnnotes.contract import Cost, IncompatibleTask, Input, Task
from nnnotes.orchestrate import FAILURE_CODES, Orchestrator
from nnnotes.store import Store
from test_stages import PIPELINE, toy_files, toy_stages

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs" / "schema"
SHA = "0" * 64


# ---------------------------------------------------------------- canonical JSON
def test_canonical_documents():
    doc = {"b": [1, 2.5, float("inf")], "a": {"é": -math.inf, "z": None}, "n": float("nan")}
    text = contract.dumps(doc)
    assert text == ('{\n "a": {\n  "z": null,\n  "é": -1e999\n },\n "b": [\n  1,\n  2.5,\n  1e999\n ],\n'
                    ' "n": {\n  "$float": "nan"\n }\n}\n')
    assert contract.encode(doc) == text.encode("utf-8")
    back = contract.loads(text)
    assert math.isnan(back["n"]) and back["b"][2] == math.inf and back["a"]["é"] == -math.inf
    assert contract.dumps(back) == text
    assert doc["n"] != doc["n"]                                     # the caller's document is not modified


def test_object_documents_keep_their_order():
    assert contract.dumps({"m_Name": "x", "m_Enabled": 1}, sort_keys=False) == \
        '{\n "m_Name": "x",\n "m_Enabled": 1\n}\n'


def test_key_text_is_compact_ascii_and_sorted():
    assert contract.key_text({"b": 1, "a": ["é", float("nan"), float("inf")]}) == \
        '{"a":["\\u00e9",{"$float":"nan"},1e999],"b":1}'
    assert contract.digest({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()
    assert contract.digest((1, 2)) == contract.digest([1, 2])
    with pytest.raises(ValueError, match="collides"):
        contract.key_text({"x": "\x00nnnotes:+inf"})                # the writer's own errors still surface


# ---------------------------------------------------------------- identities
def test_identities():
    assert contract.object_id("CAB-2f1e", -4153) == "CAB-2f1e:-4153"
    assert contract.parse_object_id("unity default resources:10") == ("unity default resources", 10)
    assert contract.artifact_id("CAB-1:5", "blob:m_Data#x") == "CAB-1:5#blob:m_Data#x"
    assert contract.parse_artifact_id("CAB-1:5#blob:m_Data#x") == ("CAB-1:5", "blob:m_Data#x")
    assert contract.parse_artifact_id("cri.audio:" + SHA + "#bgm.flac") == ("cri.audio:" + SHA, "bgm.flac")
    assert contract.parse_task_id("live2d.model:Character/Live2D/a:b") == ("live2d.model", "Character/Live2D/a:b")
    assert contract.parse_task_id("view.cards:all", "view.cards") == ("view.cards", "all")
    for bad in ("nohash", "#role", "owner#"):
        with pytest.raises(ValueError):
            contract.parse_artifact_id(bad)
    with pytest.raises(ValueError):
        contract.artifact_id("a#b", "x")
    with pytest.raises(ValueError):
        contract.parse_object_id("no-path-id")


def test_stable_bundle_names_and_addresses():
    h = "0123456789abcdef" * 2
    assert contract.stable_bundle_name(f"membercard_assets_x_{h}.bundle") == "membercard_assets_x"
    assert contract.stable_bundle_name("plain.bundle") == "plain" and contract.stable_bundle_name("raw.acb") == "raw.acb"
    names, collisions = contract.stable_bundle_names([f"a_{h}.bundle", "a.bundle", f"b_{h}.bundle"])
    assert names == {f"a_{h}.bundle": f"a_{h}.bundle", "a.bundle": "a.bundle", f"b_{h}.bundle": "b"}
    assert collisions == [["a.bundle", f"a_{h}.bundle"]]
    assert contract.stable_address("b", "assets/x.png") == "b/assets/x.png"
    assert contract.stable_address("b", "assets/x.png", 5, name="sq") == "b/assets/x.png[sq]"
    assert contract.stable_address("b", None, -7) == "b:-7"
    with pytest.raises(ValueError):
        contract.stable_address("b")


# ---------------------------------------------------------------- tasks and keys
def a_task(**kw) -> Task:
    base = dict(stage="unity.export", version=1, subject="b", params={"png": {"level": 6}},
                atoms={"png.encode": "pillow/1"},
                inputs=(Input("bundle", SHA, 10, "b_x.bundle", ({"kind": "cache", "path": "bundles/b_x.bundle"},)),),
                context={"scripts": {"CAB-1:1": "A|B|C"}}, cost=Cost(1.5, 100))
    base.update(kw)
    return Task(**base)


def test_the_key_covers_exactly_its_parts():
    t = a_task()
    same = a_task(inputs=(Input("bundle", SHA, 10, "renamed.bundle", ()),), cost=Cost(9, 9))
    assert same.key == t.key                                        # names, locators and cost are not in the key
    for kw in ({"version": 2}, {"subject": "c"}, {"params": {"png": {"level": 9}}}, {"atoms": {"png.encode": "x/1"}},
               {"inputs": (Input("bundle", "1" * 64, 10),)}, {"inputs": (Input("other", SHA, 10),)},
               {"context": {}}):
        assert a_task(**kw).key != t.key, kw
    two = a_task(inputs=(Input("b", "1" * 64, 1), Input("a", SHA, 1)))
    assert [i.role for i in two.inputs] == ["a", "b"]
    assert two.key == a_task(inputs=(Input("a", SHA, 1), Input("b", "1" * 64, 1))).key
    assert t.key == contract.digest(t.key_parts())


def test_task_descriptions_round_trip_and_are_verified():
    t = a_task()
    doc = contract.loads(contract.dumps(t.to_json()))
    assert Task.from_json(doc) == t and Task.from_json(doc).key == t.key
    with pytest.raises(IncompatibleTask, match="not the key of its parts"):
        Task.from_json(dict(doc, params={"png": {"level": 1}}))
    with pytest.raises(IncompatibleTask, match="not a nnnotes.task/1"):
        Task.from_json(dict(doc, schema="nnnotes.task/2"))
    with pytest.raises(IncompatibleTask, match="malformed"):
        Task.from_json(dict(doc, id="other.stage:b"))
    with pytest.raises(ValueError, match="duplicate input roles"):
        a_task(inputs=(Input("a", SHA, 1), Input("a", "1" * 64, 1)))
    with pytest.raises(ValueError, match="not a sha256"):
        Input("a", "xyz", 1)
    with pytest.raises(ValueError, match="unknown locator"):
        Input("a", SHA, 1, None, ({"kind": "http"},))


# ---------------------------------------------------------------- records and results
def test_items_follow_their_status_rules():
    r = contract.reason("empty.texture", "0x0")
    assert contract.item("F:1", "exported", artifacts=["F:1#image"], cls="Texture2D") == \
        {"object": "F:1", "status": "exported", "class": "Texture2D", "artifacts": ["F:1#image"]}
    assert contract.item("F:2", "contained", within="F:1#prefab")["in"] == "F:1#prefab"
    assert contract.item("F:3", "generic", artifacts=["F:3#json"], why=r)["reason"] == r
    assert contract.item("F:4", "unsupported", why=r)["status"] == "unsupported"
    for bad in (dict(status="exported"), dict(status="exported", artifacts=["a#b"], why=r),
                dict(status="contained"), dict(status="generic", artifacts=["a#b"]), dict(status="failed"),
                dict(status="failed", why=r, artifacts=["a#b"]), dict(status="nope", why=r)):
        with pytest.raises(ValueError):
            contract.item("F:9", **bad)


def test_results_are_sorted_and_checked():
    t = a_task()
    c = contract.content(b"x", "PNG")
    assert c == {"sha256": contract.sha256(b"x"), "size": 1, "mediaType": "image/png", "ext": "png"}
    a2 = contract.artifact("F:2#image", c, contract.provenance(t, obj={"file": "F", "pathId": 2}), {"kind": "t"})
    a1 = contract.artifact("F:1#image", c, contract.provenance(t, atoms={}), {"kind": "t"})
    assert a1["provenance"]["inputs"] == [{"role": "bundle", "sha256": SHA}]     # no input names in records
    assert a1["provenance"]["stage"]["params"] == contract.digest(t.params)
    doc = contract.result(t, [a2, a1], [contract.item("F:2", "failed", why=contract.reason("x")),
                                        contract.item("F:1", "exported", artifacts=["F:1#image"])])
    assert [a["id"] for a in doc["artifacts"]] == ["F:1#image", "F:2#image"]
    assert [i["object"] for i in doc["items"]] == ["F:1", "F:2"] and doc["status"] == "partial"
    with pytest.raises(ValueError, match="duplicate artifact"):
        contract.result(t, [a1, a1], [])
    with pytest.raises(ValueError, match="more than one item"):
        contract.result(t, [a1], [contract.item("F:1", "unsupported", why=contract.reason("x"))] * 2)
    with pytest.raises(ValueError, match="unknown artifact"):
        contract.result(t, [], [contract.item("F:1", "exported", artifacts=["F:1#image"])])
    with pytest.raises(ValueError, match="extension"):
        contract.artifact("F:1#x", dict(c, ext=""), {}, {"kind": "t"})
    with pytest.raises(ValueError, match="kind"):
        contract.artifact("F:1#x", c, {}, {})
    assert contract.primary_artifact([dict(a1, id="F:1#meta"), dict(a1, id="F:1#image")])["id"] == "F:1#image"
    assert contract.primary_artifact([dict(a1, id="F:1#zeta"), dict(a1, id="F:1#blob:x")])["id"] == "F:1#blob:x"


def test_run_ids_cover_the_deterministic_parts_only():
    kw = dict(context={"region": "r"}, selection=["group:a"], params={"s": {}}, stages={"s": 1},
              tasks=[{"id": "s:b", "key": SHA, "status": "ran"}, {"id": "s:a", "key": None, "status": "failed"}],
              layouts={"original": SHA}, summary={"ran": 1})
    doc = contract.run_doc(**kw)
    assert [t["id"] for t in doc["tasks"]] == ["s:a", "s:b"]
    warm = contract.run_doc(**dict(kw, tasks=[{"id": "s:b", "key": SHA, "status": "hit"},
                                              {"id": "s:a", "key": None, "status": "failed"}], summary={"hit": 1}))
    assert warm["id"] == doc["id"]
    assert contract.run_doc(**dict(kw, context={"region": "s"}))["id"] != doc["id"]
    with pytest.raises(ValueError):
        contract.run_doc(**dict(kw, tasks=[{"id": "s:a", "key": SHA, "status": "maybe"}]))


# ---------------------------------------------------------------- schemas
def validators():
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource
    docs = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.schema.json")}
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs.values())
    return {d["title"]: jsonschema.Draft202012Validator(d, registry=registry) for d in docs.values()}


def test_every_document_has_a_valid_schema():
    jsonschema = pytest.importorskip("jsonschema")
    names = {getattr(contract, n) for n in dir(contract) if isinstance(getattr(contract, n), str)
             and re.fullmatch(r"nnnotes\.[a-z-]+/[0-9]+", getattr(contract, n))}
    v = validators()
    assert names <= set(v), names - set(v)
    for p in SCHEMAS.glob("*.schema.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text(encoding="utf-8")))
        assert p.read_bytes().endswith(b"\n") and b"\r" not in p.read_bytes()


def test_the_documents_of_a_run_validate(tmp_path):
    v = validators()
    store = Store(tmp_path / "s")
    facts = {"files": toy_files(tmp_path / "in", {"a": "x\n\nFAIL\n?q", "b": "CRASH", "c": "y"})}
    p = plan_mod.plan(toy_stages(), PIPELINE, store, facts=facts, layouts={"cas": {"write": 1, "remove": 0,
                                                                                    "keep": 0}})
    run = Orchestrator(store, toy_stages(), log=lambda m: None).run(PIPELINE, facts=facts, selection=["all"])
    sources = [layout.Source(tid, doc) for tid, doc in run.results()]
    entries, _ = layout.entries("original", sources)
    w = layout.materialize(tmp_path / "out", "original", {}, entries, layout.from_store(store))
    manifest = run.write({"original": w["sha256"]})
    docs = [(contract.PLAN, p.doc), (contract.RUN, manifest), (contract.LAYOUT, w["manifest"]),
            (contract.COVERAGE, run.coverage()), (contract.FAILURES, run.failures_doc())]
    docs += [(contract.TASK, t.to_json()) for t in p.tasks]
    for _, doc in run.results():
        docs.append((contract.RESULT, doc))
        docs += [(contract.ARTIFACT, a) for a in doc["artifacts"]]
    keys = [t["key"] for t in manifest["tasks"] if t["status"] == "ran"]
    docs += [(contract.COST, store.cost(k)) for k in keys]
    for name, doc in docs:
        errors = [e.message for e in v[name].iter_errors(contract.loads(contract.dumps(doc)))]
        assert errors == [], (name, errors)
    bad = dict(run.failures_doc()["failures"][0], code="")
    assert list(v[contract.FAILURES].iter_errors({"schema": contract.FAILURES, "failures": [bad]}))
    item = {"object": "F:1", "status": "contained"}
    res = {"schema": contract.RESULT, "key": SHA, "keyParts": a_task().key_parts(), "status": "ok",
           "artifacts": [], "items": [item]}
    assert list(v[contract.RESULT].iter_errors(res))              # contained without `in`


# ---------------------------------------------------------------- documentation
def test_the_contract_is_documented():
    text = (ROOT / "docs" / "contracts.md").read_text(encoding="utf-8")
    names = [getattr(contract, n) for n in dir(contract) if isinstance(getattr(contract, n), str)
             and re.fullmatch(r"nnnotes\.[a-z-]+/[0-9]+", getattr(contract, n))]
    words = (names + list(contract.ITEM_STATUSES) + list(contract.RESULT_STATUSES) + list(contract.LOCATOR_KINDS)
             + list(contract.RUN_TASK_STATUSES) + list(contract.PRIMARY_ROLES) + [contract.CANON])
    plan_schema = json.loads((SCHEMAS / "plan.schema.json").read_text(encoding="utf-8"))
    words += plan_schema["properties"]["nodes"]["items"]["properties"]["reasons"]["items"]["properties"]["code"]["enum"]
    words += list(FAILURE_CODES) + ["$float", "1e999"]
    missing = [w for w in words if not re.search(rf"(?<![\w.-]){re.escape(w)}(?![\w-])", text)]
    assert missing == []
    for p in SCHEMAS.glob("*.schema.json"):
        assert f"schema/{p.name}" in text, p.name
