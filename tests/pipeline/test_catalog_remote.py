"""Catalog metadata and content shared by independent depots over a directory remote."""

import pytest

from nnnotes.pipeline import contract
from nnnotes.pipeline.catalogdb import CatalogDB
from nnnotes.pipeline.remote import open_remote
from tests.pipeline.test_catalogdb import APK, H3, H4, build, remote_entries


def databases(tmp_path):
    remote = open_remote((tmp_path / "remote").as_uri())
    return remote, CatalogDB(tmp_path / "a", remote=remote), CatalogDB(tmp_path / "b", remote=remote)


def test_catalog_versions_are_shared_and_content_is_fetched_on_demand(tmp_path):
    remote, first, second = databases(tmp_path)
    body, apk = build(remote_entries()), build(APK)
    version = first.add(body, apk, label="stable", region="one", language="en")
    assert remote.get(f"catalogs/versions/{version['id']}.json") == contract.encode(version)
    assert remote.get("catalogs/index.json") is None
    assert second.get("stable") == version
    assert not second.path(version["remote"]["sha256"]).exists()
    assert second.catalog_bytes(version) == (body, apk)
    assert second.path(version["apk"]["sha256"]).read_bytes() == apk
    assert CatalogDB(tmp_path / "b").get("stable") == version
    assert CatalogDB(tmp_path / "b").catalog_bytes(version) == (body, apk)


def test_independent_imports_preserve_versions_and_refresh_known_metadata(tmp_path):
    _, first, second = databases(tmp_path)
    body = build(remote_entries())
    one = first.add(body, label="one", region="r", language="en")
    assert second.get("one")["id"] == one["id"]
    two = second.add(build(remote_entries(h_a=H3)), label="two", region="r", language="en")
    three = first.add(build(remote_entries(h_a=H4)), label="three", region="r", language="en")
    assert [v["id"] for v in second.versions()] == [one["id"], two["id"], three["id"]]
    first.add(body, label="extra", region="r", language="en", resource_version="1.2.3")
    assert second.get("extra")["resourceVersion"] == "1.2.3"
    assert second.get("latest")["id"] == three["id"]
    with pytest.raises(ValueError, match="already names"):
        second.add(build(remote_entries(h_a=H3)), label="one", region="r", language="en")


def test_remote_catalog_bytes_are_verified_before_local_storage(tmp_path):
    remote, first, second = databases(tmp_path)
    body = build(remote_entries())
    version = first.add(body)
    sha = version["remote"]["sha256"]
    remote.put(f"catalogs/{sha}.bin", b"x" * len(body))
    with pytest.raises(ValueError, match="differs from its identity"):
        second.catalog_bytes(version)
    assert not second.path(sha).exists()


def test_remote_catalog_record_must_match_its_identity(tmp_path):
    remote, first, second = databases(tmp_path)
    version = first.add(build(remote_entries()))
    version["id"] = "0" * 64
    remote.put(f"catalogs/versions/{version['id']}.json", contract.encode(version))
    with pytest.raises(ValueError, match="version identity differs"):
        second.versions()


def test_catalog_task_inputs_materialize_shared_catalogs(tmp_path):
    _, first, second = databases(tmp_path)
    body, apk = build(remote_entries()), build(APK)
    version = first.add(body, apk)
    inputs = second.inputs(second.get(version["id"]))
    assert [i.sha256 for i in inputs] == [contract.sha256(body), contract.sha256(apk)]
    assert second.catalog_bytes(version) == (body, apk)


def test_catalog_locator_recovery_reads_shared_version_metadata(tmp_path):
    from nnnotes.cli.assets import CatalogFetcher
    from nnnotes.source.config import Config

    remote, first, _ = databases(tmp_path)
    body = build(remote_entries())
    version = first.add(body)
    cfg = Config({"depot": {"remote": remote.url}}, environ={})
    fetcher = CatalogFetcher(tmp_path / "fresh", cfg)
    catalog, locations = fetcher.catalog(version["id"])
    assert catalog.sources()["remote"] == body
    assert locations
