"""ADV videos: Movie / Clip VideoID -> the episode's -Video row -> `Cri/Video/<_assetName>` (a CRI Sofdec2 USM)
-> <story>/videos/<name>.webm + videos/videos.json.

An addressable key `Cri/Video/<assetName>` depends on a small bundle holding the CriWare.Assets MonoBehaviour of the
movie (`movieInfo`: size, frame rate, frame count, codec, alpha / audio / subtitle stream counts; `assetInfo`) and on
the USM itself, stored as-is on the CDN like the cue sheets' raw data (VideoManager.Prepare hands it to CRI Mana).

USM: a sequence of chunks `<signature><size:be32>` + a header (payload offset, padding size, channel, data type)
+ payload; `@SFV` carries the video stream (VP9 in an IVF container in this game), `@SFA` the audio stream (ADX),
data type 0 = stream data (1 header table, 2 metadata / end markers, 3 seek table). The stream data is masked with
32-byte masks derived from the 64-bit CRI key -- the same key the HCA audio uses, read from the game's boot data by
crikey.py: video payloads from byte 0x40 on (a rolling mask over bytes 0x100.., then a mask chained through them
over the first 0x100), audio payloads from byte 0x140 on (a fixed mask); with key 0 (decryption disabled)
the streams are plain.

The WebM keeps the VP9 stream as is (no re-encode) and carries the audio as Opus (libopus, ffmpeg's bit-exact
mode, so the file is reproducible). ffmpeg: `[paths] ffmpeg`, else found on PATH.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import UnityPy

from .addressables import remote_path
from .catalog import Catalog
from .config import tool
from .jsonio import write_json
from . import adv, cri

VIDEO_DIR = "videos"
INDEX = "videos/videos.json"
AUDIO_BITRATE = "192k"
# movieInfo.codecType (CriMana.CodecType)
CODECS = {1: "sofdec.prime", 5: "h264", 9: "vp9"}
ADX_SIGNATURE = b"\x80\x00"
ADX_FRAME = 18                 # bytes of one ADX frame: 32 samples of one channel
ADX_END = b"\x80\x01"          # the end-of-stream block after the last frame: 0x8001, its size, zero padding


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
    """Undo the video mask of one @SFV data payload (payloads shorter than 0x240 bytes are not masked).

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
    """Undo the audio mask of one @SFA data payload (bytes 0x140.. XOR mask[i % 32])."""
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


def demux(data: bytes, key: int) -> dict[str, bytes]:
    """{'video': IVF / elementary stream, 'audio': ADX} (audio only when present) of a USM, unmasked (key 0: the
    game has decryption disabled and its streams are stored plain)."""
    if data[:4] != b"CRID":
        raise ValueError(f"not a USM (CRID): {data[:4]!r}")
    m1, m2, am = masks(key)
    video, audio = [], []
    for sig, channel, dtype, payload in chunks(data):
        if dtype != 0 or sig == b"CRID":
            continue
        if channel != 0 or sig not in (b"@SFV", b"@SFA"):
            raise NotImplementedError(f"USM stream {sig.decode('latin-1')} channel {channel}")
        if not key:
            (video if sig == b"@SFV" else audio).append(payload)
        elif sig == b"@SFV":
            video.append(unmask_video(payload, m1, m2))
        else:
            audio.append(unmask_audio(payload, am))
    out = {"video": b"".join(video)}
    if audio:
        out["audio"] = b"".join(audio)
    return out


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


# --- catalog ----------------------------------------------------------------------------
def usm_asset(cat: Catalog, key: str) -> tuple[dict, Path]:
    """(the movie's CriWare.Assets MonoBehaviour typetree, the USM file) of a `Cri/Video/...` key."""
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


def export(cat: Catalog, key: str, dst: Path, cri_key: int, work: Path) -> dict:
    """One USM -> dst (.webm). Returns its record (movie info, streams)."""
    info, raw = usm_asset(cat, key)
    mi = info["movieInfo"]
    codec = CODECS.get(mi["codecType"], f"codecType {mi['codecType']}")
    if codec != "vp9":
        raise NotImplementedError(f"{key}: video codec {codec}")
    if mi["numAlphaStreams"] or mi["numSubtitleChannels"] or mi["numAudioStreams"] > 1:
        raise NotImplementedError(f"{key}: {mi['numAlphaStreams']} alpha, {mi['numSubtitleChannels']} subtitle, "
                                  f"{mi['numAudioStreams']} audio streams")
    streams = demux(raw.read_bytes(), cri_key)
    if streams["video"][:4] != b"DKIF":
        raise RuntimeError(f"{key}: the VP9 stream is not IVF after unmasking")
    work.mkdir(parents=True, exist_ok=True)
    ivf = work / "video.ivf"
    ivf.write_bytes(streams["video"])
    inputs = ["-f", "ivf", "-i", str(ivf)]
    maps = ["-map", "0:v:0"]
    audio = None
    if "audio" in streams:
        audio = adx_info(streams["audio"])
        adx = work / "audio.adx"
        adx.write_bytes(adx_frames(streams["audio"]))
        inputs += ["-f", "adx", "-i", str(adx)]
        maps += ["-map", "1:a:0", "-c:a", "libopus", "-b:a", AUDIO_BITRATE, "-flags:a", "+bitexact"]
    elif mi["numAudioStreams"]:
        raise RuntimeError(f"{key}: movie info lists an audio stream the USM does not carry")
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([tool("ffmpeg", "ffmpeg"), "-y", "-loglevel", "error", *inputs, *maps, "-c:v", "copy",
                    "-map_metadata", "-1", "-fflags", "+bitexact", "-f", "webm", str(dst)], check=True)
    shutil.rmtree(work)
    rec = {"key": key, "codec": codec, "width": mi["width"], "height": mi["height"],
           "displayWidth": mi["dispWidth"], "displayHeight": mi["dispHeight"],
           "frameRate": [mi["framerateN"], mi["framerateD"]], "frames": mi["totalFrames"],
           "assetInfo": info.get("assetInfo")}
    if audio is not None:
        rec["audio"] = {"codec": "opus", "bitrate": AUDIO_BITRATE, "source": audio}
    return rec


def extract(cat: Catalog, episode: dict, out_dir: Path, cri_key: int | None = None) -> dict | None:
    """Every video of the episode's closure -> videos/<name>.webm; videos/videos.json maps each VideoID of the
    Movie / Clip rows to its file, its -Video row (`master`: size, autoStop, hasAudio) and the stream facts.
    Returns the index, or None when the episode has no video."""
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
