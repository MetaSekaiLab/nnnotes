"""Write binary Addressables catalogs and generate locations for bundles and their assets.

`rebuild` preserves the header, typed key table and location dependencies while adding, removing or updating
locations. `bundle_entries` generates a bundle location with request options and asset locations that depend on it.
Request option CRCs describe the decompressed UnityFS content; bundle sizes describe the stored file.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from typing import Any

from . import addressables

__all__ = ["bundle_entries", "bundle_settings", "edit_entries", "rebuild"]

RM = "UnityEngine.ResourceManagement.ResourceProviders."
RM_ASSEMBLY = "Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
CORE_ASSEMBLY = "UnityEngine.CoreModule, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
LIB_ASSEMBLY = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
BUNDLE_PROVIDER = RM + "AssetBundleProvider"
ASSET_PROVIDER = RM + "BundledAssetProvider"
BUNDLE_TYPE = RM + "IAssetBundleResource"
NONE = addressables.NONE
HEADER = addressables.HEADER
LOCATION = addressables.LOCATION
REQUEST_OPTIONS = addressables.REQUEST_OPTIONS
FLAG_BITS = addressables.FLAG_BITS


class _Writer:
    """The catalog buffer: 32-byte header, then every value at an offset (arrays and strings carry their length)."""

    def __init__(self) -> None:
        self.buf = bytearray(HEADER.size)
        self._types: dict[tuple[str, str], int] = {}
        self._commons: dict[tuple[int, ...], int] = {}
        # the same id, provider or type name repeats in thousands of locations: write each value once
        self._texts: dict[str, int] = {}
        self._parts: dict[tuple[str, str], int] = {}
        self._arrays: dict[tuple[int, ...], int] = {}

    def blob(self, data: bytes) -> int:
        self.buf += struct.pack("<I", len(data))
        off = len(self.buf)
        self.buf += data
        return off

    def value(self, data: bytes) -> int:
        off = len(self.buf)
        self.buf += data
        return off

    def reserve(self, size: int) -> int:
        return self.value(bytes(size))

    def text(self, s: str) -> int:
        if s in self._texts:
            return self._texts[s]
        try:
            off = self.blob(s.encode("ascii"))
        except UnicodeEncodeError:
            off = self.blob(s.encode("utf-16-le")) | 0x80000000
        self._texts[s] = off
        return off

    def parts(self, s: str, sep: str) -> int:
        key = (s, sep)
        if key in self._parts:
            return self._parts[key]
        head = NONE
        for part in s.split(sep):
            head = self.value(struct.pack("<II", self.text(part), head)) | 0x40000000
        self._parts[key] = head
        return head

    def string(self, s: str | None, sep: str) -> int:
        if s is None:
            return NONE
        return self.parts(s, sep) if sep and sep in s else self.text(s)

    def array(self, values: Iterable[int]) -> int:
        values = tuple(values)
        if not values:
            return NONE
        if values in self._arrays:
            return self._arrays[values]
        off = self.blob(struct.pack(f"<{len(values)}I", *values))
        self._arrays[values] = off
        return off

    def type_data(self, cls: str, assembly: str = RM_ASSEMBLY) -> int:
        key = (cls, assembly)
        if key not in self._types:
            self._types[key] = self.value(struct.pack("<II", self.string(assembly, "."), self.string(cls, ".")))
        return self._types[key]

    def resolve_type(self, cls: str | None, assembly: str | None = None) -> int:
        """A TypeSerializer.Data for `cls`; `assembly` is what the source catalog said, else a guess."""
        if not cls:
            return NONE
        if not assembly:
            assembly = CORE_ASSEMBLY if cls.startswith("UnityEngine.") and not cls.startswith(RM) else RM_ASSEMBLY
        return self.type_data(cls, assembly)

    def object_init(self, init: dict[str, Any] | None) -> int:
        if not init:
            return NONE
        return self.value(
            struct.pack(
                "<III",
                self.text(init.get("id") or ""),
                self.type_data(init.get("type") or "", init.get("assembly") or RM_ASSEMBLY),
                NONE if init.get("data") is None else self.text(init["data"]),
            )
        )

    def key_object(self, key: str | int, cls: str | None) -> int:
        cls = cls or "System.String"
        if cls == "System.Int32":
            obj = self.value(struct.pack("<i", _integer(key, "catalog integer key", -(2**31), 2**31 - 1)))
        else:
            sep = "/" if isinstance(key, str) and "/" in key else ""
            obj = self.value(struct.pack("<IH", self.string(str(key), sep), ord(sep) if sep else 0) + bytes(2))
        return self.value(struct.pack("<II", self.type_data(cls, LIB_ASSEMBLY), obj))

    def request_options(self, extra: dict[str, Any]) -> int:
        common = (
            extra.get("timeout"),
            extra.get("redirectLimit"),
            extra.get("retryCount"),
            extra.get("flags"),
        )
        common_id = NONE
        if any(v is not None for v in common):
            values = (
                _integer(common[0] or 0, "bundle timeout", -(2**15), 2**15 - 1),
                _integer(common[1] or 0, "bundle redirectLimit", 0, 255),
                _integer(common[2] or 0, "bundle retryCount", 0, 255),
                _integer(common[3] or 0, "bundle flags", -(2**31), 2**31 - 1),
            )
            if values not in self._commons:
                self._commons[values] = self.value(struct.pack("<hBBi", *values))
            common_id = self._commons[values]
        hash_id = NONE if not extra.get("hash") else self.value(_hash_bytes(extra["hash"]))
        data = self.value(
            struct.pack(
                "<5I",
                hash_id,
                self.string(extra.get("bundleName"), "_"),
                _integer(extra.get("crc") or 0, "bundle crc", 0, 0xFFFFFFFF),
                _integer(extra.get("bundleSize") or 0, "bundleSize", 0, 0xFFFFFFFF),
                common_id,
            )
        )
        return self.value(struct.pack("<II", self.type_data(REQUEST_OPTIONS, RM_ASSEMBLY), data))


UPDATE_FIELDS = ("crc", "bundleSize", "hash", "bundleName")


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _hash_bytes(value: str) -> bytes:
    if len(value) != 32:
        raise ValueError("bundle hash must contain 32 hexadecimal digits")
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise ValueError("bundle hash must contain 32 hexadecimal digits") from None
    if len(raw) != 16:
        raise ValueError("bundle hash must contain 32 hexadecimal digits")
    return raw


def _entry(extra: dict[str, Any] | None) -> dict[str, Any]:
    """The AssetBundleRequestOptions fields a parsed location carries, in the writer's terms."""
    if not extra:
        return {}
    if extra.get("type") not in (None, REQUEST_OPTIONS):
        raise ValueError(f"unsupported catalog extra data type: {extra['type']}")
    return {
        "hash": extra.get("hash"),
        "bundleName": extra.get("bundleName"),
        "crc": extra.get("crc"),
        "bundleSize": extra.get("bundleSize"),
        "timeout": extra.get("timeout"),
        "redirectLimit": extra.get("redirectLimit"),
        "retryCount": extra.get("retryCount"),
        "flags": extra.get("flags"),
    }


