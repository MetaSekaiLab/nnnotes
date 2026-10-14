"""International resource versions select the catalog bytes, not just an import label."""

import hashlib
import os
import subprocess
import sys
import urllib.error
from types import SimpleNamespace

import pytest

from nnnotes import cli
from nnnotes.cli import browse, common
from nnnotes.musicdata import deckdata
from nnnotes.pipeline import catalogdb
from nnnotes.source import catalog, configfile, gameapi
from nnnotes.source.config import Config, ConfigError
from nnnotes.source.session import Session
from tests.helpers import synth

MODEL = "Character/Live2D/test/new/model/new"
EPISODE = "Adv/Episode/event_new/event_new"


def binary(*keys):
    return synth.CatalogWriter().build([(key, "Assets/" + key, []) for key in keys])


def config(tmp_path, *, api=True, version=None, region="tw"):
    return Config(
        {
            "catalog": {"region": region, "language": "en", "version": version},
            "servers": {
                region: {"cdn": "https://cdn.example/prod/" + region, **({"api": "https://api.example"} if api else {})}
            },
            "paths": {"depot": str(tmp_path / "depot")},
        },
        environ={},
    )


def test_auto_discovery_ignores_stale_main_and_advances_resource_version(tmp_path, monkeypatch):
    from nnnotes.site.webmodel import catalog_models

    cfg = config(tmp_path)
    downloads = Session(cfg).downloads()
    downloads.mkdir(parents=True)
    (downloads / catalog.Catalog.file_name("en")).write_bytes(binary("Old"))
    publication = gameapi.MasterVersion("master-one", "1.0.0.201")
    seen = []

    def version(*args):
        seen.append("version")
        return publication

    def download(url):
        seen.append(url)
        assert url.endswith(f"catalog_{publication.resource_version}_en.bin")
        return binary(MODEL, EPISODE, publication.version)

    monkeypatch.setattr(Session, "master_version", version)
    monkeypatch.setattr(catalog, "download", download)
    first = Session(cfg).catalog(bundles=False)
    assert first.has(EPISODE) and catalog_models(first) == {"new": MODEL}
    assert first.resource_version == "1.0.0.201"
    assert deckdata.catalog_info(first)["resourceVersion"] == "1.0.0.201"
    assert seen[0] == "version" and len(seen) == 2
    # An unchanged version reuses its catalog; a new version gets new bytes.
    assert Session(cfg).catalog(bundles=False).has("master-one")
    assert len(seen) == 3
    publication = gameapi.MasterVersion("master-two", "1.0.0.202")
    second = Session(cfg).catalog(bundles=False)
    assert second.has("master-two") and not second.has("master-one")
    assert first.download_dir != second.download_dir
    assert all("catalog_main" not in request for request in seen)


def test_explicit_version_download_is_offline_and_cdn_scoped(tmp_path, monkeypatch):
    cfg = config(tmp_path, version="1.0.0.201")
    monkeypatch.setattr(Session, "master_version", lambda *args: pytest.fail("pin called Version"))
    monkeypatch.setattr(catalog, "download", lambda url: binary("First"))
    first = Session(cfg).catalog(bundles=False)
    monkeypatch.setattr(catalog, "download", lambda url: pytest.fail("downloaded pin downloaded"))
    assert Session(cfg).catalog(bundles=False).has("First")
    monkeypatch.setattr(catalog, "download", lambda url: binary("Other region"))
    other = Session(config(tmp_path, version="1.0.0.201", region="en")).catalog(bundles=False)
    assert other.has("Other region") and first.download_dir != other.download_dir


def test_explicit_catalog_file_stays_offline(tmp_path, monkeypatch):
    path = tmp_path / "saved.bin"
    path.write_bytes(binary("Offline"))
    cfg = Config(
        {
            "catalog": {"region": "tw", "version": "1.0.0.201"},
            "servers": {"tw": {"api": "https://api.example", "catalog": str(path)}},
            "paths": {"depot": str(tmp_path / "depot")},
        },
        environ={},
    )
    monkeypatch.setattr(Session, "master_version", lambda *args: pytest.fail("explicit file called Version"))
    assert Session(cfg).catalog(bundles=False).has("Offline")


