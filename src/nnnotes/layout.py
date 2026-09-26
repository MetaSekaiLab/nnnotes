"""Output layouts: where the artifacts of a run go as files. A layout never changes artifact bytes or keys.

    cas        assets/<sha256>.<ext> for every distinct content, plus manifest.json
    original   the game's names: the container path of each object (in the catalog's case when a catalog location
               matches it case-insensitively), plus manifest.json

`original` path rules:
  - several objects under one container path: the primary object (the class the container's extension names,
    EXT_CLASSES; else the first by file and pathId) gets the path, every other `stem[<name>].<ext>` (the
    Addressables sub-object syntax: .../member_full[square].png); sub-objects whose paths coincide all get
    `~<pathId>` (stem[name]~123.png)
  - an object's primary artifact (contract.PRIMARY_ROLES) is written at that path, its extension appended unless
    the path already ends with it (x.prefab -> x.prefab.json, y.png -> y.png); its other artifacts at
    `<path>.<role>.<ext>` (':' and '/' of the role as '.')
  - objects without a container: `<root>/<Class>/<name>~<pathId>.<ext>`, root = the root of the object's
    serialized file when one is given (`object_roots`: the export command roots every object of a bundle at
    `_bundles/<stable bundle name>`, whichever task made the artifact), else the source's root; under a root whose
    objects come from several serialized files (scene bundles, whose files share path ids)
    `<root>/<file>/<Class>/...`
  - a container path present in several bundles (the same asset in two bundles): each bundle's objects are placed
    as above under `stem@<stable bundle name>.ext`, and the report lists the container
  - artifacts of no object (their provenance names no `object`): `<root>/<role>`, the extension appended unless
    the role ends with it
  - every path segment escapes the characters Windows refuses, '%' itself, trailing dots and spaces, and the
    reserved device names as %XX (unescape_path reverses it)
  - paths that coincide ignoring case (or a file named like another path's directory) keep the first by path and
    id; each other gets `~<first 8 hex of sha256(artifact id)>` before its extension and is reported; artifacts with
    the same path and the same content share the file
So every path ends with its artifact's extension, and `cas` follows from the `original` manifest alone; `original`
follows from `cas` plus the results (the records). Documents a stage derives for `original` from its records and
the paths (the views with the files of their objects) are the exception: `original` has the derived documents,
`cas` the records they are made from.

Writing (materialize): the layout manifest (nnnotes.layout/1) lists the files the layout owns. A run writes a
journal of the files it will write and remove, writes each file atomically (copied or hard-linked from the byte
source), removes the owned files no longer wanted, then writes the manifest and deletes the journal; a run after
an interrupted one owns what the journal lists too. Files the layout does not own are never touched: an unowned
file in the way is an error unless it already has the right content.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import contract
from .cache import temp_path
from .store import write_doc, write_file

MANIFEST = "manifest.json"
JOURNAL = ".nnnotes-layout-journal.json"
LAYOUTS = ("original", "cas")
LINKS = ("copy", "hard")
DEFAULT_ROOT = "_tasks/{stage}/{subject}"
# container extension -> the classes of the object that owns the container path (lower case, without the dot)
EXT_CLASSES = {
    **dict.fromkeys(("png", "jpg", "jpeg", "tga", "psd", "exr", "hdr", "tif", "tiff", "bmp", "gif", "iff", "pict"),
                    ("Texture2D",)),
    **dict.fromkeys(("prefab", "fbx", "obj", "blend", "dae", "3ds", "max", "ma", "mb"), ("GameObject",)),
    **dict.fromkeys(("txt", "bytes", "json", "csv", "xml", "html", "htm", "yaml", "yml", "md", "fnt"),
                    ("TextAsset",)),
    **dict.fromkeys(("wav", "mp3", "ogg", "aif", "aiff", "flac", "m4a"), ("AudioClip",)),
    **dict.fromkeys(("mp4", "mov", "webm", "avi", "m4v", "asf", "wmv", "mpg", "mpeg", "ogv", "vp8"), ("VideoClip",)),
    **dict.fromkeys(("ttf", "otf", "ttc", "fontsettings"), ("Font",)),
    **dict.fromkeys(("spriteatlas", "spriteatlasv2"), ("SpriteAtlas",)),
    "mat": ("Material",), "anim": ("AnimationClip",), "controller": ("AnimatorController",),
    "overridecontroller": ("AnimatorOverrideController",), "shader": ("Shader",),
    "shadervariants": ("ShaderVariantCollection",), "mesh": ("Mesh",), "mask": ("AvatarMask",),
    "physicmaterial": ("PhysicMaterial",), "cubemap": ("Cubemap",), "rendertexture": ("RenderTexture",),
    "asset": ("MonoBehaviour",), "playable": ("MonoBehaviour",), "unity": ("SceneAsset",),
}
_BAD = set('<>:"/\\|?*%') | {chr(c) for c in range(32)} | {"\x7f"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10)),
             "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


class LayoutError(RuntimeError):
    """A layout cannot be written as asked (a file it does not own is in the way)."""


# ---------------------------------------------------------------- escaping
def escape_segment(s: str) -> str:
    """One path segment as a name every common file system accepts (reversible with unescape_path)."""
    out = "".join(f"%{ord(c):02X}" if c in _BAD else c for c in s)
    body = out.rstrip(". ")
    tail = out[len(body):]
    out = body + "".join(f"%{ord(c):02X}" for c in tail)
    if out.split(".", 1)[0].upper() in _RESERVED:
        out = f"%{ord(out[0]):02X}" + out[1:]
    return out


def escape_path(segments) -> str:
    return "/".join(escape_segment(s) for s in segments if s != "")


def unescape_path(path: str) -> str:
    """The raw text of an escaped path ('/' inside a segment comes back as '/')."""
    out, i = [], 0
    while i < len(path):
        c = path[i]
        if c == "%" and _is_hex(path[i + 1:i + 3]):
            out.append(chr(int(path[i + 1:i + 3], 16)))
            i += 3
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _is_hex(s: str) -> bool:
    return len(s) == 2 and all(c in "0123456789abcdefABCDEF" for c in s)


# ---------------------------------------------------------------- entries
@dataclass
class Source:
    """The result of one task as layout input. `root`: the directory of its artifacts that have no container
    (default DEFAULT_ROOT)."""
    task: str
    result: dict
    root: str | None = None

    def root_segments(self) -> list[str]:
        stage, subject = contract.parse_task_id(self.task)
        root = self.root if self.root is not None else DEFAULT_ROOT.format(stage=stage, subject=subject)
        return [s for s in root.split("/") if s]


def _entry(path: str, record: dict) -> dict:
    c = record["content"]
    return {"path": path, "sha256": c["sha256"], "size": c["size"], "id": record["id"]}


def cas_entries(sources, extra=()) -> list[dict]:
    """The entries of the `cas` layout: assets/<sha256>.<ext> per artifact (artifacts of equal content share it)."""
    out = [dict(e) for e in extra]
    for s in sources:
        for a in s.result["artifacts"]:
            out.append(_entry(f"assets/{a['content']['sha256']}.{a['content']['ext']}", a))
    return sorted(out, key=lambda e: (e["path"], e["id"]))


def cas_from_manifest(doc: dict) -> list[dict]:
    """The `cas` entries of any layout manifest (its paths end with the artifacts' extensions; an artifact placed
    at several paths is one cas entry)."""
    out = {(f"assets/{e['sha256']}.{e['path'].rsplit('.', 1)[1].lower()}", e["id"]): e for e in doc["entries"]}
    return [{"path": p, "sha256": e["sha256"], "size": e["size"], "id": i} for (p, i), e in sorted(out.items())]


def _with_ext(segments: list[str], ext: str) -> list[str]:
    """The segments with `.ext` appended to the last unless it already ends with it (case-insensitively)."""
    last = segments[-1]
    if last.lower().endswith("." + ext.lower()):
        return segments
    return segments[:-1] + [f"{last}.{ext}"]


def _role_segment(role: str) -> str:
    return role.replace(":", ".").replace("/", ".")


def _split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, "." + ext) if dot and stem else (name, "")


def original_entries(sources, *, case: dict | None = None, bundles: dict | None = None,
                     object_roots: dict | None = None, extra=()) -> tuple[list[dict], dict]:
    """The entries of the `original` layout and its report: {"collisions": [{path, id, renamed}], "shared":
    [{container, sources}]} (container paths present in more than one bundle). `case`: {lower-case container path:
    the catalog's spelling}; `bundles`: {serialized file: stable bundle name} (default: the subject of the task
    whose result holds the object); `object_roots`: {serialized file: root} of the objects without a container path
    (default: their source's root); `extra`: ready entries to place too (derived documents). The artifacts of one
    object may come from several results; they are placed together."""
    case, bundles, object_roots = case or {}, bundles or {}, object_roots or {}
    root_files: dict[str, set] = {}
    for f, r in object_roots.items():
        root_files.setdefault(r, set()).add(f)
    placed: list[tuple[list[str], dict]] = [([s for s in e["path"].split("/") if s], dict(e)) for e in extra]
    objects: dict[tuple, dict] = {}              # (file, pathId) -> {obj, records, src}
    for src in sorted(sources, key=lambda s: s.task):
        owners: dict[str, list[dict]] = {}
        for a in src.result["artifacts"]:
            owners.setdefault(contract.parse_artifact_id(a["id"])[0], []).append(a)
        for owner, records in sorted(owners.items()):
            obj = next((r["provenance"]["object"] for r in records if r.get("provenance", {}).get("object")), None)
            if obj is None:                      # artifacts of no object
                for r in records:
                    role = contract.parse_artifact_id(r["id"])[1]
                    segs = src.root_segments() + [s for s in role.split("/") if s]
                    placed.append((_with_ext(segs, r["content"]["ext"]), _entry("", r)))
            else:
                o = objects.setdefault((obj["file"], int(obj["pathId"])), {"obj": obj, "records": [], "src": src})
                o["records"] += records
    files: dict[str, set] = {}                   # task -> serialized files of its objects
    for o in objects.values():
        files.setdefault(o["src"].task, set()).add(o["obj"]["file"])
    groups: dict[str, list] = {}                 # container (folded) -> [(object facts, records, container, bundle)]
    for k in sorted(objects):
        obj, records, src = objects[k]["obj"], objects[k]["records"], objects[k]["src"]
        container = obj.get("container")
        if container:
            container = case.get(container.lower(), container)
            bundle = bundles.get(obj["file"]) or contract.parse_task_id(src.task)[1]
            groups.setdefault(container.casefold(), []).append((obj, records, container, bundle))
        else:
            root = object_roots.get(obj["file"])
            if root is None:
                segs, several = src.root_segments(), len(files[src.task]) > 1   # scene bundles: shared path ids
            else:
                segs, several = [s for s in root.split("/") if s], len(root_files[root]) > 1
            name = f"{obj.get('name') or ''}~{obj['pathId']}"
            base = segs + ([obj["file"]] if several else []) + [obj.get("class") or "Object", name]
            placed += _object_files(base, records, append_ext=True)
    shared = []
    for key in sorted(groups):
        members = groups[key]
        labels = sorted({m[3] for m in members})
        if len(labels) == 1:
            placed += _container_files(members)
            continue
        shared.append({"container": min(m[2] for m in members), "sources": labels})
        for label in labels:
            placed += _container_files([m for m in members if m[3] == label], qualifier=label)
    out, report = _resolve(placed)
    report["shared"] = shared
    return out, report


def _container_files(members: list, qualifier: str | None = None) -> list:
    """Files of the objects sharing one container path (see the module documentation); `qualifier`: the source
    label that tells apart the same container path in several sources (stem@<label>.ext)."""
    container = min(m[2] for m in members)
    segs = [s for s in container.split("/") if s]
    stem, ext = _split_ext(segs[-1])
    if qualifier is not None:
        stem = f"{stem}@{qualifier}"
        segs = segs[:-1] + [stem + ext]
    classes = EXT_CLASSES.get(ext[1:].lower(), ())
    order = sorted(members, key=lambda m: (m[0].get("class") not in classes, m[0]["file"], int(m[0]["pathId"])))
    out = _object_files(segs, order[0][1], append_ext=False)
    subs: dict[str, list] = {}
    for obj, records, *_ in order[1:]:
        name = obj.get("name") or ""
        last = f"{stem}[{name}]{ext}" if name else f"{stem}~{obj['pathId']}{ext}"
        subs.setdefault(last.casefold(), []).append((last, obj, records))
    for _, same in sorted(subs.items()):
        for last, obj, records in same:
            if len(same) > 1:
                s, e = _split_ext(last)
                last = f"{s}~{obj['pathId']}{e}"
            out += _object_files(segs[:-1] + [last], records, append_ext=False)
    return out


def _object_files(base: list[str], records: list[dict], append_ext: bool) -> list:
    """(segments, entry) of one object's artifacts: the primary at base (+ extension), the others beside it."""
    primary = contract.primary_artifact(records)
    out = []
    for r in sorted(records, key=lambda r: r["id"]):
        ext = r["content"]["ext"]
        if r is primary:
            segs = base[:-1] + [f"{base[-1]}.{ext}"] if append_ext else _with_ext(base, ext)
        else:
            role = contract.parse_artifact_id(r["id"])[1]
            segs = base[:-1] + [f"{base[-1]}.{_role_segment(role)}.{ext}"]
        out.append((segs, _entry("", r)))
    return out


def _suffixed(path: str, aid: str) -> str:
    head, _, last = path.rpartition("/")
    s, e = _split_ext(last)
    last = f"{s}~{contract.sha256(aid.encode('utf-8'))[:8]}{e}"
    return f"{head}/{last}" if head else last


def _resolve(placed: list) -> tuple[list[dict], dict]:
    """Escaped paths, with collisions (ignoring case; file versus directory; the reserved names) renamed."""
    entries = []
    for segs, e in placed:
        e["path"] = escape_path(segs)
        entries.append(e)
    collisions = []
    for _ in range(8):
        entries.sort(key=lambda e: (e["path"], e["id"]))
        taken: dict[str, tuple] = {MANIFEST.casefold(): (None, None), JOURNAL.casefold(): (None, None)}
        dirs = set()
        for e in entries:
            parts = e["path"].casefold().split("/")
            dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
        changed = False
        for e in entries:
            f = e["path"].casefold()
            owner = taken.get(f)
            if f not in dirs and (owner is None or owner == (e["path"], e["sha256"])):
                taken[f] = (e["path"], e["sha256"])
                continue
            new = _suffixed(e["path"], e["id"])
            collisions.append({"path": e["path"], "id": e["id"], "renamed": new})
            e["path"] = new
            changed = True
        if not changed:
            break
    else:
        raise LayoutError("path collisions do not resolve")
    exact: dict[str, str] = {}
    for e in entries:                            # one path, one content (equal case: shared file)
        if exact.setdefault(e["path"], e["sha256"]) != e["sha256"]:
            raise LayoutError(f"two contents at {e['path']}")
    return sorted(entries, key=lambda e: (e["path"], e["id"])), {"collisions": sorted(
        collisions, key=lambda c: (c["path"], c["id"]))}


def entries(name: str, sources, **kw) -> tuple[list[dict], dict]:
    """The entries and report of layout `name`."""
    if name == "cas":
        return cas_entries(sources, kw.get("extra", ())), {"collisions": [], "shared": []}
    if name == "original":
        return original_entries(sources, **kw)
    raise ValueError(f"unknown layout {name!r} (one of {', '.join(LAYOUTS)})")


def paths(entries_: list[dict]) -> dict[str, str]:
    """{artifact id: path} of layout entries."""
    return {e["id"]: e["path"] for e in entries_}


# ---------------------------------------------------------------- writing
def from_store(store):
    """A byte source reading the store's cas/."""
    return store.path


def from_layout(directory):
    """A byte source reading another layout's directory through its manifest."""
    d = Path(directory)
    doc = read_manifest(d)
    if doc is None:
        raise LayoutError(f"{d} has no layout manifest")
    by_sha = {}
    for e in doc["entries"]:
        by_sha.setdefault(e["sha256"], d / e["path"])
    return lambda sha: by_sha[sha]


def read_manifest(directory) -> dict | None:
    try:
        return contract.loads((Path(directory) / MANIFEST).read_bytes())
    except OSError:
        return None


def _files(entries_: list[dict]) -> dict[str, tuple[str, int]]:
    files: dict[str, tuple[str, int]] = {}
    for e in entries_:
        if files.setdefault(e["path"], (e["sha256"], e["size"])) != (e["sha256"], e["size"]):
            raise LayoutError(f"two contents at {e['path']}")
    return files


def _owned(out: Path) -> tuple[dict[str, tuple[str, int]], set[str]]:
    old = read_manifest(out)
    owned = {e["path"]: (e["sha256"], e["size"]) for e in old["entries"]} if old else {}
    journal = set()
    try:
        j = contract.loads((out / JOURNAL).read_bytes())
        journal = set(j.get("write", [])) | set(j.get("remove", []))
    except (OSError, ValueError):
        pass
    return owned, journal


def _present(p: Path, size: int) -> bool:
    try:
        return p.stat().st_size == size
    except OSError:
        return False


def diff(out_dir, entries_: list[dict]) -> dict:
    """What materialize would do in `out_dir`: {"write", "remove", "keep"} file counts."""
    out = Path(out_dir)
    files = _files(entries_)
    owned, journal = _owned(out)
    write = sum(1 for p, v in files.items() if owned.get(p) != v or not _present(out / p, v[1]))
    remove = len((set(owned) | journal) - set(files))
    return {"write": write, "remove": remove, "keep": len(files) - write}


def materialize(out_dir, name: str, params: dict, entries_: list[dict], source, *, link: str = "copy") -> dict:
    """Write layout `name` into `out_dir` (see the module documentation). `source(sha256) -> Path` gives the bytes
    (from_store, from_layout); `link`: copy the files, or hard-link them (a file that cannot be linked is copied
    and counted). -> {"manifest": the layout document, "sha256": of its file, "write", "remove", "keep",
    "linked", "copied"}."""
    if link not in LINKS:
        raise ValueError(f"unknown link mode {link!r}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc = contract.layout_doc(name, params, entries_)
    files = _files(doc["entries"])
    owned, journal = _owned(out)
    mine = set(owned) | journal
    write = sorted(p for p, v in files.items() if owned.get(p) != v or not _present(out / p, v[1]))
    remove = sorted(mine - set(files))
    blocked = [p for p in write if p not in mine and (out / p).exists() and not _same(out / p, files[p])]
    if blocked:
        raise LayoutError(f"{len(blocked)} files in {out} are not this layout's: {', '.join(blocked[:5])}")
    write_file(out / JOURNAL, contract.encode({"write": write, "remove": remove}))
    stats = {"linked": 0, "copied": 0}
    for p in write:
        sha, size = files[p]
        dst = out / p
        if not (p not in mine and _present(dst, size) and _same(dst, files[p])):
            _place(Path(source(sha)), dst, link, stats)
    for p in remove:
        _remove(out, p)
    sha = write_doc(out / MANIFEST, doc)
    (out / JOURNAL).unlink(missing_ok=True)
    return {"manifest": doc, "sha256": sha, "write": len(write), "remove": len(remove),
            "keep": len(files) - len(write), **stats}


def _same(p: Path, v: tuple[str, int]) -> bool:
    if not _present(p, v[1]):
        return False
    return contract.sha256(p.read_bytes()) == v[0]


def _place(src: Path, dst: Path, link: str, stats: dict) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path(dst)
    try:
        if link == "hard":
            try:
                os.link(src, tmp)
                stats["linked"] += 1
            except OSError:
                shutil.copyfile(src, tmp)
                stats["copied"] += 1
        else:
            shutil.copyfile(src, tmp)
            stats["copied"] += 1
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _remove(out: Path, rel: str) -> None:
    p = out / rel
    p.unlink(missing_ok=True)
    d = p.parent
    while d != out and out in d.parents:
        try:
            d.rmdir()                            # only when empty
        except OSError:
            break
        d = d.parent
