"""Original Sprite geometry observations, without decoding or publishing textures.

The output is game data and belongs outside source repositories. It preserves
the logical Sprite rectangle independently of a published bitmap's trim.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .cache import write_atomic
from .export import Exporter
from .jsonio import dumps

SCHEMA = "nnnotes.observed-sprite-geometries/1"


def fingerprint(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def observe(cat, keys, *, region, client=None, native=None, out=None) -> dict:
    """Read every actual Sprite in the explicitly selected bundle closures.

    Catalog and decrypted bundle hashes identify the inputs actually read.
    `client`/`native` are observed APK identity, never inferred from a catalog.
    Public bitmap evidence is deliberately independent of these native records.
    """
    keys = list(keys)
    if not keys or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("at least one exact nonempty catalog key is required")
    keys = sorted(set(keys))
    unknown = [key for key in keys if not cat.has(key)]
    if unknown:
        raise ValueError("Sprite key not in catalog: " + ", ".join(unknown))
    sources = {name: fingerprint(data) for name, data in cat.sources().items()}
    document = {"schema": SCHEMA, "region": region, "client": client or {},
                "catalogSha256": sources["remote"]["sha256"],
                "source": {"kind": "observed-nnnotes-sprite-metadata", "catalogs": sources,
                           "native": native or {}, "runtimeVerified": False}, "sprites": {}}
    source = getattr(cat, "source", None)
    if source is not None:
        # Do not serialize endpoint/session settings or authentication state.
        document["source"]["resource"] = {"provider": source.provider, "version": source.version,
                                          "hash": source.hash, "platform": source.platform}
    for key in keys:
        exporter = Exporter(cat, Path(out or "."), textures="deferred")
        environment, _ = exporter.load(key)
        bundles = [{"file": bundle.name, **fingerprint(cat.fetch(bundle).read_bytes())}
                   for bundle in sorted(cat.resolve(key), key=lambda value: value.name)]
        found = False
        for obj in environment.objects:
            if obj.type.name != "Sprite":
                continue
            tree = obj.read_typetree()
            render, _, _ = exporter.sprite_render_data(obj, tree)
            name = tree["m_Name"]
            sprite_key = f"{key}[{name}]"
            if sprite_key in document["sprites"]:
                raise ValueError("Ambiguous Sprite name in one closure: " + sprite_key)
            geometry = {"rect": tree["m_Rect"], "pivot": tree["m_Pivot"], "border": tree["m_Border"],
                        "pixelsPerUnit": tree["m_PixelsToUnits"], "textureRect": render["textureRect"],
                        "textureRectOffset": render["textureRectOffset"], "settingsRaw": render["settingsRaw"],
                        "downscaleMultiplier": render.get("downscaleMultiplier", 1)}
            document["sprites"][sprite_key] = {"geometry": geometry, "source": {
                "key": key, "name": name, "assetFile": obj.assets_file.name, "pathId": str(obj.path_id),
                "catalogSha256": sources["remote"]["sha256"], "bundles": bundles}}
            found = True
        if not found:
            raise ValueError("No actual Sprite in requested key: " + key)
    return document


def apk_identity(apk, *, native_fingerprints=False):
    from .apkset import ApkSet
    from .deckdata import apk_client
    from .player import MANIFEST_IN_APK
    client = {key: value for key, value in apk_client(apk).items() if value is not None}
    native = {}
    with ApkSet(apk) as archive:
        if MANIFEST_IN_APK in archive.namelist():
            native["manifest"] = {"file": MANIFEST_IN_APK, **fingerprint(archive.read(MANIFEST_IN_APK))}
        if native_fingerprints:
            native["libraries"] = [{"file": name, **fingerprint(archive.read(name))}
                for name in sorted(archive.namelist())
                if name.startswith("lib/") and name.rsplit("/", 1)[-1] in ("libil2cpp.so", "libanort.so")]
    return client, native


def command(args, cfg):
    from .cli import open_catalog
    from .config import ConfigError
    keys = list(args.key or [])
    try:
        if args.keys:
            value = json.loads(Path(args.keys).read_text(encoding="utf-8"))
            if not isinstance(value, list) or any(not isinstance(key, str) or not key for key in value):
                raise ValueError("--keys must contain a JSON array of exact catalog key strings")
            keys.extend(value)
        client, native = ({}, {})
        apk = cfg.path("paths", "apk")
        if apk is not None:
            client, native = apk_identity(apk, native_fingerprints=args.native_fingerprints)
        elif args.native_fingerprints:
            raise ValueError("--native-fingerprints requires a configured APK set")
        document = observe(open_catalog(cfg), keys, region=cfg.region(), client=client, native=native, out=args.out)
        if args.public_bitmaps:
            path = Path(args.public_bitmaps)
            # A separately observed public listing is not native Sprite data.
            document["source"]["publicBitmaps"] = {"file": path.name, **fingerprint(path.read_bytes())}
        encoded = dumps(document, indent=1, ensure_ascii=False).encode("utf-8")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        write_atomic(Path(args.out), encoded)
    except (ValueError, OSError, KeyError) as exc:
        raise ConfigError(str(exc)) from None
    print(json.dumps({"file": str(Path(args.out).resolve()), "keys": len(set(keys)),
                      "sprites": len(document["sprites"]), **fingerprint(encoded)}))
