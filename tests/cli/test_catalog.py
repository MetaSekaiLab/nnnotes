import hashlib
import json
import zlib

from nnnotes import cli
from nnnotes.source import addressables
from tests.helpers import synth

PAYLOADS = [b"file-table", b"data" * 400]
CONTENT = b"".join(PAYLOADS)


def run(argv, capsys):
    """(exit code, stdout, stderr) of the command line."""
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


def catalog(tmp_path):
    path = tmp_path / "catalog_main.bin"
    path.write_bytes(synth.CatalogWriter().build([("x_01.bundle", synth.remote("x_01.bundle"), [])]))
    return path


def entries(path):
    return {
        loc["primary_key"]: (loc.get("extra_data") or {}) for loc in addressables.parse_locations(path.read_bytes())
    }


def test_rebuild_writes_the_checksum_the_client_validates(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "x_01.bundle"
    bundle.write_bytes(synth.unityfs(PAYLOADS, compression="lz4"))
    out = tmp_path / "out.bin"
    code, stdout, err = run(["catalog", "rebuild", str(cat), "-o", str(out), "--bundle", str(bundle), "--json"], capsys)
    assert code == 0, err
    fields = entries(out)["x_01.bundle"]
    assert fields["crc"] == zlib.crc32(CONTENT)
    assert fields["bundleSize"] == len(synth.unityfs(PAYLOADS, compression="lz4"))
    assert json.loads(stdout)["updated"]["x_01.bundle"]["crc"] == zlib.crc32(CONTENT)


def test_rebuild_keeps_everything_else(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "x_01.bundle"
    bundle.write_bytes(synth.unityfs(PAYLOADS))
    out = tmp_path / "out.bin"
    before = entries(cat)["x_01.bundle"]
    code, _, err = run(["catalog", "rebuild", str(cat), "-o", str(out), "--bundle", str(bundle)], capsys)
    assert code == 0, err
    after = entries(out)["x_01.bundle"]
    assert after["hash"] == hashlib.md5(bundle.read_bytes()).hexdigest()
    assert after["bundleName"] == before["bundleName"]
    assert len(addressables.parse_locations(out.read_bytes())) == len(addressables.parse_locations(cat.read_bytes()))
    assert len(addressables.parse_keys(out.read_bytes())) == len(addressables.parse_keys(cat.read_bytes()))


def test_rebuild_can_give_the_bundle_a_new_cache_hash(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "x_01.bundle"
    bundle.write_bytes(synth.unityfs(PAYLOADS))
    out = tmp_path / "out.bin"
    code, _, err = run(
        ["catalog", "rebuild", str(cat), "-o", str(out), "--bundle", str(bundle), "--hash", "ab" * 16], capsys
    )
    assert code == 0, err
    assert entries(out)["x_01.bundle"]["hash"] == "ab" * 16


def test_a_bundle_the_catalog_does_not_name_is_an_error(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "z_99.bundle"
    bundle.write_bytes(synth.unityfs(PAYLOADS))
    code, _, err = run(
        ["catalog", "rebuild", str(cat), "-o", str(tmp_path / "out.bin"), "--bundle", str(bundle)], capsys
    )
    assert code != 0 and "z_99.bundle" in err
    assert not (tmp_path / "out.bin").exists()


def test_a_file_that_is_not_a_bundle_is_an_error(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "x_01.bundle"
    bundle.write_bytes(b"not a bundle")
    code, _, err = run(
        ["catalog", "rebuild", str(cat), "-o", str(tmp_path / "out.bin"), "--bundle", str(bundle)], capsys
    )
    assert code != 0 and "UnityFS" in err


def test_a_cache_hash_needs_exactly_one_bundle(tmp_path, capsys):
    cat = catalog(tmp_path)
    code, _, err = run(["catalog", "rebuild", str(cat), "-o", str(tmp_path / "out.bin"), "--hash", "ab" * 16], capsys)
    assert code != 0 and "--bundle" in err


def test_add_bundle_description_generates_metadata_and_asset_dependency(tmp_path, capsys):
    cat = catalog(tmp_path)
    package = tmp_path / "package"
    package.mkdir()
    packed = synth.unityfs(PAYLOADS, compression="lz4")
    (package / "custom.bundle").write_bytes(packed)
    manifest = package / "catalog.json"
    manifest.write_text(
        json.dumps(
            {
                "file": "custom.bundle",
                "internal_id": "https://example.invalid/custom.bundle",
                "bundle_name": "custom_bundle",
                "assets": [
                    {
                        "primary_key": "Chart/Custom",
                        "internal_id": "Assets/Custom.bytes",
                        "resource_type": "UnityEngine.TextAsset",
                        "aliases": ["chart"],
                    },
                ],
            }
        )
    )
    out = tmp_path / "out.bin"
    code, stdout, err = run(["catalog", "rebuild", str(cat), "--add", str(manifest), "-o", str(out), "--json"], capsys)
    assert code == 0, err
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out.read_bytes())}
    bundle = locations["custom.bundle"]
    assert bundle["extra_data"]["crc"] == zlib.crc32(CONTENT)
    assert bundle["extra_data"]["bundleSize"] == len(packed)
    assert bundle["extra_data"]["hash"] == hashlib.md5(packed).hexdigest()
    assert bundle["extra_data"]["bundleName"] == "custom_bundle"
    assert locations["Chart/Custom"]["dependencies"] == [bundle["offset"]]
    assert json.loads(stdout)["addedBundles"]["https://example.invalid/custom.bundle"]["crc"] == zlib.crc32(CONTENT)


def test_add_entry_list_and_bundle_updates_apply_together(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "custom.bundle"
    packed = synth.unityfs(PAYLOADS)
    bundle.write_bytes(packed)
    manifest = tmp_path / "entries.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "primary_key": "custom.bundle",
                    "internal_id": "custom.bundle",
                    "extra_data": {"bundleName": "custom", "hash": "11" * 16, "crc": 0, "bundleSize": 0},
                },
                {
                    "primary_key": "Chart/Custom",
                    "internal_id": "Assets/Custom.bytes",
                    "resource_type": "UnityEngine.TextAsset",
                    "dependency_keys": ["custom.bundle"],
                },
            ]
        )
    )
    out = tmp_path / "out.bin"
    code, _, err = run(
        [
            "catalog",
            "rebuild",
            str(cat),
            "--add",
            str(manifest),
            "--bundle",
            str(bundle),
            "--hash",
            "ab" * 16,
            "-o",
            str(out),
        ],
        capsys,
    )
    assert code == 0, err
    fields = entries(out)["custom.bundle"]
    assert (fields["crc"], fields["bundleSize"], fields["hash"]) == (zlib.crc32(CONTENT), len(packed), "ab" * 16)


