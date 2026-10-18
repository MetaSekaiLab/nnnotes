"""Product stages (pipeline.assemble) over the fake bundles of test_objexport: a toy product rooted at a prefab and
at an atlas sprite, its object table, its result and the product layout."""

from __future__ import annotations

import json

import pytest

from nnnotes.pipeline import assemble, contract, layout, link, units
from nnnotes.pipeline.contract import Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.objexport import ExportStage, SpriteCropStage
from nnnotes.pipeline.stages import Env, Pending, describe, execute, registry
from tests.pipeline.test_objexport import ATLAS_CAB, FAKE_READER, oid, planning_env


def addresses(depot, env, keys: dict, resources=None, builtins=None, fonts=()) -> None:
    """A committed link.addresses result whose table maps `keys` {catalog key: [object ids]}, `resources`
    {resource path: [object ids]} and `builtins` {selector: [object ids]}; the bundles `fonts` hold a font key."""
    t = Task("link.addresses", 1, "main", {}, {}, ())
    doc = {
        "schema": link.ADDRESSES,
        "keys": {k: [{"objects": ids}] for k, ids in sorted(keys.items())},
        "resources": dict(sorted((resources or {}).items())),
        "builtins": dict(sorted((builtins or {}).items())),
        "objects": {},
        "files": {},
        "bundles": {b: {"keys": [f"EmbFont/{b}"]} for b in fonts},
    }
    rec = contract.artifact(
        contract.artifact_id(t.id, "addresses"),
        depot.add(contract.encode(doc), "json"),
        contract.provenance(t),
        {"kind": "link.addresses", "format": "json"},
    )
    depot.commit(contract.result(t, [rec], []))
    env.done(t.id, t.key)


class Toy(assemble.ProductStage):
    """A product whose roots are the objects of the keys of its subjects: `json` with the objects it uses and a
    `data` file of theirs."""

    name = "toy.thing"
    version = 1
    KIND = "Toy"
    KEYS: dict = {}

    def __init__(self, keys=None):
        super().__init__()
        self.KEYS = dict(Toy.KEYS if keys is None else keys)

    def keys(self, subject, env):
        return [self.KEYS.get(subject, subject)]

    def assemble(self, task, objects):
        files = {
            "json": (
                contract.encode({"subject": task.subject, "objects": objects.ids()}),
                "json",
                "toy.json",
            )
        }
        for i, o in enumerate(sorted(objects.ids("TextAsset"))):
            files[f"data/{i}"] = (objects.bytes(o, "data"), "txt", "toy.data")
        return assemble.Assembly(files)


class Rooted(Toy):
    """A Toy with root objects besides those of its keys."""

    def __init__(self, keys, objects):
        super().__init__(keys)
        self.objects = list(objects)

    def root_objects(self, subject, env):
        return self.objects


class Loaded(Toy):
    """A Toy rooted at the objects of Resources.Load paths alone."""

    def __init__(self, paths):
        super().__init__()
        self.paths = list(paths)

    def keys(self, subject, env):
        return []

    def resources(self, subject, env):
        return self.paths


class Selected(Toy):
    """A Toy rooted at the objects of built-in selectors alone."""

    def __init__(self, selectors):
        super().__init__()
        self.selectors = list(selectors)

    def keys(self, subject, env):
        return []

    def builtins(self, subject, env):
        return self.selectors


def env_for(
    depot, keys=None, names=("all", "atlas"), roots_objects=(), selected=None, resources=None, builtins=None, fonts=()
):
    """A planning environment over the fake bundles with a committed address table and the facts of a request."""
    env = planning_env(depot, names, selected=selected)
    env.facts["catalogs"] = {"main": []}
    addresses(depot, env, keys if keys is not None else {}, resources, builtins, fonts)
    subjects = sorted(keys if keys is not None else {})
    env.facts["products"] = {"toy.thing": subjects}
    env.facts["roots"] = {"keys": subjects, "objects": list(roots_objects)}
    return env


def exported(depot, env) -> dict:
    """Every unity.export task of the request, then every sprite.crop task: {stage: {subject: result}}."""
    out = {"unity.export": {}, "sprite.crop": {}}
    for stage in (ExportStage(FAKE_READER), SpriteCropStage()):
        for s in stage.subjects(env):
            t = describe(stage, s, None, env)
            execute(t, depot, registry(stage), force=True)
            env.done(t.id, t.key)
            out[stage.name][s] = depot.result(t.key)
    return out


