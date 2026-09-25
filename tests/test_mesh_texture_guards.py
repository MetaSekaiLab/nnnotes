"""mesh_arrays with 4-component normals and without an index buffer; HDR ASTC textures are refused, not decoded."""
from types import SimpleNamespace

import numpy as np
import pytest
from UnityPy.enums import TextureFormat

from nnnotes import export, unity


def fake_handler(monkeypatch, **fields):
    calls = []

    class Handler:
        def __init__(self, mesh):
            self.src = mesh
            self.m_VertexCount = fields.get("count", 0)
            self.m_Vertices = fields.get("vertices")
            self.m_Normals = fields.get("normals")
            self.m_UV0 = fields.get("uv0")
            self.m_IndexBuffer = fields.get("indices")

        def process(self):
            pass

        def get_triangles(self):
            calls.append("triangles")
            assert self.m_IndexBuffer is not None                # UnityPy's own assertion
            return fields["triangles"]
    monkeypatch.setattr(unity.MeshHelper, "MeshHandler", Handler)
    return calls


def test_three_component_channels_as_before(monkeypatch):
    v = np.arange(12, dtype=float)
    fake_handler(monkeypatch, count=4, vertices=list(v), normals=list(v + 100), uv0=list(range(8)),
                 indices=[0, 1, 2, 2, 3, 0], triangles=[[(0, 1, 2), (2, 3, 0)]])
    pos, nrm, uv, tris = unity.mesh_arrays(SimpleNamespace(m_SubMeshes=[1]))
    assert np.array_equal(pos, v.reshape(-1, 3)) and np.array_equal(nrm, (v + 100).reshape(-1, 3))
    assert np.array_equal(uv, np.arange(8, dtype=float).reshape(-1, 2))
    assert [t.tolist() for t in tris] == [[[0, 1, 2], [2, 3, 0]]]


@pytest.mark.parametrize("as_tuples", [False, True])
def test_four_component_normals_keep_xyz(monkeypatch, as_tuples):
    count = 6                                                   # 24 values: divisible by 3, silently wrong before
    n4 = np.arange(count * 4, dtype=float).reshape(count, 4)
    normals = [tuple(r) for r in n4] if as_tuples else list(n4.ravel())
    fake_handler(monkeypatch, count=count, vertices=list(np.zeros(count * 3)), normals=normals,
                 uv0=[tuple(r) for r in np.ones((count, 4))], indices=[0, 1, 2], triangles=[[(0, 1, 2)]])
    pos, nrm, uv, _ = unity.mesh_arrays(SimpleNamespace(m_SubMeshes=[1]))
    assert nrm.shape == (count, 3) and np.array_equal(nrm, n4[:, :3]) and nrm.flags["C_CONTIGUOUS"]
    assert uv.shape == (count, 2) and pos.shape == (count, 3)


def test_a_channel_that_does_not_fit_the_vertex_count_is_an_error(monkeypatch):
    fake_handler(monkeypatch, count=4, vertices=list(np.zeros(12)), normals=list(np.zeros(10)),
                 indices=[0, 1, 2], triangles=[[(0, 1, 2)]])
    with pytest.raises(ValueError, match="normals: 10 values for 4 vertices"):
        unity.mesh_arrays(SimpleNamespace(m_SubMeshes=[1]))


@pytest.mark.parametrize("indices", [None, []])
def test_no_index_buffer_is_no_triangles(monkeypatch, indices):
    calls = fake_handler(monkeypatch, count=4, vertices=list(np.zeros(12)), indices=indices)
    pos, nrm, uv, tris = unity.mesh_arrays(SimpleNamespace(m_SubMeshes=[1, 2]))
    assert calls == [] and [t.shape for t in tris] == [(0, 3), (0, 3)] and nrm.shape == (0, 3)
    assert unity.mesh_arrays(SimpleNamespace(m_SubMeshes=None))[3] == []


class FakeTexture:
    def __init__(self, fmt, name="number1_glow"):
        self.m_TextureFormat, self.m_Name, self.decoded = fmt, name, 0

    @property
    def image(self):
        self.decoded += 1
        return "image"


@pytest.mark.parametrize("fmt", [f for f in TextureFormat if f.name.startswith("ASTC_HDR")])
def test_hdr_astc_textures_are_refused_before_decoding(fmt):
    tex = FakeTexture(int(fmt))                                 # UnityPy may give the format as a plain int
    with pytest.raises(unity.UnsupportedTexture, match=rf"'number1_glow': {fmt.name} \(HDR ASTC\)"):
        unity.texture_image(tex)
    with pytest.raises(unity.UnsupportedTexture):
        export.texture_png(tex)
    assert tex.decoded == 0


def test_other_formats_decode():
    for fmt in (TextureFormat.ASTC_RGB_6x6, TextureFormat.RGBA32, TextureFormat.Alpha8):
        tex = FakeTexture(fmt)
        assert unity.texture_image(tex) == "image" and tex.decoded == 1
