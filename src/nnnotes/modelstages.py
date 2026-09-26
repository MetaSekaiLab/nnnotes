"""The model stages: Live2D models and Spine skeletons, written by the extractors of `nnnotes live2d` and
`nnnotes spot` (unchanged) from the bundles of their tasks.

live2d.model, one task per Live2D model key (Character/Live2D/<group>/<name>/model/<name>, the keys `nnnotes live2d`
takes) whose own bundle (the first dependency of the key's location) is selected:
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
               the atlas pages (PNG), as spot.write_skeleton writes them for `nnnotes spot`
    item       the asset: exported (its artifacts), or failed with the writer's error

Both tasks name their extractor as their one atom ("live2d.extract_model", "spot.write_skeleton"), whose id carries
the versions of the libraries the files depend on; this module imports UnityPy only when a task runs.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import catalogdb, contract, link
from .atoms import impl_id
from .catalog import Bundle
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

LIVE2D = "live2d.model"
SPINE = "spine.skeleton"
CENSUS = "unity.census"
SCRIPTS = "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")
# the model keys of `nnnotes live2d` (webmodel.MODEL_KEY; webmodel imports UnityPy)
MODEL_KEY = re.compile(r"Character/Live2D/(?P<group>[^/]+)/(?P<name>[^/]+)/model/(?P=name)")
SKELETON_CLASS = "SkeletonDataAsset"
# the libraries the extractors' output depends on (export._LIBS, the salt of export's PNG cache)
LIBRARIES = ("UnityPy", "Pillow", "texture2ddecoder", "astc-encoder-py", "etcpak", "numpy")
_ASSET_PREFIXES = ("Assets/", "Packages/")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
_EXT = re.compile(r"[0-9a-z]+")
# live2d.extract_model files -> artifact kinds (by suffix, the first that matches)
LIVE2D_KINDS = ((".model3.json", "live2d.model3"), (".prefab.json", "live2d.prefab"),
                (".physics3.json", "live2d.physics3"), (".exp3.json", "live2d.expression"),
                (".motion3.json", "live2d.motion"), ("motions/_fades.json", "live2d.fades"), (".moc3", "live2d.moc3"),
                ("textures/", "live2d.texture"))


def _message(e: BaseException) -> str:
    return _ADDRESS.sub("0x?", f"{type(e).__name__}: {e}")[:500]


def _ext(name: str, default: str = "bin") -> str:
    ext = name.rsplit("/", 1)[-1].rpartition(".")[2].lower() if "." in name.rsplit("/", 1)[-1] else ""
    return ext if _EXT.fullmatch(ext) else default


def _check_atoms(task, atoms: dict) -> None:
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
    return sorted(ins, key=lambda i: int(i.role[len(prefix):]))


# ---------------------------------------------------------------- the catalog of a task
class Closures:
    """Key lookup and dependency closures of a catalog index, as Catalog._entry and Catalog.resolve give them on the
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


class TaskCatalog:
    """What the model extractors ask of a Catalog (live2d.extract_model through export.Exporter: fetch_key, _entry,
    apk), answered from a task: its key, the internal id of the key's location and the bundles of the key's closure
    in load order, each bundle read through the store (Store.open_input: its locators, checked against its content
    id). Another key is not in it (KeyError). `apk` is not None: the extractors need the APK's bundles, and those of
    the closure are inputs of the task like the others."""

    def __init__(self, key: str, internal_id: str, bundles: list[Input], store):
        self.key, self.internal_id, self.bundles, self.store = key, internal_id, list(bundles), store
        self.apk = APK_FROM_INPUTS

    @classmethod
    def from_task(cls, task, store) -> "TaskCatalog":
        """The catalog of a live2d.model task: its subject, context internalId and inputs "bundle:<n>"."""
        return cls(task.subject, task.context["internalId"], _ordered(task), store)

    def _check(self, key: str) -> None:
        if key != self.key:
            raise KeyError(key)

    def has(self, key: str) -> bool:
        return key == self.key

    def keys(self, prefix: str = "") -> list[str]:
        return [self.key] if self.key.startswith(prefix) else []

    def _entry(self, key: str) -> dict:
        self._check(key)
        return {"primary_key": key, "internal_id": self.internal_id}

    def entries_for(self, key: str) -> list[dict]:
        return [self._entry(key)] if key == self.key else []

    def resolve(self, key: str) -> list[Bundle]:
        """The closure as Bundles in load order: offset = the position, name = the input's file name (its content
        id when it has none); internal_id is empty and remote True (a task has a file's content, not its place in
        a catalog; every bundle is read from the inputs)."""
        self._check(key)
        return [Bundle(i, "", inp.name or inp.sha256, True) for i, inp in enumerate(self.bundles)]

    def fetch_key(self, key: str) -> list[Path]:
        """The files of the closure's bundles, in load order."""
        self._check(key)
        return [self.store.open_input(inp) for inp in self.bundles]


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


