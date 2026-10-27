"""The story scene product assembled from a synthetic object table (content.storyscene)."""

import pytest

from nnnotes.content import storyscene
from nnnotes.formats.shader import player_roles
from nnnotes.pipeline.assemble import Objects
from nnnotes.pipeline.depot import Depot
from tests.helpers import livescene as ls
from tests.helpers.live2dmodel import ref
from tests.helpers.livenotes import clip, comp, node, table

ADV = "CAB-adv:1"
ADV_UI, EMPTY_ROOT, RENDER, EXTRA_RENDER = (f"CAB-adv:{i}" for i in (2, 3, 4, 5))
CAMERA, CHARACTER, BACKGROUND, VOLUME = (f"CAB-cm:{i}" for i in (1, 2, 3, 4))
PLAYER_SETTINGS, MASTER_ID_SETTINGS = "CAB-st:1", "CAB-st:2"
CONTROLLER, CLIP = "CAB-ac:1", "CAB-ac:2"
GRAPHICS, QUALITY, PLAYER = "globalgamemanagers:7", "globalgamemanagers:12", "globalgamemanagers:1"
SHADER_UI, SHADER_BLEND = "globalgamemanagers.assets:1", "CAB-sh:1"
SCRIPT = "CAB-m:1"
APP = "App.Runtime|App.Adv|"


def camera_prefab() -> dict:
    """The camera manager: an Animator playing one clip on its own node."""
    return {
        "name": "CameraManager",
        "nodes": [node("CameraManager", comp("Animator", 2, m_Controller=ref(CONTROLLER)))],
    }


def prefab(name: str) -> dict:
    return {"name": name, "nodes": [node(name)]}


def objects() -> dict:
    """{object id: (class, name, {role: JSON document | bytes})} of the story scene and its built-in settings."""
    out = {
        ADV: ("GameObject", "Adv", {"json": prefab("Adv")}),
        ADV_UI: ("GameObject", "AdvUI", {"json": prefab("AdvUI")}),
        EMPTY_ROOT: ("GameObject", "Empty", {}),
        RENDER: ("RenderSettings", None, {"json": {"m_Fog": 0}}),
        EXTRA_RENDER: ("RenderSettings", None, {"json": {"m_Fog": 1}}),
        CAMERA: ("GameObject", "CameraManager", {"json": camera_prefab()}),
        CHARACTER: ("GameObject", "AdvCharacterField", {"json": prefab("AdvCharacterField")}),
        BACKGROUND: ("GameObject", "AdvBackgroundField", {"json": prefab("AdvBackgroundField")}),
        VOLUME: ("GameObject", "AdvGlobalVolume", {"json": prefab("AdvGlobalVolume")}),
        PLAYER_SETTINGS: (
            "MonoBehaviour",
            "AdvPlayerSettings",
            {"json": ls.mono(APP + "AdvPlayerSettings", m_Volume=1.0)},
        ),
        MASTER_ID_SETTINGS: (
            "MonoBehaviour",
            "AdvMasterIdSettings",
            {"json": ls.mono(APP + "AdvMasterIdSettings", m_Id=7)},
        ),
        CONTROLLER: ("AnimatorController", "AdvCamera", {"json": {"name": "AdvCamera", "clips": [ref(CLIP)]}}),
        CLIP: ("AnimationClip", "fade_in", {"json": clip("fade_in", "")}),
        GRAPHICS: (
            "GraphicsSettings",
            None,
            {"json": {"m_AlwaysIncludedShaders": [ref(SHADER_UI)], "m_CustomRenderPipeline": None}},
        ),
        QUALITY: ("QualitySettings", None, {"json": {"m_CurrentQuality": 0}}),
        PLAYER: ("PlayerSettings", None, {"json": {"m_ActiveColorSpace": 0}}),
        SCRIPT: ("MonoScript", "AdvPlayerSettings", {"json": {"m_ClassName": "AdvPlayerSettings"}}),
        SHADER_UI: ls.shader("UI/Default"),
        SHADER_BLEND: ls.shader("Sirius/Adv/Blend"),
    }
    return out


