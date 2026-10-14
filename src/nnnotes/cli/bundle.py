"""`nnnotes bundle`: fetch bundles into the depot's downloads."""

from __future__ import annotations

from ..source.session import Session
from .common import command, group, known_keys, report


def cmd_pull(args, cfg):
    cat = Session(cfg).catalog()
    known_keys(args, cat, args.keys)
    files = {key: [str(p) for p in cat.fetch_key(key)] for key in args.keys}
    report(args, {"keys": files}, lambda r: [p for ps in r["keys"].values() for p in ps])


def register(sub) -> None:
    bs = group(sub, "bundle", "the bundles of the catalog")
    c = command(
        bs, "pull", cmd_pull, "fetch the bundle closure of keys into the depot (prints the downloaded bundle files)"
    )
    c.add_argument("keys", nargs="+", metavar="KEY", help="addressable keys (`catalog keys` lists them)")
