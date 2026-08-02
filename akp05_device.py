"""
Shared connection/init logic for the Ajazz AKP05 (VID_0300 & PID_3004).

Confirmed from a real capture, cross-checked against the `mirajazz` Rust
crate (github.com/4ndv/mirajazz) and the `opendeck-akp05` OpenDeck plugin
(github.com/ambiso/opendeck-akp05), which both target this exact device:

  - The device is a single vendor-defined HID collection (usage page
    0xFFA0, usage 0x01), no report IDs, no Feature reports.
  - It stays silent over USB until it receives an init sequence. Output
    commands are framed as [report_id=0x00, 'C','R','T', 0x00,0x00,
    <3-letter command>, payload...], zero-padded to 1 + packet_size bytes
    (1025 here, since this device uses 1024-byte packets): "DIS", then
    "LIG" (brightness) twice, "CLE" (clear all keys), "STP" (commit).
    The DIS + first LIG pair mirrors mirajazz's own lazy `initialize()`
    (the true minimum to wake the device); the brightness/clear/commit
    steps mirror what the real opendeck-akp05 plugin does on every
    startup, which is what we confirmed unlocks button reporting.
  - Key images: 112x112 JPEG, rotated 180 degrees, no mirroring (from
    opendeck-akp05's device constants for this exact PID group). Upload
    command is "BAT", framed the same way, payload
    [0x00, 0x00, size_hi, size_lo, wire_key], followed by the raw JPEG
    bytes chunked into packet_size-byte (1024) output reports, then a
    "STP" to commit.
  - IMPORTANT: the image wire-key numbering is NOT the same as the
    button-press key numbering below, and it's not a simple offset --
    confirmed by flashing a numbered label to each wire-key and reading
    the layout back off the physical device. Wire-keys 6-15 address the
    10 physical LCD buttons, but the two rows are swapped relative to
    button-press numbering: the bottom row (button-press keys 6-10) uses
    wire-keys 6-10 directly, while the top row (button-press keys 1-5)
    uses wire-keys 11-15. See akp05_set_image.py's BUTTON_TO_WIRE_KEY.
  - The bottom touch-strip is wire-key 1, a single target -- NOT split
    into zones (an earlier theory, disproven: small images at wire-keys
    1-4 looked like separate "zones" but were actually just small icon
    slots above the encoders that happen to share memory with the strip;
    an oversized image at wire-key 1 overflows into the button row and
    those icon slots rather than being rejected). Confirmed by binary
    search with a labeled pixel-ruler test image: the strip's real
    native resolution is 800x112 (NOT the 176x112 in opendeck-akp05's
    mappings.rs -- that number is wrong for this purpose, or means
    something else in their code). wire-key 5 isn't wired to anything
    and times out on write.
  - Input reports carry a fixed 10-byte header
    [report_id, 'A','C','K', 0x00,0x00, 'O','K', 0x00,0x00], then
    [key_index, state] at bytes 10-11 (state: 1 = down, 0 = up).
  - Keys 1-10 are the LCD keys (this is the *button-press* numbering --
    see above, it doesn't match the image wire-key numbering). Encoders
    send a one-shot report on twist (state always 0) and DOWN/UP reports
    on push, using these key indices:
      Encoder 1: press 0x37, twist 0xA0 (CCW) / 0xA1 (CW)
      Encoder 2: press 0x35, twist 0x50 (CCW) / 0x51 (CW)
      Encoder 3: press 0x33, twist 0x90 (CCW) / 0x91 (CW)
      Encoder 4: press 0x36, twist 0x70 (CCW) / 0x71 (CW)
  - The touch strip's own input events (touch/swipe) haven't been
    captured yet.
"""

import os
import sys
import time

import pywinusb.hid as hid

VENDOR_ID = 0x0300
PRODUCT_ID = 0x3004

KEY_IDX = 10
STATE_IDX = 11

BUTTON_IMAGE_SIZE = (112, 112)
STRIP_IMAGE_SIZE = (800, 112)
STRIP_WIRE_KEY = 1
STRIP_CHUNK_WIDTH = 200  # 800 / 4

# button (1-10, button-press numbering) -> wire-key (image command numbering)
BUTTON_TO_WIRE_KEY = {
    1: 11, 2: 12, 3: 13, 4: 14, 5: 15,
    6: 6, 7: 7, 8: 8, 9: 9, 10: 10,
}

# The device has no way to read an image back, and the strip only
# accepts a full 800x112 write (no per-region addressing) -- so to
# update just one 200px chunk without erasing the rest, we keep a local
# cache of what's currently on the strip (in logical, non-rotated
# orientation) and re-composite + re-upload the whole thing each time.
STRIP_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".akp05_strip_cache.png")


