"""scripts/atom_eval.py (the atom evaluation harness) on synthetic bundles: sampling, the comparisons, m1 with
candidate texture decoders and PNG encoders, and the comparison of two stage results."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import atom_eval  # noqa: E402
from fakeunity import FakeEnvironment, FakeTexture2D, rgba_bytes  # noqa: E402
from nnnotes.atoms import Impl, resolve  # noqa: E402
from nnnotes.atoms.mesh import Channel, MeshArrays, Primitive  # noqa: E402
from nnnotes.atoms.reader import UnityBundle  # noqa: E402

RGBA32, RGB24 = 4, 3


# ---------------------------------------------------------------- a fake reader and candidates (by module:attribute)
def _open(data: bytes) -> UnityBundle:
    """A bundle described as JSON: {"textures": [[pathId, width, height, format, seed], ...]}."""
    doc = json.loads(data)
    env = FakeEnvironment()
    f = env.file("CAB-test")
    for pid, w, h, fmt, seed in doc["textures"]:
        channels = 3 if fmt == RGB24 else 4
        tex = FakeTexture2D(rgba_bytes(w, h, seed, channels), w, h, fmt, name=f"t{pid}")
        f.add(pid, "Texture2D", {"m_Name": f"t{pid}", "m_Width": w, "m_Height": h, "m_TextureFormat": fmt},
              obj=tex, raw=b"x" * 8)
    return UnityBundle(env)


def fake_reader() -> Impl:
    return Impl("fake/1", _open, lambda facts: {"cpuSeconds": 0.0, "peakBytes": 0})


def same_decode() -> Impl:
    return Impl("same/1", resolve("texture.decode").fn, resolve("texture.decode").cost)


def flipped_decode() -> Impl:
    ref = resolve("texture.decode").fn
    return Impl("flipped/1", lambda inp: ref(inp).transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                resolve("texture.decode").cost)


def rgba_png() -> Impl:
    """A lossy candidate for RGB images: always writes RGBA."""
    def encode(image, level=6):
        buf = io.BytesIO()
        image.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()
    return Impl("rgba/1", encode, resolve("png.encode").cost)


def _bundles(tmp_path: Path) -> Path:
    d = tmp_path / "bundles"
    d.mkdir()
    (d / "a.bundle").write_text(json.dumps({"textures": [[1, 8, 4, RGBA32, 1], [2, 5, 3, RGB24, 2]]}))
    (d / "b.bundle").write_text(json.dumps({"textures": [[7, 4, 4, RGBA32, 3], [8, 0, 0, RGBA32, 0]]}))
    return d


def _m1(d: Path, atoms, checks) -> dict:
    cfg = {"dir": str(d), "atoms": atoms, "checks": checks, "threads": 4,
           "reference_reader": "test_atom_eval:fake_reader"}
    return atom_eval.run_m1(cfg, atom_eval.bundle_list(d, None), workers=1)


# ---------------------------------------------------------------- m1
def test_m1_texture_decoders_per_format(tmp_path):
    d = _bundles(tmp_path)
    out = _m1(d, [("reader", "test_atom_eval:fake_reader"), ("texture.decode", "test_atom_eval:same_decode"),
                  ("texture.decode", "test_atom_eval:flipped_decode")], ("texture",))
    assert out["errors"] == []
    c = out["counts"]["texture"]
    assert c["RGBA32 | same/1"] == {"equal": 2, "refused.both": 1}          # the 0x0 texture: empty.texture
    assert c["RGB24 | same/1"] == {"equal": 1}
    assert c["RGBA32 | flipped/1"] == {"differ": 2, "refused.both": 1}
    ex = out["examples"]["texture"]["RGBA32 | flipped/1"]["differ"]
    assert ex and "CAB-test:" in ex[0] and "max" in ex[0]


def test_m1_png_round_trip_and_bytes(tmp_path):
    d = _bundles(tmp_path)
    out = _m1(d, [("png.encode", "atom_eval_pillow:optimize"), ("png.encode", "atom_eval_pillow:level6"),
                  ("png.encode", "test_atom_eval:rgba_png")], ("png",))
    c, s = out["counts"]["png"], out["stats"]["png"]
    opt = [k for k in c if k.startswith("RGBA32 | ") and "optimize" in k][0]
    l6 = [k for k in c if k.startswith("RGB24 | ") and "level6" in k][0]
    assert c[opt] == {"equal": 2}
    assert c[l6] == {"equal": 1} and s[l6]["sameBytesAsReference"] == 1      # level 6 = the reference's bytes
    assert c["RGB24 | rgba/1"] == {"differ": 1}                               # RGBA written for an RGB image
    assert c["RGBA32 | rgba/1"] == {"equal": 2}
    assert s["RGBA32"]["bytes:reference"] > 0 and s["RGBA32"]["rawBytes"] == (8 * 4 + 4 * 4) * 4


def test_m1_determinism_check(tmp_path):
    d = _bundles(tmp_path)
    out = _m1(d, [("texture.decode", "test_atom_eval:same_decode"), ("png.encode", "atom_eval_pillow:level1")],
              ("det",))
    det = out["counts"]["det"]
    assert det["same/1"] == {"equal": 2}
    assert [v for k, v in det.items() if "level1" in k] == [{"equal": 2}]


def test_m1_determinism_of_an_encoder_alone(tmp_path):
    d = _bundles(tmp_path)
    out = _m1(d, [("png.encode", "atom_eval_pillow:level1")], ("det",))
    assert list(out["counts"]["det"].values()) == [{"equal": 2}]


def test_m1_objects_against_itself(tmp_path):
    d = _bundles(tmp_path)
    out = _m1(d, [("reader", "test_atom_eval:fake_reader")], ("objects",))
    c = out["counts"]["objects"]
    assert c["set"] == {"equal": 2} and c["classId"] == {"equal": 4} and c["raw"] == {"equal": 4}
    assert c["externals"] == {"equal": 2} and c["container"] == {"equal": 2}


# ---------------------------------------------------------------- sample
def test_sample_is_seeded_and_stratified(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    for i, g in enumerate(["alpha", "alpha", "beta", "gamma", "gamma", "gamma"]):
        (d / f"{g}_assets_x{i}_{i:032x}.bundle").write_bytes(b"\0" * (10 + i))
    args = dict(dir=str(d), bundles=None, seed=3, largest=1, random=1, out=None)
    a = atom_eval.cmd_sample(type("A", (), args))
    b = atom_eval.cmd_sample(type("A", (), args))
    assert a == b
    assert a["groups"] == 3 and a["population"] == 6
    groups = {n.split("_assets_")[0] for n, why in a["why"].items() if "group" in why}
    assert groups == {"alpha", "beta", "gamma"}
    assert [n for n, why in a["why"].items() if "largest" in why] == ["gamma_assets_x5_" + f"{5:032x}.bundle"]
    assert sum("random" in why for why in a["why"].values()) == 1
    other = atom_eval.cmd_sample(type("A", (), {**args, "seed": 4}))
    assert other["population"] == 6


# ---------------------------------------------------------------- comparisons
def test_image_and_png_comparisons():
    a = Image.fromarray(np.arange(48, dtype=np.uint8).reshape(4, 3, 4), "RGBA")
    assert atom_eval.image_diff(a, a.copy()) == ("equal", None)
    b = a.copy()
    b.putpixel((0, 0), (9, 1, 2, 15))
    st, what = atom_eval.image_diff(a, b)
    assert st == "differ" and what.startswith("max 12, 2 of 48 values")
    assert atom_eval.image_diff(a, a.convert("RGB"))[1] == "mode RGBA vs RGB"
    buf = io.BytesIO()
    a.save(buf, format="PNG", compress_level=1)
    assert atom_eval.png_roundtrip(buf.getvalue(), a) == ("equal", None)
    d = atom_eval.rgba_diff(a, b)
    assert d["rgbMax"] == 9 and d["alphaMax"] == 12


def test_deep_diff():
    assert atom_eval.deep_diff({"a": [1, 2.5, float("nan")], "b": b"x"}, {"a": (1, 2.5, float("nan")), "b": b"x"}) \
        is None
    assert atom_eval.deep_diff({"a": 1, "b": 2}, {"b": 2, "a": 1}).startswith(": keys")
    assert atom_eval.deep_diff({"a": [1, 2]}, {"a": [1, 3]}) == ".a[1]: 2 vs 3"
    assert atom_eval.deep_diff({"a": 1}, {"a": 1.0}) == ".a: int vs float"
    assert atom_eval.deep_diff({"a": True}, {"a": 1}) is not None


def _mesh(**changes) -> MeshArrays:
    pos = np.arange(9, dtype=np.float32).reshape(3, 3)
    ch = {"position": Channel(pos), "normal": Channel(np.zeros((3, 4), np.float16))}
    m = MeshArrays("m", 3, ch, [Primitive("triangles", np.array([[0, 1, 2]]), 0)], [])
    for k, v in changes.items():
        setattr(m, k, v)
    return m


def test_mesh_diff():
    assert atom_eval.mesh_diff(_mesh(), _mesh()) == []
    other = _mesh()
    other.channels = {"position": other.channels["position"],
                      "normal": Channel(np.zeros((3, 3), np.float32))}
    assert atom_eval.mesh_diff(_mesh(), other) == ["dtype:normal"]
    fewer = _mesh()
    fewer.channels = {"position": fewer.channels["position"]}
    assert atom_eval.mesh_diff(_mesh(), fewer) == ["missing:normal"]
    wound = _mesh(primitives=[Primitive("triangles", np.array([[0, 2, 1]]), 0)])
    assert atom_eval.mesh_diff(_mesh(), wound) == ["primitives:indices"]


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def test_compare_results():
    img = Image.fromarray(np.full((2, 2, 4), 7, np.uint8), "RGBA")
    blobs = {"p1": _png(img), "p2": atom_eval_png6(img), "j1": b"{}", "j2": b"[]"}

    def art(aid, sha, ext, cls):
        return {"id": aid, "content": {"sha256": sha, "size": len(blobs[sha]), "ext": ext},
                "provenance": {"object": {"class": cls}}}
    item = {"object": "F:1", "status": "exported", "class": "Texture2D"}
    ra = {"status": "ok", "items": [item, {"object": "F:2", "status": "exported", "class": "Material"}],
          "artifacts": [art("F:1#image", "p1", "png", "Texture2D"), art("F:2#json", "j1", "json", "Material")]}
    rb = {"status": "ok", "items": [item, {"object": "F:2", "status": "generic", "class": "Material",
                                           "reason": {"code": "generic.error", "message": "x"}}],
          "artifacts": [art("F:1#image", "p2", "png", "Texture2D"), art("F:2#json", "j2", "json", "Material")]}
    T = atom_eval.Tally()
    atom_eval.compare_results(ra, rb, blobs.__getitem__, blobs.__getitem__, T, "s")
    d = T.dump()
    assert d["counts"]["artifacts"]["Texture2D | image"] == {"equal-pixels": 1}
    assert d["counts"]["artifacts"]["Material | json"] == {"differ": 1}
    assert d["counts"]["items"]["Material"] == {"differ": 1} and d["counts"]["items"]["Texture2D"] == {"equal": 1}


def atom_eval_png6(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def test_parse_params_and_atoms():
    assert atom_eval.parse_params(["png.level=1", "blobMin=100"]) == {"png": {"level": 1}, "blobMin": 100}
    assert atom_eval.parse_atoms(["png.encode=m:a"]) == [("png.encode", "m:a")]
    with pytest.raises(SystemExit):
        atom_eval.parse_atoms(["nope=m:a"])
