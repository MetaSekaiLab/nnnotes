"""Self-contained serialized Unity objects and source media for bundle builder tests."""

from __future__ import annotations

import io
import struct
import wave

from UnityPy.helpers.Tpk import get_typetree_node
from UnityPy.helpers.TypeTreeHelper import write_typetree
from UnityPy.helpers.TypeTreeNode import TypeTreeNode
from UnityPy.helpers.UnityVersion import UnityVersion
from UnityPy.streams import EndianBinaryWriter

from nnnotes.unity.pack import build_text_asset
from tests.helpers.synth import _compress

VERSION = "2022.3.21f1"


def defaults(node):
    if node.m_Type == "string":
        return ""
    if node.m_Type == "TypelessData":
        return b""
    if node.m_Children and node.m_Children[0].m_Type == "Array":
        return []
    if node.m_Children:
        return {child.m_Name: defaults(child) for child in node.m_Children}
    if node.m_Type in ("float", "double"):
        return 0.0
    if node.m_Type == "bool":
        return False
    return 0


def template(*, flags=0x242, stripped=False):
    """A format-22 bundle with a container, binary TextAsset, texture and audio schemas."""
    version = UnityVersion.from_str(VERSION)
    nodes = [
        TypeTreeNode.from_list(
            [
                {k: v for k, v in row.items() if k != "m_Children"}
                for row in get_typetree_node(class_id, version).to_dict_list()
            ]
        )
        for class_id in (142, 49, 28, 83)
    ]
    for node in nodes:
        for child in node.traverse():
            for key in ("m_TypeFlags", "m_RefTypeHash", "m_Index", "m_MetaFlag"):
                if getattr(child, key) is None:
                    setattr(child, key, 0)
    fields = [defaults(node) for node in nodes]
    for value, name in zip(fields, ("source", "text", "picture", "sound")):
        value["m_Name"] = name
    index = fields[0]
    index["m_AssetBundleName"] = "source"
    index["m_PreloadTable"] = [{"m_FileID": 0, "m_PathID": 20}]
    index["m_Container"] = [
        ("assets/text.bytes", {"preloadIndex": 0, "preloadSize": 1, "asset": {"m_FileID": 0, "m_PathID": 20}})
    ]
    texture = fields[2]
    texture.update(m_Width=1, m_Height=1, m_TextureFormat=4, m_CompleteImageSize=4, m_MipCount=1)
    texture["image data"] = b"\x11\x22\x33\xff"
    meta, objects = EndianBinaryWriter(endian="<"), EndianBinaryWriter(endian="<")
    meta.write_string_to_null(VERSION)
    meta.write_int(13)
    meta.write_boolean(not stripped)
    meta.write_int(4)
    for class_id, node in zip((142, 49, 28, 83), nodes):
        meta.write_int(class_id)
        meta.write_boolean(False)
        meta.write_short(-1)
        meta.write_bytes(bytes(16))
        if not stripped:
            node.dump_blob(meta, 22)
            meta.write_int_array([], True)
    meta.write_int(4)
    for i, (node, value) in enumerate(zip(nodes, fields)):
        data = EndianBinaryWriter(endian="<")
        if i == 1:
            data.write(build_text_asset("text", b"template payload"))
        else:
            write_typetree(value, node, data)
        meta.align_stream()
        meta.write_long((i + 1) * 10)
        meta.write_long(objects.Position)
        meta.write_u_int(data.Length)
        meta.write_int(i)
        objects.write(data.bytes)
        objects.align_stream(8)
    meta.write_int(0)
    meta.write_int(0)
    meta.write_int(0)
    meta.write_string_to_null("")
    offset = (48 + meta.Length + 15) & ~15
    header = struct.pack(">4IB3sI3q", 0, 0, 22, 0, 0, bytes(3), meta.Length, offset + objects.Length, offset, 0)
    serialized = header + meta.bytes + bytes(offset - 48 - meta.Length) + objects.bytes
    files = {"CAB-template": serialized, "CAB-template.resS": b"unused template resource"}
    payload = b"".join(files.values())
    compression = {0: "none", 1: "lzma", 2: "lz4", 3: "lz4"}[flags & 0x3F]
    packed = _compress(payload, compression)
    table = bytes(16) + struct.pack(">IIIH", 1, len(payload), len(packed), flags & 0x3F)
    table += struct.pack(">I", len(files))
    at = 0
    for name, data in files.items():
        table += struct.pack(">qqI", at, len(data), 4 if name == "CAB-template" else 0) + name.encode() + b"\0"
        at += len(data)
    info = _compress(table, compression)
    head = bytearray(b"UnityFS\0" + struct.pack(">I", 8) + VERSION.encode() + b"\0" + VERSION.encode() + b"\0")
    size_at = len(head)
    head += struct.pack(">qIII", 0, len(info), len(table), flags)
    head += bytes(-len(head) % 16)
    out = bytearray(head)
    if flags & 0x80:
        out += packed + info
    else:
        out += info
        if flags & 0x200:
            out += bytes(-len(out) % 16)
        out += packed
    struct.pack_into(">q", out, size_at, len(out))
    return bytes(out)


def wav(*, channels=1, width=2, rate=16000):
    stream = io.BytesIO()
    frames = bytes(range(1, 101)) * (channels * width)
    with wave.open(stream, "wb") as out:
        out.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        out.writeframes(frames)
    return stream.getvalue(), frames
