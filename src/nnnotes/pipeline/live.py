"""The live products: a music's BGM (live.music), the sounds a live plays besides it (live.sounds) and the note
highway (live.notes, live.noteskin, live.noteeffect).

All are product stages (pipeline.assemble). The sound products read cue sheets: the ACBs their catalog keys load and
the files cri.audio decoded from them. They keep no objects (their files are all they hold):

live.music   subject a music id (MasterLiveMusic._id): "<task id>#music.json" (kind live.music, liveaudio.music_doc)
             and "<task id>#audio/<sheet>/<file>" for each layer of its BGM sound (kind audio.bgm) in the web
             format of the parameter `format` (encoders.WEB_AUDIO; default aac: AAC-LC in MP4, each layer recording
             its encoder delay `encoderDelay`; flac: the decoded FLAC as it is). Its manifest is music/<id>.json, its
             input "master" the music's master record (liveaudio.music_record). The atom `audio.encode` (ffmpeg)
             encodes the layers.
live.sounds  subject an option set of the note sounds (liveaudio.sounds_subject: `default`, `NoteSePatternId=1,2`):
             "<task id>#sounds.json" (kind live.sounds, liveaudio.sounds_doc) and "<task id>#audio/<sheet>/<file>" for
             each layer of its sounds (kind audio.se, the decoded FLAC). Its manifest is sounds/<subject>.json, its
             inputs "master" (liveaudio.sounds_record), the CRI ACF of the APK and, as roots, the sound settings of
             the APK's built-in files (liveaudio.SETTINGS_SCRIPTS).

The note highway products assemble with content.livenotes from the objects of their keys:

live.notes       subject `default`: "<task id>#notes.json" (kind live.notes: content.livenotes.notes) and the object
                 table the player reads; its manifest is notes/default.json, its input "master" the settings' master
                 record (livenotes.notes_record)
live.noteskin    subject a note skin (MasterLiveNoteSkin._assetName, `skin001`): "<task id>#noteskin.json" (kind
                 live.noteskin); its manifest is noteskins/<name>.json
live.noteeffect  subject a note effect set (MasterLiveNoteEffectSkin._assetName or its `Light` variant,
                 `effect001Light`): "<task id>#noteeffect.json" (kind live.noteeffect); its manifest is
                 noteeffects/<name>.json

`select` (the `product` layout) makes the live.music products of the musics whose BGM key the selection holds, the
default live.sounds when it holds every cue sheet of the default sounds (both need the master data), live.notes when
it holds every key of it (and the master data is there), and the live.noteskin and live.noteeffect products of the
skin and effect set keys it holds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..content import liveaudio, livenotes
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO, mp4_priming, web_encode
from . import contract
from .assemble import Assembly, ProductStage
from .contract import IncompatibleTask, Input
from .views import master_rows

__all__ = [
    "MUSIC",
    "NOTEEFFECT",
    "NOTES",
    "NOTESKIN",
    "SOUNDS",
    "MusicStage",
    "NoteEffectStage",
    "NoteSkinStage",
    "NotesStage",
    "SoundsStage",
    "encode_atoms",
]

MUSIC, SOUNDS = "live.music", "live.sounds"
NOTES, NOTESKIN, NOTEEFFECT = "live.notes", "live.noteskin", "live.noteeffect"
DEFAULT = "default"
MASTER_INPUT = "master"


def _ffmpeg() -> str:
    from ..source.config import tool

    return tool("ffmpeg", "ffmpeg")


def encode_atoms(fmt: str) -> dict:
    """The atoms of a live.music task in the format `fmt`: ffmpeg (audio.encode) unless the layers stay FLAC."""
    if WEB_AUDIO[fmt][1] is None:
        return {}
    from .cristages import tool_id

    return {"audio.encode": tool_id("ffmpeg", _ffmpeg())}


def _music_id(subject: str) -> int:
    if not subject.isdigit():
        raise ValueError(f"{subject}: not a music id")
    return int(subject)


def _record(env, data: dict) -> Input:
    body = contract.encode(data)
    return Input(MASTER_INPUT, env.depot.put(body), len(body), None, ({"kind": "depot"},))


class MusicStage(ProductStage):
    """live.music: a music id -> music.json and its BGM layers (module documentation)."""

    name = MUSIC
    version = 1
    KIND = "LiveMusic"
    MASTER = liveaudio.MUSIC_TABLES
    PARAMS = {"format": DEFAULT_AUDIO_FORMAT}

    def normalize(self, params: dict | None) -> dict:
        p = super().normalize(params)
        if p["format"] not in WEB_AUDIO:
            raise ValueError(f"stage {self.name}: format {p['format']!r}: one of {', '.join(WEB_AUDIO)}")
        return p

    def uses(self, subject: str, env) -> dict:
        return encode_atoms(env.params["format"])

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        tables, held = master_rows(env, self.MASTER), set(keys)
        out = []
        for r in tables["MasterLiveMusic"]:
            _, sheet = liveaudio.music_sound(tables, r["_id"])
            if liveaudio.sheet_key(sheet["_cueSheetName"]) in held:
                out.append(str(r["_id"]))
        return sorted(out)

    def keys(self, subject: str, env) -> list[str]:
        _, sheet = liveaudio.music_sound(master_rows(env, self.MASTER), _music_id(subject))
        return [liveaudio.sheet_key(sheet["_cueSheetName"])]

    def cue_sheets(self, subject: str, env) -> list[str]:
        return self.keys(subject, env)

    def more_inputs(self, subject: str, env) -> list[Input]:
        return [_record(env, liveaudio.music_record(master_rows(env, self.MASTER), _music_id(subject)))]

    def manifest(self, subject: str) -> str:
        return f"music/{_music_id(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        fmt = task.params["format"]
        want = encode_atoms(fmt)
        if task.atoms != want:
            raise IncompatibleTask(f"task {task.id}: the audio encoder here is {want} (task: {task.atoms})")
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        name = record["MasterSoundCueSheet"][0]["_cueSheetName"]
        sheet = objects.sheet(liveaudio.sheet_key(name))
        streams = sheet.streams()
        ext = WEB_AUDIO[fmt][0]
        doc = liveaudio.music_doc(record, _music_id(task.subject), sheet.acb(), streams, ext)
        files = {}
        for entry in doc["sounds"].values():
            for layer in entry["layers"]:
                src = streams[layer["awbId"]]["file"]
                if WEB_AUDIO[fmt][1] is None:
                    files[layer["file"]] = (sheet.file(src), ext[1:], "audio.bgm")
                    continue
                with tempfile.TemporaryDirectory(prefix="nnnotes-music-") as tmp:
                    flac = Path(tmp) / "in.flac"
                    flac.write_bytes(sheet.bytes(src))
                    data = web_encode(_ffmpeg(), flac, Path(tmp) / f"out{ext}", fmt)
                if fmt == "aac":
                    layer["encoderDelay"] = mp4_priming(data)
                files[layer["file"]] = (data, ext[1:], "audio.bgm")
        files["music.json"] = (contract.encode(doc), "json", "live.music")
        return Assembly(files, set())


class SoundsStage(ProductStage):
    """live.sounds: an option set of the note sounds -> sounds.json and its sounds (module documentation)."""

    name = SOUNDS
    version = 1
    KIND = "LiveSounds"
    MASTER = liveaudio.SOUND_TABLES
    APK = (liveaudio.ACF_IN_APK,)

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        default = liveaudio.sounds_subject()
        return [default] if set(self.keys(default, env)) <= set(keys) else []

    def keys(self, subject: str, env) -> list[str]:
        record = liveaudio.sounds_record(master_rows(env, self.MASTER), subject)
        return sorted(liveaudio.sheet_key(r["_cueSheetName"]) for r in record["MasterSoundCueSheet"])

    def cue_sheets(self, subject: str, env) -> list[str]:
        return self.keys(subject, env)

    def builtins(self, subject: str, env) -> list[str]:
        return [f"MonoBehaviour:{c}" for c in liveaudio.SETTINGS_SCRIPTS]

    def more_inputs(self, subject: str, env) -> list[Input]:
        return [_record(env, liveaudio.sounds_record(master_rows(env, self.MASTER), subject))]

    def manifest(self, subject: str) -> str:
        liveaudio.sounds_groups(subject)
        return f"sounds/{subject}.json"

    def assemble(self, task, objects) -> Assembly:
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        acf = objects.depot.input_bytes(task.input(f"apk:{liveaudio.ACF_IN_APK}"))

        def sheet(name: str):
            s = objects.sheet(liveaudio.sheet_key(name))
            return s.acb(), s.streams()

        svs, acd = liveaudio.sound_settings(objects)
        doc = liveaudio.sounds_doc(record, task.subject, sheet, svs, acd, acf)
        files = {}
        for entry in doc["sounds"].values():
            s = objects.sheet(liveaudio.sheet_key(entry["sheet"]))
            for layer in entry["layers"]:
                name = layer["file"].rsplit("/", 1)[1]
                files[layer["file"]] = (s.file(name), name.rsplit(".", 1)[1], "audio.se")
        files["sounds.json"] = (contract.encode(doc), "json", "live.sounds")
        return Assembly(files, set())


def _default(subject: str) -> str:
    if subject != DEFAULT:
        raise ValueError(f"{subject}: the live notes subject is {DEFAULT}")
    return subject


def _json(doc: dict, kind: str) -> tuple:
    return contract.encode(doc), "json", kind


class NotesStage(ProductStage):
    """live.notes: the note highway and its settings (module documentation)."""

    name = NOTES
    version = 1
    KIND = "LiveNotes"
    MASTER = livenotes.NOTES_TABLES

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        return [DEFAULT] if set(livenotes.notes_keys()) <= set(keys) else []

    def keys(self, subject: str, env) -> list[str]:
        _default(subject)
        return livenotes.notes_keys()

    def more_inputs(self, subject: str, env) -> list[Input]:
        return [_record(env, livenotes.notes_record(master_rows(env, self.MASTER)))]

    def manifest(self, subject: str) -> str:
        return f"notes/{_default(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        doc, keep, roles = livenotes.notes(objects, record)
        return Assembly({"notes.json": _json(doc, NOTES)}, keep, roles)


class NoteSkinStage(ProductStage):
    """live.noteskin: a note skin (module documentation)."""

    name = NOTESKIN
    version = 1
    KIND = "LiveNoteSkin"

    def select(self, keys: list[str], env) -> list[str]:
        held = set(keys)
        names = (m["name"] for m in map(livenotes.SKIN_KEY.fullmatch, keys) if m)
        return sorted(n for n in names if livenotes.NAME.fullmatch(n) and set(livenotes.noteskin_keys(n)) <= held)

    def keys(self, subject: str, env) -> list[str]:
        return livenotes.noteskin_keys(subject)

    def manifest(self, subject: str) -> str:
        return f"noteskins/{livenotes.asset_name(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = livenotes.noteskin(objects, task.subject)
        return Assembly({"noteskin.json": _json(doc, NOTESKIN)}, keep, roles)


class NoteEffectStage(ProductStage):
    """live.noteeffect: a note effect set (module documentation)."""

    name = NOTEEFFECT
    version = 1
    KIND = "LiveNoteEffect"

    def select(self, keys: list[str], env) -> list[str]:
        names = (m["name"] for m in map(livenotes.EFFECT_KEY.fullmatch, keys) if m)
        return sorted(n for n in names if livenotes.NAME.fullmatch(n))

    def keys(self, subject: str, env) -> list[str]:
        return [livenotes.effect_settings_key(livenotes.asset_name(subject))]

    def manifest(self, subject: str) -> str:
        return f"noteeffects/{livenotes.asset_name(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, keep, roles = livenotes.noteeffect(objects, task.subject)
        return Assembly({"noteeffect.json": _json(doc, NOTEEFFECT)}, keep, roles)
