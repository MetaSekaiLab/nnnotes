import hashlib
import json

import pytest

from nnnotes import cli
from nnnotes.source import addressables, catalogbuild, master
from nnnotes.source.addressables import BundleKey
from nnnotes.source.catalog import Catalog
from nnnotes.source.master import MasterKey
from tests.helpers import synth
from tests.helpers.packassets import template

MASTER_KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
BUNDLE_KEY = BundleKey(synth.BUNDLE_KEY, synth.BUNDLE_SEED)


def run(argv, capsys):
    """(exit code, stdout, stderr) of the command line."""
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    monkeypatch.setenv("NNNOTES_BUNDLE_KEY", synth.BUNDLE_KEY.hex())
    monkeypatch.setenv("NNNOTES_BUNDLE_NONCE_SEED", synth.BUNDLE_SEED.hex())


def test_pack_master_decodes_back_to_the_same_json(tmp_path, capsys, keys):
    src = tmp_path / "json" / "MasterX.json"
    src.parent.mkdir()
    src.write_text(json.dumps(synth.MASTER_TABLE, ensure_ascii=False), encoding="utf-8")
    code, out, err = run(["pack", "master", str(src.parent), "-o", str(tmp_path / "bin"), "--json"], capsys)
    assert code == 0, err
    assert json.loads(out)["packed"] == 1
    raw = (tmp_path / "bin" / "MasterX.bin").read_bytes()
    assert raw[: master.PREFIX] == bytes(master.PREFIX)
    assert master.decode(raw, MASTER_KEY) == src.read_bytes()


def test_pack_master_keeps_the_prefix_of_another_file(tmp_path, capsys, keys):
    src = tmp_path / "MasterX.json"
    src.write_text("{}", encoding="utf-8")
    original = tmp_path / "original.bin"
    original.write_bytes(synth.master_file(synth.MASTER_TABLE, prefix=bytes(range(64))))
    code, _, err = run(
        ["pack", "master", str(src), "-o", str(tmp_path / "bin"), "--prefix-from", str(original)], capsys
    )
    assert code == 0, err
    assert (tmp_path / "bin" / "MasterX.bin").read_bytes()[:64] == bytes(range(64))


def test_pack_master_rejects_a_short_prefix(tmp_path, capsys, keys):
    src = tmp_path / "MasterX.json"
    src.write_text("{}", encoding="utf-8")
    short = tmp_path / "short.bin"
    short.write_bytes(bytes(10))
    code, _, err = run(["pack", "master", str(src), "-o", str(tmp_path / "bin"), "--prefix-from", str(short)], capsys)
    assert code != 0 and "prefix" in err


