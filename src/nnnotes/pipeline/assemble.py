"""Product stages: products assembled from the unity.export results of the objects they reach, never from a bundle.

A product (a Live2D model, a story, a live, ...) is a stage whose task per subject starts from root objects: the
objects of the catalog keys `keys(subject)` gives, of the Resources.Load paths `resources(subject)` gives and the
objects `root_objects(subject)` names (all read from the planning facts alone, so a command can ask for the units its
products need before anything runs: `request`). The
objects it reaches are those of the units holding its roots and every unit an item of a reached unit references
(contract.item `refs`), through the unity.export results; a sprite whose atlas is in another bundle has its image
from sprite.crop. A reached unit that the request did not export is an error of the request; a referenced object
that no census holds is listed as missing.

A task's inputs are the object table of what it reaches ("objects", nnnotes.objects/1: every object with its class,
name, item status and files, the contained ones with the object holding them) and what `more_inputs` adds (master
tables, parameters' data); it depends on the unity.export and sprite.crop tasks it read. `assemble` makes the
product's own files from the table and the bytes of its objects; the result holds them as "<task id>#<path>", the
table of the objects they use as "<task id>#objects", and one item for the subject. A product layout
(layout.product_entries) places the files and the objects' files and writes the product's manifest.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from ..unity.atoms import Unsupported
from . import contract, units
from .contract import Cost, Input
from .layout import PRODUCT_TABLE, escape_segment
from .orchestrate import SEVERITY
from .stages import Output, Stage

EXPORT, CROP = "unity.export", "sprite.crop"
ADDRESSES = units.ADDRESSES
TABLE_KIND = "product.objects"


def request(stages: dict, products: dict, env) -> dict:
    """The fact "roots" (pipeline.units) of a request for `products` {stage name: subjects}: the catalog keys, the
    resource paths and the root objects of every subject, sorted."""
    keys, resources, objects = set(), set(), set()
    for name, subjects in sorted(products.items()):
        stage = stages[name]
        for s in subjects:
            keys.update(stage.keys(s, env))
            resources.update(stage.resources(s, env))
            objects.update(stage.root_objects(s, env))
    return {"keys": sorted(keys), "resources": sorted(resources), "objects": sorted(objects)}


def _entry(item: dict, arts: dict) -> dict:
    """The object table entry of a unity.export or sprite.crop item."""
    e: dict = {"status": item["status"]}
    cls, name, files = item.get("class"), None, {}
    for aid in item.get("artifacts", ()):
        a = arts[aid]
        c = a["content"]
        files[contract.parse_artifact_id(aid)[1]] = {"sha256": c["sha256"], "size": c["size"], "ext": c["ext"]}
        obj = (a.get("provenance") or {}).get("object") or {}
        cls, name = cls or obj.get("class"), name or obj.get("name")
    if cls:
        e["class"] = cls
    if name:
        e["name"] = name
    if files:
        e["files"] = files
    if "in" in item:
        e["in"] = contract.parse_artifact_id(item["in"])[0]
    if "reason" in item:
        e["reason"] = item["reason"]["code"]
    return e


def _merge(e: dict, other: dict) -> None:
    """Add the files of another item of the same object; the status becomes the more severe."""
    e.setdefault("files", {}).update(other.get("files", {}))
    if SEVERITY.index(other["status"]) < SEVERITY.index(e["status"]):
        e["status"] = other["status"]
        if "reason" in other:
            e["reason"] = other["reason"]


@dataclass
class Reach:
    """What a product reaches: its object table (nnnotes.objects/1), the tasks whose results it read, the bytes of
    its objects' files."""

    table: dict
    read: list = field(default_factory=list)
    size: int = 0


class _Results:
    """The items and artifact records of the results read, by result key (a result never changes)."""

    def __init__(self, size: int = 4096):
        self.size, self._docs = size, OrderedDict()

    def get(self, env, tid: str) -> tuple[list, dict]:
        key = env.key(tid)
        doc = self._docs.get(key)
        if doc is None:
            res = env.result(tid)
            doc = self._docs[key] = (res["items"], {a["id"]: a for a in res["artifacts"]})
            while len(self._docs) > self.size:
                self._docs.popitem(last=False)
        else:
            self._docs.move_to_end(key)
        return doc


