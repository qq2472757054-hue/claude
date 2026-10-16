"""DeepSeek（OpenAI 兼容接口）：文案、话题标签、字幕翻译。

未启用或调用失败时返回 None，调用方使用模板中人工填写的文字，不影响视频生成。
"""
from __future__ import annotations

import datetime as _dt
import json
import re

from .. import config, logs
from ..db import now
from ..paths import PROMPTS_DIR

log = logs.get("llm")

LANG_NAMES = {"en": "英文", "zh": "简体中文"}


def enabled(settings: dict | None = None) -> bool:
    s = (settings or config.load()).get("llm", {})
    return bool(s.get("enabled") and s.get("api_key"))


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _client(settings: dict):
    from openai import OpenAI

    s = settings["llm"]
    return OpenAI(api_key=s["api_key"], base_url=s.get("base_url") or "https://api.deepseek.com",
                  timeout=60, max_retries=1)


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def chat_json(ctx, purpose: str, prompt: str, settings: dict | None = None) -> dict | None:
    settings = settings or config.load()
    if not enabled(settings):
        return None
    try:
        client = _client(settings)
        resp = client.chat.completions.create(
            model=settings["llm"].get("model") or "deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        usage = getattr(resp, "usage", None)
        if ctx is not None and usage is not None:
            ctx.db.insert("llm_usage", {"purpose": purpose,
                                        "prompt_tokens": usage.prompt_tokens or 0,
                                        "completion_tokens": usage.completion_tokens or 0,
                                        "created_at": now()})
        return _parse_json(resp.choices[0].message.content or "")
    except Exception as e:
        log.warning("DeepSeek 调用失败（%s）：%s", purpose, e)
        return None


def generate_copy(ctx, *, brand: str, city: str, template: str, sections: list[str], spots: list[str],
                  voice: list[str], language: str) -> dict | None:
    prompt = load_prompt("copy").format(
        brand=brand, city=city, template=template, sections="；".join(sections) or "无",
        spots="、".join(spots) or "未指定", voice=" / ".join(voice)[:1500] or "无",
        language=LANG_NAMES.get(language, "英文"))
    data = chat_json(ctx, "copy", prompt)
    if not data:
        return None
    caps = data.get("captions") or []
    return {
        "hook": str(data.get("hook") or "").strip(),
        "captions": [str(c).strip() for c in caps] if isinstance(caps, list) else [],
        "cta": str(data.get("cta") or "").strip(),
        "post": str(data.get("post") or "").strip(),
        "hashtags": [str(h).strip().lstrip("#") for h in (data.get("hashtags") or []) if str(h).strip()],
    }


def translate(ctx, items: list[str], language: str) -> list[str] | None:
    if not items:
        return []
    prompt = load_prompt("translate").format(language=LANG_NAMES.get(language, "英文"),
                                             items=json.dumps(items, ensure_ascii=False))
    data = chat_json(ctx, "translate", prompt)
    if not data or not isinstance(data.get("items"), list) or len(data["items"]) != len(items):
        return None
    return [str(x).strip() for x in data["items"]]


def month_usage(ctx) -> dict:
    start = _dt.date.today().replace(day=1).strftime("%Y-%m-%d")
    row = ctx.db.one("SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS p, "
                     "COALESCE(SUM(completion_tokens),0) AS c FROM llm_usage WHERE created_at >= ?",
                     (start,))
    return {"calls": row["calls"], "prompt_tokens": row["p"], "completion_tokens": row["c"]}


def test_connection(settings: dict) -> str:
    if not settings.get("llm", {}).get("api_key"):
        return "请先填写 API Key。"
    try:
        client = _client(settings)
        client.chat.completions.create(model=settings["llm"].get("model") or "deepseek-chat",
                                       messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        return "连接成功。"
    except Exception as e:
        return f"连接失败：{e}"
