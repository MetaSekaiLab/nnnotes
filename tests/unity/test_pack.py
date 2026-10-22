import hashlib
import io
import wave

import pytest
import UnityPy
from PIL import Image

from nnnotes.unity import bundlefs
from nnnotes.unity.pack import (
    BundleBuilder,
    TextAsset,
    build_manifest,
    build_text_asset,
    parse_text_asset,
    replace_text_asset,
    wav_to_fsb5,
    write_outputs,
)
from tests.helpers.packassets import template, wav


def test_text_asset_round_trip_keeps_binary_payload():
    payload = bytes(range(256)) * 3  # not valid UTF-8, like gzip-compressed JSON
    raw = build_text_asset("0001_00", payload)
    asset = parse_text_asset(raw)
    assert asset == TextAsset("0001_00", payload)
    assert len(raw) % 4 == 0


def test_text_asset_pads_the_name_before_the_payload():
    raw = build_text_asset("abcd", b"xy")
    assert raw[:4] == (4).to_bytes(4, "little") and raw[4:8] == b"abcd"
    assert raw[8:12] == (2).to_bytes(4, "little") and raw[12:14] == b"xy"
    assert len(raw) == 16  # 2 payload bytes padded to 4
    odd = build_text_asset("abc", b"")
    assert odd[4:7] == b"abc" and odd[7] == 0  # the name is padded to 4 as well


def test_a_payload_that_looks_like_a_name_is_not_mistaken_for_one():
    first = build_text_asset("a", b"\x01\x00\x00\x00abcd")
    raw = build_text_asset("a", parse_text_asset(first).payload)
    assert parse_text_asset(raw).payload == b"\x01\x00\x00\x00abcd"


@pytest.mark.parametrize("raw", [b"", bytes(3), b"\x01\0\0\0a\0\0\0\x09\0\0\0short"])
def test_text_asset_rejects_truncated_fields(raw):
    with pytest.raises(ValueError):
        parse_text_asset(raw)


@pytest.mark.parametrize("flags", [0x40, 0x41, 0x42, 0xC2, 0x242, 0x2C2])
@pytest.mark.parametrize("stripped", [False, True])
def test_new_bundle_preserves_schema_and_flags_and_contains_only_supplied_assets(tmp_path, flags, stripped):
    payload = bytes(range(256))
    (tmp_path / "payload.bin").write_bytes(payload)
    builder = BundleBuilder(template(flags=flags, stripped=stripped), bundle_name="example")
    builder.apply(
        {
            "type": "TextAsset",
            "name": "binary",
            "file": "payload.bin",
            "path_id": 7,
            "container": "assets/binary.bytes",
            "primary_key": "Example/Binary",
        },
        base=tmp_path,
    )
    result = builder.build()
    env = UnityPy.load(result.data)
    assert set(env.assets[0].objects) == {1, 7}
    assert len(env.assets[0].types) == 2
    assert env.assets[0]._enable_type_tree is not stripped
    assert env.file.dataflags == flags
    assert set(env.file.files) == {"CAB-" + hashlib.md5(b"example").hexdigest()}
    assert env.assets[0].unity_version == "2022.3.21f1"
    assert parse_text_asset(env.assets[0].objects[7].get_raw_data()) == TextAsset("binary", payload)
    assert env.container["assets/binary.bytes"].path_id == 7
    index = env.assets[0].objects[1].read_typetree()
    assert index["m_Name"] == "example" and index["m_AssetBundleName"] == "example"
    assert index["m_PreloadTable"] == [{"m_FileID": 0, "m_PathID": 7}]
    assert result.crc == bundlefs.content_crc(result.data)
    assert result.assets == [
        {
            "primary_key": "Example/Binary",
            "internal_id": "assets/binary.bytes",
            "resource_type": "UnityEngine.TextAsset",
        }
    ]


