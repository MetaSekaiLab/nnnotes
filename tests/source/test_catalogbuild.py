import pytest

from nnnotes.source import addressables, catalogbuild
from tests.helpers import synth

BUNDLE = "https://dummy.net/asset/Android/x_01.bundle"
BUNDLE_NAME = "x_01.bundle"
CUSTOM_PROVIDER = "ExampleEncryptedBundleProvider"


def small_catalog() -> bytes:
    return synth.CatalogWriter().build(
        [
            ("x_01.bundle", BUNDLE, []),
            ("Char/A", "Assets/Game/Char/A.prefab", [0]),
        ]
    )


def provider_catalog(*providers: str) -> bytes:
    return synth.CatalogWriter().build(
        [
            (
                f"source_{i}.bundle",
                synth.remote(f"source_{i}.bundle"),
                [],
                {"provider": provider, "extra": {"flags": 8, "timeout": 60, "retryCount": 2}},
            )
            for i, provider in enumerate(providers)
        ]
    )


def test_bundle_settings_inherit_a_unique_provider_and_require_ambiguous_selection():
    assert catalogbuild.bundle_settings(provider_catalog(CUSTOM_PROVIDER)) == (CUSTOM_PROVIDER, {})
    assert catalogbuild.bundle_settings(synth.CatalogWriter().build([])) == (catalogbuild.BUNDLE_PROVIDER, {})
    mixed = provider_catalog(CUSTOM_PROVIDER, catalogbuild.BUNDLE_PROVIDER)
    with pytest.raises(ValueError, match="multiple bundle providers"):
        catalogbuild.bundle_settings(mixed)
    assert catalogbuild.bundle_settings(mixed, provider="ExplicitBundleProvider") == ("ExplicitBundleProvider", {})


def test_bundle_template_inherits_common_options_and_explicit_fields_take_precedence():
    catalog = provider_catalog(CUSTOM_PROVIDER, catalogbuild.BUNDLE_PROVIDER)
    provider, options = catalogbuild.bundle_settings(
        catalog, template="source_0.bundle", request_options={"timeout": 120}
    )
    assert provider == CUSTOM_PROVIDER
    assert options == {"flags": 8, "timeout": 120, "redirectLimit": 32, "retryCount": 2}
    assert (
        catalogbuild.bundle_settings(
            catalog, template=synth.remote("source_0.bundle"), provider="ExplicitBundleProvider"
        )[0]
        == "ExplicitBundleProvider"
    )
    with pytest.raises(ValueError, match="select exactly one"):
        catalogbuild.bundle_settings(catalog, template="absent.bundle")


def test_generated_and_low_level_bundle_locations_use_the_source_provider():
    catalog = provider_catalog(CUSTOM_PROVIDER)
    generated = catalogbuild.bundle_entries(
        synth.remote("generated.bundle"), "generated", [], hash="11" * 16, crc=0, bundle_size=32, catalog=catalog
    )
    assert generated[0]["provider"] == CUSTOM_PROVIDER
    low_level = {"primary_key": "low.bundle", "internal_id": synth.remote("low.bundle"), "extra_data": {"crc": 0}}
    rebuilt = catalogbuild.rebuild(catalog, add=[*generated, low_level])
    assert all(loc["provider"] == CUSTOM_PROVIDER for loc in addressables.parse_locations(rebuilt))


@pytest.mark.parametrize("provider", ["", 4, "bad\0provider"])
def test_bundle_provider_rejects_invalid_identifiers(provider):
    with pytest.raises(ValueError, match="nonempty string"):
        catalogbuild.bundle_settings(provider=provider)


def test_location_replacement_preserves_shared_keys_and_dependency_targets():
    data = catalogbuild.rebuild(
        small_catalog(),
        add=[
            {"primary_key": "Example/Text", "internal_id": "assets/text.bytes", "aliases": ["shared", 42]},
            {"primary_key": "Example/Text", "internal_id": "assets/other.bytes", "aliases": ["shared", 42]},
        ],
    )
    original = addressables.parse_locations(data)
    target = next(loc for loc in original if loc["internal_id"] == "assets/text.bytes")
    output = catalogbuild.rebuild(
        data, replace={target["offset"]: {"primary_key": "Example/Updated", "aliases": ["new", 43]}}
    )
    locations = {loc["internal_id"]: loc for loc in addressables.parse_locations(output)}
    memberships = {key["key"]: key["locations"] for key in addressables.parse_keys(output)}
    assert (
        memberships["Example/Text"]
        == memberships["shared"]
        == memberships[42]
        == [locations["assets/other.bytes"]["offset"]]
    )
    assert (
        memberships["Example/Updated"]
        == memberships["new"]
        == memberships[43]
        == [locations["assets/text.bytes"]["offset"]]
    )
    assert locations["Assets/Game/Char/A.prefab"]["dependencies"] == [locations[BUNDLE]["offset"]]


