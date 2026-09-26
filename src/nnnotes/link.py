"""The link stages: tables that connect catalog keys, bundles, serialized files, scripts and objects.

link.scripts (nnnotes.scripts/1), one global task: every MonoScript of the censuses, by object id:
    {"scripts": {"CAB-...:pathId": "assembly|namespace|class"}} (the assembly without `.dll`; an empty namespace
    stays empty). A MonoBehaviour's class is the entry of its m_Script; `script_context` picks the entries a task
    consumes.

link.addresses (nnnotes.addresses/1), one task per catalog pair: the catalog index and the censuses of its bundles
(by stable bundle name) ->
    bundles    {stable name: {name, files, assetBundleName, keys}}: the file name in this catalog version, its
               serialized files, the AssetBundle name the census found and the keys located in it
    files      {serialized file: stable bundle name} (CAB -> bundle)
    keys       {primary key: [{location, type, internalId, bundle | raw, objects}]}: an asset location's bundle is the
               dependency whose container holds its internal id (compared ignoring case); `objects` the object ids
               the container path names (null when a dependency bundle has no census)
    objects    {stable address: {object, class, name, byteSize, typeHash}} of the objects a container path names
    problems   files named by two bundles, bundles whose census AssetBundle name is not the catalog's, keys whose
               internal id no container holds

A stable address (contract.stable_address) finds an object again in another catalog version, where its object id
has changed with its bundle's content: "<bundle>/<container>" for the object that owns a container path (the class
its extension names, else the first by file and path id: the object the `original` layout puts at the path),
"<bundle>/<container>[<name>]" for another object under it with a name no other object under it has, else
"<bundle>:<pathId>". In a bundle of several serialized files (their path ids may coincide) the path id form names
the file after the bundle: the part of the file name after its CAB-<hex> prefix ("scene_x.sharedAssets:12").
Objects that no container path names are not listed; `address_of` gives any object's address.

link.artifacts (nnnotes.artifacts/1), one global task: the results of the stages that export objects (unity.export,
sprite.crop) -> where each object went:
    objects    {object id: [artifact ids]} of the objects with artifacts (exported, generic, unsupported with some)
    contained  {object id: the artifact it is part of}
    artifacts  {artifact id: {sha256, size, ext}}
Its context names the results it reads ({"results": {task id: key}}); results are functions of their keys, so the
table is a function of its key. With a layout manifest (its entries' ids) it gives every object's files.
"""
from __future__ import annotations

import re

from . import catalogdb, census, contract
from .contract import Cost, Input
from .layout import EXT_CLASSES
from .stages import Output, Pending, Stage

SCRIPTS = "nnnotes.scripts/1"
ADDRESSES = "nnnotes.addresses/1"
ARTIFACTS = "nnnotes.artifacts/1"
EXPORT_STAGES = ("unity.export", "sprite.crop")
OBJECT_FIELDS = ("object", "class", "name", "byteSize", "typeHash")
_CAB = re.compile(r"^CAB-[0-9a-f]+")


# ---------------------------------------------------------------- scripts
def script_entry(s: dict) -> str:
    """"assembly|namespace|class" of a census MonoScript record."""
    asm = s.get("assembly") or ""
    if asm.endswith(".dll"):
        asm = asm[:-4]
    return f"{asm}|{s.get('namespace') or ''}|{s.get('class') or ''}"


def scripts(censuses) -> dict:
    """The script table (nnnotes.scripts/1) of census documents."""
    table = {}
    for doc in censuses:
        for s in doc["scripts"]:
            table[contract.object_id(s["file"], s["pathId"])] = script_entry(s)
    return {"schema": SCRIPTS, "scripts": dict(sorted(table.items()))}


def script_context(table: dict, refs) -> dict:
    """The entries of a script table (the "scripts" of nnnotes.scripts/1) for the MonoScript object ids `refs`
    (census.script_refs of a bundle): what a task that resolves those scripts consumes. Refs the table lacks are
    left out."""
    return {r: table[r] for r in sorted(set(refs)) if r in table}


# ---------------------------------------------------------------- addresses
def file_tags(files) -> dict[str, str]:
    """{serialized file: the tag its path id addresses carry after the bundle name}: "" in a bundle of one file,
    else the file name after its CAB-<hex> prefix ("" / ".sharedAssets"); "|<file>" where those coincide."""
    files = list(files)
    if len(files) <= 1:
        return {f: "" for f in files}
    tags = {f: _CAB.sub("", f) for f in files}
    if len(set(tags.values())) < len(tags):
        tags = {f: "|" + f for f in files}
    return tags


