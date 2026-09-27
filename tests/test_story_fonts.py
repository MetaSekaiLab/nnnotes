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


def make_font(path, family="Test Sans", style="Regular", underscore=False, drop=(), extra=None):
    """A TrueType font: .notdef, space, A (square), B (triangle), U+53E3 (square with a square hole), H (square) and
    x (a box 510 units high); with `underscore` also U+005F (a bar below the baseline); the code points `drop` left
    out of the character map, `extra` {code point: glyph name} added to it."""
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
    cmap = {u: g for u, g in {**cmap, **(extra or {})}.items() if u not in drop}
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
            "_rubyShowType": 0, "_rubyMargin": 10.0, "m_tintAllSprites": 0}
    sprite = {"asset_name": "Emoji", "asset": {"characters": [], "glyphs": {}, "material": "Emoji Material"},
              "textures": {"sprite_Emoji": {"texture": "fonts/sprite_Emoji.png"}},
              "material": {"material": "Emoji Material"}}
    monkeypatch.setattr(storyfonts, "_exporter", lambda cat, player, work: Ex())
    monkeypatch.setattr(storyfonts, "text_components",
                        lambda ex, ui_doc: {"t": [ruby, {"class": "UIRubyText", "_rubyMarginTop": -22.0}]})
    monkeypatch.setattr(storyfonts, "story_ui_text_targets", lambda ex, ui_doc: {"t"})
    monkeypatch.setattr(storyfonts.advui, "sprite_asset_record", lambda ex: "rec")
    monkeypatch.setattr(storyfonts, "shown_texts", lambda episode, ui_doc, master_dir, field: ["?\U0001F600"])
    monkeypatch.setattr(storyfonts, "drawn_by_sprites", lambda ex, rec, bindings, chars: {"G SDF": {ord("?")}})
    monkeypatch.setattr(storyfonts, "sprite_needs", lambda rec, texts, drawn: [0] if drawn == {ord("?")} else [])
    monkeypatch.setattr(storyfonts, "game_sprite_asset", lambda ex, ui_dir, rec: sprite)
    monkeypatch.setattr(storyfonts, "text_shaders", lambda ex, ui_dir, mats: {n: [] for n in mats})
    monkeypatch.setattr(storyfonts, "fallback_chains", lambda ex, primaries, exported: (
        {n: [] for n in exported} if primaries == {"G SDF"} else pytest.fail(f"primaries {primaries}")))
    monkeypatch.setattr(storyfonts, "line_breaking", lambda player: {"leading": "", "following": "",
                                                                     "useModernHangulLineBreakingRules": False})
    monkeypatch.setattr(storyfonts.textstyle, "language_fonts",
                        lambda player: {"languages": {0: {"fontNames": ["G"]}}, "materialTypes": ["Default"]})
    r = storyfonts.game_fonts(None, None, game_ui, ui, "ja", {"text": {}})
    out = json.loads((ui / "fonts.json").read_text(encoding="utf-8"))
    assert r["pages"] == 2 and (ui / "fonts" / "font_G_SDF.png").read_bytes() == b"png"
    assert out["source"] == "game" and out["fonts"] == {"G SDF": {**font, "fallbacks": []}}
    assert out["texts"] == {"t": {**text, "ruby": {k: ruby[k] for k in storyfonts.RUBY_FIELDS},
                                  "uiRubyText": {"_rubyMarginTop": -22.0}, "spriteAsset": "Emoji",
                                  "m_tintAllSprites": 0}}
    assert out["textures"] == {"font_G SDF": {**doc["textures"]["font_G SDF"], "texture": "fonts/font_G_SDF.png"},
                               **sprite["textures"]}
    assert set(out["materials"]) == {"G - Default", "G SDF Material", "Emoji Material"}
    assert out["tmpSettings"] == doc["tmpSettings"]
    assert out["spriteAssets"] == {"Emoji": sprite["asset"]} and out["emojiSpriteAsset"] == "Emoji"
    assert out["coverage"] == {"characters": 2, "missing": [], "sprites": {"characters": 0, "missing": []}}
    storyfonts.game_fonts(None, None, game_ui, ui, "ja")          # without the episode: no sprite asset
    out = json.loads((ui / "fonts.json").read_text(encoding="utf-8"))
    assert "spriteAssets" not in out and "spriteAsset" not in out["texts"]["t"] and out["coverage"]["missing"] == ["?"]
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


