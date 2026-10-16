from app.ffrun import ffmpeg
from app.render.verify import check, verify_output

GOOD = {"has_video": True, "has_audio": True, "duration": 30.2, "vcodec": "h264", "acodec": "aac",
        "width": 1080, "height": 1920}


def test_good():
    assert check(GOOD, 30.0, 1.0, (1080, 1920)).ok


def test_duration_off_by_one_second_fails():
    r = check({**GOOD, "duration": 31.0}, 30.0)
    assert not r.ok and "时长" in r.problems[0]


def test_missing_audio_or_video():
    assert not check({**GOOD, "has_audio": False}, 30.0).ok
    assert not check({**GOOD, "has_video": False}, 30.0).ok


def test_wrong_codec_and_size():
    assert not check({**GOOD, "vcodec": "hevc"}, 30.0).ok
    assert not check(GOOD, 30.0, 1.0, (1080, 1080)).ok


def test_real_file(tmp_path):
    p = tmp_path / "输出 测试.mp4"
    ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-f", "lavfi", "-i", "sine=f=440",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(p)])
    assert verify_output(p, 2.0, 1.0, (320, 240)).ok
    assert not verify_output(p, 5.0).ok
    assert not verify_output(tmp_path / "missing.mp4", 2.0).ok
