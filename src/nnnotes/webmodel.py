"""Live2D models -> the model part of an ournotes-player site (web.py builds the charts of the same site).

    <site>/models.json             model index: id, manifest path, key, listing facts, sizes
    <site>/models/<id>.json        model manifest: every path of the model's files -> {asset, size}, or, for a large
                                   JSON object, {parts: [[key, asset, size], ...], size} (as a chart manifest)
    <site>/assets/<sha256>.<ext>   content-addressed files, shared with the charts (the models share their shaders)

The files of one model (the paths in its manifest), only those the player reads (read_files):

    model.json                     index: format (MODEL_FORMAT), name, key, moc3, prefab, textures, canvas, shaders,
                                   resources
    <name>.moc3                    the moc3 (as live2d.extract_runtime writes it)
    <name>.prefab.json             the whole prefab, clips, expressions and controllers inlined (the same)
    textures/*.png                 the atlas pages the drawables draw with (the same)
    shaders/shaders.json           the shader index (shader.py layout, filtered as in the chart sites: web.collect),
    shaders/<shader>.json          the parsed forms and the GLES3 programs (GLSL ES 3.00) the drawables' materials
    shaders/<shader>/gles3/*.glsl  select by their keywords; the mask shader's only when a drawable is masked

`resources` in model.json: the materials the Cubism mask pass draws with (the Resources.Load paths of
advscene.RESOURCES, read from the APK's boot data), exported as the story scene exports them.

Models are the catalog keys Character/Live2D/<group>/<name>/model/<name>; a model's id is <name> (unique in the
catalog, URL-safe). Each model is exported into a temporary directory, stored, and the directory deleted; models run in
parallel worker processes (catalog cache writes serialized by a lock). A model whose manifest exists is skipped unless
`force`. Same inputs give byte-identical outputs. A model reads no master data and the regions serve the same catalog,
so one build serves every region of a site (the bundles are fetched from the CDN of `region`).
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

from . import advscene, jsonio, live2d
from . import shader as shader_mod
from .config import Config, use
from .export import Exporter
from .web import (MODELS_DIR, MODELS_INDEX, SHADER_PLATFORM, SHADER_TYPE, SITE_FORMAT, Store, _dump, _lock_fetches,
                  _log, check_player, collect, entry_assets, text_asset, write_index, write_player)

LIVE2D_PREFIX = "Character/Live2D/"
MODEL_KEY = re.compile(r"Character/Live2D/(?P<group>[^/]+)/(?P<name>[^/]+)/model/(?P=name)")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
MODEL_INDEX = "model.json"
MODEL_FORMAT = 1                                   # model.json "format"
SHADER_DIR = "shaders"
MASK_KEYWORD = "CUBISM_MASK_ON"                    # a drawable material's keyword: the drawable is masked


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


def drawable_textures(prefab: dict) -> list[str]:
    """The atlas pages the drawables draw with (every distinct CubismRenderer._mainTexture), sorted."""
    return sorted({c["_mainTexture"]["texture"] for n in prefab["nodes"] for c in n["components"]
                   if c.get("class") == "CubismRenderer" and c.get("_mainTexture")})


def shader_files(index: list, materials: list[dict]) -> list[str]:
    """The files of a shader directory (paths relative to it) that `materials` draw with: per shader its parsed file
    and, for each distinct keyword set of its materials, the GLES3 program of subshader 0 pass 0 whose keywords are
    exactly the material's keywords among those the shader's programs there use (a global keyword such as
    _ADDITIONAL_LIGHTS is never in a material's set, so its variants are never picked)."""
    recs = {r["name"]: r for r in index}
    files = set()
    for name, kws in sorted({(m["shader"]["shader"], tuple(m["keywords"])) for m in materials}):
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
    the prefab, the drawables' atlas pages, the shader index and the shader files of the drawables' materials, plus
    those of the mask materials when a drawable is masked (MASK_KEYWORD)."""
    materials = drawable_materials(prefab)
    if any(MASK_KEYWORD in m["keywords"] for m in materials):
        materials += list(doc["resources"].values())
    sdir = doc["shaders"].rpartition("/")[0]
    return sorted({MODEL_INDEX, doc["moc3"], doc["prefab"], *doc["textures"], doc["shaders"],
                   *(f"{sdir}/{f}" if sdir else f for f in shader_files(index, materials))})


def export_model(cat, player, key: str, out_dir: Path) -> dict:
    """The files of one model (module docstring) into `out_dir`; returns the runtime export summary with `canvas`,
    `shaders` (names) and `files` (read_files: the paths to store)."""
    out_dir = Path(out_dir)
    res, ex, *_ = live2d._extract_runtime(cat, key, out_dir)
    prefab = json.loads((out_dir / res["prefab"]).read_text(encoding="utf-8"))
    rex = Exporter(cat, out_dir, player=player)
    resources = {name: rex.material(player.resource(path)) for name, path in advscene.RESOURCES.items()}
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
           "resources": resources}
    jsonio.write_json(out_dir / MODEL_INDEX, doc)
    return {**res, "textures": doc["textures"], "canvas": prefab["canvas"], "shaders": names,
            "files": read_files(doc, prefab, index)}


def model_facts(key: str, summary: dict) -> dict:
    """What a model listing needs, from the key and the export summary."""
    return {"group": MODEL_KEY.fullmatch(key)["group"], "canvas": summary["canvas"],
            "textures": len(summary["textures"]), "nodes": summary["nodes"]}


def ingest(store: Store, site: Path, mid: str, key: str, model_dir: Path, summary: dict) -> dict:
    """One exported model's files (summary["files"]) into the store + its manifest."""
    text, binary = collect(Path(model_dir), summary["files"])
    entries = {p: store.put_file(p, text_asset(p, s)) for p, s in text.items()}
    entries.update({p: store.put(p, b) for p, b in binary.items()})
    manifest = {"format": SITE_FORMAT, "id": mid, "key": key, "model": model_facts(key, summary),
                "files": dict(sorted(entries.items()))}
    (Path(site) / MODELS_DIR / f"{mid}.json").write_bytes(_dump(manifest))
    return {"id": mid, "ok": True, "files": len(entries), "bytes": sum(e["size"] for e in entries.values())}


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
    return open_catalog(cfg, region=region), player_data(cfg)


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
        r = ingest(Store(site), site, mid, key, work, summary)
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
          log=None, workers: int | None = None, region: str | None = None) -> dict:
    """Add the Live2D models `models` ({id: key}, see `catalog_models`; None: every model of the catalog) to the site
    at `out_dir`, with the player of the ournotes-player checkout or package at `player_dir` (its page files are
    written, models.json and charts.json rebuilt). The data comes from the settings `cfg` (each worker process opens
    its own), bundles from the CDN of `region` (default: [catalog] region). `workers`: parallel model processes
    (default up to 4)."""
    player_dir = check_player(player_dir)
    site = Path(out_dir).resolve()
    (site / MODELS_DIR).mkdir(parents=True, exist_ok=True)
    Store(site)
    tmp_root = Path(tmp_dir).resolve() if tmp_dir else site.parent / f"{site.name}.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log = log or _log
    t0 = time.time()
    if models is None:
        from .cli import open_catalog
        models = catalog_models(open_catalog(cfg, bundles=False, region=region))
    todo, skipped = [], []
    for mid, key in sorted(models.items()):
        if model_id(key) != mid:
            raise ValueError(f"model {mid}: key {key} has the id {model_id(key)}")
        if (site / MODELS_DIR / f"{mid}.json").exists() and not force:
            skipped.append(mid)
        else:
            todo.append((mid, key))
    if workers is None:
        workers = max(1, min(4, len(todo), (os.cpu_count() or 2) // 2))
    job = {"site": str(site), "tmp": str(tmp_root), "region": region}
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
            "modelsSkipped": skipped, "modelSeconds": round(time.time() - t0, 1), "modelWorkers": workers,
            **v, **idx}