def test_missing_glyph():
    """The game's missing glyph: the code points no font of the chain has, the substitute U+25A1, else space, else
    U+0003; the TMP settings it needs; glyph variants of a variation selector raise."""
    class F:
        def __init__(self, name, variants=()):
            self.name, self.variants = name, dict.fromkeys(variants, "v")

    class Fonts:
        def __init__(self, have):
            self.have = have

        def lookup(self, font, u):
            return ("baked", self.have[u]) if u in self.have else None
    p, fb = F("P"), F("FB", [(0x2661, 0xFE0F)])
    fonts = Fonts({0x41: p, 0x25A1: fb, 0x20: p, 0x2661: fb, 0x03: p})
    assert tmpfont.missing_glyph(fonts, p, [0x41, 0x20]) is None
    assert tmpfont.missing_glyph(fonts, p, [0x41, 0xFE0F, 0x26A1]) == {"unicode": 0x25A1,
                                                                       "characters": [0x26A1, 0xFE0F]}
    no_box = Fonts({0x41: p, 0x20: p, 0x03: p})
    assert tmpfont.missing_glyph(no_box, p, [0x42])["unicode"] == 0x20
    assert tmpfont.missing_glyph(Fonts({0x03: p}), p, [0x42])["unicode"] == 0x03
    assert tmpfont.missing_glyph(fonts, p, [0x42], 0x3F)["unicode"] == 0x20      # a set missingGlyphCharacter
    assert [u for u in (0xFDFF, 0xFE00, 0xFE0F, 0xFE10, 0xE0100, 0xE01EF, 0xE01F0)
            if tmpfont.is_variation_selector(u)] \
        == [0xFE00, 0xFE0F, 0xE0100, 0xE01EF]
    tmpfont.check_glyph_variants(fonts, p, ["A\ufe0f", "\u2661", "\u26a1\ufe0f"])
    with pytest.raises(NotImplementedError, match="glyph variant of U\\+2661 U\\+FE0F"):
        tmpfont.check_glyph_variants(fonts, p, ["x\u2661\ufe0f"])

    class Own:
        def __init__(self, r):
            self.r = r

        def _in_font(self, font, u):
            assert u == 0x5F
            return self.r
    assert [tmpfont.underline_character(Own(r), p) for r in (("baked", p), ("runtime", p), None)] == [True, True, False]

    class Player:
        def __init__(self, **tt):
            self.tt = {"m_fallbackFontAssets": [], "m_defaultFontAsset": {"m_FileID": 0, "m_PathID": 0},
                       "m_missingGlyphCharacter": 0, **tt}

        def resource(self, path):
            assert path == "TMP Settings"
            return "settings"

        def mono(self, o):
            return self.tt
    assert tmpfont.missing_glyph_character(Player()) == 0x25A1
    assert tmpfont.missing_glyph_character(Player(m_missingGlyphCharacter=0x3F)) == 0x3F
    with pytest.raises(NotImplementedError, match="fallback font assets"):
        tmpfont.missing_glyph_character(Player(m_fallbackFontAssets=[{"m_PathID": 5}]))


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
    monkeypatch.setattr(storyfonts.advui, "chat_windows", lambda episode: {})
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


