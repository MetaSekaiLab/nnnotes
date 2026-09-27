"""advvideo: USM masks, demux of every stream kind, subtitles, the movie files' ffmpeg arguments and the video index
(synthetic data, made-up key)."""
import json
import random
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from nnnotes import advvideo
from nnnotes.advvideo import Stream

KEY = 0x0123456789ABCDEF          # made up
M1, M2, AM = advvideo.masks(KEY)


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


def mask_video(plain, m1=M1, m2=M2):
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


def mask_audio(plain, am=AM):
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


def adx_body(frames, seed):
    """ADX frames: a scale word with its top bit clear, then 16 bytes of samples."""
    r = random.Random(seed)
    return b"".join(bytes([r.getrandbits(7)]) + bytes(r.getrandbits(8) for _ in range(17)) for _ in range(frames))


def hca(channels=2, rate=48000, blocks=4, block=0x40, cipher=56, encrypted=True):
    """An HCA header (fmt, comp, ciph chunks; the letters of the names with the high bit set when `encrypted`)."""
    def tag(t):
        return bytes(b | 0x80 if b else 0 for b in t) if encrypted else t
    body = tag(b"fmt\x00") + bytes([channels]) + rate.to_bytes(3, "big") + struct.pack(">IHH", blocks, 0, 0)
    body += tag(b"comp") + struct.pack(">H", block) + bytes(10)
    body += tag(b"ciph") + struct.pack(">H", cipher)
    size = 8 + len(body) + 2
    return tag(b"HCA\x00") + struct.pack(">HH", 0x0200, size) + body + b"\0\0"


def utf(name, row):
    """A one-row CRI @UTF table: ints as u32 columns, strings as string columns."""
    strings = bytearray(b"<NULL>\0" + name.encode() + b"\0")
    cols, data = b"", b""

    def text(s):
        at = len(strings)
        strings.extend(s.encode() + b"\0")
        return at
    for k, v in row.items():
        at = text(k)
        if isinstance(v, str):
            cols += bytes([0x5A]) + struct.pack(">I", at)
            data += struct.pack(">I", text(v))
        else:
            cols += bytes([0x54]) + struct.pack(">I", at)
            data += struct.pack(">I", v)
    rows_at = 24 + len(cols)
    t = struct.pack(">HHIIIHHI", 1, rows_at, rows_at + len(data), rows_at + len(data) + len(strings), 7, len(row),
                    len(data), 1) + cols + data + bytes(strings)
    return b"@UTF" + struct.pack(">I", len(t)) + t


def video_header(codec=9, w=320, h=180, rate=(30000, 1000), frames=3, disp=None, alpha_type=0):
    dw, dh = disp or (w, h)
    return {"width": w, "height": h, "disp_width": dw, "disp_height": dh, "mpeg_codec": codec,
            "alpha_type": alpha_type, "total_frames": frames, "framerate_n": rate[0], "framerate_d": rate[1]}


def audio_header(codec=2, channels=2):
    return {"audio_codec": codec, "sampling_rate": 48000, "num_channels": channels}


def build(streams, extra=(), key=True):
    """A USM of `streams`: (signature, channel, header row or None, payloads, mask "video" / "audio" / None), header
    chunks first, the payloads interleaved, then the end markers; payloads masked as the encoder does."""
    out = chunk(b"CRID", utf("CRIUSF_DIR_STREAM", {"filename": "x.usm"}), dtype=1)
    for sig, ch, head, _parts, _mask in streams:
        if head is not None:
            name = "AUDIO_HDRINFO" if sig == b"@SFA" else "VIDEO_HDRINFO"
            out += chunk(sig, utf(name, head), dtype=1, channel=ch)
    for i in range(max(len(p) for _s, _c, _h, p, _m in streams)):
        for sig, ch, _head, parts, mask in streams:
            if i < len(parts):
                p = parts[i]
                if key and mask == "video":
                    p = mask_video(p)
                elif key and mask == "audio":
                    p = mask_audio(p)
                out += chunk(sig, p, channel=ch, padding=i % 3)
    for c in extra:
        out += c
    for sig, ch, _h, _p, _m in streams:
        out += chunk(sig, b"#CONTENTS END   ===============\x00", dtype=2, channel=ch)
    return out


