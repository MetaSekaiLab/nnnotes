"""JP transport, snapshot identity and split APK regressions; all servers and data are synthetic."""
import base64
import gzip
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import synth
from test_gameapi import server as grpc_server, version_response
from nnnotes import addressables, catalogdb, cli, configfile, deckdata, gameapi, jp
from nnnotes.apkset import ApkSet
from nnnotes.catalog import Catalog, file_name, location_kind
from nnnotes.cli_assets import CatalogFetcher
from nnnotes.config import Config, ConfigError

HASH = "a" * 32
MASTER = "1.0.0.100/" + "b" * 32
server = grpc_server


def cat_bytes():
    return synth.CatalogWriter().build([
        ("Live/MusicScore/0001/0001_00", "Assets/chart.asset", [1]),
        ("chart.bundle", jp.PLACEHOLDER + "chart.bundle", []),
        ("Cri/Sound/test", jp.PLACEHOLDER + "audio/test.acb", []),
    ])


@pytest.fixture
def upstream(server):
    state = SimpleNamespace(hash=HASH, credential="synthetic-secret", asset=True, seen=[], status=200,
                            body=gzip.compress(cat_bytes()), cdn_override=None)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.seen.append((self.path, dict(self.headers)))
            expected = "Basic " + base64.b64encode(("sirius:" + state.credential).encode()).decode()
            status = state.status if self.headers.get("Authorization") == expected else 401
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "http://127.0.0.1:1/leak")
            self.end_headers()
            if status == 200:
                self.wfile.write(state.body)

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    state.cdn = f"http://127.0.0.1:{http.server_port}"

    def version(req, ctx):
        headers = [("x-sirius-env", state.cdn_override or state.cdn), ("x-sirius-cred", state.credential)]
        if state.asset:
            headers.append(("x-asset-version", json.dumps({"live": [
                {"minClientVersion": "1.0.4", "version": "1.0.0.300", "Android": state.hash}]})))
        ctx.send_initial_metadata(headers)
        return version_response(MASTER)

    server.handlers[gameapi.VERSION_METHOD] = version
    state.grpc = server
    state.cfg = Config({"catalog": {"region": "jp", "language": "ja"},
                        "client": {"version": "1.0.1"},
                        "servers": {"jp": {"cdn": state.cdn, "api": server.root, "client_version": "1.0.4"}}},
                       environ={})
    try:
        yield state
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_version_headers_and_master_without_asset(upstream):
    result = gameapi.master_version(upstream.cfg, "jp")
    assert (result.version, result.resource_version, result.resource_hash) == (MASTER, "1.0.0.300", HASH)
    assert upstream.grpc.seen[0].metadata["x-client-version"] == "1.0.4"
    assert upstream.grpc.seen[0].method == gameapi.VERSION_METHOD
    upstream.asset = False
    result = gameapi.master_version(upstream.cfg, "jp")
    assert result.version == MASTER and result.resource_version == "" and result.resource_hash is None


def test_live_selection_numeric_and_no_fallback():
    def entry(client, version):
        return {"minClientVersion": client, "version": version, "Android": HASH}
    raw = {"version": "9.9", "Android": HASH,
           "live": [entry("1.0.10", "1.0.10"), entry("1.0.2", "1.0.2"), entry("8.0", "8.0")]}
    assert jp.select_asset(json.dumps(raw), "1.0.12") == ("1.0.10", HASH)
    assert jp.select_asset(json.dumps(raw), "1.0.1") is None
    assert jp.select_asset(json.dumps({"version": "1.0", "Android": HASH, "live": []}), "1.0") == ("1.0", HASH)
    with pytest.raises(gameapi.GameApiError, match="invalid asset"):
        jp.select_asset('{"version":"../secret"}', "1.0")


def test_authenticated_download_rotation_and_redaction(upstream):
    session = jp.Session(upstream.cfg, "jp")
    obs = session.observe()
    assert upstream.credential not in repr(obs)
    upstream.credential = "synthetic-rotated"
    assert session.get(obs.source.catalog_url, source=obs.source) == upstream.body
    assert len(upstream.seen) == 2 and len(upstream.grpc.seen) == 2
    assert upstream.seen[-1][1]["User-Agent"] == "OurNotes/1.0.4"
    assert "Authorization" not in json.dumps(obs.source.to_dict())


@pytest.mark.parametrize("status", [302, 429, 500, 403])
def test_download_does_not_follow_redirects_or_loop(upstream, status):
    session = jp.Session(upstream.cfg, "jp")
    obs = session.observe()
    upstream.status = status
    with pytest.raises(gameapi.GameApiError, match=f"HTTP {status}") as error:
        session.get(obs.source.catalog_url, source=obs.source)
    expected = 2 if status == 403 else 1
    assert len(upstream.seen) == expected and len(upstream.grpc.seen) == expected
    assert upstream.credential not in str(error.value) and upstream.cdn not in str(error.value)


