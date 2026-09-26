"""texture.decode, png.encode and astc.container on synthetic textures (decode = UnityPy's parse_image_data; PNG bytes
= export.texture_png's), the reader's objects, containers and texture inputs through a fake environment, and the
atom registry."""
import io
import re
import struct
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from UnityPy.export.Texture2DConverter import parse_image_data

from fakeunity import ANDROID, VERSION, FakeEnvironment, FakeTexture2D, rgba_bytes
from nnnotes import export
from nnnotes.atoms import ATOMS, Impl, Unsupported, astc, env_name, ids, impl_id, png, resolve
from nnnotes.atoms.reader import ObjectInfo, UnityBundle
from nnnotes.atoms.texture import ASTC_BLOCKS, TextureInput, decode, is_hdr_astc

RGBA32, RGB24, ALPHA8, ARGB4444, YUY2 = 4, 3, 1, 2, 21
ASTC_4x4, ASTC_6x6, ASTC_HDR_6x6 = 54, 50, 68


def astc_blocks(w, h, block, seed=0):
    """ASTC data: random blocks and constant-colour (void-extent) blocks."""
    rng = np.random.default_rng(seed)
    n = astc.level0_size(w, h, block) // 16
    out = bytearray()
    for i in range(n):
        if i % 2:
            out += bytes([0xFC, 0xFD]) + b"\xff" * 6 + rng.integers(0, 65536, 4).astype("<u2").tobytes()
        else:
            out += rng.integers(0, 256, 16, dtype=np.uint8).tobytes()
    return bytes(out)


def fake(fmt, w=20, h=14, seed=0, extra=b""):
    if fmt in ASTC_BLOCKS:
        data = astc_blocks(w, h, ASTC_BLOCKS[fmt], seed)
    else:
        data = rgba_bytes(w, h, seed, {RGBA32: 4, RGB24: 3, ALPHA8: 1, ARGB4444: 2}[fmt])
    return FakeTexture2D(data + extra, w, h, fmt)


def inp(tex):
    return TextureInput(bytes(tex.get_image_data()), tex.m_Width, tex.m_Height, tex.m_TextureFormat, VERSION,
                        ANDROID, bytes(tex.m_PlatformBlob))


FORMATS = [RGBA32, RGB24, ALPHA8, ARGB4444, ASTC_6x6, ASTC_4x4]


# ---------------------------------------------------------------- texture.decode, png.encode
@pytest.mark.parametrize("fmt", FORMATS)
def test_decode_is_parse_image_data(fmt):
    tex = fake(fmt)
    ours = decode(inp(tex))
    theirs = parse_image_data(tex.get_image_data(), tex.m_Width, tex.m_Height, fmt, VERSION, ANDROID, [], True)
    assert (ours.mode, ours.size) == (theirs.mode, theirs.size) and ours.tobytes() == theirs.tobytes()


@pytest.mark.parametrize("fmt", FORMATS)
def test_png_bytes_equal_export_texture_png(fmt):
    tex = fake(fmt, seed=fmt)
    assert png.encode(decode(inp(tex))) == export.texture_png(tex)


@pytest.mark.parametrize("fmt", [RGBA32, ASTC_6x6])
def test_further_mip_levels_do_not_change_the_image(fmt):
    base = fake(fmt, 24, 18, seed=3)
    mips = fake(fmt, 24, 18, seed=3, extra=b"\x5a" * 777)
    assert decode(inp(mips)).tobytes() == decode(inp(base)).tobytes()


def test_decode_input_key_is_export_decode_inputs():
    tex = fake(RGBA32)
    assert inp(tex).key() == export._decode_inputs(tex)


@pytest.mark.parametrize("fmt", [f for f in range(66, 72)])
def test_hdr_astc_is_refused(fmt):
    assert is_hdr_astc(fmt)
    with pytest.raises(Unsupported) as e:
        decode(TextureInput(b"\0" * 1024, 8, 8, fmt))
    assert e.value.code == "unsupported.texture.format" and "HDR ASTC" in e.value.message


@pytest.mark.parametrize("w, h", [(0, 0), (0, 16), (16, 0)])
def test_texture_without_pixels_is_refused(w, h):
    with pytest.raises(Unsupported) as e:
        decode(TextureInput(b"", w, h, RGBA32))
    assert e.value.code == "empty.texture"


@pytest.mark.parametrize("fmt", [YUY2, 99])
def test_formats_the_decoder_does_not_implement(fmt):
    with pytest.raises(Unsupported) as e:
        decode(TextureInput(b"\0" * 64, 4, 4, fmt))
    assert e.value.code == "unsupported.texture.format"


