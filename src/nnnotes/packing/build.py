"""Construct and verify portable song packages against an explicit local baseline."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import UnityPy
from jsonschema import Draft202012Validator

from .. import schemas
from ..content import score
from ..core.jsonio import dumps
from ..formats.acb import cue_streams
from ..source import addressables, catalogbuild, master
from ..unity import bundlefs
from .assets import audio_bundle, chart_bundle, containers, extract_acb, jacket_bundle
from .audio import AudioTools, build_audio
from .plan import PackagePlan, plan_package
from .project import SongProject, Target, fingerprint
from .song import DIFFICULTIES, find

__all__ = ["PackageResult", "build_package", "verify_package"]


def _json(value) -> bytes:
    return (dumps(value, indent=2) + "\n").encode("utf-8")


@dataclass(frozen=True)
class PackageResult:
    root: Path
    manifest: dict


def _verify(root: Path, target: Target | None) -> dict:
    manifest = json.loads((root / "package.json").read_bytes())
    if manifest.get("format") != "nnnotes.song-build/1":
        raise ValueError("unsupported song package format")
    schema_errors = list(Draft202012Validator(schemas.load("song-build")).iter_errors(manifest))
    if schema_errors:
        raise ValueError(f"invalid song package manifest: {schema_errors[0].message}")
    declared = set()
    for item in manifest["files"]:
        relative = Path(item["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root.resolve()) or item["path"] in declared:
            raise ValueError("package contains an invalid or duplicate file path")
        declared.add(item["path"])
        data = path.read_bytes()
        if len(data) != item["size"] or fingerprint(data) != item["sha256"]:
            raise ValueError(f"package file integrity mismatch: {item['path']}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != declared | {"package.json"}:
        raise ValueError("package file set does not match its manifest")

    def content(relative: str) -> bytes:
        if relative not in declared:
            raise ValueError("package references a file absent from its file manifest")
        return (root / relative).read_bytes()

    catalog = content("catalog/catalog.bin")
    if (root / "catalog/catalog.hash").read_text(encoding="ascii") != fingerprint(catalog)[:32]:
        raise ValueError("catalog hash does not match catalog.bin")
    locations = addressables.parse_locations(catalog)
    offsets = {item["offset"] for item in locations}
    if any(dependency not in offsets for item in locations for dependency in item["dependencies"]):
        raise ValueError("catalog contains an unresolved dependency")
    cabs = set()
    built_assets = {}
    for bundle in manifest["bundles"]:
        data = content(bundle["path"])
        if bundle["transport"] not in ("plain", "encrypted"):
            raise ValueError("unsupported bundle transport")
        if bundle["transport"] == "encrypted":
            if target is None:
                raise ValueError("encrypted bundle verification requires the target configuration")
            data = addressables.decrypt(data, Path(bundle["path"]).name, target.session.bundle_key())
        if bundlefs.content_crc(data) != bundle["crc"] or hashlib.md5(data).hexdigest() != bundle["hash"]:
            raise ValueError("bundle content does not match its catalog identity")
        env = UnityPy.load(data)
        for file in env.assets:
            if file.name in cabs:
                raise ValueError("new bundles share a serialized file identity")
            cabs.add(file.name)
        matches = [item for item in locations if item["internal_id"] == bundle["internal_id"]]
        if len(matches) != 1 or matches[0]["provider"] != bundle["provider"]:
            raise ValueError("bundle catalog location does not match its provider")
        options = matches[0]["extra_data"]
        if (options["crc"], options["bundleSize"], options["hash"]) != (bundle["crc"], len(data), bundle["hash"]):
            raise ValueError("catalog bundle request options do not match the file")
        for asset in bundle["assets"]:
            if asset["internal_id"] not in containers(env):
                raise ValueError("catalog container does not resolve to its bundled asset")
            matches = [
                item
                for item in locations
                if item["primary_key"] == asset["primary_key"] and item["resource_type"] == asset["resource_type"]
            ]
            if len(matches) != 1 or matches[0]["internal_id"] != asset["internal_id"]:
                raise ValueError("asset catalog key/type does not match its container")
            built_assets[asset["primary_key"]] = (containers(env)[asset["internal_id"]], bundle)
            if bundle.get("audio"):
                acb = content(bundle["audio"])
                if extract_acb(data, asset["internal_id"]) != acb:
                    raise ValueError("bundled ACB does not match the audio artifact")
    master_manifest = json.loads(content("master/MasterManifest.json"))
    if master_manifest["version"] != manifest["version"]:
        raise ValueError("package and master manifest versions differ")
    baseline = target.master_files() if target else {}
    if target and fingerprint(target.catalog()) != manifest["baseline"]["catalogSha256"]:
        raise ValueError("target catalog does not match the package baseline")
    for item in master_manifest["files"]:
        if Path(item["name"]).name != item["name"]:
            raise ValueError("master manifest contains an invalid table path")
        path = root / "master" / item["name"]
        data = path.read_bytes() if path.is_file() else baseline.get(item["name"])
        if data is None:
            raise ValueError("package verification requires its local master baseline")
        if len(data) != item["size"] or fingerprint(data) != item["hash"]:
            raise ValueError("master manifest does not match a generated or baseline table")
    if target:

        def table(name):
            relative = f"master/{name}.bin"
            raw = content(relative) if relative in declared else baseline[name + ".bin"]
            return json.loads(master.decode(raw, target.session.master_key()))

        names = (
            "MasterLiveMusic",
            "MasterLiveMusicScore",
            "MasterText",
            "MasterSound",
            "MasterSoundCueSheet",
            "MasterTag",
            "MasterBand",
            "MasterLiveScoreRank",
        )
        tables = {name: table(name) for name in names}
        song = find(tables, "MasterLiveMusic", manifest["song"])
        for field, identity in song.items():
            if field.endswith("TextID"):
                find(tables, "MasterText", identity)
        for band in song["_bandIDs"]:
            find(tables, "MasterBand", band)
        for tag in song["_bestMusicTagIDs"]:
            record = find(tables, "MasterTag", tag)
            find(tables, "MasterText", record["_nameTextID"])
        if not any(row["_group"] == song["_liveScoreRankGroup"] for row in tables["MasterLiveScoreRank"]["_allData"]):
            raise ValueError("song score rank group is missing")
        for field in ("_musicSoundID", "_jingleSoundID"):
            sound = find(tables, "MasterSound", song[field])
            sheet = find(tables, "MasterSoundCueSheet", sound["_soundCueSheetID"])
            _, bundle = built_assets["Cri/Sound/" + sheet["_cueSheetName"]]
            cues = cue_streams(content(bundle["audio"]))
            if sound["_cueName"] not in cues:
                raise ValueError("song cue name is absent from its ACB")
        for difficulty in DIFFICULTIES:
            chart = find(tables, "MasterLiveMusicScore", song[f"_{difficulty}ID"])
            obj, _ = built_assets["Live/MusicScore/" + chart["_musicScoreTextFileName"]]
            from ..unity.pack import parse_text_asset

            payload = parse_text_asset(obj.get_raw_data(), endian=obj.assets_file.header.endian).payload
            if score.convert(score.load_bytes(payload))["judgementNoteCount"] != chart["_fullComboCount"]:
                raise ValueError("chart combo count does not match its master row")
        for prefix in ("Image/Jacket/", "Image/Jacket/small/"):
            if prefix + song["_jacketAssetName"] not in built_assets:
                raise ValueError("song jacket reference is missing")
    return {
        "format": "nnnotes.package-validation/1",
        "status": "passed",
        "files": len(declared),
        "bundles": len(manifest["bundles"]),
        "masterTables": len(master_manifest["files"]),
    }


def verify_package(root: str | Path, *, target: Target | str | Path | None = None, config=None) -> dict:
    """Validate files, bundle payloads, catalog references and master files without accessing the network."""
    selected = Target.load(target, config) if isinstance(target, (str, Path)) else target
    return _verify(Path(root).resolve(), selected)


def build_package(
    plan: PackagePlan | SongProject | str | Path, *, out: str | Path, config=None, tools: AudioTools | None = None
) -> PackageResult:
    """Build into a new directory; all required validations finish before the directory becomes visible."""
    plan = plan if isinstance(plan, PackagePlan) else plan_package(plan, config=config, tools=tools)
    plan.check_inputs()
    target = plan.target
    if target is None:
        raise ValueError("package plan has no target")
    tools = tools or AudioTools.configured(target.session.cfg)
    if tools.fingerprints() != plan.report.get("tools"):
        raise ValueError("audio tools changed after planning")
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("package output must be a new directory")
    project, names = plan.project, plan.names
    outputs: dict[str, bytes] = {}
    records, additions, audio_reports = [], [], {}
    catalog = target.catalog()
    templates = plan.templates
    url = target.document["bundle_base_url"].rstrip("/")
    if not url or "\0" in url:
        raise ValueError("target bundle_base_url must be a nonempty runtime URL/path")

    def publish(result, template, audio=None):
        digest = hashlib.md5(result.data).hexdigest()
        name = f"{result.bundle_name}_{digest}.bundle"
        internal = url + "/" + name
        provider, options = catalogbuild.bundle_settings(catalog, template=template.bundle["internal_id"])
        transport = target.transport(provider)
        data = (
            addressables.pack(result.data, name, target.session.bundle_key())
            if transport == "encrypted"
            else result.data
        )
        path = "bundles/" + name
        if path in outputs:
            raise ValueError("bundle output name collision")
        outputs[path] = data
        additions.extend(
            catalogbuild.bundle_entries(
                internal,
                result.bundle_name,
                result.assets,
                hash=digest,
                crc=result.crc,
                bundle_size=len(data),
                provider=provider,
                request_options=options,
            )
        )
        records.append(
            {
                "path": path,
                "internal_id": internal,
                "provider": provider,
                "transport": transport,
                "hash": digest,
                "crc": result.crc,
                "assets": result.assets,
                **({"audio": audio} if audio else {}),
            }
        )

    for difficulty in DIFFICULTIES:
        name = names["charts"][difficulty]
        publish(
            chart_bundle(templates["chart"], plan.charts[difficulty], name, name.replace("/", "_")), templates["chart"]
        )
    png = project.file(project.document["assets"]["jacket"]).read_bytes()
    for kind, small in (("jacket", False), ("jacket_small", True)):
        publish(
            jacket_bundle(templates[kind], png, names["jacket"], f"{names['jacket']}_{kind}", small=small),
            templates[kind],
        )
    for kind in ("full", "preview"):
        template, cipher, key = target.audio(kind)
        source, options = plan.audio_options[kind]
        audio = build_audio(
            source, template, cue_name=names[kind], tools=tools, options=options, cipher_type=cipher, key=key
        )
        if kind == "full":
            duration = audio.report["processed"]["samples"] * 1000 / audio.report["processed"]["sampleRate"]
            if any(chart["lastNoteMs"] > duration for chart in plan.report["charts"].values()):
                raise ValueError("chart has notes after the full audio ends")
        audio_path = f"audio/{kind}.acb"
        outputs[audio_path] = audio.data
        outputs[f"audio/{kind}.json"] = _json(audio.report)
        audio_reports[kind] = audio.report["validation"]
        publish(
            audio_bundle(templates[kind], audio.data, names[kind], f"{names[kind]}_audio"), templates[kind], audio_path
        )
    outputs["catalog/catalog.bin"] = catalogbuild.rebuild(catalog, add=additions)
    outputs["catalog/catalog.hash"] = fingerprint(outputs["catalog/catalog.bin"])[:32].encode("ascii")
    originals = target.master_files()
    key = target.session.master_key()
    round_keys = master.round_keys(key.key)
    for name in plan.report["master"]:
        original = originals[name + ".bin"]
        encoded = master.encode(_json(plan.tables[name]), key, original[: master.PREFIX], round_keys)
        if json.loads(master.decode(encoded, key, round_keys)) != plan.tables[name]:
            raise ValueError("master table did not preserve its assembled rows")
        outputs[f"master/{name}.bin"] = encoded
    version = project.document.get("version") or fingerprint(b"".join(outputs[name] for name in sorted(outputs)))[:32]
    manifest = {"version": version, "files": []}
    for name, original in sorted(originals.items()):
        data = outputs.get("master/" + name, original)
        manifest["files"].append({"name": name, "hash": fingerprint(data), "size": len(data)})
    outputs["master/MasterManifest.json"] = _json(manifest)
    outputs["validation.json"] = _json(
        {
            "format": "nnnotes.package-validation/1",
            "structure": "passed",
            "audio": audio_reports,
            "inputFingerprint": plan.report["inputFingerprint"],
        }
    )
    package = {
        "format": "nnnotes.song-build/1",
        "song": project.document["song"]["id"],
        "version": version,
        "baseline": {"catalogSha256": fingerprint(catalog)},
        "bundles": records,
        "files": [
            {"path": name, "size": len(data), "sha256": fingerprint(data)} for name, data in sorted(outputs.items())
        ],
    }
    outputs["package.json"] = _json(package)
    plan.check_inputs()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".nnnotes-package-") as directory:
        staging = Path(directory) / "package"
        for name, data in outputs.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        _verify(staging, target)
        os.replace(staging, out)
    return PackageResult(out, package)
