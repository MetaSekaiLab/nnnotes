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

from nnnotes import advui, cache, storyfonts, tmpfont  # noqa: E402

UPEM = 1000


def make_font(path, family="Test Sans", style="Regular", underscore=False):
    """A TrueType font: .notdef, space, A (square), B (triangle), U+53E3 (square with a square hole), H (square) and
    x (a box 510 units high); with `underscore` also U+005F (a bar below the baseline)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def glyph(contours):
        pen = TTGlyphPen(None)
        for pts in contours:
            pen.moveTo(pts[0])
            for p in pts[1:]:
                pen.lineTo(p)
            pen.closePath()
        return pen.glyph()
    square = [(100, 0), (100, 700), (600, 700), (600, 0)]
    fb = FontBuilder(UPEM, isTTF=True)
    order = [".notdef", "space", "A", "B", "kou", "H", "x"] + (["underscore"] if underscore else [])
    fb.setupGlyphOrder(order)
    cmap = {0x20: "space", 0x41: "A", 0x42: "B", 0x53E3: "kou", 0x48: "H", 0x78: "x"}
    glyphs = {".notdef": glyph([square]), "space": glyph([]), "A": glyph([square]),
              "B": glyph([[(50, 0), (350, 650), (650, 0)]]),
              "kou": glyph([[(80, -50), (80, 750), (920, 750), (920, -50)],
                            [(250, 150), (750, 150), (750, 550), (250, 550)]]),
              "H": glyph([square]), "x": glyph([[(50, 0), (50, 510), (450, 510), (450, 0)]])}
    metrics = {".notdef": (700, 100), "space": (333, 0), "A": (700, 100), "B": (700, 50), "kou": (1000, 80),
               "H": (700, 100), "x": (500, 50)}
    if underscore:
        cmap[0x5F] = "underscore"
        glyphs["underscore"] = glyph([[(0, -150), (0, -100), (500, -100), (500, -150)]])
        metrics["underscore"] = (500, 0)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=880, descent=-120, lineGap=0)
    fb.setupOS2(sTypoAscender=880, sTypoDescender=-120, usWinAscent=880, usWinDescent=120, sCapHeight=700,
                sxHeight=500, version=4)
    fb.setupPost(underlinePosition=-100, underlineThickness=50)
    fb.setupNameTable({"familyName": family, "styleName": style, "version": "Version 1.000",
                       "licenseInfoURL": "https://example.invalid/license"})
    buf = io.BytesIO()
    fb.save(buf)
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def font(tmp_path):
    return storyfonts.FontFile(make_font(tmp_path / "TestSans-Regular.ttf"))


def game(point=40, pad=4, mode=4134, material=None):
    mat = material or {"material": "Game SDF Material", "shader": {"shader": "TextMeshPro/Mobile/Distance Field"},
                       "keywords": [], "textures": {"_MainTex": {"texture": {"name": "atlas", "width": 4096,
                                                                             "height": 4096, "format": 1},
                                                                 "scale": {"x": 1.0, "y": 1.0},
                                                                 "offset": {"x": 0.0, "y": 0.0}}},
                       "ints": {}, "floats": {"_GradientScale": pad + 1.0, "_TextureWidth": 4096.0,
                                              "_TextureHeight": 4096.0}, "colors": {}}
    return {"pointSize": float(point), "padding": pad, "renderMode": mode, "material": mat,
            "style": {"normalStyle": 0.0, "normalSpacingOffset": 0.0, "boldStyle": 0.75, "boldSpacing": 7.0,
                      "italicStyle": 35, "tabSize": 10}}


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


@pytest.mark.parametrize("mode, expect", [(4134, (1, 4134)), (8230, (8, 8230)), (16422, (8, 8230)),
                                          (32806, (8, 8230)), (4165, (1, 4134)), (4169, (1, 4134))])
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
    assert (m1, box1) == (m8, box8)                     # metrics and rects at the point size in both
    assert a1.shape == a8.shape == (box1[3] + 12, box1[2] + 12)
    # the square's solid border is inside (> 0.5), the hole and the far margin outside
    x = 6 + int(round((250 - 80) / UPEM * 40 / 2))
    assert a8[a8.shape[0] // 2, x] > 128 and a8[a8.shape[0] // 2, a8.shape[1] // 2] < 128
    assert int(np.abs(a1.astype(int) - a8.astype(int)).max()) <= 40      # same field, finer edge
    assert np.array_equal(a8, g8.cell(gi, 6)[2])                         # deterministic
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
    assert set(f["characters"]) >= {str(ord(c)) for c in "AB口 \n"} | {str(u) for u in tmpfont.TMP_SYNTHESIZED}
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
        block = px[r["m_Y"] - 9:r["m_Y"] + r["m_Height"] + 9, r["m_X"] - 9:r["m_X"] + r["m_Width"] + 9, 3]
        assert np.array_equal(block, cell)
    space = f["glyphs"][str(gen.glyph_index(0x20))]
    assert "packed" not in space and space["rect"]["m_Width"] == 0
    mats = a["materials"]
    assert set(mats) == {"Test Sans Regular SDF Material", "Test Sans Regular - OutlineX"}
    assert a["renames"] == {"Game - OutlineX": "Test Sans Regular - OutlineX"}
    m = mats["Test Sans Regular - OutlineX"]
    assert m["textures"]["_MainTex"]["texture"] == {"name": page, "width": px.shape[1], "height": px.shape[0],
                                                    "format": 1}
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
    assert [p.tobytes() for _, p in cached["pages"]] == first[1]           # from the glyph cache
    assert json.loads(first[0])[0]["atlasRenderMode"] == 8230


def test_open_asset_checks_the_gradient_scale(font):
    g = game()
    g["material"]["floats"]["_GradientScale"] = 9.0
    with pytest.raises(NotImplementedError, match="_GradientScale"):
        storyfonts.open_asset(font, "X SDF", [0x41], g, {})


def test_language_doc():
    lang = {"languages": {0: {"fontNames": ["GameA", "GameNum"], "additionalFontNames": []}}, "materialTypes": []}
    fonts = {"Open SDF": {"faceInfo": {"m_PointSize": 40.0, "m_Scale": 1.0, "m_LineHeight": 40.0,
                                       "m_AscentLine": 35.2, "m_DescentLine": -4.8},
                          "normalSpacingOffset": 0.0, "boldSpacing": 7.0}}
    texts = {"a/b": {"fontAsset": "GameA SDF", "localized": {"fontAsset": "Open SDF", "material": "m",
                                                             "lineSpacing": -100.0}}}
    doc = storyfonts.language_doc("en", "open", fonts, texts, lang)
    assert doc == {"format": storyfonts.LANGUAGE_FORMAT, "language": "en", "mode": 1, "field": "english",
                   "lineSpacing": -100.0, "fonts": "open",
                   "roles": {"primary": {"fontAsset": "Open SDF", "lineHeightEm": 1.0, "ascentEm": 0.88,
                                         "descentEm": -0.12, "spacingOffset": 0.0, "boldSpacing": 7.0}}}


def test_line_breaking():
    class Obj:
        def __init__(self, text):
            self.text = text

        def read_typetree(self):
            return {"m_Script": self.text}

    class Player:
        def resource(self, path):
            assert path == "TMP Settings"
            return "settings"

        def mono(self, o):
            return {"m_leadingCharacters": {"m_PathID": 1}, "m_followingCharacters": {"m_PathID": 2},
                    "m_UseModernHangulLineBreakingRules": 0}

        def deref(self, owner, pptr):
            return Obj({1: "([", 2: ")]"}[pptr["m_PathID"]])
    assert storyfonts.line_breaking(Player()) == {"leading": "([", "following": ")]",
                                                  "useModernHangulLineBreakingRules": False}


def test_shown_characters():
    episode = {"text": {"a": {"english": "Hi <b>x</b>", "japanese": "ね"}, "b": {"english": None}},
               "title": {"english": "T1"}, "commands": []}
    ui = {"nodes": [{"path": "n", "textStyle": {"textKey": None, "text": "placeholder"}}]}
    got = storyfonts.shown_characters(episode, ui, None, "english")
    assert got == sorted({ord(c) for c in "Hi <b>x</b>T1"})
    # chat windows: their status texts and the texts they format at run time
    chat = {**episode, "commands": [{"cmd": "ChatWindow", "Parameter1": "57", "Parameter4": "4"}]}
    got = storyfonts.shown_characters(chat, {**ui, "chatStatusTexts": {"k": "Call"}}, None, "english")
    assert got == sorted({ord(c) for c in "Hi <b>x</b>T1Call...57%(4)"})
    ui["nodes"][0]["textStyle"]["textKey"] = "k"
    with pytest.raises(RuntimeError, match="master data"):
        storyfonts.shown_characters(episode, ui, None, "english")


def test_game_fonts_moves_the_records(tmp_path, monkeypatch):
    game_ui, ui = tmp_path / "game" / "ui", tmp_path / "open" / "ui"
    (game_ui / "textures").mkdir(parents=True)
    ui.mkdir(parents=True)
    (game_ui / "textures" / "font_G_SDF.png").write_bytes(b"png")
    text = {"m_fontSize": 30, "m_text": "", "class": "TextMeshProUGUI", "enabled": 1, "fontAsset": "G SDF",
            "material": "G - Default", "localized": {"fontAsset": "G SDF", "material": "G - Default",
                                                     "lineSpacing": 0.0}}
    font = {"faceInfo": {"m_PointSize": 40.0, "m_Scale": 1.0, "m_LineHeight": 40.0, "m_AscentLine": 30.0,
                         "m_DescentLine": -10.0},
            "normalSpacingOffset": 0.0, "boldSpacing": 7.0, "material": "G SDF Material", "fallbacks": ["Other SDF"],
            "glyphs": {"5": {"packed": {"texture": "font_G SDF", "dx": 1, "dy": 2}}, "3": {}}}
    doc = {"nodes": [{"path": "t", "text": text}, {"path": "img"}],
           "fonts": {"G SDF": font},
           "textures": {"font_G SDF": {"texture": "textures/font_G_SDF.png", "name": "font_G SDF", "width": 8,
                                       "height": 8, "mipCount": 1, "settings": {}},
                        "sprites": {"texture": "textures/sprites.png"}},
           "materials": {"G - Default": {"material": "G - Default"}, "G SDF Material": {"material": "G SDF Material"},
                         "UI-Transition": {}},
           "glyphCoverage": {"characters": 2, "missing": ["?"]}, "tmpSettings": {"m_warningsDisabled": 0}}
    (game_ui / "ui.json").write_text(json.dumps(doc), encoding="utf-8")
    (ui / "ui.json").write_text(json.dumps({"nodes": [{"path": "t", "textStyle": {}}, {"path": "img"}]}),
                                encoding="utf-8")

    class Ex:
        def key_object(self, key):
            assert key == "EmbFont/G/G - Default"
            return key

        def material(self, o):
            return {}
    ruby = {"class": "RubyTextMeshProUGUI", "_rubyVerticalOffset": "1em", "_rubyScale": 0.5, "_rubyLineHeight": "",
            "_rubyShowType": 0, "_rubyMargin": 10.0}
    monkeypatch.setattr(storyfonts, "_exporter", lambda cat, player, work: Ex())
    monkeypatch.setattr(storyfonts, "text_components",
                        lambda ex, ui_doc: {"t": [ruby, {"class": "UIRubyText", "_rubyMarginTop": -22.0}]})
    monkeypatch.setattr(storyfonts, "text_shaders", lambda ex, ui_dir, mats: {n: [] for n in mats})
    monkeypatch.setattr(storyfonts, "fallback_chains", lambda ex, primaries, exported: (
        {n: [] for n in exported} if primaries == {"G SDF"} else pytest.fail(f"primaries {primaries}")))
    monkeypatch.setattr(storyfonts, "line_breaking", lambda player: {"leading": "", "following": "",
                                                                     "useModernHangulLineBreakingRules": False})
    monkeypatch.setattr(storyfonts.textstyle, "language_fonts",
                        lambda player: {"languages": {0: {"fontNames": ["G"]}}, "materialTypes": ["Default"]})
    r = storyfonts.game_fonts(None, None, game_ui, ui, "ja")
    out = json.loads((ui / "fonts.json").read_text(encoding="utf-8"))
    assert r["pages"] == 1 and (ui / "fonts" / "font_G_SDF.png").read_bytes() == b"png"
    assert out["source"] == "game" and out["fonts"] == {"G SDF": {**font, "fallbacks": []}}
    assert out["texts"] == {"t": {**text, "ruby": {k: v for k, v in ruby.items() if k != "class"},
                                  "uiRubyText": {"_rubyMarginTop": -22.0}}}
    assert out["textures"] == {"font_G SDF": {**doc["textures"]["font_G SDF"], "texture": "fonts/font_G_SDF.png"}}
    assert set(out["materials"]) == {"G - Default", "G SDF Material"} and out["tmpSettings"] == doc["tmpSettings"]
    lang = json.loads((ui / "languages.json").read_text(encoding="utf-8"))
    assert lang["roles"]["primary"]["fontAsset"] == "G SDF" and lang["mode"] == 0


def test_text_extras():
    comps = [{"class": "RubyEmojiTextMeshProUGUI", "_rubyVerticalOffset": "1em", "_rubyScale": 0.5,
              "_rubyLineHeight": "", "_rubyShowType": 0, "_rubyMargin": 10.0, "m_text": ""},
             {"class": "UIRubyText", "_rubyMarginTop": -24.0, "_targetText": {}}, {"class": "LocalizeText"}]
    assert storyfonts.text_extras("p", comps) == {
        "ruby": {"_rubyVerticalOffset": "1em", "_rubyScale": 0.5, "_rubyLineHeight": "", "_rubyShowType": 0,
                 "_rubyMargin": 10.0},
        "uiRubyText": {"_rubyMarginTop": -24.0}}
    assert storyfonts.text_extras("p", [{"class": "TextMeshProUGUI"}]) == {}
    assert storyfonts.text_extras("p", [{"class": "LocalizeKoreanAdjust", "m_Enabled": 1, "_koreanFontStyle": 1,
                                         "_localizeText": {}}]) == {
        "localizeKoreanAdjust": {"_koreanFontStyle": 1, "m_Enabled": True}}
    with pytest.raises(RuntimeError, match="ruby text without"):
        storyfonts.text_extras("p", [{"class": "RubyTextMeshProUGUI", "_rubyScale": 0.5}])


def test_search_order():
    """TMP's depth-first fallback search, every asset once, reduced to the exported assets."""
    class F:
        def __init__(self, name):
            self.name = name
    p, a, b, c, d = F("P"), F("A"), F("B"), F("C"), F("D")
    chain = {"P": [a, b], "A": [p, c], "B": [c, d], "C": [], "D": [a]}
    fallbacks = lambda f: chain[f.name]  # noqa: E731
    # from P: A (not exported), A's fallbacks P (itself: searched, not listed) with P's B and B's C and D
    assert storyfonts.search_order(fallbacks, p, {"P", "B", "C", "D"}) == ["B", "C", "D"]
    assert storyfonts.search_order(fallbacks, p, {"C", "D"}) == ["C", "D"]
    # from D: A, P (and through P: B, C), then A's C already searched
    assert storyfonts.search_order(fallbacks, d, {"P", "B", "C", "D"}) == ["P", "B", "C"]
    assert storyfonts.search_order(fallbacks, c, {"P"}) == []


