"""Output layouts: path rules of `original`, `cas`, derivation both ways, incremental writing (synthetic results)."""
import hashlib
import os
from pathlib import Path

import pytest

from nnnotes import contract, layout
from nnnotes.contract import Task
from nnnotes.layout import LayoutError, Source, escape_segment, unescape_path
from nnnotes.store import Store


def bundle(store, subject, objects, loose=(), root=True):
    """A unity.export-like result: objects [(file, pathId, class, name, container, {role: (bytes, ext)})], loose
    artifacts of no object [(role, bytes, ext)]."""
    task = Task("unity.export", 1, subject)
    arts, items = [], []
    for file, pid, cls, name, container, roles in objects:
        oid = contract.object_id(file, pid)
        obj = {"file": file, "pathId": pid, "class": cls, "name": name}
        if container:
            obj["container"] = container
        ids = []
        for role, (data, ext) in roles.items():
            aid = contract.artifact_id(oid, role)
            arts.append(contract.artifact(aid, store.add(data, ext), contract.provenance(task, obj=obj),
                                          {"kind": "test"}))
            ids.append(aid)
        items.append(contract.item(oid, "exported", artifacts=ids, cls=cls))
    for role, data, ext in loose:
        arts.append(contract.artifact(contract.artifact_id(task.id, role), store.add(data, ext),
                                      contract.provenance(task), {"kind": "test"}))
    return Source(task.id, contract.result(task, arts, items), f"_bundles/{subject}" if root else None)


def paths(sources, **kw):
    entries, report = layout.original_entries(sources, **kw)
    return {e["id"]: e["path"] for e in entries}, report


def png(tag):
    return (b"\x89PNG" + tag.encode(), "png")


def test_escaping_is_reversible_and_safe():
    assert escape_segment('a<b>:c"d|e?f*g\\h/i') == "a%3Cb%3E%3Ac%22d%7Ce%3Ff%2Ag%5Ch%2Fi"
    assert escape_segment("50%") == "50%25" and escape_segment("x\x01") == "x%01"
    assert escape_segment("name.") == "name%2E" and escape_segment("name. ") == "name%2E%20"
    assert escape_segment("CON") == "%43ON" and escape_segment("con.txt") == "%63on.txt"
    assert escape_segment("LPT1.png") == "%4CPT1.png" and escape_segment("CONSOLE") == "CONSOLE"
    assert escape_segment("..") == "%2E%2E" and escape_segment("日本語") == "日本語"
    for s in ('a<b>:c"d|e?f*g\\h', "50%", "name. ", "CON.x", "%41"):
        assert unescape_path(escape_segment(s)) == s


def test_objects_of_one_container(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "card", [
        ("CAB-1", 10, "Texture2D", "member_full", "assets/x/member_full.png", {"image": png("t")}),
        ("CAB-1", 11, "Sprite", "square", "assets/x/member_full.png",
         {"image": png("s1"), "meta": (b"{}", "json")}),
        ("CAB-1", 12, "Sprite", "member_full", "assets/x/member_full.png", {"image": png("s2")}),
        ("CAB-1", 21, "Sprite", "dup", "assets/x/member_full.png", {"image": png("d1")}),
        ("CAB-1", 22, "Sprite", "dup", "assets/x/member_full.png", {"image": png("d2")}),
        ("CAB-1", 30, "GameObject", "foo", "assets/p/foo.prefab",
         {"prefab": (b"{}\n", "json"), "blob:m_Data/Array": (b"MOC3", "moc3")}),
        ("CAB-1", 40, "Texture2D", "hdr", "assets/x/hdr.png", {"image": (b"\x13\xab\xa1\x5c", "astc")}),
    ])
    p, report = paths([src])
    assert p == {
        "CAB-1:10#image": "assets/x/member_full.png",
        "CAB-1:11#image": "assets/x/member_full[square].png",
        "CAB-1:11#meta": "assets/x/member_full[square].png.meta.json",
        "CAB-1:12#image": "assets/x/member_full[member_full].png",
        "CAB-1:21#image": "assets/x/member_full[dup]~21.png",
        "CAB-1:22#image": "assets/x/member_full[dup]~22.png",
        "CAB-1:30#prefab": "assets/p/foo.prefab.json",
        "CAB-1:30#blob:m_Data/Array": "assets/p/foo.prefab.blob.m_Data.Array.moc3",
        "CAB-1:40#image": "assets/x/hdr.png.astc",
    }
    assert report == {"collisions": [], "shared": []}


