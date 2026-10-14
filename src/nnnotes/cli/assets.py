"""The asset commands: the default orchestration of the stages (`export assets`), its plan and single tasks (`store
plan`, `store run`).

    nnnotes export assets -o OUT [--layout original[,cas]] [--select group:G|key:PREFIX|bundle:GLOB ...]
                          [--views all|none|a,b] [--workers N] [--memory GiB] [--fetch-workers N]
                          [--stored-catalog L|SHA] [--png-level N] [--flac-level N] [--only-class C,...]
                          [--link auto|clone|hard|copy] [--strict] [--dry-run] [--explain]
    nnnotes store plan [the selection and parameters of export assets] [-o OUT] [--since RUN] [--check] [--census]
                       [--emit-tasks DIR] [--why TASK]
    nnnotes store run TASK.json|DIR [...] [--fetch] [--force]

The pipeline is the registry STAGE_SOURCES in order: each entry names the module and attribute of a stage (a Stage
class) or of a family of stages (a function returning them, `view.`). An entry whose module or attribute is not
installed is left out of the pipeline and named by --explain. Stage modules import heavy libraries (UnityPy) only
when a task runs, so `store plan`, and `store run` of stages that do not read bundles, never load them.

Facts the stages read while their tasks are described (stages.Env):

    catalogs   {"main": [Input remote, Input apk]}: the catalog version's files in <store>/catalogs/
    index      the catalog index document (nnnotes.catalog-index/1)
    bundles    {stable bundle name: Input "bundle"} of the selected bundles and their dependencies (a bundle whose
               bytes are not known yet: a Pending "fetch:<file name>")
    selected   the selected bundles' stable names, sorted (the subjects of the export stages)
    raw        {stable raw file name: Input "raw"} of the selected raw files (only with a stage that reads them;
               export fetches them while the stages before run, and reading the fact waits for that)
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

Exit codes: export assets 0 complete (unsupported objects allowed), 1 item or task failures (reports written), 2
usage or settings, 3 aborted (results written so far stay valid); store run 0, 1 (a task failed or a result is
partial), 2, 4 an incompatible task (nothing of the batch runs); store plan --check 1 when anything would run or
change.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import threading
import zipfile
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module, util
from pathlib import Path

from ..pipeline import contract
from ..pipeline.contract import Cost, IncompatibleTask, Input, Task
from ..pipeline.stages import Env, Pending, Stage, describe, execute
from ..source.apkset import ApkSet
from ..source.config import ConfigError, usable_cpus, use
from ..source.session import Session
from . import common
from .common import nonnegative, print_json, usage

GIB = 1 << 30
LAYOUT_NAMES = ("original", "cas")
FETCH_WORKERS = 8
RECYCLE_TASKS = 500
RECYCLE_RSS = 3 * GIB  # a worker whose resident memory after a task is above this is replaced ...
RECYCLE_SHARE = 0.4  # ... or above this share of its part of the memory budget (budget / workers),
RECYCLE_RSS_MIN = 256 << 20  # but not below this
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
    StageSource("catalog.index", "nnnotes.pipeline.catalogdb:IndexStage"),
    StageSource("unity.census", "nnnotes.pipeline.census:CensusStage"),
    StageSource("link.scripts", "nnnotes.pipeline.link:ScriptsStage"),
    StageSource("link.addresses", "nnnotes.pipeline.link:AddressesStage"),
    StageSource("unity.export", "nnnotes.pipeline.objexport:ExportStage", output=True, root="_bundles/{subject}"),
    StageSource("sprite.crop", "nnnotes.pipeline.objexport:SpriteCropStage", output=True),
    StageSource("cri.audio", "nnnotes.pipeline.cristages:AudioStage", output=True),
    StageSource("cri.movie", "nnnotes.pipeline.cristages:MovieStage", output=True),
    StageSource("live2d.model", "nnnotes.pipeline.modelstages:Live2DStage", output=True),
    StageSource("spine.skeleton", "nnnotes.pipeline.modelstages:SpineStage", output=True),
    StageSource("link.artifacts", "nnnotes.pipeline.link:ArtifactsStage"),
    StageSource("view.", "nnnotes.pipeline.views:stages", output=True),
    StageSource("view.voices", "nnnotes.pipeline.voices:VoiceStage", output=True),
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
    """The stages of STAGE_SOURCES (`names`: only the sources of these stage names, for `store run`)."""
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
    selected: list  # stable names of the selected bundles (export subjects)
    census: list  # the selected bundles and the bundles they depend on
    raw: list  # stable names of the selected raw files


def select(index: dict, selection: list[str]) -> Selection:
    """The bundles and raw files a selection names in a catalog index (module documentation)."""
    from ..pipeline import catalogdb

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
    for loc in index["locations"]:  # a key's first dependency is the bundle that holds it
        if (
            loc["kind"] not in catalogdb.FILE_KINDS
            and loc["dependencies"]
            and files.get(loc["dependencies"][0]) in chosen
        ):
            deps |= reach[loc["id"]]
    pick = lambda s, kind: sorted(n for k, n in s if k == kind)  # noqa: E731
    return Selection(pick(chosen, "bundle"), pick(deps, "bundle"), pick(chosen, "raw"))


# ---------------------------------------------------------------- settings
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


def recycle_rss(memory: int | None, workers: int) -> int:
    """The resident memory after a task above which a worker is replaced: RECYCLE_RSS, or with a memory budget
    RECYCLE_SHARE of each worker's part of it when that is less (not below RECYCLE_RSS_MIN). A worker keeps much of
    its largest task's memory (freed but not returned to the system), which the budget does not count."""
    if memory is None:
        return RECYCLE_RSS
    return min(RECYCLE_RSS, max(RECYCLE_RSS_MIN, int(RECYCLE_SHARE * memory / max(1, workers))))


