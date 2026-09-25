"""ADV episode assets beyond the scene: frames, particle effects, post-effect profiles, stills, chat and talk windows.

One JSON file per kind at the story root, written only when the episode's resource closure (adv.closure) has that
kind; textures go to the story's `textures/` (PNG, named like the scene's) and every Shader object of the loaded
bundle closures is handed back to advscene, which dumps them with the scene's shaders into `shaders/`:

  frames.json       Frame: "Adv/Frame/" + TargetAssetName (UIAdvWidget.SetFrame / AdvFrame), keyed by
                    TargetAssetName; each a prefab node list (AdvFrame, CanvasGroup, uGUI, Animator with its
                    controller and clips, UIParticle / ParticleSystem). Frame texts (TargetTextIDs) are episode
                    text ids (episode.json `text`).
  effects.json      Effect: "Adv/Effect/" + TargetAssetName, instantiated once per TargetName
                    (AdvEpisodeResourceLoader.LoadParticleEffect): `effects` by TargetAssetName (AdvParticleEffect,
                    ParticleSystem modules and renderers, Animator), `instances` TargetName -> TargetAssetName
  posteffects.json  PostEffect: "Adv/PostEffect/" + TargetAssetName, a URP VolumeProfile (not instantiated;
                    AdvGlobalVolume.ToggleVolume adds it as a child volume), keyed by TargetAssetName
  stills.json       Still: "Adv/Still/" + TargetAssetName (AdvStill, CanvasGroup, uGUI, DOTweenSequence
                    animations), keyed by TargetAssetName
  chat.json         ChatWindow / ChatTalk / ChatStamp: `chats` (the MasterAdvChat rows of the episode's chat ids),
                    `windows` ("Adv/Chat/Prefabs/" + _chatWindowAssetName, AdvChatWindow prefabs), `icons`
                    ("Adv/Chat/Icon/" + _chatIconAssetName) and `stamps` ("Adv/Chat/Stamp/" + TargetAssetName) as
                    sprites, `text` / `sounds` / `cueSheets`: the rows of the shared chat shards
                    (Adv/Chat/AdvChat-Text, -Sound, -SoundCueSheet)
  talkwindows.json  TalkWindow: "UI/Prefab/Parts/Adv/Talk/" + TargetAssetName (embedded content), keyed by window
                    name (the default window is also in ui/ui.json in its drawn form)

Prefabs are exported with export.Exporter (references resolved, AnimatorControllers followed, clips in the
generic format). TextMeshPro text components always keep their layout and style (size, alignment, colours,
margins, text). The font data follows `fonts`: "open" (default) exports none of it -- font / sprite / style sheet
assets become {asset, name} and TextMeshPro materials {material, shader}, each with a note, so a viewer draws the
text with a font of its own; "game" exports the font assets and their materials with the atlas textures like any
other asset. A MonoBehaviour whose script is missing (Unity runs nothing for it) is recorded as such.
"""
from __future__ import annotations

from pathlib import Path

from .catalog import Catalog
from .export import HEADER, Exporter
from .jsonio import write_json
from .player import PlayerData
from . import adv, unity

TMP_SHADER_PREFIX = "TextMeshPro/"
TMP_ASSETS = ("TMP_FontAsset", "TMP_SpriteAsset", "TMP_StyleSheet")
FONT_CHOICES = ("open", "game")
FONT_NOTE = "font data not exported (fonts: open): draw the text with a font of your own"
CHAT_SHARDS = {"text": "Adv/Chat/AdvChat-Text", "sounds": "Adv/Chat/AdvChat-Sound",
               "cueSheets": "Adv/Chat/AdvChat-SoundCueSheet"}
# resource kind -> (story.json key = file key, file, address prefix removed from the entry keys; None: last segment)
FILES = {
    "frame": ("frames", "frames.json", adv.RESOURCE_PREFIX["Frame"][1]),
    "effect": ("effects", "effects.json", adv.RESOURCE_PREFIX["Effect"][1]),
    "posteffect": ("postEffects", "posteffects.json", adv.RESOURCE_PREFIX["PostEffect"][1]),
    "still": ("stills", "stills.json", adv.RESOURCE_PREFIX["Still"][1]),
    "talkwindow": ("talkWindows", "talkwindows.json", None),
}
CHAT_KINDS = ("chatwindow", "chaticon", "chatstamp")
CHAT = ("chat", "chat.json")
KINDS = tuple(FILES) + CHAT_KINDS
INDEX_KEYS = tuple(k for k, _, _ in FILES.values()) + (CHAT[0],)


