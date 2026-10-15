"""The live products (pipeline.live) and the cue sheets of product stages (pipeline.assemble) on synthetic master
tables, ACBs, decodes and object tables."""

import json
import subprocess
import sys

import pytest

from nnnotes.content import liveaudio, livenotes, livescene
from nnnotes.formats import encoders
from nnnotes.pipeline import assemble, contract, cristages, live
from nnnotes.pipeline.assemble import Assembly, ProductStage
from nnnotes.pipeline.contract import IncompatibleTask, Input, Task
from nnnotes.pipeline.cristages import AudioStage
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.live import (
    LiveStage,
    MusicStage,
    NoteEffectStage,
    NoteSkinStage,
    NotesStage,
    SceneStage,
    SoundsStage,
)
from nnnotes.pipeline.stages import Env, describe, execute, registry
from tests.content.test_live_options import write_master
from tests.helpers import cri
from tests.helpers import live as lh
from tests.helpers import livenotes as ln
from tests.helpers import livescene as ls
from tests.pipeline.test_assemble import addresses
from tests.pipeline.test_cristages import export_result, fake_tools, planning
from tests.site.test_story_site import mp4

ACF = liveaudio.ACF_IN_APK
CATEGORIES = {3: "LiveNotesSe", 4: "LiveSe", 5: "LiveBgm"}
SCRIPT = "Assembly-CSharp|App|{}"


# ---------------------------------------------------------------- synthetic master data and cue sheets
def sheets_of_master() -> dict:
    """{sheet: {cue: cue}} of the synthetic sounds: note SE set g in sheet se<g> (cue se<g>_<type>, two layers for
    type 7), the live SE in LiveSe (live_<type>), the BGM of music 7 in bgm_7."""
    out: dict = {}
    for g in (1, 2, 3):
        out[f"se{g}"] = {
            f"se{g}_{t}": {"layers": [{"samples": 100 + t}] * (2 if t == 7 else 1), "categories": [3]}
            for t in range(1, 15)
        }
    out["LiveSe"] = {
        f"live_{t}": {"layers": [{"samples": 200 + t, "bus": (0, 5000)}], "categories": [4]} for t in range(1, 17)
    }
    out["bgm_7"] = {"bgm_7": {"layers": [{"samples": 48000, "volume": 0.5}], "categories": [5], "volume": 0.8}}
    return out


def master_dir(tmp_path):
    md = write_master(tmp_path / "master")
    sheets, sounds = [], []
    for n, (sheet, cues) in enumerate(sheets_of_master().items()):
        sheets.append({"_id": 10 + n, "_cueSheetName": sheet})
        for cue in cues:
            if sheet == "bgm_7":
                sid = 900
            elif sheet == "LiveSe":
                sid = 500 + int(cue.rsplit("_", 1)[1])
            else:
                g, t = cue[2:].split("_")
                sid = int(g) * 1000 + int(t)
            sounds.append(
                {"_id": sid, "_soundCueSheetID": 10 + n, "_cueName": cue, "_category": 1, "_isRandomPitch": False}
            )
    tables = {
        "MasterSoundCueSheet": sheets,
        "MasterSound": sounds,
        "MasterLiveSe": [{"_id": t, "_liveSeType": t, "_seId": 500 + t} for t in range(1, 17)],
        "MasterLiveMusic": [{"_id": 7, "_musicSoundID": 900}],
    }
    for name, rows in tables.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return md


def stored(depot, data: bytes, role: str) -> Input:
    return Input(role, depot.put(data), len(data), None, ({"kind": "depot"},))


def sheet_inputs(depot, names) -> tuple[list[Input], dict]:
    """The inputs "acb:<key>" and "sheets" of the cue sheets `names` decoded (FLAC bytes per stream)."""
    cues, inputs, doc = sheets_of_master(), [], {}
    for name in names:
        key = liveaudio.sheet_key(name)
        acb = stored(depot, cri.live_acb(cues[name], CATEGORIES), f"acb:{key}")
        streams = cri.live_streams(cues[name])
        files = {}
        for s in streams:
            data = f"fLaC {name} {s['stream']}".encode()
            files[s["file"]] = {"sha256": depot.put(data), "size": len(data), "ext": "flac"}
        body = contract.encode(streams)
        files["streams.json"] = {"sha256": depot.put(body), "size": len(body), "ext": "json"}
        doc[key] = {"acb": acb.sha256, "status": "exported", "files": files}
        inputs.append(acb)
    return [*inputs, stored(depot, contract.encode(doc), assemble.SHEETS_INPUT)], doc


