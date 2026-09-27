"""Tests for nnnotes.crilips using only synthetic ELF fixtures and a synthetic version table.

No game data is used: we build a tiny ELF64 whose .rodata holds random blocks, register a synthetic
Version table keyed by their sha256, and check that extraction locates, orders and verifies them.
"""
from __future__ import annotations

import hashlib
import struct

import pytest

from nnnotes import crilips
from nnnotes.crilips import Block, Const, CriLipsError, Version


def _sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _elf_with_rodata(rodata: bytes, extra_strings: bytes = b"") -> bytes:
    """A minimal ELF64 LE AArch64 object with a single .rodata section (plus .shstrtab).

    Layout: [ehdr(64)] [rodata] [extra_strings] [shstrtab] [shdrs]. Enough for _read_rodata.
    """
    shstrtab = b"\x00.rodata\x00.shstrtab\x00"
    name_rodata = 1
    name_shstr = 9
    body = rodata + extra_strings
    off_body = 64
    off_shstr = off_body + len(body)
    off_shdrs = off_shstr + len(shstrtab)
    n_sh = 3  # null, .rodata, .shstrtab

    ehdr = bytearray(64)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 2       # ELFCLASS64
    ehdr[5] = 1       # ELFDATA2LSB
    ehdr[6] = 1       # EV_CURRENT
    struct.pack_into("<H", ehdr, 0x10, 3)       # ET_DYN
    struct.pack_into("<H", ehdr, 0x12, 183)     # EM_AARCH64
    struct.pack_into("<Q", ehdr, 0x28, off_shdrs)   # e_shoff
    struct.pack_into("<H", ehdr, 0x3a, 64)          # e_shentsize
    struct.pack_into("<H", ehdr, 0x3c, n_sh)        # e_shnum
    struct.pack_into("<H", ehdr, 0x3e, 2)           # e_shstrndx

    def shdr(name, sh_type, off, size):
        h = bytearray(64)
        struct.pack_into("<I", h, 0, name)
        struct.pack_into("<I", h, 4, sh_type)
        struct.pack_into("<Q", h, 24, off)
        struct.pack_into("<Q", h, 32, size)
        return bytes(h)

    shdrs = shdr(0, 0, 0, 0)
    shdrs += shdr(name_rodata, 1, off_body, len(rodata))     # SHT_PROGBITS
    shdrs += shdr(name_shstr, 3, off_shstr, len(shstrtab))   # SHT_STRTAB
    return bytes(ehdr) + body + shstrtab + shdrs


def _synthetic_version():
    """Build a synthetic version with two weight blocks and one constant, plus matching rodata."""
    import random
    rng = random.Random(1234)
    w1 = struct.pack("<%df" % 6, *[rng.uniform(-1, 1) for _ in range(6)])
    b1 = struct.pack("<%df" % 3, *[rng.uniform(-1, 1) for _ in range(3)])
    c1 = struct.pack("<i", 7)
    # rodata: some padding, the id string, then the blocks in a shuffled order
    idstr = b"SYNTH Lips Ver.9.9.9\x00"
    rodata = b"\x00" * 16 + b1 + b"\x11\x22\x33\x44" + w1 + b"\x00" * 8 + c1 + idstr
    ver = Version(
        version="9.9.9", core_version="9.9.9", id_string=b"SYNTH Lips Ver.9.9.9",
        blocks=(
            Block("d1.w", 6, (2, 3), "dense_w", _sha16(w1)),
            Block("d1.b", 3, (3,), "bias", _sha16(b1)),
        ),
        consts=(Const("flag", "i32", 1, _sha16(c1)),),
        frontend={"mel_bands": 24},
    )
    return ver, rodata, idstr


def test_extract_synthetic_locates_orders_and_verifies():
    ver, rodata, idstr = _synthetic_version()
    so = _elf_with_rodata(rodata)
    blob, desc = crilips.extract(so, tables=(ver,))
    assert desc["version"] == "9.9.9"
    assert desc["total_floats"] == 9
    assert [b["name"] for b in desc["blocks"]] == ["d1.w", "d1.b"]  # emitted in table order
    # d1.w is emitted first even though d1.b appears earlier in rodata
    assert desc["blocks"][0]["offset"] == 0
    assert desc["blocks"][1]["offset"] == 6          # in floats
    assert desc["constants"]["flag"] == 7
    assert len(blob) == 9 * 4


def test_unknown_version_is_an_error():
    so = _elf_with_rodata(b"\x00" * 64 + b"nothing here")
    with pytest.raises(CriLipsError):
        crilips.extract(so, tables=crilips.SUPPORTED)


