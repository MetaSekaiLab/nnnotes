"""Synthetic CRI files: @UTF tables, ACBs whose cues have the shape of the live cue sheets, and an ACF."""

from __future__ import annotations

import struct

RATE = 48000
# column type -> (struct format, width) of a value stored per row
_FIXED = {0: (">B", 1), 2: (">H", 2), 4: (">I", 4), 8: (">f", 4)}


def utf(name: str, cols: list, rows: list[dict]) -> bytes:
    """A CRI @UTF table: `cols` [(column, type)] stored per row (types 0 u8, 2 u16, 4 u32, 8 f32, 0xA string, 0xB
    data)."""
    strings, data = bytearray(b"<NULL>\0"), bytearray()

    def s(x: str) -> int:
        off = len(strings)
        strings.extend(x.encode("utf-8") + b"\0")
        return off

    name_off = s(name)
    head = b"".join(bytes([0x50 | t]) + struct.pack(">I", s(c)) for c, t in cols)
    body = bytearray()
    for r in rows:
        for c, t in cols:
            v = r.get(c)
            if t == 0xA:
                body += struct.pack(">I", s(v or ""))
            elif t == 0xB:
                v = bytes(v or b"")
                body += struct.pack(">II", len(data), len(v))
                data.extend(v)
            else:
                body += struct.pack(_FIXED[t][0], v or 0)
    width = sum(_FIXED[t][1] if t in _FIXED else {0xA: 4, 0xB: 8}[t] for _, t in cols)
    rows_off = 24 + len(head)
    str_off = rows_off + len(body)
    data_off = str_off + len(strings)
    t = struct.pack(">HHIIIHHI", 1, rows_off, str_off, data_off, name_off, len(cols), width, len(rows))
    t += head + bytes(body) + bytes(strings) + bytes(data)
    return b"@UTF" + struct.pack(">I", len(t)) + t


def afs2(ids: list[int]) -> bytes:
    return (
        b"AFS2"
        + bytes([1, 4, 2, 0])
        + struct.pack("<IHH", len(ids), 32, 0)
        + b"".join(struct.pack("<H", i) for i in ids)
        + bytes(4 * (len(ids) + 1))
    )


def tlv(code: int, payload: bytes) -> bytes:
    return struct.pack(">HB", code, len(payload)) + payload


def u16(*v) -> bytes:
    return b"".join(struct.pack(">H", x) for x in v)


NONE = 65535  # no command list