def test_batch_add_replace_and_rename_keep_payload_and_update_container(tmp_path):
    (tmp_path / "first").write_bytes(b"first")
    (tmp_path / "second").write_bytes(b"second")
    (tmp_path / "template.bundle").write_bytes(template())
    result = build_manifest(
        {
            "template": "template.bundle",
            "bundle_name": "example",
            "assets": [
                {"type": "TextAsset", "name": "first", "file": "first", "path_id": 9},
                {"op": "replace", "type": "TextAsset", "name": "first", "file": "second"},
                {"op": "rename", "name": "first", "new_name": "final", "new_container": "assets/final.bytes"},
            ],
        },
        base=tmp_path,
    )
    env = UnityPy.load(result.data)
    assert set(env.container) == {"assets/final.bytes"}
    assert parse_text_asset(env.assets[0].objects[9].get_raw_data()) == TextAsset("final", b"second")


@pytest.mark.parametrize("operation", ["rename", "replace"])
def test_asset_operations_preserve_catalog_fields_and_allow_explicit_overrides(tmp_path, operation):
    (tmp_path / "payload").write_bytes(b"payload")
    builder = BundleBuilder(template(), bundle_name="example")
    metadata = {
        "primary_key": "Example/Text",
        "aliases": ["Example/Alias"],
        "dependency_keys": ["Example/Dependency"],
        "provider": "Example.AssetProvider",
        "resource_type": "Example.Text",
        "resource_type_assembly": "Example.Assembly",
    }
    builder.apply(
        {"type": "TextAsset", "name": "text", "file": "payload", "container": "assets/text.bytes", **metadata},
        base=tmp_path,
    )
    spec = {"op": operation, "name": "text", "new_name": "updated"}
    if operation == "replace":
        spec.update(type="TextAsset", file="payload")
    builder.apply(spec, base=tmp_path)
    assert builder.build().assets == [{**metadata, "internal_id": "assets/text.bytes"}]
    builder.apply(
        {
            "op": "rename",
            "name": "updated",
            "new_name": "final",
            "primary_key": "Example/Final",
            "aliases": [],
            "provider": "Example.FinalProvider",
        }
    )
    assert builder.build().assets == [
        {
            **metadata,
            "internal_id": "assets/text.bytes",
            "primary_key": "Example/Final",
            "aliases": [],
            "provider": "Example.FinalProvider",
        }
    ]


def test_container_rename_preserves_asset_keys_and_metadata(tmp_path):
    (tmp_path / "payload").write_bytes(b"payload")
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply(
        {
            "type": "TextAsset",
            "name": "text",
            "file": "payload",
            "container": "assets/text.bytes",
            "aliases": ["Example/Alias"],
            "provider": "Example.AssetProvider",
        },
        base=tmp_path,
    )
    builder.apply({"op": "rename", "name": "text", "new_name": "updated", "new_container": "assets/updated.bytes"})
    result = builder.build()
    assert result.assets == [
        {
            "primary_key": "assets/text.bytes",
            "internal_id": "assets/updated.bytes",
            "resource_type": "UnityEngine.TextAsset",
            "aliases": ["Example/Alias"],
            "provider": "Example.AssetProvider",
        }
    ]
    assert set(UnityPy.load(result.data).container) == {"assets/updated.bytes"}


def test_catalog_edits_track_original_paths_and_explicit_fields_across_operations(tmp_path):
    (tmp_path / "payload").write_bytes(b"payload")
    builder = BundleBuilder(template(), new=False)
    assert builder.build().catalog_edits == []
    builder.apply({"op": "rename", "name": "text", "new_name": "updated", "new_container": "assets/updated.bytes"})
    builder.apply(
        {
            "op": "replace",
            "type": "TextAsset",
            "name": "updated",
            "file": "payload",
            "aliases": ["Example/Alias"],
            "provider": "Example.Provider",
        },
        base=tmp_path,
    )
    builder.apply(
        {
            "op": "rename",
            "name": "updated",
            "new_name": "final",
            "new_container": "assets/final.bytes",
            "aliases": [],
        }
    )
    assert builder.build().catalog_edits == [
        {
            "source_internal_ids": ["assets/text.bytes"],
            "internal_id": "assets/final.bytes",
            "overrides": {"aliases": [], "provider": "Example.Provider"},
        }
    ]