def product(depot, env, subject="card", keys=None, stage=None):
    stage = stage or Toy(keys)
    t = describe(stage, subject, None, env)
    execute(t, depot, registry(stage), force=True)
    return t, depot.result(t.key)


def table_of(depot, doc) -> dict:
    rec = next(a for a in doc["artifacts"] if a["id"].endswith("#objects"))
    return contract.loads(depot.read(rec["content"]["sha256"]))


# ---------------------------------------------------------------- the request and the table
def test_a_products_key_is_asked_for_before_anything_runs(tmp_path):
    """request(): the roots fact of the products, from the keys of their subjects alone."""
    depot = Depot(tmp_path / "s")
    stage = Toy({"m/a": "key/a", "m/b": "key/b"})
    stages = {"toy.thing": stage}
    env = Env(depot, {"keys": {}})
    roots = assemble.request(stages, {"toy.thing": ["m/b", "m/a"]}, env)
    assert roots == {"keys": ["key/a", "key/b"], "resources": [], "builtins": [], "objects": []}
    assert stage.subjects(Env(depot, {"products": {"toy.thing": ["m/b"]}})) == ["m/b"]


def test_the_object_table_is_what_the_root_reaches(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    table = table_of(depot, doc)
    assert table["roots"] == [oid(60)] and table["keys"] == {"key/a": [oid(60)]}
    assert table["missing"] and all(m.startswith("CAB-scripts:") for m in table["missing"])  # no census of it
    # the hierarchy: the root exported, its members contained in its prefab
    assert table["objects"][oid(60)] == {
        "status": "exported",
        "class": "GameObject",
        "name": "root",
        "files": {"json": oid_file(table, oid(60), "json")},
    }
    assert table["objects"][oid(61)]["status"] == "contained" and table["objects"][oid(61)]["in"] == oid(60)
    assert table["objects"][oid(65)]["class"] == "MonoBehaviour"
    # what the prefab references across bundles: the texture and the material
    assert {"json", "image"} <= set(table["objects"][oid(10)]["files"])
    assert oid(70) not in table["objects"]  # not referenced
    item = doc["items"][0]
    assert (item["object"], item["status"], item["class"]) == (t.id, "exported", "Toy")
    assert item["artifacts"] == sorted(f"{t.id}#{p}" for p in ("json", "objects"))  # no TextAsset is reached


def oid_file(table, o, role):
    return table["objects"][o]["files"][role]


def test_a_reached_object_that_no_census_holds_is_missing(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]}, roots_objects=["CAB-none:1"])
    exported(depot, env)
    _, doc = product(depot, env, "m/a", {"m/a": "key/a"}, stage=Rooted({"m/a": "key/a"}, ["CAB-none:1"]))
    table = table_of(depot, doc)
    assert "CAB-none:1" in table["missing"] and "CAB-none:1" in table["roots"]


def test_a_root_of_a_unit_the_request_leaves_out_is_an_error(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]}, selected=["atlas"])
    env.facts["roots"] = {"keys": [], "objects": []}  # the request does not start from the product's key
    with pytest.raises(ValueError, match="no requested unit"):
        describe(Toy({"m/a": "key/a"}), "m/a", None, env)


def test_the_request_roots_select_the_units_and_nothing_else(tmp_path):
    """A request for one prefab exports its units, not every unit of its bundle."""
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]}, names=("all",), selected=[])
    subjects = ExportStage(FAKE_READER).subjects(env)
    assert subjects == ["all:10", "all:60"]  # the prefab and the texture it references


def test_a_resources_load_path_is_a_root(tmp_path):
    """A product's Resources.Load paths name its roots through the address table, ignoring case."""
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {}, names=("all",), selected=[], resources={"shaders/thing": [oid(60)]})
    stage = Loaded(["Shaders/Thing"])
    env.facts["roots"] = assemble.request({"toy.thing": stage}, {"toy.thing": ["m/a"]}, env)
    assert env.facts["roots"] == {"keys": [], "resources": ["Shaders/Thing"], "builtins": [], "objects": []}
    assert ExportStage(FAKE_READER).subjects(env) == ["all:10", "all:60"]
    exported(depot, env)
    assert stage.depends("m/a", env) == ["link.addresses:main", "unity.export:all:10", "unity.export:all:60"]
    _, doc = product(depot, env, "m/a", stage=stage)
    assert table_of(depot, doc)["roots"] == [oid(60)] and table_of(depot, doc)["keys"] == {}


