"""Stages `unity.export` (one unit of a bundle -> the artifacts of its objects) and `sprite.crop` (the images of the
sprites whose atlas is in another bundle).

unity.export: one task per unit (pipeline.units: a hierarchy, or an object outside any hierarchy) of a request. A
task reads its bundle alone (atom `reader`) and runs the converters of nnnotes.unity.convert over its unit, giving
every object of the unit exactly one item (contract.item): `exported` (its artifacts), `contained` (an object of a
hierarchy, in its root's prefab), `generic` (the object's typetree JSON, with the reason its converter gave for not
making the specialized form), `unsupported` (a class this stage does not convert; its typetree JSON is written all
the same) or `failed` (not even the typetree JSON could be made). A converter's documented limit
(atoms.Unsupported) gives the typetree JSON with its reason code; any other error of a converter gives it with
`generic.error` and the error's message. The reason codes are listed in docs/stages.md. A task's key holds the
bundle, the unit's root object, the converters of the classes of its objects and the script entries and shipped
type trees they use, so a unit converts again only when one of those changes.

The tasks of one bundle are one batch (Stage.batch): a worker loads the bundle once for them and keeps, until the
batch ends, what depends on the bundle alone: the index of its objects, its hierarchies, its atlases' render data
and the last textures it decoded. A unit's result depends on neither the other units of its batch nor their order.
A sprite cut from a texture of its bundle reads the texture's decoded image; a hierarchy that cannot be walked
gives each of its objects the typetree JSON with the reason.

sprite.crop: a sprite whose SpriteAtlas is in another bundle gets its `json` from unity.export (with the sprite's
mesh and object facts) and no image; one sprite.crop task per atlas then reads the atlas' JSON, the images of its
textures and those sprites' documents, and makes "<sprite object id>#image" exactly as unity.export makes the image
of a sprite whose atlas is in its own bundle.
"""

from __future__ import annotations

import io
from collections import OrderedDict, defaultdict

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
    BundleIndex,
    BundleObjects,
    ReadError,
    label,
    message,
    ref_object_id,
)
from . import builtins, contract, link, units
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Pending, Stage

EXPORT, CROP, CENSUS, SCRIPTS = "unity.export", "sprite.crop", "unity.census", "link.scripts"
SCRIPTS_TASK = contract.task_id(SCRIPTS, "all")
ATOM_NAMES = frozenset(ATOM_REGISTRY)
# class -> (converter, function) of the classes converted by one function alone
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
DECODED_PIXELS = 1 << 25  # the decoded textures a batch keeps: the last ones, up to this many pixels


# ---------------------------------------------------------------- one bundle in a worker
class _Bundle:
    """What the tasks of one bundle share in a worker: the bundle's view and BundleIndex, its hierarchies (valid
    roots by GameObject key, and the objects of the hierarchies that cannot be walked with the reason), its atlases'
    render data and the last decoded textures."""

    def __init__(self, sha: str, view):
        self.sha, self.view = sha, view
        self.index = BundleIndex(view)
        self.atlases: dict = {}
        self._h = None
        self.roots: dict[tuple, prefab.Root] = {}
        self.broken: dict[tuple, str] = {}
        self._decoded: OrderedDict = OrderedDict()
        self._pixels = 0

    def hierarchies(self, ctx: BundleObjects):
        if self._h is None:
            h = prefab.hierarchies(ctx)
            for root in h.roots:
                go = (root.file, root.game_object)
                info = self.index.info.get(go)
                if root.error is None and info is not None and info.class_name == "GameObject":
                    self.roots[go] = root
            for root in h.roots:
                if (root.file, root.game_object) in self.roots:
                    continue
                why = root.error or f"transform {root.transform}: no GameObject"
                for k in root.members:
                    if k in self.index.info and k not in h.member:
                        self.broken.setdefault(k, f"hierarchy at transform {root.transform}: {why}")
            self._h = h
        return self._h

    def decoded(self, ctx: BundleObjects, info) -> dict:
        """A texture's decoded image or the error (textures.decode_into), kept while it is among the last
        DECODED_PIXELS pixels decoded."""
        k = (info.file, info.path_id)
        holder = self._decoded.get(k)
        if holder is not None:
            self._decoded.move_to_end(k)
            return holder
        holder = {}
        textures.decode_into(ctx, info, holder)
        img = holder.get("image")
        self._decoded[k] = holder
        self._pixels += img.width * img.height if img is not None else 0
        while self._pixels > DECODED_PIXELS and len(self._decoded) > 1:
            _, old = self._decoded.popitem(last=False)
            img = old.get("image")
            self._pixels -= img.width * img.height if img is not None else 0
        return holder