def ivf(frames=3, w=320, h=180):
    return b"DKIF" + struct.pack("<HH4sHHIII", 0, 32, b"VP90", w, h, 30, 1, frames) + bytes(4)


VP9 = [ivf() + rand(0x3E0, 1), rand(0x500, 2), rand(0x30, 3)]
MPEG1 = [b"\0\0\x01\xb3" + rand(0x3FC, 4), rand(0x280, 5)]
H264 = [b"\0\0\0\x01\x67" + rand(0x3FB, 6), rand(0x260, 7)]
ADX = [adx(samples=640) + adx_body(40, 8), bytes([0x80, 1]) + struct.pack(">H", 14) + bytes(14)]   # + end block
HCA = [hca() + rand(0x200, 10), rand(0x300, 11)]


def sbt(language, unit, start, duration, text, nul=1):
    t = text.encode("utf-8") if isinstance(text, str) else text
    return struct.pack("<5I", language, unit, start, duration, len(t) + nul) + t + bytes(nul)


# ---------------------------------------------------------------- masks
def test_masks():
    m1, m2, am = advvideo.masks(KEY)
    assert len(m1) == len(m2) == len(am) == 32
    assert bytes(x ^ 0xFF for x in m1) == m2
    assert am[1::2] == b"URUCURUCURUCURUC"
    assert am[0::2] == m2[0::2]
    assert advvideo.masks(KEY) == (m1, m2, am) and advvideo.masks(KEY + 1) != (m1, m2, am)


@pytest.mark.parametrize("size", [0, 0x40, 0x23F, 0x240, 0x241, 0x260, 0x1000, 0x1017])
def test_unmask_video_matches_the_byte_loop(size):
    data = rand(size, size)
    assert advvideo.unmask_video(data, M1, M2) == ref_unmask_video(data, M1, M2)
    if size >= 0x240:
        assert advvideo.unmask_video(data, M1, M2) != data
        assert advvideo.unmask_video(mask_video(data), M1, M2) == data
    else:
        assert advvideo.unmask_video(data, M1, M2) == data


@pytest.mark.parametrize("size", [0x100, 0x140, 0x141, 0x800])
def test_unmask_audio(size):
    data = rand(size, size + 1)
    assert advvideo.unmask_audio(mask_audio(data), AM) == data


# ---------------------------------------------------------------- demux
def test_demux_video_and_adx_without_headers():
    """VP9 in IVF and ADX recognised from their bytes; both unmasked."""
    streams = advvideo.demux(build([(b"@SFV", 0, None, VP9, "video"), (b"@SFA", 0, None, ADX, "audio")]), KEY)
    assert [(s.name, s.codec, s.header) for s in streams] == [("video", "vp9", {}), ("audio", "adx", {})]
    assert streams[0].data == b"".join(VP9) and streams[1].data == b"".join(ADX)
    assert advvideo.adx_info(streams[1].data) == {"codec": "adx", "channels": 2, "sampleRate": 48000,
                                                  "samples": 640}
    assert advvideo.video_facts(streams[0]) == {"codec": "vp9", "width": 320, "height": 180, "frameRate": [30, 1],
                                                "frames": 3}


def test_demux_every_kind_and_channel():
    """Video (MPEG-1 by its header), alpha (masked like video), two ADX channels, an HCA channel stored plain and
    subtitles stored plain, in kind and channel order; headers read."""
    subs = sbt(0, 1000, 0, 1500, "one") + sbt(0, 1000, 2000, 500, "two")
    data = build([(b"@SFA", 1, audio_header(2), ADX, "audio"),
                  (b"@SBT", 0, None, [subs[:40], subs[40:]], None),
                  (b"@ALP", 0, video_header(1, 320, 184, disp=(320, 180), alpha_type=1), MPEG1, "video"),
                  (b"@SFV", 0, video_header(1, disp=(320, 180)), MPEG1, "video"),
                  (b"@SFA", 0, audio_header(2), ADX, "audio"),
                  (b"@SFA", 2, audio_header(4), HCA, None)])
    streams = advvideo.demux(data, KEY)
    assert [(s.name, s.codec) for s in streams] == [("video", "mpeg1"), ("alpha", "mpeg1"), ("audio", "adx"),
                                                   ("audio_1", "adx"), ("audio_2", "hca"), ("subtitle", "sbt")]
    by = {s.name: s for s in streams}
    assert by["video"].data == by["alpha"].data == b"".join(MPEG1)
    assert by["audio_1"].data == b"".join(ADX) and by["audio_2"].data == b"".join(HCA)
    assert by["subtitle"].data == subs
    assert advvideo.video_facts(by["alpha"]) == {"codec": "mpeg1", "width": 320, "height": 184, "displayWidth": 320,
                                                 "displayHeight": 180, "frames": 3, "frameRate": [30000, 1000],
                                                 "alphaType": 1}
    assert advvideo.display_size(by["video"]) == (320, 180)
    assert advvideo.hca_info(by["audio_2"].data) == {"codec": "hca", "channels": 2, "sampleRate": 48000,
                                                     "blocks": 4, "blockSize": 0x40, "cipher": 56}


def test_demux_h264_and_recognition_without_headers():
    streams = advvideo.demux(build([(b"@SFV", 0, video_header(5), H264, "video")]), KEY)
    assert [(s.codec, s.data) for s in streams] == [("h264", b"".join(H264))]
    for parts, codec in ((H264, "h264"), (MPEG1, "mpeg1"), ([bytes(6) + MPEG1[0]] + MPEG1[1:], "mpeg1")):
        assert advvideo.demux(build([(b"@SFV", 0, None, parts, "video")]), KEY)[0].codec == codec
    assert advvideo.demux(build([(b"@SFA", 0, None, HCA, None)]), KEY)[0].codec == "hca"


@pytest.mark.parametrize("streams, code", [
    ([(b"@SFV", 0, None, VP9, "video"), (b"@CUE", 0, None, [rand(0x40, 12)], None)], "unsupported.usm.stream"),
    ([(b"@SFV", 0, video_header(10), VP9, "video")], "unsupported.usm.codec"),
    ([(b"@SFV", 0, video_header(9), MPEG1, "video")], "unsupported.usm.codec"),
    ([(b"@SFV", 0, None, [rand(0x300, 13)], "video")], "unsupported.usm.codec"),
    ([(b"@SFV", 0, None, VP9, "video"), (b"@SFA", 0, audio_header(3), ADX, "audio")], "unsupported.usm.codec"),
    ([(b"@SFV", 0, None, VP9, "video"), (b"@SFA", 0, None, [rand(0x300, 14)], None)], "unsupported.usm.codec"),
])
def test_demux_unsupported(streams, code):
    with pytest.raises(advvideo.Unsupported) as e:
        advvideo.demux(build(streams), KEY)
    assert e.value.code == code


def test_demux_rejects_other_files():
    with pytest.raises(ValueError):
        advvideo.demux(b"RIFF" + bytes(60), KEY)
    with pytest.raises(ValueError):
        list(advvideo.chunks(build([(b"@SFV", 0, None, VP9, "video")])[:-5]))
    with pytest.raises(NotImplementedError):
        advvideo.adx_info(b"HCA\x00" + bytes(60))


def slices(n, seed):
    """MPEG-1 picture data with slice start codes."""
    return b"".join(b"\0\0\x01" + bytes([1 + i]) + rand(0x50, seed + i) for i in range(n))


def superframe_payload(seed, first=False):
    """An IVF payload whose VP9 frame is a superframe (two frames and their index)."""
    a, b = rand(0x180, seed), rand(0x120, seed + 1)
    index = bytes([0xC9]) + struct.pack("<HH", len(a), len(b)) + bytes([0xC9])
    frame = a + b + index
    return (ivf() if first else b"") + struct.pack("<I", len(frame)) + bytes(8) + frame


@pytest.mark.parametrize("kind, parts, mask", [
    ("mpeg1", [b"\0\0\x01\xb3" + rand(0x3C, 20) + slices(12, 21), slices(9, 40)], "video"),
    ("vp9", [superframe_payload(50, True)] + [superframe_payload(60 + 2 * i) for i in range(4)], "video"),
    ("adx", ADX, "audio"),
])
def test_streams_stored_plain_beside_masked_ones(kind, parts, mask):
    """A stream kept as stored when only that form shows its structure past byte 0x40; unmasked when stored masked."""
    sig = b"@SFA" if kind == "adx" else b"@SFV"
    header = None if kind == "adx" else video_header({"mpeg1": 1, "vp9": 9}[kind])
    for stored in (mask, None):
        (s,) = advvideo.demux(build([(sig, 0, header, parts, stored)]), KEY)
        assert s.codec == kind and s.data == b"".join(parts)


def test_key_zero_means_plain_streams():
    data = build([(b"@SFV", 0, None, VP9, "video"), (b"@SFA", 0, None, ADX, "audio")], key=False)
    assert [s.data for s in advvideo.demux(data, 0)] == [b"".join(VP9), b"".join(ADX)]


def test_adx_frames_drop_the_end_of_stream_block():
    """The frames the header counts (ceil(samples / 32) per channel, 18 bytes each) are kept; the end-of-stream block
    after them is dropped; other trailing bytes or a short stream are errors."""
    head = adx(samples=100, channels=2)
    frames = rand(4 * 18 * 2, 11)                                     # ceil(100 / 32) = 4 frames per channel
    end = bytes([0x80, 0x01]) + struct.pack(">H", 14) + bytes(14)
    assert advvideo.adx_frames(head + frames + end) == head + frames
    assert advvideo.adx_frames(head + frames) == head + frames
    with pytest.raises(ValueError, match="not an end-of-stream block"):
        advvideo.adx_frames(head + frames + end[:-1] + bytes([1]))
    with pytest.raises(ValueError, match="not an end-of-stream block"):
        advvideo.adx_frames(head + frames + rand(18, 12))
    with pytest.raises(ValueError, match="header counts"):
        advvideo.adx_frames(head + frames[:-1])


# ---------------------------------------------------------------- subtitles
def test_subtitle_records_and_text_forms():
    data = (sbt(1, 1000, 2000, 1000, "second <b> & a\r\nline") + sbt(1, 30, 3, 45, "first", nul=2)
            + sbt(1, 1000, 5000, 0, "\n", nul=0))
    recs = advvideo.subtitle_records(data)
    assert [(r["language"], r["timeUnit"], r["start"], r["duration"], r["text"], r["terminator"]) for r in recs] == [
        (1, 1000, 2000, 1000, b"second <b> & a\r\nline", 1), (1, 30, 3, 45, b"first", 2), (1, 1000, 5000, 0, b"\n", 0)]
    assert advvideo.subtitle_srt(recs) == ("1\n00:00:00,100 --> 00:00:01,600\nfirst\n\n"
                                           "2\n00:00:02,000 --> 00:00:03,000\nsecond <b> & a\nline\n\n"
                                           "3\n00:00:05,000 --> 00:00:05,000\n \n \n\n")
    assert advvideo.subtitle_webvtt(recs) == ("WEBVTT\n\n00:00:00.100 --> 00:00:01.600\nfirst\n\n"
                                              "00:00:02.000 --> 00:00:03.000\nsecond &lt;b&gt; &amp; a\nline\n\n"
                                              "00:00:05.000 --> 00:00:05.000\n \n \n\n")
    doc = advvideo.subtitle_json(3, recs + advvideo.subtitle_records(sbt(2, 1000, 0, 1, b"\x82\xa0")))
    assert doc["channel"] == 3 and doc["records"][0] == {"language": 1, "timeUnit": 1000, "start": 2000,
                                                         "duration": 1000, "text": "second <b> & a\r\nline",
                                                         "terminator": 1}
    assert doc["records"][-1] == {"language": 2, "timeUnit": 1000, "start": 0, "duration": 1, "textHex": "82a0",
                                  "terminator": 1}
    with pytest.raises(UnicodeDecodeError):
        advvideo.subtitle_srt(advvideo.subtitle_records(sbt(0, 1000, 0, 1, b"\x82\xa0")))


