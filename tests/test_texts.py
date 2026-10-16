"""文字准备：DeepSeek 开/关、失败兜底、字幕翻译（DeepSeek 调用用假函数代替）。"""
import random

from app.jobs import runner
from app.llm import deepseek
from app.render.picker import Choice
from app.render.planner import build_timeline, text_events
from app.templates.schema import Section, Template

SETTINGS = {"brand": {"name_en": "Coolplay Vacation", "name_zh": "酷玩假期"}, "llm": {"enabled": False}}


def make(lang="en", use_llm=False):
    t = Template(name="T", cities=["张家界"], duration=10, subtitle_language=lang,
                 text={"hook": "Hi", "hook_zh": "你好", "cta": "DM us", "cta_zh": "", "use_llm": use_llm},
                 sections=[Section("a", ["景点"], 1, [3, 4], caption="cap"),
                           Section("g", ["导游出镜"], 1, [3, 5], keep_audio=True)])
    chs = [Choice(0, 0, 1, 1, "x.mp4", False, 0, 6, 3.5, False, win_start=0.3, win_end=5.7),
           Choice(1, 0, 2, 2, "g.mp4", False, 0, 6, 4.0, True, 0.5, True, voice=[
               {"start": 0.2, "end": 3.0, "text": "大家好"}], voice_lang="zh", fixed_duration=True)]
    return t, build_timeline(chs, t, random.Random(1))


def test_without_llm_keeps_original_and_flags_untranslated():
    t, tl = make("en")
    tp, meta = runner.prepare_texts(None, t, tl, "张家界", {}, SETTINGS)
    assert tp.hook == ("Hi", "你好")
    assert tp.voice[1][0]["text"] == "大家好"   # 未翻译，保留原文
    assert tp.untranslated and any("未翻译" in n for n in meta["notes"])
    assert "#" not in meta["hashtags"][0] and "Zhangjiajie" in meta["hashtags"]
    evs = text_events(tl, tp, "en")
    assert [e.kind for e in evs].count("voice") == 1


def test_with_llm(monkeypatch):
    t, tl = make("bilingual", use_llm=True)
    monkeypatch.setattr(deepseek, "enabled", lambda s=None: True)
    monkeypatch.setattr(deepseek, "generate_copy", lambda ctx, **kw: {
        "hook": "AI hook", "captions": ["AI cap", "AI cap2"], "cta": "AI cta", "post": "Post!", "hashtags": ["a", "#b"]})
    monkeypatch.setattr(deepseek, "translate", lambda ctx, items, lang: [f"[{lang}]{x}" for x in items])
    tp, meta = runner.prepare_texts(None, t, tl, "张家界", {}, SETTINGS)
    assert tp.hook == ("AI hook", "[zh]AI hook")
    assert tp.captions[0] == ("AI cap", "[zh]AI cap")
    assert tp.voice[1][0] == {"start": 0.2, "end": 3.0, "text": "[en]大家好", "text_zh": "大家好"}
    assert not tp.untranslated
    assert meta["post"] == "Post!" and meta["hashtags"] == ["a", "b"]


def test_llm_failure_falls_back(monkeypatch):
    t, tl = make("en", use_llm=True)
    monkeypatch.setattr(deepseek, "enabled", lambda s=None: True)
    monkeypatch.setattr(deepseek, "generate_copy", lambda ctx, **kw: None)
    monkeypatch.setattr(deepseek, "translate", lambda ctx, items, lang: None)
    tp, meta = runner.prepare_texts(None, t, tl, "张家界", {}, SETTINGS)
    assert tp.hook[0] == "Hi" and tp.cta[0] == "DM us"
    assert any("调用失败" in n for n in meta["notes"])


def test_manual_override_wins():
    t, tl = make("en", use_llm=True)
    ov = {"text": {"hook": ["Edited", ""], "voice": {"1": [{"start": 0.2, "end": 3.0, "text": "Hello", "text_zh": ""}]},
                   "hashtags": ["x"]}}
    tp, meta = runner.prepare_texts(None, t, tl, "张家界", ov, SETTINGS)
    assert tp.hook[0] == "Edited" and tp.voice[1][0]["text"] == "Hello" and not tp.untranslated
    assert meta["hashtags"] == ["x"]
