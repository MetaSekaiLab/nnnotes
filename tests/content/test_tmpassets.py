"""TextMesh Pro font assets read from an object table (content/tmpassets.py), their lookup and settings, and the
in-memory texture packer (export.MemoryPacker), on synthetic objects; nothing here comes from game data."""

import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from nnnotes.content import tmpassets
from nnnotes.unity import fontsettings
from nnnotes.unity.texels import MemoryPacker, packed_png
from tests.helpers.fonts import make_font

FILE = "CAB-tmpassets"
SETTINGS = {"m_FilterMode": 1, "m_Aniso": 1, "m_MipBias": 0.0, "m_WrapU": 1, "m_WrapV": 1, "m_WrapW": 1}
SCRIPT = "Unity.TextMeshPro|TMPro|TMP_FontAsset"


def oid(pid: int) -> str:
    return f"{FILE}:{pid}"


def ref(o: str | None):
    """A reference value as the object layer writes it."""
    if o is None:
        return None
    file, pid = o.rsplit(":", 1)
    return {"$ref": {"file": file, "pathId": pid}}


class Table:
    """Synthetic objects with the protocol of prefabs.Objects. `entries` maps an object id to (class, name, json,
    files), files being {role: (bytes, extension)}; a json file is written from the json document when it is not None.
    `keys` maps catalog keys to object ids and `roots` lists the root object ids."""

    def __init__(self, entries, keys=None, roots=()):
        self.roots = list(roots)
        self._keys = {k: list(v) for k, v in (keys or {}).items()}
        self._info: dict[str, dict] = {}
        self._data: dict[tuple[str, str], bytes] = {}
        for o, (cls, name, doc, files) in entries.items():
            files = dict(files)
            if doc is not None:
                files["json"] = (json.dumps(doc).encode(), "json")
            self._info[o] = {
                "class": cls,
                "name": name,
                "files": {
                    role: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "ext": ext}
                    for role, (data, ext) in files.items()
                },
            }
            for role, (data, _) in files.items():
                self._data[(o, role)] = data

    def __contains__(self, o: str) -> bool:
        return o in self._info

    def ids(self, cls=None) -> list[str]:
        return sorted(o for o, i in self._info.items() if cls is None or i["class"] == cls)

    def key(self, key: str) -> list[str]:
        if key not in self._keys:
            raise KeyError(f"{key} is not a key of the product")
        return self._keys[key]

    def info(self, o: str) -> dict:
        return self._info[o]

    def bytes(self, o: str, role: str) -> bytes:
        return self._data[(o, role)]

    def json(self, o: str, role: str = "json"):
        return json.loads(self.bytes(o, role))


def png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def texture_doc(name: str, w: int, h: int, fmt: int) -> dict:
    return {
        "m_Name": name,
        "m_Width": w,
        "m_Height": h,
        "m_TextureFormat": fmt,
        "m_MipCount": 1,
        "m_TextureSettings": SETTINGS,
    }


def tmp_font(name, *, chars=(), fallbacks=(), atlases=(), material=None, source=None, dynamic=False) -> dict:
    """The json of a TMP_FontAsset: the fields tmpassets.Font reads, with `chars` in its character table."""
    return {
        "$script": SCRIPT,
        "m_Name": name,
        "m_CharacterTable": [
            {"m_Unicode": u, "m_GlyphIndex": i + 1, "m_Scale": 1.0, "m_ElementType": 1} for i, u in enumerate(chars)
        ],
        "m_GlyphTable": [],
        "m_AtlasPopulationMode": 1 if dynamic else 0,
        "m_AtlasTextures": [ref(a) for a in atlases],
        "m_Material": ref(material),
        "m_FallbackFontAssetTable": [ref(f) for f in fallbacks],
        "m_FontWeightTable": [],
        "m_SourceFontFile": ref(source),
        "m_FaceInfo": {"m_FaceIndex": 0, "m_PointSize": 40.0, "m_UnitsPerEM": 1000},
        "m_AtlasWidth": 256,
        "m_AtlasHeight": 256,
        "m_AtlasPadding": 4,
        "m_AtlasRenderMode": 4134,
        "normalStyle": 0.5,
        "normalSpacingOffset": 0.0,
        "boldStyle": 0.75,
        "boldSpacing": 7.0,
        "italicStyle": 0.35,
        "tabSize": 10,
        "m_FontFeatureTable": {"m_GlyphPairAdjustmentRecords": []},
    }


