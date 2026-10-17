"""Overlay host selection and scene state from synthetic master rows and prefabs."""

import pytest
from jsonschema import Draft202012Validator

from nnnotes import schemas
from nnnotes.content import storyhostproduct as hp
from nnnotes.pipeline import contract, story
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.storyhost import HostStage
from tests.helpers import storyui as sui
from tests.helpers.livenotes import at, comp, node
from tests.pipeline.test_live import run, stored

HOME = {
    "_id": 7,
    "_advId": 3,
    "_advNameTextId": "area",
    "_backgroundAssetPath": "Spot/room/Background/room",
    "_situationAssetPath": "Spot/room/day/day",
}


def tables():
    return {
        "MasterHomeSpot": [HOME],
        "MasterStoryHomeSpotTapTalkEpisode": [{"_id": 11, "_advId": 2, "_spotId": 7, "_characterId": 5}],
        "MasterStoryLiveResultEpisode": [{"_id": 12, "_advId": 4}],
        "MasterText": [{"_id": "area", "_japanese": "A", "_traditionalChinese": "B"}],
    }


def test_episode_host_selects_home_area_tap_or_result():
    t = tables()
    assert hp.episode_host({}, {"_id": 1, "_playbackMode": 0}) is None
    tap = hp.episode_host(t, {"_id": 2, "_playbackMode": 1})
    assert tap["subject"] == "home/7" and tap["talk"] == "tap" and tap["characterId"] == 5
    area = hp.episode_host(t, {"_id": 3, "_playbackMode": 1})
    assert area["title"]["zh-Hant"] == "B" and area["talk"] == "area"
    assert area["spotKey"] == HOME["_situationAssetPath"] and area["roomKey"] == HOME["_backgroundAssetPath"]
    assert hp.episode_host(t, {"_id": 4, "_playbackMode": 1})["subject"] == "afterlive"
    with pytest.raises(ValueError, match="found 0"):
        hp.episode_host(t, {"_id": 5, "_playbackMode": 1})
    t["MasterStoryLiveResultEpisode"].append({"_id": 13, "_advId": 2})
    with pytest.raises(ValueError, match="found 2"):
        hp.episode_host(t, {"_id": 2, "_playbackMode": 1})


def background():
    rows = [
        node(
            "room",
            comp(
                "MonoBehaviour",
                2,
                "App|App.Spot|SpotBackground",
                _situationObjects=[
                    {"SituationName": "day", "HideObjects": [{"gameObject": "room/content/wall"}, None]},
                    {"SituationName": "day", "HideObjects": [{"gameObject": "room/content/floor"}]},
                ],
            ),
        ),
        node("room/content"),
        node("room/content/floor", comp("MeshRenderer", 3)),
        node("room/content/wall", comp("MeshRenderer", 4)),
    ]
    rows[2]["active"] = False
    return {"name": "room", "nodes": rows}


def test_background_activates_children_hides_first_match_and_sets_floor_queue():
    doc = background()
    state = hp.background_state(doc, "day(Clone)")
    assert state["situation"] == {
        "name": "day",
        "matched": True,
        "activate": ["room/content/floor", "room/content/wall"],
        "hide": ["room/content/wall", None],
    }
    assert state["nodes"]["room/content/floor"] == {"active": True, "renderQueue": 2000}
    assert state["nodes"]["room/content/wall"] == {"active": False, "renderQueue": None}
    assert not doc["nodes"][2]["active"]
    unmatched = hp.background_state(doc, "night")
    assert not unmatched["nodes"]["room/content/floor"]["active"]
    assert unmatched["floor"]["renderer"] is None


def test_floor_queue_is_selected_before_the_background_root_is_activated():
    doc = background()
    doc["nodes"][0]["active"] = False
    state = hp.background_state(doc, "day")
    assert state["nodes"]["room/content/floor"]["active"]
    assert state["nodes"]["room/content/floor"]["renderQueue"] is None


