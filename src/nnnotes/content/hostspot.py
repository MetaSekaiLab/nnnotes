"""The spot files a story's home host writes from the bundles of a spot (content.storyhost): the background as
binary glTF and the situation's spot.json with its Spine files, read with UnityPy from the bundle closures of the
spot's catalog keys.

`extract_room` writes <out>.glb and <out>.json as content.room makes room.glb and room.json from a product's objects
(the same material translation, sampler and glTF writer): every MeshFilter of the background's bundle closure, in the
bundles' object order; the empty meshes listed apart (`skippedEmptyMeshes`: no vertex data,
`meshesWithoutTriangles`: vertices only). `extract_spot` writes spot.json and spine/ as content.spot makes them,
the skeletons in the bundles' object order, and `resources`: the bundle names of the background and situation keys.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import UnityPy

from ..core.jsonio import write_json
from ..formats.shader import parsed_summary
from ..source.catalog import Catalog
from ..source.config import apk_missing
from ..unity.convert.material import material_doc
from ..unity.reader import SceneGraph, deref, mesh_arrays, referent, script_class, strip_pptrs, texture_image
from . import room
from .prefabs import mat4
from .spot import LANGS, atlas_pages

__all__ = [
    "drawn_meshes",
    "extract_room",
    "extract_spot",
    "main_textures",
    "mesh_components",
    "object_key",
    "submesh_primitives",
    "text_bytes",
]


def object_key(obj) -> tuple[str, int]:
    """(serialized file name, path id) of an object: a path id is unique only within its file."""
    return obj.assets_file.name, obj.path_id


def main_textures(owner, mat_tt: dict, shader_name: str) -> list:
    """[(texture object, texture env)] of a material's main texture slot (room.main_tex_slot) when it holds a texture;
    `owner` is the material's object, whose file the env's PPtr is read from."""
    slot = room.main_tex_slot(shader_name)
    return [
        (deref(owner, env["m_Texture"]), env)
        for key, env in mat_tt["m_SavedProperties"]["m_TexEnvs"]
        if key == slot and env["m_Texture"]["m_PathID"]
    ]


# ---- extraction ---------------------------------------------------------
def submesh_primitives(tris: list, mats: list, material_for, on_null) -> list:
    """[(glTF triangle indices, material index)] of a mesh's submeshes: `tris` (per submesh, Unity winding),
    `mats` the renderer's material PPtrs of the same index. An empty submesh gives nothing; a submesh whose material
    slot is empty (path id 0) gives nothing and is reported to `on_null(submesh index)`."""
    prims = []
    for i, t in enumerate(tris):
        if not len(t):
            continue
        if not mats[i].path_id:  # empty slot: no material to draw the submesh with
            on_null(i)
            continue
        prims.append((t[:, [0, 2, 1]].reshape(-1).astype(np.uint32), material_for(mats[i])))
    return prims


def mesh_components(env) -> tuple[dict, dict]:
    """({GameObject key: MeshFilter}, {GameObject key: MeshRenderer}) of an environment. The key is (file name,
    GameObject path id), as object_key: a component is in its GameObject's file."""
    mf_by_go, mr_by_go = {}, {}
    for o in env.objects:
        if o.type.name == "MeshFilter":
            mf_by_go[(o.assets_file.name, o.read_typetree()["m_GameObject"]["m_PathID"])] = o
        elif o.type.name == "MeshRenderer":
            mr_by_go[(o.assets_file.name, o.read_typetree()["m_GameObject"]["m_PathID"])] = o
    return mf_by_go, mr_by_go


def drawn_meshes(mf_by_go: dict, mr_by_go: dict, graph, on_null_mesh):
    """(transform path id, Mesh, material PPtrs) of each GameObject with a MeshFilter and an enabled MeshRenderer
    (mesh_components maps). A MeshFilter whose mesh reference is empty (path id 0) draws nothing: its object path
    goes to `on_null_mesh(path)`."""
    for key, mf in mf_by_go.items():
        tf_pid = graph.tf_of_go.get(key[1])
        mr = mr_by_go.get(key)
        if tf_pid is None or mr is None:
            continue
        if not mr.read_typetree().get("m_Enabled", 1):
            continue
        mesh_ref = mf.read().m_Mesh
        if not mesh_ref.path_id:
            on_null_mesh(graph.path(tf_pid))
            continue
        yield tf_pid, mesh_ref.read(), mr.read().m_Materials


