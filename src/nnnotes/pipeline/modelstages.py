"""The Spine skeleton stage, written by the extractor of `nnnotes export spot` (unchanged) from the bundles of its
tasks.

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

The task names its extractor as its one atom ("spot.write_skeleton"), whose id carries the versions of the libraries
the files depend on; this module imports UnityPy only when a task runs.
"""

from __future__ import annotations

import re

from ..unity.atoms import impl_id
from . import contract, link
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

SPINE = "spine.skeleton"
CENSUS = "unity.census"
SCRIPTS = "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")
SKELETON_CLASS = "SkeletonDataAsset"
# the libraries the extractor's output depends on (export._LIBS, the salt of export's PNG cache)
LIBRARIES = ("UnityPy", "Pillow", "texture2ddecoder", "astc-encoder-py", "etcpak", "numpy")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
_EXT = re.compile(r"[0-9a-z]+")


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
    table = contract.loads(env.depot.read(env.input_of(SCRIPTS_TASK, "scripts").sha256))["scripts"]
    scripts = {oid for oid, entry in table.items() if entry.rsplit("|", 1)[-1] == SKELETON_CLASS}
    files: dict[str, str] = {}
    externals: dict[str, list[str]] = {}
    subjects: dict[str, tuple] = {}
    for tid in env.tasks(CENSUS):
        stable = contract.parse_task_id(tid, CENSUS)[1]
        doc = contract.loads(env.depot.read(env.artifact(tid, "census")["content"]["sha256"]))
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
        """From the closure's bytes (least squares over the skeletons of a catalog)."""
        size = sum(i.size for i in inputs)
        return Cost(0.27 + size * 1.9e-7, (440 << 20) + 20 * size)

    def run(self, task, depot) -> Output:
        _check_atoms(task, self.ATOMS)
        from ..content import spot
        from ..unity import reader as unity

        oid = task.context["object"]
        paths = [depot.open_input(i) for i in task.inputs if i.role.startswith("bundle:")]
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
                    depot.add(data, ext),
                    contract.provenance(task),
                    {"kind": kind, "format": ext, "facts": facts},
                )
            )
        return Output(arts, [contract.item(oid, "exported", artifacts=[a["id"] for a in arts], cls="MonoBehaviour")])