def _provider(value: Any) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("bundle_provider must be a nonempty string without null characters")
    return value


def bundle_settings(
    data: bytes | None = None,
    *,
    provider: str | None = None,
    template: str | None = None,
    request_options: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Select a bundle provider and common request options from a source catalog.

    An explicit provider takes precedence. Otherwise, a selected bundle location supplies its provider, or the
    source catalog's unique bundle provider is used. Empty catalogs use the standard AssetBundleProvider.
    `template` selects one bundle by primary key or full internal ID and also supplies its common request options;
    explicit request options override those defaults. A catalog with multiple bundle providers needs a selection.
    """
    if provider is not None:
        provider = _provider(provider)
    locations = (
        [loc for loc in addressables.parse_locations(data) if loc.get("resource_type") == BUNDLE_TYPE]
        if data is not None
        else []
    )
    defaults: dict[str, Any] = {}
    if template is not None:
        matches = [loc for loc in locations if template in (loc["primary_key"], loc["internal_id"])]
        if len(matches) != 1:
            raise ValueError("catalog_template must select exactly one bundle location by key or internal ID")
        selected = matches[0]
        provider = provider or selected.get("provider")
        selected_options: dict[str, Any] = selected.get("extra_data") or {}
        defaults = {field: selected_options.get(field) for field in ("timeout", "redirectLimit", "retryCount", "flags")}
    if provider is None:
        providers = {loc["provider"] for loc in locations if loc.get("provider")}
        if len(providers) > 1:
            raise ValueError("catalog has multiple bundle providers; set bundle_provider or catalog_template")
        provider = next(iter(providers), BUNDLE_PROVIDER)
    return _provider(provider), {**defaults, **(request_options or {})}


def bundle_entries(
    internal_id: str,
    bundle_name: str,
    assets: Sequence[dict[str, Any]],
    *,
    hash: str,
    crc: int,
    bundle_size: int,
    primary_key: str | None = None,
    request_options: dict[str, Any] | None = None,
    dependency_keys: Sequence[str] = (),
    provider: str | None = None,
    catalog: bytes | None = None,
    template: str | None = None,
) -> list[dict[str, Any]]:
    """Locations for a bundle and the assets stored in its AssetBundle container.

    `internal_id` is the bundle's download URL or runtime path. `bundle_name` is the AssetBundle's internal name;
    `hash` is a 128-bit cache version encoded as 32 hexadecimal digits. Each asset specifies `primary_key`,
    `internal_id` (its container path) and `resource_type`, with optional `aliases`, `provider` and
    `resource_type_assembly`. Bundle dependencies and asset dependencies may name existing or added primary keys.
    `catalog` supplies provider defaults; `template` selects an existing bundle's provider and common options.
    An explicit `provider` or `request_options` takes precedence.
    """
    if not internal_id or not bundle_name:
        raise ValueError("bundle internal_id and bundle_name are required")
    _hash_bytes(hash)
    _integer(crc, "bundle crc", 0, 0xFFFFFFFF)
    _integer(bundle_size, "bundle_size", 0, 0xFFFFFFFF)
    provider, request_options = bundle_settings(
        catalog, provider=provider, template=template, request_options=request_options
    )
    key = primary_key or internal_id.rsplit("/", 1)[-1]
    extra = {
        "timeout": 0,
        "redirectLimit": 32,
        "retryCount": 0,
        "flags": 0,
        **(request_options or {}),
        "hash": hash.lower(),
        "crc": crc,
        "bundleSize": bundle_size,
        "bundleName": bundle_name,
    }
    out = [
        {
            "primary_key": key,
            "internal_id": internal_id,
            "provider": provider,
            "resource_type": BUNDLE_TYPE,
            "extra_data": extra,
            "dependency_keys": list(dependency_keys),
        }
    ]
    for asset in assets:
        if not all(asset.get(field) for field in ("primary_key", "internal_id", "resource_type")):
            raise ValueError("each bundle asset requires primary_key, internal_id and resource_type")
        location = dict(asset)
        location.setdefault("provider", ASSET_PROVIDER)
        location["dependency_keys"] = list(dict.fromkeys([key, *(asset.get("dependency_keys") or [])]))
        out.append(location)
    return out


def edit_entries(
    data: bytes,
    internal_id: str,
    bundle_name: str,
    assets: Sequence[dict[str, Any]],
    edits: Sequence[dict[str, Any]],
    *,
    hash: str,
    crc: int,
    bundle_size: int,
    primary_key: str | None = None,
    provider: str | None = None,
    request_options: dict[str, Any] | None = None,
    dependency_keys: Sequence[str] | None = None,
    template: str | None = None,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Incremental catalog locations for an edited bundle, preserving unrelated locations and lookup keys.

    `edits` identifies each final container path, its source container paths and explicit catalog field overrides.
    Existing asset locations match both source container and bundle dependency. Their types, providers, aliases
    and dependency graph remain intact unless overridden. New container paths produce added locations.
    """
    locations = addressables.parse_locations(data)
    bundles = [loc for loc in locations if loc.get("resource_type") == BUNDLE_TYPE]
    selector = template or internal_id
    matches = [loc for loc in bundles if selector in (loc["primary_key"], loc["internal_id"])]
    if not matches and template is None:
        matches = [loc for loc in bundles if loc["internal_id"].rsplit("/", 1)[-1] == internal_id.rsplit("/", 1)[-1]]
    if not matches:
        return bundle_entries(
            internal_id,
            bundle_name,
            assets,
            hash=hash,
            crc=crc,
            bundle_size=bundle_size,
            primary_key=primary_key,
            request_options=request_options,
            dependency_keys=dependency_keys or (),
            provider=provider,
            catalog=data,
            template=template,
        ), {}
    if len(matches) != 1:
        raise ValueError("edited bundle must match exactly one catalog location")
    bundle = matches[0]
    selected_provider, options = bundle_settings(
        data, provider=provider, template=bundle["internal_id"], request_options=request_options
    )
    generated = bundle_entries(
        internal_id,
        bundle_name,
        [],
        hash=hash,
        crc=crc,
        bundle_size=bundle_size,
        primary_key=primary_key or bundle["primary_key"],
        provider=selected_provider,
        request_options=options,
    )[0]
    if dependency_keys is None:
        generated.pop("dependency_keys")
    else:
        generated["dependencies"] = []
        generated["dependency_keys"] = list(dependency_keys)
    replacements: dict[int, dict[str, Any]] = {bundle["offset"]: generated}
    by_offset = {loc["offset"]: loc for loc in locations}

    def depends_on_bundle(location: dict[str, Any]) -> bool:
        pending = list(location.get("dependencies") or [])
        seen: set[int] = set()
        while pending:
            offset = pending.pop()
            if offset == bundle["offset"]:
                return True
            if offset not in seen:
                seen.add(offset)
                pending.extend((by_offset.get(offset) or {}).get("dependencies") or [])
        return False

    added: list[dict[str, Any]] = []
    asset_defaults = {asset["internal_id"]: asset for asset in assets}
    for edit in edits:
        container = edit["internal_id"]
        overrides = dict(edit.get("overrides") or {})
        matched = [
            loc
            for loc in locations
            if loc["internal_id"] in edit.get("source_internal_ids", ()) and depends_on_bundle(loc)
        ]
        for location in matched:
            fields = {"internal_id": container, **overrides}
            if "dependency_keys" in overrides:
                fields["dependencies"] = [bundle["offset"]]
            replacements[location["offset"]] = {**replacements.get(location["offset"], {}), **fields}
        if not matched:
            asset = {**asset_defaults[container], **overrides}
            asset.setdefault("provider", ASSET_PROVIDER)
            asset["dependencies"] = [bundle["offset"]]
            added.append(asset)
    return added, replacements


def rebuild(
    data: bytes,
    add: list[dict[str, Any]] | None = None,
    drop: set[str] | None = None,
    update: dict[str, dict[str, Any]] | None = None,
    replace: dict[int, dict[str, Any]] | None = None,
) -> bytes:
    """Write `data` with added locations, removed primary keys and updated bundle request options.

    Added locations specify `primary_key` and `internal_id`. Optional `aliases` add string or integer lookup keys.
    `dependencies` refers to old-catalog offsets, `dependency_on_added` to zero-based indices in `add`, and
    `dependency_keys` to existing or added primary keys. All three forms can be combined.

    `update` maps full internal ids or bundle file names to `crc`, `bundleSize`, `hash` and `bundleName` fields.
    It applies to existing and added request options; full internal ids take precedence over file names.
    `replace` updates selected old location offsets in place. Existing aliases and dependency edges survive;
    explicit `primary_key` and `aliases` fields update only that location's lookup memberships.
    """
    add = add or []
    drop = drop or set()
    update = update or {}
    replace = replace or {}
    for name, fields in update.items():
        if unknown := set(fields) - set(UPDATE_FIELDS):
            raise ValueError(f"bundle update {name}: unsupported fields {', '.join(sorted(unknown))}")
    header = addressables.parse_header(data)
    locations = addressables.parse_locations(data)
    keys = addressables.parse_keys(data)

    keep = [loc for loc in locations if loc["primary_key"] not in drop]
    original_by_offset = {loc["offset"]: loc for loc in locations}
    old_offsets = [loc["offset"] for loc in keep]
    index_of = {off: i for i, off in enumerate(old_offsets)}

    by_primary: dict[str, list[int]] = {}
    if any(offset not in index_of for offset in replace):
        raise ValueError("replacement offset is unavailable in the retained catalog")
    sources = [{**loc, **replace.get(loc["offset"], {})} for loc in keep] + add
    for i, src in enumerate(sources):
        if not isinstance(src.get("primary_key"), str) or not src["primary_key"]:
            raise ValueError("catalog locations require a nonempty string primary_key")
        if not isinstance(src.get("internal_id"), str) or not src["internal_id"]:
            raise ValueError(f"catalog location {src['primary_key']}: internal_id is required")
        by_primary.setdefault(src["primary_key"], []).append(i)

    key_locations: dict[tuple[str, str | int], list[int]] = {}

    def add_key(value: str | int, typ: str, indices: Sequence[int]) -> None:
        if typ not in ("System.String", "System.Int32"):
            raise ValueError(f"unsupported catalog key type: {typ}")
        if (
            typ == "System.String"
            and not isinstance(value, str)
            or typ == "System.Int32"
            and (not isinstance(value, int) or isinstance(value, bool))
        ):
            raise ValueError(f"catalog key does not match its type: {typ}")
        locs = key_locations.setdefault((typ, value), [])
        locs.extend(i for i in indices if i not in locs)

    for entry in keys:
        offsets: list[int] = entry.get("locations") or []
        indices: list[int] = []
        for offset in offsets:
            if offset not in index_of:
                continue
            fields = replace.get(offset, {})
            old_primary = original_by_offset[offset]["primary_key"]
            new_primary = fields.get("primary_key", old_primary)
            if entry["key"] == old_primary and new_primary != old_primary:
                continue
            if "aliases" in fields and entry["key"] != new_primary and entry["key"] not in fields["aliases"]:
                continue
            indices.append(index_of[offset])
        if indices:
            add_key(entry["key"], entry["type"], indices)
    for offset, fields in replace.items():
        slot = index_of[offset]
        add_key(sources[slot]["primary_key"], "System.String", [slot])
        aliases: list[str | int] = fields.get("aliases") or []
        for alias in aliases:
            add_key(alias, "System.Int32" if isinstance(alias, int) else "System.String", [slot])
    for j, src in enumerate(add):
        slot = len(keep) + j
        add_key(src["primary_key"], "System.String", [slot])
        aliases: list[str | int] = src.get("aliases") or []
        for alias in aliases:
            add_key(alias, "System.Int32" if isinstance(alias, int) else "System.String", [slot])

    w = _Writer()
    table_len = 8 * len(key_locations)
    w.buf += struct.pack("<I", table_len)
    keys_at = w.reserve(table_len)
    records = [w.reserve(28) for _ in keep] + [w.reserve(28) for _ in add]

    def write_location(i: int, src: dict[str, Any]) -> None:
        internal = src.get("internal_id") or ""
        extra = _entry(src.get("extra_data"))
        provider = src.get("provider") or (bundle_settings(data)[0] if extra else ASSET_PROVIDER)
        fields = update.get(internal, update.get(internal.rsplit("/", 1)[-1])) if extra else None
        if fields:
            extra.update(fields)
        dep_indices: list[int] = []
        dependencies: list[int] = src.get("dependencies") or []
        for offset in dependencies:
            if offset in index_of:
                dep_indices.append(index_of[offset])
            elif (original := original_by_offset.get(offset)) and original["primary_key"] in by_primary:
                dep_indices.extend(by_primary[original["primary_key"]])
            else:
                raise ValueError(f"catalog location {src['primary_key']}: dependency offset {offset} is unavailable")
        added_dependencies: list[Any] = src.get("dependency_on_added") or []
        for idx in added_dependencies:
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(add):
                raise ValueError(f"catalog location {src['primary_key']}: added dependency index {idx} is unavailable")
            dep_indices.append(len(keep) + idx)
        dependency_keys: list[str] = src.get("dependency_keys") or []
        for key in dependency_keys:
            if key not in by_primary:
                raise ValueError(f"catalog location {src['primary_key']}: dependency key {key} is unavailable")
            dep_indices.extend(by_primary[key])
        dep_ids = [records[idx] for idx in dict.fromkeys(dep_indices)]
        extra_id = NONE
        if extra:
            extra_id = w.request_options(extra)
        rtype = src.get("resource_type")
        if not rtype and extra:
            rtype = BUNDLE_TYPE
        struct.pack_into(
            "<IIIIiII",
            w.buf,
            records[i],
            w.string(src.get("primary_key") or "", "/"),
            w.string(internal, "/"),
            w.string(provider, "."),
            w.array(dep_ids),
            _integer(src.get("dependency_hash") or 0, "dependency_hash", -(2**31), 2**31 - 1),
            extra_id,
            w.resolve_type(rtype, src.get("resource_type_assembly")),
        )

    for i, src in enumerate(sources):
        write_location(i, src)

    for row, ((cls, value), idxs) in enumerate(key_locations.items()):
        struct.pack_into("<II", w.buf, keys_at + 8 * row, w.key_object(value, cls), w.array([records[i] for i in idxs]))

    init_objects: list[dict[str, Any] | None] = header.get("initObjects") or []
    struct.pack_into(
        "<iiIIIIII",
        w.buf,
        0,
        addressables.CATALOG_MAGIC,
        addressables.CATALOG_VERSION,
        keys_at,
        w.string(header.get("locatorId") or "", ""),
        w.object_init(header.get("instanceProvider")),
        w.object_init(header.get("sceneProvider")),
        w.array(w.object_init(i) for i in init_objects),
        w.string(header.get("buildResultHash") or "", ""),
    )
    return bytes(w.buf)