def reach(env, roots, results: _Results | None = None) -> Reach:
    """The objects reachable from the object ids `roots` (module documentation)."""
    results = results or _Results()
    table = units.table(env)
    todo, seen, missing = [], set(), set()
    for o in roots:
        s = table.subject_of(o)
        if s is None:
            missing.add(o)
        else:
            todo.append(s)
    objects: dict[str, dict] = {}
    read = []
    crops: dict[str, list] = {}  # atlas object id -> sprite object ids
    while todo:
        s = todo.pop()
        if s in seen:
            continue
        seen.add(s)
        table.unit(s)  # ValueError: a unit the request does not export
        tid = contract.task_id(EXPORT, s)
        items, arts = results.get(env, tid)
        read.append(tid)
        for it in items:
            oid = it["object"]
            objects[oid] = _entry(it, arts)
            for aid in it.get("artifacts", ()):
                facts = arts[aid]["semantics"].get("facts") or {}
                if facts.get("crop") == CROP:
                    crops.setdefault(facts["atlas"], []).append(oid)
            for r in it.get("refs", ()):
                t = table.subject_of(r)
                if t is None:
                    missing.add(r)
                elif t not in seen:
                    todo.append(t)
    for atlas, sprites in sorted(crops.items()):
        s = table.subject_of(atlas)
        tid = contract.task_id(CROP, s) if s is not None else None
        if tid is None or not env.known(tid):  # the atlas is not exported: its sprites have no image
            missing.add(atlas)
            continue
        items, arts = results.get(env, tid)
        read.append(tid)
        made = {it["object"]: it for it in items}
        for oid in sprites:
            if oid in made:
                _merge(objects[oid], _entry(made[oid], arts))
    doc = {
        "schema": contract.OBJECTS,
        "roots": sorted(set(roots)),
        "objects": dict(sorted(objects.items())),
        "missing": sorted(missing),
    }
    size = sum(f["size"] for e in objects.values() for f in e.get("files", {}).values())
    return Reach(doc, sorted(read), size)


class Objects:
    """The object table of a product task and the bytes of its objects' files (from the depot)."""

    def __init__(self, doc: dict, depot):
        self.doc, self.depot = doc, depot
        self.roots: list[str] = doc["roots"]
        self._objects: dict = doc["objects"]
        self._json: dict = {}

    def __contains__(self, oid: str) -> bool:
        return oid in self._objects

    def ids(self, cls: str | None = None) -> list[str]:
        """The object ids of the table (of class `cls`), sorted."""
        return [o for o, e in self._objects.items() if cls is None or e.get("class") == cls]

    def info(self, oid: str) -> dict:
        try:
            return self._objects[oid]
        except KeyError:
            raise KeyError(f"object {oid} is not in the product's object table") from None

    def bytes(self, oid: str, role: str) -> bytes:
        files = self.info(oid).get("files") or {}
        if role not in files:
            raise KeyError(f"object {oid} has no {role!r} file")
        return self.depot.read(files[role]["sha256"])

    def json(self, oid: str, role: str = "json"):
        """The decoded JSON of a file of an object (kept for the task)."""
        k = (oid, role)
        if k not in self._json:
            self._json[k] = contract.loads(self.bytes(oid, role))
        return self._json[k]


@dataclass
class Assembly:
    """What `assemble` makes: the product's files {path: (bytes, extension, kind)} and what of the object table they
    use: `objects` the object ids (None: every object of the table), `roles` {object id: the roles of its files the
    product keeps} for an object of which it uses some files only (a shader's programs for one graphics API). The
    product's object table, and so its manifest, holds only those."""

    files: dict
    objects: set | None = None
    roles: dict = field(default_factory=dict)


def _prune(tid: str, objects: dict, made: Assembly) -> dict:
    """The entries of an object table an Assembly keeps."""
    keep = set(objects) if made.objects is None else set(made.objects)
    unknown = sorted((keep | set(made.roles)) - set(objects))
    if unknown:
        raise ValueError(f"{tid}: objects not in the table: {', '.join(unknown[:5])}")
    out = {}
    for o, e in objects.items():
        if o not in keep:
            continue
        roles = made.roles.get(o)
        if roles is not None:
            files = e.get("files") or {}
            lacking = sorted(set(roles) - set(files))
            if lacking:
                raise ValueError(f"{tid}: object {o} has no {', '.join(lacking)} file")
            e = dict(e, files={r: f for r, f in files.items() if r in roles})
        out[o] = e
    return out