def test_pack_master_writes_a_manifest(tmp_path, capsys, keys):
    src = tmp_path / "MasterX.json"
    src.write_text("{}", encoding="utf-8")
    code, out, err = run(
        ["pack", "master", str(src), "-o", str(tmp_path / "bin"), "--manifest", "1.2.3", "--json"], capsys
    )
    assert code == 0, err
    data = (tmp_path / "bin" / "MasterX.bin").read_bytes()
    manifest = json.loads((tmp_path / "bin" / "MasterManifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.2.3"
    assert manifest["files"] == [{"name": "MasterX.bin", "hash": hashlib.sha256(data).hexdigest(), "size": len(data)}]
    assert json.loads(out)["manifest"].endswith("MasterManifest.json")


def test_pack_master_needs_no_manifest_by_default(tmp_path, capsys, keys):
    src = tmp_path / "MasterX.json"
    src.write_text("{}", encoding="utf-8")
    code, out, err = run(["pack", "master", str(src), "-o", str(tmp_path / "bin"), "--json"], capsys)
    assert code == 0, err
    assert not (tmp_path / "bin" / "MasterManifest.json").exists()
    assert json.loads(out)["manifest"] is None


def test_pack_bundle_is_what_the_client_decrypts(tmp_path, capsys, keys):
    src = tmp_path / "b_02.bundle"
    src.write_bytes(synth.fake_bundle("b_02.bundle"))
    code, out, err = run(["pack", "bundle", str(src), "-o", str(tmp_path / "cdn"), "--json"], capsys)
    assert code == 0, err
    assert json.loads(out)["packed"] == 1
    stored = (tmp_path / "cdn" / "b_02.bundle").read_bytes()
    assert not stored.startswith(b"UnityFS")
    assert addressables.decrypt(stored, "b_02.bundle", BUNDLE_KEY) == src.read_bytes()


def test_pack_bundle_names_the_file_it_is_served_as(tmp_path, capsys, keys):
    src = tmp_path / "draft.bundle"
    src.write_bytes(synth.fake_bundle("served.bundle"))
    code, _, err = run(["pack", "bundle", str(src), "-o", str(tmp_path / "cdn"), "--name", "served.bundle"], capsys)
    assert code == 0, err
    stored = (tmp_path / "cdn" / "served.bundle").read_bytes()
    assert addressables.decrypt(stored, "served.bundle", BUNDLE_KEY) == src.read_bytes()


def test_pack_bundle_rejects_a_file_that_is_not_a_bundle(tmp_path, capsys, keys):
    src = tmp_path / "x.bundle"
    src.write_bytes(b"not a bundle")
    code, _, err = run(["pack", "bundle", str(src), "-o", str(tmp_path / "cdn")], capsys)
    assert code != 0 and "UnityFS" in err


def test_pack_needs_the_keys(tmp_path, capsys, monkeypatch):
    for name in ("NNNOTES_MASTER_KEY", "NNNOTES_MASTER_IV", "NNNOTES_BUNDLE_KEY", "NNNOTES_BUNDLE_NONCE_SEED"):
        monkeypatch.delenv(name, raising=False)
    src = tmp_path / "MasterX.json"
    src.write_text("{}", encoding="utf-8")
    code, _, err = run(["pack", "master", str(src), "-o", str(tmp_path / "bin")], capsys)
    assert code == 2 and "master.key" in err


def build_input(tmp_path):
    (tmp_path / "template.bundle").write_bytes(template())
    (tmp_path / "data.bin").write_bytes(bytes(range(256)))
    doc = {
        "template": "template.bundle",
        "bundle_name": "example",
        "internal_id": synth.remote("example.bundle"),
        "assets": [
            {
                "type": "TextAsset",
                "name": "binary",
                "file": "data.bin",
                "container": "Assets/Example/binary.bytes",
                "primary_key": "Example/Binary",
            }
        ],
    }
    path = tmp_path / "build.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_pack_build_emits_plain_encrypted_and_resolvable_catalog(tmp_path, capsys, keys):
    source = build_input(tmp_path)
    existing = tmp_path / "catalog.bin"
    existing.write_bytes(synth.CatalogWriter().build([]))
    plain, encrypted = tmp_path / "plain" / "example.bundle", tmp_path / "cdn" / "example.bundle"
    catalog, additions = tmp_path / "catalog-built.bin", tmp_path / "add.json"
    code, out, err = run(
        [
            "pack",
            "build",
            str(source),
            "-o",
            str(plain),
            "--encrypt",
            str(encrypted),
            "--catalog",
            str(existing),
            "--catalog-out",
            str(catalog),
            "--catalog-add",
            str(additions),
            "--json",
        ],
        capsys,
    )
    assert code == 0, err
    assert json.loads(out)["bundle_name"] == "example"
    assert addressables.decrypt(encrypted.read_bytes(), encrypted.name, BUNDLE_KEY) == plain.read_bytes()
    resolved = Catalog(catalog.read_bytes(), tmp_path / "cache").resolve("Example/Binary")
    assert [bundle.name for bundle in resolved] == ["example.bundle"]
    from nnnotes.unity.bundlefs import content_crc

    locations = addressables.parse_locations(catalog.read_bytes())
    assert locations[0]["extra_data"]["crc"] == content_crc(plain.read_bytes())
    assert locations[0]["extra_data"]["bundleSize"] == plain.stat().st_size
    second = tmp_path / "catalog-sidecar.bin"
    code, _, err = run(["catalog", "rebuild", str(existing), "-o", str(second), "--add", str(additions)], capsys)
    assert code == 0, err
    assert Catalog(second.read_bytes(), tmp_path / "cache-two").resolve("Example/Binary")[0].name == "example.bundle"


def test_pack_build_failure_leaves_all_existing_outputs_unchanged(tmp_path, capsys):
    source = build_input(tmp_path)
    doc = json.loads(source.read_text())
    doc["assets"].append({"type": "TextAsset", "name": "missing", "file": "absent"})
    source.write_text(json.dumps(doc))
    plain, additions = tmp_path / "example.bundle", tmp_path / "add.json"
    plain.write_bytes(b"original")
    additions.write_bytes(b"original additions")
    code, _, err = run(["pack", "build", str(source), "-o", str(plain), "--catalog-add", str(additions)], capsys)
    assert code != 0 and "absent" in err
    assert plain.read_bytes() == b"original" and additions.read_bytes() == b"original additions"


def test_pack_build_inherits_custom_provider_and_freezes_it_in_the_sidecar(tmp_path, capsys, keys):
    source = build_input(tmp_path)
    existing = tmp_path / "catalog.bin"
    existing.write_bytes(
        synth.CatalogWriter().build(
            [("source.bundle", synth.remote("source.bundle"), [], {"provider": "ExampleEncryptedBundleProvider"})]
        )
    )
    plain, encrypted = tmp_path / "example.bundle", tmp_path / "encrypted" / "example.bundle"
    catalog, additions = tmp_path / "built.bin", tmp_path / "add.json"
    code, stdout, err = run(
        [
            "pack",
            "build",
            str(source),
            "-o",
            str(plain),
            "--encrypt",
            str(encrypted),
            "--catalog",
            str(existing),
            "--catalog-out",
            str(catalog),
            "--catalog-add",
            str(additions),
            "--json",
        ],
        capsys,
    )
    assert code == 0, err
    assert json.loads(stdout)["bundle_provider"] == "ExampleEncryptedBundleProvider"
    description = json.loads(additions.read_text())["bundles"][0]
    assert description["bundle_provider"] == "ExampleEncryptedBundleProvider"
    locations = {loc["primary_key"]: loc for loc in addressables.parse_locations(catalog.read_bytes())}
    assert locations["example.bundle"]["provider"] == "ExampleEncryptedBundleProvider"
    assert locations["Example/Binary"]["provider"] == synth.ASSET_PROVIDER
    assert addressables.decrypt(encrypted.read_bytes(), encrypted.name, BUNDLE_KEY) == plain.read_bytes()
    empty = tmp_path / "empty.bin"
    empty.write_bytes(synth.CatalogWriter().build([]))
    second = tmp_path / "sidecar.bin"
    code, _, err = run(["catalog", "rebuild", str(empty), "--add", str(additions), "-o", str(second)], capsys)
    assert code == 0, err
    assert addressables.parse_locations(second.read_bytes())[0]["provider"] == "ExampleEncryptedBundleProvider"


def test_pack_build_ambiguous_catalog_providers_leave_outputs_unchanged(tmp_path, capsys):
    source = build_input(tmp_path)
    existing = tmp_path / "catalog.bin"
    existing.write_bytes(
        synth.CatalogWriter().build(
            [
                ("a.bundle", synth.remote("a.bundle"), [], {"provider": "ExampleEncryptedBundleProvider"}),
                ("b.bundle", synth.remote("b.bundle"), [], {"provider": synth.BUNDLE_PROVIDER}),
            ]
        )
    )
    plain, catalog = tmp_path / "example.bundle", tmp_path / "built.bin"
    plain.write_bytes(b"previous")
    code, _, err = run(
        ["pack", "build", str(source), "-o", str(plain), "--catalog", str(existing), "--catalog-out", str(catalog)],
        capsys,
    )
    assert code != 0 and "multiple bundle providers" in err
    assert plain.read_bytes() == b"previous" and not catalog.exists()


def test_pack_build_catalog_requires_source_before_writing(tmp_path, capsys):
    source = build_input(tmp_path)
    plain = tmp_path / "example.bundle"
    code, _, err = run(
        ["pack", "build", str(source), "-o", str(plain), "--catalog-out", str(tmp_path / "out.bin")], capsys
    )
    assert code != 0 and "--catalog SOURCE" in err
    assert not plain.exists()


def edit_catalog_input(tmp_path, operations):
    (tmp_path / "template.bundle").write_bytes(template())
    (tmp_path / "changed.bin").write_bytes(b"changed payload")
    manifest = tmp_path / "edit.json"
    manifest.write_text(
        json.dumps(
            {
                "mode": "edit",
                "template": "template.bundle",
                "internal_id": synth.remote("source.bundle"),
                "assets": operations,
            }
        )
    )
    catalog = tmp_path / "catalog.bin"
    catalog.write_bytes(
        catalogbuild.rebuild(
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
                    ("dep.bundle", synth.remote("dep.bundle"), []),
                ]
            ),
            add=[
                {
                    "primary_key": "Example/Text",
                    "internal_id": "assets/text.bytes",
                    "provider": "Example.TextProvider",
                    "resource_type": "UnityEngine.TextAsset",
                    "aliases": ["Example/Alias", 42],
                    "dependency_keys": ["source.bundle", "dep.bundle"],
                },
                {
                    "primary_key": "Example/Text",
                    "internal_id": "assets/other.bytes",
                    "provider": "Example.OtherProvider",
                    "resource_type": "UnityEngine.TextAsset",
                    "aliases": ["Example/Alias", 42],
                    "dependency_keys": ["dep.bundle"],
                },
            ],
        )
    )
    return manifest, catalog


