"""textstyle: TMP text records and material style values (synthetic data)."""
import json

import pytest

from nnnotes import advui, config, languages, textstyle, tmpfont
from nnnotes.config import Config, ConfigError

WHITE = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
DARK = {"r": 0.2, "g": 0.2, "b": 0.2, "a": 0.8}
SHADOW = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.5}
TW = languages.mode("zh-Hant")
LANG = {"languages": {0: {"fontNames": ["MainJ", "Digits"], "additionalFontNames": []},
                      1: {"fontNames": ["MainE", "DigitsE"], "additionalFontNames": []},
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
    swap = textstyle.font_swap(LANG, TW)
    assert swap == {"MainJ SDF": "MainT SDF", "Digits SDF": "Digits SDF"}
    assert textstyle.localize_text("p", "MainJ SDF", "MainJ - Outline", swap, LANG, TW) == \
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
    rec = textstyle.text_style(component(), LOCALIZE, LANG, style, TW)
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
    plain = textstyle.text_style(component(m_lineSpacing=-10.0), None, LANG, style, TW)
    assert plain["localized"] is False and plain["lineSpacing"] == {"serialized": -10.0, "applied": -10.0,
                                                                    "byLanguage": None}
    keyed = textstyle.text_style(component(m_enableVertexGradient=1), {**LOCALIZE, "_masterTextID": "4021"}, LANG,
                                 style, TW)
    assert keyed["textKey"] == "4021" and keyed["colorGradient"] == {"topLeft": WHITE}


def test_no_font_data_in_the_record():
    style = textstyle.material_style(material(["OUTLINE_ON", "UNDERLAY_ON"], _OutlineWidth=0.2), point_size=100.0)
    rec = textstyle.text_style(component(), LOCALIZE, LANG, style, TW)
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
    styles = textstyle.TextStyles(ex, player=None, mode=TW)
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


def test_the_language_is_an_argument():
    assert not hasattr(textstyle, "LANGUAGE_MODE") and not hasattr(textstyle, "LANGUAGE_FIELD")
    en = languages.mode("en")
    swap = textstyle.font_swap(LANG, en)
    assert swap == {"MainJ SDF": "MainE SDF", "Digits SDF": "DigitsE SDF"}
    assert textstyle.localize_text("p", "MainJ SDF", "MainJ - Outline", swap, LANG, en) == \
        {"fontAsset": "MainE SDF", "material": "MainE - Outline", "lineSpacing": -100.0}
    assert textstyle.font_swap(LANG, languages.mode("ja")) == {"MainJ SDF": "MainJ SDF", "Digits SDF": "Digits SDF"}
    style = textstyle.material_style(material(), point_size=100.0)
    assert textstyle.text_style(component(), LOCALIZE, LANG, style, en)["lineSpacing"]["applied"] == -100.0


def test_story_ui_language_follows_the_setting(tmp_path):
    assert advui.about("en") == "ADV front canvas UI data (UIAdvWidget + UIDefaultTalkWindow), en"
    assert advui.language_doc("en") == {"mode": 1, "field": "english", "lineSpacing": -100.0}
    assert advui.language_doc("zh-Hant") == {"mode": 2, "field": "traditionalChinese", "lineSpacing": 5.0}
    assert list(advui.language_doc("ko")) == ["mode", "field", "lineSpacing"]
    # no language: the setting is named before anything is read
    with pytest.raises(ConfigError, match="catalog.language"):
        advui.extract(None, None, {}, tmp_path)
    with pytest.raises(ConfigError, match="not one of the game's languages"):
        advui.extract(None, None, {}, tmp_path, language="xx")
    config.use(Config(overrides={("catalog", "language"): "ko"}))
    assert languages.configured() == "ko"
    config.use(Config(overrides={("catalog", "language"): "xx"}))
    with pytest.raises(ConfigError, match="catalog.language"):
        languages.configured()


def test_shown_texts_take_the_language_field():
    def lines(ja, en):
        return {"japanese": ja, "english": en}
    episode = {
        "commands": [
            {"cmd": "Talk", "TargetName": "a", "AdvTextID": "t1", "TargetTextIDs": ["n1"],
             "lines": lines("<b>ja1</b>", "en1")},
            {"cmd": "Talk", "TargetName": "b", "AdvTextID": "t2", "TargetStatus": 1, "lines": lines("ja2", "en2")},
            {"cmd": "Talk", "TargetName": "c", "AdvTextID": "t3", "TargetStatus": 2, "TargetTextIDs": ["n3"],
             "lines": lines("ja3", "en3")},
            {"cmd": "Location", "lines": lines("jaL", "enL")},
            {"cmd": "Talk", "TargetName": "d", "AdvTextID": "t4", "TargetTextIDs": ["n1", "n2"],
             "lines": lines("ja4", "en4")},
        ],
        "text": {"n1": lines("名1", "Name1"), "n2": lines("名2", "Name2"), "n3": lines("名3", "Name3"),
                 "unk": lines("？", "???"), "split": lines("・", "&")},
        "title": lines("題", "Title"),
    }
    ids = {"_unknownCharacterNameTextId": "unk", "_splitCharacterNameTextId": "split"}
    assert advui.shown_texts(episode, ids, "english") == ["en1", "en2", "en3", "enL", "en4", "&", "???", "Name1",
                                                          "Name2", "Title"]
    assert advui.shown_texts(episode, ids, "japanese")[:2] == ["ja1", "ja2"]
    with pytest.raises(RuntimeError, match="no title"):
        advui.shown_texts({**episode, "title": None}, ids, "english")