def test_a_builtin_selector_is_a_root(tmp_path):
    """A product's built-in selectors name its roots through the address table."""
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {}, names=("all",), selected=[], builtins={"MonoBehaviour:Settings": [oid(60)]})
    stage = Selected(["MonoBehaviour:Settings", "MonoBehaviour:Gone"])
    env.facts["roots"] = assemble.request({"toy.thing": stage}, {"toy.thing": ["m/a"]}, env)
    assert env.facts["roots"] == {
        "keys": [],
        "resources": [],
        "builtins": ["MonoBehaviour:Gone", "MonoBehaviour:Settings"],
        "objects": [],
    }
    assert ExportStage(FAKE_READER).subjects(env) == ["all:10", "all:60"]
    exported(depot, env)
    assert stage.depends("m/a", env) == ["link.addresses:main", "unity.export:all:10", "unity.export:all:60"]
    _, doc = product(depot, env, "m/a", stage=stage)
    assert table_of(depot, doc)["roots"] == [oid(60)]


def test_sprite_images_come_from_the_crop_of_their_atlas(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(13)]}, names=("all", "atlas"), roots_objects=[f"{ATLAS_CAB}:99"])
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    table = table_of(depot, doc)
    sprite = table["objects"][oid(13)]
    assert set(sprite["files"]) == {"json", "image"}  # the image from sprite.crop
    assert table["objects"][f"{ATLAS_CAB}:99"]["class"] == "SpriteAtlas"
    # the request starts from the atlas, so its own sprite is exported; the product reaches the atlas through sprite
    # 13 and not that sprite
    assert f"{ATLAS_CAB}:97" not in table["objects"] and units.table(env).unit("atlas:97")
    crop = [c for c in [*doc["artifacts"]] if c["semantics"]["kind"] == "sprite.meta"]
    assert crop == []  # the sprite's meta belongs to its export result, not to the product
    assert f"{t.id}#objects" in {a["id"] for a in doc["artifacts"]}


def test_the_font_fence_cuts_a_reference_from_an_unfenced_bundle(tmp_path):
    """The units of an unfenced bundle do not reach a fenced bundle's unit through a reference: the object table lists
    the objects its reached objects reference that way as fenced, and the sprite of the unfenced bundle takes no image
    from the atlas the product does not reach."""
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(13)]}, fonts=["atlas"])
    exported(depot, env)
    _, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    table = table_of(depot, doc)
    atlas = f"{ATLAS_CAB}:99"
    assert table["fenced"] == {atlas: {"class": "SpriteAtlas", "name": "remote"}}
    assert atlas not in table["objects"] and atlas not in table["missing"]
    assert set(table["objects"][oid(13)]["files"]) == {"json"}
    objects = assemble.Objects(table, depot)
    assert objects.fenced(atlas) == {"class": "SpriteAtlas", "name": "remote"} and objects.fenced(oid(13)) is None


def test_the_font_fence_keeps_a_request_out_of_a_fenced_bundle(tmp_path):
    """A request for the unfenced bundle exports its units and not the fenced atlas its sprite references."""
    fenced = env_for(Depot(tmp_path / "f"), {"key/a": [oid(13)]}, selected=["all"], fonts=["atlas"])
    plain = env_for(Depot(tmp_path / "p"), {"key/a": [oid(13)]}, selected=["all"])
    assert "atlas:99" not in ExportStage(FAKE_READER).subjects(fenced)
    assert "atlas:99" in ExportStage(FAKE_READER).subjects(plain)
    assert units.table(fenced).fences("all:13", "atlas:99") and not units.table(fenced).fences("atlas:99", "all:13")


def test_a_fenced_root_reaches_out_of_the_fence_and_takes_the_image_of_its_atlas(tmp_path):
    """The objects a product starts from reach what they reference, also out of a fenced bundle: the sprite of the
    fenced bundle reaches the unfenced atlas it is packed in, and its image comes from that atlas."""
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {}, selected=["all"], roots_objects=[oid(13)], fonts=["all"])
    exported(depot, env)
    _, doc = product(depot, env, "m/a", stage=Rooted({}, [oid(13)]))
    table = table_of(depot, doc)
    assert table["roots"] == [oid(13)] and table["fenced"] == {}
    assert f"{ATLAS_CAB}:99" in table["objects"] and set(table["objects"][oid(13)]["files"]) == {"json", "image"}


