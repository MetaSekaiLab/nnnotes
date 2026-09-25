"""textstyle: TMP text records and material style values (synthetic data)."""
import json

import pytest

from nnnotes import advui, textstyle, tmpfont

WHITE = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
DARK = {"r": 0.2, "g": 0.2, "b": 0.2, "a": 0.8}
SHADOW = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.5}
LANG = {"languages": {0: {"fontNames": ["MainJ", "Digits"], "additionalFontNames": []},
                      2: {"fontNames": ["MainT", "Digits"], "additionalFontNames": []}},
        "materialTypes": ["Default", "Outline"]}


def material(keywords=(), **floats):
    f = {"_GradientScale": 16.0, "_FaceDilate": 0.0, "_WeightNormal": 0.0, "_WeightBold": 0.75,
         "_OutlineWidth": 0.0, "_OutlineSoftness": 0.0, "_UnderlayOffsetX": 0.0, "_UnderlayOffsetY": 0.0,
         "_UnderlayDilate": 0.0, "_UnderlaySoftness": 0.0, "_TextureWidth": 4096.0, "_TextureHeight": 4096.0}
    f.update(floats)
    return {"material": "MainT - Outline", "keywords": list(keywords), "floats": f,
            "colors": {"_FaceColor": WHITE, "_OutlineColor": DARK, "_UnderlayColor": SHADOW},
            "textures": {"_MainTex": {"texture": {"name": "MainT Atlas"}}}}


def component(**over):
    c = {"class": "TextMeshProUGUI", "m_Enabled": 1, "m_fontAsset": {"asset": "TMP_FontAsset", "name": "MainJ SDF"},
         "m_sharedMaterial": {"material": "MainJ - Outline"}, "m_text": "placeholder", "m_isRichText": 1,
         "m_parseCtrlCharacters": 1, "m_fontSize": 36.0, "m_enableAutoSizing": 1, "m_fontSizeMin": 18.0,
         "m_fontSizeMax": 40.0, "m_charWidthMaxAdj": 0.0, "m_lineSpacingMax": 0.0, "m_fontStyle": 1 | 4,
         "m_fontWeight": 400, "m_HorizontalAlignment": 2, "m_VerticalAlignment": 512, "m_TextWrappingMode": 1,
         "m_overflowMode": 1, "m_margin": {"x": 1.0, "y": 2.0, "z": 3.0, "w": 4.0}, "m_lineSpacing": 0.0,
         "m_paragraphSpacing": 0.0, "m_characterSpacing": 2.0, "m_wordSpacing": 0.0,
         "m_characterHorizontalScale": 1.0, "m_enableKerning": 0, "m_ActiveFontFeatures": [textstyle.KERN],
         "m_isRightToLeft": 0, "m_isOrthographic": 1, "m_fontColor": WHITE, "m_colorMode": 3,
         "m_enableVertexGradient": 0, "m_fontColorGradient": {"topLeft": WHITE}, "m_overrideHtmlColors": 0}
    c.update(over)
    return c


LOCALIZE = {"class": "LocalizeText", "m_Enabled": 1, "_masterTextID": "0", "_localizeEnabled": 1}


def test_scale_ratios_follow_update_shader_ratios():
    m = material(["OUTLINE_ON", "UNDERLAY_ON"], _FaceDilate=0.01, _OutlineWidth=0.21, _OutlineSoftness=0.04,
                 _UnderlayOffsetX=0.28, _UnderlayOffsetY=-0.22, _UnderlayDilate=0.5, _UnderlaySoftness=0.78)
    a, c = textstyle.scale_ratios(m["floats"], m["keywords"])
    assert a == pytest.approx(15 / 16)
    assert c == pytest.approx((15 - (0.75 / 4 + 0.01) * 15) / (16 * 1.56))
    assert textstyle.scale_ratios(m["floats"], ["RATIOS_OFF"]) == (1.0, 1.0)


