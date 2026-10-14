"""The model and scene stages: Live2D models, Spine skeletons, room scenes and Spots, written by the extractors of
`nnnotes export live2d`, `nnnotes export room` and `nnnotes export spot` from the bundles of their tasks.

live2d.model, one task per Live2D model key (Character/Live2D/<group>/<name>/model/<name>, the keys `nnnotes export
live2d` takes) whose own bundle (the first dependency of the key's location) is selected:
    inputs     "bundle:<n>": the bundles of the key's dependency closure in the order the catalog gives them
               (Catalog.resolve; n is the position: the extractor loads them in this order, so the order is part of
               the key)
    context    {"internalId": the internal id of the key's location} (the container path the model is read from)
    artifacts  "live2d.model:<key>#<path>": every file live2d.extract_model writes, <path> relative to the model's
               directory (<name>.moc3, <name>.prefab.json, textures/*.png, <name>.model3.json, *.exp3.json,
               <name>.physics3.json, motions/*.motion3.json, motions/_fades.json), with its bytes
The extractor runs through TaskCatalog, which answers what it asks of a catalog from the task. It reads nothing of
the APK but the APK's bundles of the closure, which are inputs like the others.

spine.skeleton, one task per SkeletonDataAsset (a MonoBehaviour whose script the script table names
SkeletonDataAsset) of a selected bundle; its subject is the asset's stable address by path id,
"<bundle><file tag>:<pathId>" (link.file_tags):
    inputs     "bundle:<stable name>": the bundles holding the serialized files the asset's file reaches through
               externals, transitively (every object a reference chain from the asset can reach); externals no
               census holds (the built-in files) are left out
    context    {"object": the asset's object id}
    artifacts  "spine.skeleton:<subject>#<file>": the skeleton (.json or .skel), each of its atlases (.atlas) and
               the atlas pages (PNG), as spot.write_skeleton writes them for `nnnotes export spot`
    item       the asset: exported (its artifacts), or failed with the writer's error

room.scene, one task per background key a command asks for (the fact "requests"):
    inputs     "bundle:<n>": the bundles of the key's closure in load order, as live2d.model
    artifacts  "room.scene:<key>#room.glb": the scene as room.extract_room writes it (facts: meshCount,
               inactiveMeshes); "#room.json": its report, naming the scene room.glb

spot.scene, one task per Spot id (MasterHomeSpot `_id`) a command asks for, with master data (the fact "master"):
    inputs     "master:<Table>": the tables spot.extract reads (MasterHomeSpot, MasterStoryHomeSpotTapTalkEpisode,
               MasterText); "bundle:<n>": the bundles of the Spot's situation key (`_situationAssetPath`) in load
               order
    context    {"resources": {key: [bundle names]}}: the closures of the situation and background keys
               (`_backgroundAssetPath`) by bundle name, in load order (spot.json lists them)
    artifacts  "spot.scene:<id>#spot.json" (facts: tapTargets, skeletons) and "#spine/<file>": the Spine files of
               the situation, as spot.extract writes them

Every task names its extractor as its one atom ("live2d.extract_model", "spot.write_skeleton", "room.extract_room",
"spot.extract"), whose id carries the versions of the libraries the files depend on; this module imports UnityPy only
when a task runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..source.catalog import Bundle
from . import catalogdb, contract, link
from .atoms import impl_id
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

LIVE2D = "live2d.model"
SPINE = "spine.skeleton"
ROOM = "room.scene"
SPOT = "spot.scene"
MASTER_ROLE = "master:"
SPOT_TABLES = ("MasterHomeSpot", "MasterStoryHomeSpotTapTalkEpisode", "MasterText")
CENSUS = "unity.census"
SCRIPTS = "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")
# the model keys of `nnnotes export live2d` (webmodel.MODEL_KEY; webmodel imports UnityPy)
MODEL_KEY = re.compile(r"Character/Live2D/(?P<group>[^/]+)/(?P<name>[^/]+)/model/(?P=name)")
SKELETON_CLASS = "SkeletonDataAsset"
# the libraries the extractors' output depends on (export._LIBS, the salt of export's PNG cache)
LIBRARIES = ("UnityPy", "Pillow", "texture2ddecoder", "astc-encoder-py", "etcpak", "numpy")
_ASSET_PREFIXES = ("Assets/", "Packages/")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
_EXT = re.compile(r"[0-9a-z]+")
# live2d.extract_model files -> artifact kinds (by suffix, the first that matches)
LIVE2D_KINDS = (
    (".model3.json", "live2d.model3"),
    (".prefab.json", "live2d.prefab"),
    (".physics3.json", "live2d.physics3"),
    (".exp3.json", "live2d.expression"),
    (".motion3.json", "live2d.motion"),
    ("motions/_fades.json", "live2d.fades"),
    (".moc3", "live2d.moc3"),
    ("textures/", "live2d.texture"),
)


def _message(e: BaseException) -> str:
    return _ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:500]


def _ext(name: str, default: str = "bin") -> str:
    ext = name.rsplit("/", 1)[-1].rpartition(".")[2].lower() if "." in name.rsplit("/", 1)[-1] else ""
    return ext if _EXT.fullmatch(ext) else default


def check_atoms(task, atoms: dict) -> None:
    if task.atoms != atoms:
        raise IncompatibleTask(f"task {task.id}: this installation runs {atoms}, the task names {task.atoms}")


def _fact_input(bundles, stable: str, what: str) -> Input:
    """The Input of a bundle from the fact "bundles" (Pending while it is not fetched)."""
    if stable not in bundles:
        raise ValueError(f"{what}: bundle {stable} is not readable")
    inp = bundles[stable]
    if inp is None:
        raise Pending(f"fetch:{stable}")
    if isinstance(inp, Exception):
        raise inp
    if inp.role != "bundle":
        raise ValueError(f"{what}: input role {inp.role!r}, expected 'bundle'")
    return inp


def _ordered(task, prefix: str = "bundle:") -> list[Input]:
    """The inputs "bundle:<n>" of a task by n."""
    ins = [i for i in task.inputs if i.role.startswith(prefix)]
    return sorted(ins, key=lambda i: int(i.role[len(prefix) :]))


def write_master(task, store, mdir: Path) -> Path:
    """The inputs "master:<Table>" of a task as the decoded master data directory `mdir` (<Table>.json, each a
    link to the checked file, else a copy), for the extractors that read master data from a directory."""
    mdir.mkdir(parents=True)
    for i in task.inputs:
        if i.role.startswith(MASTER_ROLE):
            src, dst = store.open_input(i), mdir / f"{i.role[len(MASTER_ROLE) :]}.json"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    return mdir


def requested(env, stage: str) -> list[str]:
    """The subjects a command asks of `stage` (the fact "requests": {stage: [subject]}; none without it), sorted."""
    return sorted(set(env.facts.get("requests", {}).get(stage, ())))


def file_artifact(task, store, name: str, data: bytes, kind: str, ext: str, facts: dict | None = None) -> dict:
    return contract.artifact(
        contract.artifact_id(task.id, name),
        store.add(data, ext),
        contract.provenance(task),
        {"kind": kind, "format": ext, "facts": facts or {}},
    )


# ---------------------------------------------------------------- the catalog of a task
class Closures:
    """Key lookup and dependency closures of a catalog index, as Catalog.entry and Catalog.resolve give them on the
    catalogs the index was made of: a key's location is its first one inside a bundle (internal id under Assets/ or
    Packages/), else its first one (remote catalog before APK catalog, each by offset); its closure is every bundle
    location its dependencies reach, depth first, in the order Catalog.resolve lists them."""

    def __init__(self, index: dict):
        self.index = index
        self.locations = catalogdb.by_id(index)
        self.files = catalogdb.file_refs(index)
        self.by_key: dict[str, list[dict]] = {}
        for loc in index["locations"]:
            self.by_key.setdefault(loc["primaryKey"], []).append(loc)

    def keys(self) -> list[str]:
        return sorted(self.by_key)

    def entry(self, key: str) -> dict:
        locs = self.by_key.get(key)
        if not locs:
            raise KeyError(key)
        return next((loc for loc in locs if loc["internalId"].startswith(_ASSET_PREFIXES)), locs[0])

    def own_bundle(self, key: str) -> str | None:
        """The stable name of the bundle a key's location names first (the bundle holding it), else None."""
        deps = self.entry(key)["dependencies"]
        ref = self.files.get(deps[0]) if deps else None
        return ref[1] if ref is not None and ref[0] == "bundle" else None

    def closure(self, key: str) -> list[str]:
        """The location ids of the bundles of a key's closure, in Catalog.resolve order."""
        seen: set[str] = set()
        out: dict[str, None] = {}
        stack = [self.entry(key)["id"]]
        while stack:
            lid = stack.pop()
            if lid in seen:
                continue
            seen.add(lid)
            loc = self.locations.get(lid)
            if loc is None:
                continue
            if loc["kind"] == "bundle":
                out.setdefault(lid, None)
            for d in loc["dependencies"]:
                if d not in seen:
                    stack.append(d)
        return list(out)

    def closure_bundles(self, key: str) -> list[str]:
        """The stable names of the bundles of a key's closure, in Catalog.resolve order (a file two locations name
        is listed twice, as Catalog.fetch_key gives it twice)."""
        return [self.files[lid][1] for lid in self.closure(key)]


