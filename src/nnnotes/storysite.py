"""Story episodes -> the story part of an ournotes-player site (web.py builds the charts, webmodel.py the models of the
same site). The format is the player's docs/story-data-format.md.

    <site>/stories.json                 story index: per manifest the story facts (titles and story groups in every
                                        language, commands, languages), manifest path, sizes, regions
    <site>/stories/<advId>.json         story manifest: `files` (the common files) and `languages` (per language
                                        its own files), every path -> {asset, size} or, for a split JSON object,
                                        {parts: [[key, asset, size], ...], size} (as a chart manifest)
    <site>/stories/<region>/<advId>.json
                                        the manifest of a region whose story inputs differ from the first region's
                                        (only where its files differ)
    <site>/story/                       the player's story page and bundle (web.write_player)
    <site>/assets/<sha256>.<ext>        content-addressed files, shared with the charts and models

Per story: the story directory of story.build without its UI (audio: FLAC, and with a web format other than FLAC the
same samples encoded into it by the decode, web.WEB_AUDIO; the site stores the web format and cues.json names it
with its encoder delay), then per language the story UI (advui.extract in that language, fonts open) and its fonts
(storyfonts: generated from the language's font file, or the game's font assets with fonts="game"); an Overlay story
(MasterAdv._playbackMode 1) built with open fonts also gets its host screen (storyhost: host/ and per language
ui/simple/, the manifest's `host`). The files the
player reads are stored (shader directories: GLES3 programs only, as web.collect; cue sheets: cues.json and the files
it names), scene.json and ui/ui.json split per top-level key whatever their size; files whose bytes are equal in
every language are common, the others go to their language's group (ui/fonts.json, ui/languages.json, the host talk
window's ui/simple/fonts.json and the glyph pages always do). Stories run in parallel worker processes (catalog cache
writes serialized by a lock); a story whose
manifest exists is skipped unless `force`. Same inputs and library versions give byte-identical outputs.

Regions: as for charts, the regions are grouped by the story inputs of their master data (STORY_TABLES); the first
group writes stories/<id>.json, another group stories/<region>/<id>.json unless its files equal the shared manifest's
(then its regions join the shared one). A region offers the stories of its MasterAdv.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import shutil
import tempfile
import time
import traceback
import uuid
from pathlib import Path

from . import adv, advui, jsonio, languages, master, story, storyfonts, storyhost
from .config import Config, ConfigError, usable_cpus, use
from .web import (SPLIT_KEY, STORIES_DIR, STORIES_INDEX, WEB_AUDIO, Store, _dump, _lock_fetches, _log, _minify,
                  check_player, collect, configure_caches, entry_assets, join_parts, merge_regions, mp4_priming,
                  open_data, region_masters, region_meta, site_regions, text_asset)

STORIES_FORMAT = "ournotes.stories/1"
MANIFEST_FORMAT = "ournotes.story-manifest/1"
FONT_SOURCES = storyfonts.SOURCES
# master tables a story build reads (adv, advmedia, the story groups): a region's story inputs
STORY_TABLES = ("MasterAdv", "MasterAdvChat", "MasterCharacter", "MasterCharacterFriendship", "MasterHomeSpot",
                "MasterStoryChapter", "MasterStoryEpisode", "MasterStoryFriendshipEpisode",
                "MasterStoryHomeSpotTapTalkEpisode", "MasterStoryLiveResultEpisode", "MasterText")
# files stored split per top-level key whatever their size (their parts are shared between stories / languages)
SPLIT_ALWAYS = ("scene.json", "ui/ui.json")
# per-language files that go to the language group even when equal in every language
LANGUAGE_FILES = (f"ui/{storyfonts.FONTS_DOC}", f"ui/{storyfonts.LANGUAGE_DOC}",
                  f"ui/{storyhost.SIMPLE_DIR}/{storyfonts.FONTS_DOC}")
LANGUAGE_DIRS = (f"ui/{storyfonts.PAGES_DIR}/", f"ui/{storyhost.SIMPLE_DIR}/{storyfonts.PAGES_DIR}/")
STREAMS_DOC = "streams.json"                      # the cue sheets' every-stream list: not read by the player


# ---------------------------------------------------------------- master data
def all_stories(master_dir: Path) -> list[int]:
    """Every MasterAdv id, in id order."""
    return sorted(r["_id"] for r in master.table(Path(master_dir), "MasterAdv"))


def unknown_stories(cfg: Config, ids, regions=None) -> list[int]:
    """The story ids of `ids` (duplicates dropped) that no region of the site (site_regions(cfg, regions)) has: no
    MasterAdv row in its master data."""
    have: set = set()
    for md in dict.fromkeys(region_masters(cfg, site_regions(cfg, regions)).values()):
        try:
            have.update(all_stories(md))
        except FileNotFoundError as e:
            raise ConfigError(f"master data {md}: no {Path(e.filename).name} (a directory written by "
                              f"`nnnotes master decode`)") from None
    return [i for i in dict.fromkeys(int(i) for i in ids) if i not in have]


def story_inputs(master_dir: Path) -> str:
    """Fingerprint of a region's story inputs: SHA-256 over the STORY_TABLES files of its master dir."""
    h = hashlib.sha256()
    for t in STORY_TABLES:
        f = Path(master_dir) / f"{t}.json"
        h.update(t.encode("ascii") + b"\0" + (f.read_bytes() if f.is_file() else b"\0missing") + b"\0")
    return h.hexdigest()


