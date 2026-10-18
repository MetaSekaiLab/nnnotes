"""Note, skin and effect products assembled from shared object tables and master records."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from .liveoptions import OPTION_ITEM_TYPES, PLAYER_ITEMS
from .prefabs import (
    Objects,
    Prefab,
    clip_bindings,
    component_class,
    controller_clips,
    has_json,
    key_object,
    mesh_roles,
    prefab_ids,
    ref_id,
    shader_table,
)

__all__ = [
    "EFFECT_KEY",
    "FLICK_SKIN_UNITS",
    "LANE_EFFECT_KEY",
    "NOTE_PREFABS",
    "NOTES_FORMAT",
    "NOTES_TABLES",
    "SKIN_KEY",
    "animator_bindings",
    "asset_name",
    "default_assets",
    "effect_settings_key",
    "notes",
    "notes_keys",
    "notes_record",
    "notes_settings",
    "noteeffect",
    "noteskin",
    "noteskin_keys",
    "skin_key",
]

NOTES_FORMAT = 1  # notes.json, noteskin.json and noteeffect.json "format"
NOTE_VIEWS = [
    "tap",
    "flick",
    "flick_left",
    "flick_right",
    "trace",
    "slide",
    "slide_end",
    "connection",
    "guide",
    "slide_combo",
    "slide_combo_skip",
    "none",
]
NOTE_PREFABS = {f"{v}_note_view": f"EmbLive/Prefabs/LiveGame/{v}_note_view" for v in NOTE_VIEWS}
NOTE_PREFABS.update(
    {
        "slide_line_view": "EmbLive/Prefabs/LiveGame/slide_line_view",
        "guide_line_view": "EmbLive/Prefabs/LiveGame/guide_line_view",
        "pair_note_line": "EmbLive/Prefabs/LiveGame/PairNoteLine/LiveGamePairNoteView",
        "note_groove_loop": "EmbLive/Prefabs/LiveGame/Effect/note_groove_loop",
    }
)
LANE_EFFECT = "effect001"
LANE_EFFECT_KEY = f"Effect/Live/LaneEffect/{LANE_EFFECT}/LiveLaneEffectAssetSettings"
# LiveAddressablePath.GetLiveNoteSkinAssetPath / GetLiveNoteEffectSkinAssetPath
SKIN_KEY = re.compile(r"Live/Note/(?P<name>[^/]+)/LiveNoteSkinAsset")
EFFECT_KEY = re.compile(r"Effect/Live/NoteEffect/(?P<name>[^/]+)/LiveNoteEffectAssetSettings")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")  # a skin or effect set name, as a manifest path segment
# the flick views and the LiveNoteSkinAsset field of the unit that sets up each one's arrow
FLICK_SKIN_UNITS = {
    "flick_note_view": "FlickNoteAsset",
    "flick_left_note_view": "LeftFlickNoteAsset",
    "flick_right_note_view": "RightFlickNote",
}

LANE_COUNT = 24  # LiveDataCreator.CreateBootData passes 0x18
# LiveBootDataCreator.CreateViewData: arguments of LiveSettingCreator.CreateLaneViewSettings
LANE_VIEW = {
    "size": (1920.0, 1080.0),
    "topRange": 0.05,
    "bottomRange": 1.0,
    "judgementScreenBottomPosition": 2.24,
    "topPosition": 0.0,
}

# MasterOptionDefault preset 1 (CurrentPresetIndex 0 -> Preset1); item types from App.Options.OptionItemType.
OPTION_ITEMS = {
    k: OPTION_ITEM_TYPES[k]
    for k in (
        1,
        4,
        6,
        100,
        101,
        102,
        103,
        104,
        105,
        106,
        107,
        108,
        109,
        112,
        113,
        117,
        119,
        200,
        205,
        206,
        207,
        208,
        300,
        301,
        302,
        305,
        306,
        307,
        308,
        309,
        310,
    )
}
NOTE_ITEMS = dict(sorted({**OPTION_ITEMS, **{k: OPTION_ITEM_TYPES[k] for k in PLAYER_ITEMS}}.items()))
LIVE_SETTING_KEYS = [
    "note_speed_min",
    "note_speed_max",
    "note_speed_view_min",
    "note_speed_view_max",
    "note_slide_combo_rhythmic_unit",
    "stop_in_vain_time_ms_diff",
    "slide_offset_min_note_lane_width",
    "slide_offset_max_note_lane_width",
    "note_overlap_lane_buffer",
]
NOTES_TABLES = (
    "MasterOptionDefault",
    "MasterOptionRange",
    "MasterLiveSettings",
    "MasterLiveNoteSkin",
    "MasterLiveNoteEffectSkin",
)


def skin_key(name: str) -> str:
    """The note skin asset of MasterLiveNoteSkin._assetName `name` (LiveAddressablePath.GetLiveNoteSkinAssetPath)."""
    return f"Live/Note/{name}/LiveNoteSkinAsset"


def effect_settings_key(effect: str) -> str:
    """The note effect settings asset of effect set `effect` (LiveAddressablePath.GetLiveNoteEffectSkinAssetPath)."""
    return f"Effect/Live/NoteEffect/{effect}/LiveNoteEffectAssetSettings"


def asset_name(name: str) -> str:
    """A note skin or effect set name as a subject (ValueError for one that is not URL-safe)."""
    if not NAME.fullmatch(name):
        raise ValueError(f"{name!r}: not a note skin or effect set name")
    return name


def notes_keys() -> list[str]:
    """The catalog keys of the notes product: its prefabs and the lane effect settings."""
    return sorted({*NOTE_PREFABS.values(), LANE_EFFECT_KEY})


def noteskin_keys(name: str) -> list[str]:
    """The catalog keys of the noteskin product of skin `name`: its asset and the flick views its units set up."""
    return sorted({skin_key(asset_name(name)), *(NOTE_PREFABS[v] for v in FLICK_SKIN_UNITS)})


def _lane_settings() -> dict:
    return {
        "laneCount": LANE_COUNT,
        "laneSize": list(LANE_VIEW["size"]),
        "laneTopRange": LANE_VIEW["topRange"],
        "laneBottomRange": LANE_VIEW["bottomRange"],
        "judgementScreenBottomPosition": LANE_VIEW["judgementScreenBottomPosition"],
        "laneTopPosition": LANE_VIEW["topPosition"],
        "noteViewProgress": {"base": 1.065, "exponentScale": 45.0},
        "tiltCenterLane": 11.5,
    }


def notes_record(tables: dict) -> dict:
    """What notes_settings reads of the master tables `tables` {name: rows} (NOTES_TABLES): the preset-1 defaults and
    the ranges of NOTE_ITEMS, the LIVE_SETTING_KEYS rows of MasterLiveSettings, the note skins and effect sets."""
    return {
        "MasterOptionDefault": [
            r for r in tables["MasterOptionDefault"] if r["_presetId"] == 1 and r["_optionItemType"] in NOTE_ITEMS
        ],
        "MasterOptionRange": [r for r in tables["MasterOptionRange"] if r["_optionItemType"] in NOTE_ITEMS],
        "MasterLiveSettings": [r for r in tables["MasterLiveSettings"] if r["_key"] in LIVE_SETTING_KEYS],
        "MasterLiveNoteSkin": tables["MasterLiveNoteSkin"],
        "MasterLiveNoteEffectSkin": tables["MasterLiveNoteEffectSkin"],
    }


def default_assets(record: dict) -> tuple[str, str]:
    """(note skin, note effect set) of the option defaults (NoteDesignId, NoteEffectId) of a notes_record."""
    opts = {NOTE_ITEMS[r["_optionItemType"]]: r["_valueString"] for r in record["MasterOptionDefault"]}
    skins = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteSkin"]}
    effects = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteEffectSkin"]}
    return skins[int(opts["NoteDesignId"])], effects[int(opts["NoteEffectId"])]


def notes_settings(record: dict) -> dict:
    """notes.json `settings` (module documentation) from a notes_record."""
    opts = {NOTE_ITEMS[r["_optionItemType"]]: r["_valueString"] for r in record["MasterOptionDefault"]}
    ls = {r["_key"]: r["_value"] for r in record["MasterLiveSettings"]}
    skins = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteSkin"]}
    effects = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteEffectSkin"]}
    skin, effect = default_assets(record)
    return {
        **_lane_settings(),
        "optionDefaults": dict(sorted(opts.items())),
        "optionRanges": {
            NOTE_ITEMS[r["_optionItemType"]]: [r["_minValue"], r["_maxValue"]]
            for r in sorted(record["MasterOptionRange"], key=lambda r: r["_optionItemType"])
        },
        "liveSettings": {k: ls[k] for k in LIVE_SETTING_KEYS if k in ls},
        "noteDisplayTimeMs": display_time_ms(
            float(opts["NoteSpeed"]),
            float(ls["note_speed_min"]),
            float(ls["note_speed_max"]),
            float(ls["note_speed_view_min"]),
            float(ls["note_speed_view_max"]),
        ),
        "skin": skin,
        "effect": effect,
        "laneEffect": LANE_EFFECT,
        "skins": {str(i): n for i, n in sorted(skins.items())},
        "effects": {str(i): n for i, n in sorted(effects.items())},
    }


def display_time_ms(speed: float, smin: float, smax: float, vmin: float, vmax: float) -> int:
    """NoteBeforePlayingTimeGetter.GetNoteDisplayOffsetTimeMs, in float32."""
    import numpy as np

    f = np.float32
    t = max(f(0), f(speed) - f(smin)) / (f(smax) - f(smin))
    t = min(max(t, f(0)), f(1))
    e = f(1) - f(np.power(f(1) - t, f(1.31)))
    e = min(max(e, f(0)), f(1))
    return int(f((f(vmin) + (f(vmax) - f(vmin)) * e) * f(1000)))


# ------------------------------------------------------------------ products
def _loop_clips(prefab: Prefab) -> dict[str, list[str]]:
    """{Animator node path: clip ids} the LoopAnimationTimeSyncs of a prefab play: each one's `_loopAnim` on its
    `_animator`."""
    out: dict[str, list[str]] = {}
    for _, c in prefab.components("LoopAnimationTimeSync"):
        clip = ref_id(c["fields"].get("_loopAnim"))
        hit = prefab.component_of(c["fields"].get("_animator"))
        if clip is not None and hit is not None:
            out.setdefault(prefab.nodes[hit[0]]["path"], []).append(clip)
    return out


def animator_bindings(objects: Objects, extra: Mapping[str, Mapping[str, Iterable[str]]] | None = None) -> dict:
    """`bindings` of every prefab of the table: the clips of its Animators' controllers, of the LoopAnimationTimeSyncs
    on them, and those `extra` {prefab GameObject id: {Animator node path: clip ids}} gives."""
    out = {}
    for gid in prefab_ids(objects):
        doc = objects.json(gid)
        plays = controller_clips(objects, doc)
        for path, clips in _loop_clips(Prefab(doc)).items():
            plays.setdefault(path, []).extend(clips)
        for path, clips in ((extra or {}).get(gid) or {}).items():
            plays.setdefault(path, []).extend(clips)
        table = clip_bindings(objects, doc, plays)
        if table:
            out[gid] = table
    return out


def _product(objects: Objects, doc: dict) -> tuple[dict, set[str], dict[str, set[str]]]:
    shaders, roles = shader_table(objects)
    doc["shaders"] = shaders
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    return doc, keep, roles


def notes(objects: Objects, record: dict) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(notes.json, the object ids the player reads, {object id: the roles of its files it reads}) of the notes
    product (module documentation; `record`: notes_record)."""
    doc = {
        "format": NOTES_FORMAT,
        "settings": notes_settings(record),
        "prefabs": {name: key_object(objects, key, "GameObject") for name, key in NOTE_PREFABS.items()},
        "laneEffect": key_object(objects, LANE_EFFECT_KEY, "LiveLaneEffectAssetSettings"),
        "bindings": animator_bindings(objects),
    }
    return _product(objects, doc)