def test_missing_versioned_catalog_does_not_fall_back_to_main(tmp_path, monkeypatch):
    cfg = config(tmp_path, version="1.0.0.201")
    downloads = Session(cfg).downloads()
    downloads.mkdir(parents=True)
    (downloads / catalog.Catalog.file_name("en")).write_bytes(binary("Old"))
    urls = []

    def missing(url):
        urls.append(url)
        raise urllib.error.HTTPError(url, 404, "not found", None, None)

    monkeypatch.setattr(catalog, "download", missing)
    with pytest.raises(urllib.error.HTTPError):
        Session(cfg).catalog(bundles=False)
    assert len(urls) == 1 and "catalog_1.0.0.201_en.bin" in urls[0]
    assert not list((downloads / "international").rglob("*.bin"))


def test_failed_discovery_does_not_claim_main_is_current(tmp_path, monkeypatch):
    def fail(*args):
        raise gameapi.GameApiError("unavailable")

    monkeypatch.setattr(Session, "master_version", fail)
    monkeypatch.setattr(catalog, "download", lambda url: pytest.fail("fallback download"))
    with pytest.raises(gameapi.GameApiError, match="unavailable"):
        Session(config(tmp_path)).catalog(bundles=False)


def test_configured_main_never_queries_api_or_invents_resource_version(tmp_path, monkeypatch):
    cfg = config(tmp_path, version="main")
    monkeypatch.setattr(Session, "master_version", lambda *args: pytest.fail("explicit main queried API"))
    urls = []
    monkeypatch.setattr(catalog, "download", lambda url: urls.append(url) or binary("Old"))
    cat = Session(cfg).catalog(bundles=False)
    assert cat.has("Old") and cat.resource_version is None
    assert urls[0].endswith("catalog_main_en.bin")


def test_version_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(Session, "master_version", lambda *args: pytest.fail("pin queried API"))
    data = {"catalog": {"region": "tw", "version": "1.0.0.1"}, "servers": {"tw": {"catalog_version": "1.0.0.2"}}}
    assert Session(Config(data, environ={})).catalog_version() == "1.0.0.2"
    cfg = Config(data, environ={}, overrides={("catalog", "version"): "1.0.0.3"})
    assert Session(cfg).catalog_version() == "1.0.0.3"
    assert Session(config(tmp_path, api=False)).catalog_version() == "main"


def test_build_workers_inherit_the_version_pin(tmp_path, monkeypatch):
    monkeypatch.setenv("NNNOTES_CATALOG_REGION", "tw")
    monkeypatch.setenv("NNNOTES_CATALOG_LANGUAGE", "en")
    monkeypatch.setenv("NNNOTES_CATALOG_VERSION", "1.0.0.100")
    monkeypatch.setenv("NNNOTES_SERVERS_TW_CATALOG_VERSION", "1.0.0.201")
    monkeypatch.setenv("NNNOTES_SERVERS_TW_CDN", "https://cdn.example/prod/tw")
    monkeypatch.setenv("NNNOTES_SERVERS_TW_API", "https://unavailable.example")
    monkeypatch.setenv("NNNOTES_PATHS_DEPOT", str(tmp_path / "depot"))
    monkeypatch.setattr(catalog, "download", lambda url: binary(MODEL, EPISODE))
    assert Session(Config.load()).catalog(bundles=False).has(MODEL)
    command = [
        sys.executable,
        "-c",
        "from nnnotes.source.config import Config; from nnnotes.source.session import Session; "
        "from nnnotes.site.webmodel import catalog_models; c=Session(Config.load()).catalog(bundles=False); "
        "assert 'new' in catalog_models(c); print(c.resource_version)",
    ]
    child = subprocess.run(command, env=os.environ.copy(), text=True, capture_output=True, check=True)
    assert child.stdout.strip() == "1.0.0.201"


