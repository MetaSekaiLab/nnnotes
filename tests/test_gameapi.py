import json
import re
import socket
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import grpc
import pytest

import synth
from nnnotes import cli, gameapi
from nnnotes.config import Config, ConfigError

API = "https://api.example.test"
CDN_A, CDN_B = "https://cdn-a.example.test/prod/a1", "https://cdn-b.example.test/prod/a1"


# ---------------------------------------------------------------- protobuf encoding for the tests
def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def ld(num: int, data: bytes | str) -> bytes:
    data = data.encode("utf-8") if isinstance(data, str) else data
    return varint(num << 3 | 2) + varint(len(data)) + data


def version_response(version: str, resource: str = "") -> bytes:
    return ld(1, version) + (ld(2, resource) if resource else b"")


def server_info(name: str, cdn: str, api: str, area: str = "", display: str = "") -> bytes:
    return (ld(1, name) + ld(2, cdn) + ld(3, api) + ld(4, "chat.example.test") + ld(6, "live.example.test")
            + ld(7, display or name) + ld(8, area) + ld(9, "icon"))


def run(argv, capsys):
    code = 0
    try:
        cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


# ---------------------------------------------------------------- wire format
def test_fields_all_wire_types():
    buf = (varint(1 << 3 | 0) + varint(300) + varint(2 << 3 | 1) + bytes(range(8)) + ld(3, "hé")
           + varint(4 << 3 | 5) + b"\x01\x02\x03\x04" + varint(1000 << 3 | 0) + varint(2 ** 63))
    assert gameapi.fields(buf) == [(1, 300), (2, bytes(range(8))), (3, "hé".encode()), (4, b"\x01\x02\x03\x04"),
                                   (1000, 2 ** 63)]
    assert gameapi.fields(b"") == []


@pytest.mark.parametrize("buf", [
    b"\x08",                          # varint value missing
    b"\x08\x80",                      # truncated varint
    b"\x08" + b"\xff" * 10 + b"\x01",  # varint longer than 10 bytes
    b"\x0a\x05abc",                   # length past the end
    b"\x09\x00\x00",                  # fixed64 past the end
    b"\x0b",                          # wire type 3 (group)
    b"\x0e\x00",                      # wire type 6
    b"\x02\x00",                      # field number 0
])
def test_fields_malformed(buf):
    with pytest.raises(ValueError):
        gameapi.fields(buf)


def test_strings():
    buf = ld(1, "old") + varint(5 << 3) + varint(7) + ld(1, "new") + ld(9, "other")
    assert gameapi.strings(buf, {1: "a", 2: "b"}) == {"a": "new", "b": ""}
    with pytest.raises(ValueError):
        gameapi.strings(varint(1 << 3) + varint(1), {1: "a"})          # a varint where a string belongs
    with pytest.raises(ValueError):
        gameapi.strings(ld(1, b"\xff\xfe"), {1: "a"})                  # not UTF-8


def test_parse_version():
    v = gameapi.parse_version(version_response("0123abcd", "1.0.0.7") + varint(3 << 3) + varint(1))
    assert v == gameapi.MasterVersion("0123abcd", "1.0.0.7")
    assert gameapi.parse_version(b"") == gameapi.MasterVersion("", "")


def test_parse_server_list():
    buf = (ld(1, server_info("Region A", f"{CDN_A}|{CDN_B}/", f"{API}| {API}-2 |", "2", "Display A"))
           + ld(1, server_info("Region B", "", API, "3")) + ld(2, "ignored"))
    a, b = gameapi.parse_server_list(buf)
    assert (a.name, a.display_name, a.area_id, a.cdn, a.api) == ("Region A", "Display A", "2", (CDN_A, CDN_B),
                                                                 (API, API + "-2"))
    assert (b.name, b.cdn, b.api) == ("Region B", (), (API,))
    assert "example.test" not in repr(a)
    with pytest.raises(ValueError):
        gameapi.parse_server_list(varint(1 << 3) + varint(1))


def test_split_roots():
    assert gameapi.split_roots(" a/ | |b|") == ("a", "b")
    assert gameapi.split_roots("") == ()


