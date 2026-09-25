"""One ADV episode -> a self-contained story directory that a player can load without game code.

    <out>/episode.json          command list + text/sound/video shards + resource closure (adv.py)
    <out>/live2d/<model>/       every Live2D model the episode uses: moc3, atlas
                                pages, full prefab (live2d.extract_runtime)
    <out>/audio/<cueSheet>/     every cue sheet in the episode's -SoundCueSheet
                                shard, decoded per cue + cues.json (cri.py)
    <out>/scene.json, textures/, shaders/
                                player graphics, cameras, ADV fields, volumes,
                                settings and stages (advscene.py); textures/ and
                                shaders/ also hold those of the kind files below
    <out>/frames.json, effects.json, posteffects.json, stills.json, talkwindows.json, chat.json
                                the episode's frames, particle effects, post-effect
                                profiles, stills, talk windows and chat assets
                                (advmedia.py; each written only when used)
    <out>/videos/               Movie / Clip videos as WebM + videos.json (advvideo.py)
    <out>/ui/                   ADV front canvas UI: ui.json, packed textures,
                                UI shaders; also the rule transitions (advui.py)
    <out>/story.json            index of the above (a kind file the episode does not use is null)

Every resource kind of the closure has an exporter (KIND_OUTPUT); the build stops before writing anything when a
resource is missing from the catalog or of a kind without one.
"""
from __future__ import annotations

from pathlib import Path

from .catalog import Catalog
from .jsonio import write_json
from .player import PlayerData
from . import adv, advmedia, advscene, advui, advvideo, cri, live2d

# resource kind -> where the story directory holds it
KIND_OUTPUT = {
    "live2d": "live2d/<model>/",
    "stage": "scene.json stages",
    "transition": "ui/ui.json transitions",
    "video": advvideo.INDEX,
    **{k: advmedia.FILES[k][1] for k in advmedia.FILES},
    **{k: advmedia.CHAT[1] for k in advmedia.CHAT_KINDS},
}


def check_kinds(resources: list[dict]) -> None:
    """Raise for the first resource missing from the catalog or of a kind the story export does not handle."""
    missing = [r["address"] for r in resources if not r["present"]]
    if missing:
        raise RuntimeError(f"resources not in catalog: {missing}")
    for r in resources:
        if r["kind"] not in KIND_OUTPUT:
            raise NotImplementedError(f"resource kind {r['kind']}: {r['address']}")


def build(cat: Catalog, master: Path, player: PlayerData, adv_id: int, out_dir: Path,
          audio_format: str = "flac", audio: bool = True, fonts: str = "open") -> dict:
    """Build the story directory. `audio=False` leaves the cue sheets undecoded (story.json `audio` is empty);
    `fonts`: "open" exports no font data of the game, "game" exports it (advmedia, advui)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = adv.to_json(adv.extract(cat, master, adv_id))
    check_kinds(episode["resources"])
    write_json(out_dir / "episode.json", episode)

    models = {}
    for r in episode["resources"]:
        if r["kind"] == "live2d":
            name = r["address"].rsplit("/", 1)[-1]
            res = live2d.extract_runtime(cat, r["address"], out_dir / "live2d" / name)
            models[r["address"]] = {"dir": f"live2d/{name}", "moc3": res["moc3"],
                                    "prefab": res["prefab"]}

    sheets = {}
    if audio:
        for sheet in sorted({c["_cueSheetName"] for c in episode["cuesheets"].values()}):
            cri.decode(cat, sheet, out_dir / "audio" / sheet, fmt=audio_format)
            sheets[sheet] = f"audio/{sheet}"

    media = advmedia.extract(cat, player, master, episode, out_dir, fonts=fonts)
    scene = advscene.extract(cat, player, episode, out_dir, shaders=media["shaders"])
    ui = advui.extract(cat, player, episode, out_dir, fonts=fonts)
    videos = advvideo.extract(cat, episode, out_dir)
    index = {"advId": adv_id, "episode": "episode.json", "scene": "scene.json", "ui": "ui/ui.json",
             "models": models, "audio": sheets, **media["files"],
             "videos": advvideo.INDEX if videos else None}
    write_json(out_dir / "story.json", index)
    return {**index, "stages": scene["stages"], "shaders": scene["count"],
            "textures": scene["textures"], "uiSummary": ui,
            "media": {"entries": media["counts"], "textures": media["textures"]},
            "videoCount": len(videos["videos"]) if videos else 0}
