"""Synthetic Unity data and stand-ins for UnityPy objects, for the atom tests.

- vertex buffers laid out the way Unity lays them out (channels per stream, streams 16-byte aligned), and Mesh /
  Sprite typetrees holding them;
- the same data as UnityPy's own classes (Mesh, SpriteRenderData, SpriteAtlasData, ...), so UnityPy's MeshHandler
  and SpriteHelper run on exactly what the atoms get;
- Texture2D stand-ins with the attributes UnityPy's texture converter reads, PPtrs, and an environment of serialized
  files for the reader.

Nothing here comes from game data.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
from UnityPy.classes.generated import (ChannelInfo, Mesh, Rectf, SpriteAtlasData, SpriteRenderData, SubMesh,
                                       Vector2f, Vector4f, VertexData)
from UnityPy.export.Texture2DConverter import get_image_from_texture2d

VERSION = (6000, 3, 12, 1)
ANDROID = 13
# vertex formats (Unity 2019+)
F32, F16, UNORM8, SNORM8, UNORM16, SNORM16, UINT8, SINT8, UINT16, SINT16, UINT32, SINT32 = range(12)
DTYPES = {F32: "<f4", F16: "<f2", UNORM8: "u1", SNORM8: "i1", UNORM16: "<u2", SNORM16: "<i2", UINT8: "u1",
          SINT8: "i1", UINT16: "<u2", SINT16: "<i2", UINT32: "<u4", SINT32: "<i4"}
# channel indices (Unity 2018+)
POSITION, NORMAL, TANGENT, COLOR, UV0, UV1 = 0, 1, 2, 3, 4, 5
BLENDWEIGHT, BLENDINDICES = 12, 13
CLASS_IDS = {"GameObject": 1, "Transform": 4, "Material": 21, "Texture2D": 28, "Mesh": 43, "Shader": 48,
             "TextAsset": 49, "AnimationClip": 74, "Animator": 95, "MonoBehaviour": 114, "MonoScript": 115,
             "Font": 128, "AssetBundle": 142, "AnimatorController": 91, "AnimatorOverrideController": 221,
             "RectTransform": 224, "Sprite": 213, "AudioClip": 83, "SpriteAtlas": 687078895}


# ---------------------------------------------------------------- vertex and index buffers
def vertex_buffer(count: int, channels: dict) -> tuple[list[dict], bytes]:
    """(m_Channels, m_DataSize) of `count` vertices. `channels`: {channel index: (format, dimension byte, values
    (count, dimension & 0xF), stream)}; the stream defaults to 0. Absent channels are written as Unity writes them
    (dimension 0)."""
    table = [{"stream": 0, "offset": 0, "format": 0, "dimension": 0} for _ in range(14)]
    per_stream: dict[int, list] = {}
    for i in sorted(channels):
        fmt, dim, values, *rest = channels[i]
        stream = rest[0] if rest else 0
        per_stream.setdefault(stream, []).append((i, fmt, dim, np.asarray(values)))
    data = bytearray()
    for s in range(1 + max(per_stream, default=0)):
        cols, offset = [], 0
        for i, fmt, dim, values in per_stream.get(s, []):
            dt = np.dtype(DTYPES[fmt])
            n = dim & 0xF
            a = np.ascontiguousarray(values.reshape(count, n), dt)
            table[i] = {"stream": s, "offset": offset, "format": fmt, "dimension": dim}
            cols.append(a.view(np.uint8).reshape(count, n * dt.itemsize))
            offset += n * dt.itemsize
        if cols:
            data.extend(np.hstack(cols).tobytes())
        data.extend(b"\0" * (-len(data) % 16))
    return table, bytes(data)


def index_bytes(indices, bits: int = 16) -> list[int]:
    """An index buffer as the typetree gives it (a list of byte values)."""
    return list(np.asarray(indices, "<u2" if bits == 16 else "<u4").tobytes())


def submesh(first_index: int, index_count: int, topology: int = 0, base_vertex: int = 0, bits: int = 16,
            first_vertex: int = 0, vertex_count: int = 0) -> dict:
    return {"firstByte": first_index * bits // 8, "indexCount": index_count, "topology": topology,
            "baseVertex": base_vertex, "firstVertex": first_vertex, "vertexCount": vertex_count,
            "localAABB": {"m_Center": {"x": 0.0, "y": 0.0, "z": 0.0}, "m_Extent": {"x": 0.0, "y": 0.0, "z": 0.0}}}


def matrix(rows) -> dict:
    """A Matrix4x4f typetree (e00 .. e33) from 4 rows."""
    return {f"e{r}{c}": float(rows[r][c]) for c in range(4) for r in range(4)}


# ---------------------------------------------------------------- typetrees
def mesh_tt(name: str, count: int, channels: dict, indices=(), submeshes=(), bits: int = 16, bind_poses=(),
            bone_hashes=(), root_hash=None, shapes: int = 0, variable_weights: int = 0, stream: dict | None = None,
            data: bytes | None = None) -> dict:
    table, vbuf = vertex_buffer(count, channels) if count else ([], b"")
    return {
        "m_Name": name, "m_SubMeshes": [dict(s) for s in submeshes],
        "m_Shapes": {"vertices": [], "shapes": [], "channels": [{"name": f"s{i}"} for i in range(shapes)],
                     "fullWeights": []},
        "m_BindPose": list(bind_poses), "m_BoneNameHashes": list(bone_hashes), "m_RootBoneNameHash": root_hash,
        "m_MeshCompression": 0, "m_IsReadable": True, "m_IndexFormat": 0 if bits == 16 else 1,
        "m_IndexBuffer": index_bytes(indices, bits),
        "m_VertexData": {"m_VertexCount": count, "m_Channels": table, "m_DataSize": vbuf if data is None else data},
        "m_CompressedMesh": {k: {"m_NumItems": 0} for k in ("m_Vertices", "m_UV", "m_Normals", "m_Tangents",
                                                              "m_Weights", "m_Triangles")},
        "m_StreamData": stream or {"offset": 0, "size": 0, "path": ""},
        "m_VariableBoneCountWeights": {"m_Data": [0] * variable_weights},
    }


def pptr(path_id: int = 0, file_id: int = 0) -> dict:
    return {"m_FileID": file_id, "m_PathID": path_id}


def rect(x, y, w, h) -> dict:
    return {"x": float(x), "y": float(y), "width": float(w), "height": float(h)}


def quad(r: dict, pivot=(0.5, 0.5), ppu: float = 100.0) -> tuple[np.ndarray, list[int]]:
    """Positions (units, relative to the pivot) and indices of a full-rect sprite mesh over rect `r`."""
    w, h = r["width"], r["height"]
    x0, y0 = -pivot[0] * w / ppu, -pivot[1] * h / ppu
    x1, y1 = x0 + w / ppu, y0 + h / ppu
    pos = np.array([[x0, y1, 0], [x1, y1, 0], [x0, y0, 0], [x1, y0, 0]], np.float32)
    return pos, [0, 1, 2, 2, 1, 3]


def render_data(texture: dict, texture_rect: dict, settings_raw: int, positions, indices, uv=None,
                offset=(0.0, 0.0), alpha: dict | None = None) -> dict:
    """A Sprite's m_RD (or an atlas' SpriteAtlasData fields plus a mesh)."""
    positions = np.asarray(positions, np.float32)
    count = len(positions)
    chans = {POSITION: (F32, 3, positions)}
    if uv is not None:
        chans[UV0] = (F32, 2, np.asarray(uv, np.float32))
    table, vbuf = vertex_buffer(count, chans)
    return {"texture": texture, "alphaTexture": alpha or pptr(), "secondaryTextures": [],
            "m_SubMeshes": [submesh(0, len(indices), vertex_count=count)], "m_IndexBuffer": index_bytes(indices),
            "m_VertexData": {"m_VertexCount": count, "m_Channels": table, "m_DataSize": vbuf}, "m_Bindpose": [],
            "textureRect": texture_rect, "textureRectOffset": {"x": float(offset[0]), "y": float(offset[1])},
            "atlasRectOffset": {"x": -1.0, "y": -1.0}, "settingsRaw": settings_raw,
            "uvTransform": {"x": 100.0, "y": 0.0, "z": 100.0, "w": 0.0}, "downscaleMultiplier": 1.0}


def sprite_tt(name: str, r: dict, rd: dict, ppu: float = 100.0, pivot=(0.5, 0.5), atlas: dict | None = None,
              atlas_tags=(), key=((1, 2, 3, 4), 0)) -> dict:
    return {"m_Name": name, "m_Rect": r, "m_Offset": {"x": 0.0, "y": 0.0},
            "m_Border": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}, "m_PixelsToUnits": ppu,
            "m_Pivot": {"x": pivot[0], "y": pivot[1]}, "m_Extrude": 1, "m_IsPolygon": False,
            "m_RenderDataKey": [{"data[0]": key[0][0], "data[1]": key[0][1], "data[2]": key[0][2],
                                 "data[3]": key[0][3]}, key[1]],
            "m_AtlasTags": list(atlas_tags), "m_SpriteAtlas": atlas or pptr(), "m_RD": rd}


# ---------------------------------------------------------------- UnityPy objects of the same data
class FakePPtr:
    """A PPtr as UnityPy's SpriteHelper uses it: truthy when set, path_id, deref_parse_as_object."""

    _next = 1000

    def __init__(self, obj=None, path_id: int | None = None):
        self.obj = obj
        if path_id is None:
            FakePPtr._next += 1
            path_id = FakePPtr._next if obj is not None else 0
        self.path_id = self.m_PathID = path_id
        self.m_FileID = 0

    def __bool__(self):
        return self.obj is not None

    def deref_parse_as_object(self):
        return self.obj


