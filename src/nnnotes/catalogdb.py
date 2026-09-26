"""Catalog versions: the catalog index, a store of catalog versions, and the difference between two versions.

The catalog index (nnnotes.catalog-index/1) is everything a catalog pair says, decoded (addressables.parse_locations):
the remote catalog and, when given, the APK's local catalog. It holds every location (primary key, internal id,
provider, resource type, dependencies, extra data), the bundle and raw files the locations name with their
AssetBundleRequestOptions (content hash = the file name's `_<32 hex>`, CRC of the decompressed bundle data, size,
internal bundle name, flags), and the header facts of each catalog. Location ids are "<catalog>:<offset>" with
catalog "remote" or "apk". A file named by both catalogs takes the APK catalog's location (the remote catalog's
references to APK files may come from another build). The index is a function of the catalog bytes alone.

A catalog version is identified by the sha256 of its remote catalog and of its APK catalog (none without an APK).
The versions live in the store:

    <store>/catalogs/<sha256>.bin        each catalog file (remote and APK catalogs alike), by content
    <store>/catalogs/index.json          the versions (nnnotes.catalogs/1): id, labels, region, language, the two
                                         catalogs (sha256, size, buildResultHash), the CDN's `.hash` text when it was
                                         served (an opaque change signal), resource version, APK version name

`diff` compares two indexes: keys (the primary keys of asset locations: internal id, provider, resource type and
dependencies, bundles and raw files named by their stable names), bundles and raw files matched by stable name (the
name without its content hash: added, removed, changed with old and new name, size, hash, CRC) and the keys whose
dependency closure reaches a file that changed (the affected keys). Objects are compared by their stable addresses
(link.diff_addresses) when the address tables of both versions are given. Every list is sorted.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import contract
from .addressables import parse_header, parse_keys, parse_locations, remote_path
from .catalog import APK_CATALOG, Catalog, file_name, location_kind
from .contract import Cost, Input
from .stages import Output, Stage
from .store import write_file

CATALOGS = "nnnotes.catalogs/1"
DIFF = "nnnotes.catalog-diff/1"
CATALOG_NAMES = ("remote", "apk")
FILE_KINDS = ("bundle", "raw")
_RAW_HASH = re.compile(r"_[0-9a-f]{32}$")
FILE_FIELDS = ("name", "hash", "size", "crc", "bundleName")


# ---------------------------------------------------------------- index
def stable_raw_name(name: str) -> str:
    """A raw file's name without its content hash (`<path>_<32 hex>` -> `<path>`)."""
    return _RAW_HASH.sub("", name)


def _stable_names(names, stable) -> tuple[dict[str, str], list[list[str]]]:
    groups: dict[str, list[str]] = {}
    for n in sorted(set(names)):
        groups.setdefault(stable(n), []).append(n)
    out, collisions = {}, []
    for s, ns in groups.items():
        if len(ns) == 1:
            out[ns[0]] = s
        else:
            collisions.append(ns)
            out.update((n, n) for n in ns)
    return out, sorted(collisions)


def _location(catalog: str, e: dict) -> dict:
    return {"id": f"{catalog}:{e['offset']}", "primaryKey": e["primary_key"], "internalId": e["internal_id"],
            "kind": location_kind(e["internal_id"]), "provider": e["provider"], "type": e["resource_type"],
            "dependencies": [f"{catalog}:{d}" for d in e["dependencies"]], "dependencyHash": e["dependency_hash"],
            "extraData": e["extra_data"]}


def _file(loc: dict, all_ids: list[str], stable: str) -> dict:
    x = loc["extraData"] or {}
    return {"name": file_name(loc["internalId"]), "stable": stable,
            "remote": remote_path(loc["internalId"]) is not None, "location": loc["id"], "locations": all_ids,
            "hash": x.get("hash"), "bundleName": x.get("bundleName"), "crc": x.get("crc"), "size": x.get("bundleSize"),
            "flags": x.get("flags")}


