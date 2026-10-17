"""The link stages: tables that connect catalog keys, bundles, serialized files, scripts and objects.

link.scripts (nnnotes.scripts/1), one global task: every MonoScript of the censuses, by object id:
    {"scripts": {"CAB-...:pathId": "assembly|namespace|class"}} (the assembly without `.dll`; an empty namespace
    stays empty). A MonoBehaviour's class is the entry of its m_Script; `script_context` picks the entries a task
    consumes.

link.addresses (nnnotes.addresses/3), one task per catalog pair: the catalog index and the censuses of its bundles
(by stable bundle name) and of the APK's built-in files (subjects `apk/...`, pipeline.builtins) ->
    bundles    {stable name: {name, files, assetBundleName, keys}}: the file name in this catalog version, its
               serialized files, the AssetBundle name the census found and the keys located in it; a built-in file
               is listed by its subject, named by its path in the APK's `assets/bin/Data/`, without keys
    files      {serialized file: stable bundle name} (CAB -> bundle; `unity_builtin_extra` -> `apk/data.unity3d`)
    keys       {primary key: [{location, type, internalId, bundle | raw, objects}]}: an asset location's bundle is the
               dependency whose container holds its internal id (compared ignoring case); `objects` the object ids
               the container path names, for a scene the objects of its file outside a hierarchy (census.scenes);
               null when a dependency bundle has no census
    resources  {resource path: [object ids]}: the paths of the ResourceManager container of the built-in files (what
               Resources.Load finds, as stored), the objects each names in stored order
    builtins   {selector: [object ids]}: the objects of the built-in files (not MonoScripts) by class, "<class>", and
               a MonoBehaviour, including a component of a hierarchy, by its script's class,
               "MonoBehaviour:<class>" (its MonoScript in the built-in files), each list sorted
    objects    {stable address: {object, class, name, byteSize, typeHash}} of the objects a container path, a scene
               or a resource path names
    problems   files named by two bundles, bundles whose census AssetBundle name is not the catalog's, keys whose
               internal id no container holds

A stable address (contract.stable_address) finds an object again in another catalog version, where its object id
has changed with its bundle's content: "<bundle>/<container>" for the object that owns a container path (the class
its extension names, else the first by file and path id: the object the `original` layout puts at the path),
"<bundle>/<container>[<name>]" for another object under it with a name no other object under it has, else
"<bundle>:<pathId>". In a bundle of several serialized files (their path ids may coincide) the path id form names
the file after the bundle: the part of the file name after its CAB-<hex> prefix ("scene_x.sharedAssets:12"), or
"|<file>" ("apk/data.unity3d|level0:3"). A resource path counts as a container path of the built-in file holding the
ResourceManager ("apk/data.unity3d/live2d/cubism/materials/mask").
Objects that no container or resource path names are not listed; `address_of` gives any object's address.

"""

from __future__ import annotations

import re

from ..unity import scripttypes
from . import builtins, catalogdb, census, contract
from .contract import Cost, Input
from .layout import EXT_CLASSES
from .stages import Output, Pending, Stage

SCRIPTS = "nnnotes.scripts/1"
ADDRESSES = "nnnotes.addresses/3"
_CAB = re.compile(r"^CAB-[0-9a-f]+")


