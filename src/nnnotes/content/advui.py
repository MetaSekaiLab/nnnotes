"""TextMesh Pro sprite metadata, emoji sequence lookup and chat runtime text."""

from __future__ import annotations

from ..unity import textstyle

# UIManager: UIManager._cameraController -> UICameraController._uiCamera / _uiCameraData
# UIAdvWidget._centerTalkBackdropCanvasGroup / _centerTalkBackdropImage: the backdrop of a backdrop-filter talk window
# Parts of the widget the runtime draws (and their ancestors). The block canvas
# is not exported.
# the view components of the drawn parts: record key and the serialized references kept (as node paths)
# views recorded with every serialized field (references as node paths)
# the other game and uGUI components recorded in `behaviours` (per class, every component of the node in order):
# buttons and their state and animation parts, raycast targets, colour synchronizers, texts' UIText, safe areas,
# scrolling, DOTween animations, localized sprites, the backlog entry and choice item views, the dialog parts
# the dialogs of the ADV screen: the skip confirm dialog and CommonDialogManager's dialog widget
# AdvMasterIdSettings text ids the drawn parts show: the video skip dialog (AdvMenuView's skip video button), the
# skip confirm dialog and the interruption dialog
# the phone of the chat rows: UIAdvChatWidget's canvas, AdvChatView and the parent of the attached window
CHAT_COMMANDS = ("ChatWindow", "ChatTalk", "ChatStamp", "ChatRead", "ChatTyping")
# AdvMenuView: its serialized references (node paths), the fast-forward button sprites (AdvMenuView.SetFastForwardSpeed)
EMOJI_SPRITE_ASSET_KEY = "Font/SiriusEmoji/SiriusEmojiData"  # LocalizeManager.EmojiSpriteAssetAddress
# TMP_Text.GetUnderlineSpecialCharacter: the underline and the text highlight (DrawTextHighlight) draw with the '_'
# of the text's font asset (no fallback), sampled on the asset's first atlas page
UNDERLINE_CHARACTER = 0x5F

TMP_CLASSES = ("TextMeshProUGUI", "RubyTextMeshProUGUI", "RubyEmojiTextMeshProUGUI")


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------


def emoji_sequence_key(name: str, unicode: int) -> str | None:
    """TMP_EmojiSearchEngine.TryUpdateSequenceLookupTable: the lookup key of a sprite named `name` (a "-" separated
    hex code point name), None when it is a single code point (its name equals `unicode` in hex). The name goes
    through BuildNameInEmojiSurrogateFormat (file name without extension, lower case, each part left-padded with
    "0" to 8 digits); each 8-digit chunk is a code point (char.ConvertFromUtf32). A chunk that is not a scalar value
    (the game appends its 4-digit halves as UTF-16 code units) is not implemented."""
    n = name.rsplit("/", 1)[-1]
    if "." in n:
        n = n[: n.rindex(".")]
    n = n.lower()
    if "-" in n:
        n = "".join(p.rjust(8, "0") for p in n.split("-"))
    if not n or n == f"{unicode & 0xFFFFFFFF:08x}":
        return None
    out = []
    for i in range(0, len(n), 8):
        chunk = n[i : i + 8]
        v = int(chunk, 16)
        if v == 0 or v >= 0x10FFFF or 0xD800 <= v < 0xE000:
            raise NotImplementedError(f"emoji sprite {name}: {chunk} is not a code point")
        out.append(chr(v))
    return "".join(out)


def sprite_record(tt: dict, sheet: dict, material: dict) -> dict:
    """The text layout's sprite asset record from its fields, sheet metadata and material."""
    if tt.get("fallbackSpriteAssets"):
        raise NotImplementedError(f"{tt['m_Name']}: fallback sprite assets")
    chars = [
        {"index": i, "unicode": c["m_Unicode"], "name": c["m_Name"], "glyph": c["m_GlyphIndex"], "scale": c["m_Scale"]}
        for i, c in enumerate(tt["m_SpriteCharacterTable"])
    ]
    glyphs = {
        str(g["m_Index"]): {
            "metrics": g["m_Metrics"],
            "rect": g["m_GlyphRect"],
            "scale": g["m_Scale"],
            "atlasIndex": g["m_AtlasIndex"],
        }
        for g in tt["m_GlyphTable"]
    }
    sequences = [
        {"name": e["name"], "unicode": e["unicode"]}
        for e in tt.get("spriteInfoList") or []
        if e.get("name") and "-" in e["name"]
    ]
    for e in sequences:
        emoji_sequence_key(e["name"], e["unicode"])  # raises on a name the port does not read
    return {
        "name": tt["m_Name"],
        "faceInfo": tt["m_FaceInfo"],
        "characters": chars,
        "glyphs": glyphs,
        "sequences": sequences,
        "sheet": {
            "name": sheet["m_Name"],
            "width": sheet["m_Width"],
            "height": sheet["m_Height"],
            "mipCount": sheet.get("m_MipCount", 1),
            "settings": sheet.get("m_TextureSettings"),
        },
        "material": material,
    }