def test_modified_block_is_an_error():
    ver, rodata, idstr = _synthetic_version()
    # corrupt a byte inside the d1.w block so no window matches its sha256
    corrupt = bytearray(rodata)
    corrupt[40] ^= 0xFF
    so = _elf_with_rodata(bytes(corrupt))
    # keep only d1.w so the failure is unambiguous
    ver2 = Version(ver.version, ver.core_version, ver.id_string,
                   (ver.blocks[0],), (), ver.frontend)
    with pytest.raises(CriLipsError):
        crilips.extract(so, tables=(ver2,))


def test_not_an_elf():
    with pytest.raises(CriLipsError):
        crilips.extract(b"PK\x03\x04 not an elf", tables=crilips.SUPPORTED)


def test_read_library_missing_entry(tmp_path):
    import zipfile
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("classes.dex", b"x")
    with pytest.raises(CriLipsError):
        crilips.read_library(apk)


def test_read_library_from_the_split_next_to_the_apk(tmp_path):
    import zipfile
    apk = tmp_path / "base.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("classes.dex", b"x")
    with zipfile.ZipFile(tmp_path / "split_config.arm64_v8a.apk", "w") as z:
        z.writestr(crilips.LIB_ENTRY_SUFFIX, b"ELF synthetic")
    assert crilips.read_library(apk) == b"ELF synthetic"


def test_descriptor_shapes_and_kinds():
    ver, rodata, _ = _synthetic_version()
    so = _elf_with_rodata(rodata)
    _, desc = crilips.extract(so, tables=(ver,))
    d1w = next(b for b in desc["blocks"] if b["name"] == "d1.w")
    assert d1w["shape"] == [2, 3]
    assert d1w["kind"] == "dense_w"
    assert desc["dtype"] == "float32" and desc["order"] == "LE"
    assert desc["frontend"]["mel_bands"] == 24


def test_supported_versions_carry_the_player_configuration():
    # the player builds its analyzer from these keys only
    need = {"frame_ms", "hop_ms", "mel_bands", "mel_high_hz", "resample_hz", "biquad_lpf_cutoff_hz",
            "update_rate_hz", "silence_threshold_db", "window_internal_class", "window_vowel",
            "motion_class_buffer", "labial_hold_sec", "discretizer", "mouth_open", "server_hz"}
    for ver in crilips.SUPPORTED:
        assert need <= set(ver.frontend), ver.version
        d = ver.frontend["discretizer"]
        assert {"suppression", "smoothing_sec", "hold_sec", "release_sec", "max_window_sec", "wildcard"} <= set(d)
        assert len(d["wildcard"]) == 6 and len(ver.frontend["mouth_open"]["vowel_coef"]) == 5


# --- story export: which episodes carry the CRI Lips data ------------------------------------------------------------

def _prefab(*classes):
    return {"key": "m", "nodes": [{"path": "m", "components": [{"class": c} for c in classes]},
                                  {"path": "m/child", "components": [{"class": "CubismMotionSyncController"}]}]}


def test_has_motion_sync_needs_both_components_on_the_root():
    from nnnotes import webmodel
    assert webmodel.has_motion_sync(_prefab("CubismModel", "CubismMotionSyncController",
                                            "Live2DMotionSyncCriAudioInput"))
    assert not webmodel.has_motion_sync(_prefab("CubismModel", "CubismMotionSyncController"))
    assert not webmodel.has_motion_sync(_prefab("CubismModel"))       # a child's controller does not count


def _talk(names, voices, **kw):
    return {"cmd": "Talk", "TargetName": names, "VoiceIDs": voices, **kw}


def test_needs_cri_lips():
    from nnnotes import story
    ep = lambda *rows: {"commands": list(rows)}
    one = _talk("a", [1])
    # a voiced row reaches the analysis on a model without MotionSync
    assert story.needs_cri_lips(ep(one), "・", [True, False])
    assert not story.needs_cri_lips(ep(one), "・", [True, True])
    # rows without lip sync never do
    for row in (_talk("a", [1], IgnoreLipSync=1), _talk("a", [1], IgnoreData=1), _talk("", [1]), _talk("a", []),
                {"cmd": "Bgm", "VoiceIDs": [1], "TargetName": "a"}):
        assert not story.needs_cri_lips(ep(row), "・", [False])
    # a Voice row on a model without MotionSync
    assert story.needs_cri_lips(ep({"cmd": "Voice", "TargetName": "a", "VoiceIDs": [1]}), "・", [False])
    # a voice shared by more speakers, or every showing character, uses the analysis on any model
    assert story.needs_cri_lips(ep(_talk("a・b", [1])), "・", [True])
    assert not story.needs_cri_lips(ep(_talk("a・b", [1, 2])), "・", [True])
    assert story.needs_cri_lips(ep(_talk("a", [1], Parameter1=" EveryoneLipSync ")), "・", [True])
    assert not story.needs_cri_lips(ep(_talk("a・b", [1], Parameter1="AirLipSync")), "・", [True])
