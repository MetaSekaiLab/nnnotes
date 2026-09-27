"""CRI ACB cue tables: which streams each cue of a cue sheet plays, read from the ACB's @UTF tables without decoding
any audio.

An ACB is a tree of @UTF tables (big endian). A cue (CueTable, named in CueNameTable) references a waveform, a synth,
a sequence or a block sequence; synths reference waveforms, synths and sequences (ReferenceItems: u16 type, u16
index); sequences and blocks list tracks, whose events (TrackEventTable, older sheets CommandTable) note on synths
and sequences (command 2000 or 2003: u16 type, u16 index). A waveform stored in the sheet's memory AWB (AFS2, little
endian) has an AWB id (MemoryAwbId); the decoder's stream number is the 1-based position of that id in the AWB's id
list, the order in which cri.decode numbers the streams of `streams.json`.

`cue_streams(acb)` gives, per cue name, its cue id, length (ms) and the streams it reaches in reference order with
the waveform's sample count, rate and channels (for checking against the decoded streams). A waveform streamed from
an external AWB has no stream number here (`streamed`).
"""
from __future__ import annotations

import struct

UTF_MAGIC, AFS2_MAGIC = b"@UTF", b"AFS2"
REF_WAVEFORM, REF_SYNTH, REF_SEQUENCE, REF_BLOCK_SEQUENCE = 1, 2, 3, 8
NOTE_ON = (2000, 2003)
MAX_DEPTH = 32
_FORMATS = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f", 9: ">d"}


class AcbError(ValueError):
    """Bytes that are not an ACB this module reads."""


def utf_table(buf: bytes) -> list[dict]:
    """Rows of a CRI @UTF table (big endian; column storage 0x10 name flag, 0x30 constant, 0x50 per row)."""
    if bytes(buf[:4]) != UTF_MAGIC:
        raise AcbError("not an @UTF table")
    size, = struct.unpack_from(">I", buf, 4)
    t = memoryview(buf)[8:8 + size]
    rows_off, = struct.unpack_from(">H", t, 2)
    str_off, data_off = struct.unpack_from(">II", t, 4)
    ncol, row_w, nrow = struct.unpack_from(">HHI", t, 16)
    strings = bytes(t[str_off:data_off] if data_off > str_off else t[str_off:])

    def cstr(o):
        return strings[o:strings.index(b"\0", o)].decode("utf-8")

    def value(p, typ):
        f = _FORMATS.get(typ)
        if f:
            return struct.unpack_from(f, t, p)[0], p + struct.calcsize(f)
        if typ == 0xA:
            return cstr(struct.unpack_from(">I", t, p)[0]), p + 4
        if typ == 0xB:
            o, n = struct.unpack_from(">II", t, p)
            return bytes(t[data_off + o:data_off + o + n]), p + 8
        raise AcbError(f"@UTF column type {typ}")

    cols, p = [], 24
    for _ in range(ncol):
        flag = t[p]
        p += 1
        name = None
        if flag & 0x10:
            name = cstr(struct.unpack_from(">I", t, p)[0])
            p += 4
        const = None
        if flag & 0xF0 == 0x30:
            const, p = value(p, flag & 0x0F)
        cols.append((name, flag & 0xF0, flag & 0x0F, const))
    rows = []
    for r in range(nrow):
        q, row = rows_off + r * row_w, {}
        for name, storage, typ, const in cols:
            if storage == 0x50:
                row[name], q = value(q, typ)
            else:
                row[name] = const
        rows.append(row)
    return rows


def tables(acb: bytes) -> tuple[dict, dict[str, list[dict]]]:
    """(the ACB's header row, {table name: rows} of its @UTF sub-tables)."""
    top = utf_table(acb)
    if not top:
        raise AcbError("an @UTF table without rows")
    head = top[0]
    return head, {k: utf_table(v) for k, v in head.items() if isinstance(v, bytes) and v[:4] == UTF_MAGIC}


def commands(b: bytes) -> list[tuple[int, bytes]]:
    """An ACB command list: repeated (u16 code, u8 size, payload)."""
    out, p = [], 0
    while p + 3 <= len(b):
        code, = struct.unpack_from(">H", b, p)
        n = b[p + 2]
        out.append((code, b[p + 3:p + 3 + n]))
        p += 3 + n
    return out


