"""CRI Lips neural-net weight extraction from the game's ``libcri_lips_unity.so``.

The story player drives the mouth of Live2D models that have no MotionSync controller by
reimplementing CRI's ``CriLipsAtomAnalyzer`` in JavaScript. The network weights are not shipped
as code by CRI; they live in the read-only data of ``libcri_lips_unity.so``. This module reads
that library from the user's own APK and writes a documented data file (``crilips.json`` +
``crilips.bin``) that the player loads at runtime.

Nothing here is hard-coded to an address or offset: each supported library version has a table of
(block name, float count, sha256), and the blocks are located by scanning the ELF read-only data
for a window whose sha256 matches. Every block and constant is verified. An unrecognised library
version is a hard error -- the layout is never guessed. Each version also records the analysis
configuration that library creates its analyzer with (``frontend`` in the descriptor: frame and
hop, bands, rates, windows, discretizer and mouth-open defaults). Block offsets are in floats.

Fixtures for the tests are synthetic ELF files with a synthetic version table (see
``tests/test_crilips.py`` and ``docs/crilips.md``); no game data is required to test this module.
"""
from __future__ import annotations

import hashlib
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .apkset import ApkSet
from .jsonio import write_json

LIB_ENTRY_SUFFIX = "lib/arm64-v8a/libcri_lips_unity.so"
# the arm64-v8a split of an installed app (adb pull of its directory), or of an .xapk package
NATIVE_SPLITS = ("split_config.arm64_v8a.apk", "config.arm64_v8a.apk")


class CriLipsError(Exception):
    """The library is missing, unreadable, an unknown version, or fails verification."""


@dataclass(frozen=True)
class Block:
    """One contiguous float32 array in the library's read-only data."""

    name: str
    count: int
    shape: tuple[int, ...]
    kind: str          # conv_w | dense_w | bias | bn
    sha256: str        # sha256 of the count*4 little-endian bytes (integrity only)


@dataclass(frozen=True)
class Const:
    """A small constant block (scalar, vector, or int table) in read-only data."""

    name: str
    dtype: str         # f32 | i32
    count: int
    sha256: str


@dataclass(frozen=True)
class Version:
    """Everything needed to extract and describe one library version."""

    version: str                 # CRI Lips version string, e.g. "1.05.00"
    core_version: str
    id_string: bytes             # a version string that must be present in the ELF
    blocks: tuple[Block, ...]    # emitted in this order into crilips.bin
    consts: tuple[Const, ...]
    frontend: dict = field(default_factory=dict)


# --- Supported versions ------------------------------------------------------------------------
# The sha256 values are one-way integrity checksums of the arrays, not the arrays themselves.
# They let the scanner locate each block without any hard-coded address and verify it exactly.