# ---------------------------------------------------------------- one unit
class UnitRun:
    """unity.export on one unit: its converters over its objects, one item per object, the artifacts in the
    depot."""

    def __init__(self, bundle: _Bundle, task, depot, fns: dict):
        self.task, self.depot, self.bundle = task, depot, bundle
        self.ctx = BundleObjects(
            bundle.view,
            fns,
            scripts=task.context.get("scripts", {}),
            blob_min=int(task.params["blobMin"]),
            png_level=int(task.params["png"]["level"]),
            emit=self._emit,
            typetrees=task.context.get("typetrees", {}),
            index=bundle.index,
        )
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

    # ---------------------------------------------------------------- the unit
    def run(self) -> tuple[list, list]:
        key = contract.parse_object_id(self.task.context["unit"])
        info = self.ctx.info.get(key)
        if info is None:
            raise IncompatibleTask(f"task {self.task.id}: object {self.task.context['unit']} is not in the bundle")
        cls = info.class_name
        self.bundle.hierarchies(self.ctx)
        simple = {c: (conv, fn) for c, conv, fn in _SIMPLE}
        if key in self.bundle.broken:
            self._fallback(info, "generic.error", self.bundle.broken[key])
        elif key in self.bundle.roots:
            self._prefab(info, self.bundle.roots[key])
        elif cls == "Sprite":
            self._sprite(info)
        elif cls == "Texture2D":
            self._convert(info, "tex.png", textures.texture, self.bundle.decoded(self.ctx, info))
        elif cls in simple:
            self._convert(info, *simple[cls])
        elif cls == "MonoBehaviour":
            self._convert(info, "mono.json", mono.mono, usm_assets=self.usm_assets)
        elif cls in UNSUPPORTED_CLASSES:
            self._fallback(info, f"unsupported.class.{cls}", f"{cls} is not converted", status="unsupported")
        else:
            self._generic(info)
        return self.artifacts, list(self.items.values())

    def _sprite(self, info) -> None:
        self._need("sprite.png")
        self._begin("sprite.png")
        try:
            plan = textures.sprite_plan(self.ctx, (info.file, info.path_id), self.bundle.atlases)
        except Unsupported as e:
            self._fallback(info, e.code, e.message)
            return
        except Exception as e:
            self._fallback(info, "generic.error", message(e))
            return
        if plan["external"]:
            self._convert(info, "sprite.png", textures.sprite_external, plan, CROP)
        else:
            holder = self.bundle.decoded(self.ctx, self.ctx.info[plan["texture"]])
            self._convert(info, "sprite.png", textures.sprite, plan, holder)

    def _prefab(self, info, root: prefab.Root) -> None:
        h = self.bundle.hierarchies(self.ctx)
        members = [self.ctx.info[k] for k in root.members if k != (info.file, info.path_id)]
        if self._convert(info, "prefab.json", prefab.prefab, h, root):
            within = contract.artifact_id(info.id, "json")
            for i in members:
                self.items[i.id] = contract.item(i.id, "contained", within=within, cls=i.class_name)
            return
        why = self.items[info.id].get("reason") or {"code": "generic.error", "message": ""}
        for i in members:
            self._fallback(i, why["code"], f"hierarchy of {info.id}: {why['message']}")


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
    """A few JSON documents of the depot by content id (the planning reads some several times)."""

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
    """unity.export: one unit -> the artifacts of its objects, one item per object (see the module documentation).

    Subjects: the units of the request (pipeline.units.table: the facts "selected", "bundles" and "classes", and
    the censuses of the bundles). Inputs: the unit's bundle ("bundles" {stable name: Input (role "bundle")}, as
    unity.census). It reads the script table (link.scripts:all) for the entries of the MonoScripts its objects
    name. Context: "unit" (the root object's id), "bundle" (the stable bundle name), "scripts" {object id:
    "assembly|namespace|class"} and, for the MonoBehaviours of files that store no type trees (the APK's built-in
    files), "typetrees" {type hash: nodes}: the type trees nnnotes ships for their type hashes. Parameters:
    `blobMin` (bytes; byte arrays of this size or more are blobs), `png.level` (zlib level of the PNG files).
    `reader`: another implementation of the reader atom (its id enters the keys). Batch: the bundle's content id."""

    name = EXPORT
    version = 2
    after = (CENSUS, SCRIPTS)
    PARAMS = {"blobMin": 4096, "png": {"level": 6}}

    def __init__(self, reader: Impl | None = None):
        self.reader = reader
        self._docs = _Docs()
        self._atoms_memo: dict[tuple, dict] = {}
        self._loaded: _Bundle | None = None

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
        return {"blobMin": blob, "png": _png_params(p.get("png"))}

    def subjects(self, env) -> list[str]:
        return units.table(env).subjects()

    def unit(self, subject: str, env) -> units.Unit:
        return units.table(env).unit(subject)

    def inputs(self, subject: str, env) -> list[Input]:
        u = self.unit(subject, env)
        inp = env.fact("bundles")[u.bundle]
        if inp.role != "bundle":
            raise ValueError(f"{self.name} {subject}: input role {inp.role!r}, expected 'bundle'")
        return [inp]

    def uses(self, subject: str, env) -> dict:
        """The atoms and converters of the classes of the unit's objects; converter cri.usm when one of them is a
        movie asset (its script context names the class)."""
        u = self.unit(subject, env)
        usm = (
            "MonoBehaviour" in u.classes and USM_ASSET_SCRIPT in self.context(subject, env).get("scripts", {}).values()
        )
        k = (u.classes, usm)
        atoms = self._atoms_memo.get(k)
        if atoms is None:
            atoms = export_atoms(u.classes, self.reader_id())
            if usm:
                atoms = dict(sorted({**atoms, USM_CONVERTER: converter_id(USM_CONVERTER)}.items()))
            self._atoms_memo[k] = atoms
        return atoms

    def context(self, subject: str, env) -> dict:
        u = self.unit(subject, env)
        out: dict = {"unit": u.root, "bundle": u.bundle}
        if u.scripts:
            table = self._docs.get(env.depot, env.input_of(SCRIPTS_TASK, "scripts").sha256)["scripts"]
            scripts = link.script_context(table, u.scripts)
            if scripts:
                out["scripts"] = scripts
        trees = {}
        for version, h in u.trees:
            nodes = scripttypes.script_types(version).get(h)
            if nodes is not None:
                trees[h] = nodes
        if trees:
            out["typetrees"] = dict(sorted(trees.items()))
        return out

    def depends(self, subject: str, env) -> list[str]:
        return [contract.task_id(CENSUS, self.unit(subject, env).bundle), SCRIPTS_TASK]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        """CPU: the unit's share of loading its bundle (from the bundle's size, among the requested units of the
        bundle) and its own (its objects and their bytes); peak: the worker's for the whole bundle (from its size
        and object count), which the tasks of its batch hold together."""
        u = self.unit(subject, env)
        t = units.table(env)
        size = sum(i.size for i in inputs)
        n = t.objects.get(u.bundle, 0)
        load = 0.12 + size * 3.3e-7
        cpu = load / max(1, t.per_bundle[u.bundle]) + len(u.members) * 8.8e-5 + u.size * 6.6e-8
        return Cost(cpu, (80 << 20) + 80 * size + 17500 * n)

    def batch(self, task) -> str:
        return task.input("bundle").sha256

    def end_batch(self, batch: str) -> None:
        if self._loaded is not None and self._loaded.sha == batch:
            self._loaded = None

    def run(self, task, depot) -> Output:
        reader = self.reader if self.reader is not None else resolve("reader")
        _check_atoms(task, reader)
        inp = task.input("bundle")
        if self._loaded is None or self._loaded.sha != inp.sha256:
            self._loaded = None
            name = builtins.serialized_name(task.context["bundle"])
            self._loaded = _Bundle(inp.sha256, reader.fn(depot.input_bytes(inp), name))
        fns = {n: resolve(n).fn for n in task.atoms if n in ATOM_NAMES and n != "reader"}
        arts, items = UnitRun(self._loaded, task, depot, fns).run()
        return Output(arts, items)