def test_catalog_edits_identify_added_assets_without_source_paths(tmp_path):
    (tmp_path / "payload").write_bytes(b"payload")
    builder = BundleBuilder(template(), new=False)
    builder.apply(
        {"type": "TextAsset", "name": "added", "file": "payload", "primary_key": "Example/Added"}, base=tmp_path
    )
    builder.apply({"op": "rename", "name": "added", "new_name": "final", "new_container": "assets/final.bytes"})
    assert builder.build().catalog_edits == [
        {
            "source_internal_ids": [],
            "internal_id": "assets/final.bytes",
            "overrides": {"primary_key": "Example/Added"},
        }
    ]


def test_catalog_edits_preserve_each_original_path_and_record_merged_paths(tmp_path):
    source = UnityPy.load(template())
    index = source.assets[0].objects[10]
    fields = index.read_typetree()
    fields["m_Container"].append(("assets/alias.bytes", dict(fields["m_Container"][0][1])))
    index.save_typetree(fields)
    builder = BundleBuilder(source.file.save(packer="original"), new=False)
    builder.apply({"op": "rename", "name": "text", "new_name": "updated"})
    assert builder.build().catalog_edits == [
        {"source_internal_ids": [key], "internal_id": key, "overrides": {}}
        for key in ("assets/text.bytes", "assets/alias.bytes")
    ]
    builder.apply({"op": "rename", "name": "updated", "new_name": "final", "new_container": "assets/final.bytes"})
    assert builder.build().catalog_edits == [
        {
            "source_internal_ids": ["assets/text.bytes", "assets/alias.bytes"],
            "internal_id": "assets/final.bytes",
            "overrides": {},
        }
    ]


def test_replace_compatibility_keeps_existing_path_id_and_container(tmp_path):
    src, out = tmp_path / "source.bundle", tmp_path / "new" / "output.bundle"
    src.write_bytes(template())
    replace_text_asset(src, out, "text", b"\xff\xfe", new_name="renamed")
    env = UnityPy.load(out.read_bytes())
    assert set(env.assets[0].objects) == {10, 20, 30, 40}
    assert env.container["assets/text.bytes"].path_id == 20
    assert parse_text_asset(env.assets[0].objects[20].get_raw_data()) == TextAsset("renamed", b"\xff\xfe")


def test_png_texture_round_trip_preserves_pixels_and_dimensions(tmp_path):
    image = Image.new("RGBA", (3, 2))
    image.putdata([(r, 30, 50, a) for r, a in ((10, 0), (20, 255), (30, 50), (40, 80), (50, 90), (60, 100))])
    image.save(tmp_path / "picture.png")
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply(
        {"type": "Texture2D", "name": "picture", "file": "picture.png", "fields": {"m_TextureDimension": 3}},
        base=tmp_path,
    )
    env = UnityPy.load(builder.build().data)
    texture = env.assets[0].objects[2].read()
    assert texture.image.size == image.size
    assert texture.image.tobytes() == image.tobytes()
    assert texture.m_TextureFormat == 4 and texture.m_MipCount == 1
    assert texture.m_TextureDimension == 2 and texture.m_ImageCount == 1
    assert texture.m_StreamData.size == 0


def test_png_texture_clears_stripped_mip_metadata_from_the_template(tmp_path):
    image = Image.new("RGBA", (8, 4), (10, 20, 30, 255))
    image.save(tmp_path / "picture.png")
    source = UnityPy.load(template())
    texture = source.assets[0].objects[30]
    fields = texture.read_typetree()
    fields["m_MipsStripped"] = 3
    texture.save_typetree(fields)
    builder = BundleBuilder(source.file.save(packer="original"), bundle_name="example")
    builder.apply({"type": "Texture2D", "name": "picture", "file": "picture.png"}, base=tmp_path)
    texture = UnityPy.load(builder.build().data).assets[0].objects[2].read()
    assert texture.m_MipsStripped == 0 and texture.m_MipCount == 1
    assert texture.image.size == image.size and texture.image.tobytes() == image.tobytes()


