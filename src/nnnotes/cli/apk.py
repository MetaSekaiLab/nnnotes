"""`nnnotes apk`: facts of base.apk: the player's graphics settings and the HCA keycode."""

from __future__ import annotations

from pathlib import Path

from ..core.jsonio import write_json
from ..source.session import Session
from .common import command, group, out_arg, report


def cmd_settings(args, cfg):
    from ..unity.player import PlayerData

    g = PlayerData(Session(cfg).apk()).graphics()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, g)
    r = {
        "out": str(out),
        "colorSpace": g["colorSpace"],
        "qualityLevels": len(g["qualityLevels"]),
        "renderers": len(g["renderers"]),
    }
    report(
        args,
        r,
        lambda r: [f"{r['out']}  ({r['colorSpace']}, {r['qualityLevels']} quality levels, {r['renderers']} renderers)"],
    )


def cmd_hca_key(args, cfg):
    from ..formats import crikey

    cfg.require_path("paths", "apk")
    k = crikey.find_key(Session(cfg).path("paths", "apk"))
    r = {"found": bool(k), "digits": len(str(k)) if k else None, "written": None}
    if args.write and k:
        d = Path(args.write)
        d.mkdir(parents=True, exist_ok=True)
        r["written"] = str(crikey.write_hcakey(k, d))
    report(
        args,
        r,
        lambda r: [
            f"HCA keycode found ({r['digits']} digits)" if r["found"] else "no HCA keycode (decryption disabled)",
            *([r["written"]] if r["written"] else []),
        ],
    )


def register(sub) -> None:
    gs = group(sub, "apk", "facts of base.apk ([paths] apk)")
    c = command(gs, "settings", cmd_settings, "the player's graphics settings (color space, quality levels) -> JSON")
    out_arg(c, "output .json file")
    c = command(gs, "hca-key", cmd_hca_key, "find the HCA keycode of the CRI audio (never printed)")
    c.add_argument("--write", metavar="DIR", help="write it as DIR/.hcakey (the file vgmstream reads)")
