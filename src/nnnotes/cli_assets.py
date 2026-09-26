"""The asset commands: the default orchestration of the stages (export), its plan, single tasks, catalog versions and
the store.

    nnnotes export -o OUT [--layout original[,cas]] [--select group:G|key:PREFIX|bundle:GLOB ...]
                   [--views all|none|a,b] [--store DIR] [--workers N] [--memory GiB] [--fetch-workers N]
                   [--catalog-version L|SHA] [--png-level N] [--only-class C,...] [--link copy|hard] [--strict]
                   [--dry-run] [--explain]
    nnnotes plan [the selection and parameters of export] [-o OUT] [--json] [--since RUN] [--check] [--census]
                 [--emit-tasks DIR] [--why TASK]
    nnnotes run-stage TASK.json|DIR [...] [--store DIR] [--fetch] [--force]
    nnnotes catalogs list [--json] | import FILE [--label L] | fetch [--label L] | diff A B [--json]
    nnnotes store verify [--quick]

The pipeline is the registry STAGE_SOURCES in order: each entry names the module and attribute of a stage (a Stage
class) or of a family of stages (a function returning them, `view.`). An entry whose module or attribute is not
installed is left out of the pipeline and named by --explain. Stage modules import heavy libraries (UnityPy) only
when a task runs, so `plan`, and `run-stage` of stages that do not read bundles, never load them.

Facts the stages read while their tasks are described (stages.Env):

    catalogs   {"main": [Input remote, Input apk]}: the catalog version's files in <store>/catalogs/
    index      the catalog index document (nnnotes.catalog-index/1)
    bundles    {stable bundle name: Input "bundle"} of the selected bundles and their dependencies (a bundle whose
               bytes are not known yet: a Pending "fetch:<file name>")
    selected   the selected bundles' stable names, sorted (the subjects of the export stages)
    raw        {stable raw file name: Input "raw"} of the selected raw files (only with a stage that reads them)
    boot       the Input of the game's boot data (with a CRI stage: cristages.boot_input; None without an APK)
    master     the decoded master data directory, or None
    views      the names of the selected views, sorted

Selection (--select, repeatable; the union; none: everything): `group:G` the bundles of an Addressables group (the
stable name up to `_assets_`, else up to its first `_`), `key:PREFIX` the bundles and raw files the catalog keys
under PREFIX load (their dependency closure), `bundle:GLOB` bundles and raw files whose stable name matches. The
census also covers the bundles the selected ones depend on (the dependencies of the keys they hold: a key's first
dependency is its own bundle), so script and atlas references resolve.

Task cost estimates (scheduling only: never part of a key) are each stage's estimate scaled per stage by the
costs measured in earlier runs (<store>/costs/calibration.json, updated after every export).

Exit codes: export 0 complete (unsupported objects allowed), 1 item or task failures (reports written), 2 usage or
settings, 3 aborted (results written so far stay valid); run-stage 0, 1 (a task failed or a result is partial), 2,
4 an incompatible task (nothing of the batch runs); plan --check 1 when anything would run or change.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import threading
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module, util
from pathlib import Path
from types import SimpleNamespace

from . import contract
from .config import ConfigError, describe as describe_setting, usable_cpus, use
from .contract import Cost, IncompatibleTask, Input, Task
from .stages import Env, Pending, Stage, describe, execute

GIB = 1 << 30
LAYOUT_NAMES = ("original", "cas")
FETCH_WORKERS = 8
RECYCLE_TASKS = 500
RECYCLE_RSS = 3 * GIB
REPORTS = "_reports"
SELECT_KINDS = ("group", "key", "bundle")
RAW_STAGES = ("cri.audio", "cri.movie")
EXPORT_STAGE = "unity.export"
CATALOG_SUBJECT = "main"
EXIT_FAILED, EXIT_USAGE, EXIT_ABORTED, EXIT_INCOMPATIBLE = 1, 2, 3, 4


# ---------------------------------------------------------------- the stage registry
@dataclass(frozen=True)
class StageSource:
    """Where a stage (or, for a name ending with '.', a family of stages) comes from. `output`: its artifacts are
    placed in the layouts; `root`: the layout root of its artifacts without a container path (format fields
    {stage} and {subject}; a stage's LAYOUT_ROOT attribute wins)."""
    name: str
    target: str
    output: bool = False
    root: str | None = None

    def matches(self, stage: str) -> bool:
        return stage.startswith(self.name) if self.name.endswith(".") else stage == self.name


STAGE_SOURCES = (
    StageSource("catalog.index", "nnnotes.catalogdb:IndexStage"),
    StageSource("unity.census", "nnnotes.census:CensusStage"),
    StageSource("link.scripts", "nnnotes.link:ScriptsStage"),
    StageSource("link.addresses", "nnnotes.link:AddressesStage"),
    StageSource("unity.export", "nnnotes.objexport:ExportStage", output=True, root="_bundles/{subject}"),
    StageSource("sprite.crop", "nnnotes.objexport:SpriteCropStage", output=True),
    StageSource("cri.audio", "nnnotes.cristages:AudioStage", output=True),
    StageSource("cri.movie", "nnnotes.cristages:MovieStage", output=True),
    StageSource("live2d.model", "nnnotes.modelstages:Live2DStage", output=True),
    StageSource("spine.skeleton", "nnnotes.modelstages:SpineStage", output=True),
    StageSource("link.artifacts", "nnnotes.link:ArtifactsStage"),
    StageSource("view.", "nnnotes.views:stages", output=True),
)


def _present(module: str) -> bool:
    try:
        return util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def load_source(src: StageSource) -> list[Stage] | None:
    """The stages of a source, sorted by name; None when its module or attribute is not installed. Errors raised
    while importing an installed module are not hidden."""
    module, _, attr = src.target.partition(":")
    if not _present(module):
        return None
    obj = getattr(import_module(module), attr, None)
    if obj is None:
        return None
    stages = [obj()] if isinstance(obj, type) and issubclass(obj, Stage) else list(obj())
    for s in stages:
        if not src.matches(s.name):
            raise ValueError(f"{src.target} gives stage {s.name!r}, not {src.name!r}")
    return sorted(stages, key=lambda s: s.name)


@dataclass
class Installed:
    """The installed stages in pipeline order, the source of each, and the sources not installed."""
    stages: dict
    sources: dict
    missing: list


def installed(names=None) -> Installed:
    """The stages of STAGE_SOURCES (`names`: only the sources of these stage names, for run-stage)."""
    stages, of, missing = {}, {}, []
    for src in STAGE_SOURCES:
        if names is not None and not any(src.matches(n) for n in names):
            continue
        got = load_source(src)
        if got is None:
            missing.append(src.name + ("*" if src.name.endswith(".") else ""))
            continue
        for s in got:
            if s.name in stages:
                raise ValueError(f"stage {s.name} given twice")
            stages[s.name], of[s.name] = s, src
    return Installed(stages, of, missing)


def layout_root(stage: Stage, src: StageSource, subject: str) -> str | None:
    root = getattr(stage, "LAYOUT_ROOT", None) or src.root
    return None if root is None else root.format(stage=stage.name, subject=subject)


# ---------------------------------------------------------------- selection
def parse_selection(items) -> list[str]:
    """The normalized selection: sorted unique `kind:value` items, ["all"] for none."""
    out = set()
    for s in items or ():
        kind, sep, value = s.partition(":")
        if not sep or kind not in SELECT_KINDS or not value:
            raise ValueError(f"--select {s}: expected group:<group>, key:<key prefix> or bundle:<glob>")
        out.add(f"{kind}:{value}")
    return sorted(out) or ["all"]


def bundle_group(stable: str) -> str:
    """The Addressables group of a bundle: its stable name up to `_assets_`, else up to its first `_`."""
    head, sep, _ = stable.partition("_assets_")
    return head if sep else stable.split("_", 1)[0]


@dataclass
class Selection:
    selected: list          # stable names of the selected bundles (export subjects)
    census: list            # the selected bundles and the bundles they depend on
    raw: list               # stable names of the selected raw files


def select(index: dict, selection: list[str]) -> Selection:
    """The bundles and raw files a selection names in a catalog index (module documentation)."""
    from . import catalogdb
    bundles = sorted(b["stable"] for b in index["bundles"])
    raws = sorted(r["stable"] for r in index["rawFiles"])
    if selection == ["all"]:
        return Selection(bundles, bundles, raws)
    files = catalogdb.file_refs(index)
    reach = catalogdb.closures(index)
    chosen: set = set()
    for item in selection:
        kind, _, value = item.partition(":")
        if kind == "group":
            chosen |= {("bundle", s) for s in bundles if bundle_group(s) == value}
        elif kind == "bundle":
            chosen |= {("bundle", s) for s in bundles if fnmatch.fnmatchcase(s, value)}
            chosen |= {("raw", s) for s in raws if fnmatch.fnmatchcase(s, value)}
        else:
            for loc in index["locations"]:
                if loc["kind"] not in catalogdb.FILE_KINDS and loc["primaryKey"].startswith(value):
                    chosen |= reach[loc["id"]]
    deps = set(chosen)
    for loc in index["locations"]:              # a key's first dependency is the bundle that holds it
        if loc["kind"] not in catalogdb.FILE_KINDS and loc["dependencies"] and                 files.get(loc["dependencies"][0]) in chosen:
            deps |= reach[loc["id"]]
    pick = lambda s, kind: sorted(n for k, n in s if k == kind)   # noqa: E731
    return Selection(pick(chosen, "bundle"), pick(deps, "bundle"), pick(chosen, "raw"))


# ---------------------------------------------------------------- settings
def store_root(args, cfg) -> Path:
    """--store, else [paths] store, else <[paths] cache>/store."""
    p = getattr(args, "store", None)
    if p:
        return Path(p).expanduser()
    p = cfg.path("paths", "store")
    if p is not None:
        return p
    cache = cfg.path("paths", "cache")
    if cache is not None:
        return cache / "store"
    raise ConfigError("setting paths.store is not set (nor paths.cache, whose store/ directory is the default): "
                      f"give it as {describe_setting('paths', 'store', '--store')}")


def default_memory() -> int | None:
    """80 % of the physical memory (Linux), else None (no budget)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(int(line.split()[1]) * 1024 * 0.8)
    except (OSError, ValueError):
        pass
    return None


def _bundle_key(cfg):
    from .addressables import BundleKey
    return BundleKey(cfg.hex("bundle", "key", 16), cfg.hex("bundle", "nonce_seed"))


class CatalogFetcher:
    """The fetcher of "catalog" locators ({catalog: version id, location: location id}): the file is fetched through
    that catalog version of the store (the settings' CDN, bundle key and APK) into the cache. Crosses to worker
    processes as its settings."""

    def __init__(self, store_root_, cache, cfg):
        self.root, self.cache, self.cfg = Path(store_root_), Path(cache) if cache else None, cfg
        self._catalogs: dict = {}
        self._lock = threading.Lock()

    def __getstate__(self):
        return {"root": self.root, "cache": self.cache, "cfg": self.cfg}

    def __setstate__(self, state):
        self.__init__(state["root"], state["cache"], state["cfg"])

    def catalog(self, vid: str):
        """(Catalog, {location id: location}) of a stored catalog version."""
        from . import catalogdb
        from .catalog import Catalog
        with self._lock:
            hit = self._catalogs.get(vid)
            if hit is None:
                if self.cache is None:
                    raise self.cfg.missing("paths", "cache")
                db = catalogdb.CatalogDB(self.root)
                v = next((x for x in db.versions() if x["id"] == vid), None)
                if v is None:
                    raise FileNotFoundError(f"catalog version {vid[:12]} is not in the store")
                remote, apk = db.catalog_bytes(v)
                cfg, region = self.cfg, v.get("region")
                cat = Catalog(remote, self.cache, cdn=lambda: cfg.cdn(region or cfg.region()),
                              bundle_key=lambda: _bundle_key(cfg), apk=cfg.path("paths", "apk"))
                hit = self._catalogs[vid] = (cat, catalogdb.by_id(catalogdb.index(remote, apk)))
            return hit

    def fetch_location(self, vid: str, lid: str) -> Path:
        from .addressables import remote_path
        from .catalog import Bundle, file_name
        cat, locs = self.catalog(vid)
        loc = locs.get(lid)
        if loc is None:
            raise FileNotFoundError(f"catalog version {vid[:12]} has no location {lid}")
        iid = loc["internalId"]
        if loc["kind"] == "bundle":
            return cat.fetch(Bundle(0, iid, file_name(iid), remote_path(iid) is not None))
        if remote_path(iid) is None:
            return self._apk_file(iid)
        return cat.fetch_raw({"internal_id": iid})

    def _apk_file(self, internal_id: str) -> Path:
        """A raw file of the APK as stored, through the cache (raw/<its path below the APK's Addressables
        directory>, where a CDN file of that path would be); KeyError when the APK does not hold it."""
        import zipfile
        from .cache import write_atomic
        from .catalog import APK_AA_DIR
        if self.cache is None:
            raise self.cfg.missing("paths", "cache")
        apk = self.cfg.path("paths", "apk")
        if apk is None:
            raise self.cfg.missing("paths", "apk")
        rel = apk_rel(internal_id)
        dst = self.cache / "raw" / rel
        if not (dst.is_file() and dst.stat().st_size > 0):
            with zipfile.ZipFile(apk) as z:
                data = z.read(APK_AA_DIR + rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(dst, data)
        return dst

    def __call__(self, inp: Input, loc: dict) -> Path:
        return self.fetch_location(loc["catalog"], loc["location"])


def apk_rel(internal_id: str) -> str:
    """The path of an APK-local location's file below the APK's Addressables directory (catalog.APK_AA_DIR)."""
    from .catalog import LOCAL_PREFIX
    return internal_id[len(LOCAL_PREFIX):].lstrip("/") if internal_id.startswith(LOCAL_PREFIX) else internal_id


def _worker_init(store, cfg) -> None:
    """Every worker process: the settings of the command."""
    use(cfg)


def _log(message: str) -> None:
    from .orchestrate import _log as log
    log(message)


# ---------------------------------------------------------------- cost calibration
CALIBRATION = "calibration.json"                  # <store>/costs/calibration.json
MIN_SAMPLES = 10


class Calibrated:
    """A stage whose cost estimates are scaled by measured factors. Estimates only order and admit tasks: keys and
    outputs do not change."""

    def __init__(self, stage: Stage, cpu: float = 1.0, peak: float = 1.0):
        self._stage, self._cpu, self._peak = stage, float(cpu), float(peak)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stage, name)

    def estimate(self, subject, env, inputs):
        c = self._stage.estimate(subject, env, inputs)
        return Cost(c.cpu_seconds * self._cpu, int(c.peak_bytes * self._peak))


def load_calibration(root: Path) -> dict:
    """{stage: {"cpu": factor, "peak": factor, "samples": n}} of a store ({} when none was measured)."""
    try:
        doc = contract.loads((Path(root) / "costs" / CALIBRATION).read_bytes())
    except (OSError, ValueError):
        return {}
    return doc.get("stages", {}) if isinstance(doc, dict) else {}


def calibrated(stages: dict, factors: dict) -> dict:
    return {n: Calibrated(s, factors[n]["cpu"], factors[n]["peak"]) if n in factors else s for n, s in stages.items()}


def _clamp(x: float) -> float:
    return round(min(20.0, max(0.05, x)), 4)


def calibrate(log: list[dict], factors: dict, worker_bytes: int) -> dict:
    """New factors from a run log: per stage with at least MIN_SAMPLES measured tasks, CPU by the ratio of the
    measured (the task's process and its child processes, such as external tools) to the estimated sums, peak memory
    by the 95th percentile of measured (less a worker's own memory) over estimated. Where the child processes' CPU
    time cannot be measured (childCpuSeconds null) the CPU factor stays. The logged estimates carry the old factors,
    which the new ones build on."""
    est = {e["task"]: e["estimate"] for e in log if e.get("event") == "start" and e.get("estimate")}
    samples: dict[str, list] = {}
    for e in log:
        if e.get("event") == "end" and e.get("ok") and e.get("cost") and e["task"] in est:
            samples.setdefault(contract.parse_task_id(e["task"])[0], []).append((est[e["task"]], e["cost"]))
    out = {k: dict(v) for k, v in factors.items()}
    for stage, pairs in samples.items():
        pairs = [(a, c) for a, c in pairs if a["cpuSeconds"] > 0 and a["peakBytes"] > 0]
        if len(pairs) < MIN_SAMPLES:
            continue
        old = factors.get(stage, {"cpu": 1.0, "peak": 1.0})
        if any(c.get("childCpuSeconds", 0) is None for _, c in pairs):
            cpu = 1.0                                   # child processes not measured here: keep the CPU factor
        else:
            cpu = sum(c["cpuSeconds"] for _, c in pairs) / sum(a["cpuSeconds"] for a, _ in pairs)
        ratios = sorted(max(0, (c.get("peakRssBytes") or 0) - worker_bytes) / a["peakBytes"] for a, c in pairs)
        peak = ratios[min(len(ratios) - 1, int(0.95 * len(ratios)))]
        if not any(c.get("peakRssBytes") for _, c in pairs):
            peak = 1.0                                  # not measurable on this platform
        out[stage] = {"cpu": _clamp(old["cpu"] * cpu), "peak": _clamp(old["peak"] * max(peak, 0.05)),
                      "samples": len(pairs)}
    return dict(sorted(out.items()))


def write_calibration(root: Path, factors: dict) -> None:
    from .store import write_file
    write_file(Path(root) / "costs" / CALIBRATION, contract.encode({"stages": factors}))


# ---------------------------------------------------------------- the workspace of a command
class Workspace:
    """What export and plan share: the store, the catalog version, its index, the selection, the pipeline and its
    parameters, the facts."""

    def __init__(self, args, cfg, common, log=_log):
        from .catalogdb import CatalogDB
        from .store import Store
        self.args, self.cfg, self.common, self.log = args, cfg, common, log
        self.root = store_root(args, cfg)
        self.cache = cfg.path("paths", "cache")
        self.apk = cfg.path("paths", "apk")
        self.fetcher = CatalogFetcher(self.root, self.cache, cfg)
        self.store = Store(self.root, cache=self.cache, fetch=self.fetcher)
        self.db = CatalogDB(self.root)
        try:
            self.selection = parse_selection(getattr(args, "select", None))
        except ValueError as e:
            args.usage(str(e))
        self.installed = installed()
        self.calibration = load_calibration(self.root)
        self.scheduled = calibrated(self.installed.stages, self.calibration)   # the stages with calibrated costs
        self.version, self.remote, self.apk_catalog = self._catalog_version()
        self._index = None
        self._problems: list = []
        self._apk_names: set | None = None
        self._facts: dict = {}
        self._names: dict = {}
        self._done: dict = {}                             # task id -> key of the tasks with a result

    # ---------------------------------------------------------------- catalog
    def _catalog_version(self):
        from . import catalogdb
        ref = getattr(self.args, "catalog_version", None)
        if ref:
            try:
                try:
                    v = self.db.get(ref)
                except ValueError:
                    v = self.db.get(ref, self.cfg.get("catalog", "region"), self.cfg.get("catalog", "language"))
            except (KeyError, ValueError) as e:
                self.args.usage(f"--catalog-version {ref}: {e.args[0] if e.args else e}")
            remote, apk = self.db.catalog_bytes(v)
            return v, remote, apk
        cat = self.common.open_catalog(self.cfg, bundles=False)
        src = cat.sources()
        remote, apk = src["remote"], src.get("apk")
        vid = catalogdb.version_id(contract.sha256(remote), contract.sha256(apk) if apk is not None else None)
        v = next((x for x in self.db.versions() if x["id"] == vid), None)
        if v is None:
            v = self.db.add(remote, apk, region=self.cfg.get("catalog", "region"),
                            language=self.cfg.get("catalog", "language"), apk_version_name=_apk_version(self.apk))
        return v, remote, apk

    def catalogs_fact(self) -> dict:
        return {CATALOG_SUBJECT: self.db.inputs(self.version)}

    def index_task(self) -> Task:
        stage = self.installed.stages["catalog.index"]
        return describe(stage, CATALOG_SUBJECT, None, Env(self.store, {"catalogs": self.catalogs_fact()}))

    def index(self) -> dict:
        """The catalog index: from the store when catalog.index has run, else computed here."""
        if self._index is None:
            from . import catalogdb
            t = self.index_task()
            doc = self.store.result(t.key)
            if doc is not None:
                (rec,) = [a for a in doc["artifacts"] if a["id"] == contract.artifact_id(t.id, "index")]
                self._index = contract.loads(self.store.read(rec["content"]["sha256"]))
            else:
                self._index = catalogdb.index(self.remote, self.apk_catalog)
        return self._index

    def run_index(self) -> None:
        execute(self.index_task(), self.store, self.installed.stages)
        self._index = None

    def context(self) -> dict:
        v = self.version
        return {"region": self.cfg.get("catalog", "region"),
                "language": v.get("language") or self.cfg.get("catalog", "language"),
                "catalog": {"id": v["id"], "sha": v["remote"]["sha256"],
                            "label": v["labels"][0] if v["labels"] else None,
                            "resourceVersion": v.get("resourceVersion"),
                            "apkCatalogSha": v["apk"]["sha256"] if v.get("apk") else None,
                            "apkVersionName": v.get("apkVersionName")}}

    def master_context(self, results) -> dict | None:
        """The master data a run read: {"version": the `version` of <master>/MasterManifest.json (None without
        one), "tables": {table: sha256} of the tables its view tasks read}; None when no view task has a result."""
        from .views import MASTER_ROLE
        tables, any_view = {}, False
        for tid, doc in results:
            if not tid.startswith("view."):
                continue
            any_view = True
            for role, sha in doc["keyParts"]["inputs"]:
                if role.startswith(MASTER_ROLE):
                    tables[role[len(MASTER_ROLE):]] = sha
        if not any_view:
            return None
        version = None
        md = self.master()
        if md is not None:
            try:
                version = json.loads((md / "MasterManifest.json").read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError, AttributeError):
                version = None
        return {"version": version, "tables": dict(sorted(tables.items()))}

    # ---------------------------------------------------------------- pipeline
    def master(self) -> Path | None:
        section, key = self.cfg.master(self.cfg.get("catalog", "region"))
        p = self.cfg.path(section, key)
        if p is not None and not p.is_dir():
            raise ConfigError(f"setting {section}.{key}: directory {p} not found")
        return p

    def views(self) -> list[str]:
        """The selected view stages (--views; default: every installed view when master data is set)."""
        avail = [n for n in self.installed.stages if n.startswith("view.")]
        spec = getattr(self.args, "views", None)
        if spec is None:
            return avail if avail and self.master() is not None else []
        if spec == "none":
            return []
        want = avail if spec == "all" else sorted({"view." + v.strip() for v in spec.split(",") if v.strip()})
        unknown = [w[5:] for w in want if w not in avail]
        if unknown:
            self.args.usage(f"--views: no view {', '.join(unknown)} (views: "
                            f"{', '.join(a[5:] for a in avail) or 'none installed'})")
        if want and self.master() is None:
            raise self.cfg.missing(*self.cfg.master(self.cfg.get("catalog", "region")))
        return want

    def pipeline(self) -> list[str]:
        views = set(self.views())
        return [n for n in self.installed.stages if not n.startswith("view.") or n in views]

    def params(self, pipeline: list[str]) -> dict:
        """{stage: parameters} from the flags; a flag no stage of the pipeline takes is a usage error."""
        stages, out = self.installed.stages, {}
        level = getattr(self.args, "png_level", None)
        if level is not None:
            if not 0 <= level <= 9:
                self.args.usage(f"--png-level {level}: expected 0 to 9")
            takers = [n for n in pipeline if "png" in stages[n].PARAMS]
            if not takers:
                self.args.usage("--png-level: no stage of the pipeline encodes PNG")
            for n in takers:
                base = stages[n].PARAMS["png"]
                out.setdefault(n, {})["png"] = {**(base if isinstance(base, dict) else {}), "level": level}
        only = getattr(self.args, "only_class", None)
        if only:
            classes = sorted({c.strip() for c in only.split(",") if c.strip()})
            takers = [n for n in pipeline if "classes" in stages[n].PARAMS]
            if not classes or not takers:
                self.args.usage("--only-class: " + ("no class given" if not classes
                                                    else "no stage of the pipeline selects classes"))
            for n in takers:
                out.setdefault(n, {})["classes"] = classes
        for n, p in out.items():
            try:
                stages[n].normalize(p)
            except ValueError as e:
                self.args.usage(str(e))
        return out

    def workers(self) -> int:
        w = getattr(self.args, "workers", None)
        return max(0, w) if w is not None else usable_cpus()

    def memory(self) -> int | None:
        m = getattr(self.args, "memory", None)
        return int(m * GIB) if m is not None else default_memory()

    # ---------------------------------------------------------------- inputs
    def facts(self, pipeline: list[str], fetch: bool) -> dict:
        """The planning facts (module documentation). `fetch`: fetch the files whose content is not known yet
        (else they are Pending); what cannot be read is in problems()."""
        index = self.index()
        sel = select(index, self.selection)
        self._problems = []
        bundles = {b["stable"]: b for b in index["bundles"]}
        inputs = self._inputs("bundle", [bundles[s] for s in sel.census], fetch)
        sel.selected = [s for s in sel.selected if s in inputs]          # files that cannot be read: problems()
        facts = {"catalogs": self.catalogs_fact(), "index": index, "selected": sel.selected,
                 "master": self.master() if any(n.startswith("view.") for n in pipeline) else None,
                 "views": sorted(n[5:] for n in pipeline if n.startswith("view.")),
                 "bundles": inputs, "raw": Inputs({})}
        if any(n in pipeline for n in RAW_STAGES):
            from .cristages import boot_input
            raws = {r["stable"]: r for r in index["rawFiles"]}
            facts["raw"] = self._inputs("raw", [raws[s] for s in sel.raw], fetch)
            facts["boot"] = boot_input(self.store, self.apk)
        self.selected = sel
        self._facts, self._names = facts, {}
        return facts

    def problems(self) -> list[dict]:
        """Files of the selection that could not be read: {kind, stable, name, code, message}, sorted."""
        return sorted(self._problems, key=lambda p: (p["kind"], p["stable"]))

    def _cache_rel(self, kind: str, entry: dict, loc: dict) -> str:
        from .addressables import remote_path
        if kind == "bundle":
            return f"bundles/{entry['name']}"
        rel = remote_path(loc["internalId"])
        if rel is None:                                   # a raw file of the APK (CatalogFetcher._apk_file)
            rel = apk_rel(loc["internalId"])
        return "raw/" + rel.lstrip("/")

    def _inputs(self, kind: str, entries: list[dict], fetch: bool) -> dict:
        from .catalogdb import by_id
        locs = by_id(self.index())
        memo_kind = "bundles" if kind == "bundle" else "raw"
        out, todo = {}, []
        for e in entries:
            loc = locs[e["location"]]
            rel = self._cache_rel(kind, e, loc)
            locators = ([{"kind": "cache", "path": rel}] if rel else []) + [
                {"kind": "catalog", "catalog": self.version["id"], "location": e["location"]}]
            path = self.cache / rel if self.cache is not None and rel else None
            if path is not None and path.is_file() and path.stat().st_size > 0:
                todo.append((e, locators, path))
                continue
            known = self.store.named(memo_kind, e["name"]) if e["name"] != e["stable"] else None
            if known is not None:                # a hashed file name: its recorded identity holds
                out[e["stable"]] = Input(kind, known[0], known[1], e["name"], tuple(locators))
            elif not e["remote"] and self.apk is None:
                self._problem(kind, e, "source.absent", "a file of the APK, and [paths] apk is not set")
            elif not e["remote"] and not self._in_apk(loc["internalId"]):
                self._problem(kind, e, "source.absent", "not in the APK")
            elif not fetch:
                out[e["stable"]] = Pending(f"fetch:{e['name']}")
            else:
                todo.append((e, locators, None))

        def one(job):
            e, locators, path = job
            try:
                if path is None:
                    path = self.fetcher.fetch_location(self.version["id"], e["location"])
                sha, size = self.store.identify(path, memo_kind, e["name"])
            except ConfigError:
                raise
            except KeyError as x:                # not in the APK
                self._problem(kind, e, "source.absent", f"not in the APK: {x.args[0] if x.args else x}")
                return None
            except Exception as x:
                self._problem(kind, e, "source.error", f"{type(x).__name__}: {x}"[:300])
                return None
            return e["stable"], Input(kind, sha, size, e["name"], tuple(locators))

        workers = max(1, getattr(self.args, "fetch_workers", None) or FETCH_WORKERS)
        fetches = sum(1 for j in todo if j[2] is None)
        if fetches:
            self.log(f"fetching {fetches} {kind} files ({workers} at a time)")
        with ThreadPoolExecutor(workers) as pool:
            for r in pool.map(one, todo):
                if r is not None:
                    out[r[0]] = r[1]
        return Inputs(out)

    def _in_apk(self, internal_id: str) -> bool:
        """Whether the APK holds the file of a location (read from its directory, not fetched)."""
        from .catalog import APK_AA_DIR
        if self._apk_names is None:
            import zipfile
            try:
                with zipfile.ZipFile(self.apk) as z:
                    self._apk_names = set(z.namelist())
            except (OSError, zipfile.BadZipFile):
                raise ConfigError(f"setting paths.apk: {self.apk} is not a readable APK") from None
        return APK_AA_DIR + apk_rel(internal_id) in self._apk_names

    def _problem(self, kind: str, e: dict, code: str, message: str) -> None:
        self._problems.append({"kind": kind, "stable": e["stable"], "name": e["name"], "code": code,
                               "message": message})

    # ---------------------------------------------------------------- explain
    def explain(self, pipeline: list[str], params: dict, orch=None, layouts=()) -> list[str]:
        ctx = self.context()
        c = ctx["catalog"]
        lines = [f"store: {self.root}", f"cache: {self.cache if self.cache is not None else 'not set'}",
                 f"context: region {ctx['region'] or '-'}, language {ctx['language'] or '-'}, catalog "
                 f"{c['label'] or '-'} ({c['id'][:12]}), APK catalog {(c['apkCatalogSha'] or '-')[:12]}",
                 f"selection: {', '.join(self.selection)}",
                 f"pipeline: {', '.join(pipeline)}",
                 f"not installed: {', '.join(self.installed.missing) or 'none'}"]
        for n in pipeline:
            lines.append(f"params {n}: {contract.key_text(self.installed.stages[n].normalize(params.get(n)))}")
        lines.append(f"fetch workers: {getattr(self.args, 'fetch_workers', None) or FETCH_WORKERS}")
        for n in pipeline:
            if n in self.calibration:
                f = self.calibration[n]
                lines.append(f"cost calibration {n}: CPU x{f['cpu']}, peak memory x{f['peak']} "
                             f"({f.get('samples', 0)} measured tasks)")
        if orch is not None:
            lines += orch.explain()
        if layouts:
            lines.append(f"layouts: {', '.join(layouts)} (link: {getattr(self.args, 'link', 'copy')})")
        return lines

    # ---------------------------------------------------------------- plan
    def previous(self, since: str | None) -> dict | None:
        if not since:
            return self.store.latest_run(self.selection)
        doc = self.store.run(since) if contract.is_sha256(since) else None
        if doc is None:
            runs = sorted(p.name[:-5] for p in (self.root / "runs").glob("*.json") if p.name.startswith(since))
            if len(runs) != 1:
                self.args.usage(f"--since {since}: {'no run' if not runs else f'{len(runs)} runs'} with this id")
            doc = self.store.run(runs[0])
        return doc

    def plan(self, pipeline: list[str], params: dict, *, since=None, out=None, layouts=()):
        from . import plan as plan_mod
        facts = self.facts(pipeline, fetch=False)
        p = plan_mod.plan(self.scheduled, pipeline, self.store, params=params, facts=facts,
                          previous=self.previous(since), workers=self.workers(), memory=self.memory())
        if out is not None and layouts:
            from . import layout
            self.done({n["id"]: n["key"] for n in p.doc["nodes"] if n["status"] == "hit"})
            hits = [(n["id"], self.store.result(n["key"])) for n in p.doc["nodes"]
                    if n["status"] == "hit" and self._output(n["stage"])]
            for name, d in layout_dirs(Path(out), layouts).items():
                entries, _ = self.layout_entries(name, hits, store_derived=False)
                p.doc["summary"]["layouts"][name] = layout.diff(d, entries)
        return p

    def _output(self, stage: str) -> bool:
        src = self.installed.sources.get(stage)
        return bool(src and src.output)

    # ---------------------------------------------------------------- layouts
    def layout_entries(self, name: str, results, *, store_derived: bool = True) -> tuple[list[dict], dict]:
        """The entries of layout `name` for the results [(task id, result)] of the output stages (derived
        documents of the stages in `original`) and its report."""
        from . import layout
        stages = self.installed.stages
        sources, named = [], []
        for tid, doc in results:
            stage_name, subject = contract.parse_task_id(tid)
            stage = stages[stage_name]
            names = self.names(stage_name).get(subject) if name == "original" else None
            if names:
                sources += [layout.Source(tid, doc, n) for n in names]
                if len(names) > 1:
                    named.append({"task": tid, "names": list(names)})
            else:
                sources.append(layout.Source(tid, doc, layout_root(stage, self.installed.sources[stage_name],
                                                                   subject)))
        index = self.index()
        case = {loc["internalId"].lower(): loc["internalId"] for loc in index["locations"] if loc["kind"] == "asset"}
        if name != "original":
            return layout.entries(name, sources)
        derives = lambda s: hasattr(stages[contract.parse_task_id(s.task)[0]], "derived")   # noqa: E731
        roots = {}                              # every object of a bundle under its export task's root
        for s in sources:
            if s.task.startswith(EXPORT_STAGE + ":") and s.root is not None:
                for it in s.result["items"]:
                    roots.setdefault(contract.parse_object_id(it["object"])[0], s.root)
        entries, report = layout.entries(name, [s for s in sources if not derives(s)], case=case,
                                         object_roots=roots)
        report["names"] = named
        extra = self._derived(sources, entries, store_derived)
        taken = {e["path"].casefold() for e in entries}
        for e in extra:
            if e["path"].casefold() in taken:
                raise layout.LayoutError(f"derived document {e['id']}: {e['path']} is taken")
            taken.add(e["path"].casefold())
        return sorted(entries + extra, key=lambda e: (e["path"], e["id"])), report

    def done(self, tasks: dict) -> None:
        """The tasks whose results the layouts place, {task id: key} (after a run: every task with a result)."""
        self._done, self._names = dict(tasks), {}

    def names(self, stage_name: str) -> dict:
        """{subject: catalog names} of a stage that names its tasks' files (stage.names(env): the CRI stages root a
        content at each catalog name that refers to it, bundle-held content through the unity.export results;
        layout-time data, never part of a key); {} for others. The environment has the planning facts and every
        task given to done()."""
        if stage_name not in self._names:
            stage = self.installed.stages[stage_name]
            got = {}
            if hasattr(stage, "names"):
                env = Env(self.store, self._facts)
                for tid, key in sorted(self._done.items()):
                    env.done(tid, key)
                got = stage.names(env)
            self._names[stage_name] = {k: sorted(v) for k, v in got.items()}
        return self._names[stage_name]

    def _derived(self, sources, entries: list[dict], store_derived: bool) -> list[dict]:
        from . import layout
        stages = self.installed.stages
        makers = list({s.task: s for s in sources
                       if hasattr(stages[contract.parse_task_id(s.task)[0]], "derived")}.values())
        if not makers:
            return []
        objects: dict[str, list[str]] = {}
        for s in sources:
            for it in s.result["items"]:
                arts = list(it.get("artifacts", ())) or ([it["in"]] if "in" in it else [])
                objects.setdefault(it["object"], []).extend(arts)
        paths = LayoutPaths(layout.paths(entries), objects)
        out = []
        for s in makers:
            stage = stages[contract.parse_task_id(s.task)[0]]
            for rel, data in stage.derived(s.task, s.result, self.store, paths):
                sha = self.store.put(data) if store_derived else contract.sha256(data)
                out.append({"path": layout.escape_path(rel.split("/")), "sha256": sha, "size": len(data),
                            "id": contract.artifact_id(s.task, "derived:" + rel)})
        return out


class Inputs(Mapping):
    """{subject: Input} of the files of a selection, read-only; a subject whose bytes are not known yet (not
    fetched) is listed, and reading it raises its Pending ("fetch:<file name>"), as the stages expect."""

    def __init__(self, items: dict):
        self._items = dict(sorted(items.items()))

    def __getitem__(self, subject: str) -> Input:
        v = self._items[subject]
        if isinstance(v, Pending):
            raise v
        return v

    def __contains__(self, subject) -> bool:
        return subject in self._items

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def waiting(self) -> list[str]:
        """The subjects not fetched yet."""
        return [k for k, v in self._items.items() if isinstance(v, Pending)]


class LayoutPaths:
    """What a stage's derived documents may read of the `original` layout: the path of an artifact, the artifacts
    of an object (a contained object: the artifact it is part of)."""

    def __init__(self, paths: dict[str, str], objects: dict[str, list[str]]):
        self._paths, self._objects = paths, objects

    def artifact(self, aid: str) -> str | None:
        return self._paths.get(aid)

    def objects(self, oid: str) -> list[str]:
        return sorted(set(self._objects.get(oid, ())))


def layout_dirs(out: Path, names) -> dict[str, Path]:
    """One layout: in OUT; several: each in OUT/<name>."""
    names = list(names)
    return {names[0]: out} if len(names) == 1 else {n: out / n for n in names}


def parse_layouts(spec: str) -> list[str]:
    names = [n.strip() for n in (spec or "").split(",") if n.strip()]
    if not names or any(n not in LAYOUT_NAMES for n in names) or len(set(names)) != len(names):
        raise ValueError(f"--layout {spec}: expected {' or '.join(LAYOUT_NAMES)}, comma-separated")
    return names


def _apk_version(apk: Path | None) -> str | None:
    if apk is None or not apk.is_file():
        return None
    from .gameapi import apk_version_name
    return apk_version_name(apk)


# ---------------------------------------------------------------- export
def census_totals(results, subjects) -> dict[str, int]:
    """{class: objects} of the censuses of `subjects` (the census artifacts' facts)."""
    want = {contract.task_id("unity.census", s) for s in subjects}
    total: Counter = Counter()
    for tid, doc in results:
        if tid in want:
            for a in doc["artifacts"]:
                total.update(a["semantics"].get("facts", {}).get("classes", {}))
    return dict(sorted(total.items()))


def view_gaps(results) -> int:
    return sum(int(a["semantics"].get("facts", {}).get("gaps", 0)) for tid, doc in results
               if tid.startswith("view.") for a in doc["artifacts"])


def _write_report(d: Path, name: str, doc) -> Path:
    from .store import write_file
    p = d / name
    write_file(p, contract.encode(doc))
    return p


def cmd_export(args, cfg, common):
    from . import layout
    from .orchestrate import Orchestrator, failure
    try:
        names = parse_layouts(args.layout)
    except ValueError as e:
        args.usage(str(e))
    use(cfg)                                          # the tools the atoms name are part of the keys
    ws = Workspace(args, cfg, common)
    pipeline = ws.pipeline()
    params = ws.params(pipeline)
    orch = Orchestrator(ws.store, ws.scheduled, workers=ws.workers(), memory=ws.memory(),
                        recycle_tasks=RECYCLE_TASKS, recycle_rss=RECYCLE_RSS, initializer=_worker_init,
                        initargs=(cfg,), log=ws.log)
    if args.explain:
        for line in ws.explain(pipeline, params, orch, names):
            print(line, file=sys.stderr)
    out = Path(args.out)
    if args.dry_run:
        sys.stdout.write(ws.plan(pipeline, params, out=out, layouts=names).text())
        return
    ws.run_index()
    facts = ws.facts(pipeline, fetch=True)
    sel = ws.selected
    ws.log(f"{len(sel.selected)} bundles selected ({len(sel.census)} with their dependencies), "
           f"{len(facts['raw'])} raw files; {len(ws.problems())} files not readable")
    aborted = None
    try:
        run = orch.run(pipeline, params=params, facts=facts, context=ws.context(), selection=ws.selection)
    except ConfigError as e:
        run = getattr(e, "run", None)
        if run is None:
            raise
        aborted = str(e)
    except KeyboardInterrupt:
        print("nnnotes: interrupted (results written so far stay valid)", file=sys.stderr)
        sys.exit(EXIT_ABORTED)
    stage_of = {"bundle": "unity.census", "raw": next((n for n in RAW_STAGES if n in pipeline), "cri.audio")}
    for p in ws.problems():
        if p["code"] == "source.error":
            run.failures_.append(failure(contract.task_id(stage_of[p["kind"]], p["stable"]), stage_of[p["kind"]],
                                         p["code"], p["message"], p["name"]))
    ws.done({tid: t["key"] for tid, t in run.tasks.items() if t["status"] != "failed"})
    run.context["master"] = ws.master_context(run.results())
    results = [(tid, doc) for tid, doc in run.results() if ws._output(contract.parse_task_id(tid)[0])]
    reports = out / REPORTS
    written, shas = {}, {}
    for name, d in layout_dirs(out, names).items():
        entries, report = ws.layout_entries(name, results)
        w = layout.materialize(d, name, {}, entries, layout.from_store(ws.store), link=args.link)
        shas[name] = w["sha256"]
        written[name] = {"dir": str(d), "files": len(w["manifest"]["entries"]), "write": w["write"],
                         "remove": w["remove"], "keep": w["keep"], "collisions": len(report["collisions"]),
                         "shared": len(report.get("shared", []))}
        _write_report(reports, f"layout-{name}.json", report)
    manifest = run.write(shas)
    write_calibration(ws.root, calibrate(run.log, ws.calibration, orch.worker_bytes))
    all_results = list(run.results())
    _write_report(reports, "run.json", manifest)
    _write_report(reports, "coverage.json", run.coverage(census_totals(all_results, sel.selected)))
    _write_report(reports, "failures.json", run.failures_doc())
    _write_report(reports, "sources.json", {"problems": ws.problems()})
    s = manifest["summary"]
    gaps = view_gaps(all_results)
    common.print_json({"run": manifest["id"], "selection": ws.selection, "tasks": {k: s[k] for k in (
        "tasks", "hit", "ran", "failed")}, "items": s["items"], "artifacts": s["artifacts"],
        "failures": len(run.failures()), "sources": dict(sorted(Counter(p["code"] for p in ws.problems()).items())),
        "viewGaps": gaps, "layouts": written, "reports": str(reports)})
    if aborted:
        print(f"nnnotes: aborted: {aborted}", file=sys.stderr)
        sys.exit(EXIT_ABORTED)
    if run.failures() or (args.strict and gaps):
        sys.exit(EXIT_FAILED)


# ---------------------------------------------------------------- plan
def cmd_plan(args, cfg, common):
    from .orchestrate import Orchestrator
    layouts = []
    if args.out:
        try:
            layouts = parse_layouts(args.layout)
        except ValueError as e:
            args.usage(str(e))
    use(cfg)                                          # the tools the atoms name are part of the keys
    ws = Workspace(args, cfg, common)
    pipeline = ws.pipeline()
    params = ws.params(pipeline)
    if args.explain:
        for line in ws.explain(pipeline, params, layouts=layouts):
            print(line, file=sys.stderr)
    if args.census:
        ws.run_index()
        facts = ws.facts(["catalog.index", "unity.census"], fetch=True)
        Orchestrator(ws.store, ws.scheduled, workers=ws.workers(), memory=ws.memory(),
                     recycle_tasks=RECYCLE_TASKS, recycle_rss=RECYCLE_RSS, initializer=_worker_init,
                     initargs=(cfg,), log=ws.log).run(["catalog.index", "unity.census"], facts=facts)
    p = ws.plan(pipeline, params, since=args.since, out=args.out, layouts=layouts)
    if args.emit_tasks:
        files = p.emit(args.emit_tasks)
        ws.log(f"{len(files)} task descriptions in {args.emit_tasks}")
    if args.why:
        common.print_json(why(ws, p, args.why, args.since))
    elif args.json:
        common.print_json(p.doc)
    else:
        sys.stdout.write(p.text())
    if args.check:
        sys.exit(p.check())


def why(ws: Workspace, p, tid: str, since=None) -> dict:
    """Why a task of a plan has its status: its reasons, and its key parts beside those of the previous run."""
    node = next((n for n in p.doc["nodes"] if n["id"] == tid), None)
    if node is None:
        ws.args.usage(f"--why {tid}: no task with this id in the plan")
    new = None
    if node["key"] is not None:
        t = next((t for t in p.tasks if t.key == node["key"]), None)
        doc = None if t is not None else ws.store.result(node["key"])
        new = t.key_parts() if t is not None else (doc["keyParts"] if doc else None)
    prev = ws.previous(since)
    old_task = next((t for t in (prev or {}).get("tasks", []) if t["id"] == tid), None)
    old = None
    if old_task is not None and old_task.get("key"):
        doc = ws.store.result(old_task["key"])
        old = doc["keyParts"] if doc else None
    return {"task": tid, "status": node["status"], "key": node["key"], "reasons": node["reasons"],
            "previous": {"run": prev["id"], "key": old_task.get("key"), "status": old_task["status"]}
            if old_task is not None else None, "keyParts": {"old": old, "new": new}}


# ---------------------------------------------------------------- run-stage
def _task_files(items) -> list[Path]:
    out = []
    for s in items:
        p = Path(s)
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def cmd_run_stage(args, cfg, common):
    from .orchestrate import run_one
    from .store import Store
    problems, tasks = [], []
    for f in _task_files(args.tasks):
        try:
            tasks.append(Task.from_json(contract.loads(f.read_bytes())))
        except OSError as e:
            args.usage(f"{f}: {e.strerror or e}")
        except (IncompatibleTask, ValueError) as e:
            problems.append(f"{f}: {e}")
    inst = installed(sorted({t.stage for t in tasks}))
    for t in tasks:
        s = inst.stages.get(t.stage)
        if s is None:
            problems.append(f"task {t.id}: no stage {t.stage} in this installation")
        elif s.version != t.version:
            problems.append(f"task {t.id}: stage {t.stage} is version {s.version} here, the task was described "
                            f"for version {t.version}")
    if problems:
        for m in problems:
            print(f"nnnotes: incompatible task: {m}", file=sys.stderr)
        sys.exit(EXIT_INCOMPATIBLE)
    root = store_root(args, cfg)
    cache = cfg.path("paths", "cache")
    store = Store(root, cache=cache, fetch=CatalogFetcher(root, cache, cfg) if args.fetch else None)
    use(cfg)
    out, bad = [], 0
    for t in tasks:
        r = run_one(t, store, inst.stages, args.force)
        if r["ok"]:
            out.append({"id": t.id, "key": t.key, "status": r["status"], "result": r["resultStatus"]})
            bad += r["resultStatus"] != "ok"
        else:
            out.append({"id": t.id, "key": t.key, "status": "failed", "code": r["code"], "message": r["message"]})
            bad += 1
            if r["code"] == "config":
                break
    n = Counter(o["status"] for o in out)
    common.print_json({"tasks": out, "summary": {"tasks": len(tasks), "hit": n["hit"], "ran": n["ran"],
                                                 "failed": n["failed"]}})
    if bad:
        sys.exit(EXIT_FAILED)


# ---------------------------------------------------------------- catalogs
def _db(args, cfg):
    from .catalogdb import CatalogDB
    return CatalogDB(store_root(args, cfg))


def _version_line(v: dict) -> str:
    apk = v["apk"]["sha256"][:12] if v.get("apk") else "-"
    return (f"{v['seq']:>4}  {v['id'][:12]}  {','.join(v['labels']) or '-':<24}  {v.get('region') or '-':<6} "
            f"{v.get('language') or '-':<8} remote {v['remote']['sha256'][:12]}  apk {apk}  "
            f"resource {v.get('resourceVersion') or '-'}")


def cmd_catalogs_list(args, cfg, common):
    from .catalogdb import CATALOGS
    vs = _db(args, cfg).versions()
    if args.json:
        common.print_json({"schema": CATALOGS, "versions": vs})
        return
    for v in vs:
        print(_version_line(v))
    print(f"# {len(vs)} catalog versions", file=sys.stderr)


def _apk_catalog(args, cfg) -> bytes | None:
    from .catalogdb import apk_catalog
    if getattr(args, "apk_catalog", None):
        return Path(args.apk_catalog).read_bytes()
    apk = cfg.path("paths", "apk")
    if apk is None:
        return None
    if not apk.is_file():
        raise ConfigError(f"setting paths.apk: file {apk} not found")
    return apk_catalog(apk)


def cmd_catalogs_import(args, cfg, common):
    try:
        remote = Path(args.file).read_bytes()
    except OSError as e:
        args.usage(f"{args.file}: {e.strerror or e}")
    try:
        v = _db(args, cfg).add(remote, _apk_catalog(args, cfg), label=args.label,
                               region=cfg.get("catalog", "region"), language=cfg.get("catalog", "language"),
                               resource_version=args.resource_version,
                               apk_version_name=_apk_version(cfg.path("paths", "apk")))
    except ValueError as e:
        args.usage(str(e))
    print(_version_line(v))


def cmd_catalogs_fetch(args, cfg, common):
    from . import catalogdb
    region = cfg.region()
    cdn = cfg.cdn(region)
    language = cfg.require("catalog", "language")
    try:
        data, hash_text = catalogdb.fetch(cdn, language)
    except OSError as e:                             # URLError, HTTPError, timeouts (the message has no URL)
        sys.exit(f"nnnotes: catalog_main_{language}.bin could not be fetched: {type(e).__name__}: "
                 f"{getattr(e, 'reason', None) or getattr(e, 'code', None) or ''}".rstrip(": "))
    resource = None
    if cfg.has(f"servers.{region}", "api"):
        from . import gameapi
        try:
            resource = gameapi.master_version(cfg, region).resource_version
        except gameapi.GameApiError as e:
            print(f"nnnotes: no resource version (the label defaults to the catalog's sha256): {e}",
                  file=sys.stderr)
    try:
        v = _db(args, cfg).add(data, _apk_catalog(args, cfg), label=args.label, region=region, language=language,
                               hash_text=hash_text, resource_version=resource,
                               apk_version_name=_apk_version(cfg.path("paths", "apk")))
    except ValueError as e:
        args.usage(str(e))
    print(_version_line(v))


def addresses_of(store, vid: str) -> dict | None:
    """The address table (link.addresses) of the latest run over a catalog version, None when no run has one."""
    runs = sorted((store.root / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in runs:
        try:
            doc = contract.loads(p.read_bytes())
        except (OSError, ValueError):
            continue
        if (doc.get("context", {}).get("catalog") or {}).get("id") != vid:
            continue
        t = next((t for t in doc["tasks"] if t["id"] == contract.task_id("link.addresses", CATALOG_SUBJECT)
                  and t["status"] != "failed"), None)
        res = store.result(t["key"]) if t else None
        if res:
            (a,) = res["artifacts"]
            return contract.loads(store.read(a["content"]["sha256"]))
    return None


def cmd_catalogs_diff(args, cfg, common):
    from . import catalogdb
    from .store import Store
    db = _db(args, cfg)
    vs = []
    for ref in (args.old, args.new):
        try:
            vs.append(db.get(ref))
        except (KeyError, ValueError) as e:
            args.usage(f"{ref}: {e.args[0] if e.args else e}")
    store = Store(store_root(args, cfg))
    a, b = vs
    d = catalogdb.diff(db.index(a), db.index(b), addresses_of(store, a["id"]), addresses_of(store, b["id"]))
    if args.json:
        common.print_json(d)
    else:
        sys.stdout.buffer.write(catalogdb.format_diff(d).encode("utf-8"))
        sys.stdout.buffer.flush()


# ---------------------------------------------------------------- store
def cmd_store_verify(args, cfg, common):
    from .store import Store
    r = Store(store_root(args, cfg)).verify(quick=args.quick, workers=args.workers or FETCH_WORKERS)
    common.print_json(r)
    if r["problems"]:
        sys.exit(EXIT_FAILED)


# ---------------------------------------------------------------- parser
def default_common():
    """The helpers of the command line the handlers use (open_catalog, print_json)."""
    from . import cli
    return SimpleNamespace(open_catalog=cli.open_catalog, print_json=cli._print_json)


def _gib(s: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s}: expected a size in GiB") from None
    if v <= 0:
        raise argparse.ArgumentTypeError(f"{s}: expected a positive size in GiB")
    return v


def _store_arg(c) -> None:
    c.add_argument("--store", help="store directory ([paths] store; default <cache>/store)")


def _run_args(c) -> None:
    """The selection and parameters shared by export and plan."""
    c.add_argument("--select", action="append", metavar="SEL",
                   help="group:<group>, key:<key prefix> or bundle:<glob> (repeatable; default: everything)")
    c.add_argument("--views", metavar="all|none|a,b",
                   help="semantic views to build (default: all when master data is set, else none)")
    c.add_argument("--catalog-version", metavar="LABEL|SHA",
                   help="an imported catalog version (`catalogs list`; default: the current catalog)")
    c.add_argument("--png-level", type=int, metavar="N", help="PNG compression level 0-9 (changes the output)")
    c.add_argument("--only-class", metavar="C,...", help="export only objects of these classes")
    c.add_argument("--workers", type=int,
                   help="worker processes (default: the CPUs this process may use; 0: this process)")
    c.add_argument("--memory", type=_gib, metavar="GiB",
                   help="memory budget of the running tasks and workers (default: 80 %% of the physical memory)")
    c.add_argument("--fetch-workers", type=int, metavar="N", help=f"parallel downloads (default {FETCH_WORKERS})")
    c.add_argument("--layout", default="original", help="original, cas or original,cas (default original)")
    c.add_argument("--explain", action="store_true", help="print every setting of the run first")
    _store_arg(c)


def register(sub, common=None) -> None:
    """Add the asset commands to the command line's subparsers. `common`: the helpers of cli (open_catalog,
    print_json; default_common())."""
    common = common or default_common()

    def bind(fn):
        return lambda args, cfg: fn(args, cfg, common)

    c = sub.add_parser("export", help="every object of the selected bundles -> common formats (store + layouts)")
    c.add_argument("-o", "--out", required=True, help="output directory")
    _run_args(c)
    c.add_argument("--link", default="copy", choices=("copy", "hard"),
                   help="copy the files from the store, or hard-link them")
    c.add_argument("--strict", action="store_true", help="exit 1 when a view has gaps")
    c.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    c.set_defaults(func=bind(cmd_export), usage=c.error)

    c = sub.add_parser("plan", help="what export would run and why, without running it")
    c.add_argument("-o", "--out", help="output directory of the export (counts the layout files to write)")
    _run_args(c)
    c.add_argument("--json", action="store_true", help="the plan document (nnnotes.plan/1)")
    c.add_argument("--since", metavar="RUN", help="explain against this run (default: the latest of the selection)")
    c.add_argument("--check", action="store_true", help="exit 1 when anything would run or change")
    c.add_argument("--census", action="store_true", help="first fetch and census the bundles without one")
    c.add_argument("--emit-tasks", metavar="DIR", help="write the runnable tasks as task descriptions")
    c.add_argument("--why", metavar="TASK", help="the reasons and key parts of one task")
    c.set_defaults(func=bind(cmd_plan), usage=c.error)

    c = sub.add_parser("run-stage", help="run task descriptions (nnnotes.task/1) into the store")
    c.add_argument("tasks", nargs="+", metavar="TASK", help="task description files, or directories of them")
    _store_arg(c)
    c.add_argument("--fetch", action="store_true", help="fetch inputs through their catalog locators")
    c.add_argument("--force", action="store_true", help="run tasks whose result is stored (it must not change)")
    c.set_defaults(func=bind(cmd_run_stage), usage=c.error)

    c = sub.add_parser("catalogs", help="catalog versions of the store")
    csub = c.add_subparsers(dest="catalogs_cmd", required=True, metavar="<catalogs command>")
    m = csub.add_parser("list", help="the imported catalog versions")
    m.add_argument("--json", action="store_true")
    _store_arg(m)
    m.set_defaults(func=bind(cmd_catalogs_list), usage=m.error)
    m = csub.add_parser("import", help="import a catalog file (with the APK's catalog when [paths] apk is set)")
    m.add_argument("file", help="catalog_main_<language>.bin")
    m.add_argument("--label", help="label of the version (default: its resource version, else the sha256 prefix)")
    m.add_argument("--apk-catalog", help="the APK's catalog.bin (default: read from [paths] apk)")
    m.add_argument("--resource-version", help="the game's resource version of this catalog")
    _store_arg(m)
    m.set_defaults(func=bind(cmd_catalogs_import), usage=m.error)
    m = csub.add_parser("fetch", help="fetch the region's current catalog and import it")
    m.add_argument("--label", help="label of the version (default: the resource version when an API root is set)")
    _store_arg(m)
    m.set_defaults(func=bind(cmd_catalogs_fetch), usage=m.error)
    m = csub.add_parser("diff", help="keys, bundles, raw files (and objects) that differ between two versions")
    m.add_argument("old", help="label, version id or sha256 prefix")
    m.add_argument("new", help="label, version id or sha256 prefix")
    m.add_argument("--json", action="store_true", help="the diff document (nnnotes.catalog-diff/1)")
    _store_arg(m)
    m.set_defaults(func=bind(cmd_catalogs_diff), usage=m.error)

    c = sub.add_parser("store", help="the store")
    ssub = c.add_subparsers(dest="store_cmd", required=True, metavar="<store command>")
    m = ssub.add_parser("verify", help="check every stored content and result")
    m.add_argument("--quick", action="store_true", help="check sizes only, do not hash the contents")
    m.add_argument("--workers", type=int, help=f"parallel hashing (default {FETCH_WORKERS})")
    _store_arg(m)
    m.set_defaults(func=bind(cmd_store_verify), usage=m.error)


def main(argv=None) -> None:
    """The asset commands alone, with the global flags of the command line (tests; `nnnotes` has them too)."""
    from .cli import load_config
    p = argparse.ArgumentParser(prog="nnnotes")
    for flag in ("--config", "--region", "--language", "--catalog", "--cache", "--master", "--apk", "--ffmpeg",
                 "--vgmstream", "--node"):
        p.add_argument(flag)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")
    register(sub)
    args = p.parse_args(argv)
    try:
        cfg = load_config(args)
        args.func(args, cfg)
    except ConfigError as e:
        print(f"nnnotes: {e}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


if __name__ == "__main__":                            # pragma: no cover
    main()
