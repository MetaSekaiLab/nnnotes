"""storyepisode: episode documents from a synthetic object table and master rows."""

import json

import pytest

from nnnotes.content import adv, storyepisode

ASSET = "ep_test"
BASE = f"Adv/Episode/{ASSET}/{ASSET}"
ALL_LANGS = ("_japanese", "_english", "_traditionalChinese", "_simplifiedChinese", "_korean")


class FakeObjects:
    """An object table: {oid: (info, {role: value})} with catalog keys {key: [oid]}."""

    def __init__(self, objs: dict, keys: dict):
        self._objs = objs
        self._keys = keys
        self.roots: list[str] = []

    def __contains__(self, oid):
        return oid in self._objs

    def ids(self, cls=None):
        return sorted(o for o, (info, _) in self._objs.items() if cls is None or info["class"] == cls)

    def key(self, key):
        return list(self._keys.get(key, []))

    def info(self, oid):
        return self._objs[oid][0]

    def bytes(self, oid, role):
        return self._objs[oid][1][role]

    def json(self, oid, role="json"):
        return self._objs[oid][1][role]


def put(objs: dict, oid: str, cls: str, **files) -> None:
    objs[oid] = ({"class": cls, "files": {role: {} for role in files}}, files)


def shard(rows: list[dict]) -> bytes:
    return json.dumps({"_header": [], "_allData": rows}).encode("utf-8")


def table() -> FakeObjects:
    objs: dict = {}
    collection = [
        {"Index": 0, "Command": 2, "AdvTextID": 101, "VoiceIDs": [201, 202], "Speaker": 3, "Volume": 0.0, "Note": ""},
        {"Index": 1, "Command": 25, "TargetAssetName": "st_a/st_a"},
        {"Index": 2, "Command": 26, "VideoID": 41},
        {"Index": 3, "Command": 36, "TargetChatID": 7},
        {"Index": 4, "Command": 6, "Duration": 1.0},
    ]
    put(objs, "episode", "MonoBehaviour", json={"$script": "App|AdvEpisodeCollection", "Collection": collection})
    put(
        objs,
        "text",
        "TextAsset",
        data=shard([{"_id": 101, "_japanese": "こんにちは", "_english": "Hello"}]),
    )
    put(
        objs,
        "sound",
        "TextAsset",
        data=shard([{"_id": 201, "_cueName": "v_001"}, {"_id": 202, "_cueName": "v_002"}]),
    )
    put(objs, "video", "TextAsset", data=shard([{"_id": 41, "_assetName": "adv/movie_a/movie_a"}]))
    put(
        objs,
        "settings",
        "MonoBehaviour",
        json={"$script": "App|AdvPlayerSettings", "_defaultTransitionAssetAddress": "adv_transition_default/x"},
    )
    keys = {
        BASE: ["episode"],
        f"{BASE}-Text": ["text"],
        f"{BASE}-Sound": ["sound"],
        f"{BASE}-Video": ["video"],
        adv.PLAYER_SETTINGS_KEY: ["settings"],
    }
    return FakeObjects(objs, keys)


def master_tables() -> dict:
    title = {"_id": "9001", **{lang: f"title{lang}" for lang in ALL_LANGS}}
    return {
        "MasterAdv": [
            {"_id": 1, "_advEpisodeAsset": ASSET, "_titleTextId": "9001"},
            {"_id": 2, "_advEpisodeAsset": "other", "_titleTextId": ""},
        ],
        "MasterText": [title, {"_id": "9002", **{lang: "x" for lang in ALL_LANGS}}],
        "MasterAdvChat": [
            {"_id": 9, "_chatWindowAssetName": "win_9", "_chatIconAssetName": ""},
            {"_id": 7, "_chatWindowAssetName": "win_7", "_chatIconAssetName": "icon_7"},
        ],
    }


def test_keys_are_sorted_and_cover_the_shards_and_player_settings():
    assert storyepisode.keys(ASSET) == sorted(
        [
            BASE,
            f"{BASE}-Text",
            f"{BASE}-Sound",
            f"{BASE}-SoundCueSheet",
            f"{BASE}-Video",
            adv.PLAYER_SETTINGS_KEY,
        ]
    )


