"""Live start canvas (Live/UILiveStartCanvas, LightWeight mode) -> <live>/liveui/ (liveui.json, textures/).

livescene/scene.json already carries the canvas hierarchy with every component (serialized TMP texts, inline
sprite records, the start timeline and its UILiveStartCanvasTrack clips) and livescene/shaders the shaders it
draws with. This module adds what a renderer needs beyond that to draw the canvas the way
UILiveStartCanvas.Initialize leaves it:

  strings     the texts Initialize sets: music title, singer, the credit lines (LocalizeText master text id +
              replacement word), music level, high score, resolved as the game resolves them
  texts       per TMP text of the drawn part: the LocalizeText font / material / line spacing overrides
  fonts       TMP font assets reduced to the characters shown (tmpfont); FZLTH's runtime glyphs generated from
              its source font; VibeMOPro's glyph pair adjustment lookup (kerning)
  materials   the localized TMP materials ("<font> - Default") and the fonts' default materials
  sprites     WhiteRect, Songtitle_base, the Difficulty_* icons of _difficultyPairs and the music jacket; their
              texel blocks copied into textures/ (export.TexelPacker, deferred-texture mode)
  controller  the root Animator's controller (evidence only: the start timeline drives the canvas)

Texts and fonts follow the client language `language` (a catalog language code, languages.LANGUAGES): its
LanguageMode selects the text table column, the LocalizeManager fonts and the line spacing.
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import Catalog
from .export import Exporter, TexelPacker, _safe
from .jsonio import write_json
from .player import PlayerData
from .score import master_table
from . import languages, tmpfont
from .tmpfont import LANGUAGE_LINE_SPACING

SCENE_KEY = "EmbScene/Live"
CANVAS = "Live/UILiveStartCanvas"
CONTENT = f"{CANVAS}/root/UISafeArea/ContentArea"
# Parts drawn in LightWeight mode: root/base (a _simpleModeOnlyObjects entry), center_bottom and
# simple_music_jacket_panel (left_top is _normalModeOnlyObjects, right_top/battle_panel is _battleLiveGameObjects).
DRAWN = (f"{CANVAS}/root/base", f"{CONTENT}/center_bottom", f"{CONTENT}/simple_music_jacket_panel")
JACKET_KEY = "Image/Jacket/{jacket}"
# App.LiveBase.LiveDifficulty
LIVE_DIFFICULTY = {"easy": 0, "normal": 1, "hard": 2, "expert": 3, "master": 4}
TMP_STUBS = ("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet")
TMP_CLASS = "TextMeshProUGUI"
JAPANESE_COLUMN = languages.column("ja")


def _drawn(path: str) -> bool:
    return any(path == d or path.startswith(d + "/") for d in DRAWN)


def _comp(node: dict, cls: str) -> dict | None:
    hit = [c for c in node["components"] if c.get("class", c["type"]) == cls]
    if len(hit) > 1:
        raise RuntimeError(f"{node['path']}: {len(hit)} {cls}")
    return hit[0] if hit else None


def resolve_strings(master: Path, music_id: int, difficulty: str, column: str) -> tuple[dict, dict]:
    """The strings UILiveStartCanvas.Initialize sets (IReadOnlyLiveMusicScore slots 2..8), for a fresh profile, with
    the text table column `column` of the current LanguageMode. -> (strings, sources)."""
    music = next(r for r in master_table(master, "MasterLiveMusic") if r["_id"] == music_id)
    text = {r["_id"]: r for r in master_table(master, "MasterText")}

    def localized(tid: str) -> str:
        # MasterLoader.GetLocalizedText in the current LanguageMode: the column as it is, "" included (no
        # fallback language); LocalizedTextTokenResolver.Resolve returns an empty text unchanged
        return text[tid][column]

    def word(tid: str) -> str:
        # MasterLiveMusic.get_LyricistText / get_ComposerText / get_ArrangerText
        # -> LocalizeManager.GetLocalizedText: an empty id is returned as it is, else the loader's text
        return localized(tid) if tid else tid

    def credit(label_id: str, word_id: str) -> str:
        # LocalizeText.RegisterReplacementWord("{0}", word) stores the word and re-runs SetMasterTextId: an empty
        # label text goes to the text as it is, otherwise String.Replace(key, word) for each registered word
        # (ordinal, every occurrence; an empty word leaves "編曲:"). LocalizedTextTokenResolver.Resolve knows no
        # "{0}" token. Initialize and FitUI test no text for emptiness.
        label = localized(label_id)
        return label.replace("{0}", word(word_id)) if label else label

    # SongTitleDisplaySetting._useTranslation false (option 999 unset, GetBool default false):
    # MasterLiveMusic.TitleText = loader.Get(_titleTextID, Japanese)
    title = text[music["_titleTextID"]][JAPANESE_COLUMN]
    # GetBandNameTextId: _bandNameTextID, else MasterBand(_bandIDs[0])._nameTextID
    band_tid = music["_bandNameTextID"]
    if not band_tid:
        band = next(r for r in master_table(master, "MasterBand") if r["_id"] == music["_bandIDs"][0])
        band_tid = band["_nameTextID"]
    score_id = music[f"_{difficulty}ID"]
    score = next(r for r in master_table(master, "MasterLiveMusicScore") if r["_id"] == score_id)
    strings = {
        "musicTitle": title,
        "singerName": localized(band_tid),
        "lyricsWriter": credit("ui_credit_lyrics", music["_lyricistTextID"]),
        "composer": credit("ui_credit_composer", music["_composerTextID"]),
        "arranger": credit("ui_credit_arranger", music["_arrangerTextID"]),
        # MasterLiveMusicScore.get_MusicScoreLevel (_musicScoreLevel), UIText.SetText(int)
        "musicLevel": str(int(score["_musicScoreLevel"])),
        # Player.GetLiveMusicResult: a fresh profile has no entry -> new LiveMusicResult(id), get_HighScore = 0.
        # The IFix patch checks inside these getters (patch ids 0x13e0, 0x1b5b) depend on patches loaded at run
        # time; the code assumes none is loaded
        "highScore": "0",
    }
    sources = {
        "musicTitle": {"textId": music["_titleTextID"], "column": JAPANESE_COLUMN,
                       "why": "SongTitleDisplaySetting._useTranslation false (option 999 default)"},
        "singerName": {"textId": band_tid, "column": column},
        "lyricsWriter": {"label": "ui_credit_lyrics", "word": music["_lyricistTextID"], "column": column},
        "composer": {"label": "ui_credit_composer", "word": music["_composerTextID"], "column": column},
        "arranger": {"label": "ui_credit_arranger", "word": music["_arrangerTextID"], "column": column},
        "musicLevel": {"MasterLiveMusicScore": score_id, "field": "_musicScoreLevel"},
        "highScore": {"why": "fresh profile: no LiveMusicResult entry, HighScore 0"},
    }
    for key in ("lyricsWriter", "composer", "arranger"):
        if not word(sources[key]["word"]):
            sources[key]["emptyWord"] = True        # the label is shown without the word
    return strings, sources


# UILiveStartCanvas field -> strings key (UIText fields and arrays set by Initialize)
TEXT_FIELDS = {"_musicTitleText": "musicTitle", "_singerText": "singerName", "_lyricistText": "lyricsWriter",
               "_compositterText": "composer", "_compositterText2": "composer", "_arrangerText": "arranger",
               "_musicLevelTexts": "musicLevel", "_highScoreTexts": "highScore"}


def extract(cat: Catalog, player: PlayerData, out_dir: Path, master: Path, music_id: int,
            difficulty: str, *, language: str) -> dict:
    """Write <out_dir>/liveui/ (liveui.json, textures/) for one live and return a summary. Needs
    <out_dir>/livescene/shaders/shaders.json (livescene.extract) for the shader check. `language`: the client
    language (a languages.LANGUAGES code)."""
    out_dir = Path(out_dir)
    base = out_dir / "liveui"
    base.mkdir(parents=True, exist_ok=True)
    if difficulty not in LIVE_DIFFICULTY:
        raise KeyError(f"difficulty {difficulty}")
    mode, column = languages.mode(language), languages.column(language)
    ex = Exporter(cat, base, player=player, textures="deferred", stub_assets=TMP_STUBS,
                  follow=("AnimatorController",))
    packer = TexelPacker(base)

    # -- the canvas subtree of the scene (deferred: sprite / texture references) -------------------------
    env, graph = ex.load(SCENE_KEY)
    roots = [p for p in graph.tf if graph.path(p) == CANVAS]
    if len(roots) != 1:
        raise RuntimeError(f"{CANVAS}: {len(roots)} transforms")
    nodes = ex.hierarchy(env, graph, roots[0])
    by_path: dict[str, list] = {}
    for n in nodes:
        by_path.setdefault(n["path"], []).append(n)
    canvas = _comp(by_path[CANVAS][0], "UILiveStartCanvas")
    if canvas is None:
        raise RuntimeError(f"{CANVAS}: no UILiveStartCanvas")

    # -- strings, and which texts Initialize writes them to ---------------------------------------------
    strings, sources = resolve_strings(master, music_id, difficulty, column)
    text_of: dict[str, str] = {}
    for field, key in TEXT_FIELDS.items():
        refs = canvas[field] if isinstance(canvas[field], list) else [canvas[field]]
        for r in refs:
            text_of[r["gameObject"]] = strings[key]

    # -- TMP texts of the drawn part: LocalizeText overrides and the characters they show -------------
    lang = tmpfont.language_fonts(player)
    swap = tmpfont.font_swap(lang, mode)
    fonts = tmpfont.FontSet(ex)
    texts: dict[str, dict] = {}
    chars_by_font: dict[str, set] = {}
    for n in nodes:
        if not _drawn(n["path"]):
            continue
        t = _comp(n, TMP_CLASS)
        if t is None:
            continue
        if len(by_path[n["path"]]) != 1:
            raise RuntimeError(f"{n['path']}: text node path not unique")
        lt = _comp(n, "LocalizeText")
        if not (lt and lt["m_Enabled"] and lt["_localizeEnabled"]):
            raise NotImplementedError(f"{n['path']}: text without an enabled LocalizeText")
        loc = tmpfont.localize_text(n["path"], t["m_fontAsset"]["name"], t["m_sharedMaterial"]["material"],
                                    swap, lang, mode)
        shown = text_of.get(n["path"], t["m_text"])
        texts[n["path"]] = {"localized": loc, "text": shown, "setByInitialize": n["path"] in text_of}
        chars_by_font.setdefault(loc["fontAsset"], set()).update(ord(ch) for ch in shown)
    for p in text_of:
        if _drawn(p) and p not in texts:
            raise RuntimeError(f"{p}: Initialize writes a text that is not exported")

    coverage: dict[str, dict] = {}
    needed: dict[str, set] = {}
    runtime_units: dict[str, set] = {}
    primaries = set()
    for fname in sorted(chars_by_font):
        primary = fonts.by_key(f"{tmpfont.FONT_PREFIX}{fname[:-4]}/{fname}")
        primaries.add(primary.name)
        cov, nd, rt = tmpfont.glyph_coverage(fonts, primary, sorted(chars_by_font[fname]))
        if cov["missing"]:
            raise RuntimeError(f"{fname}: characters {cov['missing']} missing from the font chain")
        coverage[fname] = cov
        for k, v in nd.items():
            needed.setdefault(k, set()).update(v)
        for k, v in rt.items():
            runtime_units.setdefault(k, set()).update(v)
    materials, text_mats = tmpfont.text_materials(ex, fonts, [t["localized"] for t in texts.values()],
                                                  primaries, needed, runtime_units)
    font_out = tmpfont.export_fonts(ex, fonts, primaries, needed, runtime_units, text_mats, packer,
                                    "characters shown by the live start canvas (plus baked control characters); "
                                    "runtime characters generated from the source font (runtimeGlyphs)")

    # -- sprites: drawn Images, the difficulty icons, the jacket ----------------------------------------
    replaced = {r["gameObject"] for f in ("_jacketImages", "_difficultyImages") for r in canvas[f]}
    sprite_refs: dict[str, str] = {}               # sprite name -> spriteRef
    for n in nodes:
        im = _comp(n, "Image")
        if not _drawn(n["path"]) or im is None or n["path"] in replaced or not im["m_Sprite"]:
            continue
        sprite_refs.setdefault(im["m_Sprite"]["name"], im["m_Sprite"]["spriteRef"])
    difficulty_icons = {}
    for p in canvas["_difficultyPairs"]:
        s = p["Icon"]
        difficulty_icons[str(p["Difficulty"])] = s["name"]
        sprite_refs.setdefault(s["name"], s["spriteRef"])
    music = next(r for r in master_table(master, "MasterLiveMusic") if r["_id"] == music_id)
    jacket = ex.key_sprite(JACKET_KEY.format(jacket=music["_jacketAssetName"]))
    sprite_refs.setdefault(jacket["name"], jacket["spriteRef"])
    for name, ref in sorted(sprite_refs.items()):
        s = ex.sprite_recs[ref]
        if s["name"] != name:
            raise RuntimeError(f"sprite {name}: record named {s['name']}")
        ex.pack_sprite(ref, packer, f"sprites_{_safe(s['tex'].read_typetree()['m_Name'])}")

    textures = packer.build()
    tmpfont.resolve_font_blocks(font_out)
    sprites_out = {name: ex.packed_sprite(ref) for name, ref in sorted(sprite_refs.items())}
    for m in materials.values():
        for v in m["textures"].values():
            if v["texture"] is not None:
                v["texture"] = {"name": v["texture"]["name"], "width": v["texture"]["width"],
                                "height": v["texture"]["height"], "format": v["texture"]["format"]}

    # -- shaders: livescene's dump (same name and source bundle), GLES3 variant per material ------------
    index = json.loads((out_dir / "livescene" / "shaders" / "shaders.json").read_text(encoding="utf-8"))
    by_name = {r["name"]: r for r in index}
    keywords = {}
    for m in materials.values():
        sh = m["shader"]["shader"]
        rec = by_name.get(sh)
        if rec is None:
            raise RuntimeError(f"{m['material']}: shader {sh} not in livescene/shaders")
        if sh not in ex.shaders or ex.shaders[sh].assets_file.name != rec["source"]:
            raise RuntimeError(f"{m['material']}: shader {sh} comes from another bundle than livescene's")
        known = {k for v in rec["variants"] if v["platform"] == "gles3" and v["type"] == "GLES3" for k in v["keywords"]}
        want = sorted(k for k in m["keywords"] if k in known)
        if not any(v["platform"] == "gles3" and v["type"] == "GLES3" and v["stage"] == "vertex"
                   and sorted(v["keywords"]) == want for v in rec["variants"]):
            raise RuntimeError(f"{sh}: no GLES3 variant for material {m['material']} keywords {want}")
        keywords[m["material"]] = want
    if "UI/Default" not in by_name:
        raise RuntimeError("UI/Default not in livescene/shaders")

    # -- the root Animator's controller (evidence) -----------------------------------------------------
    an = _comp(by_path[f"{CANVAS}/root"][0], "Animator")
    controller = an["m_Controller"] if an else None
    tmp_settings = player.names_in(player.resource("TMP Settings"), player.mono(player.resource("TMP Settings")))

    doc = {
        "about": f"Live start canvas (Live/UILiveStartCanvas, LightWeight mode): what livescene/scene.json lacks, "
                 f"{language}",
        "canvas": CANVAS,
        "slice": {"musicId": music_id, "difficulty": difficulty, "liveDifficulty": LIVE_DIFFICULTY[difficulty],
                  "lightweight": True, "profile": "fresh (no play records)"},
        "language": {"mode": mode, "field": column[1:], "fonts": lang, "fontSwap": swap,
                     "lineSpacing": LANGUAGE_LINE_SPACING[mode]},
        "strings": strings,
        "stringSources": sources,
        "texts": texts,
        "sprites": sprites_out,
        "difficultyIcons": difficulty_icons,
        "jacket": jacket["name"],
        "textures": textures,
        "materials": materials,
        "materialKeywords": keywords,
        "fonts": font_out,
        "glyphCoverage": coverage,
        "tmpSettings": {k: tmp_settings[k] for k in ("m_GetFontFeaturesAtRuntime", "m_matchMaterialPreset",
                                                     "m_enableExtraPadding", "m_missingGlyphCharacter")},
        # images draw with Graphic.defaultGraphicMaterial (built-in UI/Default: livescene's dump of it has the
        # same variants as the unity_builtin_extra one the ADV exports, checked byte for byte)
        "shaders": {"index": "../livescene/shaders/shaders.json",
                    "names": sorted({m["shader"]["shader"] for m in materials.values()} | {"UI/Default"})},
        "controller": controller,
    }
    write_json(base / "liveui.json", doc)
    return {"liveui": str(base / "liveui.json"), "texts": len(texts), "sprites": sorted(sprites_out),
            "textures": {k: (v["width"], v["height"]) for k, v in textures.items()},
            "fonts": sorted(font_out), "coverage": {k: v["counts"] for k, v in coverage.items()},
            "strings": strings,
            "runtimeGlyphs": {k: len(v["runtimeGlyphs"]["generated"]) for k, v in font_out.items() if v["runtimeGlyphs"]}}