def live_acb(cues: dict, categories: dict | None = None, buses=("MasterOut",)) -> bytes:
    """An ACB of the live cue sheets' shape: each cue a polyphonic sequence of tracks, each track noting on one synth
    of one waveform of the memory AWB. `cues` {name: {"layers": [{"samples", "volume"?, "bus"?: (bus index, level
    0-10000)}], "categories"?: [category ids], "volume"?: float}}, the waveforms numbered in cue order;
    `categories` {id: name} of the AcfReferenceTable."""
    names, cue_rows, seqs, seq_cmds, tracks, track_cmds, events, synths, synth_cmds, waves = ([] for _ in range(10))
    for n, (name, cue) in enumerate(cues.items()):
        names.append({"CueName": name, "CueIndex": n})
        cue_rows.append({"CueId": n, "ReferenceType": 3, "ReferenceIndex": n, "Length": 1000 + n})
        cmd = b""
        if cue.get("categories"):
            cmd += tlv(65, b"".join(struct.pack(">I", c) for c in cue["categories"]))
        if "volume" in cue:
            cmd += tlv(146, struct.pack(">f", cue["volume"]))
        seq_cmds.append({"Command": cmd})
        first = len(tracks)
        for layer in cue["layers"]:
            track_cmds.append({"Command": tlv(146, struct.pack(">f", layer["volume"])) if "volume" in layer else b""})
            events.append({"Command": tlv(2000, struct.pack(">HH", 2, len(synths)))})
            tracks.append({"CommandIndex": len(track_cmds) - 1, "EventIndex": len(events) - 1})
            bus = layer.get("bus")
            synth_cmds.append({"Command": tlv(111, struct.pack(">HH", *bus)) if bus else b""})
            synths.append({"Type": 0, "ReferenceItems": u16(1, len(waves)), "CommandIndex": len(synth_cmds) - 1})
            waves.append({"MemoryAwbId": len(waves), "Streaming": 0, "NumSamples": layer["samples"], "LoopFlag": 1})
        seqs.append({"Type": 0, "TrackIndex": u16(*range(first, len(tracks))), "CommandIndex": len(seq_cmds) - 1})
    command = [("Command", 0xB)]
    tables = {
        "CueNameTable": utf("CueName", [("CueName", 0xA), ("CueIndex", 2)], names),
        "CueTable": utf("Cue", [("CueId", 4), ("ReferenceType", 0), ("ReferenceIndex", 2), ("Length", 4)], cue_rows),
        "SequenceTable": utf("Sequence", [("Type", 0), ("TrackIndex", 0xB), ("CommandIndex", 2)], seqs),
        "SeqCommandTable": utf("SeqCommand", command, seq_cmds),
        "TrackTable": utf("Track", [("CommandIndex", 2), ("EventIndex", 2)], tracks),
        "TrackCommandTable": utf("TrackCommand", command, track_cmds),
        "TrackEventTable": utf("TrackEvent", command, events),
        "SynthTable": utf("Synth", [("Type", 0), ("ReferenceItems", 0xB), ("CommandIndex", 2)], synths),
        "SynthCommandTable": utf("SynthCommand", command, synth_cmds),
        "WaveformTable": utf(
            "Waveform", [("MemoryAwbId", 2), ("Streaming", 0), ("NumSamples", 4), ("LoopFlag", 0)], waves
        ),
        "AcfReferenceTable": utf(
            "AcfReference",
            [("Id", 4), ("Type", 0), ("Name", 0xA)],
            [{"Id": i, "Type": 3, "Name": n} for i, n in sorted((categories or {}).items())],
        ),
        "StringValueTable": utf("StringValue", [("StringValue", 0xA)], [{"StringValue": b} for b in buses]),
    }
    cols = [(k, 0xB) for k in tables] + [("AwbFile", 0xB), ("Name", 0xA)]
    return utf("Header", cols, [{**tables, "AwbFile": afs2(list(range(len(waves)))), "Name": "sheet"}])


def live_streams(cues: dict, files: dict | None = None) -> list[dict]:
    """streams.json of a live_acb sheet as the decoder writes it: one stream per waveform, named by its cue (a
    repeated name gets `<name>_<stream>`), FLAC files unless `files` {stream: file} says otherwise."""
    out, seen = [], set()
    for name, cue in cues.items():
        for layer in cue["layers"]:
            i = len(out) + 1
            stem = name if name not in seen else f"{name}_{i}"
            seen.add(name)
            out.append(
                {
                    "stream": i,
                    "name": name,
                    "file": (files or {}).get(i, f"{stem}.flac"),
                    "sampleRate": RATE,
                    "channels": 2,
                    "samples": layer["samples"],
                }
            )
    return out


def acf(categories: dict, buses=("MasterOut",), name="Game") -> bytes:
    """An ACF: `categories` {name: (id, group)}, buses of volume 1 without effects, no REACT."""
    tables = {
        "CategoryNameTable": utf(
            "CategoryName", [("Index", 2), ("Name", 0xA)], [{"Index": i, "Name": n} for n, (i, _) in categories.items()]
        ),
        "CategoryTable": utf(
            "Category",
            [("Id", 2), ("GroupIndex", 2), ("CommandIndex", 2)],
            [{"Id": i, "GroupIndex": g, "CommandIndex": NONE} for i, g in categories.values()],
        ),
        "BusNameTable": utf("BusName", [("StringValue", 0xA)], [{"StringValue": b} for b in buses]),
        "BusTable": utf(
            "Bus",
            [("BusNameIndex", 2), ("Volume", 8), ("NumFxs", 0)],
            [{"BusNameIndex": i, "Volume": 1.0, "NumFxs": 0} for i in range(len(buses))],
        ),
    }
    cols = [(k, 0xB) for k in tables] + [("Name", 0xA)]
    return utf("Header", cols, [{**tables, "Name": name}])
