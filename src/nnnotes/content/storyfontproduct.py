"""An episode's text and glyph pages assembled from object tables and explicit font bytes."""

from __future__ import annotations

import copy

from ..source import languages
from ..unity import fontsettings, textstyle, tmpfont
from ..unity.export import MemoryPacker, _safe, packed_png
from ..unity.ids import object_id, parse_object_id
from . import advui, storyfonts, storyui, tmpassets
from .prefabs import component_class, key_object, mono_json, ref_id, shader_table

FORMAT = "nnnotes.story-fonts/1"


def asset_keys(episode: dict) -> list[str]:
    """The episode's chat windows and frames whose texts contribute glyphs."""
    return sorted({r["address"] for r in episode["resources"] if r["kind"] in ("chatwindow", "frame")})


def text_records(objects, keys: list[str], language: str, lang: dict, settings) -> tuple[dict, dict, dict]:
    """(component-id text records, sprite bindings, frame nodes) for the named prefabs."""
    mode = languages.mode(language)
    out, sprites, frames = {}, {}, {}
    for key in sorted(set(keys)):
        oid = key_object(objects, key, "GameObject")
        file, _ = parse_object_id(oid)
        nodes = objects.json(oid)["nodes"]
        extra = key.startswith((storyfonts.FRAME_PREFIX, "Adv/Chat/Prefabs/"))
        local_lang = advui._lookup_lang(lang) if extra else lang
        styles = storyui._Styles(local_lang, mode, settings)
        by_path = {
            n["path"]: object_id(file, int(c["pathId"]))
            for n in nodes
            for c in n["components"]
            if component_class(c) in storyui.TEXT_CLASSES
        }
        driven = set()
        for n in nodes:
            for c in n["components"]:
                if component_class(c) in storyfonts.UI_TEXT_CLASSES:
                    target = c["fields"].get("_targetText") or {}
                    driven.add(ref_id(target) or by_path.get(target.get("gameObject")))
        frame_nodes = []
        for node in nodes:
            comps = [{"class": component_class(c), **c["fields"]} for c in node["components"]]
            frame_nodes.append({"path": node["path"], "components": comps})
            texts = [c for c in node["components"] if component_class(c) in storyui.TEXT_CLASSES]
            locs = [c for c in comps if c["class"] == "LocalizeText"]
            if not texts:
                continue
            if len(texts) != 1 or len(locs) > 1:
                raise ValueError(f"{node['path']}: ambiguous text or localization components")
            comp = {"class": component_class(texts[0]), **texts[0]["fields"]}
            tid = object_id(file, int(texts[0]["pathId"]))
            comp["m_fontAsset"] = {"name": storyui._name(objects, comp["m_fontAsset"])}
            comp["m_sharedMaterial"] = {"material": storyui._name(objects, comp["m_sharedMaterial"])}
            record = styles.record(node["path"], comp, locs[0] if locs else None)
            record.update(storyfonts.text_extras(node["path"], comps))
            # Glyph selection always has an effective font, including texts without LocalizeText.
            effective = record["localized"] or {
                "fontAsset": comp["m_fontAsset"]["name"],
                "material": comp["m_sharedMaterial"]["material"],
                "lineSpacing": comp["m_lineSpacing"],
            }
            record["font"] = dict(effective)
            if tid in driven:
                sprites[tid] = {"m_tintAllSprites": comp["m_tintAllSprites"]}
            elif ref_id(comp.get("m_spriteAsset")) is not None:
                raise NotImplementedError(f"{node['path']}: a text with its own sprite asset")
            out[tid] = record
        if key.startswith(storyfonts.FRAME_PREFIX):
            frames[key[len(storyfonts.FRAME_PREFIX) :]] = frame_nodes
    return out, sprites, frames