@pytest.mark.parametrize("version", ["../bad", "/absolute", "1/2", "1\\2", "1%2f2", "1?x", "1#x", "v\n"])
def test_invalid_version_cannot_be_used_as_a_path(tmp_path, version):
    with pytest.raises(ConfigError):
        Session(config(tmp_path, version=version)).catalog_version()
    with pytest.raises(ValueError):
        catalog.Catalog.file_name("en", version=version)
    with pytest.raises(ValueError):
        configfile.validate(configfile.resolve("catalog.version")[0], version)


def test_empty_api_version_fails_before_download(tmp_path, monkeypatch):
    monkeypatch.setattr(Session, "master_version", lambda *args: gameapi.MasterVersion("master", ""))
    monkeypatch.setattr(catalog, "download", lambda url: pytest.fail("missing version downloaded"))
    with pytest.raises(ConfigError, match="servers.tw.api"):
        Session(config(tmp_path)).catalog(bundles=False)


def test_bad_download_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "download", lambda url: b"not a catalog")
    with pytest.raises(ValueError):
        Session(config(tmp_path, version="1.0.0.201")).catalog(bundles=False)
    assert not list((tmp_path / "depot").rglob("*.bin"))


def test_fetch_queries_version_before_downloading_and_labels_matching_bytes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NNNOTES_SERVERS_TW_CDN", "https://cdn.example/prod/tw")
    monkeypatch.setenv("NNNOTES_SERVERS_TW_API", "https://api.example")
    events = []
    data = binary(MODEL, EPISODE)

    def observe(*args):
        events.append("version")
        return gameapi.MasterVersion("master", "1.0.0.201")

    def fetch(cdn, language, *, version):
        events.append(version)
        assert language == "en" and version == "1.0.0.201"
        return data, "catalog-hash"

    monkeypatch.setattr(Session, "master_version", observe)
    monkeypatch.setattr(catalogdb, "fetch", fetch)
    cli.main(["--region", "tw", "--language", "en", "--depot", str(tmp_path / "depot"), "catalog", "fetch"])
    assert events == ["version", "1.0.0.201"]
    version = catalogdb.CatalogDB(tmp_path / "depot").versions()[0]
    assert version["resourceVersion"] == "1.0.0.201"
    assert version["remote"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert "1.0.0.201" in capsys.readouterr().out


def test_fetch_downloads_matching_bin_and_hash(tmp_path):
    cdn = tmp_path / "cdn" / "asset" / "Android"
    cdn.mkdir(parents=True)
    data = binary("Versioned")
    (cdn / "catalog_1.0.0.201_en.bin").write_bytes(data)
    (cdn / "catalog_1.0.0.201_en.hash").write_text("new-hash\n")
    (cdn / "catalog_main_en.bin").write_bytes(binary("Old"))
    assert catalogdb.fetch((tmp_path / "cdn").as_uri(), "en", version="1.0.0.201") == (data, "new-hash")


def test_browser_uses_the_same_selector(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(Session, "master_version", lambda *args: gameapi.MasterVersion("master", "1.0.0.201"))
    calls = []
    monkeypatch.setattr(catalog, "download", lambda url: calls.append(url) or binary(MODEL))
    handler = browse.Handler.__new__(browse.Handler)
    handler.server = SimpleNamespace(catalogs={}, downloads=tmp_path / "depot" / "downloads", bundle_key=None)
    handler.catalog(browse.Region("tw", "TW", cfg.cdn("tw"), ["en"], cfg), "en")
    assert len(calls) == 1 and calls[0].endswith("catalog_1.0.0.201_en.bin")


def test_catalog_version_flag_is_distinct_from_the_stored_catalog(tmp_path):
    args = cli.parse_args(
        [
            "--catalog-version",
            "1.0.0.201",
            "export",
            "assets",
            "--stored-catalog",
            "stored-label",
            "--select",
            "key:Example",
            "-o",
            str(tmp_path / "out"),
        ]
    )
    assert args.catalog_version == "1.0.0.201" and args.stored_catalog == "stored-label"
    assert Session(common.load_config(args)).catalog_version() == "1.0.0.201"
