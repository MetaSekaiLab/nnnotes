"""The note highway of a live: what is spawned or animated per note, and its settings.

Three products, each read from a product's object table (`Objects`, pipeline.assemble), never from a bundle, and each
shared by every live that loads it:

notes       the highway itself (`notes`, notes.json format NOTES_FORMAT): the note view prefabs (tap, flick
            +left/right, trace, slide/slide_end/connection, guide, slide_combo(+skip), none), the long-note body
            prefabs (slide_line_view, guide_line_view), the pair line and the groove loop (NOTE_PREFABS), the lane
            effect settings (LANE_EFFECT_KEY, the set the game always loads: LaneEffectLoadStep.GetAssetPath), and
            `settings`: the option defaults (preset 1) and ranges of the items the note runtime and the player read,
            the MasterLiveSettings values, the note display time, the lane geometry, the default note skin and effect
            set and the names of every note skin and effect set by option value (notes_settings)
noteskin    a note skin (`noteskin`, MasterLiveNoteSkin._assetName: the LiveNoteSkinAsset of its key skin_key), with
            the bindings of the arrow loops its flick units give the flick views (FLICK_SKIN_UNITS)
noteeffect  a note effect set (`noteeffect`, MasterLiveNoteEffectSkin._assetName or its `Light` variant, which the
            game loads at quality Low where the catalog has it: the LiveNoteEffectAssetSettings of its key
            effect_settings_key and the effect prefabs it references)

Each document names objects by id: notes.json `prefabs` {name: GameObject} and `laneEffect`, noteskin.json `skin`,
noteeffect.json `settings`; and each has

    bindings   {prefab GameObject id: {Animator node path: {clip id: [{path, attribute}]}}}: the clips each Animator of
               the table's prefabs plays (its controller's clips, the `_loopAnim` of a LoopAnimationTimeSync on its
               `_animator`; for a skin, the arrow loop of each flick unit on the flick view's LoopAnimationTimeSync,
               LiveFlickNoteView.OnSetupNoteSkin), named from the Animator's node (prefabs.clip_bindings)
    shaders    {shader name: object id} (prefabs.shader_table)

Of the objects a product keeps all but the MonoScripts; of a shader its parsed form, variant index and the programs of
the player's graphics API.

`extract` writes the note runtime of a live directory instead (livenotes/notes.json of the default options and the
variants a liveoptions.LiveOptions offers, with textures and shaders) through export.Exporter, following references
to AnimatorControllers and SpriteAtlases.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.jsonio import write_json
from .liveoptions import OPTION_ITEM_TYPES, PLAYER_ITEMS, LiveOptions
from .prefabs import (
    Objects,
    Prefab,
    clip_bindings,
    component_class,
    controller_clips,
    has_json,
    prefab_ids,
    ref_id,
    script_class,
    shader_table,
)

if TYPE_CHECKING:
    from ..source.catalog import Catalog
    from ..unity.export import Exporter
    from ..unity.player import PlayerData

__all__ = [
    "EFFECT_KEY",
    "FLICK_SKIN_UNITS",
    "LANE_EFFECT_KEY",
    "NOTE_PREFABS",
    "NOTES_FORMAT",
    "NOTES_TABLES",
    "SKIN_KEY",
    "asset_name",
    "effect_settings_key",
    "extract",
    "notes",
    "notes_keys",
    "notes_record",
    "notes_settings",
    "noteeffect",
    "noteskin",
    "noteskin_keys",
    "settings",
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
DEFAULT_SKIN = "skin001"  # MasterLiveNoteSkin._id 1 (NoteDesignId default 1)
DEFAULT_EFFECT = "effect001"  # MasterLiveNoteEffectSkin._id 1 (NoteEffectId default 1)


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


def notes_settings(record: dict) -> dict:
    """notes.json `settings` (module documentation) from a notes_record."""
    opts = {NOTE_ITEMS[r["_optionItemType"]]: r["_valueString"] for r in record["MasterOptionDefault"]}
    ls = {r["_key"]: r["_value"] for r in record["MasterLiveSettings"]}
    skins = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteSkin"]}
    effects = {r["_id"]: r["_assetName"] for r in record["MasterLiveNoteEffectSkin"]}
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
        "skin": skins[int(opts["NoteDesignId"])],
        "effect": effects[int(opts["NoteEffectId"])],
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
def _game_object(objects: Objects, key: str) -> str:
    """The prefab GameObject of the catalog key `key`."""
    hits = [o for o in objects.key(key) if objects.info(o).get("class") == "GameObject" and has_json(objects, o)]
    if len(hits) != 1:
        raise ValueError(f"{key}: {len(hits)} prefab GameObjects")
    return hits[0]


def _mono(objects: Objects, key: str, cls: str) -> str:
    """The MonoBehaviour of script class `cls` of the catalog key `key`."""
    hits = [o for o in objects.key(key) if script_class(objects, o) == cls]
    if len(hits) != 1:
        raise ValueError(f"{key}: {len(hits)} {cls} MonoBehaviours")
    return hits[0]


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


def _bindings(objects: Objects) -> dict[str, dict]:
    """`bindings` of every prefab of the table: its Animators' controller clips and LoopAnimationTimeSync clips."""
    out = {}
    for gid in prefab_ids(objects):
        doc = objects.json(gid)
        plays = controller_clips(objects, doc)
        for path, clips in _loop_clips(Prefab(doc)).items():
            plays.setdefault(path, []).extend(clips)
        table = clip_bindings(objects, doc, plays)
        if table:
            out[gid] = table
    return out