def _vertex_data(vd: dict) -> VertexData:
    return VertexData(m_DataSize=vd["m_DataSize"], m_VertexCount=vd["m_VertexCount"],
                      m_Channels=[ChannelInfo(**c) for c in vd["m_Channels"]])


def _submeshes(tt_list) -> list:
    return [SubMesh(firstByte=s["firstByte"], firstVertex=s["firstVertex"], indexCount=s["indexCount"],
                    localAABB=None, vertexCount=s["vertexCount"], baseVertex=s["baseVertex"], topology=s["topology"])
            for s in tt_list]


def _compressed() -> SimpleNamespace:
    empty = SimpleNamespace(m_NumItems=0)
    return SimpleNamespace(m_Vertices=empty, m_UV=empty, m_Normals=empty, m_Tangents=empty, m_Weights=empty,
                           m_Triangles=empty, m_NormalSigns=empty, m_TangentSigns=empty, m_BoneIndices=empty,
                           m_FloatColors=None, m_Colors=None, m_UVInfo=0, m_BindPoses=None)


def unitypy_mesh(tt: dict, version=VERSION) -> Mesh:
    """UnityPy's Mesh object of a mesh_tt typetree (object_reader carries the version, for MeshHandler(mesh))."""
    m = Mesh(m_BindPose=[], m_CompressedMesh=_compressed(), m_IndexBuffer=list(tt["m_IndexBuffer"]),
             m_LocalAABB=None, m_MeshCompression=0, m_MeshUsageFlags=0, m_Name=tt["m_Name"],
             m_SubMeshes=_submeshes(tt["m_SubMeshes"]), m_IndexFormat=tt["m_IndexFormat"],
             m_VertexData=_vertex_data(tt["m_VertexData"]), m_StreamData=None)
    m.object_reader = SimpleNamespace(version=version, assets_file=None)
    return m


