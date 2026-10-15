"""The story products: an ADV episode (story) and the products it plays with.

All are product stages (pipeline.assemble); story.sound, story.crilips, story.chat and story keep no objects (their
files are all they hold):

story.sound    subject a cue sheet name (the `_cueSheetName` of a story's cue sheet table): "<task id>#cues.json"
               (kind story.sound: cue name -> the cue's first stream: `file`, `sampleRate`, `channels`, `samples`,
               `loopStart`, `loopEnd` and, in AAC, `encoderDelay`) and "<task id>#<file>" for each of those streams
               (kind audio.story) in the web format of the parameter `audio` (encoders.WEB_AUDIO; default aac:
               AAC-LC in MP4, each file recording its encoder delay; flac: the decoded FLAC as it is). Its root is the
               cue sheet's catalog key `Cri/Sound/<sheet>` and its manifest cuesheets/<sheet>.json. The atom
               `audio.encode` (ffmpeg) encodes the streams. A cue sheet shared by several stories (a BGM, a sound
               effect sheet) is one product.
story.crilips  subject `default`: "<task id>#crilips.json" (kind story.crilips: the descriptor) and
               "<task id>#crilips.bin" (kind crilips.weights: the float32 weights), read from the CRI Lips library
               of the APK (formats.crilips, input "apk:lib/arm64-v8a/libcri_lips_unity.so"). Its manifest is
               crilips/default.json. A library none of the versions formats.crilips knows makes the product
               `unsupported` (unsupported.crilips.version).

story.scene    subject `default`: "<task id>#scene.json" (kind story.scene: content.storyscene.scene) and the object
               table the player reads: the ADV scene, the camera manager, the character and background fields, the
               global volume, the ADV player and master id settings, and as roots also the built-in graphics settings
               (livescene.GRAPHICS). Its manifest is storyscenes/default.json.

story.asset    subject a catalog key a story loads (a stage, frame, effect, post effect, still, chat window, chat icon
               or stamp, transition): "<task id>#asset.json" (kind story.asset: content.storyasset.asset) and the
               object table of what the key reaches. Its manifest is storyassets/<name>.json, <name> the key with
               "/" as "~" (storyasset.manifest_name). An asset several stories load is one product.

story.chat     subject `default`: "<task id>#chat.json" (kind story.chat: content.storychat.chat, the rows of the
               shared chat shards). Its manifest is storychat/default.json.

story          subject a MasterAdv id: "<task id>#episode.json" (kind story.episode: content.storyepisode.episode_json,
               the command list with its text, sounds, videos and resource closure) and "<task id>#story.json" (kind
               story: content.storyepisode.story, the episode's chat rows, its effect instances and `parts`, the
               manifest paths of the products above it plays with: part_paths). Its roots are the episode key, its
               shard keys and the ADV player settings; its input "master" the episode's master record
               (storyepisode.episode_record). Its manifest is stories/<id>.json. The parts depend on the episode's
               content, so a command asks for the story first and then for what its story.json names
               (part_products).

`select` (the `product` layout) makes the story.sound product of every cue sheet key the selection holds, story.scene
and story.chat when it holds every key of theirs, and the story of every episode whose key it holds; the CRI Lips data
and the story assets are made only when a request names them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..content import liveaudio, storyasset, storychat, storyepisode, storyscene
from ..content.livescene import GRAPHICS
from ..formats import crilips
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO, mp4_priming, web_encode
from ..unity.atoms import Unsupported
from . import contract
from .assemble import MASTER_INPUT, Assembly, ProductStage, master_input
from .contract import IncompatibleTask
from .layout import escape_segment
from .live import encode_atoms
from .live2d import MODEL, ModelStage
from .views import master_rows

__all__ = [
    "ASSET",
    "CHAT",
    "CRILIPS",
    "PART_STAGES",
    "SCENE",
    "SOUND",
    "STORY",
    "AssetStage",
    "ChatStage",
    "CriLipsStage",
    "SceneStage",
    "SoundStage",
    "StoryStage",
    "cues",
    "part_products",
]

SOUND, CRILIPS, SCENE, ASSET = "story.sound", "story.crilips", "story.scene", "story.asset"
CHAT, STORY = "story.chat", "story"
DEFAULT = "default"
SHEET_PREFIX = "Cri/Sound/"
LIBRARY = crilips.LIB_ENTRY_SUFFIX


def _ffmpeg() -> str:
    from ..source.config import tool

    return tool("ffmpeg", "ffmpeg")


def cues(streams: list[dict], ext: str) -> tuple[dict, dict]:
    """The cues of a decoded cue sheet (cri.audio streams.json, in stream order) -> (cues.json: cue name -> its first
    stream with `file` in the extension `ext`, {that file: the stream's decoded file})."""
    doc, files = {}, {}
    for s in streams:
        if s["name"] in doc:
            continue
        name = Path(s["file"]).stem + ext
        entry = {"file": name, **{k: s[k] for k in ("sampleRate", "channels", "samples")}}
        if "loopStart" in s:
            entry["loopStart"], entry["loopEnd"] = s["loopStart"], s["loopEnd"]
        doc[s["name"]] = entry
        files[name] = s["file"]
    return doc, files


def _sheet(subject: str) -> str:
    if not subject or "/" in subject:
        raise ValueError(f"{subject!r}: not a cue sheet name")
    return subject


class SoundStage(ProductStage):
    """story.sound: a cue sheet -> cues.json and its streams in the web format (module documentation)."""

    name = SOUND
    version = 1
    KIND = "StorySound"
    PARAMS = {"audio": DEFAULT_AUDIO_FORMAT}

    def normalize(self, params: dict | None) -> dict:
        p = super().normalize(params)
        if p["audio"] not in WEB_AUDIO:
            raise ValueError(f"stage {self.name}: audio {p['audio']!r}: one of {', '.join(WEB_AUDIO)}")
        return p

    def uses(self, subject: str, env) -> dict:
        return encode_atoms(env.params["audio"])

    def select(self, keys: list[str], env) -> list[str]:
        names = (k[len(SHEET_PREFIX) :] for k in keys if k.startswith(SHEET_PREFIX))
        return sorted(n for n in names if "/" not in n)

    def keys(self, subject: str, env) -> list[str]:
        return [liveaudio.sheet_key(_sheet(subject))]

    def cue_sheets(self, subject: str, env) -> list[str]:
        return self.keys(subject, env)

    def manifest(self, subject: str) -> str:
        return f"cuesheets/{escape_segment(_sheet(subject))}.json"

    def assemble(self, task, objects) -> Assembly:
        fmt = task.params["audio"]
        want = encode_atoms(fmt)
        if task.atoms != want:
            raise IncompatibleTask(f"task {task.id}: the audio encoder here is {want} (task: {task.atoms})")
        sheet = objects.sheet(liveaudio.sheet_key(_sheet(task.subject)))
        ext = WEB_AUDIO[fmt][0]
        doc, sources = cues(sheet.streams(), ext)
        files, delays = {}, {}
        for name, src in sources.items():
            if WEB_AUDIO[fmt][1] is None:
                files[name] = (sheet.file(src), ext[1:], "audio.story")
                continue
            with tempfile.TemporaryDirectory(prefix="nnnotes-story-sound-") as tmp:
                flac = Path(tmp) / "in.flac"
                flac.write_bytes(sheet.bytes(src))
                data = web_encode(_ffmpeg(), flac, Path(tmp) / f"out{ext}", fmt)
            if fmt == "aac":
                delays[name] = mp4_priming(data)
            files[name] = (data, ext[1:], "audio.story")
        for entry in doc.values():
            if entry["file"] in delays:
                entry["encoderDelay"] = delays[entry["file"]]
        files["cues.json"] = (contract.encode(doc), "json", SOUND)
        return Assembly(files, set())


class CriLipsStage(ProductStage):
    """story.crilips: the CRI Lips analysis data of the APK's library (module documentation)."""

    name = CRILIPS
    version = 1
    KIND = "StoryCriLips"
    APK = (LIBRARY,)

    def manifest(self, subject: str) -> str:
        return f"crilips/{_default(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        so = objects.depot.input_bytes(task.input(f"apk:{LIBRARY}"))
        try:
            blob, desc = crilips.extract(so, crilips.SUPPORTED)
        except crilips.CriLipsError as e:
            raise Unsupported("unsupported.crilips.version", str(e)) from None
        return Assembly(
            {"crilips.json": (contract.encode(desc), "json", CRILIPS), "crilips.bin": (blob, "bin", "crilips.weights")},
            set(),
        )


def _default(subject: str) -> str:
    if subject != DEFAULT:
        raise ValueError(f"{subject}: the only subject is {DEFAULT}")
    return subject


class SceneStage(ProductStage):
    """story.scene: the ADV scene, its prefabs and settings (module documentation)."""

    name = SCENE
    version = 1
    KIND = "StoryScene"

    def select(self, keys: list[str], env) -> list[str]:
        return [DEFAULT] if set(storyscene.keys()) <= set(keys) else []

    def keys(self, subject: str, env) -> list[str]:
        _default(subject)
        return storyscene.keys()

    def builtins(self, subject: str, env) -> list[str]:
        return sorted(GRAPHICS.values())

    def manifest(self, subject: str) -> str:
        return f"storyscenes/{_default(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = storyscene.scene(objects)
        return Assembly({"scene.json": (contract.encode(doc), "json", SCENE)}, keep, roles)


class AssetStage(ProductStage):
    """story.asset: the objects of one catalog key a story loads (module documentation)."""

    name = ASSET
    version = 1
    KIND = "StoryAsset"

    def keys(self, subject: str, env) -> list[str]:
        storyasset.manifest_name(subject)
        return [subject]

    def manifest(self, subject: str) -> str:
        return f"storyassets/{storyasset.manifest_name(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = storyasset.asset(objects, task.subject)
        return Assembly({"asset.json": (contract.encode(doc), "json", ASSET)}, keep, roles)


class ChatStage(ProductStage):
    """story.chat: the rows of the shared chat shards (module documentation)."""

    name = CHAT
    version = 1
    KIND = "StoryChat"

    def select(self, keys: list[str], env) -> list[str]:
        return [DEFAULT] if set(storychat.keys()) <= set(keys) else []

    def keys(self, subject: str, env) -> list[str]:
        _default(subject)
        return storychat.keys()

    def manifest(self, subject: str) -> str:
        return f"storychat/{_default(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = storychat.chat(objects)
        return Assembly({"chat.json": (contract.encode(doc), "json", CHAT)}, keep, roles)


def _adv_id(subject: str) -> int:
    if not subject.isdigit():
        raise ValueError(f"{subject}: not an episode id")
    return int(subject)


def part_paths(parts: dict) -> dict:
    """story.json `parts`: the manifest paths of the products of storyepisode.parts `parts`."""
    return {
        "scene": SceneStage().manifest(DEFAULT),
        "assets": {a: AssetStage().manifest(a) for a in parts["assets"]},
        "models": {a: ModelStage().manifest(a) for a in parts["models"]},
        "sounds": {s: SoundStage().manifest(s) for s in parts["sounds"]},
        "chat": ChatStage().manifest(DEFAULT) if parts["chat"] else None,
        "crilips": CriLipsStage().manifest(DEFAULT) if parts["crilips"] else None,
    }


def part_products(story: dict) -> dict[str, list[str]]:
    """{product stage: [subject]} of the products story.json `story` names in `parts`."""
    parts = story["parts"]
    out = {
        SCENE: [DEFAULT],
        ASSET: sorted(parts["assets"]),
        MODEL: sorted(parts["models"]),
        SOUND: sorted(parts["sounds"]),
        CHAT: [DEFAULT] if parts["chat"] else [],
        CRILIPS: [DEFAULT] if parts["crilips"] else [],
    }
    return {name: subjects for name, subjects in out.items() if subjects}


# the products a story plays with: {product stage: its class}
PART_STAGES = {
    SCENE: SceneStage,
    ASSET: AssetStage,
    MODEL: ModelStage,
    SOUND: SoundStage,
    CHAT: ChatStage,
    CRILIPS: CriLipsStage,
}


class StoryStage(ProductStage):
    """story: an ADV episode -> episode.json and story.json (module documentation)."""

    name = STORY
    version = 1
    KIND = "Story"
    MASTER = storyepisode.TABLES

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        held = set(keys)
        rows = master_rows(env, ("MasterAdv",))["MasterAdv"]
        return sorted(str(r["_id"]) for r in rows if storyepisode.episode_key(r["_advEpisodeAsset"]) in held)

    def record(self, subject: str, env) -> dict:
        """The episode's master record (storyepisode.episode_record)."""
        return storyepisode.episode_record(master_rows(env, self.MASTER), _adv_id(subject))

    def keys(self, subject: str, env) -> list[str]:
        return storyepisode.keys(self.record(subject, env)["MasterAdv"][0]["_advEpisodeAsset"])

    def more_inputs(self, subject: str, env) -> list:
        return [master_input(env, self.record(subject, env))]

    def manifest(self, subject: str) -> str:
        return f"stories/{_adv_id(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        episode = storyepisode.episode_json(storyepisode.episode(objects, record))
        doc = storyepisode.story(episode, record, part_paths(storyepisode.parts(episode)))
        files = {
            "episode.json": (contract.encode(episode), "json", "story.episode"),
            "story.json": (contract.encode(doc), "json", STORY),
        }
        return Assembly(files, set())