def test_bundle_description_selects_a_catalog_template_and_overrides_provider(tmp_path, capsys):
    cat = tmp_path / "catalog.bin"
    cat.write_bytes(
        synth.CatalogWriter().build(
            [
                (
                    "source.bundle",
                    synth.remote("source.bundle"),
                    [],
                    {
                        "provider": "ExampleEncryptedBundleProvider",
                        "extra": {"flags": 8, "timeout": 60},
                    },
                ),
                ("other.bundle", synth.remote("other.bundle"), [], {"provider": synth.BUNDLE_PROVIDER}),
            ]
        )
    )
    packed = synth.unityfs(PAYLOADS)
    (tmp_path / "custom.bundle").write_bytes(packed)
    manifest, out = tmp_path / "add.json", tmp_path / "out.bin"
    description = {
        "file": "custom.bundle",
        "internal_id": synth.remote("custom.bundle"),
        "bundle_name": "custom",
        "catalog_template": "source.bundle",
        "request_options": {"timeout": 120},
    }
    manifest.write_text(json.dumps(description))
    code, _, err = run(["catalog", "rebuild", str(cat), "--add", str(manifest), "-o", str(out)], capsys)
    assert code == 0, err
    bundle = next(
        loc for loc in addressables.parse_locations(out.read_bytes()) if loc["primary_key"] == "custom.bundle"
    )
    assert bundle["provider"] == "ExampleEncryptedBundleProvider"
    assert (bundle["extra_data"]["flags"], bundle["extra_data"]["timeout"]) == (8, 120)
    assert bundle["extra_data"]["crc"] == zlib.crc32(CONTENT)
    description["bundle_provider"] = "ExplicitBundleProvider"
    manifest.write_text(json.dumps(description))
    code, _, err = run(["catalog", "rebuild", str(cat), "--add", str(manifest), "-o", str(out)], capsys)
    assert code == 0, err
    bundle = next(
        loc for loc in addressables.parse_locations(out.read_bytes()) if loc["primary_key"] == "custom.bundle"
    )
    assert bundle["provider"] == "ExplicitBundleProvider"


def test_multiple_bundle_descriptions_resolve_dependencies_by_key(tmp_path, capsys):
    cat = catalog(tmp_path)
    packed = synth.unityfs(PAYLOADS)
    for name in ("a.bundle", "b.bundle"):
        (tmp_path / name).write_bytes(packed)
    manifest = tmp_path / "bundles.json"
    manifest.write_text(
        json.dumps(
            {
                "bundles": [
                    {
                        "file": "a.bundle",
                        "internal_id": "https://example.invalid/a.bundle",
                        "bundle_name": "a",
                        "dependency_keys": ["b.bundle"],
                        "hash": "12" * 16,
                        "assets": [
                            {
                                "primary_key": "Texture/A",
                                "internal_id": "Assets/A.png",
                                "resource_type": "UnityEngine.Texture2D",
                            }
                        ],
                    },
                    {"file": "b.bundle", "internal_id": "https://example.invalid/b.bundle", "bundle_name": "b"},
                ]
            }
        )
    )
    out = tmp_path / "out.bin"
    code, _, err = run(["catalog", "rebuild", str(cat), "--add", str(manifest), "-o", str(out)], capsys)
    assert code == 0, err
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(out.read_bytes())}
    assert locations["a.bundle"]["dependencies"] == [locations["b.bundle"]["offset"]]
    assert locations["Texture/A"]["dependencies"] == [locations["a.bundle"]["offset"]]
    assert locations["a.bundle"]["extra_data"]["hash"] == "12" * 16


def test_invalid_addition_is_reported_before_writing(tmp_path, capsys):
    cat = catalog(tmp_path)
    manifest = tmp_path / "invalid.json"
    manifest.write_text(
        json.dumps(
            {"primary_key": "custom", "internal_id": "Assets/Custom.bytes", "dependency_keys": ["missing.bundle"]}
        )
    )
    out = tmp_path / "out.bin"
    code, _, err = run(["catalog", "rebuild", str(cat), "--add", str(manifest), "-o", str(out)], capsys)
    assert code != 0 and "missing.bundle" in err
    assert not out.exists()


def test_invalid_cache_hash_is_a_usage_error(tmp_path, capsys):
    cat = catalog(tmp_path)
    bundle = tmp_path / "x_01.bundle"
    bundle.write_bytes(synth.unityfs(PAYLOADS))
    out = tmp_path / "out.bin"
    code, _, err = run(
        ["catalog", "rebuild", str(cat), "--bundle", str(bundle), "--hash", "bad", "-o", str(out)],
        capsys,
    )
    assert code != 0 and "32 hexadecimal digits" in err
    assert not out.exists()