def region_groups(masters: dict[str, Path]) -> list[list[str]]:
    """The regions grouped by story inputs, groups and regions in the order of `masters`."""
    groups: dict[str, list[str]] = {}
    for r, m in masters.items():
        groups.setdefault(story_inputs(m), []).append(r)
    return list(groups.values())


def story_groups(master_dir: Path) -> dict[int, list[dict]]:
    """{advId: [group]}: the rows of the story tables that name an episode (docs/story-data-format.md, story
    facts), in table order (MasterStoryEpisode, MasterStoryFriendshipEpisode, MasterStoryHomeSpotTapTalkEpisode,
    MasterStoryLiveResultEpisode, MasterHomeSpot), rows of a table by id. Names in every language with a text;
    a table the master data lacks gives no groups."""
    md = Path(master_dir)

    def table(name: str) -> list[dict]:
        try:
            return sorted(master.table(md, name), key=lambda r: r["_id"])
        except FileNotFoundError:
            return []
    texts = {r["_id"]: r for r in table("MasterText")}

    def names(text_id) -> dict:
        row = texts.get(text_id)
        return {c: t for c, t in languages.texts(row).items() if isinstance(t, str) and t} if row else {}
    chars = {r["_id"]: r for r in table("MasterCharacter")}

    def character(cid: int) -> dict:
        return {"id": cid, "names": names((chars.get(cid) or {}).get("_nameTextID"))}
    chapters = {r["_id"]: r for r in table("MasterStoryChapter")}
    friendships = {r["_id"]: r for r in table("MasterCharacterFriendship")}
    spots = {r["_id"]: r for r in table("MasterHomeSpot")}

    def spot(sid: int) -> dict:
        return {"id": sid, "names": names((spots.get(sid) or {}).get("_nameTextId"))}
    out: dict[int, list[dict]] = {}
    for r in table("MasterStoryEpisode"):
        ch = chapters.get(r["_chapterId"]) or {}
        g = {"kind": "chapter", "id": r["_id"], "episodeNumber": r["_episodeNumber"],
             "chapter": {"id": r["_chapterId"], "names": names(ch.get("_nameTextId")), "bandId": ch.get("_bandId", 0),
                         "special": bool(ch.get("_isSpecialStory"))},
             "characters": [character(r["_characterId"])] if r.get("_characterId") else []}
        out.setdefault(r["_advId"], []).append(g)
    for r in table("MasterStoryFriendshipEpisode"):
        f = friendships.get(r["_characterFriendshipId"]) or {}
        ids = [f[k] for k in ("_masterCharacterIdA", "_masterCharacterIdB") if f.get(k)]
        out.setdefault(r["_advId"], []).append({"kind": "friendship", "id": r["_id"],
                                                "episodeNumber": r["_episodeNumber"],
                                                "characters": [character(c) for c in ids]})
    for r in table("MasterStoryHomeSpotTapTalkEpisode"):
        out.setdefault(r["_advId"], []).append({"kind": "spotTalk", "id": r["_id"], "spot": spot(r["_spotId"]),
                                                "characters": [character(r["_characterId"])]
                                                if r.get("_characterId") else []})
    for r in table("MasterStoryLiveResultEpisode"):
        out.setdefault(r["_advId"], []).append({"kind": "liveResult", "id": r["_id"],
                                                "characters": [character(c) for c in r.get("_characterIds") or []]})
    for r in table("MasterHomeSpot"):
        if r.get("_advId"):
            out.setdefault(r["_advId"], []).append({"kind": "spot", "id": r["_id"], "spot": spot(r["_id"]),
                                                    "characters": []})
    return out