def unitypy_render_data(rd: dict, textures: dict) -> SpriteRenderData:
    """UnityPy's SpriteRenderData of an m_RD typetree; `textures`: path id -> FakeTexture2D."""
    def ref(p):
        return FakePPtr(textures.get(p["m_PathID"]), p["m_PathID"]) if p["m_PathID"] else FakePPtr()
    tr = rd["textureRect"]
    return SpriteRenderData(settingsRaw=rd["settingsRaw"], texture=ref(rd["texture"]),
                            textureRect=Rectf(height=tr["height"], width=tr["width"], x=tr["x"], y=tr["y"]),
                            textureRectOffset=Vector2f(x=rd["textureRectOffset"]["x"], y=rd["textureRectOffset"]["y"]),
                            alphaTexture=ref(rd["alphaTexture"]), downscaleMultiplier=rd["downscaleMultiplier"],
                            m_IndexBuffer=list(rd["m_IndexBuffer"]), m_SubMeshes=_submeshes(rd["m_SubMeshes"]),
                            m_VertexData=_vertex_data(rd["m_VertexData"]), secondaryTextures=[])


def unitypy_atlas_data(entry: dict, textures: dict) -> SpriteAtlasData:
    rd = unitypy_render_data({**entry, "m_IndexBuffer": [], "m_SubMeshes": [],
                              "m_VertexData": {"m_DataSize": b"", "m_VertexCount": 0, "m_Channels": []}}, textures)
    ut = entry["uvTransform"]
    return SpriteAtlasData(alphaTexture=rd.alphaTexture, downscaleMultiplier=entry["downscaleMultiplier"],
                           settingsRaw=entry["settingsRaw"], texture=rd.texture, textureRect=rd.textureRect,
                           textureRectOffset=rd.textureRectOffset,
                           uvTransform=Vector4f(x=ut["x"], y=ut["y"], z=ut["z"], w=ut["w"]), secondaryTextures=[])


