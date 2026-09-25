"""ADV (2D story) extractor: episode script + text/sound/video shards -> episode.json.

The episode `.asset` is an AdvEpisodeCollection MonoBehaviour whose `Collection`
is the flat command list (UnityPy read_typetree decodes it directly). The
`-Text/-Sound/-SoundCueSheet/-Video` sibling keys are plain-JSON TextAssets.
Output is a self-contained JSON a viewer can play without any game code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from . import unity

# AdvCommand enum; values 0-69, 8 and 22 unused.
COMMAND = {
    0: "In", 1: "Out", 2: "Talk", 3: "Delay", 4: "Shake", 5: "FadeOut",
    6: "FadeIn", 7: "Focus", 9: "Forward", 10: "Back", 11: "Flash",
    12: "Brightness", 13: "MoveToRight", 14: "MoveToLeft", 15: "Bgm",
    16: "SoundVolume", 17: "Expression", 18: "Pause", 19: "Resume",
    20: "Location", 21: "Motion", 23: "Character", 24: "Costume", 25: "Stage",
    26: "Movie", 27: "Clip", 28: "Subtitles", 29: "Wait", 30: "Still",
    31: "Se", 32: "Angle", 33: "Pan", 34: "Tilt", 35: "TalkWindow",
    36: "ChatWindow", 37: "ChatTalk", 38: "ChatStamp", 39: "ChatRead",
    40: "ChoiceSet", 41: "ChoiceShow", 42: "GoTo", 43: "PostEffect",
    44: "Frame", 45: "Timeline", 46: "Look", 47: "Pedestal", 48: "Track",
    49: "DoF", 50: "Role", 51: "CameraShake", 52: "Voice", 53: "Zoom",
    54: "Effect", 55: "Alpha", 56: "ForceAuto", 57: "StageEnv",
    58: "RimLight", 59: "CancelDelay", 60: "MoveToUp", 61: "MoveToDown",
    62: "MoveToForward", 63: "MoveToBack", 64: "MoveToDirection",
    65: "ChatTyping", 66: "LookTarget", 67: "PanV2", 68: "MotionLoop",
    69: "EyeBlink",
}

# command name -> (resource kind, catalog-key prefix) using TargetAssetName
RESOURCE_PREFIX = {
    "Character": ("live2d", "Character/Live2D/"),
    "Costume": ("live2d", "Character/Live2D/"),
    "Stage": ("stage", "Adv/Stage/"),
    "Still": ("still", "Adv/Still/"),
    "Frame": ("frame", "Adv/Frame/"),
    "Effect": ("effect", "Adv/Effect/"),
    "PostEffect": ("posteffect", "Adv/PostEffect/"),
    "Timeline": ("timeline", "Adv/Timeline/"),
    "ChatWindow": ("chat", "Adv/Chat/"),
    "ChatStamp": ("chatstamp", "Adv/Chat/"),
}

LANGS = ("_japanese", "_english", "_traditionalChinese", "_simplifiedChinese", "_korean")


@dataclass
class AdvExtract:
    adv_id: int
    asset: str
    commands: list[dict]
    text: dict
    sounds: dict
    cuesheets: dict
    videos: dict
    resources: list[dict]
    master: dict          # MasterAdv row: playback mode, title text id
    title: dict | None    # MasterText lines of the title (embedded master)


def _master_row(adv_id: int, master_dir: Path) -> dict:
    for r in json.loads((master_dir / "MasterAdv.json").read_text(encoding="utf-8"))["_allData"]:
        if r["_id"] == adv_id:
            return r
    raise KeyError(f"MasterAdv has no row {adv_id}")


def _master_text(text_id: str, master_dir: Path) -> dict:
    for r in json.loads((master_dir / "MasterText.json").read_text(encoding="utf-8"))["_allData"]:
        if r["_id"] == text_id:
            return {lang[1:]: r[lang] for lang in LANGS}
    raise KeyError(f"MasterText has no row {text_id}")


def _text_of(cat: Catalog, key: str) -> str | None:
    if not cat.has(key):
        return None
    for p in cat.fetch_key(key):
        ta = unity.textassets(unity.load(p))
        if ta:
            return next(iter(ta.values()))
    return None


def extract(cat: Catalog, master_dir: Path, adv_id: int) -> AdvExtract:
    row = _master_row(adv_id, master_dir)
    a = row["_advEpisodeAsset"]
    base = f"Adv/Episode/{a}/{a}"

    # 1) episode command list (MonoBehaviour typetree)
    coll = None
    for p in cat.fetch_key(base):
        tt = unity.first_mono(unity.load(p), must_have="Collection")
        if tt:
            coll = tt["Collection"]
            break
    if coll is None:
        raise RuntimeError(f"episode Collection not found for {a}")

    # 2) shards
    def shard(suf):
        t = _text_of(cat, f"{base}{suf}")
        return unity.parse_shard(t)["rows"] if t else []

    text_rows = shard("-Text")
    sound_rows = shard("-Sound")
    cue_rows = shard("-SoundCueSheet")
    video_rows = shard("-Video")
    text_idx = unity.shard_index(text_rows)
    sound_idx = unity.shard_index(sound_rows)

    # 3) enrich commands + collect resource closure
    resources: dict[tuple, dict] = {}
    out_cmds = []
    for c in coll:
        name = COMMAND.get(c["Command"], f"Cmd{c['Command']}")
        item = {"i": c["Index"], "cmd": name, "raw": c["Command"]}
        # Keep every non-default field verbatim (speaker text ids/colors,
        # camera distance, motion fade, parameters...), dropping only empties.
        for k, v in c.items():
            if k in ("Index", "Command"):
                continue
            if v in (None, "", 0, 0.0) or v == []:
                continue
            item[k] = v
        if c.get("VoiceIDs"):
            item["voiceCues"] = [
                sound_idx[v].get("_cueName") for v in c["VoiceIDs"] if v in sound_idx
            ]
        tid = c.get("AdvTextID")
        if tid and tid in text_idx:
            r = text_idx[tid]
            item["lines"] = {lang[1:]: r.get(lang, "") for lang in LANGS}
        tgt = c.get("TargetAssetName") or ""
        if name in RESOURCE_PREFIX and tgt:
            kind, prefix = RESOURCE_PREFIX[name]
            addr = _addr(prefix, tgt)
            resources.setdefault((kind, addr), {
                "kind": kind, "address": addr,
                "present": cat.has(addr),
            })
        out_cmds.append(item)

    return AdvExtract(
        adv_id=adv_id, asset=a, commands=out_cmds,
        text={r["_id"]: {lang[1:]: r.get(lang, "") for lang in LANGS} for r in text_rows if "_id" in r},
        sounds={r["_id"]: r for r in sound_rows if "_id" in r},
        cuesheets={r["_id"]: r for r in cue_rows if "_id" in r},
        videos={r["_id"]: r for r in video_rows if "_id" in r},
        resources=sorted(resources.values(), key=lambda d: (d["kind"], d["address"])),
        master=row,
        title=_master_text(row["_titleTextId"], master_dir) if row["_titleTextId"] else None,
    )


def _addr(prefix: str, target: str) -> str:
    # TargetAssetName like "003_adv/adv_live2d_rana_.../model/adv_live2d_rana_..."
    # or a Still/Stage name; the catalog key is prefix + target.
    return prefix + target


def to_json(x: AdvExtract) -> dict:
    return {
        "advId": x.adv_id, "asset": x.asset,
        "commandCount": len(x.commands),
        "commands": x.commands,
        "text": x.text, "sounds": x.sounds,
        "cuesheets": x.cuesheets, "videos": x.videos,
        "resources": x.resources,
        "master": x.master, "title": x.title,
    }