def load_strip_canvas():
    from PIL import Image

    if os.path.exists(STRIP_CACHE_PATH):
        return Image.open(STRIP_CACHE_PATH).convert("RGB")
    return Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))


def save_strip_canvas(img):
    img.save(STRIP_CACHE_PATH)


def crt_command(command: str, payload: list[int], total_len: int) -> list[int]:
    buf = [0] * total_len
    buf[1:4] = [ord(c) for c in "CRT"]
    buf[6:9] = [ord(c) for c in command]
    buf[9 : 9 + len(payload)] = payload
    return buf


def minimal_init_sequence(total_len: int) -> list[list[int]]:
    """The bare minimum to wake the device (mirajazz's `initialize()`).
    Does not touch brightness or existing key images."""
    return [
        crt_command("DIS", [], total_len),
        crt_command("LIG", [0x00, 0x00], total_len),
    ]


def build_init_sequence(total_len: int) -> list[list[int]]:
    """Full startup sequence (matches what opendeck-akp05 does on every
    connect). Clears all key images -- don't use this before an image
    upload you don't want immediately wiped."""
    return minimal_init_sequence(total_len) + [
        crt_command("LIG", [0x00, 0x00, 50], total_len),  # set_brightness(50)
        crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], total_len),  # clear all keys
        crt_command("STP", [], total_len),
    ]


def open_device(raw_data_handler=None):
    """Open the AKP05 and register raw_data_handler for input reports
    (pass None if you're only sending commands). Sends no init commands
    at all -- caller is responsible for that. Returns the open pywinusb
    device; caller must call .close() when done."""
    devices = hid.HidDeviceFilter(vendor_id=VENDOR_ID, product_id=PRODUCT_ID).get_devices()
    if not devices:
        print(f"No device found for VID_{VENDOR_ID:04X} & PID_{PRODUCT_ID:04X}.")
        sys.exit(1)

    device = devices[0]
    device.open()
    device.set_raw_data_handler(raw_data_handler or (lambda data: None))
    return device


def send_commands(device, buffers):
    for buf in buffers:
        device.send_output_report(buf)
        time.sleep(0.05)


def encode_image(image_or_path, size, rotate180: bool = True) -> bytes:
    """Load (if given a path) or use a PIL Image, resize to `size` if
    needed, rotate 180 degrees (device convention), and JPEG-encode."""
    import io

    from PIL import Image

    img = image_or_path if hasattr(image_or_path, "convert") else Image.open(image_or_path)
    img = img.convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    if rotate180:
        img = img.transpose(Image.ROTATE_180)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def build_bat_commands(wire_key: int, jpeg_bytes: bytes, total_len: int):
    """The 'BAT' image-upload command: a header report carrying the JPEG
    size and target wire-key, followed by the raw JPEG bytes chunked
    into packet-sized output reports."""
    packet_size = total_len - 1
    header_payload = [0x00, 0x00, (len(jpeg_bytes) >> 8) & 0xFF, len(jpeg_bytes) & 0xFF, wire_key]
    yield crt_command("BAT", header_payload, total_len)

    for offset in range(0, len(jpeg_bytes), packet_size):
        chunk = jpeg_bytes[offset : offset + packet_size]
        buf = [0] * total_len
        buf[1 : 1 + len(chunk)] = list(chunk)
        yield buf


def upload_image(device, wire_key: int, jpeg_bytes: bytes):
    """Wake (without wiping other keys), clear just this wire-key, upload
    the image, commit. Uses the proven wake-up sequence, scoped so it
    doesn't disturb other buttons/the strip."""
    out_len = device.hid_caps.output_report_byte_length
    send_commands(
        device,
        [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
            crt_command("LIG", [0x00, 0x00, 50], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len),
            crt_command("STP", [], out_len),
        ],
    )
    send_commands(device, build_bat_commands(wire_key, jpeg_bytes, out_len))
    send_commands(device, [crt_command("STP", [], out_len)])


def connect(raw_data_handler=None, full_init: bool = True):
    """open_device() plus the init sequence. Set full_init=False to send
    only the minimal wake-up sequence, leaving brightness and existing
    key images untouched -- note this path is unverified on real
    hardware; the full sequence is what's actually been confirmed to
    unlock the device."""
    device = open_device(raw_data_handler)
    out_len = device.hid_caps.output_report_byte_length
    sequence = build_init_sequence(out_len) if full_init else minimal_init_sequence(out_len)
    send_commands(device, sequence)
    return device