def table(depot, monos=()) -> Input:
    """The input "objects": a table whose roots are the MonoBehaviours `monos` [(path id, json document)]."""
    objects = {}
    for pid, doc in monos:
        data = contract.encode(doc)
        objects[f"globalgamemanagers.assets:{pid}"] = {
            "status": "exported",
            "class": "MonoBehaviour",
            "files": {"json": {"sha256": depot.put(data), "size": len(data), "ext": "json"}},
        }
    doc = {"schema": contract.OBJECTS, "roots": sorted(objects), "keys": {}, "objects": objects, "missing": []}
    return stored(depot, contract.encode(doc), "objects")


def master_record(env, stage, subject) -> Input:
    (inp,) = stage.more_inputs(subject, env)
    return inp


def run(depot, stage, subject, inputs, params=None, atoms=None):
    t = Task(stage.name, stage.version, subject, stage.normalize(params), atoms or {}, tuple(inputs))
    execute(t, depot, registry(stage), force=True)
    doc = depot.result(t.key)
    return t, doc, {contract.parse_artifact_id(a["id"])[1]: a for a in doc["artifacts"]}


SVS = {"$script": SCRIPT.format("SoundVolumeSettings"), "_collection": [{"CategoryName": "Bgm", "DefaultVolume": 0.7}]}
ACD = {
    "$script": SCRIPT.format("AppConfigDefaultData"),
    "_defaultData": {f"<{f}>k__BackingField": 0.5 for f in liveaudio.LIVE_VOLUMES.values()},
}


# ---------------------------------------------------------------- subjects and keys
def test_the_stages_name_their_subjects_keys_and_manifests(tmp_path):
    env = Env(Depot(tmp_path / "d"), {"master": master_dir(tmp_path)})
    music, sounds = MusicStage(), SoundsStage()
    assert music.keys("7", env) == music.cue_sheets("7", env) == ["Cri/Sound/bgm_7"]
    assert music.manifest("7") == "music/7.json"
    assert music.select(["Cri/Sound/bgm_7", "Cri/Sound/se1"], env) == ["7"]
    assert music.select(["Cri/Sound/se1"], env) == []
    assert sounds.keys("default", env) == ["Cri/Sound/LiveSe", "Cri/Sound/se1"]
    assert sounds.keys("NoteSePatternId=1,3", env) == ["Cri/Sound/LiveSe", "Cri/Sound/se1", "Cri/Sound/se3"]
    assert sounds.builtins("default", env) == [
        "MonoBehaviour:SoundVolumeSettings",
        "MonoBehaviour:AppConfigDefaultData",
    ]
    assert sounds.select(["Cri/Sound/LiveSe", "Cri/Sound/se1", "Cri/Sound/bgm_7"], env) == ["default"]
    assert sounds.select(["Cri/Sound/se1"], env) == []
    assert sounds.manifest("NoteSePatternId=1,3") == "sounds/NoteSePatternId=1,3.json"
    assert music.select(["Cri/Sound/bgm_7"], Env(env.depot, {"master": None})) == []
    with pytest.raises(ValueError, match="not a music id"):
        music.keys("x", env)
    with pytest.raises(ValueError, match="not a live sounds subject"):
        sounds.keys("NoteSePatternId=3,1", env)
    with pytest.raises(KeyError, match="sound set"):
        sounds.keys("NoteSePatternId=1,9", env)