# ---------------------------------------------------------------- facts
def run_commands(episode: dict, scene: dict) -> list[str]:
    """The command names the player runs: the rows without IgnoreData and the player settings' initialize and
    finalize rows (Command values, adv.COMMAND), sorted."""
    names = {c["cmd"] for c in episode["commands"] if not c.get("IgnoreData")}
    ps = scene["settings"]["playerSettings"]
    for r in list(ps.get("_initializeEpisodes") or []) + list(ps.get("_finalizeEpisodes") or []):
        names.add(adv.COMMAND.get(r["Command"], f"Cmd{r['Command']}"))
    return sorted(names)


def needs_motion_sync(episode: dict) -> bool:
    """A Talk row (without IgnoreData) that maps a voice onto a character's lip sync: TargetName and VoiceIDs,
    IgnoreLipSync not set."""
    return any(c["cmd"] == "Talk" and c.get("TargetName") and c.get("VoiceIDs") and not c.get("IgnoreLipSync")
               and not c.get("IgnoreData") for c in episode["commands"])


def story_facts(episode: dict, scene: dict, groups: list[dict], story_languages: list[str], language: str) -> dict:
    """The story facts of a manifest (docs/story-data-format.md)."""
    title = episode.get("title") or {}
    row = episode["master"]
    return {"advId": episode["advId"], "asset": episode["asset"], "sheetName": row.get("_sheetName", ""),
            "playbackMode": row["_playbackMode"],
            "titles": {c: title[languages.column(c)[1:]] for c in languages.LANGUAGES
                       if isinstance(title.get(languages.column(c)[1:]), str) and title[languages.column(c)[1:]]},
            "groups": groups, "commands": run_commands(episode, scene), "commandCount": episode["commandCount"],
            "language": language, "languages": list(story_languages)}


# ---------------------------------------------------------------- one story
def font_file(job: dict, language: str) -> storyfonts.FontFile:
    """The FontFile of `language` (job fontFiles), read once per process."""
    fonts = _W.setdefault("fontFiles", {})
    path = job["fontFiles"][language]
    if path not in fonts:
        fonts[path] = storyfonts.FontFile(path)
    return fonts[path]