@pytest.mark.parametrize("channels,width,rate", [(1, 2, 16000), (2, 2, 22050), (1, 2, 12345), (2, 2, 48000)])
def test_audio_clip_wav_round_trip_preserves_pcm(tmp_path, channels, width, rate):
    source, frames = wav(channels=channels, width=width, rate=rate)
    (tmp_path / "tone.wav").write_bytes(source)
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply({"type": "AudioClip", "name": "tone", "file": "tone.wav"}, base=tmp_path)
    env = UnityPy.load(builder.build().data)
    audio = env.assets[0].objects[2].read()
    with wave.open(io.BytesIO(next(iter(audio.samples.values())))) as decoded:
        assert decoded.getnchannels() == channels
        assert decoded.getsampwidth() == width
        assert decoded.getframerate() == rate
        assert decoded.readframes(decoded.getnframes()) == frames
    assert audio.m_CompressionFormat == 0 and audio.m_SubsoundIndex == 0
    assert audio.m_Resource.m_Size > len(frames)


def test_audio_replacements_preserve_other_clips_across_saved_bundle_edits(tmp_path):
    first, first_frames = wav()
    second, second_frames = wav(channels=2, rate=22050)
    replacement, replacement_frames = wav(channels=2, rate=48000)
    final, final_frames = wav(rate=12345)
    for name, data in (("first", first), ("second", second), ("replacement", replacement), ("final", final)):
        (tmp_path / f"{name}.wav").write_bytes(data)
    builder = BundleBuilder(template(), bundle_name="example")
    for name in ("first", "second"):
        builder.apply({"type": "AudioClip", "name": name, "file": f"{name}.wav"}, base=tmp_path)
    bundle = tmp_path / "example.bundle"
    bundle.write_bytes(builder.build().data)
    original = UnityPy.load(bundle.read_bytes())
    original_resource = next(name for name in original.file.files if name.endswith(".resource"))
    original_bytes = original.file.files[original_resource].bytes

    def decoded_clips(data):
        result = {}
        for obj in UnityPy.load(data).assets[0].objects.values():
            if obj.type.name != "AudioClip":
                continue
            clip = obj.read()
            with wave.open(io.BytesIO(next(iter(clip.samples.values())))) as decoded:
                result[clip.m_Name] = (
                    decoded.getnchannels(),
                    decoded.getframerate(),
                    decoded.readframes(decoded.getnframes()),
                )
        return result

    assert decoded_clips(bundle.read_bytes()) == {
        "first": (1, 16000, first_frames),
        "second": (2, 22050, second_frames),
    }
    for name, payload in (("first", "replacement"), ("second", "final")):
        edited = BundleBuilder(bundle, new=False)
        edited.apply({"op": "replace", "type": "AudioClip", "name": name, "file": f"{payload}.wav"}, base=tmp_path)
        bundle.write_bytes(edited.build().data)
        saved = UnityPy.load(bundle.read_bytes())
        assert saved.file.files[original_resource].bytes == original_bytes
        expected = {"first": (2, 48000, replacement_frames)}
        expected["second"] = (2, 22050, second_frames) if name == "first" else (1, 12345, final_frames)
        assert decoded_clips(bundle.read_bytes()) == expected
    resources = [name for name in saved.file.files if name.endswith(".resource")]
    assert len(resources) == 3