def test_the_note_stages_name_their_subjects_keys_and_manifests(tmp_path):
    env = Env(Depot(tmp_path / "d"), {"master": master_dir(tmp_path)})
    notes, skins, effects = NotesStage(), NoteSkinStage(), NoteEffectStage()
    keys = notes.keys("default", env)
    assert keys == livenotes.notes_keys() and livenotes.LANE_EFFECT_KEY in keys and len(keys) == 17
    assert notes.select(keys, env) == ["default"] and notes.select(keys[1:], env) == []
    assert notes.select(keys, Env(env.depot, {"master": None})) == []
    assert notes.manifest("default") == "notes/default.json"
    with pytest.raises(ValueError, match="the only subject is default"):
        notes.keys("NoteDesignId=1", env)
    flicks = [livenotes.NOTE_PREFABS[v] for v in livenotes.FLICK_SKIN_UNITS]
    assert skins.keys("skin002", env) == sorted([livenotes.skin_key("skin002"), *flicks])
    held = [*keys, livenotes.skin_key("skin001"), livenotes.skin_key("skin002"), "Live/Note/x y/LiveNoteSkinAsset"]
    assert skins.select(held, env) == ["skin001", "skin002"]  # a name that is no path segment is left out
    assert skins.select([livenotes.skin_key("skin001")], env) == []  # without the flick views
    assert skins.manifest("skin001") == "noteskins/skin001.json"
    fx = [livenotes.effect_settings_key(n) for n in ("effect001", "effect001Light")]
    assert effects.select([*fx, "Effect/Live/NoteEffect/effect001/note_normal"], env) == ["effect001", "effect001Light"]
    assert effects.keys("effect001Light", env) == [fx[1]]
    assert effects.manifest("effect001Light") == "noteeffects/effect001Light.json"
    with pytest.raises(ValueError, match="not a note skin or effect set name"):
        effects.keys("a/b", env)


def scene_master_dir(tmp_path):
    """master_dir with the tables of the scene's lane skins, bands, quality settings and judgement sprites."""
    md = master_dir(tmp_path)
    for name in ("MasterLiveQualitySettings", "MasterLiveLaneSkin", "MasterBand", "MasterLiveJudgementSprite"):
        (md / f"{name}.json").write_text(json.dumps({"_allData": ls.master()[name]}), encoding="utf-8")
    return md


def test_the_scene_stage_names_its_subject_keys_and_manifest(tmp_path):
    env = Env(Depot(tmp_path / "d"), {"master": scene_master_dir(tmp_path)})
    stage = SceneStage()
    keys = stage.keys("default", env)
    assert livescene.SCENE_KEY in keys and "Band/2/timeline/live_start_playable_timeline" in keys
    assert stage.builtins("default", env) == ["GraphicsSettings", "QualitySettings", "PlayerSettings"]
    assert stage.select([livescene.SCENE_KEY], env) == ["default"]
    assert stage.select([k for k in keys if k != livescene.SCENE_KEY], env) == []
    assert stage.select(keys, Env(env.depot, {"master": None})) == []
    assert stage.manifest("default") == "scenes/default.json"
    with pytest.raises(ValueError, match="the only subject is default"):
        stage.keys("band/1", env)


def test_the_scene_product_keeps_what_the_player_reads(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": scene_master_dir(tmp_path)})
    stage = SceneStage()
    record = master_record(env, stage, "default")
    objects = stored(depot, contract.encode(ls.table(depot, contract.loads(depot.input_bytes(record)))), "objects")
    _, _, arts = run(depot, stage, "default", [objects, record])
    assert set(arts) == {"scene.json", "objects"} and arts["scene.json"]["semantics"]["kind"] == "live.scene"
    scene = contract.loads(depot.read(arts["scene.json"]["content"]["sha256"]))
    assert scene["bands"]["1"]["timeline"] == ls.TL and scene["settings"]["laneSkin"] == "skin001"
    kept = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]
    assert ls.SCRIPT not in kept and ls.LIVE in kept and ls.GRAPHICS in kept


def live_master_dir(tmp_path):
    """scene_master_dir with the tables of music 7's charts, title, bands and vocal characters."""
    md = scene_master_dir(tmp_path)
    tables = lh.master()
    tables["MasterLiveMusic"][0]["_musicSoundID"] = 900  # bgm_7 of master_dir
    for name, rows in tables.items():
        if name not in ("MasterSound", "MasterSoundCueSheet"):  # master_dir's sounds
            (md / f"{name}.json").write_text(json.dumps({"_allData": rows}), encoding="utf-8")
    return md