def test_unapproved_origin_fails_before_download(upstream):
    upstream.cdn_override = "https://elsewhere.invalid"
    with pytest.raises(gameapi.GameApiError, match="CDN differs"):
        jp.Session(upstream.cfg, "jp").observe()
    assert not upstream.seen


def test_hash_only_change_does_not_rebind_old_catalog(upstream):
    session = jp.Session(upstream.cfg, "jp")
    old = session.observe().source
    upstream.hash = "c" * 32
    session.observe()
    with pytest.raises(gameapi.GameApiError, match="assets changed"):
        session.get(old.catalog_url, source=old)
    assert not upstream.seen


def test_gzip_catalog_classification_and_dependency_closure(tmp_path):
    raw = cat_bytes()
    compressed = gzip.compress(raw)
    assert addressables.parse(raw) == addressables.parse(compressed)
    assert addressables.parse_keys(raw) == addressables.parse_keys(compressed)
    cat = Catalog(compressed, tmp_path)
    assert len(cat.resolve("Live/MusicScore/0001/0001_00")) == 1
    assert cat.resolve("Live/MusicScore/0001/0001_00")[0].remote
    assert len(cat.raw_files()) == 1
    assert catalogdb.summary(catalogdb.index(compressed))["remoteBundles"] == 1
    assert file_name(jp.PLACEHOLDER + "nested/chart.bundle") == "chart.bundle"
    assert file_name(jp.PLACEHOLDER + "audio/test.acb") == "audio/test.acb"


def test_gzip_limit(monkeypatch):
    monkeypatch.setattr(addressables, "MAX_CATALOG", 256)
    with pytest.raises(ValueError, match="expanded catalog"):
        addressables.parse(gzip.compress(b"x" * 257))


@pytest.mark.parametrize("path", ["../secret", "%2e%2e/secret", "%252e%252e/secret", "/secret", "x\\y", "a?b", "a#b"])
def test_placeholder_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        location_kind(jp.PLACEHOLDER + path)


def test_catalog_cache_source_and_offline_replay(upstream, tmp_path, monkeypatch):
    cfg = upstream.cfg
    cfg._data["paths"] = {"cache": str(tmp_path / "cache")}
    cat = cli.open_catalog(cfg, bundles=False)
    path = cat.cache_dir / "catalog_main.bin"
    assert path.read_bytes() == upstream.body
    assert jp.read_source(path, upstream.body) == cat.source
    assert cat.cache_dir != tmp_path / "cache"
    assert cat.keys("Live/") == ["Live/MusicScore/0001/0001_00"]
    record = catalogdb.CatalogDB(tmp_path / "store").add(upstream.body, source=cat.source.to_dict(), region="jp")
    assert upstream.credential not in json.dumps(record)
    cfg._data["paths"]["catalog"] = str(path)
    monkeypatch.setattr(jp.Session, "observe", lambda *a, **k: pytest.fail("offline read made an API call"))
    assert cli.open_catalog(cfg, bundles=False).keys() == cat.keys()
    path.write_bytes(b"changed")
    with pytest.raises(ConfigError, match="matching"):
        cli.open_catalog(cfg, bundles=False)


def test_catalogdb_source_identity_and_fetcher(upstream, tmp_path):
    cfg = upstream.cfg
    cfg._data["paths"] = {"cache": str(tmp_path / "cache")}
    session = jp.Session(cfg, "jp")
    source = session.observe().source
    db = catalogdb.CatalogDB(tmp_path / "store")
    old = db.add(cat_bytes(), source=source.to_dict(), region="jp")
    new_source = {**source.to_dict(), "hash": "c" * 32}
    new = db.add(cat_bytes(), source=new_source, region="jp")
    assert old["id"] != new["id"] and old["labels"] != new["labels"]
    assert old["remote"] == new["remote"]
    fetcher = CatalogFetcher(tmp_path / "store", tmp_path / "cache", cfg)
    cat, _ = fetcher.catalog(old["id"])
    assert cat.source == source and cat.cache_dir == source.cache_dir(tmp_path / "cache")


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["base", "directory", "apks"])
def test_split_apk_member_routing_and_manifest_precedence(tmp_path, kind):
    base = zip_bytes({"AndroidManifest.xml": synth.axml(["versionName", "manifest", "1.0.4"], 2),
                      "assets/bin/Data/data.unity3d": b"boot"})
    assets = zip_bytes({"AndroidManifest.xml": b"split manifest", "assets/aa/catalog.bin": cat_bytes(),
                        "assets/Master/MasterManifest.json": b'{"version":"embedded", "files":[]}'})
    (tmp_path / "base.apk").write_bytes(base)
    (tmp_path / "split_UnityDataAssetPack.apk").write_bytes(assets)
    source = tmp_path / "base.apk" if kind == "base" else tmp_path
    if kind == "apks":
        source = tmp_path / "game.apks"
        source.write_bytes(zip_bytes({"base.apk": base, "split_UnityDataAssetPack.apk": assets}))
    with ApkSet(source) as archive:
        assert archive.read("assets/bin/Data/data.unity3d") == b"boot"
        assert archive.read("assets/aa/catalog.bin") == cat_bytes()
    assert gameapi.apk_version_name(source) == "1.0.4"
    assert deckdata.apk_master(source).version == "embedded"
    assert catalogdb.apk_catalog(source) == cat_bytes()


