"""Material (material.json) and Shader (shader.json)."""

from __future__ import annotations

from collections.abc import Callable

from .objects import BundleObjects, encode_object, label

__all__ = ["material", "material_doc", "shader"]


def material_doc(tt: dict, ref: Callable[[dict], object]) -> dict:
    """A Material's shader, keywords, render queue, tags, disabled passes and saved properties (textures with their
    scale and offset, ints, floats, colours); `ref(pptr)` gives the value of a reference (the shader, textures)."""
    sp = tt["m_SavedProperties"]
    return {
        "material": tt["m_Name"],
        "shader": ref(tt["m_Shader"]),
        "keywords": tt.get("m_ValidKeywords", []),
        "invalidKeywords": tt.get("m_InvalidKeywords", []),
        "renderQueue": tt.get("m_CustomRenderQueue"),
        "tags": dict(tt.get("stringTagMap", [])),
        "disabledPasses": tt.get("disabledShaderPasses", []),
        "textures": {
            k: {"texture": ref(v["m_Texture"]), "scale": v["m_Scale"], "offset": v["m_Offset"]}
            for k, v in sp["m_TexEnvs"]
        },
        "ints": dict(sp.get("m_Ints", [])),
        "floats": dict(sp.get("m_Floats", [])),
        "colors": dict(sp.get("m_Colors", [])),
    }


def material(ctx: BundleObjects, info) -> None:
    """`json`: material_doc with references as {"$ref"}."""
    key = (info.file, info.path_id)
    r = ctx.readers[key]
    doc = material_doc(ctx.tt(key), lambda p: ctx.ref(r, p))
    ctx.emit(key, "json", encode_object(doc), "json", "material", "json", {})


def shader(ctx: BundleObjects, info) -> None:
    """The parsed form (the bytes `nnnotes export shader` writes as <name>.json), every compiled sub-program as
    stored (`program:<platform>/s<S>p<P>_<stage>_<N>`), the variant index and the typetree JSON."""
    from ...formats import shader as shader_format

    key = (info.file, info.path_id)
    r, tt = ctx.readers[key], ctx.tt(key)
    name = (tt.get("m_ParsedForm") or {}).get("m_Name") or f"shader_{info.path_id}"
    text, recs, codes = shader_format._render(r, tt, name)
    variants = []
    for rec, code in zip(recs, codes):
        stem, ext = rec["file"].rsplit("/", 1)[-1].rsplit(".", 1)
        role = f"program:{rec['platform']}/{stem}"
        facts = {k: rec[k] for k in ("platform", "subShader", "pass", "stage", "type", "keywords")}
        ctx.emit(key, role, code, ext, "shader.program", ext, facts)
        variants.append({**rec, "role": role})
    platforms = sorted({v["platform"] for v in variants})
    facts = {"name": label(name), "variants": len(variants), "platforms": platforms}
    ctx.emit(key, "json", text.encode("utf-8"), "json", "shader", "json", facts)
    index = encode_object({"name": name, "variants": variants})
    ctx.emit(key, "index", index, "json", "shader.index", "json", {"variants": len(variants)})
    ctx.emit(key, "typetree", encode_object(ctx.tree(r, tt)), "json", "unity.object", "json", {})
