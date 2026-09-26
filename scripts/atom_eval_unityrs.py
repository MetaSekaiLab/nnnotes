"""Candidate atom implementations on unity-rs (the `unity-rs` package), for scripts/atom_eval.py only.

Each name below is an `Impl` factory that `atom_eval.py --atom NAME=atom_eval_unityrs:<name>` (or
NNNOTES_ATOM_<NAME>) takes:

    reader               the reference reader (UnityPy) with a unity-rs collection of the same bundle bytes next to
                         it, opened when the first texture is asked for: texture_input carries the unity-rs object,
                         so texture_decode decodes it with unity-rs (both libraries load such a bundle)
    reader_rs            unity-rs alone (for conformance only): files, objects, raw bytes, typetrees as unity-rs's
                         JSON (typetree_form "json"), TextAsset and Font bytes, sprite images, and the census document
                         as far as the Python API gives its fields
    texture_decode       a Texture2D's first mip level by unity-rs `read_texture`, in the modes of the reference
                         (Alpha8: RGB 0, alpha the texture; RGB formats: RGB; else RGBA)
    texture_decode_keep  the same, and the image keeps unity-rs's own image for png_encode*
    png_encode           unity-rs's PNG encoder at the given zlib level, for images texture_decode_keep made; any
                         other image is written by Pillow (counted in FALLBACKS)
    png_encode_default   the same at unity-rs's default compression; png_encode_fast at "fast"
    mesh_arrays          positions, normals, uv0 and triangles read back from unity-rs `read_mesh_obj`

Nothing here is used by nnnotes itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import metadata

from nnnotes.atoms import Impl, Unsupported
from nnnotes.atoms import png as png_atom
from nnnotes.atoms import reader as reader_atom
from nnnotes.atoms import texture as texture_atom
from nnnotes.atoms.mesh import MeshInput
from nnnotes.atoms.reader import ObjectInfo, UnityBundle
from nnnotes.atoms.texture import TextureInput

FALLBACKS = {"png": 0}
_KEEP = "unity_rs.image"


def _v(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "absent"


_BUILD: list[str] = []


def _build() -> str:
    """The first 12 hex digits of the SHA-256 of the installed native module (tells builds of one version apart)."""
    if not _BUILD:
        import hashlib
        from pathlib import Path
        import unity_rs
        mods = sorted(Path(unity_rs.__file__).parent.glob("_native*"))
        _BUILD.append(hashlib.sha256(mods[0].read_bytes()).hexdigest()[:12] if mods else "unknown")
    return _BUILD[0]


def _id(*parts: str) -> str:
    return "+".join(parts) + "/eval1"


def _rs() -> str:
    return f"unity-rs-{_v('unity-rs')}-{_build()}"


def portable(path: str) -> str:
    """unity-rs's file name of a serialized file: the part after `::` and the last path separator."""
    tail = path.rsplit("::", 1)[-1]
    return tail.replace("\\", "/").rsplit("/", 1)[-1]


@dataclass(frozen=True)
class RsTextureInput(TextureInput):
    source: tuple | None = field(default=None, compare=False, repr=False)     # (UnityRs, file index, path id)


@dataclass(frozen=True)
class RsMeshInput(MeshInput):
    source: tuple | None = field(default=None, compare=False, repr=False)


def _class_name(class_id: int) -> str:
    from UnityPy.enums import ClassIDType
    try:
        return ClassIDType(class_id).name
    except ValueError:
        return f"Class{class_id}"


# ---------------------------------------------------------------- readers
class HybridBundle(UnityBundle):
    """UnityPy's view of the bundle; unity-rs loads the same bytes when a texture is first asked for."""

    def __init__(self, env, data: bytes):
        super().__init__(env)
        self._data = data
        self._rs = None
        self._index: dict | None = None

    def _rs_index(self):
        if self._rs is None:
            from unity_rs import UnityRs
            self._rs = UnityRs.from_bytes(self._data, name="bundle")
            names = {f.index: portable(f.path) for f in self._rs.files()}
            self._index = {(names[o.file_index], o.path_id): o.file_index for o in self._rs.objects()
                           if o.class_id == 28}
        return self._rs, self._index

    def texture_input(self, obj: ObjectInfo) -> TextureInput:
        inp = super().texture_input(obj)
        rs, index = self._rs_index()
        fi = index.get((obj.file, obj.path_id))
        return RsTextureInput(inp.data, inp.width, inp.height, inp.format, inp.version, inp.platform,
                              inp.platform_blob, source=None if fi is None else (rs, fi, obj.path_id))


def open_hybrid(data: bytes) -> HybridBundle:
    from nnnotes.unity import load_bytes
    data = bytes(data)
    return HybridBundle(load_bytes(data), data)


