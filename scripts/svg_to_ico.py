#!/usr/bin/env python3
"""
svg_to_ico.py
Convert assets/ofisbir.svg -> assets/ofisbir.ico using Python (no ImageMagick).
Requires: cairosvg, pillow

Usage:
  pip install cairosvg pillow
  python scripts/svg_to_ico.py
"""
from pathlib import Path
from io import BytesIO
import sys
import traceback

def main():
    try:
        import cairosvg
        from PIL import Image
    except Exception:
        print("Missing dependencies. Install with: python -m pip install cairosvg pillow", file=sys.stderr)
        raise

    ROOT = Path(__file__).resolve().parents[1]
    SVG = ROOT / "assets" / "ofisbir.svg"
    ICO = ROOT / "assets" / "ofisbir.ico"

    if not SVG.exists():
        print(f"SVG not found: {SVG}", file=sys.stderr)
        sys.exit(1)

    print("Rendering SVG to PNG in-memory...")
    png_bytes = cairosvg.svg2png(url=str(SVG))
    img = Image.open(BytesIO(png_bytes)).convert("RGBA")

    # Ensure square canvas by expanding transparent background if needed
    size = max(img.width, img.height)
    if img.width != img.height:
        new = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        new.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
        img = new

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    print(f"Saving ICO to {ICO} with sizes {sizes} ...")
    img.save(ICO, format="ICO", sizes=sizes)
    print("Done.")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(3)