# ---------------------------------------------------------------- scripts
def script_entry(s: dict) -> str:
    """ "assembly|namespace|class" of a census MonoScript record (scripttypes.script_entry)."""
    return scripttypes.script_entry(s.get("assembly"), s.get("namespace"), s.get("class"))


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
    else the file name after its CAB-<hex> prefix ("" / ".sharedAssets"); "|<file>" where those coincide or a file
    has no such prefix."""
    files = list(files)
    if len(files) <= 1:
        return {f: "" for f in files}
    tags = {f: _CAB.sub("", f) for f in files}
    if len(set(tags.values())) < len(tags) or not all(_CAB.match(f) for f in files):
        tags = {f: "|" + f for f in files}
    return tags


def path_address(stable: str, tags: dict, file: str, pid: int) -> str:
    """The path-id stable address of object `pid` of serialized file `file` of bundle `stable` (`tags`: file_tags of
    the bundle's files)."""
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

    order = sorted(
        {(e["file"], e["pathId"]): e for e in members}.values(),
        key=lambda e: (cls(e) not in classes, e["file"], e["pathId"]),
    )
    out = [(contract.stable_address(stable, path), order[0])]
    names: dict[str, list[dict]] = {}
    for e in order[1:]:
        name = (info.get((e["file"], e["pathId"])) or {}).get("name") or ""
        names.setdefault(name.casefold(), []).append(e)
    for _folded, same in sorted(names.items()):
        for e in same:
            name = (info.get((e["file"], e["pathId"])) or {}).get("name") or ""
            if name and len(same) == 1:
                out.append((contract.stable_address(stable, path, name=name), e))
            else:
                out.append((path_address(stable, tags, e["file"], e["pathId"]), e))
    return out


def addresses(index: dict, censuses) -> dict:
    """The address table (nnnotes.addresses/1) of a catalog index and the censuses of its bundles and of the APK's
    built-in files: {stable bundle name or built-in subject: census document}, or (name, census document) pairs
    (read one at a time: only the container entries and the objects they name are kept). Censuses of other names
    the index does not list are ignored."""
    files_of = catalogdb.file_refs(index)
    listed = {b["stable"]: b for b in index["bundles"]}
    bundles, owners, info, groups, tags_of, resources = {}, {}, {}, {}, {}, {}
    builtin_docs = []
    problems = {"files": [], "bundleNames": [], "keys": []}
    pairs = sorted(censuses.items()) if isinstance(censuses, dict) else censuses
    for stable, doc in pairs:
        b = listed.get(stable)
        if b is None and builtins.is_builtin(stable):
            b = {"name": stable[len(builtins.PREFIX) :]}
            builtin_docs.append(doc)
        if b is None:
            continue
        names = [f["name"] for f in doc["files"]]
        for n in names:
            owners.setdefault(n, set()).add(stable)
        found = [a["assetBundleName"] for a in doc["assetBundles"] if a["assetBundleName"]]
        if b.get("bundleName") and found and b["bundleName"] + ".bundle" not in found:
            problems["bundleNames"].append({"bundle": stable, "catalog": b["bundleName"] + ".bundle", "census": found})
        g: dict[str, list[dict]] = {}
        for e in census.container(doc) + census.scenes(doc) + doc["resourcePaths"]:
            g.setdefault(e["path"].casefold(), []).append(e)
        for e in doc["resourcePaths"]:
            if e["file"] is not None:
                resources.setdefault(e["path"], []).append(contract.object_id(e["file"], e["pathId"]))
        wanted = {(e["file"], e["pathId"]) for es in g.values() for e in es if e["file"] is not None}
        for file, o in census.objects(doc):
            if (file, o["pathId"]) in wanted:
                info[(file, o["pathId"])] = o
        groups[stable], tags_of[stable] = g, file_tags(names)
        bundles[stable] = {
            "name": b["name"],
            "files": names,
            "assetBundleName": found[0] if found else None,
            "keys": set(),
        }
    cab = {n: min(s) for n, s in owners.items()}
    problems["files"] = [{"file": n, "bundles": sorted(s)} for n, s in sorted(owners.items()) if len(s) > 1]
    problems["bundleNames"].sort(key=lambda p: p["bundle"])
    objects = {}
    for stable, g in groups.items():
        for _, entries in sorted(g.items()):
            for addr, e in _container_addresses(stable, entries, info, tags_of[stable]):
                o = info.get((e["file"], e["pathId"])) or {}
                objects[addr] = {
                    "object": contract.object_id(e["file"], e["pathId"]),
                    "class": o.get("class"),
                    "name": o.get("name"),
                    "byteSize": o.get("byteSize"),
                    "typeHash": o.get("typeHash"),
                }
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
                entry["objects"] = [
                    contract.object_id(e["file"], e["pathId"]) for e in groups[home][folded] if e["file"] is not None
                ]
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
    return {
        "schema": ADDRESSES,
        "catalog": {n: cats[n]["sha256"] if n in cats else None for n in catalogdb.CATALOG_NAMES},
        "bundles": dict(sorted(bundles.items())),
        "files": dict(sorted(cab.items())),
        "keys": dict(sorted(keys.items())),
        "resources": dict(sorted(resources.items())),
        "builtins": builtin_selectors(builtin_docs),
        "objects": dict(sorted(objects.items())),
        "problems": problems,
    }


def builtin_selectors(docs) -> dict[str, list[str]]:
    """The `builtins` table of an address table: {selector: [object ids]} of the census documents `docs` of the
    built-in files (module documentation)."""
    docs = list(docs)
    names = {
        contract.object_id(s["file"], s["pathId"]): s.get("class")
        for doc in docs
        for s in doc["scripts"]
        if s.get("class")
    }
    out: dict[str, list[str]] = {}
    for doc in docs:
        for file, o in census.objects(doc):
            oid = contract.object_id(file, o["pathId"])
            if o["class"] == "MonoScript" or ("unit" in o and o["class"] != "MonoBehaviour"):
                continue
            if o["class"] == "MonoBehaviour":
                s = o.get("script")
                cls = names.get(contract.object_id(s["file"], s["pathId"])) if s and s.get("file") else None
                if cls is None:
                    continue
                selector = f"MonoBehaviour:{cls}"
            else:
                selector = o["class"]
            out.setdefault(selector, []).append(oid)
    return {k: sorted(v) for k, v in sorted(out.items())}


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
    return path_address(stable, file_tags(doc["bundles"][stable]["files"]), file, pid)


def reverse(doc: dict) -> dict[str, str]:
    """{object id: stable address} of the objects an address table lists (the first address of an object that
    several container paths name)."""
    out: dict[str, str] = {}
    for addr, o in doc["objects"].items():
        out.setdefault(o["object"], addr)
    return out


# ---------------------------------------------------------------- stages
def _subject(tid: str) -> str:
    return contract.parse_task_id(tid, census.CensusStage.name)[1]


def _census_input(env, tid: str) -> Input:
    return env.input_of(tid, "census", as_role="census:" + _subject(tid))


def _read(depot, task, role: str) -> dict:
    return contract.loads(depot.input_bytes(task.input(role)))


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
        return [
            _census_input(env, tid)
            for tid in env.tasks(census.CensusStage.name)
            if env.artifact(tid, "census")["semantics"]["facts"].get("scripts")
        ]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.05 + size / 50e6, (40 << 20) + 8 * size)

    def run(self, task, depot) -> Output:
        doc = scripts(_read(depot, task, i.role) for i in task.inputs)
        content = depot.add(contract.encode(doc), "json")
        rec = contract.artifact(
            contract.artifact_id(task.id, "scripts"),
            content,
            contract.provenance(task),
            {"kind": "link.scripts", "format": "json", "facts": {"scripts": len(doc["scripts"])}},
        )
        return Output([rec], [])


