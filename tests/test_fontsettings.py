"""The font settings table of the open-font exports (fontsettings)."""
import json

import pytest

from nnnotes import export, fontsettings
from nnnotes.tmpfont import END_OF_TEXT, SPACE

FACE = {"m_PointSize": 40.0, "m_Scale": 1.0}
STYLE = {"normalStyle": 0.0, "normalSpacingOffset": 0.0, "boldStyle": 0.75, "boldSpacing": 7.0, "italicStyle": 35,
         "tabSize": 10}


def font(chars: str, fallbacks=(), material="", variants=()):
    return {"faceInfo": FACE, "atlasPadding": 9, "atlasRenderMode": 4165, **STYLE, "material": material,
            "fallbacks": list(fallbacks), "coverage": fontsettings.encode_coverage(map(ord, chars)),
            "variants": [list(v) for v in variants], "bundle": "fonts.bundle"}


def table(**fonts):
    return {"format": fontsettings.FORMAT, "gameVersion": "1.0.0", "fonts": fonts,
            "materials": {"A SDF Material": {"material": "A SDF Material", "floats": {"_GradientScale": 10.0}}},
            "objects": {"7": {"type": "MonoBehaviour", "class": fontsettings.FONT_CLASS, "name": "A SDF"},
                        "8": {"type": "Material", "name": "A SDF Material"}}}


def test_coverage_round_trip():
    cps = {0x20, 0x41, 0x3042, 0x1F600}
    bits = fontsettings.decode_coverage(fontsettings.encode_coverage(cps))
    assert {u for u in range(len(bits) * 8) if bits[u >> 3] >> (u & 7) & 1} == cps
    assert fontsettings.decode_coverage(fontsettings.encode_coverage([])) == b""


def test_lookup_follows_the_fallback_chain_depth_first():
    """Each fallback, then its own fallbacks, before the next fallback; every asset once (a cycle ends)."""
    s = fontsettings.FontSettings(table(**{"A SDF": font("A", ["B SDF", "C SDF"], "A SDF Material"),
                                           "B SDF": font("B", ["D SDF"]), "C SDF": font("CD", ["A SDF"]),
                                           "D SDF": font("D", ["B SDF"])}))
    assert [s.lookup("A SDF", ord(c)) for c in "ABCDE"] == ["A SDF", "B SDF", "C SDF", "D SDF", None]
    assert s.lookup("C SDF", ord("B")) == "B SDF"
    assert s.has("A SDF", END_OF_TEXT)                               # TextMeshPro's synthesized characters
    assert s.underline("C SDF") is False
    with pytest.raises(KeyError, match="font asset E SDF not in the font settings"):
        s.font("E SDF")


def test_missing_glyph_and_variants():
    s = fontsettings.FontSettings(table(**{"A SDF": font("A \u25a1", ["B SDF"], variants=[(0x845B, 0xE0100)]),
                                           "B SDF": font("\u845b ", variants=[(0x845B, 0xE0101)]),
                                           "C SDF": font("C")}))
    assert s.missing_glyph("A SDF", [ord("A"), 0x845B], 0x25A1) is None
    assert s.missing_glyph("A SDF", [0x2661, ord("A"), ord("Z")], 0x25A1) == {"unicode": 0x25A1,
                                                                              "characters": [ord("Z"), 0x2661]}
    assert s.missing_glyph("B SDF", [ord("Z")], 0x25A1) == {"unicode": SPACE, "characters": [ord("Z")]}
    assert s.missing_glyph("C SDF", [ord("Z")], 0x25A1) == {"unicode": END_OF_TEXT, "characters": [ord("Z")]}
    s.check_glyph_variants("A SDF", ["\u845b\U000e0100"])            # the asset holding U+845B is B SDF
    with pytest.raises(NotImplementedError, match="B SDF: glyph variant of U\\+845B U\\+E0101"):
        s.check_glyph_variants("A SDF", ["x\u845b\U000e0101"])


