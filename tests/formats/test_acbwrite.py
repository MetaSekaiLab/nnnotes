import struct

import pytest

from nnnotes.formats.acb import cue_streams, tables, utf_table
from nnnotes.formats.acbwrite import Column, Utf, author
from tests.helpers.packing import acb_template, hca_stream


def test_acb_preserves_encoded_stream_and_updates_cue_metadata():
    source = hca_stream(1500, channels=1, rate=44100)
    data, report = author(acb_template(), source, cue_name="example")
    head, nested = tables(data)
    assert head["Name"] == "example"
    assert source in head["AwbFile"]
    assert data.index(b"AFS2") % 32 == 0
    assert nested["WaveformTable"][0]["NumSamples"] == 1500
    assert cue_streams(data)["example"]["streams"][0]["channels"] == 1
    assert report["hca"]["lengthMs"] == (1500 * 1000 + 44100 - 1) // 44100


def test_utf_constant_promotion_and_absolute_blob_alignment():
    table = Utf(
        "Example",
        0,
        [Column("name", 10, 0x30), Column("data", 11, 0x50)],
        [{"name": "one", "data": b"first"}, {"name": "two", "data": b"second"}],
    )
    data = table.write()
    assert utf_table(data) == table.rows
    assert data.index(b"first") % 32 == data.index(b"second") % 32 == 0
    assert (struct.unpack_from(">I", data, 16)[0] + 8) % 32 == 0


def test_encrypted_stream_requires_explicit_compatibility():
    from nnnotes.formats.hca import crypt

    source = crypt(hca_stream(), 321)
    with pytest.raises(ValueError, match="cipher"):
        author(acb_template(), source, cue_name="example")
    data, report = author(acb_template(), source, cue_name="example", allow_encrypted_hca=True)
    assert source in tables(data)[0]["AwbFile"]
    assert report["hca"]["cipherType"] == 56
