#!/usr/bin/env python3
"""
generate_icon.py
Create a simple ICO (assets/ofisbir.ico) programmatically using Pillow.
Does NOT require ImageMagick or cairosvg.

Usage:
  python scripts/generate_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)
ICO_PATH = ASSETS / "ofisbir.ico"

def make_base_image(size):
    img = Image.new("RGBA", (size, size), (13,27,42,255))  # background
    draw = ImageDraw.Draw(img)
    # draw three building bars
    w = size
    h = size
    bar_w = int(w * 0.12)
    gap = int(w * 0.06)
    x0 = int(w * 0.12)
    colors = [(79,195,247,255),(79,195,247,200),(79,195,247,180)]
    for i in range(3):
        bx = x0 + i * (bar_w + gap)
        by = int(h * 0.2) + i*4
        bh = int(h * (0.6 - i*0.08))
        draw.rounded_rectangle([bx, by, bx+bar_w, by+bh], radius=3, fill=colors[i])
    # small text at bottom (if size large enough)
    if size >= 64:
        try:
            f = ImageFont.truetype("SegoeUI.ttf", max(10, size//12))
        except Exception:
            f = ImageFont.load_default()
        txt = "OFİSBİR"
        try:
            # Pillow >=8: textbbox available
            bbox = draw.textbbox((0, 0), txt, font=f)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        except Exception:
            try:
                tw, th = draw.textsize(txt, font=f)
            except Exception:
                # fallback estimate
                tw, th = (len(txt) * (size // 12), size // 12)
        draw.text(((w-tw)/2, h - th - 6), txt, font=f, fill=(223,247,255,255))
    return img

def main():
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = [make_base_image(s) for s in sizes]
    # Save as multi-size ICO
    imgs[0].save(ICO_PATH, format="ICO", sizes=[(s,s) for s in sizes])
    print("Icon created:", ICO_PATH)

if __name__ == "__main__":
    main()

