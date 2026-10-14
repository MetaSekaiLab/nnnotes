"""Stages `unity.export` (one bundle -> an artifact per object) and `sprite.crop` (the images of the sprites whose atlas
is in another bundle).

unity.export reads one bundle alone (atom `reader`) and runs the converters of nnnotes.unity.convert over it, giving
every object exactly one item (contract.item): `exported` (its artifacts), `contained` (part of another artifact:
the objects of a hierarchy in its root's prefab, the MonoScripts in `scripts.json`, the AssetBundle objects in
`bundle.json`), `generic` (the object's typetree JSON, with the reason its converter gave for not making the
specialized form), `unsupported` (a class this stage does not convert; its typetree JSON is written all the same) or
`failed` (not even the typetree JSON could be made). A converter's documented limit (atoms.Unsupported) gives the
typetree JSON with its reason code; any other error of a converter gives it with `generic.error` and the error's
message. The reason codes are listed in docs/stages.md. A bundle's task key includes only the converters of the
classes its census lists.

A texture is converted before the sprites cut from it (they read its decoded image) and a hierarchy before the
MonoBehaviours outside it; the objects no converter took get their typetree JSON last.

sprite.crop: a sprite whose SpriteAtlas is in another bundle gets its `meta` from unity.export (with the sprite's
mesh and object facts); one sprite.crop task per atlas then reads the atlas' JSON, the images of its textures and the
metas, and makes "<sprite object id>#image" exactly as unity.export makes the image of a sprite whose atlas is in its
own bundle.
"""

from __future__ import annotations

import io
from collections import defaultdict
from functools import cache

from ..unity import scripttypes
from ..unity.atoms import ATOMS as ATOM_REGISTRY
from ..unity.atoms import Impl, Unsupported, atom_id, resolve
from ..unity.convert import (
    BASE_ATOMS,
    CLASS_CONVERTERS,
    CONVERTER_ATOMS,
    CONVERTERS,
    UNSUPPORTED_CLASSES,
    USM_ASSET_SCRIPT,
    USM_CONVERTER,
    animation,
    converter_id,
    export_atoms,
    files,
    generic,
    material,
    mono,
    prefab,
    textures,
)
from ..unity.convert.objects import (
    TYPETREE,
    BundleObjects,
    ReadError,
    encode_object,
    label,
    message,
    ref_object_id,
)
from . import builtins, contract, link
from . import census as census_mod
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

EXPORT, CROP, CENSUS, SCRIPTS = "unity.export", "sprite.crop", "unity.census", "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")
ATOM_NAMES = frozenset(ATOM_REGISTRY)
# class -> (converter, function), in the order they run after the textures and sprites
_SIMPLE = (
    ("Mesh", "mesh.glb", files.mesh),
    ("TextAsset", "data", files.text),
    ("Font", "font", files.font),
    ("Material", "material.json", material.material),
    ("Shader", "shader.json", material.shader),
    ("AnimationClip", "clip.json", animation.clip),
    ("AnimatorController", "controller.json", animation.controller),
    ("AnimatorOverrideController", "controller.json", animation.controller),
)


