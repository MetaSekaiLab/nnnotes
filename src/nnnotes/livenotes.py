"""Live note / effect runtime data -> livenotes/notes.json + textures + shaders.

Everything spawned or animated per note in the in-play note highway:
note view prefabs (tap, flick +left/right/direction,
trace, slide/slide_end/connection, guide, slide_combo(+skip), none), long-note
body prefabs (slide_line_view, guide_line_view), the pair line prefab,
the note skin asset (sprites per note type/width/tilt), the note-effect skin settings
of the NoteEffectId option and the lane-effect settings (set effect001) with every effect
prefab they reference (particle systems exported with all module data), the judgement text view and
the judgement / combo sprite assets, the combo animator, the note animation controllers
and clips they reference, and the option / master values the motion formulas read.
Only what the default options load: the effect set of NoteEffectId (307) at quality Middle
(the `<name>Light` set is Low only, `NoteSkinLoadStep.GetLightQualityAddress`; the
`<name>Simple` sets are not used with default options); no bar / skill line prefabs
(MeasureLineDisplay 109 / LiveSkillActivationPositionDisplay 309 FALSE). With liveoptions.LiveOptions
also: the other offered note skins (`noteSkins`, `settings.skins`), the other offered note effect sets
and, with LiveQuality Low offered, their `Light` variants where the catalog has them (in `assets`,
`settings.effects`), with MeasureLineDisplay offered the bar line view (`prefabs.bar_line_view`: the
LiveBarLineView prefab the live scene's LiveBarLineViewContainer instantiates, its _elementPrefab), and the
option tables (`settings.optionDefaults` / `optionRanges` of every item the player offers).

Objects go through export.Exporter in its "livenotes" clip format (clips and animator
controllers in registries referenced by id; binding paths and attributes left as
"crc32:<n>" until a pass names them), following references to AnimatorControllers
and SpriteAtlases. Clip binding paths that point into the Live scene's UI subtrees
are named from that scene (`add_path_source`), which is not exported here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .catalog import Catalog
from .export import Exporter
from .jsonio import write_json
from .liveoptions import OPTION_ITEM_TYPES, PLAYER_ITEMS, LiveOptions
from .livescene import LANE_COUNT, LANE_VIEW
from .player import PlayerData
from .score import master_table

# --- keys ------------------------------------------------------------------
NOTE_VIEWS = ["tap", "flick", "flick_left", "flick_right", "trace", "slide", "slide_end",
              "connection", "guide", "slide_combo", "slide_combo_skip", "none"]
PREFABS = {f"{v}_note_view": f"EmbLive/Prefabs/LiveGame/{v}_note_view" for v in NOTE_VIEWS}
PREFABS.update({
    "slide_line_view": "EmbLive/Prefabs/LiveGame/slide_line_view",
    "guide_line_view": "EmbLive/Prefabs/LiveGame/guide_line_view",
    "pair_note_line": "EmbLive/Prefabs/LiveGame/PairNoteLine/LiveGamePairNoteView",
    "note_groove_loop": "EmbLive/Prefabs/LiveGame/Effect/note_groove_loop",
    "judge_effect_view": "EmbLive/Prefabs/UILiveNoteJudgeEffectView",
})

# --- defaults (fresh profile) -------------------------------------------------
# MasterOptionDefault preset 1 (CurrentPresetIndex 0 -> Preset1); item types from App.Options.OptionItemType.
OPTION_ITEMS = {k: OPTION_ITEM_TYPES[k] for k in (1, 4, 6, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                                                  112, 113, 117, 119, 200, 205, 206, 207, 208, 300, 301, 302,
                                                  305, 306, 307, 308, 309, 310)}
LIVE_SETTING_KEYS = ["note_speed_min", "note_speed_max", "note_speed_view_min", "note_speed_view_max",
                     "note_slide_combo_rhythmic_unit", "stop_in_vain_time_ms_diff",
                     "slide_offset_min_note_lane_width", "slide_offset_max_note_lane_width",
                     "note_overlap_lane_buffer"]
DEFAULT_SKIN = "skin001"          # MasterLiveNoteSkin._id 1 (NoteDesignId default 1)
DEFAULT_EFFECT = "effect001"      # MasterLiveNoteEffectSkin._id 1 (NoteEffectId default 1)

# Catalog key families exported whole (every key under the prefix): the note effect settings of the NoteEffectId
# option's set ({effect}; LiveAddressablePath.GetLiveNoteEffectSkinAssetPath) and the lane effect settings, whose
# set is always LANE_EFFECT (LaneEffectLoadStep.GetAssetPath passes that constant to
# LiveAddressablePath.GetLiveLaneEffectSkinAssetPath), with the effect prefabs they reference by GameObject
LANE_EFFECT = "effect001"
KEY_PREFIXES = [
    "Effect/Live/NoteEffect/{effect}/",
    f"Effect/Live/LaneEffect/{LANE_EFFECT}/",
]
SINGLE_KEYS = [
    "EmbLive/Animation/Combo/UILiveCombo",              # UIComboCounterView's animator controller
    "Live/UiSpriteAssets/LiveComboSpriteAsset",
    "Live/UiSpriteAssets/LiveJudgementSpriteAsset",
]


def settings(master: Path | None, options: LiveOptions = LiveOptions()) -> dict:
    """Option defaults and master values the note runtime reads; with `options` also the offered note skins
    (`skins`: NoteDesignId -> MasterLiveNoteSkin._assetName) and effect sets (`effects`: NoteEffectId ->
    MasterLiveNoteEffectSkin._assetName; also when only other qualities are offered, whose effect set name the
    player derives from it), and with its option tables the items of PLAYER_ITEMS as well."""
    out: dict = {"skin": DEFAULT_SKIN, "effect": DEFAULT_EFFECT, "laneCount": LANE_COUNT,
                 "laneSize": list(LANE_VIEW["size"]), "laneTopRange": LANE_VIEW["topRange"],
                 "laneBottomRange": LANE_VIEW["bottomRange"],
                 "judgementScreenBottomPosition": LANE_VIEW["judgementScreenBottomPosition"],
                 "laneTopPosition": LANE_VIEW["topPosition"],
                 "noteViewProgress": {"base": 1.065, "exponentScale": 45.0},
                 "tiltCenterLane": 11.5}
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
    out["optionRanges"] = {items.get(r["_optionItemType"], r["_optionItemType"]): [r["_minValue"], r["_maxValue"]]
                           for r in master_table(master, "MasterOptionRange") if r["_optionItemType"] in items}
    ls = {r["_key"]: r["_value"] for r in master_table(master, "MasterLiveSettings")}
    out["liveSettings"] = {k: ls[k] for k in LIVE_SETTING_KEYS if k in ls}
    skins = {r["_id"]: r["_assetName"] for r in master_table(master, "MasterLiveNoteSkin")}
    effects = {r["_id"]: r["_assetName"] for r in master_table(master, "MasterLiveNoteEffectSkin")}
    out["skin"] = skins[int(opts["NoteDesignId"])]
    out["effect"] = effects[int(opts["NoteEffectId"])]
    out["judgementSprites"] = {r["_noteSimulateJudgement"]: r["_spriteName"]
                               for r in master_table(master, "MasterLiveJudgementSprite")}
    out["noteDisplayTimeMs"] = display_time_ms(float(opts["NoteSpeed"]), float(ls["note_speed_min"]),
                                               float(ls["note_speed_max"]), float(ls["note_speed_view_min"]),
                                               float(ls["note_speed_view_max"]))
    if options.designs:
        out["skins"] = {str(i): skins[i] for i in options.designs}
    if options.effects or options.qualities:
        out["effects"] = {str(i): effects[i] for i in options.values("NoteEffectId")}
    return out


def display_time_ms(speed: float, smin: float, smax: float, vmin: float, vmax: float) -> int:
    """NoteBeforePlayingTimeGetter.GetNoteDisplayOffsetTimeMs, in float32."""
    f = np.float32
    t = max(f(0), f(speed) - f(smin)) / (f(smax) - f(smin))
    t = min(max(t, f(0)), f(1))
    e = f(1) - f(np.power(f(1) - t, f(1.31)))
    e = min(max(e, f(0)), f(1))
    return int(f((f(vmin) + (f(vmax) - f(vmin)) * e) * f(1000)))


# UI subtrees of [EmbScene/Live] animated by exported clips (combo counter, judgement text); used only to name
# clip binding paths, the scene itself is exported by livescene.
SCENE_KEY = "EmbScene/Live"
SCENE_SUBTREES = ("UILiveCombo", "UILiveJudgement")
# The bar line view (MeasureLineDisplay): the scene's LiveAllNoteView -> _barLineView (LiveAllBarLineView) ->
# _container (LiveBarLineViewContainer) -> _elementPrefab (LiveBarLineView, the prefab the container instantiates)
NOTE_VIEW = ("LiveGameView/LiveGameCamera/screen_root/LiveGameAllNoteView", "LiveAllNoteView")
BAR_LINE_CHAIN = (("_barLineView", "LiveAllBarLineView"), ("_container", "LiveBarLineViewContainer"),
                  ("_elementPrefab", "LiveBarLineView"))
BAR_LINE_VIEW = "bar_line_view"


TMP_FONT_NOTE = ("not exported: the note highway draws no TextMeshPro text with default options (the judge view's "
                 "fast / slow and assist texts stay hidden)")


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
                    v["m_sharedMaterial"] = {"material": mat.get("material"), "shader": mat.get("shader"),
                                             "note": TMP_FONT_NOTE}
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


def skin_key(name: str) -> str:
    """The note skin asset of MasterLiveNoteSkin._assetName `name` (LiveAddressablePath.GetLiveNoteSkinAssetPath)."""
    return f"Live/Note/{name}/LiveNoteSkinAsset"


def effect_settings_key(effect: str) -> str:
    """The note effect settings asset of effect set `effect` (LiveAddressablePath.GetLiveNoteEffectSkinAssetPath)."""
    return f"Effect/Live/NoteEffect/{effect}/LiveNoteEffectAssetSettings"


def effect_keys(cat: Catalog, effect: str) -> list[str]:
    """The catalog keys of note effect set `effect` and of the lane effects (KEY_PREFIXES), in prefix and catalog
    order."""
    keys: list[str] = []
    for p in KEY_PREFIXES:
        keys += [k for k in cat.keys(p.format(effect=effect)) if k not in keys]
    return keys


def _mono(ex: Exporter, o, cls: str):
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
    hits = [c for c in (ex.deref(owners[0], p) for p in comps)
            if c is not None and c.type.name == "MonoBehaviour" and ex.script_class(c) == cls]
    if len(hits) != 1:
        raise RuntimeError(f"{SCENE_KEY}: {len(hits)} {cls} at {path}")
    o, tt = _mono(ex, hits[0], cls)
    for field, want in BAR_LINE_CHAIN:
        o, tt = _mono(ex, ex.deref(o, tt[field]), want)
    root = graph.tf_of_go.get(tt["m_GameObject"]["m_PathID"])
    if root is None or graph.tf[root]["m_Father"]["m_PathID"]:
        raise RuntimeError(f"{SCENE_KEY}: the bar line view is not a prefab root of the scene's bundles")
    return {"key": SCENE_KEY, "reference": ".".join(f for f, _ in BAR_LINE_CHAIN),
            "nodes": ex.hierarchy(env, graph, root)}


def extract(cat: Catalog, player: PlayerData, out_dir: Path, master: Path | None = None,
            options: LiveOptions = LiveOptions()) -> dict:
    """livenotes/ for the default options, plus the files of the variants `options` offers (the module
    docstring). The variants' objects are exported after the default ones."""
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
                continue                  # the game loads the set itself at quality Low (LiveOptions.effect_sets)
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
    doc["convention"] = ("Unity space (left-handed, Y up); transforms are local TRS; quaternions (x,y,z,w); "
                         "colours as serialized (project colour space: see player.json)")
    write_json(root / "notes.json", doc)
    return {"prefabs": len(doc["prefabs"]), "assets": len(assets), "clips": len(ex.clips),
            "controllers": len(ex.controllers), "textures": len(ex.textures) - len(dropped), "droppedFontTextures": dropped,
            "failures": failures, "unresolvedBindings": unresolved, **shader_summary}
