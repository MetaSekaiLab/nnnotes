"""Shader extraction.

Dumps every Shader object found in the given bundles:
  - `<name>.json`                  parsed form: properties, subshaders, passes
                                   (tags, render state), keyword names
  - `<name>/<platform>/s<S>p<P>_<stage>_<N>.<ext>`
                                   each compiled player sub-program (variant)
  - `shaders.json`                 index with per-variant keywords

Blob layout (Unity 2021.2+ / 6000.x): per platform, an LZ4 blob holding a table
of (offset, length, segment) entries. Code entries and parameter-binding entries
are separate; the parsed form's `m_PlayerSubPrograms` names, per variant, the
code `m_BlobIndex`, its `m_KeywordIndices` and `m_GpuProgramType`. A code entry
is: version, programType, 4 x int, keyword count + aligned strings, then the
program bytes. GLES programs are GLSL ES text holding both stages under
`#ifdef VERTEX` / `#ifdef FRAGMENT`. Vulkan programs are Unity's packed SPIR-V
container and are written as-is (`.vkprog`).

No shader is filtered by name; a viewer decides what it reimplements.

A Shader object's dump depends on its serialized bytes only: it is made once per process (cache bucket
"shaders", keyed by the object's bytes, type and Unity version) and written again from there.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import UnityPy
from UnityPy.enums import ShaderGpuProgramType
from UnityPy.export.ShaderConverter import CompressionHelper

from . import cache
from .jsonio import dumps, write_json

DUMPS = cache.bucket("shaders", salt=cache.source_salt(__file__, "UnityPy"), disk=True)

# UnityEngine.Rendering ShaderCompilerPlatform value -> tag
PLATFORM = {
    0: "gl", 4: "d3d11", 5: "gles20", 9: "gles3", 14: "metal",
    15: "glcore", 18: "vulkan", 19: "switch",
}
# which GPU program types belong to which platform
PLATFORM_TYPES = {
    9: {"GLES3", "GLES31", "GLES31AEP"},
    5: {"GLES"},
    15: {"GLCore32", "GLCore41", "GLCore43"},
    18: {"SPIRV"},
    14: {"MetalVS", "MetalFS"},
}
TEXT_TYPES = {"GLES", "GLES3", "GLES31", "GLES31AEP", "GLCore32", "GLCore41", "GLCore43",
              "MetalVS", "MetalFS"}
STAGES = ("progVertex", "progFragment", "progGeometry", "progHull", "progDomain")


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


def _entry(arr, i):
    x = arr[i]
    return x[0] if isinstance(x, list) else x


def _type_name(v: int) -> str:
    return ShaderGpuProgramType(v).name.replace("kShaderGpuProgram", "")


def _blob_table(raw: bytes) -> list[bytes]:
    n = struct.unpack_from("<i", raw, 0)[0]
    out = []
    for i in range(n):
        off, length, _seg = struct.unpack_from("<3i", raw, 4 + 12 * i)
        out.append(raw[off:off + length])
    return out


def _code_of(entry: bytes) -> bytes:
    """Program bytes of a code blob entry."""
    pos = 4 + 4 + 16                           # version, programType, 4 x int
    nkw = struct.unpack_from("<i", entry, pos)[0]
    pos += 4
    for _ in range(nkw):
        n = struct.unpack_from("<i", entry, pos)[0]
        pos += 4 + n
        pos = (pos + 3) & ~3
    n = struct.unpack_from("<i", entry, pos)[0]
    return entry[pos + 4:pos + 4 + n]


def platform_blobs(shader) -> dict[int, list[bytes]]:
    blob = bytes(shader.compressedBlob or b"")
    out = {}
    n = min(len(shader.platforms), len(shader.compressedLengths), len(shader.decompressedLengths))
    for i in range(n):
        off = _entry(shader.offsets, i)
        raw = CompressionHelper.decompress_lz4(
            blob[off:off + _entry(shader.compressedLengths, i)],
            _entry(shader.decompressedLengths, i))
        out[shader.platforms[i]] = _blob_table(raw)
    return out


def variants(tt: dict, blobs: dict[int, list[bytes]]):
    """Yield (platform, subshader, pass, stage, n, gpuType, keywords, code)."""
    pf = tt["m_ParsedForm"]
    names = pf.get("m_KeywordNames", [])
    for si, ss in enumerate(pf["m_SubShaders"]):
        for pi, ps in enumerate(ss["m_Passes"]):
            for stage in STAGES:
                prog = ps.get(stage) or {}
                n = 0
                for group in prog.get("m_PlayerSubPrograms", []):
                    for sp in group:
                        t = _type_name(sp["m_GpuProgramType"])
                        for plat, table in blobs.items():
                            if t not in PLATFORM_TYPES.get(plat, ()):
                                continue
                            code = _code_of(table[sp["m_BlobIndex"]])
                            kws = [names[k] for k in sp.get("m_KeywordIndices", []) if k < len(names)]
                            yield plat, si, pi, stage[4:].lower(), n, t, kws, code
                        n += 1


def _parsed_summary(tt: dict) -> dict:
    pf = tt["m_ParsedForm"]
    return {
        "name": pf.get("m_Name"),
        "properties": pf.get("m_PropInfo", {}).get("m_Props", []),
        "keywords": pf.get("m_KeywordNames", []),
        "subShaders": [{
            "tags": ss.get("m_Tags"), "lod": ss.get("m_LOD"),
            "passes": [{"name": ps.get("m_State", {}).get("m_Name") or ps.get("m_Name"),
                        "tags": ps.get("m_Tags"), "state": ps.get("m_State")}
                       for ps in ss["m_Passes"]],
        } for ss in pf["m_SubShaders"]],
        "fallback": pf.get("m_FallbackName"),
    }


def _render(o, tt: dict, name: str) -> tuple[str, list[dict], list[bytes]]:
    """(parsed summary JSON text, variant records, variant program bytes) of one Shader object."""
    safe = _safe(name)
    text = dumps(_parsed_summary(tt), indent=1, ensure_ascii=False, default=str)
    recs, codes = [], []
    for plat, si, pi, stage, n, t, kws, code in variants(tt, platform_blobs(o.read())):
        tag = PLATFORM.get(plat, str(plat))
        ext = "glsl" if t in TEXT_TYPES and not t.startswith("Metal") else "metal" if t.startswith("Metal") else "vkprog"
        fn = f"s{si}p{pi}_{stage}_{n}.{ext}"
        recs.append({"file": f"{safe}/{tag}/{fn}", "platform": tag, "subShader": si, "pass": pi, "stage": stage,
                     "type": t, "keywords": kws})
        codes.append(bytes(code))
    return text, recs, codes


def _dump_key(o) -> str:
    st = o.serialized_type
    type_hash = bytes(st.old_type_hash) if st is not None and st.old_type_hash else b""
    return DUMPS.key(o.get_raw_data(), type_hash, int(o.class_id), o.assets_file.unity_version,
                     int(getattr(o, "platform", 0) or 0))


def dump_objects(shaders, out_dir: Path, source: str, index: list | None = None) -> list:
    """Write parsed summaries + every compiled variant of Shader objects.

    `index` accumulates records across calls; a shader already present (same
    name) is not written twice.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = [] if index is None else index
    done = {r["name"] for r in index}
    for o in shaders:
        k = _dump_key(o)
        hit = DUMPS.get(k)
        if hit is not None:
            meta, codes = cache.unpack(hit)
            name, text, recs = json.loads(meta)
        else:
            tt = o.read_typetree()
            named = tt.get("m_ParsedForm", {}).get("m_Name")
            name = named or f"shader_{o.path_id}"
            if name in done:
                continue
            text, recs, codes = _render(o, tt, name)
            if named:                                  # a name from the path id is not a function of the bytes
                DUMPS.put(k, cache.pack(json.dumps([name, text, recs], ensure_ascii=False).encode("utf-8"), codes))
        if name in done:
            continue
        done.add(name)
        safe = _safe(name)
        (out_dir / f"{safe}.json").write_text(text, encoding="utf-8", newline="\n")      # as write_json writes it
        rec = {"name": name, "source": source, "parsed": f"{safe}.json", "variants": []}
        for v, code in zip(recs, codes):
            pdir = out_dir / v["file"].rsplit("/", 1)[0]
            pdir.mkdir(parents=True, exist_ok=True)
            (out_dir / v["file"]).write_bytes(code)   # the program text or binary exactly as the game stores it
            rec["variants"].append(v)
        index.append(rec)
    return index


def write_index(index: list, out_dir: Path) -> dict:
    write_json(Path(out_dir) / "shaders.json", index)
    return {"count": len(index), "names": sorted(r["name"] for r in index),
            "variants": sum(len(r["variants"]) for r in index)}


def dump(bundles: list, out_dir: Path) -> dict:
    index: list = []
    for bpath in bundles:
        env = UnityPy.load(str(bpath))
        dump_objects([o for o in env.objects if o.type.name == "Shader"], out_dir,
                     Path(bpath).name, index)
    return write_index(index, out_dir)