def build_dirs(cat, master_dir: Path, player, adv_id: int, work: Path, job: dict) -> dict:
    """The story directory (work/story, no ui/) and per language its ui/ with the fonts (work/lang/<language>/ui).
    -> {story, languages {language: dir}, episode, scene}."""
    sdir = Path(work) / "story"
    fmt = job["audioFormat"]
    opts = None
    if job["audio"] and WEB_AUDIO[fmt][1] is not None:
        opts = {"flac_level": 0, "also": (WEB_AUDIO[fmt][0], WEB_AUDIO[fmt][1])}
    story.build(cat, master_dir, player, adv_id, sdir, audio_format="flac", audio=job["audio"], fonts="open",
                ui=False, audio_options=opts)
    episode = json.loads((sdir / "episode.json").read_text(encoding="utf-8"))
    scene = json.loads((sdir / "scene.json").read_text(encoding="utf-8"))
    dirs = {}
    for lang in job["languages"]:
        ldir = Path(work) / "lang" / lang
        advui.extract(cat, player, episode, ldir, fonts="open", language=lang, master=master_dir)
        if job["fonts"] == "game":
            gdir = Path(work) / "game" / lang
            advui.extract(cat, player, episode, gdir, fonts="game", language=lang, master=master_dir)
            storyfonts.game_fonts(cat, player, gdir / "ui", ldir / "ui", lang)
            shutil.rmtree(gdir)
        else:
            storyfonts.open_fonts(cat, player, episode, ldir / "ui", lang, font_file(job, lang), master_dir)
        dirs[lang] = ldir
    return {"story": sdir, "languages": dirs, "episode": episode, "scene": scene}


def _files(root: Path, sub: str = "") -> list[str]:
    base = Path(root) / sub if sub else Path(root)
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()) if base.is_dir() else []


def collect_story(dirs: dict, audio: bool, audio_format: str) -> tuple[dict, dict]:
    """The files of a story -> (common {path: text or bytes}, {language: {path: text or bytes}}): the story
    directory (web.collect: shader directories reduced to their GLES3 programs), each cue sheet's cues.json and the
    files it names (in the web format with its encoder delay when that is not FLAC), the videos, and per language
    its ui/; ui/ files equal in every language are common (except LANGUAGE_FILES / LANGUAGE_DIRS)."""
    sdir = dirs["story"]
    rest = [p for p in _files(sdir) if not p.startswith(("audio/", "videos/", "ui/"))]
    text, binary = collect(sdir, rest)
    common: dict = {**text, **binary}
    if audio:
        ext = WEB_AUDIO[audio_format][0]
        for sheet in sorted(p.name for p in (sdir / "audio").iterdir()) if (sdir / "audio").is_dir() else []:
            d = sdir / "audio" / sheet
            cues = json.loads((d / "cues.json").read_text(encoding="utf-8"))
            for name, e in cues.items():
                f = d / (Path(e["file"]).stem + ext)
                data = f.read_bytes()
                if ext != ".flac":
                    e["file"] = f.name
                    if audio_format == "aac":
                        e["encoderDelay"] = mp4_priming(data)
                common[f"audio/{sheet}/{f.name}"] = data
            common[f"audio/{sheet}/cues.json"] = jsonio.dumps(cues, ensure_ascii=True, indent=1)
    for rel in _files(sdir, "videos"):
        p = sdir / rel
        common[rel] = p.read_text(encoding="utf-8") if rel.endswith(".json") else p.read_bytes()
    per: dict[str, dict] = {}
    for lang, ldir in dirs["languages"].items():
        t, b = collect(ldir, _files(ldir, "ui"))
        per[lang] = {**t, **b}
    groups: dict[str, dict] = {lang: {} for lang in per}
    for path in sorted({p for files in per.values() for p in files}):
        values = [files.get(path) for files in per.values()]
        own = path in LANGUAGE_FILES or path.startswith(LANGUAGE_DIRS)
        if not own and all(v is not None for v in values) and all(v == values[0] for v in values):
            if path in common:
                raise RuntimeError(f"{path}: in the story directory and in the UI")
            common[path] = values[0]
            continue
        for lang, files in per.items():
            if path in files:
                groups[lang][path] = files[path]
            elif not own:
                raise RuntimeError(f"{path}: in the UI of some languages only")
    return common, groups


def split_always(data: bytes) -> list[tuple[str, bytes]] | None:
    """A minified JSON object -> [(key, value text)] (as web.split_json, whatever the size); None when it cannot be
    split (not an object of two or more keys whose JSON text is the same in Python and JavaScript)."""
    if not data.startswith(b"{"):
        return None
    obj = json.loads(data)
    if not isinstance(obj, dict) or len(obj) < 2 or not all(SPLIT_KEY.fullmatch(k) for k in obj):
        return None
    parts = [(k, _minify(v).encode("utf-8")) for k, v in obj.items()]
    if join_parts(parts) != data:
        raise RuntimeError("split JSON does not rejoin to the stored text")
    return parts


