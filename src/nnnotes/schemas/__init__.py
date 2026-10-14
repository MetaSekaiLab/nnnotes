"""The JSON Schemas (draft 2020-12) of the documents nnnotes writes, shipped with the package: `<name>.schema.json`,
each with the `$id` `urn:nnnotes:schema:<name>` that the others reference."""

import json
from importlib import resources

SUFFIX = ".schema.json"


def names() -> list[str]:
    """The schema names (`music-data`, `plan`, ...), sorted."""
    return sorted(p.name[: -len(SUFFIX)] for p in resources.files(__package__).iterdir() if p.name.endswith(SUFFIX))


def load(name: str) -> dict:
    """The schema `name` (`music-data` for music-data.schema.json)."""
    path = resources.files(__package__) / f"{name}{SUFFIX}"
    if not path.is_file():
        raise KeyError(f"no schema {name!r} (schemas: {', '.join(names())})")
    return json.loads(path.read_text(encoding="utf-8"))