def _utf16(s: str) -> tuple[int, ...]:
    b = s.encode("utf-16-le")
    return tuple(int.from_bytes(b[i : i + 2], "little") for i in range(0, len(b), 2))


# TMP_TextUtilities.ToUpperFast's table (k_lookupStringU) for the units below U+0080
TMP_UPPER = (
    "-------------------------------- !-#$%&-()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[-]^_`"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ{|}~-"
)


def tmp_hash(s: str) -> int:
    """TMP_TextUtilities.GetHashCode: h = ((h << 5) + h) ^ ToUpperFast(unit) over the UTF-16 units, as an unsigned
    32-bit value (the game's int32 bits); sprite names and tag values are matched by it."""
    h = 0
    for u in _utf16(s):
        h = ((((h << 5) + h) & 0xFFFFFFFF) ^ (ord(TMP_UPPER[u]) if u < len(TMP_UPPER) else u)) & 0xFFFFFFFF
    return h


def emoji_sequence_table(sequences: list[dict]) -> tuple[dict[tuple, str], set[tuple]]:
    """TMP_EmojiSearchEngine.TryUpdateSequenceLookupTable over a sprite asset record's `sequences`: ({key: sprite
    name}, the fast lookup set of every key's prefixes), keys as tuples of UTF-16 code units; the first entry of a key
    wins."""
    table: dict[tuple, str] = {}
    fast: set[tuple] = set()
    for e in sequences:
        key = emoji_sequence_key(e["name"], e["unicode"])
        if not key:
            continue
        units = _utf16(key)
        fast.update(units[:k] for k in range(1, len(units) + 1))
        table.setdefault(units, e["name"])
    return table, fast


def emoji_sequence_names(table: dict[tuple, str], fast: set[tuple], text: str) -> list[str]:
    """The sprite names TMP_EmojiSearchEngine.ParseEmojiCharSequence writes as <sprite name="..."> tags into `text`
    (emoji_sequence_table): from each position the longest run of UTF-16 units whose every prefix is in the fast
    set, no backtracking; a run that is a whole key is consumed, else the scan goes on at the next unit."""
    if not table or not text:
        return []
    u, out, i = _utf16(text), [], 0
    while i < len(u):
        j = i
        while j < len(u) and (j == i or u[i:j] in fast):
            j += 1
        seq = u[i:j]
        if seq and seq not in fast:
            seq, j = seq[:-1], j - 1
        if seq in table:
            out.append(table[seq])
            i = j - 1 if j > i + 1 else i
        i += 1
    return out


def _lookup_lang(lang: dict) -> dict:
    """`lang` (textstyle.language_fonts) with the Japanese font names of LocalizeManager.InitJapaneseFontLookUpTable:
    the Japanese font names, then its additional font names (the table TryGetFontIndex searches)."""
    ja = lang["languages"][textstyle.JAPANESE_MODE]
    table = {**ja, "fontNames": ja["fontNames"] + ja["additionalFontNames"]}
    return {**lang, "languages": {**lang["languages"], textstyle.JAPANESE_MODE: table}}


def _number(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chat_runtime_texts(episode: dict) -> list[str]:
    """Texts the chat window formats at run time: the battery percentage of each ChatWindow row ("{0}%" of (int)
    Parameter1, when numeric), the member count a group chat name gets ("(n)" for Parameter4 2..99,
    AdvChatWindowNameHelper.Format), the ellipsis of a truncated name (TruncateName) and the read count a group read
    label appends (" n" of the ChatTalk / ChatStamp / ChatRead Parameter1, AdvChatView.BuildReadText)."""
    out = ["..."] if any(c["cmd"] in CHAT_COMMANDS for c in episode["commands"]) else []
    for c in episode["commands"]:
        if c["cmd"] not in CHAT_COMMANDS or c.get("IgnoreData"):
            continue
        p1 = _number(c.get("Parameter1"))
        if c["cmd"] == "ChatWindow":
            members = _number(c.get("Parameter4"))
            if p1 is not None and abs(p1) < 2**31:
                out.append(f"{int(p1)}%")
            if members is not None and members == int(members) and 2 <= members <= 99:
                out.append(f"({int(members)})")
        elif c["cmd"] in ("ChatTalk", "ChatStamp", "ChatRead") and p1 is not None and p1 == int(p1) and p1 >= 1:
            out.append(f" {int(p1)}")
    return out
