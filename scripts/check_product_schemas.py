"""Compare the shared product contracts with an ournotes-player checkout or extracted package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

NAMES = (
    "product",
    "episode",
    "model-json",
    "prefab",
    "live",
    "live-scene",
    "live-notes",
    "live-noteskin",
    "live-noteeffect",
    "live-music",
    "live-sounds",
    "story",
    "story-scene",
    "story-ui",
    "story-asset",
    "story-fonts",
    "story-host",
    "story-simple-ui",
    "ui-pack",
    "ui-library",
    "room",
    "spot",
    "story-video",
    "story-sound",
    "crilips",
)


def contract(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in {"$id", "title", "description"}:
                continue
            if key in {"properties", "patternProperties", "$defs", "dependentSchemas"}:
                out[key] = {name: contract(schema) for name, schema in item.items()}
            elif key in {"const", "enum"}:
                out[key] = item
            else:
                out[key] = contract(item)
        return out
    if isinstance(value, list):
        return [contract(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player", type=Path, help="ournotes-player root containing schema/")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "src" / "nnnotes" / "schemas"
    errors = []
    for name in NAMES:
        paths = [source / f"{name}.schema.json", args.player / "schema" / f"{name}.schema.json"]
        try:
            left, right = [contract(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
            if left != right:
                errors.append(f"{name}: schema constraints differ")
        except (OSError, ValueError) as error:
            errors.append(f"{name}: {error}")
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    print(f"{len(NAMES)} shared product schemas match")


if __name__ == "__main__":
    main()
