import itertools
import random

from app.render.picker import Candidate, overlap_ratio, pick
from app.templates.schema import Section, Template


def cand(i, type_="景点", city="张家界", spot="", length=6.0, **kw):
    return Candidate(clip_id=i, material_id=i, rel_path=f"待使用/{city}/{i}.mp4", city=city, spot=spot,
                     type=type_, start=0.0, end=length, width=1920, height=1080, **kw)


def tpl(*sections, duration=20):
    return Template(name="测试", cities=["张家界"], duration=duration, sections=list(sections))


def test_only_matching_city_and_no_repeat():
    cands = [cand(i) for i in range(5)] + [cand(100 + i, city="成都") for i in range(5)]
    res = pick(tpl(Section("a", ["景点"], count=5)), "张家界", cands, random.Random(1))
    assert res.ok
    ids = [c.clip_id for c in res.choices]
    assert len(ids) == len(set(ids)) == 5
    assert all(i < 100 for i in ids)


def test_shortage_message_counts_missing():
    cands = [cand(i, "导游出镜") for i in range(1)]
    res = pick(tpl(Section("导游", ["导游出镜"], count=3)), "张家界", cands, random.Random(1))
    assert not res.ok
    assert "张家界·导游出镜素材不足，还差 2 段" in res.shortages[0]


def test_relax_spot_then_type():
    cands = [cand(1, spot="天门山"), cand(2, spot="其他"), cand(3, "美食")]
    t = tpl(Section("a", ["景点"], spots=["天门山"], count=3))
    res = pick(t, "张家界", cands, random.Random(1))
    assert res.ok
    assert res.choices[0].clip_id == 1           # 先用严格匹配的
    assert any("放宽景点" in w for w in res.warnings)
    assert any("其他类型" in w for w in res.warnings)


def test_rating_then_use_count_priority():
    cands = [cand(1, rating=0), cand(2, rating=5), cand(3, rating=0, use_count=0), cand(4, rating=0, use_count=9)]
    res = pick(tpl(Section("a", ["景点"], count=2)), "张家界", cands, random.Random(3))
    ids = [c.clip_id for c in res.choices]
    assert ids[0] == 2
    assert 4 not in ids


def test_too_short_clip_skipped_and_duration_within_window():
    cands = [cand(1, length=1.6), cand(2, length=10)]
    res = pick(tpl(Section("a", ["景点"], count=1, seconds=[3, 4])), "张家界", cands, random.Random(1))
    c = res.choices[0]
    assert c.clip_id == 2
    assert 3 <= c.dur <= 4
    assert c.win_start >= 0.3 and c.win_end <= 10 - 0.3


def test_image_duration_3_to_4_seconds():
    cands = [Candidate(clip_id=1, material_id=1, rel_path="x.jpg", city="张家界", spot="", type="景点",
                       start=0, end=0, is_image=True)]
    res = pick(tpl(Section("a", ["景点"], count=1, seconds=[2, 6])), "张家界", cands, random.Random(1))
    assert 3 <= res.choices[0].dur <= 4


def test_keep_audio_cuts_on_sentence_boundaries():
    voice = [{"start": 1.0, "end": 3.0, "text": "第一句"}, {"start": 3.2, "end": 6.5, "text": "第二句"},
             {"start": 7.0, "end": 9.0, "text": "第三句"}]
    cands = [cand(1, "导游出镜", length=12, has_audio=True, voice=voice), cand(2, "导游出镜", has_audio=True)]
    res = pick(tpl(Section("g", ["导游出镜"], count=1, seconds=[4, 5], keep_audio=True)), "张家界", cands,
               random.Random(1))
    c = res.choices[0]
    assert c.clip_id == 1 and c.keep_audio and c.fixed_duration
    # 从第一句开头到第二句结尾（≥4 秒），不在句中截断；可超过段落上限 5 秒
    assert c.src_start <= 1.0 and c.src_start + c.dur >= 6.5
    assert [v["text"] for v in c.voice] == ["第一句", "第二句"]


def test_batch_overlap_at_most_30_percent():
    """验收 2：20 段素材生成 5 条同模板视频，任意两条重合率不超过 30%。"""
    cands = [cand(i, "景点" if i % 2 else "美食") for i in range(20)]
    t = tpl(Section("a", ["景点"], count=2), Section("b", ["美食"], count=2))
    prior, sets = [], []
    for k in range(5):
        res = pick(t, "张家界", cands, random.Random(k), prior=prior, max_overlap=0.3)
        assert res.ok
        s = {c.clip_id for c in res.choices}
        sets.append(s)
        prior.append(s)
    for a, b in itertools.combinations(sets, 2):
        assert overlap_ratio(a, b) <= 0.3


def test_batch_overlap_relaxes_with_warning_when_not_enough():
    cands = [cand(i) for i in range(4)]
    t = tpl(Section("a", ["景点"], count=4))
    first = pick(t, "张家界", cands, random.Random(1))
    second = pick(t, "张家界", cands, random.Random(2), prior=[first.clip_ids], max_overlap=0.3)
    assert second.ok
    assert any("重合率" in w for w in second.warnings)


def test_reproducible_with_seed():
    cands = [cand(i) for i in range(30)]
    t = tpl(Section("a", ["景点"], count=6))
    a = pick(t, "张家界", cands, random.Random(42))
    b = pick(t, "张家界", cands, random.Random(42))
    assert [c.clip_id for c in a.choices] == [c.clip_id for c in b.choices]
