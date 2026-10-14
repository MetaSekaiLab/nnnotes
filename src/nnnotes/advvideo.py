"""ADV videos: Movie / Clip VideoID -> the episode's -Video row -> `Cri/Video/<_assetName>` (a USM, usm.py) ->
<story>/videos/<name>.webm + videos/videos.json.

An addressable key `Cri/Video/<assetName>` depends on a small bundle holding the CriWare.Assets MonoBehaviour of the
movie (`movieInfo`: size, frame rate, frame count, codec, alpha / audio / subtitle stream counts; `assetInfo`) and on
the USM itself, stored as-is on the CDN like the cue sheets' raw data (VideoManager.Prepare hands it to CRI Mana). A
movie asset may instead hold the USM bytes itself (a CriSerializedBytesAssetImpl).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .jsonio import write_json
from .usm import AUDIO_BITRATE, VP9_ENCODE, adx_info, demux, hca_info, webm_reencoded, webm_streams, write_webm

if TYPE_CHECKING:
    from .catalog import Catalog

VIDEO_DIR = "videos"
INDEX = "videos/videos.json"
# movieInfo.codecType (CriMana.CodecType)
CODECS = {1: "sofdec.prime", 5: "h264", 9: "vp9"}


def usm_asset(cat: Catalog, key: str) -> tuple[dict, Path]:
    """(the movie's CriWare.Assets MonoBehaviour typetree, the USM file) of a `Cri/Video/...` key."""
    import UnityPy

    from .addressables import remote_path

    info, raw = None, None
    for e in cat.dependencies(key):
        iid = e["internal_id"]
        if remote_path(iid) is None:
            continue
        if iid.endswith(".bundle"):
            env = UnityPy.load(str(cat.fetch(cat.as_bundle(e))))
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
    picked = write_webm(
        streams, dst, work, tool("ffmpeg", "ffmpeg"), cri_key, tool("vgmstream", "vgmstream-cli") if hca else None
    )
    rec = {
        "key": key,
        "codec": codec,
        "width": mi["width"],
        "height": mi["height"],
        "displayWidth": mi["dispWidth"],
        "displayHeight": mi["dispHeight"],
        "frameRate": [mi["framerateN"], mi["framerateD"]],
        "frames": mi["totalFrames"],
        "assetInfo": info.get("assetInfo"),
    }
    if webm_reencoded(picked):
        rec["encode"] = {
            "codec": "vp9",
            "crf": int(VP9_ENCODE[VP9_ENCODE.index("-crf") + 1]),
            "alpha": any(s.kind == "alpha" for s in picked),
        }
    audio = next((s for s in picked if s.kind == "audio"), None)
    if audio is not None:
        rec["audio"] = {
            "codec": "opus",
            "bitrate": AUDIO_BITRATE,
            "source": adx_info(audio.data) if audio.codec == "adx" else hca_info(audio.data),
        }
    return rec


def extract(cat: Catalog, episode: dict, out_dir: Path, cri_key: int | None = None) -> dict | None:
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