def test_chat_bindings(monkeypatch):
    """A binding per chat window text: the serialized fields, the extras, `localized` from advui.chat_localize for
    a localized text and the serialized font, material and line spacing for the others."""
    name = {"class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"name": "CHAT SDF"},
            "m_sharedMaterial": {"material": "CHAT - Default"}, "m_fontSize": 30.0, "m_text": "", "m_lineSpacing": 2.0,
            "m_monospaceDistEm": 0.0}
    pct = {**name, "class": "RubyEmojiTextMeshProUGUI", "m_text": "100%", "_rubyVerticalOffset": "1em",
           "_rubyScale": 0.5, "_rubyLineHeight": "", "_rubyShowType": 0, "_rubyMargin": 0.0}
    loc = {"class": "LocalizeText", "m_Enabled": 1, "_localizeEnabled": 1}
    nodes = [("Line", {"path": "Line/Name", "components": [name, loc]}, name, loc),
             ("Line", {"path": "Line/Pct", "components": [pct]}, pct, None)]
    monkeypatch.setattr(storyfonts.advui, "chat_text_nodes", lambda ex, episode: (nodes, []))
    lang = {"languages": {0: {"fontNames": ["J"], "additionalFontNames": ["CHAT"]},
                          1: {"fontNames": ["E"], "additionalFontNames": ["EC"]}}, "materialTypes": ["Default"]}
    got = storyfonts.chat_bindings(None, {}, lang, 1)
    assert set(got) == {"Line"} and set(got["Line"]) == {"Line/Name", "Line/Pct"}
    n, p = got["Line"]["Line/Name"], got["Line"]["Line/Pct"]
    assert n["localized"] == {"fontAsset": "EC SDF", "material": "EC - Default", "lineSpacing": -100.0}
    assert (n["class"], n["fontAsset"], n["m_monospaceDistEm"]) == ("TextMeshProUGUI", "CHAT SDF", 0.0)
    assert p["localized"] == {"fontAsset": "CHAT SDF", "material": "CHAT - Default", "lineSpacing": 2.0}
    assert p["ruby"]["_rubyScale"] == 0.5 and "m_lineSpacing" in p
    assert "m_monospaceDistEm" in advui.TMP_FIELDS