def test_the_primary_is_the_class_the_extension_names(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "b", [
        ("CAB-1", 1, "Sprite", "icon", "assets/i/icon.png", {"image": png("s")}),
        ("CAB-1", 2, "Texture2D", "icon", "assets/i/icon.png", {"image": png("t")}),
    ])
    p, _ = paths([src])
    assert p == {"CAB-1:2#image": "assets/i/icon.png", "CAB-1:1#image": "assets/i/icon[icon].png"}


def test_objects_without_container_and_artifacts_of_no_object(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "b1", [
        ("CAB-1", 5, "Mesh", "body", None, {"mesh": (b"glTF", "glb")}),
        ("CAB-1", -6, "Mesh", "", None, {"mesh": (b"glTF2", "glb")}),
        ("CAB-1", 7, "Material", "m/1", None, {"json": (b"{}", "json")}),
    ], loose=[("bundle.json", b"{}", "json"), ("data", b"\x00", "bin"), ("sub/x.json", b"[]", "json")])
    p, _ = paths([src])
    assert p == {"CAB-1:5#mesh": "_bundles/b1/Mesh/body~5.glb", "CAB-1:-6#mesh": "_bundles/b1/Mesh/~-6.glb",
                 "CAB-1:7#json": "_bundles/b1/Material/m%2F1~7.json",
                 "unity.export:b1#bundle.json": "_bundles/b1/bundle.json",
                 "unity.export:b1#data": "_bundles/b1/data.bin",
                 "unity.export:b1#sub/x.json": "_bundles/b1/sub/x.json"}
    default = bundle(s, "b2", [], loose=[("a.json", b"1", "json")], root=False)
    assert paths([default])[0] == {"unity.export:b2#a.json": "_tasks/unity.export/b2/a.json"}


def test_a_bundle_of_several_serialized_files_names_them(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "scene", [("BuildPlayer-A", 1, "Mesh", "m", None, {"mesh": (b"1", "glb")}),
                              ("BuildPlayer-A.sharedAssets", 1, "Mesh", "m", None, {"mesh": (b"2", "glb")})])
    p, report = paths([src])
    assert p == {"BuildPlayer-A:1#mesh": "_bundles/scene/BuildPlayer-A/Mesh/m~1.glb",
                 "BuildPlayer-A.sharedAssets:1#mesh": "_bundles/scene/BuildPlayer-A.sharedAssets/Mesh/m~1.glb"}
    assert report["collisions"] == []


def test_a_container_in_two_bundles_is_told_apart_by_bundle(tmp_path):
    s = Store(tmp_path / "s")
    a = bundle(s, "group_a", [("CAB-a", 1, "Texture2D", "shared", "assets/x/shared.png", {"image": png("a")}),
                              ("CAB-a", 2, "Sprite", "part", "assets/x/shared.png", {"image": png("ap")})])
    b = bundle(s, "group_b", [("CAB-b", 1, "Texture2D", "shared", "assets/x/shared.png", {"image": png("b")})])
    p, report = paths([b, a])
    assert p == {"CAB-a:1#image": "assets/x/shared@group_a.png", "CAB-a:2#image": "assets/x/shared@group_a[part].png",
                 "CAB-b:1#image": "assets/x/shared@group_b.png"}
    assert report["shared"] == [{"container": "assets/x/shared.png", "sources": ["group_a", "group_b"]}]


def test_the_catalog_case_and_case_collisions(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "b", [("CAB-1", 1, "Texture2D", "foo", "assets/aa/foo.png", {"image": png("f")})],
                 loose=[("Readme.txt", b"1", "txt"), ("readme.txt", b"2", "txt"), ("x.json", b"3", "json"),
                        ("x.json/y.json", b"4", "json")])
    p, report = paths([src], case={"assets/aa/foo.png": "Assets/AA/Foo.png"},
                      extra=[{"path": "manifest.json", "sha256": contract.sha256(b"m"), "size": 1, "id": "v:m#x"}])
    tag = lambda aid: hashlib.sha256(aid.encode()).hexdigest()[:8]            # noqa: E731
    assert p["CAB-1:1#image"] == "Assets/AA/Foo.png"
    assert p["unity.export:b#Readme.txt"] == "_bundles/b/Readme.txt"
    assert p["unity.export:b#readme.txt"] == f"_bundles/b/readme~{tag('unity.export:b#readme.txt')}.txt"
    assert p["unity.export:b#x.json"] == f"_bundles/b/x~{tag('unity.export:b#x.json')}.json"
    assert p["unity.export:b#x.json/y.json"] == "_bundles/b/x.json/y.json"
    assert p["v:m#x"] == f"manifest~{tag('v:m#x')}.json"
    assert [c["id"] for c in report["collisions"]] == ["unity.export:b#readme.txt", "unity.export:b#x.json",
                                                       "v:m#x"]


