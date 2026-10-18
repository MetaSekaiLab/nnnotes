"""Product stages: products assembled from the unity.export results of the objects they reach, never from a bundle.

A product (a Live2D model, a story, a live, ...) is a stage whose task per subject starts from root objects: the
objects of the catalog keys `keys(subject)` gives, of the Resources.Load paths `resources(subject)` gives, of the
built-in selectors `builtins(subject)` gives (objects of the APK's built-in files by class or script class) and the
objects `root_objects(subject)` names (all read from the planning facts alone, so a command can ask for the units its
products need before anything runs: `request`). The objects it reaches are those of the units holding its roots and
every unit an item of a reached unit references (contract.item `refs`; an atlas reached through a reference does not
reach the sprites it packs, units.PACKED), through the unity.export results; a sprite whose atlas is in another
bundle has its image from sprite.crop. A reached unit that the request did not export is an error of the request; a
referenced object that no census holds is listed as missing. A referenced object of a fenced bundle (units.fences) that
the walk does not reach is listed as fenced.

A task's inputs are the object table of what it reaches ("objects", nnnotes.objects/1: every object with its class,
name, item status and files, the contained ones with the object holding them, the root objects of each of its keys,
and the font objects the fence cuts off as "fenced"),
the APK files the stage names in `APK` ("apk:<path in the APK>", from the fact "apk"), the cue sheets of the catalog
keys `cue_sheets(subject)` gives, the movies of the catalog keys `movies(subject)` gives and what `more_inputs` adds (a
master record as "master": master_input; parameters' data); it depends on the unity.export and sprite.crop tasks it
read and the cri.audio and cri.movie tasks of its cue sheets and movies. A cue sheet is the ACB the key loads, a movie
the USM: held in a bundle (the `acb` / `usm` file of an object of the key) or a raw file of the catalog (a dependency
of the key that cri.audio / cri.movie decoded); the inputs are that file ("acb:<key>" / "usm:<key>") and, in "sheets"
/ "movies", the files the stage made of it ({key: {acb or usm, status, reason?, files}}; a movie's files also carry
their facts).

`assemble` makes the product's own files from the table, the bytes of its objects, its cue sheets (Sheet) and movies
(Movie); a file may be content the depot already holds (Stored: a decoded waveform, a movie), recorded without reading
it again. The result holds them as "<task id>#<path>", the table of the objects they use as "<task id>#objects", and one
item for the subject. A product layout (layout.product_entries) places the files and the objects' files and writes the
product's manifest.
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

EXPORT, CROP, AUDIO, MOVIE = "unity.export", "sprite.crop", "cri.audio", "cri.movie"
ADDRESSES = units.ADDRESSES
TABLE_KIND = "product.objects"
SHEETS_INPUT, MOVIES_INPUT = "sheets", "movies"
MASTER_INPUT = "master"


@dataclass(frozen=True)
class Decodes:
    """How a product reads the results of a CRI stage: the file role of the content in the object table and of its
    input ("<role>:<key>"), the stage that decodes it, the input that lists the stage's files, what the content is
    called and whether the files carry their facts."""

    role: str
    stage: str
    input: str
    what: str
    facts: bool = False


SHEETS = Decodes("acb", AUDIO, SHEETS_INPUT, "cue sheet")
MOVIES = Decodes("usm", MOVIE, MOVIES_INPUT, "movie", facts=True)


def master_input(env, record: dict) -> Input:
    """The input "master" of a product task: the master record `record` (the rows it reads), stored in the depot."""
    body = contract.encode(record)
    return Input(MASTER_INPUT, env.depot.put(body), len(body), None, ({"kind": "depot"},))


def request(stages: dict, products: dict, env) -> dict:
    """The fact "roots" (pipeline.units) of a request for `products` {stage name: subjects}: the catalog keys, the
    resource paths, the built-in selectors and the root objects of every subject, sorted."""
    groups = {name: set() for name in ("keys", "resources", "builtins", "objects")}
    font_roots = {name: set() for name in groups}
    for name, subjects in sorted(products.items()):
        stage = stages[name]
        for s in subjects:
            for kind, method in (
                ("keys", "keys"),
                ("resources", "resources"),
                ("builtins", "builtins"),
                ("objects", "root_objects"),
            ):
                values = getattr(stage, method)(s, env)
                groups[kind].update(values)
                if stage.INCLUDE_FONTS:
                    font_roots[kind].update(values)
    result: dict = {kind: sorted(values) for kind, values in groups.items()}
    if any(font_roots.values()):
        result["font_roots"] = {kind: sorted(values) for kind, values in font_roots.items()}
    return result


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


def reach(env, roots, results: _Results | None = None, keys: dict | None = None, *, fonts=False) -> Reach:
    """The objects reachable from the object ids `roots` (module documentation); `keys` {catalog key: its object ids,
    all among the roots} goes into the table as it is."""
    results = results or _Results()
    table = units.table(env)
    todo, seen, missing = [], set(), set()
    for o in roots:
        s = table.subject_of(o)
        if s is None:
            missing.add(o)
        else:
            todo.append(s)
    start = set(todo)
    objects: dict[str, dict] = {}
    read = []
    crops: dict[str, list] = {}  # atlas object id -> sprite object ids
    blocked: dict[str, dict] = {}  # objects a reached object references across the font fence: {id: class, name}
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
                elif not fonts and table.fences(s, t):
                    blocked[r] = table.info[r]
                elif t not in seen and table.reaches(s, t, s in start, fonts=fonts):
                    todo.append(t)
    for atlas, sprites in sorted(crops.items()):
        s = table.subject_of(atlas)
        # a sprite of an unfenced bundle takes no image from a fenced atlas the walk does not reach
        cut = {
            o
            for o in sprites
            if not fonts
            and s is not None
            and s not in seen
            and (src := table.subject_of(o)) is not None
            and table.fences(src, s)
        }
        if cut:
            blocked[atlas] = table.info[atlas]
        sprites = [o for o in sprites if o not in cut]
        if not sprites:
            continue
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
        "keys": {k: sorted(v) for k, v in sorted((keys or {}).items())},
        "objects": dict(sorted(objects.items())),
        "fenced": {r: v for r, v in sorted(blocked.items()) if r not in objects},
        "missing": sorted(missing),
    }
    size = sum(f["size"] for e in objects.values() for f in e.get("files", {}).values())
    return Reach(doc, sorted(read), size)


@dataclass(frozen=True)
class Stored:
    """A product file that is content the depot holds: its content id and size."""

    sha256: str
    size: int


class _Decoded:
    """The content of a catalog key a CRI stage decoded for a product task, and the files the stage made of it."""

    WHAT = ""
    REASON = ""

    def __init__(self, key: str, entry: dict, source: Input, depot):
        self.key, self.entry, self._source, self.depot = key, entry, source, depot

    def _check(self) -> None:
        if self.entry["status"] != "exported":
            reason = self.entry.get("reason") or self.REASON
            raise Unsupported(reason, f"{self.WHAT} {self.key} was not decoded ({self.entry['status']})")

    def _file(self, name: str) -> dict:
        self._check()
        f = self.entry["files"].get(name)
        if f is None:
            raise KeyError(f"{self.WHAT} {self.key} has no file {name!r}")
        return f

    def file(self, name: str) -> Stored:
        f = self._file(name)
        return Stored(f["sha256"], f["size"])

    def bytes(self, name: str) -> bytes:
        return self.depot.read(self.file(name).sha256)


class Sheet(_Decoded):
    """A cue sheet of a product task: its ACB and the files cri.audio made of it (one per stream, `cues.json`,
    `streams.json`)."""

    WHAT, REASON = "cue sheet", "unsupported.cri.sheet"

    def acb(self) -> bytes:
        return self.depot.input_bytes(self._source)

    def streams(self) -> list[dict]:
        """streams.json: every stream in decoder order ({stream, name, file, sampleRate, channels, samples, ...})."""
        return contract.loads(self.bytes("streams.json"))


class Movie(_Decoded):
    """A movie of a product task: its USM and the files cri.movie made of it (its streams, `movie.<format>`), each
    with its facts."""

    WHAT, REASON = "movie", "unsupported.cri.movie"

    def _file(self, name: str) -> dict:
        """A movie whose subtitles cannot be read is `unsupported` and keeps its other files: a file it made is
        there."""
        f = self.entry["files"].get(name)
        return f if f is not None else super()._file(name)

    def facts(self, name: str) -> dict:
        """The facts cri.movie recorded for its file `name`."""
        return self._file(name).get("facts") or {}


class Objects:
    """The object table of a product task, the bytes of its objects' files (from the depot) and its cue sheets."""

    def __init__(self, doc: dict, depot, sheets: dict | None = None, movies: dict | None = None):
        self.doc, self.depot = doc, depot
        self.roots: list[str] = doc["roots"]
        self._keys: dict[str, list[str]] = doc["keys"]
        self._objects: dict = doc["objects"]
        self._fenced: dict = doc["fenced"]
        self._json: dict = {}
        self._sheets: dict[str, Sheet] = sheets or {}
        self._movies: dict[str, Movie] = movies or {}

    def sheet(self, key: str) -> Sheet:
        """The cue sheet of the catalog key `key` (one of the stage's cue_sheets)."""
        try:
            return self._sheets[key]
        except KeyError:
            raise KeyError(f"cue sheet {key} is not an input of the product") from None

    def movie(self, key: str) -> Movie:
        """The movie of the catalog key `key` (one of the stage's movies)."""
        try:
            return self._movies[key]
        except KeyError:
            raise KeyError(f"movie {key} is not an input of the product") from None

    def key(self, key: str) -> list[str]:
        """The root objects of the catalog key `key` (one of the stage's keys), sorted."""
        try:
            return self._keys[key]
        except KeyError:
            raise KeyError(f"{key} is not a key of the product") from None

    def keys(self) -> list[str]:
        """The catalog keys of the product, sorted (each with its root objects, possibly none)."""
        return sorted(self._keys)

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

    def fenced(self, oid: str) -> dict | None:
        """The class and name of the object `oid` when a reached object references it across the font fence (None:
        no reached object does)."""
        return self._fenced.get(oid)

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
    """What `assemble` makes: the product's files {path: (bytes or Stored, extension, kind)} and what of the object
    table they use: `objects` the object ids (None: every object of the table), `roles` {object id: the roles of its
    files the product keeps} for an object of which it uses some files only (a shader's programs for one graphics
    API). `table` optionally supplies the table of the retained roots when the inputs also include objects read only
    during assembly. The product's object table, and so its manifest, holds only those."""

    files: dict
    objects: set | None = None
    roles: dict = field(default_factory=dict)
    table: dict | None = None


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
    `keys`, `resources`, `builtins` and/or `root_objects` and `assemble`; `subjects` defaults to the subjects of the
    fact "products". A stage that reads master tables names them in `MASTER` (its inputs come from the fact
    "master"), one that reads files of the APK names them in `APK`."""

    after = (ADDRESSES, EXPORT, CROP, AUDIO, MOVIE)
    KIND = "product"
    INCLUDE_FONTS = False
    MASTER: tuple[str, ...] = ()
    APK: tuple[str, ...] = ()

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

    def select(self, keys: list[str], env) -> list[str]:
        """The subjects among the catalog keys `keys` (those a selection's bundles hold), sorted (from the facts
        alone)."""
        return []

    def keys(self, subject: str, env) -> list[str]:
        """The catalog keys whose objects are roots of `subject` (from the facts alone)."""
        return []

    def resources(self, subject: str, env) -> list[str]:
        """The Resources.Load paths whose objects are roots of `subject` (from the facts alone)."""
        return []

    def builtins(self, subject: str, env) -> list[str]:
        """The built-in selectors ("<class>", "MonoBehaviour:<script class>") whose objects are roots of `subject`
        (from the facts alone)."""
        return []

    def root_objects(self, subject: str, env) -> list[str]:
        """Object ids that are roots of `subject` besides those of its keys and resource paths (from the facts
        alone)."""
        return []

    def cue_sheets(self, subject: str, env) -> list[str]:
        """The catalog keys of the cue sheets `subject` reads (from the facts alone; each also one of its `keys`)."""
        return []

    def movies(self, subject: str, env) -> list[str]:
        """The catalog keys of the movies `subject` reads (from the facts alone; each also one of its `keys`)."""
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
        """What `subject` reaches (kept while the results it read are the same); nothing, and no census read, for a
        subject without roots."""
        keys = self.keys(subject, env)
        named = (self.root_objects(subject, env), self.resources(subject, env), self.builtins(subject, env))
        if not keys and not any(named):
            return Reach(
                {"schema": contract.OBJECTS, "roots": [], "keys": {}, "objects": {}, "fenced": {}, "missing": []}
            )
        roots = units.root_objects(env, keys, *named)
        by_key = {k: units.root_objects(env, [k]) for k in keys}
        sig = (tuple(roots), tuple((k, tuple(v)) for k, v in sorted(by_key.items())), id(units.table(env)))
        memo = self._memo.get(subject)
        if memo is not None and memo[0] == sig and all(env.key(t) == k for t, k in memo[1]):
            return memo[2]
        r = reach(env, roots, self._results, by_key, fonts=self.INCLUDE_FONTS)
        self._memo[subject] = (sig, [(t, env.key(t)) for t in r.read], r)
        return r

    def sheets(self, subject: str, env) -> dict[str, tuple[Input, str]]:
        """{catalog key: (its ACB as input "acb:<key>", the cri.audio task id)} of the cue sheets of `subject`."""
        return self._decodes(SHEETS, self.cue_sheets(subject, env), subject, env)

    def movie_files(self, subject: str, env) -> dict[str, tuple[Input, str]]:
        """{catalog key: (its USM as input "usm:<key>", the cri.movie task id)} of the movies of `subject`."""
        return self._decodes(MOVIES, self.movies(subject, env), subject, env)

    def _decodes(self, d: Decodes, keys: list[str], subject: str, env) -> dict[str, tuple[Input, str]]:
        """{catalog key: (the content it loads as input "<role>:<key>", the task id of stage d.stage)} of `keys`."""
        if not keys:
            return {}
        objects = self.reach(subject, env).table["objects"]
        out = {}
        for key in keys:
            held: dict[str, Input] = {}  # content id -> the file
            for o in units.root_objects(env, [key]):
                f = (objects.get(o, {}).get("files") or {}).get(d.role)
                if f is not None:
                    held[f["sha256"]] = Input(f"{d.role}:{key}", f["sha256"], f["size"], None, ({"kind": "depot"},))
            if not held:  # a raw file of the catalog that the stage decodes
                raw = env.facts.get("raw") or {}
                for stable in _raw_files(env.fact("index")).get(key, ()):
                    if stable not in raw:
                        raise ValueError(f"{d.what} {key}: the request does not hold its file {stable}")
                    i = raw[stable]
                    if env.known(contract.task_id(d.stage, i.sha256)):
                        held[i.sha256] = Input(f"{d.role}:{key}", i.sha256, i.size, i.name, i.locators)
            if len(held) != 1:
                what = f"no {d.role.upper()}" if not held else f"{len(held)} {d.role.upper()}s"
                raise ValueError(f"{d.what} {key}: {what} in what the request holds")
            (src,) = held.values()
            out[key] = (src, contract.task_id(d.stage, src.sha256))
        return out

    def inputs(self, subject: str, env) -> list[Input]:
        data = contract.encode(self.reach(subject, env).table)
        table = Input("objects", env.depot.put(data), len(data), None, ({"kind": "depot"},))
        out = [table]
        if self.APK:
            have = env.fact("apk")
            for m in self.APK:
                if m not in have:
                    raise ValueError(f"{self.name} reads {m} of the APK: set [paths] apk to an APK that holds it")
                out.append(have[m])
        for d, held in ((SHEETS, self.sheets(subject, env)), (MOVIES, self.movie_files(subject, env))):
            if held:
                out += _decodes_inputs(d, held, env)
        return out + self.more_inputs(subject, env)

    def depends(self, subject: str, env) -> list[str]:
        named = self.keys(subject, env) or self.resources(subject, env) or self.builtins(subject, env)
        own = [contract.task_id(ADDRESSES, s) for s in sorted(env.fact("catalogs"))] if named else []
        cri = {tid for _, tid in (*self.sheets(subject, env).values(), *self.movie_files(subject, env).values())}
        return own + self.reach(subject, env).read + sorted(cri)

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the bytes of the objects' files: a product reads each of them about once."""
        size = self.reach(subject, env).size + sum(i.size for i in inputs)
        return Cost(0.02 + size / 100e6, (64 << 20) + 2 * size)

    # ---------------------------------------------------------------- running
    def run(self, task, depot) -> Output:
        data = depot.input_bytes(task.input("objects"))
        doc = contract.loads(data)
        decoded: dict = {SHEETS_INPUT: {}, MOVIES_INPUT: {}}
        for d, cls in ((SHEETS, Sheet), (MOVIES, Movie)):
            if any(i.role == d.input for i in task.inputs):
                for key, e in contract.loads(depot.input_bytes(task.input(d.input))).items():
                    decoded[d.input][key] = cls(key, e, task.input(f"{d.role}:{key}"), depot)
        try:
            made = self.assemble(task, Objects(doc, depot, decoded[SHEETS_INPUT], decoded[MOVIES_INPUT]))
        except Unsupported as e:
            why = contract.reason(e.code, e.message)
            return Output([], [contract.item(task.id, "unsupported", why=why, cls=self.KIND)])
        prov = contract.provenance(task)
        arts = []
        for path, (body, ext, kind) in sorted(made.files.items()):
            if path == PRODUCT_TABLE:
                raise ValueError(f"{task.id}: a product file cannot be named {PRODUCT_TABLE!r}")
            aid = contract.artifact_id(task.id, path)
            if isinstance(body, Stored):
                if not depot.has(body.sha256, body.size):
                    raise ValueError(f"{task.id}: {path} is content {body.sha256} the depot does not hold")
                content = {"sha256": body.sha256, "size": body.size, "mediaType": contract.media_type(ext), "ext": ext}
            else:
                content = depot.add(body, ext)
            arts.append(contract.artifact(aid, content, prov, {"kind": kind}))
        if made.table is not None:
            doc = made.table
        if made.table is not None or made.objects is not None or made.roles:
            doc = dict(doc, objects=_prune(task.id, doc["objects"], made))
            data = contract.encode(doc)
        aid = contract.artifact_id(task.id, PRODUCT_TABLE)
        arts.append(contract.artifact(aid, depot.add(data, "json"), prov, {"kind": TABLE_KIND}))
        item = contract.item(task.id, "exported", artifacts=[a["id"] for a in arts], cls=self.KIND)
        return Output(arts, [item])