def material_doc(name, shader=None, texture=None) -> dict:
    """The json of a Material (export.convert material_doc, references as {"$ref"})."""
    return {
        "material": name,
        "shader": ref(shader),
        "keywords": ["OUTLINE_ON"],
        "invalidKeywords": [],
        "renderQueue": 3000,
        "tags": {"RenderType": "Transparent"},
        "disabledPasses": [],
        "textures": {"_MainTex": {"texture": ref(texture), "scale": [1.0, 1.0], "offset": [0.0, 0.0]}},
        "ints": {},
        "floats": {"_GradientScale": 4.0},
        "colors": {},
    }


def atlas_entries(pid: int, name: str, w: int = 4, h: int = 3) -> dict:
    """A Texture2D entry of the table: an RGBA atlas (its PNG holds the texels)."""
    return {
        oid(pid): (
            "Texture2D",
            name,
            texture_doc(name, w, h, 4),
            {"image": (png(np.zeros((h, w, 4), np.uint8)), "png")},
        )
    }


@pytest.mark.parametrize("prefix", ["EmbFont/", "Font/"])
def test_named_font_material_uses_its_available_catalog_folder(prefix):
    entries = {
        oid(10): ("MonoBehaviour", "X SDF", tmp_font("X SDF", material=oid(12)), {}),
        oid(12): ("Material", "X - Default", material_doc("X - Default"), {}),
    }
    keys = {f"{prefix}X/X SDF": [oid(10)], f"{prefix}X/X - Default": [oid(12)]}
    fs = tmpassets.FontSet(Table(entries, keys=keys))
    f = fs.by_key(f"{prefix}X/X SDF")
    assert fs.named_material(f, "X - Default") == f.default_material()


def test_texture_pixels_are_the_decoded_image_bottom_row_first():
    rng = np.random.default_rng(7)
    rgba = rng.integers(0, 256, (3, 4, 4), dtype=np.uint8)
    entries = {oid(1): ("Texture2D", "Atlas", texture_doc("Atlas", 4, 3, 4), {"image": (png(rgba), "png")})}
    tex = tmpassets.Texture(Table(entries), oid(1))
    assert tex.key == (FILE, 1)
    assert tex.record() == {
        "textureRef": oid(1),
        "name": "Atlas",
        "width": 4,
        "height": 3,
        "format": 4,
        "mipCount": 1,
        "settings": SETTINGS,
    }
    assert np.array_equal(tex.pixels, rgba[::-1])
    assert not tex.pixels.flags.writeable


def test_alpha8_texels_keep_their_value_in_alpha():
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    entries = {oid(2): ("Texture2D", "Glyphs", texture_doc("Glyphs", 4, 3, 1), {"image": (png(a), "png")})}
    px = tmpassets.Texture(Table(entries), oid(2)).pixels
    assert np.array_equal(px[:, :, 3], a[::-1])
    assert not px[:, :, :3].any()


def test_texture_image_must_be_png():
    entries = {oid(3): ("Texture2D", "Hdr", texture_doc("Hdr", 4, 4, 1), {"image": (b"astc", "astc")})}
    with pytest.raises(NotImplementedError, match="image file astc"):
        _ = tmpassets.Texture(Table(entries), oid(3)).pixels


def test_font_asset_fields_atlases_and_default_material():
    entries = {
        oid(10): (
            "MonoBehaviour",
            "X SDF",
            tmp_font("X SDF", chars=[0x41, 0x42], atlases=[oid(11), None], material=oid(12)),
            {},
        ),
        **atlas_entries(11, "X Atlas"),
        oid(12): ("Material", "X SDF Material", material_doc("X SDF Material", shader=oid(13), texture=oid(11)), {}),
        oid(13): ("Shader", "TextMeshPro/Distance Field", {"name": "TextMeshPro/Distance Field"}, {}),
    }
    fs = tmpassets.FontSet(Table(entries, keys={"EmbFont/X/X SDF": [oid(10)]}))
    f = fs.by_key("EmbFont/X/X SDF")
    assert f.name == "X SDF"
    assert sorted(f.chars) == [0x41, 0x42]
    assert not f.dynamic and f.source_font is None and f.font_data is None and f.cmap == set()
    assert f.atlas_names() == ["X Atlas", None]
    assert f.atlas_settings() == [SETTINGS]
    assert f.default_material() == {
        "material": "X SDF Material",
        "shader": {"shader": "TextMeshPro/Distance Field"},
        "keywords": ["OUTLINE_ON"],
        "invalidKeywords": [],
        "renderQueue": 3000,
        "tags": {"RenderType": "Transparent"},
        "disabledPasses": [],
        "textures": {
            "_MainTex": {
                "texture": {
                    "textureRef": oid(11),
                    "name": "X Atlas",
                    "width": 4,
                    "height": 3,
                    "format": 4,
                    "mipCount": 1,
                    "settings": SETTINGS,
                },
                "scale": [1.0, 1.0],
                "offset": [0.0, 0.0],
            }
        },
        "ints": {},
        "floats": {"_GradientScale": 4.0},
        "colors": {},
    }


