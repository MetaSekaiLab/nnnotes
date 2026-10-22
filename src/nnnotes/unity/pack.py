"""Build and edit UnityFS assets using the template's serialization schema and container flags.

New bundles contain the supplied assets and a rebuilt AssetBundle index. TextAsset payloads are binary,
PNG textures use RGBA32, and PCM WAV audio uses a single-sample FSB5 resource. Serialized objects accept
complete type-tree fields; raw objects accept serialized bytes with references managed by the caller.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import UnityPy
from attrs import evolve
from UnityPy.files import BundleFile, ObjectReader, SerializedFile
from UnityPy.files.SerializedFile import FileIdentifier, LocalSerializedObjectIdentifier, SerializedType
from UnityPy.helpers import TypeTreeHelper
from UnityPy.helpers.TypeTreeNode import TypeTreeNode
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

from ..core.cache import temp_path
from . import bundlefs

__all__ = [
    "BundleBuilder",
    "BuildResult",
    "TextAsset",
    "build_manifest",
    "builder_from_manifest",
    "build_text_asset",
    "parse_text_asset",
    "replace_text_asset",
    "wav_to_fsb5",
    "write_outputs",
]


@dataclass(frozen=True)
class TextAsset:
    """A TextAsset's name and binary payload."""

    name: str
    payload: bytes


def parse_text_asset(raw: bytes, *, endian: str = "<") -> TextAsset:
    """Decode the two-field TextAsset layout, rejecting truncated data."""
    try:
        name_size = struct.unpack_from(endian + "I", raw)[0]
        at = (4 + name_size + 3) & ~3
        size = struct.unpack_from(endian + "I", raw, at)[0]
        end = (at + 4 + size + 3) & ~3
        if end != len(raw):
            raise ValueError("TextAsset size does not match its serialized fields")
        return TextAsset(raw[4 : 4 + name_size].decode("utf-8"), raw[at + 4 : at + 4 + size])
    except (struct.error, UnicodeDecodeError) as e:
        raise ValueError(f"invalid TextAsset: {e}") from None


def build_text_asset(name: str, payload: bytes, *, endian: str = "<") -> bytes:
    """Encode a name and binary payload with Unity string alignment."""
    encoded = name.encode("utf-8")
    out = struct.pack(endian + "I", len(encoded)) + encoded
    out += bytes(-len(out) % 4)
    out += struct.pack(endian + "I", len(payload)) + payload
    return out + bytes(-len(out) % 4)


def wav_to_fsb5(data: bytes, name: str) -> tuple[bytes, dict[str, Any]]:
    """Wrap uncompressed mono/stereo 16-bit PCM WAV frames in a single-sample FSB5 bank."""
    try:
        with wave.open(io.BytesIO(data)) as source:
            channels, width, rate, count, compression, _ = source.getparams()
            frames = source.readframes(count)
    except (wave.Error, EOFError) as e:
        raise ValueError(f"invalid PCM WAV: {e}") from None
    if compression != "NONE" or channels not in (1, 2) or width != 2:
        raise ValueError("WAV must contain mono or stereo 16-bit integer PCM")
    if not count or count >= 1 << 30 or len(frames) != count * channels * width:
        raise ValueError("WAV sample count is empty, truncated or too large")
    sample = struct.pack("<QII", 1 | ((channels - 1) << 5) | (count << 34), (2 << 25) | (4 << 1), rate)
    names = struct.pack("<I", 4) + name.encode("utf-8") + b"\0"
    names += bytes(-(60 + len(sample) + len(names)) % 16)
    header = struct.pack(
        "<4s6I8s16s8s", b"FSB5", 1, 1, len(sample), len(names), len(frames), width, bytes(8), bytes(16), bytes(8)
    )
    fields = {
        "m_Channels": channels,
        "m_Frequency": rate,
        "m_BitsPerSample": width * 8,
        "m_Length": count / rate,
        "m_CompressionFormat": 0,
        "m_SubsoundIndex": 0,
    }
    return header + sample + names + frames, fields