def test_equal_content_at_one_path_shares_the_file():
    sha = contract.sha256(b"same")
    extra = [{"path": "views/a.json", "sha256": sha, "size": 4, "id": "x:1#a"},
             {"path": "views/a.json", "sha256": sha, "size": 4, "id": "x:2#a"}]
    entries, report = layout.original_entries([], extra=extra)
    assert [e["path"] for e in entries] == ["views/a.json", "views/a.json"] and report["collisions"] == []


def two_bundles(tmp_path):
    s = Store(tmp_path / "s")
    a = bundle(s, "a", [("CAB-a", 1, "Texture2D", "t", "assets/t.png", {"image": png("t")}),
                        ("CAB-a", 2, "Sprite", "s", "assets/t.png", {"image": png("s"), "meta": (b"{}", "json")}),
                        ("CAB-a", 3, "Mesh", "m", None, {"mesh": (b"glTF", "glb")})],
               loose=[("bundle.json", b'{"a":1}', "json")])
    b = bundle(s, "b", [("CAB-b", 1, "TextAsset", "same", "assets/text/same.bytes", {"data": (b"{}", "bytes")})])
    return s, [a, b]


def files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_cas_and_original_derive_from_each_other(tmp_path):
    s, sources = two_bundles(tmp_path)
    cas, _ = layout.entries("cas", sources)
    assert {e["path"] for e in cas} == {f"assets/{a['content']['sha256']}.{a['content']['ext']}"
                                        for src in sources for a in src.result["artifacts"]}
    orig, _ = layout.entries("original", sources)
    w_cas = layout.materialize(tmp_path / "cas", "cas", {}, cas, layout.from_store(s))
    w_orig = layout.materialize(tmp_path / "orig", "original", {}, orig, layout.from_store(s))
    assert w_cas["manifest"]["schema"] == contract.LAYOUT and w_orig["write"] == len(orig)
    # original from cas + the records; cas from the original manifest alone
    layout.materialize(tmp_path / "orig2", "original", {}, orig, layout.from_layout(tmp_path / "cas"))
    cas2 = layout.cas_from_manifest(layout.read_manifest(tmp_path / "orig"))
    assert cas2 == cas
    layout.materialize(tmp_path / "cas2", "cas", {}, cas2, layout.from_layout(tmp_path / "orig"))
    assert files(tmp_path / "orig") == files(tmp_path / "orig2")
    assert files(tmp_path / "cas") == files(tmp_path / "cas2")
    assert (tmp_path / "orig" / layout.MANIFEST).read_bytes() == contract.encode(w_orig["manifest"])
    assert w_orig["sha256"] == contract.sha256((tmp_path / "orig" / layout.MANIFEST).read_bytes())


def test_materialize_is_incremental_and_touches_only_its_files(tmp_path):
    s, sources = two_bundles(tmp_path)
    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    (out / "notes.txt").write_text("mine")                              # a file of the user
    orig, _ = layout.entries("original", sources)
    first = layout.materialize(out, "original", {}, orig, layout.from_store(s))
    assert layout.diff(out, orig) == {"write": 0, "remove": 0, "keep": len(orig)}
    again = layout.materialize(out, "original", {}, orig, layout.from_store(s))
    assert (again["write"], again["remove"], again["keep"]) == (0, 0, len(orig))
    assert again["sha256"] == first["sha256"]
    (out / "assets" / "t.png").unlink()                                 # damaged output is written again
    changed, _ = layout.entries("original", sources[:1])                # bundle b dropped
    assert layout.diff(out, changed) == {"write": 1, "remove": 1, "keep": len(changed) - 1}
    w = layout.materialize(out, "original", {}, changed, layout.from_store(s))
    assert (w["write"], w["remove"]) == (1, 1)
    assert not (out / "assets" / "text").exists() and (out / "notes.txt").read_text() == "mine"
    assert (out / "assets" / "t.png").exists() and not (out / layout.JOURNAL).exists()


def test_an_interrupted_run_is_reconciled(tmp_path):
    s, sources = two_bundles(tmp_path)
    out = tmp_path / "out"
    orig, _ = layout.entries("original", sources)
    (out / "half").mkdir(parents=True)
    (out / "half" / "written.png").write_bytes(b"partial")              # written by a run that was interrupted
    (out / layout.JOURNAL).write_bytes(contract.encode({"write": ["half/written.png"], "remove": []}))
    w = layout.materialize(out, "original", {}, orig, layout.from_store(s))
    assert w["remove"] == 1 and not (out / "half").exists() and not (out / layout.JOURNAL).exists()