def test_a_product_can_include_referenced_font_objects_without_affecting_other_products(tmp_path):
    class WithFonts(Toy):
        INCLUDE_FONTS = True

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(13)]}, selected=[], fonts=["atlas"])
    stage = WithFonts({"m/a": "key/a"})
    env.facts["roots"] = assemble.request({stage.name: stage}, {stage.name: ["m/a"]}, env)
    assert "atlas:99" in ExportStage(FAKE_READER).subjects(env)
    exported(depot, env)
    _, doc = product(depot, env, "m/a", stage=stage)
    table = table_of(depot, doc)
    assert table["fenced"] == {}
    assert set(table["objects"][oid(13)]["files"]) == {"json", "image"}
    _, ordinary = product(depot, env, "m/a", {"m/a": "key/a"})
    assert f"{ATLAS_CAB}:99" in table_of(depot, ordinary)["fenced"]


def test_depends_names_the_units_it_reads(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    deps = Toy({"m/a": "key/a"}).depends("m/a", env)
    assert deps[0] == "link.addresses:main"
    assert deps[1:] == ["unity.export:all:10", "unity.export:all:60"]  # what it reads, not every unit


def test_inputs_and_the_key_follow_the_objects(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    stage = Toy({"m/a": "key/a"})
    t = describe(stage, "m/a", None, env)
    assert [(i.role, i.size) for i in t.inputs] == [("objects", len(contract.encode(stage.reach("m/a", env).table)))]
    assert t.context == {} and t.atoms == {} and t.params == {}
    # another object of the same unit is not in the table: the key does not follow it
    t2 = describe(Toy({"m/a": "key/a"}), "m/a", None, env)
    assert t2.key == t.key and t2.cost.cpu_seconds > 0


def test_a_product_waits_for_its_units(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    with pytest.raises(Pending, match="unity.export"):
        describe(Toy({"m/a": "key/a"}), "m/a", None, env)


# ---------------------------------------------------------------- the result and the layout
def test_a_product_result_is_the_files_and_the_table(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    body = contract.loads(
        depot.read(next(a for a in doc["artifacts"] if a["id"] == f"{t.id}#json")["content"]["sha256"])
    )
    assert body["subject"] == "m/a" and body["objects"]
    assert doc["status"] == "ok"
    again = execute(t, depot, registry(Toy({"m/a": "key/a"})), force=True)
    assert again.status == "ran" and contract.encode(depot.result(t.key)) == contract.encode(doc)


def test_an_unsupported_product_is_an_item(tmp_path):
    class Picky(Toy):
        def assemble(self, task, objects):
            raise assemble.Unsupported("unsupported.toy", "nothing to make")

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"}, stage=Picky({"m/a": "key/a"}))
    assert doc["artifacts"] == []
    assert doc["items"] == [
        {
            "object": t.id,
            "status": "unsupported",
            "class": "Toy",
            "reason": {"code": "unsupported.toy", "message": "nothing to make"},
        }
    ]
    assert doc["status"] == "ok"  # an unsupported product is a reported outcome, not a failure


def test_the_table_can_be_pruned_to_the_objects_the_product_uses(tmp_path):
    class Pruned(Toy):
        def assemble(self, task, objects):
            made = super().assemble(task, objects)
            return assemble.Assembly(made.files, {oid(60)})

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    _, doc = product(depot, env, "m/a", {"m/a": "key/a"}, stage=Pruned({"m/a": "key/a"}))
    assert list(table_of(depot, doc)["objects"]) == [oid(60)]


def test_a_product_retains_one_root_closure_while_reading_additional_objects(tmp_path):
    class Closure(Toy):
        def assemble(self, task, objects):
            made = super().assemble(task, objects)
            retained = contract.loads(objects.depot.input_bytes(task.input("retained")))
            return assemble.Assembly(made.files, table=retained)

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)], "key/b": [oid(40)]})
    exported(depot, env)
    retained = assemble.reach(env, [oid(60)], keys={"key/a": [oid(60)]}).table
    stage = Closure({"m/a": "key/a"})
    planned = describe(stage, "m/a", None, env)
    data = contract.encode(retained)
    extra = contract.Input("retained", depot.put(data), len(data), None, ({"kind": "depot"},))
    task = Task(stage.name, stage.version, "m/a", {}, {}, (*planned.inputs, extra))
    execute(task, depot, registry(stage), force=True)
    doc = depot.result(task.key)
    assert table_of(depot, doc) == retained


