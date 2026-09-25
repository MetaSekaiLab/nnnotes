"""Spot (explorable 2.5D scene) extractor -> spot.json + Spine assets.

A spot = background prefab (cardboard room, see room.py) + situation prefab:
SpotSituationSettings (camera/interaction parameters), SpotCharacterController,
SpotCharacter x N (tap targets with focus transforms) and SpotSpineCharacter x M,
each driving a Spine.Unity SkeletonAnimation. Components are identified by
their MonoScript class (Catalog must be opened with `apk=`).

A reference the prefab leaves empty is written as null, not filled in: a
SpotSpineCharacter with no `_animation` has null `skeletonData`, `animation`
and `world`; a SpotCharacter with no `_focus` has a null `focusWorld`.

Spine files are written from the SkeletonDataAsset reference chain:
  SkeletonDataAsset.skeletonJSON           -> skeleton (.json or binary .skel)
  SkeletonDataAsset.atlasAssets[i].atlasFile -> <name>.atlas
  SpineAtlasAsset.materials[j]._MainTex    -> PNG named after atlas page j
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import UnityPy

from .catalog import Catalog
from .config import apk_missing
from .jsonio import write_json
from .unity import SceneGraph, script_class, strip_pptrs, texture_image

LANGS = ("_japanese", "_english", "_traditionalChinese", "_simplifiedChinese", "_korean")


def _master(md: Path, name: str) -> list[dict]:
    return json.loads((md / f"{name}.json").read_text(encoding="utf-8"))["_allData"]


def _mat4(m) -> list[float]:
    return [float(x) for x in m.T.reshape(-1)]          # column-major


def _atlas_pages(text: str) -> list[str]:
    """Page file names of a Spine atlas (a page line is followed by 'size:')."""
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for i, ln in enumerate(lines)
            if ln and ":" not in ln and i + 1 < len(lines) and lines[i + 1].startswith("size:")]


def _text_bytes(ta) -> bytes:
    s = ta.m_Script
    return s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)


def _target(ref):
    """The object a PPtr refers to, None for an empty reference (path id 0)."""
    return ref.read() if ref.path_id else None


def spine_characters(objs: list, graph, skeletons: dict, world_of) -> list[dict]:
    """SpotSpineCharacter components: object path, the SkeletonAnimation they drive (its skeleton data name,
    initial animation settings and world matrix). `skeletons`: SkeletonDataAsset path id -> skeleton record. One
    whose `_animation` is empty has those three null."""
    out = []
    for o in objs:
        ssc = o.read_typetree()
        path = graph.path(graph.tf_of_go[ssc["m_GameObject"]["m_PathID"]])
        anim_o = _target(o.read()._animation)
        if anim_o is None:
            out.append({"path": path, "skeletonData": None, "animation": None, "world": None})
            continue
        at = anim_o.object_reader.read_typetree()
        out.append({
            "path": path,
            "skeletonData": skeletons[anim_o.skeletonDataAsset.path_id]["name"],
            "animation": {k: at[k] for k in ("_animationName", "loop", "timeScale",
                                             "initialSkinName", "initialFlipX", "initialFlipY",
                                             "pmaVertexColors", "tintBlack", "zSpacing")},
            "world": world_of(at["m_GameObject"]),
        })
    return out


def tap_targets(objs: list, graph, world_of) -> list[dict]:
    """SpotCharacter components (tap targets) by character id: object path, their fields, world matrix and the world
    matrix of their `_focus` transform (null when the reference is empty)."""
    out = []
    for o in objs:
        sc = o.read_typetree()
        focus_o = _target(o.read()._focus)
        out.append({
            "path": graph.path(graph.tf_of_go[sc["m_GameObject"]["m_PathID"]]),
            **strip_pptrs({k: v for k, v in sc.items() if k != "_focus"}),
            "world": world_of(sc["m_GameObject"]),
            "focusWorld": None if focus_o is None else world_of(focus_o.object_reader.read_typetree()["m_GameObject"]),
        })
    out.sort(key=lambda c: c["_characterId"])
    return out


def extract(cat: Catalog, md: Path, spot_id: int, out_dir: Path) -> dict:
    if cat.apk is None:
        raise apk_missing("resolving the Spot / Spine component classes")
    spots = {r["_id"]: r for r in _master(md, "MasterHomeSpot")}
    sp = spots[spot_id]
    txt = {r["_id"]: r for r in _master(md, "MasterText")}
    taps: dict[int, list[int]] = {}
    for r in _master(md, "MasterStoryHomeSpotTapTalkEpisode"):
        if r.get("_spotId") == spot_id:
            taps.setdefault(r["_characterId"], []).append(r["_advId"])

    out_dir = Path(out_dir)
    spine_dir = out_dir / "spine"
    spine_dir.mkdir(parents=True, exist_ok=True)

    env = UnityPy.load(*[str(p) for p in cat.fetch_key(sp["_situationAssetPath"])])
    graph = SceneGraph(env)
    by_cls: dict[str, list] = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            by_cls.setdefault(script_class(o), []).append(o)

    def world_of(go_pptr) -> list[float]:
        return _mat4(graph.world_of_go(go_pptr["m_PathID"]))

    settings = strip_pptrs(by_cls["SpotSituationSettings"][0].read_typetree())
    controller_o = by_cls["SpotCharacterController"][0]
    controller = strip_pptrs(controller_o.read_typetree())

    # Spine skeleton data -> files
    skeleton_files: dict[int, dict] = {}
    written_atlases: dict[int, str] = {}
    for o in by_cls.get("SkeletonDataAsset", []):
        sda = o.read_typetree()
        sk = o.read().skeletonJSON.read()
        raw = _text_bytes(sk)
        is_json = raw.lstrip()[:1] == b"{"
        sk_file = f"{sk.m_Name}.json" if is_json else f"{sk.m_Name}.skel"
        (spine_dir / sk_file).write_bytes(raw)
        atlases = []
        for ap in o.read().atlasAssets:
            atlas_asset = ap.read()
            if ap.path_id not in written_atlases:
                af = atlas_asset.atlasFile.read()
                araw = _text_bytes(af)
                atext = araw.decode("utf-8")
                a_file = af.m_Name if af.m_Name.endswith(".atlas") else f"{af.m_Name}.atlas"
                (spine_dir / a_file).write_bytes(araw)   # the atlas text exactly as the game stores it
                pages = _atlas_pages(atext)
                mats = list(atlas_asset.materials)
                if len(mats) != len(pages):
                    raise RuntimeError(f"{a_file}: {len(pages)} pages vs {len(mats)} materials")
                for page, mp in zip(pages, mats):
                    mat = mp.read()
                    tex = next(e.m_Texture.read() for k, e in mat.m_SavedProperties.m_TexEnvs
                               if k == "_MainTex")
                    buf = io.BytesIO(); texture_image(tex).save(buf, format="PNG")
                    (spine_dir / page).write_bytes(buf.getvalue())
                written_atlases[ap.path_id] = a_file
            atlases.append(written_atlases[ap.path_id])
        skeleton_files[o.path_id] = {
            "name": sda["m_Name"], "skeleton": sk_file, "atlases": atlases,
            "scale": sda["scale"], "defaultMix": sda["defaultMix"],
            "mixes": [{"from": f, "to": t, "duration": d} for f, t, d in
                      zip(sda.get("fromAnimation", []), sda.get("toAnimation", []), sda.get("duration", []))],
        }

    spine_chars = spine_characters(by_cls.get("SpotSpineCharacter", []), graph, skeleton_files, world_of)
    characters = tap_targets(by_cls.get("SpotCharacter", []), graph, world_of)

    doc = {
        "spotId": spot_id,
        "master": sp,
        "name": {lang[1:]: txt.get(sp.get("_nameTextId"), {}).get(lang, "") for lang in LANGS},
        "tapTalk": {str(k): v for k, v in sorted(taps.items())},
        "situationSettings": settings,
        "controller": {k: v for k, v in controller.items()
                       if k not in ("_spotSpineCharacters", "_characters")},
        "characters": characters,
        "spineCharacters": spine_chars,
        "skeletons": list(skeleton_files.values()),
        "resources": {
            "background": [b.name for b in cat.resolve(sp["_backgroundAssetPath"])],
            "situation": [b.name for b in cat.resolve(sp["_situationAssetPath"])],
        },
        "matrixConvention": "Unity world space (left-handed), 4x4 column-major",
    }
    write_json(out_dir / "spot.json", doc)
    return doc
