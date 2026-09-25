"""ADV presentation data -> scene.json + textures + shaders.

Everything the game uses to present an ADV episode besides the episode script,
the Live2D models and the audio:

  player          colour space, quality levels, URP pipelines, renderer data and
                  features, post-process data (APK boot data, see player.py)
  cameraManager   the app's Main/Sub camera prefab
  advScene        the ADV scene (AdvSceneRoot + scene camera)
  characterField  AdvCharacterField prefab (Field / Anchors / Stages)
  backgroundField AdvBackgroundField prefab (backdrop mesh + sprite renderer)
  globalVolume    AdvGlobalVolume prefab (volumes + profiles)
  settings        AdvPlayerSettings (+ focus data), AdvMasterIdSettings
  stages          every stage prefab the episode uses (AdvStage, lights,
                  background sprite, volume profiles)
  resources       Resources.Load materials the runtime draws with (Cubism mask
                  pass: Mask / MaskCulling)
  postTextures    per camera renderer: post-process data textures used by the
                  URP chain (film grain textures, indexed by FilmGrain.type)

Objects are exported with export.Exporter (references resolved, textures as
PNG). Shaders referenced anywhere, every Shader in the loaded bundle closures
and the ADV camera renderer's shaders are dumped with shader.py.
"""
from __future__ import annotations

from pathlib import Path

from .catalog import Catalog
from .export import Exporter
from .jsonio import write_json
from .player import PlayerData
from . import shader as shader_mod

ADV_KEYS = {
    "cameraManager": "EmbCommon/Prefab/CameraManager",
    "advScene": "EmbScene/Adv",
    "characterField": "EmbCommon/Adv/Prefab/AdvCharacterField",
    "backgroundField": "EmbCommon/Adv/Prefab/AdvBackgroundField",
    "globalVolume": "EmbCommon/Adv/Prefab/AdvGlobalVolume",
}
SETTINGS_KEYS = {
    "playerSettings": "EmbCommon/Adv/Settings/AdvPlayerSettings",
    "masterIdSettings": "EmbCommon/Adv/Settings/AdvMasterIdSettings",
}
STAGE_PREFIX = "Adv/Stage/"


# Resources.Load paths used by the ADV runtime (Cubism mask pass materials)
RESOURCES = {
    "cubismMask": "Live2D/Cubism/Materials/Mask",
    "cubismMaskCulling": "Live2D/Cubism/Materials/MaskCulling",
}


def camera_renderer(player_graphics: dict, pipeline: str, renderer_index: int) -> str:
    rl = player_graphics["pipelines"][pipeline]["m_RendererDataList"]
    i = player_graphics["pipelines"][pipeline]["m_DefaultRendererIndex"] if renderer_index < 0 else renderer_index
    return rl[i]


def extract(cat: Catalog, player: PlayerData, episode: dict, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ex = Exporter(cat, out_dir, player=player)
    graphics = player.graphics()
    doc: dict = {"player": graphics}
    for name, key in ADV_KEYS.items():
        doc[name] = ex.prefab(key)
    doc["settings"] = {name: ex.asset(key) for name, key in SETTINGS_KEYS.items()}
    doc["resources"] = {name: ex.material(player.resource(path)) for name, path in RESOURCES.items()}

    stages = {}
    for c in episode["commands"]:
        if c["cmd"] == "Stage" and c.get("TargetAssetName"):
            key = STAGE_PREFIX + c["TargetAssetName"]
            stages.setdefault(c["TargetAssetName"], None)
            if stages[c["TargetAssetName"]] is None:
                stages[c["TargetAssetName"]] = ex.prefab(key)
    doc["stages"] = stages

    # shaders: model closures, everything referenced above, the ADV camera renderer
    for r in episode["resources"]:
        if r["kind"] == "live2d":
            ex.closure_shaders(r["address"])
    renderers = set()
    for node in doc["cameraManager"]["nodes"]:
        for comp in node["components"]:
            if comp.get("class") == "UniversalAdditionalCameraData":
                renderers.add(camera_renderer(graphics, graphics["defaultPipeline"],
                                              comp["m_RendererIndex"]))
    for rn in sorted(renderers):
        for o in player.renderer_shaders(rn):
            ex._shader(o)
    # post-process textures the URP chain samples (film grain lookup by FilmGrain.type)
    doc["postTextures"] = {rn: {"filmGrainTex": [ex.texture(o) for o in player.post_textures(rn, "filmGrainTex")]}
                           for rn in sorted(renderers)}
    sdir = out_dir / "shaders"
    index: list = []
    for name in sorted(ex.shaders):
        shader_mod.dump_objects([ex.shaders[name]], sdir, ex.shaders[name].assets_file.name, index)
    shader_summary = shader_mod.write_index(index, sdir)
    doc["shaders"] = {"index": "shaders/shaders.json", "names": shader_summary["names"],
                      "cameraRenderers": sorted(renderers)}
    doc["convention"] = ("Unity space (left-handed, Y up); transforms are local TRS; "
                         "quaternions (x,y,z,w); colours in the project's Gamma space")
    write_json(out_dir / "scene.json", doc)
    return {"stages": list(stages), "textures": len(ex.textures), **shader_summary}
