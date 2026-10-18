"""Player page files, bundles and region metadata for site publication."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..source.config import Config, ConfigError

READ_SET_SCRIPT = "scripts/read-set.mjs"  # node <script> <live dir> -> JSON list of the paths the player reads
PLAYER_BUNDLES = ("dist/ournotes-player.element.min.js",)
PLAYER_PAGE_DIR = "examples/chart-list"
SONGS_PAGE_DIR = "examples/songs"
SONGS_PAGE_SITE_DIR = "songs"
# module specifiers of the page that name the player's sources -> the bundle copied next to the page
PAGE_IMPORTS = {"../../src/element.js": "./ournotes-player.element.min.js"}
# the player's Live2D model page (when the player has it): its files and bundle go to SITE/<LIVE2D_PAGE_SITE_DIR>/
LIVE2D_PAGE_DIR = "examples/live2d"
LIVE2D_PAGE_SITE_DIR = "live2d"
LIVE2D_BUNDLES = ("dist/ournotes-player.live2d.element.min.js",)
LIVE2D_PAGE_IMPORTS = {"../../src/live2d/define.js": "./ournotes-player.live2d.element.min.js"}
# the player's story page (when the player has it and its bundle): its files and bundle go to
# SITE/<STORY_PAGE_SITE_DIR>/
STORY_PAGE_DIR = "examples/story-list"
STORY_PAGE_SITE_DIR = "story"
STORY_BUNDLES = ("dist/ournotes-player.story.element.min.js",)
STORY_PAGE_IMPORTS = {"../../src/story/define.js": "./ournotes-player.story.element.min.js"}
# site data written by the build (never overwritten by page files)
MODELS_DIR = "models"
MODELS_INDEX = "models.json"
STORIES_DIR = "stories"
STORIES_INDEX = "stories.json"
SITE_DATA = ("assets", "charts", MODELS_DIR, "charts.json", MODELS_INDEX, STORIES_DIR, STORIES_INDEX)
PLAYER_VERSION_MARKER = "@PLAYER_VERSION@"  # replaced in the page's files by the bundles' short hash
SITE_DATA = (*SITE_DATA, "products", "site.json", "manifest.json", ".nnnotes-layout-journal.json")


def site_regions(cfg: Config, regions=None, all_regions: bool = False) -> list[str]:
    """The regions of a build: `regions` (given order, duplicates dropped), every configured region
    (`all_regions`, config order), else the one [catalog] region. Each needs its `[servers.<region>]` table."""
    if all_regions:
        names = cfg.regions()
        if not names:
            raise ConfigError(
                "no region configured: add a [servers.<region>] table with `cdn` to the config file "
                "(or set NNNOTES_SERVERS_<REGION>_CDN)"
            )
    else:
        names = list(dict.fromkeys(regions or [])) or [cfg.region()]
    known = set(cfg.regions())
    for r in names:
        if r not in known:
            raise cfg.missing(f"servers.{r}", "cdn")
    return names


def region_masters(cfg: Config, regions: list[str]) -> dict[str, Path]:
    """{region: decoded master dir} (Session.master_dir). With more than one region the --master flag is refused and
    at most one region may use [paths] master (the others need their own `[servers.<region>] master`)."""
    from ..source.session import Session

    if len(regions) > 1 and cfg.origin("paths", "master") == "flag":
        raise ConfigError(
            "--master names one directory: give each region's master data as `master` in its "
            "[servers.<region>] table of the config file (NNNOTES_SERVERS_<REGION>_MASTER)"
        )
    shared = [r for r in regions if cfg.master(r) == ("paths", "master")]
    if len(shared) > 1:
        raise cfg.missing(f"servers.{shared[1]}", "master")
    return {r: Session(cfg).master_dir(r) for r in regions}


def region_meta(cfg: Config, regions: list[str]) -> list[dict]:
    """The index records of `regions`: id, name (`[servers.<region>] name`, else the id) and, when configured, the
    client languages (`[servers.<region>] languages`)."""
    out = []
    for r in regions:
        rec: dict = {"id": r, "name": cfg.get(f"servers.{r}", "name") or r}
        langs = cfg.get_list(f"servers.{r}", "languages")
        if langs:
            rec["languages"] = langs
        out.append(rec)
    return out


def check_player(player_dir) -> Path:
    """A compatible ournotes-player 0.2.x package with its read-set script, built bundle and page."""
    if player_dir is None:
        raise ConfigError(
            "the site needs the player: give --player <ournotes-player checkout or package> "
            "(or `player` in the [paths] table of the config file, NNNOTES_PATHS_PLAYER)"
        )
    d = Path(player_dir)
    missing = [
        rel for rel in (READ_SET_SCRIPT, *PLAYER_BUNDLES, f"{PLAYER_PAGE_DIR}/index.html") if not (d / rel).is_file()
    ]
    if missing:
        raise ConfigError(
            f"{d} is not a built ournotes-player (missing {', '.join(missing)}; a checkout needs its build first)"
        )
    try:
        package = json.loads((d / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"{d}: cannot read the player package.json; ournotes-player 0.2.x is required") from e
    if (
        not isinstance(package, dict)
        or package.get("name") != "ournotes-player"
        or not re.fullmatch(r"0\.2\.(?:0|[1-9][0-9]*)(?:\+[0-9A-Za-z.-]+)?", str(package.get("version", "")))
    ):
        raise ConfigError(f"{d}: incompatible player package; a stable ournotes-player 0.2.x is required")
    return d.resolve()


def player_bundles(player_dir: Path, rels) -> tuple[dict[str, bytes], str]:
    """The bundles `rels` of the player and their source maps ({file name: bytes}), and the bundles' short hash."""
    bundles = {}
    for rel in rels:
        bundles[Path(rel).name] = (player_dir / rel).read_bytes()
        if (player_dir / f"{rel}.map").is_file():
            bundles[Path(rel).name + ".map"] = (player_dir / f"{rel}.map").read_bytes()
    return bundles, hashlib.sha256(b"".join(bundles[Path(rel).name] for rel in rels)).hexdigest()[:16]


