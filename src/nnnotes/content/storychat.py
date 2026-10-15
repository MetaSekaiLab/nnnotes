"""The chat product of a story: the rows of the shared chat shards every chat of a story reads (text, sounds and cue
sheets of chat messages). chat.json carries the rows, so the product keeps no objects.
"""

from __future__ import annotations

from .adv import parse_shard
from .prefabs import Objects
from .storyepisode import shard_text

__all__ = ["CHAT_FORMAT", "SHARDS", "chat", "keys"]

CHAT_FORMAT = 1  # chat.json "format"
SHARDS = {
    "text": "Adv/Chat/AdvChat-Text",
    "sounds": "Adv/Chat/AdvChat-Sound",
    "cueSheets": "Adv/Chat/AdvChat-SoundCueSheet",
}


def keys() -> list[str]:
    """The catalog keys of the chat shards, sorted."""
    return sorted(SHARDS.values())


def chat(objects: Objects) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(chat.json, the object ids the player reads, {object id: the roles of its files it reads}) of the chat product.
    Each shard's rows are [] when its key has no TextAsset; the product keeps no objects, so the set and the roles
    are empty."""
    doc: dict = {"format": CHAT_FORMAT}
    for name, key in SHARDS.items():
        text = shard_text(objects, key)
        doc[name] = parse_shard(text)["rows"] if text else []
    return doc, set(), {}
