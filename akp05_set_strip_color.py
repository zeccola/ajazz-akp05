"""
Fill the AKP05's bottom touch-strip LCD with a solid color.

Usage:
    python akp05_set_strip_color.py <color>

<color> can be a hex code (#ff8800, ff8800), an "r,g,b" triple
(255,136,0), or any name PIL recognizes (red, orange, skyblue, ...).
"""

import sys

from PIL import Image, ImageColor

from akp05_device import STRIP_IMAGE_SIZE, STRIP_WIRE_KEY, encode_image, open_device, save_strip_canvas, upload_image


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
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    color = parse_color(sys.argv[1])
    print(f"Filling strip with RGB{color}...")

    img = Image.new("RGB", STRIP_IMAGE_SIZE, color)
    save_strip_canvas(img)
    jpeg_bytes = encode_image(img, STRIP_IMAGE_SIZE)

    device = open_device()
    try:
        upload_image(device, STRIP_WIRE_KEY, jpeg_bytes)
        print("Done.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
