"""Story episode (ADV) documents from a product's object table and master rows.

The episode logic is `adv.build`. This module reads its inputs from a product: the episode's command list and shard
TextAssets from the object table (`Objects`), and the MasterAdv, MasterText and MasterAdvChat rows from master tables.
Every address is taken as present, since the product names the addresses it is asked for.

`episode_json` gives episode.json; `story` gives story.json, which names the chats, the effect instances and the
products the story plays with (`parts`).
"""

from __future__ import annotations

from . import adv
from .adv import LANGS, PLAYER_SETTINGS_KEY, SHARD_SUFFIXES, AdvExtract, build
from .prefabs import Objects, has_json

TABLES = ("MasterAdv", "MasterText", "MasterAdvChat")
STORY_FORMAT = 1  # story.json "format"
ASSET_KINDS = (
    "chaticon",
    "chatstamp",
    "chatwindow",
    "effect",
    "frame",
    "posteffect",
    "stage",
    "still",
    "timeline",
    "transition",
)


def episode_key(asset: str) -> str:
    """The catalog key of the episode whose `_advEpisodeAsset` is `asset`."""
    return f"Adv/Episode/{asset}/{asset}"


def keys(asset: str) -> list[str]:
    """The catalog keys of an episode: its key, the four shard keys and the AdvPlayerSettings key, sorted."""
    base = episode_key(asset)
    return sorted([base, *(f"{base}{suf}" for suf in SHARD_SUFFIXES), PLAYER_SETTINGS_KEY])


def episode_record(tables: dict, adv_id: int) -> dict:
    """The rows of episode `adv_id` from master tables {name: rows}: its MasterAdv row, its title's MasterText row (or
    none), and every MasterAdvChat row sorted by id. KeyError when the MasterAdv row or the title text is missing."""
    rows = [r for r in tables["MasterAdv"] if r["_id"] == adv_id]
    if not rows:
        raise KeyError(f"MasterAdv has no row {adv_id}")
    row = rows[0]
    title_id = row["_titleTextId"]
    titles = [r for r in tables["MasterText"] if r["_id"] == title_id] if title_id else []
    if title_id and not titles:
        raise KeyError(f"MasterText has no row {title_id}")
    return {
        "MasterAdv": [row],
        "MasterText": titles[:1],
        "MasterAdvChat": sorted(tables["MasterAdvChat"], key=lambda r: r["_id"]),
    }


def _single(key: str, what: str, hits: list[str]) -> str:
    if len(hits) != 1:
        raise ValueError(f"{key}: {len(hits)} {what}, expected one")
    return hits[0]


def _collection(objects: Objects, key: str) -> list[dict]:
    """The `Collection` of the episode's MonoBehaviour among the objects of `key`."""
    hits = [
        o
        for o in objects.key(key)
        if has_json(objects, o) and objects.info(o).get("class") == "MonoBehaviour" and "Collection" in objects.json(o)
    ]
    return objects.json(_single(key, "MonoBehaviours with a Collection", hits))["Collection"]


def shard_text(objects: Objects, key: str) -> str | None:
    """The text of the TextAsset of `key` (its `data` file, UTF-8); None when the key has no TextAsset."""
    hits = [o for o in objects.key(key) if o in objects and objects.info(o).get("class") == "TextAsset"]
    if not hits:
        return None
    return objects.bytes(_single(key, "TextAssets", hits), "data").decode("utf-8")


def _default_transition(objects: Objects) -> str:
    """AdvPlayerSettings' default transition address."""
    hits = [
        o
        for o in objects.key(PLAYER_SETTINGS_KEY)
        if has_json(objects, o) and "_defaultTransitionAssetAddress" in objects.json(o)
    ]
    oid = _single(PLAYER_SETTINGS_KEY, "MonoBehaviours with a _defaultTransitionAssetAddress", hits)
    return objects.json(oid)["_defaultTransitionAssetAddress"]


def _title(row: dict, texts: list[dict]) -> dict | None:
    title_id = row["_titleTextId"]
    if not title_id:
        return None
    hits = [r for r in texts if r["_id"] == title_id]
    if not hits:
        raise KeyError(f"MasterText has no row {title_id}")
    return {lang[1:]: hits[0][lang] for lang in LANGS}


