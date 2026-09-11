"""
Write text to the AKP05's touch strip (800x112), rendered in Roboto --
the same font/rendering as the Home Assistant add-on's per-button Text
entities (akp05_icons.build_text), just sized for the strip instead of
a 112x112 button.

Usage:
    python akp05_write_strip_text.py "Hello there"
    python akp05_write_strip_text.py "21.4C" "#00ff88"
    python akp05_write_strip_text.py "Kitchen: 21.4C" skyblue
    python akp05_write_strip_text.py clear

Text auto-shrinks to fit the strip's width, down to a minimum readable
size -- if it's still too wide even then, it overflows off the edges
rather than wrapping or scrolling. For genuinely long/scrolling
content, akp05_play_video.py can play any video instead, which covers
scrolling text too if you generate one.
"""

import sys

from PIL import Image, ImageColor

from akp05_device import STRIP_IMAGE_SIZE, STRIP_WIRE_KEY, encode_image, open_device, save_strip_canvas, upload_image
from akp05_icons import TEXT_COLOR, build_text


def parse_color(spec: str):
    if "," in spec:
        parts = [int(p.strip()) for p in spec.split(",")]
        if len(parts) != 3:
            raise ValueError("r,g,b triple must have exactly 3 values")
        return tuple(parts)
    if not spec.startswith("#"):
        spec = f"#{spec}" if all(c in "0123456789abcdefABCDEF" for c in spec) and len(spec) in (3, 6) else spec
    return ImageColor.getrgb(spec)


def main():
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    device = open_device()
    try:
        if arg == "clear":
            img = Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))
            print("Clearing strip...")
        else:
            color = parse_color(sys.argv[2]) if len(sys.argv) == 3 else TEXT_COLOR
            img = build_text(arg, size=STRIP_IMAGE_SIZE, color=color)
            print(f"Rendering {arg!r} in RGB{color}...")

        save_strip_canvas(img)
        jpeg_bytes = encode_image(img, STRIP_IMAGE_SIZE)
        upload_image(device, STRIP_WIRE_KEY, jpeg_bytes)
        print("Done.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