def test_a_live_names_its_subjects_keys_parts_and_manifest(tmp_path):
    env = Env(Depot(tmp_path / "d"), {"master": live_master_dir(tmp_path)})
    stage = LiveStage()
    expert, normal, jacket = f"Live/MusicScore/{lh.EXPERT}", f"Live/MusicScore/{lh.NORMAL}", "Image/Jacket/jacket7"
    assert stage.keys("7/expert", env) == stage.keys("7/expert/1", env) == [jacket, expert]
    assert stage.select([expert, normal, jacket], env) == ["7/expert", "7/normal"]
    assert stage.select([expert, normal], env) == []  # without the jacket
    assert stage.select([expert, jacket], Env(env.depot, {"master": None})) == []
    assert stage.live_record("7/expert", env)["band"]["band"] == 2
    assert (
        stage.manifest("7/expert") == "lives/7_expert.json" and stage.manifest("7/expert/1") == "lives/7_expert_1.json"
    )
    assert stage.parts("7/expert", env) == {
        live.SCENE: ["default"],
        live.NOTES: ["default"],
        live.NOTESKIN: ["skin001"],
        live.NOTEEFFECT: ["fxA"],
        live.SOUNDS: ["default"],
        live.MUSIC: ["7"],
    }
    with pytest.raises(ValueError, match="not a live subject"):
        stage.manifest("7")
    with pytest.raises(KeyError, match="no easy chart"):
        stage.keys("7/easy", env)


def test_the_live_product_holds_the_chart_and_the_manifests_of_its_parts(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": live_master_dir(tmp_path)})
    stage = LiveStage()
    record = master_record(env, stage, "7/expert")
    assert contract.loads(depot.input_bytes(record))["parts"] == {
        "music": "music/7.json",
        "noteeffect": "noteeffects/fxA.json",
        "notes": "notes/default.json",
        "noteskin": "noteskins/skin001.json",
        "scene": "scenes/default.json",
        "sounds": "sounds/default.json",
    }
    objects = stored(depot, contract.encode(lh.table(depot)), "objects")
    _, _, arts = run(depot, stage, "7/expert", [objects, record])
    kinds = {n: a["semantics"]["kind"] for n, a in arts.items() if n != "objects"}
    assert kinds == {
        "live.json": "live",
        "chart.json": "live.chart",
        "notes.json": "live.notes.chart",
        "notes.mirror.json": "live.notes.chart",
    }
    doc = contract.loads(depot.read(arts["live.json"]["content"]["sha256"]))
    assert doc["jacket"] == lh.JACKET and doc["parts"]["scene"] == "scenes/default.json"
    assert depot.read(arts["chart.json"]["content"]["sha256"]) == contract.encode(lh.CHART)
    kept = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]
    assert sorted(kept) == [lh.JACKET, lh.TEXTURE]


def test_sounds_subjects_name_the_offered_sets():
    assert liveaudio.sounds_subject() == "default" and liveaudio.sounds_groups("default") == ()
    assert liveaudio.sounds_subject((3, 1)) == "NoteSePatternId=1,3"
    assert liveaudio.sounds_groups("NoteSePatternId=1,3") == (1, 3)
    for bad in ("NoteSePatternId=", "NoteSePatternId=a", "Other=1", "NoteSePatternId=1,1"):
        with pytest.raises(ValueError):
            liveaudio.sounds_groups(bad)


def test_the_music_parameter_is_a_web_format(tmp_path):
    assert MusicStage().normalize(None) == {"audio": "aac"}
    with pytest.raises(ValueError, match="audio 'wav'"):
        MusicStage().normalize({"audio": "wav"})
    assert live.encode_atoms("flac") == {}


