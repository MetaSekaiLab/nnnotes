"""Container-path lookup and jacket decoding over synthetic bundle objects."""

from types import SimpleNamespace

import pytest
from PIL import Image

from nnnotes.musicdata import MusicDataError, catalog_jacket
from nnnotes.unity import reader


def environment(monkeypatch, asset):
    pointer = {"m_FileID": 1, "m_PathID": 7}
    owner = SimpleNamespace(
        type=SimpleNamespace(name="AssetBundle"),
        read_typetree=lambda: {"m_Container": [("Assets/Jacket.PNG", {"asset": pointer})]},
    )

    def dereference(source, ref):
        assert source is owner and ref == pointer
        return asset

    monkeypatch.setattr(reader, "deref", dereference)
    return SimpleNamespace(objects=[SimpleNamespace(type=SimpleNamespace(name="Texture2D")), owner])


def test_container_matches_full_paths_and_resolves_external_references(monkeypatch):
    asset = object()
    env = environment(monkeypatch, asset)
    assert reader.container_asset(env, "assets/jacket.png") is asset
    with pytest.raises(KeyError, match="not in any AssetBundle container"):
        reader.container_asset(env, "jacket.png")
    with pytest.raises(KeyError, match="null reference"):
        reader.container_asset(environment(monkeypatch, None), "assets/jacket.png")


def test_jacket_reads_and_resizes_the_named_texture(monkeypatch):
    import io

    asset = SimpleNamespace(
        type=SimpleNamespace(name="Texture2D"),
        read=lambda: SimpleNamespace(image=Image.new("RGBA", (20, 8), (64, 128, 192, 255))),
    )
    env = environment(monkeypatch, asset)
    requested = []

    class Catalog:
        def fetch_key(self, key):
            requested.append(key)
            return ["bundle"]

        def entry(self, key):
            return {"internal_id": "Assets/Jacket.PNG"}

    def load(paths):
        assert paths == ["bundle"]
        return env

    monkeypatch.setattr(reader, "load_closure", load)
    get = catalog_jacket(Catalog(), size=10)
    image = Image.open(io.BytesIO(get("sample")))
    assert image.format == "WEBP" and image.size == (10, 4)
    assert requested == ["Image/Jacket/sample"]
    asset.type.name = "Sprite"
    with pytest.raises(MusicDataError, match="is not a Texture2D"):
        get("sample")
