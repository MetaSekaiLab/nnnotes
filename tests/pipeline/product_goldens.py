"""Synthetic task inputs for product-stage golden records."""

from nnnotes.pipeline import contract, live, live2d, readsets, siteassets, siteindex, siteproducts, spot, story
from nnnotes.pipeline.contract import Task
from nnnotes.pipeline.exportfiles import EpisodeStage
from nnnotes.pipeline.stages import Env
from tests.helpers import cri, live2dmodel, livenotes, livescene, spotobjects, storyui, synth
from tests.helpers import live as live_objects
from tests.helpers import story as story_objects
from tests.pipeline import test_live, test_spot, test_story
from tests.pipeline.test_live import master_record, stored


def task(stage, subject, inputs, params=None, atoms=None):
    return Task(stage.name, stage.version, subject, stage.normalize(params), atoms or {}, tuple(inputs))


def objects(depot, table):
    return stored(depot, contract.encode(table), "objects")


def environment(depot, name, builder):
    directory = depot.root / "fixtures" / name
    directory.mkdir(parents=True)
    return Env(depot, {"master": builder(directory)})


def provider(stage, build, libraries=()):
    return {"stage": stage, "fixtures": {"shared-objects": build}, "libraries": libraries}


def model():
    stage = live2d.ModelStage()
    return provider(stage, lambda d: task(stage, live2dmodel.KEY, [objects(d, live2dmodel.table(d))]), ["Pillow"])


def cubism():
    stage = live2d.CubismStage()
    return provider(stage, lambda d: task(stage, live2dmodel.KEY, [objects(d, live2dmodel.table(d))]), ["Pillow"])


def room():
    stage = spot.RoomStage()

    def build(depot):
        table = spotobjects.table(depot, spotobjects.room_objects(), [spotobjects.ROOM_ROOT])
        return task(stage, spotobjects.BACKGROUND, [objects(depot, table)])

    return provider(stage, build, ["Pillow", "numpy"])


def spot_product():
    stage = spot.SpotStage()

    def build(depot):
        env = environment(depot, "spot", test_spot.master_dir)
        table = spotobjects.table(depot, spotobjects.situation_objects(), [spotobjects.SIT_ROOT])
        return task(
            stage, spotobjects.SITUATION, [objects(depot, table), master_record(env, stage, spotobjects.SITUATION)]
        )

    return provider(stage, build, ["Pillow"])


def live_scene():
    stage = live.SceneStage()

    def build(depot):
        env = environment(depot, "scene", test_live.scene_master_dir)
        record = master_record(env, stage, "default")
        table = livescene.table(depot, contract.loads(depot.input_bytes(record)))
        return task(stage, "default", [objects(depot, table), record])

    return provider(stage, build)


def live_notes():
    stage = live.NotesStage()

    def build(depot):
        env = environment(depot, "notes", test_live.master_dir)
        table = livenotes.table(depot, livenotes.NOTES_KEYS)
        return task(stage, "default", [objects(depot, table), master_record(env, stage, "default")])

    return provider(stage, build)


def live_music():
    stage = live.MusicStage()

    def build(depot):
        env = environment(depot, "music", test_live.master_dir)
        sheets, _ = test_live.sheet_inputs(depot, ["bgm_7"])
        return task(stage, "7", [test_live.table(depot), *sheets, master_record(env, stage, "7")], {"audio": "flac"})

    return provider(stage, build)


def live_sounds():
    stage = live.SoundsStage()

    def build(depot):
        env = environment(depot, "sounds", test_live.master_dir)
        subject = "NoteSePatternId=1,2"
        sheets, _ = test_live.sheet_inputs(depot, ["LiveSe", "se1", "se2"])
        acf = cri.acf({"LiveNotesSe": (3, 0), "LiveSe": (4, 0), "Bgm": (0, 0)})
        return task(
            stage,
            subject,
            [
                test_live.table(depot, [(94, test_live.SVS), (97, test_live.ACD)]),
                *sheets,
                stored(depot, acf, f"apk:{test_live.ACF}"),
                master_record(env, stage, subject),
            ],
        )

    return provider(stage, build)