def catalog_semantics(data):
    locations = addressables.parse_locations(data)
    by_offset = {loc["offset"]: loc for loc in locations}

    def identity(offset):
        loc = by_offset[offset]
        return loc["primary_key"], loc["internal_id"], loc["provider"]

    records = [
        {
            **{key: value for key, value in loc.items() if key not in ("offset", "dependencies")},
            "dependencies": [identity(offset) for offset in loc["dependencies"]],
        }
        for loc in locations
    ]
    keys = [
        (key["key"], key["type"], [identity(offset) for offset in key["locations"]])
        for key in addressables.parse_keys(data)
    ]
    return records, keys


@pytest.mark.parametrize("operations", [[], [{"op": "replace", "name": "text", "file": "changed.bin"}]])
def test_edit_bundle_catalog_preserves_types_providers_aliases_and_other_locations(tmp_path, capsys, operations):
    source, existing = edit_catalog_input(tmp_path, operations)
    plain, built, sidecar = tmp_path / "source.bundle", tmp_path / "built.bin", tmp_path / "add.json"
    before, before_keys = catalog_semantics(existing.read_bytes())
    code, _, err = run(
        [
            "pack",
            "build",
            str(source),
            "-o",
            str(plain),
            "--catalog",
            str(existing),
            "--catalog-out",
            str(built),
            "--catalog-add",
            str(sidecar),
        ],
        capsys,
    )
    assert code == 0, err
    after, after_keys = catalog_semantics(built.read_bytes())
    assert after[1:] == before[1:] and after_keys == before_keys
    assert after[0]["provider"] == before[0]["provider"] == "ExampleEncryptedBundleProvider"
    assert (after[0]["extra_data"]["flags"], after[0]["extra_data"]["timeout"]) == (8, 60)
    second = tmp_path / "sidecar.bin"
    code, _, err = run(["catalog", "rebuild", str(existing), "--add", str(sidecar), "-o", str(second)], capsys)
    assert code == 0, err
    assert catalog_semantics(second.read_bytes()) == catalog_semantics(built.read_bytes())