def test_material_style_values_in_em():
    m = material(["OUTLINE_ON", "UNDERLAY_ON"], _FaceDilate=0.1, _OutlineWidth=0.2, _OutlineSoftness=0.05,
                 _UnderlayOffsetX=0.5, _UnderlayOffsetY=-0.25, _UnderlayDilate=0.25, _UnderlaySoftness=0.5)
    a, c = textstyle.scale_ratios(m["floats"], m["keywords"])
    em = 16.0 / 100.0                                     # _GradientScale texels per SDF unit / point size
    s = textstyle.material_style(m, point_size=100.0)
    assert s["face"] == {"color": WHITE, "dilateEm": pytest.approx(0.1 * a * em, abs=1e-6),
                         "boldDilateEm": pytest.approx((0.75 / 4 + 0.1) * a * em, abs=1e-6),
                         "softnessEm": pytest.approx(2 * 0.05 * a * em, abs=1e-6)}
    assert s["outline"] == {"color": DARK, "widthEm": pytest.approx(0.2 * a * em, abs=1e-6),
                            "softnessEm": s["face"]["softnessEm"]}
    u = s["underlay"]
    assert u["color"] == SHADOW and u["inner"] is False
    assert u["offsetEm"] == [pytest.approx(0.5 * c * em, abs=1e-6), pytest.approx(0.25 * c * em, abs=1e-6)]
    assert u["dilateEm"] == pytest.approx(0.25 * c * em, abs=1e-6)
    assert u["softnessEm"] == pytest.approx(2 * 0.5 * c * em, abs=1e-6)
    # face info scale and point size set the texel size
    assert textstyle.material_style(m, point_size=50.0, face_scale=0.5)["outline"]["widthEm"] == \
        s["outline"]["widthEm"]


def test_material_style_without_layers_and_unknown_keywords():
    s = textstyle.material_style(material(), point_size=80.0)
    assert s["outline"] is None and s["underlay"] is None and s["face"]["dilateEm"] == 0.0
    inner = textstyle.material_style(material(["UNDERLAY_INNER"]), point_size=80.0)["underlay"]
    assert inner["inner"] is True
    with pytest.raises(NotImplementedError, match="GLOW_ON"):
        textstyle.material_style(material(["GLOW_ON"]), point_size=80.0)
    no_sdf = material()
    del no_sdf["floats"]["_GradientScale"]
    with pytest.raises(NotImplementedError, match="distance-field"):
        textstyle.material_style(no_sdf, point_size=80.0)


def test_localization_and_roles():
    swap = textstyle.font_swap(LANG)
    assert swap == {"MainJ SDF": "MainT SDF", "Digits SDF": "Digits SDF"}
    assert textstyle.localize_text("p", "MainJ SDF", "MainJ - Outline", swap, LANG) == \
        {"fontAsset": "MainT SDF", "material": "MainT - Outline", "lineSpacing": 5.0}
    assert textstyle.material_type("MainJ SDF Material") == "Default"
    assert textstyle.material_type("MainJ - Outline (Instance)") == "Outline"
    assert textstyle.font_role("MainJ SDF", LANG) == "primary"
    assert textstyle.font_role("Digits SDF", LANG) == "number"
    with pytest.raises(RuntimeError, match="lookup table"):
        textstyle.font_role("Other SDF", LANG)
    assert textstyle.font_dir("MainT SDF") == "EmbFont/MainT/"
    assert textstyle.face_metrics({"m_Scale": 1.0, "m_PointSize": 100.0, "m_LineHeight": 115.5,
                                   "m_AscentLine": 89.2, "m_DescentLine": -26.3},
                                  {"normalSpacingOffset": 0.0, "boldSpacing": 7.0}) == \
        {"lineHeightEm": 1.155, "ascentEm": 0.892, "descentEm": -0.263, "spacingOffset": 0.0, "boldSpacing": 7.0}


