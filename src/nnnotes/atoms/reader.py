"""Atom `reader`: one bundle's serialized files, objects and the inputs of the other atoms.

`BundleView` is what a stage reads a bundle through; `UnityBundle` is the reference implementation (UnityPy, the
bundle loaded alone from its bytes: references into other bundles are not followed, resource files inside the
bundle -- `.resS` -- are read). Objects are named by `ObjectInfo` (serialized file name and path id), never by
library objects, so another implementation can stand in.

`texture_input` and `mesh_input` collect exactly what `texture.decode` and `mesh.arrays` need: a Texture2D's image
data (the streamed resource data when it is streamed), size, format, Unity version, build target and platform blob;
a Mesh's (or a Sprite's render data's) vertex buffer, channel table, index buffer and submeshes as stored. Meshes of
layouts `mesh.arrays` does not decode itself (compressed meshes, Unity before 2018.2) are decoded here by UnityPy's
MeshHandler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from . import estimate
from .mesh import CHANNELS, RAW_LAYOUT_SINCE, MeshInput, Submesh
from .texture import TextureInput
from .. import contract


@dataclass(frozen=True)
class ObjectInfo:
    file: str                       # serialized file name (CAB-...)
    path_id: int
    class_id: int
    class_name: str
    byte_size: int
    type_hash: str | None = None    # the type's stored hash (hex), None when the file does not store it

    @property
    def id(self) -> str:
        return contract.object_id(self.file, self.path_id)


class BundleView(Protocol):
    def files(self) -> list[str]:
        """Serialized file names, in bundle order."""

    def objects(self) -> list[ObjectInfo]:
        """Every object, file by file in bundle order, each file's objects in stored order."""

    def raw(self, obj: ObjectInfo) -> bytes:
        """The object's serialized bytes."""

    def typetree(self, obj: ObjectInfo) -> dict:
        """The object read through its typetree, fields in stored order."""

    def container(self) -> list[tuple[str, str, int]]:
        """(container path, serialized file, path id) of every container entry, in stored order."""

    def externals(self, file: str) -> list[str]:
        """The paths of a serialized file's externals (PPtr file id i names externals[i - 1])."""

    def unity_version(self, file: str) -> str:
        """The Unity version a serialized file was written by."""

    def texture_input(self, obj: ObjectInfo) -> TextureInput:
        ...

    def mesh_input(self, obj: ObjectInfo) -> MeshInput:
        ...


def _bytes(v) -> bytes:
    return v if isinstance(v, bytes) else bytes(v or b"")


def _file_name(path: str) -> str:
    """The serialized file an external path names (archive:/CAB-x/CAB-x -> CAB-x)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def submeshes(tt_submeshes) -> tuple:
    return tuple(Submesh(int(s["firstByte"]), int(s["indexCount"]), int(s.get("topology", 0) or 0),
                         int(s.get("baseVertex", 0) or 0), int(s.get("firstVertex", 0) or 0),
                         int(s.get("vertexCount", 0) or 0))
                 for s in tt_submeshes or ())


def mesh_input_from_typetree(tt: dict, version: tuple, resource=None) -> MeshInput:
    """MeshInput of a Mesh typetree (or a Sprite typetree: its m_RD) of a Unity 2018.2+ file. `resource(path,
    offset, size)` reads streamed vertex data (m_StreamData)."""
    sprite = "m_RD" in tt
    src = tt["m_RD"] if sprite else tt
    vd = src["m_VertexData"]
    data = _bytes(vd.get("m_DataSize"))
    stream = None if sprite else tt.get("m_StreamData")
    if stream and stream.get("path"):
        if resource is None:
            raise ValueError(f"mesh {tt.get('m_Name')!r}: streamed vertex data and no resource reader")
        data = resource(stream["path"], int(stream["offset"]), int(stream["size"]))
    if sprite:
        bits = 16
    elif "m_Use16BitIndices" in tt:
        bits = 16 if tt["m_Use16BitIndices"] else 32
    else:
        bits = 32 if tt.get("m_IndexFormat", 0) == 1 else 16
    shapes = tt.get("m_Shapes")
    n_shapes = len(shapes.get("channels", ()) or shapes.get("shapes", ())) if isinstance(shapes, dict) else 0
    vbw = tt.get("m_VariableBoneCountWeights")
    return MeshInput(
        name=tt.get("m_Name", ""), version=tuple(version), vertex_count=int(vd.get("m_VertexCount", 0)),
        channels=tuple((int(c["stream"]), int(c["offset"]), int(c["format"]), int(c["dimension"]))
                       for c in vd.get("m_Channels") or ()),
        vertex_data=data, index_buffer=_bytes(src.get("m_IndexBuffer")), index_bits=bits,
        submeshes=submeshes(src.get("m_SubMeshes")),
        bind_poses=tuple(src.get("m_Bindpose" if sprite else "m_BindPose") or ()),
        bone_name_hashes=() if sprite else tuple(tt.get("m_BoneNameHashes") or ()),
        root_bone_name_hash=None if sprite else tt.get("m_RootBoneNameHash"),
        blend_shapes=n_shapes, variable_bone_weights=len((vbw or {}).get("m_Data") or ()))


def needs_handler(tt: dict, version: tuple) -> bool:
    """True when UnityPy's MeshHandler decodes the mesh (older layouts, compressed meshes)."""
    if tuple(version)[:2] < RAW_LAYOUT_SINCE:
        return True
    cm = tt.get("m_CompressedMesh")
    return bool(cm) and int(cm["m_Vertices"]["m_NumItems"]) > 0