def index(remote: bytes, apk: bytes | None = None) -> dict:
    """The catalog index (nnnotes.catalog-index/1) of a remote catalog and, optionally, an APK catalog."""
    catalogs, locations = {}, []
    for name, data in (("remote", remote), ("apk", apk)):
        if data is None:
            continue
        data = bytes(data)
        locs = [_location(name, e) for e in parse_locations(data)]
        catalogs[name] = {"sha256": contract.sha256(data), "size": len(data), "header": parse_header(data),
                          "keys": len(parse_keys(data)), "locations": len(locs)}
        locations += locs
    files: dict[tuple[str, str], list[dict]] = {}
    for loc in locations:
        if loc["kind"] in FILE_KINDS:
            files.setdefault((loc["kind"], file_name(loc["internalId"])), []).append(loc)
    out, collisions = {}, {}
    for kind, key, stable in (("bundle", "bundles", contract.stable_bundle_name), ("raw", "rawFiles", stable_raw_name)):
        names = sorted(n for k, n in files if k == kind)
        stables, collisions[key] = _stable_names(names, stable)
        entries = []
        for n in names:
            locs = files[(kind, n)]
            chosen = next((x for x in locs if x["id"].startswith("apk:")), locs[0])
            entries.append(_file(chosen, [x["id"] for x in locs], stables[n]))
        out[key] = entries
    return {"schema": contract.CATALOG_INDEX, "catalogs": catalogs, "locations": locations,
            "bundles": out["bundles"], "rawFiles": out["rawFiles"], "collisions": collisions}


def index_catalog(cat: Catalog) -> dict:
    """The index of an open Catalog (its remote catalog and, with an APK, the APK catalog)."""
    src = cat.sources()
    return index(src["remote"], src.get("apk"))


def summary(doc: dict) -> dict:
    """Counts of an index: locations per kind, bundles (remote / APK), raw files, collisions."""
    kinds: dict[str, int] = {}
    for loc in doc["locations"]:
        kinds[loc["kind"]] = kinds.get(loc["kind"], 0) + 1
    return {"locations": len(doc["locations"]), "kinds": dict(sorted(kinds.items())),
            "bundles": len(doc["bundles"]), "remoteBundles": sum(1 for b in doc["bundles"] if b["remote"]),
            "rawFiles": len(doc["rawFiles"]),
            "collisions": sum(len(g) for v in doc["collisions"].values() for g in v)}


def by_id(doc: dict) -> dict[str, dict]:
    """{location id: location} of an index."""
    return {loc["id"]: loc for loc in doc["locations"]}


def file_refs(doc: dict) -> dict[str, tuple[str, str]]:
    """{location id: (kind, stable name)} of every bundle and raw file location of an index."""
    out = {}
    for kind, key in (("bundle", "bundles"), ("raw", "rawFiles")):
        for f in doc[key]:
            for lid in f["locations"]:
                out[lid] = (kind, f["stable"])
    return out


def closures(doc: dict) -> dict[str, frozenset]:
    """{location id: the (kind, stable name) of every bundle and raw file its dependencies reach, itself included}."""
    locs, files = by_id(doc), file_refs(doc)
    memo: dict[str, frozenset] = {}

    def closure(lid: str) -> frozenset:
        hit = memo.get(lid)
        if hit is not None:
            return hit
        seen, stack, found = {lid}, [lid], set()
        while stack:
            cur = stack.pop()
            if cur in files:
                found.add(files[cur])
            for d in locs[cur]["dependencies"] if cur in locs else ():
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        memo[lid] = frozenset(found)
        return memo[lid]

    return {lid: closure(lid) for lid in locs}


# ---------------------------------------------------------------- diff
def _key_signatures(doc: dict) -> dict[str, list[dict]]:
    """{primary key: [location signature]} of the asset (and other non-file) locations of an index. Dependencies are
    named version-independently: files by kind and stable name, other locations by primary key."""
    locs, files = by_id(doc), file_refs(doc)

    def dep(lid: str) -> str:
        if lid in files:
            return f"{files[lid][0]}:{files[lid][1]}"
        return "key:" + locs[lid]["primaryKey"] if lid in locs else "missing:" + lid

    out: dict[str, list[dict]] = {}
    for loc in doc["locations"]:
        if loc["kind"] in FILE_KINDS:
            continue
        out.setdefault(loc["primaryKey"], []).append(
            {"internalId": loc["internalId"], "provider": loc["provider"], "type": loc["type"],
             "dependencies": [dep(d) for d in loc["dependencies"]]})
    for sigs in out.values():
        sigs.sort(key=contract.key_text)
    return out


