"""Live2D models -> the model part of an ournotes-player site (web.py builds the charts of the same site).

    <site>/models.json             model index: id, manifest path, key, listing facts, sizes
    <site>/models/<id>.json        model manifest (format web.MANIFEST_FORMAT): every path of the model's files ->
                                   {asset, size}, or, for a large JSON object, {parts: [[key, asset, size], ...], size}
                                   (as a chart manifest)
    <site>/assets/<sha256>.<ext>   content-addressed files, shared with the charts (the models share their shaders)

The files of one model (the paths in its manifest), only those the player reads (read_files):

    model.json                     index: format (MODEL_FORMAT), name, key, moc3, prefab, textures, canvas, shaders,
                                   resources, motionSync
    <name>.moc3                    the moc3 (as live2d.extract_runtime writes it)
    <name>.prefab.json             the whole prefab, clips, expressions and controllers inlined (the same)
    textures/*.png                 the atlas pages the drawables draw with (the same)
    shaders/shaders.json           the shader index (shader.py layout, filtered as in the chart sites: web.collect),
    shaders/<shader>.json          the parsed forms and the GLES3 programs (GLSL ES 3.00) the drawables' materials
    shaders/<shader>/gles3/*.glsl  select by their keywords, each also with STORY_KEYWORDS (the keywords the story
                                   renderer adds to a character draw); the mask shader's only when a drawable is masked

`resources` in model.json: the materials the Cubism mask pass draws with (the Resources.Load paths of RESOURCES, read
from the APK's boot data). `motionSync`: the prefab's root has the MotionSync controller with its CRI audio input
(has_motion_sync); a model without it takes its mouth in a story from the voice's CRI Lips analysis.

Models are the catalog keys Character/Live2D/<group>/<name>/model/<name>; a model's id is <name> (unique in the
catalog, URL-safe). Each model is exported into a temporary directory, stored, and the directory deleted; models run in
parallel worker processes (catalog cache writes serialized by a lock). A model whose manifest exists and is current
is skipped unless `force`; an outdated one (`outdated`: its model.json older than MODEL_FORMAT or without
motionSync, or unreadable) is built again. Same inputs and library versions give byte-identical outputs. A model's
files read no master data and the regions serve the same catalog, so one build serves every region of a site (the
bundles are fetched from the CDN of `region`).

Names (optional): when master data is configured for `region`, the listing of each model the master data maps to a
character (model_names: MasterCharacterCostume -> MasterCharacter -> MasterText) gets `character`, `names` and
`label`; the manifests of skipped models get the same fields of this build. Without master data the fields are not
written (and skipped manifests keep theirs).

Model sources: story.build takes the models of an episode from a source, `ensure(address) -> {"id", "motionSync"}`
plus `story_fields(story_dir)` (what story.json gets besides `models`). ModelDir exports each model into <dir>/<id>/
(the files above; `nnnotes story`), SiteModels reads model.json from the model manifests of a site (the site's
stories).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from . import jsonio, languages, live2d, master
from . import shader as shader_mod
from .config import Config, usable_cpus, use
from .export import Exporter
from .web import (MANIFEST_FORMAT, MODELS_DIR, MODELS_INDEX, SHADER_PLATFORM, SHADER_TYPE, SITE_FORMAT, Store, _dump,
                  _lock_fetches, _log, check_player, collect, entry_assets, entry_text, site_store, text_asset,
                  write_index, write_player)

LIVE2D_PREFIX = "Character/Live2D/"
MODEL_KEY = re.compile(r"Character/Live2D/(?P<group>[^/]+)/(?P<name>[^/]+)/model/(?P=name)")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
MODEL_INDEX = "model.json"
MODEL_FORMAT = 2                                   # model.json "format"
SHADER_DIR = "shaders"
MASK_KEYWORD = "CUBISM_MASK_ON"                    # a drawable material's keyword: the drawable is masked
# keywords the story renderer adds to a character draw (_ADDITIONAL_LIGHTS_VERTEX: at quality 4); the mask pass draws
# with none
STORY_KEYWORDS = ("_ADDITIONAL_LIGHTS_VERTEX",)
# Resources.Load paths of the materials the Cubism mask pass draws with
RESOURCES = {
    "cubismMask": "Live2D/Cubism/Materials/Mask",
    "cubismMaskCulling": "Live2D/Cubism/Materials/MaskCulling",
}
# a model's MotionSync path (the player's CubismMotionSyncController.fromPrefab): both components on the prefab's root
MOTION_SYNC_COMPONENTS = ("CubismMotionSyncController", "Live2DMotionSyncCriAudioInput")
COSTUME_TABLE = "MasterCharacterCostume"           # _live2dPath (the key after LIVE2D_PREFIX) -> _characterID
NAME_FIELDS = ("character", "names", "label")      # listing fields from the master data (model_names)


# ---------------------------------------------------------------- models and ids
def model_id(key: str) -> str:
    """The id of a model key: its <name>."""
    m = MODEL_KEY.fullmatch(key)
    if not m:
        raise ValueError(f"{key}: not a Live2D model key (Character/Live2D/<group>/<name>/model/<name>)")
    if not MODEL_ID.fullmatch(m["name"]):
        raise ValueError(f"{key}: model name {m['name']!r} is not URL-safe")
    return m["name"]


def model_keys(keys) -> dict[str, str]:
    """{id: key} of the model keys among `keys`, in id order; two keys with the same id raise."""
    out: dict[str, str] = {}
    for k in keys:
        if MODEL_KEY.fullmatch(k):
            i = model_id(k)
            if out.get(i, k) != k:
                raise ValueError(f"model id {i}: keys {out[i]} and {k}")
            out[i] = k
    return dict(sorted(out.items()))


def select(specs, models: dict[str, str]) -> dict[str, str]:
    """The models `specs` names (ids or keys; None: all) as {id: key} in id order, from `models` ({id: key})."""
    if specs is None:
        return dict(models)
    by_key = {k: i for i, k in models.items()}
    out, unknown = {}, []
    for s in specs:
        i = s if s in models else by_key.get(s)
        if i is None:
            unknown.append(s)
        else:
            out[i] = models[i]
    if unknown:
        raise ValueError(f"not a Live2D model of the catalog: {', '.join(unknown)} "
                         f"(give a model id or a key Character/Live2D/<group>/<name>/model/<name>)")
    return dict(sorted(out.items()))


def catalog_models(cat, specs=None) -> dict[str, str]:
    """`select` over the model keys of the catalog `cat`."""
    return select(specs, model_keys(cat.keys(LIVE2D_PREFIX)))


# ---------------------------------------------------------------- names from the master data
def model_names(master_dir: Path, language: str | None = None) -> dict[str, dict]:
    """{model key: {"character", "names", "label"}} of the models the master data maps to one character: every
    MasterCharacterCostume row maps the key LIVE2D_PREFIX + `_live2dPath` to `_characterID` (the MasterCharacter id,
    `character`); `names`: {language code: the MasterText of the character's `_nameTextID`}, the languages with a
    text; `label`: the name in `language` (left out without `language` or a text in it). A key the rows map to two
    characters, or a character without a name text, is left out."""
    chars_of: dict[str, set] = {}
    for r in master.table(master_dir, COSTUME_TABLE):
        if r.get("_live2dPath") and r.get("_characterID") is not None:
            chars_of.setdefault(LIVE2D_PREFIX + r["_live2dPath"], set()).add(r["_characterID"])
    chars = {r["_id"]: r for r in master.table(master_dir, "MasterCharacter")}
    texts = {r["_id"]: r for r in master.table(master_dir, "MasterText")}
    out = {}
    for key, ids in sorted(chars_of.items()):
        if len(ids) != 1:
            continue
        (cid,) = ids
        row = texts.get((chars.get(cid) or {}).get("_nameTextID"))
        names = {c: t for c, t in languages.texts(row).items() if isinstance(t, str) and t} if row else {}
        if not names:
            continue
        entry = {"character": cid, "names": names}
        if language in names:
            entry["label"] = names[language]
        out[key] = entry
    return out


def master_names(cfg: Config, region: str | None = None, log=None) -> dict[str, dict] | None:
    """model_names of the master data of `region` (default: [catalog] region; the --master flag, else
    [servers.<region>] master, else [paths] master), labels in [catalog] language; None when no master data is
    configured or it lacks a table model_names reads (logged)."""
    section, key = cfg.master(region or cfg.get("catalog", "region"))
    if cfg.path(section, key) is None:
        return None
    from .cli import master_dir
    code = cfg.get("catalog", "language")
    try:
        return model_names(master_dir(cfg, region), languages.check(code) if code else None)
    except FileNotFoundError as e:
        (log or _log)(f"model names left out: {Path(e.filename).name if e.filename else e} not in the master data")
        return None


def refresh_names(site: Path, mid: str, entry: dict | None) -> bool:
    """Set the NAME_FIELDS of the model manifest of `mid` to those of `entry` (None: none); True when it changed."""
    p = Path(site) / MODELS_DIR / f"{mid}.json"
    man = json.loads(p.read_text(encoding="utf-8"))
    model = {k: v for k, v in man.get("model", {}).items() if k not in NAME_FIELDS}
    model.update({k: entry[k] for k in NAME_FIELDS if entry and k in entry})
    if model == man.get("model"):
        return False
    man["model"] = model
    p.write_bytes(_dump(man))
    return True


# ---------------------------------------------------------------- one model
def shader_names(doc) -> set[str]:
    """Names of the shaders an export references (its {"shader": <name>} objects)."""
    out: set[str] = set()
    stack = [doc]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            s = v.get("shader")
            if isinstance(s, str) and len(v) == 1:
                out.add(s)
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return out


def drawable_materials(prefab: dict) -> list[dict]:
    """The material of every drawable: MeshRenderer.m_Materials[0] of the prefab's nodes."""
    return [c["m_Materials"][0] for n in prefab["nodes"] for c in n["components"]
            if c.get("type") == "MeshRenderer" and c.get("m_Materials") and c["m_Materials"][0]]


def has_motion_sync(prefab: dict) -> bool:
    """The exported prefab (live2d.extract_runtime) has a MotionSync controller with its CRI audio input on its root
    node; a model without one takes its mouth in a story from the voice's CRI Lips analysis."""
    classes = {c.get("class") or c.get("type") for n in prefab["nodes"] if "/" not in n["path"]
               for c in n["components"]}
    return all(k in classes for k in MOTION_SYNC_COMPONENTS)


def drawable_textures(prefab: dict) -> list[str]:
    """The atlas pages the drawables draw with (every distinct CubismRenderer._mainTexture), sorted."""
    return sorted({c["_mainTexture"]["texture"] for n in prefab["nodes"] for c in n["components"]
                   if c.get("class") == "CubismRenderer" and c.get("_mainTexture")})


def shader_files(index: list, materials: list[dict], extra: tuple[str, ...] = ()) -> list[str]:
    """The files of a shader directory (paths relative to it) that `materials` draw with: per shader its parsed file
    and, for each distinct keyword set of its materials, the GLES3 program of subshader 0 pass 0 whose keywords are
    exactly the material's keywords among those the shader's programs there use; with `extra`, also the program of
    the set plus `extra` (the same program when the shader's programs use none of them). A global keyword such as
    _ADDITIONAL_LIGHTS is never in a material's set, so its variants are picked only through `extra`."""
    recs = {r["name"]: r for r in index}
    sets = {(m["shader"]["shader"], tuple(m["keywords"])) for m in materials}
    if extra:
        sets |= {(name, (*kws, *extra)) for name, kws in sets}
    files = set()
    for name, kws in sorted(sets):
        if name not in recs:
            raise RuntimeError(f"shader {name}: not in the shader index")
        variants = [v for v in recs[name]["variants"] if v["platform"] == SHADER_PLATFORM and v["type"] == SHADER_TYPE
                    and v["subShader"] == 0 and v["pass"] == 0]
        want = set(kws) & {k for v in variants for k in v["keywords"]}
        hits = [v for v in variants if set(v["keywords"]) == want]
        if len(hits) != 1:
            raise RuntimeError(f"shader {name}: {len(hits)} GLES3 programs with the keywords {sorted(want)}")
        files |= {recs[name]["parsed"], hits[0]["file"]}
    return sorted(files)


def read_files(doc: dict, prefab: dict, index: list) -> list[str]:
    """The files of a model the player reads (model.json `doc`, its prefab, its shader index): model.json, the moc3,
    the prefab, the drawables' atlas pages, the shader index and the shader files of the drawables' materials (each
    keyword set alone and with STORY_KEYWORDS), plus those of the mask materials when a drawable is masked
    (MASK_KEYWORD)."""
    materials = drawable_materials(prefab)
    files = set(shader_files(index, materials, STORY_KEYWORDS))
    if any(MASK_KEYWORD in m["keywords"] for m in materials):
        files |= set(shader_files(index, list(doc["resources"].values())))
    sdir = doc["shaders"].rpartition("/")[0]
    return sorted({MODEL_INDEX, doc["moc3"], doc["prefab"], *doc["textures"], doc["shaders"],
                   *(f"{sdir}/{f}" if sdir else f for f in files)})


def export_model(cat, player, key: str, out_dir: Path) -> dict:
    """The files of one model (module docstring) into `out_dir`; returns the runtime export summary with `canvas`,
    `shaders` (names) and `files` (read_files: the paths to store)."""
    out_dir = Path(out_dir)
    rt = live2d.export_runtime(cat, key, out_dir)
    res, ex = rt.summary, rt.exporter
    prefab = json.loads((out_dir / res["prefab"]).read_text(encoding="utf-8"))
    rex = Exporter(cat, out_dir, player=player)
    resources = {name: rex.material(player.resource(path)) for name, path in RESOURCES.items()}
    objs = {**rex.shaders, **ex.shaders}
    names = sorted(shader_names(prefab) | shader_names(resources))
    missing = [n for n in names if n not in objs]
    if missing:
        raise RuntimeError(f"{key}: shaders neither in the model's bundles nor in the player data: {missing}")
    index: list = []
    for n in names:
        shader_mod.dump_objects([objs[n]], out_dir / SHADER_DIR, objs[n].assets_file.name, index)
    shader_mod.write_index(index, out_dir / SHADER_DIR)
    doc = {"format": MODEL_FORMAT, "name": res["name"], "key": key, "moc3": res["moc3"], "prefab": res["prefab"],
           "textures": drawable_textures(prefab), "canvas": prefab["canvas"], "shaders": f"{SHADER_DIR}/shaders.json",
           "resources": resources, "motionSync": has_motion_sync(prefab)}
    jsonio.write_json(out_dir / MODEL_INDEX, doc)
    return {**res, "textures": doc["textures"], "canvas": prefab["canvas"], "shaders": names,
            "files": read_files(doc, prefab, index)}


def model_facts(key: str, summary: dict, names: dict | None = None) -> dict:
    """What a model listing needs, from the key and the export summary, plus the NAME_FIELDS of `names` (an entry
    of model_names)."""
    facts = {"group": MODEL_KEY.fullmatch(key)["group"], "canvas": summary["canvas"],
             "textures": len(summary["textures"]), "nodes": summary["nodes"]}
    facts.update({k: names[k] for k in NAME_FIELDS if names and k in names})
    return facts


def ingest(store: Store, site: Path, mid: str, key: str, model_dir: Path, summary: dict,
           names: dict | None = None) -> dict:
    """One exported model's files (summary["files"]) into the store + its manifest (`names`: model_facts)."""
    text, binary = collect(Path(model_dir), summary["files"])
    entries = {p: store.put_file(p, text_asset(p, s)) for p, s in text.items()}
    entries.update({p: store.put(p, b) for p, b in binary.items()})
    manifest = {"format": MANIFEST_FORMAT, "id": mid, "key": key, "model": model_facts(key, summary, names),
                "files": dict(sorted(entries.items()))}
    (Path(site) / MODELS_DIR / f"{mid}.json").write_bytes(_dump(manifest))
    return {"id": mid, "ok": True, "files": len(entries), "bytes": sum(e["size"] for e in entries.values())}


# ---------------------------------------------------------------- model sources of story.build
def source_entry(address: str, doc: dict, where: str) -> dict:
    """ensure's answer from the model.json `doc` of the model `address` (read from `where`)."""
    if doc.get("key") != address:
        raise RuntimeError(f"{where}: model.json of {doc.get('key')}, not of {address}")
    if not isinstance(doc.get("motionSync"), bool):
        raise RuntimeError(f"{where}: model.json of format {doc.get('format')} has no motionSync; export the model "
                           f"again (--force)")
    return {"id": model_id(address), "motionSync": doc["motionSync"]}


class ModelDir:
    """The models of `root`: each model in <root>/<id>/, the files of a model manifest (read_files; the shader index
    reduced to the listed GLES3 programs as web.collect reduces it) in the layout export_model writes. ensure exports
    a model whose directory does not exist, or every model once when `force`; `built` / `skipped`: the ids (a model
    another process installed meanwhile is skipped).

    Processes may share `root` (several `nnnotes story` at once): a model directory appears in one rename, whole, and
    the first process to install a model wins; the others drop their export (the same bytes: exports are
    deterministic) and use it. `force` moves a previous directory aside before installing; a process that finds the
    directory gone in between exports the model itself."""

    def __init__(self, cat, player, root: Path, force: bool = False):
        self.cat, self.player, self.root, self.force = cat, player, Path(root), force
        self.built: list[str] = []
        self.skipped: list[str] = []
        self._done: dict[str, dict] = {}

    def ensure(self, address: str) -> dict:
        mid = model_id(address)
        if mid not in self._done:
            d = self.root / mid
            index = d / MODEL_INDEX
            built = (self.force or not d.exists()) and self._export(address, d)
            if not index.is_file() and not d.exists():     # moved aside by a forced export of another process
                built = self._export(address, d)
            (self.built if built else self.skipped).append(mid)
            try:
                doc = json.loads(index.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise RuntimeError(f"{d}: no {MODEL_INDEX} (not a model directory); remove it or export again "
                                   f"(--force)") from None
            self._done[mid] = source_entry(address, doc, str(index))
        return self._done[mid]

    def _export(self, address: str, d: Path) -> bool:
        """The model into a temporary directory in `root`, then renamed to `d`; False when another process's `d` is
        there instead (kept: without `force` any `d`, with `force` one installed after the previous `d` was moved
        aside)."""
        self.root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f".{d.name}-", dir=self.root))
        try:
            summary = export_model(self.cat, self.player, address, work / "export")
            text, binary = collect(work / "export", summary["files"])
            files = {**{p: t.encode("utf-8") for p, t in text.items()}, **binary}
            model = work / "model"
            for rel, data in sorted(files.items()):
                (model / rel).parent.mkdir(parents=True, exist_ok=True)
                (model / rel).write_bytes(data)
            if _install(model, d):
                return True
            if not self.force:
                return False
            try:
                d.rename(work / "previous")
            except FileNotFoundError:                     # moved aside by another process
                pass
            return _install(model, d)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def story_fields(self, story_dir: Path) -> dict:
        """`modelsDir`: the relative path from the story directory to `root` (posix)."""
        return {"modelsDir": Path(os.path.relpath(os.path.abspath(self.root), os.path.abspath(story_dir))).as_posix()}