def shown_texts(
    episode: dict, records: dict, master: dict, settings: dict, status: dict, frames: dict, field: str
) -> list[str]:
    """The strings an episode can display, including localized labels and dynamic chat/frame text."""
    texts = [row.get(field) for row in episode["text"].values()]
    texts.append((episode.get("title") or {}).get(field))
    texts += [r["text"] for r in storyui.master_id_texts(settings, master).values()]
    texts += list(status.values()) + advui.chat_runtime_texts(episode)
    for r in records.values():
        style = r["style"]
        if style.get("textKey"):
            texts.append(master[str(style["textKey"])])
        else:
            texts.append(style.get("text"))
    texts += storyfonts.frame_texts(frames, episode, field)
    return [s for s in texts if isinstance(s, str)]


def line_breaking(objects) -> dict:
    tt = mono_json(objects, storyui.script_root(objects, "TMP_Settings"))

    def text(field):
        oid = ref_id(tt[field])
        return objects.bytes(oid, "data").decode("utf-8") if oid else ""

    return {
        "leading": text("m_leadingCharacters"),
        "following": text("m_followingCharacters"),
        "useModernHangulLineBreakingRules": bool(tt["m_UseModernHangulLineBreakingRules"]),
    }


def sprite_record(objects, fs):
    oid = key_object(objects, advui.EMOJI_SPRITE_ASSET_KEY, "TMP_EmojiSpriteAsset")
    tt = objects.json(oid)
    sheet = ref_id(tt["spriteSheet"])
    material = ref_id(tt["m_Material"])
    if sheet is None or material is None:
        raise ValueError("emoji sprite asset needs a sheet and material")
    return advui.sprite_record(tt, objects.json(sheet), fs.material(material)), sheet


def game_font_set(fs, bindings, shown, missing_char, sprite_drawn, *, subset: str) -> dict:
    """Reduced game font assets, their materials, fallback chains and PNG page bytes."""
    primaries = sorted({b["localized"]["fontAsset"] for b in bindings})
    needed, runtime, reports, missing_glyphs = {}, {}, {}, {}
    for name in primaries:
        primary = fs.loaded(name)
        chars = sorted({ord(c) for s in shown for c in s} - sprite_drawn.get(name, set()))
        tmpfont.check_glyph_variants(fs, primary, shown)
        coverage, baked, dynamic = tmpfont.glyph_coverage(fs, primary, chars)
        mg = tmpfont.missing_glyph(fs, primary, chars, missing_char)
        for u in [storyfonts.UNDERLINE_CHARACTER, *([mg["unicode"]] if mg else [])]:
            hit = fs.lookup(primary, u)
            if hit is not None:
                kind, font = hit
                if u == storyfonts.UNDERLINE_CHARACTER and font is not primary:
                    continue
                if kind != "synthesized":
                    (baked if kind == "baked" else dynamic).setdefault(font.name, set()).add(u)
        if mg:
            missing_glyphs[name] = mg
        reports[name] = coverage
        for target, source in ((needed, baked), (runtime, dynamic)):
            for fname, chars in source.items():
                target.setdefault(fname, set()).update(chars)
    materials, text_materials = tmpfont.text_material_records(
        fs,
        [b["localized"] for b in bindings],
        set(primaries),
        needed,
        runtime,
        fs.named_material,
    )
    packer = MemoryPacker()
    fonts = tmpfont.export_fonts(
        fs,
        set(primaries),
        needed,
        runtime,
        text_materials,
        packer,
        subset=subset,
    )
    textures, pages = packer.build()
    tmpfont.resolve_font_blocks(fonts)
    for name, font in fonts.items():
        font["fallbacks"] = storyfonts.search_order(
            lambda n: [f.name for f in fs.fallbacks(fs.loaded(n))],
            name,
            set(fonts),
        )
        if name in missing_glyphs:
            font["missingGlyph"] = missing_glyphs[name]
    for b in bindings:
        loc = b["localized"]
        for fb in fonts[loc["fontAsset"]]["fallbacks"]:
            name = f"{loc['material']} + {fb}"
            materials[name] = storyfonts.fallback_material(
                materials[loc["material"]],
                materials[fonts[fb]["material"]],
                name,
            )
    return {
        "fonts": fonts,
        "textures": textures,
        "materials": materials,
        "pages": pages,
        "missing": {ord(c) for r in reports.values() for c in r["missing"]},
        "substituted": {u for r in missing_glyphs.values() for u in r["characters"]},
    }


