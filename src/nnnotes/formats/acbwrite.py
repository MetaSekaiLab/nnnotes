"""Build single-cue ACB files and aligned memory AWB banks from local templates and encoded audio."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .acb import awb_ids, cue_streams, tables, utf_table
from .hca import inspect as inspect_hca

__all__ = ["Column", "Utf", "author", "memory_awb"]

FORMATS = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f", 9: ">d"}
WIDTHS = {key: struct.calcsize(value) for key, value in FORMATS.items()} | {10: 4, 11: 8}


@dataclass(frozen=True)
class Column:
    name: str
    kind: int
    storage: int


@dataclass
class Utf:
    name: str
    version: int
    columns: list[Column]
    rows: list[dict]

    @classmethod
    def read(cls, data: bytes) -> Utf:
        rows = utf_table(data)
        size = struct.unpack_from(">I", data, 4)[0]
        body = memoryview(data)[8 : size + 8]
        version, _, strings_at, data_at, name_at, count, _, _ = struct.unpack_from(">HHIIIHHI", body)
        strings = bytes(body[strings_at:data_at])

        def text(offset: int) -> str:
            return strings[offset : strings.index(b"\0", offset)].decode("utf8")

        columns, position = [], 24
        for _ in range(count):
            flag = body[position]
            position += 1
            if not flag & 0x10 or flag & 0xF0 not in (0x10, 0x30, 0x50):
                raise ValueError(f"unsupported UTF column flag {flag:#x}")
            name_offset = struct.unpack_from(">I", body, position)[0]
            position += 4
            kind, storage = flag & 15, flag & 0xF0
            if kind not in WIDTHS:
                raise ValueError(f"unsupported UTF value type {kind}")
            columns.append(Column(text(name_offset), kind, storage))
            if storage == 0x30:
                position += WIDTHS[kind]
        return cls(text(name_at), version, columns, rows)

    def write(self) -> bytes:
        """Write the original column types/storage and equivalent @UTF rows."""
        strings, string_ids = bytearray(), {}
        blobs = bytearray()

        def string(value: str) -> int:
            if value not in string_ids:
                if "\0" in value:
                    raise ValueError("UTF strings cannot contain null characters")
                string_ids[value] = len(strings)
                strings.extend(value.encode("utf8") + b"\0")
            return string_ids[value]

        table_name = string(self.name)

        def value(kind: int, data) -> bytes:
            if kind in FORMATS:
                return struct.pack(FORMATS[kind], data)
            if kind == 10:
                return struct.pack(">I", string(data))
            if kind == 11:
                raw = bytes(data)
                if not raw:
                    return bytes(8)
                # Every blob begins at a 32-byte relative offset, including AWB.
                blobs.extend(bytes(-len(blobs) % 32))
                offset = len(blobs)
                blobs.extend(raw)
                return struct.pack(">II", offset, len(raw))
            raise ValueError(f"unsupported UTF value type {kind}")

        definitions = bytearray()
        row_columns = []
        for column in self.columns:
            storage = column.storage
            values = [row[column.name] for row in self.rows]
            if storage == 0x30 and values and any(item != values[0] for item in values[1:]):
                storage = 0x50
            definitions.extend(bytes([storage | column.kind]) + struct.pack(">I", string(column.name)))
            if storage == 0x30:
                if not values:
                    raise ValueError("constant UTF columns require one row")
                definitions.extend(value(column.kind, values[0]))
            elif storage == 0x50:
                row_columns.append(column)
            elif any(item is not None for item in values):
                raise ValueError(f"name-only UTF column {column.name} cannot hold a value")
        row_width = sum(WIDTHS[column.kind] for column in row_columns)
        encoded_rows = bytearray()
        for row in self.rows:
            for column in row_columns:
                encoded_rows.extend(value(column.kind, row[column.name]))
        rows_at = 24 + len(definitions)
        strings_at = rows_at + len(encoded_rows)
        # UTF offsets are relative to byte 8; blob addresses must align in the file.
        data_at = ((8 + strings_at + len(strings) + 31) & ~31) - 8
        if rows_at > 65535 or row_width > 65535 or len(self.columns) > 65535:
            raise ValueError("UTF row/header limits exceeded")
        header = struct.pack(
            ">HHIIIHHI",
            self.version,
            rows_at,
            strings_at,
            data_at,
            table_name,
            len(self.columns),
            row_width,
            len(self.rows),
        )
        body = header + definitions + encoded_rows + strings
        body += bytes(data_at - len(body)) + blobs
        result = b"@UTF" + struct.pack(">I", len(body)) + body
        if utf_table(result) != self.rows:
            raise ValueError("written UTF did not preserve row values")
        return result


def adx_metadata(data: bytes) -> dict:
    if len(data) < 20 or data[:2] != b"\x80\0":
        raise ValueError("input must be an ADX stream")
    data_start = struct.unpack_from(">H", data, 2)[0] + 4
    encoding, block_size, sample_bits, channels = struct.unpack_from("4B", data, 4)
    rate, samples = struct.unpack_from(">II", data, 8)
    if data_start > len(data) or data[data_start - 6 : data_start] != b"(c)CRI":
        raise ValueError("invalid ADX data offset/copyright marker")
    if encoding not in (2, 3, 4) or block_size < 3 or sample_bits != 4 or channels not in (1, 2):
        raise ValueError("expected mono/stereo four-bit ADX")
    if not rate or not samples:
        raise ValueError("ADX sample rate/count must be positive")
    if data[19] != 0:
        raise ValueError("input ADX must be unencrypted")
    samples_per_block = (block_size - 2) * 8 // sample_bits
    required_blocks = (samples + samples_per_block - 1) // samples_per_block
    if len(data) - data_start < required_blocks * block_size * channels:
        raise ValueError("ADX audio blocks are truncated")
    # ADX v3/v4 finite loop metadata, when present before the CRI marker.
    loop_offset = {3: 20, 4: 24}.get(data[18])
    if loop_offset is not None and loop_offset + 24 <= data_start - 6:
        if struct.unpack_from(">I", data, loop_offset + 4)[0]:
            raise ValueError("input ADX must not loop")
    return {
        "encoding": encoding,
        "blockSize": block_size,
        "channels": channels,
        "sampleRate": rate,
        "samples": samples,
        "headerVersion": data[18],
        "lengthMs": (samples * 1000 + rate - 1) // rate,
    }


def memory_awb(stream: bytes, *, alignment: int = 32) -> bytes:
    """One-entry AFS2 memory bank: entry ID 0, no encryption subkey."""
    if alignment < 1 or alignment > 65535:
        raise ValueError("AFS2 alignment must be 1..65535")
    header = struct.pack("<4sBBBBIHH", b"AFS2", 2, 4, 4, 0, 1, alignment, 0)
    table_end = len(header) + 4 + 8
    stream_start = (table_end + alignment - 1) // alignment * alignment
    result = header + struct.pack("<III", 0, table_end, stream_start + len(stream))
    result += bytes(stream_start - len(result)) + stream
    if awb_ids(result) != [0]:
        raise ValueError("written AFS2 entry identity failed")
    return result


def hca_metadata(data: bytes, *, allow_encrypted: bool = False) -> dict:
    metadata = inspect_hca(data)
    if not allow_encrypted and metadata["cipherType"] != 0:
        raise ValueError("input HCA must explicitly use cipher type zero")
    metadata["headerVersion"] = metadata.pop("version")
    metadata.pop("tagOffsets")
    return metadata


def author(
    template: bytes, adx: bytes, *, cue_name: str, sheet_name: str | None = None, allow_encrypted_hca: bool = False
) -> tuple[bytes, dict]:
    """Preserve a one-cue template graph while replacing its finite audio."""
    if not cue_name or "\0" in cue_name:
        raise ValueError("cue_name must be nonempty without null characters")
    sheet_name = sheet_name or cue_name
    if not sheet_name or "\0" in sheet_name:
        raise ValueError("sheet_name must be nonempty without null characters")
    is_hca = bytes(x & 127 for x in adx[:4]) == b"HCA\0"
    meta = hca_metadata(adx, allow_encrypted=allow_encrypted_hca) if is_hca else adx_metadata(adx)
    top = Utf.read(template)
    if len(top.rows) != 1:
        raise ValueError("template must have exactly one top-level row")
    original_head, nested = tables(template)
    if any(len(nested.get(name, [])) != 1 for name in ("CueTable", "CueNameTable", "WaveformTable")):
        raise ValueError("template must have exactly one cue, cue name and waveform")
    if len(cue_streams(template)) != 1:
        raise ValueError("template must resolve to exactly one cue")
    top.rows[0]["Name"] = sheet_name
    modified = {}
    for name in ("CueTable", "CueNameTable", "WaveformTable"):
        table = Utf.read(top.rows[0][name])
        row = table.rows[0]
        if name == "CueTable":
            row["Length"] = meta["lengthMs"]
        elif name == "CueNameTable":
            row["CueName"] = cue_name
        else:
            row.update(
                EncodeType=2 if is_hca else 0,
                Streaming=0,
                NumChannels=meta["channels"],
                LoopFlag=1,
                SamplingRate=meta["sampleRate"],
                NumSamples=meta["samples"],
            )
            row["MemoryAwbId" if "MemoryAwbId" in row else "Id"] = 0
            if "StreamAwbId" in row:
                row["StreamAwbId"] = 65535
            if "StreamAwbPortNo" in row:
                row["StreamAwbPortNo"] = 65535
        top.rows[0][name] = table.write()
        modified[name] = table.rows
    top.rows[0]["AwbFile"] = memory_awb(adx)
    output = top.write()
    out_head, out_tables = tables(output)
    expected_preserved = set(nested) - set(modified)
    for name in expected_preserved:
        if out_head[name] != original_head[name] or out_tables[name] != nested[name]:
            raise ValueError(f"unmodified table {name} was changed")
    cues = cue_streams(output)
    cue = cues.get(cue_name)
    if cue is None or cue["lengthMs"] != meta["lengthMs"] or len(cue["streams"]) != 1:
        raise ValueError("authored cue identity or timing is invalid")
    stream = cue["streams"][0]
    if any(
        stream[field] != meta[source]
        for field, source in (("samples", "samples"), ("sampleRate", "sampleRate"), ("channels", "channels"))
    ):
        raise ValueError("authored waveform metadata does not match ADX")
    report = {
        "schema": "nnnotes.acb-build/1",
        "cueName": cue_name,
        "sheetName": sheet_name,
        "hca" if is_hca else "adx": meta,
        "cues": cues,
        "preservedTables": sorted(expected_preserved),
        "templateSha256": hashlib.sha256(template).hexdigest(),
        "hcaSha256" if is_hca else "adxSha256": hashlib.sha256(adx).hexdigest(),
        "acbSha256": hashlib.sha256(output).hexdigest(),
        "bytes": len(output),
        "memoryAwb": {"count": 1, "id": 0, "subkey": 0, "alignment": 32},
    }
    return output, report