@pytest.mark.parametrize("data, match", [
    (sbt(0, 1000, 0, 1, "x")[:12], "record header"),
    (struct.pack("<5I", 0, 0, 0, 1, 2) + b"x\0", "time unit 0"),
    (struct.pack("<5I", 0, 1000, 0, 1, 9) + b"x\0", "text of 9 bytes"),
])
def test_subtitle_records_reject_other_bytes(data, match):
    with pytest.raises(ValueError, match=match):
        advvideo.subtitle_records(data)


# ---------------------------------------------------------------- movie files
def stream(kind, codec, data=b"x", channel=0, header=None):
    return Stream(kind, channel, codec, data, header or {})


def test_stream_inputs(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        Path(args[args.index("-o") + 1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(advvideo.subprocess, "run", fake_run)
    w = tmp_path / "w"
    assert advvideo.stream_input(stream("video", "vp9", b"ivf"), w) == ["-f", "ivf", "-i", str(w / "video.ivf")]
    assert advvideo.stream_input(stream("alpha", "mpeg1", channel=1), w) == [
        "-fflags", "+genpts", "-f", "mpegvideo", "-i", str(w / "alpha_1.m1v")]
    assert advvideo.stream_input(stream("video", "h264", header=video_header(5, rate=(24000, 1001))), w) == [
        "-fflags", "+genpts", "-f", "h264", "-framerate", "24000/1001", "-i", str(w / "video.h264")]
    with pytest.raises(advvideo.Unsupported):
        advvideo.stream_input(stream("video", "h264"), w)
    head = adx(samples=100)
    assert advvideo.stream_input(stream("audio", "adx", head + rand(4 * 36, 1)), w) == ["-f", "adx", "-i",
                                                                                        str(w / "audio.adx")]
    assert advvideo.stream_input(stream("audio", "hca", b"hca", 2), w, KEY, "vgm") == ["-f", "wav", "-i",
                                                                                     str(w / "audio_2.wav")]
    assert calls == [["vgm", "-i", "-o", str(w / "audio_2.wav"), str(w / "audio_2.hca")]]
    assert (w / ".hcakey").read_bytes() == struct.pack(">Q", KEY)
    assert advvideo.stream_input(stream("subtitle", "sbt", sbt(0, 1000, 0, 10, "hi")), w) == [
        "-f", "srt", "-i", str(w / "subtitle.srt")]
    assert (w / "subtitle.srt").read_bytes() == b"1\n00:00:00,000 --> 00:00:00,010\nhi\n\n"


def test_mkv_arguments():
    streams = [stream("video", "vp9"), stream("alpha", "mpeg1"), stream("audio", "adx"), stream("audio", "hca", channel=1),
               stream("subtitle", "sbt")]
    inputs = {s.name: ["-i", s.name] for s in streams}
    assert advvideo.mkv_args(streams, inputs, Path("m.mkv"), 5) == [
        "-hide_banner", "-y", "-loglevel", "error", "-i", "video", "-i", "alpha", "-i", "audio", "-i", "audio_1",
        "-i", "subtitle", "-map", "0:v:0", "-map", "1:v:0", "-metadata:s:v:1", "title=alpha", "-disposition:v:1", "0",
        "-map", "2:a:0", "-map", "3:a:0", "-map", "4:s:0", "-c:v", "copy", "-c:a", "flac", "-compression_level", "5",
        "-flags:a", "+bitexact", "-c:s", "srt", "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "matroska",
        "m.mkv"]
    silent = advvideo.mkv_args([stream("video", "vp9")], {"video": ["-i", "v"]}, Path("m.mkv"), 8)
    assert "-c:a" not in silent and "-c:s" not in silent


def test_webm_arguments():
    """A VP9 video without alpha is copied (the story's command); anything else is re-encoded with the pinned VP9
    settings; the first audio stream as Opus; subtitles and further audio streams left out."""
    streams = [stream("video", "vp9"), stream("audio", "adx"), stream("audio", "adx", channel=1),
               stream("subtitle", "sbt")]
    picked = advvideo.webm_streams(streams)
    assert [s.name for s in picked] == ["video", "audio"] and not advvideo.webm_reencoded(picked)
    inputs = {"video": ["-f", "ivf", "-i", "video.ivf"], "audio": ["-f", "adx", "-i", "audio.adx"],
              "alpha": ["-fflags", "+genpts", "-f", "mpegvideo", "-i", "alpha.m1v"]}
    assert advvideo.webm_args(picked, inputs, Path("m.webm")) == [
        "-hide_banner", "-y", "-loglevel", "error", "-f", "ivf", "-i", "video.ivf", "-f", "adx", "-i", "audio.adx",
        "-map", "0:v:0", "-map", "1:a:0", "-c:a", "libopus", "-b:a", "192k", "-flags:a", "+bitexact", "-c:v", "copy",
        "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "webm", "m.webm"]
    video = stream("video", "vp9", header=video_header(9, 928, 580, disp=(928, 580)))
    alpha = stream("alpha", "mpeg1", header=video_header(1, 928, 584, disp=(928, 580), alpha_type=1))
    picked = advvideo.webm_streams([video, alpha, stream("alpha", "mpeg1", channel=1)])
    assert [s.name for s in picked] == ["video", "alpha"] and advvideo.webm_reencoded(picked)
    assert advvideo.webm_args(picked, inputs, Path("m.webm")) == [
        "-hide_banner", "-y", "-loglevel", "error", "-f", "ivf", "-i", "video.ivf", "-fflags", "+genpts", "-f",
        "mpegvideo", "-i", "alpha.m1v", "-filter_complex",
        "[0:v]setpts=N/FRAME_RATE/TB,crop=928:580:0:0[c];[1:v]setpts=N/FRAME_RATE/TB,crop=928:580:0:0,format=gray[a];"
        "[c][a]alphamerge,format=yuva420p[v]", "-map", "[v]", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "20",
        "-row-mt", "0", "-threads", "8", "-flags:v", "+bitexact", "-auto-alt-ref", "0", "-map_metadata", "-1",
        "-fflags", "+bitexact", "-f", "webm", "m.webm"]
    prime = [stream("video", "mpeg1", header=video_header(1, 1280, 720)), stream("audio", "hca")]
    inputs = {"video": ["-i", "video.m1v"], "audio": ["-i", "audio.wav"]}
    assert advvideo.webm_args(prime, inputs, Path("m.webm")) == [
        "-hide_banner", "-y", "-loglevel", "error", "-i", "video.m1v", "-i", "audio.wav", "-filter_complex",
        "[0:v]setpts=N/FRAME_RATE/TB,crop=1280:720:0:0[c];[c]format=yuv420p[v]", "-map", "[v]", "-map", "1:a:0",
        "-c:a", "libopus", "-b:a", "192k", "-flags:a", "+bitexact", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "20",
        "-row-mt", "0", "-threads", "8", "-flags:v", "+bitexact", "-map_metadata", "-1", "-fflags", "+bitexact",
        "-f", "webm", "m.webm"]


def test_story_export_keeps_its_command(tmp_path, monkeypatch):
    """The story's WebM of a VP9 + ADX movie: the command it always ran (plus -hide_banner), the same record."""
    info = {"movieInfo": {"codecType": 9, "width": 320, "height": 180, "dispWidth": 320, "dispHeight": 180,
                          "framerateN": 30, "framerateD": 1, "totalFrames": 3, "numAlphaStreams": 0,
                          "numSubtitleChannels": 0, "numAudioStreams": 1}, "assetInfo": {"loop": 0}}
    usm = tmp_path / "m.usm"
    usm.write_bytes(build([(b"@SFV", 0, None, VP9, "video"), (b"@SFA", 0, None, [adx(samples=100) + rand(4 * 36, 1)],
                                                              "audio")]))
    monkeypatch.setattr(advvideo, "usm_asset", lambda cat, key: (info, usm))
    monkeypatch.setattr("nnnotes.config.tool", lambda name, exe: name)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        Path(args[-1]).write_bytes(b"webm")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(advvideo.subprocess, "run", fake_run)
    work = tmp_path / "videos" / "_work"
    rec = advvideo.export(None, "Cri/Video/adv/m/m", tmp_path / "videos" / "m.webm", KEY, work)
    assert calls == [["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-f", "ivf", "-i", str(work / "video.ivf"),
                      "-f", "adx", "-i", str(work / "audio.adx"), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "libopus",
                      "-b:a", "192k", "-flags:a", "+bitexact", "-c:v", "copy", "-map_metadata", "-1", "-fflags",
                      "+bitexact", "-f", "webm", str(tmp_path / "videos" / "m.webm")]]
    assert list(rec) == ["key", "codec", "width", "height", "displayWidth", "displayHeight", "frameRate", "frames",
                         "assetInfo", "audio"]
    assert rec["audio"] == {"codec": "opus", "bitrate": "192k", "source": {"codec": "adx", "channels": 2,
                                                                          "sampleRate": 48000, "samples": 100}}
    assert not work.exists()
    usm.write_bytes(build([(b"@SFV", 0, video_header(9), VP9, "video"),
                           (b"@ALP", 0, video_header(1, alpha_type=1), MPEG1, "video")]))
    info["movieInfo"]["numAudioStreams"] = 0
    rec = advvideo.export(None, "Cri/Video/x", tmp_path / "videos" / "x.webm", KEY, work)
    assert rec["encode"] == {"codec": "vp9", "crf": 20, "alpha": True} and "audio" not in rec
    assert "alphamerge" in calls[-1][calls[-1].index("-filter_complex") + 1]


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


# ---------------------------------------------------------------- with ffmpeg (skipped without it)
def _encoders(ffmpeg):
    r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return r.stdout


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_real_streams_through_ffmpeg(tmp_path):
    """Tiny real VP9, MPEG-1 and H.264 streams (made by ffmpeg) in a USM: the Matroska file holds every track and
    decodes to the frames of the streams themselves; the WebM re-encode of VP9 + MPEG-1 alpha has an alpha channel."""
    enc = _encoders(FFMPEG)
    if "libvpx-vp9" not in enc:
        pytest.skip("ffmpeg without libvpx-vp9")

    def make(args, name):
        p = tmp_path / name
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=64x48:rate=25:duration=0.2", *args, str(p)], check=True)
        return p.read_bytes()
    vp9 = make(["-c:v", "libvpx-vp9", "-f", "ivf"], "v.ivf")
    m1v = make(["-c:v", "mpeg1video", "-bf", "2", "-f", "mpeg1video"], "a.m1v")
    parts = [vp9[:0x300], vp9[0x300:]]
    streams = [(b"@SFV", 0, video_header(9, 64, 48, (25, 1), 5), parts, "video"),
               (b"@ALP", 0, video_header(1, 64, 48, (25, 1), 5, alpha_type=1), [m1v[:0x280], m1v[0x280:]], "video")]
    if "libx264" in enc:
        h264 = make(["-c:v", "libx264", "-bf", "0", "-f", "h264"], "v.h264")
        streams.append((b"@SFV", 1, video_header(5, 64, 48, (25, 1), 5), [h264[:0x300], h264[0x300:]], "video"))
    got = advvideo.demux(build(streams), KEY)
    work, mkv = tmp_path / "w", tmp_path / "m.mkv"
    inputs = {s.name: advvideo.stream_input(s, work) for s in got}
    advvideo.run_ffmpeg(FFMPEG, advvideo.mkv_args(got, inputs, mkv, 8), "mkv")

    def md5s(*args):
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", *args, "-f", "framemd5", "-"],
                           capture_output=True, text=True, check=True)
        return [ln.rsplit(",", 1)[-1].strip() for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]
    for i, s in enumerate(got):
        assert md5s("-i", str(mkv), "-map", f"0:v:{i}") == md5s(*inputs[s.name]) and len(md5s(*inputs[s.name])) == 5
    webm = tmp_path / "m.webm"
    advvideo.write_webm(got, webm, tmp_path / "w2", FFMPEG)
    r = subprocess.run([FFMPEG, "-hide_banner", "-c:v", "libvpx-vp9", "-i", str(webm), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True)
    assert len(r.stdout) == 64 * 48 * 4 and min(r.stdout[3::4]) < 255
