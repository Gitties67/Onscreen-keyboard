#!/usr/bin/env python3
"""Generate osk.ico (multi-size keyboard glyph) with Pillow.

Usage: python tools/make_icon.py [output.ico]
Writes osk.ico next to the repo root by default, plus osk.png (256px) used
for the runtime GTK window icon.
"""
import os
import sys

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 256]
BODY = (30, 34, 44, 255)
BORDER = (0, 120, 212, 255)
KEY = (225, 230, 240, 255)
ACCENT = (0, 120, 212, 255)


def render(size: int) -> Image.Image:
    ss = 8  # supersample factor for smooth edges
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(s * 0.04)
    top, bot = round(s * 0.22), round(s * 0.80)
    d.rounded_rectangle([pad, top, s - pad, bot], radius=round(s * 0.10),
                        fill=BODY, outline=BORDER, width=max(ss, round(s * 0.035)))
    # key grid: 3 rows; small sizes use fewer, bigger keys so they stay legible
    cols = 4 if size <= 24 else 6
    rows = 3
    inner_l, inner_r = pad + s * 0.09, s - pad - s * 0.09
    inner_t, inner_b = top + s * 0.07, bot - s * 0.07
    gap = s * (0.025 if size <= 24 else 0.02)
    kw = (inner_r - inner_l - gap * (cols - 1)) / cols
    kh = (inner_b - inner_t - gap * (rows - 1)) / rows
    rad = max(1, round(min(kw, kh) * 0.2))
    for r in range(rows):
        y0 = inner_t + r * (kh + gap)
        if r == rows - 1:  # space bar row
            d.rounded_rectangle([inner_l + kw * 1.2, y0, inner_r - kw * 1.2, y0 + kh],
                                radius=rad, fill=ACCENT)
            continue
        for c in range(cols):
            x0 = inner_l + c * (kw + gap)
            d.rounded_rectangle([x0, y0, x0 + kw, y0 + kh], radius=rad, fill=KEY)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "osk.ico")
    big = render(256)
    big.save(out, format="ICO", sizes=[(n, n) for n in SIZES],
             append_images=[render(n) for n in SIZES if n != 256])
    big.save(os.path.join(os.path.dirname(os.path.abspath(out)), "osk.png"))
    print(f"wrote {out} ({', '.join(map(str, SIZES))}px) and osk.png")


if __name__ == "__main__":
    main()
