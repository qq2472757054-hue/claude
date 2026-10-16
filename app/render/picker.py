"""素材挑选（开发文档 6.4）。

挑选逻辑与数据库读取分开：pick() 只处理内存中的候选列表，便于测试。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Iterable

from ..paths import PENDING, USED
from ..templates.schema import Section, Template

EDGE = 0.3          # 避开片段首尾各 0.3 秒
IMAGE_SECONDS = (3.0, 4.0)


@dataclass
class Candidate:
    clip_id: int
    material_id: int
    rel_path: str
    city: str
    spot: str
    type: str
    start: float
    end: float
    is_image: bool = False
    rating: int = 0
    use_count: int = 0
    status: str = PENDING
    width: int = 0
    height: int = 0
    has_audio: bool = False
    voice: list[dict] = field(default_factory=list)   # 识别出的分句（素材时间轴）
    voice_lang: str = ""

    @property
    def length(self) -> float:
        return max(self.end - self.start, 0.0)

    @property
    def usable(self) -> float:
        return max(self.length - 2 * EDGE, 0.0)

    @property
    def voice_inside(self) -> list[dict]:
        """完全落在本片段内的语句。"""
        return [s for s in self.voice if s["start"] >= self.start - 0.05 and s["end"] <= self.end + 0.05]


@dataclass
class Choice:
    section: int
    slot: int
    clip_id: int
    material_id: int
    rel_path: str
    is_image: bool
    clip_start: float
    clip_end: float
    dur: float
    keep_audio: bool
    src_start: float = 0.0
    has_audio: bool = False
    width: int = 0
    height: int = 0
    type: str = ""
    spot: str = ""
    voice: list[dict] = field(default_factory=list)    # 段内时间轴（0 起）
    voice_lang: str = ""
    fixed_duration: bool = False                       # 保留原声的按语句定长，不参与时长缩放
    win_start: float = 0.0                             # 可选取窗口（素材时间轴）
    win_end: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Choice":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class PickResult:
    choices: list[Choice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shortages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.shortages

    @property
    def clip_ids(self) -> set[int]:
        return {c.clip_id for c in self.choices}


class NotEnoughMaterial(RuntimeError):
    pass


def overlap_ratio(a: Iterable[int], b: Iterable[int]) -> float:
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _voice_window(c: Candidate, min_len: float) -> tuple[float, float, list[dict]] | None:
    """保留原声：按语句边界取一段，不在一句话中间截断；时长以语句为准（可超过段落上限）。"""
    segs = c.voice_inside
    if not segs:
        return None
    start = max(segs[0]["start"] - 0.15, c.start)
    for j in range(len(segs)):
        end = min(segs[j]["end"] + 0.25, c.end)
        if end - start >= min_len:
            return start, end, segs[:j + 1]
    return start, min(segs[-1]["end"] + 0.25, c.end), segs


def _fits(c: Candidate, sec: Section) -> bool:
    if c.is_image:
        return True
    return c.usable >= sec.seconds[0] * 0.8


def make_choice(c: Candidate, sec: Section, si: int, slot: int, rng: random.Random) -> Choice:
    lo, hi = sec.seconds
    if c.is_image:
        dur = _clamp(rng.uniform(*IMAGE_SECONDS), lo, hi)
        return Choice(si, slot, c.clip_id, c.material_id, c.rel_path, True, 0.0, 0.0, round(dur, 3),
                      False, 0.0, False, c.width, c.height, c.type, c.spot)
    keep = bool(sec.keep_audio and c.has_audio)
    if keep:
        vw = _voice_window(c, lo)
        if vw:
            start, end, segs = vw
            voice = [{"start": round(s["start"] - start, 3), "end": round(s["end"] - start, 3),
                      "text": s["text"]} for s in segs]
            return Choice(si, slot, c.clip_id, c.material_id, c.rel_path, False, c.start, c.end,
                          round(end - start, 3), True, round(start, 3), True, c.width, c.height,
                          c.type, c.spot, voice, c.voice_lang, True, start, end)
    dur = min(rng.uniform(lo, hi), c.usable)
    return Choice(si, slot, c.clip_id, c.material_id, c.rel_path, False, c.start, c.end,
                  round(dur, 3), keep, 0.0, c.has_audio, c.width, c.height, c.type, c.spot,
                  win_start=c.start + EDGE, win_end=c.end - EDGE)


def place(choice: Choice, rng: random.Random) -> None:
    """在可选窗口内随机确定起点（时长确定后调用）。"""
    if choice.is_image or choice.fixed_duration:
        return
    lo, hi = choice.win_start, choice.win_end - choice.dur
    choice.src_start = round(rng.uniform(lo, hi) if hi > lo else max(lo, choice.clip_start), 3)


def pick(template: Template, city: str, candidates: list[Candidate], rng: random.Random,
         prior: list[set[int]] | None = None, max_overlap: float = 0.3,
         exclude: set[int] | None = None) -> PickResult:
    """为模板的每个段落挑选片段。

    prior：同一批次中已生成视频所用的片段集合，用于控制重合率。
    """
    prior = [set(p) for p in (prior or []) if p]
    exclude = set(exclude or ())
    res = PickResult()
    total_slots = sum(s.count for s in template.sections)
    limit = math.floor(max_overlap * total_slots + 1e-9)
    overlap_cnt = [0] * len(prior)
    in_video: set[int] = set()
    mats_in_video: set[int] = set()
    pool = [c for c in candidates if c.city == city and c.clip_id not in exclude]
    all_types = {c.type for c in pool}
    warned = set()

    def rank(c: Candidate):
        return (c.status != PENDING, c.material_id in mats_in_video, -int(c.rating or 0),
                int(c.use_count or 0), rng.random())

    for si, sec in enumerate(template.sections):
        types = set(sec.types)
        spots = set(sec.spots)
        missing = 0
        for slot in range(sec.count):
            levels = [("strict", types, spots)]
            if spots:
                levels.append(("spot", types, set()))
            levels_type = ("type", all_types, set())

            def matches(c: Candidate, ts, sp) -> bool:
                if c.clip_id in in_video or c.type not in ts:
                    return False
                if sp and c.spot not in sp:
                    return False
                return _fits(c, sec)

            def tiers(ts, sp):
                base = [c for c in pool if matches(c, ts, sp)]
                if sec.keep_audio:  # 有识别文本的优先
                    base.sort(key=lambda c: (not c.voice_inside, not c.has_audio))
                t1 = [c for c in base if not any(c.clip_id in p for p in prior)]
                t2 = [c for c in base if c not in t1 and all(
                    overlap_cnt[i] + 1 <= limit for i, p in enumerate(prior) if c.clip_id in p)]
                t3 = [c for c in base if c not in t1 and c not in t2]
                return t1, t2, t3

            order = []
            for name, ts, sp in levels:
                t1, t2, t3 = tiers(ts, sp)
                order += [(name, 1, t1), (name, 2, t2)]
            for name, ts, sp in levels:
                order.append((name, 3, tiers(ts, sp)[2]))
            t1, t2, t3 = tiers(levels_type[1], levels_type[2])
            order += [("type", 1, t1), ("type", 2, t2), ("type", 3, t3)]

            chosen = None
            for name, tier, cands in order:
                if not cands:
                    continue
                if sec.keep_audio:
                    best_key = (not cands[0].voice_inside, not cands[0].has_audio)
                    cands = [c for c in cands if (not c.voice_inside, not c.has_audio) == best_key]
                chosen = min(cands, key=rank)
                if name == "spot" and ("spot", si) not in warned:
                    res.warnings.append(f"“{sec.name}”指定景点的素材不够，已放宽景点条件")
                    warned.add(("spot", si))
                if name == "type" and ("type", si) not in warned:
                    res.warnings.append(f"“{sec.name}”{'/'.join(sec.types)}素材不够，已用其他类型素材补足")
                    warned.add(("type", si))
                if tier == 3 and "overlap" not in warned:
                    res.warnings.append(f"素材不够多，本条与同批次其他视频的素材重合率超过 {int(max_overlap * 100)}%")
                    warned.add("overlap")
                break
            if chosen is None:
                missing += 1
                continue
            for i, p in enumerate(prior):
                if chosen.clip_id in p:
                    overlap_cnt[i] += 1
            in_video.add(chosen.clip_id)
            mats_in_video.add(chosen.material_id)
            res.choices.append(make_choice(chosen, sec, si, slot, rng))
        if missing:
            res.shortages.append(f"{city}·{'/'.join(sec.types)}素材不足，还差 {missing} 段")
    return res


def shortage_message(res: PickResult) -> str:
    return "；".join(res.shortages) + "。请补充素材或修改模板。"


# ---------- 从数据库加载候选 ----------

def load_candidates(ctx, city: str, allow_used: bool, batch_id: str = "") -> list[Candidate]:
    """候选范围：能否公开使用=是；不是低质量（或已手动恢复）；文件可读；位于 待使用/（或按设置允许 已使用/）。

    同一批次中由前面任务移到 已使用/ 的素材，批次内仍可使用（参与重合率控制）。
    """
    rows = ctx.db.all(
        """SELECT c.id AS clip_id, c.start, c."end", c.use_count AS clip_use, c.low_quality,
                  c.manual_restore, m.id AS material_id, m.rel_path, m.city, m.spot, m.type,
                  m.is_image, m.rating, m.status, m.width, m.height, m.has_audio, m.publishable,
                  m.broken
           FROM clip c JOIN material m ON m.id = c.material_id
           WHERE m.city = ? AND m.publishable = '是' AND m.broken = 0
             AND m.analysis_status = '已分析'
             AND (c.low_quality = 0 OR c.manual_restore = 1)""", (city,))
    batch_mats: set[int] = set()
    if batch_id:
        batch_mats = {r["material_id"] for r in ctx.db.all(
            "SELECT DISTINCT u.material_id FROM usage u JOIN job j ON j.id = u.job_id "
            "WHERE j.batch_id = ?", (batch_id,))}
    transcripts = {}
    for t in ctx.db.all("SELECT material_id, language, segments_json, edited_text_json FROM transcript"):
        segs = t["edited_text_json"] or t["segments_json"] or "[]"
        try:
            transcripts[t["material_id"]] = (t["language"], json.loads(segs))
        except json.JSONDecodeError:
            pass
    out = []
    for r in rows:
        if r["status"] == USED and not (allow_used or r["material_id"] in batch_mats):
            continue
        if r["status"] not in (PENDING, USED):
            continue
        if not ctx.lib.abs(r["rel_path"]).exists():
            continue
        lang, segs = transcripts.get(r["material_id"], ("", []))
        out.append(Candidate(
            clip_id=r["clip_id"], material_id=r["material_id"], rel_path=r["rel_path"],
            city=r["city"], spot=r["spot"] or "", type=r["type"] or "", start=r["start"],
            end=r["end"], is_image=bool(r["is_image"]), rating=r["rating"] or 0,
            use_count=r["clip_use"] or 0,
            status=PENDING if r["material_id"] in batch_mats else r["status"],
            width=r["width"], height=r["height"], has_audio=bool(r["has_audio"]),
            voice=segs, voice_lang=lang))
    return out
