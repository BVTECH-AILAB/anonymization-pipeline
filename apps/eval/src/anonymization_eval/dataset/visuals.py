"""Raster PII for the synthetic corpus: signatures, stamps, QR codes, barcodes."""

import io
import random

import barcode
import numpy as np
import qrcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _font(
    size: int, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """Pad/crop to exactly (w, h) so the registry size matches the embedded image."""
    if img.size == (w, h):
        return img
    out = Image.new(
        img.mode, (w, h), (255, 255, 255, 0) if img.mode == "RGBA" else "white"
    )
    out.paste(img, (0, 0))
    return out


def _catmull_rom(pts: np.ndarray, n: int = 12) -> np.ndarray:
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(
                0.5
                * (
                    (2 * p1)
                    + (-p0 + p2) * t
                    + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                    + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
                )
            )
    return np.array(out)


def signature(rnd: random.Random, w: int = 420, h: int = 130) -> Image.Image:
    """Cursive-looking signature: 1-2 smooth strokes with loops, ascenders and slant."""
    scale = 3
    W, H = w * scale, h * scale
    img = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    ink = rnd.choice([(20, 30, 110, 255), (15, 15, 20, 255), (30, 40, 140, 255)])
    base = H * rnd.uniform(0.58, 0.68)
    slant = rnd.uniform(0.15, 0.45)
    words = rnd.choice([1, 2, 2])
    x = W * rnd.uniform(0.04, 0.1)
    for wi in range(words):
        ctrl = [(x - 10, base)]
        n = rnd.randint(5, 9) if wi else rnd.randint(4, 7)
        first = True
        for _ in range(n):
            x += W * rnd.uniform(0.025, 0.06) / words * 2
            r = rnd.random()
            if first:  # capital: tall loop
                top = base - H * rnd.uniform(0.45, 0.55)
                ctrl += [
                    (x, top),
                    (x + W * 0.02, base + H * 0.05),
                    (x - W * 0.03, base - H * 0.2),
                ]
                first = False
            elif r < 0.25:  # ascender loop
                ctrl += [(x, base - H * rnd.uniform(0.3, 0.45)), (x + W * 0.012, base)]
            elif r < 0.4:  # descender
                ctrl += [
                    (x, base + H * rnd.uniform(0.15, 0.28)),
                    (x - W * 0.01, base - H * 0.05),
                ]
            else:  # small hump
                ctrl += [
                    (x, base - H * rnd.uniform(0.08, 0.18)),
                    (x + W * 0.015, base + rnd.uniform(-4, 4)),
                ]
        ctrl += [(x + W * 0.03, base - H * 0.05), (x + W * 0.05, base - H * 0.08)]
        pts = np.array(ctrl, dtype=float)
        pts[:, 0] += (base - pts[:, 1]) * slant  # italic slant
        curve = _catmull_rom(pts)
        d.line(
            [tuple(p) for p in curve], fill=ink, width=rnd.randint(5, 7), joint="curve"
        )
        x += W * rnd.uniform(0.06, 0.1)
    if rnd.random() < 0.7:  # flourish underline
        y0 = base + H * rnd.uniform(0.12, 0.2)
        fl = np.array(
            [
                (W * 0.05, y0 + 10),
                (W * 0.1, y0),
                (W * 0.5, y0 - H * 0.04),
                (min(x, W * 0.95), y0 - H * rnd.uniform(0.05, 0.15)),
                (W, y0 - H * 0.2),
            ]
        )
        d.line([tuple(p) for p in _catmull_rom(fl)], fill=ink, width=4, joint="curve")
    return img.resize((w, h), Image.Resampling.LANCZOS)


def stamp(
    rnd: random.Random, lines: list[str], w: int = 520, h: int = 210
) -> Image.Image:
    """Rectangular rubber stamp with uneven ink, slightly rotated."""
    ink = rnd.choice([(40, 50, 160), (110, 40, 140), (30, 90, 160)])
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    pad = 10
    d.rectangle([pad, pad, w - pad, h - pad], outline=ink + (230,), width=5)
    d.rectangle(
        [pad + 9, pad + 9, w - pad - 9, h - pad - 9], outline=ink + (200,), width=2
    )
    usable = h - 2 * (pad + 18)
    size = min(34, int(usable / max(1, len(lines)) * 0.8))
    max_w = w - 2 * pad - 36
    y = pad + 18
    for i, line in enumerate(lines):
        fs = size + (4 if i == 0 else 0)
        f = _font(fs, bold=(i == 0))
        while d.textlength(line, font=f) > max_w and fs > 10:
            fs -= 1
            f = _font(fs, bold=(i == 0))
        tw = d.textlength(line, font=f)
        d.text(((w - tw) / 2, y), line, font=f, fill=ink + (235,))
        y += usable / len(lines)
    # ink unevenness
    a = np.array(img)
    noise = np.random.default_rng(rnd.randint(0, 1 << 30)).random(a.shape[:2])
    a[..., 3] = (a[..., 3] * np.clip(noise * 1.6, 0.25, 1)).astype(np.uint8)
    img = Image.fromarray(a).filter(ImageFilter.GaussianBlur(0.6))
    img = img.rotate(
        rnd.uniform(-7, 7), resample=Image.Resampling.BICUBIC, expand=False
    )
    return img


def qr(data: str, box: int = 6) -> Image.Image:
    q = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box, border=2
    )
    q.add_data(data)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").convert("RGB")


def barcode39(data: str) -> Image.Image:
    if ImageWriter is None:  # python-barcode without Pillow
        raise RuntimeError("python-barcode cannot write images")
    buf = io.BytesIO()
    barcode.get_barcode_class("code39")(
        data, writer=ImageWriter(), add_checksum=False
    ).write(
        buf,
        {
            "module_height": 9.0,
            "module_width": 0.25,
            "write_text": False,
            "quiet_zone": 2.0,
            "dpi": 300,
        },
    )
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def to_png(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf
