"""advvideo: USM masks, demux and the video index (synthetic data, made-up key)."""
import json
import random
import struct

import pytest

from nnnotes import advvideo

KEY = 0x0123456789ABCDEF          # made up


def ref_unmask_video(payload, m1, m2):
    """The chained video mask, byte by byte."""
    d = bytearray(payload)
    size = len(d) - 0x40
    if size >= 0x200:
        mask = bytearray(m2)
        for i in range(0x100, size):
            d[0x40 + i] ^= mask[i & 31]
            mask[i & 31] = d[0x40 + i] ^ m2[i & 31]
        mask = bytearray(m1)
        for i in range(0x100):
            mask[i & 31] ^= d[0x140 + i]
            d[0x40 + i] ^= mask[i & 31]
    return bytes(d)


def mask_video(plain, m1, m2):
    """Inverse of the video unmask (what the encoder does)."""
    d = bytearray(plain)
    size = len(d) - 0x40
    if size >= 0x200:
        chain = bytearray(m1)
        for i in range(0x100):
            chain[i & 31] ^= plain[0x140 + i]
            d[0x40 + i] = plain[0x40 + i] ^ chain[i & 31]
        for i in range(0x100, size):
            d[0x40 + i] = plain[0x40 + i] ^ m2[i & 31] ^ (plain[0x40 + i - 32] if i >= 0x120 else 0)
    return bytes(d)


def mask_audio(plain, am):
    return bytes(b ^ am[i & 31] if i >= 0x140 else b for i, b in enumerate(plain))


def chunk(sig, payload, dtype=0, channel=0, padding=0):
    header = bytes([0, 0x18]) + struct.pack(">H", padding) + bytes([channel, 0, 0, dtype]) + bytes(16)
    body = header + payload + bytes(padding)
    return sig + struct.pack(">I", len(body)) + body


def rand(n, seed):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def adx(samples=4800, rate=48000, channels=2):
    head = b"\x80\x00" + struct.pack(">H", 0x1C) + bytes([3, 18, 4, channels]) + struct.pack(">II", rate, samples)
    head += bytes(0x20 - len(head) - 6) + b"(c)CRI"
    return head


def test_masks():
    m1, m2, am = advvideo.masks(KEY)
    assert len(m1) == len(m2) == len(am) == 32
    assert bytes(x ^ 0xFF for x in m1) == m2
    assert am[1::2] == b"URUCURUCURUCURUC"
    assert am[0::2] == m2[0::2]
    assert advvideo.masks(KEY) == (m1, m2, am) and advvideo.masks(KEY + 1) != (m1, m2, am)


@pytest.mark.parametrize("size", [0, 0x40, 0x23F, 0x240, 0x241, 0x260, 0x1000, 0x1017])
def test_unmask_video_matches_the_byte_loop(size):
    m1, m2, _ = advvideo.masks(KEY)
    data = rand(size, size)
    assert advvideo.unmask_video(data, m1, m2) == ref_unmask_video(data, m1, m2)
    if size >= 0x240:
        assert advvideo.unmask_video(data, m1, m2) != data
        assert advvideo.unmask_video(mask_video(data, m1, m2), m1, m2) == data
    else:
        assert advvideo.unmask_video(data, m1, m2) == data


@pytest.mark.parametrize("size", [0x100, 0x140, 0x141, 0x800])
def test_unmask_audio(size):
    _, _, am = advvideo.masks(KEY)
    data = rand(size, size + 1)
    assert advvideo.unmask_audio(mask_audio(data, am), am) == data


def usm(video_frames, audio_parts, extra=()):
    m1, m2, am = advvideo.masks(KEY)
    out = chunk(b"CRID", b"@UTF" + bytes(12), dtype=1)
    out += chunk(b"@SFV", b"@UTF" + bytes(28), dtype=1)
    for i, f in enumerate(video_frames):
        out += chunk(b"@SFV", mask_video(f, m1, m2), padding=i % 3)
        if i < len(audio_parts):
            out += chunk(b"@SFA", mask_audio(audio_parts[i], am))
    for c in extra:
        out += c
    out += chunk(b"@SFV", b"#CONTENTS END   ===============\x00", dtype=2)
    return out