def page_files(page_dir: Path, imports: dict[str, str], version: str, names: str = "PAGE_IMPORTS") -> dict[str, bytes]:
    """The files of a player page directory; in its .html / .js files the module specifiers `imports` (`names`)
    point at the bundles copied next to the page and PLAYER_VERSION_MARKER is `version`."""
    page = {f.relative_to(page_dir).as_posix(): f.read_bytes() for f in sorted(page_dir.rglob("*")) if f.is_file()}
    for rel, data in page.items():
        if Path(rel).suffix not in (".html", ".js", ".mjs"):
            continue
        t = data.decode("utf-8")
        for src, dst in imports.items():
            for q in ('"', "'"):
                t = t.replace(f"{q}{src}{q}", f"{q}{dst}{q}")
        if re.search(r"""["'](?:\.\./)+src/""", t):
            raise RuntimeError(f"player page {rel} imports the player's sources beyond {names}")
        page[rel] = t.replace(PLAYER_VERSION_MARKER, version).encode("utf-8")
    return page


def gather(player_dir: Path, stories: bool = False) -> tuple[dict[str, bytes], dict]:
    """The player's page (every file of PLAYER_PAGE_DIR; in its .html / .js files the PAGE_IMPORTS specifiers point
    at the bundle and PLAYER_VERSION_MARKER is the bundles' short hash) and bundles (PLAYER_BUNDLES and their source
    maps) into the site root; when the player has the Live2D page, the same for LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS
    and LIVE2D_BUNDLES into LIVE2D_PAGE_SITE_DIR; when it has the story page and its bundles, the same for
    STORY_PAGE_DIR, STORY_PAGE_IMPORTS and STORY_BUNDLES into STORY_PAGE_SITE_DIR (`stories`: the build adds stories,
    so a story page without its bundles is a ConfigError instead of being left out). The songs page and all its
    local dependencies are copied into SITE/songs when the checkout supplies it."""
    player_dir = check_player(player_dir)
    bundles, version = player_bundles(player_dir, PLAYER_BUNDLES)
    page = page_files(player_dir / PLAYER_PAGE_DIR, PAGE_IMPORTS, version)
    files = {**page, **bundles}
    songs = {}
    if (player_dir / SONGS_PAGE_DIR / "index.html").is_file():
        songs = page_files(player_dir / SONGS_PAGE_DIR, {}, version, "SONGS_PAGE_IMPORTS")
        files.update({f"{SONGS_PAGE_SITE_DIR}/{r}": d for r, d in songs.items()})
    live2d = {}
    if (player_dir / LIVE2D_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in LIVE2D_BUNDLES if not (player_dir / rel).is_file()]
        if missing:
            raise ConfigError(
                f"{player_dir} has the Live2D page but not {', '.join(missing)} (a checkout needs its build first)"
            )
        live2d_bundles, live2d_version = player_bundles(player_dir, LIVE2D_BUNDLES)
        live2d = page_files(player_dir / LIVE2D_PAGE_DIR, LIVE2D_PAGE_IMPORTS, live2d_version, "LIVE2D_PAGE_IMPORTS")
        files.update({f"{LIVE2D_PAGE_SITE_DIR}/{r}": d for r, d in {**live2d, **live2d_bundles}.items()})
    story_page = {}
    if (player_dir / STORY_PAGE_DIR / "index.html").is_file():
        missing = [rel for rel in STORY_BUNDLES if not (player_dir / rel).is_file()]
        if missing and stories:
            raise ConfigError(
                f"{player_dir} has the story page but not {', '.join(missing)} (a checkout needs its build first)"
            )
        if not missing:
            story_bundles, story_version = player_bundles(player_dir, STORY_BUNDLES)
            story_page = page_files(
                player_dir / STORY_PAGE_DIR, STORY_PAGE_IMPORTS, story_version, "STORY_PAGE_IMPORTS"
            )
            files.update({f"{STORY_PAGE_SITE_DIR}/{r}": d for r, d in {**story_page, **story_bundles}.items()})
    for rel in files:
        if rel.split("/", 1)[0] in SITE_DATA:
            raise RuntimeError(f"player page file {rel} would overwrite the site data")
    out = {
        "playerVersion": version,
        "playerBytes": sum(map(len, bundles.values())),
        "pageFiles": len(page),
        "live2dPageFiles": len(live2d),
    }
    if story_page:
        out["storyPageFiles"] = len(story_page)
    if songs:
        out["songsPageFiles"] = len(songs)
    return files, out
