"""`nnnotes servers`: the server list of the bootstrap API root."""

from __future__ import annotations

import sys

from .common import command, report


def _lines(r: dict) -> list[str]:
    out = []
    for s in r["servers"]:
        roots = f"{s['cdnRoots']} CDN roots, {s['apiRoots']} API roots"
        if "cdn" in s:
            roots = f"CDN {', '.join(s['cdn']) or '-'}; API {', '.join(s['api']) or '-'}"
        out.append(f"{s['name']}\t{s['displayName']}\tarea {s['areaId']}\t{s['region'] or '-'}\t{roots}")
    return out


def cmd_servers(args, cfg):
    from ..source import gameapi

    try:
        servers = gameapi.server_list(cfg)
    except gameapi.GameApiError as e:
        sys.exit(f"nnnotes: {e}")
    configured = gameapi.configured_roots(cfg)
    report(args, {"servers": [gameapi.server_summary(s, configured, args.show_hosts) for s in servers]}, _lines)


def register(sub) -> None:
    c = command(
        sub, "servers", cmd_servers, "the server list of the bootstrap API root: regions, their CDN and API roots"
    )
    c.add_argument("--show-hosts", action="store_true", help="print the CDN and API roots, not only their counts")
