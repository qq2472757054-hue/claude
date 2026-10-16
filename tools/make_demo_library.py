"""生成一套演示用的合成素材（用于测试与演示，不需要真实素材）。

用法：python tools/make_demo_library.py <素材库目录> [--small]
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from app.ffrun import ffmpeg  # noqa: E402

SOURCES = [
    "testsrc2=size={s}:rate=30",
    "mandelbrot=size={s}:rate=30",
    "life=size={s}:rate=30:mold=10:ratio=0.5:death_color=#203040:life_color=#ffcc66",
    "smptehdbars=size={s}:rate=30",
    "rgbtestsrc=size={s}:rate=30",
    "cellauto=size={s}:rate=30:rule=110",
]


def make_video(path: Path, seconds: float, size: str = "1280x720", scenes: int = 1,
               audio: str = "tone", seed: int = 0, rotate: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    per = seconds / scenes
    args, flt = [], []
    for i in range(scenes):
        src = rng.choice(SOURCES).format(s=size)
        args += ["-f", "lavfi", "-t", f"{per:.2f}", "-i", src]
        hue = rng.randint(0, 360)
        flt.append(f"[{i}:v]format=yuv420p,hue=h={hue},noise=alls=12:allf=t,setsar=1[v{i}]")
    flt.append("".join(f"[v{i}]" for i in range(scenes)) + f"concat=n={scenes}:v=1:a=0[v]")
    maps = ["-map", "[v]"]
    if audio == "tone":
        args += ["-f", "lavfi", "-t", f"{seconds:.2f}", "-i",
                 f"sine=frequency={rng.choice([330, 440, 550])}:sample_rate=48000"]
        maps += ["-map", f"{scenes}:a"]
    meta = []
    if rotate is not None:
        meta = ["-metadata:s:v:0", f"rotate={rotate}"]
    ffmpeg([*args, "-filter_complex", ";".join(flt), *maps, "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "23", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio == "tone" else []),
            *meta, "-t", f"{seconds:.2f}", str(path)])


def make_image(path: Path, size=(1600, 1200), seed: int = 0) -> None:
    rng = random.Random(seed)
    im = Image.new("RGB", size, tuple(rng.randint(40, 200) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        r = rng.randint(20, 200)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randint(0, 255) for _ in range(3)),
                  outline=(255, 255, 255), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=92)


def make_music(path: Path, seconds: float = 40, seed: int = 0) -> None:
    rng = random.Random(seed)
    notes = [261.6, 293.7, 329.6, 392.0, 440.0, 523.3]
    expr = "+".join(f"0.15*sin(2*PI*{rng.choice(notes)}*t)*eq(mod(floor(t*2),{k}),0)"
                    for k in (1, 2, 3))
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-f", "lavfi", "-t", str(seconds), "-i", f"aevalsrc='{expr}':s=44100:c=stereo",
            "-c:a", "libmp3lame", "-q:a", "5", str(path)])


PLAN = {
    "张家界": {"天门山": ["景点", "景点", "景点", "夜景"], "": ["游客互动", "游客互动", "美食", "美食", "导游出镜"]},
    "成都": {"宽窄巷子": ["美食", "美食", "美食", "游客互动"], "": ["美食", "美食", "景点", "夜景"]},
    "重庆": {"洪崖洞": ["夜景", "夜景", "夜景", "景点"], "": ["夜景", "景点", "夜景", "景点"]},
}


def build(root: Path, small: bool = False) -> list[tuple[Path, str]]:
    """返回 [(文件, 类型)]，供测试登记标签。"""
    made = []
    n = 0
    for city, spots in PLAN.items():
        for spot, types in spots.items():
            for t in types:
                n += 1
                folder = root / "待使用" / city / spot if spot else root / "待使用" / city
                if n % 5 == 0:
                    p = folder / f"图片_{n:02d}.jpg"
                    make_image(p, seed=n)
                else:
                    portrait = n % 3 == 0
                    size = "720x1280" if portrait else "1280x720"
                    if small:
                        size = "360x640" if portrait else "640x360"
                    p = folder / f"视频 {n:02d}.mp4"
                    make_video(p, 9 if not small else 8, size, scenes=2, seed=n,
                               audio="tone" if t == "导游出镜" or n % 2 else "none")
                made.append((p, t))
    for style in ("轻快", "抒情", "动感", "城市夜景"):
        make_music(root / "配乐" / style / f"{style}_demo.mp3", 45, seed=len(style))
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--small", action="store_true")
    a = ap.parse_args()
    for p, t in build(Path(a.root), a.small):
        print(t, p)
