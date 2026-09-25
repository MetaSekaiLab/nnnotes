"""Player-wide configuration from the game's boot data in a user-supplied APK.

`assets/bin/Data/data.unity3d` holds the global managers (graphics, quality,
player settings) and `globalgamemanagers.assets` with the URP pipeline assets,
renderer data, renderer features and post-process data. IL2CPP player builds
strip script typetrees there, so MonoBehaviours are read with typetrees
generated from the game's managed assembly stubs (an Il2CppDumper `DummyDll`
folder produced from the same APK). `unity default resources` (built-in meshes)
is read from the APK as well.
"""
from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from .unity import DEFAULT_RESOURCES, deref, external_path, is_pptr

DATA_IN_APK = "assets/bin/Data/data.unity3d"
DEFAULT_RESOURCES_IN_APK = "assets/bin/Data/Resources/unity default resources"
COLOR_SPACE = {0: "Gamma", 1: "Linear"}
HEADER = ("m_GameObject", "m_Script", "m_Enabled")


class PlayerData:
    def __init__(self, apk: Path, dummy_dll: Path):
        with zipfile.ZipFile(apk) as z:
            self.env = UnityPy.load(io.BytesIO(z.read(DATA_IN_APK)))
            self.defaults = UnityPy.load(io.BytesIO(z.read(DEFAULT_RESOURCES_IN_APK)))
        self._by_type: dict[str, list] = {}
        for o in self.env.objects:
            self._by_type.setdefault(o.type.name, []).append(o)
        version = self._by_type["GraphicsSettings"][0].assets_file.unity_version
        self.gen = TypeTreeGenerator(version)
        self.gen.load_local_dll_folder(str(dummy_dll))

    # --- object access ---------------------------------------------------
    def one(self, type_name: str):
        objs = self._by_type.get(type_name, [])
        if len(objs) != 1:
            raise RuntimeError(f"{type_name}: {len(objs)} objects")
        return objs[0]

    def resource(self, path: str):
        """The object `Resources.Load(path)` returns (ResourceManager container, case-insensitive)."""
        rm = self.one("ResourceManager")
        hits = [pp for p, pp in rm.read_typetree()["m_Container"] if p.lower() == path.lower()]
        if len(hits) != 1:
            raise KeyError(f"Resources path {path}: {len(hits)} entries")
        o = self.deref(rm, hits[0])
        if o is None:
            raise KeyError(f"Resources path {path}: null reference")
        return o

    def shader(self, name: str, file: str | None = None):
        """The Shader object named `name` (parsed-form name, as Shader.Find sees it); `file` (serialized file
        name, e.g. "unity_builtin_extra") narrows the search. Exactly one must match."""
        hits = [o for o in self._by_type.get("Shader", [])
                if o.read().m_ParsedForm.m_Name == name and (file is None or o.assets_file.name == file)]
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
        """(assembly, namespace, class) of a MonoBehaviour from its raw m_Script PPtr."""
        fid, pid = struct.unpack_from("<iq", o.get_raw_data(), 16)
        ms = self.deref(o, {"m_FileID": fid, "m_PathID": pid}).read()
        return ms.m_AssemblyName, ms.m_Namespace, ms.m_ClassName

    def mono(self, o) -> dict:
        asm, ns, cls = self.script(o)
        tt = o.read_typetree(self.gen.get_nodes_up(asm, f"{ns}.{cls}" if ns else cls))
        return {k: v for k, v in tt.items() if k not in HEADER}

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
