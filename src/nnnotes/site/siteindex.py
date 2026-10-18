"""The site's indexes: charts.json, models.json and stories.json from the manifests present (write_index), and the
assets no manifest references removed."""

from __future__ import annotations

import json
from pathlib import Path

from ..content.score import DIFFICULTIES
from ..formats import compression as compress
from ..source import languages
from .sitefiles import (
    CHARTS_INDEX,
    MODELS_DIR,
    MODELS_INDEX,
    SITE_FORMAT,
    STORIES_DIR,
    STORIES_INDEX,
    _dump,
    chart_manifests,
    entry_assets,
)

STORIES_FORMAT = "ournotes.stories/1"


def index_meta(site: Path, charts: list[dict], language: str | None = None, regions: list[dict] | None = None) -> dict:
    """The top-level keys of charts.json besides `charts`: `language` (the default listing language), `languages`
    (the languages of the entries' `titles`) and `regions` (id, name, languages; merged by id into those of the
    existing charts.json, first the ones it has); keys without a value are left out."""
    old = {}
    f = Path(site) / CHARTS_INDEX
    if f.is_file():
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    new = {r["id"]: r for r in regions or []}
    recs = [new.pop(r["id"], r) for r in old.get("regions") or [] if isinstance(r, dict) and "id" in r]
    recs += list(new.values())
    known = {c for e in charts for c in (e.get("titles") or {})}
    langs = [c for c in languages.LANGUAGES if c in known] + sorted(known - set(languages.LANGUAGES))
    meta = {"language": language or old.get("language"), "languages": langs, "regions": recs}
    return {k: v for k, v in meta.items() if v}


def write_index(site: Path, language: str | None = None, regions: list[dict] | None = None) -> dict:
    """site/charts.json from every chart manifest present (index_meta: `language`, `regions` of this build),
    site/models.json from every model manifest (siteindex.write_models_index) and site/stories.json from every story
    manifest (siteindex.write_stories_index; its `language` and `regions` are those storysite.build wrote); assets
    no chart, no model and no story (common files and every language group) references removed. The summary's
    `assetBytes` is the size of the asset files, `bytes` their decoded size (compress.decoded_size)."""
    order = {d: i for i, d in enumerate(DIFFICULTIES)}
    charts, used = [], set()
    (site / "assets").mkdir(parents=True, exist_ok=True)
    for p in chart_manifests(site):
        man = json.loads(p.read_text(encoding="utf-8"))
        for e in man["files"].values():
            used.update(entry_assets(e))
        entry = {
            "id": p.stem,
            "manifest": p.relative_to(site).as_posix(),
            "musicId": man["musicId"],
            "difficulty": man["difficulty"],
            "audio": man["audio"],
            "audioFormat": man.get("audioFormat"),
            "flows": man.get("flows"),
            "bytes": sum(f["size"] for f in man["files"].values()),
            **man["chart"],
        }
        if man.get("regions"):
            entry["regions"] = man["regions"]
        charts.append(entry)
    charts.sort(key=lambda c: (c["musicId"], order[c["difficulty"]], c["manifest"]))
    meta = index_meta(site, charts, language, regions)
    (site / CHARTS_INDEX).write_bytes(_dump({"format": SITE_FORMAT, **meta, "charts": charts}))
    models, model_assets = write_models_index(site)
    used |= model_assets
    stories, story_assets = write_stories_index(site)
    used |= story_assets
    removed = 0
    for f in (site / "assets").iterdir():
        if f"assets/{f.name}" not in used:
            f.unlink()
            removed += 1
    files = list((site / "assets").iterdir())
    out = {
        "charts": len(charts),
        "models": models,
        "assets": len(files),
        "assetBytes": sum(f.stat().st_size for f in files),
        "bytes": sum(map(compress.decoded_size, files)),
        "removedAssets": removed,
    }
    if stories:
        out["stories"] = stories
    return out


def write_models_index(site: Path) -> tuple[int, set[str]]:
    """site/models.json from every model manifest present (no models directory: no models.json); returns the number
    of models and the assets their manifests reference."""
    site = Path(site)
    mdir = site / MODELS_DIR
    if not mdir.is_dir():
        (site / MODELS_INDEX).unlink(missing_ok=True)
        return 0, set()
    models, used = [], set()
    for p in sorted(mdir.glob("*.json")):
        man = json.loads(p.read_text(encoding="utf-8"))
        for e in man["files"].values():
            used.update(entry_assets(e))
        models.append(
            {
                "id": p.stem,
                "manifest": f"{MODELS_DIR}/{p.name}",
                "key": man["key"],
                "files": len(man["files"]),
                "bytes": sum(f["size"] for f in man["files"].values()),
                **man["model"],
            }
        )
    (site / MODELS_INDEX).write_bytes(_dump({"format": SITE_FORMAT, "models": models}))
    return len(models), used


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


def write_stories_index(site: Path, language: str | None = None, regions: list[dict] | None = None) -> tuple[int, set]:
    """site/stories.json from every story manifest present (no stories directory: no stories.json): entries in
    advId, manifest order, `size` the decoded bytes of the common files, of each language group and of the files of
    the referenced models (`models`; a model manifest not present counts none); `language` (default listing
    language) and `regions` of this build, else those of the existing index. Returns the number of stories and the
    assets they reference (in their manifests and in the model manifests they name)."""
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
    models: dict[str, tuple[int, set]] = {}  # model manifest path -> (decoded bytes, assets)

    def model(rel: str) -> tuple[int, set]:
        if rel not in models:
            f = site / rel
            files = json.loads(f.read_text(encoding="utf-8"))["files"].values() if f.is_file() else []
            models[rel] = (sum(e["size"] for e in files), {a for e in files for a in entry_assets(e)})
        return models[rel]

    entries, used = [], set()
    for p in story_manifests(site):
        man = json.loads(p.read_text(encoding="utf-8"))
        used |= manifest_assets(man)
        refs = [model(rel) for rel in (man.get("models") or {}).values()]
        for _, assets in refs:
            used |= assets
        size = {
            "common": sum(e["size"] for e in man["files"].values()),
            "languages": {lang: sum(e["size"] for e in g["files"].values()) for lang, g in man["languages"].items()},
            "models": sum(n for n, _ in refs),
        }
        e = {
            "id": str(man["advId"]),
            "manifest": p.relative_to(site).as_posix(),
            "size": size,
            "audio": man["audio"],
            "audioFormat": man["audioFormat"],
            "fonts": man["fonts"],
            **man["story"],
        }
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