# ---------------------------------------------------------------- one bundle
class BundleRun:
    """unity.export on one bundle: the converters over its objects (the task's `classes` only), one item per
    object, the artifacts in the depot."""

    def __init__(self, view, task, depot, fns: dict):
        self.task, self.depot = task, depot
        self.ctx = BundleObjects(
            view,
            fns,
            scripts=task.context.get("scripts", {}),
            blob_min=int(task.params["blobMin"]),
            png_level=int(task.params["png"]["level"]),
            emit=self._emit,
            typetrees=task.context.get("typetrees", {}),
        )
        classes = task.params.get("classes")
        self.selected = None if classes is None else frozenset(classes)
        self.usm_assets = USM_CONVERTER in task.atoms
        self.items: dict[str, dict] = {}
        self.artifacts: list[dict] = []
        self.emitted: set[str] = set()
        self.staged: list[dict] = []
        self.conv = "generic.json"
        self.atoms_of: dict[str, dict] = {}

    # ---------------------------------------------------------------- artifacts and items
    def _atoms(self, conv: str) -> dict:
        a = self.atoms_of.get(conv)
        if a is None:
            names = (*BASE_ATOMS, *CONVERTER_ATOMS.get(conv, ()), conv)
            a = self.atoms_of[conv] = {n: self.task.atoms[n] for n in sorted(names) if n in self.task.atoms}
        return a

    def _emit(
        self,
        key,
        role: str,
        data: bytes,
        ext: str,
        kind: str,
        fmt: str,
        facts: dict,
        *,
        issues=(),
        refs=(),
        owner: str | None = None,
    ) -> tuple[str, dict]:
        """Depot `data` and stage the artifact "<owner>#<role>" (owner: the object `key`, else a task-level name)."""
        aid = contract.artifact_id(owner or contract.object_id(*key), role)
        if aid in self.emitted or any(a["id"] == aid for a in self.staged):
            raise ValueError(f"artifact {aid} made twice")
        content = self.depot.add(data, ext)
        prov = contract.provenance(self.task, atoms=self._atoms(self.conv), obj=None if owner else self.ctx.facts(key))
        sem = {"kind": kind, "format": fmt, "facts": facts}
        if issues:
            sem["issues"] = [{"code": c, "message": m} for c, m in issues]
        if refs:
            sem["refs"] = list(refs)
        self.staged.append(contract.artifact(aid, content, prov, sem))
        return aid, content

    def _begin(self, conv: str) -> None:
        self.staged, self.conv = [], conv
        self.ctx.seen.clear()

    def _publish(self) -> list[str]:
        """Keep the staged artifacts; returns their ids."""
        ids = [a["id"] for a in self.staged]
        self.emitted.update(ids)
        self.artifacts.extend(self.staged)
        self.staged = []
        return ids

    def _refs(self, info) -> list[str]:
        """The objects the documents of `info` made since the last _begin reference."""
        return sorted(self.ctx.seen - {info.id})

    def _commit(self, info, status: str, why: dict | None = None) -> None:
        """The item of `info`: the artifacts its conversion staged and the objects they reference."""
        refs = self._refs(info)
        ids = self._publish()
        self.items[info.id] = contract.item(info.id, status, artifacts=ids, why=why, cls=info.class_name, refs=refs)

    def _failed(self, info, e: Exception, prefix: str = "") -> None:
        self._begin("generic.json")
        what = "failed.read" if isinstance(e, ReadError) else "failed.export"
        detail = str(e) if isinstance(e, ReadError) else message(e)
        why = contract.reason(what, label(prefix + detail))
        self.items[info.id] = contract.item(info.id, "failed", why=why, cls=info.class_name)

    def _need(self, conv: str) -> None:
        if conv not in self.task.atoms:
            raise IncompatibleTask(
                f"task {self.task.id}: converter {conv} is not in the task (its census lacks the class)"
            )

    def _convert(self, info, conv: str, fn, *args, **kw) -> bool:
        """Run a converter on one object: its artifacts (exported), else the typetree JSON with the reason."""
        if info.id in self.items:
            return False
        self._need(conv)
        self._begin(conv)
        try:
            fn(self.ctx, info, *args, **kw)
        except Unsupported as e:
            self._fallback(info, e.code, e.message)
            return False
        except Exception as e:
            self._fallback(info, "generic.error", message(e))
            return False
        self._commit(info, "exported")
        return True

    def _fallback(self, info, code: str, why: str, status: str = "generic") -> None:
        self._begin("generic.json")
        try:
            generic.generic(self.ctx, info)
        except Exception as e:
            self._failed(info, e, f"{code}: {why}; typetree JSON: ")
            return
        self._commit(info, status, contract.reason(code, label(why)))

    def _generic(self, info) -> None:
        """An object of a class without a specialized converter: its typetree JSON (`generic` when it could be read
        only in part)."""
        self._begin("generic.json")
        try:
            why = generic.generic(self.ctx, info)
        except Exception as e:
            self._failed(info, e)
            return
        if why is None:
            self._commit(info, "exported")
        else:
            self._commit(info, "generic", contract.reason(TYPETREE, label(why)))

    def _selected(self, cls: str) -> bool:
        return self.selected is None or cls in self.selected

    def _of(self, cls: str) -> list:
        """The objects of `cls` this task converts (none when the task's `classes` leave the class out)."""
        return self.ctx.of(cls) if self._selected(cls) else []

    # ---------------------------------------------------------------- the bundle
    def run(self) -> tuple[list, list]:
        self._textures_sprites()
        for cls, conv, fn in _SIMPLE:
            for i in self._of(cls):
                self._convert(i, conv, fn)
        self._prefabs()
        for i in self._of("MonoBehaviour"):
            self._convert(i, "mono.json", mono.mono, usm_assets=self.usm_assets)
        for conv, (cls, kind) in generic.LOOSE.items():
            self._loose(cls, conv, kind)
        for i in self.ctx.infos:
            if i.id in self.items or not self._selected(i.class_name):
                continue
            if i.class_name in UNSUPPORTED_CLASSES:
                self._fallback(
                    i, f"unsupported.class.{i.class_name}", f"{i.class_name} is not converted", status="unsupported"
                )
            else:
                self._generic(i)
        return self.artifacts, list(self.items.values())

    def _textures_sprites(self) -> None:
        waiting, atlases = defaultdict(list), {}
        for s in self._of("Sprite"):
            self._need("sprite.png")
            self._begin("sprite.png")
            try:
                plan = textures.sprite_plan(self.ctx, (s.file, s.path_id), atlases)
            except Unsupported as e:
                self._fallback(s, e.code, e.message)
                continue
            except Exception as e:
                self._fallback(s, "generic.error", message(e))
                continue
            if plan["external"]:
                self._convert(s, "sprite.png", textures.sprite_external, plan, CROP)
            else:
                waiting[plan["texture"]].append((s, plan))
        export_textures = self._selected("Texture2D")
        for t in self.ctx.of("Texture2D"):
            sprites = waiting.pop((t.file, t.path_id), ())
            if not sprites and not export_textures:
                continue
            holder: dict = {}
            if export_textures:
                self._convert(t, "tex.png", textures.texture, holder)
            else:
                textures.decode_into(self.ctx, t, holder)
            for s, plan in sprites:
                self._convert(s, "sprite.png", textures.sprite, plan, holder)
            holder.clear()
        for tkey, rest in waiting.items():
            for s, _plan in rest:
                self._fallback(s, "no_data.sprite", f"texture {contract.object_id(*tkey)} is not decoded")

    def _prefabs(self) -> None:
        if not self._selected("GameObject"):
            return
        h = prefab.hierarchies(self.ctx)
        info_of = self.ctx.info
        for root in h.roots:
            info = info_of.get((root.file, root.game_object))
            if root.error is not None or info is None or info.class_name != "GameObject":
                why = root.error or f"transform {root.transform}: no GameObject"
                for k in root.members:
                    if k in info_of and info_of[k].id not in self.items:
                        self._fallback(info_of[k], "generic.error", f"hierarchy at transform {root.transform}: {why}")
                continue
            if self._convert(info, "prefab.json", prefab.prefab, h, root):
                within = contract.artifact_id(info.id, "json")
                for k in root.members:
                    i = info_of[k]
                    if i.id != info.id:
                        self.items[i.id] = contract.item(i.id, "contained", within=within, cls=i.class_name)
            else:
                why = self.items[info.id].get("reason") or {"code": "generic.error", "message": ""}
                for k in root.members:
                    i = info_of[k]
                    if i.id not in self.items:
                        self._fallback(i, why["code"], f"hierarchy of {info.id}: {why['message']}")

    def _loose(self, cls: str, conv: str, kind: str) -> None:
        """Objects of `cls` in one task-level artifact "<task id>#<conv>" (contained in it)."""
        infos = [i for i in self._of(cls) if i.id not in self.items]
        if not infos:
            return
        self._need(conv)
        self._begin(conv)
        entries, done, failed = {}, [], []
        for i in infos:
            mark = len(self.staged)
            key = (i.file, i.path_id)
            self.ctx.seen.clear()
            try:
                entries[i.id] = self.ctx.tree(self.ctx.readers[key], self.ctx.tt(key))
                done.append((i, self._refs(i)))
            except Exception as e:
                del self.staged[mark:]
                failed.append((i, e))
        if done:
            doc = encode_object(generic.loose_doc(self.ctx, conv, entries))
            aid = self._emit(None, conv, doc, "json", kind, "json", {"objects": len(done)}, owner=self.task.id)[0]
            self._publish()
            for i, refs in done:
                self.items[i.id] = contract.item(i.id, "contained", within=aid, cls=i.class_name, refs=refs)
        for i, e in failed:
            if isinstance(e, ReadError):
                self._failed(i, e)
            else:
                self._fallback(i, "generic.error", message(e))