def test_dialog_bindings():
    """A binding per text node of each dialog of ui.json, from the components of the dialog's prefab; `localized` by
    LocalizeText's font swap in the language."""
    text = {"class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"name": "J SDF"},
            "m_sharedMaterial": {"material": "J - Default"}, "m_fontSize": 30.0, "m_text": "", "m_lineSpacing": 0.0}

    class Ex:
        def prefab(self, key):
            assert key == "EmbUI/Prefab/D"
            return {"nodes": [{"path": "D/Title", "components": [text, {"class": "LocalizeText", "m_Enabled": 1}]},
                              {"path": "D/Image", "components": [{"class": "Image"}]}]}
    ui = {"dialogs": {"D": {"key": "EmbUI/Prefab/D",
                            "nodes": [{"path": "D/Title", "textStyle": {"textKey": "ui_ok"}}, {"path": "D/Image"}]}}}
    lang = {"languages": {0: {"fontNames": ["J"]}, 1: {"fontNames": ["E"]}}, "materialTypes": ["Default"]}
    got = storyfonts.dialog_bindings(Ex(), ui, lang, 1)
    assert set(got) == {"D"} and set(got["D"]) == {"D/Title"}
    t = got["D"]["D/Title"]
    assert (t["class"], t["fontAsset"], t["material"]) == ("TextMeshProUGUI", "J SDF", "J - Default")
    assert t["m_fontSize"] == 30.0
    assert t["localized"]["fontAsset"] == "E SDF" and t["localized"]["material"] == "E - Default"
    assert storyfonts.dialog_bindings(Ex(), {"nodes": []}, lang, 1) == {}
    # the dialogs' text keys are shown characters
    episode = {"text": {}, "title": {"english": ""}, "commands": []}
    with pytest.raises(RuntimeError, match="master data"):
        storyfonts.shown_characters(episode, {"nodes": [], **ui}, None, "english")
