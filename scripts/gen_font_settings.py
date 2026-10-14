"""Regenerate the font settings table the open-font exports read (maintainer tool; not installed, not needed to use
nnnotes).

With `--fonts open` (the default) the story, start canvas and talk window exports give each localized game font
asset an open font asset with that asset's point size, padding, render mode, style settings, line metrics, fallback
chain and text materials. nnnotes ships these values in `src/nnnotes/fontsettings.json` (fontsettings.FORMAT), so
those exports read no game font asset; this script reads them from the game's font assets and writes the file.

Supporting a new game version
-----------------------------
When the game changes its font assets or their materials (an open-font export stops with "... not in the font
settings", or the values differ from the game's), regenerate the table with a configured catalog and APK of that
version (`nnnotes config check`):

    python scripts/gen_font_settings.py [--region REGION] [--game-version VERSION] [-o PATH]

It reads LocalizeManager's font names of every language and their material types from the player data, each of
those font assets with the assets of its fallback chain, their default materials and every localized text material
("<font> - <type>") the catalog has, and writes the table (`--game-version` default: the APK's versionName). The
`fonts` extra must be installed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from nnnotes import fontsettings, tmpfont
from nnnotes.config import Config, use
from nnnotes.export import Exporter
from nnnotes.web import open_data


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="regenerate src/nnnotes/fontsettings.json from the game's font assets")
    p.add_argument("--region", help="region of the catalog (default: [catalog] region)")
    p.add_argument("--game-version", help="game version to record (default: the APK manifest's versionName)")
    p.add_argument("-o", "--out", type=Path, default=fontsettings.FILE, help="output file")
    args = p.parse_args(argv)
    cfg = use(Config.load())
    cat, _, player = open_data(cfg, args.region)
    with tempfile.TemporaryDirectory(prefix="fontsettings-") as work:
        ex = Exporter(cat, Path(work), player=player, textures="deferred")
        doc = tmpfont.build_font_settings(ex, player, args.game_version or player.game_version)
    fontsettings.write(doc, args.out)
    print(
        f"{args.out}: {len(doc['fonts'])} font assets, {len(doc['materials'])} materials, "
        f"{len(doc['objects'])} objects",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