def noteskin(objects: Objects, name: str) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(noteskin.json, the object ids the player reads, roles of its files) of the note skin `name` (module
    documentation)."""
    skin = key_object(objects, skin_key(asset_name(name)), "LiveNoteSkinAsset")
    fields = objects.json(skin)
    out = {}
    for view, unit_field in FLICK_SKIN_UNITS.items():
        unit = ref_id(fields.get(unit_field))
        clip = ref_id(objects.json(unit).get("_arrowLoopAnimation")) if unit and has_json(objects, unit) else None
        if clip is None:
            continue
        gid = key_object(objects, NOTE_PREFABS[view], "GameObject")
        doc = objects.json(gid)
        prefab = Prefab(doc)
        syncs = [c for c in doc["nodes"][0]["components"] if "_loopAnimationTimeSync" in c["fields"]]
        if len(syncs) != 1:
            raise ValueError(f"{view}: {len(syncs)} note views with a _loopAnimationTimeSync on its root")
        sync = prefab.component_of(syncs[0]["fields"]["_loopAnimationTimeSync"])
        animator = prefab.component_of(sync[1]["fields"]["_animator"]) if sync is not None else None
        if animator is None or component_class(animator[1]) != "Animator":
            raise ValueError(f"{view}: its LoopAnimationTimeSync has no Animator")
        table = clip_bindings(objects, doc, {prefab.nodes[animator[0]]["path"]: [clip]})
        if table:
            out[gid] = table
    return _product(objects, {"format": NOTES_FORMAT, "name": name, "skin": skin, "bindings": out})


def noteeffect(objects: Objects, name: str) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(noteeffect.json, the object ids the player reads, roles of its files) of the note effect set `name` (module
    documentation)."""
    settings_id = key_object(objects, effect_settings_key(asset_name(name)), "LiveNoteEffectAssetSettings")
    doc = {"format": NOTES_FORMAT, "name": name, "settings": settings_id, "bindings": animator_bindings(objects)}
    return _product(objects, doc)


# Catalog key families exported whole (every key under the prefix): the note effect settings of the NoteEffectId
# option's set ({effect}) and the lane effect settings (LANE_EFFECT), with the effect prefabs they reference


# UI subtrees of [EmbScene/Live] animated by exported clips (combo counter, judgement text); used only to name
# clip binding paths, the scene itself is exported by livescene.
# The bar line view (MeasureLineDisplay): the scene's LiveAllNoteView -> _barLineView (LiveAllBarLineView) ->
# _container (LiveBarLineViewContainer) -> _elementPrefab (LiveBarLineView, the prefab the container instantiates)
NOTE_VIEW = ("LiveGameView/LiveGameCamera/screen_root/LiveGameAllNoteView", "LiveAllNoteView")
BAR_LINE_CHAIN = (
    ("_barLineView", "LiveAllBarLineView"),
    ("_container", "LiveBarLineViewContainer"),
    ("_elementPrefab", "LiveBarLineView"),
)
