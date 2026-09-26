"""Live options that select files: which option variants a live directory carries files for.

The game reads its Live options once, when a live boots (LiveDataCreator.CreateBootData, LiveBootDataCreator
.CreateViewData, LiveSettingCreator.CreateLiveNoteSettings / CreateSESettings); a fresh profile uses option preset 1
(MasterOptionDefault rows of `_presetId` 1). A live directory holds the files those defaults read. Most options only
change values the player computes from the data; the options below also change which files a live reads, so a
viewer can offer them only when the live directory carries those files as well:

  MirrorChart       the score converted mirrored (SsMusicScoreConverter.Load with isMirror):
                    score/<chart>.mirror.notes.json, live.json `notesMirror`
  NoteDesignId      note skins (MasterLiveNoteSkin): livenotes settings.skins, noteSkins
  NoteEffectId      note effect sets (MasterLiveNoteEffectSkin): livenotes settings.effects, their assets (the lane
                    effect set is always effect001, LaneEffectLoadStep.GetAssetPath)
  LiveQuality       qualities (MasterLiveQualitySettings); Low (2) loads a note effect set's `<name>Light` variant
                    where it exists (NoteSkinLoadStep.GetLightQualityAddress); the chart manifest's
                    `options.LiveQuality`
  NoteSePatternId   note sound sets (MasterLiveNoteSe groups): audio/live-audio.json noteSe.groups and their sounds
  MeasureLineDisplay  the bar lines (LiveAllBarLineView): the bar line view the live scene's LiveBarLineViewContainer
                    instantiates (its _elementPrefab), livenotes prefabs.bar_line_view, and its sprite's texture

and the option tables: the preset-1 default and the range of every option item the player offers
(livenotes settings.optionDefaults / optionRanges), implied by any of the above.

A request names options as the game does (the names the player's settings use), from command-line specs:
`MirrorChart`, `NoteDesignId` (every value the master data has) or `NoteDesignId=1,3` (those values), ...,
`defaults` (the option tables only) and `all` (every option with every value). `resolve` checks a request against
the master data and gives a LiveOptions; each offered option lists its values including the preset-1 default. An
empty LiveOptions is the default output, byte for byte.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from pathlib import Path

from .score import master_table

# App.Options.OptionItemType (every value but None 0), the names of MasterOptionDefault / MasterOptionRange
# `_optionItemType`
OPTION_ITEM_TYPES = {
    1: "NoteSpeed", 2: "NoteTiming", 3: "ChartPosition", 4: "MirrorChart", 5: "AssistMode", 6: "LiveQuality",
    7: "Vibration",
    100: "FastSlowDisplay", 101: "PerfectFastSlowDisplay", 102: "JudgeOffsetMsDisplay", 103: "JudgeResultPositionType",
    104: "JudgePosition", 105: "JudgePositionDisplay", 106: "SlideOpacity", 107: "GuideOpacity",
    108: "SimultaneousLineDisplay", 109: "MeasureLineDisplay", 110: "MvQuality", 111: "MvDataRetention",
    112: "LiveSkillEffect", 113: "ComboEffect", 114: "DamageEffect", 115: "StageEffect", 116: "FcAcChallengeAssist",
    117: "PreLiveSimpleOption", 118: "FrameRate", 119: "GekisouEffect",
    200: "ScreenMode", 201: "BackgroundBrightness", 202: "MvModeBrightness", 203: "BackgroundSwitch",
    205: "SkillEffectDisplay", 206: "ComboCountDisplay", 207: "JudgeDetailDisplay", 208: "ContinuationEffectDisplay",
    300: "LaneOpacity", 301: "GuidelineOpacity", 302: "GuidelineCount", 303: "GekisouDisplay",
    304: "GekisouDisplayPositionChange", 305: "LiveSkinId", 306: "NoteDesignId", 307: "NoteEffectId",
    308: "NoteStartPosition", 309: "LiveSkillActivationPositionDisplay", 310: "GekisouSimpleEffect",
    400: "SystemBgmVolume", 401: "SystemSeVolume", 402: "SystemVoiceVolume", 403: "SystemBgmMute",
    404: "SystemSeMute", 405: "SystemVoiceMute",
    410: "LiveMusicVolume", 411: "LiveNoteSeVolume", 412: "LiveSeVolume", 413: "LiveVoiceVolume",
    414: "LiveMusicMute", 415: "LiveNoteSeMute", 416: "LiveSeMute", 417: "LiveVoiceMute",
    420: "NoteSePatternId", 421: "UseIndividualNoteSe",
    430: "EmptyTapSeId", 431: "EmptyTapSeVolume", 432: "TapSeId", 433: "TapSeVolume", 434: "FlickSeId",
    435: "FlickSeVolume", 436: "SideFlickSeId", 437: "SideFlickSeVolume", 438: "SlideSeId", 439: "SlideSeVolume",
    440: "TraceSeId", 441: "TraceSeVolume",
    450: "GekisouTapSeId", 451: "GekisouTapSeVolume", 452: "GekisouFlickSeId", 453: "GekisouFlickSeVolume",
    454: "GekisouSideFlickSeId", 455: "GekisouSideFlickSeVolume", 456: "GekisouSlideSeId",
    457: "GekisouSlideSeVolume", 458: "GekisouTraceSeId", 459: "GekisouTraceSeVolume",
    460: "EmptyTapSeMute", 461: "TapSeMute", 462: "FlickSeMute", 463: "SideFlickSeMute", 464: "SlideSeMute",
    465: "TraceSeMute", 466: "GekisouTapSeMute", 467: "GekisouFlickSeMute", 468: "GekisouSideFlickSeMute",
    469: "GekisouSlideSeMute", 470: "GekisouTraceSeMute",
    500: "BluetoothNoteTiming", 501: "BluetoothNoteChartPosition",
    510: "BluetoothSystemBgmVolume", 511: "BluetoothSystemSeVolume", 512: "BluetoothSystemVoiceVolume",
    513: "BluetoothSystemBgmMute", 514: "BluetoothSystemSeMute", 515: "BluetoothSystemVoiceMute",
    520: "BluetoothLiveMusicVolume", 521: "BluetoothLiveNoteSeVolume", 522: "BluetoothLiveSeVolume",
    523: "BluetoothLiveVoiceVolume", 524: "BluetoothLiveMusicMute", 525: "BluetoothLiveNoteSeMute",
    526: "BluetoothLiveSeMute", 527: "BluetoothLiveVoiceMute",
    600: "QualitySetting", 601: "PurchaseAlert", 602: "LiveBoostFullRecoveryNotification",
    603: "LateNightNotification", 604: "OfflineBonusReachedMaxRewardsNotification",
    999: "SongTitleDisplay",
}
# The items the player offers beyond those the note runtime reads (livenotes.OPTION_ITEMS): the option tables add
# their defaults and ranges. NoteTiming; AssistMode, Vibration, FcAcChallengeAssist, FrameRate (accepted without an
# effect or at the default); the live volumes and mutes; the note sound set, the individual switch and the per-type
# sound ids, volumes and mutes (empty tap, tap, flick, side flick, slide, trace).
PLAYER_ITEMS = (2, 5, 7, 116, 118, *range(410, 418), 420, 421, *range(430, 442), *range(460, 466))

# option name -> (OptionItemType, master table and column of its values); MirrorChart is a switch
ENUM_OPTIONS = {
    "NoteDesignId": (306, "MasterLiveNoteSkin", "_id"),
    "NoteEffectId": (307, "MasterLiveNoteEffectSkin", "_id"),
    "LiveQuality": (6, "MasterLiveQualitySettings", "_quality"),
    "NoteSePatternId": (420, "MasterLiveNoteSe", "_groupID"),
}
SWITCH_OPTIONS = {"MirrorChart": 4, "MeasureLineDisplay": 109}
OPTION_NAMES = (*SWITCH_OPTIONS, *ENUM_OPTIONS)
TABLES = "defaults"           # request item: the option tables only
ALL = "all"                   # request item: every option with every value (and the tables)
FIELDS = {"MirrorChart": "mirror", "NoteDesignId": "designs", "NoteEffectId": "effects", "LiveQuality": "qualities",
          "NoteSePatternId": "se_patterns", "MeasureLineDisplay": "bar_lines"}
# options whose files are pictures (read-set variants combine them); the note sounds do not depend on them
VISUAL_OPTIONS = ("MirrorChart", "NoteDesignId", "NoteEffectId", "LiveQuality")
LOW_QUALITY = 2               # LiveQuality Low: the `<name>Light` effect sets (NoteSkinLoadStep.GetLightQualityAddress)
LIGHT_SUFFIX = "Light"


class OptionSpecError(ValueError):
    """A malformed --live-option spec, or a value the master data does not have."""


def parse_specs(specs) -> dict:
    """Command-line specs -> a request (JSON-able): {option name: "all" | [values] | True, "defaults": True}.
    `Name` offers every value of an option (a switch: both), `Name=v,v` those values, `defaults` the option tables,
    `all` every option. Repeated names merge."""
    req: dict = {}
    for spec in specs or ():
        s = spec.strip()
        name, eq, vals = s.partition("=")
        name = name.strip()
        if name in (TABLES, ALL):
            if eq:
                raise OptionSpecError(f"--live-option {name} takes no values")
            req[name] = True
            continue
        if name in SWITCH_OPTIONS:
            if eq:
                raise OptionSpecError(f"--live-option {name} is a switch: give it without values")
            req[name] = True
            continue
        if name not in ENUM_OPTIONS:
            raise OptionSpecError(f"--live-option {name!r}: one of {', '.join((*OPTION_NAMES, TABLES, ALL))}")
        if not eq:
            req[name] = "all"
            continue
        try:
            values = sorted({int(v) for v in vals.split(",") if v.strip()})
        except ValueError:
            raise OptionSpecError(f"--live-option {s}: values are integers, comma-separated") from None
        if not values:
            raise OptionSpecError(f"--live-option {s}: no values")
        old = req.get(name)
        if old != "all":
            req[name] = sorted(set(values) | set(old or ()))
    return req


@dataclass(frozen=True)
class LiveOptions:
    """The file-selecting options a live directory carries files for. Each enum option: the offered values in
    ascending order, including the preset-1 default; () when only the default is offered. Each switch: offered
    (both values) or not. `tables`: the option tables (the default and range of every item the player offers)."""
    mirror: bool = False
    bar_lines: bool = False
    designs: tuple = ()
    effects: tuple = ()
    qualities: tuple = ()
    se_patterns: tuple = ()
    tables: bool = False
    defaults: tuple = ()          # ((option name, preset-1 value), ...) of the enum options, from the master data

    def __bool__(self) -> bool:
        return bool(self.tables or self.mirror or self.bar_lines or self.designs or self.effects or self.qualities
                    or self.se_patterns)

    def default(self, name: str) -> int:
        return dict(self.defaults)[name]

    def values(self, name: str) -> list:
        """The values of option `name` the files support (the default alone when it is not offered)."""
        if name in SWITCH_OPTIONS:
            return [False, True] if getattr(self, FIELDS[name]) else [False]
        return list(getattr(self, FIELDS[name]) or (self.default(name),))

    def manifest_options(self) -> dict | None:
        """The chart manifest's `options`: the values of the options the player takes from the manifest
        (LiveQuality; the other options are offered by the data itself), None when only the default is offered."""
        return {"LiveQuality": list(self.qualities)} if self.qualities else None

    def effect_sets(self, names: dict) -> list[tuple[str, bool]]:
        """The note effect sets (MasterLiveNoteEffectSkin._assetName, `names`: id -> name) the files may carry beyond
        the default set, as (name, is a Light variant): the other offered sets, and with LiveQuality Low offered the
        `Light` variant of every offered set (the default one included), in id order. The game loads a Light
        variant only where it exists and the set itself otherwise (NoteSkinLoadStep.CollectDownloadAddresses:
        ResourceManager.ExistsAsset; LoadNoteEffectSkinAssetSettingsAsync falls back when the Light load fails)."""
        if not self.defaults:
            return []
        low = LOW_QUALITY in self.qualities
        out = []
        for i in self.values("NoteEffectId"):
            if i != self.default("NoteEffectId"):
                out.append((names[i], False))
            if low:
                out.append((names[i] + LIGHT_SUFFIX, True))
        return out

    def read_variants(self) -> list[dict]:
        """The settings (option name -> value, non-default values only) whose read sets, with the default one,
        make the union of the files a chart reads over every offered variant: every combination of the offered
        picture options (the note skin, the effect set and its quality variant, and the mirrored score's flick
        directions select pictures together), then the bar lines alone (their sprite does not depend on the other
        options) and each other note sound set alone (sounds do not depend on the pictures). The all-default
        combination is left out."""
        if not self.defaults:
            return []
        dims = []
        for name in VISUAL_OPTIONS:
            vals = self.values(name)
            default = False if name in SWITCH_OPTIONS else self.default(name)
            dims.append([(name, v) for v in [default, *[v for v in vals if v != default]]])
        out = []
        for combo in itertools.product(*dims):
            s = {n: v for n, v in combo if v != (False if n in SWITCH_OPTIONS else self.default(n))}
            if s:
                out.append(s)
        if self.bar_lines:
            out.append({"MeasureLineDisplay": True})
        out += [{"NoteSePatternId": g} for g in self.se_patterns if g != self.default("NoteSePatternId")]
        return out


def preset_defaults(master: Path) -> dict[int, str]:
    """OptionItemType -> MasterOptionDefault preset-1 value string (a fresh profile's options)."""
    return {r["_optionItemType"]: r["_valueString"] for r in master_table(master, "MasterOptionDefault")
            if r["_presetId"] == 1}


def resolve(request: dict | None, master: Path | None) -> LiveOptions:
    """The LiveOptions of a request (parse_specs) with the master data: `all` values from its tables, requested
    values checked against them, the preset-1 defaults added. An empty request (or None) is LiveOptions()."""
    if not request:
        return LiveOptions()
    if master is None:
        raise OptionSpecError("--live-option needs the master data")
    master = Path(master)
    everything = bool(request.get(ALL))
    preset = preset_defaults(master)
    defaults, fields = [], {}
    for name, (item, table, column) in ENUM_OPTIONS.items():
        have = sorted({int(r[column]) for r in master_table(master, table)})
        d = int(preset[item])
        defaults.append((name, d))
        want = "all" if everything else request.get(name)
        if want is None:
            continue
        want = have if want == "all" else list(want)
        bad = [v for v in want if v not in have]
        if bad:
            raise OptionSpecError(f"--live-option {name}: {', '.join(map(str, bad))} not in {table} "
                                  f"({', '.join(map(str, have))})")
        vals = tuple(sorted(set(want) | {d}))
        if len(vals) > 1:
            fields[FIELDS[name]] = vals
    switches = {FIELDS[name]: everything or bool(request.get(name)) for name in SWITCH_OPTIONS}
    out = LiveOptions(defaults=tuple(defaults), **switches, **fields)
    return replace(out, tables=True) if (out or request.get(TABLES)) else out