def _install(src: Path, d: Path) -> bool:
    """Rename the directory `src` to `d`; False when `d` exists (a non-empty directory: POSIX refuses to replace it,
    Windows any directory)."""
    try:
        src.rename(d)
        return True
    except OSError:
        if not d.exists():
            raise
        return False


class SiteModels:
    """The models of the site at `site`: ensure reads model.json from the model manifest models/<id>.json (the models
    are built before the stories: storysite.build)."""

    def __init__(self, site: Path):
        self.site = Path(site)

    def ensure(self, address: str) -> dict:
        where = f"{MODELS_DIR}/{model_id(address)}.json"
        p = self.site / where
        if not p.is_file():
            raise RuntimeError(f"{address}: no model manifest {where} in the site")
        man = json.loads(p.read_text(encoding="utf-8"))
        return source_entry(address, json.loads(entry_text(self.site, man["files"][MODEL_INDEX])), where)

    def story_fields(self, story_dir: Path) -> dict:
        return {}


def outdated(site: Path, mid: str) -> str | None:
    """Why the existing model manifest of `mid` is built again: "outdated" (its model.json has a format older than
    MODEL_FORMAT or no motionSync), "unreadable" (the manifest or its model.json cannot be read); None when it is
    current."""
    try:
        man = json.loads((Path(site) / MODELS_DIR / f"{mid}.json").read_text(encoding="utf-8"))
        doc = json.loads(entry_text(Path(site), man["files"][MODEL_INDEX]))
        fmt, ms = doc.get("format"), doc.get("motionSync")
    except Exception:
        return "unreadable"
    if not isinstance(fmt, int) or fmt < MODEL_FORMAT or not isinstance(ms, bool):
        return "outdated"
    return None


