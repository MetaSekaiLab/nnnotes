"""Clip bindings named in a synthetic prefab hierarchy (nnnotes.unity.bindings)."""

import pytest

from nnnotes.unity import bindings
from nnnotes.unity.bindings import crc

PARAM = "Live2D.Cubism|Live2D.Cubism.Core|CubismParameter"
PART = "Live2D.Cubism|Live2D.Cubism.Core|CubismPart"
MAT = {"$ref": {"file": "CAB-m", "pathId": 7, "class": "Material"}}


def mono(script, **fields):
    return {"type": "MonoBehaviour", "pathId": 1, "script": script, "fields": fields}


def node(path, *components):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "components": list(components)}


def prefab():
    return {
        "name": "model",
        "nodes": [
            node("model", mono("Live2D.Cubism|Live2D.Cubism.Rendering|CubismRenderController", Opacity=1.0)),
            node("model/Parameters"),
            node("model/Parameters/ParamAngleX", mono(PARAM, Value=0.0, MinimumValue=-30.0)),
            node("model/Parameters/ParamAngleY", mono(PARAM, Value=0.0, MinimumValue=-30.0)),
            node("model/Parts"),
            node("model/Parts/PartArm", mono(PART, Opacity=1.0)),
            node("model/Drawables"),
            node(
                "model/Drawables/ArtMesh0",
                {"type": "MeshRenderer", "pathId": 2, "fields": {"m_Enabled": 1, "m_Materials": [MAT, None]}},
            ),
            node("model/Twin"),
            node("model/Twin"),
            node("other"),
        ],
    }


def binding(path, attr, type_id=114, script=None, **kw):
    b = {"pathCrc": crc(path), "attributeCrc": crc(attr) if isinstance(attr, str) else attr}
    b.update(typeID=type_id, customType=0, pptr=False, int=False, serializeReference=False)
    if script is not None:
        b.update(script={"$ref": {"file": "CAB-s", "pathId": 3}}, scriptClass=script)
    b.update(kw)
    return b


def test_paths_are_relative_to_the_animators_game_object():
    out = bindings.resolve(
        [
            binding("Parameters/ParamAngleX", "Value", script=PARAM),
            binding("Parts/PartArm", "Opacity", script=PART),
            binding("", "Opacity", script="Live2D.Cubism|Live2D.Cubism.Rendering|CubismRenderController"),
            binding("Parameters/ParamGone", "Value", script=PARAM),
        ],
        prefab(),
        "model",
    )
    assert out == [
        {"path": "Parameters/ParamAngleX", "attribute": "Value"},
        {"path": "Parts/PartArm", "attribute": "Opacity"},
        {"path": "", "attribute": "Opacity"},
        {"path": None, "attribute": "Value"},  # not below the Animator: a field of the class in the hierarchy
    ]
    below = bindings.resolve([binding("ParamAngleY", "Value", script=PARAM)], prefab(), "model/Parameters")
    assert below == [{"path": "ParamAngleY", "attribute": "Value"}]


def test_a_path_two_transforms_share_names_them_both():
    (r,) = bindings.resolve([binding("Twin", "m_IsActive", type_id=1)], prefab(), "model")
    assert r == {"path": "Twin", "attribute": "m_IsActive"}  # an engine property


def test_the_bound_component_is_the_bindings_script():
    other = "Game|Game|Other"
    (r,) = bindings.resolve([binding("Parameters/ParamAngleX", "MinimumValue", script=other)], prefab(), "model")
    assert r == {"path": "Parameters/ParamAngleX", "attribute": None}  # no Other there, nor anywhere


def test_a_transform_bindings_attribute_is_the_clips():
    b = binding("Parts", 2, type_id=4, attribute="m_LocalRotation", curves=4)
    assert bindings.resolve([b], prefab(), "model") == [{"path": "Parts", "attribute": "m_LocalRotation"}]


def test_a_renderer_material_binding_names_a_property_of_its_materials():
    tint = crc("_TintColor") & 0x0FFFFFFF
    docs = {7: {"floats": {"_Cutoff": 0.5}, "colors": {"_TintColor": {"r": 1, "g": 1, "b": 1, "a": 1}}}}

    def material(ref):
        return docs.get(ref["$ref"]["pathId"])

    out = bindings.resolve(
        [
            binding(
                "Drawables/ArtMesh0", tint | 3 << 28 | 1 << 30, type_id=23, customType=22, **{"class": "MeshRenderer"}
            ),
            binding("Drawables/ArtMesh0", tint | 1 << 28, type_id=23, customType=22),
            binding("Drawables/ArtMesh0", crc("_Cutoff") & 0x0FFFFFFF, type_id=23, customType=22),
            binding("Drawables/ArtMesh0", crc("_Other") & 0x0FFFFFFF, type_id=23, customType=22),
        ],
        prefab(),
        "model",
        material,
    )
    assert [r["attribute"] for r in out] == ["material._TintColor.a", "material._TintColor.y", "material._Cutoff", None]
    assert bindings.resolve([binding("Drawables/ArtMesh0", tint, type_id=23, customType=22)], prefab(), "model") == [
        {"path": "Drawables/ArtMesh0", "attribute": None}  # its materials unknown
    ]


def test_a_field_of_the_bound_renderer():
    (r,) = bindings.resolve([binding("Drawables/ArtMesh0", "m_Enabled", type_id=23)], prefab(), "model")
    assert r == {"path": "Drawables/ArtMesh0", "attribute": "m_Enabled"}


def test_an_ambiguous_path_lists_its_candidates(monkeypatch):
    monkeypatch.setattr(bindings, "crc", lambda s: 1 if s in ("Parts", "Drawables") else 0x7FFFFFFF)
    (r,) = bindings.resolve([{"pathCrc": 1, "attributeCrc": 5, "typeID": 1}], prefab(), "model")
    assert r == {"path": None, "attribute": None, "pathCandidates": ["Drawables", "Parts"]}


def test_the_animators_game_object_is_in_the_hierarchy():
    with pytest.raises(KeyError, match="not in the hierarchy"):
        bindings.resolve([], prefab(), "nope")


def test_material_attribute_layout():
    kinds = {"_Color": "vector", "_Alpha": "float"}
    c = crc("_Color") & 0x0FFFFFFF
    assert bindings.material_attribute(c | 0 << 28 | 1 << 30, kinds) == "material._Color.r"
    assert bindings.material_attribute(c | 2 << 28, kinds) == "material._Color.z"
    assert bindings.material_attribute(crc("_Alpha") & 0x0FFFFFFF, kinds) == "material._Alpha"
    assert bindings.material_attribute(crc("_Alpha") & 0x0FFFFFFF | 1 << 30, kinds) == "material._Alpha.r"
    assert bindings.material_prop_names(["_Extra"])[:3] == [
        "material._AddColor",
        "material._AddColor.r",
        "material._AddColor.g",
    ]
    assert "material._Extra.w" in bindings.material_prop_names(["_Extra"])
    assert bindings.flat_names({"m_Color": {"r": 1, "g": 0}, "list": [1]}) == [
        "m_Color",
        "m_Color.r",
        "m_Color.g",
        "list",
    ]
