"""执行一个生成任务。

素材流转保证：渲染全部在 临时/job_<id>/ 中进行；只有所有比例的输出都通过校验、
放进 输出/待审核/ 之后，才移动素材。任何失败或取消都不动素材。
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import shutil
import threading
from pathlib import Path

from .. import logs
from ..db import now
from ..ffrun import Cancelled, FFmpegError
from ..library import mover
from ..llm import deepseek
from ..render import audio as audio_mod
from ..render import cover as cover_mod
from ..render import imgfx
from ..render.engine import aspect_tag, render_aspect
from ..render.ffmpeg_cmd import detect_hw_encoder
from ..render.picker import Choice, load_candidates, pick, shortage_message
from ..render.planner import TextPack, Timeline, build_timeline
from ..render.verify import verify_output
from ..templates import schema
from ..templates.schema import ASPECTS

log = logs.get("runner")

CITY_EN = {"重庆": "Chongqing", "成都": "Chengdu", "张家界": "Zhangjiajie", "北京": "Beijing",
           "上海": "Shanghai", "西安": "Xian", "桂林": "Guilin", "杭州": "Hangzhou"}


class JobError(RuntimeError):
    """给使用者看的错误（中文、可操作）。"""


def friendly_error(e: BaseException) -> str:
    if isinstance(e, JobError):
        return str(e)
    if isinstance(e, FFmpegError):
        return "视频编码失败（素材文件可能损坏或格式特殊）。详细信息已写入日志，可点“重试”或更换素材。"
    if isinstance(e, FileNotFoundError):
        return f"找不到文件：{getattr(e, 'filename', '') or e}。请检查素材是否被移动或删除。"
    if isinstance(e, schema.TemplateError):
        return f"模板有问题：{e}"
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "磁盘空间不足，请清理磁盘后重试。"
    return f"生成失败：{e}"


# ---------- 文字准备 ----------

def _voice_sources(tl: Timeline) -> list[tuple[int, dict]]:
    out = []
    for i, c in enumerate(tl.choices):
        if c.keep_audio:
            for ln in c.voice:
                out.append((i, ln))
    return out


def prepare_texts(ctx, template: schema.Template, tl: Timeline, city: str, overrides: dict,
                  settings: dict) -> tuple[TextPack, dict]:
    """返回 (字幕文字, 发布信息{title, post, hashtags, notes})。"""
    lang = template.subtitle_language
    t = template.text
    tp = TextPack(hook=(t.get("hook", ""), t.get("hook_zh", "")),
                  cta=(t.get("cta", ""), t.get("cta_zh", "")),
                  captions={i: (s.caption, s.caption_zh) for i, s in enumerate(template.sections)})
    meta = {"title": template.cover.get("title") or t.get("hook", ""), "post": "", "hashtags": [],
            "notes": []}
    llm_on = deepseek.enabled(settings)
    want_llm = bool(t.get("use_llm")) and llm_on and not overrides.get("text")

    if want_llm:
        main_lang = "zh" if lang == "zh" else "en"
        spots = sorted({c.spot for c in tl.choices if c.spot})
        voice_txt = [ln["text"] for _i, ln in _voice_sources(tl)]
        gen = deepseek.generate_copy(
            ctx, brand=settings["brand"].get("name_en") or settings["brand"].get("name_zh"),
            city=city, template=template.name,
            sections=[f"{s.name}（{'/'.join(s.types)}）" for s in template.sections],
            spots=spots, voice=voice_txt, language=main_lang)
        if gen:
            slot = 1 if main_lang == "zh" else 0

            def put(pair, val):
                if not val:
                    return pair
                p = list(pair)
                p[slot] = val
                if lang == "bilingual":
                    p[1 - slot] = ""     # 另一种语言稍后翻译
                return tuple(p)

            tp.hook = put(tp.hook, gen["hook"])
            tp.cta = put(tp.cta, gen["cta"])
            for i, cap in enumerate(gen["captions"][:len(template.sections)]):
                tp.captions[i] = put(tp.captions.get(i, ("", "")), cap)
            meta["post"] = gen["post"]
            meta["hashtags"] = gen["hashtags"]
            if not template.cover.get("title") and gen["hook"]:
                meta["title"] = gen["hook"]
            meta["notes"].append("标题和文案由 DeepSeek 生成，请人工检查")
        else:
            meta["notes"].append("DeepSeek 调用失败，已使用模板文字")

    # 人工修改（审核界面“编辑后重新生成”）
    ov = overrides.get("text") or {}
    if "hook" in ov:
        tp.hook = tuple(ov["hook"])
    if "cta" in ov:
        tp.cta = tuple(ov["cta"])
    for k, v in (ov.get("captions") or {}).items():
        tp.captions[int(k)] = tuple(v)
    for k in ("title", "post", "hashtags"):
        if k in ov:
            meta[k] = ov[k]

    # 双语：缺的一种语言用 DeepSeek 翻译
    if lang == "bilingual" and llm_on:
        pairs = [("hook", None), ("cta", None)] + [("cap", i) for i in tp.captions]
        need_zh = [(k, i) for k, i in pairs if _get(tp, k, i)[0] and not _get(tp, k, i)[1]]
        need_en = [(k, i) for k, i in pairs if _get(tp, k, i)[1] and not _get(tp, k, i)[0]]
        for need, src_slot, target in ((need_zh, 0, "zh"), (need_en, 1, "en")):
            if not need:
                continue
            tr = deepseek.translate(ctx, [_get(tp, k, i)[src_slot] for k, i in need], target)
            if tr:
                for (k, i), txt in zip(need, tr):
                    p = list(_get(tp, k, i))
                    p[1 - src_slot] = txt
                    _set(tp, k, i, tuple(p))

    # 语音字幕
    voice_ov = {int(k): v for k, v in (ov.get("voice") or {}).items()}
    sources = _voice_sources(tl)
    by_choice: dict[int, list[dict]] = {}
    to_translate: dict[str, list[tuple[int, int, str]]] = {"en": [], "zh": []}
    for i, ln in sources:
        src_lang = tl.choices[i].voice_lang or ""
        entry = {"start": ln["start"], "end": ln["end"], "text": "", "text_zh": ""}
        if src_lang == "zh":
            entry["text_zh"] = ln["text"]
        else:
            entry["text"] = ln["text"]
        by_choice.setdefault(i, []).append(entry)
        k = len(by_choice[i]) - 1
        needs = {"en": ["en"], "zh": ["zh"], "bilingual": ["en", "zh"], "none": []}[lang]
        for target in needs:
            have = entry["text"] if target == "en" else entry["text_zh"]
            if not have:
                to_translate[target].append((i, k, ln["text"]))
    untranslated = False
    for target, items in to_translate.items():
        if not items:
            continue
        tr = deepseek.translate(ctx, [x[2] for x in items], target) if llm_on else None
        if tr is None:
            untranslated = True
            for i, k, orig in items:   # 未翻译：保留原文
                e = by_choice[i][k]
                if lang != "bilingual":
                    e["text" if target == "en" else "text_zh"] = orig
        else:
            for (i, k, _o), txt in zip(items, tr):
                by_choice[i][k]["text" if target == "en" else "text_zh"] = txt
    by_choice.update(voice_ov)
    tp.voice = by_choice
    tp.untranslated = untranslated and not voice_ov
    if tp.untranslated:
        meta["notes"].append("字幕未翻译（原声语言与字幕语言不一致，未开启 DeepSeek）")

    # 发布文案兜底
    if not meta["post"]:
        hook = tp.hook[0] or tp.hook[1]
        cta = tp.cta[0] or tp.cta[1]
        meta["post"] = "\n\n".join(x for x in (hook, cta) if x)
    if not meta["hashtags"]:
        brand_tag = "".join((settings["brand"].get("name_en") or "").split())
        tags = [CITY_EN.get(city, city), "ChinaTravel", "TravelChina", "VisitChina"]
        tags += sorted({c.spot for c in tl.choices if c.spot and c.spot.isascii()})
        if brand_tag:
            tags.append(brand_tag)
        meta["hashtags"] = [x.replace(" ", "") for x in tags if x]
    meta["hashtags"] = [h.strip().lstrip("#") for h in meta["hashtags"] if h.strip().lstrip("#")]
    return tp, meta


def _get(tp: TextPack, k: str, i):
    return {"hook": tp.hook, "cta": tp.cta}.get(k) if k != "cap" else tp.captions.get(i, ("", ""))


def _set(tp: TextPack, k: str, i, v):
    if k == "hook":
        tp.hook = v
    elif k == "cta":
        tp.cta = v
    else:
        tp.captions[i] = v


# ---------- 命名 ----------

def next_name(ctx, city: str, template: schema.Template, aspect: str, date: str | None = None) -> str:
    date = date or _dt.date.today().strftime("%Y%m%d")
    prefix = f"{date}_{city}_{template.short_name}_{aspect_tag(aspect)}_"
    taken = {r["name"] for r in ctx.db.all("SELECT name FROM output WHERE name LIKE ?", (prefix + "%",))}
    for d in (ctx.lib.out_pending, ctx.lib.out_approved, ctx.lib.out_rejected):
        if d.is_dir():
            taken |= {p.name for p in d.iterdir() if p.name.startswith(prefix)}
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


# ---------- 执行 ----------

def _set_status(ctx, job_id: int, status: str, progress: str = "") -> None:
    ctx.db.update("job", job_id, {"status": status, "progress": progress})


def _batch_prior(ctx, job: dict) -> list[set[int]]:
    if not job["batch_id"]:
        return []
    prior = []
    for j in ctx.db.all("SELECT plan_json FROM job WHERE batch_id = ? AND id != ? AND plan_json != '' "
                        "AND status IN ('完成', '渲染中', '校验中')", (job["batch_id"], job["id"])):
        try:
            plan = json.loads(j["plan_json"])
            prior.append({c["clip_id"] for c in plan["timeline"]["choices"]})
        except (json.JSONDecodeError, KeyError):
            continue
    return prior


def _pick_choices(ctx, job: dict, template, overrides: dict, rng, settings) -> tuple[list[Choice], list[str]]:
    warnings: list[str] = []
    if overrides.get("reuse_plan"):
        choices = [Choice.from_dict(c) for c in overrides["reuse_plan"]]
        for c in choices:   # 素材可能已移到 已使用/：以数据库中的当前位置为准
            m = ctx.db.one("SELECT rel_path, status FROM material WHERE id = ?", (c.material_id,))
            if not m or m["status"] == "丢失" or not ctx.lib.abs(m["rel_path"]).exists():
                raise JobError(f"原素材找不到了：{c.rel_path}，请勾选替换这个片段")
            c.rel_path = m["rel_path"]
        replace = set(overrides.get("replace") or [])
        if replace:
            cands = load_candidates(ctx, job["city"], True)
            keep_ids = {c.clip_id for c in choices}
            for idx in sorted(replace):
                if idx >= len(choices):
                    continue
                old = choices[idx]
                sec = template.sections[old.section] if old.section < len(template.sections) else None
                if sec is None:
                    continue
                sub = schema.Template.from_dict(template.to_dict())
                sub.sections = [schema.Section(**{**sec.__dict__, "count": 1})]
                res = pick(sub, job["city"], cands, rng, exclude=keep_ids)
                if not res.choices:
                    raise JobError(f"没有可替换“{sec.name}”的其他素材")
                new = res.choices[0]
                new.section, new.slot = old.section, old.slot
                choices[idx] = new
                keep_ids.add(new.clip_id)
        for c in choices:   # 复用的片段重新随机起点前恢复原始时长窗口
            if not c.fixed_duration and not c.is_image and c.win_end <= c.win_start:
                c.win_start, c.win_end = c.clip_start + 0.3, c.clip_end - 0.3
        return choices, warnings
    cands = load_candidates(ctx, job["city"], bool(settings["render"].get("allow_used_materials")),
                            job["batch_id"])
    res = pick(template, job["city"], cands, rng, _batch_prior(ctx, job),
               float(settings["render"].get("max_overlap_in_batch", 0.3)))
    if not res.ok:
        raise JobError(shortage_message(res))
    return res.choices, res.warnings


def run_job(ctx, job_id: int, cancel: threading.Event, progress=None) -> list[int]:
    """执行任务，返回生成的 output id 列表。失败抛异常（素材不动）。"""
    job = ctx.db.one("SELECT * FROM job WHERE id = ?", (job_id,))
    settings = ctx.settings
    template = schema.load(ctx.lib.abs(job["template_file"]))
    errs = template.validate()
    if errs:
        raise JobError("模板有问题：" + "；".join(errs))
    overrides = json.loads(job["overrides_json"] or "{}")
    aspects = [a for a in (job["aspect"] or "").split(",") if a in ASPECTS] or template.aspects
    seed = job["seed"]
    rng = random.Random(seed)

    _set_status(ctx, job_id, "挑选素材")
    choices, warnings = _pick_choices(ctx, job, template, overrides, rng, settings)
    tl = build_timeline(choices, template, rng)
    warnings += tl.warnings
    texts, meta = prepare_texts(ctx, template, tl, job["city"], overrides, settings)
    batch_music = set()
    if job["batch_id"]:
        for j in ctx.db.all("SELECT plan_json FROM job WHERE batch_id = ? AND id != ? AND plan_json != ''",
                            (job["batch_id"], job_id)):
            try:
                mid = json.loads(j["plan_json"]).get("music_id")
                if mid:
                    batch_music.add(mid)
            except json.JSONDecodeError:
                pass
    music = None
    if overrides.get("music_id"):
        music = ctx.db.one("SELECT * FROM music WHERE id = ? AND commercial_ok = '是'", (overrides["music_id"],))
    if music is None:
        music = audio_mod.pick_music(ctx, template.music.get("style", ""), template.music.get("track", ""),
                                     rng, batch_music)
    if music is None:
        warnings.append(f"“{template.music.get('style', '')}”风格下没有“能否商用=是”的配乐，本条视频没有背景音乐")
    plan = {"seed": seed, "timeline": tl.to_dict(), "texts": texts.to_dict(), "meta": meta,
            "music_id": music["id"] if music else None, "warnings": warnings}
    ctx.db.update("job", job_id, {"plan_json": json.dumps(plan, ensure_ascii=False),
                                  "warnings": "\n".join(warnings)})
    if cancel.is_set():
        raise Cancelled()

    work = ctx.lib.temp / f"job_{job_id}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    _set_status(ctx, job_id, "渲染中", "混音")
    voices = [audio_mod.VoicePart(ctx.lib.abs(c.rel_path), c.src_start, tl.starts[i], tl.durs[i])
              for i, c in enumerate(tl.choices) if c.keep_audio]
    music_path = ctx.lib.abs(music["rel_path"]) if music else None
    audio_wav, audio_note = audio_mod.build_audio(
        work, tl.total, music_path, float(template.music.get("volume", 0.6)), voices,
        float(settings["render"].get("target_lufs", -14)), cancel)
    encoder = detect_hw_encoder(settings["render"].get("hw_encoder", "auto"))
    brand = dict(settings["brand"])
    results = {}
    for ai, aspect in enumerate(aspects):
        def prog(f, msg, ai=ai):
            _set_status(ctx, job_id, "渲染中", msg)
            if progress:
                progress((ai + f) / len(aspects), msg)
        results[aspect] = render_aspect(
            lib=ctx.lib, tl=tl, template=template, aspect=aspect, texts=texts, audio_wav=audio_wav,
            work=work, brand=brand, encoder=encoder, cancel=cancel, progress=prog, seed=seed,
            color_grade=bool(template.color_grade or settings["render"].get("color_grade")))
        _set_status(ctx, job_id, "校验中", f"[{aspect}] 校验")
        vr = verify_output(results[aspect].video, tl.total, 1.0, ASPECTS[aspect])
        if not vr.ok:
            raise JobError(f"输出校验没通过（{aspect}）：" + "；".join(vr.problems))
    if cancel.is_set():
        raise Cancelled()

    # 封面帧：第一个段落 + 人工评分最高的片段
    font_path = imgfx.find_font(ctx.lib.fonts, bold=True)
    logo = ctx.lib.abs(brand["logo"]) if brand.get("logo") else None
    cover_frame = None
    if template.cover.get("enabled", True):
        first_sec = [c for c in tl.choices if c.section == (tl.choices[0].section if tl.choices else 0)]
        rated = sorted(tl.choices, key=lambda c: -(ctx.db.scalar(
            "SELECT rating FROM material WHERE id = ?", (c.material_id,)) or 0))[:2]
        cands = []
        for c in first_sec + [r for r in rated if r not in first_sec]:
            cands.append((ctx.lib.abs(c.rel_path), c.src_start + c.dur / 2, c.is_image))
        cover_frame = cover_mod.choose_frame(cands)

    # 写入 输出/待审核/
    output_ids = []
    date = _dt.date.today().strftime("%Y%m%d")
    used = [(c.material_id, c.clip_id) for c in tl.choices]
    for aspect in aspects:
        r = results[aspect]
        name = next_name(ctx, job["city"], template, aspect, date)
        folder = ctx.lib.out_pending / name
        stage = ctx.lib.out_pending / f".{name}.part"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        shutil.copy2(r.video, stage / "video.mp4")
        shutil.copy2(r.srt, stage / "subtitles.srt")
        covers = []
        if cover_frame is not None:
            covers = [p.name for p in cover_mod.make_covers(
                cover_frame, stage, meta["title"], font_path, logo,
                template.subtitle_style.color, template.subtitle_style.outline_color)]
        hashtags = " ".join("#" + h for h in meta["hashtags"])
        (stage / "caption.txt").write_text(f"{meta['post']}\n\n{hashtags}\n", encoding="utf-8")
        info = {
            "name": name, "template": template.name, "template_file": job["template_file"],
            "city": job["city"], "aspect": aspect, "seed": seed, "job_id": job_id,
            "generated_at": now(), "duration": round(r.duration, 2), "encoder": r.encoder,
            "music": music["rel_path"] if music else None, "audio": audio_note,
            "materials": [{"rel_path": c.rel_path, "material_id": c.material_id, "clip_id": c.clip_id,
                           "section": template.sections[c.section].name if c.section < len(template.sections) else "",
                           "src_start": c.src_start, "duration": c.dur, "keep_audio": c.keep_audio}
                          for c in tl.choices],
            "warnings": warnings, "notes": meta["notes"],
        }
        (stage / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        stage.rename(folder)
        rel = ctx.lib.rel(folder / "video.mp4")
        oid = ctx.db.insert("output", {
            "job_id": job_id, "kind": "video", "name": name, "city": job["city"],
            "template_name": template.name, "aspect": aspect, "file_path": rel,
            "review_status": "待审核", "title": meta["title"], "caption": meta["post"],
            "hashtags": hashtags, "srt_path": ctx.lib.rel(folder / "subtitles.srt"),
            "cover_paths": ",".join(covers), "notes": "\n".join(warnings + meta["notes"]),
            "created_at": now()})
        output_ids.append(oid)

    # 成功后才移动素材
    mv = mover.record_success(ctx, job_id, output_ids, used,
                              settings["render"].get("move_rule", "any_clip_used"))
    if mv.errors:
        warnings += mv.errors
        ctx.db.update("job", job_id, {"warnings": "\n".join(warnings)})
    if music:
        ctx.db.execute("UPDATE music SET use_count = use_count + 1 WHERE id = ?", (music["id"],))
    # 编辑后重新生成：替换旧输出
    old = overrides.get("replaces_output")
    if old:
        delete_output(ctx, int(old), return_materials=False)
    shutil.rmtree(work, ignore_errors=True)
    return output_ids


def delete_output(ctx, output_id: int, return_materials: bool = False) -> None:
    o = ctx.db.one("SELECT * FROM output WHERE id = ?", (output_id,))
    if not o:
        return
    if return_materials:
        mover.return_to_pending(ctx, output_id)
    p = ctx.lib.abs(o["file_path"])
    folder = p.parent if p.name in ("video.mp4", "poster.png") or o["kind"] == "poster" else p
    if folder.is_dir() and folder != ctx.lib.out_pending and folder.parent.parent == ctx.lib.output:
        shutil.rmtree(folder, ignore_errors=True)
    ctx.db.execute("DELETE FROM output WHERE id = ?", (output_id,))