def extract_room(cat: Catalog, bg_key: str, out_path: Path) -> dict:
    env = UnityPy.load(*[str(p) for p in cat.fetch_key(bg_key)])
    graph = SceneGraph(env)
    mf_by_go, mr_by_go = mesh_components(env)

    glb = room.Glb()
    tex_cache: dict[tuple[str, int], int] = {}  # texture object_key -> glTF texture
    mat_cache: dict[tuple[str, int], int] = {}  # material object_key -> glTF material

    def material_for(pptr) -> int:
        mat = pptr.deref()
        mkey = object_key(mat)
        if mkey in mat_cache:
            return mat_cache[mkey]
        mat_tt = mat.read_typetree()
        shader_tt = referent(mat, mat_tt["m_Shader"]).read_typetree()
        gm = room.translate_material(material_doc(mat_tt, lambda p: p), parsed_summary(shader_tt))
        for tex_obj, env_ in main_textures(mat, mat_tt, shader_tt["m_ParsedForm"]["m_Name"]):
            tid = object_key(tex_obj)
            if tid not in tex_cache:
                tex_tt = tex_obj.read_typetree()
                buf = io.BytesIO()
                texture_image(tex_obj.read()).save(buf, format="PNG")
                tex_cache[tid] = glb.texture(buf.getvalue(), tex_tt["m_Name"], room.sampler(tex_tt))
            info, transform = room.texture_info(tex_cache[tid], env_["m_Scale"], env_["m_Offset"])
            if transform:
                glb.ext_used.add("KHR_texture_transform")
            gm["pbrMetallicRoughness"]["baseColorTexture"] = info
        mat_cache[mkey] = glb.material(gm)
        return mat_cache[mkey]

    n_mesh = 0
    n_inactive = 0
    skipped = []
    null_slots = []  # submeshes whose material slot is empty
    null_meshes = []  # objects whose MeshFilter has no mesh
    no_triangles = []  # meshes with vertices but no triangles
    for tf_pid, mesh, mats in drawn_meshes(mf_by_go, mr_by_go, graph, null_meshes.append):
        arrays = mesh_arrays(mesh)
        if arrays is None:
            skipped.append(mesh.m_Name)  # empty mesh (no vertex data)
            continue
        v, vn, uv0, tris = arrays
        if not any(len(t) for t in tris):  # vertices only (e.g. a placeholder mesh without index data)
            no_triangles.append({"mesh": mesh.m_Name, "path": graph.path(tf_pid)})
            continue
        if len(mats) < len(tris):
            raise NotImplementedError(f"{mesh.m_Name}: {len(tris)} submeshes, {len(mats)} materials")
        m = graph.world(tf_pid)
        p = (np.hstack([v, np.ones((len(v), 1))]) @ m.T)[:, :3]
        p[:, 2] *= -1.0  # Unity LH -> glTF RH
        if len(vn):
            nw = vn @ np.linalg.inv(m[:3, :3]).T
            nw /= np.maximum(np.linalg.norm(nw, axis=1, keepdims=True), 1e-12)
            nw[:, 2] *= -1.0
        else:
            nw = np.empty((0, 3))
        uv = uv0.copy()
        uv[:, 1] = 1.0 - uv[:, 1]  # Unity UV origin bottom-left -> glTF top-left
        prims = submesh_primitives(
            tris,
            mats,
            material_for,
            lambda i: null_slots.append({"mesh": mesh.m_Name, "path": graph.path(tf_pid), "submesh": i}),
        )
        if not prims:
            continue
        is_active = graph.active(tf_pid)
        glb.add_mesh(mesh.m_Name, p, nw, uv, prims, active=is_active)
        n_mesh += 1
        n_inactive += 0 if is_active else 1

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(glb.bytes({"backgroundKey": bg_key}))
    doc = {
        "backgroundKey": bg_key,
        "glb": out_path.name,
        "meshCount": n_mesh,
        "inactiveMeshes": n_inactive,
        "skippedEmptyMeshes": skipped,
        "nullMaterialSubmeshes": null_slots,
        "nullMeshFilters": null_meshes,
        "meshesWithoutTriangles": no_triangles,
        **glb.summary(),
    }
    write_json(out_path.with_suffix(".json"), doc)
    return doc


def _master(md: Path, name: str) -> list[dict]:
    return json.loads((md / f"{name}.json").read_text(encoding="utf-8"))["_allData"]


def text_bytes(ta) -> bytes:
    s = ta.m_Script
    return s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)


def _target(ref):
    """The object a PPtr refers to, None for an empty reference (path id 0)."""
    return ref.read() if ref.path_id else None