def test_materials_and_game_record():
    s = fontsettings.FontSettings(table(**{"A SDF": font("A", material="A SDF Material")}))
    m = s.material("A SDF", "A SDF Material")
    m["floats"]["_GradientScale"] = 1.0
    assert s.material("A SDF", "A SDF Material")["floats"]["_GradientScale"] == 10.0     # a copy each time
    assert s.has_material("A SDF", "A SDF Material") and not s.has_material("A SDF", "A - Outline")
    with pytest.raises(KeyError, match="material A - Outline of A SDF not in .* game version 1.0.0"):
        s.material("A SDF", "A - Outline")
    assert s.game_record("A SDF") == {"pointSize": 40.0, "padding": 9, "renderMode": 4165, "style": STYLE,
                                      "material": {"material": "A SDF Material", "floats": {"_GradientScale": 10.0}}}
    assert s.objects[7]["name"] == "A SDF"
    with pytest.raises(ValueError, match="format"):
        fontsettings.FontSettings({"format": "other"})


def test_table_material_keeps_the_texture_values_the_exports_take():
    tex = {"textureRef": "cab:1", "name": "A SDF Atlas", "width": 512, "height": 256, "format": 1,
           "mipCount": 1, "settings": {"m_FilterMode": 1}}
    m = {"material": "A SDF Material", "textures": {"_MainTex": {"texture": tex, "scale": {"x": 1.0, "y": 1.0}},
                                                    "_FaceTex": {"texture": None}}}
    t = fontsettings.table_material(m)
    assert t["textures"]["_MainTex"] == {"texture": {"name": "A SDF Atlas", "width": 512, "height": 256, "format": 1},
                                         "scale": {"x": 1.0, "y": 1.0}}
    assert t["textures"]["_FaceTex"] == {"texture": None} and m["textures"]["_MainTex"]["texture"] is tex


def test_write_one_entry_per_line(tmp_path):
    doc = table(**{"A SDF": font("A\u3042", material="A SDF Material"), "B SDF": font("B")})
    path = tmp_path / "fontsettings.json"
    fontsettings.write(doc, path)
    text = path.read_bytes().decode("utf-8")
    assert json.loads(text) == doc and "\r" not in text and text.endswith("}\n")
    lines = text.splitlines()
    assert sum(ln.startswith('"A SDF":') or ln.startswith('"B SDF":') for ln in lines) == 2
    assert sum(ln.startswith('"7":') or ln.startswith('"8":') for ln in lines) == 2


def test_font_stub_resolves_from_the_objects():
    """A reference into a font bundle left out of the closure: a font asset is named, a material is its record."""
    s = fontsettings.FontSettings(table(**{"A SDF": font("A", material="A SDF Material")}))
    ex = export.Exporter.__new__(export.Exporter)
    ex.font_stubs = s
    asset = export.FontStub(s.objects[7], 7)
    assert ex.font_stub(asset) == {"asset": fontsettings.FONT_CLASS, "name": "A SDF"}
    assert ex.font_stub(export.FontStub(s.objects[8], 8))["floats"] == {"_GradientScale": 10.0}


def test_builtin_table_is_complete():
    """Every fallback, default material and object the shipped table names is in it."""
    s = fontsettings.builtin()
    assert s.doc["format"] == fontsettings.FORMAT and s.fonts
    for name, f in s.fonts.items():
        assert set(f["fallbacks"]) <= set(s.fonts), name
        assert f["material"] in s.materials, name
        assert set(fontsettings.STYLE_KEYS) <= set(f) and f["bundle"], name
        fontsettings.decode_coverage(f["coverage"])
    for rec in s.objects.values():
        assert rec["name"] in (s.fonts if rec["type"] == "MonoBehaviour" else s.materials)
    assert {r["name"] for r in s.objects.values() if r["type"] == "MonoBehaviour"} == set(s.fonts)