def test_per_region_apk_and_client_settings(tmp_path):
    cfg = Config({"catalog": {"region": "tw"}, "paths": {"apk": "global.apk"},
                  "servers": {"jp": {"apk": "jp.apks", "client_version": "1.0.4"}},
                  "client": {"version": "1.0.1"}}, base=tmp_path, environ={})
    assert cfg.path("paths", "apk") == tmp_path / "global.apk"
    assert cfg.for_region("jp").path("paths", "apk") == tmp_path / "jp.apks"
    assert gameapi.client_version(cfg, "jp") == "1.0.4" and gameapi.client_version(cfg, "tw") == "1.0.1"
    assert configfile.resolve("servers.jp.apk")[0].kind == "path"
    assert configfile.resolve("servers.jp.client_version")[0].kind == "string"


def test_datapack_is_loaded_in_the_boot_environment(tmp_path, monkeypatch):
    from nnnotes import player
    boot = SimpleNamespace(objects=[SimpleNamespace(type=SimpleNamespace(name="GraphicsSettings"),
                                                    assets_file=SimpleNamespace(unity_version="6000.3.12f1"))])
    loaded = []
    boot.load_file = lambda stream, **kw: loaded.append((stream.read(), kw["name"]))
    monkeypatch.setattr(player.UnityPy, "load", lambda stream: boot if stream.read() == b"boot" else object())
    path = tmp_path / "base.apk"
    path.write_bytes(zip_bytes({player.DATA_IN_APK: b"boot", player.DEFAULT_RESOURCES_IN_APK: b"defaults",
                               "assets/bin/Data/datapack.unity3d": b"resources"}))
    player.PlayerData(path)
    assert loaded == [(b"resources", "datapack.unity3d")]


def test_jp_master_manifest_checks_before_writing(tmp_path):
    from nnnotes import master
    manifest = {"version": MASTER, "files": [{"name": "../secret.bin", "hash": "a" * 64, "size": 128}]}
    seen = []
    def get(url):
        seen.append(url)
        return json.dumps(manifest).encode()
    with pytest.raises(master.DownloadError, match="manifest entry"):
        master.download("https://cdn.invalid", MASTER, tmp_path, get=get, strict=True)
    assert len(seen) == 1 and not (tmp_path / "MasterManifest.json").exists()


def test_export_cache_locator_uses_source_namespace(tmp_path):
    from nnnotes.cli_assets import Workspace
    source = jp.Source("jp", "1.0.0.300", HASH, "https://cdn.invalid")
    workspace = Workspace.__new__(Workspace)
    workspace.version = {"source": source.to_dict()}
    rel = workspace._cache_rel("bundle", {"name": "same.bundle"}, {})
    assert tmp_path / rel == source.cache_dir(tmp_path) / "bundles/same.bundle"
    rel = workspace._cache_rel("raw", {}, {"internalId": jp.PLACEHOLDER + "audio/sample.acb"})
    assert tmp_path / rel == source.cache_dir(tmp_path) / "raw/audio/sample.acb"


def test_music_data_preserves_asset_hash(tmp_path):
    from test_musicdata import CHARTS, bgm, master_dir, KEY, PROV
    from nnnotes import musicdata
    output = tmp_path / "music.json"
    musicdata.export(output, deckdata.master_files(master_dir(tmp_path)), KEY, CHARTS.__getitem__, bgm,
                     **{**PROV, "catalog": {**PROV["catalog"], "resourceHash": HASH}})
    doc = json.loads(output.read_bytes())
    assert doc["provenance"]["catalog"]["resourceHash"] == HASH
