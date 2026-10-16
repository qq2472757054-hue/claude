"""图文海报（Pillow）：单图大标题 / 双图对比 / 四宫格；4:5 与 1:1。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from . import imgfx

LAYOUTS = {"single": "单图大标题", "dual": "双图对比", "grid": "四宫格"}
SIZES = {"4:5": (1080, 1350), "1:1": (1080, 1080)}
BRAND_BAR = (18, 32, 47)
ACCENT = (255, 196, 61)


def _brand_bar(im: Image.Image, brand: dict, font_path: str | None, logo_path) -> int:
    """底部品牌条，返回品牌条高度。"""
    w, h = im.size
    bar_h = int(h * 0.10)
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, h - bar_h, w, h), fill=BRAND_BAR)
    x = int(w * 0.04)
    logo = imgfx.load_logo(logo_path, int(bar_h * 0.75))
    if logo is not None:
        if logo.height > bar_h * 0.8:
            logo = logo.resize((int(logo.width * bar_h * 0.8 / logo.height), int(bar_h * 0.8)))
        rgba = im.convert("RGBA")
        rgba.alpha_composite(logo, (x, h - bar_h + (bar_h - logo.height) // 2))
        im.paste(rgba.convert("RGB"))
        x += logo.width + int(w * 0.02)
    name = " · ".join(n for n in (brand.get("name_en"), brand.get("name_zh")) if n)
    f_name = imgfx.font(font_path, int(bar_h * 0.30))
    f_contact = imgfx.font(font_path, int(bar_h * 0.22))
    draw = ImageDraw.Draw(im)
    contacts = "   ".join(imgfx.contact_lines(brand))
    if contacts:
        draw.text((x, h - bar_h + bar_h * 0.14), name, font=f_name, fill=(255, 255, 255))
        max_w = w - x - int(w * 0.03)
        while draw.textlength(contacts, font=f_contact) > max_w and f_contact.size > 14:
            f_contact = imgfx.font(font_path, f_contact.size - 2)
        draw.text((x, h - bar_h + bar_h * 0.56), contacts, font=f_contact, fill=(200, 215, 230))
    else:
        draw.text((x, h - bar_h + bar_h * 0.33), name, font=f_name, fill=(255, 255, 255))
    return bar_h


def _text_panel(im: Image.Image, box: tuple[int, int, int, int], title: str, subtitle: str,
                points: list[str], font_path: str | None, dark: bool = True) -> None:
    x0, y0, x1, y1 = box
    w = x1 - x0
    draw = ImageDraw.Draw(im)
    pad = int(w * 0.05)
    y = y0 + pad
    color = (255, 255, 255) if dark else (20, 30, 40)
    sub_color = ACCENT if dark else (200, 110, 0)
    size = int(im.width * 0.075)
    for size_try in (size, int(size * 0.85), int(size * 0.72), int(size * 0.6)):
        f = imgfx.font(font_path, size_try)
        lines = imgfx.wrap_px(draw, title, f, w - 2 * pad, 10)
        if len(lines) <= 2:
            break
    for line in lines[:3]:
        draw.text((x0 + pad, y), line, font=f, fill=color)
        y += int(f.size * 1.2)
    if subtitle:
        fs = imgfx.font(font_path, int(im.width * 0.038))
        for line in imgfx.wrap_px(draw, subtitle, fs, w - 2 * pad, 2):
            draw.text((x0 + pad, y + 4), line, font=fs, fill=sub_color)
            y += int(fs.size * 1.3)
    y += int(im.width * 0.015)
    fp = imgfx.font(font_path, int(im.width * 0.034))
    for p in [p for p in points if p.strip()][:4]:
        if y > y1 - fp.size:
            break
        r = fp.size // 4
        draw.ellipse((x0 + pad, y + fp.size // 2 - r, x0 + pad + 2 * r, y + fp.size // 2 + r), fill=sub_color)
        lines = imgfx.wrap_px(draw, p.strip(), fp, w - 2 * pad - 4 * r, 2)
        for line in lines:
            draw.text((x0 + pad + 4 * r, y), line, font=fp, fill=color)
            y += int(fp.size * 1.3)
        y += int(fp.size * 0.3)


def render_poster(images: list[Image.Image], layout: str, size_key: str, title: str, subtitle: str,
                  points: list[str], brand: dict, font_path: str | None, logo_path=None) -> Image.Image:
    if not images:
        raise ValueError("至少需要 1 张图片")
    w, h = SIZES[size_key]
    im = Image.new("RGB", (w, h), BRAND_BAR)
    bar_h = int(h * 0.10)
    content_h = h - bar_h
    if layout == "single":
        photo = imgfx.cover_fit(images[0], w, content_h)
        photo = imgfx.bottom_gradient(photo, 0.62, 230)
        im.paste(photo, (0, 0))
        n_pts = len([p for p in points if p.strip()])
        panel_h = int(content_h * (0.30 + 0.06 * n_pts))
        _text_panel(im, (0, content_h - panel_h, w, content_h), title, subtitle, points, font_path)
    elif layout == "dual":
        text_h = int(content_h * 0.44)
        ph = content_h - text_h
        gap = int(w * 0.012)
        half = (w - gap) // 2
        imgs = (images + images)[:2]
        im.paste(imgfx.cover_fit(imgs[0], half, ph), (0, 0))
        im.paste(imgfx.cover_fit(imgs[1], w - half - gap, ph), (half + gap, 0))
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, ph, w, content_h), fill=(250, 247, 240))
        _text_panel(im, (0, ph, w, content_h), title, subtitle, points, font_path, dark=False)
    else:  # grid
        text_h = int(content_h * 0.36)
        gh = content_h - text_h
        gap = int(w * 0.01)
        cw, ch = (w - gap) // 2, (gh - gap) // 2
        imgs = (images * 4)[:4]
        for i, src in enumerate(imgs):
            x = (i % 2) * (cw + gap)
            y = text_h + (i // 2) * (ch + gap)
            im.paste(imgfx.cover_fit(src, cw if i % 2 == 0 else w - cw - gap, ch), (x, y))
        _text_panel(im, (0, 0, w, text_h), title, subtitle, points[:2], font_path)
    _brand_bar(im, brand, font_path, logo_path)
    return im


def save_poster(ctx, images: list[Image.Image], layout: str, sizes: list[str], title: str,
                subtitle: str, points: list[str], brand: dict, city: str = "") -> list[int]:
    """生成海报放进 输出/待审核/，返回 output id。"""
    import datetime as _dt

    from ..db import now

    font_path = imgfx.find_font(ctx.lib.fonts, bold=True)
    logo = ctx.lib.abs(brand["logo"]) if brand.get("logo") else None
    ids = []
    date = _dt.date.today().strftime("%Y%m%d")
    for size_key in sizes:
        im = render_poster(images, layout, size_key, title, subtitle, points, brand, font_path, logo)
        prefix = f"{date}_{city or '海报'}_海报{LAYOUTS[layout]}_{size_key.replace(':', 'x')}_"
        n = 1
        while any((d / f"{prefix}{n:02d}").exists() for d in
                  (ctx.lib.out_pending, ctx.lib.out_approved, ctx.lib.out_rejected)):
            n += 1
        name = f"{prefix}{n:02d}"
        folder = ctx.lib.out_pending / name
        folder.mkdir(parents=True)
        im.save(folder / "poster.png")
        caption = "\n".join(x for x in (title, subtitle, *[f"• {p}" for p in points if p.strip()]) if x)
        (folder / "caption.txt").write_text(caption + "\n", encoding="utf-8")
        ids.append(ctx.db.insert("output", {
            "kind": "poster", "name": name, "city": city, "template_name": f"海报·{LAYOUTS[layout]}",
            "aspect": size_key, "file_path": ctx.lib.rel(folder / "poster.png"), "review_status": "待审核",
            "title": title, "caption": caption, "created_at": now()}))
    return ids


def load_sources(paths: list[str | Path]) -> list[Image.Image]:
    from ..imageio import open_image

    return [open_image(p) for p in paths]
