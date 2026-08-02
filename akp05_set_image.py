"""
Upload a custom image to one (or all) of the AKP05's LCD keys, or to a
200px-wide chunk of the bottom touch-strip.

Confirmed from the `mirajazz` crate's images.rs and the `opendeck-akp05`
plugin's device constants for this exact PID group (0x0300:0x3004 and
siblings): key images are 112x112 JPEG, rotated 180 degrees, no
mirroring. Wire command is "BAT" (CRT-framed) carrying
[0x00, 0x00, size_hi, size_lo, key], followed by the raw JPEG bytes
chunked into 1024-byte output reports, committed with "STP".

IMPORTANT: the wire-level key index for image commands (BAT/CLE) is a
*different* numbering than the button-press index from akp05_buttons.py,
and it's not a simple offset -- confirmed with a calibration image
labeled per wire-key and read back off the physical device: wire-keys
6-15 address the 10 physical LCD buttons, but the two rows are swapped
relative to button-press numbering -- see akp05_device.py's
BUTTON_TO_WIRE_KEY for the full story. This script's "button" argument
uses the familiar 1-10 numbering and translates for you.

The strip (targets "11"-"14") is a different case: the device only
accepts a single full 800x112 write for the whole strip (wire-key 1,
confirmed by pixel-ruler calibration -- see akp05_device.py), there's no
real per-region addressing and no way to read the current image back. So
"11"-"14" here are a software-only convenience: each is a 200px-wide
slice (800/4) that gets pasted into a locally-cached copy of the strip
(akp05_device.STRIP_CACHE_PATH), then the whole composited image is
re-uploaded. akp05_set_strip.py / akp05_set_strip_color.py update that
same cache, so switching between the two stays consistent.

Usage:
    python akp05_set_image.py <button 1-10 | all> <path/to/image.png>
    python akp05_set_image.py <button 1-10 | all> clear
    python akp05_set_image.py <strip chunk 11-14> <path/to/image.png>
    python akp05_set_image.py <strip chunk 11-14> clear
"""

import sys

from PIL import Image

from akp05_device import (
    BUTTON_IMAGE_SIZE,
    BUTTON_TO_WIRE_KEY,
    STRIP_CHUNK_WIDTH,
    STRIP_IMAGE_SIZE,
    STRIP_WIRE_KEY,
    build_bat_commands,
    crt_command,
    encode_image,
    load_strip_canvas,
    open_device,
    save_strip_canvas,
    send_commands,
    upload_image,
)

STRIP_CHUNK_KEYS = {11: 0, 12: 1, 13: 2, 14: 3}  # target -> chunk index
STRIP_CHUNK_SIZE = (STRIP_CHUNK_WIDTH, STRIP_IMAGE_SIZE[1])


def parse_buttons(target: str) -> list[int]:
    if target == "all":
        return list(range(1, 11))
    button = int(target)
    if not 1 <= button <= 10:
        raise ValueError("button must be 1-10, or 'all'")
    return [button]


def handle_buttons(device, out_len, target: str, arg: str):
    buttons = parse_buttons(target)
    wire_keys = [BUTTON_TO_WIRE_KEY[b] for b in buttons]

    wake_up = [
        crt_command("DIS", [], out_len),
        crt_command("LIG", [0x00, 0x00], out_len),
        crt_command("LIG", [0x00, 0x00, 50], out_len),
    ]
    for wire_key in wire_keys:
        wake_up.append(crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len))
    wake_up.append(crt_command("STP", [], out_len))
    send_commands(device, wake_up)

    if arg == "clear":
        print(f"Cleared button(s): {buttons}")
    else:
        jpeg_bytes = encode_image(arg, BUTTON_IMAGE_SIZE)
        print(f"Encoded {arg} -> {len(jpeg_bytes)} bytes JPEG ({BUTTON_IMAGE_SIZE[0]}x{BUTTON_IMAGE_SIZE[1]})")
        for button, wire_key in zip(buttons, wire_keys):
            print(f"Uploading to button {button} (wire key {wire_key})...")
            send_commands(device, build_bat_commands(wire_key, jpeg_bytes, out_len))
        send_commands(device, [crt_command("STP", [], out_len)])


def handle_strip_chunk(device, target: str, arg: str):
    chunk_index = STRIP_CHUNK_KEYS[int(target)]
    x_offset = chunk_index * STRIP_CHUNK_WIDTH

    canvas = load_strip_canvas()
    if arg == "clear":
        patch = Image.new("RGB", STRIP_CHUNK_SIZE, (0, 0, 0))
    else:
        patch = Image.open(arg).convert("RGB").resize(STRIP_CHUNK_SIZE, Image.LANCZOS)
    canvas.paste(patch, (x_offset, 0))
    save_strip_canvas(canvas)

    jpeg_bytes = encode_image(canvas, STRIP_IMAGE_SIZE)
    print(f"Composited chunk {target} at x={x_offset} -> re-uploading full {STRIP_IMAGE_SIZE[0]}x{STRIP_IMAGE_SIZE[1]} strip ({len(jpeg_bytes)} bytes JPEG)")
    upload_image(device, STRIP_WIRE_KEY, jpeg_bytes)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    target, arg = sys.argv[1], sys.argv[2]
    device = open_device()
    out_len = device.hid_caps.output_report_byte_length
    try:
        if target in ("11", "12", "13", "14"):
            handle_strip_chunk(device, target, arg)
        else:
            handle_buttons(device, out_len, target, arg)
        print("Done.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