def _decodes_inputs(d: Decodes, held: dict[str, tuple[Input, str]], env) -> list[Input]:
    """The inputs of the contents `held` (ProductStage._decodes): each content as "<role>:<key>" and the input d.input
    that lists, per key, the status and the files of its d.stage result."""
    doc, out = {}, []
    for key, (src, tid) in held.items():
        res = env.result(tid)
        (item,) = res["items"]
        e = {d.role: src.sha256, "status": item["status"]}
        if "reason" in item:
            e["reason"] = item["reason"]["code"]
        files = {}
        for a in res["artifacts"]:
            f = {k: a["content"][k] for k in ("sha256", "size", "ext")}
            if d.facts and a["semantics"].get("facts"):
                f["facts"] = a["semantics"]["facts"]
            files[contract.parse_artifact_id(a["id"])[1]] = f
        e["files"] = files
        doc[key] = e
        out.append(src)
    data = contract.encode(doc)
    return [*out, Input(d.input, env.depot.put(data), len(data), None, ({"kind": "depot"},))]


_RAW: dict = {}  # id of the last catalog index -> (the index, {catalog key: the raw files its locations depend on})


def _raw_files(index: dict) -> dict[str, list[str]]:
    """{catalog key: stable names of the raw files its locations depend on} of a catalog index (kept for the last
    index)."""
    hit = _RAW.get(id(index))
    if hit is not None and hit[0] is index:
        return hit[1]
    from . import catalogdb

    refs = catalogdb.file_refs(index)
    out: dict[str, set] = {}
    for loc in index["locations"]:
        raws = {refs[d][1] for d in loc["dependencies"] if d in refs and refs[d][0] == "raw"}
        if raws and loc["kind"] not in catalogdb.FILE_KINDS:
            out.setdefault(loc["primaryKey"], set()).update(raws)
    table = {k: sorted(v) for k, v in out.items()}
    _RAW.clear()
    _RAW[id(index)] = (index, table)
    return table
