"""The speed paths give what the plain paths give: JSON writer, scene graph paths, typetree values, texture / PNG
caches, deferred texture writes, shader dump replay, cue sheet decode (parallel, cached, second encode) and the
bundle closure cache. Synthetic objects only; the reference functions below are the code paths as they were
before the caches."""
import copy
import io
import json
import math
import random
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from nnnotes import cache, cri, export, jsonio, shader, unity
from nnnotes.export import Exporter, TexelPacker
from nnnotes.unity import SceneGraph


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.configure(enabled=True, memory_mb=cache.DEFAULT_MEMORY_MB, directory="")
    unity.clear_closures()
    yield
    cache.configure(enabled=True, memory_mb=cache.DEFAULT_MEMORY_MB, directory="", closures=4, closure_mb=512)
    unity.clear_closures()


def tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(Path(d).rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- JSON writer
_POS, _NEG = "\x00nnnotes:+inf", "\x00nnnotes:-inf"


def ref_dumps(obj, **kw):
    found = []

    def fin(v):
        if isinstance(v, float):
            if math.isfinite(v):
                return v
            if v != v:
                raise ValueError("NaN")
            found.append(1)
            return _POS if v > 0 else _NEG
        if isinstance(v, dict):
            return {k: fin(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [fin(x) for x in v]
        if isinstance(v, str) and v in (_POS, _NEG):
            raise ValueError("collides")
        return v
    s = json.dumps(fin(obj), allow_nan=False, **kw)
    if found:
        s = s.replace(json.dumps(_POS), "1e999").replace(json.dumps(_NEG), "-1e999")
    return s


def random_doc(rng, depth=0):
    r = rng.random()
    if depth > 3 or r < 0.3:
        return rng.choice([1, -2, 0.5, 1e-7, 3.4028234663852886e+38, math.inf, -math.inf, "t", "ü\n\x01", None,
                           True, False, 12345678901234567890])
    if r < 0.65:
        return {f"k{i}{'é' if i % 3 == 0 else ''}": random_doc(rng, depth + 1) for i in range(rng.randrange(4))}
    return [random_doc(rng, depth + 1) for _ in range(rng.randrange(4))] + ([(1, math.inf)] if r > 0.95 else [])


@pytest.mark.parametrize("kw", [{}, {"indent": 1, "ensure_ascii": False}, {"sort_keys": True, "separators": (",", ":")},
                                {"indent": 1, "ensure_ascii": True}])
def test_dumps_matches_reference(kw):
    rng = random.Random(7)
    for _ in range(400):
        doc = random_doc(rng)
        assert jsonio.dumps(doc, **kw) == ref_dumps(doc, **kw)


def test_dumps_errors_unchanged():
    with pytest.raises(ValueError, match="NaN"):
        jsonio.dumps([1, math.nan])
    with pytest.raises(ValueError, match="collides"):
        jsonio.dumps({"a": [_NEG]})
    assert jsonio.dumps({"s": "\x00nnnotes"}) == ref_dumps({"s": "\x00nnnotes"})


# ---------------------------------------------------------------- scene graph
def fake_obj(kind, pid, tt, file="f"):
    return SimpleNamespace(type=SimpleNamespace(name=kind), path_id=pid, read_typetree=lambda tt=tt: copy.deepcopy(tt),
                           assets_file=SimpleNamespace(name=file))


def fake_scene(rng, n=300):
    objs, parent = [], {}
    for i in range(n):
        go, tf = 1000 + i, 5000 + i
        parent[tf] = 5000 + rng.randrange(i) if i and rng.random() < 0.9 else 0
        objs.append(fake_obj("GameObject", go, {"m_Name": rng.choice(["a", "b", "c", "node"]), "m_IsActive": 1,
                                               "m_Component": []}))
        objs.append(fake_obj("Transform", tf, {"m_GameObject": {"m_FileID": 0, "m_PathID": go},
                                              "m_Father": {"m_FileID": 0, "m_PathID": parent[tf]},
                                              "m_Children": []}))
    return SimpleNamespace(objects=objs)


def ref_path(g, tf_pid):
    parts = []
    while tf_pid in g.tf:
        parts.append(g.go.get(g.tf[tf_pid]["m_GameObject"]["m_PathID"], {}).get("m_Name", "?"))
        tf_pid = g.tf[tf_pid]["m_Father"]["m_PathID"]
    return "/".join(reversed(parts))


def test_scene_graph_paths_and_index():
    g = SceneGraph(fake_scene(random.Random(3)))
    for tf in list(g.tf) + [0, 42]:
        assert g.path(tf) == ref_path(g, tf)
        assert g.path(tf) == ref_path(g, tf)                  # memoised
    for full in {ref_path(g, t) for t in g.tf} | {"nope"}:
        assert g.gos_at_path(full) == [go for go, tf in g.tf_of_go.items() if ref_path(g, tf) == full]


def test_head_node_cut_after_script():
    kids = [TypeTreeNode(1, t, n, 0, 0) for t, n in (("PPtr<GameObject>", "m_GameObject"), ("UInt8", "m_Enabled"),
                                                       ("PPtr<MonoScript>", "m_Script"), ("string", "m_Name"),
                                                       ("int", "_x"))]
    node = TypeTreeNode(0, "MonoBehaviour", "Base", -1, 1, m_Children=kids)
    head = unity._head_node(node)
    assert [c.m_Name for c in head.m_Children] == ["m_GameObject", "m_Enabled", "m_Script"]
    assert unity._head_node(node) is head
    assert unity._head_node(TypeTreeNode(0, "X", "Base", -1, 1, m_Children=kids[:2])) is None


# ---------------------------------------------------------------- typetree values
def ref_value(ex, owner, v):
    if unity.is_pptr(v):
        return ex.ref(owner, v)
    if isinstance(v, dict):
        return {k: ref_value(ex, owner, x) for k, x in v.items()}
    if isinstance(v, list):
        return [ref_value(ex, owner, x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def test_value_fast_path(tmp_path):
    ex = Exporter(None, tmp_path)
    ex.ref = lambda owner, p: {"ref": p["m_PathID"]}
    rng = random.Random(5)
    for _ in range(200):
        v = [rng.random() for _ in range(rng.randrange(5))]
        doc = {"a": v, "b": [1, "x", None, True], "c": [{"m_FileID": 0, "m_PathID": 3}, 2.0], "d": [b"\x01", 1],
               "e": [[1, 2], [3]], "f": [], "g": {"h": [{"m_FileID": 1, "m_PathID": 0}]}}
        out = ex.value(None, doc)
        assert out == ref_value(ex, None, doc)
        assert out["a"] is not v and out["f"] == []           # new lists, as before


# ---------------------------------------------------------------- textures
class FakeTex:
    decodes = 0

    def __init__(self, data: bytes, w: int, h: int, fmt: int = 4, mode="RGBA"):
        self.data, self.m_Width, self.m_Height, self.m_TextureFormat, self.mode = data, w, h, fmt, mode
        self.object_reader = SimpleNamespace(version=(2022, 3, 62, 1), platform=13)
        self.m_PlatformBlob = []

    def get_image_data(self):
        return self.data

    @property
    def image(self):
        FakeTex.decodes += 1
        rng = np.random.default_rng(int.from_bytes(self.data[:8].ljust(8, b"\0"), "little"))
        bands = {"RGBA": 4, "RGB": 3, "L": 1}[self.mode]
        a = rng.integers(0, 256, (self.m_Height, self.m_Width, bands), dtype=np.uint8)
        return Image.fromarray(a[:, :, 0] if bands == 1 else a, self.mode)


def tex_obj(tex, name="t", pid=1, file="CAB-a"):
    tt = {"m_Name": name, "m_Width": tex.m_Width, "m_Height": tex.m_Height, "m_TextureFormat": tex.m_TextureFormat,
          "m_MipCount": 1, "m_ColorSpace": 1, "m_TextureSettings": {"m_FilterMode": 1, "m_WrapU": 1, "m_WrapV": 1}}
    return SimpleNamespace(read_typetree=lambda: copy.deepcopy(tt), read=lambda: tex, path_id=pid,
                           assets_file=SimpleNamespace(name=file), type=SimpleNamespace(name="Texture2D"))


def png_of(img, **kw):
    b = io.BytesIO()
    img.save(b, format="PNG", **kw)
    return b.getvalue()


def test_texture_png_cached_and_equal():
    t = FakeTex(b"abcdefgh" * 10, 16, 8)
    FakeTex.decodes = 0
    want = png_of(t.image)
    assert export.texture_png(t) == want
    assert export.texture_png(FakeTex(b"abcdefgh" * 10, 16, 8)) == want      # same inputs: no decode
    assert FakeTex.decodes == 2
    assert export.texture_png(FakeTex(b"abcdefgh" * 10, 8, 16)) != want      # other size: decoded
    cache.configure(enabled=False)
    assert export.texture_png(t) == want


def test_exporter_texture_files_immediate_and_deferred(tmp_path):
    objs = [tex_obj(FakeTex(bytes([i]) * 64, 8, 4), name=f"t{i}", pid=i) for i in range(4)]
    a = Exporter(None, tmp_path / "a")
    recs_a = [a.texture(o) for o in objs]
    b = Exporter(None, tmp_path / "b", defer_writes=True)
    recs_b = [b.texture(o) for o in objs]
    assert recs_a == recs_b
    assert not list((tmp_path / "b" / "textures").iterdir())
    skip = {recs_b[1]["texture"]}
    assert b.write_textures(skip=skip) == sorted(r["texture"] for r in recs_b if r["texture"] not in skip)
    ta = tree(tmp_path / "a")
    assert tree(tmp_path / "b") == {k: v for k, v in ta.items() if k not in skip}
    assert ta[recs_a[0]["texture"]] == png_of(objs[0].read().image)


def ref_pixels(tex_obj):
    tt = tex_obj.read_typetree()
    img = tex_obj.read().image
    if tt["m_TextureFormat"] == 1:
        a = np.asarray(img, np.uint8) if img.mode == "L" else np.asarray(img, np.uint8)[:, :, 3]
        arr = np.zeros(a.shape + (4,), np.uint8)
        arr[:, :, 3] = a
    else:
        arr = np.asarray(img.convert("RGBA"), np.uint8)
    return arr[::-1].copy()


@pytest.mark.parametrize("fmt,mode", [(4, "RGBA"), (3, "RGB"), (1, "L"), (1, "RGBA")])
def test_packer_pixels_and_png(tmp_path, fmt, mode):
    o = tex_obj(FakeTex(b"seed%d" % fmt + mode.encode(), 12, 6, fmt, mode))
    FakeTex.decodes = 0
    want = ref_pixels(o)
    p1, p2 = TexelPacker(tmp_path / "1"), TexelPacker(tmp_path / "2")
    a = p1.pixels(o)
    assert np.array_equal(a, want) and not a.flags.writeable
    assert np.array_equal(p2.pixels(o), want)
    assert FakeTex.decodes == 2                              # the reference, then one decode for both packers
    for p in (p1, p2):
        p.add("g", o, 0, 0, 12, 6)
        p.add("g", o, -1, -1, 3, 3)
    r1, r2 = p1.build(), p2.build()
    assert r1 == r2 and tree(tmp_path / "1") == tree(tmp_path / "2")
    out = tree(tmp_path / "1")["textures/g.png"]
    arr = np.asarray(Image.open(io.BytesIO(out)))
    assert out == png_of(Image.fromarray(arr.copy(), "RGBA"), optimize=True)


def test_packed_png_matches_pillow():
    rgba = np.random.default_rng(1).integers(0, 256, (5, 7, 4), dtype=np.uint8)
    assert export.packed_png(rgba) == png_of(Image.fromarray(rgba[::-1].copy(), "RGBA"), optimize=True)


# ---------------------------------------------------------------- shaders
def code_entry(program: bytes, keywords=()) -> bytes:
    out = bytearray(b"\x01\0\0\0" + b"\x04\0\0\0" + bytes(16))
    out += len(keywords).to_bytes(4, "little")
    for k in keywords:
        kb = k.encode()
        out += len(kb).to_bytes(4, "little") + kb
        out += bytes((-len(out)) % 4)
    out += len(program).to_bytes(4, "little") + program
    return bytes(out)


def fake_shader(name: str, programs: list[bytes], pid=9):
    import lz4.block
    entries = [code_entry(p, ["KW_A"]) for p in programs]
    head = 4 + 12 * len(entries)
    table, offs = bytearray(len(entries).to_bytes(4, "little")), head
    body = bytearray()
    for e in entries:
        table += offs.to_bytes(4, "little") + len(e).to_bytes(4, "little") + (0).to_bytes(4, "little")
        offs += len(e)
        body += e
    raw = bytes(table + body)
    comp = lz4.block.compress(raw, store_size=False)
    subs = [[{"m_GpuProgramType": 4, "m_BlobIndex": i, "m_KeywordIndices": [0, 5]} for i in range(len(programs))]]
    tt = {"m_ParsedForm": {"m_Name": name, "m_PropInfo": {"m_Props": [{"m_Name": "_Color"}]},
                           "m_KeywordNames": ["KW_A"], "m_FallbackName": "",
                           "m_SubShaders": [{"m_Tags": {"t": 1}, "m_LOD": 100,
                                             "m_Passes": [{"m_State": {"m_Name": "P0", "x": b"\x01"},
                                                           "m_Tags": {}, "progVertex": {"m_PlayerSubPrograms": subs},
                                                           "progFragment": {"m_PlayerSubPrograms": subs}}]}]}}
    sh = SimpleNamespace(compressedBlob=comp, platforms=[9], offsets=[[0]], compressedLengths=[[len(comp)]],
                         decompressedLengths=[[len(raw)]])
    return SimpleNamespace(read_typetree=lambda: copy.deepcopy(tt), read=lambda: sh, path_id=pid,
                           get_raw_data=lambda: name.encode() + raw, class_id=48, platform=13,
                           serialized_type=SimpleNamespace(old_type_hash=b"\x02" * 16),
                           assets_file=SimpleNamespace(name="CAB-s", unity_version="2022.3.62f1"),
                           type=SimpleNamespace(name="Shader"))


def ref_dump_objects(shaders, out_dir: Path, source: str, index: list) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    done = {r["name"] for r in index}
    for o in shaders:
        tt = o.read_typetree()
        name = tt.get("m_ParsedForm", {}).get("m_Name") or f"shader_{o.path_id}"
        if name in done:
            continue
        done.add(name)
        safe = shader._safe(name)
        jsonio.write_json(out_dir / f"{safe}.json", shader._parsed_summary(tt), default=str)
        rec = {"name": name, "source": source, "parsed": f"{safe}.json", "variants": []}
        for plat, si, pi, stage, n, t, kws, code in shader.variants(tt, shader.platform_blobs(o.read())):
            tag = shader.PLATFORM.get(plat, str(plat))
            pdir = out_dir / safe / tag
            pdir.mkdir(parents=True, exist_ok=True)
            fn = f"s{si}p{pi}_{stage}_{n}.glsl"
            (pdir / fn).write_bytes(code)
            rec["variants"].append({"file": f"{safe}/{tag}/{fn}", "platform": tag, "subShader": si, "pass": pi,
                                    "stage": stage, "type": t, "keywords": kws})
        index.append(rec)
    return index


def test_shader_dump_replay_equals_reference(tmp_path):
    objs = [fake_shader("Test/One", [b"#version 300 es\nvoid main(){}", b"x"]), fake_shader("Test/Two:x", [b"y"]),
            fake_shader("Test/One", [b"other"], pid=3)]
    ref = ref_dump_objects(objs, tmp_path / "ref", "src", [])
    shader.write_index(ref, tmp_path / "ref")
    for run in ("miss", "hit"):
        idx = shader.dump_objects(objs, tmp_path / run, "src", [])
        shader.write_index(idx, tmp_path / run)
        assert idx == ref
        assert tree(tmp_path / run) == tree(tmp_path / "ref")
    assert shader.DUMPS.stats()["hits"] >= 2


# ---------------------------------------------------------------- cue sheets
class FakeTools:
    """vgmstream / ffmpeg stand-ins: metadata per stream, deterministic 'decoded' and 'encoded' bytes."""

    def __init__(self, names):
        self.names, self.calls, self.lock = names, [], threading.Lock()

    def __call__(self, args, **kw):
        with self.lock:
            self.calls.append(args[1:3])
        exe = Path(args[0]).name
        if exe == "vgmstream" and args[1] == "-m":
            i = int(args[3])
            loop = f"loop start: {i} samples\nloop end: {i * 90} samples\n" if i % 2 else ""
            out = (f"stream count: {len(self.names)}\nstream name: {self.names[i - 1]}\nsample rate: 48000 Hz\n"
                   f"channels: 2\nstream total samples: {i * 1000} (0:01.000 seconds)\n{loop}")
            return subprocess.CompletedProcess(args, 0, out, "")
        if exe == "vgmstream":
            Path(args[args.index("-o") + 1]).write_bytes(b"RIFF" + args[3].encode() * 100)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        src = Path(args[args.index("-i") + 1]).read_bytes()
        Path(args[-1]).write_bytes(("|".join(args[args.index("-i") + 2:-1])).encode() + src)
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for n in ("vgmstream", "ffmpeg"):
        (bin_dir / n).write_bytes(b"#!")
    monkeypatch.setattr(cri, "tool", lambda name, exe: str(bin_dir / name))
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": b"@UTF" + sheet.encode()}, cri.EMBEDDED_ACB))
    fake = FakeTools(["a", "b", "a", "c", "c"])
    monkeypatch.setattr(cri.subprocess, "run", fake)
    return fake


def ref_decode(sheet, out_dir: Path, fmt, level=cri.FLAC_LEVEL):
    """The sequential decode as it was (same tool calls)."""
    vgm, ffmpeg = cri.tool("vgmstream", ""), cri.tool("ffmpeg", "")
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    acb = work / f"{sheet}.acb"
    acb.write_bytes(b"@UTF" + sheet.encode())

    def meta(i):
        r = cri.subprocess.run([vgm, "-m", "-s", str(i), str(acb)], capture_output=True, text=True)
        return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
    first = meta(1)
    result, manifest, streams, stems = {}, {}, [], set()
    for i in range(1, int(first["stream count"]) + 1):
        info = first if i == 1 else meta(i)
        name = info["stream name"]
        stem = name if name not in manifest else f"{name}_{i}"
        stems.add(stem)
        wav = work / f"{stem}.wav"
        cri.subprocess.run([vgm, "-i", "-s", str(i), "-o", str(wav), str(acb)], check=True, capture_output=True)
        dst = out_dir / f"{stem}.{fmt}"
        codec = {"flac": ["-c:a", "flac", "-compression_level", str(level)], "ogg": ["-c:a", "libvorbis", "-q:a", "5"]}
        cri.subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), *codec[fmt], str(dst)], check=True)
        wav.unlink()
        entry = {"file": dst.name, "sampleRate": cri._samples(info.get("sample rate")),
                 "channels": cri._samples(info.get("channels")),
                 "samples": cri._samples(info.get("stream total samples"))}
        if "loop start" in info:
            entry["loopStart"], entry["loopEnd"] = cri._samples(info["loop start"]), cri._samples(info["loop end"])
        streams.append({"stream": i, "name": name, **entry})
        if name not in manifest:
            manifest[name], result[name] = entry, dst
    jsonio.write_json(out_dir / "cues.json", manifest, ensure_ascii=True)
    jsonio.write_json(out_dir / "streams.json", streams, ensure_ascii=True)
    import shutil
    shutil.rmtree(work)
    return {k: v.name for k, v in result.items()}


@pytest.mark.parametrize("fmt", ["flac", "ogg"])
def test_decode_parallel_and_cached_equal_sequential(tmp_path, tools, fmt):
    want = ref_decode("Sheet", tmp_path / "ref", fmt)
    for run, workers in (("w1", 1), ("w4", 4), ("hit", 4)):
        if run != "hit":
            cache.clear()                                     # decoded, not replayed
        n = len(tools.calls)
        got = cri.decode(None, "Sheet", tmp_path / run, key=0, fmt=fmt, workers=workers)
        assert {k: v.name for k, v in got.items()} == want and all(v.parent == tmp_path / run for v in got.values())
        assert tree(tmp_path / run) == tree(tmp_path / "ref")
        assert (len(tools.calls) == n) == (run == "hit")      # replayed: no tool ran


def test_decode_disk_cache_and_levels(tmp_path, tools):
    cache.configure(directory=tmp_path / "c")
    cri.decode(None, "S", tmp_path / "a", key=0, flac_level=0)
    cache.clear()                                               # another process: the disk layer
    n = len(tools.calls)
    cri.decode(None, "S", tmp_path / "b", key=0, flac_level=0)
    assert len(tools.calls) == n and tree(tmp_path / "a") == tree(tmp_path / "b")
    cri.decode(None, "S", tmp_path / "c12", key=0)              # another level: decoded again
    assert len(tools.calls) > n
    assert tree(tmp_path / "a")["a.flac"] != tree(tmp_path / "c12")["a.flac"]


def test_decode_refuses_a_sheet_with_an_external_awb(tmp_path, tools, monkeypatch):
    monkeypatch.setattr(cri, "acb_data", lambda cat, sheet: ({"acb": b"@UTF x", "awb": b"AFS2 y"}, cri.RAW_ACB))
    with pytest.raises(NotImplementedError, match="^cue sheet Voices: external AWB \\(streamed waveforms\\) not "
                                                  "supported$"):
        cri.decode(None, "Voices", tmp_path / "o", key=0)
    assert tools.calls == [] and not (tmp_path / "o").exists()


def test_decode_also_encodes_from_the_same_samples(tmp_path, tools):
    args = ["-c:a", "aac", "-b:a", "160k"]
    got = cri.decode(None, "S", tmp_path / "x", key=0, also=(".m4a", args))
    files = tree(tmp_path / "x")
    assert sorted(f for f in files if f.endswith(".m4a")) == ["a.m4a", "a_3.m4a", "b.m4a", "c.m4a", "c_5.m4a"]
    assert json.loads(files["cues.json"])["a"]["file"] == "a.flac"          # manifests name the first file
    wav = b"RIFF" + b"1" * 100
    assert files["a.m4a"] == "|".join(cri.encode_args("w", "d", args)[6:-1]).encode() + wav
    assert {k: v.name for k, v in got.items()} == {"a": "a.flac", "b": "b.flac", "c": "c.flac"}
    n = len(tools.calls)
    cri.decode(None, "S", tmp_path / "y", key=0, also=(".m4a", args))
    assert len(tools.calls) == n and tree(tmp_path / "y") == files
    with pytest.raises(ValueError):
        cri.decode(None, "S", tmp_path / "z", key=0, also=(".flac", args))


def test_hca_key_read_once(tmp_path, monkeypatch):
    apk = tmp_path / "base.apk"
    apk.write_bytes(b"PK")
    calls = []
    monkeypatch.setattr(cri.crikey, "find_key", lambda p: calls.append(p) or 1234)
    assert cri.hca_key(apk) == cri.hca_key(apk) == 1234 and len(calls) == 1
    apk.write_bytes(b"PK2")
    assert cri.hca_key(apk) == 1234 and len(calls) == 2


def test_acb_data_and_layout_memo(tmp_path, monkeypatch):
    b = tmp_path / "x.bundle"
    b.write_bytes(b"UnityFS")
    cat = SimpleNamespace(fetch_key=lambda key: [b])
    scans = []
    impl = {"data": {"data": list(b"@UTFdata")}}

    def bundle_asset(cat, sheet):
        scans.append(sheet)
        return (cri.EMBEDDED_ACB, None, {"awb": None}, impl)
    monkeypatch.setattr(cri, "_bundle_asset", bundle_asset)
    assert cri.layout(cat, "S") == cri.EMBEDDED_ACB and len(scans) == 1
    assert cri.acb_data(cat, "S") == ({"acb": b"@UTFdata"}, cri.EMBEDDED_ACB) and len(scans) == 2
    assert cri.acb_data(cat, "S") == ({"acb": b"@UTFdata"}, cri.EMBEDDED_ACB) and len(scans) == 2
    assert cri.layout(cat, "S") == cri.EMBEDDED_ACB and len(scans) == 2
    b.write_bytes(b"UnityFS2")                                 # another bundle file: read again
    cri.acb_data(cat, "S")
    assert len(scans) == 3


# ---------------------------------------------------------------- bundle closures
def test_load_closure_shares_per_thread(tmp_path, monkeypatch):
    files = []
    for i in range(3):
        f = tmp_path / f"b{i}"
        f.write_bytes(b"x" * (i + 1))
        files.append(f)
    loads = []
    monkeypatch.setattr(unity.UnityPy, "load", lambda *p: loads.append(p) or object())
    a = unity.load_closure(files[:2])
    assert unity.load_closure(files[:2]) is a and len(loads) == 1
    assert unity.load_closure(files[1::-1]) is not a              # another order is another environment
    other = []
    t = threading.Thread(target=lambda: other.append(unity.load_closure(files[:2])))
    t.start()
    t.join()
    assert other[0] is not a                                      # per thread
    files[0].write_bytes(b"changed")
    assert unity.load_closure(files[:2]) is not a
    cache.configure(closures=1)
    x = unity.load_closure(files[:1])
    unity.load_closure(files[1:2])
    assert unity.load_closure(files[:1]) is not x                 # evicted (one closure kept)
    cache.configure(enabled=False)
    assert unity.load_closure(files[:1]) is not unity.load_closure(files[:1])