def _store_input(role: str, record: dict) -> Input:
    c = record["content"]
    return Input(role, c["sha256"], c["size"], None, ({"kind": "depot"},))


def _artifact(env, tid: str, aid: str) -> dict | None:
    return next((a for a in env.result(tid)["artifacts"] if a["id"] == aid), None)


class SpriteCropStage(Stage):
    """sprite.crop: the images of the sprites whose SpriteAtlas is in another bundle. One task per atlas, subject the
    unity.export subject of the atlas' unit; inputs: "atlas" (the atlas' JSON), "sprite:<object id>" (each sprite's
    JSON), "texture:<object id>" (the image of each atlas texture the sprites use), all artifacts of unity.export
    results. The sprites are those of the unity.export tasks of Sprite units whose JSON names an atlas of another
    bundle; waits for unity.export tasks that have not run. Artifacts "<sprite object id>#image"."""

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

    def index(self, env) -> dict:
        """{subjects: {atlas subject: (atlas object id, [(sprite object id, export task id, its JSON record)])},
        missing: atlas object ids that sprites name but no unity.export result holds}."""
        pending = env.pending(EXPORT)
        if pending:
            raise Pending(pending[0])
        table = units.table(env)
        tids = []
        for t in env.tasks(EXPORT):
            u = table.units.get(contract.parse_task_id(t, EXPORT)[1])
            if u is not None and u.cls == "Sprite":
                tids.append(t)
        sig = (id(table), tuple(env.key(t) for t in tids))
        if self._memo is not None and self._memo[0] == sig:
            return self._memo[1]
        requests = defaultdict(list)
        for tid in tids:
            for a in env.result(tid)["artifacts"]:
                facts = a["semantics"].get("facts") or {}
                if a["semantics"]["kind"] == "sprite.meta" and facts.get("crop") == CROP:
                    requests[facts["atlas"]].append((contract.parse_artifact_id(a["id"])[0], tid, a))
        subjects, missing = {}, []
        for atlas, reqs in sorted(requests.items()):
            subject = table.subject_of(atlas)
            if subject is None or not env.known(contract.task_id(EXPORT, subject)):
                missing.append(atlas)
                continue
            try:
                env.key(contract.task_id(EXPORT, subject))
            except Pending:
                missing.append(atlas)
                continue
            subjects[subject] = (atlas, sorted(reqs, key=lambda r: r[0]))
        idx = {"subjects": subjects, "missing": missing}
        self._memo = (sig, idx)
        return idx

    def subjects(self, env) -> list[str]:
        return sorted(self.index(env)["subjects"])

    def _subject(self, subject: str, env):
        idx = self.index(env)
        if subject not in idx["subjects"]:
            raise ValueError(f"{self.name}: no sprites wait for atlas {subject}")
        return idx["subjects"][subject]

    def _textures(self, subject: str, env) -> tuple[dict, list]:
        """(the atlas' JSON record, [(texture object id, the export task holding it)]) of the textures the
        sprites use."""
        atlas, reqs = self._subject(subject, env)
        rec = _artifact(env, contract.task_id(EXPORT, subject), contract.artifact_id(atlas, "json"))
        if rec is None:
            raise ValueError(f"{self.name} {subject}: the atlas {atlas} has no JSON")
        entries = textures.atlas_entries(self._docs.get(env.depot, rec["content"]["sha256"]))
        needed = set()
        for _owner, _tid, meta in reqs:
            doc = self._docs.get(env.depot, meta["content"]["sha256"])
            entry = textures.atlas_entry(entries, doc["sprite"].get("m_RenderDataKey"))
            if entry is not None:
                needed.update(
                    t for t in (ref_object_id(entry.get("texture")), ref_object_id(entry.get("alphaTexture"))) if t
                )
        table = units.table(env)
        out = []
        for t in sorted(needed):
            s = table.subject_of(t)
            if s is not None and env.known(contract.task_id(EXPORT, s)):
                out.append((t, contract.task_id(EXPORT, s)))
        return rec, out

    def inputs(self, subject: str, env) -> list[Input]:
        _atlas, reqs = self._subject(subject, env)
        rec, texs = self._textures(subject, env)
        ins = [_store_input("atlas", rec)]
        ins += [_store_input("sprite:" + owner, meta) for owner, _tid, meta in reqs]
        for t, tid in texs:
            try:
                img = _artifact(env, tid, contract.artifact_id(t, "image"))
            except Pending:
                continue
            if img is not None and img["semantics"]["kind"] == "texture.image":
                ins.append(_store_input("texture:" + t, img))
        return ins

    def uses(self, subject: str, env) -> dict:
        return dict(self.ATOMS)

    def depends(self, subject: str, env) -> list[str]:
        _atlas, reqs = self._subject(subject, env)
        _rec, texs = self._textures(subject, env)
        return sorted({contract.task_id(EXPORT, subject), *(tid for _o, tid, _m in reqs), *(tid for _t, tid in texs)})

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
