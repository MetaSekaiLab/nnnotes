"""The contract of the asset stages: canonical JSON, identities, keys and the documents stages exchange.

Everything here is a public format (docs/contracts.md, JSON Schemas in docs/schema/):

    canon/1        UTF-8, LF, one trailing newline, non-ASCII as is. Contract documents: keys sorted, indent 1.
                   Object documents (typetree JSON): the object's own field order, indent 1. Non-finite floats as
                   `1e999` / `-1e999` (jsonio), NaN as {"$float": "nan"}.
    content id     sha256 of the bytes (hex).
    key hash       sha256 of the compact canonical text (ASCII only, keys sorted) of a JSON value.
    object id      "<serialized file>:<pathId>" (CAB-2f1e...:-4153...; built-in files keep their name:
                   "unity default resources:10"). A bundle's serialized file name changes with its content, so an
                   object id names one content version; stable_address names the object across versions.
    artifact id    "<object id>#<role>" for an object's artifact, "<stage>:<subject>#<role>" for any other.
    task id        "<stage>:<subject>"; bundle stages use the stable bundle name as subject, so a task keeps its id
                   across catalog versions while its key follows the content.

A task is one stage applied to one subject. Its key covers exactly what its output depends on: the stage name and
version, the subject (it names the task's artifacts of no object), the output-affecting parameters (defaults
filled), the implementation ids of the atoms it uses, the roles and content ids of its inputs, and the context
entries it consumes. Input names, locators and the cost estimate never enter it, so the same subject with the same
content in another catalog version or region is the same task. A stage whose output depends on anything else puts
it into its parameters or context.

The result of a task (its action-cache entry) is therefore a function of its key alone: it holds no input name,
locator, timestamp or traceback (input names are in the task description; region, language and catalog and master
versions in the run manifest), so identical bundles of two regions or catalog versions share one result.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cached_property

from . import jsonio

CANON = "canon/1"
TASK = "nnnotes.task/1"
ARTIFACT = "nnnotes.artifact/1"
RESULT = "nnnotes.result/1"
RUN = "nnnotes.run/1"
LAYOUT = "nnnotes.layout/1"
COVERAGE = "nnnotes.coverage/1"
FAILURES = "nnnotes.failures/1"
PLAN = "nnnotes.plan/1"
COST = "nnnotes.cost/1"
CATALOG_INDEX = "nnnotes.catalog-index/1"
CENSUS = "nnnotes.census/1"
VIEW = "nnnotes.view/1"

NAN = {"$float": "nan"}
# item statuses of a result: every object a task covers has exactly one
ITEM_STATUSES = ("exported", "contained", "generic", "unsupported", "failed")
RESULT_STATUSES = ("ok", "partial")
RUN_TASK_STATUSES = ("hit", "ran", "failed")
# locator kinds of a task input: where the bytes can be read (never part of the key)
LOCATOR_KINDS = ("store", "cache", "file", "catalog")
# an object's primary artifact is the one whose role comes first here; its other artifacts are secondary
PRIMARY_ROLES = ("image", "mesh", "audio", "video", "font", "data", "prefab", "json")
MEDIA_TYPES = {
    "png": "image/png", "astc": "image/astc", "jpg": "image/jpeg", "json": "application/json",
    "glb": "model/gltf-binary", "flac": "audio/flac", "wav": "audio/wav", "ogg": "audio/ogg", "mkv": "video/x-matroska",
    "ivf": "video/x-ivf", "adx": "audio/x-adx", "ttf": "font/ttf", "otf": "font/otf", "ttc": "font/collection",
    "woff": "font/woff", "woff2": "font/woff2", "txt": "text/plain", "xml": "application/xml", "csv": "text/csv",
    "bin": "application/octet-stream", "bytes": "application/octet-stream",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXT = re.compile(r"[0-9a-z]+")
_BUNDLE_SUFFIX = re.compile(r"(_[0-9a-f]{32})?\.bundle$")


# ---------------------------------------------------------------- canonical JSON
def _tag_nan(v):
    """`v` with every NaN replaced by {"$float": "nan"}; containers are copied only where something changed."""
    if isinstance(v, float):
        return dict(NAN) if v != v else v
    if isinstance(v, dict):
        out = None
        for k, x in v.items():
            y = _tag_nan(x)
            if y is not x:
                if out is None:
                    out = dict(v)
                out[k] = y
        return v if out is None else out
    if isinstance(v, (list, tuple)):
        out = None
        for i, x in enumerate(v):
            y = _tag_nan(x)
            if y is not x:
                if out is None:
                    out = list(v)
                out[i] = y
        return v if out is None else out
    return v


def _text(obj, **kw) -> str:
    try:
        return jsonio.dumps(obj, **kw)
    except ValueError:
        tagged = _tag_nan(obj)
        if tagged is obj:
            raise                                  # not a NaN: the writer's own error
    return jsonio.dumps(tagged, **kw)


def dumps(obj, *, sort_keys: bool = True) -> str:
    """The canon/1 text of a document: keys sorted (`sort_keys=False` keeps the document's order, for object
    documents), indent 1, non-ASCII as is, one trailing newline."""
    return _text(obj, indent=1, ensure_ascii=False, sort_keys=sort_keys) + "\n"


def encode(obj, *, sort_keys: bool = True) -> bytes:
    """dumps() as UTF-8 bytes."""
    return dumps(obj, sort_keys=sort_keys).encode("utf-8")


def encode_chunks(obj, size: int = 1 << 20):
    """encode(obj) as byte strings of about `size` bytes, made as they are written (a large document's text is
    never whole in memory, nor the encoder's list of its pieces). Raises ValueError, possibly after some chunks,
    for a document with a non-finite number: encode() writes those."""
    enc = json.JSONEncoder(indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False)
    buf, n = [], 0
    for piece in enc.iterencode(obj):
        buf.append(piece)
        n += len(piece)
        if n >= size:
            yield "".join(buf).encode("utf-8")
            buf, n = [], 0
    buf.append("\n")
    yield "".join(buf).encode("utf-8")


def _untag(d: dict):
    return float("nan") if d == NAN else d


def loads(text):
    """A canon/1 (or any JSON) document: {"$float": "nan"} reads back as NaN, 1e999 as infinity."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    return json.loads(text, object_hook=_untag)


def key_text(value) -> str:
    """The compact canonical text a key hash is taken of: keys sorted, ASCII only, no whitespace."""
    return _text(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def digest(value) -> str:
    """The key hash of a JSON value (sha256 hex of key_text)."""
    return hashlib.sha256(key_text(value).encode("ascii")).hexdigest()


def sha256(data) -> str:
    """The content id of bytes."""
    return hashlib.sha256(data).hexdigest()


def is_sha256(s) -> bool:
    return isinstance(s, str) and _SHA256.fullmatch(s) is not None


# ---------------------------------------------------------------- identities
def object_id(file: str, path_id: int) -> str:
    return f"{file}:{int(path_id)}"


def parse_object_id(oid: str) -> tuple[str, int]:
    """(serialized file, pathId) of an object id."""
    file, sep, pid = oid.rpartition(":")
    if not sep or not file:
        raise ValueError(f"not an object id: {oid!r}")
    return file, int(pid)


def artifact_id(owner: str, role: str) -> str:
    """The id of the artifact `role` of `owner` (an object id, or a task id for artifacts of no object)."""
    if "#" in owner:
        raise ValueError(f"artifact owner {owner!r} contains '#'")
    if not role:
        raise ValueError("empty artifact role")
    return f"{owner}#{role}"


def parse_artifact_id(aid: str) -> tuple[str, str]:
    """(owner, role): the owner never contains '#', the role may."""
    owner, sep, role = aid.partition("#")
    if not sep or not owner or not role:
        raise ValueError(f"not an artifact id: {aid!r}")
    return owner, role


def task_id(stage: str, subject: str) -> str:
    return f"{stage}:{subject}"


def parse_task_id(tid: str, stage: str | None = None) -> tuple[str, str]:
    """(stage, subject). Stage names contain no ':'; subjects may."""
    if stage is not None:
        if not tid.startswith(stage + ":"):
            raise ValueError(f"task id {tid!r} is not of stage {stage!r}")
        return stage, tid[len(stage) + 1:]
    name, sep, subject = tid.partition(":")
    if not sep or not name:
        raise ValueError(f"not a task id: {tid!r}")
    return name, subject


def stable_bundle_name(name: str) -> str:
    """A bundle's file name without its content hash and `.bundle` (membercard_x_3fa...c2.bundle -> membercard_x):
    the same across catalog versions while the content changes."""
    return _BUNDLE_SUFFIX.sub("", name)


def stable_bundle_names(names) -> tuple[dict[str, str], list[list[str]]]:
    """{file name: stable name} for a catalog's bundles, and the collisions: names whose stable names coincide keep
    their full file name (each collision group sorted, groups sorted)."""
    groups: dict[str, list[str]] = {}
    for n in sorted(set(names)):
        groups.setdefault(stable_bundle_name(n), []).append(n)
    out, collisions = {}, []
    for stable, ns in groups.items():
        if len(ns) == 1:
            out[ns[0]] = stable
        else:
            collisions.append(ns)
            out.update((n, n) for n in ns)
    return out, sorted(collisions)


def stable_address(bundle: str, container: str | None = None, path_id: int | None = None,
                   name: str | None = None) -> str:
    """Where an object is found again in another catalog version. A bundle's serialized file names (and so its
    object and artifact ids) change with its content while path ids stay; the address uses what stays: the stable
    bundle name and the container path ("<bundle>/<container>", "[<name>]" appended for a sub-object of the
    container), else the path id ("<bundle>:<pathId>")."""
    if container:
        return f"{bundle}/{container}" + (f"[{name}]" if name else "")
    if path_id is None:
        raise ValueError("an object without a container is addressed by its path id")
    return f"{bundle}:{int(path_id)}"


def media_type(ext: str) -> str:
    return MEDIA_TYPES.get(ext.lower(), "application/octet-stream")


# ---------------------------------------------------------------- tasks
@dataclass(frozen=True)
class Cost:
    """An estimate (or measurement) of a task: CPU seconds and peak memory in bytes."""
    cpu_seconds: float = 0.0
    peak_bytes: int = 0

    def to_json(self) -> dict:
        return {"cpuSeconds": round(float(self.cpu_seconds), 3), "peakBytes": int(self.peak_bytes)}

    @classmethod
    def from_json(cls, d: dict | None) -> "Cost | None":
        return None if d is None else cls(float(d.get("cpuSeconds", 0.0)), int(d.get("peakBytes", 0)))


@dataclass(frozen=True)
class Input:
    """One input of a task: its role, content id and size; `name` and `locators` (where to read it) are
    information for running it, not part of the key."""
    role: str
    sha256: str
    size: int
    name: str | None = None
    locators: tuple = ()

    def __post_init__(self):
        if not is_sha256(self.sha256):
            raise ValueError(f"input {self.role!r}: not a sha256: {self.sha256!r}")
        for loc in self.locators:
            if loc.get("kind") not in LOCATOR_KINDS:
                raise ValueError(f"input {self.role!r}: unknown locator {loc!r}")

    def to_json(self) -> dict:
        d = {"role": self.role, "sha256": self.sha256, "size": int(self.size)}
        if self.name is not None:
            d["name"] = self.name
        d["locators"] = [dict(loc) for loc in self.locators]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Input":
        return cls(d["role"], d["sha256"], int(d["size"]), d.get("name"),
                   tuple(dict(loc) for loc in d.get("locators", ())))


class IncompatibleTask(ValueError):
    """A task description this installation cannot run as described (key mismatch, unknown stage or version)."""


@dataclass(frozen=True)
class Task:
    """One stage applied to one subject (nnnotes.task/1)."""
    stage: str
    version: int
    subject: str
    params: dict = field(default_factory=dict)
    atoms: dict = field(default_factory=dict)
    inputs: tuple = ()
    context: dict = field(default_factory=dict)
    cost: Cost | None = None

    def __post_init__(self):
        if not self.stage or ":" in self.stage or "#" in self.stage:
            raise ValueError(f"bad stage name {self.stage!r}")
        if "#" in self.subject:
            raise ValueError(f"task subject {self.subject!r} contains '#'")
        ins = tuple(sorted(self.inputs, key=lambda i: (i.role, i.sha256)))
        roles = [i.role for i in ins]
        if len(set(roles)) != len(roles):
            raise ValueError(f"task {self.id}: duplicate input roles")
        object.__setattr__(self, "inputs", ins)

    @property
    def id(self) -> str:
        return task_id(self.stage, self.subject)

    def key_parts(self) -> dict:
        """What the key is the hash of."""
        return {"stage": {"name": self.stage, "version": self.version}, "subject": self.subject,
                "params": self.params, "atoms": self.atoms, "inputs": [[i.role, i.sha256] for i in self.inputs],
                "context": self.context}

    @cached_property
    def key(self) -> str:
        return digest(self.key_parts())

    def input(self, role: str) -> Input:
        for i in self.inputs:
            if i.role == role:
                return i
        raise KeyError(f"task {self.id} has no input {role!r}")

    def params_digest(self) -> str:
        return digest(self.params)

    def to_json(self) -> dict:
        d = {"schema": TASK, "id": self.id, "stage": {"name": self.stage, "version": self.version},
             "params": self.params, "atoms": self.atoms, "inputs": [i.to_json() for i in self.inputs],
             "context": self.context, "key": self.key}
        if self.cost is not None:
            d["cost"] = self.cost.to_json()
        return d

    @classmethod
    def from_json(cls, doc: dict) -> "Task":
        """The task a description holds; raises IncompatibleTask when it is not a task/1 document or its key is
        not the key of its parts (it was written by another version or edited)."""
        if not isinstance(doc, dict) or doc.get("schema") != TASK:
            raise IncompatibleTask(f"not a {TASK} document")
        try:
            name, version = doc["stage"]["name"], int(doc["stage"]["version"])
            _, subject = parse_task_id(doc["id"], name)
            t = cls(name, version, subject, doc.get("params", {}), doc.get("atoms", {}),
                    tuple(Input.from_json(i) for i in doc.get("inputs", ())), doc.get("context", {}),
                    Cost.from_json(doc.get("cost")))
        except (KeyError, TypeError, ValueError) as e:
            raise IncompatibleTask(f"malformed task: {e}") from None
        if doc.get("key") != t.key:
            raise IncompatibleTask(f"task {t.id}: key {doc.get('key')} is not the key of its parts ({t.key})")
        return t


# ---------------------------------------------------------------- artifacts and results
def content(data: bytes, ext: str, media: str | None = None) -> dict:
    """The content part of an artifact record for `data`."""
    ext = ext.lower().lstrip(".")
    return {"sha256": sha256(data), "size": len(data), "mediaType": media or media_type(ext), "ext": ext}


def provenance(task: Task, *, atoms: dict | None = None, obj: dict | None = None) -> dict:
    """The provenance of an artifact a task produced: its stage (with the digest of the parameters), the atoms that
    made it (default: all the task's atoms), the roles and content ids of the task's inputs, and the object facts."""
    p = {"stage": {"name": task.stage, "version": task.version, "params": task.params_digest()},
         "atoms": dict(task.atoms if atoms is None else atoms),
         "inputs": [{"role": i.role, "sha256": i.sha256} for i in task.inputs]}
    if obj is not None:
        p["object"] = obj
    return p


def artifact(aid: str, content_: dict, provenance_: dict, semantics: dict) -> dict:
    """An artifact record (nnnotes.artifact/1)."""
    parse_artifact_id(aid)
    for k in ("sha256", "size", "mediaType", "ext"):
        if k not in content_:
            raise ValueError(f"artifact {aid}: content without {k}")
    if not _EXT.fullmatch(content_["ext"]):
        raise ValueError(f"artifact {aid}: extension {content_['ext']!r} is not lower-case letters and digits")
    if "kind" not in semantics:
        raise ValueError(f"artifact {aid}: semantics without kind")
    return {"id": aid, "content": content_, "provenance": provenance_, "semantics": semantics}


def reason(code: str, message: str = "") -> dict:
    """Why an object is generic, unsupported or failed: a documented code and a deterministic message."""
    if not code:
        raise ValueError("empty reason code")
    return {"code": code, "message": message}


def item(obj: str, status: str, *, artifacts=(), within: str | None = None, why: dict | None = None,
         cls: str | None = None) -> dict:
    """The outcome for one object of a task. exported: its artifacts; contained: `within` (the artifact it is part
    of); generic: its artifacts and why the specialized form was not made; unsupported: why (and artifacts, if
    any); failed: why."""
    if status not in ITEM_STATUSES:
        raise ValueError(f"item {obj}: unknown status {status!r}")
    need_art = status in ("exported", "generic")
    need_why = status in ("generic", "unsupported", "failed")
    if need_art and not artifacts:
        raise ValueError(f"item {obj}: {status} without artifacts")
    if need_why != (why is not None):
        raise ValueError(f"item {obj}: {status} {'needs' if need_why else 'takes no'} reason")
    if (status == "contained") != (within is not None):
        raise ValueError(f"item {obj}: `in` is for contained items only")
    if status in ("contained", "failed") and artifacts:
        raise ValueError(f"item {obj}: {status} with artifacts")
    d = {"object": obj, "status": status}
    if cls is not None:
        d["class"] = cls
    if artifacts:
        d["artifacts"] = sorted(artifacts)
    if within is not None:
        d["in"] = within
    if why is not None:
        d["reason"] = why
    return d


def result(task: Task, artifacts, items) -> dict:
    """The result document (nnnotes.result/1) of a task: the commit point of its run. Artifacts sorted by id, items
    by object; status `partial` when an item failed."""
    arts = sorted(artifacts, key=lambda a: a["id"])
    ids = [a["id"] for a in arts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"task {task.id}: duplicate artifact ids")
    its = sorted(items, key=lambda i: i["object"])
    objs = [i["object"] for i in its]
    if len(set(objs)) != len(objs):
        raise ValueError(f"task {task.id}: more than one item for an object")
    known = set(ids)
    for i in its:
        for a in i.get("artifacts", ()):
            if a not in known:
                raise ValueError(f"task {task.id}: item {i['object']} names unknown artifact {a}")
    status = "partial" if any(i["status"] == "failed" for i in its) else "ok"
    return {"schema": RESULT, "key": task.key, "keyParts": task.key_parts(), "status": status,
            "artifacts": arts, "items": its}


def primary_artifact(records: list[dict]) -> dict:
    """The primary artifact of one object's records (PRIMARY_ROLES order, then role name)."""
    def rank(r):
        role = parse_artifact_id(r["id"])[1]
        return (PRIMARY_ROLES.index(role) if role in PRIMARY_ROLES else len(PRIMARY_ROLES), role)
    return min(records, key=rank)


# ---------------------------------------------------------------- runs and layouts
def run_id(doc: dict) -> str:
    """A run's id: the hash of its deterministic parts (context, selection, parameters, stage versions, the tasks'
    ids and keys, layout manifests); statuses and the summary are not part of it."""
    return digest({"context": doc.get("context", {}), "selection": doc.get("selection", []),
                   "params": doc.get("params", {}), "stages": doc.get("stages", {}),
                   "tasks": [[t["id"], t["key"]] for t in doc.get("tasks", [])],
                   "layouts": doc.get("layouts", {})})


def run_doc(*, context: dict, selection: list, params: dict, stages: dict, tasks: list[dict], layouts: dict,
            summary: dict) -> dict:
    """A run manifest (nnnotes.run/1): tasks sorted by id."""
    doc = {"schema": RUN, "context": context, "selection": list(selection), "params": params, "stages": stages,
           "tasks": sorted(tasks, key=lambda t: t["id"]), "layouts": layouts, "summary": summary}
    for t in doc["tasks"]:
        if t["status"] not in RUN_TASK_STATUSES:
            raise ValueError(f"run task {t['id']}: unknown status {t['status']!r}")
    doc["id"] = run_id(doc)
    return doc


def layout_doc(name: str, params: dict, entries: list[dict]) -> dict:
    """A layout manifest (nnnotes.layout/1): entries sorted by path, then id."""
    return {"schema": LAYOUT, "name": name, "params": params,
            "entries": sorted(entries, key=lambda e: (e["path"], e["id"]))}
