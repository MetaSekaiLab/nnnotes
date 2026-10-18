"""Story fonts (storyfonts.py) and the open-font entry of tmpfont.RuntimeGlyphs, on a font generated here (a few
square and triangle glyphs); nothing here comes from game data."""

import hashlib
import io
import json

import numpy as np
import pytest

pytest.importorskip("freetype")
pytest.importorskip("scipy")
pytest.importorskip("fontTools")

from nnnotes.content import storyfonts
from nnnotes.core import cache
from nnnotes.unity import fontsettings, tmpfont  # noqa: E402
from tests.helpers.fonts import UPEM, make_font


@pytest.fixture
def font(tmp_path):
    return storyfonts.FontFile(make_font(tmp_path / "TestSans-Regular.ttf"))


def game(point=40, pad=4, mode=4134, material=None):
    mat = material or {
        "material": "Game SDF Material",
        "shader": {"shader": "TextMeshPro/Mobile/Distance Field"},
        "keywords": [],
        "textures": {
            "_MainTex": {
                "texture": {"name": "atlas", "width": 4096, "height": 4096, "format": 1},
                "scale": {"x": 1.0, "y": 1.0},
                "offset": {"x": 0.0, "y": 0.0},
            }
        },
        "ints": {},
        "floats": {"_GradientScale": pad + 1.0, "_TextureWidth": 4096.0, "_TextureHeight": 4096.0},
        "colors": {},
    }
    return {
        "pointSize": float(point),
        "padding": pad,
        "renderMode": mode,
        "material": mat,
        "style": {
            "normalStyle": 0.0,
            "normalSpacingOffset": 0.0,
            "boldStyle": 0.75,
            "boldSpacing": 7.0,
            "italicStyle": 35,
            "tabSize": 10,
        },
    }


def outline_material(pad=4, underlay=0.5):
    m = game(pad=pad)["material"]
    m = json.loads(json.dumps(m))
    m["material"] = "Game - OutlineX"
    m["keywords"] = ["OUTLINE_ON", "UNDERLAY_ON"]
    m["floats"].update({"_OutlineWidth": 0.2, "_UnderlayOffsetX": underlay, "_UnderlayOffsetY": -underlay})
    return m


def test_font_file_names(font, tmp_path):
    assert (font.family, font.style, font.version) == ("Test Sans", "Regular", "Version 1.000")
    assert font.license == "https://example.invalid/license"
    assert font.asset_name == "Test Sans Regular SDF"
    assert font.sha256 == hashlib.sha256((tmp_path / "TestSans-Regular.ttf").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "mode, expect",
    [
        (4134, (1, 4134)),
        (8230, (8, 8230)),
        (16422, (8, 8230)),
        (32806, (8, 8230)),
        (4165, (1, 4134)),
        (4169, (1, 4134)),
    ],
)
def test_oversample_of(mode, expect):
    assert storyfonts.oversample_of(mode) == expect


def test_oversample_of_needs_a_distance_field_mode():
    with pytest.raises(NotImplementedError):
        storyfonts.oversample_of(tmpfont.RASTER_MODE["8BIT"] | tmpfont.RASTER_MODE["1X"])


def test_cell_is_the_runtime_distance_field(font):
    """At oversample 1 the cell is RuntimeGlyphs.sdf over the padded rect, continued outwards over the margin."""
    gen = tmpfont.RuntimeGlyphs.from_font_file(font.data, 0, 40, 4)
    gi = gen.glyph_index(ord("B"))
    m, box, a = gen.sdf(gi)
    m2, box2, cell = gen.cell(gi, 4)
    assert (m, box) == (m2, box2) and np.array_equal(a, cell)
    _, _, wide = gen.cell(gi, 9)
    assert wide.shape == (box[3] + 18, box[2] + 18)
    assert np.array_equal(wide[5:-5, 5:-5], a)
    assert wide[0, 0] == 0 and wide.max() == 255


