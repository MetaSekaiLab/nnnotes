"""Read-set tasks keyed by player source files, product files and the Node implementation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..source.config import tool
from . import contract
from .contract import Cost, IncompatibleTask, Input
from .stages import Output, Stage

READ_SET = "site.readset"


def relative(path: str) -> str:
    if (
        not isinstance(path, str)
        or re.search(r"[\\:?#\x00]", path)
        or any(p in {"", ".", ".."} for p in path.split("/"))
    ):
        raise ValueError(f"read-set path must be relative: {path!r}")
    return path


def node_identity(executable: str) -> str:
    code = "JSON.stringify([process.version,process.versions.v8,process.platform,process.arch])"
    result = subprocess.run([executable, "-p", code], check=True, capture_output=True, text=True, timeout=15)
    return "node:" + json.dumps(json.loads(result.stdout), separators=(",", ":"))


def request(entry: str, *, mode="plan", settings=None, keys=False, variants=()) -> dict:
    relative(entry)
    if mode not in {"plan", "full"}:
        raise ValueError("read-set mode must be plan or full")
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("read-set settings must be an object")
    return {
        "entry": entry,
        "mode": mode,
        "settings": settings or {},
        "keys": bool(keys),
        "variants": sorted({relative(p) for p in variants}),
    }


def player_inputs(depot, directory) -> list[Input]:
    """The player's source tree and read-set script, frozen into the depot."""
    root = Path(directory)
    files = {root / "package.json", root / "scripts" / "read-set.mjs"}
    for name in ["src", "scripts/lib"]:
        files.update(p for p in (root / name).rglob("*") if p.is_file())
    out = []
    for path in sorted(files):
        data = path.read_bytes()
        role = "player:" + relative(path.relative_to(root).as_posix())
        out.append(Input(role, depot.put(data), len(data), None, ({"kind": "depot"},)))
    return out


def data_inputs(entries) -> list[Input]:
    by_path = {}
    for entry in entries:
        path = relative(entry["path"])
        inp = Input("data:" + path, entry["sha256"], entry["size"], None, ({"kind": "depot"},))
        if path in by_path and by_path[path] != inp:
            raise ValueError(f"read-set file has two contents: {path}")
        by_path[path] = inp
    return [by_path[path] for path in sorted(by_path)]


class ReadSetError(RuntimeError):
    """The player did not return a complete read set for the requested mode."""


class ReadSetStage(Stage):
    name, version = READ_SET, 1

    def subjects(self, env):
        return sorted(env.facts.get(self.name, {}))

    def inputs(self, subject, env):
        return env.fact(self.name)[subject]

    def uses(self, subject, env):
        return {"player.node": node_identity(tool("node", "node"))}

    def estimate(self, subject, env, inputs):
        return Cost(1, max(256 << 20, sum(i.size for i in inputs) * 3))

    def run(self, task, depot):
        executable = tool("node", "node")
        if task.atoms != {"player.node": node_identity(executable)}:
            raise IncompatibleTask("read-set Node implementation differs from the task")
        spec = json.loads(depot.input_bytes(task.input("request")))
        spec = request(**spec)
        work = depot.root / "tmp"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="readset-", dir=work) as directory:
            base = Path(directory)
            seen = set()
            for inp in task.inputs:
                if inp.role == "request":
                    continue
                prefix, _, path = inp.role.partition(":")
                if prefix not in {"player", "data"}:
                    raise ValueError(f"unknown read-set input role: {inp.role}")
                path = relative(path)
                key = (prefix + "/" + path).casefold()
                if key in seen:
                    raise ValueError(f"read-set paths differ only by case: {path}")
                seen.add(key)
                dst = base / prefix / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                src = depot.open_input(inp)
                if os.name == "nt":
                    shutil.copyfile(src, dst)
                else:
                    try:
                        os.link(src, dst)
                    except OSError:
                        shutil.copyfile(src, dst)
            args = [executable, str(base / "player/scripts/read-set.mjs"), str(base / "data" / spec["entry"])]
            if spec["mode"] == "plan":
                args.append("--plan")
            if spec["keys"]:
                args.append("--keys")
            if spec["settings"]:
                args.append("--settings=" + json.dumps(spec["settings"], separators=(",", ":")))
            if spec["variants"]:
                args.append("--variants=" + json.dumps(spec["variants"], separators=(",", ":")))
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=1200)
            if result.returncode:
                error = (result.stderr or result.stdout).strip().replace(str(base), "<input>")
                raise ReadSetError(f"{spec['mode']} read set failed: {error[-8000:]}")
            try:
                data = json.loads(result.stdout)
            except ValueError as e:
                raise ReadSetError("player returned an invalid read-set document") from e
            expected = "planned" if spec["mode"] == "plan" else "ended"
            if data.get("mode") != spec["mode"] or data.get("state") != expected:
                raise ReadSetError(f"read set did not reach {expected}")
            files = data.get("files")
            if not isinstance(files, list) or any(not isinstance(p, str) or not p for p in files):
                raise ReadSetError("read set has no file list")
            if data.get("read") != len(set(files)):
                raise ReadSetError("read-set file count differs from its list")
            for p in files:
                if not re.fullmatch(r"[^/\\]+:-?[0-9]+#.+", p):
                    relative(p)
            for name in ["frames", "readAfterLoad", "read"]:
                if type(data.get(name)) is not int or data[name] < 0:
                    raise ReadSetError(f"read-set {name} must be a nonnegative integer")
            # Keep only deterministic player results. Execution paths and durations belong to run logs.
            out = {
                "mode": spec["mode"],
                "state": expected,
                "frames": data["frames"],
                "readAfterLoad": data["readAfterLoad"],
                "read": data["read"],
                "files": sorted(set(files)),
            }
            if spec["settings"]:
                out["settings"] = spec["settings"]
            if spec["keys"]:
                if not isinstance(data.get("keys"), list) or any(not isinstance(p, str) for p in data["keys"]):
                    raise ReadSetError("read set has no key list")
                out["keys"] = sorted(set(data["keys"]))
        art = contract.artifact(
            contract.artifact_id(task.id, "readset.json"),
            depot.add(contract.encode(out), "json"),
            contract.provenance(task),
            {"kind": "site.readset"},
        )
        return Output([art])