def put_file(store: Store, path: str, data) -> dict:
    """One file into the store: text through web.text_asset (SPLIT_ALWAYS files split per top-level key, other
    JSON as web.Store.put_file), binary as is."""
    if isinstance(data, bytes):
        return store.put(path, data)
    b = text_asset(path, data)
    parts = split_always(b) if path in SPLIT_ALWAYS else None
    if parts is None:
        return store.put_file(path, b)
    return {"size": len(b), "parts": [[k, store.put(f"{path}#{k}.json", t)["asset"], len(t)] for k, t in parts]}


def manifest_file(site: Path, adv_id: int, prefix: str = "") -> Path:
    """stories/<prefix><advId>.json (`prefix`: "" or "<region>/")."""
    return Path(site) / STORIES_DIR / f"{prefix}{adv_id}.json"


def story_manifests(site: Path) -> list[Path]:
    """Every story manifest: stories/*.json, then the region manifests stories/<region>/*.json."""
    d = Path(site) / STORIES_DIR
    return [*sorted(d.glob("*.json")), *sorted(d.glob("*/*.json"))]


def manifest_assets(man: dict) -> set[str]:
    """The assets a story manifest references: its common files and every language group's."""
    used: set[str] = set()
    for files in [man["files"], *(g["files"] for g in man["languages"].values())]:
        for e in files.values():
            used.update(entry_assets(e))
    return used


def ingest(store: Store, site: Path, adv_id: int, dirs: dict, job: dict, groups: list[dict]) -> dict:
    """One story's files into the store + its manifest stories/<prefix><advId>.json, serving job `regions` (added
    to the regions of the manifest it replaces)."""
    common, per = collect_story(dirs, job["audio"], job["audioFormat"])
    episode, scene = dirs["episode"], dirs["scene"]
    facts = story_facts(episode, scene, groups, job["languages"], job["language"])
    files = {p: put_file(store, p, d) for p, d in sorted(common.items())}
    langs = {lang: {"files": {p: put_file(store, p, d) for p, d in sorted(per[lang].items())}}
             for lang in job["languages"]}
    manifest = {"format": MANIFEST_FORMAT, "advId": adv_id, "story": facts, "language": job["language"],
                "audio": bool(job["audio"]), "audioFormat": job["audioFormat"] if job["audio"] else None,
                "fonts": job["fonts"],
                "requires": {"commands": facts["commands"], "cubismCore": True,
                             "motionSync": needs_motion_sync(episode)},
                "files": files, "languages": langs}
    if dirs.get("host"):                      # Overlay stories: storyhost.build's entry (host/host.json, ui/simple/)
        manifest["host"] = dirs["host"]
    path = manifest_file(site, adv_id, job.get("prefix", ""))
    old = json.loads(path.read_text(encoding="utf-8")).get("regions") if path.is_file() else None
    if old or job.get("regions"):
        manifest["regions"] = merge_regions(old, job.get("regions") or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dump(manifest))
    sizes = {"common": sum(e["size"] for e in files.values()),
             "languages": {lang: sum(e["size"] for e in g["files"].values()) for lang, g in langs.items()}}
    return {"id": str(adv_id), "ok": True, "files": len(files) + sum(len(g["files"]) for g in langs.values()),
            "bytes": sizes["common"] + sum(sizes["languages"].values()), "size": sizes}


# ---------------------------------------------------------------- per story (in a worker or in this process)
_W: dict = {}


def _worker_init(cfg: Config, lock, job: dict) -> None:
    use(cfg)
    configure_caches(job)
    cat, master_dir, player = open_data(cfg, job.get("region"))
    if lock is not None:
        _lock_fetches(cat, lock)
    _W.update(cat=cat, master=master_dir, player=player, job=job, groups=story_groups(master_dir))