def _product(objects: Objects, doc: dict) -> tuple[dict, set[str], dict[str, set[str]]]:
    shaders, roles = shader_table(objects)
    doc["shaders"] = shaders
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    return doc, keep, roles


def notes(objects: Objects, record: dict) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(notes.json, the object ids the player reads, {shader id: the roles of its files it reads}) of the notes
    product (module documentation; `record`: notes_record)."""
    doc = {
        "format": NOTES_FORMAT,
        "settings": notes_settings(record),
        "prefabs": {name: _game_object(objects, key) for name, key in NOTE_PREFABS.items()},
        "laneEffect": _mono(objects, LANE_EFFECT_KEY, "LiveLaneEffectAssetSettings"),
        "bindings": _bindings(objects),
    }
    return _product(objects, doc)


def noteskin(objects: Objects, name: str) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(noteskin.json, the object ids the player reads, shader roles) of the note skin `name` (module
    documentation)."""
    skin = _mono(objects, skin_key(asset_name(name)), "LiveNoteSkinAsset")
    fields = objects.json(skin)
    out = {}
    for view, unit_field in FLICK_SKIN_UNITS.items():
        unit = ref_id(fields.get(unit_field))
        clip = ref_id(objects.json(unit).get("_arrowLoopAnimation")) if unit and has_json(objects, unit) else None
        if clip is None:
            continue
        gid = _game_object(objects, NOTE_PREFABS[view])
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
    """(noteeffect.json, the object ids the player reads, shader roles) of the note effect set `name` (module
    documentation)."""
    settings_id = _mono(objects, effect_settings_key(asset_name(name)), "LiveNoteEffectAssetSettings")
    doc = {"format": NOTES_FORMAT, "name": name, "settings": settings_id, "bindings": _bindings(objects)}
    return _product(objects, doc)


# ------------------------------------------------------------------ live directory (export.Exporter)
PREFABS = {**NOTE_PREFABS, "judge_effect_view": "EmbLive/Prefabs/UILiveNoteJudgeEffectView"}
# Catalog key families exported whole (every key under the prefix): the note effect settings of the NoteEffectId
# option's set ({effect}) and the lane effect settings (LANE_EFFECT), with the effect prefabs they reference
KEY_PREFIXES = [
    "Effect/Live/NoteEffect/{effect}/",
    f"Effect/Live/LaneEffect/{LANE_EFFECT}/",
]
SINGLE_KEYS = [
    "EmbLive/Animation/Combo/UILiveCombo",  # UIComboCounterView's animator controller
    "Live/UiSpriteAssets/LiveComboSpriteAsset",
    "Live/UiSpriteAssets/LiveJudgementSpriteAsset",
]


