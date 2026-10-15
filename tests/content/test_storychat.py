"""storychat: the chat document from synthetic shard TextAssets."""

import json

from nnnotes.content import storychat

ROWS = {
    "text": [{"_id": 1, "_japanese": "こんにちは", "_english": "Hello"}],
    "sounds": [{"_id": 2, "_cueName": "v_001"}],
    "cueSheets": [{"_id": 3, "_name": "AdvSe_Common"}],
}


class FakeObjects:
    """An object table: {oid: (info, {role: value})} with catalog keys {key: [oid]}."""

    def __init__(self, objs: dict, keys: dict):
        self._objs = objs
        self._keys = keys

    def __contains__(self, oid):
        return oid in self._objs

    def key(self, key):
        return list(self._keys.get(key, []))

    def info(self, oid):
        return self._objs[oid][0]

    def bytes(self, oid, role):
        return self._objs[oid][1][role]


def shard(rows: list[dict]) -> bytes:
    return json.dumps({"_header": [], "_allData": rows}).encode("utf-8")


def table(shards: tuple[str, ...]) -> FakeObjects:
    """A table holding one TextAsset per named shard of ROWS, keyed by its catalog key."""
    objs: dict = {}
    keys: dict = {}
    for name in shards:
        oid = f"shard_{name}"
        objs[oid] = ({"class": "TextAsset", "files": {"data": {}}}, {"data": shard(ROWS[name])})
        keys[storychat.SHARDS[name]] = [oid]
    return FakeObjects(objs, keys)


def test_keys_are_the_sorted_shard_keys():
    assert storychat.keys() == sorted(storychat.SHARDS.values())
    assert len(storychat.keys()) == 3


def test_chat_document_holds_the_rows_of_all_three_shards():
    doc, keep, roles = storychat.chat(table(("text", "sounds", "cueSheets")))
    assert doc == {"format": storychat.CHAT_FORMAT, **ROWS}
    assert keep == set()
    assert roles == {}


def test_a_missing_shard_gives_no_rows():
    doc, _, _ = storychat.chat(table(("text", "cueSheets")))
    assert doc == {"format": storychat.CHAT_FORMAT, "text": ROWS["text"], "sounds": [], "cueSheets": ROWS["cueSheets"]}