# ---------------------------------------------------------------- stages
def _png_params(p: dict | None) -> dict:
    png = dict(p or {})
    unknown = sorted(set(png) - {"level"})
    if unknown:
        raise ValueError(f"unknown png parameters {', '.join(unknown)}")
    level = png.get("level", 6)
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 9:
        raise ValueError(f"png level {level!r}: expected 0-9")
    return {"level": level}


def _check_atoms(task, reader: Impl | None = None) -> None:
    """IncompatibleTask when this installation would run the task with other atoms or converters than it names."""
    for n, v in task.atoms.items():
        if n in CONVERTERS:
            want = converter_id(n)
        elif n == "reader" and reader is not None:
            want = reader.id
        elif n in ATOM_NAMES:
            want = atom_id(n)
        else:
            raise IncompatibleTask(f"task {task.id}: unknown atom or converter {n!r}")
        if v != want:
            raise IncompatibleTask(f"task {task.id}: {n} is {want} here, the task names {v}")


class _Docs:
    """A few JSON documents of the depot by content id (the planning reads each census several times)."""

    def __init__(self, size: int = 64):
        self.size, self.docs = size, {}

    def get(self, depot, sha: str) -> dict:
        doc = self.docs.get(sha)
        if doc is None:
            doc = contract.loads(depot.read(sha))
            if len(self.docs) >= self.size:
                self.docs.pop(next(iter(self.docs)))
            self.docs[sha] = doc
        return doc


