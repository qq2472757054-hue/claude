"""Pillow 图像工具：填充（裁切/模糊背景）、带描边文字、片尾品牌卡。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..paths import ASSETS_DIR


def find_font(fonts_dir: str | Path | None, bold: bool = True) -> str | None:
    """在 字体/ 中找字体文件；优先 Noto Sans SC，找不到用程序自带字体。"""
    dirs = [Path(fonts_dir)] if fonts_dir else []
    dirs.append(ASSETS_DIR / "fonts")
    prefer = ["NotoSansSC-Bold", "NotoSansSC-Regular"] if bold else ["NotoSansSC-Regular", "NotoSansSC-Bold"]
    for d in dirs:
        if not d.is_dir():
            continue
        for name in prefer:
            for ext in (".ttf", ".otf"):
                if (d / f"{name}{ext}").is_file():
                    return str(d / f"{name}{ext}")
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                return str(f)
    return None


def font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def cover_fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """等比放大并居中裁切填满。"""
    scale = max(w / im.width, h / im.height)
    nw, nh = max(int(round(im.width * scale)), w), max(int(round(im.height * scale)), h)
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


def blur_fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """原画面等比缩放居中，背景用同一画面放大并高斯模糊。"""
    small = cover_fit(im, max(w // 8, 8), max(h // 8, 8)).filter(ImageFilter.GaussianBlur(4))
    bg = small.resize((w, h), Image.BILINEAR)
    bg = Image.blend(bg, Image.new("RGB", (w, h), (0, 0, 0)), 0.15)
    scale = min(w / im.width, h / im.height)
    fw, fh = int(im.width * scale), int(im.height * scale)
    fg = im.resize((fw, fh), Image.LANCZOS)
    bg.paste(fg, ((w - fw) // 2, (h - fh) // 2))
    return bg


def fill(im: Image.Image, w: int, h: int, mode: str) -> Image.Image:
    src_ar, dst_ar = im.width / im.height, w / h
    if mode == "crop" or abs(src_ar - dst_ar) / dst_ar < 0.03:
        return cover_fit(im, w, h)
    return blur_fit(im, w, h)


def wrap_px(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: float, max_lines: int = 3) -> list[str]:
    from .subtitles import wrap_lines

    lines = wrap_lines(text, 10_000, lambda s: draw.textlength(s, font=fnt), max_w)
    return lines[:max_lines]


def draw_text_block(im: Image.Image, text: str, font_path: str | None, size: int, center_y: float,
                    max_w: float, fill_color=(255, 255, 255), stroke=(0, 0, 0), stroke_w: int = 6,
                    max_lines: int = 3, shrink: bool = True) -> None:
    """在 center_y 附近居中绘制多行文字（自动缩小字号以放下）。"""
    draw = ImageDraw.Draw(im)
    size_now = size
    while True:
        fnt = font(font_path, size_now)
        lines = wrap_px(draw, text, fnt, max_w, 100)
        if len(lines) <= max_lines or not shrink or size_now <= 24:
            lines = lines[:max_lines]
            break
        size_now = int(size_now * 0.9)
    line_h = int(size_now * 1.25)
    y = center_y - line_h * len(lines) / 2
    for line in lines:
        tw = draw.textlength(line, font=fnt)
        draw.text(((im.width - tw) / 2, y), line, font=fnt, fill=fill_color,
                  stroke_width=stroke_w, stroke_fill=stroke)
        y += line_h


def bottom_gradient(im: Image.Image, frac: float = 0.45, strength: int = 200) -> Image.Image:
    w, h = im.size
    grad = Image.new("L", (1, 256))
    for y in range(256):
        grad.putpixel((0, y), int(strength * (y / 255) ** 1.6))
    gh = int(h * frac)
    mask = grad.resize((w, gh))
    black = Image.new("RGB", (w, gh), (0, 0, 0))
    out = im.copy()
    out.paste(black, (0, h - gh), mask)
    return out


def load_logo(path: str | Path | None, max_w: int) -> Image.Image | None:
    if not path or not Path(path).is_file():
        return None
    try:
        with Image.open(path) as lg:
            lg = lg.convert("RGBA")
        scale = max_w / lg.width
        return lg.resize((max_w, max(int(lg.height * scale), 1)), Image.LANCZOS)
    except Exception:
        return None


def end_card(w: int, h: int, brand: dict, logo_path: str | Path | None, font_path: str | None,
             background: Image.Image | None = None) -> Image.Image:
    """片尾品牌卡：品牌名 + 联系方式，内容来自设置。"""
    if background is not None:
        bg = cover_fit(background, w, h).filter(ImageFilter.GaussianBlur(radius=max(w // 40, 8)))
        bg = Image.blend(bg, Image.new("RGB", (w, h), (10, 20, 30)), 0.55)
    else:
        bg = Image.new("RGB", (w, h), (18, 32, 47))
    im = bg.convert("RGBA")
    y = h * 0.30
    logo = load_logo(logo_path, int(min(w, h) * 0.28))
    if logo is not None:
        im.alpha_composite(logo, ((w - logo.width) // 2, int(y - logo.height / 2)))
        y += logo.height / 2 + h * 0.06
    im = im.convert("RGB")
    base = min(w, h)
    name_en = brand.get("name_en") or ""
    name_zh = brand.get("name_zh") or ""
    if name_en:
        draw_text_block(im, name_en, font_path, int(base * 0.085), y, w * 0.86, stroke_w=0, max_lines=2)
        y += base * 0.11
    if name_zh:
        draw_text_block(im, name_zh, font_path, int(base * 0.06), y, w * 0.86, stroke_w=0,
                        fill_color=(230, 230, 230), max_lines=1)
        y += base * 0.10
    contacts = contact_lines(brand)
    for line in contacts:
        draw_text_block(im, line, font_path, int(base * 0.042), y, w * 0.86, stroke_w=0,
                        fill_color=(210, 220, 230), max_lines=1)
        y += base * 0.065
    return im


def contact_lines(brand: dict) -> list[str]:
    out = []
    for key, label in (("whatsapp", "WhatsApp"), ("line", "LINE"), ("phone", "Tel"), ("website", "")):
        v = (brand.get(key) or "").strip()
        if v:
            out.append(f"{label}: {v}" if label else v)
    return out
