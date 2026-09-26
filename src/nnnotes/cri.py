"""CRI ADX2 cue sheets -> per-cue audio files.

An addressable key `Cri/Sound/<cueSheet>` holds a cue sheet in one of three layouts:

  raw CDN data   the key depends on a small bundle holding the CriWare.Assets
                 MonoBehaviour and on the cue sheet's raw data on the CDN
                 (`cri_assets_cri/sound/<cueSheet>_<hash>`, stored as-is, not
                 bundle-encrypted): an ACB (`@UTF`, waveforms in its memory
                 AWB); a separate AWB (`AFS2`, streamed waveforms) would come
                 as a second dependency: decode refuses such a sheet
                 (vgmstream opening the ACB lists only its memory waveforms);
  SplitAcbData   a `Fwk.Sound.SplitAcbData` MonoBehaviour whose `_chunks`
                 TextAssets, joined in order with every byte XORed with one mask
                 byte, are an ACB with its AWB embedded (full live songs;
                 Fwk.Sound.SplitAcbLoader.Load); the mask is recovered from the
                 ACB signature `@UTF` the joined data must start with;
  embedded ACB   a CriWare.Assets MonoBehaviour named like the cue sheet whose managed
                 reference `implementation` is a CriSerializedBytesAssetImpl holding the
                 ACB bytes (`data.data`, `@UTF`, AWB in memory) in the key's own bundle
                 (live note SE / live SE / live voice sheets).

`decode` writes one file per vgmstream stream (a name repeated within the sheet, e.g.
the layered waveforms of one cue, gets `<name>_<stream>`), `cues.json` (first stream
per name) and `streams.json` (every stream in vgmstream order). `decode_acb_bytes` does the same for ACB bytes
given directly (no catalog, no cache: the cri.audio stage keeps its results in the store); `split_acb` and
`embedded_acb` give the ACB bytes of the two in-bundle layouts from their fields.

HCA streams are decrypted with the keycode from the game's own boot data (see
crikey.py, read once per APK and process). Decoding uses vgmstream; flac/ogg encoding uses ffmpeg (`[paths]
vgmstream` / `[paths] ffmpeg`, else found on PATH). The streams of a sheet are decoded in parallel (`workers`).

A sheet's ACB / AWB bytes are read once per bundle closure and process; a decoded sheet is kept by content (cache
bucket "cuesheets": ACB bytes, HCA key, format, FLAC level and the tools' files), so a sheet that several
lives or episodes play (note SE, cheers) is decoded once and its files are written again from there.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import numpy as np

from .addressables import remote_path
from .catalog import Catalog
from .config import tool, usable_cpus
from .cristages import FLAC_LEVEL                           # ffmpeg -compression_level of the flac output
from . import cache, crikey
from .jsonio import dumps, write_json

DECODED = cache.bucket("cuesheets", salt=cache.source_salt(__file__), disk=True)
SHEETS = cache.bucket("acb", max_item=128 << 20)           # (cue sheet, bundle closure) -> ACB / AWB bytes, layout
_keys: dict[tuple, int] = {}
_keys_lock = threading.Lock()


def hca_key(apk) -> int:
    """crikey.find_key of an APK, read once per process (keyed by the file's path, size and modification time)."""
    k = cache.file_id(apk)
    with _keys_lock:
        if k in _keys:
            return _keys[k]
    v = crikey.find_key(Path(apk))
    with _keys_lock:
        _keys[k] = v
    return v


def default_workers() -> int:
    """Parallel stream decodes of one sheet: a quarter of the CPUs, 1 to 4."""
    return max(1, min(4, usable_cpus() // 4))

ACB_SIGNATURE = b"@UTF"
SPLIT_ACB = "SplitAcbData (chunks joined, XOR-masked)"
EMBEDDED_ACB = "CriSerializedBytesAssetImpl (ACB bytes inside the bundle)"
RAW_ACB = "raw CDN ACB/AWB"


def raw_files(cat: Catalog, cue_sheet: str) -> dict[str, Path]:
    """{'acb': path, 'awb': path?} for a cue sheet, via the key's dependencies."""
    entry = cat._entry(f"Cri/Sound/{cue_sheet}")
    out: dict[str, Path] = {}
    for off in entry["dependencies"]:
        e = cat._by_off.get(off)
        if e is None:
            continue
        iid = e["internal_id"]
        if remote_path(iid) is None or iid.endswith(".bundle"):
            continue
        p = cat.fetch_raw(e)
        magic = p.read_bytes()[:4]
        kind = {b"@UTF": "acb", b"AFS2": "awb"}.get(magic)
        if kind is None:
            raise RuntimeError(f"unknown CRI data {iid} magic={magic!r}")
        out[kind] = p
    if "acb" not in out:
        raise RuntimeError(f"no ACB among dependencies of Cri/Sound/{cue_sheet}")
    return out


def _bundle_asset(cat: Catalog, cue_sheet: str):
    """(layout, objects by path id, typetree, bytes holder) of the cue sheet asset inside the key's bundles, or None (raw CDN
    layout). SplitAcbData: a `Fwk.Sound.SplitAcbData` MonoBehaviour with `_cueSheetName` = the sheet; embedded: a
    MonoBehaviour named like the sheet whose `implementation` managed reference is a CriSerializedBytesAssetImpl.
    SplitAcbData wins when a closure held both."""
    import UnityPy
    embedded = None
    for p in cat.fetch_key(f"Cri/Sound/{cue_sheet}"):
        env = UnityPy.load(str(p))
        for o in env.objects:
            if o.type.name != "MonoBehaviour":
                continue
            t = o.read_typetree()
            if t.get("_cueSheetName") == cue_sheet and "_chunks" in t:
                return SPLIT_ACB, {x.path_id: x for x in env.objects}, t, None
            if embedded is None and t.get("m_Name") == cue_sheet and "implementation" in t:
                refs = {r["rid"]: r for r in t.get("references", {}).get("RefIds", [])}
                impl = refs.get(t["implementation"]["rid"])
                if impl and impl["type"]["class"] == "CriSerializedBytesAssetImpl":
                    embedded = (EMBEDDED_ACB, None, t, impl)
    return embedded


def text_bytes(s) -> bytes:
    """The stored bytes of a TextAsset's m_Script (a str read with surrogateescape, or bytes)."""
    return s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)


def split_acb(parts, cue_sheet: str = "") -> bytes:
    """The ACB of a SplitAcbData from the bytes of its `_chunks` TextAssets, in order. Fwk.Sound.SplitAcbLoader.Load
    joins them and XORs every byte with one mask byte -> an in-memory ACB (`@UTF`, AWB embedded) passed to
    CriAtomExAcb.LoadAcbData. Full songs (`Cri/Sound/<X>_<Name>`,
    `Assets/AddressableResources/Cri/Sound/MusicScore/*.asset`). The mask is the one that gives the signature."""
    joined = b"".join(bytes(p) for p in parts)
    mask = joined[0] ^ ACB_SIGNATURE[0] if joined else 0
    acb = (np.frombuffer(joined, np.uint8) ^ np.uint8(mask)).tobytes()
    return _checked(acb, cue_sheet, SPLIT_ACB)


def embedded_acb(t: dict, impl: dict, cue_sheet: str = "") -> bytes:
    """The ACB of an embedded cue sheet asset: the `data.data` bytes of its CriSerializedBytesAssetImpl managed
    reference `impl` (`t`: the asset typetree; an external AWB reference is refused)."""
    acb = bytes(impl["data"]["data"])
    if (t.get("awb") or {}).get("m_PathID"):
        raise NotImplementedError(f"{cue_sheet}: embedded ACB with an external AWB reference")
    return _checked(acb, cue_sheet, EMBEDDED_ACB)


def _checked(acb: bytes, cue_sheet: str, layout: str) -> bytes:
    if acb[:4] != ACB_SIGNATURE:
        raise RuntimeError(f"{cue_sheet}: {layout} data is not an ACB (@UTF): {acb[:4]!r}")
    return acb


def _asset_acb(cue_sheet: str, layout: str, objs, t: dict, impl: dict | None) -> bytes:
    if layout == SPLIT_ACB:
        return split_acb([text_bytes(objs[c["m_PathID"]].read().m_Script) for c in t["_chunks"]], cue_sheet)
    return embedded_acb(t, impl, cue_sheet)


def _sheet_key(cat: Catalog, cue_sheet: str) -> str:
    return SHEETS.key(cue_sheet, [cache.file_id(p) for p in cat.fetch_key(f"Cri/Sound/{cue_sheet}")])


def acb_data(cat: Catalog, cue_sheet: str) -> tuple[dict[str, bytes], str]:
    """({'acb': bytes, 'awb': bytes?}, layout) of a cue sheet: SplitAcbData, then the embedded ACB, then the raw
    CDN dependencies (RAW_ACB). Read once per bundle closure and process."""
    k = _sheet_key(cat, cue_sheet)
    hit = SHEETS.get(k)
    if hit is not None and hit[0] is not None:
        return dict(hit[0]), hit[1]
    asset = _bundle_asset(cat, cue_sheet)
    if asset is not None:
        out = {"acb": _asset_acb(cue_sheet, *asset)}, asset[0]
    else:
        out = {k2: p.read_bytes() for k2, p in raw_files(cat, cue_sheet).items()}, RAW_ACB
    SHEETS.put(k, (dict(out[0]), out[1]), sum(map(len, out[0].values())))
    return out


def layout(cat: Catalog, cue_sheet: str) -> str:
    """Which layout a cue sheet is stored in (SPLIT_ACB, EMBEDDED_ACB or RAW_ACB), without extracting it."""
    k = _sheet_key(cat, cue_sheet)
    hit = SHEETS.get(k)
    if hit is not None:
        return hit[1]
    asset = _bundle_asset(cat, cue_sheet)
    lay = asset[0] if asset is not None else RAW_ACB
    SHEETS.put(k, (None, lay), 0)
    return lay


def encode_args(src: Path, dst: Path, args: list[str]) -> list[str]:
    """ffmpeg arguments (after the executable) of a bit-exact encode: no metadata, bitexact container and codec
    flags, then `args` (codec options)."""
    return ["-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map_metadata", "-1",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", *args, str(dst)]


def _check_format(fmt: str, also) -> None:
    if fmt not in ("wav", "flac", "ogg"):
        raise KeyError(fmt)
    if also is not None and also[0] == f".{fmt}":
        raise ValueError(f"also: {also[0]} is the format of the first file")


def decode(cat: Catalog, cue_sheet: str, out_dir: Path, key: int | None = None,
           fmt: str = "flac", *, flac_level: int = FLAC_LEVEL, workers: int | None = None,
           also: tuple[str, list[str]] | None = None) -> dict[str, Path]:
    """Decode every stream of a cue sheet. Returns {streamName: file of its first stream}.

    flac (default) keeps the decoded PCM bit-exact (`flac_level`: ffmpeg's compression level; any level decodes to
    the same samples); ogg re-encodes (lossy). `workers`: streams decoded at once (default default_workers()).
    `also` = (extension, ffmpeg codec options): every stream is encoded a second time from the same decoded samples
    into `<stem><extension>` next to its file (encode_args: no metadata, bit-exact flags), e.g. the web format of a
    BGM, so that it need not be transcoded from the first file later; the manifests name the first file only.
    """
    vgm = tool("vgmstream", "vgmstream-cli")
    ffmpeg = tool("ffmpeg", "ffmpeg") if fmt != "wav" or also else None
    if key is None:
        if cat.apk is None:
            raise RuntimeError("HCA key: pass key= or open the Catalog with apk=")
        key = hca_key(cat.apk)
    _check_format(fmt, also)

    out_dir = Path(out_dir)
    files, _ = acb_data(cat, cue_sheet)
    if "awb" in files:
        raise NotImplementedError(f"cue sheet {cue_sheet}: external AWB (streamed waveforms) not supported")
    ck = DECODED.key(cue_sheet, files["acb"], int(key), fmt,
                     int(flac_level) if fmt == "flac" else None, cache.file_id(vgm),
                     cache.file_id(ffmpeg) if ffmpeg else None, None if also is None else [also[0], list(also[1])])
    hit = DECODED.get(ck)
    if hit is not None:
        return _replay(hit, out_dir)
    d = decode_acb_bytes(files["acb"], None, key, fmt, out_dir, name=cue_sheet, flac_level=flac_level,
                         workers=workers, also=also, vgmstream=vgm, ffmpeg=ffmpeg)
    if cache.enabled():
        meta_json = {"files": d.files, "result": {k: v.name for k, v in d.result.items()},
                     "cues": dumps(d.cues, indent=1, ensure_ascii=True),
                     "streams": dumps(d.streams, indent=1, ensure_ascii=True)}
        DECODED.put(ck, cache.pack(json.dumps(meta_json).encode("utf-8"),
                                   [(out_dir / f).read_bytes() for f in d.files]))
    return d.result


class Decoded(NamedTuple):
    """What decode_acb_bytes wrote: {streamName: file of its first stream}, the values of cues.json and
    streams.json, and the names of the stream files (with the `also` files), sorted."""
    result: dict
    cues: dict
    streams: list
    files: list


def decode_acb_bytes(acb: bytes, awb: bytes | None, key: int, fmt: str = "flac", out_dir: Path = Path("."), *,
                     name: str = "sheet", flac_level: int = FLAC_LEVEL, workers: int | None = None,
                     also: tuple[str, list[str]] | None = None, vgmstream: str | None = None,
                     ffmpeg: str | None = None) -> Decoded:
    """Decode every stream of an ACB (its waveforms in its memory AWB) into out_dir as decode does: one file per
    stream, cues.json, streams.json. `awb`: an external AWB (refused: vgmstream opening the ACB lists only its
    memory waveforms). `key`: the HCA keycode; `name`: the stem of the ACB file vgmstream reads (the stream names
    come from the cue names inside the ACB); `vgmstream` / `ffmpeg`: the tools (default: the configured ones). The
    work directory is out_dir/_work, removed at the end."""
    if awb is not None:
        raise NotImplementedError(f"cue sheet {name}: external AWB (streamed waveforms) not supported")
    _check_format(fmt, also)
    vgm = vgmstream or tool("vgmstream", "vgmstream-cli")
    if ffmpeg is None and (fmt != "wav" or also):
        ffmpeg = tool("ffmpeg", "ffmpeg")
    out_dir = Path(out_dir)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    acb_file = work / f"{name}.acb"
    acb_file.write_bytes(acb)
    crikey.write_hcakey(key, work)

    def meta(i):
        r = subprocess.run([vgm, "-m", "-s", str(i), str(acb_file)], capture_output=True, text=True)
        info = {}
        for ln in r.stdout.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                info[k.strip()] = v.strip()
        return info

    first = meta(1)
    count = int(first.get("stream count", "1"))
    n = max(1, min(count, workers or default_workers()))
    with ThreadPoolExecutor(n) as ex:
        infos = [first] + list(ex.map(meta, range(2, count + 1)))
    # file names in stream order: a name repeated within the sheet gets `<name>_<stream>`
    plan: list[tuple[int, dict, str, str]] = []
    seen: set[str] = set()
    stems: set[str] = set()
    for i, info in enumerate(infos, 1):
        cue = info.get("stream name")
        if not cue:
            raise RuntimeError(f"{name} stream {i}: no cue name")
        stem = cue if cue not in seen else f"{cue}_{i}"             # layered waveforms share a name
        if stem in stems:
            raise RuntimeError(f"{name} stream {i}: file name {stem} already used")
        seen.add(cue)
        stems.add(stem)
        plan.append((i, info, cue, stem))

    def one(item) -> Path:
        i, _, _, stem = item
        wav = work / f"{stem}.wav"
        # -i: one pass of the stream (no loop repetition / fade); loop points
        # go to the manifest so a player can loop sample-exactly.
        subprocess.run([vgm, "-i", "-s", str(i), "-o", str(wav), str(acb_file)],
                       check=True, capture_output=True)
        if also is not None:
            r = subprocess.run([ffmpeg, *encode_args(wav, out_dir / f"{stem}{also[0]}", also[1])],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg {also[0]} failed on {name} stream {i}: {r.stderr[-2000:]}")
        if fmt == "wav":
            dst = out_dir / wav.name
            shutil.move(str(wav), dst)
        else:
            dst = out_dir / f"{stem}.{fmt}"
            codec = {"flac": ["-c:a", "flac", "-compression_level", str(int(flac_level))],
                     "ogg": ["-c:a", "libvorbis", "-q:a", "5"]}[fmt]
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), *codec, str(dst)],
                           check=True)
            wav.unlink()
        return dst

    with ThreadPoolExecutor(n) as ex:
        dsts = list(ex.map(one, plan))
    result: dict[str, Path] = {}
    manifest: dict[str, dict] = {}
    streams: list[dict] = []
    for (i, info, cue, _), dst in zip(plan, dsts):
        entry = {"file": dst.name, "sampleRate": _samples(info.get("sample rate")),
                 "channels": _samples(info.get("channels")),
                 "samples": _samples(info.get("stream total samples"))}
        if "loop start" in info:
            entry["loopStart"] = _samples(info["loop start"])
            entry["loopEnd"] = _samples(info["loop end"])
        streams.append({"stream": i, "name": cue, **entry})
        if cue not in manifest:
            manifest[cue] = entry
            result[cue] = dst
    write_json(out_dir / "cues.json", manifest, ensure_ascii=True)
    write_json(out_dir / "streams.json", streams, ensure_ascii=True)
    shutil.rmtree(work)
    names = sorted({d.name for d in dsts} | ({f"{p[3]}{also[0]}" for p in plan} if also else set()))
    return Decoded(result, manifest, streams, names)


def _replay(packed: bytes, out_dir: Path) -> dict[str, Path]:
    """Write a cached decode into out_dir (the files, cues.json, streams.json as decode writes them)."""
    meta, blobs = cache.unpack(packed)
    m = json.loads(meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in zip(m["files"], blobs):
        (out_dir / name).write_bytes(data)
    (out_dir / "cues.json").write_text(m["cues"], encoding="utf-8", newline="\n")
    (out_dir / "streams.json").write_text(m["streams"], encoding="utf-8", newline="\n")
    return {k: out_dir / v for k, v in m["result"].items()}


def _samples(v: str | None) -> int | None:
    return int(v.split()[0]) if v else None
