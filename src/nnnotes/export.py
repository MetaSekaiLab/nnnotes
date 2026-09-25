"""Typetree export with every reference resolved.

`Exporter` turns Unity objects into JSON-ready values: textures become PNG
files (or, in deferred mode, references whose texels a `TexelPacker` copies
later), sprites carry their mesh and UVs (texture-bound or packed in a
SpriteAtlas), materials carry shader name, keywords and properties, meshes carry
vertex data (optional), ScriptableObject MonoBehaviours are inlined once, and
references to GameObjects, Transforms and components become hierarchy paths.
Prefabs and scenes are exported as ordered node lists (hierarchy order,
components in GameObject order). AnimationClips carry the Mecanim clip data the
Animator samples (streamed cubic keys, dense samples, constants) with their
bindings resolved to relative paths and property names; AnimatorControllers
carry layers, states, transitions and parameters. `export_key` exports any
addressable key by the type of its container asset.

Clip, binding and controller JSON comes in several shapes (`clip_format`:
"generic", or "livescene" / "livenotes" for the live scene and live notes exports;
`clip_curves` and `controller_states` for the ADV UI). All of them are views of one record
(`clip_record` / `controller_record`), so resolution lives here only.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import struct
import zlib
from pathlib import Path

import numpy as np
import UnityPy
from PIL import Image
from UnityPy.helpers import MeshHelper

from .catalog import Catalog
from .unity import (DEFAULT_RESOURCES, SceneGraph, deref, external_path, is_pptr,
                    mesh_arrays, script_class)

HEADER = ("m_GameObject", "m_Script")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def _key(o) -> tuple[str, int]:
    return (o.assets_file.name, o.path_id)


def _rid(o, name: str) -> str:
    """Stable id for an object exported into a registry: name + hash of (file, path id)."""
    return f"{name}#{hashlib.sha1(f'{o.assets_file.name}:{o.path_id}'.encode()).hexdigest()[:8]}"


def _crc(s: str) -> int:
    return zlib.crc32(s.encode("utf-8"))


FLT_MAX = 3.4028234663852886e+38


def streamed_frames(words: list[int]) -> list[list]:
    """Mecanim StreamedClip data (uint32 words) -> [[time, [[curve, c0, c1, c2, c3], ...]], ...].

    A frame holds the cubic segments that start at its time; a curve's value at t
    comes from its last key at or before t: ((c0*x + c1)*x + c2)*x + c3, x = t - time.
    The stream usually opens with a frame at -FLT_MAX (every curve, constant); clips
    whose curves all start at t >= 0 open later. It closes with an empty frame at +inf,
    which is dropped here (JSON has no infinity). An empty stream (no streamed
    curves) gives no frames.
    """
    if not words:
        return []
    buf = struct.pack(f"<{len(words)}I", *words)
    frames, i = [], 0
    while i < len(buf):
        t, n = struct.unpack_from("<fi", buf, i)
        i += 8
        keys = [list(struct.unpack_from("<i4f", buf, i + 20 * k)) for k in range(n)]
        i += 20 * n
        frames.append([t, keys])
    if i != len(buf):
        raise ValueError("streamed clip data does not end on a frame boundary")
    last = frames.pop()
    if last[0] != float("inf") or last[1]:
        raise ValueError(f"streamed clip does not close with an empty +inf frame: {last[0]}")
    return frames


def flat_names(tt, prefix: str = "") -> list[str]:
    """Every field path of a typetree ("m_Color", "m_Color.r", "m_Enabled", ...); lists are leaves."""
    out = []
    if isinstance(tt, dict):
        for k, v in tt.items():
            p = f"{prefix}.{k}" if prefix else k
            out.append(p)
            out.extend(flat_names(v, p))
    return out


# --- clip bindings ------------------------------------------------------------
# Unity class IDs (GenericBinding.typeID) -> class name
CLASS_ID = {1: "GameObject", 4: "Transform", 23: "MeshRenderer", 95: "Animator", 198: "ParticleSystem",
            199: "ParticleSystemRenderer", 212: "SpriteRenderer", 224: "RectTransform", 225: "CanvasGroup",
            114: "MonoBehaviour", 137: "SkinnedMeshRenderer", 120: "LineRenderer", 96: "TrailRenderer",
            331: "SpriteMask", 222: "CanvasRenderer"}
# Transform binding attribute -> (property, curve count) (GenericBinding, typeID 4)
TRANSFORM_ATTR = {1: ("m_LocalPosition", 3), 2: ("m_LocalRotation", 4), 3: ("m_LocalScale", 3),
                  4: ("localEulerAnglesRaw", 3)}
# Animatable property names a binding can name (attribute = crc32 of the name) that are not field paths of a
# component the exporter has seen: common component properties, RectTransform driven fields, and material
# properties ("material.<name>[.<channel>]", renderer material property blocks).
ENGINE_PROPS = ("m_IsActive", "m_Enabled", "m_Color.r", "m_Color.g", "m_Color.b", "m_Color.a",
                "m_Size.x", "m_Size.y", "m_FlipX", "m_FlipY", "m_SortingOrder", "m_Sprite", "m_Alpha",
                "m_SizeDelta.x", "m_SizeDelta.y", "m_AnchoredPosition.x", "m_AnchoredPosition.y",
                "m_LocalPosition.x", "m_LocalPosition.y", "m_LocalPosition.z",
                "m_LocalScale.x", "m_LocalScale.y", "m_LocalScale.z")
RECT_PROPS = ("m_AnchoredPosition.x", "m_AnchoredPosition.y", "m_SizeDelta.x", "m_SizeDelta.y",
              "m_AnchorMin.x", "m_AnchorMin.y", "m_AnchorMax.x", "m_AnchorMax.y", "m_Pivot.x", "m_Pivot.y")
MATERIAL_PROPS = ("_Color", "_TintColor", "_MainTex_ST", "_BaseColor", "_EmissionColor", "_Alpha", "_Cutoff",
                  "_Intensity", "_Offset", "_Scroll", "_ScrollX", "_ScrollY", "_Power", "_Value", "_Glow",
                  "_GlowColor", "_AddColor", "_MultiplyColor", "_Progress", "_Fade", "_Brightness",
                  "_Dissolve", "_MainTex", "_Speed", "_Rotation")


def material_prop_names(extra) -> list[str]:
    out = []
    for p in sorted(set(MATERIAL_PROPS) | set(extra)):
        out.append(f"material.{p}")
        for c in ("r", "g", "b", "a", "x", "y", "z", "w"):
            out.append(f"material.{p}.{c}")
    return out


def _events(tt: dict) -> list[dict]:
    return [{x: e[x] for x in ("time", "functionName", "data", "floatParameter", "intParameter",
                               "messageOptions")} for e in tt["m_Events"]]


# --- texel blocks -> packed textures ---------------------------------------------
def grow_box(x0: float, y0: float, x1: float, y1: float, margin: int) -> tuple[int, int, int, int]:
    """Texel box covering [x0, x1) x [y0, y1) grown by `margin` texels on every side."""
    return (math.floor(x0) - margin, math.floor(y0) - margin, math.ceil(x1) + margin, math.ceil(y1) + margin)


class TexelPacker:
    """Copies texel blocks of source textures into packed textures.

    Block coordinates are Unity texel coordinates (origin bottom-left). A block
    [x0, x1) x [y0, y1) is read with the source texture's wrap mode, so texels
    a sampler would fetch across an edge are the ones copied. Blocks keep their
    size; a block's UVs move by the integer translation `offset` (in texels), so
    sampling a packed texture returns the same texel values as the source.
    """

    WIDTH = 2048

    def __init__(self, out_dir: Path):
        self.out = Path(out_dir)
        self._pixels: dict[tuple, np.ndarray] = {}
        self.groups: dict[str, list] = {}

    def pixels(self, tex_obj) -> np.ndarray:
        k = _key(tex_obj)
        if k not in self._pixels:
            tt = tex_obj.read_typetree()
            img = tex_obj.read().image
            if tt["m_TextureFormat"] == 1:               # Alpha8: value in the alpha channel
                if img.mode == "L":
                    a = np.asarray(img, np.uint8)
                elif img.mode == "RGBA":
                    a = np.asarray(img, np.uint8)[:, :, 3]
                else:
                    raise NotImplementedError(f"Alpha8 decoded as {img.mode}")
                arr = np.zeros(a.shape + (4,), np.uint8)
                arr[:, :, 3] = a                          # rgb 0 (GL alpha texture); TMP reads .a only
            else:
                arr = np.asarray(img.convert("RGBA"), np.uint8)
            if arr.shape[:2] != (tt["m_Height"], tt["m_Width"]):
                raise RuntimeError(f"{tt['m_Name']}: decoded size {arr.shape}")
            self._pixels[k] = arr[::-1].copy()            # bottom row first (Unity v = 0)
        return self._pixels[k]

    @staticmethod
    def _wrap(mode: int) -> str:
        if mode == 0:
            return "wrap"
        if mode == 1:
            return "clip"
        raise NotImplementedError(f"texture wrap mode {mode}")

    def add(self, group: str, tex_obj, x0: int, y0: int, x1: int, y1: int) -> dict:
        """Register a block; returns a handle filled in by build()."""
        tt = tex_obj.read_typetree()
        h = {"tex": tex_obj, "key": _key(tex_obj), "settings": tt["m_TextureSettings"], "box": (x0, y0, x1, y1),
             "srcName": tt["m_Name"], "srcSize": (tt["m_Width"], tt["m_Height"])}
        self.groups.setdefault(group, []).append(h)
        return h

    def add_array(self, group: str, name: str, rgba: np.ndarray, origin: tuple[int, int], settings: dict) -> dict:
        """Register generated texels (RGBA, bottom row first) covering [origin, origin + shape) of a
        virtual texture as one block; the whole array is copied (no wrap)."""
        ox, oy = origin
        h = {"array": rgba, "key": ("generated", name), "settings": settings,
             "box": (ox, oy, ox + rgba.shape[1], oy + rgba.shape[0]), "srcName": name,
             "srcSize": (rgba.shape[1], rgba.shape[0])}
        self.groups.setdefault(group, []).append(h)
        return h

    def build(self) -> dict:
        textures = {}
        for group, handles in self.groups.items():
            settings = {json.dumps(h["settings"], sort_keys=True) for h in handles}
            if len(settings) != 1:
                raise RuntimeError(f"{group}: blocks from textures with different sampler settings")
            # dedupe identical blocks of the same texture
            uniq: dict[tuple, dict] = {}
            for h in handles:
                uniq.setdefault((h["key"], h["box"]), h)
            blocks = sorted(uniq.values(), key=lambda h: (-(h["box"][3] - h["box"][1]), h["box"]))
            x = y = shelf = 0
            width = max(self.WIDTH, max(b["box"][2] - b["box"][0] for b in blocks))
            for b in blocks:
                w, hgt = b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]
                if x + w > width:
                    x, y, shelf = 0, y + shelf, 0
                b["dst"] = (x, y)
                x += w
                shelf = max(shelf, hgt)
            height = y + shelf
            if len(blocks) == 1:              # a single block keeps its own size
                width = blocks[0]["box"][2] - blocks[0]["box"][0]
            out = np.zeros((height, width, 4), np.uint8)
            for b in blocks:
                x0, y0, x1, y1 = b["box"]
                dx, dy = b["dst"]
                if "array" in b:
                    out[dy:dy + (y1 - y0), dx:dx + (x1 - x0)] = b["array"]
                    continue
                src = self.pixels(b["tex"])
                st = b["settings"]
                rows = np.take(np.arange(src.shape[0]), np.arange(y0, y1), mode=self._wrap(st["m_WrapV"]))
                cols = np.take(np.arange(src.shape[1]), np.arange(x0, x1), mode=self._wrap(st["m_WrapU"]))
                out[dy:dy + (y1 - y0), dx:dx + (x1 - x0)] = src[np.ix_(rows, cols)]
            for h in handles:
                d = uniq[(h["key"], h["box"])]["dst"]
                h["offset"] = (d[0] - h["box"][0], d[1] - h["box"][1])
                h["texture"] = group
            fn = f"textures/{_safe(group)}.png"
            (self.out / "textures").mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            Image.fromarray(out[::-1].copy(), "RGBA").save(buf, format="PNG", optimize=True)
            (self.out / fn).write_bytes(buf.getvalue())
            s = next(iter(handles))["settings"]
            textures[group] = {"texture": fn, "name": group, "width": width, "height": height,
                               "mipCount": 1, "settings": s,
                               "sources": sorted({h["srcName"] for h in handles}),
                               "blocks": len(blocks)}
        return textures


CLIP_FORMATS = ("generic", "livescene", "livenotes")


class Exporter:
    """`player` resolves references into `unity default resources` (PlayerData);
    `inline_meshes=False` emits meshes as name + vertex count; MonoBehaviour
    classes in `stub_assets` (assets exported elsewhere, e.g. motions) are
    emitted as {asset, name} only.

    `textures="deferred"`: textures and sprites become references
    ({textureRef} / {spriteRef}) and nothing is written; `tex_objs` / `sprite_recs`
    hold the objects, and `pack_sprite` / `packed_sprite` with a `TexelPacker` copy
    only the texel blocks a draw can sample.
    `clip_format`: the JSON shape of clips and bindings ("generic": inlined once,
    bindings {path, typeID, class, attribute}; "livescene": inlined once, bindings with
    crc32 keys, candidates and curve counts; "livenotes": clips and controllers in the
    `clips` / `controllers` registries, referenced by id).
    `follow`: referenced kinds exported instead of named ("AnimatorController",
    "SpriteAtlas")."""

    def __init__(self, cat: Catalog, out_dir: Path, player=None, inline_meshes: bool = True,
                 stub_assets: tuple[str, ...] = (), textures: str = "write", clip_format: str = "generic",
                 follow: tuple[str, ...] = ()):
        if textures not in ("write", "deferred"):
            raise ValueError(f"textures={textures!r}")
        if clip_format not in CLIP_FORMATS:
            raise ValueError(f"clip_format={clip_format!r}")
        self.cat = cat
        self.player = player
        self.out = Path(out_dir)
        self.inline_meshes = inline_meshes
        self.stub_assets = stub_assets
        self.deferred = textures == "deferred"
        self.clip_format = clip_format
        self.follow = tuple(follow)
        self.textures: dict[tuple, dict] = {}
        self.shaders: dict[str, object] = {}
        self.tex_objs: dict[str, object] = {}      # deferred: "file:pathID" -> Texture2D
        self.sprite_recs: dict[str, dict] = {}     # deferred: "file:pathID" -> sprite render record
        self.clips: dict[str, dict] = {}           # livenotes format: id -> clip
        self.controllers: dict[str, dict] = {}     # livenotes format: id -> controller
        self.material_props: set[str] = set()      # float/colour property names of exported materials
        self._graph_of_file: dict[int, SceneGraph] = {}
        self._done: dict[tuple, dict] = {}         # MonoBehaviour/clip/controller/atlas -> reference once exported
        self._component_ref: dict[tuple, str] = {}
        self._objects: dict[tuple, object] = {}
        self._go_file: dict[int, str] = {}
        self._loaded: dict[str, tuple] = {}
        self._clip_recs: dict[tuple, dict] = {}
        self._atlas_maps: dict[tuple, tuple] = {}
        self._names_of: dict[tuple, list] = {}     # object -> flat field names
        self._names_by_class: dict = {}            # typeID | ("MonoBehaviour", class) -> names of exported components
        self._class_of: dict[tuple, str | None] = {}
        self._rel_tables: dict[int, dict] = {}
        self._gos_at_cache: dict[tuple, list] = {}
        self._path_sources: list[tuple[str, dict]] = []
        self._crcs: dict[str, int] = {}

    # --- loading ---------------------------------------------------------
    def load(self, key: str):
        """(env, graph) of a key's bundle closure, loaded once."""
        if key in self._loaded:
            return self._loaded[key]
        env = UnityPy.load(*[str(p) for p in self.cat.fetch_key(key)])
        graph = SceneGraph(env)
        go_file = self._go_file
        for o in env.objects:
            self._graph_of_file.setdefault(id(o.assets_file), graph)
            self._objects[_key(o)] = o
            if o.type.name == "Shader":
                self._shader(o)
            elif o.type.name == "GameObject":
                go_file[o.path_id] = o.assets_file.name
        # Components living on GameObjects are exported where they sit in the
        # hierarchy; references to them elsewhere become {component, gameObject}.
        for go_pid, go in graph.go.items():
            path = graph.path(graph.tf_of_go[go_pid]) if go_pid in graph.tf_of_go else go["m_Name"]
            for c in go["m_Component"]:
                self._component_ref[(go_file[go_pid], c["component"]["m_PathID"])] = path
        self._loaded[key] = (env, graph)
        return env, graph

    def deref(self, owner, pptr: dict):
        if not pptr["m_PathID"]:
            return None
        if external_path(owner, pptr) == DEFAULT_RESOURCES:
            if self.player is None:
                raise RuntimeError("reference into unity default resources needs player data")
            return self.player.deref(owner, pptr)
        return deref(owner, pptr)

    def graph(self, o) -> SceneGraph:
        return self._graph_of_file[id(o.assets_file)]

    # --- values ----------------------------------------------------------
    def value(self, owner, v):
        if is_pptr(v):
            return self.ref(owner, v)
        if isinstance(v, dict):
            return {k: self.value(owner, x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(owner, x) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return v.hex()
        return v

    def ref(self, owner, pptr: dict):
        o = self.deref(owner, pptr)
        if o is None:
            return None
        t = o.type.name
        if t in ("AnimatorController", "AnimatorOverrideController") and "AnimatorController" in self.follow:
            return self.controller(o)
        if t == "SpriteAtlas" and "SpriteAtlas" in self.follow:
            return self.atlas(o)
        if t == "Texture2D":
            return self.texture(o)
        if t == "Sprite":
            return self.sprite(o)
        if t == "Material":
            return self.material(o)
        if t == "Shader":
            return {"shader": self._shader(o)}
        if t == "Mesh":
            return self.mesh(o)
        if t == "AnimationClip":
            return self.clip(o)
        if t == "GameObject":
            g = self.graph(o)
            return {"gameObject": g.path(g.tf_of_go[o.path_id])}
        if t in ("Transform", "RectTransform"):
            return {"transform": self.graph(o).path(o.path_id)}
        if _key(o) in self._component_ref:
            desc = {"component": t, "gameObject": self._component_ref[_key(o)]}
            if t == "MonoBehaviour":
                desc["class"] = script_class(o)
            return desc
        if t == "MonoBehaviour":
            return self.scriptable(o)
        if t == "MonoScript":
            ms = o.read()
            return {"script": f"{ms.m_Namespace}.{ms.m_ClassName}".lstrip(".")}
        return {"object": t, "name": getattr(o.read(), "m_Name", None)}

    # --- shaders, textures, sprites, materials, meshes ---------------------
    def add_shader(self, o) -> str:
        """Register a Shader object (dumped by the caller from `shaders`); returns its name."""
        name = o.read().m_ParsedForm.m_Name
        self.shaders.setdefault(name, o)
        return name

    _shader = add_shader

    def texture(self, o) -> dict:
        tt = o.read_typetree()
        if self.deferred:
            ref = f"{o.assets_file.name}:{o.path_id}"
            self.tex_objs[ref] = o
            return {"textureRef": ref, "name": tt["m_Name"], "width": tt["m_Width"], "height": tt["m_Height"],
                    "format": tt["m_TextureFormat"], "mipCount": tt.get("m_MipCount", 1),
                    "settings": tt.get("m_TextureSettings")}
        k = _key(o)
        if k not in self.textures:
            tex = o.read()
            h = hashlib.sha1(f"{k[0]}:{k[1]}".encode()).hexdigest()[:8]
            fn = f"textures/{_safe(tt['m_Name'])}-{h}.png"
            (self.out / "textures").mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            tex.image.save(buf, format="PNG")
            (self.out / fn).write_bytes(buf.getvalue())
            self.textures[k] = {
                "texture": fn, "name": tt["m_Name"],
                "width": tt["m_Width"], "height": tt["m_Height"],
                "format": tt["m_TextureFormat"], "mipCount": tt.get("m_MipCount", 1),
                "colorSpace": tt.get("m_ColorSpace"),
                "settings": tt.get("m_TextureSettings"),
            }
        return self.textures[k]

    def sprite_render_data(self, o, tt: dict | None = None):
        """(render data, texture owner, atlas typetree) of a Sprite: its own m_RD when it is bound to a
        texture; for a sprite packed into a SpriteAtlas (m_RD.texture null) the atlas' m_RenderDataMap entry
        under the sprite's m_RenderDataKey, which holds texture, textureRect and uvTransform (Unity
        SpriteAtlas runtime binding). (None, None, None) when the sprite has neither."""
        tt = tt or o.read_typetree()
        rd = tt["m_RD"]
        if rd["texture"]["m_PathID"]:
            return rd, o, None
        atlas_o = self.deref(o, tt["m_SpriteAtlas"])
        if atlas_o is None:
            return None, None, None
        k = _key(atlas_o)
        if k not in self._atlas_maps:
            at = atlas_o.read_typetree()
            index: dict[tuple, list] = {}
            for rk, d in at["m_RenderDataMap"]:
                index.setdefault((json.dumps(rk[0], sort_keys=True), rk[1]), []).append(d)
            self._atlas_maps[k] = (at, index)
        at, index = self._atlas_maps[k]
        key = tt["m_RenderDataKey"]
        hits = index.get((json.dumps(key[0], sort_keys=True), key[1]), [])
        if len(hits) != 1:
            raise RuntimeError(f"sprite {tt['m_Name']}: {len(hits)} atlas render data entries")
        return hits[0], atlas_o, at

    def sprite(self, o) -> dict:
        tt = o.read_typetree()
        src, owner, at = self.sprite_render_data(o, tt)
        if self.deferred:
            return self._deferred_sprite(o, tt, src, owner)
        if src is None:
            return {"sprite": tt["m_Name"], "texture": None, "unboundAtlasTags": tt.get("m_AtlasTags")}
        tex = self.ref(owner, src["texture"])
        rd = tt["m_RD"]
        vd = rd["m_VertexData"]
        raw = vd["m_DataSize"]
        raw = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
        n = vd["m_VertexCount"]
        ch0 = vd["m_Channels"][0]
        if ch0["format"] != 0 or ch0["dimension"] != 3 or ch0["stream"] != 0 or ch0["offset"] != 0:
            raise NotImplementedError(f"sprite {tt['m_Name']}: position channel {ch0}")
        pos = np.frombuffer(raw, "<f4", count=n * 3).reshape(n, 3)
        idx = list(np.frombuffer(bytes(rd["m_IndexBuffer"]), "<u2"))
        # Sprite UVs are derived from positions with uvTransform
        # (x: texels per unit, y: pivot texel x, z: texels per unit, w: pivot texel y).
        ut = src["uvTransform"]
        uv = [((p[0] * ut["x"] + ut["y"]) / tex["width"], (p[1] * ut["z"] + ut["w"]) / tex["height"])
              for p in pos]
        out = {"sprite": tt["m_Name"], "texture": tex}
        if at is not None:
            out["atlas"] = at["m_Name"]
        out.update({"rect": tt["m_Rect"], "pivot": tt["m_Pivot"], "border": tt["m_Border"],
                    "pixelsToUnits": tt["m_PixelsToUnits"], "offset": tt["m_Offset"],
                    "textureRect": src["textureRect"], "textureRectOffset": src["textureRectOffset"]})
        if at is not None:
            out["atlasRectOffset"] = src["atlasRectOffset"]
            out["downscaleMultiplier"] = src["downscaleMultiplier"]
        out.update({"settingsRaw": src["settingsRaw"], "uvTransform": ut,
                    "vertices": pos.astype(float).tolist(), "uv": [list(map(float, x)) for x in uv],
                    "indices": [int(i) for i in idx]})
        return out

    def _deferred_sprite(self, o, tt: dict, src, owner) -> dict:
        if src is None:
            raise RuntimeError(f"sprite {tt['m_Name']}: no texture and no atlas")
        tex = self.deref(owner, src["texture"])
        if src["alphaTexture"]["m_PathID"] or src.get("secondaryTextures"):
            raise NotImplementedError(f"sprite {tt['m_Name']}: alpha/secondary textures")
        raw = src["settingsRaw"]
        if (raw >> 2) & 0xF:
            raise NotImplementedError(f"sprite {tt['m_Name']}: packing rotation {(raw >> 2) & 0xF}")
        if src.get("downscaleMultiplier", 1.0) != 1.0:
            raise NotImplementedError(f"sprite {tt['m_Name']}: downscaleMultiplier")
        ref = f"{o.assets_file.name}:{o.path_id}"
        self.tex_objs[f"{tex.assets_file.name}:{tex.path_id}"] = tex
        self.sprite_recs[ref] = {
            "name": tt["m_Name"], "tex": tex, "rect": tt["m_Rect"], "border": tt["m_Border"],
            "pivot": tt["m_Pivot"], "pixelsPerUnit": tt["m_PixelsToUnits"],
            "textureRect": src["textureRect"], "textureRectOffset": src["textureRectOffset"],
            "settingsRaw": raw,
        }
        return {"spriteRef": ref, "name": tt["m_Name"]}

    def pack_sprite(self, ref: str, packer: TexelPacker, group: str, margin: int = 1) -> None:
        """Deferred mode: register the texels a sprite draw samples (its texture rect grown by `margin`
        texels for bilinear filtering) as a block of `group`."""
        s = self.sprite_recs[ref]
        tr = s["textureRect"]
        s["_block"] = packer.add(group, s["tex"], *grow_box(tr["x"], tr["y"], tr["x"] + tr["width"],
                                                            tr["y"] + tr["height"], margin))

    def packed_sprite(self, ref: str) -> dict:
        """Deferred mode, after packer.build(): the sprite with its texture rect moved into the packed texture."""
        s = self.sprite_recs[ref]
        b = s.pop("_block")
        tr = s["textureRect"]
        return {"texture": b["texture"], "rect": s["rect"], "border": s["border"], "pivot": s["pivot"],
                "pixelsPerUnit": s["pixelsPerUnit"], "settingsRaw": s["settingsRaw"],
                "textureRect": {"x": tr["x"] + b["offset"][0], "y": tr["y"] + b["offset"][1],
                                "width": tr["width"], "height": tr["height"]},
                "textureRectOffset": s["textureRectOffset"], "source": b["srcName"]}

    def material(self, o) -> dict:
        tt = o.read_typetree()
        sp = tt["m_SavedProperties"]
        m = {
            "material": tt["m_Name"],
            "shader": self.ref(o, tt["m_Shader"]),
            "keywords": tt.get("m_ValidKeywords", []),
            "invalidKeywords": tt.get("m_InvalidKeywords", []),
            "renderQueue": tt.get("m_CustomRenderQueue"),
            "tags": dict(tt.get("stringTagMap", [])),
            "disabledPasses": tt.get("disabledShaderPasses", []),
            "textures": {k: {"texture": self.ref(o, v["m_Texture"]),
                             "scale": v["m_Scale"], "offset": v["m_Offset"]}
                         for k, v in sp["m_TexEnvs"]},
            "ints": dict(sp.get("m_Ints", [])),
            "floats": dict(sp.get("m_Floats", [])),
            "colors": dict(sp.get("m_Colors", [])),
        }
        self.material_props.update(m["floats"])
        self.material_props.update(m["colors"])
        return m

    def mesh(self, o) -> dict:
        m = o.read()
        if not self.inline_meshes:
            return {"mesh": m.m_Name, "vertexCount": m.m_VertexData.m_VertexCount}
        arrays = mesh_arrays(m)
        if arrays is None:
            return {"mesh": m.m_Name, "empty": True}
        v, n, uv, tris = arrays
        out = {"mesh": m.m_Name, "vertices": v.tolist(), "normals": n.tolist(),
               "uv0": uv.tolist(), "submeshes": [t.reshape(-1).tolist() for t in tris]}
        tt = o.read_typetree()
        if tt.get("m_BindPose"):
            out.update(self._skin(m, tt, len(v)))
        return out

    @staticmethod
    def _skin(m, tt: dict, vertex_count: int) -> dict:
        """A skinned mesh's skin as serialized: `m_Skin` (BoneWeights4 per vertex: weight[4], boneIndex[4], from
        the BlendWeight / BlendIndices vertex channels, or m_Skin in older layouts; unused slots 0),
        `m_BindPose` (Matrix4x4f per bone, e00..e33), `m_BoneNameHashes`, `m_RootBoneNameHash`. Bone index i is
        the renderer's m_Bones[i]."""
        h = MeshHelper.MeshHandler(m)
        h.process()
        w, ix = h.m_BoneWeights, h.m_BoneIndices
        if not w or len(w) != vertex_count or len(ix) != vertex_count:
            raise RuntimeError(f"mesh {tt['m_Name']}: {len(w or [])} bone weights for {vertex_count} vertices")
        skin = []
        for ws, bs in zip(w, ix):
            if len(ws) > 4 or len(bs) != len(ws):
                raise NotImplementedError(f"mesh {tt['m_Name']}: {len(ws)} weights / {len(bs)} bone indices per vertex")
            pad = 4 - len(ws)
            skin.append({"weight": [float(x) for x in ws] + [0.0] * pad, "boneIndex": [int(x) for x in bs] + [0] * pad})
        return {"m_Skin": skin, "m_BindPose": tt["m_BindPose"], "m_BoneNameHashes": tt.get("m_BoneNameHashes", []),
                "m_RootBoneNameHash": tt.get("m_RootBoneNameHash")}

    # --- field names (crc32 resolution) ------------------------------------
    def _crc_of(self, name: str) -> int:
        c = self._crcs.get(name)
        if c is None:
            c = self._crcs[name] = _crc(name)
        return c

    def _names(self, o, tt: dict | None = None) -> list[str]:
        """Flat field paths of an object's typetree, computed once."""
        k = _key(o)
        if k not in self._names_of:
            self._names_of[k] = flat_names(tt if tt is not None else o.read_typetree())
        return self._names_of[k]

    def _mono_class(self, o) -> str | None:
        k = _key(o)
        if k not in self._class_of:
            try:
                self._class_of[k] = script_class(o)
            except Exception:                  # script not loadable from this closure
                self._class_of[k] = None
        return self._class_of[k]

    def _register_names(self, o, tt: dict) -> None:
        """Field paths of an exported component, by class (names a clip binding can target)."""
        k = ("MonoBehaviour", self._mono_class(o)) if o.type.name == "MonoBehaviour" else o.type.value
        self._names_by_class.setdefault(k, set()).update(self._names(o, tt))

    # --- clip bindings -----------------------------------------------------
    def _rel_table(self, graph: SceneGraph) -> dict[int, set[str]]:
        """crc32 -> transform paths relative to any of their ancestors (or themselves: "").

        An Animator binding path is relative to the Animator's GameObject, which need not be
        the hierarchy root, and the clip does not say which GameObject plays it."""
        t = self._rel_tables.get(id(graph))
        if t is None:
            t = {0: {""}}
            for tf_pid in graph.tf:
                parts = graph.path(tf_pid).split("/")
                for i in range(1, len(parts)):
                    s = "/".join(parts[i:])
                    t.setdefault(self._crc_of(s), set()).add(s)
            self._rel_tables[id(graph)] = t
        return t

    def _all_graphs(self) -> list[SceneGraph]:
        return list({id(g): g for g in self._graph_of_file.values()}.values())

    def add_path_source(self, key: str, roots: tuple[str, ...] = ()) -> int:
        """Name binding paths from a hierarchy that is not exported (e.g. the scene whose UI a clip animates):
        paths relative to any transform inside the subtrees rooted at transforms named in `roots` (the whole
        hierarchy when empty). Used after the loaded hierarchies; resolved bindings record the key. Returns
        the number of subtrees."""
        env = UnityPy.load(*[str(p) for p in self.cat.fetch_key(key)])
        g = SceneGraph(env)
        paths = [g.path(pid) for pid in g.tf]
        bases = [p for p in paths if not roots or p.rsplit("/", 1)[-1] in roots]
        table: dict[int, set[str]] = {}
        for base in bases:
            d = base.count("/")
            for full in paths:
                if full == base or full.startswith(base + "/"):
                    parts = full.split("/")
                    for i in range(d, len(parts)):
                        s = "/".join(parts[i + 1:])
                        table.setdefault(self._crc_of(s), set()).add(s)
        self._path_sources.append((key, table))
        return len(bases)

    def _gos_at(self, graph: SceneGraph, rel: str) -> list[int]:
        """GameObjects a relative binding path can name ("" = any)."""
        k = (id(graph), rel)
        if k not in self._gos_at_cache:
            self._gos_at_cache[k] = [go_pid for go_pid, tf in graph.tf_of_go.items()
                                     if rel == "" or graph.path(tf) == rel or graph.path(tf).endswith("/" + rel)]
        return self._gos_at_cache[k]

    def _binding_gos(self, r: dict):
        """(graph, GameObject) pairs a resolved binding path can name: exactly root/path when the binding was
        found below its animated root (bind_clip), else every GameObject whose path ends with it."""
        for g in r["graphs"]:
            if r["root"] is not None:
                full = r["root"] if r["path"] == "" else f"{r['root']}/{r['path']}"
                yield from ((g, go) for go, tf in g.tf_of_go.items() if g.path(tf) == full)
            else:
                yield from ((g, go) for go in self._gos_at(g, r["path"]))

    def _bound_names(self, r: dict) -> set[str]:
        """Field paths of the components a resolved binding path can name (and the bound component kind)."""
        tid, names = r["typeID"], set()
        for g, go_pid in self._binding_gos(r):
            if tid == 1:
                r["component"] = "GameObject"
                names.update(flat_names(g.go[go_pid]))
            for c in g.go[go_pid]["m_Component"]:
                co = self._objects.get((self._go_file[go_pid], c["component"]["m_PathID"]))
                if co is None:
                    continue
                if tid == 114:
                    if (r["script"] is not None and co.type.name == "MonoBehaviour"
                            and self._mono_class(co) == r["script"][1]):
                        names.update(self._names(co))
                elif co.type.value == tid:
                    r["component"] = co.type.name
                    names.update(self._names(co))
        return names

    def _material_attribute(self, r: dict) -> str | None:
        """Attribute of a renderer material property binding (customType 22, RendererMaterial):
        attribute = (crc32(property) & 0x0FFFFFFF) | channel << 28, bit 30 set for colour channels (r, g, b, a),
        clear for vector channels (x, y, z, w) or a float property. The property is one of the materials of the
        renderer the binding names; returns "material.<property>[.<channel>]" (Unity's property path).
        The bit layout is native engine code (not in the C# reference); the layout above is the one the data
        shows: the four _TintColor bindings of the live intro's lane_mesh carry 4..7 << 28 over
        crc32("_TintColor") & 0x0FFFFFFF, with 4..6 constant (0.538, 0.510, 2.040: the effect's _TintColor rgb)
        and 7 animated 0 -> 1 -> 0 (alpha)."""
        a = r["attributeCrc"]
        kinds: dict[str, str] = {}
        for g, go_pid in self._binding_gos(r):
            for c in g.go[go_pid]["m_Component"]:
                co = self._objects.get((self._go_file[go_pid], c["component"]["m_PathID"]))
                if co is None or co.type.value != r["typeID"]:
                    continue
                for mp in co.read_typetree().get("m_Materials", []):
                    mo = self.deref(co, mp)
                    if mo is None:
                        continue
                    sp = mo.read_typetree()["m_SavedProperties"]
                    kinds.update({k: "float" for k, _ in sp.get("m_Floats", [])})
                    kinds.update({k: "vector" for k, _ in sp.get("m_Colors", [])})
        hits = [k for k in kinds if self._crc_of(k) & 0x0FFFFFFF == a & 0x0FFFFFFF]
        if len(hits) != 1:
            return None
        name, channel, colour = hits[0], (a >> 28) & 3, (a >> 30) & 1
        if kinds[name] == "float" and not colour and channel == 0:
            return f"material.{name}"
        return f"material.{name}.{('rgba' if colour else 'xyzw')[channel]}"

    def _class_names(self, r: dict) -> set[str]:
        """Names any binding of the class can target: engine properties, material properties of the
        exported materials, and field paths of every exported component of the class."""
        k = ("MonoBehaviour", r["script"][1]) if r["typeID"] == 114 and r["script"] else r["typeID"]
        return (set(ENGINE_PROPS) | set(RECT_PROPS) | set(material_prop_names(self.material_props))
                | self._names_by_class.get(k, set()))

    def _path_tiers(self, owner):
        """(graphs, source, crc32 -> paths | None for the union of `graphs`) in resolution order: the clip's
        own hierarchy, every loaded hierarchy, then the path sources (after bind_clip, the animated root's
        subtree comes first: see _resolve)."""
        own = self._graph_of_file.get(id(owner.assets_file))
        if own is not None:
            yield [own], None, self._rel_table(own)
        yield self._all_graphs(), None, None
        for key, table in self._path_sources:
            yield [], key, table

    def _resolve(self, r: dict) -> None:
        """Name a binding's path and attribute from their crc32: first tier with exactly one hit."""
        crc = r["pathCrc"]
        if r["path"] is None and r["bound"]:
            graph, root = r["bound"]
            cands = self._subtree_table(graph, root).get(crc, set())
            if len(cands) == 1:
                r.update(path=next(iter(cands)), pathSource=None, graphs=[graph], pathCandidates=None, root=root)
        if r["path"] is None:
            first = None
            for graphs, source, table in self._path_tiers(r["owner"]):
                if table is not None:
                    cands = table.get(crc, set())
                else:
                    cands = set().union(*(self._rel_table(g).get(crc, ()) for g in graphs))
                if cands and first is None:
                    first = cands
                if len(cands) == 1:
                    r.update(path=next(iter(cands)), pathSource=source, graphs=graphs, pathCandidates=None)
                    break
            else:
                r["pathCandidates"] = sorted(first) if first and len(first) > 1 else None
        if r["attribute"] is None and r["customType"] == 22:          # RendererMaterial: not a field path crc
            if r["path"] is not None:
                self._bound_names(r)                                     # names the bound component
                r["attribute"] = self._material_attribute(r)
        elif r["attribute"] is None:
            a = r["attributeCrc"]
            hit = [x for x in (self._bound_names(r) if r["path"] is not None else ()) if self._crc_of(x) == a]
            if len(hit) != 1:
                hit = [x for x in self._class_names(r) if self._crc_of(x) == a]
            if len(hit) == 1:
                r["attribute"] = hit[0]

    def _binding_record(self, owner, b: dict) -> dict:
        tid = b["typeID"]
        r = {"owner": owner, "raw": b, "pathCrc": b["path"], "typeID": tid, "attributeCrc": b["attribute"],
             "pptr": bool(b["isPPtrCurve"]), "int": bool(b["isIntCurve"]), "customType": b.get("customType"),
             "serializeReference": bool(b.get("isSerializeReferenceCurve")),
             "path": None, "pathCandidates": None, "pathSource": None, "graphs": [],
             "attribute": None, "curveCount": 1, "transformAttr": False, "component": None, "script": None,
             "bound": False, "root": None}
        if tid == 4 and b["attribute"] in TRANSFORM_ATTR and not r["pptr"]:
            r["attribute"], r["curveCount"] = TRANSFORM_ATTR[b["attribute"]]
            r["transformAttr"] = True
        if tid == 114:
            so = self.deref(owner, b["script"])
            if so is not None:
                ms = so.read()
                r["script"] = (ms.m_Namespace, ms.m_ClassName)
        self._resolve(r)
        return r

    def resolve_pending(self) -> int:
        """Resolve bindings left unresolved when their clip was exported (target hierarchy, material or
        component class seen later, path sources added since) and update the emitted binding JSON in
        place. Returns the number of paths and attributes still unresolved."""
        left = 0
        for rec in self._clip_recs.values():
            changed = False
            for r in rec["bindings"]:
                if r["path"] is None or r["attribute"] is None:
                    before = (r["path"], r["attribute"], r["component"])
                    self._resolve(r)
                    changed |= before != (r["path"], r["attribute"], r["component"])
                left += (r["path"] is None) + (r["attribute"] is None)
            if changed:
                self._refresh_views(rec)
        return left

    @staticmethod
    def _refresh_views(rec: dict) -> None:
        """Re-render a clip's emitted binding JSON in place after its records changed."""
        for view, dicts in rec["views"]:
            for r, d in zip(rec["bindings"], dicts):
                new = view(r)
                if new != d:
                    d.clear()
                    d.update(new)

    def _subtree_table(self, graph: SceneGraph, root: str) -> dict[int, set[str]]:
        """crc32 -> paths relative to the transform at `root` of every transform in its subtree ("" = root)."""
        k = ("subtree", id(graph), root)
        if k not in self._rel_tables:
            t: dict[int, set[str]] = {}
            for tf_pid in graph.tf:
                full = graph.path(tf_pid)
                if full == root or full.startswith(root + "/"):
                    rel = full[len(root) + 1:]
                    t.setdefault(self._crc_of(rel), set()).add(rel)
            if not t:
                raise KeyError(f"animated root {root} not in the hierarchy")
            self._rel_tables[k] = t
        return self._rel_tables[k]

    def bind_clip(self, clip_o, graph: SceneGraph, root: str) -> dict:
        """The clip is played by the Animator on the GameObject at `root` in `graph` (e.g. the object a timeline
        track is bound to): binding paths are relative to it. A path found exactly once in root's subtree replaces
        the guess of the generic tiers; a path not in the subtree keeps it (objects parented below root at
        runtime). In the livescene format the clip's bindings also get the livenotes binding fields (`class`,
        `curves`). The clip must have been exported. Returns {"subtree": n, "outside": n, "unresolved": n}."""
        rec = self._clip_recs[_key(clip_o)]
        table = self._subtree_table(graph, root)
        counts = {"subtree": 0, "outside": 0, "unresolved": 0}
        for r in rec["bindings"]:
            r["bound"] = (graph, root)
            cands = table.get(r["pathCrc"], set())
            if len(cands) == 1:
                path = next(iter(cands))
                if r["path"] != path:           # the generic guess named another object: re-resolve below root
                    r.update(component=None, attribute=r["attribute"] if r["transformAttr"] else None)
                r.update(path=path, pathSource=None, graphs=[graph], pathCandidates=None, root=root)
                counts["subtree"] += 1
            self._resolve(r)
            if r["root"] is None:
                counts["outside" if r["path"] is not None else "unresolved"] += 1
        self._refresh_views(rec)
        return counts

    def timeline_tracks(self, asset_o) -> list[tuple]:
        """(track object, track typetree, [AnimationClip objects]) of every track of a TimelineAsset (depth first
        through group tracks): the clips of its TimelineClips' AnimationPlayableAssets and its infinite clip."""
        out = []

        def walk(pptrs, owner):
            for p in pptrs:
                t = self.deref(owner, p)
                if t is None:
                    continue
                tt = t.read_typetree()
                clips = []
                for c in tt.get("m_Clips", []):
                    a = self.deref(t, c["m_Asset"])
                    at = a.read_typetree() if a is not None else {}
                    if isinstance(at.get("m_Clip"), dict) and at["m_Clip"].get("m_PathID"):
                        clips.append(self.deref(a, at["m_Clip"]))
                if isinstance(tt.get("m_AnimClip"), dict) and tt["m_AnimClip"].get("m_PathID"):
                    clips.append(self.deref(t, tt["m_AnimClip"]))
                out.append((t, tt, clips))
                walk(tt.get("m_Children", []), t)

        walk(asset_o.read_typetree()["m_Tracks"], asset_o)
        return out

    # --- clips -------------------------------------------------------------
    def clip_record(self, o) -> dict:
        """Clip data and resolved binding records, built once (every clip view reads this)."""
        k = _key(o)
        rec = self._clip_recs.get(k)
        if rec is None:
            tt = o.read_typetree()
            if tt["m_Legacy"] or tt["m_Compressed"]:
                raise NotImplementedError(f"clip {tt['m_Name']}: legacy/compressed")
            mc = tt["m_MuscleClip"]
            data = mc["m_Clip"]["data"]
            cbc = tt["m_ClipBindingConstant"]
            rec = {"object": o, "tt": tt, "name": tt["m_Name"], "muscle": mc,
                   "streamed": data["m_StreamedClip"], "dense": data["m_DenseClip"],
                   "constant": list(data["m_ConstantClip"]["data"]),
                   "pptrCurveMapping": list(cbc.get("pptrCurveMapping", [])),
                   "bindings": [self._binding_record(o, b) for b in cbc["genericBindings"]], "views": []}
            self._clip_recs[k] = rec
        return rec

    @staticmethod
    def _curve_total(rec: dict) -> int:
        return rec["streamed"]["curveCount"] + rec["dense"]["m_CurveCount"] + len(rec["constant"])

    @staticmethod
    def _clip_header(rec: dict) -> dict:
        tt, mc = rec["tt"], rec["muscle"]
        return {"sampleRate": tt["m_SampleRate"], "wrapMode": tt["m_WrapMode"],
                "startTime": mc["m_StartTime"], "stopTime": mc["m_StopTime"],
                "loopTime": bool(mc["m_LoopTime"]), "cycleOffset": mc["m_CycleOffset"], "events": _events(tt)}

    @staticmethod
    def _dense(rec: dict) -> dict:
        de = rec["dense"]
        return {"curveCount": de["m_CurveCount"], "frameCount": de["m_FrameCount"],
                "sampleRate": de["m_SampleRate"], "beginTime": de["m_BeginTime"],
                "samples": list(de["m_SampleArray"])}

    def clip(self, o) -> dict:
        """An AnimationClip with the data the Animator samples, in `clip_format`.

        Curve indices run over streamed curves, then dense, then constant curves; each
        binding takes its curve count of them in `bindings` order."""
        k = _key(o)
        if k in self._done:
            return self._done[k]
        rec = self.clip_record(o)
        return {"generic": self._clip_generic, "livescene": self._clip_livescene,
                "livenotes": self._clip_livenotes}[self.clip_format](rec)

    def _views(self, rec: dict, view) -> list[dict]:
        dicts = [view(r) for r in rec["bindings"]]
        rec["views"].append((view, dicts))
        return dicts

    def _clip_generic(self, rec: dict) -> dict:
        o, st = rec["object"], rec["streamed"]
        bindings = self._views(rec, self._binding_generic)
        floats = sum(r["curveCount"] for r in rec["bindings"] if not r["pptr"])
        if self._curve_total(rec) != floats:
            raise RuntimeError(f"clip {rec['name']}: curve count != binding curve count")
        ref = {"clip": rec["name"]}
        self._done[_key(o)] = ref
        streamed = {"curveCount": st["curveCount"], "frames": streamed_frames(st["data"])}
        if st.get("discreteCurveCount"):
            streamed["discreteCurveCount"] = st["discreteCurveCount"]
        out = {**ref, **self._clip_header(rec), "bindings": bindings, "streamed": streamed,
               "dense": self._dense(rec), "constant": rec["constant"]}
        if rec["pptrCurveMapping"]:
            out["pptrCurveMapping"] = [self.ref(o, p) for p in rec["pptrCurveMapping"]]
        return out

    @staticmethod
    def _binding_generic(r: dict) -> dict:
        if r["serializeReference"]:
            raise NotImplementedError(f"clip binding kind {r['raw']}")
        tid = r["typeID"]
        cls = r["script"][1] if tid == 114 and r["script"] else CLASS_ID.get(tid, f"classID {tid}")
        d = {"path": r["path"], "typeID": tid, "class": cls, "attribute": r["attribute"]}
        if r["curveCount"] != 1:
            d["curves"] = r["curveCount"]
        if r["pptr"]:
            d["pptr"] = True
        if r["int"]:
            d["int"] = True
        if r["path"] is None:
            d["pathCrc"] = r["pathCrc"]
        if r["attribute"] is None:
            d["attributeCrc"] = r["attributeCrc"]
        return d

    def _clip_livescene(self, rec: dict) -> dict:
        o, st = rec["object"], rec["streamed"]
        bindings = self._views(rec, self._binding_livescene)
        ref = {"clip": rec["name"]}
        self._done[_key(o)] = ref
        float_curves = sum(b["curveCount"] for b in bindings if not b["isPPtrCurve"])
        pptr = [self.value(o, p) for p in rec["pptrCurveMapping"]]
        out = {
            **ref, **self._clip_header(rec),
            "curveOrder": "streamed, dense, constant (each binding takes curveCount curves in order)",
            "bindings": bindings,
            "streamed": {"curveCount": st["curveCount"], "discreteCurveCount": st.get("discreteCurveCount", 0),
                         "frames": streamed_frames(st["data"])},
            "dense": self._dense(rec), "constant": rec["constant"], "pptrCurveMapping": pptr,
        }
        total = self._curve_total(rec)
        npptr = sum(1 for b in bindings if b["isPPtrCurve"])
        if total != float_curves + npptr:
            out["curveCountMismatch"] = {"data": total, "bindings": float_curves, "pptrBindings": npptr}
        return out

    @staticmethod
    def _binding_livescene(r: dict) -> dict:
        tid = r["typeID"]
        d = {"pathCrc": r["pathCrc"], "path": r["path"], "typeID": tid, "attributeCrc": r["attributeCrc"],
             "isPPtrCurve": r["pptr"], "isIntCurve": r["int"], "customType": r["customType"]}
        if r["pathCandidates"]:
            d["pathCandidates"] = r["pathCandidates"]
        if r["transformAttr"]:
            d["attribute"] = r["attribute"]
        else:
            if tid == 114 and r["script"]:
                d["class"] = f"{r['script'][0]}.{r['script'][1]}".lstrip(".")
            if tid != 114 and r["component"]:
                d["component"] = r["component"]
            if r["attribute"] is not None:
                d["attribute"] = r["attribute"]
        d["curveCount"] = r["curveCount"]
        if r["bound"]:                  # timeline clips also carry the livenotes binding fields (class, curves)
            if "class" not in d:
                d["class"] = CLASS_ID.get(tid, f"classID {tid}")
            d["curves"] = r["curveCount"]
        return d

    def _clip_livenotes(self, rec: dict) -> dict:
        o, st = rec["object"], rec["streamed"]
        bindings = self._views(rec, self._binding_livenotes)
        float_curves = sum(r["curveCount"] for r in rec["bindings"] if not r["pptr"])
        de = rec["dense"]
        if self._curve_total(rec) != float_curves:
            raise RuntimeError(f"clip {rec['name']}: curve count != binding curve count "
                               f"({st['curveCount']}+{de['m_CurveCount']}+{len(rec['constant'])} vs {float_curves})")
        pptr = [self.ref(o, p) for p in rec["pptrCurveMapping"]]
        rid = _rid(o, rec["name"])
        ref = {"clip": rid}
        self._done[_key(o)] = ref
        try:
            frames = streamed_frames(st["data"])
        except ValueError as e:
            raise ValueError(f"clip {rec['name']}: {e}") from None
        self.clips[rid] = {
            "name": rec["name"], **self._clip_header(rec), "bindings": bindings,
            "streamed": {"curveCount": st["curveCount"], "frames": frames},
            "dense": self._dense(rec), "constant": rec["constant"], "pptrCurveMapping": pptr,
            "discreteCurveCount": st.get("discreteCurveCount", 0),
        }
        return ref

    @staticmethod
    def _binding_livenotes(r: dict) -> dict:
        tid = r["typeID"]
        if tid == 114 and not r["pptr"] and r["script"] and r["path"] is not None and r["attribute"] is not None:
            d = {"path": r["path"], "typeID": tid, "class": r["script"][1], "attribute": r["attribute"], "curves": 1}
        else:
            d = {"path": r["path"] if r["path"] is not None else f"crc32:{r['pathCrc']}", "typeID": tid,
                 "class": CLASS_ID.get(tid, f"classID {tid}")}
            if r["pptr"]:
                d["pptr"] = True
            if r["transformAttr"]:
                d.update(attribute=r["attribute"], curves=r["curveCount"])
            else:
                if tid == 114:
                    d["script"] = r["script"][1] if r["script"] else None
                d["attribute"] = r["attribute"] if r["attribute"] is not None else f"crc32:{r['attributeCrc']}"
                d["curves"] = r["curveCount"]
                if r["int"]:
                    d["int"] = True
        if r["pathSource"]:
            d["scene"] = r["pathSource"]
        return d

    def clip_curves(self, o) -> dict:
        """A clip as per-property streamed cubic keys, for runtimes that animate RectTransform,
        CanvasGroup alpha and Transform euler angles of the animated root only (ADV UI)."""
        rec = self.clip_record(o)
        tt, mc, st, de = rec["tt"], rec["muscle"], rec["streamed"], rec["dense"]
        name, const = rec["name"], rec["constant"]
        if st["discreteCurveCount"] or de["m_CurveCount"]:
            raise NotImplementedError(f"clip {name}: discrete/dense curves")
        props = []
        for r in rec["bindings"]:
            if r["pathCrc"] != 0:
                raise NotImplementedError(f"clip {name}: binding below the animated root")
            if r["pptr"] or r["int"] or r["serializeReference"]:
                raise NotImplementedError(f"clip {name}: binding kind {r['raw']}")
            if r["typeID"] == 224 and r["attribute"] in RECT_PROPS:
                props.append("RectTransform." + r["attribute"])
            elif r["typeID"] == 225 and r["attribute"] == "m_Alpha":
                props.append("CanvasGroup.m_Alpha")
            elif r["typeID"] == 4 and r["attributeCrc"] == 4:          # localEulerAnglesRaw (x, y, z)
                props += ["Transform.localEulerAngles.x", "Transform.localEulerAngles.y",
                          "Transform.localEulerAngles.z"]
            else:
                raise NotImplementedError(f"clip {name}: binding {r['raw']}")
        if st["curveCount"] + len(const) != len(props):
            raise RuntimeError(f"clip {name}: curve count != bound properties")
        curves = {p: {"keys": []} for p in props[:st["curveCount"]]}
        for t, keys in streamed_frames(st["data"]):
            for ci, c0, c1, c2, c3 in keys:
                curves[props[ci]]["keys"].append([t, c0, c1, c2, c3])
        for i, v in enumerate(const):
            curves[props[st["curveCount"] + i]] = {"constant": v}
        return {"name": name, "sampleRate": tt["m_SampleRate"],
                "startTime": mc["m_StartTime"], "stopTime": mc["m_StopTime"], "loopTime": bool(mc["m_LoopTime"]),
                "events": tt["m_Events"], "curves": curves,
                "keyFormat": "[time, c0, c1, c2, c3]; value(t) = ((c0*x + c1)*x + c2)*x + c3, x = t - time, "
                             "from the last key with time <= t (first key at -FLT_MAX)"}

    # --- animator controllers, sprite atlases --------------------------------
    def controller_record(self, o) -> dict:
        tt = o.read_typetree()
        if o.type.name != "AnimatorController":
            raise NotImplementedError(f"{o.type.name} {tt.get('m_Name')}")
        return {"object": o, "tt": tt, "name": tt["m_Name"], "tos": dict(tt["m_TOS"])}

    def controller(self, o) -> dict:
        """AnimatorController: layers, state machines (states with speed, loop, blend trees, transitions),
        parameters and default values; clips exported in `clip_format`. Inlined once, or in the
        `controllers` registry for the livenotes format."""
        k = _key(o)
        if k in self._done:
            return self._done[k]
        rec = self.controller_record(o)
        tt, tos = rec["tt"], rec["tos"]
        registry = self.clip_format == "livenotes"
        cid = _rid(o, tt["m_Name"]) if registry else tt["m_Name"]
        ref = {"controller": cid}
        self._done[k] = ref
        clips = [self.ref(o, c) for c in tt["m_AnimationClips"]]
        c = tt["m_Controller"]
        machines = []
        for sm in c["m_StateMachineArray"]:
            sm = sm["data"]
            states = []
            for st in sm["m_StateConstantArray"]:
                st = st["data"]
                trees = []
                for bt in st["m_BlendTreeConstantArray"]:
                    nodes = bt["data"]["m_NodeArray"]
                    trees.append([{"clip": n["data"]["m_ClipID"], "duration": n["data"]["m_Duration"],
                                   "blendType": n["data"]["m_BlendType"],
                                   "children": n["data"]["m_ChildIndices"]} for n in nodes])
                states.append({
                    "name": tos.get(st["m_NameID"], st["m_NameID"]), "path": tos.get(st["m_FullPathID"]),
                    "speed": st["m_Speed"], "cycleOffset": st["m_CycleOffset"], "loop": st["m_Loop"],
                    "writeDefaultValues": st["m_WriteDefaultValues"], "mirror": st["m_Mirror"],
                    "speedParam": tos.get(st["m_SpeedParamID"], st["m_SpeedParamID"]),
                    "timeParam": tos.get(st["m_TimeParamID"], st["m_TimeParamID"]),
                    "blendTrees": trees,
                    "transitions": [self._transition(t["data"], tos) for t in st["m_TransitionConstantArray"]],
                })
            machines.append({"defaultState": sm["m_DefaultState"], "states": states,
                             "anyStateTransitions": [self._transition(t["data"], tos)
                                                     for t in sm["m_AnyStateTransitionConstantArray"]]})
        layers = [{"name": tos.get(l["data"]["m_Binding"]), "stateMachine": l["data"]["m_StateMachineIndex"],
                   "defaultWeight": l["data"]["m_DefaultWeight"],
                   "blending": l["data"]["(int&)m_LayerBlendingMode"]} for l in c["m_LayerArray"]]
        vals = c["m_Values"]["data"]["m_ValueArray"] if "data" in c["m_Values"] else []
        params = [{"name": tos.get(v["m_ID"], v["m_ID"]), "type": v["m_Type"], "index": v["m_Index"]} for v in vals]
        body = {"name": tt["m_Name"], "clips": clips, "layers": layers, "stateMachines": machines,
                "parameters": params, "defaultValues": self.value(o, c["m_DefaultValues"])}
        if registry:
            self.controllers[cid] = body
            return ref
        return {**ref, **body}

    @staticmethod
    def _transition(t: dict, tos: dict) -> dict:
        return {"destination": t["m_DestinationState"], "name": tos.get(t["m_ID"], t["m_ID"]),
                "duration": t["m_TransitionDuration"], "offset": t["m_TransitionOffset"],
                "exitTime": t["m_ExitTime"], "hasExitTime": t["m_HasExitTime"],
                "fixedDuration": t["m_HasFixedDuration"], "canTransitionToSelf": t["m_CanTransitionToSelf"],
                "conditions": [{"mode": x["data"]["m_ConditionMode"],
                                "event": tos.get(x["data"]["m_EventID"], x["data"]["m_EventID"]),
                                "threshold": x["data"]["m_EventThreshold"], "exitTime": x["data"]["m_ExitTime"]}
                               for x in t["m_ConditionConstantArray"]]}

    def controller_states(self, o) -> dict:
        """A single-layer AnimatorController without transitions, blend trees or parameter-driven states
        as its state list (name, speed, loop, cycle offset, clip name) -- what the ADV UI runtime plays."""
        rec = self.controller_record(o)
        tt, tos = rec["tt"], rec["tos"]
        clips = [self.deref(o, p).read().m_Name for p in tt["m_AnimationClips"]]
        ctrl = tt["m_Controller"]
        if len(ctrl["m_StateMachineArray"]) != 1 or len(ctrl["m_LayerArray"]) != 1:
            raise NotImplementedError(f"controller {tt['m_Name']}: several layers")
        sm = ctrl["m_StateMachineArray"][0]["data"]
        states = []
        for st in sm["m_StateConstantArray"]:
            d = st["data"]
            motion = []
            for bt in d["m_BlendTreeConstantArray"]:
                for node in bt["data"]["m_NodeArray"]:
                    n = node["data"]
                    if n["m_ChildIndices"]:
                        raise NotImplementedError(f"controller {tt['m_Name']}: blend trees")
                    motion.append(clips[n["m_ClipID"]] if n["m_ClipID"] != 0xFFFFFFFF else None)
            if d["m_SpeedParamID"] or d["m_TimeParamID"] or d["m_CycleOffsetParamID"]:
                raise NotImplementedError(f"controller {tt['m_Name']}: parameter-driven state")
            if d["m_TransitionConstantArray"]:
                raise NotImplementedError(f"controller {tt['m_Name']}: state transitions")
            states.append({"name": tos[d["m_NameID"]], "speed": d["m_Speed"], "loop": d["m_Loop"],
                           "cycleOffset": d["m_CycleOffset"], "clip": motion[0] if motion else None})
        if sm["m_AnyStateTransitionConstantArray"]:
            raise NotImplementedError(f"controller {tt['m_Name']}: any-state transitions")
        return {"name": tt["m_Name"], "defaultState": states[sm["m_DefaultState"]]["name"], "states": states}

    def atlas(self, o) -> dict:
        """SpriteAtlas: every packed sprite (name -> sprite with atlas texture and UVs), inlined once."""
        k = _key(o)
        if k in self._done:
            return self._done[k]
        tt = o.read_typetree()
        ref = {"atlas": tt["m_Name"]}
        self._done[k] = ref
        sprites = {}
        for p in tt["m_PackedSprites"]:
            s = self.ref(o, p)
            if s is not None:
                sprites[s["sprite"]] = s
        return {**ref, "tag": tt.get("m_Tag"), "isVariant": tt.get("m_IsVariant"), "sprites": sprites}

    # --- MonoBehaviours, components ------------------------------------------
    def scriptable(self, o) -> dict:
        """A MonoBehaviour that is not a component (ScriptableObject asset), inlined once."""
        k = _key(o)
        if k in self._done:
            return self._done[k]
        cls = script_class(o)
        tt = o.read_typetree()
        ref = {"asset": cls, "name": tt.get("m_Name", "")}
        if cls in self.stub_assets:
            return ref
        self._done[k] = ref
        return {**ref, **self.value(o, {x: y for x, y in tt.items() if x not in HEADER})}

    def component(self, o) -> dict:
        tt = o.read_typetree()
        self._register_names(o, tt)
        body = {k: v for k, v in tt.items() if k not in HEADER}
        out = {"type": o.type.name}
        if o.type.name == "MonoBehaviour":
            out["class"] = script_class(o)
        out.update(self.value(o, body))
        return out

    # --- hierarchies -----------------------------------------------------
    def hierarchy(self, env, graph: SceneGraph, root_tf: int) -> list[dict]:
        by_pid = {}
        for o in env.objects:
            by_pid.setdefault(o.path_id, []).append(o)
        nodes = []

        def walk(tf_pid):
            t = graph.tf[tf_pid]
            go = graph.go[t["m_GameObject"]["m_PathID"]]
            comps = []
            for c in go["m_Component"]:
                objs = [x for x in by_pid.get(c["component"]["m_PathID"], [])
                        if x.type.name not in ("GameObject",)]
                if len(objs) != 1:
                    raise RuntimeError(f"{graph.path(tf_pid)}: component {c} resolves to {len(objs)}")
                co = objs[0]
                if co.type.name in ("Transform", "RectTransform"):
                    continue
                comps.append(self.component(co))
            node = {
                "path": graph.path(tf_pid), "name": go["m_Name"],
                "active": bool(go["m_IsActive"]), "layer": go["m_Layer"], "tag": go.get("m_Tag"),
                "localPosition": t["m_LocalPosition"], "localRotation": t["m_LocalRotation"],
                "localScale": t["m_LocalScale"],
                "components": comps,
            }
            if "m_AnchorMin" in t:
                node["rect"] = {k: t[k] for k in ("m_AnchorMin", "m_AnchorMax", "m_AnchoredPosition",
                                                  "m_SizeDelta", "m_Pivot")}
            nodes.append(node)
            for ch in t["m_Children"]:
                walk(ch["m_PathID"])

        walk(root_tf)
        return nodes

    # --- keys --------------------------------------------------------------
    def _container_asset(self, env, internal_id: str):
        for o in env.objects:
            if o.type.name != "AssetBundle":
                continue
            tt = o.read_typetree()
            for path, info in tt["m_Container"]:
                if path.lower() == internal_id.lower():
                    return self.deref(o, info["asset"])
        raise KeyError(f"{internal_id} not in any AssetBundle container")

    def key_object(self, key: str):
        """The object an addressable key names (its bundle's container asset)."""
        env, _ = self.load(key)
        return self._container_asset(env, self.cat._entry(key)["internal_id"])

    def prefab(self, key: str, loaded=None) -> dict:
        """A prefab or scene as a node list; `loaded` = (env, graph) from load(key)."""
        env, graph = loaded or self.load(key)
        iid = self.cat._entry(key)["internal_id"]
        if iid.endswith(".unity"):
            scene_files = set()
            for o in env.objects:
                if o.type.name == "AssetBundle":
                    for path, cab in o.read_typetree().get("m_SceneHashes", []):
                        if path.lower() == iid.lower():
                            scene_files.add(cab.lower())
            roots = [o.path_id for o in env.objects
                     if o.type.name in ("Transform", "RectTransform")
                     and o.assets_file.name.lower().startswith(tuple(scene_files))
                     and not graph.tf[o.path_id]["m_Father"]["m_PathID"]]
            if not roots:
                raise RuntimeError(f"{key}: no scene roots")
            return {"key": key, "scene": True,
                    "nodes": [n for r in sorted(roots, key=graph.path)
                              for n in self.hierarchy(env, graph, r)]}
        go = self._container_asset(env, iid)
        if go is None or go.type.name != "GameObject":
            raise RuntimeError(f"{key}: container asset is {go and go.type.name}")
        return {"key": key, "nodes": self.hierarchy(env, graph, graph.tf_of_go[go.path_id])}

    def asset(self, key: str) -> dict:
        o = self.key_object(key)
        if o.type.name != "MonoBehaviour":
            raise RuntimeError(f"{key}: asset is {o.type.name}")
        return {"key": key, **self.scriptable(o)}

    def export_key(self, key: str) -> dict:
        """Any addressable key by its container asset: prefab or scene (node list), ScriptableObject,
        AnimatorController, AnimationClip, SpriteAtlas, Material, Texture2D, Sprite, Mesh; other kinds as
        {object, value} (typetree with references resolved)."""
        iid = self.cat._entry(key)["internal_id"]
        if iid.endswith(".unity"):
            return self.prefab(key)
        env, graph = self.load(key)
        o = self._container_asset(env, iid)
        t = o.type.name
        if t == "GameObject":
            return self.prefab(key, (env, graph))
        kinds = {"MonoBehaviour": self.scriptable, "AnimatorController": self.controller,
                 "AnimatorOverrideController": self.controller, "AnimationClip": self.clip,
                 "SpriteAtlas": self.atlas, "Material": self.material, "Texture2D": self.texture,
                 "Sprite": self.sprite, "Mesh": self.mesh}
        if t in kinds:
            return {"key": key, **kinds[t](o)}
        return {"key": key, "object": t, "value": self.value(o, o.read_typetree())}

    def key_sprite(self, key: str) -> dict:
        """The Sprite that a texture key's bundle holds for its texture (Addressables sprite sub-asset)."""
        env, _ = self.load(key)
        tex = self.key_object(key)
        hits = []
        for o in env.objects:
            if o.type.name == "Sprite":
                t = self.deref(o, o.read_typetree()["m_RD"]["texture"])
                if t is not None and t.path_id == tex.path_id:
                    hits.append(o)
        if len(hits) != 1:
            raise RuntimeError(f"{key}: {len(hits)} sprites for the texture")
        return {"key": key, **self.sprite(hits[0])}

    # --- shaders ---------------------------------------------------------
    def dump_shaders(self, out_dir: Path, objects: dict | None = None) -> tuple[list, dict]:
        """Dump shaders (name -> Shader object; default every registered one) in name order with
        shader.py's layout into `out_dir`; returns (index records, write_index summary)."""
        from . import shader as shader_mod
        objects = self.shaders if objects is None else objects
        index: list = []
        for name in sorted(objects):
            shader_mod.dump_objects([objects[name]], out_dir, objects[name].assets_file.name, index)
        return index, shader_mod.write_index(index, out_dir)

    def closure_shaders(self, key: str):
        env = UnityPy.load(*[str(p) for p in self.cat.fetch_key(key)])
        for o in env.objects:
            if o.type.name == "Shader":
                self._shader(o)