def settings(master: Path | None, options: LiveOptions = LiveOptions()) -> dict:
    """The live directory's notes.json `settings`: option defaults and master values the note runtime reads; with
    `options` also the offered note skins (`skins`: NoteDesignId -> MasterLiveNoteSkin._assetName) and effect sets
    (`effects`: NoteEffectId -> MasterLiveNoteEffectSkin._assetName; also when only other qualities are offered,
    whose effect set name the player derives from it), and with its option tables the items of PLAYER_ITEMS as
    well."""
    from .score import master_table

    out: dict = {"skin": DEFAULT_SKIN, "effect": DEFAULT_EFFECT, **_lane_settings()}
    if master is None:
        return out
    items = OPTION_ITEMS
    if options.tables:
        items = {**OPTION_ITEMS, **{k: OPTION_ITEM_TYPES[k] for k in PLAYER_ITEMS}}
    opts = {}
    for r in master_table(master, "MasterOptionDefault"):
        if r["_presetId"] == 1 and r["_optionItemType"] in items:
            opts[items[r["_optionItemType"]]] = r["_valueString"]
    out["optionDefaults"] = opts
    out["optionRanges"] = {
        items.get(r["_optionItemType"], r["_optionItemType"]): [r["_minValue"], r["_maxValue"]]
        for r in master_table(master, "MasterOptionRange")
        if r["_optionItemType"] in items
    }
    ls = {r["_key"]: r["_value"] for r in master_table(master, "MasterLiveSettings")}
    out["liveSettings"] = {k: ls[k] for k in LIVE_SETTING_KEYS if k in ls}
    skins = {r["_id"]: r["_assetName"] for r in master_table(master, "MasterLiveNoteSkin")}
    effects = {r["_id"]: r["_assetName"] for r in master_table(master, "MasterLiveNoteEffectSkin")}
    out["skin"] = skins[int(opts["NoteDesignId"])]
    out["effect"] = effects[int(opts["NoteEffectId"])]
    out["judgementSprites"] = {
        r["_noteSimulateJudgement"]: r["_spriteName"] for r in master_table(master, "MasterLiveJudgementSprite")
    }
    out["noteDisplayTimeMs"] = display_time_ms(
        float(opts["NoteSpeed"]),
        float(ls["note_speed_min"]),
        float(ls["note_speed_max"]),
        float(ls["note_speed_view_min"]),
        float(ls["note_speed_view_max"]),
    )
    if options.designs:
        out["skins"] = {str(i): skins[i] for i in options.designs}
    if options.effects or options.qualities:
        out["effects"] = {str(i): effects[i] for i in options.values("NoteEffectId")}
    return out


# UI subtrees of [EmbScene/Live] animated by exported clips (combo counter, judgement text); used only to name
# clip binding paths, the scene itself is exported by livescene.
SCENE_KEY = "EmbScene/Live"
SCENE_SUBTREES = ("UILiveCombo", "UILiveJudgement")
# The bar line view (MeasureLineDisplay): the scene's LiveAllNoteView -> _barLineView (LiveAllBarLineView) ->
# _container (LiveBarLineViewContainer) -> _elementPrefab (LiveBarLineView, the prefab the container instantiates)
NOTE_VIEW = ("LiveGameView/LiveGameCamera/screen_root/LiveGameAllNoteView", "LiveAllNoteView")
BAR_LINE_CHAIN = (
    ("_barLineView", "LiveAllBarLineView"),
    ("_container", "LiveBarLineViewContainer"),
    ("_elementPrefab", "LiveBarLineView"),
)
BAR_LINE_VIEW = "bar_line_view"


TMP_FONT_NOTE = (
    "not exported: the note highway draws no TextMeshPro text with default options (the judge view's "
    "fast / slow and assist texts stay hidden)"
)


def _drop_tmp_fonts(doc: dict, root: Path) -> list[str]:
    """Reduce the font asset and material of every TextMeshProUGUI record to a name stub and delete the texture
    files no record references any more (the TMP font atlases). Returns the deleted files."""

    def walk(v):
        if isinstance(v, dict):
            if v.get("class") == "TextMeshProUGUI" and "m_fontAsset" in v:
                fa, mat = v["m_fontAsset"], v.get("m_sharedMaterial")
                if isinstance(fa, dict):
                    v["m_fontAsset"] = {"asset": fa.get("asset"), "name": fa.get("name"), "note": TMP_FONT_NOTE}
                if isinstance(mat, dict):
                    v["m_sharedMaterial"] = {
                        "material": mat.get("material"),
                        "shader": mat.get("shader"),
                        "note": TMP_FONT_NOTE,
                    }
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(doc)
    used: set[str] = set()

    def refs(v):
        if isinstance(v, dict):
            t = v.get("texture")
            if isinstance(t, str):
                used.add(t)
            for x in v.values():
                refs(x)
        elif isinstance(v, list):
            for x in v:
                refs(x)

    refs(doc)
    dropped = []
    for f in sorted((root / "textures").glob("*.png")):
        rel = f"textures/{f.name}"
        if rel not in used:
            f.unlink()
            dropped.append(rel)
    return dropped


def effect_keys(cat: Catalog, effect: str) -> list[str]:
    """The catalog keys of note effect set `effect` and of the lane effects (KEY_PREFIXES), in prefix and catalog
    order."""
    keys: list[str] = []
    for p in KEY_PREFIXES:
        keys += [k for k in cat.keys(p.format(effect=effect)) if k not in keys]
    return keys


def _scene_mono(ex: Exporter, o, cls: str):
    if o is None or o.type.name != "MonoBehaviour" or ex.script_class(o) != cls:
        raise RuntimeError(f"expected a {cls} MonoBehaviour, got {o and o.type.name}")
    return o, o.read_typetree()


