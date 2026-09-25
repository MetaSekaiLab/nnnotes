"""Live note / effect runtime data -> livenotes/notes.json + textures + shaders.

Everything spawned or animated per note in the in-play note highway:
note view prefabs (tap, flick +left/right/direction,
trace, slide/slide_end/connection, guide, slide_combo(+skip), none), long-note
body prefabs (slide_line_view, guide_line_view), the pair line prefab,
the note skin asset (sprites per note type/width/tilt), the note-effect and
lane-effect skin settings of the NoteEffectId option with every effect prefab they
reference (particle systems exported with all module data), the judgement text view and
the judgement / combo sprite assets, the combo animator, the note animation controllers
and clips they reference, and the option / master values the motion formulas read.
Only what the default options load: the effect set of NoteEffectId (307) at quality Middle
(the `<name>Light` set is Low only, `NoteSkinLoadStep.GetLightQualityAddress`; the
`<name>Simple` sets are not used with default options and not exported); no bar /
skill line prefabs (MeasureLineDisplay 109 / LiveSkillActivationPositionDisplay 309 FALSE).

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
from .livescene import LANE_COUNT, LANE_VIEW, OPTION_ITEM_TYPES
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

# Catalog key families exported whole (every key under the prefix; {effect} = the NoteEffectId option's set):
# the note / lane effect settings and the effect prefabs they reference by GameObject
KEY_PREFIXES = [
    "Effect/Live/NoteEffect/{effect}/",
    "Effect/Live/LaneEffect/{effect}/",
]
SINGLE_KEYS = [
    "EmbLive/Animation/Combo/UILiveCombo",              # UIComboCounterView's animator controller
    "Live/UiSpriteAssets/LiveComboSpriteAsset",
    "Live/UiSpriteAssets/LiveJudgementSpriteAsset",
]


def settings(master: Path | None) -> dict:
    """Option defaults and master values the note runtime reads."""
    out: dict = {"skin": DEFAULT_SKIN, "effect": DEFAULT_EFFECT, "laneCount": LANE_COUNT,
                 "laneSize": list(LANE_VIEW["size"]), "laneTopRange": LANE_VIEW["topRange"],
                 "laneBottomRange": LANE_VIEW["bottomRange"],
                 "judgementScreenBottomPosition": LANE_VIEW["judgementScreenBottomPosition"],
                 "laneTopPosition": LANE_VIEW["topPosition"],
                 "noteViewProgress": {"base": 1.065, "exponentScale": 45.0},
                 "tiltCenterLane": 11.5}
    if master is None:
        return out
    opts = {}
    for r in master_table(master, "MasterOptionDefault"):
        if r["_presetId"] == 1 and r["_optionItemType"] in OPTION_ITEMS:
            opts[OPTION_ITEMS[r["_optionItemType"]]] = r["_valueString"]
    out["optionDefaults"] = opts
    out["optionRanges"] = {OPTION_ITEMS.get(r["_optionItemType"], r["_optionItemType"]): [r["_minValue"], r["_maxValue"]]
                           for r in master_table(master, "MasterOptionRange") if r["_optionItemType"] in OPTION_ITEMS}
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


def extract(cat: Catalog, player: PlayerData, out_dir: Path, master: Path | None = None) -> dict:
    out_dir = Path(out_dir)
    root = out_dir / "livenotes"
    root.mkdir(parents=True, exist_ok=True)
    ex = Exporter(cat, root, player=player, clip_format="livenotes", follow=("AnimatorController", "SpriteAtlas"))
    doc: dict = {"settings": settings(master)}
    skin = doc["settings"]["skin"]

    # note / line prefabs first: their hierarchies resolve clip binding paths of skin/effect assets
    doc["prefabs"] = {name: ex.export_key(key) for name, key in PREFABS.items()}
    doc["noteSkin"] = ex.export_key(f"Live/Note/{skin}/LiveNoteSkinAsset")
    keys = []
    for p in KEY_PREFIXES:
        keys += [k for k in cat.keys(p.format(effect=doc["settings"]["effect"])) if k not in keys]
    keys += [k for k in SINGLE_KEYS if cat.has(k) and k not in keys]
    assets, failures = {}, {}
    for k in keys:
        try:
            assets[k] = ex.export_key(k)
        except NotImplementedError as e:
            failures[k] = str(e)
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