def test_frame_bindings_and_texts():
    """A binding per TMP text of each frame (as chat_bindings, a UIText's text with the sprite asset); the serialized
    frame texts and the texts a frame with an IAdvFrameTextReceiver gets at run time from its Frame rows'
    TargetTextIDs (NFC, then "@" and a line feed)."""
    body = {"class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"name": "J SDF"},
            "m_sharedMaterial": {"material": "J - Default"}, "m_fontSize": 52.0, "m_text": "", "m_lineSpacing": 0.0,
            "m_tintAllSprites": 0}
    loc = {"class": "LocalizeText", "m_Enabled": 1, "_localizeEnabled": 1}
    ui = {"class": "UIText", "_targetText": {"gameObject": "Card/Body"}}
    frames = {"slander": [{"path": "Card", "components": [{"class": "AdvSlanderCommentFrame"}]},
                          {"path": "Card/Body", "components": [body, loc, ui]}],
              "plain": [{"path": "Img", "components": [{"class": "Image"}]},
                        {"path": "Label", "components": [{**body, "m_text": "Fixed"}]}]}
    lang = {"languages": {0: {"fontNames": ["J"], "additionalFontNames": []},
                          1: {"fontNames": ["E"], "additionalFontNames": []}}, "materialTypes": ["Default"]}
    got = storyfonts.frame_bindings(frames, lang, 1, "Emoji")
    assert set(got) == {"slander", "plain"} and set(got["slander"]) == {"Card/Body"} and set(got["plain"]) == {"Label"}
    b = got["slander"]["Card/Body"]
    assert b["localized"] == {"fontAsset": "E SDF", "material": "E - Default", "lineSpacing": -100.0}
    assert (b["spriteAsset"], b["m_tintAllSprites"], b["m_fontSize"]) == ("Emoji", 0, 52.0)
    episode = {"text": {"t1": {"english": "Ame\u0301lie"}, "t2": {"english": "id"}, "t3": {"english": 5}},
               "commands": [{"cmd": "Frame", "TargetAssetName": "slander", "TargetTextIDs": ["t1", "t2", "t3", "nope"]},
                            {"cmd": "Frame", "TargetAssetName": "plain", "TargetTextIDs": ["t2"]},
                            {"cmd": "Frame", "TargetAssetName": "slander", "TargetTextIDs": ["t2"], "IgnoreData": 1},
                            {"cmd": "Talk", "TargetTextIDs": ["t2"]}]}
    assert storyfonts.frame_texts(frames, episode, "english") == ["Fixed", "Am\u00e9lie", "id", "@\n"]
    assert storyfonts.frame_texts({"plain": frames["plain"]}, episode, "english") == ["Fixed"]


def test_dialog_bindings():
    """A binding per text node of each dialog of ui.json, from the components of the dialog's prefab; `localized` by
    LocalizeText's font swap in the language."""
    text = {"class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"name": "J SDF"},
            "m_sharedMaterial": {"material": "J - Default"}, "m_fontSize": 30.0, "m_text": "", "m_lineSpacing": 0.0}

    class Ex:
        def prefab(self, key):
            assert key == "EmbUI/Prefab/D"
            return {"nodes": [{"path": "D/Title", "components": [text, {"class": "LocalizeText", "m_Enabled": 1}]},
                              {"path": "D/Body",
                               "components": [{**text, "m_tintAllSprites": 0},
                                              {"class": "UIText", "_targetText": {"gameObject": "D/Body"}}]},
                              {"path": "D/Image", "components": [{"class": "Image"}]}]}
    ui = {"dialogs": {"D": {"key": "EmbUI/Prefab/D",
                            "nodes": [{"path": "D/Title", "textStyle": {"textKey": "ui_ok"}},
                                      {"path": "D/Body", "textStyle": {}}, {"path": "D/Image"}]}}}
    lang = {"languages": {0: {"fontNames": ["J"]}, 1: {"fontNames": ["E"]}}, "materialTypes": ["Default"]}
    got = storyfonts.dialog_bindings(Ex(), ui, lang, 1, "Emoji")
    assert set(got) == {"D"} and set(got["D"]) == {"D/Title", "D/Body"}
    assert "spriteAsset" not in got["D"]["D/Title"]
    assert (got["D"]["D/Body"]["spriteAsset"], got["D"]["D/Body"]["m_tintAllSprites"]) == ("Emoji", 0)
    t = got["D"]["D/Title"]
    assert (t["class"], t["fontAsset"], t["material"]) == ("TextMeshProUGUI", "J SDF", "J - Default")
    assert t["m_fontSize"] == 30.0
    assert t["localized"]["fontAsset"] == "E SDF" and t["localized"]["material"] == "E - Default"
    assert storyfonts.dialog_bindings(Ex(), {"nodes": []}, lang, 1) == {}
    # the dialogs' text keys are shown characters
    episode = {"text": {}, "title": {"english": ""}, "commands": []}
    with pytest.raises(RuntimeError, match="master data"):
        storyfonts.shown_characters(episode, {"nodes": [], **ui}, None, "english")