def bar_line_prefab(ex: Exporter) -> dict:
    """The bar line view as a node list: the LiveBarLineView prefab the live scene's bar line container
    instantiates (BAR_LINE_CHAIN from the scene's LiveAllNoteView), its root first."""
    env, graph = ex.load(SCENE_KEY)
    path, cls = NOTE_VIEW
    gos = set(graph.gos_at_path(path))
    comps = [c["component"] for go in gos for c in graph.go[go]["m_Component"]]
    owners = [o for o in env.objects if o.type.name == "GameObject" and o.path_id in gos]
    if len(gos) != 1 or len(owners) != 1:
        raise RuntimeError(f"{SCENE_KEY}: {len(gos)} GameObjects at {path}")
    hits = [
        c
        for c in (ex.deref(owners[0], p) for p in comps)
        if c is not None and c.type.name == "MonoBehaviour" and ex.script_class(c) == cls
    ]
    if len(hits) != 1:
        raise RuntimeError(f"{SCENE_KEY}: {len(hits)} {cls} at {path}")
    o, tt = _scene_mono(ex, hits[0], cls)
    for field, want in BAR_LINE_CHAIN:
        o, tt = _scene_mono(ex, ex.deref(o, tt[field]), want)
    root = graph.tf_of_go.get(tt["m_GameObject"]["m_PathID"])
    if root is None or graph.tf[root]["m_Father"]["m_PathID"]:
        raise RuntimeError(f"{SCENE_KEY}: the bar line view is not a prefab root of the scene's bundles")
    return {
        "key": SCENE_KEY,
        "reference": ".".join(f for f, _ in BAR_LINE_CHAIN),
        "nodes": ex.hierarchy(env, graph, root),
    }


def extract(
    cat: Catalog, player: PlayerData, out_dir: Path, master: Path | None = None, options: LiveOptions = LiveOptions()
) -> dict:
    """livenotes/ for the default options, plus the files of the variants `options` offers (the module
    docstring). The variants' objects are exported after the default ones."""
    from ..unity.export import Exporter

    out_dir = Path(out_dir)
    root = out_dir / "livenotes"
    root.mkdir(parents=True, exist_ok=True)
    ex = Exporter(cat, root, player=player, clip_format="livenotes", follow=("AnimatorController", "SpriteAtlas"))
    doc: dict = {"settings": settings(master, options)}
    skin = doc["settings"]["skin"]

    # note / line prefabs first: their hierarchies resolve clip binding paths of skin/effect assets
    doc["prefabs"] = {name: ex.export_key(key) for name, key in PREFABS.items()}
    doc["noteSkin"] = ex.export_key(skin_key(skin))
    keys = effect_keys(cat, doc["settings"]["effect"])
    keys += [k for k in SINGLE_KEYS if cat.has(k) and k not in keys]
    assets, failures = {}, {}

    def export(ks):
        for k in ks:
            try:
                assets[k] = ex.export_key(k)
            except NotImplementedError as e:
                failures[k] = str(e)

    export(keys)
    other_skins = [n for n in doc["settings"].get("skins", {}).values() if n != skin]
    if other_skins:
        doc["noteSkins"] = {n: ex.export_key(skin_key(n)) for n in other_skins}
    names = {int(i): n for i, n in doc["settings"].get("effects", {}).items()}
    for effect, light in options.effect_sets(names):
        if not cat.has(effect_settings_key(effect)):
            if light:
                continue  # the game loads the set itself at quality Low (LiveOptions.effect_sets)
            raise KeyError(f"effect set {effect}: {effect_settings_key(effect)} not in the catalog")
        export([k for k in effect_keys(cat, effect) if k not in assets and k not in failures])
    if options.bar_lines:
        doc["prefabs"][BAR_LINE_VIEW] = bar_line_prefab(ex)
    doc["assets"] = assets
    ex.add_path_source(SCENE_KEY, SCENE_SUBTREES)
    unresolved = ex.resolve_pending()
    doc["clips"] = ex.clips
    doc["controllers"] = ex.controllers
    doc["exportFailures"] = failures

    dropped = _drop_tmp_fonts(doc, root)
    _, shader_summary = ex.dump_shaders(root / "shaders")
    doc["shaders"] = {"index": "shaders/shaders.json", "names": shader_summary["names"]}
    doc["convention"] = (
        "Unity space (left-handed, Y up); transforms are local TRS; quaternions (x,y,z,w); "
        "colours as serialized (project colour space: see player.json)"
    )
    write_json(root / "notes.json", doc)
    return {
        "prefabs": len(doc["prefabs"]),
        "assets": len(assets),
        "clips": len(ex.clips),
        "controllers": len(ex.controllers),
        "textures": len(ex.textures) - len(dropped),
        "droppedFontTextures": dropped,
        "failures": failures,
        "unresolvedBindings": unresolved,
        **shader_summary,
    }