def story_task(adv_id: int, job: dict | None = None, data=None, groups: dict | None = None) -> dict:
    """One story: its directories under a new temporary directory, ingest; the directory is deleted. `data`:
    (catalog, master dir, PlayerData), `groups`: story_groups of that master dir (default: the worker's)."""
    job = job or _W["job"]
    cat, master_dir, player = data or (_W["cat"], _W["master"], _W["player"])
    groups = _W["groups"] if groups is None else groups
    site = Path(job["site"])
    work = Path(tempfile.mkdtemp(prefix=f"s{adv_id}-", dir=job["tmp"]))
    t0, stage = time.time(), "export"
    try:
        dirs = build_dirs(cat, master_dir, player, adv_id, work, job)
        if job["fonts"] == "open":           # the host talk window has open fonts only: no host data with game fonts
            stage = "host"
            dirs["host"] = storyhost.build(
                cat, master_dir, player, dirs["episode"], dirs["story"], dirs["languages"], groups.get(adv_id, []),
                fonts="open", font_files={lang: font_file(job, lang) for lang in job["languages"]})
        stage = "ingest"
        r = ingest(Store(site), site, adv_id, dirs, job, groups.get(adv_id, []))
        return {**r, "id": job.get("prefix", "") + r["id"], "seconds": round(time.time() - t0, 1)}
    except ConfigError:
        raise
    except Exception as e:
        cause = f"{type(e).__name__}: {e}"
        _log(f"{adv_id}: {stage} failed: {cause[:300]}")
        return {"id": job.get("prefix", "") + str(adv_id), "ok": False, "stage": stage, "error": cause[:2000],
                "trace": traceback.format_exc()[-3000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _pool_task(adv_id: int) -> dict:
    return story_task(adv_id)


def fold_variant(site: Path, adv_id: int, prefix: str) -> bool:
    """Drop the region manifest stories/<prefix><id>.json when its files (common and per language) equal those
    of the shared stories/<id>.json; its regions join the shared manifest. True when it was dropped."""
    var, shared = manifest_file(site, adv_id, prefix), manifest_file(site, adv_id)
    if not (prefix and var.is_file() and shared.is_file()):
        return False
    v = json.loads(var.read_text(encoding="utf-8"))
    s = json.loads(shared.read_text(encoding="utf-8"))
    if (v["files"], v["languages"]) != (s["files"], s["languages"]):
        return False
    merged = merge_regions(s.get("regions"), v.get("regions") or [])
    if merged != s.get("regions"):
        s["regions"] = merged
        shared.write_bytes(_dump(s))
    var.unlink()
    return True


def add_regions(path: Path, regions: list[str]) -> bool:
    """Add `regions` to a story manifest's regions; True when the file changed."""
    man = json.loads(Path(path).read_text(encoding="utf-8"))
    merged = merge_regions(man.get("regions"), regions)
    if merged == man.get("regions"):
        return False
    man["regions"] = merged
    Path(path).write_bytes(_dump(man))
    return True


# ---------------------------------------------------------------- index
def write_stories_index(site: Path, language: str | None = None, regions: list[dict] | None = None) -> tuple[int, set]:
    """site/stories.json from every story manifest present (no stories directory: no stories.json): entries in
    advId, manifest order; `language` (default listing language) and `regions` of this build, else those of the
    existing index. Returns the number of stories and the assets their manifests reference."""
    site = Path(site)
    index = site / STORIES_INDEX
    if not (site / STORIES_DIR).is_dir():
        index.unlink(missing_ok=True)
        return 0, set()
    old = {}
    if index.is_file():
        try:
            old = json.loads(index.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    entries, used = [], set()
    for p in story_manifests(site):
        man = json.loads(p.read_text(encoding="utf-8"))
        used |= manifest_assets(man)
        size = {"common": sum(e["size"] for e in man["files"].values()),
                "languages": {lang: sum(e["size"] for e in g["files"].values())
                              for lang, g in man["languages"].items()}}
        e = {"id": str(man["advId"]), "manifest": p.relative_to(site).as_posix(), "size": size,
             "audio": man["audio"], "audioFormat": man["audioFormat"], "fonts": man["fonts"], **man["story"]}
        if man.get("regions"):
            e["regions"] = man["regions"]
        entries.append(e)
    entries.sort(key=lambda e: (e["advId"], e["manifest"]))
    new = {r["id"]: r for r in regions or []}
    recs = [new.pop(r["id"], r) for r in old.get("regions") or [] if isinstance(r, dict) and "id" in r]
    recs += list(new.values())
    known = {c for e in entries for c in [*e.get("titles", {}), *e.get("languages", [])]}
    langs = [c for c in languages.LANGUAGES if c in known]
    meta = {"language": language or old.get("language"), "languages": langs, "regions": recs}
    doc = {"format": STORIES_FORMAT, **{k: v for k, v in meta.items() if v}, "stories": entries}
    index.write_bytes(_dump(doc))
    return len(entries), used


# ---------------------------------------------------------------- build
def font_files(cfg: Config, story_languages: list[str], flags: dict[str, str] | None = None) -> dict[str, str]:
    """{language: font file path} of the story languages: `flags` ({language: path} from --font), else
    `[paths] fonts.<language>`; a missing or unknown one is a ConfigError naming the setting."""
    flags = dict(flags or {})
    out = {}
    for lang in story_languages:
        p = Path(flags[lang]) if lang in flags else cfg.path("paths.fonts", lang)
        if p is None:
            raise ConfigError(f"the story text of {lang} needs a font file: give it as `{lang}` in the "
                              f"[paths.fonts] table of the config file (`fonts.{lang}` in [paths]), the environment "
                              f"variable NNNOTES_PATHS_FONTS_{lang.upper().replace('-', '_')} or --font {lang}=PATH")
        if not p.is_file():
            raise ConfigError(f"font file of {lang} not found (--font {lang} / paths.fonts.{lang})")
        out[lang] = str(p.resolve())
    return out


def check_languages(codes) -> list[str]:
    """The story languages `codes` (None: every language) in the order of languages.LANGUAGES, checked."""
    if not codes:
        return list(languages.LANGUAGES)
    bad = [c for c in codes if c not in languages.LANGUAGES]
    if bad:
        raise ValueError(f"story language {bad[0]}: one of {', '.join(languages.LANGUAGES)}")
    return [c for c in languages.LANGUAGES if c in set(codes)]


def _run_group(site: Path, ids: list[int], cfg: Config, job: dict, workers: int, log) -> list[dict]:
    """The stories `ids` of one region group (job region / prefix / regions) -> one result per story."""
    results = []
    if workers <= 1 or len(ids) <= 1:
        data = open_data(cfg, job["region"])
        groups = story_groups(data[1])
        _W.update(job=job)
        for i in ids:
            results.append(story_task(i, job, data=data, groups=groups))
        return results
    ctx = mp.get_context("spawn")
    with ctx.Manager() as mgr:
        with ctx.Pool(workers, initializer=_worker_init, initargs=(cfg, mgr.Lock(), job)) as pool:
            for r in pool.imap_unordered(_pool_task, ids):
                results.append(r)
                if len(results) % 20 == 0:
                    log(f"{len(results)}/{len(ids)} stories")
    return results


def build(out_dir, adv_ids, cfg: Config, player_dir, audio_format: str = "aac", audio: bool = True,
          force: bool = False, *, tmp_dir=None, log=None, workers: int | None = None, regions: list[str] | None = None,
          story_languages=None, fonts: str = "open", fonts_flags: dict[str, str] | None = None) -> dict:
    """Add the stories `adv_ids` (MasterAdv ids; None: every story of every region's master data) to the site at
    `out_dir`, with the player of the ournotes-player checkout or package at `player_dir` (its page files are
    written, the indexes rebuilt). The data comes from the settings `cfg` (each worker process opens its own).
    `audio_format`: web.WEB_AUDIO format of every waveform (`audio` False: none); `story_languages`: the language
    groups (default every language); `fonts`: "open" (glyphs from the font file of each language: `fonts_flags`
    {language: path}, else `[paths] fonts.<language>`) or "game"; `regions`: as for web.build (module docstring);
    `workers`: parallel story processes (default a quarter of the CPUs, up to 8)."""
    from .web import write_index, write_player
    from .tmpfont import require_extra
    player_dir = check_player(player_dir)
    if audio_format not in WEB_AUDIO:
        raise ValueError(f"audio format {audio_format}: one of {', '.join(WEB_AUDIO)}")
    if fonts not in FONT_SOURCES:
        raise ValueError(f"fonts {fonts!r}: one of {', '.join(FONT_SOURCES)}")
    require_extra()
    langs = check_languages(story_languages)
    base = languages.check(cfg.require("catalog", "language"))
    default = base if base in langs else langs[0]
    files = font_files(cfg, langs, fonts_flags) if fonts == "open" else {}
    regions = site_regions(cfg, regions)
    masters = region_masters(cfg, regions)
    groups = region_groups(masters)
    site = Path(out_dir).resolve()
    (site / STORIES_DIR).mkdir(parents=True, exist_ok=True)
    Store(site)
    tmp_root = Path(tmp_dir).resolve() if tmp_dir else site.parent / f"{site.name}.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    log = log or _log
    t0 = time.time()
    job0 = {"site": str(site), "tmp": str(tmp_root), "audioFormat": audio_format, "audio": bool(audio),
            "languages": langs, "language": default, "fonts": fonts, "fontFiles": files,
            "cache": str(tmp_root / "cache"), "build": uuid.uuid4().hex}
    configure_caches(job0)
    ids = None if adv_ids is None else list(dict.fromkeys(int(i) for i in adv_ids))
    results, skipped, folded, offered, used_workers = [], [], [], set(), 1
    for gi, group in enumerate(groups):
        rep = group[0]
        prefix = "" if gi == 0 else f"{rep}/"
        have = all_stories(masters[rep])
        gids = have if ids is None else [i for i in ids if i in set(have)]
        offered.update(gids)
        todo = []
        for i in gids:
            if manifest_file(site, i, prefix).exists() and not force:
                add_regions(manifest_file(site, i, prefix), group)
                skipped.append(prefix + str(i))
            else:
                todo.append(i)
        if not todo:
            continue
        w = workers if workers is not None else max(1, min(8, len(todo), usable_cpus() // 4))
        used_workers = max(used_workers, w)
        job = {**job0, "region": rep, "prefix": prefix, "regions": group}
        log(f"{len(todo)} stories{f' for {rep}' if prefix else ''}, {w} worker(s), languages {', '.join(langs)}, "
            f"fonts {fonts}, audio {audio_format if audio else 'none'}")
        results += _run_group(site, todo, cfg, job, w, log)
        if prefix:
            folded += [prefix + str(i) for i in todo if fold_variant(site, i, prefix)]
    for i in ids or []:
        if i not in offered:
            results.append({"id": str(i), "ok": False, "stage": "master",
                            "error": f"no MasterAdv row {i} in the master data of {', '.join(regions)}"})
    v = write_player(site, player_dir, stories=True)
    write_stories_index(site, base, region_meta(cfg, regions))
    idx = write_index(site)
    results.sort(key=lambda r: r["id"])
    failed = [r for r in results if not r["ok"]]
    if failed:
        (site.parent / f"{site.name}.story-failures.json").write_bytes(_dump(failed))
    return {"site": str(site),
            "storiesBuilt": [{k: r[k] for k in ("id", "files", "bytes", "size")} for r in results if r["ok"]],
            "storiesFailed": [{k: r.get(k) for k in ("id", "stage", "error")} for r in failed],
            "storiesSkipped": skipped, "storyRegionGroups": groups, "foldedStoryManifests": folded,
            "storyLanguages": langs, "storySeconds": round(time.time() - t0, 1), "storyWorkers": used_workers,
            **v, **idx}