def build(
    objects, episode: dict, language: str, master: dict, settings, source: str, font_files: dict
) -> tuple[dict, dict, set, dict]:
    """(fonts.json, PNG files, shader ids, shader roles) from object data and font inputs {code: bytes}."""
    lang = textstyle.language_fonts_json(mono_json(objects, storyui.script_root(objects, "LocalizeManager")))
    ui_keys = [
        storyui.WIDGET_KEY,
        storyui.CHAT_WIDGET_KEY,
        storyui.UI_MANAGER_KEY,
        *storyui.window_keys(objects.keys()).values(),
        *storyui.DIALOG_KEYS.values(),
    ]
    records, sprite_bindings, frames = text_records(objects, ui_keys + asset_keys(episode), language, lang, settings)
    status = storyui.chat_status_texts(objects, master)
    ids = objects.json(key_object(objects, storyui.MASTER_ID_SETTINGS_KEY, "AdvMasterIdSettings"))
    shown = shown_texts(episode, records, master, ids, status, frames, languages.column(language)[1:])
    return font_product(
        objects,
        records,
        sprite_bindings,
        shown,
        language,
        lang,
        settings,
        source,
        font_files,
        format=FORMAT,
        subset="episode text, synthesized control characters, underline and missing glyph",
    )


def font_product(
    objects,
    records: dict,
    sprite_bindings: dict,
    shown: list[str],
    language: str,
    lang: dict,
    settings,
    source: str,
    font_files: dict,
    *,
    format: str,
    subset: str,
) -> tuple[dict, dict, set, dict]:
    """Font records and PNG pages for explicit component records and displayed strings."""
    chars = sorted({ord(c) for s in shown for c in s})
    bindings = {tid: {"localized": dict(r["font"])} for tid, r in records.items()}
    fs = tmpassets.FontSet(objects)
    for name in sorted({r["font"]["fontAsset"] for r in records.values()}):
        fs.by_key(fontsettings.font_key(tmpassets._Keys(objects), name, name))
    material_ids = {fs.loaded(name).material_oid for name in fs.fonts}
    for r in records.values():
        font = r["font"]
        key = fontsettings.font_key(tmpassets._Keys(objects), font["fontAsset"], font["material"])
        material_ids.add(key_object(objects, key, "Material"))
    rec, sheet, sprite_drawn = None, None, {}
    if sprite_bindings:
        rec, sheet = sprite_record(objects, fs)
        sprite_id = key_object(objects, advui.EMOJI_SPRITE_ASSET_KEY, "TMP_EmojiSpriteAsset")
        sprite_material = ref_id(objects.json(sprite_id)["m_Material"])
        if sprite_material is None:
            raise ValueError("emoji sprite asset needs a material")
        material_ids.add(sprite_material)
        sprite_drawn = storyfonts.drawn_by_sprites(
            rec,
            [bindings[t] for t in sprite_bindings],
            chars,
            lambda name, u: fs.lookup(fs.loaded(name), u),
        )
    missing_char = tmpassets.missing_glyph_character(objects)
    if source == "open":
        loaded = {}

        def font_of(code):
            if code not in font_files:
                return storyfonts.no_font_file(code)
            if code not in loaded:
                loaded[code] = storyfonts.FontFile(f"{code}.font", data=font_files[code])
            return loaded[code]

        result = storyfonts.build_open_font_set(
            language,
            font_of(language),
            list(bindings.values()),
            shown,
            lang,
            missing_char,
            font_of=font_of,
            sprite_drawn=sprite_drawn,
            settings=settings,
        )
    elif source == "game":
        result = game_font_set(fs, list(bindings.values()), shown, missing_char, sprite_drawn, subset=subset)
    else:
        raise ValueError(f"font source {source!r}: expected open or game")
    for tid, record in records.items():
        record["font"] = bindings[tid]["localized"]
    coverage = {"characters": len(chars), "missing": [chr(u) for u in sorted(result["missing"])]}
    if result["substituted"]:
        coverage["missingGlyph"] = [chr(u) for u in sorted(result["substituted"])]
    doc = {
        "format": format,
        "language": language,
        "source": source,
        **{k: result[k] for k in ("fonts", "textures", "materials")},
        "texts": records,
        "sprites": {},
        "coverage": coverage,
        "lineBreaking": line_breaking(objects),
    }
    pages = result["pages"]
    indices = storyfonts.sprite_needs(rec, shown, set().union(*sprite_drawn.values())) if rec else []
    if rec is not None and indices:
        if source == "open":
            emoji = storyfonts.EmojiFont("emoji.font", data=font_files["emoji"]) if "emoji" in font_files else None
            name = emoji.asset_name if emoji else storyfonts.NO_EMOJI_FONT
            sprites = storyfonts.open_sprite_asset(emoji, rec, indices, name)
            for pname, px in sprites["pages"]:
                pages[sprites["textures"][pname]["texture"]] = packed_png(px)
            asset, textures, material = sprites["asset"], sprites["textures"], sprites["material"]
            coverage["sprites"] = {"characters": len(asset["characters"]), "missing": sprites["missing"]}
        else:
            name = rec["name"]
            sh = rec["sheet"]
            if sh["mipCount"] != 1:
                raise NotImplementedError(f"{name}: sprite sheet with mip levels")
            page, path = f"sprite_{name}", f"fonts/{_safe(name)}.png"
            pages[path] = objects.bytes(sheet, "image")
            material = storyfonts._sheet_material(
                rec["material"], rec["material"]["material"], page, sh["width"], sh["height"]
            )
            asset = {k: copy.deepcopy(rec[k]) for k in ("faceInfo", "characters", "glyphs", "sequences")}
            asset["material"] = material["material"]
            for g in asset["glyphs"].values():
                if g["rect"]["m_Width"] and g["rect"]["m_Height"]:
                    g["packed"] = {"texture": page, "dx": 0, "dy": 0}
            textures = {
                page: {"texture": path, **{k: sh[k] for k in ("name", "width", "height", "mipCount", "settings")}}
            }
            coverage["sprites"] = {"characters": len(asset["characters"]), "missing": []}
        doc["textures"].update(textures)
        doc["materials"][material["material"]] = material
        doc["spriteAssets"], doc["emojiSpriteAsset"] = {name: asset}, name
        doc["sprites"] = {t: {"spriteAsset": name, **v} for t, v in sprite_bindings.items()}
    found = {ref_id(objects.json(oid)["shader"]) for oid in material_ids if oid is not None}
    shaders, roles = shader_table(objects, found=[o for o in found if o is not None])
    keep, selected, keywords = set(), {}, {}
    for name, material in sorted(doc["materials"].items()):
        shader = material["shader"]["shader"]
        sid = shaders[shader]
        variants = objects.json(sid, "index")["variants"]
        gles = [v for v in variants if v["platform"] == "gles3" and v["type"] == "GLES3"]
        known = {k for v in gles for k in v["keywords"]}
        want = sorted(k for k in material["keywords"] if k in known)
        if not any(sorted(v["keywords"]) == want and v["stage"] == "vertex" for v in gles):
            raise ValueError(f"{shader}: no GLES3 variant for material {name} keywords {want}")
        keywords[name], selected[shader] = want, sid
        keep.add(sid)
        storyfonts._material_textures(material)
    doc["materialKeywords"], doc["shaders"] = keywords, selected
    return doc, pages, keep, {o: roles[o] for o in keep}