def write_skeleton(o, write, written_atlases: dict[int, str] | None = None) -> dict:
    """The Spine files of the SkeletonDataAsset object `o` (module docstring), each given to `write(file name,
    bytes)` in this order: the skeleton, then for each of its atlases not in `written_atlases` ({SpineAtlasAsset
    path id: atlas file name}, updated here) the atlas text as stored and one PNG per page. Returns the skeleton's
    record of spot.json: name, skeleton file, atlas files, scale, default mix, mixes."""
    written = {} if written_atlases is None else written_atlases
    sda = o.read_typetree()
    sk = o.read().skeletonJSON.read()
    raw = text_bytes(sk)
    is_json = raw.lstrip()[:1] == b"{"
    ext = ".json" if is_json else ".skel"
    sk_file = sk.m_Name if sk.m_Name.endswith(ext) else f"{sk.m_Name}{ext}"  # as the atlas below
    write(sk_file, raw)
    atlases = []
    for ap in o.read().atlasAssets:
        atlas_asset = ap.read()
        if ap.path_id not in written:
            af = atlas_asset.atlasFile.read()
            araw = text_bytes(af)
            atext = araw.decode("utf-8")
            a_file = af.m_Name if af.m_Name.endswith(".atlas") else f"{af.m_Name}.atlas"
            write(a_file, araw)  # the atlas text exactly as the game stores it
            pages = atlas_pages(atext)
            mats = list(atlas_asset.materials)
            if len(mats) != len(pages):
                raise RuntimeError(f"{a_file}: {len(pages)} pages vs {len(mats)} materials")
            for page, mp in zip(pages, mats):
                mat = mp.read()
                tex = next(e.m_Texture.read() for k, e in mat.m_SavedProperties.m_TexEnvs if k == "_MainTex")
                buf = io.BytesIO()
                texture_image(tex).save(buf, format="PNG")
                write(page, buf.getvalue())
            written[ap.path_id] = a_file
        atlases.append(written[ap.path_id])
    return {
        "name": sda["m_Name"],
        "skeleton": sk_file,
        "atlases": atlases,
        "scale": sda["scale"],
        "defaultMix": sda["defaultMix"],
        "mixes": [
            {"from": f, "to": t, "duration": d}
            for f, t, d in zip(sda.get("fromAnimation", []), sda.get("toAnimation", []), sda.get("duration", []))
        ],
    }


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
        out.append(
            {
                "path": path,
                "skeletonData": skeletons[anim_o.skeletonDataAsset.path_id]["name"],
                "animation": {
                    k: at[k]
                    for k in (
                        "_animationName",
                        "loop",
                        "timeScale",
                        "initialSkinName",
                        "initialFlipX",
                        "initialFlipY",
                        "pmaVertexColors",
                        "tintBlack",
                        "zSpacing",
                    )
                },
                "world": world_of(at["m_GameObject"]),
            }
        )
    return out


def tap_targets(objs: list, graph, world_of) -> list[dict]:
    """SpotCharacter components (tap targets) by character id: object path, their fields, world matrix and the world
    matrix of their `_focus` transform (null when the reference is empty)."""
    out = []
    for o in objs:
        sc = o.read_typetree()
        focus_o = _target(o.read()._focus)
        out.append(
            {
                "path": graph.path(graph.tf_of_go[sc["m_GameObject"]["m_PathID"]]),
                **strip_pptrs({k: v for k, v in sc.items() if k != "_focus"}),
                "world": world_of(sc["m_GameObject"]),
                "focusWorld": None
                if focus_o is None
                else world_of(focus_o.object_reader.read_typetree()["m_GameObject"]),
            }
        )
    out.sort(key=lambda c: c["_characterId"])
    return out


def extract_spot(cat: Catalog, md: Path, spot_id: int, out_dir: Path) -> dict:
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
    by_cls: dict[str | None, list] = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            by_cls.setdefault(script_class(o), []).append(o)

    def world_of(go_pptr) -> list[float]:
        return mat4(graph.world_of_go(go_pptr["m_PathID"]))

    settings = strip_pptrs(by_cls["SpotSituationSettings"][0].read_typetree())
    controller_o = by_cls["SpotCharacterController"][0]
    controller = strip_pptrs(controller_o.read_typetree())

    # Spine skeleton data -> files
    def write(name: str, data: bytes) -> None:
        (spine_dir / name).write_bytes(data)

    skeleton_files: dict[int, dict] = {}
    written_atlases: dict[int, str] = {}
    for o in by_cls.get("SkeletonDataAsset", []):
        skeleton_files[o.path_id] = write_skeleton(o, write, written_atlases)

    spine_chars = spine_characters(by_cls.get("SpotSpineCharacter", []), graph, skeleton_files, world_of)
    characters = tap_targets(by_cls.get("SpotCharacter", []), graph, world_of)

    doc = {
        "spotId": spot_id,
        "master": sp,
        "name": {lang[1:]: txt.get(sp.get("_nameTextId"), {}).get(lang, "") for lang in LANGS},
        "tapTalk": {str(k): v for k, v in sorted(taps.items())},
        "situationSettings": settings,
        "controller": {k: v for k, v in controller.items() if k not in ("_spotSpineCharacters", "_characters")},
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
