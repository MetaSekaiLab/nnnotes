"""Regenerate the embedded MonoBehaviour typetrees from an APK (maintainer tool; not installed, not needed to use
nnnotes).

nnnotes reads a few MonoBehaviours of the APK's boot data (`assets/bin/Data/data.unity3d`): the URP pipeline
assets, renderer data, renderer features and post-process data, DOTween and TextMeshPro settings, and the sound
and app-config defaults. IL2CPP player builds strip script typetrees from that data, so nnnotes ships the
typetrees of exactly these classes (CLASSES below) in `src/nnnotes/typetrees/<Unity version>.json`, one file per
Unity version. Each entry holds the typetree nodes as [level, type, name, meta flag] rows and the type hash the
serialized file records for the class; the file also records the game version it was generated from.

The rows have the node layout Unity itself serializes for a MonoBehaviour (as in the typetrees of asset bundles
built with them): root type `MonoBehaviour`; a `string` is `string / Array / int size / char data`; built-in
structs carry their native names (`Vector3f`, `ColorRGBA`, `AABB`, ...), and arrays of them, of strings and of
PPtrs are `vector`; the align flag (0x4000, align the stream to 4 bytes after the node) sits where Unity puts it.
unity_rows turns the rows of TypeTreeGeneratorAPI's default (AssetsTools) backend into that layout. The other meta
flag bits are editor-only and left 0.

Supporting a new game version
-----------------------------
When a command that reads the player data (`player`, `story`, `live`, `web`) stops with "... is not supported
yet", regenerate the file from the new APK:

1. Produce managed assembly stubs of the same game version, e.g. the `DummyDll` folder Il2CppDumper writes from
   the game's `libil2cpp.so` and `global-metadata.dat`. `--il2cpp <libil2cpp.so> <global-metadata.dat>` lets
   the typetree generator read the binary directly instead.
2. Run, from the repository root (UnityPy's optional `TypeTreeGeneratorAPI` package must be installed):

       python scripts/gen_typetrees.py <base.apk> --dll <DummyDll folder>

   It reads every object of each class in CLASSES with the generated typetree (the read must consume exactly the
   object's serialized size), then writes `src/nnnotes/typetrees/<Unity version>.json` (`-o` for another
   directory; `--game-version` when the APK's manifest has no versionName). A class that is not in the boot data,
   has a field Unity's layout is not known for here (a `[SerializeReference]` field, another array form), or does
   not read back stops the script without writing anything.
3. When a new game version keeps the Unity version, the file is replaced; a new Unity version adds a file. Keep
   the older files while the game versions they describe are still in use.
4. When code starts reading another MonoBehaviour class with `PlayerData.mono`, add the class to CLASSES and
   regenerate.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

from nnnotes.player import (TYPETREE_FORMAT, PlayerData, dump_typetrees, typetree_key, typetree_node)

# (assembly, namespace, class) as the MonoScript objects name them
CLASSES = (
    ("Adv", "AdvSystem.Rendering", "AdvFieldRendererFeature"),
    ("App.Runtime", "App.Config", "AppConfigDefaultData"),
    ("DOTween.dll", "DG.Tweening.Core", "DOTweenSettings"),
    ("Fwk", "Fwk.Rendering", "ScreenCaptureRendererFeature"),
    ("Fwk", "Fwk.Sound", "SoundVolumeSettings"),
    ("Fwk", "Fwk.UI.Rendering", "UIRendererFeature"),
    ("SiriusAsset", "App.GachaAnimation.Blur.DualKawase", "DualKawaseBlurFeature"),
    ("SiriusAsset", "App.GachaAnimation.ChromaticAberration", "ChromaticAberrationFeature"),
    ("SiriusAsset", "App.GachaAnimation.Paraffin", "ParaffinFeature"),
    ("SiriusAsset", "App.GachaAnimation.PostProcessing", "CameraBlendFeature"),
    ("SiriusAsset", "App.GachaAnimation.PostProcessing", "RadialBlurFeature"),
    ("SiriusAsset", "App.UI.Text.DropShadow", "TextDropShadowFeature"),
    ("Unity.RenderPipelines.Universal.Runtime", "UnityEngine.Rendering.Universal", "PostProcessData"),
    ("Unity.RenderPipelines.Universal.Runtime", "UnityEngine.Rendering.Universal", "UniversalRenderPipelineAsset"),
    ("Unity.RenderPipelines.Universal.Runtime", "UnityEngine.Rendering.Universal", "UniversalRendererData"),
    ("Unity.TextMeshPro", "TMPro", "TMP_Settings"),
    ("Unity.TextMeshPro", "TMPro", "TMP_StyleSheet"),
)
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "src" / "nnnotes" / "typetrees"

ALIGN = 0x4000                  # TransferMetaFlags.kAlignBytesFlag
# the MonoBehaviour header: field -> meta flag
HEADER = {"m_GameObject": 0, "m_Enabled": ALIGN, "m_Script": 0, "m_Name": 0}
# built-in structs the generator names after their C# type -> the native type name Unity serializes
NATIVE_TYPES = {"Bounds": "AABB", "Color": "ColorRGBA", "Color32": "ColorRGBA", "LayerMask": "BitField",
                "Quaternion": "Quaternionf", "Rect": "Rectf", "Vector2": "Vector2f", "Vector2Int": "int2_storage",
                "Vector3": "Vector3f", "Vector4": "Vector4f"}
# built-in structs whose C# and native names agree (their arrays are `vector`s too)
NATIVE_STRUCTS = {*NATIVE_TYPES, "AnimationCurve", "Gradient"}
# built-in structs the generator leaves without fields -> their fields
NATIVE_FIELDS = {"RenderingLayerMask": [("unsigned int", "m_Bits")]}
BYTE_TYPES = ("UInt8", "SInt8")


@dataclass
class _Node:
    type: str
    name: str
    flags: int
    children: list = field(default_factory=list)


def _tree(rows: list) -> _Node:
    stack: list[_Node] = []
    for lv, ty, nm, mf in rows:
        node = _Node(ty, nm, mf)
        del stack[lv:]
        if stack:
            stack[-1].children.append(node)
        stack.append(node)
    return stack[0]


def _rows(node: _Node, level: int = 0, out: list | None = None) -> list:
    out = [] if out is None else out
    out.append([level, node.type, node.name, node.flags])
    for c in node.children:
        _rows(c, level + 1, out)
    return out


def _flag(node: _Node, on: bool) -> None:
    node.flags = node.flags | ALIGN if on else node.flags & ~ALIGN


def _string(node: _Node) -> None:
    """A string in Unity's form: the string node unaligned, its Array aligned, size and char data unaligned."""
    if not node.children:
        node.children = [_Node("Array", "Array", 0, [_Node("int", "size", 0), _Node("char", "data", 0)])]
    _flag(node, False)
    _flag(node.children[0], True)


