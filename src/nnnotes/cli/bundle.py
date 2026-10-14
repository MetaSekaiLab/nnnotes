"""`nnnotes bundle`: copy the decrypted bundles of keys out of the depot's downloads."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..core.cache import temp_path
from ..source.session import Session
from .common import command, group, known_keys, out_arg, report


def cmd_pull(args, cfg):
    cat = Session(cfg).catalog()
    known_keys(args, cat, args.keys)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    copied: dict[Path, Path] = {}
    files = {}
    for key in args.keys:
        for p in (paths := cat.fetch_key(key)):
            if p not in copied:
                copied[p] = _copy(p, out / p.name)
        files[key] = [str(copied[p]) for p in paths]
    report(args, {"keys": files}, lambda r: [p for ps in r["keys"].values() for p in ps])


def _copy(src: Path, dst: Path) -> Path:
    tmp = temp_path(dst)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dst


def register(sub) -> None:
    bs = group(sub, "bundle", "the bundles of the catalog")
    c = command(
        bs,
        "pull",
        cmd_pull,
        "fetch the bundle closure of keys and copy the decrypted bundles to a directory (prints the bundle files)",
    )
    c.add_argument("keys", nargs="+", metavar="KEY", help="addressable keys (`catalog keys` lists them)")
    out_arg(c, "directory the bundles are copied to, under their file names")