def test_text_components_of_every_talk_window():
    """The components of each text node of ui.json from the widget prefab or from the prefab of its talk window (the
    TalkView children of ui.json, advui.window_nodes); a text node found in neither raises."""
    text = {"class": "TextMeshProUGUI", "m_Enabled": 1}
    view = advui.WINDOW_PARENT

    class Cat:
        def has(self, address):
            return False

    class Ex:
        cat = Cat()

        def prefab(self, key):
            if key == advui.WIDGET_KEY:
                return {"nodes": [{"path": "UIAdvWidget/FrontCanvas/Title", "components": [text, {"id": "title"}]}]}
            name = key.rsplit("/", 1)[1]
            return {"nodes": [{"path": name, "components": []},
                              {"path": f"{name}/TalkText", "components": [text, {"id": name}]}]}
    nodes = [{"path": "UIAdvWidget/FrontCanvas/Title", "name": "Title", "textStyle": {}},
             {"path": view, "name": "TalkView"},
             {"path": f"{view}/UIDefaultTalkWindow", "name": "UIDefaultTalkWindow"},
             {"path": f"{view}/UIDefaultTalkWindow/TalkText", "name": "TalkText", "textStyle": {}},
             {"path": f"{view}/UICenterTalkWindow", "name": "UICenterTalkWindow"},
             {"path": f"{view}/UICenterTalkWindow/TalkText", "name": "TalkText", "textStyle": {}}]
    got = storyfonts.text_components(Ex(), {"nodes": nodes})
    assert {p: c[1]["id"] for p, c in got.items()} == {
        "UIAdvWidget/FrontCanvas/Title": "title", f"{view}/UIDefaultTalkWindow/TalkText": "UIDefaultTalkWindow",
        f"{view}/UICenterTalkWindow/TalkText": "UICenterTalkWindow"}
    lone = [n for n in nodes if "UICenterTalkWindow" not in n["path"]] + [
        {"path": f"{view}/UICenterTalkWindow/TalkText", "name": "TalkText", "textStyle": {}}]
    with pytest.raises(RuntimeError, match="0 prefab nodes"):
        storyfonts.text_components(Ex(), {"nodes": lone})


def test_ui_text_targets_and_sprite_binding():
    """A text a UIText (or UIRubyText) drives, on its node or another, gets the emoji sprite asset; any other text
    keeps its serialized sprite asset (only none is)."""
    text = {"class": "TextMeshProUGUI", "m_tintAllSprites": 0, "m_spriteAsset": None}
    nodes = [{"path": "W/A", "components": [text, {"class": "UIText", "_targetText": {"gameObject": "W/A"}}]},
             {"path": "W/B", "components": [text]}]
    assert storyfonts.ui_text_targets(nodes) == {"W/A"}
    rerooted = [{"path": "P/W/A", "components": [text, {"class": "UIRubyText", "_targetText": {"gameObject": "W/A"}}]}]
    assert storyfonts.ui_text_targets(rerooted, "P/") == {"P/W/A"}
    other = [{"path": "W", "components": [{"class": "UIText", "_targetText": {"gameObject": "W/B"}},
                                          {"class": "UIText", "_targetText": None}]}]
    assert storyfonts.ui_text_targets(other) == {"W/B"}, "the text of another node; none without a target"
    assert storyfonts.sprite_binding("W/A", [text], True, "Emoji") == {"spriteAsset": "Emoji", "m_tintAllSprites": 0}
    assert storyfonts.sprite_binding("W/B", [text], False, "Emoji") == {}
    with pytest.raises(NotImplementedError, match="own sprite asset"):
        storyfonts.sprite_binding("W/B", [{**text, "m_spriteAsset": {"name": "Own"}}], False, "Emoji")


