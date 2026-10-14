"""Texture2D (tex.png) and Sprite (sprite.png).

A texture's image is decoded once per bundle: `texture` (or `decode_into`, when the task leaves textures out) puts it
in a holder that the sprites cut from it read. Where a sprite's image comes from is its plan (`sprite_plan`).
"""

from __future__ import annotations

import json

from ..atoms import Unsupported
from ..ids import object_id
from .objects import BundleObjects, encode_object, message, ref_object_id

__all__ = [
    "atlas_entries",
    "atlas_entry",
    "crop_meta",
    "decode_into",
    "sprite",
    "sprite_external",
    "sprite_plan",
    "texture",
]


def _canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=True)


def texture(ctx: BundleObjects, info, holder: dict) -> None:
    """`image`: PNG of the first mip level (HDR ASTC: the .astc file). The decoded image (or the error) goes into
    `holder` for the texture's sprites."""
    from ..atoms.texture import ASTC_BLOCKS, format_name, is_hdr_astc

    key = (info.file, info.path_id)
    try:
        tt = ctx.tt(key)
        inp = ctx.view.texture_input(info)
    except Exception as e:
        holder["error"] = e
        raise
    fmt, w, h = int(inp.format), int(inp.width), int(inp.height)
    facts = {
        "width": w,
        "height": h,
        "textureFormat": format_name(fmt),
        "mipCount": int(tt.get("m_MipCount", 1) or 1),
        "exportedMips": [0],
    }
    if w and h and is_hdr_astc(fmt):
        holder["error"] = Unsupported(
            "unsupported.texture.format", f"{format_name(fmt)} (HDR ASTC; the texture is exported as .astc)"
        )
        data = ctx.fns["astc.container"](inp.data, w, h, ASTC_BLOCKS[fmt])
        ctx.emit(key, "image", data, "astc", "texture.image", "astc", facts)
        return
    try:
        img = ctx.fns["texture.decode"](inp)
    except Exception as e:
        holder["error"] = e
        raise
    holder["image"] = img
    facts["mode"] = img.mode
    data = ctx.fns["png.encode"](img, ctx.png_level)
    ctx.emit(key, "image", data, "png", "texture.image", "png", facts)


def decode_into(ctx: BundleObjects, info, holder: dict) -> None:
    """The texture's image for its sprites only (a task whose classes leave Texture2D out)."""
    from ..atoms.texture import format_name, is_hdr_astc

    try:
        inp = ctx.view.texture_input(info)
        if inp.width and inp.height and is_hdr_astc(inp.format):
            raise Unsupported("unsupported.texture.format", f"{format_name(inp.format)} (HDR ASTC)")
        holder["image"] = ctx.fns["texture.decode"](inp)
    except Exception as e:
        holder["error"] = e


def _atlas_entry(ctx: BundleObjects, memo: dict, akey: tuple, rd_key) -> dict:
    m = memo.get(akey)
    if m is None:
        m = memo[akey] = [(_canon(k), d) for k, d in ctx.tt(akey).get("m_RenderDataMap") or ()]
    want = _canon(rd_key)
    for k, d in m:
        if k == want:
            return d
    raise ValueError(f"atlas {object_id(*akey)} has no render data for the sprite's key")


def _texture_key(ctx: BundleObjects, owner, pptr: dict) -> tuple:
    k = ctx.target(owner, pptr)
    if k is None or k not in ctx.info:
        raise Unsupported(
            "unsupported.sprite.external_texture",
            f"texture {pptr.get('m_FileID')}:{pptr.get('m_PathID')} is outside the bundle",
        )
    if ctx.info[k].class_name != "Texture2D":
        raise Unsupported("no_data.sprite", f"the texture reference names a {ctx.info[k].class_name}")
    return k


