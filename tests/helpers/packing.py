"""Synthetic single-cue banks and song package inputs."""

import json
import struct

from nnnotes.formats.hca import crc16
from tests.helpers.cri import utf


def hca_stream(samples=2048, channels=2, rate=48000):
    frames = (samples + 1023) // 1024
    head = bytearray(b"HCA\0" + struct.pack(">HH", 0x200, 64))
    head += b"fmt\0" + bytes([channels]) + rate.to_bytes(3, "big")
    head += struct.pack(">IHH", frames, 0, frames * 1024 - samples)
    head += b"comp" + struct.pack(">H", 16) + bytes(10)
    head += b"ciph" + struct.pack(">H", 0) + b"pad\0"
    head += bytes(62 - len(head))
    head += struct.pack(">H", crc16(head))
    output = bytes(head)
    for index in range(frames):
        frame = b"\xff\xff" + bytes((i * 19 + index) & 255 for i in range(12))
        output += frame + struct.pack(">H", crc16(frame))
    return output


def acb_template():
    nested = {
        "CueNameTable": utf("CueName", [("CueName", 10), ("CueIndex", 2)], [{"CueName": "tone", "CueIndex": 0}]),
        "CueTable": utf(
            "Cue",
            [("CueId", 4), ("ReferenceType", 0), ("ReferenceIndex", 2), ("Length", 4)],
            [{"CueId": 0, "ReferenceType": 1, "ReferenceIndex": 0, "Length": 100}],
        ),
        "WaveformTable": utf(
            "Waveform",
            [
                ("MemoryAwbId", 2),
                ("Streaming", 0),
                ("NumSamples", 4),
                ("LoopFlag", 0),
                ("EncodeType", 0),
                ("NumChannels", 0),
                ("SamplingRate", 4),
            ],
            [
                {
                    "MemoryAwbId": 0,
                    "Streaming": 0,
                    "NumSamples": 2048,
                    "LoopFlag": 1,
                    "EncodeType": 2,
                    "NumChannels": 2,
                    "SamplingRate": 48000,
                }
            ],
        ),
    }
    from nnnotes.formats.acbwrite import memory_awb

    return utf(
        "Header",
        [(key, 11) for key in nested] + [("AwbFile", 11), ("Name", 10)],
        [{**nested, "AwbFile": memory_awb(hca_stream()), "Name": "template"}],
    )


def song_tables():
    song = {
        "_id": 1,
        "_titleTextID": "title",
        "_bandNameTextID": "band",
        "_bandIDs": [1],
        "_bestMusicTagIDs": [1],
        "_musicSoundID": 10,
        "_jingleSoundID": 11,
        "_jacketAssetName": "jacket",
        "_liveScoreRankGroup": 1,
        **{f"_{name}ID": 10 + index for index, name in enumerate(("easy", "normal", "hard", "expert"))},
    }
    return {
        "MasterLiveMusic": {"_allData": [song]},
        "MasterLiveMusicScore": {
            "_allData": [
                {"_id": 10 + i, "_musicScoreTextFileName": "chart", "_musicScoreLevel": i + 1, "_fullComboCount": 1}
                for i in range(4)
            ]
        },
        "MasterText": {"_allData": [{"_id": "title", "_english": "Template"}, {"_id": "band", "_english": "Band"}]},
        "MasterSound": {"_allData": [{"_id": i, "_soundCueSheetID": i, "_cueName": "tone"} for i in (10, 11)]},
        "MasterSoundCueSheet": {"_allData": [{"_id": i, "_cueSheetName": "tone"} for i in (10, 11)]},
        "MasterLiveScoreRank": {"_allData": [{"_id": 1, "_group": 1, "_requiredScore": 10}]},
        "MasterBand": {"_allData": [{"_id": 1}]},
        "MasterTag": {"_allData": [{"_id": 1}]},
    }