# ---------------------------------------------------------------- index
def write_models_index(site: Path) -> tuple[int, set[str]]:
    """site/models.json from every model manifest present (no models directory: no models.json); returns the number
    of models and the assets their manifests reference."""
    site = Path(site)
    mdir = site / MODELS_DIR
    if not mdir.is_dir():
        (site / MODELS_INDEX).unlink(missing_ok=True)
        return 0, set()
    models, used = [], set()
    for p in sorted(mdir.glob("*.json")):
        man = json.loads(p.read_text(encoding="utf-8"))
        for e in man["files"].values():
            used.update(entry_assets(e))
        models.append({"id": p.stem, "manifest": f"{MODELS_DIR}/{p.name}", "key": man["key"],
                       "files": len(man["files"]),
                       "bytes": sum(f["size"] for f in man["files"].values()), **man["model"]})
    (site / MODELS_INDEX).write_bytes(_dump({"format": SITE_FORMAT, "models": models}))
    return len(models), used


# ---------------------------------------------------------------- per model (in a worker or in this process)
_W: dict = {}


def _open(cfg: Config, region: str | None = None):
    from .cli import open_catalog, player_data
    return open_catalog(cfg, region=region), player_data(cfg, region)


def _worker_init(cfg: Config, lock, job: dict) -> None:
    use(cfg)
    cat, player = _open(cfg, job.get("region"))
    _lock_fetches(cat, lock)
    _W.update(cat=cat, player=player, job=job)