_HANDLER_FIELDS = {"position": "m_Vertices", "normal": "m_Normals", "tangent": "m_Tangents", "color": "m_Colors",
                   **{f"uv{i}": f"m_UV{i}" for i in range(8)}, "blendWeight": "m_BoneWeights",
                   "blendIndices": "m_BoneIndices"}


def handler_input(src, tt: dict, version: tuple) -> MeshInput:
    """MeshInput decoded by UnityPy's MeshHandler from `src` (a Mesh object, or a Sprite's m_RD)."""
    import numpy as np
    from UnityPy.helpers.MeshHelper import MeshHandler
    h = MeshHandler(src, tuple(version))
    h.process()
    decoded = {}
    for name in CHANNELS:
        values = getattr(h, _HANDLER_FIELDS[name], None)
        if not values:
            continue
        a = np.asarray(values)
        if a.dtype.kind in "iu":
            a = a.astype(np.uint32 if name == "blendIndices" else np.uint8)
            decoded[name] = (a, name != "blendIndices")
        else:
            decoded[name] = (a.astype(np.float32), False)
    base = mesh_input_from_typetree({**tt, "m_StreamData": None} if "m_RD" not in tt else tt, version)
    ib = h.m_IndexBuffer
    return MeshInput(
        name=base.name, version=base.version, vertex_count=int(h.m_VertexCount or 0), decoded=decoded,
        index_buffer=[] if not ib else list(ib) if not isinstance(ib, (bytes, bytearray)) else bytes(ib),
        index_bits=16 if h.m_Use16BitIndices else 32, submeshes=base.submeshes, bind_poses=base.bind_poses,
        bone_name_hashes=base.bone_name_hashes, root_bone_name_hash=base.root_bone_name_hash,
        blend_shapes=base.blend_shapes, variable_bone_weights=base.variable_bone_weights)


def _resource(assets_file, path: str, offset: int, size: int) -> bytes:
    """`size` bytes at `offset` of the resource file `path` (a .resS / .resource inside the same bundle). Unlike
    UnityPy's own lookup, nothing outside the loaded bundle is searched."""
    import ntpath
    base = ntpath.basename(path)
    stem = ntpath.splitext(base)[0]
    env = assets_file.environment
    for name in (base, f"{stem}.resource", f"{stem}.assets.resS", f"{stem}.resS"):
        reader = env.get_cab(name)
        if reader is not None:
            reader.Position = offset
            data = reader.read_bytes(size)
            if len(data) != size:
                raise ValueError(f"resource {base}: {len(data)} of {size} bytes at {offset}")
            return bytes(data)
    raise FileNotFoundError(f"resource file {base} is not in the bundle")