def write_outputs(outputs: dict[Path, bytes]) -> None:
    """Stage all files before replacement. Failed recovery preserves backups and reports their paths."""
    resolved = [path.resolve() for path in outputs]
    if len(set(resolved)) != len(resolved):
        raise ValueError("output paths must be distinct")
    if any(path.exists() and not path.is_file() for path in outputs):
        raise ValueError("output paths must be files")
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for path, data in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = staged[path] = temp_path(path)
            temporary.write_bytes(data)
        for path in outputs:
            if path.exists():
                backup = backups[path] = temp_path(path)
                os.replace(path, backup)
            os.replace(staged[path], path)
            published.append(path)
    except BaseException as failure:
        recovery_errors = []
        for path in reversed(published):
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                recovery_errors.append(str(e))
        for path, backup in list(backups.items()):
            try:
                os.replace(backup, path)
                del backups[path]
            except OSError as e:
                recovery_errors.append(f"{path}: {e}; recoverable backup: {backup}")
        if recovery_errors:
            raise OSError(
                f"output replacement failed: {failure}; recovery incomplete: " + "; ".join(recovery_errors)
            ) from failure
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        backups.clear()
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _raw(obj) -> bytes:
    return obj.data if obj.data is not None else obj.get_raw_data()


def _set(obj, raw: bytes) -> None:
    obj.set_raw_data(raw)
    obj.reader = EndianBinaryReader(raw, endian=obj.assets_file.header.endian)
    obj.byte_start, obj.byte_size = 0, len(raw)


def _tree(obj, node=None) -> dict:
    reader = EndianBinaryReader(_raw(obj), endian=obj.assets_file.header.endian)
    return cast(
        dict,
        TypeTreeHelper.read_typetree(
            node or obj._get_typetree_node(),
            reader,
            as_dict=True,
            assetsfile=obj.assets_file,
            byte_size=len(_raw(obj)),
            check_read=True,
        ),
    )


def _save(obj, fields: dict, node=None) -> None:
    writer = EndianBinaryWriter(endian=obj.assets_file.header.endian)
    TypeTreeHelper.write_typetree(fields, node or obj._get_typetree_node(), writer, obj.assets_file)
    _set(obj, writer.bytes)


def _name(obj) -> str | None:
    if obj.type.name == "TextAsset":
        return parse_text_asset(_raw(obj), endian=obj.assets_file.header.endian).name
    return _tree(obj).get("m_Name")


def _node(value):
    if isinstance(value, TypeTreeNode):
        return value
    if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
        node = TypeTreeNode.from_list([TypeTreeNode(lv, ty, nm, 0, 1, m_MetaFlag=mf) for lv, ty, nm, mf in value])
    else:
        rows = [
            {key: child for key, child in row.items() if key != "m_Children"} if isinstance(row, dict) else row
            for row in value
        ]
        node = TypeTreeNode.from_list(rows)
    for child in node.traverse():
        for key in ("m_TypeFlags", "m_RefTypeHash", "m_Index", "m_MetaFlag"):
            if getattr(child, key) is None:
                setattr(child, key, 0)
    return node