def test_edit_entries_preserve_multiple_asset_locations_and_ignore_other_bundles():
    data = synth.CatalogWriter().build(
        [
            ("source.bundle", synth.remote("source.bundle"), [], {"provider": CUSTOM_PROVIDER}),
            ("other.bundle", synth.remote("other.bundle"), []),
            (
                "Example/Text",
                "assets/text.bytes",
                [0, 1],
                {"provider": "Example.TextProvider", "type": "UnityEngine.TextAsset"},
            ),
            (
                "Example/Second",
                "assets/text.bytes",
                [0],
                {"provider": "Example.SecondProvider", "type": "UnityEngine.TextAsset"},
            ),
            (
                "Example/Text",
                "assets/text.bytes",
                [1],
                {"provider": "Example.OtherProvider", "type": "UnityEngine.TextAsset"},
            ),
        ]
    )
    added, replacements = catalogbuild.edit_entries(
        data,
        synth.remote("source.bundle"),
        "source",
        [],
        [{"source_internal_ids": ["assets/text.bytes"], "internal_id": "assets/new.bytes", "overrides": {}}],
        hash="a1" * 16,
        crc=123,
        bundle_size=456,
    )
    assert added == []
    output = catalogbuild.rebuild(data, replace=replacements)
    locations = addressables.parse_locations(output)
    changed = [loc for loc in locations if loc["internal_id"] == "assets/new.bytes"]
    assert {loc["primary_key"] for loc in changed} == {"Example/Text", "Example/Second"}
    assert {loc["provider"] for loc in changed} == {"Example.TextProvider", "Example.SecondProvider"}
    assert all(loc["resource_type"] == "UnityEngine.TextAsset" for loc in changed)
    unchanged = next(loc for loc in locations if loc["provider"] == "Example.OtherProvider")
    assert unchanged["internal_id"] == "assets/text.bytes"
    bundle = next(loc for loc in locations if loc["primary_key"] == "source.bundle")
    assert (bundle["extra_data"]["crc"], bundle["extra_data"]["bundleSize"]) == (123, 456)


def signatures(data: bytes):
    return sorted(
        (
            loc["primary_key"] or "",
            loc["internal_id"] or "",
            loc["resource_type"] or "",
            (loc.get("extra_data") or {}).get("hash"),
            (loc.get("extra_data") or {}).get("bundleSize"),
        )
        for loc in addressables.parse_locations(data)
    )


def test_rebuild_without_changes_keeps_every_entry():
    data = small_catalog()
    out = catalogbuild.rebuild(data)
    assert signatures(out) == signatures(data)
    assert len(addressables.parse_keys(out)) == len(addressables.parse_keys(data))


def test_rebuild_adds_a_bundle_and_its_asset():
    data = small_catalog()
    bundle = {
        "primary_key": "y_02.bundle",
        "internal_id": "https://dummy.net/asset/Android/y_02.bundle",
        "extra_data": {
            "hash": "11" * 16,
            "bundleName": "2222",
            "crc": 0,
            "bundleSize": 1234,
            "flags": 0,
            "timeout": 0,
            "redirectLimit": 0,
            "retryCount": 0,
        },
    }
    asset = {
        "primary_key": "Live/MusicScore/9001/9001_00",
        "internal_id": "Assets/AddressableResources/Live/MusicScore/9001/9001_00.bytes",
        "resource_type": "UnityEngine.TextAsset",
        "provider": "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
        "dependency_on_added": [0],
    }
    out = catalogbuild.rebuild(data, add=[bundle, asset])
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}
    assert "y_02.bundle" in locations and locations["y_02.bundle"]["resource_type"].endswith("IAssetBundleResource")
    music = locations["Live/MusicScore/9001/9001_00"]
    assert music["resource_type"] == "UnityEngine.TextAsset"
    assert music["dependencies"] == [locations["y_02.bundle"]["offset"]]
    keys = {k["key"]: k for k in addressables.parse_keys(out)}
    assert keys["Live/MusicScore/9001/9001_00"]["locations"] == [music["offset"]]
    assert (locations["y_02.bundle"]["extra_data"] or {})["bundleSize"] == 1234


