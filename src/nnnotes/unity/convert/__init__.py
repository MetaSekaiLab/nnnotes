"""Converters: each Unity class has one, turning an object of a bundle into artifacts.

A converter reads one bundle alone through its `BundleObjects` (objects.py) and writes its artifacts through the
`emit` function the caller gives it; the caller (the unity.export stage) keeps the items, the depot and the
provenance. Each converter carries a version (CONVERTERS); a task's key includes the converters of the classes its
bundle holds, with the atoms they use (CONVERTER_ATOMS).

    Texture2D             tex.png          `image`: PNG of the first mip level (HDR ASTC: the .astc file)
    Sprite                sprite.png       `image`: PNG on the sprite's rect; `meta`: JSON
    Mesh                  mesh.glb         `mesh`: binary glTF in the mesh's local space
    GameObject (a root)   prefab.json      `prefab`: the hierarchy below it with its components
    Material              material.json    `json`
    Shader                shader.json      `json`: the parsed form as `nnnotes export shader` writes it;
                                           `program:<platform>/s<S>p<P>_<stage>_<N>`: each compiled sub-program as
                                           stored; `index`: the variants (platform, pass, stage, GPU program type,
                                           keywords); `typetree`
    AnimationClip         clip.json        `json`: the Mecanim clip data and its bindings as stored
    AnimatorController    controller.json  `json`
    MonoBehaviour         mono.json        `json` (a MonoBehaviour that is not part of a hierarchy); a cue sheet asset
                                           also `acb`: its ACB (kind cri.acb); with converter cri.usm, a movie asset
                                           also `usm`: its USM (kind cri.usm)
    TextAsset             data             `data`: the stored bytes
    Font                  font             `font`: the font file
    MonoScript            scripts.json     "<task id>#scripts.json": every MonoScript of the bundle
    AssetBundle           bundle.json      "<task id>#bundle.json": serialized files, externals, AssetBundle objects
    any other class       generic.json     `json`: the object's typetree

Object JSON (canon/1 in the object's own field order):
- a reference is {"$ref": {"file", "pathId"}}, with "class" and "name" when the target is in the bundle; a null
  reference is null. Inside a prefab, a GameObject, Transform or component of the same prefab is named by its path:
  {"gameObject": path}, {"transform": path}, {"component": class, "gameObject": path};
- a byte array (vector of UInt8 / SInt8 / char, TypelessData) shorter than `blobMin` bytes is {"$hex": "..."}; a
  longer one is {"$blob": {"sha256", "size", "format"}} with its bytes in the artifact
  "<object id>#blob:<field path>" (field names and array indices joined by "/", the format from atom `sniff`);
- a string whose stored bytes are not UTF-8 is {"$hex": "<the stored bytes>"};
- NaN is {"$float": "nan"}, infinities 1e999 / -1e999.

A converter's documented limit raises atoms.Unsupported with a reason code; the reason codes are listed in
docs/stages.md.
"""

from __future__ import annotations

from ..atoms import atom_id

__all__ = [
    "ALWAYS",
    "BASE_ATOMS",
    "CLASS_CONVERTERS",
    "CONVERTERS",
    "CONVERTER_ATOMS",
    "REASONS",
    "UNSUPPORTED_CLASSES",
    "USM_ASSET_SCRIPT",
    "USM_CONVERTER",
    "converter_id",
    "converters_for",
    "export_atoms",
]

# converter -> version
CONVERTERS = {
    "tex.png": 1,
    "sprite.png": 1,
    "mesh.glb": 1,
    "prefab.json": 1,
    "material.json": 1,
    "clip.json": 2,
    "controller.json": 2,
    "mono.json": 2,
    "data": 1,
    "font": 1,
    "scripts.json": 1,
    "bundle.json": 1,
    "shader.json": 1,
    "generic.json": 1,
    "cri.usm": 1,
}
# class -> the converters its objects may use (every class may fall back to generic.json)
CLASS_CONVERTERS = {
    "Texture2D": ("tex.png",),
    "Sprite": ("sprite.png",),
    "Mesh": ("mesh.glb",),
    "GameObject": ("prefab.json",),
    "Material": ("material.json",),
    "AnimationClip": ("clip.json",),
    "AnimatorController": ("controller.json",),
    "AnimatorOverrideController": ("controller.json",),
    "MonoBehaviour": ("mono.json",),
    "TextAsset": ("data",),
    "Font": ("font",),
    "MonoScript": ("scripts.json",),
    "AssetBundle": ("bundle.json",),
    "Shader": ("shader.json",),
}
ALWAYS = ("generic.json",)
BASE_ATOMS = ("reader", "sniff")
CONVERTER_ATOMS = {
    "tex.png": ("texture.decode", "png.encode", "astc.container"),
    "sprite.png": ("texture.decode", "png.encode", "sprite.crop", "mesh.arrays"),
    "mesh.glb": ("mesh.arrays", "gltf.write"),
}
UNSUPPORTED_CLASSES = ("AudioClip", "VideoClip", "MovieTexture")
# a movie asset holding its USM: converter cri.usm, only in the key of a bundle whose script context names the class
USM_CONVERTER = "cri.usm"
USM_ASSET_SCRIPT = "CriMw.CriWare.Assets.Runtime|CriWare.Assets|CriManaUsmAsset"
# every reason code the converters and their caller give (the atoms give more; docs/stages.md lists them all)
REASONS = (
    "generic.error",
    "generic.clip.legacy",
    "generic.controller.override",
    "script.unresolved",
    "script.missing",
    "no_data.sprite",
    "no_data.font",
    "unsupported.sprite.external_texture",
    "unsupported.cri.awb_external",
    "failed.read",
    "failed.export",
    *(f"unsupported.class.{c}" for c in UNSUPPORTED_CLASSES),
)


def converter_id(name: str) -> str:
    return f"{name}/{CONVERTERS[name]}"


def converters_for(classes) -> list[str]:
    """The converters a bundle holding objects of `classes` may use."""
    out: set[str] = set(ALWAYS)
    for c in classes:
        out.update(CLASS_CONVERTERS.get(c, ()))
    return sorted(out)


def export_atoms(classes, reader_id: str | None = None) -> dict:
    """The atoms and converters a bundle holding `classes` uses (the `atoms` of its unity.export task)."""
    convs = converters_for(classes)
    names = set(BASE_ATOMS)
    for c in convs:
        names.update(CONVERTER_ATOMS.get(c, ()))
    out = {n: (reader_id if n == "reader" and reader_id else atom_id(n)) for n in names}
    out.update((c, converter_id(c)) for c in convs)
    return dict(sorted(out.items()))