class UnityBundle:
    """BundleView of a UnityPy environment holding one bundle (open_bundle loads it from the bundle's bytes)."""

    def __init__(self, env):
        self.env = env
        self._files = {f.name: f for f in self.env.assets}
        self._readers: dict[tuple[str, int], object] = {}
        self._infos: list[ObjectInfo] = []
        for name, f in self._files.items():
            for pid, r in f.objects.items():
                st = r.serialized_type
                h = getattr(st, "old_type_hash", None) if st is not None else None
                self._readers[(name, pid)] = r
                self._infos.append(ObjectInfo(name, int(pid), int(r.class_id), r.type.name, int(r.byte_size),
                                              h.hex() if h else None))

    def _reader(self, obj: ObjectInfo):
        return self._readers[(obj.file, obj.path_id)]

    def reader(self, obj: ObjectInfo):
        """The library's object reader of `obj` (UnityPy's ObjectReader), for code that works on library objects."""
        return self._readers[(obj.file, obj.path_id)]

    def files(self) -> list[str]:
        return list(self._files)

    def objects(self) -> list[ObjectInfo]:
        return list(self._infos)

    def raw(self, obj: ObjectInfo) -> bytes:
        return self._reader(obj).get_raw_data()

    def typetree(self, obj: ObjectInfo) -> dict:
        return self._reader(obj).read_typetree()

    def container(self) -> list[tuple[str, str, int]]:
        out = []
        for name, f in self._files.items():
            for path, info in f.container.container:
                pptr = info.asset
                fid = int(pptr.m_FileID)
                target = name if fid == 0 else _file_name(f.externals[fid - 1].path)
                out.append((path, target, int(pptr.m_PathID)))
        return out

    def externals(self, file: str) -> list[str]:
        return [e.path for e in self._files[file].externals]

    def unity_version(self, file: str) -> str:
        f = self._files[file]
        v = getattr(f, "unity_version", None)
        return v if isinstance(v, str) else ".".join(str(x) for x in f.version)

    def texture_input(self, obj: ObjectInfo) -> TextureInput:
        r = self._reader(obj)
        tex = r.read()
        w, h = int(tex.m_Width), int(tex.m_Height)
        blob = getattr(tex, "m_PlatformBlob", None)
        # the arguments UnityPy's Texture2D.image passes to parse_image_data (as export._decode_inputs); a texture
        # without pixels has no image data to read
        data = b""
        if w and h:
            stream = getattr(tex, "m_StreamData", None)
            if tex.image_data:
                data = bytes(tex.image_data)
            elif stream is not None and stream.path:
                data = _resource(r.assets_file, stream.path, int(stream.offset), int(stream.size))
            else:
                raise ValueError(f"{obj.id}: texture without image data")
        return TextureInput(data, w, h, int(tex.m_TextureFormat), tuple(getattr(r, "version", (0, 0, 0, 0))),
                            int(getattr(r, "platform", 0) or 0), None if blob is None else bytes(blob))

    def mesh_input(self, obj: ObjectInfo) -> MeshInput:
        r = self._reader(obj)
        if obj.class_name not in ("Mesh", "Sprite"):
            raise TypeError(f"{obj.id}: {obj.class_name} has no mesh")
        tt = r.read_typetree()
        version = tuple(r.version)
        if needs_handler(tt, version):
            o = r.read()
            return handler_input(o.m_RD if obj.class_name == "Sprite" else o, tt, version)
        return mesh_input_from_typetree(tt, version, lambda path, offset, size: _resource(r.assets_file, path,
                                                                                           offset, size))


def open_bundle(data: bytes) -> UnityBundle:
    """The BundleView of a bundle's bytes (decrypted UnityFS), loaded alone."""
    from ..unity import load_bytes
    return UnityBundle(load_bytes(bytes(data)))


def cost(facts: dict) -> dict:
    """facts: size (bundle bytes), objects (count)."""
    size, n = int(facts.get("size", 0)), int(facts.get("objects", 0))
    return estimate(0.01 + size * 1.5e-8 + n * 1.2e-4, 7e7 + size * 8)
