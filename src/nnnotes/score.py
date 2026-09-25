"""Live chart (MusicScore) extraction and a re-implementation of the game's chart converter.

Each function follows the game method named in its docstring. The pipeline,
as in the game:

    MusicScoreLoader.Load            (gunzip when the bytes start 1F 8B)
      SsRootDeserializer             (JSON -> SsRoot, field defaults)          -> parse()
      SsTickConverter                (tick -> bar position / ms)               -> TickConverter
      SsMusicScoreConverter          (SsNote -> NoteInfoData, merges, lines)   -> _build_note_infos()
      MusicScoreNoteCreator          (NoteInfoData -> notes, slide combo ticks)-> _Creator
      MusicScoreUtility.GetTimeMsFromBar (note time, float32)                  -> time_ms_from_bar()

`convert()` returns the runtime note list (the notes the game puts in
MusicScore.NoteDictionary, including SlideComboNote ticks), the note lines and
the events. `extract()` writes the shipped chart, the converted notes, the
master rows and the decoded song for one music/difficulty.

Game resources are only read from the user's own catalog/cache and written to
the user's output directory.
"""
from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import cri, languages
from .jsonio import write_json

f32 = np.float32

LANE_COUNT = 24                      # MusicScore.LaneCount (SsMusicScoreConverter.Load sets 0x18)
TICKS_PER_BEAT = 480                 # 60000 / (bpm * 480) in SsTickConverter.TickToTimeMs
TICKS_PER_WHOLE = 1920               # ticks per measure = numerator * 1920 / denominator
MAX_LINES = 20                       # LineIndexAssigner(0x14)
DIFFICULTIES = ("easy", "normal", "hard", "expert")   # _easyID.._expertID -> file suffix _00.._03

# SsNoteType / SsFlickDir / SsEase / SsFadeType (SsRootDeserializer.Parse*)
NOTE_TYPES = {"tap": 0, "flick": 1, "trace": 2, "long": 3, "guide": 4, "node": 5}
FLICK_DIRS = {"up": 0, "left": 1, "right": 2, "down": 3}
EASES = {"linear": 0, "in": 1, "out": 2}
FADES = {"none": 0, "in": 1, "out": 2}          # unknown string -> none (no exception)

# NoteOperateType
OP_NAMES = {
    0: "None", 1: "Normal", 20: "SlideBegin", 21: "SlideConnection", 22: "SlideEnd",
    40: "Flick", 41: "SlideBeginFlick", 42: "SlideEndFlick", 60: "Trace", 61: "SlideBeginTrace",
    62: "SlideEndTrace", 63: "SlideConnectionTrace", 80: "HiddenSlideBegin", 82: "HiddenSlideEnd",
    100: "GuideBegin", 101: "GuideBeginNormal", 102: "GuideBeginFlick", 103: "GuideEnd",
    104: "GuideBeginTrace", 105: "GuideEndTrace", 120: "Combo", 121: "ComboSkip", 122: "Hidden",
    123: "InvalidHidden",
}
# NoteLineEaseType as the runtime names it (LiveCalculator.GetNoteLineTypeEasing):
# 0 Linear t, 1 "EaseIn" (2-t)t, 2 "EaseOut" t*t.  Chart "in" maps to 2 and "out" to 1 (ConvertEase).
LINE_EASE_NAMES = {0: "Linear", 1: "EaseIn", 2: "EaseOut"}
DIRECTION_NAMES = {0: "Normal", 1: "Left", 2: "Right"}
# NoteJudgementType (FTLiveSimulator.Runtime)
JUDGEMENT_TYPE_NAMES = {0: "None", 1: "Normal", 2: "EasyNormal", 5: "Flick", 10: "SlideBegin",
                        11: "SlideEnd", 12: "SlideEndFlick", 15: "SlideBeginEasy", 21: "Trace",
                        22: "SlideEndTrace"}
# JudgementAreaOffsetType
OFFSET_TYPE_NAMES = {0: "Default", 1: "Slide", 2: "SlideBegin", 3: "SlideEnd", 4: "Flick", 5: "Trace",
                     6: "SlideMin", 7: "SlideMax", 8: "EasyDefault", 9: "EasySlideBegin"}

SLIDE_BEGIN_CLASS = {20, 41, 61, 80, 104}      # classes derived from SlideBeginNote (have AddCombo)
SLIDE_END_CLASS = {22, 42, 62, 82, 105}        # classes derived from SlideEndNote (ISlideNoteEnd)
GUIDE_BEGIN_OPS = {100, 101, 102, 104}
GUIDE_END_OPS = {103, 105}


# --------------------------------------------------------------------------- numeric helpers
def net_round(x: float) -> float:
    """System.Math.Round(double) (MidpointRounding.ToEven) exactly as the inlined IL2CPP code does it."""
    x = float(x)
    frac, ip = math.modf(x)
    if x >= 0.0:
        if frac == 0.5:
            return ip + 1.0 if int(ip) & 1 else ip
        return float(math.floor(x + 0.5))
    if frac == -0.5:
        return ip - 1.0 if int(ip) & 1 else ip
    return float(int(x - 0.5))                  # (double)(long)(x - 0.5): truncation toward zero


def net_round_int(x: float) -> int:
    v = net_round(x)
    return int(v) if math.isfinite(v) else -2**31


def is_judgement_note(op: int) -> bool:
    """NoteOperateTypeUtility.IsJudgementNote."""
    return op not in (0, 80, 82, 100, 103, 121, 122, 123)


def is_flick_type(op: int) -> bool:
    """NoteOperateTypeUtility.IsFlickType."""
    return op in (40, 41, 42, 102)


def judgement_type(op: int, crit: bool) -> int:
    """NoteJudgementTypeMap.ConvertJudgementType."""
    if op in (0, 21):
        return op
    if op == 1:
        return 2 if crit else 1
    if op == 20:
        return 15 if crit else 10
    if op == 22:
        return 11
    if op in (40, 41, 42, 102):
        return 5
    if op in (60, 61, 63, 104, 105, 120):
        return 21
    if op == 62:
        return 22
    if op in (80, 82, 100, 101, 103, 121, 122):
        return 1
    raise ValueError(f"ConvertJudgementType: operateType {op}")