def project_document():
    return {
        "format": "nnnotes.song-package/1",
        "target": "target.json",
        "template_song": 1,
        "song": {"id": 7, "title": "Example", "performer": "Artist", "bands": [], "tags": ["Example"]},
        "assets": {
            "jacket": "jacket.png",
            "audio": {"full": {"file": "tone.wav"}, "preview": {"from": "full", "start_ms": 0, "duration_ms": 10}},
        },
        "charts": {
            name: {"file": "chart.json", "score_id": 700 + i, "level": i + 1}
            for i, name in enumerate(("easy", "normal", "hard", "expert"))
        },
    }


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def resource_templates():
    from UnityPy.helpers.Tpk import get_typetree_node
    from UnityPy.helpers.UnityVersion import UnityVersion

    from nnnotes.unity.pack import BundleBuilder
    from tests.helpers.packassets import VERSION, defaults, template

    node = get_typetree_node(213, UnityVersion.from_str(VERSION))
    fields = defaults(node)
    pair = fields["m_RenderDataKey"]
    fields["m_RenderDataKey"] = (pair["first"], pair["second"])
    fields["m_Name"] = "jacket"
    fields["m_RD"]["texture"] = {"m_FileID": 0, "m_PathID": 30}
    builder = BundleBuilder(template(), new=False)
    builder.apply(
        {
            "type": "serialized",
            "name": "jacket",
            "path_id": 50,
            "schema": {"class_id": 213, "type_hash": "12" * 16, "nodes": node.to_dict_list()},
            "fields": fields,
            "container": "jacket",
        }
    )
    jacket = builder.build().data
    builder = BundleBuilder(template(), new=False)
    builder.apply(
        {"type": "TextAsset", "name": "text", "op": "replace", "data": bytes(byte ^ 0x5A for byte in acb_template())}
    )
    nodes = [
        [0, "MonoBehaviour", "Base", 0],
        [1, "string", "m_Name", 0x4000],
        [1, "string", "_cueSheetName", 0x4000],
        [1, "vector", "_chunks", 0],
        [2, "Array", "Array", 0x4000],
        [3, "int", "size", 0],
        [3, "PPtr<TextAsset>", "data", 0],
        [4, "int", "m_FileID", 0],
        [4, "SInt64", "m_PathID", 0],
    ]
    builder.apply(
        {
            "type": "serialized",
            "name": "tone",
            "path_id": 50,
            "schema": {"class_id": 114, "type_hash": "12" * 16, "script_id": "34" * 16, "nodes": nodes},
            "fields": {"m_Name": "tone", "_cueSheetName": "tone", "_chunks": [{"m_FileID": 0, "m_PathID": 20}]},
            "container": "sound",
        }
    )
    return {"chart": template(), "jacket": jacket, "full": builder.build().data}


def package_inputs(root, monkeypatch):
    import hashlib

    from PIL import Image

    from nnnotes.source import catalogbuild, master
    from tests.helpers import synth
    from tests.helpers.packassets import wav

    monkeypatch.setenv("NNNOTES_MASTER_KEY", synth.MASTER_KEY.hex())
    monkeypatch.setenv("NNNOTES_MASTER_IV", synth.MASTER_IV.hex())
    tables = song_tables()
    directory = root / "master"
    directory.mkdir()
    listed = []
    key = master.MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
    for name, table in tables.items():
        data = master.encode(json.dumps(table).encode(), key)
        (directory / (name + ".bin")).write_bytes(data)
        listed.append({"name": name + ".bin", "size": len(data), "hash": hashlib.sha256(data).hexdigest()})
    write_json(directory / "MasterManifest.json", {"version": "example", "files": listed})
    inputs = resource_templates()
    bundles = root / "bundles"
    bundles.mkdir()
    for name, data in inputs.items():
        (bundles / (name + ".bundle")).write_bytes(data)
    entries = []
    for key, name, internal, resource in [
        ("Live/MusicScore/chart", "chart", "assets/text.bytes", "UnityEngine.TextAsset"),
        ("Image/Jacket/jacket", "jacket", "jacket", "UnityEngine.Sprite"),
        ("Image/Jacket/small/jacket", "jacket", "jacket", "UnityEngine.Sprite"),
        ("Cri/Sound/tone", "full", "sound", "Example.SplitAcbData"),
    ]:
        entries.append((key, internal, name, resource))
    # Several asset locations can depend on the same bundle location.
    locations = []
    for name in inputs:
        locations.extend(
            catalogbuild.bundle_entries(
                f"https://example.com/{name}.bundle",
                name,
                [],
                hash=hashlib.md5(inputs[name]).hexdigest(),
                crc=0,
                bundle_size=len(inputs[name]),
            )
        )
    for key, internal, name, resource in entries:
        locations.append(
            {
                "primary_key": key,
                "internal_id": internal,
                "resource_type": resource,
                "dependency_keys": [name + ".bundle"],
            }
        )
    (root / "catalog.bin").write_bytes(catalogbuild.rebuild(synth.CatalogWriter().build([]), add=locations))
    (root / "template.acb").write_bytes(acb_template())
    Image.new("RGBA", (2, 2), "red").save(root / "jacket.png")
    (root / "tone.wav").write_bytes(wav(rate=48000)[0])
    write_json(
        root / "chart.json",
        {
            "score": {
                "events": {"bpm": [{"t": 0, "bpm": 120}], "sig": [{"t": 0, "sig": [4, 4]}]},
                "notes": [{"t": 1, "pos": 8, "size": 3}],
            }
        },
    )
    write_json(
        root / "target.json",
        {
            "format": "nnnotes.pack-target/1",
            "catalog": "catalog.bin",
            "master": "master",
            "bundles": "bundles",
            "bundle_base_url": "https://example.com/custom",
            "audio": {"cipher_type": 0, "templates": {"full": "template.acb", "preview": "template.acb"}},
        },
    )
    write_json(root / "song.json", project_document())
    return root / "song.json"