class ProductStage(Stage):
    """Base of the product stages (module documentation). Subclasses set `name`, `version` and `KIND`, and define
    `keys`, `resources` and/or `root_objects` and `assemble`; `subjects` defaults to the subjects of the fact
    "products". A stage that reads master tables names them in `MASTER` (its inputs come from the fact "master")."""

    after = (ADDRESSES, EXPORT, CROP)
    KIND = "product"
    MASTER: tuple[str, ...] = ()

    def __init__(self):
        self._memo: dict = {}
        self._results = _Results()

    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k not in ("_memo", "_results")}

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._memo, self._results = {}, _Results()

    # ---------------------------------------------------------------- what a subclass defines
    def subjects(self, env) -> list[str]:
        return sorted((env.facts.get("products") or {}).get(self.name, ()))

    def select(self, keys: list[str]) -> list[str]:
        """The subjects among the catalog keys `keys` (those a selection's bundles hold), sorted."""
        return []

    def keys(self, subject: str, env) -> list[str]:
        """The catalog keys whose objects are roots of `subject` (from the facts alone)."""
        return []

    def resources(self, subject: str, env) -> list[str]:
        """The Resources.Load paths whose objects are roots of `subject` (from the facts alone)."""
        return []

    def root_objects(self, subject: str, env) -> list[str]:
        """Object ids that are roots of `subject` besides those of its keys and resource paths (from the facts
        alone)."""
        return []

    def more_inputs(self, subject: str, env) -> list[Input]:
        """Inputs besides the object table."""
        return []

    def assemble(self, task, objects: Objects) -> Assembly:
        raise NotImplementedError

    def manifest(self, subject: str) -> str:
        """The path of the subject's manifest in a product layout: one directory, then a file."""
        return f"{self.name}/{escape_segment(subject)}.json"

    # ---------------------------------------------------------------- planning
    def reach(self, subject: str, env) -> Reach:
        """What `subject` reaches (kept while the results it read are the same)."""
        roots = units.root_objects(
            env, self.keys(subject, env), self.root_objects(subject, env), self.resources(subject, env)
        )
        sig = (tuple(roots), id(units.table(env)))
        memo = self._memo.get(subject)
        if memo is not None and memo[0] == sig and all(env.key(t) == k for t, k in memo[1]):
            return memo[2]
        r = reach(env, roots, self._results)
        self._memo[subject] = (sig, [(t, env.key(t)) for t in r.read], r)
        return r

    def inputs(self, subject: str, env) -> list[Input]:
        data = contract.encode(self.reach(subject, env).table)
        table = Input("objects", env.depot.put(data), len(data), None, ({"kind": "depot"},))
        return [table, *self.more_inputs(subject, env)]

    def depends(self, subject: str, env) -> list[str]:
        named = self.keys(subject, env) or self.resources(subject, env)
        own = [contract.task_id(ADDRESSES, s) for s in sorted(env.fact("catalogs"))] if named else []
        return own + self.reach(subject, env).read

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the bytes of the objects' files: a product reads each of them about once."""
        size = self.reach(subject, env).size + sum(i.size for i in inputs)
        return Cost(0.02 + size / 100e6, (64 << 20) + 2 * size)

    # ---------------------------------------------------------------- running
    def run(self, task, depot) -> Output:
        data = depot.input_bytes(task.input("objects"))
        doc = contract.loads(data)
        try:
            made = self.assemble(task, Objects(doc, depot))
        except Unsupported as e:
            why = contract.reason(e.code, e.message)
            return Output([], [contract.item(task.id, "unsupported", why=why, cls=self.KIND)])
        prov = contract.provenance(task)
        arts = []
        for path, (body, ext, kind) in sorted(made.files.items()):
            if path == PRODUCT_TABLE:
                raise ValueError(f"{task.id}: a product file cannot be named {PRODUCT_TABLE!r}")
            aid = contract.artifact_id(task.id, path)
            arts.append(contract.artifact(aid, depot.add(body, ext), prov, {"kind": kind}))
        if made.objects is not None or made.roles:
            doc = dict(doc, objects=_prune(task.id, doc["objects"], made))
            data = contract.encode(doc)
        aid = contract.artifact_id(task.id, PRODUCT_TABLE)
        arts.append(contract.artifact(aid, depot.add(data, "json"), prov, {"kind": TABLE_KIND}))
        item = contract.item(task.id, "exported", artifacts=[a["id"] for a in arts], cls=self.KIND)
        return Output(arts, [item])