def test_rebuild_drops_a_keyed_location():
    data = small_catalog()
    out = catalogbuild.rebuild(data, drop={"Char/A"})
    assert "Char/A" not in {loc["primary_key"] for loc in addressables.parse_locations(out)}
    assert "x_01.bundle" in {loc["primary_key"] for loc in addressables.parse_locations(out)}


def test_rebuild_updates_a_bundles_size_and_checksum():
    data = small_catalog()
    before = {loc["primary_key"]: (loc.get("extra_data") or {}) for loc in addressables.parse_locations(data)}
    out = catalogbuild.rebuild(data, update={BUNDLE_NAME: {"crc": 0x1234, "bundleSize": 999}})
    after = {loc["primary_key"]: (loc.get("extra_data") or {}) for loc in addressables.parse_locations(out)}
    assert (after["x_01.bundle"]["crc"], after["x_01.bundle"]["bundleSize"]) == (0x1234, 999)
    assert after["x_01.bundle"]["hash"] == before["x_01.bundle"]["hash"]  # a field we did not name is kept
    assert len(addressables.parse_locations(out)) == len(addressables.parse_locations(data))
    kept = [s for s in signatures(out) if s[0] != "x_01.bundle"]
    assert kept == [s for s in signatures(data) if s[0] != "x_01.bundle"]


def test_rebuild_does_not_invent_request_options():
    data = small_catalog()
    out = catalogbuild.rebuild(data, update={"A.prefab": {"crc": 1, "bundleSize": 2}})
    asset = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}["Char/A"]
    assert asset["extra_data"] is None  # not a bundle location: nothing to correct


def test_add_to_existing_key_preserves_all_locations_and_dependencies():
    data = small_catalog()
    original = {loc["primary_key"]: loc for loc in addressables.parse_locations(data)}
    out = catalogbuild.rebuild(
        data,
        add=[
            {
                "primary_key": "Char/A",
                "internal_id": "Assets/Game/Char/Alternative.prefab",
                "resource_type": "UnityEngine.GameObject",
                "dependency_keys": ["x_01.bundle"],
            }
        ],
    )
    locations = addressables.parse_locations(out)
    keys = addressables.parse_keys(out)
    assert len(locations) == 3
    assert len(keys) == 2
    bundle, first, second = locations
    assert first["internal_id"] == original["Char/A"]["internal_id"]
    assert second["internal_id"] == "Assets/Game/Char/Alternative.prefab"
    assert first["dependencies"] == second["dependencies"] == [bundle["offset"]]
    assert next(key for key in keys if key["key"] == "Char/A")["locations"] == [first["offset"], second["offset"]]


def test_added_assets_can_combine_existing_offsets_added_indices_and_keys():
    data = small_catalog()
    old_bundle = addressables.parse_locations(data)[0]["offset"]
    out = catalogbuild.rebuild(
        data,
        add=[
            {
                "primary_key": "shared.bundle",
                "internal_id": "https://example.invalid/shared.bundle",
                "extra_data": {"hash": "ab" * 16, "bundleName": "shared", "crc": 7, "bundleSize": 50},
            },
            {
                "primary_key": "Texture/Checker",
                "internal_id": "Assets/Checker.png",
                "resource_type": "UnityEngine.Texture2D",
                "dependencies": [old_bundle],
                "dependency_on_added": [0],
                "dependency_keys": ["shared.bundle", "x_01.bundle"],
            },
        ],
    )
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}
    assert locations["Texture/Checker"]["dependencies"] == [
        locations["x_01.bundle"]["offset"],
        locations["shared.bundle"]["offset"],
    ]