_V10500 = Version(
    version="1.05.00",
    core_version="1.00.17",
    id_string=b"CRI Lips/Android_ARMv8A Ver.1.05.00",
    blocks=(
        # Net1: internal-class classifier (3x3 conv -> 4 dense -> softmax 24)
        Block("net1.conv.w", 90,     (10, 3, 3),  "conv_w",  "54afa68f6e20c5f7"),
        Block("net1.conv.b", 10,     (10,),       "bias",    "55204e31f7cdc74e"),
        Block("net1.d1.w",   28160,  (220, 128),  "dense_w", "baa5c59e2e19eff8"),
        Block("net1.d1.b",   128,    (128,),      "bias",    "dff64c73cb632dc0"),
        Block("net1.d2.w",   16384,  (128, 128),  "dense_w", "acbcbea9f2862ef4"),
        Block("net1.d2.b",   128,    (128,),      "bias",    "0452488773d7430f"),
        Block("net1.d3.w",   16384,  (128, 128),  "dense_w", "f8f4d715a0c8fb64"),
        Block("net1.d3.b",   128,    (128,),      "bias",    "48d45ebc017dcf43"),
        Block("net1.d4.w",   3072,   (128, 24),   "dense_w", "d37aa7df8d16bc09"),
        Block("net1.d4.b",   24,     (24,),       "bias",    "2575d6f5b8935dca"),
        # Net2: raw-lip parameters (avg3 -> 3 dense relu -> dense sigmoid)
        Block("net2.d1.w",   768,    (24, 32),    "dense_w", "0e0ad798e5abf2f7"),
        Block("net2.d1.b",   32,     (32,),       "bias",    "91b5660a8c8ca7ce"),
        Block("net2.d2.w",   1024,   (32, 32),    "dense_w", "8dc46dc8d0b11298"),
        Block("net2.d2.b",   32,     (32,),       "bias",    "3d9039bcc7a95ec8"),
        Block("net2.d3.w",   1024,   (32, 32),    "dense_w", "3a9df3da133f42f9"),
        Block("net2.d3.b",   32,     (32,),       "bias",    "b44b9d4e0641ab2c"),
        Block("net2.d4.w",   64,     (32, 2),     "dense_w", "b53be2dce4472ca2"),
        Block("net2.d4.b",   2,      (2,),        "bias",    "04cc81a98b06d422"),
        # Net3: Japanese AIUEO targets (dense+BN+relu x2 -> dense softmax 5)
        Block("net3.d1.w",   2880,   (72, 40),    "dense_w", "ae669da348feb392"),
        Block("net3.d1.b",   40,     (40,),       "bias",    "b393978842a0fa3d"),
        Block("net3.bn1.beta",  40,  (40,),       "bn",      "9e96f4a355341c58"),
        Block("net3.bn1.gamma", 40,  (40,),       "bn",      "641153fc4ee3b8a4"),
        Block("net3.bn1.mean",  40,  (40,),       "bn",      "fba8a4e816b569af"),
        Block("net3.bn1.var",   40,  (40,),       "bn",      "42b2019f96dc1f94"),
        Block("net3.d2.w",   800,    (40, 20),    "dense_w", "94421bacb040d80a"),
        Block("net3.d2.b",   20,     (20,),       "bias",    "5b6fb58e61fa4759"),
        Block("net3.bn2.beta",  20,  (20,),       "bn",      "860bb1709e28e460"),
        Block("net3.bn2.gamma", 20,  (20,),       "bn",      "92caa15aedc1f6f6"),
        Block("net3.bn2.mean",  20,  (20,),       "bn",      "4b7b37807378c692"),
        Block("net3.bn2.var",   20,  (20,),       "bn",      "bb0eaa39b6fd2e1a"),
        Block("net3.d3.w",   100,    (20, 5),     "dense_w", "10ec3cda53d3c85a"),
        Block("net3.d3.b",   5,      (5,),        "bias",    "de47c9b27eb8d300"),
    ),
    consts=(
        Const("silence_coef",           "f32", 1,  "e401200e58084389"),
        Const("antiflap_defaults",      "f32", 4,  "c97bf6430bb8708f"),   # w_large, w_small, thr_up, thr_lo
        Const("motion_class_table",     "i32", 24, "7c5d3820cf97d3dd"),
        Const("discretizer_type_table", "i32", 25, "93c75960789019e9"),
    ),
    frontend={
        "frame_ms": 30.0, "hop_ms": 10.0, "mel_bands": 24, "mel_high_hz": 8000.0,
        "resample_hz": 16000, "biquad_lpf_cutoff_hz": 16000.0, "process_len_ms": 10.0,
        "update_rate_hz": 100, "silence_threshold_db": -40.0,
        # analysis configuration of this library version (defaults the analyzer is created with)
        "window_internal_class": "hamming", "window_vowel": "blackman-harris",
        "motion_class_buffer": 3, "labial_hold_sec": 0.0,
        "discretizer": {"suppression": 0.5, "smoothing_sec": 0.1, "hold_sec": 0.0,
                        "release_sec": 0.1, "max_window_sec": 1.0,
                        "wildcard": [0.2, 0.2, 0.0, 0.0, 0.0, 0.0]},
        "mouth_open": {"vowel_coef": [1.0, 1.0, 1.0, 1.0, 1.0], "anti_shake_sec": 0.03},
        "server_hz": 60.0,
    },
)

# Note: every sha256 above is truncated to 16 hex chars (a one-way integrity checksum, never the
# array bytes). The scanner compares the same 16-char prefix and re-verifies each located block.
SUPPORTED: tuple[Version, ...] = (_V10500,)


# --- Minimal ELF reader ------------------------------------------------------------------------

