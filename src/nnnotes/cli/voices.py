"""`nnnotes voices`: list, search, summarize and fetch the character voices of the voices view (pipeline.voices):
from an export's views/voices.json (--from), else from the document of the configured master data and catalog
(content only; the episodes named by --episode or a row id are read from the catalog)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ..pipeline.voices import STATUSES
from ..source.languages import LANGUAGES
from ..source.session import Session
from .common import AUDIO_CHOICES, command, flac_level_arg, flac_options, group, note, out_arg, print_json, write


def _label(doc: dict, ids, language: str) -> str:
    names = {c["id"]: c for c in doc["characters"]}
    out = []
    for i in ids:
        c = names.get(i)
        n = ((c or {}).get("names", {}).get("name") or {}).get(language) if c else None
        out.append(n or (c or {}).get("token") or str(i))
    return ",".join(out) or "-"


def _line(doc: dict, r: dict, language: str) -> str:
    chars = r["characters"] or r.get("inferred", {}).get("characters", [])
    text = ((r.get("text") or {}).get("texts") or {}).get(language) or ""
    cat = r["category"].get("name") or r.get("inferred", {}).get("situation") or "-"
    where = f"{r['sound'].get('sheet')}/{r['sound'].get('cue')}"
    who = _label(doc, chars, language)
    if "speaker" in r:
        names = [(t.get("texts") or {}).get(language) for t in r["speaker"]["texts"]]
        who = ",".join(n for n in names if n) or r["speaker"]["name"] or who
    return "\t".join([r["id"], who, f"{r['source']['name']}.{cat}", where, r["status"], text.replace("\n", " ")]) + "\n"


def _document(args, cfg, episodes=()) -> tuple[dict, Path | None]:
    """The document of --from and the export directory it is in, else the index of the configured master data and
    catalog with the story episodes `episodes` (ids or assets) read from the catalog."""
    from ..pipeline import contract, views, voices

    if args.source_dir:
        p = Path(args.source_dir)
        f = p if p.is_file() else p / views.view_path(voices.VIEW, views.MAIN_SUBJECT)
        if not f.is_file():
            args.usage(
                f"--from {p}: no {views.view_path(voices.VIEW, views.MAIN_SUBJECT)} "
                "(export assets --views voices writes it)"
            )
        doc = contract.loads(f.read_bytes())
        if doc.get("schema") != voices.SCHEMA:
            args.usage(f"{f}: not a voices document")
        return doc, (f.parent.parent if not p.is_file() else None)
    return voices.index_document(Session(cfg), episodes), None


def _language(args, cfg) -> str:
    lang = cfg.get("catalog", "language") or "ja"
    if lang not in LANGUAGES:
        args.usage(f"--language {lang}: one of {', '.join(LANGUAGES)}")
    return lang


def _filters(args, doc: dict) -> dict:
    from ..pipeline import voices

    chars = None
    if args.character:
        chars = voices.character_ids(doc, args.character)
        if not chars:
            args.usage(f"--character {args.character}: no character of the master data has this id or name")
    eps = None
    if args.episode:
        eps = voices.episode_ids(doc, args.episode)
        if not eps:
            args.usage(f"--episode {args.episode}: no episode of the master data has this id or asset")
    return {
        "characters": chars,
        "category": args.category,
        "source": args.source,
        "status": args.status,
        "episodes": eps,
    }


def _emit(args, doc: dict, rows: list[dict], language: str) -> None:
    rows = rows[: args.limit] if args.limit else rows
    if args.json:
        print_json(rows)
        return
    write("".join(_line(doc, r, language) for r in rows))


def _episode_args(args) -> tuple:
    return (args.episode,) if args.episode else ()


def cmd_list(args, cfg) -> None:
    from ..pipeline import voices

    doc, _ = _document(args, cfg, _episode_args(args))
    lang = _language(args, cfg)
    _emit(args, doc, voices.select(doc, **_filters(args, doc)), lang)


def cmd_search(args, cfg) -> None:
    from ..pipeline import voices

    doc, _ = _document(args, cfg, _episode_args(args))
    lang = _language(args, cfg)
    rows = voices.select(doc, **_filters(args, doc), text=args.text, language=args.search_language)
    _emit(args, doc, rows, lang)


def _summary_lines(s: dict) -> str:
    out = [f"rows {s['rows']}  gaps {s['gaps']}  " + "  ".join(f"{k} {v}" for k, v in s["statuses"].items())]
    out += [f"  {k}\t{v['rows']}\t{v['ok']} ok" for k, v in s["categories"].items()]
    rev = s["reverse"]
    out.append(
        f"reverse: {rev['keys']} keys, {sum(rev['unreferencedKeys'].values())} named by no row; {rev['streams']} "
        f"decoded streams, {sum(rev['unreferencedStreams'].values())} reached by no row"
    )
    if "story" in s:
        st = s["story"]
        out.append(
            f"story: {st['episodes']} episodes ("
            + ", ".join(f"{k} {v}" for k, v in st["statuses"].items())
            + f"); {st['sharedCues']['rows']} voices share a cue with another source"
        )
        out += [f"  {k}\t{v['episodes']} episodes\t{v['voices']} voices" for k, v in st["kinds"].items()]
    return "".join(f"{line}\n" for line in out)


def cmd_summary(args, cfg) -> None:
    from ..pipeline import voices

    doc, _ = _document(args, cfg)
    s = voices.summary(doc)
    if args.json:
        print_json(s)
    else:
        write(_summary_lines(s))


def cmd_get(args, cfg) -> None:
    from ..core.jsonio import dumps
    from ..formats.encoders import FLAC_LEVEL
    from ..pipeline import voices

    flac = flac_options(args)
    head = f"{voices.load_rules()[voices.VIEW].get('story', {}).get('table')}:"
    story = (args.id[len(head) :].split(":", 1)[0],) if args.id.startswith(head) else ()
    doc, base = _document(args, cfg, story)
    rows = voices.rows_for(doc, args.id)
    if not rows:
        args.usage(f"{args.id}: no voice row or sound id of that number")
    r = next((x for x in rows if x["status"] == "ok"), rows[0])
    sheet, cue = r["sound"].get("sheet"), r["sound"].get("cue")
    dst = Path(args.out)
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    streams = (r.get("audio") or {}).get("streams", [])
    exported = base is not None and args.format == "flac" and not flac
    if exported and streams and all(s.get("path") for s in streams):
        for s in streams:
            written.append(dst / s["file"])
            shutil.copyfile(base / s["path"], written[-1])
    elif sheet is None or cue is None or r["status"] in ("no-value", "missing-key", "missing-cue", "unsupported"):
        note(f"nnnotes: {r['id']}: {r['status']}" + (f" ({r['detail']})" if r.get("detail") else ""))
        sys.exit(1)
    else:
        cfg.require_path("paths", "apk")  # the HCA keycode is read from the APK
        cat = Session(cfg).catalog()
        try:
            written = voices.decode_cue(cat, sheet, cue, args.format, flac.get("flac_level", FLAC_LEVEL), dst)
        except LookupError as e:
            note(f"nnnotes: {r['id']}: {e}")
            sys.exit(1)
    (dst / "voice.json").write_text(
        dumps({"rows": rows, "files": [p.name for p in written]}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if args.json:
        print_json({"rows": [x["id"] for x in rows], "files": [str(p) for p in written]})
    else:
        write("".join(f"{p}\n" for p in written))
    if not written:
        sys.exit(1)


def register(sub) -> None:
    vs = group(
        sub, "voices", "character voices of the master data and the story episodes: list, search, summary, get one"
    )

    def base(p, text=True):
        p.add_argument(
            "--from",
            dest="source_dir",
            metavar="OUT",
            help="an export directory with views/voices.json (export assets --views voices); else the index is "
            "built from the configured master data and catalog (no files)",
        )
        if text:
            p.add_argument("--character", help="a MasterCharacter id or a part of a name in any language")
            p.add_argument(
                "--category", help="a category (CharacterRankUp or character-rank-up), Source.Category or a source"
            )
            p.add_argument("--source", help="a source (Talk, CharacterVoice, ..., HomeSpot, Title, Sound, Story)")
            p.add_argument(
                "--episode",
                metavar="ADV",
                help="the Story voices of one episode: a MasterAdv id or an "
                "episode asset (without --from: read from the catalog)",
            )
            p.add_argument("--status", choices=STATUSES, help="only the rows with this status")
            p.add_argument("--limit", type=int, default=0, help="at most N rows (0: all)")

    p = command(
        vs,
        "list",
        cmd_list,
        "the voices matching the filters (the text column in the --language language, default [catalog] language)",
    )
    base(p)
    p = command(vs, "search", cmd_search, "the voices whose text (or cue name) contains TEXT")
    p.add_argument("text", help="text to look for")
    base(p)
    p.add_argument("--search-language", choices=tuple(LANGUAGES), help="search this language only (default: all five)")
    p = command(vs, "summary", cmd_summary, "rows per status, category and character; gaps both ways")
    base(p, text=False)
    p = command(
        vs, "get", cmd_get, "the files of one voice: a row id (MasterTalk:561, MasterAdv:<id>:<command>) or a sound id"
    )
    p.add_argument("id", help="row id or sound id")
    out_arg(p, "output directory")
    base(p, text=False)
    p.add_argument(
        "--format",
        choices=AUDIO_CHOICES,
        default="flac",
        help="flac (default: the exported files when --from has them), ogg or wav (decodes the sheet)",
    )
    flac_level_arg(p, "of --format flac: decode the sheet at this level, also with --from")