# the value of TaskCatalog.apk: not None, the APK's bundles of the closure are inputs of the task
APK_FROM_INPUTS = "<the task's inputs>"
BUNDLE_ROLE = "bundle:"


class TaskCatalog:
    """What the extractors ask of a Catalog (live2d.extract_model through export.Exporter, room.extract_room,
    spot.extract, adv.extract: fetch_key, resolve, has, keys, entry, apk), answered from a task:

    closures      {key: the bundles of its closure in load order}, each bundle read through the store
                  (Store.open_input: its locators, checked against its content id)
    internal_ids  {key: the internal id of its location}, for the keys whose entry the extractor reads
    names         {key: [bundle names in load order]}: the names `resolve` lists, also for keys whose bundles the
                  extractor only names (else a bundle's input file name, its content id when it has none)
    known         further keys the catalog has (`has` and `keys` answer from them and the keys of `closures`)

    A key the task does not hold raises KeyError (`has` answers False). `apk` is not None: the extractors need the
    APK's bundles, and those of the closures are inputs of the task like the others."""

    def __init__(
        self,
        closures: dict[str, list[Input]],
        store,
        *,
        internal_ids: dict[str, str] | None = None,
        names: dict[str, list[str]] | None = None,
        known=(),
    ):
        self.closures = {k: list(v) for k, v in closures.items()}
        self.store = store
        self.internal_ids = dict(internal_ids or {})
        self.names = dict(names or {})
        for k, v in self.names.items():
            if k in self.closures and len(v) != len(self.closures[k]):
                raise ValueError(f"{k}: {len(v)} bundle names for {len(self.closures[k])} bundles")
        self.known = set(known) | set(self.closures)
        self.apk = APK_FROM_INPUTS

    @classmethod
    def from_task(cls, task, store) -> TaskCatalog:
        """The catalog of a live2d.model task: its subject, context internalId and inputs "bundle:<n>"."""
        return cls({task.subject: _ordered(task)}, store, internal_ids={task.subject: task.context["internalId"]})

    @classmethod
    def of_closures(cls, task, store, **kw) -> TaskCatalog:
        """The catalog of a task whose context "closures" lists each key's bundles by stable name in load order
        ({key: [stable names]}) and whose inputs are "bundle:<stable name>" (closure_context)."""
        by_stable = {i.role[len(BUNDLE_ROLE) :]: i for i in task.inputs if i.role.startswith(BUNDLE_ROLE)}
        closures = {k: [by_stable[s] for s in stables] for k, stables in task.context["closures"].items()}
        return cls(closures, store, **kw)

    def has(self, key: str) -> bool:
        return key in self.known

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.known if k.startswith(prefix))

    def entry(self, key: str) -> dict:
        if key not in self.internal_ids:
            raise KeyError(f"{key}: the task has no internal id of it")
        return {"primary_key": key, "internal_id": self.internal_ids[key]}

    def entries_for(self, key: str) -> list[dict]:
        return [self.entry(key)] if key in self.internal_ids else []

    def resolve(self, key: str) -> list[Bundle]:
        """The closure as Bundles in load order: offset = the position, name = its name in `names`, else the
        input's file name (its content id when it has none); internal_id is empty and remote True (a task has a
        file's content, not its place in a catalog; every bundle is read from the inputs)."""
        if key in self.names:
            names = self.names[key]
        elif key in self.closures:
            names = [inp.name or inp.sha256 for inp in self.closures[key]]
        else:
            raise KeyError(key)
        return [Bundle(i, "", name, True) for i, name in enumerate(names)]

    def fetch_key(self, key: str) -> list[Path]:
        """The files of the bundles of the key's closure, in load order."""
        if key not in self.closures:
            raise KeyError(key)
        return [self.store.open_input(inp) for inp in self.closures[key]]


