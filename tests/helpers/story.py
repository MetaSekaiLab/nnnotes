"""Synthetic story data: the master tables and the object table of a small ADV episode, and the chat shards."""

import json

from nnnotes.content import storychat, storyepisode
from nnnotes.content.adv import PLAYER_SETTINGS_KEY
from tests.helpers import livenotes as ln

ASSET = "ep_test"
BASE = storyepisode.episode_key(ASSET)
ALL_LANGS = ("_japanese", "_english", "_traditionalChinese", "_simplifiedChinese", "_korean")
CHAT_KEYS = storychat.SHARDS

COLLECTION = [
    {"Index": 0, "Command": 2, "AdvTextID": 101, "VoiceIDs": [201, 202], "Speaker": 3, "Volume": 0.0, "Note": ""},
    {"Index": 1, "Command": 25, "TargetAssetName": "st_a/st_a"},
    {"Index": 2, "Command": 26, "VideoID": 41},
    {"Index": 3, "Command": 36, "TargetChatID": 7},
    {"Index": 4, "Command": 6, "Duration": 1.0},
]


def shard(rows: list[dict]) -> bytes:
    """The bytes of a shard TextAsset holding `rows`."""
    return json.dumps({"_header": [], "_allData": rows}).encode("utf-8")


def master_tables() -> dict:
    """Master tables {name: rows}: episode 1 (title text 9001, chats 7 and 9), episode 2 (another asset) and episode
    10 (the same asset as episode 1, no title)."""
    title = {"_id": "9001", **{lang: f"title{lang}" for lang in ALL_LANGS}}
    return {
        "MasterAdv": [
            {"_id": 1, "_advEpisodeAsset": ASSET, "_titleTextId": "9001"},
            {"_id": 2, "_advEpisodeAsset": "other", "_titleTextId": ""},
            {"_id": 10, "_advEpisodeAsset": ASSET, "_titleTextId": ""},
        ],
        "MasterText": [title, {"_id": "9002", **{lang: "x" for lang in ALL_LANGS}}],
        "MasterAdvChat": [
            {"_id": 9, "_chatWindowAssetName": "win_9", "_chatIconAssetName": ""},
            {"_id": 7, "_chatWindowAssetName": "win_7", "_chatIconAssetName": "icon_7"},
        ],
    }


def master_dir(tmp_path, tables: dict | None = None):
    """A master directory holding each table as <Table>.json ({"_allData": rows})."""
    md = tmp_path / "master"
    md.mkdir(parents=True, exist_ok=True)
    for name, rows in (tables if tables is not None else master_tables()).items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return md


def episode_table(depot) -> dict:
    """The object table of episode ASSET: its command list, its four shards (the cue sheet shard is absent) and the
    AdvPlayerSettings, with the catalog keys the episode reads."""
    objs = {
        "episode": ("MonoBehaviour", "ep", {"json": {"$script": "App|AdvEpisodeCollection", "Collection": COLLECTION}}),
        "text": ("TextAsset", "text", {"data": shard([{"_id": 101, "_japanese": "こんにちは", "_english": "Hello"}])}),
        "sound": (
            "TextAsset",
            "sound",
            {"data": shard([{"_id": 201, "_cueName": "v_001"}, {"_id": 202, "_cueName": "v_002"}])},
        ),
        "video": ("TextAsset", "video", {"data": shard([{"_id": 41, "_assetName": "adv/movie_a/movie_a"}])}),
        "settings": (
            "MonoBehaviour",
            "settings",
            {
                "json": {
                    "$script": "App|AdvPlayerSettings",
                    "_defaultTransitionAssetAddress": "adv_transition_default/x",
                }
            },
        ),
    }
    keys = {
        BASE: ["episode"],
        f"{BASE}-Text": ["text"],
        f"{BASE}-Sound": ["sound"],
        f"{BASE}-SoundCueSheet": [],
        f"{BASE}-Video": ["video"],
        PLAYER_SETTINGS_KEY: ["settings"],
    }
    return ln.table(depot, keys, objs)


def chat_table(depot, present: tuple[str, ...] = tuple(CHAT_KEYS)) -> dict:
    """The object table of the chat shards: each shard named in `present` (text, sounds, cueSheets) has a TextAsset
    with its rows; the other shard keys are catalog keys without an object."""
    rows = {
        "text": [{"_id": 1, "_text": "hello"}],
        "sounds": [{"_id": 2, "_cueName": "chat_se"}],
        "cueSheets": [{"_id": 3, "_cueSheetName": "chat_sheet"}],
    }
    objs, keys = {}, {}
    for name, key in CHAT_KEYS.items():
        keys[key] = []
        if name in present:
            oid = f"{name}-shard"
            objs[oid] = ("TextAsset", name, {"data": shard(rows[name])})
            keys[key] = [oid]
    return ln.table(depot, keys, objs)