def test_material_without_shader_or_texture_keeps_them_null():
    entries = {oid(12): ("Material", "M", material_doc("M"), {})}
    m = tmpassets.FontSet(Table(entries)).material(oid(12))
    assert m["shader"] is None
    assert m["textures"]["_MainTex"]["texture"] is None


def test_material_texture_must_be_a_texture2d():
    entries = {
        oid(12): ("Material", "M", material_doc("M", texture=oid(11)), {}),
        oid(11): ("RenderTexture", "RT", {"m_Name": "RT"}, {}),
    }
    with pytest.raises(NotImplementedError, match="RenderTexture"):
        tmpassets.FontSet(Table(entries)).material(oid(12))


def test_lookup_follows_the_fallback_chain():
    entries = {
        oid(1): ("MonoBehaviour", "P SDF", tmp_font("P SDF", chars=[0x41], fallbacks=[oid(2)], material=oid(9)), {}),
        oid(2): ("MonoBehaviour", "F SDF", tmp_font("F SDF", chars=[0x42], fallbacks=[oid(3)], material=oid(9)), {}),
        oid(3): ("MonoBehaviour", "G SDF", tmp_font("G SDF", chars=[0x43], material=oid(9)), {}),
        oid(9): ("Material", "M", material_doc("M"), {}),
    }
    fs = tmpassets.FontSet(Table(entries, keys={"EmbFont/P/P SDF": [oid(1)]}))
    p = fs.by_key("EmbFont/P/P SDF")
    assert sorted(fs.fonts) == ["F SDF", "G SDF", "P SDF"]
    assert [fb.name for fb in fs.fallbacks(p)] == ["F SDF"]
    assert fs.lookup(p, 0x41) == ("baked", p)
    assert fs.lookup(p, 0x42)[1].name == "F SDF"
    assert fs.lookup(p, 0x43)[1].name == "G SDF"
    assert fs.lookup(p, 0x44) is None
    assert fs.baked_anywhere(0x43) == ["G SDF"]


