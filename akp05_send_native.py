"""
Debug helper: upload an image to any wire-key at its NATIVE resolution,
no resizing. Used for calibrating the touch-strip zone dimensions, which
aren't nailed down yet (unlike the buttons, which are a confirmed
112x112). See akp05_device.py for what's confirmed vs. still guesswork.

Usage:
    python akp05_send_native.py <wire_key> <path/to/image.png>
"""

import io
import sys

from PIL import Image

from akp05_device import crt_command, open_device, send_commands


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    wire_key = int(sys.argv[1])
    path = sys.argv[2]

    img = Image.open(path).convert("RGB")
    img = img.transpose(Image.ROTATE_180)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()
    print(f"{path}: {img.size[0]}x{img.size[1]} -> {len(jpeg_bytes)} bytes JPEG")

    device = open_device()
    out_len = device.hid_caps.output_report_byte_length
    try:
        wake_up = [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
            crt_command("LIG", [0x00, 0x00, 50], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len),
            crt_command("STP", [], out_len),
        ]
        send_commands(device, wake_up)

        packet_size = out_len - 1
        header_payload = [0x00, 0x00, (len(jpeg_bytes) >> 8) & 0xFF, len(jpeg_bytes) & 0xFF, wire_key]
        cmds = [crt_command("BAT", header_payload, out_len)]
        for offset in range(0, len(jpeg_bytes), packet_size):
            chunk = jpeg_bytes[offset : offset + packet_size]
            b = [0] * out_len
            b[1 : 1 + len(chunk)] = list(chunk)
            cmds.append(b)
        send_commands(device, cmds)
        send_commands(device, [crt_command("STP", [], out_len)])
        print(f"Sent to wire key {wire_key}.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