def test_bundle_entries_generate_options_aliases_and_resource_types():
    additions = catalogbuild.bundle_entries(
        "{Runtime.RemoteRoot}/custom.bundle",
        "custom_bundle",
        [
            {
                "primary_key": "Chart/Custom",
                "internal_id": "Assets/Custom.bytes",
                "resource_type": "UnityEngine.TextAsset",
                "aliases": ["chart", 42],
            },
            {
                "primary_key": "Audio/Custom",
                "internal_id": "Assets/Custom.wav",
                "resource_type": "UnityEngine.AudioClip",
            },
        ],
        hash="AB" * 16,
        crc=0xF2345678,
        bundle_size=4096,
        dependency_keys=["x_01.bundle"],
        request_options={"flags": 4, "timeout": 120, "retryCount": 2},
    )
    out = catalogbuild.rebuild(small_catalog(), add=additions)
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}
    bundle = locations["custom.bundle"]
    assert bundle["resource_type"] == catalogbuild.BUNDLE_TYPE
    assert bundle["resource_type_assembly"] == catalogbuild.RM_ASSEMBLY
    assert bundle["dependencies"] == [locations["x_01.bundle"]["offset"]]
    opts = bundle["extra_data"]
    assert (opts["bundleName"], opts["hash"], opts["crc"], opts["bundleSize"]) == (
        "custom_bundle",
        "ab" * 16,
        0xF2345678,
        4096,
    )
    assert (opts["timeout"], opts["redirectLimit"], opts["retryCount"], opts["flags"]) == (120, 32, 2, 4)
    for key in ("Chart/Custom", "Audio/Custom"):
        assert locations[key]["provider"] == catalogbuild.ASSET_PROVIDER
        assert locations[key]["resource_type_assembly"] == catalogbuild.CORE_ASSEMBLY
        assert locations[key]["dependencies"] == [bundle["offset"]]
    keys = {key["key"]: key for key in addressables.parse_keys(out)}
    assert keys[42]["type"] == "System.Int32"
    assert keys[42]["locations"] == keys["chart"]["locations"] == [locations["Chart/Custom"]["offset"]]
    rebuilt = catalogbuild.rebuild(out)
    assert signatures(rebuilt) == signatures(out)
    assert [(key["key"], key["type"]) for key in addressables.parse_keys(rebuilt)] == [
        (key["key"], key["type"]) for key in addressables.parse_keys(out)
    ]


def test_full_internal_id_update_takes_precedence_and_updates_added_entries():
    added = catalogbuild.bundle_entries(
        "https://example.invalid/new/x_01.bundle",
        "new_bundle",
        [],
        hash="00" * 16,
        crc=0,
        bundle_size=0,
        primary_key="new.bundle",
    )
    out = catalogbuild.rebuild(
        small_catalog(),
        add=added,
        update={
            "x_01.bundle": {"crc": 1, "bundleSize": 2},
            BUNDLE: {"crc": 3, "bundleSize": 4},
        },
    )
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}
    assert locations["x_01.bundle"]["extra_data"]["crc"] == 3
    assert locations["new.bundle"]["extra_data"]["crc"] == 1


@pytest.mark.parametrize(
    "dependency",
    [
        {"dependencies": [123456]},
        {"dependency_on_added": [-1]},
        {"dependency_on_added": [1]},
        {"dependency_keys": ["unknown.bundle"]},
    ],
)
def test_unavailable_added_dependencies_are_rejected(dependency):
    with pytest.raises(ValueError, match="dependency.*unavailable"):
        catalogbuild.rebuild(
            small_catalog(),
            add=[
                {"primary_key": "Chart/Custom", "internal_id": "Assets/Custom.bytes", **dependency},
            ],
        )


@pytest.mark.parametrize("hash", ["a" * 30, "gg" * 16, "ab " * 10 + "ab"])
def test_bundle_hash_has_exactly_sixteen_bytes(hash):
    with pytest.raises(ValueError, match="32 hexadecimal digits"):
        catalogbuild.bundle_entries(
            "https://example.invalid/custom.bundle", "custom", [], hash=hash, crc=0, bundle_size=0
        )


def test_drop_removes_empty_lookup_keys():
    out = catalogbuild.rebuild(small_catalog(), drop={"Char/A"})
    assert [key["key"] for key in addressables.parse_keys(out)] == ["x_01.bundle"]