def _read_rodata(so: bytes) -> bytes:
    """Return the bytes of the ``.rodata`` section of an ELF64 little-endian AArch64 object."""
    if so[:4] != b"\x7fELF" or so[4] != 2 or so[5] != 1:
        raise CriLipsError("not an ELF64 little-endian object")
    e_shoff = struct.unpack_from("<Q", so, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", so, 0x3a)
    shstr_off = struct.unpack_from("<Q", so, e_shoff + e_shstrndx * e_shentsize + 24)[0]

    def name_at(n: int) -> str:
        end = so.index(b"\x00", shstr_off + n)
        return so[shstr_off + n: end].decode()

    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        n_name = struct.unpack_from("<I", so, base)[0]
        off, size = struct.unpack_from("<QQ", so, base + 24)
        if name_at(n_name) == ".rodata":
            return so[off: off + size]
    raise CriLipsError(".rodata section not found")


def _sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _locate_block(rodata: bytes, blk: Block) -> int:
    """Offset of ``blk`` inside ``rodata`` by scanning 4-aligned windows for its sha256."""
    n = blk.count * 4
    for off in range(0, len(rodata) - n + 1, 4):
        if _sha16(rodata[off: off + n]) == blk.sha256:
            return off
    raise CriLipsError(f"weight block {blk.name} not found (library modified or wrong version)")


def _scan_const(rodata: bytes, c: Const) -> bytes:
    """Locate a constant by its recorded sha256, scanning 4-aligned windows. No game bytes are
    stored in this module -- only the one-way checksum -- so the located bytes are re-verified."""
    n = c.count * 4
    for off in range(0, len(rodata) - n + 1, 4):
        window = rodata[off: off + n]
        if _sha16(window) == c.sha256:
            return window
    raise CriLipsError(f"constant {c.name} not found (library modified or wrong version)")


# --- Public API --------------------------------------------------------------------------------

def _library_in(apk: Path) -> bytes | None:
    try:
        with ApkSet(apk) as z:
            for info in z.infolist():
                if info.filename.endswith(LIB_ENTRY_SUFFIX):
                    return z.read(info.filename)
    except (OSError, zipfile.BadZipFile) as e:
        raise CriLipsError(f"{apk} is not a readable APK: {e}") from None
    return None


def read_library(apk: Path) -> bytes:
    """Read ``libcri_lips_unity.so`` from an APK (or a raw .so path). An installed app keeps its native libraries in
    the arm64-v8a split next to ``base.apk`` (NATIVE_SPLITS); that split is read when the APK itself has none."""
    apk = Path(apk)
    if apk.suffix.lower() == ".so":
        return apk.read_bytes()
    so = _library_in(apk)
    for name in NATIVE_SPLITS:
        if so is not None:
            break
        if (apk.parent / name).is_file():
            so = _library_in(apk.parent / name)
    if so is None:
        raise CriLipsError(f"{LIB_ENTRY_SUFFIX} not found in {apk.name} or in an arm64-v8a split next to it "
                           f"({', '.join(NATIVE_SPLITS)})")
    return so


def extract(so: bytes, tables: tuple[Version, ...] = SUPPORTED) -> tuple[bytes, dict]:
    """Return ``(crilips.bin bytes, descriptor dict)`` for the library bytes ``so``.

    Blocks are located and verified by sha256; constants are located and their bytes captured.
    Raises :class:`CriLipsError` on an unknown version or any verification failure.
    """
    ver = None
    for v in tables:
        if v.id_string in so:
            ver = v
            break
    if ver is None:
        raise CriLipsError("unknown libcri_lips_unity.so version")
    rodata = _read_rodata(so)

    blob = bytearray()
    desc = {"version": ver.version, "core_version": ver.core_version, "dtype": "float32",
            "order": "LE", "blocks": [], "constants": {}, "frontend": dict(ver.frontend)}
    for blk in ver.blocks:
        off = _locate_block(rodata, blk)
        raw = rodata[off: off + blk.count * 4]
        desc["blocks"].append({"name": blk.name, "offset": len(blob) // 4, "count": blk.count,
                               "shape": list(blk.shape), "kind": blk.kind, "sha256": _sha16(raw)})
        blob += raw
    # Constants: located and verified by their one-way checksum only.
    for c in ver.consts:
        raw = _scan_const(rodata, c)
        if c.dtype == "f32":
            vals = list(struct.unpack_from("<%df" % c.count, raw, 0))
        else:
            vals = list(struct.unpack_from("<%di" % c.count, raw, 0))
        desc["constants"][c.name] = vals[0] if c.count == 1 else vals
    desc["total_floats"] = sum(b["count"] for b in desc["blocks"])
    desc["blob_sha256"] = _sha16(bytes(blob))
    return bytes(blob), desc


def write_data(apk: Path, out_dir: Path) -> dict:
    """Extract from an APK and write ``crilips.json`` and ``crilips.bin`` into ``out_dir``."""
    so = read_library(apk)
    blob, desc = extract(so)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "crilips.bin").write_bytes(blob)
    write_json(out_dir / "crilips.json", desc)
    return desc
