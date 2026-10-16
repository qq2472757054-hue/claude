"""生成时间轴：片段时长调整、起点、转场、字幕事件、片尾卡。"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

from ..templates.schema import Template
from .ffmpeg_cmd import XFADE_TYPES
from .picker import Choice, place
from .subtitles import SubEvent

END_CARD_SECONDS = 2.5
MIN_SEG = 1.0


@dataclass
class TextPack:
    """字幕文字（已按目标语言准备好）。值为 (英文/主语言, 中文)。"""
    hook: tuple[str, str] = ("", "")
    cta: tuple[str, str] = ("", "")
    captions: dict[int, tuple[str, str]] = field(default_factory=dict)       # 段落序号 → 文字
    voice: dict[int, list[dict]] = field(default_factory=dict)              # 选片序号 → [{start,end,text,text_zh}]
    untranslated: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["captions"] = {str(k): list(v) for k, v in self.captions.items()}
        d["voice"] = {str(k): v for k, v in self.voice.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TextPack":
        return cls(hook=tuple(d.get("hook", ("", ""))), cta=tuple(d.get("cta", ("", ""))),
                   captions={int(k): tuple(v) for k, v in (d.get("captions") or {}).items()},
                   voice={int(k): v for k, v in (d.get("voice") or {}).items()},
                   untranslated=bool(d.get("untranslated")))


@dataclass
class Timeline:
    choices: list[Choice]
    starts: list[float]          # 每段在成片中的开始时间（含片尾卡）
    durs: list[float]
    transitions: list[str]       # len = 段数 - 1
    trans_dur: float
    total: float
    end_card: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"choices": [c.to_dict() for c in self.choices], "starts": self.starts,
                "durs": self.durs, "transitions": self.transitions, "trans_dur": self.trans_dur,
                "total": self.total, "end_card": self.end_card, "warnings": self.warnings}

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls([Choice.from_dict(c) for c in d["choices"]], d["starts"], d["durs"],
                   d["transitions"], d["trans_dur"], d["total"], d["end_card"], d.get("warnings", []))


def _total(durs: list[float], trans: float) -> float:
    return sum(durs) - trans * max(len(durs) - 1, 0)


def _max_len(c: Choice) -> float:
    if c.is_image:
        return 6.0
    return max(c.win_end - c.win_start, c.dur)


def fit_durations(choices: list[Choice], template: Template, trans: float, end_card: bool) -> list[str]:
    """把总时长调整到目标 ±10% 以内（只改不保留原声的片段）。"""
    warnings = []
    target = float(template.duration)
    lo, hi = target * 0.9, target * 1.1
    extra = END_CARD_SECONDS if end_card else 0.0
    for _ in range(4):
        durs = [c.dur for c in choices] + ([extra] if end_card else [])
        total = _total(durs, trans)
        if lo <= total <= hi:
            break
        flex = [c for c in choices if not c.fixed_duration]
        flex_sum = sum(c.dur for c in flex)
        if flex_sum <= 0:
            break
        need = target - (total - flex_sum)
        factor = need / flex_sum
        for c in flex:
            c.dur = round(min(max(c.dur * factor, MIN_SEG, trans * 2 + 0.2), _max_len(c)), 3)
    durs = [c.dur for c in choices] + ([extra] if end_card else [])
    total = _total(durs, trans)
    if not (lo <= total <= hi):
        warnings.append(f"成片时长 {total:.1f} 秒，超出目标 {target:.0f} 秒 ±10%（素材长度限制）")
    return warnings


def build_timeline(choices: list[Choice], template: Template, rng: random.Random,
                   end_card: bool | None = None) -> Timeline:
    if end_card is None:
        end_card = bool(template.brand.get("end_card", True))
    trans = template.transition_duration
    if choices:
        shortest = min(c.dur for c in choices)
        trans = min(trans, round(shortest * 0.4, 3))
    warnings = fit_durations(choices, template, trans, end_card)
    for c in choices:
        place(c, rng)
    durs = [c.dur for c in choices] + ([END_CARD_SECONDS] if end_card else [])
    starts, t = [], 0.0
    for i, d in enumerate(durs):
        starts.append(round(t, 3))
        t += d - trans
    total = round(_total(durs, trans), 3)
    ttype = template.transition_type
    kinds = []
    for _ in range(max(len(durs) - 1, 0)):
        options = XFADE_TYPES.get(ttype, ["fade"])
        kinds.append(rng.choice(options) if ttype == "random" else options[0])
    return Timeline(choices, starts, durs, kinds, trans, total, end_card, warnings)


def text_events(tl: Timeline, texts: TextPack, language: str) -> list[SubEvent]:
    """按段落时间轴放置开场标题、段落说明、语音字幕、结尾行动号召。"""
    if language == "none":
        return []
    bilingual = language == "bilingual"

    def pair(p: tuple[str, str]) -> tuple[str, str]:
        en, zh = (p + ("", ""))[:2]
        if language == "zh":
            return (zh or en), ""
        if language == "en":
            return en, ""
        return en, zh

    events: list[SubEvent] = []
    n = len(tl.choices)
    trans = tl.trans_dur
    # 段落起止
    seg_end = [tl.starts[i] + tl.durs[i] for i in range(len(tl.durs))]
    voice_spans = []
    for i, c in enumerate(tl.choices):
        lines = texts.voice.get(i)
        if not c.keep_audio or not lines:
            continue
        for ln in lines:
            s = tl.starts[i] + float(ln["start"])
            e = min(tl.starts[i] + float(ln["end"]), seg_end[i])
            en, zh = pair((ln.get("text", ""), ln.get("text_zh", "")))
            if e - s > 0.3 and (en or zh):
                events.append(SubEvent(round(s, 3), round(e, 3), en, zh, "voice"))
        voice_spans.append((tl.starts[i], seg_end[i]))

    def free(s, e):
        return all(e <= vs or s >= ve for vs, ve in voice_spans)

    hook_en, hook_zh = pair(texts.hook)
    hook_end = 0.0
    if (hook_en or hook_zh) and n:
        hook_end = min(3.0, seg_end[0] + (tl.durs[1] * 0.5 if n > 1 else 0))
        hook_end = max(hook_end, min(2.0, tl.total))
        events.append(SubEvent(0.15, round(hook_end, 3), hook_en, hook_zh, "hook"))
    first_of_section: dict[int, int] = {}
    for i, c in enumerate(tl.choices):
        first_of_section.setdefault(c.section, i)
    for sec, i in sorted(first_of_section.items()):
        en, zh = pair(texts.captions.get(sec, ("", "")))
        if not (en or zh):
            continue
        s = max(tl.starts[i] + trans, hook_end + 0.1)
        # 说明文字持续到本段落结束，最长 3.5 秒
        last = max(j for j, c in enumerate(tl.choices) if c.section == sec)
        e = min(seg_end[last] - trans, s + 3.5)
        if e - s >= 1.0 and free(s, e):
            events.append(SubEvent(round(s, 3), round(e, 3), en, zh, "caption"))
    cta_en, cta_zh = pair(texts.cta)
    if cta_en or cta_zh:
        if tl.end_card:
            s = tl.starts[-1] + trans * 0.5
        else:
            s = max(tl.total - 2.5, 0)
        events.append(SubEvent(round(s, 3), round(tl.total - 0.05, 3), cta_en, cta_zh, "cta"))
    return sorted(events, key=lambda e: e.start)
