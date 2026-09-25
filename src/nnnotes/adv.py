"""ADV (2D story) extractor: episode script + text/sound/video shards -> episode.json.

The episode `.asset` is an AdvEpisodeCollection MonoBehaviour whose `Collection`
is the flat command list (UnityPy read_typetree decodes it directly). The
`-Text/-Sound/-SoundCueSheet/-Video` sibling keys are plain-JSON TextAssets.
Output is a self-contained JSON a viewer can play without any game code.

`resources` is the episode's asset closure as AdvEpisodeResourceLoader.AddEpisodeLoadingTask
builds it row by row (see `closure`); cue sheets are not listed (the -SoundCueSheet shard holds them).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from .config import apk_missing
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

# AdvEpisodeResourceLoader.AddEpisodeLoadingTask, per command: (resource kind, catalog-key prefix) of the asset named
# by TargetAssetName. The address is prefix + name; a blank name loads nothing (GetXxxAssetAddress). Costume rows load
# nothing (the model is the Character row with the same TargetName and TargetAssetIndex).
RESOURCE_PREFIX = {
    "Character": ("live2d", "Character/Live2D/"),
    "Stage": ("stage", "Adv/Stage/"),
    "Still": ("still", "Adv/Still/"),
    "Frame": ("frame", "Adv/Frame/"),
    "Effect": ("effect", "Adv/Effect/"),              # instance keyed by TargetName (LoadParticleEffect)
    "PostEffect": ("posteffect", "Adv/PostEffect/"),
    "Timeline": ("timeline", "Adv/Timeline/"),        # TargetAssetName, else TargetName
    "ChatStamp": ("chatstamp", "Adv/Chat/Stamp/"),
}
# Resources derived from other fields
TRANSITION_PREFIX = "Adv/Transition/"      # FadeOut / FadeIn: TargetAssetName, else the player settings' default
TALK_WINDOW_PREFIX = "UI/Prefab/Parts/Adv/Talk/"   # TalkWindow: TargetAssetName, else DEFAULT_TALK_WINDOW
DEFAULT_TALK_WINDOW = "UIDefaultTalkWindow"
CHAT_WINDOW_PREFIX = "Adv/Chat/Prefabs/"   # ChatWindow: MasterAdvChat[TargetChatID]._chatWindowAssetName
CHAT_ICON_PREFIX = "Adv/Chat/Icon/"        # ChatTalk / ChatStamp: MasterAdvChat[TargetChatID]._chatIconAssetName
VIDEO_ADDRESS = "Cri/Video/{0}"            # Movie / Clip: the -Video row of VideoID, _assetName (VideoAssetHelper)
EMBEDDED_PREFIX = "Emb"                    # AddressableConsts.TryResolveAddressInternal: address, else "Emb" + address
PLAYER_SETTINGS_KEY = "EmbCommon/Adv/Settings/AdvPlayerSettings"
TRANSITION_COMMANDS = ("FadeOut", "FadeIn")
VIDEO_COMMANDS = ("Movie", "Clip")
CHAT_ICON_COMMANDS = ("ChatTalk", "ChatStamp")

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

    # 3) enrich commands
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
        out_cmds.append(item)

    # 4) resource closure
    chats: dict = {}
    default_transition: list[str] = []

    def chat(chat_id: int) -> dict:
        if not chats:
            chats.update({r["_id"]: r for r in _master_table(master_dir, "MasterAdvChat")})
        if chat_id not in chats:
            raise KeyError(f"MasterAdvChat has no row {chat_id}")
        return chats[chat_id]

    def transition() -> str:
        if not default_transition:
            default_transition.append(_default_transition(cat))
        return default_transition[0]

    resources = closure(coll, cat.has, unity.shard_index(video_rows), chat, transition)

    return AdvExtract(
        adv_id=adv_id, asset=a, commands=out_cmds,
        text={r["_id"]: {lang[1:]: r.get(lang, "") for lang in LANGS} for r in text_rows if "_id" in r},
        sounds={r["_id"]: r for r in sound_rows if "_id" in r},
        cuesheets={r["_id"]: r for r in cue_rows if "_id" in r},
        videos={r["_id"]: r for r in video_rows if "_id" in r},
        resources=resources,
        master=row,
        title=_master_text(row["_titleTextId"], master_dir) if row["_titleTextId"] else None,
    )


def _name(v) -> str | None:
    """A name field as GetXxxAssetAddress reads it: None when null, empty or white space, else the value as is."""
    return v if isinstance(v, str) and v.strip() else None


def closure(rows: list[dict], has, videos: dict, chat, default_transition) -> list[dict]:
    """The assets AddEpisodeLoadingTask loads for the episode's rows (raw `Collection` rows, `Command` numbers),
    as [{kind, address, present}] sorted by (kind, address).

    `has(address)`: whether the catalog has the address; `videos`: -Video rows by id; `chat(id)`: the MasterAdvChat
    row of a chat id; `default_transition()`: the player settings' default transition (called only when a
    FadeOut / FadeIn row has no transition name). A row with IgnoreData only registers its Key: it loads nothing.
    An Effect instance is keyed by TargetName: a row without one loads nothing. Talk window prefabs are embedded
    content: the address resolves to the "Emb" key when the catalog does not have the plain one.
    """
    found: dict[tuple, dict] = {}

    def add(kind: str, address: str) -> None:
        found.setdefault((kind, address), {"kind": kind, "address": address, "present": has(address)})

    for c in rows:
        if c.get("IgnoreData"):
            continue
        name = COMMAND.get(c["Command"])
        tgt = _name(c.get("TargetAssetName"))
        if name in RESOURCE_PREFIX:
            kind, prefix = RESOURCE_PREFIX[name]
            if name == "Timeline":
                tgt = tgt or _name(c.get("TargetName"))
            if name == "Effect" and not _name(c.get("TargetName")):
                tgt = None
            if tgt:
                add(kind, prefix + tgt)
        if name in TRANSITION_COMMANDS:
            add("transition", TRANSITION_PREFIX + (tgt or default_transition()))
        elif name == "TalkWindow":
            address = TALK_WINDOW_PREFIX + (tgt or DEFAULT_TALK_WINDOW)
            add("talkwindow", address if has(address) else EMBEDDED_PREFIX + address)
        elif name in VIDEO_COMMANDS and (c.get("VideoID") or 0) > 0:
            # PrepareVideoTask: a VideoID without a -Video row (or without an asset name) loads nothing
            asset = _name((videos.get(c["VideoID"]) or {}).get("_assetName"))
            if asset:
                add("video", VIDEO_ADDRESS.format(asset))
        if (c.get("TargetChatID") or 0) > 0:
            if name == "ChatWindow":
                window = _name(chat(c["TargetChatID"]).get("_chatWindowAssetName"))
                if window:
                    add("chatwindow", CHAT_WINDOW_PREFIX + window)
            elif name in CHAT_ICON_COMMANDS:
                icon = _name(chat(c["TargetChatID"]).get("_chatIconAssetName"))
                if icon:
                    add("chaticon", CHAT_ICON_PREFIX + icon)
    return sorted(found.values(), key=lambda d: (d["kind"], d["address"]))


def _master_table(master_dir: Path, name: str) -> list[dict]:
    return json.loads((Path(master_dir) / f"{name}.json").read_text(encoding="utf-8"))["_allData"]


def _default_transition(cat: Catalog) -> str:
    """AdvPlayerSettings._defaultTransitionAssetAddress (embedded content: the catalog needs the APK's)."""
    if not cat.has(PLAYER_SETTINGS_KEY):
        raise apk_missing("the default ADV transition (AdvPlayerSettings)")
    for p in cat.fetch_key(PLAYER_SETTINGS_KEY):
        tt = unity.first_mono(unity.load(p), must_have="_defaultTransitionAssetAddress")
        if tt:
            return tt["_defaultTransitionAssetAddress"]
    raise RuntimeError(f"{PLAYER_SETTINGS_KEY}: no _defaultTransitionAssetAddress")


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