# ---------------------------------------------------------------- live2d.model
def live2d_kind(path: str) -> str:
    """The artifact kind of a file live2d.extract_model writes (LIVE2D_KINDS; else "live2d.file")."""
    for suffix, kind in LIVE2D_KINDS:
        if path.endswith(suffix) or (suffix.endswith("/") and path.startswith(suffix)):
            return kind
    return "live2d.file"


def _tree(root: Path) -> list[tuple[str, Path]]:
    """(relative POSIX path, file) of every file under `root`, sorted by path."""
    return sorted((p.relative_to(root).as_posix(), p) for p in root.rglob("*") if p.is_file())


class ClosureStage(Stage):
    """A stage whose task reads the bundles of a key's closure in load order (inputs "bundle:<n>"), from the facts
    "index" (the catalog index) and "bundles" ({stable name: Input "bundle"})."""

    def __init__(self):
        self._closures: Closures | None = None

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.__init__()

    def closures(self, env) -> Closures:
        index = env.fact("index")
        if self._closures is None or self._closures.index is not index:
            self._closures = Closures(index)
        return self._closures

    def closure_inputs(self, key: str, env, what: str) -> list[Input]:
        """The inputs "bundle:<n>" of the bundles of `key`'s closure (n: the position in load order)."""
        bundles = env.fact("bundles")
        names = self.closures(env).closure_bundles(key)
        width = max(3, len(str(len(names) - 1)))
        out = []
        for i, stable in enumerate(names):
            inp = _fact_input(bundles, stable, what)
            out.append(Input(f"bundle:{i:0{width}d}", inp.sha256, inp.size, inp.name, inp.locators))
        return out

    def closure_names(self, key: str, env) -> list[str]:
        """The catalog names of the bundles of `key`'s closure, in load order."""
        c = self.closures(env)
        names = {b["stable"]: b["name"] for b in c.index["bundles"]}
        return [names[stable] for stable in c.closure_bundles(key)]

    def closure_context(self, keys, env, what: str) -> tuple[list[Input], dict]:
        """The inputs "bundle:<stable name>" of the bundles of the closures of `keys` (each bundle once) and the
        context entry "closures": {key: [stable names in load order]} (TaskCatalog.of_closures)."""
        bundles, c = env.fact("bundles"), self.closures(env)
        closures = {k: c.closure_bundles(k) for k in sorted(set(keys))}
        inputs = {}
        for stables in closures.values():
            for stable in stables:
                if stable not in inputs:
                    inp = _fact_input(bundles, stable, what)
                    inputs[stable] = Input(BUNDLE_ROLE + stable, inp.sha256, inp.size, inp.name, inp.locators)
        return [inputs[s] for s in sorted(inputs)], {"closures": closures}

    def master_inputs(self, tables, env, what: str) -> list[Input]:
        """The inputs "master:<Table>" of the master tables `tables` (the fact "master": the decoded tables)."""
        mdir = Path(env.fact("master"))
        out = []
        for t in tables:
            p = mdir / f"{t}.json"
            if not p.is_file():
                raise ValueError(f"{what}: master table {t} is not in the master data")
            sha, size = env.store.identify(p)
            out.append(Input(MASTER_ROLE + t, sha, size, p.name, ({"kind": "file", "path": str(p)},)))
        return out