def _path_address(stable: str, tags: dict, file: str, pid: int) -> str:
    tag = tags.get(file, "|" + file) if tags else ""
    return contract.stable_address(stable + tag, path_id=pid)


def _container_addresses(stable: str, entries: list[dict], info: dict, tags: dict) -> list[tuple[str, dict]]:
    """[(address, container entry)] of the objects one container path names in a bundle."""
    members = [e for e in entries if e["file"] is not None]
    if not members:
        return []
    path = min(e["path"] for e in members)
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    classes = EXT_CLASSES.get(ext, ())

    def cls(e):
        return (info.get((e["file"], e["pathId"])) or {}).get("class")

    order = sorted({(e["file"], e["pathId"]): e for e in members}.values(),
                   key=lambda e: (cls(e) not in classes, e["file"], e["pathId"]))
    out = [(contract.stable_address(stable, path), order[0])]
    names: dict[str, list[dict]] = {}
    for e in order[1:]:
        name = (info.get((e["file"], e["pathId"])) or {}).get("name") or ""
        names.setdefault(name.casefold(), []).append(e)
    for folded, same in sorted(names.items()):
        for e in same:
            name = (info.get((e["file"], e["pathId"])) or {}).get("name") or ""
            if name and len(same) == 1:
                out.append((contract.stable_address(stable, path, name=name), e))
            else:
                out.append((_path_address(stable, tags, e["file"], e["pathId"]), e))
    return out


def addresses(index: dict, censuses) -> dict:
    """The address table (nnnotes.addresses/1) of a catalog index and the censuses of its bundles: {stable bundle
    name: census document}, or (stable name, census document) pairs (read one at a time: only the container
    entries and the objects they name are kept). Censuses of names the index does not list are ignored."""
    files_of = catalogdb.file_refs(index)
    listed = {b["stable"]: b for b in index["bundles"]}
    bundles, owners, info, groups, tags_of = {}, {}, {}, {}, {}
    problems = {"files": [], "bundleNames": [], "keys": []}
    pairs = sorted(censuses.items()) if isinstance(censuses, dict) else censuses
    for stable, doc in pairs:
        b = listed.get(stable)
        if b is None:
            continue
        names = [f["name"] for f in doc["files"]]
        for n in names:
            owners.setdefault(n, set()).add(stable)
        found = [a["assetBundleName"] for a in doc["assetBundles"] if a["assetBundleName"]]
        if b.get("bundleName") and found and b["bundleName"] + ".bundle" not in found:
            problems["bundleNames"].append({"bundle": stable, "catalog": b["bundleName"] + ".bundle",
                                            "census": found})
        g: dict[str, list[dict]] = {}
        for e in census.container(doc):
            g.setdefault(e["path"].casefold(), []).append(e)
        wanted = {(e["file"], e["pathId"]) for es in g.values() for e in es if e["file"] is not None}
        for file, o in census.objects(doc):
            if (file, o["pathId"]) in wanted:
                info[(file, o["pathId"])] = o
        groups[stable], tags_of[stable] = g, file_tags(names)
        bundles[stable] = {"name": b["name"], "files": names, "assetBundleName": found[0] if found else None,
                           "keys": set()}
    cab = {n: min(s) for n, s in owners.items()}
    problems["files"] = [{"file": n, "bundles": sorted(s)} for n, s in sorted(owners.items()) if len(s) > 1]
    problems["bundleNames"].sort(key=lambda p: p["bundle"])
    objects = {}
    for stable, g in groups.items():
        for _, entries in sorted(g.items()):
            for addr, e in _container_addresses(stable, entries, info, tags_of[stable]):
                o = info.get((e["file"], e["pathId"])) or {}
                objects[addr] = {"object": contract.object_id(e["file"], e["pathId"]), "class": o.get("class"),
                                 "name": o.get("name"), "byteSize": o.get("byteSize"), "typeHash": o.get("typeHash")}
    keys: dict[str, list[dict]] = {}
    for loc in index["locations"]:
        entry = {"location": loc["id"], "type": loc["type"], "internalId": loc["internalId"]}
        if loc["id"] in files_of:
            kind, stable = files_of[loc["id"]]
            entry[kind] = stable
            if stable in bundles:
                bundles[stable]["keys"].add(loc["primaryKey"])
        else:
            deps = [files_of[d] for d in loc["dependencies"] if d in files_of]
            in_bundles = [s for k, s in deps if k == "bundle"]
            raws = [s for k, s in deps if k == "raw"]
            folded = loc["internalId"].casefold()
            home = next((s for s in in_bundles if folded in groups.get(s, {})), None)
            if home is not None:
                entry["bundle"] = home
                entry["objects"] = [contract.object_id(e["file"], e["pathId"]) for e in groups[home][folded]
                                    if e["file"] is not None]
                bundles[home]["keys"].add(loc["primaryKey"])
            else:
                entry["bundle"] = in_bundles[0] if in_bundles else None
                known = in_bundles and all(s in groups for s in in_bundles)
                entry["objects"] = [] if known else None
                if known and loc["kind"] == "asset" and not raws:
                    problems["keys"].append(loc["primaryKey"])
            if raws:
                entry["raw"] = raws
        keys.setdefault(loc["primaryKey"], []).append(entry)
    for b in bundles.values():
        b["keys"] = sorted(b["keys"])
    problems["keys"] = sorted(set(problems["keys"]))
    cats = index["catalogs"]
    return {"schema": ADDRESSES,
            "catalog": {n: cats[n]["sha256"] if n in cats else None for n in catalogdb.CATALOG_NAMES},
            "bundles": dict(sorted(bundles.items())), "files": dict(sorted(cab.items())),
            "keys": dict(sorted(keys.items())),
            "objects": dict(sorted(objects.items())), "problems": problems}