def test_fsb5_rejects_non_wav_and_empty_pcm():
    with pytest.raises(ValueError, match="WAV"):
        wav_to_fsb5(b"not audio", "tone")
    data = io.BytesIO()
    with wave.open(data, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    with pytest.raises(ValueError, match="empty"):
        wav_to_fsb5(data.getvalue(), "tone")
    with pytest.raises(ValueError, match="16-bit"):
        wav_to_fsb5(wav(width=1)[0], "tone")


def test_serialized_fields_use_explicit_schema_and_validate_references(tmp_path):
    schema = {
        "class_id": 114,
        "type_hash": "12" * 16,
        "script_id": "34" * 16,
        "nodes": [
            [0, "MonoBehaviour", "Base", 0],
            [1, "string", "m_Name", 0x4000],
            [1, "PPtr<Object>", "target", 0],
            [2, "int", "m_FileID", 0],
            [2, "SInt64", "m_PathID", 0],
        ],
    }
    (tmp_path / "data").write_bytes(b"binary")
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply(
        {
            "type": "serialized",
            "name": "link",
            "path_id": 2,
            "schema": schema,
            "fields": {"m_Name": "", "target": {"m_FileID": 0, "m_PathID": 3}},
        }
    )
    with pytest.raises(ValueError, match="unresolved reference"):
        builder.build()
    builder.apply({"type": "TextAsset", "name": "binary", "path_id": 3, "file": "data"}, base=tmp_path)
    env = UnityPy.load(builder.build().data)
    assert env.assets[0].objects[2].read_typetree()["target"]["m_PathID"] == 3
    index = env.assets[0].objects[1].read_typetree()
    assert index["m_Container"][0][1]["preloadSize"] == 2


def test_cloned_monobehaviours_keep_resolvable_script_type_indices():
    schema = {
        "class_id": 114,
        "type_hash": "12" * 16,
        "script_id": "34" * 16,
        "nodes": [
            [0, "MonoBehaviour", "Base", 0],
            [1, "string", "m_Name", 0x4000],
            [1, "PPtr<MonoScript>", "m_Script", 0],
            [2, "int", "m_FileID", 0],
            [2, "SInt64", "m_PathID", 0],
        ],
    }
    source = BundleBuilder(template(), bundle_name="source")
    source.add_external("archive:/scripts/scripts")
    source.apply(
        {
            "type": "serialized",
            "schema": schema,
            "path_id": 2,
            "name": "component",
            "fields": {"m_Name": "component", "m_Script": {"m_FileID": 1, "m_PathID": 42}},
        }
    )
    builder = BundleBuilder(source.build().data, bundle_name="example")
    builder.add_external("archive:/scripts/scripts")
    for path_id, script_id in [(2, 42), (3, 43)]:
        builder.apply(
            {
                "type": "serialized",
                "template": {"type": "MonoBehaviour"},
                "path_id": path_id,
                "name": f"component{path_id}",
                "fields": {"m_Name": "", "m_Script": {"m_FileID": 1, "m_PathID": script_id}},
            }
        )
    result = UnityPy.load(builder.build().data).assets[0]
    for path_id, script_id in [(2, 42), (3, 43)]:
        obj = result.objects[path_id]
        ref = result.script_types[obj.serialized_type.script_type_index]
        assert (ref.local_serialized_file_index, ref.local_identifier_in_file) == (1, script_id)


def test_raw_assets_require_explicit_reference_ownership(tmp_path):
    (tmp_path / "raw").write_bytes(build_text_asset("raw", b"opaque"))
    builder = BundleBuilder(template(), bundle_name="example")
    spec = {"type": "raw", "template": {"type": "TextAsset"}, "name": "raw", "file": "raw"}
    with pytest.raises(ValueError, match="managed_references"):
        builder.apply(spec, base=tmp_path)
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply({**spec, "managed_references": True}, base=tmp_path)
    env = UnityPy.load(builder.build().data)
    assert parse_text_asset(env.assets[0].objects[2].get_raw_data()) == TextAsset("raw", b"opaque")


def test_duplicate_path_ids_and_container_paths_are_rejected(tmp_path):
    (tmp_path / "data").write_bytes(b"data")
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply({"type": "TextAsset", "name": "one", "file": "data", "container": "same"}, base=tmp_path)
    with pytest.raises(ValueError, match="path_id"):
        builder.apply({"type": "TextAsset", "name": "two", "file": "data", "path_id": 2}, base=tmp_path)
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply({"type": "TextAsset", "name": "one", "file": "data", "container": "same"}, base=tmp_path)
    with pytest.raises(ValueError, match="container"):
        builder.apply({"type": "TextAsset", "name": "two", "file": "data", "container": "same"}, base=tmp_path)
    with pytest.raises(ValueError, match="failed operation"):
        builder.build()


def test_atomic_outputs_restore_prior_files_when_publication_fails(tmp_path, monkeypatch):
    import nnnotes.unity.pack as pack

    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"original one")
    two.write_bytes(b"original two")
    original = pack.os.replace
    count = 0

    def failing(source, target):
        nonlocal count
        count += 1
        if count == 4:
            raise OSError("replacement failed")
        return original(source, target)

    monkeypatch.setattr(pack.os, "replace", failing)
    with pytest.raises(OSError, match="replacement failed"):
        write_outputs({one: b"new one", two: b"new two"})
    assert one.read_bytes() == b"original one" and two.read_bytes() == b"original two"
    assert set(tmp_path.iterdir()) == {one, two}