class Live2DStage(ClosureStage):
    """live2d.model: a Live2D model key -> the files of `nnnotes export live2d` (module documentation). Subjects and
    inputs come from the facts "index" (the catalog index), "selected" and "bundles" ({stable name: Input "bundle"})."""

    name = LIVE2D
    version = 1
    ATOMS = {"live2d.extract_model": impl_id(LIBRARIES, 1)}
    LAYOUT_ROOT = "{subject}"

    def subjects(self, env) -> list[str]:
        c, selected = self.closures(env), set(env.fact("selected"))
        return [k for k in c.keys() if MODEL_KEY.fullmatch(k) and c.own_bundle(k) in selected]

    def inputs(self, subject: str, env) -> list[Input]:
        return self.closure_inputs(subject, env, f"{self.name} {subject}")

    def context(self, subject: str, env) -> dict:
        return {"internalId": self.closures(env).entry(subject)["internalId"]}

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the closure's bytes (least squares over the models of a catalog: CPU seconds; peak resident bytes
        of a worker process)."""
        size = sum(i.size for i in inputs)
        return Cost(0.9 + size * 2.2e-7, (420 << 20) + 10 * size)

    def run(self, task, store) -> Output:
        check_atoms(task, self.ATOMS)
        from ..unity import live2d

        cat = TaskCatalog.from_task(task, store)
        arts = []
        with tempfile.TemporaryDirectory(prefix="nnnotes-live2d-") as d:
            # TaskCatalog answers the catalog calls of the model extractor
            summary = live2d.extract_model(cat, task.subject, Path(d))  # pyright: ignore[reportArgumentType]
            for rel, p in _tree(Path(d)):
                kind = live2d_kind(rel)
                facts = {k: summary[k] for k in sorted(summary)} if kind == "live2d.model3" else {}
                arts.append(file_artifact(task, store, rel, p.read_bytes(), kind, _ext(rel), facts))
        return Output(arts, [])


# ---------------------------------------------------------------- spine.skeleton
def skeleton_index(env, selected) -> dict:
    """What spine.skeleton plans from, read from the census results and the script table: {"subjects": {subject:
    (stable bundle name, file, pathId)} of the SkeletonDataAssets of the `selected` bundles, "files": {serialized
    file (case-folded): stable bundle name} (a file two bundles hold: the first by name), "externals": {serialized
    file (case-folded): [its external files]}}. Raises Pending while a census task has not run or the script table
    is missing; failed censuses are left out."""
    pending = env.pending(CENSUS)
    if pending:
        raise Pending(pending[0])
    table = contract.loads(env.store.read(env.input_of(SCRIPTS_TASK, "scripts").sha256))["scripts"]
    scripts = {oid for oid, entry in table.items() if entry.rsplit("|", 1)[-1] == SKELETON_CLASS}
    files: dict[str, str] = {}
    externals: dict[str, list[str]] = {}
    subjects: dict[str, tuple] = {}
    for tid in env.tasks(CENSUS):
        stable = contract.parse_task_id(tid, CENSUS)[1]
        doc = contract.loads(env.store.read(env.artifact(tid, "census")["content"]["sha256"]))
        for f in doc["files"]:
            files.setdefault(f["name"].casefold(), stable)
            externals.setdefault(f["name"].casefold(), [x["file"] for x in f["externals"]])
        if not scripts or stable not in selected or "MonoBehaviour" not in doc["classes"]:
            continue
        tags = link.file_tags(f["name"] for f in doc["files"])
        for f in doc["files"]:
            for o in f["objects"]:
                s = o.get("script")
                if (
                    o["class"] == "MonoBehaviour"
                    and s
                    and s.get("file") is not None
                    and contract.object_id(s["file"], s["pathId"]) in scripts
                ):
                    subject = contract.stable_address(stable + tags[f["name"]], path_id=o["pathId"])
                    subjects[subject] = (stable, f["name"], int(o["pathId"]))
    return {"subjects": subjects, "files": files, "externals": externals}


def reach(index: dict, file: str) -> list[str]:
    """The stable names of the bundles holding `file` and every serialized file it reaches through externals,
    sorted (skeleton_index document; files no census holds are left out)."""
    start = file.casefold()
    seen, stack, out = {start}, [start], set()
    while stack:
        f = stack.pop()
        b = index["files"].get(f)
        if b is None:
            continue
        out.add(b)
        for x in index["externals"].get(f, ()):
            xf = x.casefold()
            if xf not in seen:
                seen.add(xf)
                stack.append(xf)
    return sorted(out)


class SpineStage(Stage):
    """spine.skeleton: a SkeletonDataAsset -> its Spine files as `nnnotes export spot` writes them (module
    documentation). Subjects from the census results, the script table (link.scripts:all) and the fact "selected";
    inputs from the fact "bundles". Waits for census tasks that have not run."""

    name = SPINE
    version = 1
    after = (CENSUS, SCRIPTS)
    ATOMS = {"spot.write_skeleton": impl_id(LIBRARIES, 2)}

    def __init__(self):
        self._memo = None

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.__init__()

    def index(self, env) -> dict:
        """skeleton_index, kept while the census results, the script table and the selection stay the same."""
        pending = env.pending(CENSUS)
        if pending:
            raise Pending(pending[0])
        selected = tuple(sorted(env.fact("selected")))
        sig = (selected, tuple(env.key(t) for t in env.tasks(CENSUS)), env.key(SCRIPTS_TASK))
        if self._memo is None or self._memo[0] != sig:
            self._memo = (sig, skeleton_index(env, set(selected)))
        return self._memo[1]

    def _subject(self, subject: str, env) -> tuple[dict, tuple]:
        idx = self.index(env)
        if subject not in idx["subjects"]:
            raise ValueError(f"{self.name}: no SkeletonDataAsset {subject} in the selected bundles")
        return idx, idx["subjects"][subject]

    def subjects(self, env) -> list[str]:
        return sorted(self.index(env)["subjects"])

    def closure(self, subject: str, env) -> list[str]:
        idx, (_stable, file, _pid) = self._subject(subject, env)
        return reach(idx, file)

    def inputs(self, subject: str, env) -> list[Input]:
        bundles, what = env.fact("bundles"), f"{self.name} {subject}"
        out = []
        for stable in self.closure(subject, env):
            inp = _fact_input(bundles, stable, what)
            out.append(Input(f"bundle:{stable}", inp.sha256, inp.size, inp.name, inp.locators))
        return out

    def context(self, subject: str, env) -> dict:
        _idx, (_stable, file, pid) = self._subject(subject, env)
        return {"object": contract.object_id(file, pid)}

    def depends(self, subject: str, env) -> list[str]:
        return sorted({*(contract.task_id(CENSUS, s) for s in self.closure(subject, env)), SCRIPTS_TASK})

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the closure's bytes (least squares over the skeletons of a catalog, as Live2DStage.estimate)."""
        size = sum(i.size for i in inputs)
        return Cost(0.27 + size * 1.9e-7, (440 << 20) + 20 * size)

    def run(self, task, store) -> Output:
        check_atoms(task, self.ATOMS)
        from ..content import spot
        from ..unity import reader as unity

        oid = task.context["object"]
        paths = [store.open_input(i) for i in task.inputs if i.role.startswith("bundle:")]
        env = unity.load_closure(paths)
        file, pid = contract.parse_object_id(oid)
        written: dict[str, bytes] = {}
        try:
            sf = env.get_cab(file)
            o = getattr(sf, "objects", {}).get(pid)
            if o is None:
                raise KeyError(f"{oid} is not in the task's bundles")
            record = spot.write_skeleton(o, written.__setitem__)
        except (MemoryError, OSError):
            raise
        except Exception as e:
            return Output(
                [],
                [contract.item(oid, "failed", why=contract.reason("failed.export", _message(e)), cls="MonoBehaviour")],
            )
        arts = []
        for name in sorted(written):
            data = written[name]
            if name == record["skeleton"]:
                ext, kind, facts = _ext(name), "spine.skeleton", record
            elif name in record["atlases"]:
                ext, kind, facts = _ext(name, "atlas"), "spine.atlas", {}
            else:
                ext, kind, facts = "png", "spine.page", {}
            arts.append(
                contract.artifact(
                    contract.artifact_id(task.id, name),
                    store.add(data, ext),
                    contract.provenance(task),
                    {"kind": kind, "format": ext, "facts": facts},
                )
            )
        return Output(arts, [contract.item(oid, "exported", artifacts=[a["id"] for a in arts], cls="MonoBehaviour")])


