"""Player-wide configuration from the game's boot data in a user-supplied APK.

`assets/bin/Data/data.unity3d` holds the global managers (graphics, quality,
player settings) and `globalgamemanagers.assets` with the URP pipeline assets,
renderer data, renderer features and post-process data. IL2CPP player builds
strip script typetrees there, so MonoBehaviours are read with typetrees that
ship with nnnotes: `typetrees/<Unity version>.json`, generated from the game's
managed assembly stubs by `scripts/gen_typetrees.py`. Each entry records the
type hash the serialized file stores for its class; a class without an entry,
a different type hash or a read that does not consume exactly the object's
serialized size raises UnsupportedVersion. `unity default resources`
(built-in meshes) is read from the APK as well.
"""
from __future__ import annotations

import copy
import io
import json
import re
import struct
import zipfile
from importlib import resources
from pathlib import Path

import UnityPy
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from .config import ConfigError
from .unity import DEFAULT_RESOURCES, deref, external_path, is_pptr

DATA_IN_APK = "assets/bin/Data/data.unity3d"
DEFAULT_RESOURCES_IN_APK = "assets/bin/Data/Resources/unity default resources"
MANIFEST_IN_APK = "AndroidManifest.xml"
COLOR_SPACE = {0: "Gamma", 1: "Linear"}
HEADER = ("m_GameObject", "m_Script", "m_Enabled")
TYPETREE_FORMAT = 1


class UnsupportedVersion(ConfigError):
    """The APK's boot data has a MonoBehaviour class that nnnotes has no matching typetree for."""


# ---------------------------------------------------------------- embedded typetrees
def typetree_key(assembly: str, namespace: str, cls: str) -> str:
    """Key of a class in a typetree file: `assembly|namespace|class`, as its MonoScript names them."""
    return f"{assembly}|{namespace}|{cls}"


def typetree_file(unity_version: str):
    """The embedded typetree file of a Unity version (an importlib.resources Traversable; it may not exist)."""
    return resources.files(__package__).joinpath("typetrees", f"{unity_version}.json")


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


def typetree_node(rows: list) -> TypeTreeNode:
    """TypeTreeNode from [level, type, name, meta flag] rows (byte size and version 0, as generated)."""
    return TypeTreeNode.from_list([TypeTreeNode(lv, ty, nm, 0, 0, m_MetaFlag=mf) for lv, ty, nm, mf in rows])