def test_new_bundle_requires_explicit_bundle_identity():
    with pytest.raises(ValueError, match="bundle_name"):
        BundleBuilder(template())


def test_edit_serialized_reference_rebuilds_preload_closure(tmp_path):
    schema = {
        "class_id": 114,
        "type_hash": "12" * 16,
        "script_id": "34" * 16,
        "nodes": [
            [0, "MonoBehaviour", "Base", 0],
            [1, "string", "m_Name", 0x4000],
            [1, "PPtr<Object>", "target", 0],
            [2, "int", "m_FileID", 0],
            [2, "SInt64", "m_PathID", 0],
        ],
    }
    (tmp_path / "data").write_bytes(b"data")
    builder = BundleBuilder(template(), bundle_name="example")
    for path_id in (3, 4):
        builder.apply({"type": "TextAsset", "name": str(path_id), "path_id": path_id, "file": "data"}, base=tmp_path)
    builder.apply(
        {
            "type": "serialized",
            "name": "link",
            "path_id": 2,
            "schema": schema,
            "fields": {"m_Name": "link", "target": {"m_FileID": 0, "m_PathID": 3}},
        }
    )
    edited = BundleBuilder(builder.build().data, new=False)
    edited.apply(
        {
            "op": "replace",
            "type": "serialized",
            "name": "link",
            "fields": {"m_Name": "link", "target": {"m_FileID": 0, "m_PathID": 4}},
        }
    )
    env = UnityPy.load(edited.build().data)
    index = env.assets[0].objects[1].read_typetree()
    entry = dict(index["m_Container"])["link"]
    refs = index["m_PreloadTable"][entry["preloadIndex"] : entry["preloadIndex"] + entry["preloadSize"]]
    assert refs == [{"m_FileID": 0, "m_PathID": 2}, {"m_FileID": 0, "m_PathID": 4}]


def test_failed_recovery_keeps_original_output_backups(tmp_path, monkeypatch):
    import nnnotes.unity.pack as pack

    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"original one")
    two.write_bytes(b"original two")
    original = pack.os.replace
    count = 0

    def failing(source, target):
        nonlocal count
        count += 1
        if count >= 4:
            raise OSError("filesystem unavailable")
        return original(source, target)

    monkeypatch.setattr(pack.os, "replace", failing)
    with pytest.raises(OSError, match="recoverable backup") as failure:
        write_outputs({one: b"new one", two: b"new two"})
    backups = list(tmp_path.iterdir())
    assert {path.read_bytes() for path in backups} == {b"original one", b"original two"}
    assert all(str(path) in str(failure.value) for path in backups)


