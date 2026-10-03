"""Synthetic metadata only. The producer never decodes or exports artwork."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import jsonschema
import pytest

from nnnotes import cli, spritegeometry


KEY = "Example/Trimmed"
TREE = {"m_Name": "portrait", "m_Rect": {"x": 9, "y": 7, "width": 120.5, "height": 200.25},
        "m_Pivot": {"x": .5, "y": .5}, "m_Border": {"x": 0, "y": 0, "z": 0, "w": 0}, "m_PixelsToUnits": 100}
RENDER = {"textureRect": {"x": 30.25, "y": 40.5, "width": 60.5, "height": 140.25},
          "textureRectOffset": {"x": 20.75, "y": 10.5}, "settingsRaw": 64}


@pytest.fixture
def observed(tmp_path, monkeypatch):
    bundle_path = tmp_path / "example.bundle"
    bundle_path.write_bytes(b"synthetic bundle")
    sprite = NS(type=NS(name="Sprite"), assets_file=NS(name="synthetic-CAB"),
                path_id=-(1 << 62) + 13, read_typetree=lambda: TREE)
    # Accessing texture pixel data is a regression. It must remain unread.
    def no_texture_read():
        raise AssertionError("texture decode is forbidden in metadata producer")
    texture = NS(type=NS(name="Texture2D"), read=no_texture_read, read_typetree=no_texture_read)
    environment = NS(objects=[sprite, texture])
    class Exporter:
        def __init__(self, cat, out, **options):
            assert options == {"textures": "deferred"}
        def load(self, key):
            assert key == KEY
            return environment, None
        def sprite_render_data(self, obj, tree):
            assert obj is sprite and tree is TREE
            return RENDER, obj, None
    class Catalog:
        source = None
        def has(self, key):
            return key == KEY
        def sources(self):
            return {"remote": b"synthetic catalog"}
        def resolve(self, key):
            return [NS(name=bundle_path.name)]
        def fetch(self, bundle):
            return bundle_path
    monkeypatch.setattr(spritegeometry, "Exporter", Exporter)
    return Catalog(), environment


def test_exact_rect_trim_source_and_int64_identity(observed):
    cat, _ = observed
    document = spritegeometry.observe(cat, [KEY, KEY], region="example")
    entry = document["sprites"][KEY + "[portrait]"]
    assert entry["geometry"]["rect"] == TREE["m_Rect"]
    assert entry["geometry"]["textureRectOffset"] == RENDER["textureRectOffset"]
    assert entry["geometry"]["downscaleMultiplier"] == 1
    assert entry["source"]["pathId"] == str(-(1 << 62) + 13)
    assert entry["source"]["bundles"][0]["sha256"] == hashlib.sha256(b"synthetic bundle").hexdigest()
    assert document["client"] == {} and document["source"]["runtimeVerified"] is False
    schema = json.loads((Path(__file__).parents[1] / "docs/schema/sprite-geometries.schema.json").read_text())
    jsonschema.validate(document, schema)


def test_unknown_or_empty_keys_never_produce_guessed_records(observed):
    cat, _ = observed
    for keys in [[], ["Example/Unknown"]]:
        with pytest.raises(ValueError):
            spritegeometry.observe(cat, keys, region="example")


def test_duplicate_sprite_names_are_an_explicit_error(observed):
    cat, env = observed
    env.objects.append(env.objects[0])
    with pytest.raises(ValueError, match="Ambiguous Sprite"):
        spritegeometry.observe(cat, [KEY], region="example")


def test_cli_keys_file_and_separate_public_bitmap_fingerprint(observed, tmp_path, monkeypatch, capsys):
    cat, _ = observed
    monkeypatch.setattr(cli, "open_catalog", lambda cfg: cat)
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps([KEY]))
    public = tmp_path / "public.json"
    public.write_bytes(b'{"observed":"synthetic bitmap listing"}')
    out = tmp_path / "private" / "sprite-geometries.json"
    cli.main(["--region", "example", "sprite-geometries", "--keys", str(keys), "--public-bitmaps", str(public), "-o", str(out)])
    document = json.loads(out.read_text())
    assert document["region"] == "example"
    assert document["source"]["publicBitmaps"]["sha256"] == hashlib.sha256(public.read_bytes()).hexdigest()
    assert "publicBitmaps" not in document["sprites"][KEY + "[portrait]"]["source"]
    assert json.loads(capsys.readouterr().out)["sprites"] == 1


def test_native_fingerprint_request_without_apk_is_rejected(tmp_path, capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(["sprite-geometries", "--key", KEY, "--native-fingerprints", "-o", str(tmp_path / "x.json")])
    assert result.value.code == 2
    assert "requires a configured APK" in capsys.readouterr().err
