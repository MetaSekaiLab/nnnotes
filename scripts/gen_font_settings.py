"""Generate a font settings table from the inputs of a saved text-product task.

Usage: python scripts/gen_font_settings.py TASK.json --depot DEPOT [-o OUTPUT]

The task's object table, font-bundle identities and APK manifest are read through the depot. The table contains
the font assets LocalizeManager names, their fallback chains, materials and object ids. Text products with these
inputs include story.fonts and live.ui. The fonts extra must be installed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nnnotes.content import storyui
from nnnotes.content.prefabs import mono_json
from nnnotes.pipeline import contract, textfonts
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import open_depot
from nnnotes.source.config import Config, use
from nnnotes.unity import fontsettings, textstyle


def generate(task, depot) -> dict:
    """Font settings from the complete stored inputs of a text product task."""
    objects = Objects(contract.loads(depot.input_bytes(task.input("objects"))), depot)
    localize = storyui.script_root(objects, "LocalizeManager")
    language = textstyle.language_fonts_json(mono_json(objects, localize))
    return textfonts.font_settings(task, objects, language).doc


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="generate font settings from a saved text-product task")
    parser.add_argument("task", type=Path, help="nnnotes.task/1 JSON with font inputs")
    parser.add_argument("--depot", type=Path, help="depot holding the task inputs ([paths] depot)")
    parser.add_argument("-o", "--out", type=Path, default=fontsettings.FILE, help="output table")
    args = parser.parse_args(argv)
    overrides = {("paths", "depot"): str(args.depot.resolve())} if args.depot else {}
    cfg = use(Config.load(overrides=overrides))
    task = contract.Task.from_json(contract.loads(args.task.read_bytes()))
    doc = generate(task, open_depot(cfg))
    fontsettings.write(doc, args.out)
    print(
        f"{args.out}: {len(doc['fonts'])} font assets, {len(doc['materials'])} materials, "
        f"{len(doc['objects'])} objects",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