def sprite_record():
    """A sprite asset record as advui.sprite_asset_record gives it: 32 x 32 glyphs in a 128 x 128 sheet."""
    names = [("1f60a", 0x1F60A), ("1f60a", 0x1F60A), ("2764-fe0f", 0x2764), ("1f647-200d-2640-fe0f", 0x1F647),
             ("1f647", 0x1F647), ("0", 0), ("1f600", 0x1F600)]
    chars = [{"index": i, "unicode": u, "name": n, "glyph": i, "scale": 1.0} for i, (n, u) in enumerate(names)]
    metrics = {"m_Width": 32.0, "m_Height": 32.0, "m_HorizontalBearingX": 0.0, "m_HorizontalBearingY": 28.8,
               "m_HorizontalAdvance": 32.0}
    glyphs = {str(i): {"metrics": metrics, "scale": 1.0, "atlasIndex": 0,
                       "rect": {"m_X": 32 * (i % 4), "m_Y": 32 * (i // 4), "m_Width": 32, "m_Height": 32}}
              for i in range(len(names)) if i != 6}
    mat = {"material": "Emoji Material", "shader": {"shader": "TextMeshPro/Sprite"}, "keywords": [],
           "textures": {"_MainTex": {"texture": {"name": "Sheet", "width": 128, "height": 128, "format": 48},
                                     "scale": {"x": 1.0, "y": 1.0}, "offset": {"x": 0.0, "y": 0.0}}},
           "ints": {}, "floats": {}, "colors": {"_Color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}}}
    return {"name": "Emoji", "faceInfo": {"m_PointSize": 0.0}, "characters": chars, "glyphs": glyphs,
            "sequences": [{"name": "2764-fe0f", "unicode": 0x2764},
                          {"name": "1f647-200d-2640-fe0f", "unicode": 0x1F647}],
            "sheet": {"name": "Sheet", "width": 128, "height": 128, "mipCount": 1, "settings": {}}, "material": mat}


def test_sprite_needs():
    """The sprites of the sequences the texts hold (by name hash, the first character of a name) and of the code
    points drawn by the sprite asset (the first character of a code point); a character without its glyph is left
    out; a sequence that does not match stays text."""
    rec = sprite_record()
    got = storyfonts.sprite_needs(rec, ["a\u2764\ufe0f", "\U0001F647\u200d\u2640\ufe0f", "\u2764x"],
                                  {0x1F60A, 0x1F600, 0x41})
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
        a[4:h - 4, 4:w - 4] = color
        b = io.BytesIO()
        Image.fromarray(a, "RGBA").save(b, format="PNG")
        return b.getvalue()
    order = [".notdef", "space", "u1F60A", "u1F647", "u2764", "u200D", "u2640", "uFE0F", "u1F647_u200D_u2640"]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x20: "space", 0x1F60A: "u1F60A", 0x1F647: "u1F647", 0x2764: "u2764", 0x200D: "u200D",
                          0x2640: "u2640", 0xFE0F: "uFE0F"})
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
    for n, c in (("u1F60A", (255, 200, 0, 255)), ("u1F647", (0, 0, 255, 255)), ("u2764", (255, 0, 0, 255)),
                 ("u1F647_u200D_u2640", (255, 0, 255, 255))):
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
    assert emoji.glyph("\U0001F647\u200d\u2640\ufe0f") == "u1F647_u200D_u2640"
    assert emoji.glyph("\u2764\ufe0f") == "u2764" and emoji.glyph("\U0001F600") is None
    a = emoji.image("\U0001F60A", 32, 32)
    assert a.shape == (32, 32, 4) and a[16, 16].tolist() == [255, 200, 0, 255] and a[0, 0, 3] == 0
    rec = sprite_record()
    got = storyfonts.open_sprite_asset(emoji, rec, [3, 2, 0, 6], "Test Emoji Regular Sprites")
    asset = got["asset"]
    assert [c["index"] for c in asset["characters"]] == [0, 2, 3], "character 6 has no glyph in the record"
    assert asset["faceInfo"] == rec["faceInfo"] and asset["sequences"] == rec["sequences"]
    assert got["missing"] == [] and set(asset["glyphs"]) == {"0", "2", "3"}
    (page, px), = got["pages"]
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
        self.name, self.chars, self.fb, self.variants = name, set(have), list(fallbacks), {}
        self.tt = {"m_FaceInfo": {"m_PointSize": float(point)}, "m_AtlasPadding": pad, "m_AtlasRenderMode": 4134,
                   **game()["style"]}
        self.material_obj = ("default", name, pad)


def chain_env(monkeypatch, fonts, lang):
    """open_font_set's game side over the ChainFont list `fonts` and the LocalizeManager table `lang`."""
    by_name = {f.name: f for f in fonts}

    class FontSet:
        def __init__(self, ex):
            self.fonts = dict(by_name)

        def by_key(self, key):
            return self.fonts[key.rsplit("/", 1)[1]]

        def fallbacks(self, f):
            return [self.fonts[n] for n in f.fb]

        def _in_font(self, f, u):
            return ("baked", f) if u in f.chars else None

        def lookup(self, primary, u):
            for f in [primary] + [self.fonts[n] for n in storyfonts.search_order(self.fallbacks, primary,
                                                                                  set(self.fonts))]:
                if u in f.chars:
                    return ("baked", f)
            return None

    class Ex:
        def material(self, obj):
            if isinstance(obj, tuple):                    # a font asset's default material
                m = game(pad=obj[2])["material"]
                m = json.loads(json.dumps(m))
                m["material"] = f"{obj[1][:-4]} SDF Material"
                m["floats"].update({"_WeightNormal": 0.0, "_WeightBold": 0.5 + obj[2] / 100, "_FaceDilate": 0.0})
                return m
            m = outline_material(pad=by_name[obj.split("/")[1] + " SDF"].tt["m_AtlasPadding"])
            m["material"] = obj.rsplit("/", 1)[1]
            m["floats"].update({"_WeightNormal": 0.0, "_WeightBold": 0.75, "_FaceDilate": 0.0})
            return m

        def key_object(self, key):
            return key

    class Cat:
        def has(self, key):
            return True
    monkeypatch.setattr(tmpfont, "FontSet", FontSet)
    monkeypatch.setattr(tmpfont, "missing_glyph_character", lambda player: 0x25A1)
    monkeypatch.setattr(storyfonts.textstyle, "language_fonts", lambda player: lang)
    return Cat(), Ex()


# LocalizeManager fonts: ja and en J, zh Z, ko K
CHAIN_LANG = {"languages": {0: {"fontNames": ["J"], "additionalFontNames": []},
                            1: {"fontNames": ["J"], "additionalFontNames": []},
                            2: {"fontNames": ["Z"], "additionalFontNames": []},
                            3: {"fontNames": ["Z"], "additionalFontNames": []},
                            4: {"fontNames": ["K"], "additionalFontNames": ["K"]}}, "materialTypes": ["OutlineX"]}


def test_counterpart_language():
    assert storyfonts.counterpart_language(CHAIN_LANG, "ko", "K SDF") == "ko"
    assert storyfonts.counterpart_language(CHAIN_LANG, "ko", "J SDF") == "ja"           # the first that lists it
    assert storyfonts.counterpart_language(CHAIN_LANG, "en", "J SDF") == "en"           # its own language first
    assert storyfonts.counterpart_language(CHAIN_LANG, "ja", "Z SDF") == "zh-Hant"
    assert storyfonts.counterpart_language(CHAIN_LANG, "ja", "Fallback SDF") is None


def test_fallback_material():
    """GetFallbackMaterial: the text material with the target's texture, gradient scale, texture size and weights;
    UpdateShaderRatios in float32 for those."""
    m = outline_material(pad=15)
    m["floats"].update({"_WeightNormal": 0.0, "_WeightBold": 0.75, "_FaceDilate": 0.0, "_ScaleRatioA": 0.9,
                        "_ScaleRatioC": 0.7, "_UnderlayDilate": 0.0, "_UnderlaySoftness": 0.0})
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


def test_open_font_set_mirrors_the_fallback_chain(tmp_path, monkeypatch):
    """ko texts of K (fallback J, whose fallbacks are a game-only asset X and K): a character the ko font file
    lacks goes to the asset of the ja font file standing for J; the missing glyph; the fallback materials; the
    bindings."""
    ko = storyfonts.FontFile(make_font(tmp_path / "Ko.ttf", family="Ko Sans", underscore=True, drop={0x53E3},
                                       extra={0x25A1: "A"}))
    ja = storyfonts.FontFile(make_font(tmp_path / "Ja.ttf", family="Ja Sans", underscore=True))
    fonts = [ChainFont("K SDF", {0x41, 0x42, 0x20, 0x5F, 0x25A1, 0x78}, ["J SDF"], pad=15, point=100),
             ChainFont("J SDF", {0x53E3, 0x48, 0x5F}, ["X SDF", "K SDF"]), ChainFont("X SDF", {0x2606})]
    cat, ex = chain_env(monkeypatch, fonts, CHAIN_LANG)
    binding = {"localized": {"fontAsset": "K SDF", "material": "K - OutlineX", "lineSpacing": 0}}
    asked = []

    def font_of(code):
        asked.append(code)
        return {"ja": ja}[code]
    r = storyfonts.open_font_set(cat, "player", ex, "ko", ko, [binding], ["AB \u53e3Hx\u2661_"], tmp_path, font_of)
    prim, fb = "Ko Sans Regular SDF", "Ja Sans Regular SDF (J SDF)"
    assert asked == ["ja"] and set(r["fonts"]) == {prim, fb}
    p, f = r["fonts"][prim], r["fonts"][fb]
    assert p["fallbacks"] == [fb] and f["fallbacks"] == [prim]
    assert {ord(c) for c in "AB Hx_\u25a1"} <= {int(u) for u in p["characters"]}
    assert str(0x53E3) not in p["characters"] and str(0x2661) not in p["characters"]
    assert str(0x53E3) in f["characters"] and str(0x41) not in f["characters"] and str(0x5F) not in f["characters"]
    assert p["missingGlyph"] == {"unicode": 0x25A1, "characters": [0x2661]} and "missingGlyph" not in f
    assert r["missing"] == set() and r["substituted"] == {0x2661}
    assert p["atlasPadding"] == 15 and f["atlasPadding"] == 4 and f["faceInfo"]["m_PointSize"] == 40
    text, fbm = "Ko Sans Regular - OutlineX", f"Ko Sans Regular - OutlineX + {fb}"
    assert set(r["materials"]) == {f"{prim} Material", text, f"{fb} Material", fbm}
    m, own = r["materials"][fbm], r["materials"][text]
    assert m["floats"]["_GradientScale"] == 5.0 and own["floats"]["_GradientScale"] == 16.0
    assert m["textures"]["_MainTex"] == r["materials"][f"{fb} Material"]["textures"]["_MainTex"]
    assert m["keywords"] == own["keywords"] and m["floats"]["_OutlineWidth"] == own["floats"]["_OutlineWidth"]
    assert binding["localized"] == {"fontAsset": prim, "material": text, "lineSpacing": 0}
    assert all((tmp_path / t["texture"]).is_file() for t in r["textures"].values())
    # nothing the ko font lacks: no fallback asset, no fallback material, no other font file asked for
    asked.clear()
    b2 = {"localized": {"fontAsset": "K SDF", "material": "K - OutlineX", "lineSpacing": 0}}
    r2 = storyfonts.open_font_set(cat, "player", ex, "ko", ko, [b2], ["ABx"], tmp_path / "b", font_of)
    assert asked == [] and set(r2["fonts"]) == {prim} and r2["fonts"][prim]["fallbacks"] == []
    assert set(r2["materials"]) == {f"{prim} Material", text} and "missingGlyph" not in r2["fonts"][prim]
    # a character only the ja file has, without it: a ConfigError naming the setting
    b3 = {"localized": {"fontAsset": "K SDF", "material": "K - OutlineX", "lineSpacing": 0}}
    with pytest.raises(storyfonts.ConfigError, match="needs the font file of ja"):
        storyfonts.open_font_set(cat, "player", ex, "ko", ko, [b3], ["A\u53e3"], tmp_path / "c")
    # a character no file of the chain maps stays with the primary as missing; a sprite-drawn one is left out
    b4 = {"localized": {"fontAsset": "K SDF", "material": "K - OutlineX", "lineSpacing": 0}}
    r4 = storyfonts.open_font_set(cat, "player", ex, "ko", ko, [b4], ["A\u2606B"], tmp_path / "d", font_of,
                                  sprite_drawn={"K SDF": {0x42}})
    assert r4["missing"] == {0x2606} and str(0x42) not in r4["fonts"][prim]["characters"]
