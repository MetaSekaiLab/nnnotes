"""One ADV episode -> a self-contained story directory that a player can load without game code.

    <out>/episode.json          command list + text/sound/video shards (adv.py)
    <out>/live2d/<model>/       every Live2D model the episode uses: moc3, atlas
                                pages, full prefab (live2d.extract_runtime)
    <out>/audio/<cueSheet>/     every cue sheet in the episode's -SoundCueSheet
                                shard, decoded per cue + cues.json (cri.py)
    <out>/scene.json, textures/, shaders/
                                player graphics, cameras, ADV fields, volumes,
                                settings and stages (advscene.py)
    <out>/ui/                   ADV front canvas UI: ui.json, packed textures,
                                UI shaders (advui.py)
    <out>/story.json            index of the above
"""
from __future__ import annotations

from pathlib import Path

from .catalog import Catalog
from .jsonio import write_json
from .player import PlayerData
from . import adv, advscene, advui, cri, live2d


def build(cat: Catalog, master: Path, player: PlayerData, adv_id: int, out_dir: Path,
          audio_format: str = "flac") -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = adv.to_json(adv.extract(cat, master, adv_id))
    missing = [r["address"] for r in episode["resources"] if not r["present"]]
    if missing:
        raise RuntimeError(f"resources not in catalog: {missing}")
    write_json(out_dir / "episode.json", episode)

    models = {}
    for r in episode["resources"]:
        if r["kind"] == "live2d":
            name = r["address"].rsplit("/", 1)[-1]
            res = live2d.extract_runtime(cat, r["address"], out_dir / "live2d" / name)
            models[r["address"]] = {"dir": f"live2d/{name}", "moc3": res["moc3"],
                                    "prefab": res["prefab"]}
        elif r["kind"] != "stage":
            raise NotImplementedError(f"resource kind {r['kind']}: {r['address']}")

    audio = {}
    for sheet in sorted({c["_cueSheetName"] for c in episode["cuesheets"].values()}):
        cri.decode(cat, sheet, out_dir / "audio" / sheet, fmt=audio_format)
        audio[sheet] = f"audio/{sheet}"

    scene = advscene.extract(cat, player, episode, out_dir)
    ui = advui.extract(cat, player, episode, out_dir)
    index = {"advId": adv_id, "episode": "episode.json", "scene": "scene.json", "ui": "ui/ui.json",
             "models": models, "audio": audio}
    write_json(out_dir / "story.json", index)
    return {**index, "stages": scene["stages"], "shaders": scene["count"],
            "textures": scene["textures"], "uiSummary": ui}