class AddressesStage(Stage):
    """link.addresses: a catalog index (input "index", the result of catalog.index:<subject>) and the censuses of its
    bundles and of the APK's built-in files (inputs "census:<stable name or subject>") -> the address table.
    Subjects: those of catalog.index (the fact "catalogs"). Waits for census tasks of its bundles that have not run;
    skips failed ones. Artifact "link.addresses:<subject>#addresses"."""

    name = "link.addresses"
    version = 5
    after = ("catalog.index", "unity.census")

    def subjects(self, env) -> list[str]:
        return sorted(env.fact("catalogs"))

    def inputs(self, subject: str, env) -> list[Input]:
        tid = contract.task_id(catalogdb.IndexStage.name, subject)
        idx = env.input_of(tid, "index")
        stables = {b["stable"] for b in env.depot.document(idx.sha256)["bundles"]}
        wanted = {contract.task_id(census.CensusStage.name, s) for s in stables}
        wanted |= {t for t in env.tasks(census.CensusStage.name) if builtins.is_builtin(_subject(t))}
        pending = [t for t in env.pending(census.CensusStage.name) if t in wanted]
        if pending:
            raise Pending(pending[0])
        return [idx] + [_census_input(env, t) for t in env.tasks(census.CensusStage.name) if t in wanted]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.5 + size / 50e6, (200 << 20) + size // 2)

    def run(self, task, depot) -> Output:
        idx = _read(depot, task, "index")
        docs = (
            (i.role.split(":", 1)[1], _read(depot, task, i.role)) for i in task.inputs if i.role.startswith("census:")
        )
        doc = addresses(idx, docs)
        content = depot.add(contract.encode(doc), "json")
        facts = {
            "bundles": len(doc["bundles"]),
            "keys": len(doc["keys"]),
            "resources": len(doc["resources"]),
            "builtins": len(doc["builtins"]),
            "objects": len(doc["objects"]),
            "problems": {k: len(v) for k, v in doc["problems"].items()},
        }
        rec = contract.artifact(
            contract.artifact_id(task.id, "addresses"),
            content,
            contract.provenance(task),
            {"kind": "link.addresses", "format": "json", "facts": facts},
        )
        return Output([rec], [])
