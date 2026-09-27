"""CRI Sofdec2 movies (USM): demux, masks, subtitles; ADV videos: Movie / Clip VideoID -> the episode's -Video row ->
`Cri/Video/<_assetName>` (a USM) -> <story>/videos/<name>.webm + videos/videos.json.

An addressable key `Cri/Video/<assetName>` depends on a small bundle holding the CriWare.Assets MonoBehaviour of the
movie (`movieInfo`: size, frame rate, frame count, codec, alpha / audio / subtitle stream counts; `assetInfo`) and on
the USM itself, stored as-is on the CDN like the cue sheets' raw data (VideoManager.Prepare hands it to CRI Mana). A
movie asset may instead hold the USM bytes itself (a CriSerializedBytesAssetImpl).

USM: a sequence of chunks `<signature><size:be32>` + a header (payload offset, padding size, channel, data type, frame
time, frame rate) + payload. Streams by signature, each in one or more channels: `@SFV` video, `@ALP` alpha (a second
video stream whose luma is the opacity of the video's frames, decoded in step with them), `@SFA` audio, `@SBT`
subtitles. Data type 0 = stream data, 1 = the stream's header table (a CRI @UTF table: VIDEO_HDRINFO,
AUDIO_HDRINFO, ...), 2 = metadata / end markers, 3 = seek table.

Codecs: video and alpha by the header's `mpeg_codec`: 1 MPEG-1 video (CRI's "Sofdec.Prime": an MPEG-1 elementary
stream whose user data asks for an 11-bit intra DC precision, the TMPGEnc extension FFmpeg's MPEG-1 decoder reads),
5 H.264 (an Annex B elementary stream), 9 VP9 (in an IVF container); audio by the header's `audio_codec`: 2 ADX,
4 HCA. Without a header the codec is recognised from the stream's first bytes.

Masks: 32-byte masks derived from the 64-bit CRI key -- the same key the HCA audio uses, read from the game's boot
data by crikey.py. Video and alpha payloads are masked from byte 0x40 on (a rolling mask over bytes 0x100.., then a
mask chained through them over the first 0x100), ADX payloads from byte 0x140 on (a fixed mask); HCA payloads and
subtitles are stored plain (an HCA stream carries its own cipher, which the decoder undoes with the same key). With
key 0 (decryption disabled) every stream is plain. A catalog may hold movies stored plain beside masked ones: a video,
alpha or ADX stream is kept as stored when only that form shows the stream's structure past byte 0x40 (MPEG-1 slice
start codes, H.264 start codes, VP9 superframe indexes whose frame sizes add up, ADX frames whose scale word has its
top bit clear), else unmasked.

Subtitles: a stream of records, each five little-endian u32 (language id, time unit in ticks per second, start,
duration, text size) followed by the text bytes, which end in up to two NUL bytes. The text is kept as stored;
subtitle_srt / subtitle_webvtt write the records of one channel whose text is UTF-8.

Movie files (ffmpeg: `[paths] ffmpeg`, else found on PATH): the Matroska file of cri.movie carries every stream
(mkv_args); the WebM of the story and of cri.movie's `format: webm` (write_webm) keeps a VP9 stream as is and
re-encodes any other video, and a video with an alpha stream, as VP9 (libvpx at a constant quality, with a straight
alpha channel from the alpha stream's luma); its audio is the first audio stream as Opus (libopus). Every encoder runs
in its bit-exact mode, so the files are reproducible. An MPEG-1 or H.264 elementary stream carries no timestamps:
ffmpeg derives them (`-fflags +genpts`), from the frame rate of the stream's header for H.264.
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .jsonio import write_json

if TYPE_CHECKING:
    from .catalog import Catalog

VIDEO_DIR = "videos"
INDEX = "videos/videos.json"
AUDIO_BITRATE = "192k"
# movieInfo.codecType (CriMana.CodecType)
CODECS = {1: "sofdec.prime", 5: "h264", 9: "vp9"}
ADX_SIGNATURE = b"\x80\x00"
ADX_FRAME = 18                 # bytes of one ADX frame: 32 samples of one channel
ADX_END = b"\x80\x01"          # the end-of-stream block after the last frame: 0x8001, its size, zero padding
HCA_SIGNATURES = (b"HCA\x00", b"\xc8\xc3\xc1\x00")      # plain, and with the high bits of an encrypted header
KINDS = {b"@SFV": "video", b"@ALP": "alpha", b"@SFA": "audio", b"@SBT": "subtitle"}
KIND_ORDER = ("video", "alpha", "audio", "subtitle")
VIDEO_CODECS = {1: "mpeg1", 5: "h264", 9: "vp9"}          # VIDEO_HDRINFO mpeg_codec
AUDIO_CODECS = {2: "adx", 4: "hca"}                       # AUDIO_HDRINFO audio_codec
EXTENSIONS = {"vp9": "ivf", "mpeg1": "m1v", "h264": "h264", "adx": "adx", "hca": "hca"}
SBT_RECORD = struct.Struct("<5I")
CODEC_REASON, STREAM_REASON = "unsupported.usm.codec", "unsupported.usm.stream"


class Unsupported(ValueError):
    """A USM stream nnnotes does not convert; `code` is its reason code (docs/stages.md)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Stream:
    """One stream of a USM: `kind` video / alpha / audio / subtitle, its channel, its codec (vp9, h264, mpeg1; adx,
    hca; sbt), the first row of its header table ({} without one) and its bytes as stored, unmasked."""
    kind: str
    channel: int
    codec: str
    data: bytes = field(repr=False)
    header: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        """`<kind>` for channel 0, else `<kind>_<channel>`."""
        return self.kind if self.channel == 0 else f"{self.kind}_{self.channel}"