def _always(address: str) -> bool:
    return True


def episode(objects: Objects, record: dict, *, has=_always) -> AdvExtract:
    """The episode of `record` (episode_record) from the object table: pass the result to `adv.to_json`."""
    row = record["MasterAdv"][0]
    base = episode_key(row["_advEpisodeAsset"])
    coll = _collection(objects, base)
    shards = {suf: shard_text(objects, f"{base}{suf}") for suf in SHARD_SUFFIXES}
    chats = {r["_id"]: r for r in record["MasterAdvChat"]}

    def chat(chat_id: int) -> dict:
        if chat_id not in chats:
            raise KeyError(f"MasterAdvChat has no row {chat_id}")
        return chats[chat_id]

    return build(
        row,
        coll,
        shards,
        has,
        chat,
        lambda: _default_transition(objects),
        _title(row, record["MasterText"]),
    )


def episode_json(x: AdvExtract) -> dict:
    """`adv.to_json(x)` with each resource reduced to its kind and address. The product names the addresses it is
    asked for, so a resource carries no presence flag."""
    doc = adv.to_json(x)
    doc["resources"] = [{"kind": r["kind"], "address": r["address"]} for r in doc["resources"]]
    return doc


def lip_synced(episode: dict) -> bool:
    """A command of the episode is a voice with a target and voice cues, and neither IgnoreData nor IgnoreLipSync is
    set. Such a voice can reach the CRI Lips analysis; whether it does depends on the MotionSync of its models."""
    return any(
        c["cmd"] in ("Talk", "Voice")
        and c.get("TargetName")
        and c.get("VoiceIDs")
        and not c.get("IgnoreData")
        and not c.get("IgnoreLipSync")
        for c in episode["commands"]
    )


def chat_ids(episode: dict) -> list[int]:
    """The distinct non-zero TargetChatID values of the episode's commands, sorted."""
    return sorted({c["TargetChatID"] for c in episode["commands"] if c.get("TargetChatID")})


def parts(episode: dict) -> dict:
    """What the story plays with, from episode.json: the addresses of the ASSET_KINDS resources, the addresses of the
    live2d models, the cue sheets of the episode, the addresses of its videos, whether it shows chat and whether a
    voice reaches the CRI Lips analysis. Talk windows are not listed."""
    resources = episode["resources"]
    return {
        "assets": sorted(r["address"] for r in resources if r["kind"] in ASSET_KINDS),
        "models": sorted(r["address"] for r in resources if r["kind"] == "live2d"),
        "sounds": sorted({cs["_cueSheetName"] for cs in episode["cuesheets"].values() if cs.get("_cueSheetName")}),
        "videos": sorted(r["address"] for r in resources if r["kind"] == "video"),
        "chat": bool(chat_ids(episode)),
        "crilips": lip_synced(episode),
    }


def story(episode: dict, record: dict, paths: dict) -> dict:
    """The story.json document: the MasterAdvChat row of each chat id of the episode (KeyError when there is none), the
    effect instances (TargetName -> "Adv/Effect/" + TargetAssetName) and `parts`. `paths` are the manifest paths of the
    products the story plays with, given by the caller: {"scene", "assets": {address: path}, "models": {address: path},
    "sounds": {cue sheet: path}, "videos": {address: path}, "chat", "crilips"}."""
    chats = {}
    for chat_id in chat_ids(episode):
        hits = [r for r in record["MasterAdvChat"] if r["_id"] == chat_id]
        if not hits:
            raise KeyError(f"MasterAdvChat has no row {chat_id}")
        chats[str(chat_id)] = hits[0]
    prefix = adv.RESOURCE_PREFIX["Effect"][1]
    return {
        "format": STORY_FORMAT,
        "advId": episode["advId"],
        "episode": "episode.json",
        "chats": chats,
        "effects": {name: prefix + asset for name, asset in adv.effect_instances(episode["commands"]).items()},
        "parts": paths,
    }