def _fields(old: list[dict], new: list[dict]) -> list[str]:
    if len(old) != len(new):
        return ["locations"]
    return sorted({k for a, b in zip(old, new) for k in a if a[k] != b.get(k)})


def _files(doc: dict, key: str) -> dict[str, dict]:
    return {f["stable"]: f for f in doc[key]}


def _brief(f: dict) -> dict:
    return {k: f[k] for k in FILE_FIELDS}


def _diff_files(old: dict[str, dict], new: dict[str, dict]) -> dict:
    added = [_brief(new[s]) | {"stable": s} for s in sorted(set(new) - set(old))]
    removed = [_brief(old[s]) | {"stable": s} for s in sorted(set(old) - set(new))]
    changed, same = [], 0
    for s in sorted(set(old) & set(new)):
        a, b = _brief(old[s]), _brief(new[s])
        fields = [k for k in FILE_FIELDS if a[k] != b[k]]
        if fields:
            changed.append({"stable": s, "fields": fields, "old": a, "new": b})
        else:
            same += 1
    return {"added": added, "removed": removed, "changed": changed, "unchanged": same}


def diff(old: dict, new: dict, old_addresses: dict | None = None, new_addresses: dict | None = None) -> dict:
    """The difference between two catalog indexes (nnnotes.catalog-diff/1), and between their objects when the
    address tables (link.addresses) of both are given."""
    ko, kn = _key_signatures(old), _key_signatures(new)
    keys = {"added": sorted(set(kn) - set(ko)), "removed": sorted(set(ko) - set(kn)), "changed": []}
    for k in sorted(set(ko) & set(kn)):
        if ko[k] != kn[k]:
            keys["changed"].append({"key": k, "fields": _fields(ko[k], kn[k]), "old": ko[k], "new": kn[k]})
    files = {key: _diff_files(_files(old, key), _files(new, key)) for key in ("bundles", "rawFiles")}
    touched_new, touched_old = set(), set()
    for key, kind in (("bundles", "bundle"), ("rawFiles", "raw")):
        d = files[key]
        touched_new |= {(kind, f["stable"]) for f in d["added"] + d["changed"]}
        touched_old |= {(kind, f["stable"]) for f in d["removed"] + d["changed"]}
    affected = set(keys["added"]) | set(keys["removed"]) | {c["key"] for c in keys["changed"]}
    for doc, touched in ((new, touched_new), (old, touched_old)):
        if not touched:
            continue
        reach = closures(doc)
        for loc in doc["locations"]:
            if loc["kind"] not in FILE_KINDS and reach[loc["id"]] & touched:
                affected.add(loc["primaryKey"])
    out = {"schema": DIFF, "old": _identity(old), "new": _identity(new), "keys": keys,
           "bundles": files["bundles"], "rawFiles": files["rawFiles"], "affectedKeys": sorted(affected)}
    if old_addresses is not None and new_addresses is not None:
        from .link import diff_addresses
        out["objects"] = diff_addresses(old_addresses, new_addresses)
    out["summary"] = diff_summary(out)
    return out


def _identity(doc: dict) -> dict:
    return {name: doc["catalogs"][name]["sha256"] if name in doc["catalogs"] else None for name in CATALOG_NAMES}


def diff_summary(d: dict) -> dict:
    out = {"keys": {k: len(v) for k, v in d["keys"].items()}, "affectedKeys": len(d["affectedKeys"])}
    for key in ("bundles", "rawFiles"):
        out[key] = {k: (v if isinstance(v, int) else len(v)) for k, v in d[key].items()}
    if "objects" in d:
        out["objects"] = d["objects"]["summary"]
    return out


