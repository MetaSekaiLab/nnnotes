"""The units of unity.export, from the censuses: what one export task converts, and which of them a request needs.

A unit is a hierarchy (a root GameObject with the GameObjects, Transforms and components its prefab holds: the
objects whose census record names it as their `unit`) or an object outside any hierarchy. Its subject is the
path-id stable address of its root object (link.path_address: "<bundle>:<pathId>", with the file after the bundle in
a bundle of several serialized files), which stays when the bundle's content changes; its object ids do not.

The request (`table`): every unit holding an object of the selected bundles (fact "selected") or a root object of the
fact "roots", and every unit reachable from those through what their objects reference (census `refs`) across the
censuses of the bundles of the fact "bundles"; with the fact "classes" (a list of class names; absent or None:
every class), only the units whose root object is of those classes. A bundle whose census failed has no units; a
reference into a file no census holds is left out.

The fact "roots" (absent: none) is {"keys": catalog keys, "resources": resource paths, "objects": object ids}, each
list optional: the objects of each key and of each resource path (all those the address tables of link.addresses
list for it: a resource path is a Resources.Load path of the APK's built-in files, compared ignoring case; one
without objects adds none) and the objects named. The products of a request start from them (pipeline.assemble).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import contract, link, views
from .census import CensusStage
from .stages import Pending

CENSUS = CensusStage.name
ADDRESSES = views.ADDRESSES_STAGE


@dataclass(frozen=True)
class Unit:
    """One unit: its subject, stable bundle name, root object id and class, member object ids (the root first),
    ((class, objects)) of its members by class, the MonoScripts they name (m_Script and an AnimationClip's binding
    scripts), the (Unity version, type hash) of its MonoBehaviours in files without type trees, and the byte size of
    its members."""

    subject: str
    bundle: str
    root: str
    cls: str
    members: tuple
    counts: tuple
    scripts: tuple
    trees: tuple
    size: int

    @property
    def classes(self) -> tuple:
        return tuple(c for c, _ in self.counts)


class Units:
    """The units of a request: `units` {subject: Unit} of the requested units; `of` {object id: subject} of the
    objects of every unit of the censuses read; `files` {serialized file: stable bundle name}; `objects` {stable
    bundle name: its objects}; `per_bundle` the number of requested units per bundle; `classes` the objects of the
    requested units per class."""

    def __init__(self, units: dict, of: dict, files: dict, objects: dict):
        self.units, self.of, self.files, self.objects = units, of, files, objects
        self.per_bundle = Counter(u.bundle for u in units.values())
        self.classes: Counter = Counter()
        for u in units.values():
            for c, n in u.counts:
                self.classes[c] += n

    def subjects(self) -> list[str]:
        return sorted(self.units)

    def unit(self, subject: str) -> Unit:
        try:
            return self.units[subject]
        except KeyError:
            raise ValueError(f"unity.export: no requested unit {subject}") from None

    def subject_of(self, oid: str) -> str | None:
        """The subject of the unit holding object `oid` (None when no census read holds it)."""
        return self.of.get(oid)


def _ref(r: dict | None) -> str | None:
    """The object id of a census reference record (None for null or an unknown file index)."""
    if not r or "file" not in r:
        return None
    return contract.object_id(r["file"], r["pathId"])


def _census_units(stable: str, doc: dict, need_refs: bool):
    """(units, {object id: subject}, {file: stable}, {subject: referenced object ids}) of one census."""
    tags = link.file_tags([f["name"] for f in doc["files"]])
    groups: dict[str, list] = {}  # root object id -> [(file record, object record)], the root first
    for f in doc["files"]:
        for o in f["objects"]:
            oid = contract.object_id(f["name"], o["pathId"])
            root = o.get("unit", oid)
            entry = (f, o)
            if root == oid:
                groups.setdefault(root, []).insert(0, entry)
            else:
                groups.setdefault(root, []).append(entry)
    units, of, refs = {}, {}, {}
    for root, members in groups.items():
        rf, ro = members[0]
        if contract.object_id(rf["name"], ro["pathId"]) != root:
            continue  # members of a root the census does not list
        subject = link.path_address(stable, tags, rf["name"], ro["pathId"])
        ids, classes, scripts, trees, size, out = [], Counter(), set(), set(), 0, set()
        for f, o in members:
            oid = contract.object_id(f["name"], o["pathId"])
            ids.append(oid)
            of[oid] = subject
            classes[o["class"]] += 1
            size += int(o.get("byteSize") or 0)
            for s in [o.get("script"), *(o.get("bindingScripts") or ())]:
                r = _ref(s)
                if r is not None:
                    scripts.add(r)
            if o["class"] == "MonoBehaviour" and not f["typetree"] and o.get("typeHash"):
                trees.add((f["unityVersion"] or "", o["typeHash"]))
            if need_refs:
                out.update(o.get("refs") or ())
        units[subject] = Unit(
            subject,
            stable,
            root,
            ro["class"],
            tuple(ids),
            tuple(sorted(classes.items())),
            tuple(sorted(scripts)),
            tuple(sorted(trees)),
            size,
        )
        if need_refs:
            refs[subject] = out.difference(ids)
    return units, of, {f["name"]: stable for f in doc["files"]}, refs


def build(censuses, bundles, selected, classes=None, roots=()) -> Units:
    """The Units of a request over `censuses` [(stable bundle name, census document)] of the bundles `bundles`, from
    the units of the bundles `selected` and of the objects `roots` (module documentation)."""
    selected = set(selected)
    every = selected >= set(bundles)  # no reference can add a unit: none is read
    units: dict[str, Unit] = {}
    of: dict[str, str] = {}
    files: dict[str, str] = {}
    refs: dict[str, set] = {}
    objects: dict[str, int] = {}
    for stable, doc in censuses:
        objects[stable] = int(doc["objects"])
        u, o, f, r = _census_units(stable, doc, need_refs=not every)
        units.update(u)
        of.update(o)
        files.update(f)
        refs.update(r)
    if every:
        want = set(units)
    else:
        want = {s for s, u in units.items() if u.bundle in selected}
        want.update(of[o] for o in roots if o in of)
        todo = list(want)
        while todo:
            for oid in refs.get(todo.pop(), ()):
                s = of.get(oid)
                if s is not None and s not in want:
                    want.add(s)
                    todo.append(s)
    if classes is not None:
        keep = set(classes)
        want = {s for s in want if units[s].cls in keep}
    return Units({s: units[s] for s in sorted(want)}, of, files, objects)


_MEMO: list = []  # [(signature, Units)] of the last table
_ADDRESSES: dict = {}  # {sha256: AddressIndex} of the last address table read


def address_index(env, subject: str) -> views.AddressIndex:
    """The AddressIndex of the result of link.addresses for the catalog `subject` (Pending while it has not run)."""
    sha = env.artifact(contract.task_id(ADDRESSES, subject), "addresses")["content"]["sha256"]
    index = _ADDRESSES.get(sha)
    if index is None:
        index = views.AddressIndex.from_addresses(contract.loads(env.depot.read(sha)))
        _ADDRESSES.clear()
        _ADDRESSES[sha] = index
    return index


def root_objects(env, keys=(), objects=(), resources=()) -> list[str]:
    """The object ids of the catalog `keys` and the resource paths `resources` (through the address tables of the
    fact "catalogs") and the `objects`, sorted."""
    out = set(objects)
    if keys or resources:
        for subject in sorted(env.fact("catalogs")):
            index = address_index(env, subject)
            for k in keys:
                out.update(o.id for o in index.objects(k) or ())
            for p in resources:
                out.update(o.id for o in index.resource(p) or ())
    return sorted(out)


def table(env) -> Units:
    """The Units of the request of a planning environment: the censuses of the fact "bundles" (Pending while one
    has not run; a failed one is skipped), the facts "selected", "roots" (Pending while link.addresses has not run,
    when it names keys or resource paths) and "classes". Kept for the planning state it was made in."""
    bundles = sorted(env.fact("bundles"))
    keys = []
    for b in bundles:
        tid = contract.task_id(CENSUS, b)
        try:
            keys.append((b, env.key(tid)))
        except Pending as p:
            if not p.failed:
                raise
    classes = env.facts.get("classes")
    request = env.facts.get("roots") or {}
    roots = root_objects(env, request.get("keys", ()), request.get("objects", ()), request.get("resources", ()))
    sig = (
        id(env.depot),
        tuple(keys),
        tuple(env.fact("selected")),
        None if classes is None else tuple(classes),
        tuple(roots),
    )
    if _MEMO and _MEMO[0][0] == sig:
        return _MEMO[0][1]

    def censuses():
        for b, key in keys:
            tid = contract.task_id(CENSUS, b)
            aid = contract.artifact_id(tid, "census")
            rec = next(a for a in env.depot.committed(key, tid)["artifacts"] if a["id"] == aid)
            yield b, contract.loads(env.depot.read(rec["content"]["sha256"]))

    t = build(censuses(), bundles, env.fact("selected"), classes, roots)
    _MEMO[:] = [(sig, t)]
    return t
