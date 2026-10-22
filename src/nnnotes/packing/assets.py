"""Song asset adapters over the template-driven Unity bundle builder."""

from __future__ import annotations

import copy
import hashlib
import io
from dataclasses import dataclass
from typing import Any, cast

import UnityPy
from PIL import Image

from ..formats import cri
from ..source import addressables, catalogbuild
from ..unity.pack import BuildResult, BundleBuilder, parse_text_asset
from .project import Target


@dataclass
class Template:
    data: bytes
    asset: dict
    bundle: dict
    dependencies: list[str]


def containers(env) -> dict:
    """Local container roots without resolving preload references or searching the host filesystem."""
    output = {}
    for file in env.assets:
        for key, pointer in file.container.items():
            if pointer.m_FileID != 0:
                raise ValueError("asset container roots must be local objects")
            output[key] = file.objects[pointer.path_id]
    return output


def select_template(target: Target, key: str, resource_type: str | None = None) -> Template:
    locations = addressables.parse_locations(target.catalog())
    by_offset = {item["offset"]: item for item in locations}
    assets = [
        item
        for item in locations
        if item["primary_key"] == key and (resource_type is None or item["resource_type"] == resource_type)
    ]
    if len(assets) != 1:
        raise ValueError(f"template key must select one asset with the requested type: {key}")
    asset = assets[0]
    pending = list(asset["dependencies"])
    candidates, seen = [], set()
    while pending:
        offset = pending.pop(0)
        if offset in seen:
            continue
        seen.add(offset)
        location = by_offset[offset]
        if location["resource_type"] == catalogbuild.BUNDLE_TYPE:
            candidates.append(location)
        pending.extend(location["dependencies"])
    for location in candidates:
        data = target.bundle(location)
        env = UnityPy.load(data)
        if asset["internal_id"] in containers(env):
            dependencies = [
                by_offset[offset]["primary_key"] for offset in asset["dependencies"] if offset != location["offset"]
            ]
            return Template(data, asset, location, dependencies)
    raise ValueError(f"template container is missing from local bundle dependencies: {key}")


def _asset_spec(template: Template, key: str, internal: str) -> dict:
    return {
        "primary_key": key,
        "internal_id": internal,
        "resource_type": template.asset["resource_type"],
        "resource_type_assembly": template.asset.get("resource_type_assembly"),
        "provider": template.asset["provider"],
        "dependency_keys": template.dependencies,
    }


def chart_bundle(template: Template, payload: bytes, name: str, bundle_name: str) -> BuildResult:
    builder = BundleBuilder(template.data, bundle_name=bundle_name)
    internal = f"Assets/AddressableResources/Live/MusicScore/{name}.bytes"
    builder.apply(
        {
            "type": "TextAsset",
            "name": name.rsplit("/", 1)[-1],
            "data": payload,
            "template": {"type": "TextAsset"},
            "container": internal,
        }
    )
    result = builder.build()
    asset = containers(UnityPy.load(result.data))[internal]
    if parse_text_asset(asset.get_raw_data(), endian=asset.assets_file.header.endian).payload != payload:
        raise ValueError("chart bundle did not preserve its payload")
    return BuildResult(result.data, result.bundle_name, [_asset_spec(template, "Live/MusicScore/" + name, internal)])


def jacket_bundle(template: Template, png: bytes, name: str, bundle_name: str, *, small: bool) -> BuildResult:
    builder = BundleBuilder(template.data, bundle_name=bundle_name)
    sprite = builder.select({"type": "Sprite"}, templates=True)
    fields = copy.deepcopy(sprite.read_typetree())
    source = builder.select({"path_id": fields["m_RD"]["texture"]["m_PathID"]}, templates=True)
    texture = source.read_typetree()
    width, height = texture["m_Width"], texture["m_Height"]
    with Image.open(io.BytesIO(png)) as image:
        if image.format != "PNG" or image.width != image.height:
            raise ValueError("jacket must be a square PNG")
        image = image.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        resized = output.getvalue()
    prefix = "Assets/AddressableResources/Image/Jacket/" + ("small/" if small else "")
    key = "Image/Jacket/" + ("small/" if small else "") + name
    # The texture encoder accepts PNG paths; its temporary input stays local to this adapter.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="nnnotes-jacket-") as directory:
        path = Path(directory) / "jacket.png"
        path.write_bytes(resized)
        builder.apply(
            {
                "type": "Texture2D",
                "name": name,
                "path_id": 2,
                "file": str(path),
                "template": {"type": "Texture2D", "path_id": source.path_id},
                "container": prefix + name + ".texture",
            }
        )
    fields["m_Name"] = name
    fields["m_RD"]["texture"] = {"m_FileID": 0, "m_PathID": 2}
    if fields["m_RD"].get("alphaTexture", {}).get("m_PathID"):
        fields["m_RD"]["alphaTexture"] = {"m_FileID": 0, "m_PathID": 0}
    if "m_SpriteAtlas" in fields:
        fields["m_SpriteAtlas"] = {"m_FileID": 0, "m_PathID": 0}
    if "m_RenderDataKey" in fields:
        guid = hashlib.md5(key.encode()).digest()
        fields["m_RenderDataKey"] = (
            {f"data[{i}]": int.from_bytes(guid[4 * i : 4 * i + 4], "little") for i in range(4)},
            fields["m_RenderDataKey"][1],
        )
    internal = prefix + name + ".png"
    builder.apply(
        {
            "type": "serialized",
            "template": {"type": "Sprite", "path_id": sprite.path_id},
            "name": name,
            "path_id": 3,
            "fields": fields,
            "container": internal,
        }
    )
    result = builder.build()
    reread = cast(Any, containers(UnityPy.load(result.data))[internal].read())
    if reread.m_RD.texture.deref().read().image.size != (width, height):
        raise ValueError("jacket Sprite does not resolve to the expected texture")
    return BuildResult(result.data, result.bundle_name, [_asset_spec(template, key, internal)])


