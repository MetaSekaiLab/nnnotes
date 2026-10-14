"""The type trees of game script classes that ship with nnnotes.

IL2CPP player builds strip the type trees of script classes from the player's own files (`assets/bin/Data/` of the
APK: the global managers, the URP pipeline assets, renderer data, renderer features and post-process data), so a
MonoBehaviour there reads only its header. nnnotes ships the type trees of the classes it reads from those files in
`typetrees/<Unity version>.json`, generated from the game's managed assembly stubs by `scripts/gen_typetrees.py`:

    {"format": 1, "unityVersion", "gameVersion", "classes": {"assembly|namespace|class": {"typeHash", "nodes"}}}

A class is keyed by the names its MonoScript stores (typetree_key); `nodes` are [level, type, name, meta flag] rows
of Unity's layout and `typeHash` the hex type hash the serialized file stores for the class. A tree fits an object
when the type hashes are equal and its read consumes exactly the object's serialized size.

Nothing here imports UnityPy until typetree_node is called.
"""

from __future__ import annotations

import json
import re
from importlib import resources

__all__ = [
    "TYPETREE_FORMAT",
    "dump_typetrees",
    "load_typetrees",
    "script_entry",
    "script_types",
    "typetree_file",
    "typetree_key",
    "typetree_node",
]

TYPETREE_FORMAT = 1


def typetree_key(assembly: str, namespace: str, cls: str) -> str:
    """Key of a class in a typetree file: `assembly|namespace|class`, as its MonoScript names them."""
    return f"{assembly}|{namespace}|{cls}"


def script_entry(assembly: str | None, namespace: str | None, cls: str | None) -> str:
    """The script table entry of a class: "assembly|namespace|class", the assembly without `.dll`, an empty
    namespace empty."""
    asm = assembly or ""
    if asm.endswith(".dll"):
        asm = asm[:-4]
    return f"{asm}|{namespace or ''}|{cls or ''}"


def typetree_file(unity_version: str):
    """The embedded typetree file of a Unity version (an importlib.resources Traversable; it may not exist)."""
    return resources.files("nnnotes.unity").joinpath("typetrees", f"{unity_version}.json")


def load_typetrees(unity_version: str) -> dict | None:
    """The embedded typetrees of a Unity version, or None when nnnotes has none for it."""
    if not re.fullmatch(r"[0-9A-Za-z.]+", unity_version or ""):
        return None
    f = typetree_file(unity_version)
    if not f.is_file():
        return None
    doc = json.loads(f.read_text(encoding="utf-8"))
    if doc.get("format") != TYPETREE_FORMAT or doc.get("unityVersion") != unity_version:
        raise ValueError(f"typetree file {unity_version}.json: unexpected format or Unity version")
    return doc


def script_types(unity_version: str) -> dict[str, dict]:
    """{script table entry (script_entry): {"typeHash", "nodes"}} of the embedded typetrees of a Unity version ({}
    when nnnotes has none for it)."""
    doc = load_typetrees(unity_version)
    if doc is None:
        return {}
    return {script_entry(*k.split("|", 2)): v for k, v in sorted(doc["classes"].items())}


def typetree_node(rows: list):
    """UnityPy TypeTreeNode from [level, type, name, meta flag] rows (byte size and version 0, as generated)."""
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode

    return TypeTreeNode.from_list([TypeTreeNode(lv, ty, nm, 0, 0, m_MetaFlag=mf) for lv, ty, nm, mf in rows])


def dump_typetrees(doc: dict) -> str:
    """Text of a typetree file: sorted keys, one class per line, LF."""
    head = {k: v for k, v in doc.items() if k != "classes"}
    lines = [json.dumps(head, sort_keys=True, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"classes":{']
    items = sorted(doc["classes"].items())
    for i, (k, v) in enumerate(items):
        lines.append(
            json.dumps(k, ensure_ascii=False)
            + ":"
            + json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            + ("," if i + 1 < len(items) else "")
        )
    lines.append("}}")
    return "\n".join(lines) + "\n"
