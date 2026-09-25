"""spot: Spine characters and tap targets, with and without the references the prefab leaves empty
(synthetic components)."""
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
