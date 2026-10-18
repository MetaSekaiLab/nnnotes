"""Standalone file exports assembled from the same object and audio products used by sites."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..content import adv, storyepisode
from ..formats.encoders import encode_args
from ..formats.shader import _safe as safe_name
from . import contract
from .assemble import MASTER_INPUT, Assembly
from .cristages import _tool, tool_id
from .stages import Output, Stage
from .story import SoundStage, StoryStage
from .views import master_rows


class EpisodeStage(StoryStage):
    """The episode document alone, without its playback products."""

    name, version = "export.episode", 1
    KIND = "Episode"
    MASTER = ("MasterAdv",)

    def record(self, subject, env):
        tables = master_rows(env, self.MASTER)
        row = next(r for r in tables["MasterAdv"] if r["_id"] == int(subject))
        tables["MasterText"] = master_rows(env, ("MasterText",))["MasterText"] if row["_titleTextId"] else []
        tables["MasterAdvChat"] = (
            master_rows(env, ("MasterAdvChat",))["MasterAdvChat"]
            if (Path(env.fact("master")) / "MasterAdvChat.json").is_file()
            else []
        )
        record = storyepisode.episode_record(tables, int(subject))
        record["catalogKeys"] = sorted(
            {row["primaryKey"] for row in (env.facts.get("index") or {}).get("locations", [])}
        )
        return record

    def assemble(self, task, objects):
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        keys = set(record["catalogKeys"])
        episode = adv.to_json(storyepisode.episode(objects, record, has=keys.__contains__))
        return Assembly({"episode.json": (contract.encode(episode), "json", "story.episode")}, set())


class AudioFilesStage(SoundStage):
    """Every decoded stream of a cue sheet, with its cue and stream indexes."""

    name, version = "export.audio", 1
    PARAMS = {"audio": "flac"}

    def normalize(self, params):
        params = Stage.normalize(self, params)
        if params["audio"] not in {"flac", "ogg", "wav"}:
            raise ValueError("export.audio format must be flac, ogg or wav")
        return params

    def uses(self, subject, env):
        return {} if env.params["audio"] == "flac" else {"audio.encode": tool_id("ffmpeg", _tool("ffmpeg"))}

    def assemble(self, task, objects):
        fmt = task.params["audio"]
        sheet = objects.sheet(f"Cri/Sound/{task.subject}")
        files, renamed = {}, {}
        encoder = _tool("ffmpeg") if fmt != "flac" else None
        if encoder and task.atoms != {"audio.encode": tool_id("ffmpeg", encoder)}:
            raise contract.IncompatibleTask("the audio encoder differs from the task's encoder")
        for stream in sheet.streams():
            source = stream["file"]
            name = str(Path(source).with_suffix("." + fmt))
            renamed[source] = name
            if encoder is None:
                body = sheet.file(source)
            else:
                codec = ["-c:a", "libvorbis", "-q:a", "5"] if fmt == "ogg" else ["-c:a", "pcm_s16le"]
                with tempfile.TemporaryDirectory(prefix="nnnotes-audio-") as temp:
                    src, dst = Path(temp) / "input.flac", Path(temp) / ("output." + fmt)
                    src.write_bytes(sheet.bytes(source))
                    result = subprocess.run([encoder, *encode_args(src, dst, codec)], capture_output=True, text=True)
                    if result.returncode:
                        raise RuntimeError(f"audio encoding failed: {result.stderr[-2000:]}")
                    body = dst.read_bytes()
            files[name] = (body, fmt, "audio.stream")
        for name in ("cues.json", "streams.json"):
            doc = contract.loads(sheet.bytes(name))
            for entry in doc.values() if isinstance(doc, dict) else doc:
                entry["file"] = renamed[entry["file"]]
            files[name] = (contract.encode(doc), "json", "cri.cues" if name == "cues.json" else "cri.streams")
        return Assembly(files, set())


class ShaderFilesStage(Stage):
    """A shader directory from converted Shader object artifacts, without reading bundles again."""

    name, version = "export.shader", 1

    def subjects(self, env):
        return []

    def run(self, task, depot):
        records = contract.loads(depot.input_bytes(task.input("records")))
        files, index, names = {}, [], {}

        def put(path, content):
            if path in files and files[path] != content:
                raise ValueError(f"shader files have different contents at {path}")
            files[path] = content

        for record in records:
            artifacts = record["artifacts"]
            for item in record["items"]:
                if item.get("class") != "Shader" or item["status"] != "exported":
                    continue
                arts = {contract.parse_artifact_id(a)[1]: artifacts[a] for a in item["artifacts"]}
                variant = contract.loads(depot.read(arts["index"]["content"]["sha256"]))
                name = variant["name"]
                signature = contract.digest({role: art["content"] for role, art in arts.items() if role != "typetree"})
                if name in names:
                    if names[name] != signature:
                        raise ValueError(f"shader {name}: differing contents share a name")
                    continue
                names[name] = signature
                parsed = safe_name(name) + ".json"
                put(parsed, arts["json"]["content"])
                variants = []
                for entry in variant["variants"]:
                    put(entry["file"], arts[entry["role"]]["content"])
                    variants.append({k: v for k, v in entry.items() if k != "role"})
                index.append({"name": name, "source": record["source"], "parsed": parsed, "variants": variants})
        files["shaders.json"] = depot.add(contract.encode(index), "json")
        return Output(
            [
                contract.artifact(
                    contract.artifact_id(task.id, path), content, contract.provenance(task), {"kind": self.name}
                )
                for path, content in sorted(files.items())
            ]
        )
