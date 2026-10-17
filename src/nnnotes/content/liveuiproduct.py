"""Live start text, font pages and image bindings assembled from object tables."""

from __future__ import annotations

from ..source import languages
from ..unity import textstyle
from ..unity.ids import object_id, parse_object_id
from . import live, liveui
from .prefabs import component_class, component_ref, has_json, key_object, mono_json, ref_id

FORMAT = "nnnotes.live-ui/1"
TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterBand", "MasterText")


def parse_subject(subject: str) -> tuple[int, str, str]:
    """The music id, difficulty and language of <musicId>/<difficulty>/<language>."""
    parts = subject.split("/")
    if len(parts) != 3:
        raise ValueError(f"{subject!r}: expected <musicId>/<difficulty>/<language>")
    music, difficulty, _ = live.parse_subject("/".join(parts[:2]))
    return music, difficulty, languages.check(parts[2])


def record(tables: dict, subject: str) -> dict:
    """Master rows read by the selected chart's start canvas in one language."""
    music_id, difficulty, code = parse_subject(subject)
    music = next(r for r in tables["MasterLiveMusic"] if r["_id"] == music_id)
    score = next(r for r in tables["MasterLiveMusicScore"] if r["_id"] == music[f"_{difficulty}ID"])
    column = languages.column(code)
    return {
        "MasterLiveMusic": [music],
        "MasterLiveMusicScore": [score],
        "MasterBand": [r for r in tables["MasterBand"] if r["_id"] in music["_bandIDs"]],
        "MasterText": [
            {"_id": r["_id"], **{c: r.get(c) or "" for c in {column, languages.column("ja")}}}
            for r in tables["MasterText"]
        ],
    }


def keys(master: dict) -> list[str]:
    return [liveui.SCENE_KEY, live.jacket_key(master["MasterLiveMusic"][0]["_jacketAssetName"])]


def canvas(objects) -> tuple[str, dict, dict]:
    """The scene prefab, canvas node and UILiveStartCanvas component."""
    hits = [
        (oid, node, comp)
        for oid in objects.key(liveui.SCENE_KEY)
        if oid in objects and objects.info(oid)["class"] == "GameObject" and has_json(objects, oid)
        for node in objects.json(oid)["nodes"]
        if node["path"] == liveui.CANVAS
        for comp in node["components"]
        if component_class(comp) == "UILiveStartCanvas"
    ]
    if len(hits) != 1:
        raise ValueError(f"{liveui.CANVAS}: expected one UILiveStartCanvas, found {len(hits)}")
    return hits[0]


def text_records(objects, master: dict, subject: str, settings) -> tuple[dict, dict, dict]:
    """Canvas metadata, TMP records by component id and LocalizeManager font data."""
    from . import storyui

    music_id, difficulty, language = parse_subject(subject)
    prefab, node, component = canvas(objects)
    file, _ = parse_object_id(prefab)
    nodes = objects.json(prefab)["nodes"]
    fields = component["fields"]
    strings, _ = liveui.record_strings(master, music_id, difficulty, languages.column(language))
    lang = textstyle.language_fonts_json(mono_json(objects, storyui.script_root(objects, "LocalizeManager")))
    styles = storyui._Styles(lang, languages.mode(language), settings)

    def target(value):
        hit = component_ref(objects, prefab, value)
        if hit is None:
            raise ValueError("live start canvas requires a component target")
        holder, index, comp = hit
        return object_id(parse_object_id(holder)[0], int(comp["pathId"])), objects.json(holder)["nodes"][index]["path"]

    initialized = {}
    for field, key in liveui.TEXT_FIELDS.items():
        refs = fields[field] if isinstance(fields[field], list) else [fields[field]]
        for value in refs:
            _, path = target(value)
            initialized[path] = key
    records = {}
    paths = {}
    table = {str(r["_id"]): r[languages.column(language)] for r in master["MasterText"]}
    for n in nodes:
        if not liveui._drawn(n["path"]):
            continue
        texts = [c for c in n["components"] if component_class(c) in storyui.TEXT_CLASSES]
        locs = [c for c in n["components"] if component_class(c) == "LocalizeText"]
        if not texts:
            continue
        if len(texts) != 1 or len(locs) > 1 or n["path"] in paths:
            raise ValueError(f"{n['path']}: ambiguous text or localization components")
        comp = {"class": component_class(texts[0]), **texts[0]["fields"]}
        comp["m_fontAsset"] = {"name": storyui._name(objects, comp["m_fontAsset"])}
        comp["m_sharedMaterial"] = {"material": storyui._name(objects, comp["m_sharedMaterial"])}
        localize = {"class": "LocalizeText", **locs[0]["fields"]} if locs else None
        rec = styles.record(n["path"], comp, localize)
        text_key = rec["style"].get("textKey")
        shown = (
            strings[initialized[n["path"]]]
            if n["path"] in initialized
            else (table[str(text_key)] if text_key else comp["m_text"])
        )
        tid = object_id(file, int(texts[0]["pathId"]))
        paths[n["path"]] = tid
        rec.update(path=n["path"], text=shown, setByInitialize=n["path"] in initialized)
        rec["font"] = dict(
            rec["localized"]
            or {
                "fontAsset": comp["m_fontAsset"]["name"],
                "material": comp["m_sharedMaterial"]["material"],
                "lineSpacing": comp["m_lineSpacing"],
            }
        )
        records[tid] = rec
    missing = [p for p in initialized if liveui._drawn(p) and p not in paths]
    if missing:
        raise ValueError(f"live start text targets have no TMP records: {missing}")
    jacket = key_object(objects, live.jacket_key(master["MasterLiveMusic"][0]["_jacketAssetName"]), "Sprite")
    icons = {str(p["Difficulty"]): ref_id(p["Icon"]) for p in fields["_difficultyPairs"]}
    icon = icons[str(liveui.LIVE_DIFFICULTY[difficulty])]
    if icon is None:
        raise ValueError(f"{difficulty}: no difficulty icon")
    images = {
        target(value)[0]: sprite
        for field, sprite in (("_jacketImages", jacket), ("_difficultyImages", icon))
        for value in fields[field]
    }
    metadata = {
        "musicId": music_id,
        "difficulty": difficulty,
        "scene": {"manifest": "scenes/default.json", "prefab": prefab, "canvas": node["path"]},
        "strings": strings,
        "textStyle": styles.summary(),
        "images": images,
        "jacket": jacket,
        "difficultyIcons": icons,
    }
    return metadata, records, lang


def build(objects, master: dict, subject: str, settings, source: str, font_files: dict):
    """ui.json, PNG pages and font shader objects for a live start canvas."""
    from .storyfontproduct import font_product

    _, _, language = parse_subject(subject)
    metadata, records, lang = text_records(objects, master, subject, settings)
    doc, pages, keep, roles = font_product(
        objects,
        records,
        {},
        [r["text"] for r in records.values()],
        language,
        lang,
        settings,
        source,
        font_files,
        format=FORMAT,
        subset="live start canvas text, synthesized control characters, underline and missing glyph",
    )
    doc.update(metadata)
    return doc, pages, keep, roles