def model_task(mid: str, key: str, job: dict | None = None, data=None) -> dict:
    """One model: export into a temporary directory, ingest; the directory is deleted."""
    cat, player = data or (_W["cat"], _W["player"])
    job = job or _W["job"]
    site = Path(job["site"])
    work = Path(tempfile.mkdtemp(prefix=f"{mid}-", dir=job["tmp"]))
    t0, stage = time.time(), "export"
    try:
        summary = export_model(cat, player, key, work)
        stage = "ingest"
        r = ingest(site_store(site, job["encoding"]), site, mid, key, work, summary,
                   (job.get("names") or {}).get(key))
        return {**r, "seconds": round(time.time() - t0, 1)}
    except Exception as e:
        cause = f"{type(e).__name__}: {e}"
        _log(f"{mid}: {stage} failed: {cause[:300]}")
        return {"id": mid, "ok": False, "stage": stage, "error": cause[:2000], "trace": traceback.format_exc()[-3000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _pool_task(args):
    return model_task(*args)


# ---------------------------------------------------------------- build
def build(out_dir, models: dict[str, str] | None, cfg: Config, player_dir, force: bool = False, *, tmp_dir=None,
          log=None, workers: int | None = None, region: str | None = None, encoding: str = "gzip") -> dict:
    """Add the Live2D models `models` ({id: key}, see `catalog_models`; None: every model of the catalog) to the site
    at `out_dir`, with the player of the ournotes-player checkout or package at `player_dir` (its page files are
    written, models.json and charts.json rebuilt). The data comes from the settings `cfg` (each worker process opens
    its own), bundles from the CDN of `region` (default: [catalog] region), names from the master data of `region`
    when it is configured (master_names). `workers`: parallel model processes (default up to 4); `encoding`: the
    stored encoding of the assets (web.site_store). Without `force`, an existing manifest that is not current
    (`outdated`) is built again and listed in modelsRebuilt with the reason."""
    player_dir = check_player(player_dir)
    site = Path(out_dir).resolve()
    (site / MODELS_DIR).mkdir(parents=True, exist_ok=True)
    site_store(site, encoding)
    tmp_root = Path(tmp_dir).resolve() if tmp_dir else site.parent / f"{site.name}.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log = log or _log
    t0 = time.time()
    if models is None:
        from .cli import open_catalog
        models = catalog_models(open_catalog(cfg, bundles=False, region=region))
    todo, skipped, rebuilt = [], [], []
    for mid, key in sorted(models.items()):
        if model_id(key) != mid:
            raise ValueError(f"model {mid}: key {key} has the id {model_id(key)}")
        if (site / MODELS_DIR / f"{mid}.json").exists() and not force:
            why = outdated(site, mid)
            if why is None:
                skipped.append(mid)
                continue
            rebuilt.append({"id": mid, "reason": why})
        todo.append((mid, key))
    if workers is None:
        workers = max(1, min(4, len(todo), usable_cpus() // 2))
    names = master_names(cfg, region, log)
    if names is not None:
        for mid in skipped:
            refresh_names(site, mid, names.get(models[mid]))
    job = {"site": str(site), "tmp": str(tmp_root), "region": region, "names": names or {}, "encoding": encoding}
    results = []
    if todo:
        log(f"{len(todo)} models, {workers} worker(s)")
        if workers <= 1:
            data = _open(cfg) if region is None else _open(cfg, region)
            for mid, key in todo:
                results.append(model_task(mid, key, job, data=data))
        else:
            ctx = mp.get_context("spawn")
            with ctx.Manager() as mgr:
                lock = mgr.Lock()
                with ctx.Pool(workers, initializer=_worker_init, initargs=(cfg, lock, job)) as pool:
                    for r in pool.imap_unordered(_pool_task, [(mid, key) for mid, key in todo]):
                        results.append(r)
                        if len(results) % 20 == 0:
                            log(f"{len(results)}/{len(todo)} models")
    v = write_player(site, player_dir)
    idx = write_index(site)
    results.sort(key=lambda r: r["id"])
    failed = [r for r in results if not r["ok"]]
    if failed:
        (site.parent / f"{site.name}.model-failures.json").write_bytes(_dump(failed))
    return {"site": str(site),
            "modelsBuilt": [{k: r[k] for k in ("id", "files", "bytes")} for r in results if r["ok"]],
            "modelsFailed": [{k: r.get(k) for k in ("id", "stage", "error")} for r in failed],
            "modelsSkipped": skipped, "modelsRebuilt": rebuilt, "modelSeconds": round(time.time() - t0, 1),
            "modelWorkers": workers,
            "modelNames": None if names is None else sum(1 for k in models.values() if k in names),
            **v, **idx}