def test_edit_catalog_rename_and_overrides_change_only_matching_location(tmp_path, capsys):
    source, existing = edit_catalog_input(
        tmp_path,
        [
            {
                "op": "rename",
                "name": "text",
                "new_name": "renamed",
                "new_container": "assets/new.bytes",
                "primary_key": "Example/Updated",
                "provider": "Example.UpdatedProvider",
                "aliases": ["updated", 43],
                "dependency_keys": [],
            },
            {"type": "TextAsset", "name": "added", "file": "changed.bin", "primary_key": "Example/Added"},
        ],
    )
    plain, built = tmp_path / "source.bundle", tmp_path / "built.bin"
    code, _, err = run(
        [
            "pack",
            "build",
            str(source),
            "-o",
            str(plain),
            "--catalog",
            str(existing),
            "--catalog-out",
            str(built),
        ],
        capsys,
    )
    assert code == 0, err
    locations = {loc["internal_id"]: loc for loc in addressables.parse_locations(built.read_bytes())}
    changed, other, added = locations["assets/new.bytes"], locations["assets/other.bytes"], locations["added"]
    assert changed["primary_key"] == "Example/Updated" and changed["provider"] == "Example.UpdatedProvider"
    assert changed["resource_type"] == "UnityEngine.TextAsset"
    assert changed["dependencies"] == [locations[synth.remote("source.bundle")]["offset"]]
    assert other["primary_key"] == "Example/Text" and other["provider"] == "Example.OtherProvider"
    keys = {key["key"]: key["locations"] for key in addressables.parse_keys(built.read_bytes())}
    assert keys["Example/Text"] == keys["Example/Alias"] == keys[42] == [other["offset"]]
    assert keys["Example/Updated"] == keys["updated"] == keys[43] == [changed["offset"]]
    assert keys["Example/Added"] == [added["offset"]]