# ---------------------------------------------------------------- metadata and targets
def test_metadata():
    md = gameapi.metadata("1.2.3")
    assert [k for k, _ in md] == ["x-request-id", "x-platform", "x-client-version"]
    assert dict(md)["x-platform"] == "android" and dict(md)["x-client-version"] == "1.2.3"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", dict(md)["x-request-id"])
    assert dict(gameapi.metadata("1.2.3"))["x-request-id"] != dict(md)["x-request-id"]
    assert dict(gameapi.metadata("1", "rid"))["x-request-id"] == "rid"


@pytest.mark.parametrize("root, target", [
    (API, ("api.example.test:443", True)),
    (API + "/", ("api.example.test:443", True)),
    ("api.example.test", ("api.example.test:443", True)),
    ("api.example.test:8443", ("api.example.test:8443", True)),
    (" https://API.example.test:9000 ", ("api.example.test:9000", True)),
    ("http://127.0.0.1:50051", ("127.0.0.1:50051", False)),
    ("http://localhost", ("localhost:80", False)),
    ("https://[::1]:9", ("[::1]:9", True)),
])
def test_channel_target(root, target):
    assert gameapi.channel_target(root) == target


@pytest.mark.parametrize("root", ["ftp://secret.example.test", "https://secret.example.test/prod/x",
                                  "https://secret.example.test/?q=1", "https://:443", "https://secret.example.test:x",
                                  "https://secret.example.test:99999"])
def test_channel_target_errors_hide_the_value(root):
    with pytest.raises(ValueError) as e:
        gameapi.channel_target(root)
    assert "secret" not in str(e.value)


# ---------------------------------------------------------------- calls (plain-text gRPC server on the loopback)
@pytest.fixture
def server():
    """A gRPC server on 127.0.0.1: `handlers` {method path: fn(request, context)}; `seen` records every call."""
    handlers, seen = {}, []

    class Handler(grpc.GenericRpcHandler):
        def service(self, details):
            fn = handlers.get(details.method)
            if fn is None:
                return None

            def unary(request, context):
                seen.append(SimpleNamespace(method=details.method, request=request,
                                            metadata=dict(context.invocation_metadata())))
                return fn(request, context)
            return grpc.unary_unary_rpc_method_handler(unary)

    srv = grpc.server(ThreadPoolExecutor(2))
    srv.add_generic_rpc_handlers((Handler(),))
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    yield SimpleNamespace(root=f"http://127.0.0.1:{port}", port=port, handlers=handlers, seen=seen)
    srv.stop(None)


def test_fetch_master_version(server):
    server.handlers[gameapi.VERSION_METHOD] = lambda req, ctx: version_response("0123abcd", "1.0.0.7")
    v = gameapi.fetch_master_version(server.root, "1.2.3")
    assert v == gameapi.MasterVersion("0123abcd", "1.0.0.7")
    (c,) = server.seen
    assert c.method == "/app.masterdata.MasterdataService/Version" and c.request == b""
    assert c.metadata["x-platform"] == "android" and c.metadata["x-client-version"] == "1.2.3"
    assert len(c.metadata["x-request-id"]) == 36


def test_empty_response_is_asked_again(server):
    answers = [b"", version_response("v2")]
    server.handlers[gameapi.VERSION_METHOD] = lambda req, ctx: answers.pop(0)
    assert gameapi.fetch_master_version(server.root, "1").version == "v2"
    assert len(server.seen) == 2 and server.seen[0].metadata["x-request-id"] != server.seen[1].metadata["x-request-id"]


def test_empty_response_every_time(server):
    server.handlers[gameapi.VERSION_METHOD] = lambda req, ctx: b""
    with pytest.raises(gameapi.GameApiError, match="no master data version"):
        gameapi.fetch_master_version(server.root, "1")
    assert len(server.seen) == gameapi.ATTEMPTS


