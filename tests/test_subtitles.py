from app.render import subtitles as st
from app.render.subtitles import SubEvent
from app.templates.schema import SubtitleStyle


def test_english_wrap_max_32_chars():
    text = "Avatar mountains for real and a really long sentence about our trip to Zhangjiajie national park"
    lines = st.wrap_lines(text, 32)
    assert all(len(line) <= 32 for line in lines)
    assert " ".join(lines) == text            # 不丢词、不在单词中间断开


def test_chinese_wrap_max_16_chars():
    text = "张家界的天门山是世界上最美的山之一我们今天带大家一起去看看真正的阿凡达山"
    lines = st.wrap_lines(text, 32)
    assert all(len(line) <= 16 for line in lines)
    assert "".join(lines) == text


def test_punctuation_not_at_line_start():
    text = "我们今天去天门山玻璃栈道走一走看一看啊，然后去吃饭。"
    for line in st.wrap_lines(text, 32)[1:]:
        assert line[0] not in "，。！？"


def test_screens_at_most_two_lines():
    ev = SubEvent(0, 6, "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                        "fifteen sixteen seventeen eighteen nineteen twenty", "", "voice")
    screens = st.layout_events([ev], "9:16", False)
    assert len(screens) >= 2
    assert all(len(s.lines) <= 2 for s in screens)
    assert screens[0].start == 0 and abs(screens[-1].end - 6) < 0.01
    for a, b in zip(screens, screens[1:]):
        assert a.end <= b.start + 0.001


def test_bilingual_one_line_each():
    ev = SubEvent(0, 4, "Welcome to Zhangjiajie, the real Avatar mountains", "欢迎来到张家界，真正的阿凡达山", "voice")
    screens = st.layout_events([ev], "9:16", True)
    for s in screens:
        assert len(s.lines) <= 1 and len(s.lines_zh) <= 1
    ass = st.build_ass(screens, 1080, 1920, "9:16", SubtitleStyle())
    assert r"\N{\fs" in ass                    # 英文在上、中文在下，中文单独字号
    srt = st.build_srt(screens)
    assert "欢迎来到" in srt and "Welcome" in srt


def test_pixel_measure_limits_width():
    measure = lambda s: len(s) * 40.0           # noqa: E731  每个字符 40px
    lines = st.wrap_lines("aaaa bbbb cccc dddd eeee", 32, measure, 400)
    assert all(measure(line) <= 400 for line in lines)


def test_ass_safe_area_vertical():
    m = st.safe_margins(1080, 1920, "9:16")
    assert m["bottom"] >= 1920 * 0.15
    assert m["r"] >= 1080 * 0.12
    ass = st.build_ass([], 1080, 1920, "9:16", SubtitleStyle())
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    voice_style = [l for l in ass.splitlines() if l.startswith("Style: Voice")][0]
    margin_l, margin_r, margin_v = map(int, voice_style.split(",")[-4:-1])
    assert margin_r >= 130 and margin_v >= 288


def test_srt_timestamps():
    screens = [st.Screen(1.5, 3.25, ["Hello"]), st.Screen(61.0, 62.0, ["你好"])]
    srt = st.build_srt(screens)
    assert "00:00:01,500 --> 00:00:03,250" in srt
    assert "00:01:01,000 --> 00:01:02,000" in srt


def test_ass_escapes_braces():
    ass = st.build_ass([st.Screen(0, 1, ["a {b} c"])], 1080, 1920, "9:16", SubtitleStyle())
    assert "{b}" not in ass
