"""Serialized UI library for ournotes-player/ui, from user-supplied game files.

Outputs contain game data and belong outside either source repository. This is
an inspectable prefab preview format, not a claim of Unity runtime parity.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .export import Exporter
from .jsonio import dumps, write_json
from .cache import write_atomic


def identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def object_id(obj) -> str:
    return identity(f"{obj.assets_file.name}:{obj.path_id}")


class UIExporter(Exporter):
    """The generic Exporter plus portable images and stable PPtr identities."""

    def ref(self, owner, pointer):
        out = super().ref(owner, pointer)
        if not isinstance(out, dict):
            return out
        if "gameObject" not in out and "transform" not in out and out.get("object") != "AnimatorController":
            return out
        obj = self.deref(owner, pointer)
        if obj is None:
            return out
        if obj.type.name == "AnimatorController":
            return {**out, "id": object_id(obj)}
        go = obj if obj.type.name == "GameObject" else self.deref(obj, obj.read_typetree()["m_GameObject"])
        return {**out, "nodeId": object_id(go)} if go is not None else out

    def hierarchy(self, env, graph, root_tf):
        nodes = super().hierarchy(env, graph, root_tf)
        transforms = []

        def walk(pid):
            transforms.append(pid)
            for child in graph.tf[pid]["m_Children"]:
                walk(child["m_PathID"])

        walk(root_tf)
        for node, pid in zip(nodes, transforms, strict=True):
            go_pid = graph.tf[pid]["m_GameObject"]["m_PathID"]
            node["nodeId"] = identity(f"{self._go_file[go_pid]}:{go_pid}")
        return nodes

    def sprite(self, obj):
        try:
            return super().sprite(obj)
        except NotImplementedError as exc:
            if "packing rotation" not in str(exc):
                raise
            tt = obj.read_typetree()
            image = obj.read().image.convert("RGBA")
            key = f"{obj.assets_file.name}:{obj.path_id}"
            texture_key = "decoded-sprite:" + key
            if not hasattr(self, "decoded_sprites"):
                self.decoded_sprites = {}
            self.decoded_sprites[texture_key] = image
            self.sprite_recs[key] = {
                "name": tt["m_Name"], "textureRef": texture_key,
                "rect": {"x": 0, "y": 0, "width": image.width, "height": image.height},
                "textureRect": {"x": 0, "y": 0, "width": image.width, "height": image.height},
                "textureRectOffset": {"x": 0, "y": 0}, "border": tt["m_Border"],
                "pivot": tt["m_Pivot"], "pixelsPerUnit": tt["m_PixelsToUnits"], "settingsRaw": 0,
            }
            return {"spriteRef": key, "name": tt["m_Name"]}

    def atlas(self, obj):
        tt = obj.read_typetree()
        return {"atlas": tt["m_Name"], "sprites": [self.sprite(sprite) for ptr in tt.get("m_PackedSprites", [])
                if (sprite := self.deref(obj, ptr)) is not None]}


def _font_metrics(ex, document, out):
    used = {c["m_fontAsset"]["name"] for n in document.get("nodes", []) for c in n["components"]
            if isinstance(c.get("m_fontAsset"), dict) and "VibeMO" in c["m_fontAsset"].get("name", "")}
    if not used:
        return {}
    (out / 'fonts').mkdir(parents=True, exist_ok=True)
    result = {}
    for obj in ex._objects.values():
        if obj.type.name != "MonoBehaviour" or ex.script_class(obj) != "TMP_FontAsset":
            continue
        tt = obj.read_typetree()
        if tt.get("m_Name") not in used:
            continue
        atlas = ex.deref(obj, tt["m_AtlasTextures"][0])
        if atlas is None:
            raise ValueError(f"UI font {tt['m_Name']}: no atlas")
        key = f"{atlas.assets_file.name}:{atlas.path_id}"
        ex.tex_objs[key] = atlas
        chars = {str(c["m_Unicode"]): c["m_GlyphIndex"] for c in tt["m_CharacterTable"] if 32 <= c["m_Unicode"] < 127}
        glyphs = {str(g["m_Index"]): g for g in tt["m_GlyphTable"] if g["m_Index"] in chars.values()}
        rel = f"fonts/{object_id(obj)}.json"
        write_json(out / rel, {"name": tt["m_Name"], "face": tt["m_FaceInfo"], "characters": chars,
                              "glyphs": glyphs, "texture": f"../textures/{identity(key)}.png", "textureBase": "metrics"})
        result[tt['m_Name']] = rel
    if used - result.keys():
        raise ValueError("UI font metrics not present in the loaded dependency closure")
    return result


def resources(ex, document, out: Path, written=None) -> dict:
    written = written if written is not None else set()
    result = {"textures": {}, "sprites": {}, "fonts": {}}
    metrics = _font_metrics(ex, document, out)
    if metrics:
        result["fontMetrics"] = next(iter(metrics.values()))
        result["fontMetricsByAsset"] = metrics
    (out / "textures").mkdir(parents=True, exist_ok=True)
    for key, obj in ex.tex_objs.items():
        rel = f"textures/{identity(key)}.png"
        if rel not in written or not (out / rel).is_file():
            obj.read().image.save(out / rel, format="PNG")
            written.add(rel)
        result["textures"][key] = rel
    for key, sprite in ex.sprite_recs.items():
        texture_key = sprite.get("textureRef")
        if texture_key is None:
            texture_key = f"{sprite['tex'].assets_file.name}:{sprite['tex'].path_id}"
        result["sprites"][key] = {**{k: v for k, v in sprite.items() if k != "tex"}, "textureRef": texture_key}
    for key, image in getattr(ex, "decoded_sprites", {}).items():
        rel = f"textures/{identity(key)}.png"
        if rel not in written or not (out / rel).is_file():
            image.save(out / rel, format="PNG")
            written.add(rel)
        result["textures"][key] = rel
    for obj in ex._objects.values():
        if obj.type.name != "Font":
            continue
        font = obj.read()
        if font.m_FontData:
            rel = f"fonts/{object_id(obj)}.ttf"
            (out / "fonts").mkdir(exist_ok=True)
            if rel not in written or not (out / rel).is_file():
                (out / rel).write_bytes(bytes(font.m_FontData))
                written.add(rel)
            result["fonts"][font.m_Name] = rel
    return result


def _record(out, ident, name, kind, document, resource_table, directory, **extra):
    rel = f"{directory}/{ident}.json"
    write_json(out / rel, {"schema": 1, "format": "ournotes-ui-pack", "resourceBase": "../",
                          "document": document, "resources": resource_table})
    nodes = document.get("nodes", [])
    files = {rel, *resource_table.get('textures', {}).values(), *resource_table.get('fonts', {}).values(),
             *resource_table.get('fontMetricsByAsset', {}).values()}
    if resource_table.get('fontMetrics'):
        files.add(resource_table['fontMetrics'])
    return {"id": ident, "name": name, "kind": kind, "file": rel, "status": "exported",
            "sha256": hashlib.sha256((out / rel).read_bytes()).hexdigest(), "node_count": len(nodes),
            "classes": dict(Counter(c.get("class", c["type"]) for n in nodes for c in n["components"])),
            "files": {name: hashlib.sha256(_output_file(out, name).read_bytes()).hexdigest() for name in sorted(files)},
            "runtime_verified": False, **extra}


def _output_file(out, name):
    file = (out / name).resolve()
    if not file.is_relative_to(out):
        raise ValueError("UI index contains a file outside its output directory")
    return file


def _valid_record(out, record):
    files = record.get('files') if record else None
    if not files or record.get('status') != 'exported':
        return False
    if record.get('file') not in files:
        return False
    _output_file(out, record['file'])
    for name, expected in files.items():
        file = _output_file(out, name)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            return False
    return True


def export_library(cat, out, *, player=None, keys=None, prefix="EmbUI/", dependencies=True, force=False, provenance=None):
    """Incrementally export keys, their unregistered prefab roots and controllers.

    Resume verifies packs, dependency packs and every texture/font hash; failures stay explicit in index.json.
    Existing unrelated entries remain present when exporting an additional key.
    """
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for directory in ("packs", "embedded", "controllers", "fonts", "textures"):
        (out / directory).mkdir(exist_ok=True)
    keys = sorted(set(keys if keys is not None else cat.keys(prefix)))
    old = json.loads((out / "index.json").read_text(encoding="utf-8")) if (out / "index.json").is_file() else {}
    provenance = provenance or {'apk_catalog_sha256': hashlib.sha256(cat._sources.get('apk', cat._sources['remote'])).hexdigest()}
    if old and old.get('provenance') != provenance:
        raise ValueError("UI output belongs to different input data; use a separate output directory")
    assets = {e["key"]: e for e in old.get("assets", [])}
    embedded = {e["id"]: e for e in old.get("embedded", [])}
    controllers = {e["id"]: e for e in old.get("controllers", [])}
    written, processed_roots, processed_controllers = set(), set(), set()

    def exporter():
        return UIExporter(cat, out, player=player, textures="deferred", inline_meshes=False,
                          stub_assets=("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet"))

    def save_index():
        rows = sorted(assets.values(), key=lambda e: e["key"])
        index = {"schema": 1, "format": "ournotes-ui-library", "scope": "serialized UI assets and dependency roots",
                 "counts": dict(Counter(e["status"] for e in rows)), "assets": rows,
                 "embedded": sorted(embedded.values(), key=lambda e: e["id"]),
                 "controllers": sorted(controllers.values(), key=lambda e: e["id"]), "runtime_verified": False,
                 "provenance": provenance}
        write_atomic(out / 'index.json', dumps(index, indent=1, ensure_ascii=False).encode('utf-8'))
        return index

    for key in keys:
        previous = assets.get(key)
        if not force and _valid_record(out, previous):
            refs = previous.get('dependencies', {})
            valid_deps = (not dependencies or previous.get('dependency_export') and
                          all(_valid_record(out, embedded.get(i)) for i in refs.get('embedded', [])) and
                          all(_valid_record(out, controllers.get(i)) for i in refs.get('controllers', [])))
            if valid_deps:
                continue
        ident, name = identity(key), key.rsplit("/", 1)[-1]
        try:
            ex = exporter()
            refs = {'embedded': [], 'controllers': []}
            document = ex.export_key(key)
            env, graph = ex.load(key)
            main_roots = {n["nodeId"] for n in document.get("nodes", []) if n["path"].count("/") == 0}
            if dependencies and document.get("nodes"):
                for pid, transform in graph.tf.items():
                    if transform["m_Father"]["m_PathID"]:
                        continue
                    go_pid = transform["m_GameObject"]["m_PathID"]
                    root_id = identity(f"{ex._go_file[go_pid]}:{go_pid}")
                    if root_id in main_roots:
                        continue
                    refs['embedded'].append(root_id)
                    go_name = graph.go[go_pid]["m_Name"]
                    if root_id not in processed_roots and (force or not _valid_record(out, embedded.get(root_id))):
                        doc = {"key": go_name, "nodes": ex.hierarchy(env, graph, pid)}
                        embedded[root_id] = _record(out, root_id, go_name, "prefab", doc, resources(ex, doc, out, written), "embedded", source_key=key)
                        processed_roots.add(root_id)
            if dependencies:
                for obj in env.objects:
                    if obj.type.name != "AnimatorController":
                        continue
                    cid = object_id(obj)
                    refs['controllers'].append(cid)
                    if cid in processed_controllers or not force and _valid_record(out, controllers.get(cid)):
                        continue
                    fresh = exporter()
                    fresh.load(key)
                    owned = fresh._objects[(obj.assets_file.name, obj.path_id)]
                    doc = fresh.controller(owned)
                    controllers[cid] = _record(out, cid, doc["name"], "controller", doc, resources(fresh, doc, out, written), "controllers", source_key=key)
                    processed_controllers.add(cid)
            kind = cat._entry(key)["internal_id"].rsplit(".", 1)[-1]
            assets[key] = _record(out, ident, name, kind, document, resources(ex, document, out, written), "packs", key=key,
                                  dependencies=refs, dependency_export=dependencies)
        except Exception as exc:
            assets[key] = {"id": ident, "name": name, "key": key, "status": "failed", "error_type": type(exc).__name__, "error": str(exc), "runtime_verified": False}
        save_index()
    return save_index()


def command(args, cfg):
    from .cli import bundle_key, player_data
    from .apkset import ApkSet
    from .catalog import Catalog, APK_CATALOG
    from .config import ConfigError
    from .player import MANIFEST_IN_APK, manifest_version_name, manifest_version_code
    if args.limit is not None and args.limit < 1:
        args.usage("--limit must be positive")
    apk = cfg.require_path("paths", "apk")
    with ApkSet(apk) as source:
        catalog_data = source.read(APK_CATALOG)
        members = sorted((i.filename, i.CRC, i.file_size) for i in source.infolist())
        manifest = source.read(MANIFEST_IN_APK) if MANIFEST_IN_APK in source.namelist() else b''
        provenance = {'apk_catalog_sha256': hashlib.sha256(catalog_data).hexdigest(),
                      'apk_members_sha256': hashlib.sha256(dumps(members).encode('utf-8')).hexdigest(),
                      'region': cfg.get('catalog', 'region'),
                      'client': {'versionName': manifest_version_name(manifest), 'versionCode': manifest_version_code(manifest)}}
    cat = Catalog(catalog_data, cfg.require_path("paths", "cache"), bundle_key=lambda: bundle_key(cfg), apk=apk)
    keys = args.key or cat.keys(args.prefix)
    if args.limit is not None:
        keys = keys[:args.limit]
    unknown = sorted(set(keys) - set(cat.keys("")))
    if unknown:
        raise ConfigError("UI key not in the APK catalog: " + ", ".join(unknown))
    try:
        index = export_library(cat, args.out, player=player_data(cfg), keys=keys, dependencies=not args.no_dependencies,
                               force=args.force, provenance=provenance)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    print(json.dumps({"index": str(Path(args.out).resolve() / "index.json"), "counts": index["counts"],
                      "embedded": len(index["embedded"]), "controllers": len(index["controllers"])}))
    if index["counts"].get("failed"):
        raise SystemExit(1)