def unitypy_sprite(tt: dict, textures: dict, atlas_entry: dict | None = None, version=VERSION):
    """The object UnityPy's SpriteHelper.get_image_from_sprite reads, for a sprite_tt typetree (with its atlas'
    render data entry when it is atlas-packed)."""
    key = tt["m_RenderDataKey"]
    k = (tuple(key[0].values()), key[1])
    atlas = None
    if atlas_entry is not None:
        atlas = SimpleNamespace(m_RenderDataMap=[(k, unitypy_atlas_data(atlas_entry, textures))])
    return SimpleNamespace(
        m_Name=tt["m_Name"], m_SpriteAtlas=FakePPtr(atlas), m_AtlasTags=tt["m_AtlasTags"],
        m_RD=unitypy_render_data(tt["m_RD"], textures), m_RenderDataKey=k, m_PixelsToUnits=tt["m_PixelsToUnits"],
        assets_file=SimpleNamespace(_cache={}, objects={}), object_reader=SimpleNamespace(version=version))


class FakeTexture2D:
    """A Texture2D as UnityPy's texture converter reads it (and `image`, as UnityPy's Texture2D.image)."""

    def __init__(self, data: bytes, width: int, height: int, fmt: int, name: str = "tex", version=VERSION,
                 platform: int = ANDROID, blob: bytes = b"", stream=None):
        self.image_data = data
        self.m_Width, self.m_Height, self.m_TextureFormat, self.m_Name = width, height, fmt, name
        self.m_PlatformBlob = list(blob)
        self.m_StreamData = stream
        self.object_reader = SimpleNamespace(version=version, platform=platform)

    def get_image_data(self):
        if self.image_data:
            return self.image_data
        raise ValueError("No image data found")

    @property
    def image(self):
        return get_image_from_texture2d(self)


def rgba_bytes(width: int, height: int, seed: int = 0, channels: int = 4) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, channels), dtype=np.uint8).tobytes()


# ---------------------------------------------------------------- an environment for the reader
# ---------------------------------------------------------------- typetree nodes
def _node(level: int, type_name: str, name: str, children=()):
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    return TypeTreeNode(level, type_name, name, -1, 1, list(children))


def _vector(level: int, name: str, element) -> object:
    array = _node(level + 1, "Array", "Array", [_node(level + 2, "int", "size"), element])
    return _node(level, "vector", name, [array])


