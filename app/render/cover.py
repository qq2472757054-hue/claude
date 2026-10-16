"""封面：自动选帧、叠加标题与 logo，输出竖版 1080×1920 与横版 1280×720。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..analysis.quality import laplacian_var
from ..imageio import grab_frame, open_image, to_gray
from . import imgfx

COVER_SIZES = {"cover_9x16.jpg": (1080, 1920), "cover_16x9.jpg": (1280, 720)}


def frame_score(im: Image.Image) -> float:
    """清晰度高、亮度适中的帧得分高。"""
    small = im.copy()
    small.thumbnail((320, 320))
    g = to_gray(small)
    sharp = laplacian_var(g)
    bright = float(g.mean())
    bright_pen = abs(bright - 125) / 125     # 0 最好
    return min(sharp, 600) / 600 * 0.6 + (1 - bright_pen) * 0.4


def choose_frame(candidates: list[tuple[Path, float, bool]], samples: int = 3) -> Image.Image | None:
    """candidates: [(素材路径, 时间点, 是否图片)]。在每个候选附近取几帧，返回得分最高的原尺寸帧。"""
    best, best_score = None, -1.0
    for path, t, is_image in candidates:
        try:
            if is_image:
                frames = [(None, open_image(path))]
            else:
                frames = [(t + d, grab_frame(path, t + d, 480)) for d in
                          ([0.0] if samples == 1 else [-0.6, 0.0, 0.6])[:samples]]
        except Exception:
            continue
        for tt, im in frames:
            if im is None:
                continue
            s = frame_score(im)
            if s > best_score:
                best_score = s
                best = (path, tt, is_image, im)
    if best is None:
        return None
    path, tt, is_image, im = best
    if is_image:
        return im
    full = grab_frame(path, max(tt or 0, 0), None)
    return full or im


def render_cover(frame: Image.Image, size: tuple[int, int], title: str, font_path: str | None,
                 logo_path: str | Path | None, fill_mode: str = "crop", title_color: str = "#FFFFFF",
                 stroke_color: str = "#000000") -> Image.Image:
    w, h = size
    im = imgfx.fill(frame, w, h, fill_mode)
    if title:
        im = imgfx.bottom_gradient(im, 0.5 if h > w else 0.6, 190)
        vertical = h > w
        size_px = int(w * (0.105 if vertical else 0.075))
        center_y = h * (0.70 if vertical else 0.74)
        max_w = w * (0.80 if vertical else 0.86)
        imgfx.draw_text_block(im, title, font_path, size_px, center_y, max_w,
                              fill_color=_rgb(title_color), stroke=_rgb(stroke_color),
                              stroke_w=max(size_px // 14, 3), max_lines=3)
    logo = imgfx.load_logo(logo_path, int(min(w, h) * 0.16))
    if logo is not None:
        rgba = im.convert("RGBA")
        margin = int(min(w, h) * 0.04)
        rgba.alpha_composite(logo, (margin, margin))
        im = rgba.convert("RGB")
    return im


def make_covers(frame: Image.Image, out_dir: Path, title: str, font_path: str | None,
                logo_path: str | Path | None, style_color: str = "#FFFFFF",
                outline_color: str = "#000000") -> list[Path]:
    out = []
    for name, size in COVER_SIZES.items():
        im = render_cover(frame, size, title, font_path, logo_path, "crop", style_color, outline_color)
        p = out_dir / name
        im.save(p, "JPEG", quality=90)
        out.append(p)
    return out


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "#FFFFFF").lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255