def test_a_product_names_an_object_the_table_lacks(tmp_path):
    class Wrong(Toy):
        def assemble(self, task, objects):
            made = super().assemble(task, objects)
            return assemble.Assembly(made.files, {"CAB-none:1"})

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t = describe(Wrong({"m/a": "key/a"}), "m/a", None, env)
    with pytest.raises(ValueError, match="not in the table"):
        execute(t, depot, registry(Wrong({"m/a": "key/a"})), force=True)


class Roles(Toy):
    """A Toy that keeps the `json` file alone of the texture its prefab references."""

    ROLES: dict = {oid(10): {"json"}}

    def assemble(self, task, objects):
        made = super().assemble(task, objects)
        return assemble.Assembly(made.files, roles=self.ROLES)


def test_a_product_keeps_some_files_of_an_object(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"}, stage=Roles({"m/a": "key/a"}))
    table = table_of(depot, doc)
    assert set(table["objects"][oid(10)]["files"]) == {"json"}  # no image
    assert oid(60) in table["objects"] and oid(61) in table["objects"]  # the others whole
    entries, manifest = manifest_of(depot, t, doc)
    assert set(manifest["objects"][oid(10)]["files"]) == {"json"}
    assert f"{oid(10)}#image" not in {e["id"] for e in entries}


def test_a_product_cannot_keep_a_file_an_object_lacks(tmp_path):
    class Wrong(Roles):
        ROLES = {oid(10): {"json", "mesh"}}

    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t = describe(Wrong({"m/a": "key/a"}), "m/a", None, env)
    with pytest.raises(ValueError, match="has no mesh file"):
        execute(t, depot, registry(Wrong({"m/a": "key/a"})), force=True)


# ---------------------------------------------------------------- the layout
def manifest_of(depot, t, doc):
    """(entries, the manifest) of a product layout for one result."""
    table = table_of(depot, doc)
    stage = Toy({"m/a": "key/a"})
    path = stage.manifest("m/a")
    body = contract.encode(layout.product_manifest(t.id, doc, table))
    sha = depot.put(body)
    rec = {"id": f"{t.id}#manifest", "sha256": sha, "size": len(body)}
    return layout.product_entries([(path, rec, doc, table)]), contract.loads(body)


def test_the_product_layout_places_the_files_and_the_objects(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    entries, manifest = manifest_of(depot, t, doc)
    place = {e["path"]: e["id"] for e in entries}
    assert place["toy.thing/m%2Fa.json"] == f"{t.id}#manifest"
    json_rec = next(a for a in doc["artifacts"] if a["id"] == f"{t.id}#json")
    assert place[f"assets/{json_rec['content']['sha256']}.json"] == f"{t.id}#json"
    assert place[f"assets/{oid_file(table_of(depot, doc), oid(10), 'image')['sha256']}.png"] == f"{oid(10)}#image"
    assert manifest["files"]["json"] == {
        "asset": f"assets/{json_rec['content']['sha256']}.json",
        "size": json_rec["content"]["size"],
    }
    assert manifest["objects"][oid(10)]["class"] == "Texture2D"
    assert oid(61) not in manifest["objects"]  # contained in the prefab of oid(60), named by its file
    assert "assets" in manifest["objects"][oid(10)]["files"]["image"]["asset"]
    # the manifest path is one directory and one file
    assert len(entries) == len({(e["path"], e["id"]) for e in entries})


def test_the_product_layout_refuses_a_nested_manifest_path(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    table = table_of(depot, doc)
    body = contract.encode(layout.product_manifest(t.id, doc, table))
    rec = {"id": f"{t.id}#manifest", "sha256": depot.put(body), "size": len(body)}
    with pytest.raises(ValueError, match="manifest path"):
        layout.product_entries([("a/b/c.json", rec, doc, table)])


def test_the_product_layout_writes_and_hits(tmp_path):
    depot = Depot(tmp_path / "s")
    env = env_for(depot, {"key/a": [oid(60)]})
    exported(depot, env)
    t, doc = product(depot, env, "m/a", {"m/a": "key/a"})
    entries, _ = manifest_of(depot, t, doc)
    w = layout.materialize(tmp_path / "out", "product", {}, entries, layout.from_depot(depot))
    assert w["write"] == len(entries) and w["remove"] == 0
    assert (tmp_path / "out" / "toy.thing" / "m%2Fa.json").is_file()
    assert layout.diff(tmp_path / "out", entries) == {"write": 0, "remove": 0, "keep": len(entries)}
    assert json.loads((tmp_path / "out" / "toy.thing" / "m%2Fa.json").read_text(encoding="utf-8"))["schema"] == (
        contract.PRODUCT
    )