class ExportStage(Stage):
    """unity.export: one bundle -> an artifact per object, one item per object (see the module documentation).

    Subjects: the selected bundles (the fact "selected", stable bundle names) and, when a selected bundle holds
    sprites, the bundles of the fact "bundles" (the selection's closure) that hold a SpriteAtlas: a sprite reaches its
    atlas texture through the atlas' bundle, whose export makes the texture's image (sprite.crop reads it). Inputs:
    "bundles" {subject: Input (role "bundle")}, as unity.census. It reads the census of its bundle
    (unity.census:<subject>) for the classes (the converters and atoms of its key) and the MonoScripts its objects
    reference, and the script table (link.scripts:all) for the entries of those: its context
    {"scripts": {object id: "assembly|namespace|class"}}, and for the MonoBehaviours of files that store no type trees
    (the APK's built-in files) the type trees nnnotes ships for their classes: "typetrees" {script entry:
    {"typeHash", "nodes"}}. Parameters: `blobMin` (bytes; byte arrays of this size or
    more are blobs), `png.level` (zlib level of the PNG files). `reader`: another implementation of the reader atom
    (its id enters the keys)."""

    name = EXPORT
    version = 1
    after = (CENSUS, SCRIPTS)
    PARAMS = {"blobMin": 4096, "classes": None, "png": {"level": 6}}
    LAYOUT_ROOT = "_bundles/{subject}"

    def __init__(self, reader: Impl | None = None):
        self.reader = reader
        self._docs = _Docs()

    def __getstate__(self):
        return {"reader": self.reader}

    def __setstate__(self, state):
        self.__init__(state["reader"])

    @property
    def ATOMS(self) -> dict:
        return dict(
            sorted(
                {**export_atoms(CLASS_CONVERTERS, self.reader_id()), USM_CONVERTER: converter_id(USM_CONVERTER)}.items()
            )
        )

    def reader_id(self) -> str:
        return self.reader.id if self.reader is not None else atom_id("reader")

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        blob = p.get("blobMin", 4096)
        if not isinstance(blob, int) or isinstance(blob, bool) or blob < 1:
            raise ValueError(f"stage {self.name}: blobMin {blob!r}: expected a positive integer")
        classes = p.get("classes")
        if classes is not None:
            if isinstance(classes, str) or not all(isinstance(c, str) and c for c in classes):
                raise ValueError(f"stage {self.name}: classes {classes!r}: expected a list of class names")
            classes = sorted(set(classes))
        return {"blobMin": blob, "classes": classes, "png": _png_params(p.get("png"))}

    def subjects(self, env) -> list[str]:
        selected = set(env.fact("selected"))
        out = set(selected)
        if any("Sprite" in _census_classes(env, s) for s in sorted(selected)):
            out.update(s for s in env.fact("bundles") if s not in out and "SpriteAtlas" in _census_classes(env, s))
        return sorted(out)

    def inputs(self, subject: str, env) -> list[Input]:
        inp = env.fact("bundles")[subject]
        if inp.role != "bundle":
            raise ValueError(f"{self.name} {subject}: input role {inp.role!r}, expected 'bundle'")
        return [inp]

    def census(self, subject: str, env) -> dict:
        inp = env.input_of(contract.task_id(CENSUS, subject), "census")
        return self._docs.get(env.depot, inp.sha256)

    def uses(self, subject: str, env) -> dict:
        """The atoms and converters of the classes the bundle's census lists; with the task's `classes` (env.params
        while the task is described), of those of them only; converter cri.usm when a MonoBehaviour of the bundle is
        a movie asset (its script context names the class)."""
        classes = list(self.census(subject, env)["classes"])
        only = (getattr(env, "params", None) or {}).get("classes")
        if only is not None:
            keep = set(only)
            classes = [c for c in classes if c in keep]
        atoms = export_atoms(classes, self.reader_id())
        if "MonoBehaviour" in classes and USM_ASSET_SCRIPT in self.context(subject, env).get("scripts", {}).values():
            atoms = dict(sorted({**atoms, USM_CONVERTER: converter_id(USM_CONVERTER)}.items()))
        return atoms

    def context(self, subject: str, env) -> dict:
        doc = self.census(subject, env)
        refs = census_mod.script_refs(doc)
        if not refs:
            return {}
        table = self._docs.get(env.depot, env.input_of(SCRIPTS_TASK, "scripts").sha256)["scripts"]
        out = {"scripts": link.script_context(table, refs)}
        types = _script_types(doc, out["scripts"])
        if types:
            out["typetrees"] = types
        return out

    def depends(self, subject: str, env) -> list[str]:
        return [contract.task_id(CENSUS, subject), SCRIPTS_TASK]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """From the bundle's size, its census' object count and the size of its resource files (least squares over
        a whole catalog: CPU seconds; peak resident bytes of a worker process, which grow with the objects)."""
        size = sum(i.size for i in inputs)
        try:
            doc = self.census(subject, env)
            n = int(doc["objects"])
            res = sum(int(r.get("size") or 0) for r in doc.get("resources") or ())
        except Exception:
            n = res = 0
        return Cost(0.12 + size * 3.3e-7 + n * 8.8e-5 + res * 6.6e-8, (80 << 20) + 80 * size + 17500 * n)

    def run(self, task, depot) -> Output:
        reader = self.reader if self.reader is not None else resolve("reader")
        _check_atoms(task, reader)
        view = reader.fn(depot.input_bytes(task.input("bundle")), builtins.serialized_name(task.subject))
        fns = {n: resolve(n).fn for n in task.atoms if n in ATOM_NAMES and n != "reader"}
        arts, items = BundleRun(view, task, depot, fns).run()
        return Output(arts, items)