class RsBundle:
    """unity-rs alone, as far as its Python API goes (conformance checks only)."""
    typetree_form = "json"

    def __init__(self, data: bytes):
        from unity_rs import UnityRs
        self.rs = UnityRs.from_bytes(bytes(data), name="bundle")
        self._files = self.rs.files()
        self._names = [portable(f.path) for f in self._files]
        self._fi = {n: i for i, n in zip((f.index for f in self._files), self._names)}
        self._objs = self.rs.objects()
        self._infos = []
        self._raw_info = {}
        by_index = dict(zip((f.index for f in self._files), self._names))
        for o in self._objs:
            name = by_index[o.file_index]
            info = ObjectInfo(name, int(o.path_id), int(o.class_id), _class_name(int(o.class_id)), int(o.byte_size))
            self._infos.append(info)
            self._raw_info[(name, int(o.path_id))] = o

    def _loc(self, obj: ObjectInfo) -> tuple[int, int]:
        return self._fi[obj.file], obj.path_id

    def files(self) -> list[str]:
        return list(self._names)

    def objects(self) -> list[ObjectInfo]:
        return list(self._infos)

    def raw(self, obj: ObjectInfo) -> bytes:
        return self.rs.read_raw(*self._loc(obj))

    def typetree(self, obj: ObjectInfo) -> dict:
        return json.loads(self.rs.read_type_tree_json(*self._loc(obj)))

    def container(self) -> list:
        out = []
        for o in self._infos:
            if o.class_id != 142:
                continue
            ab = self.rs.read_asset_bundle(*self._loc(o))
            for path, _pi, _ps, (fid, pid) in ab.container:
                if fid != 0:
                    raise NotImplementedError("a container entry names another file; the API has no externals")
                out.append((path, o.file, int(pid)))
        return out

    def externals(self, file: str) -> list[str]:
        raise NotImplementedError("the Python API has no externals list")

    def unity_version(self, file: str) -> str:
        return self._files[self._names.index(file)].unity_version

    def text_bytes(self, obj: ObjectInfo) -> bytes:
        return self.rs.read_text(*self._loc(obj))

    def font_bytes(self, obj: ObjectInfo) -> bytes:
        return self.rs.read_font(*self._loc(obj)).data

    def sprite_image(self, obj: ObjectInfo):
        from PIL import Image
        ri = self.rs.read_sprite(*self._loc(obj))
        return Image.frombytes("RGBA", (ri.width, ri.height), ri.rgba)

    def texture_input(self, obj: ObjectInfo) -> TextureInput:
        tt = self.typetree(obj)
        return RsTextureInput(b"", int(tt.get("m_Width", 0)), int(tt.get("m_Height", 0)),
                              int(tt.get("m_TextureFormat", 0)), source=(self.rs, *self._loc(obj)))

    def mesh_input(self, obj: ObjectInfo) -> MeshInput:
        return RsMeshInput(obj.class_name, (), 0, source=(self.rs, *self._loc(obj)))

    def census(self) -> dict:
        files, bundles, scripts, classes = [], [], [], {}
        by_file: dict[str, list] = {n: [] for n in self._names}
        for info in self._infos:
            o = self._raw_info[(info.file, info.path_id)]
            rec = {"pathId": info.path_id, "classId": info.class_id, "class": info.class_name,
                   "byteSize": info.byte_size}
            if o.name is not None:
                rec["name"] = o.name
            by_file[info.file].append(rec)
            classes[info.class_name] = classes.get(info.class_name, 0) + 1
            if info.class_id == 142:
                ab = self.rs.read_asset_bundle(*self._loc(info))
                d = {"file": info.file, "pathId": info.path_id, "name": ab.name,
                     "assetBundleName": ab.asset_bundle_name, "dependencies": list(ab.dependencies),
                     "isStreamedSceneAssetBundle": bool(ab.is_streamed_scene_asset_bundle)}
                if all(fid == 0 for _p, _i, _s, (fid, _pid) in ab.container):
                    d["container"] = [{"path": p, "file": info.file if pid else None, "pathId": int(pid) or None,
                                       "preloadIndex": pi, "preloadSize": ps} for p, pi, ps, (_fid, pid) in ab.container]
                bundles.append(d)
            elif info.class_id == 115:
                m = self.rs.read_mono_script(*self._loc(info))
                scripts.append({"file": info.file, "pathId": info.path_id, "assembly": m.assembly_name,
                                "namespace": m.namespace, "class": m.class_name, "name": m.name})
        for f, n in sorted(zip(self._files, self._names), key=lambda x: x[1]):
            files.append({"name": n, "unityVersion": f.unity_version,
                          "objects": sorted(by_file[n], key=lambda r: r["pathId"])})
        resources = sorted(({"name": portable(r.path), "size": int(r.byte_size)} for r in self.rs.resources()),
                           key=lambda r: r["name"])
        return {"files": files, "resources": resources, "assetBundles": bundles, "scripts": scripts,
                "classes": classes, "objects": len(self._infos)}


@cache
def reader() -> Impl:
    return Impl(_id(_rs(), f"unitypy-{_v('UnityPy')}"), open_hybrid, reader_atom.cost)


@cache
def reader_rs() -> Impl:
    return Impl(_id(_rs(), "alone"), RsBundle, reader_atom.cost)