# ---------------------------------------------------------------- room.scene
class RoomStage(ClosureStage):
    """room.scene: a background key -> the glTF scene of `nnnotes export room` and its report (module
    documentation). Subjects: the keys of the catalog a command asks for (the fact "requests")."""

    name = ROOM
    version = 1
    ATOMS = {"room.extract_room": impl_id(LIBRARIES, 1)}

    def subjects(self, env) -> list[str]:
        c = self.closures(env)
        return [k for k in requested(env, self.name) if k in c.by_key]

    def inputs(self, subject: str, env) -> list[Input]:
        return self.closure_inputs(subject, env, f"{self.name} {subject}")

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the closure's bytes (the form of Live2DStage.estimate)."""
        size = sum(i.size for i in inputs)
        return Cost(1.0 + size * 3e-7, (480 << 20) + 12 * size)

    def run(self, task, store) -> Output:
        check_atoms(task, self.ATOMS)
        from ..content import room

        cat = TaskCatalog({task.subject: _ordered(task)}, store)
        with tempfile.TemporaryDirectory(prefix="nnnotes-room-") as d:
            glb = Path(d) / "room.glb"
            doc = room.extract_room(cat, task.subject, glb)  # pyright: ignore[reportArgumentType]
            facts = {"meshCount": doc["meshCount"], "inactiveMeshes": doc["inactiveMeshes"]}
            arts = [
                file_artifact(task, store, "room.glb", glb.read_bytes(), "room.glb", "glb", facts),
                file_artifact(task, store, "room.json", glb.with_suffix(".json").read_bytes(), "room.report", "json"),
            ]
        return Output(arts, [])