@pytest.mark.parametrize("name", ["../escape.bundle", "a/b.bundle", "..\\escape.bundle", "C:escape.bundle"])
def test_pack_bundle_rejects_served_paths(tmp_path, capsys, keys, name):
    src = tmp_path / "source.bundle"
    src.write_bytes(synth.fake_bundle("source.bundle"))
    target = tmp_path / "output"
    code, _, err = run(["pack", "bundle", str(src), "-o", str(target), "--name", name], capsys)
    assert code != 0 and "file names" in err
    assert not target.exists()


@pytest.mark.parametrize("command,suffix", [("master", ".json"), ("bundle", ".bundle")])
def test_pack_batch_duplicate_names_leave_output_absent(tmp_path, capsys, keys, command, suffix):
    sources = []
    for dirname in ("a", "b"):
        path = tmp_path / dirname / f"same{suffix}"
        path.parent.mkdir()
        path.write_bytes(b"{}" if command == "master" else synth.fake_bundle("same.bundle"))
        sources.append(str(path))
    target = tmp_path / "output"
    code, _, err = run(["pack", command, *sources, "-o", str(target)], capsys)
    assert code != 0 and "duplicate" in err
    assert not target.exists()


def test_pack_bundle_batch_invalid_input_does_not_publish_valid_input(tmp_path, capsys, keys):
    first, last = tmp_path / "a.bundle", tmp_path / "z.bundle"
    first.write_bytes(synth.fake_bundle("a.bundle"))
    last.write_bytes(b"invalid")
    target = tmp_path / "output"
    code, _, _ = run(["pack", "bundle", str(first), str(last), "-o", str(target)], capsys)
    assert code != 0 and not target.exists()