@cache
def _embedded(unity_version: str) -> dict:
    return scripttypes.script_types(unity_version)


def _script_types(doc: dict, scripts: dict) -> dict:
    """{script entry: {"typeHash", "nodes"}} of the embedded type trees (nnnotes.unity.scripttypes, of each file's
    Unity version) of the classes of the MonoBehaviours in the census' files that store no type trees ({} when it has
    none)."""
    out = {}
    for f in doc["files"]:
        if f["typetree"]:
            continue
        types = _embedded(f["unityVersion"])
        for o in f["objects"]:
            s = o.get("script") if o["class"] == "MonoBehaviour" else None
            entry = scripts.get(contract.object_id(s["file"], s["pathId"])) if s and s.get("file") else None
            if entry in types and out.setdefault(entry, types[entry]) != types[entry]:
                raise ValueError(f"{entry}: files of several Unity versions with different type trees of it")
    return dict(sorted(out.items()))


def _census_classes(env, subject: str) -> dict:
    """{class: objects} of a bundle's census ({} when its census has no result)."""
    try:
        return env.artifact(contract.task_id(CENSUS, subject), "census")["semantics"]["facts"]["classes"]
    except (Pending, KeyError):
        return {}


def _store_input(role: str, record: dict) -> Input:
    c = record["content"]
    return Input(role, c["sha256"], c["size"], None, ({"kind": "depot"},))