def test_game_error_code(server):
    def fail(req, ctx):
        ctx.set_trailing_metadata(((gameapi.ERROR_CODE_TRAILER, "CLIENT_UPDATE_REQUIRED"),))
        ctx.abort(grpc.StatusCode.UNKNOWN, "details that are not shown")
    server.handlers[gameapi.VERSION_METHOD] = fail
    with pytest.raises(gameapi.GameApiError) as e:
        gameapi.fetch_master_version(server.root, "0.0.1", setting="[servers.zz] api")
    msg = str(e.value)
    assert msg == ("game API call Version to [servers.zz] api failed: UNKNOWN (game error code "
                   "CLIENT_UPDATE_REQUIRED, the client version is older than the server accepts)")
    assert len(server.seen) == 1                                        # not retried


def test_unknown_method(server):
    with pytest.raises(gameapi.GameApiError, match="UNIMPLEMENTED .*API root"):
        gameapi.fetch_master_version(server.root, "1")


def test_deadline(server):
    server.handlers[gameapi.VERSION_METHOD] = lambda req, ctx: time.sleep(0.5) or version_response("late")
    with pytest.raises(gameapi.GameApiError, match="DEADLINE_EXCEEDED .no answer before the deadline"):
        gameapi.call(server.root, gameapi.VERSION_METHOD, b"", "1", timeout=0.1, attempts=1)


def test_unreachable_is_retried_and_hides_the_address():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(gameapi.GameApiError) as e:
        gameapi.call(f"http://127.0.0.1:{port}", gameapi.VERSION_METHOD, b"", "1", timeout=2, attempts=2)
    assert "UNAVAILABLE (server unreachable)" in str(e.value)
    assert "127.0.0.1" not in str(e.value) and str(port) not in str(e.value)


def test_fetch_server_list(server):
    server.handlers[gameapi.SERVER_LIST_METHOD] = lambda req, ctx: ld(1, server_info("Region A", CDN_A, API, "2"))
    (s,) = gameapi.fetch_server_list(server.root, "1")
    assert (s.name, s.area_id, s.cdn, s.api) == ("Region A", "2", (CDN_A,), (API,))
    server.handlers[gameapi.SERVER_LIST_METHOD] = lambda req, ctx: b""
    with pytest.raises(gameapi.GameApiError, match="empty server list"):
        gameapi.fetch_server_list(server.root, "1")


# ---------------------------------------------------------------- settings
def cfg(data=None, **env):
    return Config(data, environ={f"NNNOTES_{k}": v for k, v in env.items()})


def apk(tmp_path, version: str | None = "1.2.3", manifest: bool = True):
    p = tmp_path / "base.apk"
    with zipfile.ZipFile(p, "w") as z:
        if manifest:
            strings = ["versionName", "manifest"] + ([version] if version is not None else [])
            z.writestr("AndroidManifest.xml", synth.axml(strings, 2 if version is not None else None))
        z.writestr("classes.dex", b"")
    return p


def test_api_root_setting():
    assert gameapi.api_root(cfg(SERVERS_ZZ_API=API), "servers.zz") == API
    assert gameapi.api_root(cfg({"bootstrap": {"api": API}}), "bootstrap") == API
    with pytest.raises(ConfigError, match=r"servers\.zz\.api is not set.*NNNOTES_SERVERS_ZZ_API"):
        gameapi.api_root(cfg(), "servers.zz")
    with pytest.raises(ConfigError, match=r"bootstrap\.api is not set.*\[bootstrap\].*NNNOTES_BOOTSTRAP_API"):
        gameapi.api_root(cfg(), "bootstrap")
    with pytest.raises(ConfigError) as e:
        gameapi.api_root(cfg(SERVERS_ZZ_API=CDN_A), "servers.zz")
    assert str(e.value).startswith("setting servers.zz.api: must be the API root") and "example" not in str(e.value)


def test_client_version_setting(tmp_path):
    assert gameapi.client_version(cfg(CLIENT_VERSION=" 1.0.9 ")) == "1.0.9"
    assert gameapi.client_version(cfg(PATHS_APK=str(apk(tmp_path)))) == "1.2.3"
    assert gameapi.client_version(cfg(PATHS_APK=str(apk(tmp_path)), CLIENT_VERSION="2.0.0")) == "2.0.0"
    with pytest.raises(ConfigError) as e:
        gameapi.client_version(cfg())
    for s in ("`version` in the [client] table", "NNNOTES_CLIENT_VERSION", "NNNOTES_PATHS_APK", "--apk"):
        assert s in str(e.value)
    with pytest.raises(ConfigError, match="setting client.version: must be printable ASCII"):
        gameapi.client_version(cfg(CLIENT_VERSION="1.0é"))