@pytest.mark.parametrize("mode", ["RGBA", "RGB", "L", "LA"])
def test_png_is_lossless_at_every_level(mode):
    bands = len(mode)
    a = np.random.default_rng(5).integers(0, 256, (13, 17, bands), dtype=np.uint8)
    img = Image.fromarray(a[:, :, 0] if bands == 1 else a, mode)
    default = io.BytesIO()
    img.save(default, format="PNG")
    assert png.encode(img) == default.getvalue()                   # level 6 = Pillow's default bytes
    for level in range(10):
        back = Image.open(io.BytesIO(png.encode(img, level)))
        assert back.mode == mode and back.tobytes() == img.tobytes()


@pytest.mark.parametrize("level", [-1, 10, 6.0, True, None])
def test_png_level_must_be_0_to_9(level):
    with pytest.raises(ValueError, match="PNG level"):
        png.encode(Image.new("RGB", (1, 1)), level)


# ---------------------------------------------------------------- astc.container
def test_astc_container_header_and_first_level():
    blocks = astc_blocks(340, 340, (6, 6))
    assert len(blocks) == 57 * 57 * 16
    out = astc.container(blocks + b"\x01" * 4096, 340, 340, (6, 6))       # further mip levels are cut
    assert out[:4] == bytes([0x13, 0xAB, 0xA1, 0x5C]) and out[4:7] == bytes([6, 6, 1])
    assert out[7:16] == (340).to_bytes(3, "little") * 2 + (1).to_bytes(3, "little")
    assert out[16:] == blocks
    assert astc.parse_header(out) == {"block": (6, 6, 1), "size": (340, 340, 1)}
    assert struct.unpack_from("<I", out)[0] == astc.MAGIC


def test_astc_container_refusals():
    with pytest.raises(ValueError, match="bytes, 64 needed"):
        astc.container(b"\0" * 48, 8, 8, (4, 4))
    with pytest.raises(ValueError, match="not a 2D ASTC block size"):
        astc.container(b"\0" * 64, 8, 8, (7, 7))
    with pytest.raises(ValueError, match="sizes must be"):
        astc.container(b"", 0, 8, (4, 4))
    with pytest.raises(ValueError, match="magic"):
        astc.parse_header(b"\0" * 16)


def test_astc_blocks_by_format():
    assert ASTC_BLOCKS[ASTC_HDR_6x6] == (6, 6) and ASTC_BLOCKS[ASTC_4x4] == (4, 4) and ASTC_BLOCKS[53] == (12, 12)


# ---------------------------------------------------------------- reader
def bundle():
    env = FakeEnvironment()
    a = env.file("CAB-aa", externals=["archive:/CAB-bb/CAB-bb", "Library/unity default resources"])
    inline = fake(RGBA32, 8, 6, seed=1)
    streamed_data = rgba_bytes(8, 4, 2)
    streamed = FakeTexture2D(b"", 8, 4, RGBA32, stream=type("S", (), {"path": "archive:/CAB-aa/CAB-aa.resS",
                                                                      "offset": 32, "size": len(streamed_data)})())
    empty = FakeTexture2D(b"", 0, 0, RGBA32, stream=type("S", (), {"path": "", "offset": 0, "size": 0})())
    env.resources["CAB-aa.resS"] = b"\0" * 32 + streamed_data
    a.add(1, "AssetBundle", {"m_Name": "b"}, raw=b"ab")
    a.add(-5, "Texture2D", {"m_Name": "inline"}, inline, raw=b"t" * 10, type_hash=bytes(range(16)))
    a.add(7, "Texture2D", {"m_Name": "streamed"}, streamed)
    a.add(9, "Texture2D", {"m_Name": "empty"}, empty)
    a.contain("assets/x/inline.png", -5)
    a.contain("assets/x/other.png", 42, file_id=1)
    b = env.file("CAB-aa.sharedAssets")
    b.add(3, "TextAsset", {"m_Name": "t", "m_Script": "hi"})
    return env, UnityBundle(env), inline, streamed_data


def test_reader_lists_files_objects_and_containers():
    env, view, _, _ = bundle()
    assert view.files() == ["CAB-aa", "CAB-aa.sharedAssets"]
    objs = view.objects()
    assert [(o.file, o.path_id, o.class_name) for o in objs] == [
        ("CAB-aa", 1, "AssetBundle"), ("CAB-aa", -5, "Texture2D"), ("CAB-aa", 7, "Texture2D"),
        ("CAB-aa", 9, "Texture2D"), ("CAB-aa.sharedAssets", 3, "TextAsset")]
    tex = objs[1]
    assert tex == ObjectInfo("CAB-aa", -5, 28, "Texture2D", 10, bytes(range(16)).hex())
    assert tex.id == "CAB-aa:-5" and objs[0].type_hash is None
    assert view.raw(tex) == b"t" * 10 and view.typetree(objs[4]) == {"m_Name": "t", "m_Script": "hi"}
    assert view.container() == [("assets/x/inline.png", "CAB-aa", -5), ("assets/x/other.png", "CAB-bb", 42)]
    assert view.externals("CAB-aa") == ["archive:/CAB-bb/CAB-bb", "Library/unity default resources"]
    assert view.unity_version("CAB-aa") == "6000.3.12f1"