class SpriteCropStage(Stage):
    """sprite.crop: the images of the sprites whose SpriteAtlas is in another bundle. One task per atlas, subject
    "<unity.export subject of the atlas' bundle>:<atlas pathId>"; inputs: "atlas" (the atlas' JSON), "sprite:<object
    id>" (each sprite's meta), "texture:<object id>" (the image of each atlas texture the sprites use), all artifacts
    of unity.export results. Waits for unity.export tasks that have not run. Artifacts "<sprite object id>#image"."""

    name = CROP
    version = 1
    after = (EXPORT,)
    PARAMS = {"png": {"level": 6}}
    CROP_ATOMS = ("png.encode", "sprite.crop")

    def __init__(self):
        self._memo = None
        self._docs = _Docs(256)

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.__init__()

    @property
    def ATOMS(self) -> dict:
        return {n: atom_id(n) for n in self.CROP_ATOMS}

    def normalize(self, params: dict | None) -> dict:
        p = dict(params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"stage {self.name}: unknown parameters {', '.join(unknown)}")
        return {"png": _png_params(p.get("png"))}

    @staticmethod
    def _may_hold(env, tid: str) -> bool:
        subject = contract.parse_task_id(tid, EXPORT)[1]
        try:
            classes = env.artifact(contract.task_id(CENSUS, subject), "census")["semantics"]["facts"]["classes"]
        except Exception:
            return True
        return "Sprite" in classes or "SpriteAtlas" in classes

    def index(self, env) -> dict:
        """{subjects: {subject: (atlas object id, [(sprite object id, export task id, meta record)])}, atlases,
        textures, missing: atlas object ids that sprites name but no result holds}."""
        pending = env.pending(EXPORT)
        if pending:
            raise Pending(pending[0])
        tids = env.tasks(EXPORT)
        sig = tuple(env.key(t) for t in tids)
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        atlases, textures, requests = {}, {}, defaultdict(list)
        for tid in tids:
            if not self._may_hold(env, tid):
                continue
            for a in env.result(tid)["artifacts"]:
                owner, role = contract.parse_artifact_id(a["id"])
                sem, obj = a["semantics"], a["provenance"].get("object") or {}
                facts = sem.get("facts") or {}
                if sem["kind"] == "sprite.meta" and facts.get("crop") == CROP:
                    requests[facts["atlas"]].append((owner, tid, a))
                elif role == "json" and obj.get("class") == "SpriteAtlas":
                    atlases[owner] = (tid, a)
                elif role == "image" and sem["kind"] == "texture.image":
                    textures[owner] = (tid, a)
        subjects = {}
        for atlas, reqs in requests.items():
            if atlas in atlases:
                tid = atlases[atlas][0]
                subject = f"{contract.parse_task_id(tid, EXPORT)[1]}:{contract.parse_object_id(atlas)[1]}"
                subjects[subject] = (atlas, sorted(reqs, key=lambda r: r[0]))
        idx = {
            "subjects": subjects,
            "atlases": atlases,
            "textures": textures,
            "missing": sorted(set(requests) - set(atlases)),
        }
        self._memo = (sig, idx)
        return idx

    def subjects(self, env) -> list[str]:
        return sorted(self.index(env)["subjects"])

    def _subject(self, subject: str, env):
        idx = self.index(env)
        if subject not in idx["subjects"]:
            raise ValueError(f"{self.name}: no sprites wait for atlas {subject}")
        return idx, *idx["subjects"][subject]

    def inputs(self, subject: str, env) -> list[Input]:
        idx, atlas, reqs = self._subject(subject, env)
        rec = idx["atlases"][atlas][1]
        ins = [_store_input("atlas", rec)]
        entries = textures.atlas_entries(self._docs.get(env.depot, rec["content"]["sha256"]))
        needed = set()
        for owner, _tid, meta in reqs:
            ins.append(_store_input("sprite:" + owner, meta))
            doc = self._docs.get(env.depot, meta["content"]["sha256"])
            entry = textures.atlas_entry(entries, doc["sprite"].get("m_RenderDataKey"))
            if entry is not None:
                needed.update(
                    t for t in (ref_object_id(entry.get("texture")), ref_object_id(entry.get("alphaTexture"))) if t
                )
        ins += [_store_input("texture:" + t, idx["textures"][t][1]) for t in sorted(needed) if t in idx["textures"]]
        return ins

    def uses(self, subject: str, env) -> dict:
        return dict(self.ATOMS)

    def depends(self, subject: str, env) -> list[str]:
        idx, atlas, reqs = self._subject(subject, env)
        return sorted({idx["atlases"][atlas][0], *(tid for _o, tid, _m in reqs)})

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        tex = sum(i.size for i in inputs if i.role.startswith("texture:"))
        n = sum(1 for i in inputs if i.role.startswith("sprite:"))
        return Cost(0.05 + n * 0.01 + tex * 5e-8, (72 << 20) + 40 * tex)

    def run(self, task, depot) -> Output:
        from PIL import Image

        _check_atoms(task)
        crop, encode = resolve("sprite.crop").fn, resolve("png.encode").fn
        level = int(task.params["png"]["level"])
        entries = textures.atlas_entries(contract.loads(depot.input_bytes(task.input("atlas"))))
        images = {}

        def image(oid: str):
            if oid not in images:
                try:
                    inp = task.input("texture:" + oid)
                except KeyError:
                    raise Unsupported("no_data.sprite", f"no image of the atlas texture {oid}") from None
                im = Image.open(io.BytesIO(depot.input_bytes(inp)))
                im.load()
                images[oid] = im
            return images[oid]

        arts, items = [], []
        for inp in task.inputs:
            if not inp.role.startswith("sprite:"):
                continue
            oid = inp.role[len("sprite:") :]
            meta = contract.loads(depot.input_bytes(inp))
            try:
                img = textures.crop_meta(meta, entries, image, crop)
                aid = contract.artifact_id(oid, "image")
                arts.append(
                    contract.artifact(
                        aid,
                        depot.add(encode(img, level), "png"),
                        contract.provenance(task, obj=meta.get("object")),
                        {
                            "kind": "sprite.image",
                            "format": "png",
                            "facts": {"width": img.width, "height": img.height, "mode": img.mode},
                        },
                    )
                )
                items.append(contract.item(oid, "exported", artifacts=[aid], cls="Sprite"))
            except Unsupported as e:
                items.append(
                    contract.item(oid, "unsupported", why=contract.reason(e.code, label(e.message)), cls="Sprite")
                )
            except Exception as e:
                items.append(
                    contract.item(oid, "failed", why=contract.reason("failed.export", message(e)), cls="Sprite")
                )
        return Output(arts, items)


STAGES = (ExportStage, SpriteCropStage)