# ---------------------------------------------------------------- spot.scene
def _spot_row(rows: list[dict], spot_id: int) -> dict:
    for r in rows:
        if r.get("_id") == spot_id:
            return r
    raise ValueError(f"{SPOT}: no MasterHomeSpot row {spot_id}")


class SpotStage(ClosureStage):
    """spot.scene: a Spot id -> spot.json and the Spine files of `nnnotes export spot` (module documentation).
    Subjects: the ids a command asks for (the fact "requests"), when the fact "master" names the master data."""

    name = SPOT
    version = 1
    ATOMS = {"spot.extract": impl_id(LIBRARIES, 1)}

    def subjects(self, env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        return requested(env, self.name)

    def row(self, subject: str, env) -> dict:
        p = Path(env.fact("master")) / f"{SPOT_TABLES[0]}.json"
        return _spot_row(json.loads(p.read_bytes())["_allData"], int(subject))

    def inputs(self, subject: str, env) -> list[Input]:
        masters = self.master_inputs(SPOT_TABLES, env, f"{self.name} {subject}")
        key = self.row(subject, env)["_situationAssetPath"]
        return masters + self.closure_inputs(key, env, f"{self.name} {subject}")

    def context(self, subject: str, env) -> dict:
        row = self.row(subject, env)
        keys = (row["_backgroundAssetPath"], row["_situationAssetPath"])
        return {"resources": {k: self.closure_names(k, env) for k in sorted(set(keys))}}

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the bytes of the closure and the tables (the form of Live2DStage.estimate)."""
        size = sum(i.size for i in inputs)
        return Cost(1.0 + size * 2e-7, (480 << 20) + 12 * size)

    def run(self, task, store) -> Output:
        check_atoms(task, self.ATOMS)
        from ..content import spot

        spot_id = int(task.subject)
        with tempfile.TemporaryDirectory(prefix="nnnotes-spot-") as d:
            mdir, out = write_master(task, store, Path(d, "master")), Path(d, "out")
            rows = json.loads((mdir / f"{SPOT_TABLES[0]}.json").read_bytes())["_allData"]
            key = _spot_row(rows, spot_id)["_situationAssetPath"]
            names = task.context["resources"]
            cat = TaskCatalog({key: _ordered(task)}, store, names=names)
            doc = spot.extract(cat, mdir, spot_id, out)  # pyright: ignore[reportArgumentType]
            facts = {"tapTargets": len(doc["characters"]), "skeletons": len(doc["skeletons"])}
            arts = []
            for rel, p in _tree(out):
                if rel == "spot.json":
                    arts.append(file_artifact(task, store, rel, p.read_bytes(), "spot.json", "json", facts))
                else:
                    ext = _ext(rel)
                    arts.append(file_artifact(task, store, rel, p.read_bytes(), "spine.file", ext))
        return Output(arts, [])
