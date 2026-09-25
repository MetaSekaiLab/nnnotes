"""Anonymous calls to the game's API: the master data version a region serves, and the server list.

The game's API is gRPC over HTTP/2 with TLS. Two unary methods can be called anonymously; both take an empty request
message:

`app.masterdata.MasterdataService/Version`, on a region's API root
    response `string version = 1` (the master data version: the `<version>` of `<cdn>/master/<version>/`) and
    `string resource_version = 2`.
`GetServerList`, on the bootstrap API root
    response `repeated ServerInfo servers = 1`; of ServerInfo only `string name = 1`, `string cdnRoot = 2`,
    `string apiServerRoot = 3`, `string displayName = 7` and `string areaID = 8` are read. A root field can hold
    several alternative roots separated by `|`.

Every call sends the metadata the game client sends with each call: `x-request-id` (a random UUID), `x-platform`
(`android`) and `x-client-version` (the client version). Requests and responses are raw bytes; the few fields above
are read with a small protobuf wire-format decoder, without generated code. A call has a deadline and is retried when
the server is unreachable, misses the deadline or answers with an empty message. Error messages name the method and
the setting, never the address.
"""
from __future__ import annotations

import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import grpc

from .config import Config, ConfigError, describe

VERSION_METHOD = "/app.masterdata.MasterdataService/Version"
SERVER_LIST_METHOD = "/app.playerlogin.PlayerLoginService/GetServerList"
PLATFORM = "android"
TIMEOUT = 10.0                  # seconds, per attempt
ATTEMPTS = 3
RETRY_STATUS = (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
ERROR_CODE_TRAILER = "x-sirius-error-code"     # the game's error code (with status UNKNOWN / PERMISSION_DENIED)
STATUS_HINT = {
    grpc.StatusCode.UNAVAILABLE: "server unreachable",
    grpc.StatusCode.DEADLINE_EXCEEDED: "no answer before the deadline",
    grpc.StatusCode.UNIMPLEMENTED: "no such method there: is the setting the API root?",
}
GAME_ERROR_HINT = {
    "CLIENT_UPDATE_REQUIRED": "the client version is older than the server accepts",
    "UNDER_MAINTENANCE": "the server is under maintenance",
}


class GameApiError(RuntimeError):
    """A game API call failed (the message names the method and the setting, never the address)."""


# ---------------------------------------------------------------- protobuf wire format
def _varint(buf: bytes, i: int) -> tuple[int, int]:
    val = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift >= 64:
            raise ValueError("varint longer than 10 bytes")


def fields(buf: bytes) -> list[tuple[int, int | bytes]]:
    """(field number, value) of every field of a protobuf message, in wire order. Varints are ints; 64-bit, 32-bit
    and length-delimited fields are their raw bytes. Raises ValueError on malformed data."""
    out: list[tuple[int, int | bytes]] = []
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if num == 0:
            raise ValueError("field number 0")
        if wire == 0:
            v, i = _varint(buf, i)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, i = _varint(buf, i)
            else:
                size = 8 if wire == 1 else 4
            if i + size > n:
                raise ValueError(f"field {num} runs past the end of the message")
            v, i = bytes(buf[i:i + size]), i + size
        else:
            raise ValueError(f"field {num}: unsupported wire type {wire}")
        out.append((num, v))
    return out


def strings(buf: bytes, names: dict[int, str]) -> dict[str, str]:
    """The string fields `names` ({field number: name}) of a message. An absent field is "" (the proto3 default);
    of repeated occurrences the last one counts."""
    out = dict.fromkeys(names.values(), "")
    for num, v in fields(buf):
        if num in names:
            if not isinstance(v, bytes):
                raise ValueError(f"field {num} is not a string")
            out[names[num]] = v.decode("utf-8")
    return out


# ---------------------------------------------------------------- messages
@dataclass(frozen=True)
class MasterVersion:
    version: str                # master data version
    resource_version: str


@dataclass(frozen=True)
class Server:
    """An entry of the server list. `cdn` / `api`: the alternative roots in the server's order (not in the repr)."""
    name: str
    display_name: str
    area_id: str
    cdn: tuple[str, ...] = field(repr=False)
    api: tuple[str, ...] = field(repr=False)


SERVER_INFO = {1: "name", 2: "cdn", 3: "api", 7: "display_name", 8: "area_id"}


def split_roots(value: str) -> tuple[str, ...]:
    """`a|b|c` -> ("a", "b", "c"): blanks dropped, trailing `/` removed."""
    return tuple(p.strip().rstrip("/") for p in value.split("|") if p.strip())


def parse_version(buf: bytes) -> MasterVersion:
    d = strings(buf, {1: "version", 2: "resource_version"})
    return MasterVersion(d["version"], d["resource_version"])


def parse_server_list(buf: bytes) -> list[Server]:
    out = []
    for num, v in fields(buf):
        if num != 1:
            continue
        if not isinstance(v, bytes):
            raise ValueError("field 1 is not a message")
        d = strings(v, SERVER_INFO)
        out.append(Server(d["name"], d["display_name"], d["area_id"], split_roots(d["cdn"]), split_roots(d["api"])))
    return out


# ---------------------------------------------------------------- calls
def metadata(client_version: str, request_id: str | None = None) -> list[tuple[str, str]]:
    """The metadata of a call: a fresh `x-request-id` unless one is given."""
    return [("x-request-id", request_id or str(uuid.uuid4())), ("x-platform", PLATFORM),
            ("x-client-version", client_version)]


def channel_target(root: str) -> tuple[str, bool]:
    """(gRPC target `host:port`, TLS) of an API root: `https://host[:port]` or `host[:port]` (TLS, port 443 by
    default), or `http://host[:port]` (plain text, port 80 by default; for local servers). A trailing `/` is
    allowed, any other path is not. Raises ValueError with a message that does not repeat the value."""
    s = root.strip()
    if "://" not in s:
        s = "https://" + s
    u = urlsplit(s)
    if u.scheme not in ("https", "http"):
        raise ValueError("must be https://host[:port], http://host[:port] or host[:port]")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("must be the API root (scheme, host and port only), not a URL with a path")
    try:
        host, port = u.hostname, u.port
    except ValueError:
        raise ValueError("has an invalid port") from None
    if not host:
        raise ValueError("has no host")
    if ":" in host:                                   # IPv6 literal
        host = f"[{host}]"
    tls = u.scheme == "https"
    return f"{host}:{port or (443 if tls else 80)}", tls


def call(root: str, method: str, request: bytes, client_version: str, *, timeout: float = TIMEOUT,
         attempts: int = ATTEMPTS, setting: str = "the API root") -> bytes:
    """One unary call with raw bytes; returns the response message's bytes. `setting` names the root in errors.
    Raises GameApiError."""
    target, tls = channel_target(root)
    name = method.rsplit("/", 1)[-1]                  # e.g. Version
    channel = grpc.secure_channel(target, grpc.ssl_channel_credentials()) if tls else grpc.insecure_channel(target)
    try:
        stub = channel.unary_unary(method)            # no (de)serializers: bytes in, bytes out
        attempts = max(1, attempts)
        for i in range(attempts):
            last = i + 1 == attempts
            try:
                body = stub(request, timeout=timeout, metadata=metadata(client_version))
            except grpc.RpcError as e:
                code = e.code()
                if code in RETRY_STATUS and not last:
                    time.sleep(0.5 * (i + 1))
                    continue
                raise GameApiError(failure(name, setting, code, e.trailing_metadata())) from None
            if body or last:
                return body
            time.sleep(0.3 * (i + 1))                 # OK with an empty message: transient, ask again
    finally:
        channel.close()
    raise AssertionError("unreachable")


def failure(name: str, setting: str, code, trailers) -> str:
    """The one-line error of a failed call: status, a hint, and the game's error code when the server sent one."""
    status = code.name if code is not None else "no status"
    hint = STATUS_HINT.get(code)
    game = next((v for k, v in (trailers or ()) if k == ERROR_CODE_TRAILER), None)
    extra = ", ".join(x for x in (hint, f"game error code {game}" if game else None, GAME_ERROR_HINT.get(game))
                      if x)
    return f"game API call {name} to {setting} failed: {status}" + (f" ({extra})" if extra else "")


def fetch_master_version(root: str, client_version: str, *, timeout: float = TIMEOUT,
                         setting: str = "the API root") -> MasterVersion:
    body = call(root, VERSION_METHOD, b"", client_version, timeout=timeout, setting=setting)
    try:
        v = parse_version(body)
    except ValueError as e:
        raise GameApiError(f"game API call Version to {setting}: malformed response ({e})") from None
    if not v.version:
        raise GameApiError(f"game API call Version to {setting}: no master data version in the response")
    return v


def fetch_server_list(root: str, client_version: str, *, timeout: float = TIMEOUT,
                      setting: str = "the bootstrap API root") -> list[Server]:
    body = call(root, SERVER_LIST_METHOD, b"", client_version, timeout=timeout, setting=setting)
    try:
        servers = parse_server_list(body)
    except ValueError as e:
        raise GameApiError(f"game API call GetServerList to {setting}: malformed response ({e})") from None
    if not servers:
        raise GameApiError(f"game API call GetServerList to {setting}: empty server list")
    return servers


# ---------------------------------------------------------------- settings
def api_root(cfg: Config, section: str) -> str:
    """`[<section>] api`, checked (a ConfigError names the setting, not the value)."""
    v = cfg.require(section, "api")
    try:
        channel_target(v)
    except ValueError as e:
        raise ConfigError(f"setting {section}.api: {e}") from None
    return v


def apk_version_name(apk: Path) -> str | None:
    """`versionName` of the APK's AndroidManifest.xml (None when the APK has none)."""
    from .player import MANIFEST_IN_APK, manifest_version_name
    try:
        with zipfile.ZipFile(apk) as z:
            data = z.read(MANIFEST_IN_APK) if MANIFEST_IN_APK in z.namelist() else None
    except (zipfile.BadZipFile, OSError):
        raise ConfigError(f"setting paths.apk: {apk} is not a readable APK") from None
    return manifest_version_name(data) if data is not None else None


def client_version(cfg: Config) -> str:
    """The `x-client-version` of calls: `[client] version`, else the versionName of `[paths] apk`."""
    v = cfg.get("client", "version")
    what = "setting client.version"
    if v is None:
        apk = cfg.path("paths", "apk")
        if apk is None:
            raise ConfigError(f"the game API needs the client version: give it as {describe('client', 'version')}, "
                              f"or give base.apk as {describe('paths', 'apk', '--apk')} to use its versionName")
        if not apk.is_file():
            raise ConfigError(f"setting paths.apk: file {apk} not found")
        v = apk_version_name(apk)
        if not v:
            raise ConfigError("the APK's AndroidManifest.xml has no versionName: give the client version as "
                              + describe("client", "version"))
        what = "the APK's versionName"
    v = v.strip()
    if not v or not v.isascii() or not v.isprintable():
        raise ConfigError(f"{what}: must be printable ASCII")
    return v


def master_version(cfg: Config, region: str, *, timeout: float = TIMEOUT) -> MasterVersion:
    """The master data version region `region` serves now (its `[servers.<region>] api`)."""
    section = f"servers.{region}"
    root = api_root(cfg, section)
    return fetch_master_version(root, client_version(cfg), timeout=timeout, setting=f"[{section}] api")


def server_list(cfg: Config, *, timeout: float = TIMEOUT) -> list[Server]:
    """The server list of `[bootstrap] api`."""
    root = api_root(cfg, "bootstrap")
    return fetch_server_list(root, client_version(cfg), timeout=timeout, setting="[bootstrap] api")


def configured_roots(cfg: Config) -> dict[str, set[str]]:
    """{region: its `cdn` and `api` settings, trailing `/` removed} of the configured regions."""
    out = {}
    for r in cfg.regions():
        roots = {cfg.get(f"servers.{r}", k) for k in ("cdn", "api")}
        out[r] = {s.strip().rstrip("/") for s in roots if s}
    return out


def server_summary(s: Server, configured: dict[str, set[str]], hosts: bool = False) -> dict:
    """A server list entry for printing: the configured region with one of its roots, root counts, and the roots
    themselves only when `hosts`."""
    region = next((r for r, roots in configured.items() if roots & {*s.cdn, *s.api}), None)
    d = {"name": s.name, "displayName": s.display_name, "areaId": s.area_id, "region": region,
         "cdnRoots": len(s.cdn), "apiRoots": len(s.api)}
    if hosts:
        d.update(cdn=list(s.cdn), api=list(s.api))
    return d