def format_diff(d: dict) -> str:
    """A diff as text: one line per added (+), removed (-) or changed (~) key and file, then the counts."""
    lines = []
    for k in d["keys"]["added"]:
        lines.append(f"+ key {k}")
    for k in d["keys"]["removed"]:
        lines.append(f"- key {k}")
    for c in d["keys"]["changed"]:
        lines.append(f"~ key {c['key']} ({', '.join(c['fields'])})")
    for key, label in (("bundles", "bundle"), ("rawFiles", "raw")):
        for f in d[key]["added"]:
            lines.append(f"+ {label} {f['stable']} {f['name']} {f['size']}")
        for f in d[key]["removed"]:
            lines.append(f"- {label} {f['stable']} {f['name']} {f['size']}")
        for c in d[key]["changed"]:
            lines.append(f"~ {label} {c['stable']} {c['old']['name']} -> {c['new']['name']} "
                         f"({', '.join(c['fields'])})")
    objects = d.get("objects", {})
    lines += [f"+ object {a}" for a in objects.get("added", [])]
    lines += [f"- object {a}" for a in objects.get("removed", [])]
    lines += [f"~ object {o['address']} ({', '.join(o['fields'])})" for o in objects.get("changed", [])]
    s = d["summary"]
    lines.append(f"# keys +{s['keys']['added']} -{s['keys']['removed']} ~{s['keys']['changed']}; "
                 f"bundles +{s['bundles']['added']} -{s['bundles']['removed']} ~{s['bundles']['changed']} "
                 f"={s['bundles']['unchanged']}; raw files +{s['rawFiles']['added']} -{s['rawFiles']['removed']} "
                 f"~{s['rawFiles']['changed']} ={s['rawFiles']['unchanged']}; affected keys {s['affectedKeys']}")
    if "objects" in s:
        o = s["objects"]
        lines.append(f"# objects +{o['added']} -{o['removed']} ~{o['changed']} rebuilt {o['rebuilt']} "
                     f"={o['unchanged']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the store of versions
def version_id(remote_sha: str, apk_sha: str | None) -> str:
    """The id of a catalog version: the key hash of its two catalogs' content ids."""
    return contract.digest({"remote": remote_sha, "apk": apk_sha})


def default_label(remote_sha: str, resource_version: str | None = None) -> str:
    """The label of an imported version when none is given: the game's resource version when known, else the first
    12 hex digits of the remote catalog's sha256."""
    return resource_version or remote_sha[:12]


def apk_catalog(apk) -> bytes:
    """The local catalog inside an APK."""
    with zipfile.ZipFile(apk) as z:
        return z.read(APK_CATALOG)


def fetch(cdn: str, language: str, timeout: float = 120) -> tuple[bytes, str | None]:
    """The remote catalog of `language` from a CDN base, and the text of its `.hash` file (None when not served)."""
    base = cdn.rstrip("/") + "/asset/Android/" + Catalog.cache_file(language, Path(".")).name
    with urllib.request.urlopen(base, timeout=timeout) as r:
        data = r.read()
    try:
        with urllib.request.urlopen(base[:-len(".bin")] + ".hash", timeout=timeout) as r:
            text = r.read().decode("ascii", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError):
        text = None
    return data, text or None


class CatalogDB:
    """The catalog versions of a store (<store>/catalogs/)."""

    def __init__(self, store_root):
        self.root = Path(store_root) / "catalogs"

    def path(self, sha: str) -> Path:
        if not contract.is_sha256(sha):
            raise ValueError(f"not a sha256: {sha!r}")
        return self.root / f"{sha}.bin"

    def _load(self) -> list[dict]:
        try:
            doc = contract.loads((self.root / "index.json").read_bytes())
        except FileNotFoundError:
            return []
        if doc.get("schema") != CATALOGS:
            raise ValueError(f"{self.root / 'index.json'} is not a {CATALOGS} document")
        return doc["versions"]

    def _save(self, versions: list[dict]) -> None:
        write_file(self.root / "index.json", contract.encode({"schema": CATALOGS, "versions": versions}))

    def versions(self, region: str | None = None, language: str | None = None) -> list[dict]:
        """The versions in import order (of `region` / `language` when given)."""
        return [v for v in self._load() if (region is None or v["region"] == region)
                and (language is None or v["language"] == language)]

    def _put(self, data: bytes) -> dict:
        sha = contract.sha256(data)
        p = self.path(sha)
        if not (p.is_file() and p.stat().st_size == len(data)):
            write_file(p, data)
        return {"sha256": sha, "size": len(data), "buildResultHash": parse_header(data)["buildResultHash"]}

    def add(self, remote: bytes, apk: bytes | None = None, *, label: str | None = None, region: str | None = None,
            language: str | None = None, hash_text: str | None = None, resource_version: str | None = None,
            apk_version_name: str | None = None) -> dict:
        """Import a catalog pair (idempotent: a known pair gets the label and the facts it did not have yet); the
        version. A label names one version per region and language."""
        remote_rec = self._put(bytes(remote))
        apk_rec = self._put(bytes(apk)) if apk is not None else None
        vid = version_id(remote_rec["sha256"], apk_rec["sha256"] if apk_rec else None)
        versions = self._load()
        v = next((x for x in versions if x["id"] == vid), None)
        if v is None:
            v = {"id": vid, "seq": max((x["seq"] for x in versions), default=0) + 1, "labels": [],
                 "region": region, "language": language, "remote": remote_rec, "apk": apk_rec, "hash": None,
                 "resourceVersion": None, "apkVersionName": None}
            versions.append(v)
        for k, given in (("region", region), ("language", language), ("hash", hash_text),
                         ("resourceVersion", resource_version), ("apkVersionName", apk_version_name)):
            if given is not None and v[k] is None:
                v[k] = given
        label = label or default_label(remote_rec["sha256"], resource_version)
        for other in versions:
            if other is not v and label in other["labels"] and (other["region"], other["language"]) == (
                    v["region"], v["language"]):
                raise ValueError(f"label {label!r} already names catalog version {other['id'][:12]}")
        if label not in v["labels"]:
            v["labels"].append(label)
        self._save(versions)
        return v

    def get(self, ref: str, region: str | None = None, language: str | None = None) -> dict:
        """The version a label, a version id prefix or a remote catalog sha256 prefix names (of `region` /
        `language` when given); "latest" is the last imported. KeyError when none, ValueError when several do."""
        versions = self.versions(region, language)
        if ref == "latest":
            if not versions:
                raise KeyError("no catalog version imported")
            return versions[-1]
        hits = [v for v in versions if ref in v["labels"]]
        if not hits and re.fullmatch(r"[0-9a-f]{4,64}", ref):
            hits = [v for v in versions if v["id"].startswith(ref) or v["remote"]["sha256"].startswith(ref)]
        if not hits:
            raise KeyError(f"no catalog version {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"{ref!r} names {len(hits)} catalog versions: "
                             + ", ".join(f"{v['id'][:12]} ({v['region']}/{v['language']})" for v in hits))
        return hits[0]

    def catalog_bytes(self, version: dict) -> tuple[bytes, bytes | None]:
        """(remote catalog, APK catalog or None) of a version."""
        remote = self.path(version["remote"]["sha256"]).read_bytes()
        apk = self.path(version["apk"]["sha256"]).read_bytes() if version["apk"] else None
        return remote, apk

    def index(self, version: dict) -> dict:
        return index(*self.catalog_bytes(version))

    def inputs(self, version: dict) -> list[Input]:
        """The inputs of the catalog.index task of a version (roles "remote" and "apk"), read from this store."""
        out = []
        for role in CATALOG_NAMES:
            rec = version[role]
            if rec:
                out.append(Input(role, rec["sha256"], rec["size"], f"{rec['sha256']}.bin",
                                 ({"kind": "file", "path": str(self.path(rec["sha256"]))},)))
        return out


# ---------------------------------------------------------------- stage
class IndexStage(Stage):
    """catalog.index: a catalog pair -> its index. Subjects and inputs come from the fact "catalogs": {subject:
    [Input "remote", Input "apk" (optional)]} (CatalogDB.inputs). Artifact "<task id>#index"."""
    name = "catalog.index"
    version = 1

    def subjects(self, env) -> list[str]:
        return sorted(env.fact("catalogs"))

    def inputs(self, subject: str, env) -> list[Input]:
        ins = list(env.fact("catalogs")[subject])
        roles = sorted(i.role for i in ins)
        if "remote" not in roles or not set(roles) <= set(CATALOG_NAMES):
            raise ValueError(f"catalog.index {subject}: inputs {roles}, expected remote (+ apk)")
        return ins

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.2 + size / 10e6, (60 << 20) + 40 * size)

    def run(self, task, store) -> Output:
        roles = {i.role for i in task.inputs}
        doc = index(store.input_bytes(task.input("remote")),
                    store.input_bytes(task.input("apk")) if "apk" in roles else None)
        content = store.add(contract.encode(doc), "json")
        rec = contract.artifact(contract.artifact_id(task.id, "index"), content, contract.provenance(task),
                                {"kind": "catalog.index", "format": "json", "facts": summary(doc)})
        return Output([rec], [])