def test_demux_round_trip():
    frames = [b"DKIF" + rand(0x3FC, 1), rand(0x500, 2), rand(0x30, 3)]
    audio = [adx(), rand(0x400, 4)]
    streams = advvideo.demux(usm(frames, audio), KEY)
    assert streams == {"video": b"".join(frames), "audio": b"".join(audio)}
    assert advvideo.adx_info(streams["audio"]) == {"codec": "adx", "channels": 2, "sampleRate": 48000,
                                                   "samples": 4800}
    assert "audio" not in advvideo.demux(usm(frames, []), KEY)


def test_demux_rejects_other_streams_and_files():
    with pytest.raises(NotImplementedError):
        advvideo.demux(usm([rand(0x300, 5)], [], extra=[chunk(b"@ALP", rand(0x300, 6))]), KEY)
    with pytest.raises(NotImplementedError):
        advvideo.demux(usm([rand(0x300, 5)], [], extra=[chunk(b"@SFA", rand(0x300, 6), channel=1)]), KEY)
    with pytest.raises(ValueError):
        advvideo.demux(b"RIFF" + bytes(60), KEY)
    with pytest.raises(ValueError):
        list(advvideo.chunks(usm([rand(0x300, 7)], [])[:-5]))
    with pytest.raises(NotImplementedError):
        advvideo.adx_info(b"HCA\x00" + bytes(60))


def test_video_index(tmp_path, monkeypatch):
    def fake_export(cat, key, dst, cri_key, work):
        assert cri_key == KEY
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"webm")
        return {"key": key, "codec": "vp9", "frames": 10}
    monkeypatch.setattr(advvideo, "export", fake_export)
    episode = {
        "resources": [{"kind": "video", "address": "Cri/Video/adv/m_b/m_b", "present": True},
                      {"kind": "video", "address": "Cri/Video/adv/m_a/m_a", "present": True},
                      {"kind": "stage", "address": "Adv/Stage/s/s", "present": True}],
        "commands": [{"cmd": "Movie", "VideoID": 2}, {"cmd": "Clip", "VideoID": 1}, {"cmd": "Clip"},
                     {"cmd": "Clip", "VideoID": 2}, {"cmd": "Movie", "VideoID": 9}, {"cmd": "Stage", "VideoID": 1}],
        "videos": {1: {"_id": 1, "_assetName": "adv/m_a/m_a", "_hasAudio": True},
                   2: {"_id": 2, "_assetName": "adv/m_b/m_b", "_hasAudio": False}},
    }
    index = advvideo.extract(None, episode, tmp_path, cri_key=KEY)
    assert list(index["videos"]) == ["2", "1"]
    assert index["videos"]["1"]["file"] == "videos/m_a.webm" and index["videos"]["2"]["master"]["_hasAudio"] is False
    assert (tmp_path / "videos" / "m_a.webm").read_bytes() == b"webm"
    assert json.loads((tmp_path / "videos" / "videos.json").read_text(encoding="utf-8")) == index
    assert advvideo.extract(None, {"resources": [], "commands": [], "videos": {}}, tmp_path / "none") is None


def test_key_zero_means_plain_streams():
    frames = [b"DKIF" + rand(0x3FC, 8), rand(0x500, 9)]
    data = chunk(b"CRID", b"@UTF" + bytes(12), dtype=1) + b"".join(chunk(b"@SFV", f) for f in frames)
    data += chunk(b"@SFA", adx() + rand(0x300, 10))
    assert advvideo.demux(data, 0) == {"video": b"".join(frames), "audio": adx() + rand(0x300, 10)}
