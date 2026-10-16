"""字幕：自动换行、ASS（烧录）与 SRT（发布包）生成。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from ..templates.schema import SubtitleStyle

# 每行最大“宽度单位”：英文字符记 1，中文（全角）字符记 2
MAX_UNITS = {"9:16": 32, "4:5": 34, "1:1": 36, "16:9": 56}
MAX_LINES = 2

_CLOSING_PUNCT = set("，。、；：！？）》」』】,.;:!?)]}%…”’")
_TOKEN_RE = re.compile(r"\s+|[A-Za-z0-9À-ɏ'’\-_./:@#&+%$]+|.", re.S)


def char_units(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_units(s: str) -> int:
    return sum(char_units(c) for c in s)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def wrap_lines(text: str, max_units: int, measure: Callable[[str], float] | None = None,
               max_px: float | None = None) -> list[str]:
    """按宽度换行（不限行数）。英文按单词断行，中文按字断行，标点不放行首。"""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []

    def fits(s: str) -> bool:
        if text_units(s) > max_units:
            return False
        if measure is not None and max_px is not None and measure(s) > max_px:
            return False
        return True

    lines: list[str] = []
    cur = ""
    for tok in _tokens(text):
        if tok.isspace():
            if cur:
                cur += " "
            continue
        if fits((cur + tok).strip()):
            cur += tok
            continue
        if len(tok) == 1 and tok in _CLOSING_PUNCT and cur.strip():
            # 标点不放行首：跟在上一行末尾
            lines.append(cur.rstrip() + tok)
            cur = ""
            continue
        if cur.strip():
            lines.append(cur.strip())
        cur = ""
        if fits(tok):
            cur = tok
            continue
        for ch in tok:  # 单个超长“词”：按字符硬切
            if cur and not fits(cur + ch):
                lines.append(cur)
                cur = ""
            cur += ch
    if cur.strip():
        lines.append(cur.strip())
    return lines


def chunk_text(text: str, max_units: int, max_lines: int = MAX_LINES,
               measure: Callable[[str], float] | None = None, max_px: float | None = None) -> list[list[str]]:
    """把文本切成若干屏，每屏最多 max_lines 行。"""
    lines = wrap_lines(text, max_units, measure, max_px)
    return [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]


def split_proportional(texts: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """按文字长度把 [start, end] 分给各屏。"""
    weights = [max(text_units(t), 1) for t in texts]
    total = sum(weights)
    out = []
    t = start
    for w in weights:
        d = (end - start) * w / total
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    return out


@dataclass
class SubEvent:
    start: float
    end: float
    text: str = ""          # 主语言（英文 / 中文 / 双语时为英文）
    text_zh: str = ""       # 双语时的中文行
    kind: str = "caption"   # hook / caption / voice / cta


@dataclass
class Screen:
    start: float
    end: float
    lines: list[str]
    lines_zh: list[str] = field(default_factory=list)
    kind: str = "caption"


def layout_events(events: list[SubEvent], aspect: str, bilingual: bool,
                  measure_en: Callable[[str], float] | None = None,
                  measure_zh: Callable[[str], float] | None = None,
                  max_px: float | None = None) -> list[Screen]:
    """换行并切屏。双语模式下每种语言每屏一行（合计两行）。"""
    max_units = MAX_UNITS.get(aspect, 32)
    screens: list[Screen] = []
    for ev in sorted(events, key=lambda e: e.start):
        if ev.end - ev.start < 0.2:
            continue
        if bilingual and ev.text and ev.text_zh:
            en = wrap_lines(ev.text, max_units, measure_en, max_px)
            zh = wrap_lines(ev.text_zh, max_units, measure_zh, max_px)
            n = max(len(en), len(zh))
            en_groups = _spread(en, n)
            zh_groups = _spread(zh, n)
            spans = split_proportional([" ".join(g) or "x" for g in en_groups], ev.start, ev.end)
            for (s, e), g_en, g_zh in zip(spans, en_groups, zh_groups):
                screens.append(Screen(s, e, [" ".join(g_en)] if g_en else [],
                                      ["".join(g_zh)] if g_zh else [], ev.kind))
        else:
            text = ev.text or ev.text_zh
            measure = measure_zh if (not ev.text and ev.text_zh) else measure_en
            chunks = chunk_text(text, max_units, MAX_LINES, measure, max_px)
            if not chunks:
                continue
            spans = split_proportional([" ".join(c) for c in chunks], ev.start, ev.end)
            for (s, e), c in zip(spans, chunks):
                screens.append(Screen(s, e, c, [], ev.kind))
    return screens


def _spread(lines: list[str], n: int) -> list[list[str]]:
    """把 lines 分成 n 组（尽量均匀，保持顺序）。"""
    if n <= 0:
        return []
    groups: list[list[str]] = [[] for _ in range(n)]
    if not lines:
        return groups
    for i, line in enumerate(lines):
        groups[min(i * n // len(lines), n - 1)].append(line)
    return groups


# ---------- 安全区 ----------

def safe_margins(w: int, h: int, aspect: str) -> dict[str, int]:
    """竖屏：底部预留约 15% 高度、右侧约 12% 宽度，避开平台按钮。"""
    if aspect == "9:16":
        return {"l": round(w * 0.06), "r": round(w * 0.12), "bottom": round(h * 0.15) + 20,
                "top": round(h * 0.12)}
    if aspect == "4:5":
        return {"l": round(w * 0.06), "r": round(w * 0.10), "bottom": round(h * 0.12), "top": round(h * 0.08)}
    return {"l": round(w * 0.06), "r": round(w * 0.06), "bottom": round(h * 0.08), "top": round(h * 0.08)}


def max_text_px(w: int, aspect: str) -> float:
    m = safe_margins(w, 1, aspect)
    return w - m["l"] - m["r"]


# ---------- ASS ----------

def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(t, 0.0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ")


def build_ass(screens: list[Screen], w: int, h: int, aspect: str, style: SubtitleStyle) -> str:
    m = safe_margins(w, h, aspect)
    scale = w / 1080 if aspect == "16:9" else 1.0
    fs = max(int(style.font_size * scale), 20)
    fs_zh = max(int(style.font_size_zh * scale), 18)
    primary = _ass_color(style.color)
    outline_c = _ass_color(style.outline_color)
    if style.box:
        border_style, outline_c, back = 3, _ass_color("#000000", 0x60), _ass_color("#000000", 0x60)
        outline = max(int(style.outline), 8)
    else:
        border_style, back = 1, _ass_color("#000000", 0x80)
        outline = int(style.outline)
    pos_align = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    pos_margin_v = {"bottom": m["bottom"], "middle": 0, "top": m["top"]}[style.position]

    def style_line(name, size, align, margin_v, bold=-1):
        return (f"Style: {name},{style.font},{size},{primary},{primary},{outline_c},{back},"
                f"{bold},0,0,0,100,100,0,0,{border_style},{outline},1,{align},"
                f"{m['l']},{m['r']},{margin_v},1")

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line("Caption", fs, pos_align, pos_margin_v),
        style_line("Voice", fs, 2, m["bottom"]),
        style_line("Hook", int(fs * 1.3), 5, 0),
        style_line("CTA", int(fs * 1.1), 2, m["bottom"] + int(h * 0.03)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    style_of = {"hook": "Hook", "cta": "CTA", "voice": "Voice", "caption": "Caption"}
    for sc in screens:
        st = style_of.get(sc.kind, "Caption")
        parts = [_ass_escape(x) for x in sc.lines]
        text = r"\N".join(parts)
        if sc.lines_zh:
            zh_size = int(fs_zh * (1.3 if st == "Hook" else 1.1 if st == "CTA" else 1.0))
            zh = r"\N".join(_ass_escape(x) for x in sc.lines_zh)
            text = (text + r"\N" if text else "") + f"{{\\fs{zh_size}}}" + zh
        fade = r"{\fad(150,150)}"
        lines.append(f"Dialogue: 0,{_ass_time(sc.start)},{_ass_time(sc.end)},{st},,0,0,0,,{fade}{text}")
    return "\n".join(lines) + "\n"


# ---------- SRT ----------

def _srt_time(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(screens: list[Screen]) -> str:
    out = []
    for i, sc in enumerate(screens, 1):
        text = "\n".join(sc.lines + sc.lines_zh)
        out.append(f"{i}\n{_srt_time(sc.start)} --> {_srt_time(sc.end)}\n{text}\n")
    return "\n".join(out)


def font_measurer(font_path: str | None, size: int) -> Callable[[str], float] | None:
    """用实际字体测量像素宽度（比 libass 的实际渲染略保守）。"""
    if not font_path:
        return None
    try:
        from PIL import ImageFont

        f = ImageFont.truetype(font_path, size)
        return lambda s: f.getlength(s)
    except Exception:
        return None