def test_a_file_that_is_not_the_layouts_is_never_overwritten(tmp_path):
    s, sources = two_bundles(tmp_path)
    out = tmp_path / "out"
    orig, _ = layout.entries("original", sources)
    (out / "assets").mkdir(parents=True)
    (out / "assets" / "t.png").write_bytes(b"someone else's")
    with pytest.raises(LayoutError, match="not this layout's: assets/t.png"):
        layout.materialize(out, "original", {}, orig, layout.from_store(s))
    assert (out / "assets" / "t.png").read_bytes() == b"someone else's"
    (out / "assets" / "t.png").write_bytes(png("t")[0])                 # the right content: adopted
    w = layout.materialize(out, "original", {}, orig, layout.from_store(s))
    assert w["write"] == len(orig)


def test_hard_links(tmp_path):
    s, sources = two_bundles(tmp_path)
    cas, _ = layout.entries("cas", sources)
    w = layout.materialize(tmp_path / "out", "cas", {}, cas, layout.from_store(s), link="hard")
    assert w["linked"] + w["copied"] == w["write"]
    e = cas[0]
    if w["linked"]:
        assert os.path.samefile(tmp_path / "out" / e["path"], s.path(e["sha256"]))
    with pytest.raises(ValueError):
        layout.materialize(tmp_path / "out", "cas", {}, cas, layout.from_store(s), link="soft")


def test_an_objects_artifacts_from_several_results_are_placed_together(tmp_path):
    s = Store(tmp_path / "s")
    export = bundle(s, "atlas_b", [("CAB-b", 1, "Texture2D", "atlas", "assets/a/atlas.png", {"image": png("t")}),
                                   ("CAB-b", 2, "Sprite", "icon", "assets/a/atlas.png", {"meta": (b"{}", "json")})])
    crop = bundle(s, "tex_other", [("CAB-b", 2, "Sprite", "icon", "assets/a/atlas.png", {"image": png("c")})])
    p, report = paths([crop, export], bundles={"CAB-b": "atlas_b"})
    assert p == {"CAB-b:1#image": "assets/a/atlas.png", "CAB-b:2#image": "assets/a/atlas[icon].png",
                 "CAB-b:2#meta": "assets/a/atlas[icon].png.meta.json"}
    assert report["shared"] == []
    assert paths([crop, export])[0] == p                            # merged: the first result (by task) names it
    other = bundle(s, "tex_other", [("CAB-c", 2, "Sprite", "icon", "assets/a/atlas.png", {"image": png("o")})])
    p, report = paths([other, export], bundles={"CAB-b": "atlas_b", "CAB-c": "atlas_c"})
    assert report["shared"] == [{"container": "assets/a/atlas.png", "sources": ["atlas_b", "atlas_c"]}]
    assert p["CAB-c:2#image"] == "assets/a/atlas@atlas_c.png" and p["CAB-b:1#image"] == "assets/a/atlas@atlas_b.png"


def test_derivation_keeps_lower_case_extensions(tmp_path):
    s = Store(tmp_path / "s")
    src = bundle(s, "b", [("CAB-1", 1, "Texture2D", "x", "Assets/X.PNG", {"image": png("x")})])
    orig, _ = layout.entries("original", [src])
    assert [e["path"] for e in orig] == ["Assets/X.PNG"]
    layout.materialize(tmp_path / "o", "original", {}, orig, layout.from_store(s))
    assert layout.cas_from_manifest(layout.read_manifest(tmp_path / "o")) == layout.entries("cas", [src])[0]


def test_objects_without_container_go_under_the_root_of_their_file(tmp_path):
    s = Store(tmp_path / "s")
    export = bundle(s, "sprites_b", [("CAB-s", 3, "Sprite", "loose", None, {"meta": (b"{}", "json")})])
    crop = bundle(s, "atlas_a:7", [("CAB-s", 3, "Sprite", "loose", None, {"image": png("c")})])
    crop.root = "_tasks/sprite.crop/atlas_a:7"
    p, _ = paths([crop, export])
    assert p["CAB-s:3#image"] == "_tasks/sprite.crop/atlas_a%3A7/Sprite/loose~3.png"      # the first source
    p, _ = paths([crop, export], object_roots={"CAB-s": "_bundles/sprites_b"})
    assert p == {"CAB-s:3#image": "_bundles/sprites_b/Sprite/loose~3.png",
                 "CAB-s:3#meta": "_bundles/sprites_b/Sprite/loose~3.meta.json"}
    two = bundle(s, "scene", [("F-a", 1, "Mesh", "m", None, {"mesh": (b"1", "glb")})])
    p, _ = paths([two], object_roots={"F-a": "_bundles/scene", "F-a.shared": "_bundles/scene"})
    assert p == {"F-a:1#mesh": "_bundles/scene/F-a/Mesh/m~1.glb"}                     # the bundle has two files