# --- USM ------------------------------------------------------------------------------
def masks(key: int) -> tuple[bytes, bytes, bytes]:
    """(video mask 1, video mask 2, audio mask) of a 64-bit CRI key."""
    k = int(key).to_bytes(8, "little")
    t = [0] * 0x20
    t[0x00] = k[0]
    t[0x01] = k[1]
    t[0x02] = k[2]
    t[0x03] = (k[3] - 0x34) & 0xFF
    t[0x04] = (k[4] + 0xF9) & 0xFF
    t[0x05] = k[5] ^ 0x13
    t[0x06] = (k[6] + 0x61) & 0xFF
    t[0x07] = t[0x00] ^ 0xFF
    t[0x08] = (t[0x02] + t[0x01]) & 0xFF
    t[0x09] = (t[0x01] - t[0x07]) & 0xFF
    t[0x0A] = t[0x02] ^ 0xFF
    t[0x0B] = t[0x01] ^ 0xFF
    t[0x0C] = (t[0x0B] + t[0x09]) & 0xFF
    t[0x0D] = (t[0x08] - t[0x03]) & 0xFF
    t[0x0E] = t[0x0D] ^ 0xFF
    t[0x0F] = (t[0x0A] - t[0x0B]) & 0xFF
    t[0x10] = (t[0x08] - t[0x0F]) & 0xFF
    t[0x11] = t[0x10] ^ t[0x07]
    t[0x12] = t[0x0F] ^ 0xFF
    t[0x13] = t[0x03] ^ 0x10
    t[0x14] = (t[0x04] - 0x32) & 0xFF
    t[0x15] = (t[0x05] + 0xED) & 0xFF
    t[0x16] = t[0x06] ^ 0xF3
    t[0x17] = (t[0x13] - t[0x0F]) & 0xFF
    t[0x18] = (t[0x15] + t[0x07]) & 0xFF
    t[0x19] = (0x21 - t[0x13]) & 0xFF
    t[0x1A] = t[0x14] ^ t[0x17]
    t[0x1B] = (t[0x16] + t[0x16]) & 0xFF
    t[0x1C] = (t[0x17] + 0x44) & 0xFF
    t[0x1D] = (t[0x03] + t[0x04]) & 0xFF
    t[0x1E] = (t[0x05] - t[0x16]) & 0xFF
    t[0x1F] = t[0x1D] ^ t[0x13]
    audio = b"URUC"
    return (bytes(t), bytes(x ^ 0xFF for x in t),
            bytes(audio[(i >> 1) & 3] if i & 1 else t[i] ^ 0xFF for i in range(0x20)))


