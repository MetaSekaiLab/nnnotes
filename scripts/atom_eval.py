#!/usr/bin/env python3
"""Measure candidate implementations of the export atoms against the reference implementations (maintainer tool;
not installed, not needed to use nnnotes).

`nnnotes.atoms.ATOMS` holds one reference implementation per atom; `NNNOTES_ATOM_<NAME>=module:attribute` names
another one for evaluation (docs/stages.md, "Atoms"). This script gives the numbers a switch is decided on:

    sample   DIR -o FILE                  a seeded stratified sample of a bundle directory: one bundle of every
                                          group (the Addressables group `export --select group:` names), the largest
                                          bundles, random bundles
    m1       DIR --atom NAME=SPEC ...     conformance: every object of the checked classes through the reference
                                          and through each candidate; per check and class (or texture format):
                                          equal / differ / refused / ...
    prepare  DIR --base STORE             census and link.scripts of the bundles into a base store (the tasks of
                                          `m3` are described from it)
    m3       DIR --base STORE --store S   cost of a stage per bundle: wall, user + system CPU and peak resident
                                          memory per task, in a worker pool (per-worker peak) or with --fresh in a
                                          new process per bundle (imports measured apart)
    imports  [--module M ...]             import cost of modules, each set in new processes
    compare  RUN_A.json RUN_B.json        the results of two `m3` runs, artifact by artifact (determinism, or a
                                          candidate against the reference)

DIR holds bundle files (decrypted UnityFS). `--bundles FILE` limits a command to the bundles a `sample` file (or a
JSON list, or a text file with one name per line) names; names are paths relative to DIR.

Candidates (`--atom NAME=module:attribute`, repeatable; several candidates of one atom are measured side by side in
`m1`) are `Impl` objects as NNNOTES_ATOM_<NAME> takes them. Modules next to this script are importable. `m1`
compares, per check:

    objects    (reader) files, objects (class id, byte size, type hash), raw object bytes, container, externals
    census     (reader) the document of stage unity.census: the reference's against the candidate view's census()
    typetree   (reader) every object's typetree: read / refused, and the values when the candidate view says
               `typetree_form = "reference"`
    text       (reader) TextAsset and Font bytes (the candidate view's text_bytes / font_bytes, else its typetree)
    texture    (texture.decode) the image of every Texture2D, per texture format; HDR ASTC also against an
               independent decoder (astcenc through astc-encoder-py, HDR profile, channels clamped to [0, 1] and
               rounded to 8 bits, top row first)
    png        (png.encode) lossless round trip of every texture image, byte equality with the reference, size
    mesh       (mesh.arrays) every channel (stored type and values), primitives, skin, bind poses
    sprite     (reader) the candidate view's sprite_image() against UnityPy's SpriteHelper image
    det        (texture.decode, png.encode) the candidate's bytes on two runs and from --threads threads

A candidate view is the reader candidate's result; the optional methods census(), text_bytes(obj), font_bytes(obj),
sprite_image(obj) and the attribute typetree_form serve the checks that have no atom of their own. A candidate
raising NotImplementedError has no such facet ("absent"); `nnnotes.atoms.Unsupported` is a refusal.

Statuses: equal, differ, refused (the candidate raised where the reference gave a result), refused.both, extra (a
result where the reference refused), absent, reference-error. Output: one JSON document (-o); m1 and m3 also write
one JSON line per bundle next to it (<out>.jsonl).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

try:
    import resource
except ImportError:                             # not POSIX: CPU from os.times, no peak memory
    resource = None

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

EXAMPLES = 5
CHECKS = ("objects", "census", "typetree", "text", "texture", "png", "mesh", "sprite", "det")
PREPARED = "atom_eval.prepare.json"
G: dict = {}


# ---------------------------------------------------------------- small helpers
def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def message(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def status_kb(key: str) -> int:
    try:
        with open("/proc/self/status") as f:
            for ln in f:
                if ln.startswith(key + ":"):
                    return int(ln.split()[1])
    except OSError:
        pass
    return 0


def reset_hwm() -> bool:
    """Reset the process' peak resident size (Linux clear_refs 5); False where that is not possible."""
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
        return True
    except OSError:
        return False


def cpu_now() -> tuple[float, float]:
    if resource is None:
        t = os.times()
        return t.user, t.system
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime, r.ru_stime


def check_clean_env() -> None:
    set_ = sorted(k for k in os.environ if k.startswith("NNNOTES_ATOM_"))
    if set_:
        raise SystemExit(f"unset {', '.join(set_)}: the reference must be the registry's (use --atom)")


def load_impl(spec: str):
    """The Impl named by module:attribute (an Impl, or a callable returning one)."""
    from nnnotes.atoms import Impl
    module, sep, attr = spec.partition(":")
    if not sep or not module or not attr:
        raise SystemExit(f"{spec!r}: expected module:attribute")
    obj = getattr(importlib.import_module(module), attr)
    impl = obj if isinstance(obj, Impl) else obj()
    if not isinstance(impl, Impl):
        raise SystemExit(f"{spec!r}: not an Impl")
    return impl


def parse_atoms(items) -> list[tuple[str, str]]:
    from nnnotes.atoms import ATOMS
    out = []
    for it in items or ():
        name, sep, spec = it.partition("=")
        if not sep or name not in ATOMS:
            raise SystemExit(f"--atom {it!r}: expected NAME=module:attribute with NAME one of {', '.join(ATOMS)}")
        out.append((name, spec))
    return out


def parse_params(items) -> dict:
    """--param a.b=value (value read as JSON when it parses) -> {"a": {"b": value}}."""
    out: dict = {}
    for it in items or ():
        path, sep, value = it.partition("=")
        if not sep:
            raise SystemExit(f"--param {it!r}: expected path=value")
        try:
            v = json.loads(value)
        except ValueError:
            v = value
        d = out
        parts = path.split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = v
    return out


def bundle_list(root: Path, listing: str | None) -> list[tuple[str, int]]:
    """[(name relative to root, size)] of the listed bundles (all files of root without a listing), sorted."""
    if listing:
        text = Path(listing).read_text(encoding="utf-8")
        try:
            doc = json.loads(text)
            names = doc["bundles"] if isinstance(doc, dict) else doc
        except ValueError:
            names = [ln.strip() for ln in text.splitlines() if ln.strip()]
    else:
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return [(n, (root / n).stat().st_size) for n in sorted(set(names))]


def write_json(path, doc) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8",
                 newline="\n")


def pool_map(fn, jobs, workers: int, init=None, initargs=(), maxtasks: int | None = 40, on_result=None):
    """fn over jobs in `workers` forked processes (in this process when workers <= 1), in completion order."""
    out = []
    if workers <= 1:
        if init:
            init(*initargs)
        for j in jobs:
            r = fn(j)
            out.append(r)
            if on_result:
                on_result(r)
        return out
    ctx = get_context("fork")
    with ctx.Pool(workers, initializer=init, initargs=initargs, maxtasksperchild=maxtasks) as p:
        for i, r in enumerate(p.imap_unordered(fn, jobs, 1)):
            out.append(r)
            if on_result:
                on_result(r)
            if (i + 1) % 500 == 0:
                log("  ", i + 1, "/", len(jobs))
    return out


# ---------------------------------------------------------------- comparisons (pure)
def image_diff(a, b) -> tuple[str, str | None]:
    """("equal", None) when mode, size and pixels are equal, else ("differ", what)."""
    import numpy as np
    if a.mode != b.mode:
        return "differ", f"mode {a.mode} vs {b.mode}"
    if a.size != b.size:
        return "differ", f"size {a.size} vs {b.size}"
    x, y = a.tobytes(), b.tobytes()
    if x == y:
        return "equal", None
    d = np.abs(np.frombuffer(x, np.uint8).astype(np.int16) - np.frombuffer(y, np.uint8).astype(np.int16))
    return "differ", f"max {int(d.max())}, {int((d > 0).sum())} of {d.size} values, {int((d > 1).sum())} by more than 1"