def extract_acb(data: bytes, internal: str) -> bytes:
    env = UnityPy.load(data)
    root = containers(env)[internal]
    fields = cast(dict, root.read_typetree())
    if "_chunks" in fields:
        parts = []
        for pointer in fields["_chunks"]:
            if pointer["m_FileID"] != 0:
                raise ValueError("split ACB chunks must be local TextAssets")
            obj = root.assets_file.objects[pointer["m_PathID"]]
            parts.append(parse_text_asset(obj.get_raw_data(), endian=obj.assets_file.header.endian).payload)
        return cri.split_acb(parts)
    reference = fields["implementation"]["rid"]
    impl = next(item for item in fields["references"]["RefIds"] if item["rid"] == reference)
    return cri.embedded_acb(fields, impl)


def audio_bundle(template: Template, acb: bytes, sheet: str, bundle_name: str) -> BuildResult:
    source = containers(UnityPy.load(template.data))[template.asset["internal_id"]]
    fields = cast(dict, source.read_typetree())
    if "_chunks" in fields:
        builder = BundleBuilder(template.data, bundle_name=bundle_name)
        for external in source.assets_file.externals:
            builder.add_external(external.path, guid=external.guid.hex(), type=external.type)
        masked = bytes(byte ^ 0x5A for byte in acb)
        count = len(fields["_chunks"])
        if not count:
            raise ValueError("split ACB template has no chunks")
        pointers = []
        for index, source_chunk in enumerate(fields["_chunks"]):
            identity = index + 2
            part = masked[len(masked) * index // count : len(masked) * (index + 1) // count]
            builder.apply(
                {
                    "type": "TextAsset",
                    "template": {"path_id": source_chunk["m_PathID"]},
                    "path_id": identity,
                    "name": f"{sheet}-{index:03}",
                    "data": part,
                    "container": f"Assets/AddressableResources/Cri/Sound/MusicScore/{sheet}/{index:03}",
                }
            )
            pointers.append({"m_FileID": 0, "m_PathID": identity})
        fields.update(m_Name=sheet, _cueSheetName=sheet, _chunks=pointers)
        internal = f"Assets/AddressableResources/Cri/Sound/MusicScore/{sheet}.asset"
        builder.apply(
            {
                "type": "serialized",
                "template": {"path_id": source.path_id},
                "name": sheet,
                "path_id": count + 2,
                "fields": fields,
                "container": internal,
            }
        )
    else:
        builder = BundleBuilder(template.data, new=False, bundle_name=bundle_name)
        reference = fields["implementation"]["rid"]
        impl = next(item for item in fields["references"]["RefIds"] if item["rid"] == reference)
        if impl["type"]["class"] != "CriSerializedBytesAssetImpl":
            raise ValueError("unsupported embedded ACB implementation")
        impl["data"]["data"] = list(acb)
        internal = f"Assets/AddressableResources/Cri/Sound/MusicScoreShort/{sheet}.acb"
        builder.apply(
            {
                "op": "replace",
                "type": "serialized",
                "target": {"path_id": source.path_id},
                "new_name": sheet,
                "fields": fields,
                "container": internal,
            }
        )
    result = builder.build()
    if extract_acb(result.data, internal) != acb:
        raise ValueError("sound bundle did not preserve its ACB payload")
    return BuildResult(result.data, result.bundle_name, [_asset_spec(template, "Cri/Sound/" + sheet, internal)])
