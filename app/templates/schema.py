"""主题模板：数据结构、校验、YAML 读写。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
SUBTITLE_LANGS = {"en": "英文", "zh": "中文", "bilingual": "中英双行", "none": "无"}
TRANSITIONS = {"none": "无", "cut": "硬切", "fade": "淡入淡出", "slide": "滑动", "random": "随机"}
POSITIONS = {"bottom": "底部", "middle": "中部", "top": "顶部"}
FILL_MODES = {"crop": "裁切填满", "blur": "模糊背景填充"}


class TemplateError(ValueError):
    pass


@dataclass
class Section:
    name: str = "段落"
    types: list[str] = field(default_factory=list)
    spots: list[str] = field(default_factory=list)
    count: int = 1
    seconds: list[float] = field(default_factory=lambda: [2.5, 3.5])
    keep_audio: bool = False
    caption: str = ""
    caption_zh: str = ""


@dataclass
class SubtitleStyle:
    font: str = "Noto Sans SC"
    font_size: int = 64
    font_size_zh: int = 56
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: int = 4
    position: str = "bottom"
    box: bool = False


@dataclass
class Template:
    name: str = "新模板"
    cities: list[str] = field(default_factory=list)
    aspects: list[str] = field(default_factory=lambda: ["9:16"])
    duration: float = 30
    subtitle_language: str = "en"
    transition: dict = field(default_factory=lambda: {"type": "fade", "duration": 0.4})
    fill_mode: str = "blur"
    music: dict = field(default_factory=lambda: {"style": "轻快", "track": "", "volume": 0.6})
    brand: dict = field(default_factory=lambda: {"logo_corner": True, "end_card": True})
    text: dict = field(default_factory=lambda: {"hook": "", "hook_zh": "", "cta": "", "cta_zh": "",
                                                "use_llm": False})
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    sections: list[Section] = field(default_factory=list)
    cover: dict = field(default_factory=lambda: {"enabled": True, "title": ""})
    color_grade: bool = False
    file_path: str = ""          # 运行时字段，不写回 YAML

    # ---------- 序列化 ----------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Template":
        d = dict(d or {})
        t = cls()
        for k in ("name", "duration", "subtitle_language", "fill_mode", "color_grade"):
            if k in d:
                setattr(t, k, d[k])
        if "cities" in d:
            t.cities = _as_list(d["cities"])
        if "aspects" in d:
            t.aspects = [str(a) for a in _as_list(d["aspects"])]
        for k in ("transition", "music", "brand", "text", "cover"):
            if isinstance(d.get(k), dict):
                merged = dict(getattr(t, k))
                merged.update(d[k])
                setattr(t, k, merged)
        if isinstance(d.get("subtitle_style"), dict):
            known = SubtitleStyle.__dataclass_fields__
            t.subtitle_style = SubtitleStyle(**{k: v for k, v in d["subtitle_style"].items() if k in known})
        secs = []
        for s in d.get("sections") or []:
            s = dict(s)
            secs.append(Section(
                name=str(s.get("name", "段落")), types=[str(x) for x in _as_list(s.get("types"))],
                spots=[str(x) for x in _as_list(s.get("spots") or s.get("spot"))],
                count=int(s.get("count", 1)),
                seconds=[float(x) for x in _as_list(s.get("seconds", [2.5, 3.5]))],
                keep_audio=bool(s.get("keep_audio", False)), caption=str(s.get("caption", "") or ""),
                caption_zh=str(s.get("caption_zh", "") or ""),
            ))
        t.sections = secs
        return t

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("file_path", None)
        for s in d["sections"]:
            if not s["spots"]:
                s.pop("spots")
            for k in ("caption", "caption_zh"):
                if not s[k]:
                    s.pop(k)
            if not s["keep_audio"]:
                s.pop("keep_audio")
        return d

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    # ---------- 校验 ----------
    def validate(self) -> list[str]:
        errs = []
        if not str(self.name).strip():
            errs.append("模板名称不能为空")
        for a in self.aspects:
            if a not in ASPECTS:
                errs.append(f"不支持的输出比例：{a}")
        if not self.aspects:
            errs.append("至少选择一个输出比例")
        try:
            if float(self.duration) <= 3:
                errs.append("目标时长太短")
        except (TypeError, ValueError):
            errs.append("目标时长必须是数字")
        if self.subtitle_language not in SUBTITLE_LANGS:
            errs.append(f"字幕语言无效：{self.subtitle_language}")
        if self.fill_mode not in FILL_MODES:
            errs.append(f"画面填充方式无效：{self.fill_mode}")
        if self.transition.get("type") not in TRANSITIONS:
            errs.append(f"转场类型无效：{self.transition.get('type')}")
        if self.subtitle_style.position not in POSITIONS:
            errs.append("字幕位置无效")
        if not self.sections:
            errs.append("至少需要一个段落")
        for i, s in enumerate(self.sections, 1):
            if not s.types:
                errs.append(f"第{i}段（{s.name}）没有选择素材类型")
            if s.count < 1:
                errs.append(f"第{i}段（{s.name}）段数至少为 1")
            if len(s.seconds) != 2 or s.seconds[0] <= 0 or s.seconds[0] > s.seconds[1]:
                errs.append(f"第{i}段（{s.name}）时长范围无效")
        return errs

    @property
    def transition_type(self) -> str:
        return self.transition.get("type", "fade")

    @property
    def transition_duration(self) -> float:
        if self.transition_type in ("none", "cut"):
            return 0.0
        return float(self.transition.get("duration", 0.4))

    @property
    def short_name(self) -> str:
        """文件命名用：去掉城市名和空格，如 “张家界 48 小时” → “48小时”。"""
        n = str(self.name)
        for c in self.cities:
            n = n.replace(c, "")
        n = re.sub(r"\s+", "", n)
        n = re.sub(r'[\\/:*?"<>|]', "", n)
        return n or re.sub(r"\s+", "", str(self.name)) or "模板"


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def load(path: str | Path) -> Template:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise TemplateError(f"模板文件格式错误：{path.name}（{e}）") from e
    t = Template.from_dict(data)
    t.file_path = str(path)
    return t


def save(t: Template, path: str | Path) -> Path:
    errs = t.validate()
    if errs:
        raise TemplateError("；".join(errs))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(t.to_yaml(), encoding="utf-8")
    t.file_path = str(path)
    return path


def safe_filename(name: str) -> str:
    n = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return n or "模板"


def list_templates(templates_dir: str | Path) -> list[Template]:
    out = []
    for p in sorted(Path(templates_dir).glob("*.yaml")):
        try:
            out.append(load(p))
        except TemplateError:
            continue
    return out


def sync_index(ctx, templates: list[Template]) -> None:
    """数据库中只缓存模板索引（YAML 文件为准）。"""
    from ..db import now

    paths = set()
    for t in templates:
        rel = ctx.lib.rel(t.file_path)
        paths.add(rel)
        row = ctx.db.one("SELECT id FROM template WHERE file_path = ?", (rel,))
        if row:
            ctx.db.update("template", row["id"], {"name": t.name, "updated_at": now()})
        else:
            ctx.db.insert("template", {"name": t.name, "file_path": rel, "updated_at": now()})
