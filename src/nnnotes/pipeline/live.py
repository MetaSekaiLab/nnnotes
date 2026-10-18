"""The live products: a music's BGM (live.music), the sounds a live plays besides it (live.sounds), the note highway
(live.notes, live.noteskin, live.noteeffect), the scene (live.scene), and a live (live), the chart of a music at a
difficulty that names the others it plays with.

All are product stages (pipeline.assemble). The sound products read cue sheets: the ACBs their catalog keys load and
the files cri.audio decoded from them. They keep no objects (their files are all they hold):

live.music   subject a music id (MasterLiveMusic._id): "<task id>#music.json" (kind live.music, liveaudio.music_doc)
             and "<task id>#audio/<sheet>/<file>" for each layer of its BGM sound (kind audio.bgm) in the web
             format of the parameter `audio` (encoders.WEB_AUDIO; default aac: AAC-LC in MP4, each layer recording
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

live.scene       subject `default`: "<task id>#scene.json" (kind live.scene: content.livescene.scene) and the object
                 table the player reads; its manifest is scenes/default.json, its input "master" the master record of
                 its settings, lane skins and bands (livescene.scene_record), its roots also the built-in graphics
                 settings (livescene.GRAPHICS)

live.ui          subject `<musicId>/<difficulty>/<language>`: ui.json with start-canvas strings, text records,
                 generated glyph pages and image bindings, with manifest liveui/<musicId>_<difficulty>_<language>.json.
                 It reads the client's font assets, localization settings, jacket and master text. The `fonts`
                 parameter is open or game; open font bytes are explicit inputs. The canvas belongs to live.scene.

live             subject a live (content.live.live_subject: `<musicId>/<difficulty>[/<band>]`): "<task id>#live.json"
                 (kind live: content.live.live), "<task id>#chart.json" (kind live.chart: the chart as shipped),
                 "<task id>#notes.json" and "#notes.mirror.json" (kind live.notes.chart: the converted chart) and the
                 object table of its jacket; its manifest is lives/<musicId>_<difficulty>[_<band>].json, its input
                 "master" the live's master record (live.live_record) with the manifest paths of its parts. `parts`
                 gives the products a live plays with: the scene, the note highway with the default note skin and
                 effect set, the default sounds, the music and UI manifests by language.

`select` (the `product` layout) makes the live.music products of the musics whose BGM key the selection holds, the
default live.sounds when it holds every cue sheet of the default sounds (both need the master data), live.notes when
it holds every key of it, live.scene when it holds the scene and the live of each chart whose key and jacket it holds
(and the master data is there), and the live.noteskin and live.noteeffect products of the skin and effect set keys it
holds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..content import live, liveaudio, livenotes, livescene, liveuiproduct
from ..content.prefabs import mono_json
from ..formats.encoders import DEFAULT_AUDIO_FORMAT, WEB_AUDIO, mp4_priming, web_encode
from ..source import languages
from ..unity import textstyle
from . import contract, units
from .assemble import MASTER_INPUT, Assembly, ProductStage, master_input, reach
from .contract import IncompatibleTask, Input
from .textfonts import FontInputsStage, font_atoms, font_settings, record_input
from .views import master_rows

__all__ = [
    "LIVE",
    "MUSIC",
    "NOTEEFFECT",
    "NOTES",
    "NOTESKIN",
    "SCENE",
    "SOUNDS",
    "UI",
    "LiveStage",
    "MusicStage",
    "NoteEffectStage",
    "NoteSkinStage",
    "NotesStage",
    "SceneStage",
    "SoundsStage",
    "UiStage",
    "encode_atoms",
]

MUSIC, SOUNDS = "live.music", "live.sounds"
NOTES, NOTESKIN, NOTEEFFECT = "live.notes", "live.noteskin", "live.noteeffect"
SCENE = "live.scene"
LIVE = "live"
UI = "live.ui"
DEFAULT = "default"


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


class MusicStage(ProductStage):
    """live.music: a music id -> music.json and its BGM layers (module documentation)."""

    name = MUSIC
    version = 1
    KIND = "LiveMusic"
    MASTER = liveaudio.MUSIC_TABLES
    PARAMS = {"audio": DEFAULT_AUDIO_FORMAT}

    def normalize(self, params: dict | None) -> dict:
        p = super().normalize(params)
        if p["audio"] not in WEB_AUDIO:
            raise ValueError(f"stage {self.name}: audio {p['audio']!r}: one of {', '.join(WEB_AUDIO)}")
        return p

    def uses(self, subject: str, env) -> dict:
        return encode_atoms(env.params["audio"])

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
        return [master_input(env, liveaudio.music_record(master_rows(env, self.MASTER), _music_id(subject)))]

    def manifest(self, subject: str) -> str:
        return f"music/{_music_id(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        fmt = task.params["audio"]
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
        return [master_input(env, liveaudio.sounds_record(master_rows(env, self.MASTER), subject))]

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
        raise ValueError(f"{subject}: the only subject is {DEFAULT}")
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
        return [master_input(env, livenotes.notes_record(master_rows(env, self.MASTER)))]

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


class SceneStage(ProductStage):
    """live.scene: the scene of a live and the start timeline of each band (module documentation)."""

    name = SCENE
    version = 1
    KIND = "LiveScene"
    MASTER = livescene.SCENE_TABLES

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        return [DEFAULT] if livescene.SCENE_KEY in keys else []

    def keys(self, subject: str, env) -> list[str]:
        _default(subject)
        return livescene.scene_keys(livescene.scene_record(master_rows(env, self.MASTER)))

    def builtins(self, subject: str, env) -> list[str]:
        return list(livescene.GRAPHICS.values())

    def more_inputs(self, subject: str, env) -> list[Input]:
        return [master_input(env, livescene.scene_record(master_rows(env, self.MASTER)))]

    def manifest(self, subject: str) -> str:
        return f"scenes/{_default(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        doc, keep, roles = livescene.scene(objects, record)
        return Assembly({"scene.json": _json(doc, SCENE)}, keep, roles)


class UiStage(FontInputsStage):
    """live.ui: start-canvas strings, text styles, font pages and image bindings."""

    name = UI
    version = 1
    KIND = "LiveUi"
    MASTER = liveuiproduct.TABLES
    PARAMS = {"fonts": "open"}

    def normalize(self, params):
        p = super().normalize(params)
        if p["fonts"] not in ("open", "game"):
            raise ValueError(f"stage {self.name}: fonts must be open or game")
        return p

    def uses(self, subject, env):
        return font_atoms()

    def record(self, subject, env):
        return liveuiproduct.record(master_rows(env, self.MASTER), subject)

    def keys(self, subject, env):
        from ..content import storyui

        catalog = {loc["primaryKey"] for loc in env.fact("index")["locations"]}
        return sorted({*liveuiproduct.keys(self.record(subject, env)), *storyui.font_keys(catalog)})

    def resources(self, subject, env):
        liveuiproduct.parse_subject(subject)
        return ["TMP Settings"]

    def builtins(self, subject, env):
        liveuiproduct.parse_subject(subject)
        return ["MonoBehaviour:LocalizeManager"]

    def more_inputs(self, subject, env):
        record = self.record(subject, env)
        jacket = live.jacket_key(record["MasterLiveMusic"][0]["_jacketAssetName"])
        roots = units.root_objects(env, [jacket])
        table = reach(env, roots, self._results, {jacket: roots}).table
        out = [
            master_input(env, record),
            self.font_bundles(subject, env),
            record_input(env, "jacket.objects", table),
        ]
        if env.params["fonts"] == "open":
            for code, path in sorted((env.facts.get("font_files") or {}).items()):
                data = Path(path).read_bytes()
                out.append(Input(f"font:{code}", env.depot.put(data), len(data), None, ({"kind": "depot"},)))
        return out

    def manifest(self, subject):
        music, difficulty, language = liveuiproduct.parse_subject(subject)
        return f"liveui/{music}_{difficulty}_{language}.json"

    def assemble(self, task, objects):
        from ..content import storyui

        if task.atoms != font_atoms():
            raise IncompatibleTask(f"task {task.id}: the font libraries differ from the task")
        lang = textstyle.language_fonts_json(mono_json(objects, storyui.script_root(objects, "LocalizeManager")))
        settings = font_settings(task, objects, lang)
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        fonts = {i.role[5:]: objects.depot.input_bytes(i) for i in task.inputs if i.role.startswith("font:")}
        doc, pages, keep, roles = liveuiproduct.build(
            objects, record, task.subject, settings, task.params["fonts"], fonts
        )
        jacket = contract.loads(objects.depot.input_bytes(task.input("jacket.objects")))
        keep.update(o for o, row in jacket["objects"].items() if row["class"] != "MonoScript")
        files = {p: (body, "png", "live.glyphs") for p, body in pages.items()}
        files["ui.json"] = _json(doc, UI)
        return Assembly(files, keep, roles)


# the parts of a live: {part: product stage}
PARTS = {
    "scene": SCENE,
    "notes": NOTES,
    "noteskin": NOTESKIN,
    "noteeffect": NOTEEFFECT,
    "sounds": SOUNDS,
    "music": MUSIC,
}
PART_STAGES = {
    SCENE: SceneStage,
    NOTES: NotesStage,
    NOTESKIN: NoteSkinStage,
    NOTEEFFECT: NoteEffectStage,
    SOUNDS: SoundsStage,
    MUSIC: MusicStage,
    UI: UiStage,
}


class LiveStage(ProductStage):
    """live: a music's chart at a difficulty and the products it plays with (module documentation)."""

    name = LIVE
    version = 3
    PARAMS = {"languages": []}
    KIND = "Live"
    MASTER = tuple(sorted({*live.LIVE_TABLES, *livenotes.NOTES_TABLES}))

    def normalize(self, params):
        params = super().normalize(params)
        params["languages"] = sorted({languages.check(code) for code in params["languages"]})
        return params

    def select(self, keys: list[str], env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        tables, held = master_rows(env, live.LIVE_TABLES), set(keys)
        scores = {r["_id"]: r for r in tables["MasterLiveMusicScore"]}
        out = []
        for m in tables["MasterLiveMusic"]:
            jacket = live.jacket_key(m["_jacketAssetName"])
            for d in live.DIFFICULTIES:
                s = scores.get(m[f"_{d}ID"])
                if s is not None and {live.chart_key(s["_musicScoreTextFileName"]), jacket} <= held:
                    out.append(live.live_subject(m["_id"], d))
        return sorted(out)

    def live_record(self, subject: str, env) -> dict:
        """The live's master record (content.live.live_record)."""
        return live.live_record(master_rows(env, live.LIVE_TABLES), subject)

    def keys(self, subject: str, env) -> list[str]:
        return live.live_keys(self.live_record(subject, env))

    def parts(self, subject: str, env, requested_languages=()) -> dict[str, list[str]]:
        """{product stage: [subject]} of the products the live plays with (module documentation)."""
        music_id, difficulty, _ = live.parse_subject(subject)
        skin, effect = livenotes.default_assets(livenotes.notes_record(master_rows(env, livenotes.NOTES_TABLES)))
        parts = {
            SCENE: [DEFAULT],
            NOTES: [DEFAULT],
            NOTESKIN: [skin],
            NOTEEFFECT: [effect],
            SOUNDS: [liveaudio.sounds_subject()],
            MUSIC: [str(music_id)],
        }
        if requested_languages:
            parts[UI] = [
                f"{music_id}/{difficulty}/{languages.check(code)}" for code in sorted(set(requested_languages))
            ]
        return parts

    def more_inputs(self, subject: str, env) -> list[Input]:
        parts = self.parts(subject, env)
        paths = {part: PART_STAGES[name]().manifest(parts[name][0]) for part, name in PARTS.items()}
        music_id, difficulty, _ = live.parse_subject(subject)
        paths["ui"] = {
            code: UiStage().manifest(f"{music_id}/{difficulty}/{code}") for code in env.params.get("languages", [])
        }
        return [master_input(env, {"live": self.live_record(subject, env), "parts": paths})]

    def manifest(self, subject: str) -> str:
        music_id, difficulty, band = live.parse_subject(subject)
        return f"lives/{music_id}_{difficulty}{'' if band is None else f'_{band}'}.json"

    def assemble(self, task, objects) -> Assembly:
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        doc, chart, notes, keep = live.live(objects, record["live"], record["parts"])
        out = {"live.json": _json(doc, LIVE), "chart.json": (chart, "json", "live.chart")}
        out.update({name: _json(body, "live.notes.chart") for name, body in notes.items()})
        return Assembly(out, keep)
