"""The story scene of a product: what the ADV story screen loads before its first line.

One product (`scene`, scene.json format SCENE_FORMAT), read from a product's object table (`Objects`,
pipeline.assemble), the way livescene reads the live scene:

    scene           the root GameObjects of the addressable scene EmbScene/Adv that have their prefab document
                    (the ADV scene's roots and their references to each other, prefabs.component_ref)
    renderSettings  the scene's RenderSettings
    prefabs         the camera manager, the character and background fields and the global volume (PREFAB_KEYS)
    settings        the ADV player and master id settings, by script class (SETTINGS_KEYS)
    graphics        the built-in settings the render path reads (livescene.GRAPHICS)
    bindings        {prefab GameObject id: {Animator node path: {clip id: [{path, attribute}]}}}: the clips each
                    Animator plays through its controller (livenotes.animator_bindings)
    shaders         {shader name: object id}: what a lookup by name finds (prefabs.shader_table; of different
                    shaders of one name the one GraphicsSettings always includes)

Of the objects the product keeps all but the MonoScripts; of a shader its parsed form, variant index and the programs
of the player's graphics API; of a Mesh its typetree JSON and its `arrays` document (prefabs.mesh_roles).
"""

from __future__ import annotations

from .livenotes import animator_bindings
from .livescene import GRAPHICS
from .prefabs import Objects, builtin_root, has_json, key_object, mesh_roles, ref_id, shader_table

SCENE_FORMAT = 1  # scene.json "format"
SCENE_KEY = "EmbScene/Adv"
PREFAB_KEYS = {
    "cameraManager": "EmbCommon/Prefab/CameraManager",
    "characterField": "EmbCommon/Adv/Prefab/AdvCharacterField",
    "backgroundField": "EmbCommon/Adv/Prefab/AdvBackgroundField",
    "globalVolume": "EmbCommon/Adv/Prefab/AdvGlobalVolume",
}
# the settings of `settings`: {name: (catalog key, script class)}
SETTINGS_KEYS = {
    "playerSettings": ("EmbCommon/Adv/Settings/AdvPlayerSettings", "AdvPlayerSettings"),
    "masterIdSettings": ("EmbCommon/Adv/Settings/AdvMasterIdSettings", "AdvMasterIdSettings"),
}


def keys() -> list[str]:
    """The catalog keys of the story scene: the scene key, the prefab keys and the settings keys, sorted."""
    return sorted([SCENE_KEY, *PREFAB_KEYS.values(), *(key for key, _ in SETTINGS_KEYS.values())])


def scene(objects: Objects) -> tuple[dict, set[str], dict[str, set[str]]]:
    """(scene.json, the object ids the player reads, {object id: the roles of its files it reads}) of the story scene
    product (module documentation)."""
    roots = [o for o in objects.key(SCENE_KEY) if o in objects]
    gos = [o for o in roots if objects.info(o).get("class") == "GameObject" and has_json(objects, o)]
    render = [o for o in roots if objects.info(o).get("class") == "RenderSettings"]
    if len(render) != 1:
        raise ValueError(f"{SCENE_KEY}: {len(render)} RenderSettings")
    graphics = {name: builtin_root(objects, cls) for name, cls in GRAPHICS.items()}
    always = (ref_id(r) for r in objects.json(graphics["graphics"]).get("m_AlwaysIncludedShaders", ()))
    shaders, roles = shader_table(objects, found=[o for o in always if o is not None])
    doc = {
        "format": SCENE_FORMAT,
        "scene": gos,
        "renderSettings": render[0],
        "prefabs": {name: key_object(objects, key, "GameObject") for name, key in PREFAB_KEYS.items()},
        "settings": {name: key_object(objects, key, cls) for name, (key, cls) in SETTINGS_KEYS.items()},
        "graphics": graphics,
        "bindings": animator_bindings(objects),
        "shaders": shaders,
    }
    keep = {o for o in objects.ids() if objects.info(o).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    return doc, keep, roles