def test_external_stripped_template_recovers_builtin_schema(tmp_path):
    (tmp_path / "stripped.bundle").write_bytes(template(stripped=True))
    (tmp_path / "data").write_bytes(b"binary")
    builder = BundleBuilder(template(), bundle_name="example")
    builder.apply(
        {
            "type": "TextAsset",
            "name": "binary",
            "file": "data",
            "template": {"file": "stripped.bundle", "type": "TextAsset"},
        },
        base=tmp_path,
    )
    result = builder.build()
    env = UnityPy.load(result.data)
    assert env.assets[0].objects[2].serialized_type.node is not None
    assert parse_text_asset(env.assets[0].objects[2].get_raw_data()).payload == b"binary"


def test_external_reference_is_included_in_preload_without_local_dereference(tmp_path):
    schema = {
        "class_id": 114,
        "type_hash": "12" * 16,
        "script_id": "34" * 16,
        "nodes": [
            [0, "MonoBehaviour", "Base", 0],
            [1, "string", "m_Name", 0x4000],
            [1, "PPtr<Object>", "target", 0],
            [2, "int", "m_FileID", 0],
            [2, "SInt64", "m_PathID", 0],
        ],
    }
    builder = BundleBuilder(template(), bundle_name="example")
    assert builder.add_external("CAB-external") == 1
    builder.apply(
        {
            "type": "serialized",
            "name": "link",
            "path_id": 2,
            "schema": schema,
            "fields": {"m_Name": "link", "target": {"m_FileID": 1, "m_PathID": 99}},
        }
    )
    env = UnityPy.load(builder.build().data)
    index = env.assets[0].objects[1].read_typetree()
    assert index["m_PreloadTable"] == [{"m_FileID": 0, "m_PathID": 2}, {"m_FileID": 1, "m_PathID": 99}]


def test_edit_main_asset_preload_tracks_reordered_container(tmp_path):
    source = UnityPy.load(template())
    index = source.assets[0].objects[10]
    fields = index.read_typetree()
    fields["m_MainAsset"] = {"preloadIndex": 0, "preloadSize": 1, "asset": {"m_FileID": 0, "m_PathID": 20}}
    index.save_typetree(fields)
    builder = BundleBuilder(source.file.save(packer="original"), new=False)
    (tmp_path / "data").write_bytes(b"second")
    builder.apply(
        {"type": "TextAsset", "name": "second", "file": "data", "template": {"type": "TextAsset"}}, base=tmp_path
    )
    builder.apply({"op": "rename", "name": "text", "new_name": "text", "new_container": "Assets/renamed.bytes"})
    env = UnityPy.load(builder.build().data)
    fields = env.assets[0].objects[10].read_typetree()
    main = fields["m_MainAsset"]
    assert fields["m_PreloadTable"][main["preloadIndex"] : main["preloadIndex"] + main["preloadSize"]] == [
        {"m_FileID": 0, "m_PathID": 20}
    ]


def test_manifest_registers_explicit_externals_and_bundle_dependencies(tmp_path):
    (tmp_path / "template.bundle").write_bytes(template())
    schema = {
        "class_id": 114,
        "type_hash": "12" * 16,
        "script_id": "34" * 16,
        "nodes": [
            [0, "MonoBehaviour", "Base", 0],
            [1, "string", "m_Name", 0x4000],
            [1, "PPtr<Object>", "target", 0],
            [2, "int", "m_FileID", 0],
            [2, "SInt64", "m_PathID", 0],
        ],
    }
    result = build_manifest(
        {
            "template": "template.bundle",
            "bundle_name": "example",
            "externals": [{"path": "CAB-dependency", "guid": "56" * 16, "type": 2}],
            "dependencies": ["dependency"],
            "assets": [
                {
                    "type": "serialized",
                    "name": "link",
                    "schema": schema,
                    "fields": {"m_Name": "link", "target": {"m_FileID": 1, "m_PathID": 99}},
                },
            ],
        },
        base=tmp_path,
    )
    env = UnityPy.load(result.data)
    external = env.assets[0].externals[0]
    assert (external.path, external.guid, external.type) == ("CAB-dependency", bytes.fromhex("56" * 16), 2)
    assert env.assets[0].objects[1].read_typetree()["m_Dependencies"] == ["dependency"]
