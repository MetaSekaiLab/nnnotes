"""A spot (an explorable 2.5D scene of the home) assembled from the objects its situation prefab reaches: spot.json and
the Spine files of its characters.

A spot is a background prefab (a room, content.room) and a situation prefab: SpotSituationSettings (camera and
interaction parameters), SpotCharacterController, SpotCharacter x N (tap targets with focus transforms) and
SpotSpineCharacter x M, each driving a Spine.Unity SkeletonAnimation. `spot` reads the situation prefab's document
(content.prefabs) and the objects its components reach, never a bundle; a MonoBehaviour's class is its script's. The
spot's row of MasterHomeSpot, its name and its tap talks come from the master data (`master_record`).

A reference the prefab leaves empty is written as null: a SpotSpineCharacter with no `_animation` has null
`skeletonData`, `animation` and `world`; a SpotCharacter with no `_focus` has a null `focusWorld`. Fields that name
objects are written as the prefab document writes them (references). World matrices are 4x4 column-major in Unity's
world space (left-handed) of the situation prefab, its root's own transform included.

Spine files (spine/<file>) of each SkeletonDataAsset of the table (spine_files):
  SkeletonDataAsset.skeletonJSON              -> the TextAsset's bytes: the skeleton (.json or binary .skel)
  SkeletonDataAsset.atlasAssets[i].atlasFile  -> <name>.atlas, as stored
  SpineAtlasAsset.materials[j]._MainTex       -> the Texture2D's PNG, named after atlas page j
"""

from __future__ import annotations

import re

from .prefabs import Objects, Prefab, mat4, ref_id, root_game_object, script_class

__all__ = ["LANGS", "SITUATION_KEY", "atlas_pages", "master_record", "situation_name", "spine_files", "spot"]

# a spot situation's catalog key: Spot/<place>/<situation>/<situation>
SITUATION_KEY = r"Spot/(?P<place>[^/]+)/(?P<situation>[^/]+)/(?P=situation)"
LANGS = ("_japanese", "_english", "_traditionalChinese", "_simplifiedChinese", "_korean")
MASTER_TABLES = ("MasterHomeSpot", "MasterText", "MasterStoryHomeSpotTapTalkEpisode")
HEADER = ("m_GameObject", "m_Script", "m_Enabled", "m_Name")  # a component's Unity header fields
ANIMATION_FIELDS = (
    "_animationName",
    "loop",
    "timeScale",
    "initialSkinName",
    "initialFlipX",
    "initialFlipY",
    "pmaVertexColors",
    "tintBlack",
    "zSpacing",
)
MATRIX_CONVENTION = "Unity world space (left-handed), 4x4 column-major"


def situation_name(key: str) -> str:
    """The <situation> of a situation key (ValueError for another key, a background's included)."""
    m = re.fullmatch(SITUATION_KEY, key)
    if m is None or m["situation"] == "Background":
        raise ValueError(f"{key}: not a spot situation key (Spot/<place>/<situation>/<situation>)")
    return m["situation"]


def master_record(tables: dict[str, list[dict]], key: str) -> dict:
    """{row, name, tapTalk} of the spot whose situation is `key`, from the master tables MASTER_TABLES ({table:
    rows}): its MasterHomeSpot row, its name per language and {character id: adv ids} of its tap talks (ValueError
    when no row, or several, has the key)."""
    rows = [r for r in tables["MasterHomeSpot"] if r.get("_situationAssetPath") == key]
    if len(rows) != 1:
        raise ValueError(f"{key}: {len(rows)} MasterHomeSpot rows have it as their situation")
    row = rows[0]
    text = next((t for t in tables["MasterText"] if t.get("_id") == row.get("_nameTextId")), {})
    taps: dict[int, list[int]] = {}
    for r in tables["MasterStoryHomeSpotTapTalkEpisode"]:
        if r.get("_spotId") == row["_id"]:
            taps.setdefault(r["_characterId"], []).append(r["_advId"])
    return {
        "row": row,
        "name": {lang[1:]: text.get(lang, "") for lang in LANGS},
        "tapTalk": {str(k): v for k, v in sorted(taps.items())},
    }


def atlas_pages(text: str) -> list[str]:
    """Page file names of a Spine atlas (a page line is followed by 'size:')."""
    lines = [ln.strip() for ln in text.splitlines()]
    return [
        ln
        for i, ln in enumerate(lines)
        if ln and ":" not in ln and i + 1 < len(lines) and lines[i + 1].startswith("size:")
    ]


def _object(objects: Objects, v, what: str) -> str:
    oid = ref_id(v)
    if oid is None or oid not in objects:
        raise ValueError(f"{what} is not in the object table")
    return oid


def _text_asset(objects: Objects, v, what: str) -> tuple[str, bytes]:
    """(name, bytes) of the TextAsset a reference names."""
    oid = _object(objects, v, what)
    return objects.json(oid)["m_Name"], objects.bytes(oid, "data")