def test_cell_values_and_oversampling(font):
    g1 = tmpfont.RuntimeGlyphs.from_font_file(font.data, 0, 40, 4)
    g8 = tmpfont.RuntimeGlyphs.from_font_file(font.data, 0, 40, 4, oversample=8)
    gi = g1.glyph_index(0x53E3)
    m1, box1, a1 = g1.cell(gi, 6)
    m8, box8, a8 = g8.cell(gi, 6)
    assert (m1, box1) == (m8, box8)  # metrics and rects at the point size in both
    assert a1.shape == a8.shape == (box1[3] + 12, box1[2] + 12)
    # the square's solid border is inside (> 0.5), the hole and the far margin outside
    x = 6 + int(round((250 - 80) / UPEM * 40 / 2))
    assert a8[a8.shape[0] // 2, x] > 128 and a8[a8.shape[0] // 2, a8.shape[1] // 2] < 128
    assert int(np.abs(a1.astype(int) - a8.astype(int)).max()) <= 40  # same field, finer edge
    assert np.array_equal(a8, g8.cell(gi, 6)[2])  # deterministic
    _, box, empty = g1.cell(g1.glyph_index(0x20), 6)
    assert box[2:] == (0, 0) and empty.shape == (12, 12) and not empty.any()


def test_face_info(font):
    gen = tmpfont.RuntimeGlyphs.from_font_file(font.data, 0, 40, 4)
    fi = storyfonts.face_info(gen, font, 40)
    s = 40 / UPEM
    assert fi["m_PointSize"] == 40.0 and fi["m_UnitsPerEM"] == UPEM and fi["m_Scale"] == 1.0
    assert fi["m_AscentLine"] == float(np.float32(880 * s)) and fi["m_DescentLine"] == float(np.float32(-120 * s))
    assert fi["m_LineHeight"] == pytest.approx(1000 * s)
    # the H and x glyph tops (700 and 510 units: 28 and 20.4 px) rounded up, not the OS/2 heights (700, 500)
    assert (fi["m_CapLine"], fi["m_MeanLine"]) == (28.0, 21.0)
    assert fi["m_StrikethroughOffset"] == float(np.float32(8.4))
    # FreeType's underline position: the post table's minus half the thickness
    assert fi["m_UnderlineOffset"] == pytest.approx(-125 * s) and fi["m_UnderlineThickness"] == pytest.approx(2.0)
    assert fi["m_TabWidth"] == 13.0 and fi["m_FamilyName"] == "Test Sans"


def test_pack_cells(monkeypatch):
    monkeypatch.setattr(storyfonts, "PAGE_WIDTH", 10)
    monkeypatch.setattr(storyfonts, "PAGE_MAX_HEIGHT", 8)
    where, w, h = storyfonts.pack_cells([(1, 4, 4), (2, 4, 3), (3, 4, 4), (4, 12, 2), (5, 3, 3)])
    assert w == 12
    assert where == {1: (0, 0, 0), 2: (0, 4, 0), 3: (0, 8, 0), 4: (0, 0, 4), 5: (1, 0, 0)}
    assert h == 6


def test_open_asset(font):
    chars = [ord(c) for c in "AB口A Z\n"]
    a = storyfonts.open_asset(font, font.asset_name, chars, game(), {"Game - OutlineX": outline_material()})
    f = a["font"]
    assert a["missing"] == {ord("Z")}
    assert set(f["characters"]) >= {str(ord(c)) for c in "AB口 \n"} | {str(u) for u in fontsettings.TMP_SYNTHESIZED}
    assert str(ord("Z")) not in f["characters"]
    assert f["characters"]["10"]["glyph"] == 0 and f["glyphs"]["0"]["metrics"]["m_HorizontalAdvance"] == 0.0
    assert f["atlasRenderMode"] == 4134 and f["atlasPopulationMode"] == 0 and f["atlasPadding"] == 4
    assert f["source"]["generator"]["oversample"] == 1 and f["source"]["license"] == font.license
    # margin: ceil(5) + ceil(0.5 * 5) + 1
    assert f["source"]["generator"]["margin"] == 9
    ((page, px),) = a["pages"]
    assert f["atlases"] == [page] and (px.shape[1], px.shape[0]) == (f["atlasWidth"], f["atlasHeight"])
    gen = tmpfont.RuntimeGlyphs.from_font_file(font.data, 0, 40, 4)
    for u in "AB口":
        gi = str(gen.glyph_index(ord(u)))
        g = f["glyphs"][gi]
        assert g["packed"] == {"texture": page, "dx": 0, "dy": 0} and g["atlasIndex"] == 0
        r = g["rect"]
        _, box, cell = gen.cell(int(gi), 9)
        assert (r["m_Width"], r["m_Height"]) == box[2:]
        block = px[r["m_Y"] - 9 : r["m_Y"] + r["m_Height"] + 9, r["m_X"] - 9 : r["m_X"] + r["m_Width"] + 9, 3]
        assert np.array_equal(block, cell)
    space = f["glyphs"][str(gen.glyph_index(0x20))]
    assert "packed" not in space and space["rect"]["m_Width"] == 0
    mats = a["materials"]
    assert set(mats) == {"Test Sans Regular SDF Material", "Test Sans Regular - OutlineX"}
    assert a["renames"] == {"Game - OutlineX": "Test Sans Regular - OutlineX"}
    m = mats["Test Sans Regular - OutlineX"]
    assert m["textures"]["_MainTex"]["texture"] == {
        "name": page,
        "width": px.shape[1],
        "height": px.shape[0],
        "format": 1,
    }
    assert (m["floats"]["_TextureWidth"], m["floats"]["_TextureHeight"]) == (float(px.shape[1]), float(px.shape[0]))
    assert m["keywords"] == ["OUTLINE_ON", "UNDERLAY_ON"] and m["floats"]["_OutlineWidth"] == 0.2
    assert a["textures"][page]["texture"].startswith("fonts/") and a["textures"][page]["mipCount"] == 1


def test_open_asset_keeps_the_underline_character_on_the_first_page(tmp_path, monkeypatch):
    """U+005F is in every open asset (the underline and highlight glyph), on page 0, not reported missing when the
    font lacks it; with small pages the later characters go to later pages."""
    under = storyfonts.FontFile(make_font(tmp_path / "U.ttf", underscore=True))
    monkeypatch.setattr(storyfonts, "PAGE_WIDTH", 64)
    monkeypatch.setattr(storyfonts, "PAGE_MAX_HEIGHT", 160)
    a = storyfonts.open_asset(under, "U SDF", [0x41, 0x42, 0x53E3], game(point=100, pad=15, mode=4134), {})
    f = a["font"]
    gi = str(f["characters"]["95"]["glyph"])
    assert f["glyphs"][gi]["atlasIndex"] == 0 and len(a["pages"]) > 1
    assert a["missing"] == set() and "U+005F" in storyfonts.SUBSET
    plain = storyfonts.FontFile(make_font(tmp_path / "P.ttf"))
    b = storyfonts.open_asset(plain, "P SDF", [0x41], game(), {})
    assert "95" not in b["font"]["characters"] and b["missing"] == set()


def test_open_asset_is_deterministic(font):
    def run():
        cache.clear()
        a = storyfonts.open_asset(font, "X SDF", [0x41, 0x53E3], game(point=100, pad=15, mode=32806), {})
        pages = [p.tobytes() for _, p in a["pages"]]
        return json.dumps([a["font"], a["textures"], a["materials"]], sort_keys=True), pages

    first = run()
    assert run() == first
    cached = storyfonts.open_asset(font, "X SDF", [0x41, 0x53E3], game(point=100, pad=15, mode=32806), {})
    assert [p.tobytes() for _, p in cached["pages"]] == first[1]  # from the glyph cache
    assert json.loads(first[0])[0]["atlasRenderMode"] == 8230


def test_open_asset_checks_the_gradient_scale(font):
    g = game()
    g["material"]["floats"]["_GradientScale"] = 9.0
    with pytest.raises(NotImplementedError, match="_GradientScale"):
        storyfonts.open_asset(font, "X SDF", [0x41], g, {})


def test_text_extras():
    comps = [
        {
            "class": "RubyEmojiTextMeshProUGUI",
            "_rubyVerticalOffset": "1em",
            "_rubyScale": 0.5,
            "_rubyLineHeight": "",
            "_rubyShowType": 0,
            "_rubyMargin": 10.0,
            "m_text": "",
        },
        {"class": "UIRubyText", "_rubyMarginTop": -24.0, "_targetText": {}},
        {"class": "LocalizeText"},
    ]
    assert storyfonts.text_extras("p", comps) == {
        "ruby": {
            "_rubyVerticalOffset": "1em",
            "_rubyScale": 0.5,
            "_rubyLineHeight": "",
            "_rubyShowType": 0,
            "_rubyMargin": 10.0,
        },
        "uiRubyText": {"_rubyMarginTop": -24.0},
    }
    assert storyfonts.text_extras("p", [{"class": "TextMeshProUGUI"}]) == {}
    assert storyfonts.text_extras(
        "p", [{"class": "LocalizeKoreanAdjust", "m_Enabled": 1, "_koreanFontStyle": 1, "_localizeText": {}}]
    ) == {"localizeKoreanAdjust": {"_koreanFontStyle": 1, "m_Enabled": True}}
    with pytest.raises(RuntimeError, match="ruby text without"):
        storyfonts.text_extras("p", [{"class": "RubyTextMeshProUGUI", "_rubyScale": 0.5}])


def test_search_order():
    """TMP's depth-first fallback search, every asset once, reduced to the exported assets."""
    chain = {"P": ["A", "B"], "A": ["P", "C"], "B": ["C", "D"], "C": [], "D": ["A"]}
    fallbacks = chain.__getitem__
    # from P: A (not exported), A's fallbacks P (itself: searched, not listed) with P's B and B's C and D
    assert storyfonts.search_order(fallbacks, "P", {"P", "B", "C", "D"}) == ["B", "C", "D"]
    assert storyfonts.search_order(fallbacks, "P", {"C", "D"}) == ["C", "D"]
    # from D: A, P (and through P: B, C), then A's C already searched
    assert storyfonts.search_order(fallbacks, "D", {"P", "B", "C", "D"}) == ["P", "B", "C"]
    assert storyfonts.search_order(fallbacks, "C", {"P"}) == []


def sprite_record():
    """A sprite asset record as advui.sprite_asset_record gives it: 32 x 32 glyphs in a 128 x 128 sheet."""
    names = [
        ("1f60a", 0x1F60A),
        ("1f60a", 0x1F60A),
        ("2764-fe0f", 0x2764),
        ("1f647-200d-2640-fe0f", 0x1F647),
        ("1f647", 0x1F647),
        ("0", 0),
        ("1f600", 0x1F600),
    ]
    chars = [{"index": i, "unicode": u, "name": n, "glyph": i, "scale": 1.0} for i, (n, u) in enumerate(names)]
    metrics = {
        "m_Width": 32.0,
        "m_Height": 32.0,
        "m_HorizontalBearingX": 0.0,
        "m_HorizontalBearingY": 28.8,
        "m_HorizontalAdvance": 32.0,
    }
    glyphs = {
        str(i): {
            "metrics": metrics,
            "scale": 1.0,
            "atlasIndex": 0,
            "rect": {"m_X": 32 * (i % 4), "m_Y": 32 * (i // 4), "m_Width": 32, "m_Height": 32},
        }
        for i in range(len(names))
        if i != 6
    }
    mat = {
        "material": "Emoji Material",
        "shader": {"shader": "TextMeshPro/Sprite"},
        "keywords": [],
        "textures": {
            "_MainTex": {
                "texture": {"name": "Sheet", "width": 128, "height": 128, "format": 48},
                "scale": {"x": 1.0, "y": 1.0},
                "offset": {"x": 0.0, "y": 0.0},
            }
        },
        "ints": {},
        "floats": {},
        "colors": {"_Color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}},
    }
    return {
        "name": "Emoji",
        "faceInfo": {"m_PointSize": 0.0},
        "characters": chars,
        "glyphs": glyphs,
        "sequences": [{"name": "2764-fe0f", "unicode": 0x2764}, {"name": "1f647-200d-2640-fe0f", "unicode": 0x1F647}],
        "sheet": {"name": "Sheet", "width": 128, "height": 128, "mipCount": 1, "settings": {}},
        "material": mat,
    }


def test_sprite_needs():
    """The sprites of the sequences the texts hold (by name hash, the first character of a name) and of the code
    points drawn by the sprite asset (the first character of a code point); a character without its glyph is left
    out; a sequence that does not match stays text."""
    rec = sprite_record()
    got = storyfonts.sprite_needs(
        rec, ["a\u2764\ufe0f", "\U0001f647\u200d\u2640\ufe0f", "\u2764x"], {0x1F60A, 0x1F600, 0x41}
    )
    assert got == [0, 2, 3]
    assert storyfonts.sprite_needs(rec, ["\u2764\u200d"], set()) == []


def make_emoji_font(path):
    """A colour bitmap font (sbix, PNG glyphs of 40 x 36 pixels with a 4-pixel transparent border): U+1F60A, U+1F647,
    U+2764 and the ligature U+1F647 U+200D U+2640; U+200D, U+2640, U+FE0F without images."""
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.tables._s_b_i_x import table__s_b_i_x
    from fontTools.ttLib.tables.sbixGlyph import Glyph
    from fontTools.ttLib.tables.sbixStrike import Strike
    from PIL import Image

    def png(color, w=40, h=36):
        a = np.zeros((h, w, 4), np.uint8)
        a[4 : h - 4, 4 : w - 4] = color
        b = io.BytesIO()
        Image.fromarray(a, "RGBA").save(b, format="PNG")
        return b.getvalue()

    order = [".notdef", "space", "u1F60A", "u1F647", "u2764", "u200D", "u2640", "uFE0F", "u1F647_u200D_u2640"]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(
        {
            0x20: "space",
            0x1F60A: "u1F60A",
            0x1F647: "u1F647",
            0x2764: "u2764",
            0x200D: "u200D",
            0x2640: "u2640",
            0xFE0F: "uFE0F",
        }
    )
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf({n: empty for n in order})
    fb.setupHorizontalMetrics({n: (1000, 0) for n in order})
    fb.setupHorizontalHeader(ascent=900, descent=-100)
    fb.setupNameTable({"familyName": "Test Emoji", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    addOpenTypeFeaturesFromString(fb.font, "feature ccmp { sub u1F647 u200D u2640 by u1F647_u200D_u2640; } ccmp;")
    sbix = table__s_b_i_x()
    st = Strike(ppem=36, resolution=72)
    for n, c in (
        ("u1F60A", (255, 200, 0, 255)),
        ("u1F647", (0, 0, 255, 255)),
        ("u2764", (255, 0, 0, 255)),
        ("u1F647_u200D_u2640", (255, 0, 255, 255)),
    ):
        st.glyphs[n] = Glyph(glyphName=n, graphicType="png ", imageData=png(c), originOffsetX=0, originOffsetY=0)
    sbix.strikes[36] = st
    fb.font["sbix"] = sbix
    fb.font.save(str(path))
    return path


def test_emoji_font_and_open_sprite_asset(tmp_path):
    """Images of an emoji font of one's own at the game glyph size: a sequence through the font's ligature, U+FE0F
    dropped when the full sequence has none; one page with transparent margins; glyphs without an image keep their
    metrics; the game's face info, characters, sequence list and material settings."""
    emoji = storyfonts.EmojiFont(make_emoji_font(tmp_path / "TestEmoji.ttf"))
    assert (emoji.ppem, emoji.asset_name) == (36, "Test Emoji Regular Sprites")
    assert emoji.glyph("\U0001f647\u200d\u2640\ufe0f") == "u1F647_u200D_u2640"
    assert emoji.glyph("\u2764\ufe0f") == "u2764" and emoji.glyph("\U0001f600") is None
    a = emoji.image("\U0001f60a", 32, 32)
    assert a.shape == (32, 32, 4) and a[16, 16].tolist() == [255, 200, 0, 255] and a[0, 0, 3] == 0
    rec = sprite_record()
    got = storyfonts.open_sprite_asset(emoji, rec, [3, 2, 0, 6], "Test Emoji Regular Sprites")
    asset = got["asset"]
    assert [c["index"] for c in asset["characters"]] == [0, 2, 3], "character 6 has no glyph in the record"
    assert asset["faceInfo"] == rec["faceInfo"] and asset["sequences"] == rec["sequences"]
    assert got["missing"] == [] and set(asset["glyphs"]) == {"0", "2", "3"}
    ((page, px),) = got["pages"]
    g = asset["glyphs"]["3"]
    r = g["rect"]
    assert g["packed"] == {"texture": page, "dx": 0, "dy": 0} and (r["m_Width"], r["m_Height"]) == (32, 32)
    assert px[r["m_Y"] + 16, r["m_X"] + 16].tolist() == [255, 0, 255, 255], "the ligature's image"
    assert px[r["m_Y"] - 1, r["m_X"] + 16, 3] == 0, "a transparent margin"
    assert got["textures"][page]["width"] == px.shape[1]
    assert got["material"]["textures"]["_MainTex"]["texture"]["name"] == page
    assert got["material"]["material"] == "Test Emoji Regular Sprites Material"
    assert asset["source"]["family"] == "Test Emoji"
    bare = storyfonts.open_sprite_asset(None, rec, [0, 2], storyfonts.NO_EMOJI_FONT)
    assert bare["pages"] == [] and bare["textures"] == {} and bare["missing"] == ["1f60a", "2764-fe0f"]
    g0 = bare["asset"]["glyphs"]["0"]
    assert "packed" not in g0 and g0["metrics"] == rec["glyphs"]["0"]["metrics"]
    assert bare["material"]["textures"]["_MainTex"]["texture"] is None


class ChainFont:
    """A game font asset for open_font_set: its characters, fallback names and settings."""

    def __init__(self, name, have, fallbacks=(), pad=4, point=40):
        self.name, self.chars, self.fb, self.pad, self.point = name, set(have), list(fallbacks), pad, point


# LocalizeManager fonts: ja and en J, zh Z, ko K
CHAIN_LANG = {
    "languages": {
        0: {"fontNames": ["J"], "additionalFontNames": []},
        1: {"fontNames": ["J"], "additionalFontNames": []},
        2: {"fontNames": ["Z"], "additionalFontNames": []},
        3: {"fontNames": ["Z"], "additionalFontNames": []},
        4: {"fontNames": ["K"], "additionalFontNames": ["K"]},
    },
    "materialTypes": ["OutlineX"],
}


def test_counterpart_language():
    assert storyfonts.counterpart_language(CHAIN_LANG, "ko", "K SDF") == "ko"
    assert storyfonts.counterpart_language(CHAIN_LANG, "ko", "J SDF") == "ja"  # the first that lists it
    assert storyfonts.counterpart_language(CHAIN_LANG, "en", "J SDF") == "en"  # its own language first
    assert storyfonts.counterpart_language(CHAIN_LANG, "ja", "Z SDF") == "zh-Hant"
    assert storyfonts.counterpart_language(CHAIN_LANG, "ja", "Fallback SDF") is None


def test_fallback_material():
    """GetFallbackMaterial: the text material with the target's texture, gradient scale, texture size and weights;
    UpdateShaderRatios in float32 for those."""
    m = outline_material(pad=15)
    m["floats"].update(
        {
            "_WeightNormal": 0.0,
            "_WeightBold": 0.75,
            "_FaceDilate": 0.0,
            "_ScaleRatioA": 0.9,
            "_ScaleRatioC": 0.7,
            "_UnderlayDilate": 0.0,
            "_UnderlaySoftness": 0.0,
        }
    )
    t = game(pad=4)["material"]
    t = json.loads(json.dumps(t))
    t["textures"]["_MainTex"]["texture"]["name"] = "page J"
    t["floats"].update({"_WeightNormal": 0.0, "_WeightBold": 0.9, "_TextureWidth": 64.0, "_TextureHeight": 32.0})
    out = storyfonts.fallback_material(m, t, "M + J")
    f = out["floats"]
    assert out["material"] == "M + J" and out["keywords"] == m["keywords"] and m["material"] == "Game - OutlineX"
    assert out["textures"]["_MainTex"]["texture"]["name"] == "page J"
    assert (f["_GradientScale"], f["_TextureWidth"], f["_TextureHeight"], f["_WeightBold"]) == (5.0, 64.0, 32.0, 0.9)
    assert f["_OutlineWidth"] == 0.2 and f["_UnderlayOffsetX"] == 0.5
    f32 = np.float32
    t_a = max(f32(1), f32(f32(f32(0.9) / f32(4)) + f32(0.2)))
    assert f["_ScaleRatioA"] == float(f32(f32(4) / f32(f32(5) * t_a)))
    rng = f32(f32(f32(0.9) / f32(4)) * f32(4))
    assert f["_ScaleRatioC"] == float(f32(f32(f32(4) - rng) / f32(5)))
    assert storyfonts.fallback_material({**m, "keywords": ["RATIOS_OFF"]}, t, "x")["floats"]["_ScaleRatioA"] == 1.0