def test_client_version_from_a_bad_apk(tmp_path):
    with pytest.raises(ConfigError, match="has no versionName.*NNNOTES_CLIENT_VERSION"):
        gameapi.client_version(cfg(PATHS_APK=str(apk(tmp_path, version=None))))
    with pytest.raises(ConfigError, match="has no versionName"):
        gameapi.client_version(cfg(PATHS_APK=str(apk(tmp_path, manifest=False))))
    with pytest.raises(ConfigError, match="the APK's versionName: must be printable ASCII"):
        gameapi.client_version(cfg(PATHS_APK=str(apk(tmp_path, version="1\n2"))))
    (tmp_path / "junk.apk").write_bytes(b"not a zip")
    with pytest.raises(ConfigError, match="setting paths.apk: .* is not a readable APK"):
        gameapi.client_version(cfg(PATHS_APK=str(tmp_path / "junk.apk")))
    with pytest.raises(ConfigError, match="setting paths.apk: file .* not found"):
        gameapi.client_version(cfg(PATHS_APK=str(tmp_path / "absent.apk")))


def test_master_version_reads_the_region_settings(monkeypatch):
    calls = []

    def fake_call(root, method, request, client_version, **kw):
        calls.append((root, method, request, client_version, kw["setting"]))
        return version_response("0123abcd", "1.0.0.7")
    monkeypatch.setattr(gameapi, "call", fake_call)
    v = gameapi.master_version(cfg(SERVERS_ZZ_API=API, CLIENT_VERSION="1.2.3"), "zz")
    assert v.version == "0123abcd"
    assert calls == [(API, gameapi.VERSION_METHOD, b"", "1.2.3", "[servers.zz] api")]


def test_server_summary():
    s = gameapi.Server("Region A", "A", "2", (CDN_A, CDN_B), (API,))
    c = cfg({"servers": {"tw": {"cdn": CDN_B + "/"}, "xx": {"cdn": "https://other.example.test"}}})
    assert gameapi.configured_roots(c) == {"tw": {CDN_B}, "xx": {"https://other.example.test"}}
    d = gameapi.server_summary(s, gameapi.configured_roots(c))
    assert d == {"name": "Region A", "displayName": "A", "areaId": "2", "region": "tw", "cdnRoots": 2, "apiRoots": 1}
    assert gameapi.server_summary(s, {}, hosts=True)["cdn"] == [CDN_A, CDN_B]
    assert gameapi.server_summary(s, {"yy": {API}})["region"] == "yy"


# ---------------------------------------------------------------- command line
@pytest.fixture
def fake_api(monkeypatch):
    """gameapi.call answered from `answers` {method path: response bytes}; `calls` records (root, method)."""
    ns = SimpleNamespace(answers={}, calls=[])

    def fake_call(root, method, request, client_version, **kw):
        ns.calls.append((root, method))
        if method not in ns.answers:
            raise gameapi.GameApiError(gameapi.failure(method, kw["setting"], grpc.StatusCode.UNAVAILABLE, ()))
        return ns.answers[method]
    monkeypatch.setattr(gameapi, "call", fake_call)
    return ns


def test_master_version_command(fake_api, capsys, monkeypatch):
    fake_api.answers[gameapi.VERSION_METHOD] = version_response("0123abcd", "1.0.0.7")
    code, _, err = run(["--region", "zz", "master", "version"], capsys)
    assert code == 2 and "servers.zz.api" in err and "NNNOTES_SERVERS_ZZ_API" in err and not fake_api.calls
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_API", API)
    code, _, err = run(["--region", "zz", "master", "version"], capsys)
    assert code == 2 and "NNNOTES_CLIENT_VERSION" in err and not fake_api.calls
    monkeypatch.setenv("NNNOTES_CLIENT_VERSION", "1.2.3")
    code, out, _ = run(["--region", "zz", "master", "version"], capsys)
    assert code == 0 and json.loads(out) == {"region": "zz", "masterVersion": "0123abcd", "resourceVersion": "1.0.0.7"}
    assert fake_api.calls == [(API, gameapi.VERSION_METHOD)]