def _array_parts(node: _Node):
    """(Array node, element node) when the node is an array container, else None."""
    if len(node.children) == 1 and node.children[0].type == "Array":
        arr = node.children[0]
        if [c.name for c in arr.children] != ["size", "data"]:
            raise ValueError(f"{node.name}: unexpected Array children {[c.name for c in arr.children]}")
        return arr, arr.children[1]
    return None


def _normalize(node: _Node) -> None:
    if node.type == "managedReference":
        raise ValueError(f"{node.name}: [SerializeReference] fields are not supported")
    parts = _array_parts(node)
    if node.type == "PropertyName":              # ExposedReference name: string { string id }
        (inner,) = node.children
        _normalize(inner)
        node.type = "string"
        _flag(node, False)
        _flag(inner, True)
        return
    if node.type == "string" and (parts is None or parts[1].type == "char"):
        _string(node)
        return
    if parts is not None:
        arr, elem = parts
        if node.type == "vector" or elem.type in NATIVE_STRUCTS or elem.type == "string":
            node.type = "vector"                 # arrays of primitives, strings, PPtrs and built-in structs
            _flag(node, elem.type in BYTE_TYPES)
            _flag(arr, not elem.type.startswith("PPtr<"))
        elif node.type == elem.type:             # an array of a serializable class keeps the class name
            _flag(arr, False)
        else:
            raise ValueError(f"{node.name}: array container {node.type} of {elem.type}")
        _flag(arr.children[0], False)
        _flag(elem, False)
    if not node.children and node.type in NATIVE_FIELDS:
        node.children = [_Node(ty, nm, 0) for ty, nm in NATIVE_FIELDS[node.type]]
    node.type = NATIVE_TYPES.get(node.type, node.type)
    for c in node.children:
        _normalize(c)


