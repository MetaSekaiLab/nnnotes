"""Synthetic TrueType fonts for the tests (built with fontTools; nothing here comes from game data)."""

import io

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
    glyphs = {
        ".notdef": glyph([square]),
        "space": glyph([]),
        "A": glyph([square]),
        "B": glyph([[(50, 0), (350, 650), (650, 0)]]),
        "kou": glyph(
            [[(80, -50), (80, 750), (920, 750), (920, -50)], [(250, 150), (750, 150), (750, 550), (250, 550)]]
        ),
        "H": glyph([square]),
        "x": glyph([[(50, 0), (50, 510), (450, 510), (450, 0)]]),
    }
    metrics = {
        ".notdef": (700, 100),
        "space": (333, 0),
        "A": (700, 100),
        "B": (700, 50),
        "kou": (1000, 80),
        "H": (700, 100),
        "x": (500, 50),
    }
    if underscore:
        cmap[0x5F] = "underscore"
        glyphs["underscore"] = glyph([[(0, -150), (0, -100), (500, -100), (500, -150)]])
        metrics["underscore"] = (500, 0)
    cmap = {u: g for u, g in {**cmap, **(extra or {})}.items() if u not in drop}
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=880, descent=-120, lineGap=0)
    fb.setupOS2(
        sTypoAscender=880,
        sTypoDescender=-120,
        usWinAscent=880,
        usWinDescent=120,
        sCapHeight=700,
        sxHeight=500,
        version=4,
    )
    fb.setupPost(underlinePosition=-100, underlineThickness=50)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": style,
            "version": "Version 1.000",
            "licenseInfoURL": "https://example.invalid/license",
        }
    )
    buf = io.BytesIO()
    fb.save(buf)
    path.write_bytes(buf.getvalue())
    return path