def semantic_catalog(data):
    header = addressables.parse_header(data)
    header.pop("keysOffset")
    locations = addressables.parse_locations(data)
    index = {loc["offset"]: i for i, loc in enumerate(locations)}
    entries = []
    for loc in locations:
        entry = dict(loc)
        entry.pop("offset")
        entry["dependencies"] = [index[off] for off in loc["dependencies"]]
        entries.append(entry)
    keys = [dict(key, locations=[index[off] for off in key["locations"]]) for key in addressables.parse_keys(data)]
    return header, entries, keys


def test_roundtrip_preserves_header_unicode_options_types_and_typed_aliases():
    additions = catalogbuild.bundle_entries(
        "https://example.invalid/custom.bundle",
        "custom_bundle",
        [
            {
                "primary_key": "Chart/自作",
                "internal_id": "Assets/自作.bytes",
                "resource_type": "UnityEngine.TextAsset",
                "aliases": ["42", 42],
                "dependency_hash": -123456,
            },
        ],
        hash="fa" * 16,
        crc=0xFFFFFFFF,
        bundle_size=0xFFFFFFFF,
        request_options={"timeout": 32767, "redirectLimit": 255, "retryCount": 255, "flags": 31},
    )
    original = catalogbuild.rebuild(small_catalog(), add=additions)
    out = catalogbuild.rebuild(original)
    assert semantic_catalog(out) == semantic_catalog(original)
    typed_keys = {(key["type"], key["key"]) for key in addressables.parse_keys(out)}
    assert ("System.String", "42") in typed_keys
    assert ("System.Int32", 42) in typed_keys


def test_roundtrip_preserves_null_request_common_and_bundle_name():
    data = synth.CatalogWriter().build(
        [
            ("empty.bundle", synth.remote("empty.bundle"), [], {"extra": {"hash": None, "bundleName": None}}),
        ]
    )
    parsed = addressables.parse_locations(data)[0]
    extra = parsed["extra_data"]
    common = None
    for field in ("timeout", "redirectLimit", "retryCount", "flags"):
        extra[field] = common
    out = catalogbuild.rebuild(small_catalog(), add=[parsed])
    location = addressables.parse_locations(out)[-1]
    assert location["extra_data"]["bundleName"] is None
    assert all(location["extra_data"][field] is None for field in ("timeout", "redirectLimit", "retryCount", "flags"))
    assert semantic_catalog(catalogbuild.rebuild(out)) == semantic_catalog(out)


def test_unsupported_extra_data_is_reported():
    data = synth.CatalogWriter().build(
        [
            ("custom", "Assets/Custom.asset", [], {"extra_object": ("UnityEngine.CustomOptions", bytes(8))}),
        ]
    )
    with pytest.raises(ValueError, match="unsupported catalog extra data type"):
        catalogbuild.rebuild(data)


def test_bundle_update_fields_are_validated():
    with pytest.raises(ValueError, match="unsupported fields"):
        catalogbuild.rebuild(small_catalog(), update={BUNDLE_NAME: {"crcc": 7}})


def test_replacing_a_dropped_bundle_redirects_existing_dependencies():
    replacements = catalogbuild.bundle_entries(
        "https://example.invalid/custom.bundle",
        "custom",
        [],
        primary_key="x_01.bundle",
        hash="ab" * 16,
        crc=3,
        bundle_size=100,
    )
    out = catalogbuild.rebuild(small_catalog(), add=replacements, drop={"x_01.bundle"})
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out)}
    assert locations["Char/A"]["dependencies"] == [locations["x_01.bundle"]["offset"]]
    assert locations["x_01.bundle"]["internal_id"] == "https://example.invalid/custom.bundle"


def test_dropping_a_required_dependency_is_reported():
    with pytest.raises(ValueError, match="dependency offset.*unavailable"):
        catalogbuild.rebuild(small_catalog(), drop={"x_01.bundle"})


@pytest.mark.parametrize("fields", [{"crc": -1}, {"bundleSize": 2**32}, {"crc": 1.5}])
def test_request_options_integer_fields_are_validated(fields):
    with pytest.raises(ValueError, match="must be an integer"):
        catalogbuild.rebuild(small_catalog(), update={BUNDLE_NAME: fields})
