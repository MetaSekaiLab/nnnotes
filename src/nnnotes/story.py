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
    <out>/crilips/              crilips.json + crilips.bin: the CRI Lips analysis data read from the
                                APK (crilips.py), when a voice reaches the CRI Lips path (needs_cri_lips)
    <out>/story.json            index of the above (a kind file the episode does not use is null)

Every resource kind of the closure has an exporter (KIND_OUTPUT); the build stops before writing anything when a
resource is missing from the catalog or of a kind without one.
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import Catalog
from .jsonio import write_json
from .player import PlayerData
from . import adv, advmedia, advscene, advui, advvideo, cri, crilips, live2d

# resource kind -> where the story directory holds it
KIND_OUTPUT = {
    "live2d": "live2d/<model>/",
    "stage": "scene.json stages",
    "transition": "ui/ui.json transitions",
    "video": advvideo.INDEX,
    **{k: advmedia.FILES[k][1] for k in advmedia.FILES},
    **{k: advmedia.CHAT[1] for k in advmedia.CHAT_KINDS},
}


CRILIPS_DIR = "crilips"
# a model's MotionSync path (the player's CubismMotionSyncController.fromPrefab): both components on the prefab's root
MOTION_SYNC_COMPONENTS = ("CubismMotionSyncController", "Live2DMotionSyncCriAudioInput")
AIR_LIP_SYNC = ("airlipsync", "airlipsync_holdopen")        # Talk Parameter1 modes without a voice analysis


def has_motion_sync(prefab: dict) -> bool:
    """The exported prefab (live2d.extract_runtime) has a MotionSync controller with its CRI audio input on its root
    node; a model without one takes its mouth from the voice's CRI Lips analysis."""
    classes = {c.get("class") or c.get("type") for n in prefab["nodes"] if "/" not in n["path"]
               for c in n["components"]}
    return all(k in classes for k in MOTION_SYNC_COMPONENTS)


def needs_cri_lips(episode: dict, split_key: str, motion_sync: list[bool]) -> bool:
    """A voice of the episode reaches the CRI Lips analysis: a lip-synced voice row (Talk or Voice with TargetName and
    VoiceIDs; IgnoreLipSync and IgnoreData not set) when a model of the episode has no MotionSync controller
    (`motion_sync`: per model), or a Talk row whose voice also drives other speakers, which use the analysis whatever
    their model (StartSharedAnalyzerLipSync: the EveryoneLipSync mode, or more speakers than voices)."""
    for c in episode["commands"]:
        if c["cmd"] not in ("Talk", "Voice") or c.get("IgnoreData") or c.get("IgnoreLipSync"):
            continue
        if not c.get("TargetName") or not c.get("VoiceIDs"):
            continue
        if not all(motion_sync):
            return True
        if c["cmd"] == "Talk":
            mode = (c.get("Parameter1") or "").strip().lower()
            if mode == "everyonelipsync":
                return True
            if mode not in AIR_LIP_SYNC and len(c["TargetName"].split(split_key)) > len(c["VoiceIDs"]):
                return True
    return False


def check_kinds(resources: list[dict]) -> None:
    """Raise for the first resource missing from the catalog or of a kind the story export does not handle."""
    missing = [r["address"] for r in resources if not r["present"]]
    if missing:
        raise RuntimeError(f"resources not in catalog: {missing}")
    for r in resources:
        if r["kind"] not in KIND_OUTPUT:
            raise NotImplementedError(f"resource kind {r['kind']}: {r['address']}")


def build(cat: Catalog, master: Path, player: PlayerData, adv_id: int, out_dir: Path,
          audio_format: str = "flac", audio: bool = True, fonts: str = "open", *, ui: bool = True,
          audio_options: dict | None = None) -> dict:
    """Build the story directory. `audio=False` leaves the cue sheets undecoded (story.json `audio` is empty);
    `fonts`: "open" exports no font data of the game, "game" exports it (advmedia, advui). `ui=False` writes no
    ui/ (story.json still names ui/ui.json: the caller writes it, e.g. per language as storysite does);
    `audio_options`: further cri.decode options of every cue sheet (flac_level, also). With audio, the CRI Lips data
    is read from the catalog's APK when a voice reaches it (needs_cri_lips; story.json `crilips`, else null)."""
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
            cri.decode(cat, sheet, out_dir / "audio" / sheet, fmt=audio_format, **(audio_options or {}))
            sheets[sheet] = f"audio/{sheet}"

    media = advmedia.extract(cat, player, master, episode, out_dir, fonts=fonts)
    scene = advscene.extract(cat, player, episode, out_dir, shaders=media["shaders"])
    ui = advui.extract(cat, player, episode, out_dir, fonts=fonts, master=master) if ui else None
    videos = advvideo.extract(cat, episode, out_dir)
    lips = None
    if audio:
        split = json.loads((out_dir / "scene.json").read_text(encoding="utf-8"))["settings"]["playerSettings"][
            "_targetNameSplitKey"]
        ms = [has_motion_sync(json.loads((out_dir / m["dir"] / m["prefab"]).read_text(encoding="utf-8")))
              for m in models.values()]
        if needs_cri_lips(episode, split, ms):
            if cat.apk is None:
                raise crilips.CriLipsError("the CRI Lips data is read from the APK: set [paths] apk")
            crilips.write_data(cat.apk, out_dir / CRILIPS_DIR)
            lips = {"descriptor": f"{CRILIPS_DIR}/crilips.json", "data": f"{CRILIPS_DIR}/crilips.bin"}
    index = {"advId": adv_id, "episode": "episode.json", "scene": "scene.json", "ui": "ui/ui.json",
             "models": models, "audio": sheets, **media["files"],
             "videos": advvideo.INDEX if videos else None, "crilips": lips}
    write_json(out_dir / "story.json", index)
    return {**index, "stages": scene["stages"], "shaders": scene["count"],
            "textures": scene["textures"], "uiSummary": ui,
            "media": {"entries": media["counts"], "textures": media["textures"]},
            "videoCount": len(videos["videos"]) if videos else 0}