MATHF_EPSILON = f32(1.401298e-45)    # Mathf.Epsilon (denormal min; 1.17549435e-38 if flush-to-zero is on, assumed off)


def mathf_approximately(a: float, b: float) -> bool:
    """UnityEngine.Mathf.Approximately (inlined in IsSamePosition / EqualMusicInfoPosition)."""
    a, b = f32(a), f32(b)
    m = max(abs(a), abs(b))
    tol = max(f32(m * f32(1e-6)), f32(MATHF_EPSILON * f32(8.0)))
    return bool(abs(f32(b - a)) < tol)


# --------------------------------------------------------------------------- SsRoot (ReadNote/ReadEvents)
@dataclass
class SsNote:
    type: int = 0            # SsNoteType, default Tap
    t: int = 0
    pos: np.float32 = f32(0.0)
    pos_auto: bool = False
    size: np.float32 = f32(6.0)   # ReadNote writes 6.0f (0x40c00000) before parsing
    crit: bool = False
    dir: int = 0             # SsFlickDir, default Up
    ease_l: int = 0          # SsEase
    ease_r: int = 0
    visible: bool = True     # ReadNote writes 1 before parsing
    alpha: int = 0           # SsFadeType
    node: list | None = None
    src: tuple = ()          # (note index, node index) in the shipped JSON, for traceability