def rgba_diff(a, b) -> dict:
    """Per-channel differences of two same-size RGBA images: max over RGB and A, share of RGB values off by > 1."""
    import numpy as np
    x = np.asarray(a.convert("RGBA"), np.int16)
    y = np.asarray(b.convert("RGBA"), np.int16)
    if x.shape != y.shape:
        return {"shape": [list(x.shape), list(y.shape)]}
    d = np.abs(x - y)
    rgb = d[..., :3]
    return {"rgbMax": int(rgb.max()), "alphaMax": int(d[..., 3].max()),
            "rgbOffBy2": round(float((rgb > 1).mean()), 6), "rgbOff": round(float((rgb > 0).mean()), 6)}


def png_roundtrip(data: bytes, want) -> tuple[str, str | None]:
    """Whether PNG `data` decodes to exactly `want` (mode and pixels)."""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return image_diff(im, want)


def _float_eq(a: float, b: float) -> bool:
    return a == b or (a != a and b != b)


def deep_diff(a, b, path: str = "") -> str | None:
    """The first path where two typetree values differ (None when equal). Lists and tuples compare by elements,
    floats by value (NaN equal to NaN), dicts by keys in order and values."""
    if isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict)):
            return f"{path}: {type(a).__name__} vs {type(b).__name__}"
        if list(a) != list(b):
            return f"{path}: keys {list(a)[:6]} vs {list(b)[:6]}"
        for k in a:
            d = deep_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = deep_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        return None if type(a) is type(b) and a == b else f"{path}: {a!r} vs {b!r}"[:200]
    if isinstance(a, float) and isinstance(b, float):
        return None if _float_eq(a, b) else f"{path}: {a!r} vs {b!r}"
    if isinstance(a, (bytes, bytearray)) and isinstance(b, (bytes, bytearray)):
        return None if bytes(a) == bytes(b) else f"{path}: bytes ({len(a)} vs {len(b)})"
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    return None if a == b else f"{path}: {a!r} vs {b!r}"[:200]


def _arr_eq(x, y) -> bool:
    import numpy as np
    x, y = np.asarray(x), np.asarray(y)
    if x.dtype != y.dtype or x.shape != y.shape:
        return False
    if x.dtype.kind == "f":
        return bool(np.array_equal(x, y, equal_nan=True))
    return bool(np.array_equal(x, y))


def mesh_diff(a, b) -> list[str]:
    """What differs between two MeshArrays (empty when equal): kinds like missing:<channel>, dtype:<channel>."""
    import numpy as np
    out = []
    if a.vertex_count != b.vertex_count:
        out.append("vertexCount")
    for name in list(a.channels) + [n for n in b.channels if n not in a.channels]:
        ca, cb = a.channels.get(name), b.channels.get(name)
        if cb is None:
            out.append(f"missing:{name}")
        elif ca is None:
            out.append(f"extra:{name}")
        elif ca.data.dtype != cb.data.dtype:
            out.append(f"dtype:{name}")
        elif ca.data.shape != cb.data.shape:
            out.append(f"shape:{name}")
        elif not _arr_eq(ca.data, cb.data):
            out.append(f"values:{name}")
        elif bool(ca.normalized) != bool(cb.normalized):
            out.append(f"normalized:{name}")
    if len(a.primitives) != len(b.primitives):
        out.append("primitives:count")
    else:
        for pa, pb in zip(a.primitives, b.primitives):
            if pa.topology != pb.topology:
                out.append("primitives:topology")
                break
            if (pa.indices is None) != (pb.indices is None) or (
                    pa.indices is not None and not np.array_equal(np.asarray(pa.indices), np.asarray(pb.indices))):
                out.append("primitives:indices")
                break
    if (a.skin is None) != (b.skin is None):
        out.append("skin:presence")
    elif a.skin is not None and not (_arr_eq(a.skin.joints, b.skin.joints)
                                     and _arr_eq(a.skin.weights.data, b.skin.weights.data)):
        out.append("skin:values")
    if not _arr_eq(a.bind_poses, b.bind_poses):
        out.append("bindPoses")
    if list(a.bone_name_hashes) != list(b.bone_name_hashes) or a.root_bone_name_hash != b.root_bone_name_hash:
        out.append("boneHashes")
    if [c for c, _ in a.issues] != [c for c, _ in b.issues]:
        out.append("issues")
    return out


# ---------------------------------------------------------------- tallies
class Tally:
    """counts[check][key][status], stats[check][key][name] (sums), examples[check][key][status] (first few)."""

    def __init__(self):
        self.counts = defaultdict(lambda: defaultdict(Counter))
        self.stats = defaultdict(lambda: defaultdict(Counter))
        self.examples = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    def add(self, check: str, key: str, status: str, oid: str | None = None, detail: str | None = None) -> None:
        self.counts[check][key][status] += 1
        if status != "equal" and oid is not None:
            ex = self.examples[check][key][status]
            if len(ex) < EXAMPLES:
                ex.append(f"{oid} {detail or ''}".strip()[:300])

    def stat(self, check: str, key: str, name: str, value) -> None:
        self.stats[check][key][name] += value

    def dump(self) -> dict:
        return {"counts": {c: {k: dict(v) for k, v in d.items()} for c, d in self.counts.items()},
                "stats": {c: {k: {n: (round(x, 6) if isinstance(x, float) else x) for n, x in v.items()}
                              for k, v in d.items()} for c, d in self.stats.items()},
                "examples": {c: {k: dict(v) for k, v in d.items()} for c, d in self.examples.items()}}


def merge(total: dict, rec: dict) -> None:
    for c, d in rec.get("counts", {}).items():
        for k, v in d.items():
            tgt = total["counts"].setdefault(c, {}).setdefault(k, {})
            for s, n in v.items():
                tgt[s] = tgt.get(s, 0) + n
    for c, d in rec.get("stats", {}).items():
        for k, v in d.items():
            tgt = total["stats"].setdefault(c, {}).setdefault(k, {})
            for s, n in v.items():
                if s.startswith("max:"):
                    tgt[s] = max(tgt.get(s, 0), n)
                else:
                    tgt[s] = round(tgt.get(s, 0) + n, 6)
    for c, d in rec.get("examples", {}).items():
        for k, v in d.items():
            for s, ex in v.items():
                lst = total["examples"].setdefault(c, {}).setdefault(k, {}).setdefault(s, [])
                for e in ex:
                    if len(lst) < EXAMPLES:
                        lst.append(f"{rec['bundle']}: {e}")


# ---------------------------------------------------------------- sample
def cmd_sample(a) -> dict:
    from nnnotes import contract
    from nnnotes.cli_assets import bundle_group
    root = Path(a.dir)
    items = bundle_list(root, a.bundles)
    base = {n: n.rsplit("/", 1)[-1] for n, _ in items}
    stable, collisions = contract.stable_bundle_names(base.values())
    groups: dict[str, list[str]] = defaultdict(list)
    for n, _ in items:
        groups[bundle_group(stable[base[n]])].append(n)
    rng = random.Random(a.seed)
    why: dict[str, list[str]] = defaultdict(list)
    for g in sorted(groups):
        why[rng.choice(sorted(groups[g]))].append("group")
    for n, _ in sorted(items, key=lambda x: (-x[1], x[0]))[:a.largest]:
        why[n].append("largest")
    rest = sorted(n for n, _ in items if n not in why)
    for n in rng.sample(rest, min(a.random, len(rest))):
        why[n].append("random")
    doc = {"seed": a.seed, "population": len(items), "groups": len(groups), "largest": a.largest,
           "random": a.random, "bundles": sorted(why), "why": {n: why[n] for n in sorted(why)},
           "bytes": sum(s for n, s in items if n in why)}
    if a.out:
        write_json(a.out, doc)
    log("sample", len(doc["bundles"]), "bundles of", len(items), "in", len(groups), "groups")
    return doc