def live_product():
    stage = live.LiveStage()

    def fixture(name, languages):
        def build(depot):
            env = environment(depot, name, test_live.live_master_dir)
            env.params = stage.normalize({"languages": languages})
            return task(
                stage,
                "7/expert",
                [objects(depot, live_objects.table(depot, gz=False)), master_record(env, stage, "7/expert")],
                env.params,
            )

        return build

    return {
        "stage": stage,
        "fixtures": {"plain": fixture("plain", []), "localized": fixture("localized", ["ja", "en"])},
        "libraries": ["Pillow"],
    }


def episode_provider(stage):
    def fixture(name, languages):
        def build(depot):
            env = environment(depot, name, story_objects.master_dir)
            env.params = stage.normalize({"languages": languages})
            return task(
                stage,
                "1",
                [objects(depot, story_objects.episode_table(depot)), master_record(env, stage, "1")],
                env.params,
            )

        return build

    return {
        "stage": stage,
        "fixtures": {"plain": fixture("plain", []), "localized": fixture("localized", ["ja", "en"])},
    }


def story_product():
    return episode_provider(story.StoryStage())


def episode():
    return episode_provider(EpisodeStage())


def story_chat():
    stage = story.ChatStage()
    return provider(stage, lambda d: task(stage, "default", [objects(d, story_objects.chat_table(d))]))


def story_sound():
    stage = story.SoundStage()

    def build(depot):
        inputs, _ = test_story.sheet(depot, "adv_voice_1")
        return task(stage, "adv_voice_1", [test_live.table(depot), *inputs], {"audio": "flac"})

    return provider(stage, build)


def story_ui():
    stage = story.UiStage()

    def build(depot):
        fonts, keys = storyui.font_objects()
        chat_key, chat_oid, chat = storyui.chat_window()
        table = storyui.table_doc(
            depot,
            objs={**storyui.objects(), **fonts, chat_oid: chat},
            keys={**storyui.table_keys(), **keys, chat_key: [chat_oid]},
        )
        return task(
            stage,
            "zh-Hant",
            [
                objects(depot, table),
                stored(depot, contract.encode(storyui.table_doc(depot)), "ui.objects"),
                stored(depot, contract.encode({"CAB-font": "font.bundle"}), "font-bundles"),
                stored(depot, synth.axml(["versionName", "manifest", "1.2.3"], 2), "apk:AndroidManifest.xml"),
                stored(
                    depot,
                    contract.encode({"texts": {str(i): f"zh {i}" for i in (*range(101, 111), 301, 302)}}),
                    "master",
                ),
            ],
        )

    return provider(stage, build)


def story_video():
    stage = story.VideoStage()
    return provider(
        stage, lambda d: task(stage, test_story.VIDEO_KEY, [test_story.video_table(d), *test_story.movie_inputs(d)])
    )


def note_skin():
    stage = live.NoteSkinStage()
    return provider(stage, lambda d: task(stage, "skin001", [objects(d, livenotes.table(d, livenotes.SKIN_KEYS))]))


def note_effect():
    stage = live.NoteEffectStage()
    return provider(
        stage, lambda d: task(stage, "effect001Light", [objects(d, livenotes.table(d, livenotes.EFFECT_KEYS))])
    )


def story_scene():
    from tests.content import test_storyscene as scene

    stage = story.SceneStage()
    return provider(
        stage, lambda d: task(stage, "default", [objects(d, livenotes.table(d, scene.keys(), scene.objects()))])
    )


def story_asset():
    from tests.content import test_storyasset as asset

    stage = story.AssetStage()
    return provider(
        stage,
        lambda d: task(stage, asset.KEY, [objects(d, livenotes.table(d, {asset.KEY: [asset.PREFAB]}, asset.SMALL))]),
    )


def ui_pack():
    from nnnotes.pipeline.ui import UiStage
    from tests.content import test_ui

    stage = UiStage()

    def build(depot):
        directory = depot.root / "fixture"
        directory.mkdir(parents=True)
        other, graph = test_ui.graph(directory)
        for entry in graph.doc["objects"].values():
            for file in entry["files"].values():
                depot.put(other.read(file["sha256"]))
        return task(stage, test_ui.KEY, [objects(depot, graph.doc)])

    return provider(stage, build)


