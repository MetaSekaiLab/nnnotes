"""`nnnotes pack`: JSON tables and UnityFS bundles -> the encrypted form the game stores.

The inverse of `master decode` and of the bundle decryption: what a build has to write into a CDN tree of its own.
Both keys come from the configuration (`[master] key`/`[master] iv`, `[bundle] key`/`[bundle] nonce_seed`), so a
packed file is only readable by a client configured with the same pair.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import NoReturn

from ..source.session import Session
from .common import command, group, out_arg, report


def _fail(message: str) -> NoReturn:
    """An error of this command: `nnnotes: …` on standard error, exit status 1."""
    print(f"nnnotes: {message}", file=sys.stderr)
    raise SystemExit(1)


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _inputs(inputs: list[Path], suffix: str) -> list[Path]:
    """Files given, and the `*<suffix>` files of directories given, sorted by name per directory."""
    out: list[Path] = []
    for p in map(Path, inputs):
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() == suffix)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(p)
    return out


def _write(path: Path, data: bytes) -> Path:
    from ..unity.pack import write_outputs

    write_outputs({path: data})
    return path


def _filename(name: str) -> str:
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0", ":")):
        raise ValueError("served names must be file names without directory components")
    return name


def _batch(outputs: dict[Path, bytes], path: Path, data: bytes) -> None:
    if path.name.casefold() in {p.name.casefold() for p in outputs}:
        raise ValueError(f"duplicate output file name {path.name!r}")
    outputs[path] = data


def cmd_master(args, cfg):
    from ..source import master
    from ..unity.pack import write_outputs

    try:
        files = _inputs(args.inputs, ".json")
    except OSError as e:
        _fail(str(e))
    if not files:
        _fail("no master data tables (*.json) among the inputs")
    key = Session(cfg).master_key()
    try:
        prefix = Path(args.prefix_from).read_bytes()[: master.PREFIX] if args.prefix_from else b""
    except OSError as e:
        _fail(str(e))
    if args.prefix_from and len(prefix) != master.PREFIX:
        _fail(f"{args.prefix_from} is shorter than the {master.PREFIX}-byte prefix")
    out = Path(args.out)
    outputs: dict[Path, bytes] = {}
    rk = master.round_keys(key.key)
    for src in files:
        try:
            data = master.encode(src.read_bytes(), key, prefix, rk)
            _batch(outputs, out / _filename(f"{src.stem}.bin"), data)
        except (OSError, ValueError) as e:
            _fail(f"{src.name}: {e}")
    packed = [str(path) for path in outputs]
    manifest = ""
    if args.manifest:
        listed = [
            {"name": p.name, "hash": hashlib.sha256(data).hexdigest(), "size": len(data)} for p, data in outputs.items()
        ]
        doc = {"version": args.manifest, "files": listed}
        manifest = str(out / "MasterManifest.json")
        outputs[Path(manifest)] = json.dumps(doc, indent=1).encode("utf-8")
    try:
        write_outputs(outputs)
    except (OSError, ValueError) as e:
        _fail(str(e))
    report(
        args,
        {"out": str(out), "packed": len(packed), "files": packed, "manifest": manifest or None},
        lambda r: [
            f"{r['out']}  ({r['packed']} tables packed)",
            *r["files"],
            *([r["manifest"]] if r["manifest"] else []),
        ],
    )


def cmd_bundle(args, cfg):
    from ..source import addressables
    from ..unity.pack import write_outputs

    try:
        files = _inputs(args.inputs, args.suffix)
    except OSError as e:
        _fail(str(e))
    if not files:
        _fail(f"no bundle files (*{args.suffix}) among the inputs")
    if args.name and len(files) != 1:
        _fail("--name applies to a single input")
    key = Session(cfg).bundle_key()
    out = Path(args.out)
    outputs: dict[Path, bytes] = {}
    for src in files:
        try:
            name = _filename(args.name or src.name)
            data = addressables.pack(src.read_bytes(), name, key)
            _batch(outputs, out / name, data)
        except (OSError, ValueError) as e:
            _fail(f"{src.name}: {e}")
    try:
        write_outputs(outputs)
    except (OSError, ValueError) as e:
        _fail(str(e))
    packed = [str(path) for path in outputs]
    report(
        args,
        {"out": str(out), "packed": len(packed), "files": packed},
        lambda r: [f"{r['out']}  ({r['packed']} bundles packed)", *r["files"]],
    )


def cmd_asset(args, cfg):
    from ..unity import bundlefs
    from ..unity import pack as assetpack

    out = Path(args.out)
    try:
        data = Path(args.data).read_bytes()
        assetpack.replace_text_asset(Path(args.bundle), out, args.name, data, new_name=args.new_name)
    except (OSError, KeyError, ValueError) as e:
        _fail(str(e).strip("'"))
    content = out.read_bytes()
    report(
        args,
        {
            "in": args.bundle,
            "out": str(out),
            "asset": args.new_name or args.name,
            "payloadBytes": len(data),
            "bundleBytes": len(content),
            "crc": bundlefs.content_crc(content),
        },
        lambda r: [
            f"{r['out']}  (asset {r['asset']}: {r['payloadBytes']} B payload, {r['bundleBytes']} B bundle, "
            f"catalog crc {r['crc']})"
        ],
    )


def cmd_build(args, cfg):
    from ..source import addressables, catalogbuild
    from ..unity import pack as assetpack

    try:
        manifest = Path(args.file)
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        if doc.get("format") == "nnnotes.song-package/1":
            from ..packing import build_package

            if args.encrypt or args.catalog_add or args.catalog_out or args.name:
                raise ValueError("song builds define bundle and catalog outputs through their target manifest")
            package = build_package(manifest, out=args.out, config=cfg)
            report(
                args,
                {"out": str(package.root), **package.manifest},
                lambda r: [f"{r['out']}  (song {r['song']}, {len(r['bundles'])} bundles)"],
            )
            return
        result = assetpack.build_manifest(doc, base=manifest.resolve().parent)
        catalog_source = args.catalog or (cfg.path("paths", "catalog") if args.catalog_out and cfg else None)
        existing = Path(catalog_source).read_bytes() if catalog_source else None
        if args.catalog_out and existing is None:
            raise ValueError("--catalog-out requires --catalog SOURCE")
        catalog_template = doc.get("catalog_template")
        if existing is not None and doc.get("mode") == "edit" and catalog_template is None and doc.get("internal_id"):
            matches = [
                location
                for location in addressables.parse_locations(existing)
                if location["resource_type"] == catalogbuild.BUNDLE_TYPE
                and (
                    location["internal_id"] == doc["internal_id"]
                    or location["internal_id"].rsplit("/", 1)[-1] == doc["internal_id"].rsplit("/", 1)[-1]
                )
            ]
            if len(matches) == 1:
                catalog_template = matches[0]["internal_id"]
        provider, request_options = catalogbuild.bundle_settings(
            existing,
            provider=doc.get("bundle_provider"),
            template=catalog_template,
            request_options=doc.get("request_options"),
        )
        out = Path(args.out)
        outputs = {out: result.data}
        served_name = _filename(args.name or (Path(args.encrypt).name if args.encrypt else out.name))
        if args.encrypt:
            outputs[Path(args.encrypt)] = addressables.pack(result.data, served_name, Session(cfg).bundle_key())
        internal_id = doc.get("internal_id")
        if (args.catalog_add or args.catalog_out) and not internal_id:
            raise ValueError("catalog output requires a bundle internal_id in the build manifest")
        if internal_id and internal_id.rsplit("/", 1)[-1] != served_name:
            raise ValueError("bundle internal_id file name must match the served file name")
        description = {
            "file": str(out.resolve()),
            "internal_id": internal_id,
            "bundle_name": result.bundle_name,
            "bundle_provider": provider,
            "assets": result.assets,
            "hash": doc.get("hash") or hashlib.md5(result.data).hexdigest(),
            "request_options": request_options,
        }
        if doc.get("mode") == "edit":
            description.update(mode="edit", catalog_edits=result.catalog_edits)
            if catalog_template:
                description["catalog_template"] = catalog_template
        for field in ("primary_key", "dependency_keys"):
            if field in doc:
                description[field] = doc[field]
        if args.catalog_add:
            outputs[Path(args.catalog_add)] = (
                json.dumps({"bundles": [description]}, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            )
        if args.catalog_out:
            assert existing is not None
            if doc.get("mode") == "edit":
                additions, replacements = catalogbuild.edit_entries(
                    existing,
                    internal_id,
                    result.bundle_name,
                    result.assets,
                    result.catalog_edits,
                    hash=description["hash"],
                    crc=result.crc,
                    bundle_size=len(result.data),
                    primary_key=doc.get("primary_key"),
                    request_options=request_options,
                    dependency_keys=doc.get("dependency_keys"),
                    provider=provider,
                    template=catalog_template,
                )
                outputs[Path(args.catalog_out)] = catalogbuild.rebuild(existing, add=additions, replace=replacements)
            else:
                additions = catalogbuild.bundle_entries(
                    internal_id,
                    result.bundle_name,
                    result.assets,
                    hash=description["hash"],
                    crc=result.crc,
                    bundle_size=len(result.data),
                    primary_key=doc.get("primary_key"),
                    request_options=request_options,
                    dependency_keys=doc.get("dependency_keys", ()),
                    provider=provider,
                )
                drop = {entry["primary_key"] for entry in additions}
                outputs[Path(args.catalog_out)] = catalogbuild.rebuild(existing, add=additions, drop=drop)
        expected = 1 + bool(args.encrypt) + bool(args.catalog_add) + bool(args.catalog_out)
        if len(outputs) != expected:
            raise ValueError("output paths must be distinct")
        assetpack.write_outputs(outputs)
    except (OSError, ValueError, KeyError, TypeError) as e:
        _fail(str(e).strip("'"))
    report(
        args,
        {
            "out": str(out),
            "bundle_name": result.bundle_name,
            "bundle_provider": provider,
            "assets": result.assets,
            "bundleBytes": len(result.data),
            "crc": result.crc,
            "hash": description["hash"],
            "encrypted": args.encrypt,
            "catalog": args.catalog_out,
            "catalog_add": args.catalog_add,
        },
        lambda r: [
            f"{r['out']}  ({len(r['assets'])} assets, {r['bundleBytes']} B, crc {r['crc']})",
            *[str(path) for path in outputs if path != out],
        ],
    )


def cmd_plan(args, cfg):
    from ..packing import plan_package

    try:
        plan = plan_package(args.file, config=cfg)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        _fail(str(error))
    report(
        args,
        plan.report,
        lambda r: [
            f"{len(r['assets'])} assets; {len(r['diagnostics'])} errors",
            *[f"{item['path']}: {item['message']}" for item in r["diagnostics"]],
        ],
    )
    if plan.report["diagnostics"]:
        raise SystemExit(1)


def cmd_verify(args, cfg):
    from ..packing import verify_package

    try:
        result = verify_package(args.directory, target=args.target, config=cfg)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        _fail(str(error))
    report(args, result, lambda r: [f"{r['status']}: {r['files']} files, {r['bundles']} bundles"])


def cmd_audio(args, cfg):
    from ..core.jsonio import dumps
    from ..packing import AudioOptions, AudioTools, Target, build_audio
    from ..unity.pack import write_outputs

    try:
        target = Target.load(args.target, cfg)
        template, cipher, key = target.audio(args.template)
        options = AudioOptions(
            sample_rate=args.sample_rate,
            channels=args.channels,
            stream=args.stream,
            gain_db=args.gain_db,
            start_ms=args.start_ms,
            duration_ms=args.duration_ms,
            fade_in_ms=args.fade_in_ms,
            fade_out_ms=args.fade_out_ms,
            quality=args.quality,
        )
        result = build_audio(
            args.source,
            template,
            cue_name=args.cue_name,
            options=options,
            tools=AudioTools.configured(cfg),
            cipher_type=cipher,
            key=key,
        )
        out = Path(args.out)
        report_path = Path(args.report) if args.report else out.with_suffix(out.suffix + ".json")
        if out.resolve() == report_path.resolve() or args.source.resolve() in (out.resolve(), report_path.resolve()):
            raise ValueError("audio input, ACB output and report paths must be distinct")
        write_outputs({out: result.data, report_path: (dumps(result.report, indent=2) + "\n").encode("utf-8")})
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        _fail(str(error))
    report(
        args,
        {"out": str(out), "report": str(report_path), **result.report},
        lambda r: [
            f"{r['out']}  ({r['processed']['samples']} samples, {r['processed']['sampleRate']} Hz)",
            r["report"],
        ],
    )


def register(sub) -> None:
    ps = group(sub, "pack", "build audio, song packages, master tables and UnityFS bundles from local inputs")
    p = command(ps, "plan", cmd_plan, "check a song project and its local resource dependencies")
    p.add_argument("file", type=Path, help="nnnotes.song-package/1 JSON project")
    p = command(ps, "verify", cmd_verify, "verify a generated song package against its local baseline")
    p.add_argument("directory", type=Path, help="directory containing package.json")
    p.add_argument("--target", type=Path, required=True, help="nnnotes.pack-target/1 JSON target")
    p = command(ps, "audio", cmd_audio, "MP3/WAV/FLAC/AAC -> a validated single-cue HCA ACB")
    p.add_argument("source", type=Path, help="local source audio file")
    out_arg(p, "output ACB file")
    p.add_argument(
        "--target", type=Path, required=True, help="JSON target with local ACB templates and cipher settings"
    )
    p.add_argument("--template", choices=("full", "preview"), default="full", help="target ACB profile (default full)")
    p.add_argument("--cue-name", required=True, help="name of the output cue and sheet")
    p.add_argument("--report", type=Path, help="validation JSON path (default <output>.json)")
    p.add_argument(
        "--sample-rate", type=int, choices=(44100, 48000), help="output sample rate (default preserve input)"
    )
    p.add_argument("--channels", type=int, choices=(1, 2), help="output channels (default preserve input)")
    p.add_argument("--stream", type=int, help="source audio stream index when the file contains several tracks")
    p.add_argument("--gain-db", type=float, default=0, help="explicit PCM gain in decibels (default 0)")
    p.add_argument("--start-ms", type=int, default=0, help="crop start on the decoded timeline (default 0)")
    p.add_argument("--duration-ms", type=int, help="crop duration; omitted keeps the remainder")
    p.add_argument("--fade-in-ms", type=int, default=0, help="linear fade-in duration (default 0)")
    p.add_argument("--fade-out-ms", type=int, default=0, help="linear fade-out duration (default 0)")
    p.add_argument(
        "--quality",
        choices=("highest", "high", "middle", "low", "lowest"),
        default="high",
        help="HCA encoder quality preset (default high)",
    )
    p = command(ps, "master", cmd_master, "master data tables (.json) -> <Table>.bin (Rijndael-256-CBC over gzip JSON)")
    p.add_argument("inputs", nargs="+", type=Path, help="directories (their *.json files) or files")
    out_arg(p, "output directory")
    p.add_argument(
        "--prefix-from",
        metavar="BIN",
        help="copy the 64-byte prefix of this master data file (the loader ignores it; a replacement should keep it)",
    )
    p.add_argument(
        "--manifest",
        metavar="VERSION",
        help="also write MasterManifest.json (the file `master download` reads) for this master data version",
    )
    p = command(ps, "bundle", cmd_bundle, "UnityFS bundles -> the form the CDN serves (AES-128-CTR, first 16 KiB)")
    p.add_argument("inputs", nargs="+", type=Path, help="directories (their bundles) or files")
    out_arg(p, "output directory")
    p.add_argument(
        "--suffix",
        default=".bundle",
        help="the file name suffix bundles have among the inputs (default .bundle)",
    )
    p.add_argument(
        "--name",
        metavar="NAME",
        help="the file name the bundle is served under (it seeds the nonce; default: the input's name)",
    )
    p = command(ps, "asset", cmd_asset, "replace a binary TextAsset in a bundle; --new-name changes its object name")
    p.add_argument("bundle", type=Path, help="the bundle to copy")
    out_arg(p, "output bundle")
    p.add_argument("--name", required=True, help="the TextAsset's name inside the bundle")
    p.add_argument("--data", required=True, help="the file whose bytes become the asset's payload")
    p.add_argument("--new-name", help="also rename the object; its container path is preserved")
    p = command(ps, "build", cmd_build, "build a song package or a UnityFS bundle from a JSON manifest")
    p.add_argument("file", type=Path, help="song project or asset manifest with template, mode and asset operations")
    out_arg(p, "new song package directory, or output UnityFS bundle")
    p.add_argument("--encrypt", metavar="FILE", help="also write the encrypted bundle to this file")
    p.add_argument("--name", metavar="NAME", help="served file name used for encryption and catalog validation")
    p.add_argument("--catalog-add", metavar="JSON", help="also write a manifest accepted by catalog rebuild --add")
    p.add_argument("--catalog-out", metavar="FILE", help="rebuild the --catalog source with the bundle's locations")