def test_episode_document_from_the_object_table():
    record = storyepisode.episode_record(master_tables(), 1)
    doc = adv.to_json(storyepisode.episode(table(), record))
    lines_en = {
        "japanese": "こんにちは",
        "english": "Hello",
        "traditionalChinese": "",
        "simplifiedChinese": "",
        "korean": "",
    }
    title = {lang[1:]: f"title{lang}" for lang in ALL_LANGS}
    assert doc == {
        "advId": 1,
        "asset": ASSET,
        "commandCount": 5,
        "commands": [
            {
                "i": 0,
                "cmd": "Talk",
                "raw": 2,
                "AdvTextID": 101,
                "VoiceIDs": [201, 202],
                "Speaker": 3,
                "voiceCues": ["v_001", "v_002"],
                "lines": lines_en,
            },
            {"i": 1, "cmd": "Stage", "raw": 25, "TargetAssetName": "st_a/st_a"},
            {"i": 2, "cmd": "Movie", "raw": 26, "VideoID": 41},
            {"i": 3, "cmd": "ChatWindow", "raw": 36, "TargetChatID": 7},
            {"i": 4, "cmd": "FadeIn", "raw": 6, "Duration": 1.0},
        ],
        "text": {101: lines_en},
        "sounds": {201: {"_id": 201, "_cueName": "v_001"}, 202: {"_id": 202, "_cueName": "v_002"}},
        "cuesheets": {},
        "videos": {41: {"_id": 41, "_assetName": "adv/movie_a/movie_a"}},
        "resources": [
            {"kind": "chatwindow", "address": "Adv/Chat/Prefabs/win_7", "present": True},
            {"kind": "stage", "address": "Adv/Stage/st_a/st_a", "present": True},
            {"kind": "transition", "address": "Adv/Transition/adv_transition_default/x", "present": True},
            {"kind": "video", "address": "Cri/Video/adv/movie_a/movie_a", "present": True},
        ],
        "master": {"_id": 1, "_advEpisodeAsset": ASSET, "_titleTextId": "9001"},
        "title": title,
    }


def test_default_transition_is_read_only_when_a_fade_needs_it():
    tables = master_tables()
    record = storyepisode.episode_record(tables, 1)
    objs = table()
    objs._keys.pop(adv.PLAYER_SETTINGS_KEY)
    commands = [{"Index": 0, "Command": 25, "TargetAssetName": "st_a/st_a"}]
    objs._objs["episode"][1]["json"]["Collection"] = commands
    doc = adv.to_json(storyepisode.episode(objs, record))
    assert [r["address"] for r in doc["resources"]] == ["Adv/Stage/st_a/st_a"]
    objs._objs["episode"][1]["json"]["Collection"] = [{"Index": 0, "Command": 6}]
    with pytest.raises(ValueError, match="_defaultTransitionAssetAddress"):
        storyepisode.episode(objs, record)


def test_shard_without_a_text_asset_gives_no_rows():
    objs = table()
    objs._keys.pop(f"{BASE}-Video")
    objs._objs["episode"][1]["json"]["Collection"] = [{"Index": 0, "Command": 26, "VideoID": 41}]
    doc = adv.to_json(storyepisode.episode(objs, storyepisode.episode_record(master_tables(), 1)))
    assert doc["videos"] == {} and doc["resources"] == []


def test_episode_needs_one_collection_monobehaviour():
    objs = table()
    del objs._objs["episode"][1]["json"]["Collection"]
    with pytest.raises(ValueError, match="Collection"):
        storyepisode.episode(objs, storyepisode.episode_record(master_tables(), 1))


def test_episode_record_selects_rows_and_reports_missing_ones():
    tables = master_tables()
    record = storyepisode.episode_record(tables, 1)
    assert record["MasterAdv"] == [tables["MasterAdv"][0]]
    assert record["MasterText"] == [tables["MasterText"][0]]
    assert [r["_id"] for r in record["MasterAdvChat"]] == [7, 9]
    assert storyepisode.episode_record(tables, 2)["MasterText"] == []
    with pytest.raises(KeyError, match="MasterAdv"):
        storyepisode.episode_record(tables, 3)
    broken = master_tables()
    broken["MasterText"] = []
    with pytest.raises(KeyError, match="MasterText"):
        storyepisode.episode_record(broken, 1)
