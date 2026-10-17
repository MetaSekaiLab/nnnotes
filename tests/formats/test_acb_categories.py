"""The CRI categories of the cues of a synthetic ACB (no game data)."""

import struct

from nnnotes.formats import acb
from tests.pipeline.test_voices import tlv, utf


def acb_with_categories() -> bytes:
    ref = lambda i, t, n: {"Id": i, "Type": t, "Name": n}  # noqa: E731
    tables = {
        "CueNameTable": utf(
            "CueName",
            [("CueName", 0xA), ("CueIndex", 2)],
            [{"CueName": n, "CueIndex": i} for i, n in enumerate(["voice", "plain", "wave"])],
        ),
        "CueTable": utf(
            "Cue",
            [("CueId", 4), ("ReferenceType", 0), ("ReferenceIndex", 2), ("Length", 4)],
            [
                {"CueId": 0, "ReferenceType": 3, "ReferenceIndex": 0, "Length": 10},
                {"CueId": 1, "ReferenceType": 3, "ReferenceIndex": 1, "Length": 10},
                {"CueId": 2, "ReferenceType": 1, "ReferenceIndex": 0, "Length": 10},
            ],
        ),
        "SequenceTable": utf(
            "Sequence",
            [("Type", 0), ("TrackIndex", 0xB), ("CommandIndex", 2)],
            [
                {"Type": 0, "TrackIndex": b"", "CommandIndex": 0},
                {"Type": 0, "TrackIndex": b"", "CommandIndex": 65535},
            ],
        ),
        "SeqCommandTable": utf(
            "SeqCommand", [("Command", 0xB)], [{"Command": tlv(65, struct.pack(">II", 7, 9)) + tlv(146, bytes(4))}]
        ),
        "AcfReferenceTable": utf(
            "AcfReference",
            [("Id", 2), ("Type", 0), ("Name", 0xA)],
            [ref(7, 3, "Voice"), ref(9, 3, "VoiceConfig"), ref(1, 2, "Bus")],
        ),
    }
    cols = [(k, 0xB) for k in tables] + [("Name", 0xA)]
    return utf("Header", cols, [{**tables, "Name": "sheet"}])


def test_a_cue_names_the_categories_its_sequence_puts_it_in():
    assert acb.cue_categories(acb_with_categories()) == {
        "voice": ["Voice", "VoiceConfig"],  # command 65 of its sequence
        "plain": [],  # a sequence without commands
        "wave": [],  # not a sequence
    }