# ---------------------------------------------------------------- m1
def m1_init(cfg: dict) -> None:
    from nnnotes.atoms import resolve
    G.clear()
    G.update(cfg)
    G["dir"] = Path(cfg["dir"])
    G["ref"] = {n: resolve(n) for n in ("reader", "texture.decode", "png.encode", "mesh.arrays")}
    if cfg.get("reference_reader"):
        G["ref"]["reader"] = load_impl(cfg["reference_reader"])
    cand = defaultdict(list)
    for name, spec in cfg["atoms"]:
        cand[name].append(load_impl(spec))
    G["cand"] = dict(cand)
    from PIL import Image                       # load the PNG codec before anything is timed
    tiny = Image.new("RGBA", (2, 2))
    for p in [G["ref"]["png.encode"], *G["cand"].get("png.encode", [])]:
        try:
            p.fn(tiny, 6)
        except Exception:                       # noqa: BLE001 (a candidate that needs its own images)
            pass


def _key(info) -> tuple:
    return (info.file, info.path_id)


def _call(fn, *args):
    """(result, None) or (None, refusal) where refusal is the Unsupported code or the error's message."""
    from nnnotes.atoms import Unsupported
    try:
        return fn(*args), None
    except Unsupported as e:
        return None, e.code
    except NotImplementedError as e:
        return None, "absent: " + str(e)[:100]
    except Exception as e:                      # noqa: BLE001
        return None, message(e)


def _facet(fn, *args):
    """(value, "absent" | error message | None)."""
    try:
        return fn(*args), None
    except NotImplementedError:
        return None, "absent"
    except Exception as e:                      # noqa: BLE001
        return None, message(e)


def check_objects(T: Tally, ref, cv, bundle: str) -> None:
    for facet, fr, fc in (("files", ref.files, cv.files), ("container", ref.container, cv.container)):
        a, _ = _facet(fr)
        b, err = _facet(fc)
        T.add("objects", facet, "absent" if err == "absent" else "refused" if err else
              "equal" if a == b else "differ", bundle, err)
    for f in ref.files():
        a, _ = _facet(ref.externals, f)
        b, err = _facet(cv.externals, f)
        T.add("objects", "externals", "absent" if err == "absent" else "refused" if err else
              "equal" if a == b else "differ", f"{bundle}:{f}", err)
        a, _ = _facet(ref.unity_version, f)
        b, err = _facet(cv.unity_version, f)
        T.add("objects", "unityVersion", "absent" if err == "absent" else "refused" if err else
              "equal" if a == b else "differ", f"{bundle}:{f}", err)
    rc = {_key(i): i for i in ref.objects()}
    cc = {_key(i): i for i in cv.objects()}
    T.add("objects", "set", "equal" if rc.keys() == cc.keys() else "differ", bundle,
          f"{len(rc.keys() - cc.keys())} only in the reference, {len(cc.keys() - rc.keys())} only in the candidate")
    for k, i in rc.items():
        c = cc.get(k)
        if c is None:
            T.add("objects", "classId", "absent", i.id)
            continue
        T.add("objects", "classId", "equal" if i.class_id == c.class_id else "differ", i.id)
        T.add("objects", "byteSize", "equal" if i.byte_size == c.byte_size else "differ", i.id)
        if i.type_hash is not None:
            T.add("objects", "typeHash", "absent" if c.type_hash is None else
                  "equal" if i.type_hash == c.type_hash else "differ", i.id)
        ra, _ = _facet(ref.raw, i)
        rb, err = _facet(cv.raw, c)
        T.add("objects", "raw", "absent" if err == "absent" else "refused" if err else
              "equal" if ra == rb else "differ", i.id, err)


def _census_facets(T: Tally, section: str, ra: dict, rb: dict | None, oid: str, skip=()) -> None:
    for f, v in ra.items():
        if f in skip:
            continue
        if rb is None or f not in rb:
            T.add("census", f"{section}.{f}", "absent", oid)
        else:
            T.add("census", f"{section}.{f}", "equal" if rb[f] == v else "differ", oid, f"{v!r} vs {rb[f]!r}"[:200])
    if rb:
        for f in rb:
            if f not in ra and f not in skip and rb[f] is not None:
                T.add("census", f"{section}.{f}", "differ", oid, "only in the candidate")


def check_census(T: Tally, ref, cv, bundle: str, data: bytes) -> None:
    from nnnotes import census
    from nnnotes.unity import load_bytes
    if not hasattr(cv, "census"):
        T.add("census", "document", "absent", bundle)
        return
    t0 = time.process_time()
    try:
        a = census.census_env(load_bytes(data))           # timed from the bytes: the open is part of the census
    except Exception as e:                      # noqa: BLE001
        T.add("census", "document", "reference-error", bundle, message(e))
        return
    T.stat("census", "document", "referenceCpu", time.process_time() - t0)
    t0 = time.process_time()
    b, err = _facet(lambda: G["cand"]["reader"][0].fn(data).census())
    T.stat("census", "document", "candidateCpu", time.process_time() - t0)
    if err:
        T.add("census", "document", "absent" if err == "absent" else "refused", bundle, err)
        return
    T.add("census", "document", "equal" if a == b else "differ", bundle)
    bf = {f["name"]: f for f in b.get("files", ())}
    for f in a["files"]:
        g = bf.get(f["name"])
        _census_facets(T, "file", f, g, f"{bundle}:{f['name']}", skip=("objects", "name"))
        go = {o["pathId"]: o for o in (g or {}).get("objects", ())}
        for o in f["objects"]:
            _census_facets(T, "object", o, go.get(o["pathId"]), f"{f['name']}:{o['pathId']}", skip=("pathId",))
    for sec, key in (("assetBundles", ("file", "pathId")), ("scripts", ("file", "pathId"))):
        bm = {tuple(x.get(k) for k in key): x for x in b.get(sec, ())}
        for x in a.get(sec, ()):
            _census_facets(T, sec, x, bm.get(tuple(x.get(k) for k in key)), f"{x['file']}:{x['pathId']}",
                           skip=key)
    for sec in ("resources", "classes", "objects", "errors"):
        if sec not in b:
            T.add("census", sec, "absent", bundle)
        else:
            T.add("census", sec, "equal" if a.get(sec) == b[sec] else "differ", bundle)


def check_typetree(T: Tally, ref, cv, infos, cmap) -> None:
    compare = getattr(cv, "typetree_form", "reference") == "reference"
    for i in infos:
        c = cmap.get(_key(i))
        if c is None:
            T.add("typetree", i.class_name, "absent", i.id)
            continue
        t0 = time.process_time()
        b, err = _call(cv.typetree, c)
        T.stat("typetree", i.class_name, "candidateCpu", time.process_time() - t0)
        t0 = time.process_time()
        a, rerr = _call(ref.typetree, i)
        T.stat("typetree", i.class_name, "referenceCpu", time.process_time() - t0)
        if not compare:
            T.add("typetree", i.class_name, "refused.both" if err and rerr else "extra" if rerr else
                  "refused" if err else "read", i.id, err or rerr)
            continue
        if rerr and err:
            T.add("typetree", i.class_name, "refused.both", i.id, err)
        elif rerr:
            T.add("typetree", i.class_name, "extra", i.id, rerr)
        elif err:
            T.add("typetree", i.class_name, "refused", i.id, err)
        else:
            d = deep_diff(a, b)
            T.add("typetree", i.class_name, "differ" if d else "equal", i.id, d)