def _bundle_key(cfg):
    from ..source.addressables import BundleKey

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
        from ..pipeline import catalogdb
        from ..source.catalog import Catalog

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
                region = region or cfg.get("catalog", "region")
                if region:
                    cfg = cfg.for_region(region)
                source, client, cache = None, None, self.cache
                if v.get("source") is not None:
                    from ..source.jp import Client, Source

                    source = Source.from_dict(v["source"])
                    client = Client(cfg, cfg.region())
                    cache = source.cache_dir(cache)
                cat = Catalog(
                    remote,
                    cache,
                    cdn=lambda: cfg.cdn(cfg.region()),
                    bundle_key=lambda: _bundle_key(cfg),
                    apk_catalog=apk,
                    source=source,
                    client=client,
                )
                # Replay imported offsets; the current APK is needed only for uncached local files.
                cat.apk = cfg.path("paths", "apk")
                hit = self._catalogs[vid] = (cat, catalogdb.by_id(catalogdb.index(remote, apk)))
            return hit

    def fetch_location(self, vid: str, lid: str) -> Path:
        from ..source.addressables import remote_path
        from ..source.catalog import Bundle, file_name

        cat, locs = self.catalog(vid)
        loc = locs.get(lid)
        if loc is None:
            raise FileNotFoundError(f"catalog version {vid[:12]} has no location {lid}")
        iid = loc["internalId"]
        if loc["kind"] == "bundle":
            return cat.fetch(Bundle(0, iid, file_name(iid), remote_path(iid) is not None))
        if remote_path(iid) is None:
            return self._apk_file(iid, cat)
        return cat.fetch_raw({"internal_id": iid})

    def _apk_file(self, internal_id: str, cat) -> Path:
        """A raw APK file through its snapshot's cache; only a cache miss needs the matching APK."""
        from ..core.cache import write_atomic
        from ..source.catalog import APK_AA_DIR

        rel = apk_rel(internal_id)
        dst = cat.local_cache_dir() / "raw" / rel
        if not (dst.is_file() and dst.stat().st_size > 0):
            if cat.apk is None:
                raise self.cfg.missing("paths", "apk")
            with ApkSet(cat.apk) as z:
                cat.check_apk(z)
                data = z.read(APK_AA_DIR + rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(dst, data)
        return dst

    def __call__(self, inp: Input, loc: dict) -> Path:
        return self.fetch_location(loc["catalog"], loc["location"])


def apk_rel(internal_id: str) -> str:
    """The path of an APK-local location's file below the APK's Addressables directory (catalog.APK_AA_DIR)."""
    from ..source.catalog import LOCAL_PREFIX

    return internal_id[len(LOCAL_PREFIX) :].lstrip("/") if internal_id.startswith(LOCAL_PREFIX) else internal_id


def _worker_init(store, cfg) -> None:
    """Every worker process: the settings of the command."""
    use(cfg)


def _log(message: str) -> None:
    from ..pipeline.orchestrate import _log as log

    log(message)


# ---------------------------------------------------------------- cost calibration
CALIBRATION = "calibration.json"  # <store>/costs/calibration.json
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
    by the 95th percentile of measured over estimated, the measured peak less the process's memory at the task's
    start (startRssBytes; in older records a worker's own memory). Where the child processes' CPU
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
            cpu = 1.0  # child processes not measured here: keep the CPU factor
        else:
            cpu = sum(c["cpuSeconds"] for _, c in pairs) / sum(a["cpuSeconds"] for a, _ in pairs)
        ratios = sorted(
            max(0, (c.get("peakRssBytes") or 0) - (c.get("startRssBytes") or worker_bytes)) / a["peakBytes"]
            for a, c in pairs
        )
        peak = ratios[min(len(ratios) - 1, int(0.95 * len(ratios)))]
        if not any(c.get("peakRssBytes") for _, c in pairs):
            peak = 1.0  # not measurable on this platform
        out[stage] = {
            "cpu": _clamp(old["cpu"] * cpu),
            "peak": _clamp(old["peak"] * max(peak, 0.05)),
            "samples": len(pairs),
        }
    return dict(sorted(out.items()))


def write_calibration(root: Path, factors: dict) -> None:
    from ..pipeline.store import write_file

    write_file(Path(root) / "costs" / CALIBRATION, contract.encode({"stages": factors}))


# ---------------------------------------------------------------- the workspace of a command
class Workspace:
    """What export and plan share: the store, the catalog version, its index, the selection, the pipeline and its
    parameters, the facts."""

    def __init__(self, args, cfg, log=_log):
        from ..pipeline.catalogdb import CatalogDB
        from ..pipeline.store import Store

        self.args, self.cfg, self.log = args, cfg, log
        self.root = Session(cfg).store_root()
        self.cache = cfg.path("paths", "cache")
        self.apk = cfg.path("paths", "apk")
        self.fetcher = CatalogFetcher(self.root, self.cache, cfg)
        self.store = Store(self.root, cache=self.cache, fetch=self.fetcher)
        self.db = CatalogDB(self.root)
        try:
            self.selection = parse_selection(getattr(args, "select", None))
        except ValueError as e:
            usage(args, str(e))
        self.installed = installed()
        self.calibration = load_calibration(self.root)
        self.scheduled = calibrated(self.installed.stages, self.calibration)  # the stages with calibrated costs
        self.version, self.remote, self.apk_catalog = self._catalog_version()
        if self.version.get("region"):
            self.cfg = cfg.for_region(self.version["region"])
            self.apk = self.cfg.path("paths", "apk")
        self._index: dict | None = None
        self._problems: list = []
        self._apk_names: set | None = None
        self._facts: dict = {}
        self._later = None  # the background fetch of the raw files
        self._cancel = threading.Event()  # set: start no more fetches
        self._names: dict = {}
        self._done: dict = {}  # task id -> key of the tasks with a result

    # ---------------------------------------------------------------- catalog
    def _catalog_version(self):
        from ..pipeline import catalogdb

        ref = getattr(self.args, "stored_catalog", None)
        if ref:
            try:
                try:
                    v = self.db.get(ref)
                except ValueError:
                    v = self.db.get(ref, self.cfg.get("catalog", "region"), self.cfg.get("catalog", "language"))
            except (KeyError, ValueError) as e:
                usage(self.args, f"--stored-catalog {ref}: {e.args[0] if e.args else e}")
            remote, apk = self.db.catalog_bytes(v)
            return v, remote, apk
        cat = Session(self.cfg).catalog(bundles=False)
        src = cat.sources()
        remote, apk = src["remote"], src.get("apk")
        source = cat.source.to_dict() if cat.source is not None else None
        vid = catalogdb.version_id(contract.sha256(remote), contract.sha256(apk) if apk is not None else None, source)
        v = next((x for x in self.db.versions() if x["id"] == vid), None)
        if v is None:
            v = self.db.add(
                remote,
                apk,
                region=self.cfg.get("catalog", "region"),
                language=self.cfg.get("catalog", "language"),
                apk_version_name=catalogdb.apk_version(self.apk),
                source=source,
                resource_version=source["version"] if source else cat.resource_version,
            )
        return v, remote, apk

    def catalogs_fact(self) -> dict:
        return {CATALOG_SUBJECT: self.db.inputs(self.version)}

    def index_task(self) -> Task:
        stage = self.installed.stages["catalog.index"]
        return describe(stage, CATALOG_SUBJECT, None, Env(self.store, {"catalogs": self.catalogs_fact()}))

    def index(self) -> dict:
        """The catalog index: from the store when catalog.index has run, else computed here."""
        if self._index is None:
            from ..pipeline import catalogdb

            t = self.index_task()
            doc = self.store.result(t.key)
            if doc is not None:
                (rec,) = [a for a in doc["artifacts"] if a["id"] == contract.artifact_id(t.id, "index")]
                self._index = self.store.document(rec["content"]["sha256"])
            else:
                self._index = catalogdb.index(self.remote, self.apk_catalog)
        return self._index

    def run_index(self) -> None:
        execute(self.index_task(), self.store, self.installed.stages)
        self._index = None

    def context(self) -> dict:
        v = self.version
        return {
            "region": self.cfg.get("catalog", "region"),
            "language": v.get("language") or self.cfg.get("catalog", "language"),
            "catalog": {
                "id": v["id"],
                "sha": v["remote"]["sha256"],
                "label": v["labels"][0] if v["labels"] else None,
                "resourceVersion": v.get("resourceVersion"),
                "apkCatalogSha": v["apk"]["sha256"] if v.get("apk") else None,
                "apkVersionName": v.get("apkVersionName"),
            },
        }

    def master_context(self, results) -> dict | None:
        """The master data a run read: {"version": the `version` of <master>/MasterManifest.json (None without
        one), "tables": {table: sha256} of the tables its view tasks read}; None when no view task has a result."""
        from ..pipeline.views import MASTER_ROLE

        tables, any_view = {}, False
        for tid, doc in results:
            if not tid.startswith("view."):
                continue
            any_view = True
            for role, sha in doc["keyParts"]["inputs"]:
                if role.startswith(MASTER_ROLE):
                    tables[role[len(MASTER_ROLE) :]] = sha
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
            usage(
                self.args,
                f"--views: no view {', '.join(unknown)} (views: {', '.join(a[5:] for a in avail) or 'none installed'})",
            )
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
                usage(self.args, f"--png-level {level}: expected 0 to 9")
            takers = [n for n in pipeline if "png" in stages[n].PARAMS]
            if not takers:
                usage(self.args, "--png-level: no stage of the pipeline encodes PNG")
            for n in takers:
                base = stages[n].PARAMS["png"]
                out.setdefault(n, {})["png"] = {**(base if isinstance(base, dict) else {}), "level": level}
        level = getattr(self.args, "flac_level", None)
        if level is not None:
            if not 0 <= level <= 12:
                usage(self.args, f"--flac-level {level}: expected 0 to 12")
            takers = [n for n in pipeline if "flac" in stages[n].PARAMS]
            if not takers:
                usage(self.args, "--flac-level: no stage of the pipeline encodes FLAC")
            for n in takers:
                out.setdefault(n, {})["flac"] = {**stages[n].PARAMS["flac"], "level": level}
        only = getattr(self.args, "only_class", None)
        if only:
            classes = sorted({c.strip() for c in only.split(",") if c.strip()})
            takers = [n for n in pipeline if "classes" in stages[n].PARAMS]
            if not classes or not takers:
                usage(
                    self.args,
                    "--only-class: "
                    + ("no class given" if not classes else "no stage of the pipeline selects classes"),
                )
            for n in takers:
                out.setdefault(n, {})["classes"] = classes
        for n, p in out.items():
            try:
                stages[n].normalize(p)
            except ValueError as e:
                usage(self.args, str(e))
        return out

    def workers(self) -> int:
        w = getattr(self.args, "workers", None)
        return max(0, w) if w is not None else usable_cpus()

    def memory(self) -> int | None:
        m = getattr(self.args, "memory", None)
        return int(m * GIB) if m is not None else default_memory()

    def recycle_rss(self) -> int:
        return recycle_rss(self.memory(), self.workers())

    # ---------------------------------------------------------------- inputs
    def facts(self, pipeline: list[str], fetch: bool, background: bool = False) -> dict:
        """The planning facts (module documentation). `fetch`: fetch the files whose content is not known yet
        (else they are Pending); what cannot be read is in problems() (after wait()). `background`: the raw files
        are fetched while the caller goes on (the fact waits for them when it is read)."""
        index = self.index()
        sel = select(index, self.selection)
        self._problems = []
        bundles = {b["stable"]: b for b in index["bundles"]}
        inputs = self._inputs("bundle", [bundles[s] for s in sel.census], fetch)
        sel.selected = [s for s in sel.selected if s in inputs]  # files that cannot be read: problems()
        facts = {
            "catalogs": self.catalogs_fact(),
            "index": index,
            "selected": sel.selected,
            "master": self.master() if any(n.startswith("view.") for n in pipeline) else None,
            "views": sorted(n[5:] for n in pipeline if n.startswith("view.")),
            "bundles": inputs,
            "raw": Inputs({}),
        }
        if any(n in pipeline for n in RAW_STAGES):
            from ..pipeline.cristages import boot_input

            raws = {r["stable"]: r for r in index["rawFiles"]}
            job = (self._inputs, "raw", [raws[s] for s in sel.raw], fetch)
            if background and fetch:
                pool = ThreadPoolExecutor(1, thread_name_prefix="nnnotes-raw")
                self._later = pool.submit(*job)
                pool.shutdown(wait=False)
                facts["raw"] = Later(self._later)
            else:
                facts["raw"] = job[0](*job[1:])
            facts["boot"] = boot_input(self.store, self.apk)
        self.selected = sel
        self._facts, self._names = facts, {}
        return facts

    def wait(self) -> None:
        """Wait for the background fetch of facts(background=True), if any (its errors are raised here)."""
        if self._later is not None:
            self._later.result()

    def cancel(self) -> None:
        """Start no more fetches: a background fetch ends once the downloads under way are done."""
        self._cancel.set()

    def problems(self) -> list[dict]:
        """Files of the selection that could not be read: {kind, stable, name, code, message}, sorted."""
        return sorted(self._problems, key=lambda p: (p["kind"], p["stable"]))

    def _cache_rel(self, kind: str, entry: dict, loc: dict) -> str:
        from ..source.addressables import remote_path

        prefix = ""
        if self.version.get("source"):
            from ..source.jp import Source

            prefix = Source.from_dict(self.version["source"]).cache_dir(Path(".")).as_posix() + "/"
            if not entry.get("remote", True) and self.version.get("apk"):
                prefix += "apk/" + self.version["apk"]["sha256"] + "/"
        if kind == "bundle":
            return prefix + f"bundles/{entry['name']}"
        rel = remote_path(loc["internalId"])
        if rel is None:  # a raw file of the APK (CatalogFetcher._apk_file)
            rel = apk_rel(loc["internalId"])
        return prefix + "raw/" + rel.lstrip("/")

    def _inputs(self, kind: str, entries: list[dict], fetch: bool) -> Inputs:
        from ..pipeline.catalogdb import by_id

        locs = by_id(self.index())
        memo_kind = "bundles" if kind == "bundle" else "raw"
        memo_prefix = self.version["id"] + ":" if self.version.get("source") else ""
        out, todo = {}, []
        for e in entries:
            loc = locs[e["location"]]
            rel = self._cache_rel(kind, e, loc)
            locators = ([{"kind": "cache", "path": rel}] if rel else []) + [
                {"kind": "catalog", "catalog": self.version["id"], "location": e["location"]}
            ]
            path = self.cache / rel if self.cache is not None and rel else None
            if path is not None and path.is_file() and path.stat().st_size > 0:
                todo.append((e, locators, path))
                continue
            known = self.store.named(memo_kind, memo_prefix + e["name"]) if e["name"] != e["stable"] else None
            if known is not None:  # a hashed file name: its recorded identity holds
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
            if self._cancel.is_set():
                return None
            try:
                if path is None:
                    path = self.fetcher.fetch_location(self.version["id"], e["location"])
                sha, size = self.store.identify(path, memo_kind, memo_prefix + e["name"])
            except ConfigError:
                raise
            except KeyError as x:  # not in the APK
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
        from ..source.catalog import APK_AA_DIR

        if self._apk_names is None:
            try:
                with ApkSet(self.apk) as z:
                    self._apk_names = set(z.namelist())
            except (OSError, zipfile.BadZipFile):
                raise ConfigError(f"setting paths.apk: {self.apk} is not a readable APK") from None
        return APK_AA_DIR + apk_rel(internal_id) in self._apk_names

    def _problem(self, kind: str, e: dict, code: str, message: str) -> None:
        self._problems.append(
            {"kind": kind, "stable": e["stable"], "name": e["name"], "code": code, "message": message}
        )

    # ---------------------------------------------------------------- explain
    def explain(self, pipeline: list[str], params: dict, orch=None, layouts=(), placement=None) -> list[str]:
        ctx = self.context()
        c = ctx["catalog"]
        lines = [
            f"store: {self.root}",
            f"cache: {self.cache if self.cache is not None else 'not set'}",
            f"context: region {ctx['region'] or '-'}, language {ctx['language'] or '-'}, catalog "
            f"{c['label'] or '-'} ({c['id'][:12]}), APK catalog {(c['apkCatalogSha'] or '-')[:12]}",
            f"selection: {', '.join(self.selection)}",
            f"pipeline: {', '.join(pipeline)}",
            f"not installed: {', '.join(self.installed.missing) or 'none'}",
        ]
        for n in pipeline:
            lines.append(f"params {n}: {contract.key_text(self.installed.stages[n].normalize(params.get(n)))}")
        lines.append(f"fetch workers: {getattr(self.args, 'fetch_workers', None) or FETCH_WORKERS}")
        for n in pipeline:
            if n in self.calibration:
                f = self.calibration[n]
                lines.append(
                    f"cost calibration {n}: CPU x{f['cpu']}, peak memory x{f['peak']} "
                    f"({f.get('samples', 0)} measured tasks)"
                )
        if orch is not None:
            lines += orch.explain()
        if layouts:
            how = placement[1] if placement else f"{link_mode(self.args, self.cfg)}, probed when the files are placed"
            lines.append(f"layouts: {', '.join(layouts)} (placement: {how})")
        return lines

    # ---------------------------------------------------------------- plan
    def previous(self, since: str | None) -> dict | None:
        if not since:
            return self.store.latest_run(self.selection)
        doc = self.store.run(since) if contract.is_sha256(since) else None
        if doc is None:
            runs = sorted(p.name[:-5] for p in (self.root / "runs").glob("*.json") if p.name.startswith(since))
            if len(runs) != 1:
                usage(self.args, f"--since {since}: {'no run' if not runs else f'{len(runs)} runs'} with this id")
            doc = self.store.run(runs[0])
        return doc

    def plan(self, pipeline: list[str], params: dict, *, since=None, out=None, layouts=()):
        from ..pipeline import plan as plan_mod

        facts = self.facts(pipeline, fetch=False)
        p = plan_mod.plan(
            self.scheduled,
            pipeline,
            self.store,
            params=params,
            facts=facts,
            previous=self.previous(since),
            workers=self.workers(),
            memory=self.memory(),
        )
        if out is not None and layouts:
            from ..pipeline import layout

            self.done({n["id"]: n["key"] for n in p.doc["nodes"] if n["status"] == "hit"})
            hits = [(n["id"], n["key"]) for n in p.doc["nodes"] if n["status"] == "hit" and self._output(n["stage"])]
            placed = self.layout_input(dict(hits))
            for tid, key in hits:
                placed.add(tid, self.store.committed(key, tid))
            for name, d in layout_dirs(Path(out), layouts).items():
                entries, _ = self.layout_entries(name, placed, store_derived=False)
                p.doc["summary"]["layouts"][name] = layout.diff(d, entries)
        return p

    def _output(self, stage: str) -> bool:
        src = self.installed.sources.get(stage)
        return bool(src and src.output)

    # ---------------------------------------------------------------- layouts
    def layout_input(self, tasks: dict) -> LayoutInput:
        """An empty LayoutInput for the output tasks `tasks` ({task id: key}) that knows the objects the derived
        documents name: the results of the stages that make them are read here (small: the views) and their
        documents made once with no paths, noting the objects asked for."""
        stages, asked = self.installed.stages, _Asked()
        makers = sorted(t for t in tasks if hasattr(stages[contract.parse_task_id(t)[0]], "derived"))
        for tid in makers:
            stages[contract.parse_task_id(tid)[0]].derived(tid, self.store.result(tasks[tid]), self.store, asked)
        return LayoutInput(asked.objects_asked, makers)

    def layout_entries(self, name: str, placed: LayoutInput, *, store_derived: bool = True) -> tuple[list[dict], dict]:
        """The entries of layout `name` for the results of the output stages given to `placed` (derived documents
        of the stages in `original`) and its report."""
        from ..pipeline import layout

        stages = self.installed.stages
        sources, named = [], []
        for tid, doc in placed.results:
            stage_name, subject = contract.parse_task_id(tid)
            stage = stages[stage_name]
            names = self.names(stage_name).get(subject) if name == "original" else None
            if names:
                sources += [layout.Source(tid, doc, n) for n in names]
                if len(names) > 1:
                    named.append({"task": tid, "names": list(names)})
            else:
                sources.append(layout.Source(tid, doc, layout_root(stage, self.installed.sources[stage_name], subject)))
        index = self.index()
        case = {loc["internalId"].lower(): loc["internalId"] for loc in index["locations"] if loc["kind"] == "asset"}
        if name != "original":
            return layout.entries(name, sources)
        derives = lambda s: hasattr(stages[contract.parse_task_id(s.task)[0]], "derived")  # noqa: E731
        roots = {}  # every object of a bundle under its export task's root
        for s in sources:
            if s.task.startswith(EXPORT_STAGE + ":") and s.root is not None:
                for f in s.result["files"]:
                    roots.setdefault(f, s.root)
        entries, report = layout.entries(name, [s for s in sources if not derives(s)], case=case, object_roots=roots)
        report["names"] = named
        extra = self._derived(sources, entries, store_derived, placed.objects)
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

    def _derived(self, sources, entries: list[dict], store_derived: bool, objects: dict) -> list[dict]:
        from ..pipeline import layout

        stages = self.installed.stages
        makers = list(
            {s.task: s for s in sources if hasattr(stages[contract.parse_task_id(s.task)[0]], "derived")}.values()
        )
        if not makers:
            return []
        paths = LayoutPaths(layout.paths(entries), objects)
        out = []
        for s in makers:
            stage = stages[contract.parse_task_id(s.task)[0]]
            for rel, data in stage.derived(s.task, s.result, self.store, paths):
                sha = self.store.put(data) if store_derived else contract.sha256(data)
                out.append(
                    {
                        "path": layout.escape_path(rel.split("/")),
                        "sha256": sha,
                        "size": len(data),
                        "id": contract.artifact_id(s.task, "derived:" + rel),
                    }
                )
        return out


class LayoutInput:
    """What the layouts read of the output stages' results, given one at a time (add) and not kept whole (the
    results of a full catalog take GBs as objects): of each result its artifacts with the fields the layouts use
    (id, content, the object facts) and, for an export task, the serialized files of its objects; the whole result
    of a stage with derived documents (`makers`: small, the views); the artifacts of the objects those documents
    name (`asked`: object ids; an object contained in another: the artifact it is part of)."""

    def __init__(self, asked=(), makers=()):
        self.asked, self.makers = set(asked), set(makers)
        self.results: list[tuple[str, dict]] = []
        self.objects: dict[str, set] = {}

    def add(self, tid: str, doc: dict) -> None:
        if self.asked:
            for it in doc["items"]:
                if it["object"] in self.asked:
                    arts = it.get("artifacts") or ([it["in"]] if "in" in it else [])
                    self.objects.setdefault(it["object"], set()).update(arts)
        if tid in self.makers:
            self.results.append((tid, doc))
            return
        arts = []
        for a in doc["artifacts"]:
            c = a["content"]
            slim = {"id": a["id"], "content": {"sha256": c["sha256"], "size": c["size"], "ext": c["ext"]}}
            obj = a.get("provenance", {}).get("object")
            if obj:
                slim["provenance"] = {"object": obj}
            arts.append(slim)
        files = {}
        if tid.startswith(EXPORT_STAGE + ":"):
            for it in doc["items"]:
                files.setdefault(contract.parse_object_id(it["object"])[0], None)
        self.results.append((tid, {"artifacts": arts, "files": list(files)}))


class _Asked:
    """LayoutPaths for a first making of the derived documents: places nothing, notes the objects asked for."""

    def __init__(self):
        self.objects_asked: set[str] = set()

    def artifact(self, aid: str) -> None:
        return None

    def objects(self, oid: str) -> list[str]:
        self.objects_asked.add(oid)
        return []


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


class Later(Mapping):
    """A mapping a background job makes (Future): reading it waits for the job."""

    def __init__(self, future):
        self._future = future

    def __getitem__(self, key):
        return self._future.result()[key]

    def __contains__(self, key) -> bool:
        return key in self._future.result()

    def __iter__(self):
        return iter(self._future.result())

    def __len__(self) -> int:
        return len(self._future.result())

    def waiting(self) -> list[str]:
        return self._future.result().waiting()


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


# ---------------------------------------------------------------- single subjects
def _literal(name: str) -> str:
    """A stable name as a `bundle:` glob that matches that name only."""
    return "".join(f"[{c}]" if c in "*?[" else c for c in name)


def key_selection(index: dict, keys=(), files=()) -> list[str]:
    """The selection of the bundles and raw files the catalog keys `keys` load (every location of each key and its
    dependency closure) and of the files `files` (stable names), by stable name."""
    from ..pipeline import catalogdb

    want, reach = set(keys), catalogdb.closures(index)
    names = set(files)
    for loc in index["locations"]:
        if loc["primaryKey"] in want:
            names |= {stable for _, stable in reach[loc["id"]]}
    return parse_selection([f"bundle:{_literal(stable)}" for stable in names])


def subject_pipeline(stages: dict[str, Stage], want) -> list[str]:
    """The stages `want` and the stages they read (Stage.after, transitively), in the order of the pipeline."""
    need, stack = set(), list(want)
    while stack:
        name = stack.pop()
        if name not in need:
            need.add(name)
            stack += [a for a in stages[name].after if a in stages]
    return [n for n in stages if n in need or n == "catalog.index"]


class SubjectRun:
    """The stages of one command's subjects, run in this process: `run(want, params, keys=, files=)` runs the stages
    `want` and those they read over the files the catalog keys `keys` load and the files `files` (key_selection);
    `result(task id)` is a task's result. The store, catalog and settings are those of `export assets`."""

    def __init__(self, args, cfg):
        self.ws = Workspace(args, cfg)
        self.ws.run_index()

    @property
    def store(self):
        return self.ws.store

    def index(self) -> dict:
        return self.ws.index()

    def run(self, want, params: dict | None = None, *, keys=(), files=()):
        from ..pipeline.orchestrate import Orchestrator

        ws = self.ws
        ws.selection = key_selection(ws.index(), keys, files)
        pipeline = subject_pipeline(ws.installed.stages, want)
        facts = ws.facts(pipeline, fetch=True)
        self.orch = Orchestrator(ws.store, ws.scheduled, workers=0, log=ws.log)
        self.last = self.orch.run(pipeline, params=params, facts=facts, context=ws.context(), selection=ws.selection)
        return self.last

    def result(self, tid: str) -> dict:
        """The result of task `tid` of the last run; when it failed or did not run (a file it reads could not be
        read), the command exits with status 1 naming why."""
        t = self.last.tasks.get(tid)
        if t is None or t["status"] == "failed":
            why = [f"{f['code']}: {f['message']}" for f in self.last.failures_ if f["task"] == tid]
            why += [f"{p['name']}: {p['message']}" for p in self.ws.problems()]
            sys.exit(f"nnnotes: {tid}: no result" + (f" ({'; '.join(why)})" if why else ""))
        return self.store.committed(t["key"], tid)

    def names(self, stage: str) -> dict:
        """{subject: catalog names} of a stage that names its tasks' files (Workspace.names), after the last run."""
        self.ws.done({tid: t["key"] for tid, t in self.last.tasks.items() if t["status"] != "failed"})
        return self.ws.names(stage)


def place(store, doc: dict, out: Path, roles=None) -> list[str]:
    """Write the artifacts of a result into the directory `out`, each as the file its role names (the part of its
    id after the task id), relative to `out`; `roles`: only those. Returns the files written, sorted."""
    from ..pipeline.store import write_file

    written = []
    for a in doc["artifacts"]:
        _, role = contract.parse_artifact_id(a["id"])
        if roles is not None and role not in roles:
            continue
        dst = Path(out, *role.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        write_file(dst, store.read(a["content"]["sha256"]))
        written.append(role)
    return sorted(written)


# ---------------------------------------------------------------- export
class Totals:
    """What export reports of the results besides the run's counts, from its one reading of each (Run.scan): the
    census totals of `subjects` ({class: objects}, the census artifacts' facts) and the results of the view tasks
    (small; their master data tables and gaps)."""

    def __init__(self, subjects):
        self.want = {contract.task_id("unity.census", s) for s in subjects}
        self.classes: Counter = Counter()
        self.views: list[tuple[str, dict]] = []

    def add(self, tid: str, doc: dict) -> None:
        if tid in self.want:
            for a in doc["artifacts"]:
                self.classes.update(a["semantics"].get("facts", {}).get("classes", {}))
        if tid.startswith("view."):
            self.views.append((tid, doc))

    def census(self) -> dict[str, int]:
        return dict(sorted(self.classes.items()))


def view_gaps(results) -> int:
    return sum(
        int(a["semantics"].get("facts", {}).get("gaps", 0))
        for tid, doc in results
        if tid.startswith("view.")
        for a in doc["artifacts"]
    )


def _write_report(d: Path, name: str, doc) -> Path:
    from ..pipeline.store import write_file

    p = d / name
    write_file(p, contract.encode(doc))
    return p


def cmd_export(args, cfg):
    from ..pipeline import layout
    from ..pipeline.orchestrate import Orchestrator, failure

    try:
        names = parse_layouts(args.layout)
    except ValueError as e:
        usage(args, str(e))
    use(cfg)  # the tools the atoms name are part of the keys
    ws = Workspace(args, cfg)
    pipeline = ws.pipeline()
    params = ws.params(pipeline)
    orch = Orchestrator(
        ws.store,
        ws.scheduled,
        workers=ws.workers(),
        memory=ws.memory(),
        recycle_tasks=RECYCLE_TASKS,
        recycle_rss=ws.recycle_rss(),
        initializer=_worker_init,
        initargs=(cfg,),
        log=ws.log,
    )
    out = Path(args.out)
    placement = None
    if not args.dry_run:  # before any work: an explicit method that cannot work stops it
        try:
            placement = layout.probe(out, ws.root / "cas" / "sha256", link_mode(args, cfg))
        except layout.LayoutError as e:
            usage(args, str(e))
    if args.explain:
        for line in ws.explain(pipeline, params, orch, names, placement):
            print(line, file=sys.stderr)
    if placement is None:  # --dry-run
        sys.stdout.write(ws.plan(pipeline, params, out=out, layouts=names).text())
        return
    ws.run_index()
    facts = ws.facts(pipeline, fetch=True, background=True)
    sel = ws.selected
    ws.log(
        f"{len(sel.selected)} bundles selected ({len(sel.census)} with their dependencies), "
        f"{len(sel.raw)} raw files"
        + (" (fetched while the bundle stages run)" if isinstance(facts["raw"], Later) else "")
        + f"; {sum(p['kind'] == 'bundle' for p in ws.problems())} bundles not readable"
    )
    aborted = None
    try:
        run = orch.run(pipeline, params=params, facts=facts, context=ws.context(), selection=ws.selection)
    except ConfigError as e:
        run = getattr(e, "run", None)
        if run is None:
            ws.cancel()
            raise
        aborted = str(e)
    except KeyboardInterrupt:
        ws.cancel()
        print("nnnotes: interrupted (results written so far stay valid)", file=sys.stderr)
        sys.exit(EXIT_ABORTED)
    try:
        ws.wait()
    except ConfigError as e:  # the raw files' fetch stopped (the run stopped with it)
        aborted = aborted or str(e)
    if isinstance(facts["raw"], Later) and aborted is None:
        ws.log(f"{sum(p['kind'] == 'raw' for p in ws.problems())} raw files not readable")
    stage_of = {"bundle": "unity.census", "raw": next((n for n in RAW_STAGES if n in pipeline), "cri.audio")}
    for p in ws.problems():
        if p["code"] == "source.error":
            run.failures_.append(
                failure(
                    contract.task_id(stage_of[p["kind"]], p["stable"]),
                    stage_of[p["kind"]],
                    p["code"],
                    p["message"],
                    p["name"],
                )
            )
    done = {tid: t["key"] for tid, t in run.tasks.items() if t["status"] != "failed"}
    ws.done(done)
    output = {tid for tid in done if ws._output(contract.parse_task_id(tid)[0])}
    placed = ws.layout_input({tid: done[tid] for tid in output})
    totals = Totals(sel.selected)

    def visit(tid, doc):
        totals.add(tid, doc)
        if tid in output:
            placed.add(tid, doc)

    run.scan(visit, census=totals.census)  # the only reading of the results
    run.context["master"] = ws.master_context(totals.views)
    reports = out / REPORTS
    written, shas, unplaced = {}, {}, 0
    for name, d in layout_dirs(out, names).items():
        entries, report = ws.layout_entries(name, placed)
        w = layout.materialize(d, name, {}, entries, layout.from_store(ws.store), link=placement[0])
        shas[name] = w["sha256"]
        written[name] = {
            "dir": str(d),
            "files": len(w["manifest"]["entries"]),
            "write": w["write"],
            "remove": w["remove"],
            "keep": w["keep"],
            "cloned": w["cloned"],
            "linked": w["linked"],
            "copied": w["copied"],
            "failed": len(w["failed"]),
            "collisions": len(report["collisions"]),
            "shared": len(report.get("shared", [])),
        }
        unplaced += len(w["failed"])
        ws.log(
            f"layout {name}: placed {w['cloned'] + w['linked'] + w['copied']}: {w['cloned']} cloned / "
            f"{w['linked']} hard-linked (read-only) / {w['copied']} copied"
            + (f"; {len(w['failed'])} not placed (layout-{name}.json)" if w["failed"] else "")
        )
        _write_report(reports, f"layout-{name}.json", dict(report, failed=w["failed"]) if w["failed"] else report)
    manifest = run.write(shas, placement={"method": placement[0], "reason": placement[1]})
    write_calibration(ws.root, calibrate(run.log, ws.calibration, orch.worker_bytes))
    _write_report(reports, "run.json", manifest)
    _write_report(reports, "coverage.json", run.coverage(totals.census()))
    _write_report(reports, "failures.json", run.failures_doc())
    _write_report(reports, "sources.json", {"problems": ws.problems()})
    s = manifest["summary"]
    gaps = view_gaps(totals.views)
    common.report(
        args,
        {
            "run": manifest["id"],
            "selection": ws.selection,
            "tasks": {k: s[k] for k in ("tasks", "hit", "ran", "failed")},
            "items": s["items"],
            "artifacts": s["artifacts"],
            "failures": len(run.failures()),
            "sources": dict(sorted(Counter(p["code"] for p in ws.problems()).items())),
            "viewGaps": gaps,
            "placement": manifest["placement"],
            "layouts": written,
            "reports": str(reports),
        },
    )
    if aborted:
        print(f"nnnotes: aborted: {aborted}", file=sys.stderr)
        sys.exit(EXIT_ABORTED)
    if run.failures() or unplaced or (args.strict and gaps):
        sys.exit(EXIT_FAILED)


def link_mode(args, cfg) -> str:
    """How export places the layout files (layout.LINKS): --link, else [export] link, else auto."""
    from ..pipeline.layout import LINKS

    v = getattr(args, "link", None)
    if v:
        return v
    v = cfg.get("export", "link") or "auto"
    if v not in LINKS:
        raise ConfigError(f"setting export.link: {v!r} is not one of {', '.join(LINKS)}")
    return v


# ---------------------------------------------------------------- plan
def cmd_plan(args, cfg):
    from ..pipeline.orchestrate import Orchestrator

    layouts = []
    if args.out:
        try:
            layouts = parse_layouts(args.layout)
        except ValueError as e:
            usage(args, str(e))
    use(cfg)  # the tools the atoms name are part of the keys
    ws = Workspace(args, cfg)
    pipeline = ws.pipeline()
    params = ws.params(pipeline)
    if args.explain:
        for line in ws.explain(pipeline, params, layouts=layouts):
            print(line, file=sys.stderr)
    if args.census:
        ws.run_index()
        facts = ws.facts(["catalog.index", "unity.census"], fetch=True)
        Orchestrator(
            ws.store,
            ws.scheduled,
            workers=ws.workers(),
            memory=ws.memory(),
            recycle_tasks=RECYCLE_TASKS,
            recycle_rss=ws.recycle_rss(),
            initializer=_worker_init,
            initargs=(cfg,),
            log=ws.log,
        ).run(["catalog.index", "unity.census"], facts=facts)
    p = ws.plan(pipeline, params, since=args.since, out=args.out, layouts=layouts)
    if args.emit_tasks:
        files = p.emit(args.emit_tasks)
        ws.log(f"{len(files)} task descriptions in {args.emit_tasks}")
    if args.why:
        common.report(args, why(ws, p, args.why, args.since), why_lines)
    elif args.json:
        print_json(p.doc)
    else:
        sys.stdout.write(p.text())
    if args.check:
        sys.exit(p.check())


def why(ws: Workspace, p, tid: str, since=None) -> dict:
    """Why a task of a plan has its status: its reasons, and its key parts beside those of the previous run."""
    node = next((n for n in p.doc["nodes"] if n["id"] == tid), None)
    if node is None:
        usage(ws.args, f"--why {tid}: no task with this id in the plan")
    new = None
    if node["key"] is not None:
        t = next((t for t in p.tasks if t.key == node["key"]), None)
        doc = None if t is not None else ws.store.result(node["key"])
        new = t.key_parts() if t is not None else (doc["keyParts"] if doc else None)
    prev = ws.previous(since)
    old_task = next((t for t in prev.get("tasks", []) if t["id"] == tid), None) if prev is not None else None
    previous = old = None
    if prev is not None and old_task is not None:
        previous = {"run": prev["id"], "key": old_task.get("key"), "status": old_task["status"]}
        if old_task.get("key"):
            doc = ws.store.result(old_task["key"])
            old = doc["keyParts"] if doc else None
    return {
        "task": tid,
        "status": node["status"],
        "key": node["key"],
        "reasons": node["reasons"],
        "previous": previous,
        "keyParts": {"old": old, "new": new},
    }


def why_lines(r: dict) -> list[str]:
    from ..pipeline.plan import reason_text

    prev = r["previous"]
    return [
        f"{r['task']}: {r['status']} ({', '.join(reason_text(x) for x in r['reasons']) or '-'})",
        f"key {r['key'] or '-'}",
        f"previous run {prev['run']}: {prev['status']}, key {prev['key'] or '-'}" if prev else "no previous run",
    ]


# ---------------------------------------------------------------- store run
def run_lines(r: dict) -> list[str]:
    lines = []
    for t in r["tasks"]:
        tail = t["result"] if t["status"] != "failed" else f"{t['code']}: {t['message']}"
        lines.append(f"{t['status']:<6} {t['id']}  {tail}")
    s = r["summary"]
    return [*lines, f"{s['tasks']} tasks: {s['hit']} hit, {s['ran']} ran, {s['failed']} failed"]


def _task_files(items) -> list[Path]:
    out = []
    for s in items:
        p = Path(s)
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def cmd_run_stage(args, cfg):
    from ..pipeline.orchestrate import run_one
    from ..pipeline.store import Store

    problems, tasks = [], []
    for f in _task_files(args.tasks):
        try:
            tasks.append(Task.from_json(contract.loads(f.read_bytes())))
        except OSError as e:
            usage(args, f"{f}: {e.strerror or e}")
        except (IncompatibleTask, ValueError) as e:
            problems.append(f"{f}: {e}")
    inst = installed(sorted({t.stage for t in tasks}))
    for t in tasks:
        s = inst.stages.get(t.stage)
        if s is None:
            problems.append(f"task {t.id}: no stage {t.stage} in this installation")
        elif s.version != t.version:
            problems.append(
                f"task {t.id}: stage {t.stage} is version {s.version} here, the task was described "
                f"for version {t.version}"
            )
    if problems:
        for m in problems:
            print(f"nnnotes: incompatible task: {m}", file=sys.stderr)
        sys.exit(EXIT_INCOMPATIBLE)
    root = Session(cfg).store_root()
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
    common.report(
        args,
        {"tasks": out, "summary": {"tasks": len(tasks), "hit": n["hit"], "ran": n["ran"], "failed": n["failed"]}},
        run_lines,
    )
    if bad:
        sys.exit(EXIT_FAILED)


# ---------------------------------------------------------------- catalog versions
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
        t = next(
            (
                t
                for t in doc["tasks"]
                if t["id"] == contract.task_id("link.addresses", CATALOG_SUBJECT) and t["status"] != "failed"
            ),
            None,
        )
        res = store.result(t["key"]) if t else None
        if res:
            (a,) = res["artifacts"]
            return contract.loads(store.read(a["content"]["sha256"]))
    return None


# ---------------------------------------------------------------- parser
def _gib(s: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s}: expected a size in GiB") from None
    if v <= 0:
        raise argparse.ArgumentTypeError(f"{s}: expected a positive size in GiB")
    return v


def run_args(c) -> None:
    """The selection and parameters shared by `export assets` and `store plan`."""
    c.add_argument(
        "--select",
        action="append",
        metavar="SEL",
        help="group:<group>, key:<key prefix> or bundle:<glob> (repeatable; default: everything)",
    )
    c.add_argument(
        "--views",
        metavar="all|none|a,b",
        help="semantic views to build (default: all when master data is set, else none)",
    )
    c.add_argument(
        "--stored-catalog",
        metavar="LABEL|SHA",
        help="a catalog version of the store (`catalog versions`; default: the current catalog)",
    )
    c.add_argument("--png-level", type=int, metavar="N", help="PNG compression level 0-9 (changes the output)")
    c.add_argument(
        "--flac-level",
        type=int,
        metavar="N",
        help="FLAC compression level 0-12 of the CRI audio (default 8; every level decodes to the same "
        "samples; changes the files)",
    )
    c.add_argument("--only-class", metavar="C,...", help="export only objects of these classes")
    c.add_argument(
        "--workers",
        type=nonnegative,
        metavar="N",
        help="worker processes (default: the CPUs this process may use; 0: this process)",
    )
    c.add_argument(
        "--memory",
        type=_gib,
        metavar="GiB",
        help="memory budget of the running tasks and workers (default: 80 %% of the physical memory)",
    )
    c.add_argument("--fetch-workers", type=int, metavar="N", help=f"parallel downloads (default {FETCH_WORKERS})")
    c.add_argument("--layout", default="original", help="original, cas or original,cas (default original)")
    c.add_argument("--explain", action="store_true", help="print every setting of the run first")
