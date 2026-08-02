"""
Upload a custom image to the AKP05's bottom touch-strip LCD.

Confirmed by binary search with a labeled pixel-ruler test image: the
strip is a single target (wire-key 1, not split into zones) with a
native resolution of 800x112 -- see akp05_device.py for the full story
(the 176x112 spec in opendeck-akp05's mappings.rs is wrong for this).

This overwrites the whole strip and updates the local strip cache (see
akp05_device.py's STRIP_CACHE_PATH) that akp05_set_image.py's chunk mode
(buttons "11"-"14") uses, so the two stay in sync.

Usage:
    python akp05_set_strip.py <path/to/image.png>
    python akp05_set_strip.py clear
"""

import sys

from PIL import Image

from akp05_device import STRIP_IMAGE_SIZE, STRIP_WIRE_KEY, encode_image, open_device, save_strip_canvas, upload_image


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "clear":
        canvas = Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))
    else:
        canvas = Image.open(arg).convert("RGB").resize(STRIP_IMAGE_SIZE, Image.LANCZOS)

    save_strip_canvas(canvas)
    jpeg_bytes = encode_image(canvas, STRIP_IMAGE_SIZE)
    print(f"{'Cleared' if arg == 'clear' else f'Encoded {arg} ->'} {len(jpeg_bytes)} bytes JPEG ({STRIP_IMAGE_SIZE[0]}x{STRIP_IMAGE_SIZE[1]})")

    device = open_device()
    try:
        upload_image(device, STRIP_WIRE_KEY, jpeg_bytes)
        print("Done.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
