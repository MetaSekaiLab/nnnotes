"""CRI ADX2 cue sheets -> per-cue audio files.

An addressable key `Cri/Sound/<cueSheet>` holds a cue sheet in one of three layouts:

  raw CDN data   the key depends on a small bundle holding the CriWare.Assets
                 MonoBehaviour and on the cue sheet's raw data on the CDN
                 (`cri_assets_cri/sound/<cueSheet>_<hash>`, stored as-is, not
                 bundle-encrypted): an ACB (`@UTF`), optionally with an AWB
                 (`AFS2`) for streamed waveforms (ADV voices, BGM, SE);
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
per name) and `streams.json` (every stream in vgmstream order).

HCA streams are decrypted with the keycode from the game's own boot data (see
crikey.py). Decoding uses vgmstream; flac/ogg encoding uses ffmpeg (`[paths]
vgmstream` / `[paths] ffmpeg`, else found on PATH).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import UnityPy

from .addressables import remote_path
from .catalog import Catalog
from .config import tool
from . import crikey
from .jsonio import write_json

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


def _asset_acb(cue_sheet: str, layout: str, objs, t: dict, impl: dict | None) -> bytes:
    if layout == SPLIT_ACB:
        # Fwk.Sound.SplitAcbLoader.Load: `_chunks` TextAssets joined in order, every byte XORed with one mask byte
        # -> an in-memory ACB (`@UTF`, AWB embedded) passed to CriAtomExAcb.LoadAcbData. Full songs
        # (`Cri/Sound/<X>_<Name>`, `Assets/AddressableResources/Cri/Sound/MusicScore/*.asset`).
        parts = []
        for c in t["_chunks"]:
            s = objs[c["m_PathID"]].read().m_Script
            parts.append(s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s))
        joined = b"".join(parts)
        mask = joined[0] ^ ACB_SIGNATURE[0] if joined else 0
        acb = (np.frombuffer(joined, np.uint8) ^ np.uint8(mask)).tobytes()
    else:
        acb = bytes(impl["data"]["data"])
        if (t.get("awb") or {}).get("m_PathID"):
            raise NotImplementedError(f"{cue_sheet}: embedded ACB with an external AWB reference")
    if acb[:4] != ACB_SIGNATURE:
        raise RuntimeError(f"{cue_sheet}: {layout} data is not an ACB (@UTF): {acb[:4]!r}")
    return acb


def acb_data(cat: Catalog, cue_sheet: str) -> tuple[dict[str, bytes], str]:
    """({'acb': bytes, 'awb': bytes?}, layout) of a cue sheet: SplitAcbData, then the embedded ACB, then the raw
    CDN dependencies (RAW_ACB)."""
    asset = _bundle_asset(cat, cue_sheet)
    if asset is not None:
        return {"acb": _asset_acb(cue_sheet, *asset)}, asset[0]
    return {k: p.read_bytes() for k, p in raw_files(cat, cue_sheet).items()}, RAW_ACB


def layout(cat: Catalog, cue_sheet: str) -> str:
    """Which layout a cue sheet is stored in (SPLIT_ACB, EMBEDDED_ACB or RAW_ACB), without extracting it."""
    asset = _bundle_asset(cat, cue_sheet)
    return asset[0] if asset is not None else RAW_ACB


def decode(cat: Catalog, cue_sheet: str, out_dir: Path, key: int | None = None,
           fmt: str = "flac") -> dict[str, Path]:
    """Decode every stream of a cue sheet. Returns {streamName: file of its first stream}.

    flac (default) keeps the decoded PCM bit-exact; ogg re-encodes (lossy).
    """
    vgm = tool("vgmstream", "vgmstream-cli")
    ffmpeg = tool("ffmpeg", "ffmpeg") if fmt != "wav" else None
    if key is None:
        if cat.apk is None:
            raise RuntimeError("HCA key: pass key= or open the Catalog with apk=")
        key = crikey.find_key(cat.apk)

    out_dir = Path(out_dir)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    files, _ = acb_data(cat, cue_sheet)
    acb = work / f"{cue_sheet}.acb"
    acb.write_bytes(files["acb"])
    if "awb" in files:                       # vgmstream pairs <name>.acb with <name>.awb
        (work / f"{cue_sheet}.awb").write_bytes(files["awb"])
    crikey.write_hcakey(key, work)

    def meta(i):
        r = subprocess.run([vgm, "-m", "-s", str(i), str(acb)], capture_output=True, text=True)
        info = {}
        for ln in r.stdout.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                info[k.strip()] = v.strip()
        return info

    first = meta(1)
    count = int(first.get("stream count", "1"))
    result: dict[str, Path] = {}
    manifest: dict[str, dict] = {}
    streams: list[dict] = []
    stems: set[str] = set()
    for i in range(1, count + 1):
        info = first if i == 1 else meta(i)
        name = info.get("stream name")
        if not name:
            raise RuntimeError(f"{cue_sheet} stream {i}: no cue name")
        stem = name if name not in manifest else f"{name}_{i}"      # layered waveforms share a name
        if stem in stems:
            raise RuntimeError(f"{cue_sheet} stream {i}: file name {stem} already used")
        stems.add(stem)
        wav = work / f"{stem}.wav"
        # -i: one pass of the stream (no loop repetition / fade); loop points
        # go to the manifest so a player can loop sample-exactly.
        subprocess.run([vgm, "-i", "-s", str(i), "-o", str(wav), str(acb)],
                       check=True, capture_output=True)
        if fmt == "wav":
            dst = out_dir / wav.name
            shutil.move(str(wav), dst)
        else:
            dst = out_dir / f"{stem}.{fmt}"
            codec = {"flac": ["-c:a", "flac", "-compression_level", "12"],
                     "ogg": ["-c:a", "libvorbis", "-q:a", "5"]}[fmt]
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), *codec, str(dst)],
                           check=True)
            wav.unlink()
        entry = {"file": dst.name, "sampleRate": _samples(info.get("sample rate")),
                 "channels": _samples(info.get("channels")),
                 "samples": _samples(info.get("stream total samples"))}
        if "loop start" in info:
            entry["loopStart"] = _samples(info["loop start"])
            entry["loopEnd"] = _samples(info["loop end"])
        streams.append({"stream": i, "name": name, **entry})
        if name not in manifest:
            manifest[name] = entry
            result[name] = dst
    write_json(out_dir / "cues.json", manifest, ensure_ascii=True)
    write_json(out_dir / "streams.json", streams, ensure_ascii=True)
    shutil.rmtree(work)
    return result


def _samples(v: str | None) -> int | None:
    return int(v.split()[0]) if v else None