def test_text_record_shape():
    style = textstyle.material_style(material(["OUTLINE_ON"], _OutlineWidth=0.2), point_size=100.0)
    rec = textstyle.text_style(component(), LOCALIZE, LANG, style)
    assert rec["fontRole"] == "primary" and rec["materialType"] == "Outline"
    assert rec["localized"] is True and rec["textKey"] is None and rec["text"] == "placeholder"
    assert rec["fontSize"] == 36.0
    assert rec["autoSize"] == {"enabled": True, "min": 18.0, "max": 40.0, "maxCharWidthAdjust": 0.0,
                               "maxLineSpacingAdjust": 0.0}
    assert rec["fontStyle"] == ["bold", "underline"]
    assert rec["alignment"] == {"horizontal": "center", "vertical": "middle"}
    assert rec["wrapping"] == "normal" and rec["overflow"] == "ellipsis"
    assert rec["margin"] == {"left": 1.0, "top": 2.0, "right": 3.0, "bottom": 4.0}
    assert rec["lineSpacing"] == {"serialized": 0.0, "applied": 5.0,
                                  "byLanguage": {"japanese": 0.0, "english": -100.0, "traditionalChinese": 5.0,
                                                 "simplifiedChinese": 5.0, "korean": 5.0}}
    assert rec["characterSpacing"] == 2.0 and rec["kerning"] is True
    assert rec["color"] == WHITE and rec["colorMode"] == "fourCornersGradient" and rec["colorGradient"] is None
    assert rec["face"] == style["face"] and rec["outline"] == style["outline"] and rec["underlay"] is None
    # without an enabled LocalizeText the serialized line spacing applies
    plain = textstyle.text_style(component(m_lineSpacing=-10.0), None, LANG, style)
    assert plain["localized"] is False and plain["lineSpacing"] == {"serialized": -10.0, "applied": -10.0,
                                                                    "byLanguage": None}
    keyed = textstyle.text_style(component(m_enableVertexGradient=1), {**LOCALIZE, "_masterTextID": "4021"}, LANG,
                                 style)
    assert keyed["textKey"] == "4021" and keyed["colorGradient"] == {"topLeft": WHITE}


def test_no_font_data_in_the_record():
    style = textstyle.material_style(material(["OUTLINE_ON", "UNDERLAY_ON"], _OutlineWidth=0.2), point_size=100.0)
    rec = textstyle.text_style(component(), LOCALIZE, LANG, style)
    blob = json.dumps(rec)
    for leak in ("SDF", "MainJ", "MainT", "Atlas", "m_fontAsset", "m_sharedMaterial", "_MainTex", "glyph",
                 "texture", "shader"):
        assert leak not in blob, leak
    assert set(rec) >= {"fontRole", "fontSize", "autoSize", "alignment", "wrapping", "overflow", "lineSpacing",
                        "characterSpacing", "wordSpacing", "color", "fontStyle", "face", "outline", "underlay"}


class FakeObject:
    def __init__(self, tt):
        self.tt = tt

    def read_typetree(self):
        return self.tt


class FakeCatalog:
    def __init__(self, keys):
        self.keys = keys

    def has(self, key):
        return key in self.keys


class FakeExporter:
    def __init__(self, objects, materials):
        self.objects, self.materials = objects, materials
        self.cat = FakeCatalog(set(objects) | set(materials))

    def key_object(self, key):
        return self.objects.get(key) or FakeObject({"_material": key})

    def material(self, o):
        return self.materials[o.tt["_material"]]


def test_text_styles_reads_the_localized_material(monkeypatch):
    monkeypatch.setattr(textstyle, "language_fonts", lambda player: LANG)
    face = {"m_Scale": 1.0, "m_PointSize": 100.0, "m_LineHeight": 120.0, "m_AscentLine": 90.0,
            "m_DescentLine": -30.0}
    ex = FakeExporter({"EmbFont/MainT/MainT SDF": FakeObject({"m_FaceInfo": face, "normalSpacingOffset": 0.0,
                                                              "boldSpacing": 7.0})},
                      {"EmbFont/MainT/MainT - Outline": material(["OUTLINE_ON"], _OutlineWidth=0.25)})
    styles = textstyle.TextStyles(ex, player=None)
    rec = styles.record("Canvas/Text", component(), LOCALIZE)
    assert rec["outline"]["widthEm"] == pytest.approx(0.25 * 15 / 16 * 0.16, abs=1e-6)
    assert styles.summary()["roles"] == {"primary": {"lineHeightEm": 1.2, "ascentEm": 0.9, "descentEm": -0.3,
                                                     "spacingOffset": 0.0, "boldSpacing": 7.0}}
    assert styles.summary()["language"] == "traditionalChinese"
    with pytest.raises(KeyError, match="material key"):
        styles.record("Canvas/Text", component(m_sharedMaterial={"material": "MainJ - Default"}), LOCALIZE)


def test_font_modes_and_extra(monkeypatch):
    assert advui.FONT_MODES == ("open", "game")
    with pytest.raises(ValueError, match="fonts must be one of"):
        advui.extract(None, None, {}, ".", fonts="other")
    monkeypatch.setattr(tmpfont.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(tmpfont.ConfigError, match=r"nnnotes\[fonts\]") as e:
        tmpfont.require_extra()
    assert "\n" not in str(e.value) and "fonttools" in str(e.value)