def test_reader_texture_inputs():
    env, view, inline, streamed_data = bundle()
    objs = {o.path_id: o for o in view.objects()}
    ti = view.texture_input(objs[-5])
    assert ti.key() == export._decode_inputs(inline)
    assert view.texture_input(objs[7]).data == streamed_data and env.lookups == ["CAB-aa.resS"]
    env.lookups.clear()
    empty = view.texture_input(objs[9])
    assert (empty.data, empty.width, empty.height) == (b"", 0, 0) and env.lookups == []   # nothing read
    with pytest.raises(Unsupported):
        decode(empty)


def test_reader_does_not_look_outside_the_bundle():
    env, view, _, _ = bundle()
    env.resources.clear()
    with pytest.raises(FileNotFoundError, match="not in the bundle"):
        view.texture_input([o for o in view.objects() if o.path_id == 7][0])


def test_reader_mesh_input_is_for_meshes_and_sprites():
    _, view, _, _ = bundle()
    with pytest.raises(TypeError, match="has no mesh"):
        view.mesh_input(view.objects()[1])


# ---------------------------------------------------------------- registry
REPLACEMENT = Impl("pillow-test/9", lambda image, level=6: b"replaced", lambda facts: {"cpuSeconds": 0.0,
                                                                                       "peakBytes": 0})


def test_every_atom_has_one_reference_with_versioned_id():
    assert set(ATOMS) == {"reader", "texture.decode", "png.encode", "astc.container", "sprite.crop", "mesh.arrays",
                          "gltf.write", "sniff"}
    for name in ATOMS:
        impl = ATOMS[name]
        assert re.fullmatch(r"(nnnotes|[a-z0-9.-]+-[^+/]+(\+[a-z0-9.-]+-[^+/]+)*)/\d+", impl.id), impl.id
        assert callable(impl.fn) and set(impl.cost({})) == {"cpuSeconds", "peakBytes"}
    assert ATOMS["texture.decode"].id.startswith("unitypy-") and "+pillow-" in ATOMS["texture.decode"].id
    assert ATOMS["png.encode"].id.startswith("pillow-") and ATOMS["png.encode"].fn is png.encode
    assert ids("png.encode", "astc.container") == {"astc.container": "nnnotes/1", "png.encode": ATOMS["png.encode"].id}
    assert impl_id((), 3) == "nnnotes/3" and impl_id(("no-such-dist",), 1) == "no-such-dist-absent/1"


def test_evaluation_override(monkeypatch):
    assert env_name("png.encode") == "NNNOTES_ATOM_PNG_ENCODE"
    monkeypatch.setenv("NNNOTES_ATOM_PNG_ENCODE", "test_atoms_texture:REPLACEMENT")
    assert resolve("png.encode") is REPLACEMENT and ATOMS["png.encode"].fn(None) == b"replaced"
    assert ids("png.encode") == {"png.encode": "pillow-test/9"}                 # keys change with the override
    monkeypatch.setenv("NNNOTES_ATOM_PNG_ENCODE", "no-colon")
    with pytest.raises(ValueError, match="module:attribute"):
        resolve("png.encode")
    monkeypatch.delenv("NNNOTES_ATOM_PNG_ENCODE")
    assert ATOMS["png.encode"].fn is png.encode
    with pytest.raises(KeyError):
        resolve("no.such.atom")


def test_override_with_the_reference_id_is_refused(monkeypatch):
    import test_atoms_texture as me
    monkeypatch.setattr(me, "SAME", Impl(ATOMS["png.encode"].id, png.encode, png.cost), raising=False)
    monkeypatch.setenv("NNNOTES_ATOM_PNG_ENCODE", "test_atoms_texture:SAME")
    with pytest.raises(ValueError, match="reference's id"):
        resolve("png.encode")


def test_listing_the_registry_loads_no_image_or_unity_library():
    code = ("import sys, nnnotes.atoms as a; a.ids(*a.ATOMS); "
            "print(sorted(m for m in ('UnityPy', 'PIL') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


# ---------------------------------------------------------------- docs as contract
def test_every_atom_and_reason_code_is_documented():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs" / "stages.md").read_text(encoding="utf-8")
    atoms_table = doc.split("## Atoms", 1)[1].split("## Reason codes", 1)[0]
    documented = set(re.findall(r"^\| `([a-z.]+)` \|", atoms_table, re.M))
    assert documented == set(ATOMS)
    codes = set(re.findall(r"^\| `([a-zA-Z_.]+)` \|", doc.split("## Reason codes", 1)[1], re.M))
    emitted = set()
    for src in (root / "src" / "nnnotes" / "atoms").glob("*.py"):
        emitted |= set(re.findall(r"\"((?:empty|unsupported|partial|generic|no_data)\.[a-zA-Z_.]+)\"",
                                  src.read_text(encoding="utf-8")))
    assert emitted and emitted <= codes, sorted(emitted - codes)