def sprite_plan(ctx: BundleObjects, key: tuple, atlases: dict) -> dict:
    """Where a sprite's image comes from (UnityPy's get_image_from_sprite): its SpriteAtlas' render data entry when
    it names an atlas ("external": an atlas of another bundle), else the atlas of its file named by its first atlas
    tag, else its own render data. `atlases`: a memo of the atlases' render data maps, kept across the sprites."""
    r = ctx.readers[key]
    tt = ctx.tt(key)
    atlas_key = None
    sa = tt.get("m_SpriteAtlas")
    if isinstance(sa, dict) and sa.get("m_PathID"):
        akey = ctx.target(r, sa)
        if akey is None:
            raise ValueError("m_SpriteAtlas names an unknown file")
        if akey not in ctx.readers:
            return {"tt": tt, "external": True, "atlasKey": akey}
        atlas_key = akey
    elif tt.get("m_AtlasTags"):
        tag = tt["m_AtlasTags"][0]
        for i in ctx.infos:
            if i.file == key[0] and i.class_name == "SpriteAtlas" and ctx.name_of((i.file, i.path_id)) == tag:
                atlas_key = (i.file, i.path_id)
                break
    if atlas_key is not None:
        src, owner = _atlas_entry(ctx, atlases, atlas_key, tt["m_RenderDataKey"]), ctx.readers[atlas_key]
    else:
        src, owner = tt["m_RD"], r
    tex = src.get("texture") or {}
    if not tex.get("m_PathID"):
        raise Unsupported("no_data.sprite", "the sprite's render data names no texture")
    tkey = _texture_key(ctx, owner, tex)
    alpha = src.get("alphaTexture") or {}
    akey = _texture_key(ctx, owner, alpha) if alpha.get("m_PathID") else None
    return {
        "tt": tt,
        "external": False,
        "src": src,
        "owner": owner,
        "atlasKey": atlas_key,
        "texture": tkey,
        "alpha": akey,
    }


def sprite(ctx: BundleObjects, info, plan: dict, holder: dict) -> None:
    """`image`: PNG on the sprite's rect cut from its texture's image in `holder`; `meta`: the sprite's JSON and
    where its image came from."""
    from ..atoms import sprite as sprite_atom

    err = holder.get("error")
    if err is not None:
        if isinstance(err, Unsupported):
            raise Unsupported(err.code, f"texture: {err.message}")
        raise RuntimeError(f"texture: {message(err)}")
    if "image" not in holder:
        raise RuntimeError("texture image not decoded")
    key = (info.file, info.path_id)
    r, tt, src = ctx.readers[key], plan["tt"], plan["src"]
    alpha = ctx.fns["texture.decode"](ctx.view.texture_input(ctx.info[plan["alpha"]])) if plan["alpha"] else None
    raw = int(src["settingsRaw"])
    st = sprite_atom.settings(raw)
    mesh = ctx.fns["mesh.arrays"](ctx.view.mesh_input(info)) if st.packing_mode == sprite_atom.TIGHT else None
    downscale = float(src.get("downscaleMultiplier", 1.0))
    img = ctx.fns["sprite.crop"](
        holder["image"],
        src["textureRect"],
        raw,
        mesh,
        float(tt["m_PixelsToUnits"]),
        alpha,
        downscale=downscale,
        canvas={"rect": tt["m_Rect"], "offset": src["textureRectOffset"], "pivot": tt["m_Pivot"]},
    )
    data = ctx.fns["png.encode"](img, ctx.png_level)
    refs = [{"rel": "texture", "object": object_id(*plan["texture"])}]
    if plan["atlasKey"] is not None:
        refs.append({"rel": "atlas", "object": object_id(*plan["atlasKey"])})
    facts = {"width": img.width, "height": img.height, "mode": img.mode}
    ctx.emit(key, "image", data, "png", "sprite.image", "png", facts, refs=refs)
    meta = {
        "sprite": ctx.tree(r, tt),
        "image": {
            "source": "atlas" if plan["atlasKey"] is not None else "renderData",
            "atlas": None if plan["atlasKey"] is None else ctx.dref(plan["atlasKey"]),
            "texture": ctx.dref(plan["texture"]),
            "alphaTexture": None if plan["alpha"] is None else ctx.dref(plan["alpha"]),
            "textureRect": src["textureRect"],
            "textureRectOffset": src["textureRectOffset"],
            "settingsRaw": raw,
            "packed": st.packed,
            "packingMode": st.packing_mode,
            "packingRotation": st.packing_rotation,
            "downscaleMultiplier": downscale,
            "width": img.width,
            "height": img.height,
        },
    }
    ctx.emit(key, "meta", encode_object(meta), "json", "sprite.meta", "json", {}, refs=refs)