def test_master_version_command_failure(fake_api, capsys, monkeypatch):
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_API", API)
    monkeypatch.setenv("NNNOTES_CLIENT_VERSION", "1.2.3")
    with pytest.raises(SystemExit) as e:
        cli.main(["--region", "zz", "master", "version"])
    msg = e.value.code                                   # printed to standard error, exit status 1
    assert isinstance(msg, str) and not capsys.readouterr().out
    assert msg.startswith("nnnotes: game API call") and "UNAVAILABLE" in msg and "example" not in msg


@pytest.mark.parametrize("argv, message", [
    (["master", "download", "-o", "m"], "one of the arguments --version --latest is required"),
    (["master", "download", "--latest", "--version", "1", "-o", "m"], "not allowed with"),
])
def test_master_download_version_or_latest(argv, message, capsys):
    code, _, err = run(argv, capsys)
    assert code == 2 and message in err


def test_master_download_latest(fake_api, tmp_path, capsys, monkeypatch):
    cdn = synth.serve_master_version(tmp_path / "cdn", "0123abcd",
                                     {"MasterA.bin": synth.master_file(synth.MASTER_TABLE)})
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_CDN", cdn)
    monkeypatch.setenv("NNNOTES_CLIENT_VERSION", "1.2.3")
    argv = ["--region", "zz", "master", "download", "--latest", "-o", str(tmp_path / "m")]
    code, _, err = run(argv, capsys)
    assert code == 2 and "servers.zz.api" in err and not fake_api.calls        # checked before any call
    monkeypatch.setenv("NNNOTES_SERVERS_ZZ_API", API)
    fake_api.answers[gameapi.VERSION_METHOD] = version_response("0123abcd", "1.0.0.7")
    code, out, _ = run(argv, capsys)
    r = json.loads(out)
    assert code == 0 and (r["version"], r["files"], r["downloaded"]) == ("0123abcd", 1, 1)
    assert (tmp_path / "m" / "MasterA.bin").is_file()
    code, _, _ = run(["--region", "zz", "master", "download", "--version", "0123abcd", "-o", str(tmp_path / "m")],
                     capsys)
    assert code == 0 and len(fake_api.calls) == 1                               # --version: no API call


def test_servers_command(fake_api, tmp_path, capsys):
    fake_api.answers[gameapi.SERVER_LIST_METHOD] = (ld(1, server_info("Region A", f"{CDN_A}|{CDN_B}", API, "2"))
                                                    + ld(1, server_info("Region B", "", "", "3")))
    conf = tmp_path / "nnnotes.toml"
    conf.write_text(f'[client]\nversion = "1.2.3"\n[servers.tw]\ncdn = "{CDN_A}"\n', encoding="utf-8")
    code, _, err = run(["--config", str(conf), "servers"], capsys)
    assert code == 2 and "bootstrap.api" in err and "NNNOTES_BOOTSTRAP_API" in err
    conf.write_text(conf.read_text(encoding="utf-8") + f'[bootstrap]\napi = "{API}"\n', encoding="utf-8")
    code, out, _ = run(["--config", str(conf), "servers"], capsys)
    assert code == 0 and "example.test" not in out
    a, b = json.loads(out)["servers"]
    assert (a["name"], a["region"], a["cdnRoots"], a["apiRoots"]) == ("Region A", "tw", 2, 1)
    assert (b["name"], b["region"], b["cdnRoots"], b["apiRoots"]) == ("Region B", None, 0, 0)
    code, out, _ = run(["--config", str(conf), "servers", "--show-hosts"], capsys)
    a, _ = json.loads(out)["servers"]
    assert code == 0 and a["cdn"] == [CDN_A, CDN_B] and a["api"] == [API]
    assert fake_api.calls[0] == (API, gameapi.SERVER_LIST_METHOD)