def keys(**overrides) -> dict:
    """{key: root object ids} of the story scene: every key of keys() holds its object, the built-in settings sit
    under a key of their own."""
    out = {
        storyscene.SCENE_KEY: [ADV, ADV_UI, EMPTY_ROOT, RENDER],
        storyscene.PREFAB_KEYS["cameraManager"]: [CAMERA],
        storyscene.PREFAB_KEYS["characterField"]: [CHARACTER],
        storyscene.PREFAB_KEYS["backgroundField"]: [BACKGROUND],
        storyscene.PREFAB_KEYS["globalVolume"]: [VOLUME],
        storyscene.SETTINGS_KEYS["playerSettings"][0]: [PLAYER_SETTINGS],
        storyscene.SETTINGS_KEYS["masterIdSettings"][0]: [MASTER_ID_SETTINGS],
        "builtin": [GRAPHICS, QUALITY, PLAYER],
    }
    out.update(overrides)
    return out


def scene_objects(tmp_path, objs=None, **overrides) -> Objects:
    depot = Depot(tmp_path / "d")
    return Objects(table(depot, keys(**overrides), objs if objs is not None else objects()), depot)


def test_the_scene_document(tmp_path):
    doc, _, _ = storyscene.scene(scene_objects(tmp_path))
    assert doc == {
        "format": 1,
        "scene": [ADV, ADV_UI],
        "renderSettings": RENDER,
        "prefabs": {
            "cameraManager": CAMERA,
            "characterField": CHARACTER,
            "backgroundField": BACKGROUND,
            "globalVolume": VOLUME,
        },
        "settings": {"playerSettings": PLAYER_SETTINGS, "masterIdSettings": MASTER_ID_SETTINGS},
        "graphics": {"graphics": GRAPHICS, "quality": QUALITY, "player": PLAYER},
        "bindings": {CAMERA: {"CameraManager": {CLIP: [{"path": "", "attribute": "m_Color.a"}]}}},
        "shaders": {"Sirius/Adv/Blend": SHADER_BLEND, "UI/Default": SHADER_UI},
    }


def test_keep_excludes_monoscripts_and_roles_hold_the_shader_roles(tmp_path):
    _, keep, roles = storyscene.scene(scene_objects(tmp_path))
    assert SCRIPT not in keep and ADV in keep and SHADER_UI in keep
    assert roles[SHADER_UI] == {"json", "index"} | player_roles(ls.SHADER_VARIANTS)
    assert roles[SHADER_BLEND] == roles[SHADER_UI]
    assert SCRIPT not in roles


def test_a_missing_prefab_key_raises_value_error(tmp_path):
    objs = scene_objects(tmp_path, **{storyscene.PREFAB_KEYS["globalVolume"]: []})
    with pytest.raises(ValueError, match=storyscene.PREFAB_KEYS["globalVolume"]):
        storyscene.scene(objs)


def test_two_render_settings_raise_value_error(tmp_path):
    objs = scene_objects(tmp_path, **{storyscene.SCENE_KEY: [ADV, RENDER, EXTRA_RENDER]})
    with pytest.raises(ValueError, match="2 RenderSettings"):
        storyscene.scene(objs)


CATALOG = [storyscene.SCENE_KEY, *storyscene.PREFAB_KEYS.values(), *(k for k, _ in storyscene.SETTINGS_KEYS.values())]


def test_a_manager_the_boot_scene_holds_is_no_catalog_key():
    without = [k for k in CATALOG if k != storyscene.PREFAB_KEYS["cameraManager"]]
    assert storyscene.keys(without) == sorted(without) and len(storyscene.keys(without)) == 6


def test_keys_list_the_seven_keys_sorted():
    ks = storyscene.keys(CATALOG)
    assert len(ks) == 7 and ks == sorted(ks)
    assert storyscene.SCENE_KEY in ks
    assert set(storyscene.PREFAB_KEYS.values()) <= set(ks)
    assert {key for key, _ in storyscene.SETTINGS_KEYS.values()} <= set(ks)


def test_a_manager_of_the_boot_scene_stands_for_its_missing_catalog_key(tmp_path):
    held = keys()
    del held[storyscene.PREFAB_KEYS["cameraManager"]]
    held["builtin"] = [*held["builtin"], CAMERA]
    doc, _, _ = storyscene.scene(Objects(table(Depot(tmp_path / "d"), held, objects()), Depot(tmp_path / "d")))
    assert doc["prefabs"]["cameraManager"] == CAMERA
