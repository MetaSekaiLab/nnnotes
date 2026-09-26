"""spot: Spine characters and tap targets, with and without the references the prefab leaves empty
(synthetic components), and the Spine files of a skeleton (synthetic objects)."""
import io

import pytest
from PIL import Image

from nnnotes import spot


class Ref:
    def __init__(self, path_id, target=None):
        self.path_id, self.target = path_id, target

    def read(self):
        assert self.path_id, "an empty reference is not read"
        return self.target


class Reader:
    def __init__(self, tt):
        self.tt = tt

    def read_typetree(self):
        return self.tt


class Read:
    def __init__(self, tt=None, **fields):
        self.object_reader = Reader(tt)
        self.__dict__.update(fields)


class Obj:
    def __init__(self, tt, **fields):
        self.tt, self.fields = tt, fields

    def read_typetree(self):
        return self.tt

    def read(self):
        return Read(**self.fields)


class Graph:
    tf_of_go = {1: 11, 2: 12, 3: 13, 4: 14}

    def path(self, tf_pid):
        return f"Situation/obj{tf_pid}"


def go(pid):
    return {"m_FileID": 0, "m_PathID": pid}


def world_of(go_ref):
    return [float(go_ref["m_PathID"])]


ANIM = {"m_GameObject": go(3), "_animationName": "idle", "loop": 1, "timeScale": 1.0, "initialSkinName": "default",
        "initialFlipX": 0, "initialFlipY": 0, "pmaVertexColors": 1, "tintBlack": 0, "zSpacing": 0.0}


def test_spine_characters_keep_one_without_animation_as_null():
    anim = Read(ANIM, skeletonDataAsset=Ref(50))
    objs = [Obj({"m_GameObject": go(1)}, _animation=Ref(30, anim)), Obj({"m_GameObject": go(2)}, _animation=Ref(0))]
    got = spot.spine_characters(objs, Graph(), {50: {"name": "chr_sd"}}, world_of)
    assert got[0] == {"path": "Situation/obj11", "skeletonData": "chr_sd",
                      "animation": {k: v for k, v in ANIM.items() if k != "m_GameObject"}, "world": [3.0]}
    assert got[1] == {"path": "Situation/obj12", "skeletonData": None, "animation": None, "world": None}


def test_tap_targets_without_focus_have_a_null_focus_world():
    focus = Read({"m_GameObject": go(3)})
    objs = [Obj({"m_GameObject": go(2), "m_Enabled": 1, "_characterId": 7, "_focus": go(0)}, _focus=Ref(0)),
            Obj({"m_GameObject": go(1), "m_Enabled": 1, "_characterId": 5, "_focus": go(33)}, _focus=Ref(33, focus))]
    got = spot.tap_targets(objs, Graph(), world_of)
    assert [c["_characterId"] for c in got] == [5, 7]
    assert got[0] == {"path": "Situation/obj11", "_characterId": 5, "world": [1.0], "focusWorld": [3.0]}
    assert got[1] == {"path": "Situation/obj12", "_characterId": 7, "world": [2.0], "focusWorld": None}


# ---------------------------------------------------------------- Spine files (synthetic objects)
class Text:
    def __init__(self, name, script):
        self.m_Name, self.m_Script = name, script


class Tex:
    m_TextureFormat = 4                                  # RGBA32

    def __init__(self, image):
        self.m_Name, self.image = "page", image


class TexEnv:
    def __init__(self, tex):
        self.m_Texture = Ref(1, tex)


class Material:
    def __init__(self, tex):
        self.m_SavedProperties = Read(m_TexEnvs=[("_BumpMap", TexEnv(None)), ("_MainTex", TexEnv(tex))])


ATLAS_TEXT = "\np1.png\nsize: 2,2\nformat: RGBA8888\nr\n  bounds: 0,0,1,1\n\np2.png\nsize: 1,1\nr2\n  bounds: 0,0,1,1\n"


def atlas_asset(name, pages):
    return Read(atlasFile=Ref(20, Text(name, ATLAS_TEXT)),
                materials=[Ref(30 + i, Material(t)) for i, t in enumerate(pages)])


def skeleton(name, script, atlases):
    tt = {"m_Name": name + "_SkeletonData", "scale": 0.01, "defaultMix": 0.2, "fromAnimation": ["a"],
          "toAnimation": ["b"], "duration": [0.5]}
    return Obj(tt, skeletonJSON=Ref(9, Text(name, script)), atlasAssets=atlases)


def images():
    return Image.new("RGBA", (2, 2), (1, 2, 3, 4)), Image.new("RGBA", (1, 1), (9, 8, 7, 6))


def png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_write_skeleton_writes_the_skeleton_atlas_and_pages_in_order():
    a, b = images()
    shared = Ref(40, atlas_asset("chr", [Tex(a), Tex(b)]))
    got = []
    written = {}
    rec = spot.write_skeleton(skeleton("chr", b"\x00binary", [shared]), lambda n, d: got.append((n, d)), written)
    assert [n for n, _ in got] == ["chr.skel", "chr.atlas", "p1.png", "p2.png"]
    assert got[0][1] == b"\x00binary" and got[1][1] == ATLAS_TEXT.encode()
    assert got[2][1] == png(a) and got[3][1] == png(b)
    assert rec == {"name": "chr_SkeletonData", "skeleton": "chr.skel", "atlases": ["chr.atlas"], "scale": 0.01,
                   "defaultMix": 0.2, "mixes": [{"from": "a", "to": "b", "duration": 0.5}]}
    assert written == {40: "chr.atlas"}
    got.clear()                                           # a second skeleton on the same atlas: written once
    rec2 = spot.write_skeleton(skeleton("chr2", ' {"skeleton": 1}', [shared]), lambda n, d: got.append((n, d)),
                               written)
    assert got == [("chr2.json", b' {"skeleton": 1}')] and rec2["atlases"] == ["chr.atlas"]


def test_write_skeleton_keeps_a_name_that_already_has_the_extension():
    a, b = images()
    got = []
    shared = Ref(40, atlas_asset("chr.atlas", [Tex(a), Tex(b)]))
    rec = spot.write_skeleton(skeleton("chr.skel", b"\x00binary", [shared]), lambda n, d: got.append((n, d)))
    assert [n for n, _ in got] == ["chr.skel", "chr.atlas", "p1.png", "p2.png"] and rec["skeleton"] == "chr.skel"


def test_write_skeleton_checks_pages_against_materials():
    a, _ = images()
    with pytest.raises(RuntimeError, match="2 pages vs 1 materials"):
        spot.write_skeleton(skeleton("x", b"s", [Ref(41, atlas_asset("x.atlas", [Tex(a)]))]), lambda n, d: None)