def _to_int(v) -> int:
    """(int)JToken: Newtonsoft converts floats with Convert.ToInt32 (round half to even)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return net_round_int(v)
    return int(str(v).strip())


def _to_f32(v) -> np.float32:
    if isinstance(v, str):
        return f32(float(v))
    return f32(v)


def _enum(table: dict, s: str, what: str) -> int:
    if s not in table:
        raise ValueError(f"ss {what} value '{s}'")   # InvalidOperationException("ss" + s + ...)
    return table[s]


def read_note(n: dict, src: tuple = ()) -> SsNote:
    """SsRootDeserializer.ReadNote."""
    o = SsNote(src=src)
    if isinstance(n.get("type"), str):
        o.type = _enum(NOTE_TYPES, n["type"], "type")
    if n.get("t") is not None:
        o.t = _to_int(n["t"])
    p = n.get("pos")
    if p is not None:
        if isinstance(p, str) and p == "auto":
            o.pos_auto = True
        else:
            o.pos = _to_f32(p)
    if n.get("size") is not None:
        o.size = _to_f32(n["size"])
    if n.get("crit") is not None:
        o.crit = bool(n["crit"])
    if isinstance(n.get("dir"), str):
        o.dir = _enum(FLICK_DIRS, n["dir"], "dir")
    e = n.get("ease")
    if e is not None:
        if isinstance(e, list):
            o.ease_l = _enum(EASES, e[0], "ease")
            o.ease_r = _enum(EASES, e[1], "ease")
        else:
            o.ease_l = o.ease_r = _enum(EASES, e, "ease")
    if n.get("visible") is not None:
        o.visible = bool(n["visible"])
    if isinstance(n.get("alpha"), str):
        o.alpha = FADES.get(n["alpha"], 0)
    if isinstance(n.get("node"), list):
        o.node = [read_note(c, src + (i,)) for i, c in enumerate(n["node"])]
    return o


@dataclass
class SsEvents:
    bpm: list = field(default_factory=list)      # [(t, bpm f32)]
    sig: list = field(default_factory=list)      # [(t, num, den)]
    skill: list = field(default_factory=list)    # [t]
    fever: list = field(default_factory=list)    # [(t0, t1)]
    call: list = field(default_factory=list)     # [(t, [timing ints])]


def read_events(ev: dict) -> SsEvents:
    """SsRootDeserializer.ReadEvents."""
    o = SsEvents()
    for b in ev.get("bpm") or []:
        o.bpm.append((_to_int(b["t"]), _to_f32(b["bpm"])))
    for s in ev.get("sig") or []:
        o.sig.append((_to_int(s["t"]), _to_int(s["sig"][0]), _to_int(s["sig"][1])))
    for t in ev.get("skill") or []:
        o.skill.append(_to_int(t))
    for f in ev.get("fever") or []:
        o.fever.append((_to_int(f[0]), _to_int(f[1])))
    for c in ev.get("call") or []:
        o.call.append((_to_int(c["t"]), [_to_int(x) for x in c["timing"]]))
    return o


def load_bytes(data: bytes) -> dict:
    """MusicScoreLoader.Load: gunzip when the first two bytes are 1F 8B, then UTF-8 JSON."""
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def parse(root: dict) -> tuple[SsEvents, list[SsNote]]:
    """SsRootDeserializer.ReadRoot/ReadScore: only `score` is read (`meta` is ignored)."""
    score = root.get("score")
    if not isinstance(score, dict):
        raise ValueError("ss score")                 # SsMusicScoreConverter.Error("ss score")
    ev = read_events(score["events"]) if isinstance(score.get("events"), dict) else SsEvents()
    notes = [read_note(n, (i,)) for i, n in enumerate(score.get("notes") or [])]
    return ev, notes


# --------------------------------------------------------------------------- SsTickConverter
@dataclass(frozen=True)
class BarPos:
    bar: int
    rhythm: int
    unit: int                 # RhythmicUnit = ticks per measure of the segment
    progress: np.float32      # (float)rhythm / (float)unit

    @property
    def key(self) -> np.float32:     # BarProgress + (float)Bar, the dictionary key of the converter
        return f32(self.progress + f32(self.bar))


class TickConverter:
    """SsTickConverter (its constructor builds the sig / bpm segments)."""

    def __init__(self, sigs: list, bpms: list):
        self.sig = self._sig_segments(sigs)
        self.bpm = self._bpm_segments(bpms)

    @staticmethod
    def _sig_segments(sigs):
        """BuildSigSegments -> [(startTick, startBar, ticksPerMeasure, num, den)]."""
        s = sorted(sigs, key=lambda x: x[0])      # List.Sort(Comparison by T); ties keep chart order here
        out = []
        if not s or s[0][0] > 0:
            out.append((0, 0, 1920, 4, 4))
        bar, prev_t, tpm = 0, 0, 1920
        for t, num, den in s:
            q = int((t - prev_t) / tpm) if tpm != 0 else 0          # C# int division truncates
            if t - prev_t != 0 and prev_t <= t:
                bar += q
            tpm = int(num * 1920 / den) if den != 0 else 0
            out.append((t, bar, tpm, num, den))
            prev_t = t
        return out

    @staticmethod
    def _bpm_segments(bpms):
        """BuildBpmSegments -> [(startTick, startMs, bpm f32)]; startMs = Math.Round(acc)."""
        s = sorted(bpms, key=lambda x: x[0])
        out = []
        if not s or s[0][0] > 0:
            out.append((0, 0, f32(120.0)))
        acc, prev_t, prev_bpm = 0.0, 0, f32(120.0)
        for t, bpm in s:
            if t - prev_t != 0 and prev_t <= t:
                acc += (float(t - prev_t) * 60000.0) / float(f32(prev_bpm * f32(480.0)))
            out.append((t, net_round_int(acc), bpm))
            prev_t, prev_bpm = t, bpm
        return out

    def _find(self, segs, tick):
        for s in reversed(segs):
            if s[0] <= tick:
                return s
        return segs[0]

    def time_ms(self, tick: int) -> int:
        """TickToTimeMs: floor(double(dt)*60000.0 / double(float(bpm*480f)) + startMs)."""
        t0, ms0, bpm = self._find(self.bpm, tick)
        v = (float(tick - t0) * 60000.0) / float(f32(bpm * f32(480.0))) + float(ms0)
        return int(math.floor(v))

    def bar_position(self, tick: int) -> BarPos:
        """TickToBarPosition."""
        t0, bar0, tpm, _, _ = self._find(self.sig, tick)
        q = int((tick - t0) / tpm) if tpm != 0 else 0
        r = (tick - t0) - q * tpm
        return BarPos(bar0 + q, r, tpm, f32(f32(r) / f32(tpm)))

    def bar_head_tick(self, bar: int) -> int:
        """BarHeadTick."""
        seg = self.sig[0]
        for s in reversed(self.sig):
            if s[1] <= bar:
                seg = s
                break
        return seg[0] + (bar - seg[1]) * seg[2]


# --------------------------------------------------------------------------- runtime positions/events
@dataclass
class Pos:
    """MusicScorePosition."""
    bar: int
    rhythm: int
    unit: int
    progress: np.float32
    ms: int

    @property
    def key(self) -> np.float32:
        return f32(self.progress + f32(self.bar))

    def before(self, bar: int, progress) -> bool:
        return self.bar < bar or (self.bar == bar and self.progress < progress)


def make_position(tick: int, tc: TickConverter) -> Pos:
    """SsMusicScoreConverter.MakePosition."""
    bp = tc.bar_position(tick)
    return Pos(bp.bar, bp.rhythm, bp.unit, bp.progress, tc.time_ms(tick))


def time_ms_from_bar(bar: int, progress, bpm_events: list, bar_events: list) -> int:
    """MusicScoreUtility.GetTimeMsFromBar (all float32, floor)."""
    progress = f32(progress)
    ref = Pos(0, 0, 0, f32(0.0), 0)
    beats = f32(4.0)
    for value, p in bar_events:                 # BarChangeEvent (ChangeBar, position)
        if p.before(bar, progress):
            beats = value
            if p.ms > ref.ms:
                ref = p
    bpm = f32(160.0)
    for value, p in bpm_events:                 # BPMChangeEvent (ChangeBPM, position)
        if p.before(bar, progress):
            bpm = value
            if p.ms > ref.ms:
                ref = p
    spb = f32(f32(beats * f32(60.0)) / bpm)
    x = f32(f32(spb * f32(bar - ref.bar)) + f32(spb * f32(progress - ref.progress)))
    x = f32(x * f32(1000.0))
    return ref.ms + int(math.floor(float(x)))


def bar_rhythm(bar: int, bar_events: list) -> np.float32:
    """MusicScoreUtility.GetBarRhythm."""
    v = f32(4.0)
    for value, p in bar_events:
        if bar < p.bar:
            return v
        v = value
    return v


def bpm_at_ms(ms: int, bpm_events: list) -> np.float32:
    """MusicScoreNoteCreator.GetBpmAtTimeMs."""
    v = f32(160.0)
    for value, p in bpm_events:
        if p.ms <= ms:
            v = value
    return v


def same_position(a: Pos, b: Pos) -> bool:
    """MusicScoreNoteCreator.IsSamePosition / MusicScoreUtility.EqualMusicInfoPosition."""
    return a.bar == b.bar and mathf_approximately(a.progress, b.progress)


def ease(t, kind: int) -> np.float32:
    """LiveCalculator.GetNoteLineTypeEasing."""
    t = f32(t)
    if kind == 0:
        return t
    if kind == 2:
        return f32(t * t)
    if kind == 1:
        return f32(f32(f32(2.0) - t) * t)
    raise ValueError(f"noteLineEaseType {kind}")


def convert_ease(e: int) -> int:
    """SsMusicScoreConverter.ConvertEase: chart in(1) -> 2, out(2) -> 1, linear -> 0."""
    return 2 if e == 1 else (1 if e == 2 else 0)


def flick_direction(n: SsNote) -> int:
    """ResolveFlickDir: left -> Left(1), right -> Right(2), up/down -> Normal(0)."""
    return 1 if n.dir == 1 else (2 if n.dir == 2 else 0)


def mirror_direction(d: int, mirror: bool) -> int:
    """ApplyMirrorDirection."""
    if not mirror:
        return d
    return 2 if d == 1 else (1 if d == 2 else 0)


def apply_mirror(lane, width, mirror: bool) -> np.float32:
    """ApplyMirror: 24 - (lane + 0) - width when mirrored, else lane + 0."""
    if mirror:
        return f32(f32(f32(24.0) - f32(lane + f32(0.0))) - width)
    return f32(lane + f32(0.0))


def overlap_key(t: int, pos, size) -> tuple:
    """MakeOverlapKey: (t, Math.Round(pos), Math.Round(size))."""
    return (t, net_round_int(float(pos)), net_round_int(float(size)))


# --------------------------------------------------------------------------- NoteInfoData
@dataclass
class NoteInfo:
    bar: int
    rhythm: int
    unit: int
    progress: np.float32
    lane: int
    lane_f: np.float32
    width: int
    width_f: np.float32
    op: int
    slide_along: bool = False
    crit: bool = False
    direction: int = 0
    ease: int = 0
    ease_r: int = 0
    lines: list = field(default_factory=list)       # LineIndexList (line *indices*, 0..19)
    src: tuple = ()
    tick: int = 0
    visible: bool = True
    alpha: int = 0

    @property
    def key(self) -> np.float32:
        return f32(self.progress + f32(self.bar))


class LineIndexAssigner:
    """SsMusicScoreConverter.LineIndexAssigner (Acquire, Register)."""

    def __init__(self, max_lines: int = MAX_LINES):
        self.in_use: list[tuple[int, int]] = []
        self.max = max_lines

    def acquire(self, start_tick: int) -> int:
        for i in range(len(self.in_use) - 1, -1, -1):
            if self.in_use[i][1] < start_tick:
                del self.in_use[i]
        used = {i for i, _ in self.in_use}
        for i in range(self.max):
            if i not in used:
                return i
        raise ValueError(f"ss lineIndex {self.max} startTick {start_tick}")

    def register(self, idx: int, end_tick: int) -> None:
        self.in_use.append((idx, end_tick))


def _single_info(n: SsNote, tc: TickConverter, mirror: bool) -> NoteInfo:
    """BuildSingleNoteInfo."""
    bp = tc.bar_position(n.t)
    lane_f = apply_mirror(n.pos, n.size, mirror)
    lane = net_round_int(float(lane_f))
    width = max(1, net_round_int(float(n.size)))
    if n.type == 1:
        op, d = 40, flick_direction(n)
    elif n.type == 2:
        op, d = 60, 0
    else:
        op, d = 1, 0
    return NoteInfo(bp.bar, bp.rhythm, bp.unit, bp.progress, lane, lane_f, width, n.size, op,
                    False, n.crit, mirror_direction(d, mirror), 0, 0, [], n.src, n.t, n.visible, n.alpha)


def _long_node_op(nodes: list, i: int) -> int:
    """ResolveLongNodeOperateType."""
    n = nodes[i]
    if i == 0:
        if not n.visible:
            return 80
        return 41 if n.type == 1 else (61 if n.type == 2 else 20)
    if i == len(nodes) - 1:
        if not n.visible:
            return 82
        return 42 if n.type == 1 else (62 if n.type == 2 else 22)
    if n.pos_auto:
        return 21
    if not n.visible:
        return 122
    return 63 if n.type == 2 else 21


def _guide_node_op(nodes: list, i: int, guide_map: dict | None) -> int:
    """ResolveGuideNodeOperateType (its constant table: tap 101, flick 102, trace 104)."""
    n = nodes[i]
    if i == 0:
        if guide_map is not None and not n.pos_auto:
            s = guide_map.get(overlap_key(n.t, n.pos, n.size))
            if s is not None and s.type < 3:
                return (101, 102, 104)[s.type]
        return 100
    if i == len(nodes) - 1:
        if not n.visible:
            return 103
        return 105 if n.type == 2 else 103
    if n.pos_auto:
        return 63
    return 122 if not n.visible else 63


def _line_infos(chains: list, is_long: bool, tc: TickConverter, mirror: bool, out: list,
                guide_map: dict | None, assigner: LineIndexAssigner) -> None:
    """BuildLongOrGuideNoteInfos."""
    for _, chain in chains:
        nodes = chain.node
        N = len(nodes)
        line_idx = assigner.acquire(nodes[0].t)
        end_tick = nodes[-1].t
        for i, nd in enumerate(nodes):
            op = _long_node_op(nodes, i) if is_long else _guide_node_op(nodes, i, guide_map)
            if not nd.pos_auto:
                pos, size = nd.pos, nd.size
            elif i == 0 or i >= N - 1:
                pos, size = nodes[0].pos, nodes[0].size
            else:
                j = i
                while True:                       # nearest non-auto node in [1, i-1], else node 0
                    if j < 2:
                        j = 0
                        break
                    j -= 1
                    if not nodes[j].pos_auto:
                        break
                k = i + 1
                while k < N - 1 and nodes[k].pos_auto:   # nearest non-auto node in [i+1, N-2], else N-1
                    k += 1
                a, b = nodes[j], nodes[k]
                span = b.t - a.t
                prog = f32(0.0) if span < 1 else f32(f32(nd.t - a.t) / f32(span))
                el = ease(prog, convert_ease(a.ease_l))
                er = ease(prog, convert_ease(a.ease_r))
                pos = f32(a.pos + f32(el * f32(b.pos - a.pos)))
                a_r = f32(a.pos + a.size)
                right = f32(a_r + f32(er * f32(f32(b.pos + b.size) - a_r)))
                size = f32(right - pos)
            bp = tc.bar_position(nd.t)
            lane_f = apply_mirror(pos, size, mirror)
            lane = net_round_int(float(lane_f))
            width = max(1, net_round_int(float(size)))
            dir_src = nd
            if guide_map is not None and i == 0 and not is_long and not nd.pos_auto:
                s = guide_map.get(overlap_key(nd.t, nd.pos, nd.size))
                if s is not None:
                    dir_src = s
            d = flick_direction(dir_src) if dir_src.type == 1 else 0
            out.append(NoteInfo(bp.bar, bp.rhythm, bp.unit, bp.progress, lane, lane_f, width, size, op,
                                nd.pos_auto, nd.crit, mirror_direction(d, mirror),
                                convert_ease(nd.ease_l), convert_ease(nd.ease_r), [line_idx], nd.src,
                                nd.t, nd.visible, nd.alpha))
        assigner.register(line_idx, end_tick)


def _build_note_infos(notes: list, tc: TickConverter, mirror: bool) -> list[NoteInfo]:
    """BuildNoteInfoList."""
    guide_starts = set()
    for n in notes:
        if n.type == 4 and n.node:
            first = n.node[0]
            if not first.pos_auto:
                guide_starts.add(overlap_key(first.t, first.pos, first.size))
    guide_map: dict = {}
    merged: set[int] = set()
    for i, n in enumerate(notes):
        if n.type < 3:
            k = overlap_key(n.t, n.pos, n.size)
            if k in guide_starts and k not in guide_map:
                guide_map[k] = n
                merged.add(i)
    out: list[NoteInfo] = []
    for i, n in enumerate(notes):
        if i not in merged and n.type < 3:
            out.append(_single_info(n, tc, mirror))
    order = lambda it: (it[1].node[0].t, it[0])                  # <BuildNoteInfoList>b__7_0/7_1
    longs = sorted(((i, n) for i, n in enumerate(notes) if n.type == 3 and n.node and len(n.node) > 1), key=order)
    assigner = LineIndexAssigner(MAX_LINES)
    _line_infos(longs, True, tc, mirror, out, None, assigner)
    guides = sorted(((i, n) for i, n in enumerate(notes) if n.type == 4 and n.node and len(n.node) > 1), key=order)
    _line_infos(guides, False, tc, mirror, out, guide_map, assigner)
    return out


MERGEABLE = {20, 22, 41, 42, 61, 62, 80, 82, 100, 101, 102, 103, 104, 105}   # IsMergeableSlideEndpoint


def _info_dictionary(infos: list[NoteInfo], log: list) -> dict:
    """BuildNoteInfoDictionary + TryMergeSlideEndpoint."""
    d: dict = {}
    for n in infos:
        lanes = d.setdefault(n.key, {})
        lst = lanes.setdefault(n.lane, [])
        if n.op in MERGEABLE and n.lines:
            hit = next((e for e in lst if e.op == n.op and e.width == n.width and e.crit == n.crit
                        and e.direction == n.direction and e.ease == n.ease and e.ease_r == n.ease_r), None)
            if hit is not None:
                hit.lines.extend(n.lines)
                continue
        if is_judgement_note(n.op):
            cnt = sum(1 for e in lst if is_judgement_note(e.op))
            if cnt > 2:
                log.append(f"{3} bar {n.bar} barProgress {float(n.progress)} lane {n.lane} op {OP_NAMES[n.op]}")
        lst.append(n)
    return d


def _priority(op: int) -> int:
    """<CreateNoteDictionary>b__23_2 (sort priority)."""
    if op in (20, 41, 61, 80, 100, 101, 102, 104):
        return 8
    if op == 122:
        return 9
    return 10


# --------------------------------------------------------------------------- MusicScoreNoteCreator
@dataclass(eq=False)
class Note:
    id: int
    pos: Pos
    op: int
    lane_start: int
    lane_end: int
    lane_start_f: np.float32
    lane_end_f: np.float32
    width: np.float32
    slide_along: bool
    crit: bool
    direction: int
    line_ids: list
    ease: int
    ease_r: int
    offset_type: int
    pair_id: int = 0
    fever: int | None = None
    info: NoteInfo | None = None
    # line bookkeeping (SlideBeginNote / GuideBeginNote / SlideEndNote)
    view_notes: list = field(default_factory=list)
    combo_notes: list = field(default_factory=list)
    begin_notes: list = field(default_factory=list)
    end_combo: list = field(default_factory=list)
    begin_ref: "Note | None" = None
    end_ref: "Note | None" = None


class _Creator:
    """MusicScoreNoteCreator (Initialize, Create)."""

    def __init__(self, start_id: int, bpm_events, bar_events, fevers):
        self.bpm_events, self.bar_events, self.fevers = bpm_events, bar_events, fevers
        self.cur_id = start_id
        self.cur_line = start_id
        self.cur_guide_line = start_id
        self.pair_tmp: Note | None = None
        self.begin_by_line: dict[int, Note] = {}
        self.line_arr = [-1] * MAX_LINES
        self.guide_arr = [-1] * MAX_LINES
        self.log: list[str] = []

    def _note(self, info: NoteInfo, pos: Pos, op: int, line_ids: list, offset: int) -> Note:
        # NoteBase(id, pos, noteInfo, lineIds, offsetType)
        return Note(self.cur_id, pos, op, info.lane, info.lane + info.width - 1, info.lane_f,
                    f32(f32(info.lane_f + info.width_f) - f32(1.0)), info.width_f, info.slide_along,
                    info.crit, info.direction, line_ids, info.ease, info.ease_r, offset, info=info)

    def create(self, info: NoteInfo) -> Note | None:
        ms = time_ms_from_bar(info.bar, info.progress, self.bpm_events, self.bar_events)
        pos = Pos(info.bar, info.rhythm, info.unit, info.progress, ms)
        self.cur_id += 1
        op = info.op
        crit = info.crit
        if op == 1:
            note = self._note(info, pos, op, [], 8 if crit else 0)
        elif op in (20, 41, 61, 80):
            ids = []
            for li in info.lines:
                self.cur_line += 1
                ids.append(self.cur_line)
                self.line_arr[li] = self.cur_line
            offset = {20: (9 if crit else 2), 41: 4, 61: 5, 80: 5}[op]      # Create*Begin*Note ctor argument
            note = self._note(info, pos, op, ids, offset)
            for lid in ids:
                self.begin_by_line[lid] = note
        elif op in (21, 63):
            arr = self.line_arr if op == 21 else self.guide_arr
            lid = arr[info.lines[0]]
            note = self._note(info, pos, op, [lid], 1 if op == 21 else 5)
            b = self.begin_by_line.get(lid)
            if b is not None:
                b.view_notes.append(note)
        elif op in (22, 42, 62, 82):
            ids = []
            for li in info.lines:
                ids.append(self.line_arr[li])
                self.line_arr[li] = -1
            note = self._note(info, pos, op, ids, {22: 3, 42: 4, 62: 5, 82: 5}[op])
            self._end_line(note)
        elif op == 40:
            note = self._note(info, pos, op, [], 4)
        elif op == 60:
            note = self._note(info, pos, op, [], 5)
        elif op in (100, 101, 102, 104):
            ids = []
            for li in info.lines:
                lid = self.cur_guide_line + 10001
                self.cur_guide_line += 1
                ids.append(lid)
                self.guide_arr[li] = lid
            note = self._note(info, pos, op, ids, 5 if op == 104 else 0)
            for lid in ids:
                self.begin_by_line[lid] = note
        elif op in (103, 105):
            ids = []
            for li in info.lines:
                ids.append(self.guide_arr[li])
                self.guide_arr[li] = -1
            note = self._note(info, pos, op, ids, 5 if op == 105 else 0)
            self._end_line(note)
        elif op == 122:
            lid = self.line_arr[info.lines[0]]
            if lid < 1:
                lid = self.guide_arr[info.lines[0]]
                if lid < 1:
                    self.log.append(f"noteId {self.cur_id} pos {pos}")
                    return None
            note = self._note(info, pos, op, [lid], 0)
            b = self.begin_by_line.get(lid)
            if b is not None:
                b.view_notes.append(note)
        else:
            self.log.append(f"noteId {self.cur_id} type {OP_NAMES.get(op, op)}")
            return None
        if op in (1, 20, 22, 40, 41, 42):                     # IsPairNoteType
            t = self.pair_tmp
            if t is not None and same_position(t.pos, note.pos):   # TrySetPairNoteId
                note.pair_id, t.pair_id = t.id, note.id
            self.pair_tmp = note
        note.fever = self._fever(note.pos.ms)
        return note

    def _fever(self, ms: int) -> int | None:
        """IsFeverTargetNote: first fever with start.ms <= ms <= end.ms."""
        for idx, start, end in self.fevers:
            if start.ms <= ms <= end.ms:
                return idx
        return None

    def _end_line(self, end: Note) -> None:
        """TrySetSlideNoteId, then SlideEndNote.AddBeginNote."""
        begins = []
        ok = True
        for lid in end.line_ids:
            begin = self.begin_by_line.get(lid)
            if begin is None:
                ok = False
                break
            begins.append(begin)
            notes = [x for x in begin.view_notes if lid in x.line_ids]
            notes.sort(key=lambda x: (x.pos.bar, float(x.pos.progress)))      # OrderBy(Bar).ThenBy(BarProgress)
            notes.append(end)
            cur = next(x for x in notes if not x.slide_along)                 # First(!SlideAlong)
            start_pos, end_pos = begin.pos, end.pos
            mids = [x.pos for x in notes if x is not end and is_judgement_note(x.op)]   # <>c__DisplayClass43_0.b__4
            beats = list(_beats_between(start_pos, end_pos, mids, self.bpm_events, self.bar_events))
            prev = begin
            walk = 0
            for k, beat in enumerate(beats):
                if any(is_judgement_note(x.op) and same_position(x.pos, beat) for x in notes):   # b__6
                    continue
                window = 15000.0 / float(bpm_at_ms(beat.ms, self.bpm_events))
                skip = any(float(mp.ms) - window <= float(beat.ms) and beat.ms < mp.ms for mp in mids)
                if k == 0 and window > float(beat.ms - start_pos.ms):
                    skip = True
                if k == len(beats) - 1 and window > float(end_pos.ms - beat.ms):
                    skip = True
                while cur.pos.key < beat.key:                                 # walk to the enclosing segment
                    nxt = notes[walk]
                    walk += 1
                    if nxt.id != cur.id and not nxt.slide_along:
                        prev, cur = cur, nxt
                    if walk >= len(notes):
                        break
                ls, le = lane_position(beat, prev, cur)
                cid = lid + k * 10000 + 10000
                # SlideComboNote..ctor: LaneStart/EndIndex = floor (not truncation), Width = end - start + 1
                combo = Note(cid, beat, 121 if skip else 120, int(math.floor(float(ls))), int(math.floor(float(le))),
                             ls, le, f32(f32(le - ls) + f32(1.0)), False, False, 0, [lid], 0, 0, 1)
                combo.begin_ref, combo.end_ref = begin, end
                combo.fever = self._fever(beat.ms)
                if begin.op in SLIDE_BEGIN_CLASS:                             # GuideBeginNote.AddCombo is empty
                    begin.combo_notes.append(combo)
            self.begin_by_line.pop(lid, None)
        if ok:
            for b in begins:
                end.begin_notes.append(b)
                if end.op in SLIDE_END_CLASS and b.op in SLIDE_BEGIN_CLASS:
                    end.end_combo.extend(b.combo_notes)


def _beats_between(start: Pos, end: Pos, mids: list, bpm_events, bar_events):
    """GetBeatsBetweenNotes d__44: eighth notes of every [boundary, next boundary) segment."""
    bounds = [start, *mids, end]
    for s in range(len(bounds) - 1):
        yield from _eighths(bounds[s], bounds[s + 1], bpm_events, bar_events)


def _eighths(seg_start: Pos, seg_end: Pos, bpm_events, bar_events):
    """GetRelativeEighthNotePositions d__45 (double accumulator, restarts at the segment start)."""
    bar = seg_start.bar
    prog = float(seg_start.progress)
    r = bar_rhythm(bar, bar_events)
    step = 1.0 / (float(r) + float(r))
    while True:
        prog += step
        if prog >= 1.0:
            bar += 1
            prog -= 1.0
            r = bar_rhythm(bar, bar_events)
            step = 1.0 / (float(r) + float(r))
        fp = f32(prog)
        ms = time_ms_from_bar(bar, fp, bpm_events, bar_events)
        if seg_end.ms <= ms:
            return
        r2 = bar_rhythm(bar, bar_events)
        unit = int(f32(r2 + r2))
        rhythm = net_round_int(float(f32(fp * f32(unit))))
        yield Pos(bar, rhythm, unit, fp, ms)


def lane_position(pos: Pos, a: Note, b: Note) -> tuple:
    """MusicScoreUtility.GetLanePosition (one ease, a.LineEaseType, for both edges)."""
    span = f32(b.pos.key - a.pos.key)
    if span <= 0.0:
        e = ease(f32(1.0), a.ease)
    else:
        e = ease(f32(f32(pos.key - a.pos.key) / span), a.ease)
    e = min(max(e, f32(0.0)), f32(1.0))
    return (f32(a.lane_start_f + f32(e * f32(b.lane_start_f - a.lane_start_f))),
            f32(a.lane_end_f + f32(e * f32(b.lane_end_f - a.lane_end_f))))


# --------------------------------------------------------------------------- convert
def convert(root: dict, mirror: bool = False, start_note_id: int = 0) -> dict:
    """SsMusicScoreConverter.Load -> plain dict of the runtime score."""
    ev, notes = parse(root)
    tc = TickConverter(ev.sig, ev.bpm)
    infos = _build_note_infos(notes, tc, mirror)

    bpm_events = [(b, make_position(t, tc)) for t, b in sorted(ev.bpm, key=lambda x: x[0])]         # BuildBpmEvents
    bar_events = [(f32(f32(f32(n) * f32(4.0)) / f32(d)), make_position(t, tc))                       # BuildBarEvents
                  for t, n, d in sorted(ev.sig, key=lambda x: x[0])]
    fevers = [(i, make_position(a, tc), make_position(b, tc))                                        # BuildFeverList
              for i, (a, b) in enumerate(sorted(ev.fever, key=lambda x: x[0]))]
    skills = [(i, make_position(t, tc)) for i, t in enumerate(ev.skill)]                             # BuildSkillList
    calls = []                                                                                        # BuildCallList
    for t, timing in sorted(ev.call, key=lambda x: x[0]):
        n = len(timing)
        calls.append((make_position(t, tc), [float(f32(f32(i + 1.0) / f32(n))) for i, v in enumerate(timing) if v == 1]))

    max_bar = max((n.bar for n in infos), default=-1)                                                # <Load>b__5_0
    bar_line_ms = [tc.time_ms(tc.bar_head_tick(b)) for b in range(max_bar + 1)]

    log: list[str] = []
    idict = _info_dictionary(infos, log)
    creator = _Creator(start_note_id, bpm_events, bar_events, fevers)
    by_key: dict = {}
    for key in sorted(idict):
        flat = [x for lst in idict[key].values() for x in lst]
        flat.sort(key=lambda x: _priority(x.op))
        for info in flat:
            note = creator.create(info)
            if note is None:
                continue
            by_key.setdefault(note.pos.key, []).append(note)
            if note.op in SLIDE_END_CLASS and note.end_combo:
                for c in note.end_combo:
                    lst = by_key.setdefault(c.pos.key, [])
                    if c not in lst:
                        lst.append(c)
    log += creator.log
    ordered = [n for k in sorted(by_key) for n in by_key[k]]
    return _to_json(ordered, infos, tc, ev, bpm_events, bar_events, fevers, skills, calls, bar_line_ms, log,
                    mirror, start_note_id)


def _pos_json(p: Pos) -> dict:
    return {"bar": p.bar, "rhythm": p.rhythm, "rhythmicUnit": p.unit, "barProgress": float(p.progress), "timeMs": p.ms}


def _to_json(notes, infos, tc, ev, bpm_events, bar_events, fevers, skills, calls, bar_line_ms, log, mirror, start_id):
    out_notes = []
    lines: dict[int, dict] = {}
    for n in notes:
        info = n.info
        rec = {
            "id": n.id, "op": n.op, "opName": OP_NAMES[n.op],
            "timeMs": n.pos.ms, "bar": n.pos.bar, "rhythm": n.pos.rhythm, "rhythmicUnit": n.pos.unit,
            "barProgress": float(n.pos.progress),
            "laneStart": n.lane_start, "laneEnd": n.lane_end,
            "laneStartFloat": float(n.lane_start_f), "laneEndFloat": float(n.lane_end_f), "width": float(n.width),
            "critical": n.crit, "direction": DIRECTION_NAMES[n.direction], "slideAlong": n.slide_along,
            "lineIds": list(n.line_ids), "lineEase": LINE_EASE_NAMES[n.ease], "lineEaseR": LINE_EASE_NAMES[n.ease_r],
            "pairNoteId": n.pair_id, "fever": n.fever,
            "judgement": is_judgement_note(n.op), "flick": is_flick_type(n.op),
            "judgementType": JUDGEMENT_TYPE_NAMES[judgement_type(n.op, n.crit)] if n.op != 123 else None,
            "judgementAreaOffset": OFFSET_TYPE_NAMES[n.offset_type],
        }
        if info is not None:
            rec.update({"tick": info.tick, "laneIndexFloat": float(info.lane_f), "widthFloat": float(info.width_f),
                        "laneIndex": info.lane, "laneWidth": info.width, "lineIndices": list(info.lines),
                        "visible": info.visible, "alpha": ["none", "in", "out"][info.alpha], "src": list(info.src)})
        else:  # SlideComboNote
            rec.update({"lineBeginId": n.begin_ref.id, "lineEndId": n.end_ref.id})
        out_notes.append(rec)
        for lid in n.line_ids:
            ln = lines.setdefault(lid, {"lineId": lid, "type": "guide" if lid > start_id + 10000 else "long",
                                         "noteIds": [], "comboIds": []})
            (ln["comboIds"] if info is None else ln["noteIds"]).append(n.id)
    judged = sum(1 for n in notes if is_judgement_note(n.op))
    return {
        "format": "nnnotes.live-score/1",
        "mirror": mirror, "startNoteId": start_id, "laneCount": LANE_COUNT,
        "judgementNoteCount": judged,
        "counts": {OP_NAMES[k]: v for k, v in sorted(_count_ops(notes).items())},
        "notes": out_notes,
        "lines": sorted(lines.values(), key=lambda x: x["lineId"]),
        "bpmChanges": [{"bpm": float(v), **_pos_json(p)} for v, p in bpm_events],
        "barChanges": [{"beatsPerBar": float(v), **_pos_json(p)} for v, p in bar_events],
        "tickSegments": {"bpm": [{"startTick": a, "startTimeMs": b, "bpm": float(c)} for a, b, c in tc.bpm],
                         "sig": [{"startTick": a, "startBar": b, "ticksPerMeasure": c, "numerator": d,
                                  "denominator": e} for a, b, c, d, e in tc.sig]},
        "barLineTimeMs": bar_line_ms,
        "fever": [{"index": i, "start": _pos_json(a), "end": _pos_json(b)} for i, a, b in fevers],
        "skill": [{"index": i, **_pos_json(p)} for i, p in skills],
        "call": [{**_pos_json(p), "rhythms": r} for p, r in calls],
        "lastNoteTimeMs": max((n.pos.ms for n in notes), default=0),
        "log": log,
    }


def _count_ops(notes) -> dict:
    c: dict = {}
    for n in notes:
        c[n.op] = c.get(n.op, 0) + 1
    return c


# --------------------------------------------------------------------------- extraction
def master_table(master_dir: Path, name: str) -> list:
    """Rows of a decoded master table (<master_dir>/<name>.json, `_allData`)."""
    return json.loads((Path(master_dir) / f"{name}.json").read_text(encoding="utf-8"))["_allData"]


def chart_key(file_name: str) -> str:
    """LiveEntry loads `Live/MusicScore/` + MasterLiveMusicScore._musicScoreTextFileName."""
    return f"Live/MusicScore/{file_name}"


def fetch_chart(cat, file_name: str) -> bytes:
    """Raw TextAsset bytes of a chart (still gzip, as shipped)."""
    from .unity import load_closure
    key = chart_key(file_name)
    name = file_name.rsplit("/", 1)[-1]
    for p in cat.fetch_key(key):
        env = load_closure([p])
        for o in env.objects:
            if o.type.name == "TextAsset":
                d = o.read()
                if d.m_Name != name:
                    continue
                raw = d.m_Script
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "surrogateescape")
                return bytes(raw)
    raise KeyError(key)


def music_rows(master_dir: Path, music_id: int) -> dict:
    """MasterLiveMusic row, its 4 MasterLiveMusicScore rows, title / band name texts in every language
    (languages.LANGUAGES) and sound rows."""
    music = next(r for r in master_table(master_dir, "MasterLiveMusic") if r["_id"] == music_id)
    scores = {r["_id"]: r for r in master_table(master_dir, "MasterLiveMusicScore")}
    text = {r["_id"]: r for r in master_table(master_dir, "MasterText")}
    bands = {r["_id"]: r for r in master_table(master_dir, "MasterBand")}
    sounds = {r["_id"]: r for r in master_table(master_dir, "MasterSound")}
    sheets = {r["_id"]: r for r in master_table(master_dir, "MasterSoundCueSheet")}

    def tx(i):
        r = text.get(i)
        return None if r is None else {"id": i, **languages.texts(r)}

    def snd(i):
        r = sounds.get(i)
        if r is None:
            return None
        return {"MasterSound": r, "MasterSoundCueSheet": sheets.get(r["_soundCueSheetID"])}

    return {
        "MasterLiveMusic": music,
        "MasterLiveMusicScore": {d: scores.get(music[f"_{d}ID"]) for d in DIFFICULTIES},
        "title": tx(music["_titleTextID"]),
        "bands": [{"MasterBand": bands.get(b), "name": tx(bands[b]["_nameTextID"]) if b in bands else None}
                  for b in music["_bandIDs"]],
        "music": snd(music["_musicSoundID"]),
        "jingle": snd(music["_jingleSoundID"]),
    }


def extract_audio(cat, master_dir, music_id: int, out_dir, audio_fmt: str = "flac", *,
                  flac_level: int = cri.FLAC_LEVEL, also=None, rows: dict | None = None) -> dict:
    """<out>/audio/<cueSheet>/: the live BGM cue sheet decoded per cue + cues.json (cri.decode; `flac_level`,
    `also` as there). -> the `audio` entry of extract's summary (paths relative to out_dir)."""
    out_dir = Path(out_dir)
    rows = rows or music_rows(Path(master_dir), music_id)
    sheet = rows["music"]["MasterSoundCueSheet"]["_cueSheetName"]
    cue = rows["music"]["MasterSound"]["_cueName"]
    files = cri.decode(cat, sheet, out_dir / "audio" / sheet, fmt=audio_fmt, flac_level=flac_level, also=also)

    def rel(p) -> str:
        return Path(p).relative_to(out_dir).as_posix()
    return {"cueSheet": sheet, "cue": cue, "file": rel(files[cue]) if cue in files else None,
            "source": cri.layout(cat, sheet), "cues": {k: rel(v) for k, v in files.items()}}