def unity_rows(rows: list) -> list:
    """Generated [level, type, name, meta flag] rows in the layout Unity serializes MonoBehaviour typetrees with
    (see the module docstring). Raises ValueError on what that layout is not known for."""
    root = _tree(rows)
    names = [c.name for c in root.children[:len(HEADER)]]
    if names != list(HEADER):
        raise ValueError(f"unexpected MonoBehaviour header {names}")
    root.type, root.flags = "MonoBehaviour", 0
    for c in root.children:
        _normalize(c)
    for c in root.children[:len(HEADER)]:
        c.flags = HEADER[c.name]
        for k in c.children:
            if c.type.startswith("PPtr<"):
                k.flags = 0
    return _rows(root)


def objects_by_class(player: PlayerData) -> dict[tuple[str, str, str], list]:
    out: dict[tuple[str, str, str], list] = {}
    for o in player._by_type.get("MonoBehaviour", []):
        try:
            k = player.script(o)
        except Exception:            # script in a file outside the boot data, or no script
            continue
        out.setdefault(k, []).append(o)
    return out


def generate(player: PlayerData, gen: TypeTreeGenerator, game_version: str) -> dict:
    found = objects_by_class(player)
    classes = {}
    for asm, ns, cls in CLASSES:
        name = f"{ns}.{cls}" if ns else cls
        objs = found.get((asm, ns, cls))
        if not objs:
            raise SystemExit(f"{name} ({asm}): no object of this class in the boot data")
        try:
            rows = unity_rows([[n.m_Level, n.m_Type, n.m_Name, n.m_MetaFlag]
                               for n in gen.get_nodes(asm if asm.endswith(".dll") else f"{asm}.dll", name)])
        except ValueError as e:
            raise SystemExit(f"{name} ({asm}): {e}")
        node = typetree_node(rows)
        hashes = {o.serialized_type.old_type_hash.hex() for o in objs}
        if len(hashes) != 1:
            raise SystemExit(f"{name} ({asm}): {len(hashes)} different type hashes")
        for o in objs:
            try:
                o.read_typetree(node, check_read=True)
            except Exception as e:
                raise SystemExit(f"{name} ({asm}): generated typetree does not read object {o.path_id}: {e}")
        classes[typetree_key(asm, ns, cls)] = {"typeHash": hashes.pop(), "nodes": rows}
    return {"format": TYPETREE_FORMAT, "unityVersion": player.unity_version, "gameVersion": game_version,
            "classes": classes}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="regenerate src/nnnotes/typetrees/<Unity version>.json from an APK")
    p.add_argument("apk", type=Path, help="base.apk of the game version")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dll", type=Path, help="folder of managed assembly stubs (e.g. Il2CppDumper DummyDll)")
    g.add_argument("--il2cpp", nargs=2, type=Path, metavar=("LIBIL2CPP", "METADATA"),
                   help="libil2cpp.so and global-metadata.dat of the same game version")
    p.add_argument("--game-version", help="game version to record (default: the APK manifest's versionName)")
    p.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="output directory")
    args = p.parse_args(argv)

    player = PlayerData(args.apk)
    game_version = args.game_version or player.game_version
    if not game_version:
        raise SystemExit("the APK manifest has no versionName: give --game-version")
    gen = TypeTreeGenerator(player.unity_version)
    if args.dll:
        gen.load_local_dll_folder(str(args.dll))
    else:
        gen.load_il2cpp(args.il2cpp[0].read_bytes(), args.il2cpp[1].read_bytes())
    doc = generate(player, gen, game_version)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{player.unity_version}.json"
    out.write_bytes(dump_typetrees(doc).encode("utf-8"))
    print(f"{out}  ({len(doc['classes'])} classes, Unity {player.unity_version}, game version {game_version})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