def dump_typetrees(doc: dict) -> str:
    """Text of a typetree file: sorted keys, one class per line, LF."""
    head = {k: v for k, v in doc.items() if k != "classes"}
    lines = [json.dumps(head, sort_keys=True, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"classes":{']
    items = sorted(doc["classes"].items())
    for i, (k, v) in enumerate(items):
        lines.append(json.dumps(k, ensure_ascii=False) + ":"
                     + json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                     + ("," if i + 1 < len(items) else ""))
    lines.append("}}")
    return "\n".join(lines) + "\n"


def manifest_version_name(data: bytes) -> str | None:
    """`android:versionName` of a binary AndroidManifest.xml (None when absent or unreadable)."""
    try:
        pos, strings = 8, []
        while pos + 8 <= len(data):
            typ, hsize, size = struct.unpack_from("<HHI", data, pos)
            if typ == 0x0001:                                   # string pool
                count, _styles, flags, start = struct.unpack_from("<IIII", data, pos + 8)
                for off in struct.unpack_from(f"<{count}I", data, pos + hsize):
                    q = pos + start + off
                    if flags & 0x100:                           # UTF-8: UTF-16 length, UTF-8 length, bytes
                        q += 2 if data[q] & 0x80 else 1
                        n = data[q]
                        q += 1
                        if n & 0x80:
                            n = ((n & 0x7F) << 8) | data[q]
                            q += 1
                        strings.append(data[q:q + n].decode("utf-8"))
                    else:                                       # UTF-16LE: length in code units, chars
                        n, = struct.unpack_from("<H", data, q)
                        q += 2
                        if n & 0x8000:
                            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, q)[0]
                            q += 2
                        strings.append(data[q:q + 2 * n].decode("utf-16-le"))
            elif typ == 0x0102:                                 # start element
                _ns, name, astart, asize, acount = struct.unpack_from("<IIHHH", data, pos + 16)
                if strings[name] == "manifest":
                    for i in range(acount):
                        _ans, aname, raw = struct.unpack_from("<III", data, pos + 16 + astart + i * asize)
                        if strings[aname] == "versionName" and raw != 0xFFFFFFFF:
                            return strings[raw]
                    return None
            if size < 8:
                return None
            pos += size
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    return None


class PlayerData:
    def __init__(self, apk: Path):
        with zipfile.ZipFile(apk) as z:
            self.env = UnityPy.load(io.BytesIO(z.read(DATA_IN_APK)))
            self.defaults = UnityPy.load(io.BytesIO(z.read(DEFAULT_RESOURCES_IN_APK)))
            self.game_version = (manifest_version_name(z.read(MANIFEST_IN_APK))
                                 if MANIFEST_IN_APK in z.namelist() else None)
        self._by_type: dict[str, list] = {}
        for o in self.env.objects:
            self._by_type.setdefault(o.type.name, []).append(o)
        self.unity_version = self._by_type["GraphicsSettings"][0].assets_file.unity_version
        self.typetrees = load_typetrees(self.unity_version)
        self._nodes: dict[str, tuple[TypeTreeNode, str]] = {}
        self._clear_memos()

    def _clear_memos(self) -> None:
        """Empty the memos of script, mono, shader, resource and graphics (an object is kept with its entry, so its
        id is not reused while the entry exists; the objects belong to `env` / `defaults` anyway)."""
        self._scripts: dict[int, tuple] = {}          # id(object) -> (object, (assembly, namespace, class))
        self._monos: dict[int, tuple] = {}            # id(object) -> (object, fields)
        self._shader_names: dict[str, list] | None = None
        self._container: dict[str, list] | None = None
        self._graphics: dict | None = None

    # --- object access ---------------------------------------------------
    def one(self, type_name: str):
        objs = self._by_type.get(type_name, [])
        if len(objs) != 1:
            raise RuntimeError(f"{type_name}: {len(objs)} objects")
        return objs[0]

    def resource(self, path: str):
        """The object `Resources.Load(path)` returns (ResourceManager container, case-insensitive)."""
        rm = self.one("ResourceManager")
        if self._container is None:                   # lower-case path -> PPtrs, in container order
            self._container = {}
            for p, pp in rm.read_typetree()["m_Container"]:
                self._container.setdefault(p.lower(), []).append(pp)
        hits = self._container.get(path.lower(), [])
        if len(hits) != 1:
            raise KeyError(f"Resources path {path}: {len(hits)} entries")
        o = self.deref(rm, hits[0])
        if o is None:
            raise KeyError(f"Resources path {path}: null reference")
        return o

    def shader(self, name: str, file: str | None = None):
        """The Shader object named `name` (parsed-form name, as Shader.Find sees it); `file` (serialized file
        name, e.g. "unity_builtin_extra") narrows the search. Exactly one must match."""
        if self._shader_names is None:                # parsed-form name -> Shader objects, in object order
            self._shader_names = {}
            for o in self._by_type.get("Shader", []):
                self._shader_names.setdefault(o.read().m_ParsedForm.m_Name, []).append(o)
        hits = [o for o in self._shader_names.get(name, []) if file is None or o.assets_file.name == file]
        if len(hits) != 1:
            raise KeyError(f"shader {name!r}{f' in {file}' if file else ''}: {len(hits)} objects")
        return hits[0]

    def deref(self, owner, pptr: dict):
        if not pptr["m_PathID"]:
            return None
        if external_path(owner, pptr) == DEFAULT_RESOURCES:
            return self.defaults.files[next(iter(self.defaults.files))].objects[pptr["m_PathID"]]
        return deref(owner, pptr)

    def script(self, o) -> tuple[str, str, str]:
        """(assembly, namespace, class) of a MonoBehaviour from its raw m_Script PPtr (read once per object)."""
        hit = self._scripts.get(id(o))
        if hit is not None and hit[0] is o:
            return hit[1]
        fid, pid = struct.unpack_from("<iq", o.get_raw_data(), 16)
        ms = self.deref(o, {"m_FileID": fid, "m_PathID": pid}).read()
        r = ms.m_AssemblyName, ms.m_Namespace, ms.m_ClassName
        self._scripts[id(o)] = (o, r)
        return r

    def mono(self, o) -> dict:
        """A MonoBehaviour's fields (header removed), read with the embedded typetree of its class; read once per
        object, each call returns its own deep copy (callers may change what they get)."""
        hit = self._monos.get(id(o))
        if hit is None or hit[0] is not o:
            hit = self._monos[id(o)] = (o, self._read_mono(o))
        return copy.deepcopy(hit[1])

    def _read_mono(self, o) -> dict:
        """A MonoBehaviour's fields (header removed), read with the embedded typetree of its class."""
        asm, ns, cls = self.script(o)
        key = typetree_key(asm, ns, cls)
        if key not in self._nodes:
            entry = (self.typetrees or {}).get("classes", {}).get(key)
            if entry is None:
                raise self.unsupported(asm, ns, cls, "no embedded typetree for this class" if self.typetrees
                                       else f"no embedded typetrees for Unity {self.unity_version}")
            self._nodes[key] = typetree_node(entry["nodes"]), entry["typeHash"]
        node, type_hash = self._nodes[key]
        st = o.serialized_type
        if st is not None and st.old_type_hash.hex() != type_hash:
            raise self.unsupported(asm, ns, cls, "its serialized type hash differs from the embedded typetree's")
        try:
            tt = o.read_typetree(node, check_read=True)
        except Exception as e:
            raise self.unsupported(asm, ns, cls, f"the embedded typetree does not fit the serialized data ({e})"
                                   ) from e
        return {k: v for k, v in tt.items() if k not in HEADER}

    def unsupported(self, asm: str, ns: str, cls: str, why: str) -> UnsupportedVersion:
        made = f" (embedded typetrees from game version {self.typetrees['gameVersion']})" if self.typetrees else ""
        return UnsupportedVersion(
            f"MonoBehaviour {ns + '.' if ns else ''}{cls} ({asm}): {why}{made}. "
            f"Game version {self.game_version or 'unknown'} (Unity {self.unity_version}) is not supported yet.")

    def name_of(self, owner, pptr: dict) -> str | None:
        """`m_Name` of a referenced object (shader names come from the parsed form)."""
        o = self.deref(owner, pptr)
        if o is None:
            return None
        if o.type.name == "Shader":
            return o.read().m_ParsedForm.m_Name
        if o.type.name == "MonoBehaviour":
            return self.mono(o).get("m_Name")
        return o.read().m_Name

    def names_in(self, owner, tt):
        """Typetree with every PPtr replaced by the referenced object's name."""
        if is_pptr(tt):
            return self.name_of(owner, tt)
        if isinstance(tt, dict):
            return {k: self.names_in(owner, v) for k, v in tt.items()}
        if isinstance(tt, list):
            return [self.names_in(owner, v) for v in tt]
        return tt

    # --- settings --------------------------------------------------------
    def color_space(self) -> str:
        """PlayerSettings.m_ActiveColorSpace.

        The built-in PlayerSettings typetree diverges from this build's layout
        after the colour space field, so only the prefix up to it is read.
        """
        o = self.one("PlayerSettings")
        node = o._get_typetree_node()
        names = [c.m_Name for c in node.m_Children]
        i = names.index("m_ActiveColorSpace")
        prefix = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize,
                              node.m_Version, m_MetaFlag=node.m_MetaFlag,
                              m_Children=node.m_Children[:i + 1])
        return COLOR_SPACE[o.read_typetree(prefix, check_read=False)["m_ActiveColorSpace"]]

    def graphics(self) -> dict:
        """The render settings tree (_read_graphics), built once; each call returns its own deep copy."""
        if self._graphics is None:
            self._graphics = self._read_graphics()
        return copy.deepcopy(self._graphics)

    def _read_graphics(self) -> dict:
        gs_o, qs_o = self.one("GraphicsSettings"), self.one("QualitySettings")
        gs, qs = gs_o.read_typetree(), qs_o.read_typetree()
        pipelines, renderers, post = {}, {}, {}

        def pipeline(owner, pptr) -> str:
            o = self.deref(owner, pptr)
            tt = self.mono(o)
            name = tt["m_Name"]
            if name not in pipelines:
                tt["m_RendererDataList"] = [renderer(o, p) for p in tt["m_RendererDataList"]]
                pipelines[name] = self.names_in(o, tt)
            return name

        def renderer(owner, pptr) -> str:
            o = self.deref(owner, pptr)
            tt = self.mono(o)
            name = tt["m_Name"]
            if name not in renderers:
                feats = []
                for fp in tt["m_RendererFeatures"]:
                    fo = self.deref(o, fp)
                    asm, ns, cls = self.script(fo)
                    feats.append({"class": f"{ns}.{cls}" if ns else cls, "assembly": asm,
                                  **self.names_in(fo, self.mono(fo))})
                tt["m_RendererFeatures"] = feats
                pp = tt.get("postProcessData")
                if pp and pp["m_PathID"]:
                    po = self.deref(o, pp)
                    pt = self.mono(po)
                    post.setdefault(pt["m_Name"], self.names_in(po, pt))
                    tt["postProcessData"] = pt["m_Name"]
                renderers[name] = self.names_in(o, tt)
            return name

        levels = []
        for q in qs["m_QualitySettings"]:
            q = dict(q)
            q["customRenderPipeline"] = pipeline(qs_o, q["customRenderPipeline"])
            levels.append(q)
        return {
            "colorSpace": self.color_space(),
            "defaultPipeline": pipeline(gs_o, gs["m_CustomRenderPipeline"]),
            "qualityLevels": levels,
            "qualityPerPlatform": qs.get("m_PerPlatformDefaultQuality"),
            "pipelines": pipelines,
            "renderers": renderers,
            "postProcessData": post,
        }

    def renderer_shaders(self, renderer_name: str) -> list:
        """Shader objects referenced by a renderer's features and its post-process data."""
        out = []
        for o in self._by_type.get("MonoBehaviour", []):
            if self.script(o)[2] != "UniversalRendererData":
                continue
            tt = self.mono(o)
            if tt["m_Name"] != renderer_name:
                continue
            owners = [(self.deref(o, fp)) for fp in tt["m_RendererFeatures"]]
            if tt["postProcessData"]["m_PathID"]:
                owners.append(self.deref(o, tt["postProcessData"]))
            for fo in owners:
                self._collect_shaders(fo, self.mono(fo), out)
        return out

    def post_textures(self, renderer_name: str, group: str) -> list:
        """Texture objects of a renderer's post-process data `textures.<group>` list (index order)."""
        for o in self._by_type.get("MonoBehaviour", []):
            if self.script(o)[2] != "UniversalRendererData":
                continue
            tt = self.mono(o)
            if tt["m_Name"] != renderer_name:
                continue
            po = self.deref(o, tt["postProcessData"])
            return [self.deref(po, p) for p in self.mono(po)["textures"][group]]
        raise KeyError(f"renderer {renderer_name} not in player data")

    def _collect_shaders(self, owner, tt, out: list):
        if is_pptr(tt):
            o = self.deref(owner, tt)
            if o is not None and o.type.name == "Shader" and o not in out:
                out.append(o)
        elif isinstance(tt, dict):
            for v in tt.values():
                self._collect_shaders(owner, v, out)
        elif isinstance(tt, list):
            for v in tt:
                self._collect_shaders(owner, v, out)