def check_text(T: Tally, ref, cv, infos, cmap) -> None:
    for i in infos:
        if i.class_name not in ("TextAsset", "Font"):
            continue
        c = cmap.get(_key(i))
        if c is None:
            T.add("text", i.class_name, "absent", i.id)
            continue
        t0 = time.process_time()
        tt, rerr = _call(ref.typetree, i)
        T.stat("text", i.class_name, "referenceCpu", time.process_time() - t0)
        if rerr:
            T.add("text", i.class_name, "reference-error", i.id, rerr)
            continue
        if i.class_name == "TextAsset":
            s = tt.get("m_Script", "")
            want = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s or b"")
            fn = getattr(cv, "text_bytes", None)
        else:
            want = bytes(tt.get("m_FontData") or b"")
            fn = getattr(cv, "font_bytes", None)
        if fn is None:
            field = "m_Script" if i.class_name == "TextAsset" else "m_FontData"

            def fn(o, field=field):
                v = cv.typetree(o).get(field)
                return v.encode("utf-8", "surrogateescape") if isinstance(v, str) else bytes(v or b"")
        t0 = time.process_time()
        got, err = _call(fn, c)
        T.stat("text", i.class_name, "candidateCpu", time.process_time() - t0)
        T.stat("text", i.class_name, "bytes", len(want))
        T.add("text", i.class_name, "refused" if err else "equal" if got == want else "differ", i.id,
              err or (None if got == want else f"{len(want)} vs {len(got)} bytes"))