def node_of(value, name: str = "Base", type_name: str | None = None, bytes_fields=(), level: int = 0,
            types: dict | None = None):
    """A UnityPy TypeTreeNode that reads as `value` (a typetree dict): dicts are classes (a {m_FileID, m_PathID}
    dict a PPtr), lists vectors of their first element's node (empty: of int), tuples of two pairs, bytes
    TypelessData, str / bool / int / float the matching primitives. A list under a name in `bytes_fields` is a
    vector<UInt8> (a byte array as UnityPy reads it). `types`: {field name: class type name} for class nodes."""
    types = types or {}
    if isinstance(value, dict):
        if value.keys() == {"m_FileID", "m_PathID"}:
            return _node(level, "PPtr<Object>", name, [_node(level + 1, "int", "m_FileID"),
                                                     _node(level + 1, "SInt64", "m_PathID")])
        return _node(level, type_name or types.get(name, "Class"), name,
                     [node_of(v, k, None, bytes_fields, level + 1, types) for k, v in value.items()])
    if isinstance(value, tuple) and len(value) == 2:
        return _node(level, "pair", name, [node_of(value[0], "first", None, bytes_fields, level + 1, types),
                                           node_of(value[1], "second", None, bytes_fields, level + 1, types)])
    if isinstance(value, (list, tuple)):
        if name in bytes_fields:
            return _vector(level, name, _node(level + 2, "UInt8", "data"))
        element = node_of(value[0], "data", None, bytes_fields, level + 2, types) if value else             _node(level + 2, "int", "data")
        return _vector(level, name, element)
    if isinstance(value, (bytes, bytearray)):
        return _node(level, "TypelessData", name)
    if isinstance(value, bool):
        return _node(level, "bool", name)
    if isinstance(value, int):
        return _node(level, "SInt64", name)
    if isinstance(value, float):
        return _node(level, "float", name)
    if isinstance(value, str) or value is None:
        return _node(level, "string", name)
    raise TypeError(f"{name}: no node for {type(value).__name__}")


class FakeObjectReader:
    def __init__(self, assets_file, path_id: int, class_name: str, tt: dict, obj=None, raw: bytes = b"",
                 type_hash: bytes | None = None, version=VERSION, platform: int = ANDROID, node=None,
                 bytes_fields=()):
        self.assets_file, self.path_id, self.tt, self.obj, self.raw = assets_file, path_id, tt, obj, raw
        self.class_id = CLASS_IDS[class_name]
        self.type = SimpleNamespace(name=class_name, value=self.class_id)
        self.byte_size = len(raw)
        self.serialized_type = SimpleNamespace(old_type_hash=type_hash)
        self.version, self.platform = version, platform
        self.typetree_reads = 0
        self._node, self._bytes_fields = node, tuple(bytes_fields)

    def read_typetree(self, nodes=None, wrap: bool = False, check_read: bool = True):
        """The typetree (a copy); with a cut node (UnityPy's head reads) only the fields the node names."""
        self.typetree_reads += 1
        tt = copy.deepcopy(self.tt)
        if nodes is not None:
            names = [c.m_Name for c in nodes.m_Children]
            tt = {k: v for k, v in tt.items() if k in names}
        return tt

    def _get_typetree_node(self, node=None):
        if self._node is None:
            self._node = node_of(self.tt, "Base", self.type.name, self._bytes_fields)
        return self._node

    def peek_name(self):
        return self.tt.get("m_Name")

    def read(self):
        return self.obj

    def get_raw_data(self):
        return self.raw


class FakeResource:
    def __init__(self, data: bytes):
        self.data, self.Position = data, 0

    def read_bytes(self, n: int) -> bytes:
        out = self.data[self.Position:self.Position + n]
        self.Position += len(out)
        return out


class FakeSerializedFile:
    def __init__(self, name: str, environment, externals=(), version=VERSION, unity_version="6000.3.12f1"):
        self.name, self.environment, self.version, self.unity_version = name, environment, version, unity_version
        self.objects: dict[int, FakeObjectReader] = {}
        self.externals = [SimpleNamespace(path=p) for p in externals]
        self.container = SimpleNamespace(container=[])
        self.ref_types: list = []

    def add(self, path_id: int, class_name: str, tt: dict, obj=None, **kw) -> FakeObjectReader:
        r = self.objects[path_id] = FakeObjectReader(self, path_id, class_name, tt, obj, **kw)
        return r

    def contain(self, path: str, path_id: int, file_id: int = 0) -> None:
        self.container.container.append((path, SimpleNamespace(asset=SimpleNamespace(m_FileID=file_id,
                                                                                      m_PathID=path_id))))


class FakeEnvironment:
    """UnityPy's Environment as the reader uses it: `assets` (serialized files) and `get_cab` (resource files)."""

    def __init__(self):
        self.assets: list[FakeSerializedFile] = []
        self.resources: dict[str, bytes] = {}
        self.lookups: list[str] = []

    def file(self, name: str, externals=()) -> FakeSerializedFile:
        f = FakeSerializedFile(name, self, externals)
        self.assets.append(f)
        return f

    def get_cab(self, name: str):
        self.lookups.append(name)
        return FakeResource(self.resources[name]) if name in self.resources else None
