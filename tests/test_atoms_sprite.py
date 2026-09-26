"""sprite.crop against UnityPy's SpriteHelper.get_image_from_sprite on the same synthetic sprites: texture-bound and
atlas-packed sprites (the atlas' texture rect and settings, the sprite's own mesh), rectangle and tight packing,
every packing rotation, masks, meshes with texture coordinates, alpha textures, fractional and overhanging rects;
then the canvas placement and the refusals."""
import numpy as np
import pytest
from UnityPy.export.SpriteHelper import get_image_from_sprite

from fakeunity import (ANDROID, VERSION, FakeTexture2D, pptr, quad, rect, render_data, rgba_bytes, sprite_tt,
                       unitypy_sprite)
from nnnotes.atoms import ATOMS, Unsupported, sprite
from nnnotes.atoms.mesh import arrays
from nnnotes.atoms.reader import mesh_input_from_typetree
from nnnotes.atoms.texture import TextureInput, decode

W, H = 64, 48
TEX = 3


def texture(fmt=4, seed=1, w=W, h=H):
    ch = {4: 4, 3: 3, 1: 1}[fmt]
    return FakeTexture2D(rgba_bytes(w, h, seed, ch), w, h, fmt)


def decoded(tex):
    return decode(TextureInput(tex.image_data, tex.m_Width, tex.m_Height, tex.m_TextureFormat, VERSION, ANDROID,
                               bytes(tex.m_PlatformBlob)))


def same(a, b):
    assert (a.mode, a.size) == (b.mode, b.size)
    assert a.tobytes() == b.tobytes()


def check(tt, tex, entry=None, alpha_tex=None):
    """Our crop (texture image + render data values + the sprite's mesh) equals UnityPy's image of the sprite."""
    textures = {TEX: tex}
    if alpha_tex is not None:
        textures[TEX + 1] = alpha_tex
    theirs = get_image_from_sprite(unitypy_sprite(tt, textures, entry))
    src = entry or tt["m_RD"]
    mesh = arrays(mesh_input_from_typetree(tt, VERSION))
    ours = ATOMS["sprite.crop"].fn(decoded(tex), src["textureRect"], src["settingsRaw"], mesh, tt["m_PixelsToUnits"],
                                   None if alpha_tex is None else decoded(alpha_tex))
    same(ours, theirs)
    return ours


def polygon(r, pivot=(0.5, 0.5), ppu=100.0):
    """A tight mesh inside rect `r` (a hexagon fanned from its centre), units relative to the pivot."""
    w, h = r["width"], r["height"]
    pts = [(0.5, 0.0), (1.0, 0.3), (1.0, 0.8), (0.5, 1.0), (0.0, 0.7), (0.1, 0.2)]
    px = [((u * w) - pivot[0] * w, (v * h) - pivot[1] * h) for u, v in pts]
    pos = np.array([[x / ppu, y / ppu, 0.0] for x, y in px + [(sum(p[0] for p in px) / 6, sum(p[1] for p in px) / 6)]],
                   np.float32)
    idx = [k for i in range(6) for k in (6, i, (i + 1) % 6)]
    return pos, idx


# ---------------------------------------------------------------- texture-bound sprites
@pytest.mark.parametrize("raw", [64, 0, 2, 66])          # tight bits (full rect / tight mesh), rectangle
def test_texture_bound_sprite(raw):
    r = rect(8, 4, 20, 16)
    pos, idx = quad(r)
    check(sprite_tt("s", r, render_data(pptr(TEX), r, raw, pos, idx)), texture())


def test_tight_mesh_mask_on_an_unpacked_sprite():
    r = rect(10, 6, 30, 24)
    pos, idx = polygon(r)
    out = check(sprite_tt("s", r, render_data(pptr(TEX), r, 0, pos, idx)), texture())
    alpha = np.asarray(out)[:, :, 3]
    assert (alpha == 0).any() and (alpha != 0).any()                     # masked, not a rectangle


def test_rgb_texture_gets_the_mask_as_alpha():
    r = rect(4, 4, 24, 20)
    pos, idx = polygon(r)
    out = check(sprite_tt("s", r, render_data(pptr(TEX), r, 0, pos, idx)), texture(fmt=3))
    assert out.mode == "RGBA"