def address_of(doc: dict, oid: str, container_objects: dict | None = None) -> str | None:
    """The stable address of an object id in an address table (None when its file is in no listed bundle).
    `container_objects`: {object id: address} of the listed objects (reverse(doc)), for repeated calls."""
    rev = container_objects if container_objects is not None else reverse(doc)
    if oid in rev:
        return rev[oid]
    file, pid = contract.parse_object_id(oid)
    stable = doc["files"].get(file)
    if stable is None:
        return None
    return _path_address(stable, file_tags(doc["bundles"][stable]["files"]), file, pid)


def reverse(doc: dict) -> dict[str, str]:
    """{object id: stable address} of the objects an address table lists (the first address of an object that
    several container paths name)."""
    out: dict[str, str] = {}
    for addr, o in doc["objects"].items():
        out.setdefault(o["object"], addr)
    return out


def diff_addresses(old: dict, new: dict) -> dict:
    """The objects of two address tables compared by stable address: added, removed, changed (class, name, size or
    type hash differ), rebuilt (only the object id differs: its bundle changed), unchanged (a count)."""
    a, b = old["objects"], new["objects"]
    changed, rebuilt, same = [], [], 0
    for addr in sorted(set(a) & set(b)):
        fields = [f for f in OBJECT_FIELDS if a[addr].get(f) != b[addr].get(f)]
        if not fields:
            same += 1
        elif fields == ["object"]:
            rebuilt.append({"address": addr, "old": a[addr]["object"], "new": b[addr]["object"]})
        else:
            changed.append({"address": addr, "fields": fields, "old": a[addr], "new": b[addr]})
    out = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)), "changed": changed,
           "rebuilt": rebuilt, "unchanged": same}
    out["summary"] = {k: (v if isinstance(v, int) else len(v)) for k, v in out.items()}
    return out


# ---------------------------------------------------------------- stages
def _census_input(env, tid: str) -> Input:
    return env.input_of(tid, "census", as_role="census:" + contract.parse_task_id(tid, census.CensusStage.name)[1])


def _read(store, task, role: str) -> dict:
    return contract.loads(store.input_bytes(task.input(role)))


class ScriptsStage(Stage):
    """link.scripts: the censuses that hold MonoScripts -> the script table. One subject ("all"); inputs "census:<bundle
    subject>". Waits for census tasks that have not run; skips failed ones. Artifact "link.scripts:all#scripts"."""
    name = "link.scripts"
    version = 1
    after = ("unity.census",)

    def subjects(self, env) -> list[str]:
        return ["all"]

    def inputs(self, subject: str, env) -> list[Input]:
        pending = env.pending(census.CensusStage.name)
        if pending:
            raise Pending(pending[0])
        return [_census_input(env, tid) for tid in env.tasks(census.CensusStage.name)
                if env.artifact(tid, "census")["semantics"]["facts"].get("scripts")]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.05 + size / 50e6, (40 << 20) + 8 * size)

    def run(self, task, store) -> Output:
        doc = scripts(_read(store, task, i.role) for i in task.inputs)
        content = store.add(contract.encode(doc), "json")
        rec = contract.artifact(contract.artifact_id(task.id, "scripts"), content, contract.provenance(task),
                                {"kind": "link.scripts", "format": "json", "facts": {"scripts": len(doc["scripts"])}})
        return Output([rec], [])


