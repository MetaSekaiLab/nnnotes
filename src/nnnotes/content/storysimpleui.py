"""Localized talk-window and system-message objects for Overlay stories."""

from __future__ import annotations

from ..source import languages
from ..unity import textstyle
from . import storyui
from .livenotes import animator_bindings
from .prefabs import builtin_root, key_object, mesh_roles, mono_json, ref_id, shader_table

FORMAT = "nnnotes.story-simple-ui/1"
ADDRESSES = {
    "talkWindow": "UI/Prefab/Parts/Adv/Talk/UISimpleAdvTalkWindow",
    "systemMessage": "UI/Prefab/UISystemMessageWidget",
}


def addresses(catalog) -> dict[str, str]:
    """The plain catalog address of each prefab, or its embedded address."""
    held = set(catalog)
    out = {}
    for name, address in ADDRESSES.items():
        found = next((key for key in (address, f"Emb{address}") if key in held), None)
        if found is None:
            raise ValueError(f"{address}: no plain or embedded prefab in the catalog")
        out[name] = found
    return out


def keys(catalog) -> list[str]:
    return sorted({*addresses(catalog).values(), *(k for k in (storyui.UI_MANAGER_KEY,) if k in set(catalog))})


def ui(objects, language: str, settings) -> tuple[dict, set, dict]:
    """ui.json with component-id text styles, prefab roots, animation bindings and shader references."""
    code = languages.check(language)
    mode = languages.mode(code)
    localize = storyui.script_root(objects, "LocalizeManager")
    lang = textstyle.language_fonts_json(mono_json(objects, localize))
    styles = storyui._Styles(lang, mode, settings)
    roots = {name: key_object(objects, key, "GameObject") for name, key in addresses(objects.keys()).items()}
    roots["uiManager"] = key_object(objects, storyui.UI_MANAGER_KEY, "GameObject")
    texts = {}
    for oid in roots.values():
        texts.update(storyui._texts(objects, oid, styles))
    graphics = builtin_root(objects, "GraphicsSettings")
    found = [ref_id(r) for r in objects.json(graphics).get("m_AlwaysIncludedShaders", ())]
    shaders, roles = shader_table(objects, found=[oid for oid in found if oid is not None])
    doc = {
        "format": FORMAT,
        "language": {
            "code": code,
            "mode": mode,
            "field": languages.column(code)[1:],
            "lineSpacing": textstyle.LANGUAGE_LINE_SPACING[mode],
            "fontNames": lang["languages"][mode]["fontNames"],
            "additionalFontNames": lang["languages"][mode]["additionalFontNames"],
            "materialTypes": lang["materialTypes"],
            "fontSwap": styles.swap,
        },
        **roots,
        "settings": {
            "localizeManager": localize,
            "tmpSettings": storyui.script_root(objects, "TMP_Settings"),
            "dotween": storyui.script_root(objects, "DOTweenSettings"),
        },
        "graphics": {"graphics": graphics},
        "texts": texts,
        "textStyle": styles.summary(),
        "bindings": animator_bindings(objects),
        "shaders": shaders,
    }
    keep = {oid for oid in objects.ids() if objects.info(oid).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    return doc, keep, roles