def extract(cat, master_dir, music_id: int, difficulty: str | int, out_dir, audio: bool = True,
            audio_fmt: str = "flac", *, flac_level: int = cri.FLAC_LEVEL, also=None) -> dict:
    """Write <out>/score/<file>.json (shipped chart JSON, un-gzipped), <file>.notes.json (converted runtime
    notes), <out>/score/master.json (master rows) and <out>/audio/<cueSheet>/ (the live BGM cue sheet decoded
    per cue + cues.json, extract_audio)."""
    out_dir = Path(out_dir)
    sdir = out_dir / "score"
    sdir.mkdir(parents=True, exist_ok=True)
    rows = music_rows(Path(master_dir), music_id)
    diff = DIFFICULTIES[difficulty] if isinstance(difficulty, int) else difficulty
    score_row = rows["MasterLiveMusicScore"][diff]
    fname = score_row["_musicScoreTextFileName"]
    raw = fetch_chart(cat, fname)
    txt = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    base = fname.rsplit("/", 1)[-1]
    (sdir / f"{base}.json").write_bytes(txt)
    conv = convert(load_bytes(raw))
    conv["source"] = {"key": chart_key(fname), "musicId": music_id, "difficulty": diff,
                      "masterLiveMusicScoreId": score_row["_id"], "fullComboCount": score_row["_fullComboCount"]}
    write_json(sdir / f"{base}.notes.json", conv)
    write_json(sdir / "master.json", rows)
    def rel(p) -> str:  # paths in summary.json are relative to out_dir, POSIX separators (loadable as relative URLs)
        return Path(p).relative_to(out_dir).as_posix()

    summary = {"chart": rel(sdir / f"{base}.json"), "notes": rel(sdir / f"{base}.notes.json"),
               "master": rel(sdir / "master.json"), "judgementNoteCount": conv["judgementNoteCount"],
               "fullComboCount": score_row["_fullComboCount"], "noteCount": len(conv["notes"]),
               "lastNoteTimeMs": conv["lastNoteTimeMs"]}
    if audio:
        summary["audio"] = extract_audio(cat, master_dir, music_id, out_dir, audio_fmt, flac_level=flac_level,
                                         also=also, rows=rows)
    write_json(out_dir / "score" / "summary.json", summary)
    return summary