def sprite_external(ctx: BundleObjects, info, plan: dict, crop: str) -> None:
    """`meta` of a sprite whose SpriteAtlas is in another bundle: its JSON, mesh and object facts, for the stage
    `crop` that cuts its image from the atlas texture."""
    from ..atoms import sprite as sprite_atom

    key = (info.file, info.path_id)
    m = ctx.fns["mesh.arrays"](ctx.view.mesh_input(info))
    mesh = sprite_atom.mesh_values(m) if "position" in m.channels else None
    atlas_oid = object_id(*plan["atlasKey"])
    meta = {
        "object": ctx.facts(key),
        "sprite": ctx.tree(ctx.readers[key], plan["tt"]),
        "image": {"source": "atlas", "atlas": ctx.dref(plan["atlasKey"]), "stage": crop},
        "mesh": mesh,
    }
    facts = {"crop": crop, "atlas": atlas_oid}
    refs = [{"rel": "atlas", "object": atlas_oid}]
    ctx.emit(key, "meta", encode_object(meta), "json", "sprite.meta", "json", facts, refs=refs)


# ---------------------------------------------------------------- sprites cut from an atlas of another bundle
def atlas_entries(atlas_doc: dict) -> list:
    """The render data entries of a SpriteAtlas' JSON, keyed for atlas_entry."""
    return [(_canon(k), d) for k, d in atlas_doc.get("m_RenderDataMap") or ()]


def atlas_entry(entries: list, key):
    """The entry of atlas_entries under a sprite's m_RenderDataKey (None when the atlas has none)."""
    want = _canon(key)
    return next((d for k, d in entries if k == want), None)


def crop_meta(meta: dict, entries: list, image, crop):
    """The image of a sprite whose atlas is in another bundle, from its `meta` (sprite_external), its atlas'
    render data entries and `image(object id)`, the decoded image of an atlas texture; `crop`: atom sprite.crop."""
    from ..atoms import sprite as sprite_atom

    s = meta["sprite"]
    entry = atlas_entry(entries, s.get("m_RenderDataKey"))
    if entry is None:
        raise ValueError("the atlas has no render data for the sprite's key")
    tex = ref_object_id(entry.get("texture"))
    if tex is None:
        raise Unsupported("no_data.sprite", "the atlas entry names no texture")
    alpha_oid = ref_object_id(entry.get("alphaTexture"))
    raw = int(entry["settingsRaw"])
    mesh = None
    if sprite_atom.settings(raw).packing_mode == sprite_atom.TIGHT:
        if not meta.get("mesh"):
            raise Unsupported("unsupported.sprite.tight_mask", "tightly packed sprite without a mesh")
        mesh = sprite_atom.mesh_from_values(meta["mesh"])
    return crop(
        image(tex),
        entry["textureRect"],
        raw,
        mesh,
        float(s["m_PixelsToUnits"]),
        None if alpha_oid is None else image(alpha_oid),
        downscale=float(entry.get("downscaleMultiplier", 1.0)),
        canvas={"rect": s["m_Rect"], "offset": entry["textureRectOffset"], "pivot": s["m_Pivot"]},
    )
