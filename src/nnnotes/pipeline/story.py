"""The story products: the cue sheets a story plays (story.sound) and the CRI Lips analysis data (story.crilips).

Both are product stages (pipeline.assemble) that keep no objects (their files are all they hold):

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

`select` (the `product` layout) makes the story.sound product of every cue sheet key the selection holds; the
CRI Lips data is made only when a request names it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..content import liveaudio
from ..formats import crilips
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO, mp4_priming, web_encode
from ..unity.atoms import Unsupported
from . import contract
from .assemble import Assembly, ProductStage
from .contract import IncompatibleTask
from .layout import escape_segment
from .live import encode_atoms

__all__ = ["CRILIPS", "SOUND", "CriLipsStage", "SoundStage", "cues"]

SOUND, CRILIPS = "story.sound", "story.crilips"
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
        if subject != DEFAULT:
            raise ValueError(f"{subject}: the only subject is {DEFAULT}")
        return f"crilips/{DEFAULT}.json"

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