# ---------------------------------------------------------------- the products
def test_the_music_product_keeps_the_decoded_flac(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = MusicStage()
    sheets, doc = sheet_inputs(depot, ["bgm_7"])
    t, res, arts = run(depot, stage, "7", [table(depot), *sheets, master_record(env, stage, "7")], {"audio": "flac"})
    assert {p: a["semantics"]["kind"] for p, a in arts.items()} == {
        "audio/bgm_7/bgm_7.flac": "audio.bgm",
        "music.json": "live.music",
        "objects": "product.objects",
    }
    flac = doc["Cri/Sound/bgm_7"]["files"]["bgm_7.flac"]
    assert arts["audio/bgm_7/bgm_7.flac"]["content"]["sha256"] == flac["sha256"]  # the decode, not a copy
    music = contract.loads(depot.read(arts["music.json"]["content"]["sha256"]))
    assert music["format"] == liveaudio.MUSIC_FORMAT and music["musicId"] == 7
    assert music["music"] == {"soundId": 900, "volume": 1.0, "startTimeSec": 0.0}
    entry = music["sounds"]["900"]
    assert entry["categories"] == ["LiveBgm"] and entry["volume"] == pytest.approx(0.8)
    (layer,) = entry["layers"]
    assert layer["file"] == "audio/bgm_7/bgm_7.flac" and layer["samples"] == 48000 and layer["volume"] == 0.5
    assert "encoderDelay" not in layer
    assert contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"] == {}


def test_the_music_product_in_aac_records_the_encoder_delay(tmp_path, monkeypatch):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = MusicStage()
    monkeypatch.setattr(cristages, "tool_id", lambda which, exe: f"{which}-fake/1")
    monkeypatch.setattr(live, "_ffmpeg", lambda: "ffmpeg")
    seen = []

    def encode(ffmpeg, src, dst, fmt):
        seen.append((src.read_bytes(), fmt))
        return mp4(2112)

    monkeypatch.setattr(live, "web_encode", encode)
    sheets, _ = sheet_inputs(depot, ["bgm_7"])
    inputs = [table(depot), *sheets, master_record(env, stage, "7")]
    atoms = {"audio.encode": "ffmpeg-fake/1"}
    assert live.encode_atoms("aac") == atoms
    _, _, arts = run(depot, stage, "7", inputs, None, atoms)
    assert seen == [(b"fLaC bgm_7 1", "aac")]
    assert depot.read(arts["audio/bgm_7/bgm_7.m4a"]["content"]["sha256"]) == mp4(2112)
    music = contract.loads(depot.read(arts["music.json"]["content"]["sha256"]))
    (layer,) = music["sounds"]["900"]["layers"]
    assert layer["file"] == "audio/bgm_7/bgm_7.m4a" and layer["encoderDelay"] == 2112
    with pytest.raises(IncompatibleTask, match="audio encoder"):
        run(depot, stage, "7", inputs, None, {"audio.encode": "ffmpeg-other/1"})


def test_the_notes_product_keeps_what_the_player_reads(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = NotesStage()
    objects = stored(depot, contract.encode(ln.table(depot, ln.NOTES_KEYS)), "objects")
    t, doc, arts = run(depot, stage, "default", [objects, master_record(env, stage, "default")])
    assert set(arts) == {"notes.json", "objects"} and arts["notes.json"]["semantics"]["kind"] == "live.notes"
    notes = contract.loads(depot.read(arts["notes.json"]["content"]["sha256"]))
    assert notes["prefabs"]["tap_note_view"] == ln.prefab_id("tap_note_view")
    assert notes["settings"]["skins"] == {"1": "skin001", "2": "skin002", "3": "skin003"}
    kept = contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"]
    assert ln.SCRIPT not in kept and ln.prefab_id("tap_note_view") in kept
    assert sorted(kept[ln.SHADER]["files"]) == [
        "index",
        "json",
        "program:gles3/s0p0_progVertex_0",
        "program:gles3/s0p1_progVertex_1",
    ]


def test_the_sounds_product(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = SoundsStage()
    subject = "NoteSePatternId=1,2"
    sheets, doc = sheet_inputs(depot, ["LiveSe", "se1", "se2"])
    acf = cri.acf({"LiveNotesSe": (3, 0), "LiveSe": (4, 0), "Bgm": (0, 0)})
    inputs = [
        table(depot, [(94, SVS), (97, ACD)]),
        *sheets,
        stored(depot, acf, f"apk:{ACF}"),
        master_record(env, stage, subject),
    ]
    _, res, arts = run(depot, stage, subject, inputs)
    sounds = contract.loads(depot.read(arts["sounds.json"]["content"]["sha256"]))
    assert sounds["format"] == liveaudio.SOUNDS_FORMAT
    # the default set (1), the live SE of every result and the other offered set (2)
    assert set(sounds["sounds"]) == {
        *(str(1000 + t) for t in range(1, 15)),
        *(str(500 + t) for t in liveaudio.LIVE_SE_SOUNDS),
        *(str(2000 + t) for t in range(1, 15)),
    }
    assert sounds["noteSe"]["patternId"] == 1 and list(sounds["noteSe"]["groups"]) == ["1", "2"]
    assert sounds["categories"] == {"Bgm": 0.7, **{f"{c}Config": 0.5 for c in liveaudio.LIVE_VOLUMES}}
    assert sounds["acf"]["name"] == "Game" and sounds["react"] == []
    assert sounds["liveSe"]["9"] == 509 and sounds["timeline"] == liveaudio.TIMELINE
    slide = sounds["sounds"]["1007"]
    assert [x["file"] for x in slide["layers"]] == ["audio/se1/se1_7.flac", "audio/se1/se1_7_8.flac"]
    cheer = sounds["sounds"]["509"]
    assert cheer["layers"][0]["busSends"] == {"MasterOut": 0.5} and cheer["categories"] == ["LiveSe"]
    files = {p for p in arts if p.startswith("audio/")}
    assert len(files) == 2 * (14 + 1) + 6  # sets 1 and 2 (two layers of type 7), the live SE
    for p in files:
        sheet, name = p.split("/")[1:]
        assert arts[p]["content"]["sha256"] == doc[f"Cri/Sound/{sheet}"]["files"][name]["sha256"]
        assert arts[p]["semantics"]["kind"] == "audio.se"
    assert contract.loads(depot.read(arts["objects"]["content"]["sha256"]))["objects"] == {}


def test_the_sound_settings_are_one_object_each(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = SoundsStage()
    sheets, _ = sheet_inputs(depot, ["LiveSe", "se1"])
    acf = stored(depot, cri.acf({"LiveSe": (4, 0)}), f"apk:{ACF}")
    inputs = [table(depot, [(94, SVS)]), *sheets, acf, master_record(env, stage, "default")]
    with pytest.raises(ValueError, match="AppConfigDefaultData: 0 objects"):
        run(depot, stage, "default", inputs)


def test_a_cue_sheet_that_was_not_decoded_makes_the_product_unsupported(tmp_path):
    depot = Depot(tmp_path / "d")
    env = Env(depot, {"master": master_dir(tmp_path)})
    stage = MusicStage()
    sheets, doc = sheet_inputs(depot, ["bgm_7"])
    doc["Cri/Sound/bgm_7"] = {
        "acb": doc["Cri/Sound/bgm_7"]["acb"],
        "status": "unsupported",
        "reason": "unsupported.cri.awb_external",
        "files": {},
    }
    sheets[-1] = stored(depot, contract.encode(doc), assemble.SHEETS_INPUT)
    _, res, arts = run(depot, stage, "7", [table(depot), *sheets, master_record(env, stage, "7")], {"audio": "flac"})
    (item,) = res["items"]
    assert item["status"] == "unsupported" and item["reason"]["code"] == "unsupported.cri.awb_external"


# ---------------------------------------------------------------- cue sheets of a product
class Sheets(ProductStage):
    """A product reading the cue sheets of its keys: the files `cues.json` of each."""

    name = "toy.sheets"
    version = 1

    def __init__(self, keys):
        super().__init__()
        self.sheet_keys = list(keys)

    def keys(self, subject, env):
        return self.sheet_keys

    def cue_sheets(self, subject, env):
        return self.sheet_keys

    def assemble(self, task, objects) -> Assembly:
        files = {f"{k}/cues.json": (objects.sheet(k).file("cues.json"), "json", "cues") for k in self.sheet_keys}
        return Assembly(files, set())


def test_cue_sheets_are_the_acbs_the_request_holds(tmp_path):
    """A cue sheet held in a bundle is the `acb` file of an object of its key; a raw one is the raw file of the key
    that cri.audio decoded. The product reads cri.audio's files of both, without reading their bytes again."""
    depot = Depot(tmp_path / "d")
    raw_acb, held_acb = b"@UTF raw sheet", b"@UTF held sheet"
    with fake_tools():
        env = planning(
            depot,
            tmp_path,
            {"cri_assets_cri/sound/raw": raw_acb},
            keys={"Cri/Sound/Raw": ["cri_assets_cri/sound/raw"]},
        )
        export_result(env, "sheets_a", {"Held": held_acb})
        env.facts["catalogs"] = {"main": []}
        addresses(depot, env, {"Cri/Sound/Held": ["CAB-sheets_a:1"], "Cri/Sound/Raw": []})
        stage = Sheets(["Cri/Sound/Held", "Cri/Sound/Raw"])
        env.facts["products"] = {stage.name: ["x"]}
        env.facts["roots"] = assemble.request({stage.name: stage}, {stage.name: ["x"]}, env)
        audio = AudioStage()
        for s in audio.subjects(env):
            t = describe(audio, s, None, env)
            execute(t, depot, registry(audio), force=True)
            env.done(t.id, t.key)
        held, raw = contract.sha256(held_acb), contract.sha256(raw_acb)
        sheets = stage.sheets("x", env)
        assert {k: (i.role, i.sha256, tid) for k, (i, tid) in sheets.items()} == {
            "Cri/Sound/Held": ("acb:Cri/Sound/Held", held, f"cri.audio:{held}"),
            "Cri/Sound/Raw": ("acb:Cri/Sound/Raw", raw, f"cri.audio:{raw}"),
        }
        assert {f"cri.audio:{held}", f"cri.audio:{raw}"} <= set(stage.depends("x", env))
        t = describe(stage, "x", None, env)
        assert [i.role for i in t.inputs] == ["acb:Cri/Sound/Held", "acb:Cri/Sound/Raw", "objects", "sheets"]
        execute(t, depot, registry(stage), force=True)
    res = depot.result(t.key)
    cues = {a["id"]: a["content"]["sha256"] for a in res["artifacts"]}
    for key, sha in (("Cri/Sound/Held", held), ("Cri/Sound/Raw", raw)):
        decoded = env.artifact(f"cri.audio:{sha}", "cues.json")["content"]["sha256"]
        assert cues[f"toy.sheets:x#{key}/cues.json"] == decoded


def test_a_cue_sheet_the_request_does_not_hold_is_an_error(tmp_path):
    depot = Depot(tmp_path / "d")
    with fake_tools():
        env = planning(depot, tmp_path, {}, keys={"Cri/Sound/Raw": ["cri_assets_cri/sound/raw"]})
        export_result(env, "sheets_a", {"Held": b"@UTF held"})
        env.facts["catalogs"] = {"main": []}
        addresses(depot, env, {"Cri/Sound/Held": [], "Cri/Sound/Raw": []})
        env.facts["roots"] = {"keys": [], "objects": []}
        with pytest.raises(ValueError, match="Cri/Sound/Held: no ACB"):
            Sheets(["Cri/Sound/Held"]).sheets("x", env)
        with pytest.raises(ValueError, match="does not hold its file cri_assets_cri/sound/raw"):
            Sheets(["Cri/Sound/Raw"]).sheets("x", env)


def test_planning_the_stages_does_not_import_unitypy():
    code = (
        "import sys, nnnotes.pipeline.live as m; m.MusicStage(); m.SoundsStage(); m.NotesStage(); "
        "m.NoteSkinStage(); m.NoteEffectStage(); m.SceneStage(); "
        "print(sorted(x for x in ('UnityPy', 'numpy', 'PIL') if x in sys.modules))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stderr[-2000:]


def test_web_formats_and_the_mp4_encoder_delay():
    assert encoders.DEFAULT_AUDIO_FORMAT in encoders.WEB_AUDIO and encoders.WEB_AUDIO["flac"][1] is None
    assert encoders.mp4_priming(mp4(1024)) == 1024 and encoders.mp4_priming(b"") == 0
