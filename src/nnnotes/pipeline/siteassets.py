"""Site asset tasks: JSON projection and minification, splitting, and stored encodings.

Each subject is an input content id and extension. The result holds the decoded content, its encoded form when
smaller, and an entry document. A layout places these artifact bytes without decoding or recompressing them.
"""

from __future__ import annotations

import json
import re
import zlib
from importlib import metadata

from ..core import jsonio
from ..formats import compression
from . import contract
from .contract import IncompatibleTask, Input
from .stages import Output, Stage

ASSET = "site.asset"
SPLIT_MIN_BYTES = 512 * 1024
_SUBJECT = re.compile(r"([0-9a-f]{64})\.([a-z0-9]+)")
_SPLIT_KEY = re.compile(r"[A-Za-z0-9_$.\-]+")


def subject(inp: Input, extension: str) -> str:
    value = f"{inp.sha256}.{extension}"
    if not _SUBJECT.fullmatch(value):
        raise ValueError(f"invalid site asset extension: {extension!r}")
    return value


def _extension(value: str) -> str:
    match = _SUBJECT.fullmatch(value)
    if not match:
        raise ValueError(f"invalid site asset subject: {value!r}")
    return match[2]


def _json(value) -> bytes:
    return jsonio.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def atoms(extension: str, encoding: str) -> dict:
    used = {"json.minify": "nnnotes-json-minify/1"} if extension == "json" else {}
    if extension in compression.COMPRESSIBLE:
        if encoding == "gzip":
            used["asset.gzip"] = f"nnnotes-gzip/1:zlib/{zlib.ZLIB_RUNTIME_VERSION}"
        elif encoding == "br":
            used["asset.brotli"] = f"nnnotes-brotli/1:brotli/{metadata.version('brotli')}"
    return used


class AssetStage(Stage):
    """One source file transformed for the site, shared across logical paths and products."""

    name, version = ASSET, 1
    PARAMS = {"encoding": "gzip", "split": SPLIT_MIN_BYTES, "omit": []}

    def normalize(self, params):
        p = super().normalize(params)
        compression.check(p["encoding"])
        if type(p["split"]) is not int or p["split"] < 0:
            raise ValueError("site.asset split must be a nonnegative byte count")
        paths = p["omit"]
        if not isinstance(paths, list) or any(
            not isinstance(parts, list) or not parts or any(not isinstance(k, str) or not k for k in parts)
            for parts in paths
        ):
            raise ValueError("site.asset omit must be an array of nonempty string paths")
        p["omit"] = [list(parts) for parts in sorted({tuple(parts) for parts in paths})]
        return p

    def subjects(self, env):
        return sorted(env.facts.get("site_assets", {}))

    def inputs(self, subject, env):
        _extension(subject)
        inp = env.fact("site_assets")[subject]
        if not subject.startswith(inp.sha256 + "."):
            raise ValueError("site asset subject differs from its input content id")
        return [Input("source", inp.sha256, inp.size, inp.name, inp.locators)]

    def uses(self, subject, env):
        return atoms(_extension(subject), env.params["encoding"])

    def run(self, task, depot):
        ext = _extension(task.subject)
        if task.atoms != atoms(ext, task.params["encoding"]):
            raise IncompatibleTask("site asset encoder implementations differ from the task")
        data = depot.input_bytes(task.input("source"))
        parts = None
        if ext == "json":
            doc = json.loads(data)
            for path in task.params["omit"]:
                parent = doc
                for key in path[:-1]:
                    parent = parent.get(key) if isinstance(parent, dict) else None
                if isinstance(parent, dict):
                    parent.pop(path[-1], None)
            data = _json(doc)
            threshold = task.params["split"]
            if threshold and len(data) > threshold and isinstance(doc, dict) and len(doc) > 1:
                if all(_SPLIT_KEY.fullmatch(k) for k in doc):
                    parts = [(k, _json(v)) for k, v in doc.items()]
                    joined = b"{" + b",".join(_json(k) + b":" + v for k, v in parts) + b"}"
                    if joined != data:
                        raise ValueError("site JSON parts differ from the complete document")
        elif task.params["omit"]:
            raise ValueError("site asset omit applies to JSON only")

        artifacts, placed = [], {}

        def emit(role, body, extension, kind, **facts):
            aid = contract.artifact_id(task.id, role)
            artifacts.append(
                contract.artifact(aid, depot.add(body, extension), contract.provenance(task), {"kind": kind, **facts})
            )
            return aid

        def store(body):
            sha = contract.sha256(body)
            if sha in placed:
                return dict(placed[sha])
            path = f"assets/{sha}.{ext}"
            encoded = None
            if task.params["encoding"] != "none" and ext in compression.COMPRESSIBLE:
                candidate = compression.encode(task.params["encoding"], body)
                if len(candidate) < len(body):
                    encoded = candidate
            entry = {"asset": path, "size": len(body)}
            if encoded is None:
                emit(sha, body, ext, "site.asset", path=path)
            else:
                suffix = compression.SUFFIXES[task.params["encoding"]]
                entry.update(asset=path + suffix, stored=len(encoded))
                emit(sha, body, ext, "site.decoded")
                emit(sha + suffix, encoded, suffix[1:], "site.asset", path=entry["asset"])
            placed[sha] = entry
            return dict(entry)

        if parts is None:
            entry = store(data)
        else:
            rows = []
            for key, body in parts:
                piece = store(body)
                rows.append([key, piece["asset"], piece["size"], *([piece["stored"]] if "stored" in piece else [])])
            entry = {"size": len(data), "parts": rows}
        emit("entry.json", contract.encode(entry), "json", "site.entry")
        return Output(artifacts)


def entry(result, depot) -> dict:
    """The product file entry represented by a committed site asset task."""
    artifact = next(a for a in result["artifacts"] if a["semantics"]["kind"] == "site.entry")
    return contract.loads(depot.read(artifact["content"]["sha256"]))


def placements(result) -> list[dict]:
    """Layout entries for the task's stored bytes, with decoded content ids in their paths."""
    return [
        {"path": a["semantics"]["path"], "sha256": a["content"]["sha256"], "size": a["content"]["size"], "id": a["id"]}
        for a in result["artifacts"]
        if a["semantics"]["kind"] == "site.asset"
    ]