def host_table(depot, kind):
    objs = sui.objects()
    roots = {}

    def add(name, doc, cls="GameObject"):
        oid = f"CAB-host:{10 + len(roots)}"
        roots[name] = oid
        objs[oid] = (cls, name, {"json": doc})
        return oid

    add("cameraTarget", {"name": "CameraTargetRenderer", "nodes": [node("CameraTargetRenderer")]})
    if kind == "home":
        add(
            "spotWidget",
            {
                "name": "UISpotWidget",
                "nodes": [
                    node(
                        "UISpotWidget",
                        comp(
                            "MonoBehaviour",
                            31,
                            "App|App.Spot|UISpotWidget",
                            _afterTalkContents={"gameObject": "UISpotWidget/talk"},
                            _fadeIn=at("UISpotWidget/open"),
                        ),
                    ),
                    node(
                        "UISpotWidget/talk", comp("MonoBehaviour", 32, "App|App.Adv|SimpleAdvLayoutRoot", _slotCount=2)
                    ),
                    node("UISpotWidget/open", comp("Animator", 33, m_Controller=None)),
                ],
            },
        )
        add("spotLetterbox", {"name": "UISpotLetterbox", "nodes": [node("UISpotLetterbox")]})
        add("background", background())
        add("situation", {"name": "day", "nodes": [node("day")]})
        record = hp.record(tables(), "home/7")
    else:
        add(
            "resultWidget",
            {
                "name": "UILiveResultWidget",
                "nodes": [
                    node(
                        "Result",
                        comp(
                            "MonoBehaviour",
                            31,
                            "App|App.Live|UILiveResultWidget",
                            _advSimpleContents={"gameObject": "Result/View/RewardPanel/Talk"},
                            _inAnimation=at("Result/open"),
                        ),
                    ),
                    node(
                        "Result/View",
                        comp(
                            "MonoBehaviour",
                            32,
                            "App|App.Live|UISoloLiveMusicResultView",
                            _inAnimation=at("Result/open"),
                        ),
                    ),
                    node("Result/View/RewardPanel"),
                    node(
                        "Result/View/RewardPanel/Talk",
                        comp("MonoBehaviour", 33, "App|App.Adv|SimpleAdvLayoutRoot", _slotCount=2),
                    ),
                    node("Result/open", comp("Animator", 34, m_Controller=None)),
                ],
            },
        )
        add(
            "resultBackground",
            {
                "name": "Background",
                "nodes": [
                    node(
                        "Background",
                        comp(
                            "MonoBehaviour",
                            41,
                            "App|App.Live|UILiveResultBackgroundWidget",
                            _inAnimation=at("Background/open"),
                            _backgroundImage=at("Background/image", "Image"),
                        ),
                    ),
                    node("Background/open", comp("Animator", 42, m_Controller=None)),
                    node("Background/image", comp("Image", 43, m_Sprite=None)),
                ],
            },
        )
        add("resultBackgroundSprite", {}, "Sprite")
        record = hp.record({}, "afterlive")
    keys = {"Emb" + hp.ADDRESSES[name]: [oid] for name, oid in roots.items() if name in hp.ADDRESSES}
    if kind == "home":
        keys.update(
            {HOME["_backgroundAssetPath"]: [roots["background"]], HOME["_situationAssetPath"]: [roots["situation"]]}
        )
    return sui.table_doc(depot, objs=objs, keys=keys), record, roots


@pytest.mark.parametrize("kind", ["home", "afterlive"])
def test_host_product_keeps_hierarchies_and_initial_state(tmp_path, kind):
    depot = Depot(tmp_path / "d")
    table, record, roots = host_table(depot, kind)
    inputs = [
        stored(depot, contract.encode(table), "objects"),
        stored(
            depot,
            contract.encode({"host": record, "parts": {"room": "rooms/room.json", "spot": "spots/day.json"}}),
            "master",
        ),
    ]
    subject = "home/7" if kind == "home" else "afterlive"
    _, _, arts = run(depot, HostStage(), subject, inputs)
    doc = contract.loads(depot.read(arts["host.json"]["content"]["sha256"]))
    Draft202012Validator(schemas.load("story-host")).validate(doc)
    assert doc["kind"] == kind and doc["cameraTarget"] == roots["cameraTarget"]
    assert doc["layoutRoot"]["fields"]["_slotCount"] == 2
    assert len(doc["openedSequences"]) == (1 if kind == "home" else 3)
    if kind == "home":
        assert doc["home"]["backgroundState"]["floor"]["renderer"] == "room/content/floor"
        assert doc["home"]["parts"]["room"] == "rooms/room.json"
    else:
        assert doc["afterlive"]["backgroundSprite"] == roots["resultBackgroundSprite"]
        assert doc["afterlive"]["rewardPanel"]["path"] == "Result/View/RewardPanel"
    kept = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]
    assert set(roots.values()) <= set(kept)
    assert sui.SCRIPT not in kept


def test_home_story_requests_host_room_and_spot_products():
    host = hp.episode_host(tables(), {"_id": 3, "_playbackMode": 1})
    parts = {"assets": [], "models": [], "sounds": [], "videos": [], "chat": False, "crilips": False}
    paths = story.part_paths(parts, 3, overlay=True, host=host)
    requested = story.part_products({"advId": 3, "parts": paths}, ["ja"])
    assert requested["story.host"] == ["home/7"]
    assert requested["room.gltf"] == [HOME["_backgroundAssetPath"]]
    assert requested["spot.files"] == [HOME["_situationAssetPath"]]