class MediaExporter(Exporter):
    """export.Exporter for uGUI and particle prefabs: AnimatorControllers followed, missing-script MonoBehaviours
    recorded; with fonts="open" the TextMeshPro font / sprite / style sheet assets and materials as name stubs."""

    def __init__(self, cat: Catalog, out_dir: Path, player: PlayerData, fonts: str = "open"):
        if fonts not in FONT_CHOICES:
            raise ValueError(f"fonts={fonts!r}")
        super().__init__(cat, out_dir, player=player, stub_assets=TMP_ASSETS if fonts == "open" else (),
                         follow=("AnimatorController",))
        self.fonts = fonts

    def scriptable(self, o) -> dict:
        out = super().scriptable(o)
        if self.fonts == "open" and out.get("asset") in TMP_ASSETS:
            return {**out, "note": FONT_NOTE}
        return out

    def material(self, o) -> dict:
        if self.fonts == "game":
            return super().material(o)
        tt = o.read_typetree()
        shader = self.deref(o, tt["m_Shader"])
        name = self.add_shader(shader) if shader is not None else None
        if name is None or not name.startswith(TMP_SHADER_PREFIX):
            return super().material(o)
        return {"material": tt["m_Name"], "shader": {"shader": name}, "note": FONT_NOTE}

    def component(self, o) -> dict:
        if o.type.name == "MonoBehaviour":
            tt = o.read_typetree()
            if not tt["m_Script"]["m_PathID"]:
                return {"type": "MonoBehaviour", "class": None, "missingScript": True,
                        **self.value(o, {k: v for k, v in tt.items() if k not in HEADER})}
        return super().component(o)


def _suffix(address: str, prefix: str | None) -> str:
    if prefix is None:
        return address.rsplit("/", 1)[-1]
    if not address.startswith(prefix):
        raise ValueError(f"{address}: not under {prefix}")
    return address[len(prefix):]


def effect_instances(commands: list[dict]) -> dict[str, str]:
    """TargetName -> TargetAssetName of the effect instances (LoadParticleEffect: the first asset loaded under a
    TargetName is the instance every later Effect row with that TargetName drives)."""
    out: dict[str, str] = {}
    for c in commands:
        if c["cmd"] == "Effect" and not c.get("IgnoreData") and adv._name(c.get("TargetName")) \
                and adv._name(c.get("TargetAssetName")):
            out.setdefault(c["TargetName"], c["TargetAssetName"])
    return out


def _chat(cat: Catalog, master: Path, ex: Exporter, episode: dict, resources: list[dict]) -> dict:
    rows = {r["_id"]: r for r in adv._master_table(master, "MasterAdvChat")}
    ids = sorted({c["TargetChatID"] for c in episode["commands"] if c.get("TargetChatID")})
    doc: dict = {"chats": {str(i): rows[i] for i in ids if i in rows},
                 "windows": {}, "icons": {}, "stamps": {}}
    for r in resources:
        if r["kind"] == "chatwindow":
            doc["windows"][_suffix(r["address"], adv.CHAT_WINDOW_PREFIX)] = ex.export_key(r["address"])
        elif r["kind"] == "chaticon":
            doc["icons"][_suffix(r["address"], adv.CHAT_ICON_PREFIX)] = ex.key_sprite(r["address"])
        elif r["kind"] == "chatstamp":
            doc["stamps"][_suffix(r["address"], adv.RESOURCE_PREFIX["ChatStamp"][1])] = ex.key_sprite(r["address"])
    for name, key in CHAT_SHARDS.items():
        t = adv._text_of(cat, key)
        doc[name] = unity.parse_shard(t)["rows"] if t else []
    return doc


def extract(cat: Catalog, player: PlayerData, master: Path, episode: dict, out_dir: Path,
            fonts: str = "open") -> dict:
    """Write the kind files the episode needs (`fonts`: see the module docstring). Returns {"files": {index key: file or None} for every INDEX_KEYS
    entry, "shaders": {name: Shader object}, "counts": {index key: entries}, "textures": textures written}."""
    out_dir = Path(out_dir)
    by_kind: dict[str, list[dict]] = {}
    for r in episode["resources"]:
        if r["kind"] in KINDS:
            by_kind.setdefault(r["kind"], []).append(r)
    files: dict[str, str | None] = dict.fromkeys(INDEX_KEYS)
    shaders: dict = {}
    counts: dict[str, int] = {}
    textures = 0

    def done(ex: Exporter, name: str, doc: dict) -> None:
        nonlocal textures
        ex.resolve_pending()
        write_json(out_dir / name, doc)
        for k, o in ex.shaders.items():
            shaders.setdefault(k, o)
        textures += len(ex.textures)

    for kind, (key, name, prefix) in FILES.items():
        if kind not in by_kind:
            continue
        ex = MediaExporter(cat, out_dir, player, fonts)
        entries = {_suffix(r["address"], prefix): ex.export_key(r["address"]) for r in by_kind[kind]}
        doc: dict = {key: entries}
        if kind == "effect":
            doc["instances"] = effect_instances(episode["commands"])
        done(ex, name, doc)
        files[key] = name
        counts[key] = len(entries)
    chat = [r for k in CHAT_KINDS for r in by_kind.get(k, [])]
    if chat:
        ex = MediaExporter(cat, out_dir, player, fonts)
        done(ex, CHAT[1], _chat(cat, master, ex, episode, chat))
        files[CHAT[0]] = CHAT[1]
        counts[CHAT[0]] = len(chat)
    return {"files": files, "shaders": shaders, "counts": counts, "textures": textures}