@pytest.mark.parametrize("tr", [rect(8.5, 3.5, 20.25, 16.5), rect(56, 40, 16, 16), rect(-4, -2, 12, 10)])
def test_fractional_and_overhanging_rects(tr):
    pos, idx = quad(tr)
    check(sprite_tt("s", tr, render_data(pptr(TEX), tr, 2, pos, idx)), texture())


def test_alpha_texture_is_merged():
    r = rect(8, 8, 16, 16)
    pos, idx = quad(r)
    tt = sprite_tt("s", r, render_data(pptr(TEX), r, 2, pos, idx, alpha=pptr(TEX + 1)))
    check(tt, texture(), alpha_tex=texture(seed=9))


# ---------------------------------------------------------------- atlas-packed sprites (texture only in the atlas)
def atlas_case(raw, tight_mesh=False, rotated=False):
    r = rect(0, 0, 18, 12)                                  # the sprite's own rect
    tr = rect(20, 10, 12, 18) if rotated else rect(20, 10, 18, 12)
    pos, idx = polygon(r) if tight_mesh else quad(r)
    rd = render_data(pptr(), rect(0, 0, 18, 12), 64, pos, idx)          # m_RD without a texture
    tt = sprite_tt("s", r, rd, atlas=pptr(77, 1), atlas_tags=["atlas"])
    entry = render_data(pptr(TEX), tr, raw, pos, idx)
    return tt, entry


@pytest.mark.parametrize("rotation", [0, 1, 2, 3, 4])
def test_atlas_rectangle_packing_every_rotation(rotation):
    tt, entry = atlas_case(1 | 2 | rotation << 2, rotated=rotation == 4)
    check(tt, texture(), entry)


@pytest.mark.parametrize("rotation", [0, 1, 3])
def test_atlas_tight_packing_is_masked_with_the_sprites_mesh(rotation):
    tt, entry = atlas_case(1 | rotation << 2, tight_mesh=True)
    out = check(tt, texture(), entry)
    assert (np.asarray(out)[:, :, 3] == 0).any()


def test_settings_bits():
    assert sprite.settings(1 | 2 | 3 << 2 | 1 << 6) == sprite.Settings(True, 1, 3, 1)
    assert sprite.settings(64) == sprite.Settings(False, 0, 0, 1)


# ---------------------------------------------------------------- tight sprites drawn from texture coordinates
def uv_case(scale=1.0):
    r = rect(8, 4, 20, 16)
    pos, idx = polygon(r)
    px = pos[:, :2] * 100.0 + [r["width"] / 2 + r["x"], r["height"] / 2 + r["y"]]      # texel position of each vertex
    uv = px / [W, H]
    tt = sprite_tt("s", r, render_data(pptr(TEX), r, 0, pos * scale, idx, uv=uv))
    return tt


@pytest.mark.parametrize("scale", [1.0, 1.5])           # same-size triangles (copied) and resized ones (affine)
def test_tight_sprite_with_texture_coordinates(scale):
    check(uv_case(scale), texture())


@pytest.mark.parametrize("case", ["tight", "uv"])
def test_mesh_values_carry_the_sprite_without_its_bundle(case):
    """A task holding only the atlas texture image and the sprite's values (JSON) crops the same image."""
    import json
    if case == "tight":
        tt, entry = atlas_case(1 | 1 << 2, tight_mesh=True)
    else:
        tt, entry = uv_case(1.5), None
    src = entry or tt["m_RD"]
    mesh = arrays(mesh_input_from_typetree(tt, VERSION))
    carried = sprite.mesh_from_values(json.loads(json.dumps(sprite.mesh_values(mesh))))
    img = decoded(texture())
    same(sprite.crop(img, src["textureRect"], src["settingsRaw"], carried, 100.0),
         sprite.crop(img, src["textureRect"], src["settingsRaw"], mesh, 100.0))


# ---------------------------------------------------------------- canvas, refusals, registry
def test_canvas_places_the_image_at_its_offset():
    img = texture().image
    r = rect(0, 0, 30, 20)
    tr = rect(10, 10, 12, 8)
    pos, idx = quad(tr)
    mesh = arrays(mesh_input_from_typetree(sprite_tt("s", tr, render_data(pptr(TEX), tr, 2, pos, idx)), VERSION))
    plain = sprite.crop(img, tr, 2, mesh, 100.0)
    out = sprite.crop(img, tr, 2, mesh, 100.0, canvas={"rect": r, "offset": {"x": 5.0, "y": 3.0}})
    assert out.size == (30, 20) and out.mode == "RGBA"
    a = np.asarray(out)
    top = 20 - 3 - 8
    assert np.array_equal(a[top:top + 8, 5:17], np.asarray(plain))
    a = a.copy()
    a[top:top + 8, 5:17] = 0
    assert not a.any()                                                   # transparent elsewhere