def unmask_video(payload: bytes, mask1: bytes, mask2: bytes) -> bytes:
    """Undo the video mask of one @SFV / @ALP data payload (payloads shorter than 0x240 bytes are not masked).

    Bytes 0x140.. (0x100.. of the masked part): out[i] = in[i] ^ mask2[i % 32] ^ out[i - 32] (seeded with in ^ mask2
    for the first 32); then the first 0x100: out[i] = in[i] ^ mask1[i % 32] ^ (xor of out[0x100 + j], j <= i,
    j = i mod 32)."""
    body = np.frombuffer(payload, np.uint8)[0x40:]
    if len(body) < 0x200:
        return payload
    tail = body[0x100:]
    n = len(tail)
    x = np.zeros(-(-n // 32) * 32, np.uint8)
    x[:n] = tail ^ np.resize(np.frombuffer(mask2, np.uint8), n)
    tail = np.bitwise_xor.accumulate(x.reshape(-1, 32), axis=0).reshape(-1)[:n]
    chain = np.bitwise_xor.accumulate(tail[:0x100].reshape(8, 32), axis=0) ^ np.frombuffer(mask1, np.uint8)
    head = body[:0x100] ^ chain.reshape(-1)
    return payload[:0x40] + head.tobytes() + tail.tobytes()


def unmask_audio(payload: bytes, mask: bytes) -> bytes:
    """Undo the audio mask of one ADX @SFA data payload (bytes 0x140.. XOR mask[i % 32])."""
    if len(payload) <= 0x140:
        return payload
    body = np.frombuffer(payload, np.uint8)[0x140:]
    return payload[:0x140] + (body ^ np.resize(np.frombuffer(mask, np.uint8), len(body))).tobytes()


def chunks(data: bytes):
    """(signature, channel, data type, payload) of every USM chunk."""
    i = 0
    while i < len(data):
        if len(data) - i < 0x20:
            raise ValueError(f"USM: truncated chunk at {i:#x}")
        sig = data[i:i + 4]
        size = struct.unpack_from(">I", data, i + 4)[0]
        offset, padding = data[i + 9], struct.unpack_from(">H", data, i + 10)[0]
        channel, dtype = data[i + 12], data[i + 15]
        end = i + 8 + size
        if end > len(data) or 8 + offset > size + 8 - padding:
            raise ValueError(f"USM: chunk {sig!r} at {i:#x} overruns the file")
        yield sig, channel, dtype, data[i + 8 + offset:end - padding]
        i = end


def _header(payload: bytes) -> dict:
    """The first row of a stream's @UTF header table (its scalar and string columns)."""
    from .acb import utf_table
    rows = utf_table(payload)
    return {k: v for k, v in rows[0].items() if isinstance(k, str) and not isinstance(v, bytes)} if rows else {}


def _video_codec(header: dict, data: bytes, what: str) -> str:
    n = header.get("mpeg_codec")
    if n is not None:
        codec = VIDEO_CODECS.get(n)
        if codec is None:
            raise Unsupported(CODEC_REASON, f"{what}: codec {n} (mpeg_codec)")
    else:
        codec = _sniff_video(data)
        if codec is None:
            raise Unsupported(CODEC_REASON, f"{what}: no header and an unknown stream start {data[:8].hex()}")
    if codec == "vp9" and not (data[:4] == b"DKIF" and data[8:12] == b"VP90"):
        raise Unsupported(CODEC_REASON, f"{what}: the VP9 stream is not in IVF: {data[:4].hex()} {data[8:12].hex()}")
    return codec


def _sniff_video(data: bytes) -> str | None:
    if data[:4] == b"DKIF":
        return "vp9" if data[8:12] == b"VP90" else None
    head = data[:4096]
    body = head.lstrip(b"\0")
    if len(head) - len(body) < 2 or body[:1] != b"\x01" or len(body) < 2:
        return None
    if body[1] == 0xB3:                       # MPEG sequence header
        return "mpeg1"
    return "h264" if body[1] & 0x80 == 0 else None    # an H.264 NAL unit header


def _audio_codec(header: dict, first: bytes, what: str) -> str:
    n = header.get("audio_codec")
    if n is not None:
        codec = AUDIO_CODECS.get(n)
        if codec is None:
            raise Unsupported(CODEC_REASON, f"{what}: codec {n} (audio_codec)")
        return codec
    if first[:2] == ADX_SIGNATURE:
        return "adx"
    if first[:4] in HCA_SIGNATURES:
        return "hca"
    raise Unsupported(CODEC_REASON, f"{what}: no header and an unknown stream start {first[:8].hex()}")


def demux(data: bytes, key: int) -> list[Stream]:
    """Every stream of a USM, unmasked, ordered video, alpha, audio, subtitle and by channel. Unsupported (with its
    reason code) for a stream of another kind or codec."""
    if data[:4] != b"CRID":
        raise ValueError(f"not a USM (CRID): {data[:4]!r}")
    m1, m2, am = masks(key)
    headers: dict[tuple[str, int], dict] = {}
    parts: dict[tuple[str, int], list[bytes]] = {}
    for sig, channel, dtype, payload in chunks(data):
        if sig == b"CRID":
            continue
        kind = KINDS.get(sig)
        if dtype == 1 and kind is not None and payload[:4] == b"@UTF":
            headers[(kind, channel)] = _header(payload)
        elif dtype == 0:
            if kind is None:
                raise Unsupported(STREAM_REASON, f"USM stream {sig.decode('latin-1')} channel {channel}")
            parts.setdefault((kind, channel), []).append(payload)
    out = []
    for kind, channel in sorted(parts, key=lambda k: (KIND_ORDER.index(k[0]), k[1])):
        ps, header, what = parts[(kind, channel)], headers.get((kind, channel), {}), f"{kind} channel {channel}"
        if kind in ("video", "alpha"):
            codec = _video_codec(header, ps[0][:0x40], what)      # a payload's first 0x40 bytes are never masked
            if key:
                ps = _form(codec, ps, lambda p: unmask_video(p, m1, m2))
        elif kind == "audio":
            codec = _audio_codec(header, ps[0], what)
            if key and codec == "adx":
                ps = _form(codec, ps, lambda p: unmask_audio(p, am))
        else:
            codec = "sbt"
        out.append(Stream(kind, channel, codec, b"".join(ps), header))
    return out


_START = re.compile(rb"\x00\x00\x01")
_MPEG_SLICE = re.compile(rb"\x00\x00\x01[\x01-\xaf]")


def _form(codec: str, payloads: list[bytes], unmask) -> list[bytes]:
    """The payloads unmasked, or as stored when only that form shows the stream's structure (_structure)."""
    unmasked = [unmask(p) for p in payloads]
    return payloads if _structure(codec, payloads) > _structure(codec, unmasked) else unmasked


def _structure(codec: str, payloads: list[bytes]) -> int:
    """How often a stream's structure shows in the masked part of its payloads (past byte 0x40): MPEG-1 slice start
    codes, H.264 start codes, VP9 frames ending in a superframe index whose frame sizes add up, ADX frames whose
    scale word has its top bit clear."""
    if codec == "mpeg1":
        return sum(len(_MPEG_SLICE.findall(p, 0x40)) for p in payloads)
    if codec == "h264":
        return sum(len(_START.findall(p, 0x40)) for p in payloads)
    if codec == "vp9":
        return sum(_superframe(p) for p in payloads)
    data = b"".join(payloads)
    start = struct.unpack_from(">H", data, 2)[0] + 4
    return int(np.count_nonzero(np.frombuffer(data, np.uint8)[start::ADX_FRAME] < 0x80))


def _superframe(p: bytes) -> int:
    """1 when the VP9 frame of an IVF payload (its 12-byte frame header, after the file header in the first one) ends
    in a superframe index whose frame sizes add up to the frame, else 0."""
    at = 32 if p[:4] == b"DKIF" else 0
    if len(p) < at + 12:
        return 0
    size = int.from_bytes(p[at:at + 4], "little")
    d = p[at + 12:at + 12 + size]
    if not d or len(d) != size or d[-1] & 0xE0 != 0xC0:
        return 0
    marker = d[-1]
    frames, width = (marker & 7) + 1, ((marker >> 3) & 3) + 1
    index = 2 + frames * width
    if len(d) < index or d[-index] != marker:
        return 0
    sizes = sum(int.from_bytes(d[len(d) - index + 1 + i * width:len(d) - index + 1 + (i + 1) * width], "little")
                for i in range(frames))
    return int(sizes + index == len(d))


def video_facts(s: Stream) -> dict:
    """The facts of a video or alpha stream: its codec and what its header table says (coded and display size,
    frame rate, frames; an alpha stream's alpha type); a VP9 stream without a header: its IVF header's."""
    h = s.header
    if not h and s.codec == "vp9":
        w, ht, rate, scale, frames = struct.unpack_from("<HHIII", s.data, 12)
        return {"codec": "vp9", "width": w, "height": ht, "frameRate": [rate, scale], "frames": frames}
    facts = {"codec": s.codec}
    for k, name in (("width", "width"), ("height", "height"), ("disp_width", "displayWidth"),
                    ("disp_height", "displayHeight"), ("total_frames", "frames")):
        if h.get(k) is not None:
            facts[name] = h[k]
    if h.get("framerate_n") and h.get("framerate_d"):
        facts["frameRate"] = [h["framerate_n"], h["framerate_d"]]
    if s.kind == "alpha" and h.get("alpha_type") is not None:
        facts["alphaType"] = h["alpha_type"]
    return facts


def display_size(video: Stream) -> tuple[int, int] | None:
    """The display size of a video stream (its header's disp_width x disp_height), None when not known."""
    w, h = video.header.get("disp_width"), video.header.get("disp_height")
    return (w, h) if w and h else None


# --- audio ------------------------------------------------------------------------------
def adx_info(adx: bytes) -> dict:
    """ADX header: sample rate, channels, samples (big-endian fields after the 0x8000 signature)."""
    if adx[:2] != ADX_SIGNATURE:
        raise NotImplementedError(f"USM audio is not ADX: {adx[:4]!r}")
    data_at = struct.unpack_from(">H", adx, 2)[0] + 4
    if adx[data_at - 6:data_at] != b"(c)CRI":
        raise ValueError("ADX header without the (c)CRI mark")
    return {"codec": "adx", "channels": adx[7], "sampleRate": struct.unpack_from(">I", adx, 8)[0],
            "samples": struct.unpack_from(">I", adx, 12)[0]}


def adx_frames(adx: bytes) -> bytes:
    """The ADX stream up to the last frame its header counts (ceil(samples / 32) frames per channel after the
    header), without the end-of-stream block the USM streams carry after it (0x8001, the block size, zeros). ffmpeg's
    ADX demuxer reports a final read shorter than one frame of every channel as an input/output error; the decoded
    samples are the same without the block."""
    info = adx_info(adx)
    end = struct.unpack_from(">H", adx, 2)[0] + 4 + -(-info["samples"] // 32) * ADX_FRAME * info["channels"]
    if len(adx) < end:
        raise ValueError(f"ADX: {len(adx)} bytes, the header counts frames up to byte {end}")
    tail = adx[end:]
    if tail and not (tail[:2] == ADX_END and len(tail) >= 4 and len(tail) == 4 + struct.unpack_from(">H", tail, 2)[0]
                     and not tail[4:].strip(bytes(1))):
        raise ValueError(f"ADX: the {len(tail)} bytes after the last frame are not an end-of-stream block")
    return adx[:end]


def hca_info(hca: bytes) -> dict:
    """HCA header: channels, sample rate, blocks, block size, cipher type (the header's chunk names may carry the
    high bit of an encrypted header)."""
    if hca[:4] not in HCA_SIGNATURES:
        raise ValueError(f"not an HCA stream: {hca[:4].hex()}")
    size = struct.unpack_from(">H", hca, 6)[0]
    head = bytes(b & 0x7F for b in hca[:size])
    info = {"codec": "hca"}
    i = head.find(b"fmt\x00")
    if i < 0:
        raise ValueError("HCA header without fmt")
    info["channels"], info["sampleRate"] = hca[i + 4], int.from_bytes(hca[i + 5:i + 8], "big")
    info["blocks"] = struct.unpack_from(">I", hca, i + 8)[0]
    for tag in (b"comp", b"dec\x00"):
        j = head.find(tag)
        if j >= 0:
            info["blockSize"] = struct.unpack_from(">H", hca, j + 4)[0]
            break
    j = head.find(b"ciph")
    info["cipher"] = struct.unpack_from(">H", hca, j + 4)[0] if j >= 0 else 0
    return info


# --- subtitles --------------------------------------------------------------------------
def subtitle_records(data: bytes) -> list[dict]:
    """The records of a subtitle stream: language, timeUnit (ticks per second), start, duration, text (the stored
    bytes without their NUL terminators), terminator (their count). ValueError when the bytes are not a sequence of
    records."""
    out, i = [], 0
    while i < len(data):
        if len(data) - i < SBT_RECORD.size:
            raise ValueError(f"subtitle record at byte {i}: {len(data) - i} bytes, a record header has 20")
        language, unit, start, duration, size = SBT_RECORD.unpack_from(data, i)
        i += SBT_RECORD.size
        if unit == 0:
            raise ValueError(f"subtitle record at byte {i - SBT_RECORD.size}: time unit 0")
        if len(data) - i < size:
            raise ValueError(f"subtitle record at byte {i - SBT_RECORD.size}: text of {size} bytes, {len(data) - i} "
                             f"left")
        text = data[i:i + size]
        i += size
        stripped = text.rstrip(b"\0")
        out.append({"language": language, "timeUnit": unit, "start": start, "duration": duration, "text": stripped,
                    "terminator": len(text) - len(stripped)})
    return out


def _ms(ticks: int, unit: int) -> int:
    return (ticks * 1000 + unit // 2) // unit


def _clock(ms: int, sep: str) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _cues(records: list[dict]) -> list[tuple[int, int, list[str]]]:
    """(start ms, end ms, text lines) in start order (stored order among equal starts); the text decoded as UTF-8,
    line breaks normalised to LF, an empty line (which would end the cue) as a single space."""
    cues = []
    for r in records:
        text = r["text"].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        start = _ms(r["start"], r["timeUnit"])
        cues.append((start, _ms(r["start"] + r["duration"], r["timeUnit"]), [ln or " " for ln in text.split("\n")]))
    return sorted(cues, key=lambda c: c[0])


def subtitle_srt(records: list[dict]) -> str:
    """SubRip of one channel's records (UnicodeDecodeError when a text is not UTF-8)."""
    return "".join(f"{n}\n{_clock(a, ',')} --> {_clock(b, ',')}\n" + "\n".join(lines) + "\n\n"
                   for n, (a, b, lines) in enumerate(_cues(records), 1))


def subtitle_webvtt(records: list[dict]) -> str:
    """WebVTT of one channel's records, `&`, `<`, `>` escaped (UnicodeDecodeError when a text is not UTF-8)."""
    esc = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
    return "WEBVTT\n\n" + "".join(f"{_clock(a, '.')} --> {_clock(b, '.')}\n" + "\n".join(ln.translate(esc) for ln in lines)
                                  + "\n\n" for a, b, lines in _cues(records))


def subtitle_json(channel: int, records: list[dict]) -> dict:
    """One channel's records as stored: language, timeUnit, start, duration (ticks of 1/timeUnit s), terminator,
    and `text` (the bytes decoded as UTF-8) or, when they are not UTF-8, `textHex`."""
    out = []
    for r in records:
        rec = {k: r[k] for k in ("language", "timeUnit", "start", "duration")}
        try:
            rec["text"] = r["text"].decode("utf-8")
        except UnicodeDecodeError:
            rec["textHex"] = r["text"].hex()
        rec["terminator"] = r["terminator"]
        out.append(rec)
    return {"channel": channel, "records": out}


# --- movie files ------------------------------------------------------------------------
# ffmpeg's input options per codec: an MPEG-1 or H.264 elementary stream has no timestamps (+genpts); an H.264 one
# no frame rate either (the header's)
INPUT_FORMATS = {"vp9": ("-f", "ivf"), "mpeg1": ("-fflags", "+genpts", "-f", "mpegvideo"),
                 "h264": ("-fflags", "+genpts", "-f", "h264")}
# the WebM re-encode of a stream that is not VP9 or has an alpha stream: libvpx VP9 at a constant quality, one
# thread layout (the bytes do not depend on the machine's cores)
VP9_ENCODE = ("-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "20", "-row-mt", "0", "-threads", "8", "-flags:v", "+bitexact")
VP9_ALPHA = ("-auto-alt-ref", "0")
OPUS_ENCODE = ("-c:a", "libopus", "-b:a", AUDIO_BITRATE, "-flags:a", "+bitexact")
TRACK_TYPES = {"video": "v", "alpha": "v", "audio": "a", "subtitle": "s"}


def stream_input(s: Stream, work: Path, key: int = 0, vgmstream: str | None = None) -> list[str]:
    """Write the file ffmpeg reads for stream `s` into `work`; its ffmpeg input options. Video and alpha: the stream
    as stored; ADX: its frames (adx_frames); HCA: the samples vgmstream decodes with the key (`.hcakey` beside it);
    subtitles: SubRip (subtitle_srt)."""
    work.mkdir(parents=True, exist_ok=True)
    if s.kind in ("video", "alpha"):
        p = work / f"{s.name}.{EXTENSIONS[s.codec]}"
        p.write_bytes(s.data)
        opts = list(INPUT_FORMATS[s.codec])
        if s.codec == "h264":
            n, d = s.header.get("framerate_n"), s.header.get("framerate_d")
            if not (n and d):
                raise Unsupported(CODEC_REASON, f"{s.name}: an H.264 stream without a frame rate in its header")
            opts += ["-framerate", f"{n}/{d}"]
        return [*opts, "-i", str(p)]
    if s.kind == "audio" and s.codec == "adx":
        p = work / f"{s.name}.adx"
        p.write_bytes(adx_frames(s.data))
        return ["-f", "adx", "-i", str(p)]
    if s.kind == "audio":
        from . import crikey
        if vgmstream is None:
            raise ValueError(f"{s.name}: an HCA stream needs vgmstream")
        p, wav = work / f"{s.name}.hca", work / f"{s.name}.wav"
        p.write_bytes(s.data)
        crikey.write_hcakey(key, work)
        subprocess.run([vgmstream, "-i", "-o", str(wav), str(p)], check=True, capture_output=True)
        return ["-f", "wav", "-i", str(wav)]
    p = work / f"{s.name}.srt"
    p.write_text(subtitle_srt(subtitle_records(s.data)), encoding="utf-8", newline="\n")
    return ["-f", "srt", "-i", str(p)]


def mkv_args(streams: list[Stream], inputs: dict[str, list[str]], dst: Path, flac_level: int) -> list[str]:
    """ffmpeg arguments (after the executable) of the Matroska file of `streams`: video and alpha copied (an alpha
    track titled "alpha", not a default track), each audio stream as FLAC (`flac_level`), each subtitle channel as a
    SubRip track; no metadata, bit-exact container and codec flags. `inputs`: stream_input's options by stream
    name."""
    args, maps, count = ["-hide_banner", "-y", "-loglevel", "error"], [], {"v": 0, "a": 0, "s": 0}
    for i, s in enumerate(streams):
        t = TRACK_TYPES[s.kind]
        args += inputs[s.name]
        maps += ["-map", f"{i}:{t}:0"]
        if s.kind == "alpha":
            maps += [f"-metadata:s:v:{count['v']}", "title=alpha", f"-disposition:v:{count['v']}", "0"]
        count[t] += 1
    codecs = ["-c:v", "copy"]
    if count["a"]:
        codecs += ["-c:a", "flac", "-compression_level", str(int(flac_level)), "-flags:a", "+bitexact"]
    if count["s"]:
        codecs += ["-c:s", "srt"]
    return [*args, *maps, *codecs, "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "matroska", str(dst)]


def webm_streams(streams: list[Stream]) -> list[Stream]:
    """The streams of the WebM: the first video stream, the alpha stream of its channel, the first audio stream (the
    lowest channel: the track the game plays); no subtitles."""
    video = next((s for s in streams if s.kind == "video"), None)
    alpha = next((s for s in streams if video is not None and s.kind == "alpha" and s.channel == video.channel), None)
    audio = next((s for s in streams if s.kind == "audio"), None)
    return [s for s in (video, alpha, audio) if s is not None]


def webm_reencoded(streams: list[Stream]) -> bool:
    """Whether the WebM of these streams (webm_streams) re-encodes the video: a video that is not VP9, or an alpha
    stream."""
    return any(s.kind == "alpha" or (s.kind == "video" and s.codec != "vp9") for s in streams)


def webm_args(streams: list[Stream], inputs: dict[str, list[str]], dst: Path) -> list[str]:
    """ffmpeg arguments (after the executable) of the WebM of `streams` (webm_streams): a VP9 video without alpha
    copied; any other video, and a video with an alpha stream, re-encoded as VP9 (VP9_ENCODE) from its frames in
    decoding order, cropped to its display size, the alpha stream's luma as a straight alpha channel (yuva420p); the
    audio as Opus; no metadata, bit-exact container and codec flags."""
    args, maps = ["-hide_banner", "-y", "-loglevel", "error"], []
    for s in streams:
        args += inputs[s.name]
    video = next((s for s in streams if s.kind == "video"), None)
    alpha = next((s for s in streams if s.kind == "alpha"), None)
    audio_at = next((i for i, s in enumerate(streams) if s.kind == "audio"), None)
    codecs: list[str] = []
    if video is not None and not webm_reencoded(streams):
        maps += ["-map", "0:v:0"]
        codecs = ["-c:v", "copy"]
    elif video is not None:
        size = display_size(video)
        crop = f",crop={size[0]}:{size[1]}:0:0" if size else ""
        graph = f"[0:v]setpts=N/FRAME_RATE/TB{crop}[c];"
        graph += (f"[1:v]setpts=N/FRAME_RATE/TB{crop},format=gray[a];[c][a]alphamerge,format=yuva420p[v]" if alpha
                  else "[c]format=yuv420p[v]")
        args += ["-filter_complex", graph]
        maps += ["-map", "[v]"]
        codecs = [*VP9_ENCODE, *(VP9_ALPHA if alpha else ())]
    if audio_at is not None:
        maps += ["-map", f"{audio_at}:a:0", *OPUS_ENCODE]
    return [*args, *maps, *codecs, "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "webm", str(dst)]


def run_ffmpeg(ffmpeg: str, args: list[str], what: str) -> None:
    r = subprocess.run([ffmpeg, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg {what} failed: {r.stderr[-2000:]}")


def write_webm(streams: list[Stream], dst: Path, work: Path, ffmpeg: str, key: int = 0,
               vgmstream: str | None = None) -> list[Stream]:
    """The WebM of a movie's streams (webm_streams, webm_args) -> dst, through files in `work` (removed after).
    Returns the streams it carries."""
    picked = webm_streams(streams)
    try:
        inputs = {s.name: stream_input(s, work, key, vgmstream) for s in picked}
        dst.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(ffmpeg, webm_args(picked, inputs, dst), "webm")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return picked


# --- catalog ----------------------------------------------------------------------------
def usm_asset(cat: "Catalog", key: str) -> tuple[dict, Path]:
    """(the movie's CriWare.Assets MonoBehaviour typetree, the USM file) of a `Cri/Video/...` key."""
    import UnityPy
    from .addressables import remote_path
    info, raw = None, None
    for off in cat._entry(key)["dependencies"]:
        e = cat._by_off.get(off)
        if e is None:
            continue
        iid = e["internal_id"]
        if remote_path(iid) is None:
            continue
        if iid.endswith(".bundle"):
            env = UnityPy.load(str(cat.fetch(cat._as_bundle(e))))
            for o in env.objects:
                if o.type.name == "MonoBehaviour":
                    tt = o.read_typetree()
                    if "movieInfo" in tt:
                        info = tt
        else:
            raw = cat.fetch_raw(e)
    if info is None or raw is None:
        raise RuntimeError(f"{key}: movie asset or USM data not among the dependencies")
    return info, raw


def export(cat: "Catalog", key: str, dst: Path, cri_key: int, work: Path) -> dict:
    """One USM -> dst (.webm, write_webm: VP9 copied, or re-encoded when the movie's video is not VP9 or has an alpha
    stream; the first audio stream as Opus). Returns its record (movie info, the audio's source; `encode` when the
    video was re-encoded)."""
    from .config import tool
    info, raw = usm_asset(cat, key)
    mi = info["movieInfo"]
    codec = CODECS.get(mi["codecType"], f"codecType {mi['codecType']}")
    streams = demux(raw.read_bytes(), cri_key)
    if mi["numAudioStreams"] and not any(s.kind == "audio" for s in streams):
        raise RuntimeError(f"{key}: movie info lists an audio stream the USM does not carry")
    hca = any(s.kind == "audio" and s.codec == "hca" for s in webm_streams(streams))
    picked = write_webm(streams, dst, work, tool("ffmpeg", "ffmpeg"), cri_key,
                        tool("vgmstream", "vgmstream-cli") if hca else None)
    rec = {"key": key, "codec": codec, "width": mi["width"], "height": mi["height"],
           "displayWidth": mi["dispWidth"], "displayHeight": mi["dispHeight"],
           "frameRate": [mi["framerateN"], mi["framerateD"]], "frames": mi["totalFrames"],
           "assetInfo": info.get("assetInfo")}
    if webm_reencoded(picked):
        rec["encode"] = {"codec": "vp9", "crf": int(VP9_ENCODE[VP9_ENCODE.index("-crf") + 1]),
                         "alpha": any(s.kind == "alpha" for s in picked)}
    audio = next((s for s in picked if s.kind == "audio"), None)
    if audio is not None:
        rec["audio"] = {"codec": "opus", "bitrate": AUDIO_BITRATE,
                        "source": adx_info(audio.data) if audio.codec == "adx" else hca_info(audio.data)}
    return rec


def extract(cat: "Catalog", episode: dict, out_dir: Path, cri_key: int | None = None) -> dict | None:
    """Every video of the episode's closure -> videos/<name>.webm; videos/videos.json maps each VideoID of the
    Movie / Clip rows to its file, its -Video row (`master`: size, autoStop, hasAudio) and the stream facts.
    Returns the index, or None when the episode has no video."""
    from . import adv, cri
    keys = sorted(r["address"] for r in episode["resources"] if r["kind"] == "video")
    if not keys:
        return None
    if cri_key is None:
        if cat.apk is None:
            raise RuntimeError("USM key: pass cri_key= or open the Catalog with apk=")
        cri_key = cri.hca_key(cat.apk)
    out_dir = Path(out_dir)
    files: dict[str, dict] = {}
    names: dict[str, str] = {}
    for key in keys:
        name = key.rsplit("/", 1)[-1]
        if name in names:
            raise RuntimeError(f"videos {names[name]} and {key} share the file name {name}")
        names[name] = key
        rel = f"{VIDEO_DIR}/{name}.webm"
        files[key] = {"file": rel, **export(cat, key, out_dir / rel, cri_key, out_dir / VIDEO_DIR / "_work")}
    videos = {}
    for c in episode["commands"]:
        vid = c.get("VideoID")
        if c["cmd"] not in adv.VIDEO_COMMANDS or not vid or str(vid) in videos:
            continue
        row = episode["videos"].get(vid) or episode["videos"].get(str(vid))
        if not row:
            continue
        key = adv.VIDEO_ADDRESS.format(row["_assetName"])
        if key in files:
            videos[str(vid)] = {"master": row, **files[key]}
    index = {"videos": videos, "format": "WebM: VP9 as stored in the USM, audio as Opus"}
    write_json(out_dir / INDEX, index)
    return index