class AddressesStage(Stage):
    """link.addresses: a catalog index (input "index", the result of catalog.index:<subject>) and the censuses of its
    bundles (inputs "census:<stable name>") -> the address table. Subjects: those of catalog.index (the fact
    "catalogs"). Waits for census tasks of its bundles that have not run; skips failed ones. Artifact
    "link.addresses:<subject>#addresses"."""
    name = "link.addresses"
    version = 1
    after = ("catalog.index", "unity.census")

    def subjects(self, env) -> list[str]:
        return sorted(env.fact("catalogs"))

    def inputs(self, subject: str, env) -> list[Input]:
        tid = contract.task_id(catalogdb.IndexStage.name, subject)
        idx = env.input_of(tid, "index")
        stables = {b["stable"] for b in contract.loads(env.store.read(idx.sha256))["bundles"]}
        wanted = {contract.task_id(census.CensusStage.name, s) for s in stables}
        pending = [t for t in env.pending(census.CensusStage.name) if t in wanted]
        if pending:
            raise Pending(pending[0])
        return [idx] + [_census_input(env, t) for t in env.tasks(census.CensusStage.name) if t in wanted]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.5 + size / 50e6, (200 << 20) + size // 2)

    def run(self, task, store) -> Output:
        idx = _read(store, task, "index")
        docs = ((i.role.split(":", 1)[1], _read(store, task, i.role)) for i in task.inputs
                if i.role.startswith("census:"))
        doc = addresses(idx, docs)
        content = store.add(contract.encode(doc), "json")
        facts = {"bundles": len(doc["bundles"]), "keys": len(doc["keys"]), "objects": len(doc["objects"]),
                 "problems": {k: len(v) for k, v in doc["problems"].items()}}
        rec = contract.artifact(contract.artifact_id(task.id, "addresses"), content, contract.provenance(task),
                                {"kind": "link.addresses", "format": "json", "facts": facts})
        return Output([rec], [])


def artifacts(results) -> dict:
    """The artifact table (nnnotes.artifacts/1) of results [(task id, result)]."""
    objects, contained, arts = {}, {}, {}
    for _, doc in results:
        for a in doc["artifacts"]:
            c = a["content"]
            arts[a["id"]] = {"sha256": c["sha256"], "size": c["size"], "ext": c["ext"]}
        for it in doc["items"]:
            if it.get("artifacts"):
                objects.setdefault(it["object"], set()).update(it["artifacts"])
            elif "in" in it:
                contained[it["object"]] = it["in"]
    return {"schema": ARTIFACTS, "objects": {o: sorted(v) for o, v in sorted(objects.items())},
            "contained": dict(sorted(contained.items())), "artifacts": dict(sorted(arts.items()))}


class ArtifactsStage(Stage):
    """link.artifacts: the results of the export stages (EXPORT_STAGES, those of the pipeline) -> the artifact table.
    One subject ("all"); no inputs, context {"results": {task id: key}}. Waits for export tasks that have not run;
    skips failed ones. Artifact "link.artifacts:all#artifacts"."""
    name = "link.artifacts"
    version = 1
    after = ("unity.export",)

    def subjects(self, env) -> list[str]:
        return ["all"]

    def _tasks(self, env) -> list[str]:
        for stage in EXPORT_STAGES:
            pending = env.pending(stage)
            if pending:
                raise Pending(pending[0])
        return [t for stage in EXPORT_STAGES for t in env.tasks(stage)]

    def depends(self, subject: str, env) -> list[str]:
        return self._tasks(env)

    def context(self, subject: str, env) -> dict:
        return {"results": {t: env.key(t) for t in self._tasks(env)}}

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        n = len(self._tasks(env))
        return Cost(0.05 + 0.002 * n, (60 << 20) + 200_000 * n)

    def run(self, task, store) -> Output:
        results = []
        for tid, key in sorted(task.context.get("results", {}).items()):
            doc = store.result(key)
            if doc is None:
                raise FileNotFoundError(f"result of {tid} ({key}) is not in the store")
            results.append((tid, doc))
        doc = artifacts(results)
        content = store.add(contract.encode(doc), "json")
        facts = {"objects": len(doc["objects"]), "contained": len(doc["contained"]),
                 "artifacts": len(doc["artifacts"])}
        rec = contract.artifact(contract.artifact_id(task.id, "artifacts"), content, contract.provenance(task),
                                {"kind": "link.artifacts", "format": "json", "facts": facts})
        return Output([rec], [])