def _merge(fields: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if key not in fields:
            raise ValueError(f"field {key!r} is absent from the selected schema")
        if isinstance(value, dict) and isinstance(fields[key], dict):
            _merge(fields[key], value)
        else:
            fields[key] = value
    return fields


def _references(value):
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            yield value
        else:
            for child in value.values():
                yield from _references(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _references(child)


def _payload(spec: dict, base: Path) -> bytes:
    if "data" in spec and "file" in spec:
        raise ValueError("supply either data or file for a binary payload")
    data = bytes(spec["data"]) if "data" in spec else (base / spec["file"]).read_bytes()
    encoding = spec.get("encoding", "binary")
    if encoding == "gzip-json":
        json.loads(data)
        return gzip.compress(data, mtime=0)
    if encoding != "binary":
        raise ValueError("payload encoding must be binary or gzip-json")
    return data


def _binary_fields(fields: dict, specs: list, base: Path) -> None:
    for spec in specs:
        path = spec.get("path")
        if not isinstance(path, list) or not path:
            raise ValueError("binary field path must be a nonempty array of field names and indices")
        target = fields
        try:
            for key in path[:-1]:
                target = target[key]
            previous = target[path[-1]]
            data = _payload(spec, base)
            target[path[-1]] = list(data) if isinstance(previous, list) else data
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"binary field path does not resolve: {path}") from e


@dataclass(frozen=True)
class BuildResult:
    """Validated bundle bytes, container locations and explicit catalog edits.

    Each catalog edit names its original container paths, final internal ID and fields supplied by asset
    operations. Added assets have no original paths; unchanged assets have no edit record.
    """

    data: bytes
    bundle_name: str
    assets: list[dict]
    catalog_edits: list[dict] = field(default_factory=list)

    @property
    def crc(self) -> int:
        return bundlefs.content_crc(self.data)


class BundleBuilder:
    """A template edited in memory; ``new=True`` starts with an empty object and resource table.

    Asset schemas come from the template, compatible additional templates, or explicit serialized type metadata.
    Path IDs are unique within the selected serialized file. Raw references remain the caller's responsibility;
    serialized field references are validated before the bundle is saved.
    """

    def __init__(
        self,
        template,
        *,
        new: bool = True,
        bundle_name: str | None = None,
        serialized_file: str | None = None,
        cab_name: str | None = None,
    ):
        self.env = UnityPy.load(str(template) if isinstance(template, (str, Path)) else template)
        if not isinstance(self.env.file, BundleFile) or self.env.file.signature != "UnityFS":
            raise ValueError("template must be a UnityFS bundle")
        self.bundle = self.env.file
        original = Path(template).read_bytes() if isinstance(template, (str, Path)) else template
        self.header = bundlefs.parse_header(original)
        files = [f for f in self.env.assets if not serialized_file or f.name == serialized_file]
        if len(files) != 1:
            raise ValueError("select exactly one serialized_file from the template")
        self.file = cast(SerializedFile, files[0])
        self.templates = list(self.file.objects.values())
        indices = [o for o in self.templates if o.type.name == "AssetBundle"]
        if len(indices) != 1:
            raise ValueError("template must contain exactly one AssetBundle index in the selected file")
        self.index = indices[0]
        self.index_fields = _tree(self.index)
        if new and not bundle_name:
            raise ValueError("new bundles require a nonempty bundle_name")
        self.bundle_name = bundle_name or self.index_fields["m_Name"]
        independent = new or (bundle_name is not None and bundle_name != self.index_fields["m_Name"])
        self.entries = list(self.index_fields["m_Container"])
        self.locations: dict[str, dict] = {}
        self.original_containers: dict[int, list[str]] = {}
        if not new:
            for key, entry in self.entries:
                if entry["asset"]["m_FileID"] == 0:
                    self.original_containers.setdefault(entry["asset"]["m_PathID"], []).append(key)
        self.catalog_overrides: dict[int, dict] = {}
        self.checked: set[int] = set()
        self.raw_objects: set[int] = set()
        self.failed = False
        self.nodes: dict[int, Any] = {}
        self.new = new
        if new:
            self.file.objects = {}
            self.file.externals = []
            self.file.script_types = []
            self.file.ref_types = []
            self.file.userInformation = ""
            self.file._cache.clear()
            self.bundle.files = {self.file.name: self.file}
            self.entries = []
            self.index = self._clone(self.index, 1)
            self.index_fields["m_PreloadTable"] = []
            self.index_fields["m_MainAsset"] = {
                "preloadIndex": 0,
                "preloadSize": 0,
                "asset": {"m_FileID": 0, "m_PathID": 0},
            }
            for key in ("m_Dependencies", "m_SceneHashes"):
                if key in self.index_fields:
                    self.index_fields[key] = []
            if "m_IsStreamedSceneAssetBundle" in self.index_fields:
                self.index_fields["m_IsStreamedSceneAssetBundle"] = False
        if independent or cab_name is not None:
            self._rename_cab(cab_name or f"CAB-{hashlib.md5(self.bundle_name.encode('utf-8')).hexdigest()}")
        self.index_fields["m_Name"] = self.bundle_name
        if "m_AssetBundleName" in self.index_fields:
            self.index_fields["m_AssetBundleName"] = self.bundle_name

    def _rename_cab(self, name: str) -> None:
        if not isinstance(name, str) or name in ("", ".", "..") or any(c in name for c in ("/", "\\", "\0", ":")):
            raise ValueError("cab_name must be a nonempty file name")
        old = self.file.name
        if name == old:
            return
        if len(self.env.assets) != 1:
            raise ValueError("independent bundle identity requires a single serialized file")
        mapping = {
            key: name + key[len(old) :] for key in self.bundle.files if isinstance(key, str) and key.startswith(old)
        }
        mapping[old] = name

        def remap(value: Any) -> Any:
            if isinstance(value, str):
                for source, target in sorted(mapping.items(), key=lambda item: -len(item[0])):
                    value = value.replace(f"archive:/{old}/{source}", f"archive:/{name}/{target}")
                return value
            if isinstance(value, dict):
                return {key: remap(child) for key, child in value.items()}
            if isinstance(value, (tuple, list)):
                return [remap(child) for child in value]
            return value

        for obj in self.file.objects.values():
            if obj.type.name in ("AssetBundle", "TextAsset"):
                continue
            fields = _tree(obj)
            changed = remap(fields)
            if changed != fields:
                _save(obj, changed)
        self.bundle.files = {
            mapping.get(key, key) if isinstance(key, str) else key: value for key, value in self.bundle.files.items()
        }
        self.file.name = name

    def _clone(self, source, path_id: int):
        if isinstance(path_id, bool) or not isinstance(path_id, int) or not 0 < path_id < 1 << 63:
            raise ValueError("path_id must be a positive signed 64-bit integer")
        if path_id in self.file.objects:
            raise ValueError(f"path_id {path_id} already exists")
        serialized_type = source.serialized_type
        type_id = next((i for i, typ in enumerate(self.file.types) if typ is serialized_type), None)
        if type_id is None:
            serialized_type = copy.copy(serialized_type)
            if serialized_type is None:
                raise ValueError("template object has no serialized type metadata")
            if self.file._enable_type_tree and serialized_type.node is None:
                original_node = source._get_typetree_node()
                serialized_type.node = _node(
                    [{k: v for k, v in row.items() if k != "m_Children"} for row in original_node.to_dict_list()]
                )
                serialized_type.type_dependencies = serialized_type.type_dependencies or ()
            self.file.types.append(serialized_type)
            type_id = len(self.file.types) - 1
        obj = evolve(source, assets_file=self.file, path_id=path_id, type_id=type_id, serialized_type=serialized_type)
        _set(obj, _raw(source))
        self.file.objects[path_id] = obj
        return obj

    def select(self, selector: dict, *, templates: bool = False):
        objects = self.templates if templates else list(self.file.objects.values())
        matches = [
            o
            for o in objects
            if ("path_id" not in selector or o.path_id == selector["path_id"])
            and (not selector.get("type") or o.type.name == selector["type"])
            and (not selector.get("name") or _name(o) == selector["name"])
        ]
        if len(matches) != 1:
            raise ValueError(f"asset selector must match exactly one object; matched {len(matches)}")
        return matches[0]

    def add_external(self, path: str, *, guid: str = "0" * 32, type: int = 0) -> int:
        """Register a serialized-file reference and return its one-based PPtr file ID."""
        if self.failed:
            raise ValueError("builder contains a failed operation; create a new builder")
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("external path must be a nonempty string without null characters")
        encoded_guid = bytes.fromhex(guid)
        if len(encoded_guid) != 16 or not isinstance(type, int) or isinstance(type, bool) or type not in range(4):
            raise ValueError("external guid must have 32 hexadecimal digits and type must be in 0..3")
        identifier = FileIdentifier.__new__(FileIdentifier)
        identifier.path, identifier.temp_empty, identifier.guid, identifier.type = path, "", encoded_guid, type
        self.file.externals.append(identifier)
        return len(self.file.externals)

    def _source(self, spec: dict, base: Path):
        selector = spec.get("template") or {"type": spec.get("type")}
        if isinstance(selector, str):
            selector = {"name": selector, "type": spec.get("type")}
        if template_file := selector.get("file"):
            env = UnityPy.load(str(base / template_file))
            candidates = [
                o
                for o in env.objects
                if ("path_id" not in selector or o.path_id == selector["path_id"])
                and (not selector.get("name") or _name(o) == selector["name"])
                and (not selector.get("type") or o.type.name == selector["type"])
            ]
            if len(candidates) != 1:
                raise ValueError("external template selector must match exactly one object")
            source = candidates[0]
            if (
                source.assets_file.unity_version,
                source.assets_file.header.version,
                source.assets_file.header.endian,
                source.assets_file.target_platform,
            ) != (
                self.file.unity_version,
                self.file.header.version,
                self.file.header.endian,
                self.file.target_platform,
            ):
                raise ValueError("asset template Unity version, format, endian and platform must match the bundle")
            return source
        if schema := spec.get("schema"):
            from UnityPy.enums import ClassIDType

            typ = SerializedType.__new__(SerializedType)
            typ.__attrs_init__(schema["class_id"])  # pyright: ignore[reportAttributeAccessIssue]
            typ.is_stripped_type = False
            typ.node = _node(schema["nodes"])
            typ.old_type_hash = bytes.fromhex(schema["type_hash"])
            if len(typ.old_type_hash) != 16:
                raise ValueError("schema type_hash must contain 32 hexadecimal digits")
            typ.script_type_index = schema.get("script_type_index", -1)
            typ.script_id = bytes.fromhex(schema["script_id"]) if schema.get("script_id") else None
            if typ.class_id == 114 and (typ.script_id is None or len(typ.script_id) != 16):
                raise ValueError("MonoBehaviour schema requires a 32-digit script_id")
            typ.type_dependencies = tuple(schema.get("dependencies", ()))
            typ.type_tree_content_hash = bytes(16)
            return ObjectReader(
                self.file, EndianBinaryReader(b""), 0, 0, typ, typ.class_id, ClassIDType(typ.class_id), 0, 0, 0, 0, b""
            )
        return self.select(selector, templates=True)

    def apply(self, spec: dict, *, base: Path = Path(".")) -> None:
        """Apply an add, replace or rename operation. File paths are relative to ``base``."""
        if self.failed:
            raise ValueError("builder contains a failed operation; create a new builder")
        try:
            self._apply(spec, base=base)
        except BaseException:
            self.failed = True
            raise

    def _apply(self, spec: dict, *, base: Path) -> None:
        operation = spec.get("op", "add")
        if operation not in ("add", "replace", "rename"):
            raise ValueError(f"unknown asset operation {operation!r}")
        kind = spec.get("type")
        if operation == "add":
            if not spec.get("name"):
                raise ValueError("new assets require a nonempty name")
            source = self._source(spec, base)
            obj = self._clone(source, spec.get("path_id", max(self.file.objects, default=0) + 1))
        else:
            obj = self.select(spec.get("target") or {k: spec[k] for k in ("name", "path_id") if k in spec})
            source = obj
        if obj.type.name == "AssetBundle":
            raise ValueError("the AssetBundle index is managed by the builder")
        name = spec.get("new_name") or (spec.get("name") if operation == "add" else _name(obj))
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError("asset name must be a nonempty string without null characters")
        nodes = _node(spec["typetree"]) if spec.get("typetree") else None
        if nodes:
            self.nodes[obj.path_id] = nodes
            if obj.serialized_type is None:
                raise ValueError("object has no serialized type metadata")
            typ = copy.copy(obj.serialized_type)
            typ.node = nodes
            self.file.types.append(typ)
            obj.serialized_type, obj.type_id = typ, len(self.file.types) - 1
        schema_node = nodes or obj._get_typetree_node()
        if (
            self.new
            and kind != "raw"
            and any(
                child.m_Type in ("ManagedReferencesRegistry", "ReferencedObjectData")
                for child in schema_node.traverse()
            )
        ):
            raise ValueError("managed-reference objects require raw serialized data with caller-managed metadata")
        if operation == "rename":
            if obj.type.name == "TextAsset":
                payload = parse_text_asset(_raw(obj), endian=self.file.header.endian).payload
                _set(obj, build_text_asset(name, payload, endian=self.file.header.endian))
            else:
                fields = _tree(obj, nodes)
                fields["m_Name"] = name
                _save(obj, fields, nodes)
        elif kind == "raw":
            if not spec.get("managed_references"):
                raise ValueError("raw objects require managed_references=true")
            _set(obj, (base / spec["file"]).read_bytes())
            self.raw_objects.add(obj.path_id)
        elif kind == "serialized":
            fields = spec.get("fields")
            if spec.get("file"):
                fields = json.loads((base / spec["file"]).read_text(encoding="utf-8"))
            if "patch" in spec:
                if fields is not None:
                    raise ValueError("serialized fields and patch are mutually exclusive")
                fields = _merge(copy.deepcopy(_tree(source, nodes)), spec["patch"])
            if not isinstance(fields, dict):
                raise ValueError("serialized objects require complete fields or a JSON file")
            fields = copy.deepcopy(fields)
            _binary_fields(fields, spec.get("binary_fields", []), base)
            if "m_Name" in fields:
                fields["m_Name"] = name
            _save(obj, fields, nodes)
            self.checked.add(obj.path_id)
        elif obj.type.name == "TextAsset":
            node = nodes or obj._get_typetree_node()
            if [child.m_Name for child in node.m_Children] != ["m_Name", "m_Script"]:
                raise ValueError("binary TextAsset requires the m_Name/m_Script schema")
            _set(obj, build_text_asset(name, _payload(spec, base), endian=self.file.header.endian))
        elif obj.type.name in ("Texture2D", "AudioClip"):
            fields = _merge(copy.deepcopy(_tree(source, nodes)), spec.get("fields", {}))
            fields["m_Name"] = name
            if obj.type.name == "Texture2D":
                from PIL import Image
                from UnityPy.export.Texture2DConverter import image_to_texture2d

                with Image.open(base / spec["file"]) as image:
                    if image.format != "PNG":
                        raise ValueError("Texture2D input must be a PNG image")
                    pixels, format_id = image_to_texture2d(
                        image.convert("RGBA"), 4, obj.platform, fields.get("m_PlatformBlob")
                    )
                    _merge(
                        fields,
                        {
                            "m_Width": image.width,
                            "m_Height": image.height,
                            "m_TextureFormat": int(format_id),
                            "m_CompleteImageSize": len(pixels),
                            "image data": pixels,
                        },
                    )
                for key, value in (
                    ("m_MipCount", 1),
                    ("m_MipsStripped", 0),
                    ("m_MipMap", False),
                    ("m_StreamingMipmaps", False),
                    ("m_ImageCount", 1),
                    ("m_TextureDimension", 2),
                ):
                    if key in fields:
                        fields[key] = value
                if "m_StreamData" in fields:
                    fields["m_StreamData"] = {"offset": 0, "size": 0, "path": ""}
            else:
                bank, audio_fields = wav_to_fsb5((base / spec["file"]).read_bytes(), name)
                _merge(fields, {key: value for key, value in audio_fields.items() if key in fields})
                if "m_Resource" in fields:
                    resource = f"{self.file.name}.resource"
                    resources = cast(dict[str, Any], self.bundle.files)
                    suffix = 0
                    while resource in resources and not isinstance(resources[resource], EndianBinaryWriter):
                        suffix += 1
                        resource = f"{self.file.name}_{suffix}.resource"
                    writer = self.bundle.get_writeable_cab(resource)
                    if writer is None:
                        raise ValueError("bundle cannot create an audio resource")
                    offset = writer.Position
                    writer.write(bank)
                    fields["m_Resource"] = {
                        "m_Source": f"archive:/{self.file.name}/{resource}",
                        "m_Offset": offset,
                        "m_Size": len(bank),
                    }
                elif "m_AudioData" in fields:
                    fields["m_AudioData"] = bank
                else:
                    raise ValueError("AudioClip schema has no m_Resource or m_AudioData field")
                for key, value in (
                    ("m_LoadType", 0),
                    ("m_PreloadAudioData", True),
                    ("m_LoadInBackground", False),
                    ("m_IsTrackerFormat", False),
                ):
                    if key in fields:
                        fields[key] = value
            _save(obj, fields, nodes)
            self.checked.add(obj.path_id)
        else:
            raise ValueError("use type='serialized' or type='raw' for this object class")
        if kind not in ("serialized", "raw") and kind and kind != obj.type.name:
            raise ValueError("asset type does not match the selected template")
        prior = [key for key, entry in self.entries if entry["asset"] == {"m_FileID": 0, "m_PathID": obj.path_id}]
        previous_locations = {key: self.locations[key] for key in prior if key in self.locations}
        container = spec.get("new_container") or spec.get("container")
        if operation == "add":
            container = container or name
        if container:
            if container in {key for key, _ in self.entries} - set(prior):
                raise ValueError(f"container path {container!r} already exists")
            self.entries = [(key, entry) for key, entry in self.entries if key not in prior]
            self.entries.append(
                (container, {"preloadIndex": 0, "preloadSize": 0, "asset": {"m_FileID": 0, "m_PathID": obj.path_id}})
            )
            for key in prior:
                self.locations.pop(key, None)
            if len(previous_locations) == 1:
                self.locations[container] = next(iter(previous_locations.values()))
            prior = [container]
        catalog_fields = (
            "primary_key",
            "resource_type",
            "aliases",
            "dependency_keys",
            "resource_type_assembly",
            "provider",
        )
        overrides = self.catalog_overrides.setdefault(obj.path_id, {})
        overrides.update({name: copy.deepcopy(spec[name]) for name in catalog_fields if name in spec})
        for key in prior:
            location = dict(self.locations.get(key, {}))
            location.setdefault("primary_key", key)
            location["internal_id"] = key
            location.setdefault("resource_type", f"UnityEngine.{obj.type.name}")
            for name in catalog_fields:
                if name in spec:
                    location[name] = spec[name]
            self.locations[key] = location

    def build(self) -> BuildResult:
        """Validate generated field references, rebuild preload ranges and save with ``packer='original'``."""
        if self.failed:
            raise ValueError("builder contains a failed operation; create a new builder")
        for path_id in self.checked:
            for ref in _references(_tree(self.file.objects[path_id], self.nodes.get(path_id))):
                if ref["m_PathID"] and (
                    (ref["m_FileID"] == 0 and ref["m_PathID"] not in self.file.objects)
                    or not 0 <= ref["m_FileID"] <= len(self.file.externals)
                ):
                    raise ValueError(f"object {path_id} contains an unresolved reference {ref}")

        # Serialized MonoBehaviour types index the file's script-reference table.
        # Derive that index from each object's final script pointer after edits.
        for obj in list(self.file.objects.values()):
            if obj.type.name != "MonoBehaviour" or obj.path_id in self.raw_objects:
                continue
            if obj.serialized_type is None:
                raise ValueError("MonoBehaviour has no serialized type metadata")
            script = _tree(obj, self.nodes.get(obj.path_id)).get("m_Script")
            if not isinstance(script, dict) or not script.get("m_PathID"):
                continue
            reference = (script["m_FileID"], script["m_PathID"])
            script_index = next(
                (
                    i
                    for i, item in enumerate(self.file.script_types)
                    if (item.local_serialized_file_index, item.local_identifier_in_file) == reference
                ),
                len(self.file.script_types),
            )
            if script_index == len(self.file.script_types):
                item = LocalSerializedObjectIdentifier.__new__(LocalSerializedObjectIdentifier)
                item.local_serialized_file_index, item.local_identifier_in_file = reference
                self.file.script_types.append(item)
            if obj.serialized_type.script_type_index != script_index:
                typ = copy.copy(obj.serialized_type)
                typ.script_type_index = script_index
                self.file.types.append(typ)
                obj.serialized_type, obj.type_id = typ, len(self.file.types) - 1
        for obj in self.file.objects.values():
            if obj.serialized_type is None or obj.serialized_type.script_type_index >= len(self.file.script_types):
                raise ValueError(f"object {obj.path_id} contains an unresolved script type index")

        def closure(pointer: dict) -> list[dict]:
            pending, seen, pointers = [pointer], set(), []
            while pending:
                ref = pending.pop(0)
                key = ref["m_FileID"], ref["m_PathID"]
                if key in seen or not ref["m_PathID"]:
                    continue
                seen.add(key)
                pointers.append(ref)
                if ref["m_FileID"] == 0 and ref["m_PathID"] not in self.raw_objects:
                    if ref["m_PathID"] not in self.file.objects:
                        raise ValueError(f"container contains an unresolved local reference {ref}")
                    obj = self.file.objects[ref["m_PathID"]]
                    if obj.type.name != "TextAsset":
                        pending.extend(_references(_tree(obj, self.nodes.get(obj.path_id))))
            return pointers

        preload = []
        for _, entry in self.entries:
            root = entry["asset"]
            if root["m_FileID"] == 0 and root["m_PathID"] in self.raw_objects and not self.new:
                previous = self.index_fields["m_PreloadTable"]
                start, size = entry["preloadIndex"], entry["preloadSize"]
                if start < 0 or start + size > len(previous):
                    raise ValueError("container preload range is outside the preload table")
                entry["preloadIndex"] = len(preload)
                preload.extend(previous[start : start + size])
            else:
                start = len(preload)
                preload.extend(closure(root))
                entry["preloadIndex"], entry["preloadSize"] = start, len(preload) - start
        self.index_fields["m_PreloadTable"] = preload
        main = self.index_fields.get("m_MainAsset")
        if main and main["asset"]["m_PathID"]:
            matching = next((entry for _, entry in self.entries if entry["asset"] == main["asset"]), None)
            if matching:
                main["preloadIndex"], main["preloadSize"] = matching["preloadIndex"], matching["preloadSize"]
            else:
                main["preloadIndex"] = len(preload)
                refs = closure(main["asset"])
                preload.extend(refs)
                main["preloadSize"] = len(refs)
        self.index_fields["m_Container"] = self.entries
        _save(self.index, self.index_fields)
        if self.new:
            used = {id(o.serialized_type) for o in self.file.objects.values()}
            self.file.types = [typ for typ in self.file.types if id(typ) in used]
            for obj in self.file.objects.values():
                obj.type_id = next(i for i, typ in enumerate(self.file.types) if typ is obj.serialized_type)
        data = self.bundle.save(packer="original")
        header = bundlefs.parse_header(data)
        if (header.version, header.engine, header.revision, header.flags) != (
            self.header.version,
            self.header.engine,
            self.header.revision,
            self.header.flags,
        ):
            raise ValueError("saved UnityFS header does not match the template")
        reread = UnityPy.load(data)
        file = cast(SerializedFile, next(f for f in reread.assets if f.name == self.file.name))
        if file.unity_version != self.file.unity_version or set(file.objects) != set(self.file.objects):
            raise ValueError("saved serialized file does not match the object table")
        for path_id, obj in self.file.objects.items():
            if file.objects[path_id].get_raw_data() != _raw(obj):
                raise ValueError(f"saved object {path_id} does not match its serialized payload")
        for key, entry in self.entries:
            pointer = file.container[key]
            if pointer.path_id != entry["asset"]["m_PathID"]:
                raise ValueError(f"saved container entry {key!r} does not resolve to its asset")
        assets = [
            self.locations.get(key, {"primary_key": key, "internal_id": key, "resource_type": "UnityEngine.Object"})
            for key, _ in self.entries
        ]
        catalog_edits = []
        for key, entry in self.entries:
            path_id = entry["asset"]["m_PathID"]
            if entry["asset"]["m_FileID"] != 0 or path_id not in self.catalog_overrides:
                continue
            source_paths = self.original_containers.get(path_id, [])
            catalog_edits.append(
                {
                    "source_internal_ids": [key] if key in source_paths else list(source_paths),
                    "internal_id": key,
                    "overrides": copy.deepcopy(self.catalog_overrides[path_id]),
                }
            )
        return BuildResult(data, self.bundle_name, assets, catalog_edits)


def build_manifest(doc: dict, *, base: Path = Path(".")) -> BuildResult:
    """Build a JSON manifest; relative template and payload paths resolve against ``base``."""
    return builder_from_manifest(doc, base=base).build()


def builder_from_manifest(doc: dict, *, base: Path = Path(".")) -> BundleBuilder:
    """Apply manifest operations and return an in-memory builder for further asset operations."""
    if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
        raise ValueError("build manifest requires a template and an assets array")
    mode = doc.get("mode", "new")
    if mode not in ("new", "edit"):
        raise ValueError("build mode must be 'new' or 'edit'")
    if not isinstance(doc.get("template"), str) or not doc["template"]:
        raise ValueError("build manifest requires a template file")
    builder = BundleBuilder(
        base / doc["template"],
        new=mode == "new",
        bundle_name=doc.get("bundle_name"),
        serialized_file=doc.get("serialized_file"),
        cab_name=doc.get("cab_name"),
    )
    for external in doc.get("externals", []):
        builder.add_external(**external)
    if "dependencies" in doc:
        if "m_Dependencies" not in builder.index_fields:
            raise ValueError("AssetBundle schema has no dependency names field")
        if not isinstance(doc["dependencies"], list) or any(
            not isinstance(name, str) or not name for name in doc["dependencies"]
        ):
            raise ValueError("bundle dependencies must be nonempty strings")
        builder.index_fields["m_Dependencies"] = doc["dependencies"]
    for spec in doc["assets"]:
        if not isinstance(spec, dict):
            raise ValueError("asset operations must be objects")
        builder.apply(spec, base=base)
    return builder


def replace_text_asset(
    bundle_in, bundle_out, name: str, payload: bytes, *, new_name: str | None = None, packer: str = "original"
) -> None:
    """Replace binary TextAsset bytes while preserving the bundle layout and existing container paths."""
    if packer != "original":
        raise ValueError("asset editing uses packer='original'")
    builder = BundleBuilder(bundle_in, new=False)
    try:
        obj = builder.select({"type": "TextAsset", "name": name})
    except ValueError:
        raise KeyError(f"no unique TextAsset {name!r} in the bundle") from None
    _set(obj, build_text_asset(new_name or name, payload, endian=builder.file.header.endian))
    write_outputs({Path(bundle_out): builder.build().data})