def host():
    from nnnotes.pipeline.storyhost import HostStage
    from tests.content.test_storyhostproduct import host_table

    stage = HostStage()

    def fixture(kind):
        def build(depot):
            table, record, _ = host_table(depot, kind)
            master = {"host": record, "parts": {"room": "rooms/room.json", "spot": "spots/day.json"}}
            return task(
                stage,
                "home/7" if kind == "home" else "afterlive",
                [objects(depot, table), stored(depot, contract.encode(master), "master")],
            )

        return build

    return {"stage": stage, "fixtures": {kind: fixture(kind) for kind in ("home", "afterlive")}}


def simple_ui():
    from tests.content.test_storysimpleui import simple_keys

    stage = story.SimpleUiStage()

    def build(depot):
        base = story_ui()["fixtures"]["shared-objects"](depot)
        inputs = [i for i in base.inputs if i.role != "ui.objects"]
        inputs.append(stored(depot, contract.encode(storyui.table_doc(depot, keys=simple_keys())), "ui.objects"))
        return task(stage, "zh-Hant", inputs)

    return provider(stage, build)


FONT_LIBRARIES = ("fonttools", "freetype-py", "scipy", "numpy", "Pillow")


def font_provider(stage, fixture, subject, *, extra=None):
    from tests.helpers.fonts import make_font

    def build(source):
        def inputs(depot):
            values = fixture(depot)[0]
            if source == "open":
                data = make_font(depot.root / "font.ttf", underscore=True, extra=extra).read_bytes()
                values += [stored(depot, data, f"font:{code}") for code in ("ja", "zh-Hant")]
            return task(stage, subject, values, {"fonts": source}, story.font_atoms())

        return inputs

    return {
        "stage": stage,
        "fixtures": {source: build(source) for source in ("game", "open")},
        "libraries": FONT_LIBRARIES,
    }


def story_fonts():
    from tests.content.test_storyfontproduct import fixture

    return font_provider(story.FontsStage(), fixture, "1/zh-Hant")


def live_ui():
    from tests.content.test_liveuiproduct import fixture

    return font_provider(live.UiStage(), fixture, "7/expert/zh-Hant", extra={ord(c): "A" for c in "0257"})


class SyntheticLips(story.CriLipsStage):
    def run(self, task, depot):
        from unittest.mock import patch

        from nnnotes.formats import crilips

        version, _, _ = test_story._synthetic_version()
        with patch.object(crilips, "SUPPORTED", (version,)):
            return super().run(task, depot)


def crilips():
    stage = SyntheticLips()

    def build(depot):
        _, rodata, _ = test_story._synthetic_version()
        data = test_story._elf_with_rodata(rodata)
        return task(stage, "default", [test_live.table(depot), stored(depot, data, f"apk:{story.LIBRARY}")])

    return provider(stage, build)


def audio_files():
    from nnnotes.pipeline.exportfiles import AudioFilesStage

    stage = AudioFilesStage()

    def build(depot):
        inputs, sheets = test_story.sheet(depot, "sample")
        cues = {}
        for stream in test_story.STREAMS:
            cues.setdefault(stream["name"], {k: v for k, v in stream.items() if k not in {"name", "stream"}})
        sheets["Cri/Sound/sample"]["files"]["cues.json"] = depot.add(contract.encode(cues), "json")
        inputs[-1] = stored(depot, contract.encode(sheets), "sheets")
        return task(stage, "sample", [test_live.table(depot), *inputs])

    return provider(stage, build)


def shader_files():
    from nnnotes.pipeline.exportfiles import ShaderFilesStage

    stage = ShaderFilesStage()

    def build(depot):
        variant = {
            "name": "Example/Unlit",
            "variants": [
                {
                    "platform": "gles3",
                    "stage": "vertex",
                    "role": "program:gles3/vertex",
                    "file": "Example_Unlit/vertex.glsl",
                }
            ],
        }
        files = {
            "json": (contract.encode({"name": variant["name"], "properties": []}), "json"),
            "index": (contract.encode(variant), "json"),
            "program:gles3/vertex": (b"void main() {}", "glsl"),
        }
        artifacts = {
            contract.artifact_id("unity.export:shader", role): {"content": depot.add(body, ext)}
            for role, (body, ext) in files.items()
        }
        records = [
            {
                "source": "example.bundle",
                "items": [{"class": "Shader", "status": "exported", "artifacts": list(artifacts)}],
                "artifacts": artifacts,
            }
        ]
        return task(stage, "selected", [stored(depot, contract.encode(records), "records")])

    return provider(stage, build)


