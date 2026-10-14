"""nnnotes command line: `nnnotes <group> <command>`.

Every setting (keys, the CDN base of each region, the paths of your own data and tools) comes from the config file,
the environment or the global options; see source/config.py and source/nnnotes.example.toml (`nnnotes config init`
writes it). The global options, --json among them, go before or after the command's name. A command prints a short
summary on standard output, or with --json its report as JSON (UTF-8 whatever the console encoding); notes and
errors go to standard error. Exit status 2 is a usage or settings error.

    nnnotes config init|set|unset|check|path
    nnnotes servers [--show-hosts]
    nnnotes catalog keys|browse|versions|import|fetch|diff
    nnnotes bundle pull KEY...
    nnnotes master version|download|decode
    nnnotes export assets|episode|story|live|live2d|spot|room|shader|audio|ui ... -o OUT
    nnnotes apk settings -o FILE | hca-key [--write DIR]
    nnnotes depot plan|run|verify|gc
    nnnotes voices list|search|summary|get
    nnnotes site build -o SITE
    nnnotes music-data -o FILE

Each group is a module of this package with a `register(subparsers)` function; a command imports what it runs when
it runs.
"""

from __future__ import annotations

import argparse
import os
import sys

# numpy's OpenBLAS starts one busy thread per CPU in every process that imports it, and no export uses its
# parallelism. Set when the command line loads, before a command imports numpy; the worker processes of a build
# inherit it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from .. import __version__
from ..source.config import ConfigError
from ..source.gameapi import GameApiError
from . import apk, bundle, catalog, config, depot, export, master, musicdata, servers, site, voices
from .common import GLOBALS, Formatter, fill_globals, load_config

GROUPS = (config, servers, catalog, bundle, master, export, apk, depot, voices, site, musicdata)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nnnotes", description="BanG Dream! Our Notes data toolkit", parents=[GLOBALS], formatter_class=Formatter
    )
    p.add_argument("--version", action="version", version=f"nnnotes {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")
    for g in GROUPS:
        g.register(sub)
    return p


def parse_args(argv=None) -> argparse.Namespace:
    """The arguments of a command line, every global option present (None, or False for --json, when not given)."""
    args = build_parser().parse_args(argv)
    fill_globals(args)
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        cfg = load_config(args) if args.reads_config else None
        args.func(args, cfg)
    except ConfigError as e:
        print(f"nnnotes: {e}", file=sys.stderr)
        sys.exit(2)
    except GameApiError as e:
        sys.exit(f"nnnotes: {e}")


if __name__ == "__main__":
    main()