def hdr_reference(inp):
    """The independent image of an HDR ASTC texture: astcenc's HDR decode (astc-encoder-py), each channel clamped to
    [0, 1] and rounded to 8 bits, top row first. None when astc-encoder-py is not installed."""
    try:
        import astc_encoder as ae
    except ImportError:
        return None
    import numpy as np
    from PIL import Image
    from nnnotes.atoms.texture import ASTC_BLOCKS
    bx, by = ASTC_BLOCKS[int(inp.format)]
    w, h = int(inp.width), int(inp.height)
    n = -(-w // bx) * -(-h // by) * 16
    cfg = ae.ASTCConfig(ae.ASTCProfile.HDR, bx, by, block_z=1, quality=100)
    img = ae.ASTCImage(ae.ASTCType.F32, w, h, 1)
    ae.ASTCContext(cfg).decompress(bytes(inp.data[:n]), img, ae.ASTCSwizzle.from_str("RGBA"))
    f = np.frombuffer(img.data, np.float32).reshape(h, w, 4)
    u8 = np.floor(np.clip(np.nan_to_num(f, nan=0.0), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(u8[::-1].copy(), "RGBA")


def _hdr_library_image(inp):
    """What the reference's library returns for an HDR ASTC texture (the reference atom refuses it)."""
    from UnityPy.export.Texture2DConverter import parse_image_data
    return parse_image_data(inp.data, inp.width, inp.height, int(inp.format), tuple(inp.version), inp.platform,
                            inp.platform_blob, True)


def check_textures(T: Tally, ref, cv, infos, cmap, checks) -> None:
    from nnnotes.atoms.texture import format_name, is_hdr_astc
    ref_dec, ref_png = G["ref"]["texture.decode"], G["ref"]["png.encode"]
    decs, pngs = G["cand"].get("texture.decode", []), G["cand"].get("png.encode", [])
    det_inputs = []
    for i in infos:
        if i.class_name != "Texture2D":
            continue
        try:
            rin = ref.texture_input(i)
        except Exception as e:                  # noqa: BLE001
            T.add("texture", "?", "reference-error", i.id, message(e))
            continue
        fmt = format_name(rin.format)
        c = cmap.get(_key(i)) if cv is not None else None
        cin = rin
        if cv is not None and hasattr(cv, "texture_input") and c is not None:
            cin, err = _call(cv.texture_input, c)
            if err:
                T.add("texture", fmt, "refused", i.id, "texture_input: " + err)
                continue
        det_inputs.append(cin)
        t0 = time.process_time()
        rimg, rerr = _call(ref_dec.fn, rin)
        T.stat("texture", fmt, "referenceCpu", time.process_time() - t0)
        T.stat("texture", fmt, "pixels", int(rin.width) * int(rin.height))
        cimgs = []
        if decs and ("texture" in checks or "png" in checks):
            for d in decs:
                t0 = time.process_time()
                img, err = _call(d.fn, cin)
                T.stat("texture", fmt, f"cpu:{d.id}", time.process_time() - t0)
                cimgs.append(img)
                key = f"{fmt} | {d.id}"
                if rerr and err:
                    T.add("texture", key, "refused.both", i.id, f"{rerr} / {err}")
                elif rerr:
                    T.add("texture", key, "extra", i.id, rerr)
                elif err:
                    T.add("texture", key, "refused", i.id, err)
                else:
                    st, what = image_diff(rimg, img)
                    T.add("texture", key, st, i.id, what)
            if "texture" in checks and is_hdr_astc(rin.format) and rin.width and rin.height:
                indep = hdr_reference(rin)
                if indep is not None:
                    lib, lerr = _call(_hdr_library_image, rin)
                    if lib is not None:
                        rd = rgba_diff(lib, indep)
                        T.add("texture.hdr", f"{fmt} | reference library", "equal" if not any(
                            rd.get(k) for k in ("rgbMax", "alphaMax", "shape")) else "differ", i.id, json.dumps(rd))
                    for d, img in zip(decs, cimgs):
                        if img is None:
                            T.add("texture.hdr", f"{fmt} | {d.id}", "refused", i.id)
                            continue
                        rd = rgba_diff(img, indep)
                        T.add("texture.hdr", f"{fmt} | {d.id}", "equal" if not any(
                            rd.get(k) for k in ("rgbMax", "alphaMax", "shape")) else "differ", i.id, json.dumps(rd))
                        for k in ("rgbMax", "alphaMax"):
                            if k in rd:
                                T.stat("texture.hdr", f"{fmt} | {d.id}", f"max:{k}", rd[k])
        if "png" in checks and rimg is not None:
            src = next((x for x in cimgs if x is not None), None) if decs else rimg
            t0 = time.process_time()
            want = ref_png.fn(rimg, 6)
            T.stat("png", fmt, "cpu:reference", time.process_time() - t0)
            T.stat("png", fmt, "bytes:reference", len(want))
            T.stat("png", fmt, "rawBytes", len(rimg.tobytes()))
            if src is None:
                for p in pngs:
                    T.add("png", f"{fmt} | {p.id}", "refused", i.id, "no candidate image")
                continue
            for p in pngs:
                t0 = time.process_time()
                data, err = _call(p.fn, src, 6)
                T.stat("png", fmt, f"cpu:{p.id}", time.process_time() - t0)
                key = f"{fmt} | {p.id}"
                if err:
                    T.add("png", key, "refused", i.id, err)
                    continue
                T.stat("png", fmt, f"bytes:{p.id}", len(data))
                T.stat("png", key, "sameBytesAsReference", int(data == want))
                st, what = png_roundtrip(data, rimg)
                T.add("png", key, st, i.id, what)
    if "det" in checks and det_inputs and (decs or pngs):
        check_det(T, det_inputs)


def check_det(T: Tally, inputs) -> None:
    """The first texture.decode candidate (and the first png.encode candidate) on every input: two sequential runs
    and one from G["threads"] threads give the same bytes. Without a texture.decode candidate the reference decodes
    once, sequentially, and only the png.encode candidate runs twice and from threads."""
    decs = G["cand"].get("texture.decode") or []
    dec = decs[0] if decs else None
    png = (G["cand"].get("png.encode") or [None])[0]
    decoded = {}
    if dec is None:
        for n, inp in enumerate(inputs):
            decoded[n], _ = _call(G["ref"]["texture.decode"].fn, inp)

    def one(job):
        n, inp = job
        img, err = _call(dec.fn, inp) if dec is not None else (decoded[n], "reference refused")
        if img is None:
            return ("refused", err), None
        h = sha(img.mode.encode() + repr(img.size).encode() + img.tobytes())
        p = None
        if png is not None:
            data, perr = _call(png.fn, img, 6)
            p = sha(data) if data is not None else ("refused", perr)
        return h, p

    jobs = list(enumerate(inputs))
    r1 = [one(x) for x in jobs]
    r2 = [one(x) for x in jobs]
    with ThreadPoolExecutor(G.get("threads", 8)) as ex:
        r3 = list(ex.map(one, jobs))
    if dec is not None:
        T.add("det", dec.id, "equal" if [a for a, _ in r1] == [a for a, _ in r2] == [a for a, _ in r3] else "differ")
    if png is not None:
        T.add("det", png.id, "equal" if [b for _, b in r1] == [b for _, b in r2] == [b for _, b in r3] else "differ")


def check_mesh(T: Tally, ref, cv, infos, cmap) -> None:
    ref_arr = G["ref"]["mesh.arrays"]
    for i in infos:
        if i.class_name != "Mesh":
            continue
        rin = a = rerr = None
        try:
            rin = ref.mesh_input(i)
            t0 = time.process_time()
            a = ref_arr.fn(rin)
            T.stat("mesh", "Mesh", "referenceCpu", time.process_time() - t0)
        except Exception as e:                  # noqa: BLE001
            rerr = message(e)
        c = cmap.get(_key(i)) if cv is not None else None
        cin = rin
        if cv is not None and hasattr(cv, "mesh_input") and c is not None:
            cin, err = _call(cv.mesh_input, c)
            if err:
                T.add("mesh", "Mesh", "refused", i.id, "mesh_input: " + err)
                continue
        for m in G["cand"]["mesh.arrays"]:
            key = f"Mesh | {m.id}"
            if cin is None:
                T.add("mesh", key, "refused.both" if rerr else "refused", i.id, rerr)
                continue
            t0 = time.process_time()
            b, err = _call(m.fn, cin)
            T.stat("mesh", key, "cpu", time.process_time() - t0)
            if rerr and err:
                T.add("mesh", key, "refused.both", i.id, f"{rerr} / {err}")
            elif rerr:
                T.add("mesh", key, "extra", i.id, rerr)
            elif err:
                T.add("mesh", key, "refused", i.id, err)
            else:
                d = mesh_diff(a, b)
                T.add("mesh", key, "differ" if d else "equal", i.id, ", ".join(d[:6]))
                for kind in d:
                    T.stat("mesh", key, kind, 1)


def check_sprite(T: Tally, ref, cv, infos, cmap) -> None:
    from UnityPy.export.SpriteHelper import get_image_from_sprite
    for i in infos:
        if i.class_name != "Sprite":
            continue
        c = cmap.get(_key(i))
        try:
            want = get_image_from_sprite(ref.reader(i).read())
            want = want if want.mode == "RGBA" else want.convert("RGBA")
            rerr = None
        except Exception as e:                  # noqa: BLE001
            want, rerr = None, message(e)
        if c is None:
            T.add("sprite", "Sprite", "absent", i.id)
            continue
        got, err = _call(cv.sprite_image, c)
        if rerr and err:
            T.add("sprite", "Sprite", "refused.both", i.id, f"{rerr} / {err}")
        elif rerr:
            T.add("sprite", "Sprite", "extra", i.id, rerr)
        elif err:
            T.add("sprite", "Sprite", "refused", i.id, err)
        else:
            st, what = image_diff(want, got)
            T.add("sprite", "Sprite", st, i.id, what)


def m1_bundle(job) -> dict:
    name, size = job
    T = Tally()
    rec = {"bundle": name, "size": size}
    checks = G["checks"]
    t0, c0 = time.perf_counter(), time.process_time()
    try:
        data = (G["dir"] / name).read_bytes()
        ref = G["ref"]["reader"].fn(data)
        cr = (G["cand"].get("reader") or [None])[0]
        cv = cr.fn(data) if cr is not None else None
        infos = ref.objects()
        cmap = {_key(i): i for i in cv.objects()} if cv is not None else {}
        if cv is not None:
            if "objects" in checks:
                check_objects(T, ref, cv, name)
            if "census" in checks:
                check_census(T, ref, cv, name, data)
            if "typetree" in checks:
                check_typetree(T, ref, cv, infos, cmap)
            if "text" in checks:
                check_text(T, ref, cv, infos, cmap)
            if "sprite" in checks and hasattr(cv, "sprite_image"):
                check_sprite(T, ref, cv, infos, cmap)
        if {"texture", "png", "det"} & set(checks):
            check_textures(T, ref, cv, infos, cmap, checks)
        if "mesh" in checks and G["cand"].get("mesh.arrays"):
            check_mesh(T, ref, cv, infos, cmap)
    except Exception as e:                      # noqa: BLE001
        rec["error"] = message(e)
        rec["trace"] = traceback.format_exc()[-1200:]
    rec.update(T.dump())
    rec["seconds"] = round(time.perf_counter() - t0, 3)
    rec["cpu"] = round(time.process_time() - c0, 3)
    rec["rssKb"] = status_kb("VmHWM")
    return rec


def run_m1(cfg: dict, items, workers: int, jsonl: Path | None = None) -> dict:
    total = {"counts": {}, "stats": {}, "examples": {}, "errors": [], "bundles": 0, "seconds": 0.0,
             "maxRssKb": 0}
    fo = open(jsonl, "w", encoding="utf-8", newline="\n") if jsonl else None

    def take(r):
        total["bundles"] += 1
        total["seconds"] = round(total["seconds"] + r.get("seconds", 0), 3)
        total["maxRssKb"] = max(total["maxRssKb"], r.get("rssKb", 0))
        if r.get("error") and len(total["errors"]) < 50:
            total["errors"].append({"bundle": r["bundle"], "error": r["error"], "trace": r.get("trace")})
        merge(total, r)
        if fo:
            fo.write(json.dumps({k: r[k] for k in ("bundle", "size", "seconds", "cpu", "rssKb", "counts")
                                 if k in r} | ({"error": r["error"]} if r.get("error") else {}),
                                ensure_ascii=False) + "\n")

    order = sorted(items, key=lambda x: -x[1])
    try:
        pool_map(m1_bundle, order, workers, m1_init, (cfg,), maxtasks=20, on_result=take)
    finally:
        if fo:
            fo.close()
    return total


def cmd_m1(a) -> dict:
    check_clean_env()
    checks = tuple(a.checks.split(",")) if a.checks else CHECKS
    bad = [c for c in checks if c not in CHECKS]
    if bad:
        raise SystemExit(f"unknown checks {bad}")
    atoms = parse_atoms(a.atom)
    cfg = {"dir": a.dir, "atoms": atoms, "checks": checks, "threads": a.threads,
           "reference_reader": a.reference_reader}
    m1_init(cfg)                                # fail early on a bad spec; ids for the report
    ids = {n: [i.id for i in v] for n, v in G["cand"].items()}
    ref_ids = {n: i.id for n, i in G["ref"].items()}
    items = bundle_list(Path(a.dir), a.bundles)
    log("m1", len(items), "bundles, checks", ",".join(checks), "candidates", ids)
    t0 = time.perf_counter()
    total = run_m1(cfg, items, a.workers, Path(a.out).with_suffix(".jsonl") if a.out else None)
    doc = {"command": "m1", "checks": list(checks), "reference": ref_ids, "candidates": ids,
           "bundles": len(items), "wall": round(time.perf_counter() - t0, 1), "workers": a.workers, **total,
           "versions": versions()}
    if a.out:
        write_json(a.out, doc)
    return doc


def versions() -> dict:
    from importlib import metadata
    out = {"python": sys.version.split()[0]}
    for d in ("nnnotes", "UnityPy", "Pillow", "numpy", "texture2ddecoder", "astc-encoder-py", "unity-rs"):
        try:
            out[d] = metadata.version(d)
        except metadata.PackageNotFoundError:
            pass
    return out


# ---------------------------------------------------------------- prepare (base store)
def _identify(job):
    from nnnotes.store import Store
    root, store, name = job
    s, size = Store(store).identify(Path(root) / name)
    return name, s, size


def _census_task(task):
    from nnnotes import census
    from nnnotes.stages import execute
    ex = execute(task, G["store"], {"unity.census": census.CensusStage()})
    return task.subject, task.key, ex.status


def cmd_prepare(a) -> dict:
    from nnnotes import census, contract, link
    from nnnotes.contract import Input
    from nnnotes.stages import Env, describe, execute
    from nnnotes.store import Store
    check_clean_env()
    root, base = Path(a.dir).resolve(), Path(a.base)
    items = bundle_list(root, a.bundles)
    store = Store(base)
    t0 = time.perf_counter()
    ids = pool_map(_identify, [(str(root), str(base), n) for n, _ in sorted(items, key=lambda x: -x[1])],
                   a.workers, maxtasks=None)
    names = {n: n.rsplit("/", 1)[-1] for n, _ in items}
    stable, collisions = contract.stable_bundle_names(names.values())
    inputs, subj_name = {}, {}
    for n, s, size in ids:
        subject = stable[names[n]]
        inputs[subject] = Input("bundle", s, size, names[n], ({"kind": "file", "path": str(root / n)},))
        subj_name[subject] = n
    env = Env(store, {"bundles": inputs})
    tasks = [describe(census.CensusStage(), s, None, env) for s in sorted(inputs, key=lambda s: -inputs[s].size)]
    G["store"] = store
    res = pool_map(_census_task, tasks, a.workers)
    for t in tasks:
        env.done(t.id, t.key)
    st = describe(link.ScriptsStage(), "all", None, env)
    execute(st, store, {"link.scripts": link.ScriptsStage()})
    doc = {"dir": str(root), "bundles": {s: {"name": subj_name[s], "sha256": inputs[s].sha256,
                                             "size": inputs[s].size} for s in sorted(inputs)},
           "census": {t.subject: t.key for t in tasks}, "scripts": st.key, "collisions": collisions,
           "statuses": dict(Counter(r[2] for r in res)), "wall": round(time.perf_counter() - t0, 1)}
    write_json(base / PREPARED, doc)
    log("prepared", len(inputs), "bundles into", base)
    return doc


# ---------------------------------------------------------------- m3 (stage cost)
def _prepared_env(base: Path):
    from nnnotes.contract import Input
    from nnnotes.stages import Env
    from nnnotes.store import Store
    prep = json.loads((base / PREPARED).read_text(encoding="utf-8"))
    root = Path(prep["dir"])
    inputs = {s: Input("bundle", b["sha256"], b["size"], b["name"].rsplit("/", 1)[-1],
                       ({"kind": "file", "path": str(root / b["name"])},)) for s, b in prep["bundles"].items()}
    env = Env(Store(base), {"bundles": inputs})
    for s, k in prep["census"].items():
        env.done(f"unity.census:{s}", k)
    env.done("link.scripts:all", prep["scripts"])
    return prep, env


def _new_store(base: Path, path: Path):
    """A run store that knows the base store's input identities (so inputs are not hashed again)."""
    from nnnotes.store import Store
    path.mkdir(parents=True, exist_ok=True)
    src = base / "inputs"
    if src.is_dir():
        (path / "inputs").mkdir(exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and not (path / "inputs" / f.name).exists():
                shutil.copyfile(f, path / "inputs" / f.name)
    return Store(path)


def _stage(name: str):
    from nnnotes import census
    from nnnotes.objexport import ExportStage
    return {"unity.export": ExportStage, "unity.census": census.CensusStage}[name]()


def _run_task(task, store, stage_name: str) -> dict:
    from nnnotes.stages import execute
    hwm = reset_hwm()
    u0, s0 = cpu_now()
    t0 = time.perf_counter()
    rec = {"subject": task.subject, "key": task.key}
    try:
        ex = execute(task, store, {stage_name: _stage(stage_name)}, force=True)
        rec.update(status=ex.status, result=ex.result_status)
    except Exception as e:                      # noqa: BLE001
        rec.update(status="error", error=message(e), trace=traceback.format_exc()[-1200:])
    u1, s1 = cpu_now()
    rec.update(wall=round(time.perf_counter() - t0, 4), user=round(u1 - u0, 4), sys=round(s1 - s0, 4),
               hwmKb=status_kb("VmHWM"), hwmReset=hwm, pid=os.getpid())
    return rec


def _m3_worker(task) -> dict:
    return _run_task(task, G["store"], G["stage"])


def _apply_atoms(atoms) -> None:
    from nnnotes.atoms import env_name, resolve
    for name, spec in atoms:
        os.environ[env_name(name)] = spec
    for name, _ in atoms:
        resolve(name)                           # import the candidate now (shared by forked workers)


def _import_stage_modules() -> dict:
    """Import what a unity.export task needs, timed (wall, CPU)."""
    t0, c0 = time.perf_counter(), time.process_time()
    for m in ("numpy", "PIL.Image", "nnnotes.objexport", "nnnotes.export"):
        importlib.import_module(m)
    return {"wall": round(time.perf_counter() - t0, 4), "cpu": round(time.process_time() - c0, 4)}


def _describe_all(stage_name: str, subjects, params, env):
    from nnnotes.stages import describe
    stage = _stage(stage_name)
    return [describe(stage, s, params, env) for s in subjects]


def _subjects(prep: dict, listing: str | None) -> list[str]:
    if not listing:
        return sorted(prep["bundles"])
    want = {n for n, _ in bundle_list(Path(prep["dir"]), listing)}
    return sorted(s for s, b in prep["bundles"].items() if b["name"] in want)


def summarize_runs(recs: list) -> dict:
    ok = [r for r in recs if r.get("status") in ("ran", "hit")]
    bad = [r for r in recs if r.get("status") not in ("ran", "hit")]
    per_worker: dict[int, int] = {}
    for r in ok:
        per_worker[r["pid"]] = max(per_worker.get(r["pid"], 0), r["hwmKb"])
    hwm = sorted(r["hwmKb"] for r in ok)
    cpu = [r["user"] + r["sys"] for r in ok]
    return {"tasks": len(recs), "ok": len(ok), "errors": [{k: r.get(k) for k in ("subject", "error", "trace")}
                                                         for r in bad][:20],
            "results": dict(Counter(r.get("result") for r in ok)),
            "taskCpu": round(sum(cpu), 2), "taskUser": round(sum(r["user"] for r in ok), 2),
            "taskSys": round(sum(r["sys"] for r in ok), 2), "taskWall": round(sum(r["wall"] for r in ok), 2),
            "hwmMedianKb": hwm[len(hwm) // 2] if hwm else None, "hwmMaxKb": hwm[-1] if hwm else None,
            "workerPeakKb": sorted(per_worker.values(), reverse=True)[:32],
            "hwmReset": all(r.get("hwmReset") for r in ok)}


def cmd_m3(a) -> dict:
    check_clean_env()
    base, out_store = Path(a.base), Path(a.store)
    atoms = parse_atoms(a.atom)
    params = parse_params(a.param) or None
    prep, env = _prepared_env(base)
    subjects = _subjects(prep, a.bundles)
    if a.fresh:
        return m3_fresh(a, base, out_store, atoms, params, prep, subjects)
    imp = _import_stage_modules()
    _apply_atoms(atoms)
    tasks = _describe_all(a.stage, subjects, params, env)
    tasks.sort(key=lambda t: -t.inputs[0].size)
    store = _new_store(base, out_store)
    G.update(store=store, stage=a.stage)
    log("m3", a.stage, len(tasks), "tasks,", a.workers, "workers, atoms", dict(atoms), "params", params)
    jsonl = Path(a.out).with_suffix(".jsonl") if a.out else None
    fo = open(jsonl, "w", encoding="utf-8", newline="\n") if jsonl else None

    def take(r):
        if fo:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    t0 = time.perf_counter()
    c0 = os.times()
    try:
        recs = pool_map(_m3_worker, tasks, a.workers, maxtasks=a.maxtasks, on_result=take)
    finally:
        if fo:
            fo.close()
    c1 = os.times()
    doc = {"command": "m3", "stage": a.stage, "mode": "pool", "workers": a.workers, "maxtasks": a.maxtasks,
           "atoms": dict(atoms), "params": params, "store": str(out_store.resolve()),
           "taskAtoms": tasks[0].atoms if tasks else None,
           "wall": round(time.perf_counter() - t0, 2),
           "childrenCpu": round((c1.children_user - c0.children_user) + (c1.children_system - c0.children_system), 2),
           "importInParent": imp, **summarize_runs(recs),
           "taskKeys": {r["subject"]: r["key"] for r in recs}, "versions": versions()}
    if a.out:
        write_json(a.out, doc)
    log("m3 done", doc["wall"], "s wall,", doc["taskCpu"], "task CPU-s")
    return doc


def m3_fresh(a, base, out_store, atoms, params, prep, subjects) -> dict:
    """One new process per bundle: its wall, user + system CPU and peak RSS (wait4), and inside it the import
    time and the task's own cost."""
    _new_store(base, out_store)
    cmd = [sys.executable, str(Path(__file__).resolve()), "m3-one", "--base", str(base), "--store", str(out_store),
           "--stage", a.stage]
    for n, s in atoms:
        cmd += ["--atom", f"{n}={s}"]
    for p in a.param or ():
        cmd += ["--param", p]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(HERE), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    sizes = {s: prep["bundles"][s]["size"] for s in subjects}
    queue = sorted(subjects, key=lambda s: -sizes[s])
    running: dict[int, tuple] = {}
    recs = []
    tmp = out_store / ".atom_eval"
    tmp.mkdir(exist_ok=True)
    jsonl = Path(a.out).with_suffix(".jsonl") if a.out else None
    fo = open(jsonl, "w", encoding="utf-8", newline="\n") if jsonl else None
    t0 = time.perf_counter()
    log("m3 fresh", a.stage, len(queue), "bundles,", a.workers, "at a time")
    while queue or running:
        while queue and len(running) < a.workers:
            s = queue.pop(0)
            fo_out = open(tmp / f"{len(recs) + len(running)}.out", "w+b")
            fo_err = open(tmp / f"{len(recs) + len(running)}.err", "w+b")
            p = subprocess.Popen(cmd + ["--subject", s], stdout=fo_out, stderr=fo_err, env=env)
            running[p.pid] = (p, s, time.perf_counter(), fo_out, fo_err)
        pid, status, ru = os.wait4(-1, 0)
        if pid not in running:
            continue
        p, s, start, fo_out, fo_err = running.pop(pid)
        out, err = b"", b""
        for f in (fo_out, fo_err):
            f.seek(0)
        out, err = fo_out.read(), fo_err.read()
        for f in (fo_out, fo_err):
            f.close()
            os.unlink(f.name)
        p.returncode = os.waitstatus_to_exitcode(status)
        rec = {"subject": s, "size": sizes[s], "exit": p.returncode, "processWall": round(time.perf_counter() - start, 4),
               "processUser": round(ru.ru_utime, 4), "processSys": round(ru.ru_stime, 4),
               "processMaxRssKb": ru.ru_maxrss}
        try:
            rec.update(json.loads(out.decode("utf-8").strip().splitlines()[-1]))
        except (ValueError, IndexError):
            rec["error"] = err.decode("utf-8", "replace")[-800:]
        recs.append(rec)
        if fo:
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if len(recs) % 200 == 0:
            log("  ", len(recs), "/", len(subjects))
    if fo:
        fo.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ok = [r for r in recs if r.get("status") in ("ran", "hit")]
    bad = [r for r in recs if r.get("status") not in ("ran", "hit")]
    rss = sorted(r["processMaxRssKb"] for r in ok)
    doc = {"command": "m3", "stage": a.stage, "mode": "fresh", "workers": a.workers, "atoms": dict(atoms),
           "params": params, "store": str(out_store.resolve()), "wall": round(time.perf_counter() - t0, 2),
           "tasks": len(recs), "ok": len(ok),
           "errors": [{k: r.get(k) for k in ("subject", "error", "trace")} for r in bad][:20],
           "processCpu": round(sum(r["processUser"] + r["processSys"] for r in ok), 2),
           "processWall": round(sum(r["processWall"] for r in ok), 2),
           "importWall": round(sum(r["imports"]["wall"] for r in ok), 2),
           "importCpu": round(sum(r["imports"]["cpu"] for r in ok), 2),
           "taskCpu": round(sum(r["user"] + r["sys"] for r in ok), 2),
           "taskWall": round(sum(r["wall"] for r in ok), 2),
           "processMaxRssMedianKb": rss[len(rss) // 2] if rss else None, "processMaxRssMaxKb": rss[-1] if rss else None,
           "taskKeys": {r["subject"]: r.get("key") for r in recs}, "versions": versions()}
    if a.out:
        write_json(a.out, doc)
    log("m3 fresh done", doc["wall"], "s wall")
    return doc


def cmd_m3_one(a) -> None:
    """(internal) one task in this new process; prints one JSON line."""
    c0 = time.process_time()
    imp = _import_stage_modules()
    atoms = parse_atoms(a.atom)
    t0, ci = time.perf_counter(), time.process_time()
    _apply_atoms(atoms)
    imp["candidates"] = {"wall": round(time.perf_counter() - t0, 4), "cpu": round(time.process_time() - ci, 4)}
    imp["wall"] = round(imp["wall"] + imp["candidates"]["wall"], 4)
    imp["cpu"] = round(imp["cpu"] + imp["candidates"]["cpu"], 4)
    imp["rssKb"] = status_kb("VmRSS")
    base = Path(a.base)
    t0, cd = time.perf_counter(), time.process_time()
    prep, env = _prepared_env(base)
    (task,) = _describe_all(a.stage, [a.subject], parse_params(a.param) or None, env)
    describe_cost = {"wall": round(time.perf_counter() - t0, 4), "cpu": round(time.process_time() - cd, 4)}
    from nnnotes.store import Store
    rec = _run_task(task, Store(a.store), a.stage)
    rec.update(imports=imp, describe=describe_cost, processCpuInside=round(time.process_time() - c0, 4))
    print(json.dumps(rec, ensure_ascii=False))


# ---------------------------------------------------------------- imports
def cmd_imports(a) -> dict:
    sets = [m.split(",") for m in (a.module or ["numpy,PIL.Image,nnnotes.objexport,nnnotes.export"])]
    code = ("import resource, sys, time, json\n"
            "t = time.perf_counter(); c = time.process_time()\n"
            "for m in sys.argv[1].split(','): __import__(m)\n"
            "r = resource.getrusage(resource.RUSAGE_SELF)\n"
            "print(json.dumps({'wall': time.perf_counter() - t, 'cpu': time.process_time() - c, "
            "'maxRssKb': r.ru_maxrss}))\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(HERE), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    out = {}
    for mods in sets:
        rows = []
        for _ in range(a.repeat):
            t0 = time.perf_counter()
            r = subprocess.run([sys.executable, "-c", code, ",".join(mods)], capture_output=True, text=True, env=env)
            if r.returncode:
                rows.append({"error": r.stderr[-300:]})
                continue
            d = json.loads(r.stdout.strip().splitlines()[-1])
            d["processWall"] = time.perf_counter() - t0
            rows.append(d)
        good = [x for x in rows if "error" not in x]

        def med(k):
            v = sorted(x[k] for x in good)
            return round(v[len(v) // 2], 4) if v else None
        out[",".join(mods)] = {"n": len(good), "wall": med("wall"), "cpu": med("cpu"), "maxRssKb": med("maxRssKb"),
                               "processWall": med("processWall"),
                               "errors": [x["error"] for x in rows if "error" in x][:2]}
    doc = {"command": "imports", "repeat": a.repeat, "sets": out, "versions": versions()}
    if a.out:
        write_json(a.out, doc)
    return doc


# ---------------------------------------------------------------- compare
def _class_role(record: dict) -> tuple[str, str]:
    obj = (record.get("provenance") or {}).get("object") or {}
    role = record["id"].rsplit("#", 1)[-1]
    return obj.get("class") or "(task)", role.split(":", 1)[0]


def compare_results(ra: dict, rb: dict, read_a, read_b, T: Tally, subject: str) -> None:
    """Items (status and reason) and artifacts (content) of two results of the same task subject."""
    ia = {i["object"]: i for i in ra.get("items", ())}
    ib = {i["object"]: i for i in rb.get("items", ())}
    for o, x in ia.items():
        y = ib.get(o)
        cls = x.get("class", "?")
        if y is None:
            T.add("items", cls, "missing", o)
            continue
        same = (x["status"], (x.get("reason") or {}).get("code")) == (y["status"], (y.get("reason") or {}).get("code"))
        T.add("items", cls, "equal" if same else "differ", o,
              None if same else f"{x['status']}/{(x.get('reason') or {}).get('code')} vs "
                                f"{y['status']}/{(y.get('reason') or {}).get('code')}")
    for o in ib.keys() - ia.keys():
        T.add("items", ib[o].get("class", "?"), "extra", o)
    aa = {x["id"]: x for x in ra.get("artifacts", ())}
    ab = {x["id"]: x for x in rb.get("artifacts", ())}
    for aid, x in aa.items():
        cls, role = _class_role(x)
        key = f"{cls} | {role}"
        y = ab.get(aid)
        if y is None:
            T.add("artifacts", key, "missing", aid)
            continue
        if x["content"]["sha256"] == y["content"]["sha256"]:
            T.add("artifacts", key, "equal")
            continue
        if x["content"].get("ext") == "png" and y["content"].get("ext") == "png":
            from PIL import Image
            ia_ = Image.open(io.BytesIO(read_a(x["content"]["sha256"])))
            ib_ = Image.open(io.BytesIO(read_b(y["content"]["sha256"])))
            st, what = image_diff(ia_, ib_)
            T.add("artifacts", key, "equal-pixels" if st == "equal" else "differ", aid, what)
            T.stat("artifacts", key, "bytesA", x["content"]["size"])
            T.stat("artifacts", key, "bytesB", y["content"]["size"])
        else:
            T.add("artifacts", key, "differ", aid, f"{x['content']['size']} vs {y['content']['size']} bytes")
    for aid in ab.keys() - aa.keys():
        cls, role = _class_role(ab[aid])
        T.add("artifacts", f"{cls} | {role}", "extra", aid)


def _compare_job(job) -> dict:
    subject, ka, kb = job
    sa, sb = G["sa"], G["sb"]
    T = Tally()
    ra = sa.result(ka) if ka else None
    rb = sb.result(kb) if kb else None
    if ra is None or rb is None:
        T.add("tasks", "result", "missing", subject, f"A {'ok' if ra else 'none'}, B {'ok' if rb else 'none'}")
    else:
        T.add("tasks", "result", "equal" if ra.get("status") == rb.get("status") else "differ", subject)
        compare_results(ra, rb, sa.read, sb.read, T, subject)
    return {"bundle": subject, **T.dump()}


def cmd_compare(a) -> dict:
    from nnnotes.store import Store
    ra, rb = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.run_a, a.run_b))
    G.update(sa=Store(ra["store"]), sb=Store(rb["store"]))
    ta, tb = ra["taskKeys"], rb["taskKeys"]
    jobs = [(s, ta.get(s), tb.get(s)) for s in sorted(ta.keys() | tb.keys())]
    total = {"counts": {}, "stats": {}, "examples": {}}
    for r in pool_map(_compare_job, jobs, a.workers, maxtasks=None):
        merge(total, r)
    doc = {"command": "compare", "a": {k: ra.get(k) for k in ("atoms", "params", "store", "workers", "mode")},
           "b": {k: rb.get(k) for k in ("atoms", "params", "store", "workers", "mode")}, "subjects": len(jobs),
           **total}
    if a.out:
        write_json(a.out, doc)
    return doc


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, bundles=True):
        if bundles:
            p.add_argument("--bundles", help="sample file, JSON list or text file of bundle names")
        p.add_argument("-o", "--out", help="output JSON")
        p.add_argument("--workers", type=int, default=os.cpu_count() or 1)

    p = sub.add_parser("sample")
    p.add_argument("dir")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--largest", type=int, default=50)
    p.add_argument("--random", type=int, default=1000)
    common(p)
    p = sub.add_parser("m1")
    p.add_argument("dir")
    p.add_argument("--atom", action="append", help="NAME=module:attribute (repeatable)")
    p.add_argument("--checks", help=f"comma-separated subset of {','.join(CHECKS)}")
    p.add_argument("--threads", type=int, default=8, help="threads of the det check")
    p.add_argument("--reference-reader", help="module:attribute of another reference reader (tests)")
    common(p)
    p = sub.add_parser("prepare")
    p.add_argument("dir")
    p.add_argument("--base", required=True)
    common(p)
    for name in ("m3", "m3-one"):
        p = sub.add_parser(name)
        if name == "m3":
            p.add_argument("--fresh", action="store_true", help="a new process per bundle")
            p.add_argument("--maxtasks", type=int, default=40, help="tasks per worker process before it is replaced")
            common(p)
        else:
            p.add_argument("--subject", required=True)
        p.add_argument("--base", required=True)
        p.add_argument("--store", required=True)
        p.add_argument("--stage", default="unity.export", choices=("unity.export", "unity.census"))
        p.add_argument("--atom", action="append")
        p.add_argument("--param", action="append", help="stage parameter path=value, e.g. png.level=1")
    p = sub.add_parser("imports")
    p.add_argument("--module", action="append", help="comma-separated modules imported together (repeatable)")
    p.add_argument("--repeat", type=int, default=5)
    common(p, bundles=False)
    p = sub.add_parser("compare")
    p.add_argument("run_a")
    p.add_argument("run_b")
    common(p, bundles=False)
    a = ap.parse_args(argv)
    fn = {"sample": cmd_sample, "m1": cmd_m1, "prepare": cmd_prepare, "m3": cmd_m3, "m3-one": cmd_m3_one,
          "imports": cmd_imports, "compare": cmd_compare}[a.cmd]
    doc = fn(a)
    if doc is not None and not getattr(a, "out", None):
        print(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
