"""Versioned local song projects and target resource sets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .. import schemas
from ..source import addressables, master
from ..source.config import Config
from ..source.session import Session

__all__ = ["SongProject", "Target", "fingerprint"]


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document(path: Path, format: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format") != format:
        raise ValueError(f"expected {format} document")
    schema = "song-package" if format == "nnnotes.song-package/1" else "pack-target"
    errors = sorted(Draft202012Validator(schemas.load(schema)).iter_errors(value), key=lambda error: str(error.path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.path)
        raise ValueError(f"{pointer}: {error.message}")
    return value


@dataclass
class SongProject:
    """A song manifest. All relative paths resolve against the manifest's directory."""

    path: Path
    document: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> SongProject:
        path = Path(path).resolve()
        return cls(path, _document(path, "nnnotes.song-package/1"))

    def file(self, value: str) -> Path:
        return (self.path.parent / value).resolve()


@dataclass
class Target:
    """An offline catalog, master set, local bundle directory and audio compatibility profile."""

    path: Path
    document: dict[str, Any]
    session: Session = field(repr=False)
    inputs: dict[Path, str] = field(default_factory=dict, repr=False)
    _audio_key: int | None = field(default=None, repr=False)

    @classmethod
    def load(cls, path: str | Path, config: Config | None = None) -> Target:
        path = Path(path).resolve()
        result = cls(path, _document(path, "nnnotes.pack-target/1"), Session(config or Config.load()))
        result.read(path)
        return result

    def file(self, value: str) -> Path:
        return (self.path.parent / value).resolve()

    def read(self, path: Path) -> bytes:
        path = path.resolve()
        data = path.read_bytes()
        digest = fingerprint(data)
        if path in self.inputs and self.inputs[path] != digest:
            raise ValueError(f"build input changed: {path.name}")
        self.inputs[path] = digest
        return data

    def catalog(self) -> bytes:
        return self.read(self.file(self.document["catalog"]))

    def master_files(self) -> dict[str, bytes]:
        root = self.file(self.document["master"])
        files = {}
        for path in sorted(root.glob("Master*.bin")):
            files[path.name] = self.read(path)
        if not files:
            raise ValueError("target master directory must contain encoded Master*.bin files")
        manifest = root / "MasterManifest.json"
        if not manifest.is_file():
            raise ValueError("target master directory requires MasterManifest.json")
        doc = json.loads(self.read(manifest))
        for item in doc["files"]:
            name = item["name"]
            if name != Path(name).name or name not in files:
                raise ValueError("master manifest names a missing or invalid table file")
            if len(files[name]) != item["size"] or fingerprint(files[name]) != item["hash"]:
                raise ValueError(f"target master integrity mismatch: {name}")
        if {item["name"] for item in doc["files"]} != set(files):
            raise ValueError("target master manifest must cover every table")
        return files

    def tables(self, names: tuple[str, ...] | None = None) -> dict[str, dict]:
        key = self.session.master_key()
        return {
            name[:-4]: json.loads(master.decode(data, key))
            for name, data in self.master_files().items()
            if names is None or name[:-4] in names
        }

    def bundle(self, location: dict) -> bytes:
        name = location["internal_id"].rsplit("/", 1)[-1]
        if name != Path(name).name or name in ("", ".", ".."):
            raise ValueError("bundle internal ID must end with a file name")
        root = self.file(self.document["bundles"])
        data = self.read(root / name)
        if not data.startswith(b"UnityFS\0"):
            data = addressables.decrypt(data, name, self.session.bundle_key())
        if not data.startswith(b"UnityFS\0"):
            raise ValueError(f"local template is not a readable UnityFS bundle: {name}")
        return data

    def transport(self, provider: str) -> str:
        from ..source.catalogbuild import BUNDLE_PROVIDER

        value = self.document.get("providers", {}).get(provider, "plain" if provider == BUNDLE_PROVIDER else None)
        if value not in ("plain", "encrypted"):
            raise ValueError("target providers must explicitly map this bundle provider to plain or encrypted")
        return value

    def audio(self, kind: str) -> tuple[bytes, int, int | None]:
        from ..formats import crikey

        profile = self.document["audio"]
        cipher = profile["cipher_type"]
        if cipher not in (0, 56):
            raise ValueError("audio cipher_type must be 0 or 56")
        key = None
        if cipher == 56:
            apk = self.session.required_path("paths", "apk")
            if self._audio_key is None:
                if apk.is_file():
                    self.read(apk)
                self._audio_key = crikey.find_key(apk)
            key = self._audio_key
            if not key:
                raise ValueError("target APK has no enabled HCA key")
        return self.read(self.file(profile["templates"][kind])), cipher, key

    def check_inputs(self) -> None:
        for path, expected in self.inputs.items():
            if fingerprint(path.read_bytes()) != expected:
                raise ValueError(f"build input changed: {path.name}")