def spine_files(objects: Objects, sda: str, written: dict[str, str]) -> tuple[dict, dict[str, bytes]]:
    """(the skeleton's record of spot.json: name, skeleton, atlases, scale, defaultMix, mixes; {file name: bytes})
    of the SkeletonDataAsset `sda`: the skeleton, then for each of its atlases not in `written` ({SpineAtlasAsset
    object id: atlas file name}, updated here) the atlas text as stored and one PNG per page."""
    d = objects.json(sda)
    name = d["m_Name"]
    files: dict[str, bytes] = {}
    sk_name, raw = _text_asset(objects, d.get("skeletonJSON"), f"{name}: the skeleton")
    ext = ".json" if raw.lstrip()[:1] == b"{" else ".skel"
    sk_file = sk_name if sk_name.endswith(ext) else f"{sk_name}{ext}"
    files[sk_file] = raw
    atlases = []
    for ap in d.get("atlasAssets") or ():
        aid = _object(objects, ap, f"{name}: an atlas asset")
        if aid not in written:
            asset = objects.json(aid)
            a_name, a_raw = _text_asset(objects, asset.get("atlasFile"), f"{name}: an atlas file")
            a_file = a_name if a_name.endswith(".atlas") else f"{a_name}.atlas"
            files[a_file] = a_raw  # the atlas text exactly as the game stores it
            pages = atlas_pages(a_raw.decode("utf-8"))
            mats = asset.get("materials") or []
            if len(mats) != len(pages):
                raise ValueError(f"{a_file}: {len(pages)} pages vs {len(mats)} materials")
            for page, mp in zip(pages, mats):
                mat = objects.json(_object(objects, mp, f"{a_file}: the material of page {page}"))
                slot = (mat.get("textures") or {}).get("_MainTex")
                files[page] = objects.bytes(_object(objects, slot and slot["texture"], f"{page}: the texture"), "image")
            written[aid] = a_file
        atlases.append(written[aid])
    record = {
        "name": name,
        "skeleton": sk_file,
        "atlases": atlases,
        "scale": d["scale"],
        "defaultMix": d["defaultMix"],
        "mixes": [
            {"from": f, "to": t, "duration": x}
            for f, t, x in zip(d.get("fromAnimation", []), d.get("toAnimation", []), d.get("duration", []))
        ],
    }
    return record, files


def _fields(f: dict, *drop: str) -> dict:
    return {k: v for k, v in f.items() if k not in HEADER and k not in drop}


def _first(prefab: Prefab, cls: str, key: str) -> dict:
    found = [c for _, c in prefab.components(cls)]
    if not found:
        raise ValueError(f"{key}: no {cls} in the situation prefab")
    return found[0]["fields"]


def spot(objects: Objects, key: str, master: dict) -> tuple[dict, dict[str, bytes]]:
    """(spot.json, {spine/<file>: bytes}) of the spot whose situation is `key` and whose master_record is `master`
    (module documentation)."""
    prefab = Prefab(objects.json(root_game_object(objects, key)))
    files: dict[str, bytes] = {}
    skeletons: dict[str, dict] = {}  # SkeletonDataAsset object id -> its record
    written: dict[str, str] = {}
    sdas = [o for o in objects.ids("MonoBehaviour") if script_class(objects, o) == "SkeletonDataAsset"]
    for sda in sorted(sdas, key=lambda o: (objects.json(o)["m_Name"], o)):
        record, made = spine_files(objects, sda, written)
        skeletons[sda] = record
        for f, data in made.items():
            if f"spine/{f}" in files:
                raise ValueError(f"{key}: two Spine files are named {f!r}")
            files[f"spine/{f}"] = data

    spine_characters = []
    for i, c in prefab.components("SpotSpineCharacter"):
        path = prefab.nodes[i]["path"]
        anim = prefab.component_of(c["fields"].get("_animation"))
        if anim is None:
            spine_characters.append({"path": path, "skeletonData": None, "animation": None, "world": None})
            continue
        j, a = anim
        sda = _object(objects, a["fields"].get("skeletonDataAsset"), f"{path}: the skeleton data")
        spine_characters.append(
            {
                "path": path,
                "skeletonData": skeletons[sda]["name"],
                "animation": {k: a["fields"][k] for k in ANIMATION_FIELDS},
                "world": mat4(prefab.world(j)),
            }
        )
    characters = []
    for i, c in prefab.components("SpotCharacter"):
        focus = prefab.node_of(c["fields"].get("_focus"))
        characters.append(
            {
                "path": prefab.nodes[i]["path"],
                **_fields(c["fields"], "_focus"),
                "world": mat4(prefab.world(i)),
                "focusWorld": None if focus is None else mat4(prefab.world(focus)),
            }
        )
    characters.sort(key=lambda x: x["_characterId"])
    row = master["row"]
    doc = {
        "spotId": row["_id"],
        "master": row,
        "name": master["name"],
        "tapTalk": master["tapTalk"],
        "situationSettings": _fields(_first(prefab, "SpotSituationSettings", key)),
        "controller": _fields(_first(prefab, "SpotCharacterController", key), "_spotSpineCharacters", "_characters"),
        "characters": characters,
        "spineCharacters": spine_characters,
        "skeletons": list(skeletons.values()),
        "matrixConvention": MATRIX_CONVENTION,
    }
    return doc, files
