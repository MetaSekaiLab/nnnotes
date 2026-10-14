"""`nnnotes music-data`: every live song and chart -> one JSON file (musicdata)."""

from __future__ import annotations

import sys
from pathlib import Path

from ..source.session import Session
from .common import command, out_arg, report


def cmd_music_data(args, cfg):
    from .. import musicdata
    from ..musicdata import deckdata

    if args.apk_master:
        cfg.require_path("paths", "apk")  # the master data files ship in the APK
    session = Session(cfg)
    apk = session.path("paths", "apk")
    try:
        deck = (
            None
            if args.no_deck
            else musicdata.Deck(
                seeds=args.seeds, workers=args.workers, aptitude=not args.no_gekisou_aptitude, cache=args.stats_cache
            )
        )
        if args.apk_master:
            src, region = deckdata.apk_master(apk), deckdata.EMBEDDED
        elif args.decoded_master:  # decoded elsewhere: no master key
            src, region = deckdata.decoded_master(session.master_dir()), cfg.region()
        else:
            src, region = deckdata.master_files(Path(args.master_files)), cfg.region()
        key = None if src.decoded else session.master_key()
        cat = session.catalog()
        r = musicdata.export(
            Path(args.out),
            src,
            key,
            deckdata.catalog_fetch(cat),
            None if args.no_bgm else musicdata.catalog_bgm(cat),
            region=region,
            client=deckdata.apk_client(apk) if apk is not None else {},
            catalog=deckdata.catalog_info(cat, session.store_root()),
            deck=deck,
            full=args.full,
            jacket=musicdata.catalog_jacket(cat) if args.jackets else None,
            jackets_dir=args.jackets,
            replay_dir=args.replay_dir,
            replay_engine=args.replay_engine,
            recommend_engine=args.recommend_engine,
        )
    except (deckdata.DeckDataError, musicdata.MusicDataError) as e:
        sys.exit(f"nnnotes: {e}")
    report(args, r)


def register(sub) -> None:
    c = command(
        sub,
        "music-data",
        cmd_music_data,
        "every live song and chart: metadata in every language, chart facts and the deck model's chart "
        "statistics -> one JSON file",
    )
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--master-files",
        metavar="DIR",
        help="master data files as served: MasterManifest.json and the .bin files it lists (`master download`)",
    )
    g.add_argument("--apk-master", action="store_true", help="the master data files of base.apk ([paths] apk)")
    g.add_argument(
        "--decoded-master",
        action="store_true",
        help="decoded master data ([paths] master or --master) with the MasterManifest.json of the files "
        "it was decoded from; no master key",
    )
    c.add_argument(
        "--full",
        action="store_true",
        help="also write the deck model's input: every chart's runtime notes and the master data tables "
        "about cards, skills, bonuses, scores and events",
    )
    c.add_argument("--no-deck", action="store_true", help="do not run the deck model (every chart's deck is null)")
    c.add_argument(
        "--seeds",
        type=int,
        default=8,
        metavar="N",
        help="replay seeds for a chart with a luck range (default 8); expectations do not use sampling",
    )
    c.add_argument(
        "--workers", type=int, metavar="N", help="threads measuring charts (default: every processor; 0: one thread)"
    )
    c.add_argument(
        "--no-gekisou-aptitude",
        action="store_true",
        help="leave out the charts' Gekisou aptitude (every gekisouAptitude is null)",
    )
    c.add_argument(
        "--stats-cache",
        type=Path,
        metavar="DIR",
        help="reuse native simulation programs and runs in DIR across builds; "
        "completed cache entries survive interrupted builds",
    )
    c.add_argument(
        "--no-bgm", action="store_true", help="do not read the BGM cue sheets (every song's bgm.length is null)"
    )
    c.add_argument(
        "--replay-dir",
        metavar="DIR",
        help="write canonical runtime DeckData, per-chart inputs and replay manifest under the output directory",
    )
    c.add_argument(
        "--replay-engine",
        metavar="PKG",
        help="the deck model's replay WASM release package (ournotes-replay-wasm-vVERSION.tar.gz or its "
        "directory); its web JS/WASM and build-info.json are copied into --replay-dir",
    )
    c.add_argument(
        "--recommend-engine",
        metavar="PKG",
        help="the deck model's recommendation WASM release package (ournotes-recommend-wasm-vVERSION.tar.gz "
        "or its directory); its web JS/WASM and build-info.json are copied into --replay-dir",
    )
    c.add_argument(
        "--jackets",
        metavar="DIR",
        help="also write every song's jacket as DIR/<jacket>.webp (at most 320 px on the longer side)",
    )
    out_arg(c, "output file (.json, or .json.gz for gzip)")