def test_canvas_of_an_rgb_sprite_is_rgba_and_overflow_is_refused():
    img = texture(fmt=3).image
    tr = rect(0, 0, 10, 10)
    out = sprite.crop(img, tr, 2, None, 100.0, canvas={"rect": rect(0, 0, 12, 12), "offset": {"x": 1, "y": 1}})
    assert out.mode == "RGBA" and np.asarray(out)[0, 0, 3] == 0 and np.asarray(out)[5, 5, 3] == 255
    with pytest.raises(Unsupported) as e:
        sprite.crop(img, tr, 2, None, 100.0, canvas={"rect": rect(0, 0, 10, 10), "offset": {"x": 1, "y": 0}})
    assert e.value.code == "unsupported.sprite.canvas"


def test_canvas_of_a_mesh_drawn_sprite_uses_its_bounds_and_pivot():
    tt = uv_case()
    mesh = arrays(mesh_input_from_typetree(tt, VERSION))
    img = texture().image
    out = sprite.crop(img, tt["m_RD"]["textureRect"], 0, mesh, 100.0,
                      canvas={"rect": tt["m_Rect"], "offset": {"x": 0, "y": 0}, "pivot": tt["m_Pivot"]})
    plain = sprite.crop(img, tt["m_RD"]["textureRect"], 0, mesh, 100.0)
    assert out.size == (20, 16) and plain.size[0] <= 20


def test_downscaled_atlas_sprites_are_refused():
    with pytest.raises(Unsupported) as e:
        sprite.crop(texture().image, rect(0, 0, 4, 4), 3, None, 100.0, downscale=0.5)
    assert e.value.code == "unsupported.sprite.downscale"


def test_flat_or_triangle_less_uv_meshes_are_refused():
    r = rect(8, 4, 20, 16)
    pos, idx = polygon(r)
    pos = pos.copy()
    pos[:, 2] = np.arange(len(pos))                                     # not flat on any axis
    tt = sprite_tt("s", r, render_data(pptr(TEX), r, 0, pos, idx, uv=np.full((len(pos), 2), 0.25)))
    with pytest.raises(Unsupported) as e:
        sprite.crop(texture().image, r, 0, arrays(mesh_input_from_typetree(tt, VERSION)), 100.0)
    assert e.value.code == "unsupported.sprite.tight_mask"


def test_tight_sprite_needs_its_mesh():
    with pytest.raises(ValueError, match="without its mesh"):
        sprite.crop(texture().image, rect(0, 0, 4, 4), 0, None, 100.0)


def test_mismatched_alpha_texture_is_an_error():
    with pytest.raises(ValueError, match="alpha texture"):
        sprite.crop(texture().image, rect(0, 0, 4, 4), 2, None, 100.0, texture(w=32, h=32).image)


def test_registry_entry():
    impl = ATOMS["sprite.crop"]
    assert impl.fn is sprite.crop and impl.id.startswith("pillow-") and "+numpy-" in impl.id
    assert impl.cost({"width": 10, "height": 10})["peakBytes"] > 0


def test_canvas_takes_the_rounding_pixel_of_a_fractional_rect():
    img = texture().image
    tr = rect(4.4, 2.4, 10.2, 8.2)                                        # cut at rounded edges: 11 x 9
    out = sprite.crop(img, tr, 2, None, 100.0, canvas={"rect": rect(0, 0, 10.2, 8.2), "offset": {"x": 0, "y": 0}})
    assert out.size == (11, 9)
    out = sprite.crop(img, tr, 2, None, 100.0, canvas={"rect": rect(0, 0, 10, 8), "offset": {"x": 0, "y": 0}})
    assert out.size == (11, 9)                                            # a whole rect, a fractional cut
    with pytest.raises(Unsupported, match="outside"):
        sprite.crop(img, tr, 2, None, 100.0, canvas={"rect": rect(0, 0, 9.2, 8.2), "offset": {"x": 0, "y": 0}})