class Live2DStage(Stage):
    """live2d.model: a Live2D model key -> the files of `nnnotes live2d` (module documentation). Subjects and inputs
    come from the facts "index" (the catalog index), "selected" and "bundles" ({stable name: Input "bundle"})."""
    name = LIVE2D
    version = 1
    ATOMS = {"live2d.extract_model": impl_id(LIBRARIES, 1)}
    LAYOUT_ROOT = "{subject}"

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

    def subjects(self, env) -> list[str]:
        c, selected = self.closures(env), set(env.fact("selected"))
        return [k for k in c.keys() if MODEL_KEY.fullmatch(k) and c.own_bundle(k) in selected]

    def inputs(self, subject: str, env) -> list[Input]:
        bundles, what = env.fact("bundles"), f"{self.name} {subject}"
        names = self.closures(env).closure_bundles(subject)
        width = max(3, len(str(len(names) - 1)))
        out = []
        for i, stable in enumerate(names):
            inp = _fact_input(bundles, stable, what)
            out.append(Input(f"bundle:{i:0{width}d}", inp.sha256, inp.size, inp.name, inp.locators))
        return out

    def context(self, subject: str, env) -> dict:
        return {"internalId": self.closures(env).entry(subject)["internalId"]}

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the closure's bytes (least squares over the models of a catalog: CPU seconds; peak resident bytes
        of a worker process)."""
        size = sum(i.size for i in inputs)
        return Cost(0.9 + size * 2.2e-7, (420 << 20) + 10 * size)

    def run(self, task, store) -> Output:
        _check_atoms(task, self.ATOMS)
        from . import live2d
        cat = TaskCatalog.from_task(task, store)
        arts = []
        with tempfile.TemporaryDirectory(prefix="nnnotes-live2d-") as d:
            summary = live2d.extract_model(cat, task.subject, Path(d))
            for rel, p in _tree(Path(d)):
                kind = live2d_kind(rel)
                facts = {k: summary[k] for k in sorted(summary)} if kind == "live2d.model3" else {}
                ext = _ext(rel)
                arts.append(contract.artifact(contract.artifact_id(task.id, rel), store.add(p.read_bytes(), ext),
                                              contract.provenance(task), {"kind": kind, "format": ext,
                                                                          "facts": facts}))
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
                if o["class"] == "MonoBehaviour" and s and s.get("file") is not None and \
                        contract.object_id(s["file"], s["pathId"]) in scripts:
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
    """spine.skeleton: a SkeletonDataAsset -> its Spine files as `nnnotes spot` writes them (module documentation).
    Subjects from the census results, the script table (link.scripts:all) and the fact "selected"; inputs from the
    fact "bundles". Waits for census tasks that have not run."""
    name = SPINE
    version = 1
    after = (CENSUS, SCRIPTS)
    ATOMS = {"spot.write_skeleton": impl_id(LIBRARIES, 1)}

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
        _check_atoms(task, self.ATOMS)
        from . import spot, unity
        oid = task.context["object"]
        paths = [store.open_input(i) for i in task.inputs if i.role.startswith("bundle:")]
        env = unity.load_closure(paths)
        file, pid = contract.parse_object_id(oid)
        written: dict[str, bytes] = {}
        try:
            sf = env.get_cab(file)
            o = sf.objects[pid] if sf is not None and pid in getattr(sf, "objects", {}) else None
            if o is None:
                raise KeyError(f"{oid} is not in the task's bundles")
            record = spot.write_skeleton(o, written.__setitem__)
        except (MemoryError, OSError):
            raise
        except Exception as e:
            return Output([], [contract.item(oid, "failed", why=contract.reason("failed.export", _message(e)),
                                             cls="MonoBehaviour")])
        arts = []
        for name in sorted(written):
            data = written[name]
            if name == record["skeleton"]:
                ext, kind, facts = _ext(name), "spine.skeleton", record
            elif name in record["atlases"]:
                ext, kind, facts = _ext(name, "atlas"), "spine.atlas", {}
            else:
                ext, kind, facts = "png", "spine.page", {}
            arts.append(contract.artifact(contract.artifact_id(task.id, name), store.add(data, ext),
                                          contract.provenance(task), {"kind": kind, "format": ext, "facts": facts}))
        return Output(arts, [contract.item(oid, "exported", artifacts=[a["id"] for a in arts], cls="MonoBehaviour")])