def u16s(b: bytes) -> list[int]:
    return [struct.unpack_from(">H", b, i)[0] for i in range(0, len(b) - 1, 2)]


def awb_ids(awb: bytes) -> list[int]:
    """The file ids of an AFS2 archive in stored order (u16 little endian after the 16-byte header)."""
    if bytes(awb[:4]) != AFS2_MAGIC:
        raise AcbError("not an AFS2 archive")
    count, = struct.unpack_from("<I", awb, 8)
    width = awb[6] or 2
    fmt = {2: "<H", 4: "<I"}.get(width)
    if fmt is None:
        raise AcbError(f"AFS2 id width {width}")
    return [struct.unpack_from(fmt, awb, 16 + i * width)[0] for i in range(count)]


class _Walk:
    """The waveforms (WaveformTable indices) a reference reaches, in reference order, each once."""

    def __init__(self, T: dict[str, list[dict]]):
        self.T = T
        self.events = T.get("TrackEventTable") or T.get("CommandTable") or []

    def _row(self, table: str, i: int):
        rows = self.T.get(table) or []
        return rows[i] if 0 <= i < len(rows) else None

    def ref(self, kind: int, index: int, out: list, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise AcbError(f"references nested deeper than {MAX_DEPTH}")
        if kind == REF_WAVEFORM:
            if index not in out and self._row("WaveformTable", index) is not None:
                out.append(index)
        elif kind == REF_SYNTH:
            syn = self._row("SynthTable", index)
            items = (syn or {}).get("ReferenceItems") or b""
            for p in range(0, len(items) - 3, 4):
                t, i = struct.unpack_from(">HH", items, p)
                if t == 0:
                    break
                self.ref(t, i, out, depth + 1)
        elif kind == REF_SEQUENCE:
            seq = self._row("SequenceTable", index)
            self._tracks((seq or {}).get("TrackIndex") or b"", out, depth)
        elif kind == REF_BLOCK_SEQUENCE:
            bs = self._row("BlockSequenceTable", index) or {}
            self._tracks(bs.get("TrackIndex") or b"", out, depth)
            for bi in u16s(bs.get("BlockIndex") or b""):
                self._tracks((self._row("BlockTable", bi) or {}).get("TrackIndex") or b"", out, depth)

    def _tracks(self, index: bytes, out: list, depth: int) -> None:
        for ti in u16s(index):
            tr = self._row("TrackTable", ti)
            ev = (tr or {}).get("EventIndex")
            if ev is None or ev == 0xFFFF or not 0 <= ev < len(self.events):
                continue
            for code, v in commands(self.events[ev].get("Command") or b""):
                if code in NOTE_ON and len(v) >= 4:
                    t, i = struct.unpack_from(">HH", v, 0)
                    self.ref(t, i, out, depth + 1)


def cue_streams(acb: bytes) -> dict[str, dict]:
    """{cue name: {cueId, lengthMs, streams: [{stream, awbId, samples, sampleRate, channels} | {streamed, ...}]}} of
    an ACB (module docstring); a name given to two cues keeps the first."""
    head, T = tables(acb)
    awb = head.get("AwbFile")
    position = {aid: i + 1 for i, aid in enumerate(awb_ids(awb))} if isinstance(awb, bytes) and awb else {}
    waves = T.get("WaveformTable") or []
    cues = T.get("CueTable") or []
    walk = _Walk(T)
    out: dict[str, dict] = {}
    for r in T.get("CueNameTable") or []:
        name, ci = r.get("CueName"), r.get("CueIndex")
        if not name or name in out or ci is None or not 0 <= ci < len(cues):
            continue
        c = cues[ci]
        found: list[int] = []
        walk.ref(c.get("ReferenceType") or 0, c.get("ReferenceIndex") or 0, found)
        streams = []
        for wi in found:
            w = waves[wi]
            info = {"samples": w.get("NumSamples"), "sampleRate": w.get("SamplingRate"),
                    "channels": w.get("NumChannels")}
            memory = (w.get("Streaming") or 0) != 1
            aid = w.get("MemoryAwbId") if "MemoryAwbId" in w else w.get("Id")
            if memory and aid in position:
                streams.append({"stream": position[aid], "awbId": aid, **info})
            else:
                streams.append({"streamed": True, "awbId": w.get("StreamAwbId", aid), **info})
        out[name] = {"cueId": c.get("CueId"), "lengthMs": c.get("Length"), "streams": streams}
    return out
