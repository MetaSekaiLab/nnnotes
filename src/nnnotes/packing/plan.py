"""Offline package planning, input fingerprints and structured diagnostics."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..content import score
from ..core.jsonio import dumps
from ..source import addressables
from ..source.config import Config, ConfigError
from . import assets
from .audio import AudioOptions, AudioTools, probe_audio
from .project import SongProject, Target, fingerprint
from .song import DIFFICULTIES, assemble, find

__all__ = ["PackagePlan", "plan_package"]


@dataclass
class PackagePlan:
    """Resolved local inputs and a public report. Errors prevent building."""

    project: SongProject
    report: dict
    target: Target | None = field(default=None, repr=False)
    tables: dict = field(default_factory=dict, repr=False)
    names: dict = field(default_factory=dict, repr=False)
    charts: dict = field(default_factory=dict, repr=False)
    templates: dict = field(default_factory=dict, repr=False)
    audio_options: dict = field(default_factory=dict, repr=False)
    inputs: dict[Path, str] = field(default_factory=dict, repr=False)

    def raise_for_errors(self) -> None:
        diagnostics = self.report["diagnostics"]
        if diagnostics:
            raise ValueError("; ".join(f"{item['path']}: {item['message']}" for item in diagnostics))

    def read(self, path: Path) -> bytes:
        data = path.read_bytes()
        self.inputs[path] = fingerprint(data)
        return data

    def check_inputs(self) -> None:
        self.raise_for_errors()
        for path, expected in self.inputs.items():
            if fingerprint(path.read_bytes()) != expected:
                raise ValueError(f"build input changed: {path.name}")
        if self.target:
            self.target.check_inputs()


def plan_package(
    project: SongProject | str | Path, *, config: Config | None = None, tools: AudioTools | None = None
) -> PackagePlan:
    """Resolve a local song package without downloading or writing resource outputs."""
    project = project if isinstance(project, SongProject) else SongProject.load(project)
    report = {"format": "nnnotes.package-plan/1", "diagnostics": [], "charts": {}, "assets": [], "master": []}
    plan = PackagePlan(project, report)
    doc = project.document

    def attempt(path, operation):
        try:
            return operation()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ConfigError) as error:
            report["diagnostics"].append({"code": "invalid_input", "path": path, "message": str(error)})
            return None

    plan.read(project.path)
    target = attempt("/target", lambda: Target.load(project.file(doc["target"]), config))
    if target is None:
        return plan
    plan.target = target
    for difficulty in DIFFICULTIES:

        def load_chart():
            spec = doc["charts"][difficulty]
            data = plan.read(project.file(spec["file"]))
            if spec.get("input_format") == "gzip-json" and not data.startswith(b"\x1f\x8b"):
                raise ValueError("chart input_format gzip-json requires a gzip payload")
            if spec.get("input_format") == "json" and data.startswith(b"\x1f\x8b"):
                raise ValueError("chart input_format json requires an uncompressed JSON payload")
            root = score.load_bytes(data)
            runtime = score.convert(root)
            if runtime["log"]:
                raise ValueError("chart conversion reported unresolved notes: " + "; ".join(runtime["log"]))
            if not runtime["judgementNoteCount"]:
                raise ValueError("chart must contain playable notes")
            if not isinstance(spec["level"], int) or isinstance(spec["level"], bool) or spec["level"] <= 0:
                raise ValueError("chart level must be a positive integer")
            payload = data if data.startswith(b"\x1f\x8b") else gzip.compress(data, mtime=0)
            plan.charts[difficulty] = payload
            report["charts"][difficulty] = {
                "notes": runtime["judgementNoteCount"],
                "lastNoteMs": runtime["lastNoteTimeMs"],
                "sha256": fingerprint(payload),
            }

        attempt("/charts/" + difficulty, load_chart)
    table_names = (
        "MasterLiveMusic",
        "MasterLiveMusicScore",
        "MasterText",
        "MasterSound",
        "MasterSoundCueSheet",
        "MasterLiveScoreRank",
        "MasterTag",
        "MasterBand",
    )
    baseline = attempt("/target/master", lambda: target.tables(table_names))
    if baseline is None or len(plan.charts) != len(DIFFICULTIES):
        return plan
    assembled = attempt("/song", lambda: assemble(baseline, doc, report["charts"]))
    if assembled is None:
        return plan
    plan.tables, plan.names = assembled
    report["song"] = doc["song"]["id"]
    report["master"] = [name for name in plan.tables if plan.tables[name] != baseline[name]]
    report["masterChanges"] = {}
    for name in report["master"]:
        original_ids = {row["_id"] for row in baseline[name]["_allData"]}
        report["masterChanges"][name] = {
            "addedIds": [row["_id"] for row in plan.tables[name]["_allData"] if row["_id"] not in original_ids]
        }
    report["names"] = plan.names
    music = find(baseline, "MasterLiveMusic", doc["template_song"])
    chart_row = find(baseline, "MasterLiveMusicScore", music["_easyID"])
    selectors: dict[str, tuple[str, str | None]] = {
        "chart": ("Live/MusicScore/" + chart_row["_musicScoreTextFileName"], "UnityEngine.TextAsset"),
        "jacket": ("Image/Jacket/" + music["_jacketAssetName"], "UnityEngine.Sprite"),
        "jacket_small": ("Image/Jacket/small/" + music["_jacketAssetName"], "UnityEngine.Sprite"),
    }
    for kind, sound_field in (("full", "_musicSoundID"), ("preview", "_jingleSoundID")):
        sound = find(baseline, "MasterSound", music[sound_field])
        sheet = find(baseline, "MasterSoundCueSheet", sound["_soundCueSheetID"])
        selectors[kind] = ("Cri/Sound/" + sheet["_cueSheetName"], None)
    for kind, (key, resource_type) in selectors.items():
        template = attempt("/target/templates/" + kind, lambda: assets.select_template(target, key, resource_type))
        if template:
            plan.templates[kind] = template
            transport = attempt(
                "/target/providers/" + kind, lambda selected=template: target.transport(selected.bundle["provider"])
            )
            report.setdefault("templates", {})[kind] = {
                "asset": key,
                "provider": template.bundle["provider"],
                "transport": transport,
                "dependencies": template.dependencies,
            }

    def jacket():
        path = project.file(doc["assets"]["jacket"])
        plan.read(path)
        with Image.open(path) as image:
            if image.format != "PNG" or image.width != image.height:
                raise ValueError("jacket must be a square PNG")

    attempt("/assets/jacket", jacket)
    audio = doc["assets"]["audio"]
    for kind in ("full", "preview"):

        def audio_input():
            spec = audio[kind]
            if kind == "preview" and spec.get("from") == "full":
                if "file" in spec or "start_ms" not in spec or "duration_ms" not in spec:
                    raise ValueError("preview from full requires start_ms/duration_ms and no file")
                source = audio["full"]["file"]
            else:
                if "from" in spec:
                    raise ValueError("audio from is supported only for preview from full")
                source = spec["file"]
            path = project.file(source)
            plan.read(path)
            options = AudioOptions(
                **{
                    **audio.get("processing", {}),
                    **{key: value for key, value in spec.items() if key not in ("file", "from")},
                }
            )
            selected_tools = tools or AudioTools.configured(target.session.cfg)
            report["tools"] = selected_tools.fingerprints()
            info = probe_audio(path, selected_tools, stream=options.stream)
            target.audio(kind)
            plan.audio_options[kind] = (path, options)
            report.setdefault("audio", {})[kind] = {"source": info, "options": options.__dict__}

        attempt("/assets/audio/" + kind, audio_input)
    existing = {item["primary_key"] for item in addressables.parse_locations(target.catalog())}
    keys = [
        "Image/Jacket/" + plan.names["jacket"],
        "Image/Jacket/small/" + plan.names["jacket"],
        "Cri/Sound/" + plan.names["full"],
        "Cri/Sound/" + plan.names["preview"],
        *["Live/MusicScore/" + name for name in plan.names["charts"].values()],
    ]
    for key in keys:
        if key in existing:
            report["diagnostics"].append(
                {"code": "key_conflict", "path": "/song/id", "message": f"resource key exists: {key}"}
            )
    report["assets"] = keys
    report["inputFingerprint"] = fingerprint(
        dumps(
            {
                "project": fingerprint(project.path.read_bytes()),
                "inputs": sorted([*plan.inputs.values(), *target.inputs.values()]),
            }
        ).encode()
    )
    return plan