def document_provider(stage, source, spec):
    def build(depot):
        return task(
            stage,
            "example",
            [stored(depot, contract.encode(source), "source"), stored(depot, contract.encode(spec), "spec")],
        )

    return provider(stage, build)


def site_references():
    source = {"parts": {"ui": {"ja": "ui/a.json", "en": "ui/b.json"}, "models": ["models/a.json"]}}
    spec = {
        "languagePaths": [["parts", "ui"]],
        "languages": ["en"],
        "paths": [["parts", "ui"], ["parts", "models"]],
        "routes": {"ui/b.json": "products/ui.json", "models/a.json": "products/model.json"},
    }
    return document_provider(siteproducts.ReferencesStage(), source, spec)


def site_product():
    return {
        "schema": contract.PRODUCT,
        "product": "live",
        "subject": "7/expert",
        "files": {
            "live.json": {"asset": "assets/source.json", "size": 2},
            "music.flac": {"asset": "assets/music.flac", "size": 4},
        },
        "objects": {
            "model:1": {
                "class": "TextAsset",
                "name": "Chart",
                "files": {
                    "json": {"asset": "assets/chart.json", "size": 2},
                    "data": {"asset": "assets/chart.bin", "size": 1},
                },
            }
        },
    }


def site_manifest():
    source = site_product()
    spec = {
        "files": {name: {**entry, "asset": "assets/rewritten-" + name} for name, entry in source["files"].items()},
        "objects": {"model:1": source["objects"]["model:1"]["files"]},
        "root": "../../",
        "language": "en",
        "variants": ["notes/other.json"],
        "metadata": {
            "musicId": 7,
            "difficulty": "expert",
            "audio": False,
            "audioFormat": None,
            "quality": 1,
            "flows": ["direct"],
            "regions": ["one"],
            "chart": {"title": "Song", "notes": 3},
        },
    }
    return document_provider(siteproducts.ManifestStage(), source, spec)


def site_selection():
    return document_provider(
        siteproducts.SelectionStage(), site_product(), {"path": "lives/7_expert.json", "reads": [], "audio": False}
    )


def site_asset():
    from tests.pipeline.test_siteassets import task_for

    stage = siteassets.AssetStage()
    return provider(
        stage,
        lambda d: task_for(
            d,
            b'{"a":[1,2,3],"b":[1,2,3],"c":{"x":true,"label":"text"}}',
            params={"encoding": "none", "split": 10, "omit": [["c", "label"]]},
        ),
    )


def site_index():
    stage = siteindex.IndexStage()
    catalog = {
        "format": siteindex.CATALOG,
        "name": "example",
        "language": "en",
        "regions": [{"id": "one", "name": "One"}, {"id": "two", "name": "Two"}],
        "topics": [
            {
                "kind": "model",
                "id": "sample",
                "region": region,
                "manifest": "products/model.json",
                "entry": {"key": "Character/Model", "names": {"en": label}},
            }
            for region, label in [("one", "Model"), ("two", "Other label")]
        ],
    }
    return provider(stage, lambda d: task(stage, "example", [stored(d, contract.encode(catalog), "catalog")]))


class SyntheticReader(readsets.ReadSetStage):
    def run(self, task, depot):
        from unittest.mock import patch

        with patch.object(readsets, "node_identity", lambda _: "node:synthetic"):
            return super().run(task, depot)


def site_readset():
    from tests.pipeline.test_readsets import SCRIPT

    stage = SyntheticReader()

    def fixture(mode):
        def build(depot):
            inputs = [
                stored(depot, b'{"type":"module"}', "player:package.json"),
                stored(depot, SCRIPT.encode(), "player:scripts/read-set.mjs"),
                stored(depot, b'{"files":["model:1#json","scene.json"]}', "data:lives/one.json"),
                stored(
                    depot,
                    contract.encode(
                        readsets.request("lives/one.json", mode=mode, settings={"Alternate": True}, keys=True)
                    ),
                    "request",
                ),
            ]
            return task(stage, "one", inputs, atoms={"player.node": "node:synthetic"})

        return build

    return {"stage": stage, "fixtures": {mode: fixture(mode) for mode in ("plan", "full")}}