# ---------------------------------------------------------------- texture.decode
_MODES: dict[int, str] = {}


def _mode(fmt: int) -> str:
    """The reference's image mode for a format: "A" (alpha only, RGB 0), "RGB", "RGBA", or "" (other)."""
    hit = _MODES.get(fmt)
    if hit is None:
        from UnityPy.export.Texture2DConverter import CONV_TABLE, pillow
        from UnityPy.enums import TextureFormat
        try:
            fn, args = CONV_TABLE[TextureFormat(fmt)]
        except (KeyError, ValueError):
            fn, args = None, ()
        if fn is pillow:
            mode, raw = args[0], args[2]
            hit = "A" if (mode, raw) == ("RGBA", "A") else mode if mode in ("RGB", "RGBA") else ""
        else:
            hit = "RGBA"
        _MODES[fmt] = hit
    return hit


def _decode(inp: TextureInput, keep: bool = False):
    from PIL import Image
    if not inp.width or not inp.height:
        raise Unsupported("empty.texture", f"{inp.width}x{inp.height} texture")
    src = getattr(inp, "source", None)
    if src is None:
        raise Unsupported("unsupported.texture.format", "no unity-rs object for this texture")
    fmt = int(inp.format)
    mode = _mode(fmt)
    if not mode:
        raise Unsupported("unsupported.texture.format", f"{texture_atom.format_name(fmt)}: no mode convention")
    rs, fi, pid = src
    ri = rs.read_texture(fi, pid)
    img = Image.frombytes("RGBA", (ri.width, ri.height), ri.rgba)
    if mode == "A":
        a = img.getchannel("A")
        zero = Image.new("L", img.size, 0)
        img = Image.merge("RGBA", (zero, zero, zero, a))
    elif mode == "RGB":
        img = img.convert("RGB")
    if keep:
        img.info[_KEEP] = (id(img), ri)
    return img


def _decode_keep(inp: TextureInput):
    return _decode(inp, keep=True)


@cache
def texture_decode() -> Impl:
    return Impl(_id(_rs()), _decode, texture_atom.cost)


@cache
def texture_decode_keep() -> Impl:
    return Impl(_id(_rs(), "keep"), _decode_keep, texture_atom.cost)


# ---------------------------------------------------------------- png.encode
def _png_with(compression):
    def encode(image, level: int = png_atom.DEFAULT_LEVEL) -> bytes:
        kept = image.info.get(_KEEP)
        if kept is None or kept[0] != id(image):
            FALLBACKS["png"] += 1
            return png_atom.encode(image, level)
        return kept[1].encode("png", compression=level if compression == "level" else compression)
    return encode


@cache
def png_encode() -> Impl:
    return Impl(_id(_rs(), "level"), _png_with("level"), png_atom.cost)


@cache
def png_encode_default() -> Impl:
    return Impl(_id(_rs(), "default"), _png_with(None), png_atom.cost)


@cache
def png_encode_fast() -> Impl:
    return Impl(_id(_rs(), "fast"), _png_with("fast"), png_atom.cost)


# ---------------------------------------------------------------- mesh.arrays
def _mesh(inp: MeshInput):
    import numpy as np
    from nnnotes.atoms.mesh import Channel, MeshArrays, Primitive
    src = getattr(inp, "source", None)
    if src is None:
        raise Unsupported("empty.mesh", "no unity-rs object for this mesh")
    rs, fi, pid = src
    text = rs.read_mesh_obj(fi, pid).decode("utf-8", "surrogateescape")
    v, vt, vn, groups = [], [], [], []
    name = None
    for ln in text.split("\r\n"):
        if ln.startswith("v "):
            x, y, z = ln[2:].split()
            v.append((-np.float32(x), np.float32(y), np.float32(z)))
        elif ln.startswith("vt "):
            u, w = ln[3:].split()
            vt.append((np.float32(u), np.float32(w)))
        elif ln.startswith("vn "):
            x, y, z = ln[3:].split()
            vn.append((-np.float32(x), np.float32(y), np.float32(z)))
        elif ln.startswith("g "):
            if name is None:
                name = ln[2:]
            else:
                groups.append([])
        elif ln.startswith("f "):
            a, b, c = (int(t.split("/")[0]) - 1 for t in ln[2:].split())
            groups[-1].append((c, b, a))
    n = len(v)
    channels = {"position": Channel(np.asarray(v, np.float32).reshape(n, 3))}
    if vn:
        channels["normal"] = Channel(np.asarray(vn, np.float32).reshape(n, 3))
    if vt:
        channels["uv0"] = Channel(np.asarray(vt, np.float32).reshape(n, 2))
    prims = [Primitive("triangles", np.asarray(g, np.int64).reshape(-1, 3), i) for i, g in enumerate(groups)]
    return MeshArrays(name or "", n, channels, prims, [])


@cache
def mesh_arrays() -> Impl:
    from nnnotes.atoms import mesh as mesh_atom
    return Impl(_id(_rs(), "obj"), _mesh, mesh_atom.cost)