def test_dynamic_font_reads_its_source_font(tmp_path):
    pytest.importorskip("fontTools")
    data = make_font(tmp_path / "Test.ttf").read_bytes()
    entries = {
        oid(1): (
            "MonoBehaviour",
            "T SDF",
            tmp_font("T SDF", chars=[0x41], dynamic=True, source=oid(4), material=oid(9)),
            {},
        ),
        oid(4): ("Font", "Test Sans", {"m_Name": "Test Sans"}, {"font": (data, "ttf")}),
        oid(9): ("Material", "M", material_doc("M"), {}),
    }
    f = tmpassets.FontSet(Table(entries)).of(oid(1))
    assert f.dynamic
    assert {0x20, 0x41, 0x42, 0x48, 0x78, 0x53E3} <= f.cmap
    assert f.variants == {}
    assert f.font_data == data
    assert f.source_font == {"name": "Test Sans", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_build_font_settings_reads_the_listed_fonts_and_their_materials():
    lang = {"languages": {"ja": {"fontNames": ["X"], "additionalFontNames": []}}, "materialTypes": ["Outline"]}
    entries = {
        oid(1): (
            "MonoBehaviour",
            "X SDF",
            tmp_font("X SDF", chars=[0x41], fallbacks=[oid(2)], atlases=[oid(4)], material=oid(3)),
            {},
        ),
        oid(2): ("MonoBehaviour", "Y SDF", tmp_font("Y SDF", chars=[0x42], material=oid(5)), {}),
        oid(3): ("Material", "X SDF Material", material_doc("X SDF Material", texture=oid(4)), {}),
        oid(5): ("Material", "Y SDF Material", material_doc("Y SDF Material"), {}),
        oid(6): ("Material", "X - Outline", material_doc("X - Outline"), {}),
        **atlas_entries(4, "X Atlas"),
    }
    keys = {"EmbFont/X/X SDF": [oid(1)], "EmbFont/X/X - Outline": [oid(6)]}
    doc = tmpassets.build_font_settings(
        Table(entries, keys), lang, "9.9.9", lambda name: f"embfont-{name[:-4].lower()}.bundle"
    )
    assert doc["format"] == fontsettings.FORMAT and doc["gameVersion"] == "9.9.9"
    assert sorted(doc["fonts"]) == ["X SDF", "Y SDF"]
    x = doc["fonts"]["X SDF"]
    assert x["material"] == "X SDF Material"
    assert x["fallbacks"] == ["Y SDF"]
    assert x["bundle"] == "embfont-x.bundle"
    assert x["atlasPadding"] == 4 and x["faceInfo"] == {"m_FaceIndex": 0, "m_PointSize": 40.0, "m_UnitsPerEM": 1000}
    assert fontsettings.decode_coverage(x["coverage"]) == bytes(8) + bytes([0b10])
    assert sorted(doc["materials"]) == ["X - Outline", "X SDF Material", "Y SDF Material"]
    assert doc["materials"]["X SDF Material"]["textures"]["_MainTex"]["texture"] == {
        "name": "X Atlas",
        "width": 4,
        "height": 3,
        "format": 4,
    }
    assert doc["objects"] == {
        "1": {"type": "MonoBehaviour", "class": "TMP_FontAsset", "name": "X SDF"},
        "2": {"type": "MonoBehaviour", "class": "TMP_FontAsset", "name": "Y SDF"},
        "3": {"type": "Material", "name": "X SDF Material"},
        "5": {"type": "Material", "name": "Y SDF Material"},
        "6": {"type": "Material", "name": "X - Outline"},
    }
    table = fontsettings.FontSettings(doc)
    assert table.lookup("X SDF", 0x42) == "Y SDF"


def test_missing_glyph_character_of_the_table_resource():
    def table(**fields) -> Table:
        doc = {
            "$script": "Unity.TextMeshPro|TMPro|TMP_Settings",
            "m_fallbackFontAssets": [],
            "m_defaultFontAsset": None,
            "m_missingGlyphCharacter": 0,
        }
        doc.update(fields)
        return Table({oid(9): ("MonoBehaviour", "TMP Settings", doc, {})}, roots=[oid(9)])

    assert tmpassets.missing_glyph_character(table()) == 0x25A1
    assert tmpassets.missing_glyph_character(table(m_missingGlyphCharacter=0x3F)) == 0x3F
    with pytest.raises(NotImplementedError):
        tmpassets.missing_glyph_character(table(m_fallbackFontAssets=[ref(oid(4))]))
    with pytest.raises(ValueError, match="expected one"):
        tmpassets.missing_glyph_character(Table({}))


def test_unresolved_reference_raises():
    assert tmpassets._ref(None) is None
    with pytest.raises(ValueError, match="does not resolve"):
        tmpassets._ref({"$ref": {"fileId": 1, "pathId": "5"}})


class Block:
    """A source texture as MemoryPacker reads it: its key, name, sampler settings, size and RGBA texels."""

    def __init__(self, key, name, pixels):
        self.key, self.name, self.settings = key, name, SETTINGS
        self.height, self.width = pixels.shape[:2]
        self.pixels = pixels


def test_memory_packer_lays_out_blocks_and_returns_the_pages():
    a = np.arange(2 * 4 * 4, dtype=np.uint8).reshape(2, 4, 4)
    b = (np.arange(2 * 3 * 4, dtype=np.uint8) + 100).reshape(2, 3, 4)
    ta, tb = Block((FILE, 1), "A", a), Block((FILE, 2), "B", b)
    packer = MemoryPacker()
    packer.WIDTH = 4  # the blocks fill one row
    ha = packer.add("font_X", ta, 0, 0, 2, 2)
    hb = packer.add("font_X", tb, 1, 0, 3, 2)
    glyph = np.full((1, 1, 4), 9, np.uint8)
    hg = packer.add_array("other", "runtime glyph 7", glyph, (5, 6), SETTINGS)
    textures, pages = packer.build()

    out = np.zeros((2, 4, 4), np.uint8)
    out[:, 0:2] = a[:, 0:2]
    out[:, 2:4] = b[:, 1:3]
    assert textures["font_X"] == {
        "texture": "textures/font_X.png",
        "name": "font_X",
        "width": 4,
        "height": 2,
        "mipCount": 1,
        "settings": SETTINGS,
        "sources": ["A", "B"],
        "blocks": 2,
    }
    assert textures["other"] == {
        "texture": "textures/other.png",
        "name": "other",
        "width": 1,
        "height": 1,
        "mipCount": 1,
        "settings": SETTINGS,
        "sources": ["runtime glyph 7"],
        "blocks": 1,
    }
    assert pages == {"textures/font_X.png": packed_png(out), "textures/other.png": packed_png(glyph)}
    assert (ha["texture"], ha["offset"]) == ("font_X", (0, 0))
    assert (hb["texture"], hb["offset"]) == ("font_X", (1, 0))
    assert (hg["texture"], hg["offset"]) == ("other", (-5, -6))
