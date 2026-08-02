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
  - Input reports carry a fixed 10-byte header
    [report_id, 'A','C','K', 0x00,0x00, 'O','K', 0x00,0x00], then
    [key_index, state] at bytes 10-11 (state: 1 = down, 0 = up).
  - Keys 1-10 are the LCD keys. Encoders send a one-shot report on twist
    (state always 0) and DOWN/UP reports on push, using these key indices:
      Encoder 1: press 0x37, twist 0xA0 (CCW) / 0xA1 (CW)
      Encoder 2: press 0x35, twist 0x50 (CCW) / 0x51 (CW)
      Encoder 3: press 0x33, twist 0x90 (CCW) / 0x91 (CW)
      Encoder 4: press 0x36, twist 0x70 (CCW) / 0x71 (CW)
  - The touch strip hasn't been captured yet.
"""

import sys
import time

import pywinusb.hid as hid

VENDOR_ID = 0x0300
PRODUCT_ID = 0x3004

KEY_IDX = 10
STATE_IDX = 11


def crt_command(command: str, payload: list[int], total_len: int) -> list[int]:
    buf = [0] * total_len
    buf[1:4] = [ord(c) for c in "CRT"]
    buf[6:9] = [ord(c) for c in command]
    buf[9 : 9 + len(payload)] = payload
    return buf


def build_init_sequence(total_len: int) -> list[list[int]]:
    return [
        crt_command("DIS", [], total_len),
        crt_command("LIG", [0x00, 0x00], total_len),
        crt_command("LIG", [0x00, 0x00, 50], total_len),  # set_brightness(50)
        crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], total_len),  # clear all keys
        crt_command("STP", [], total_len),
    ]


def connect(raw_data_handler):
    """Open the AKP05, run the init sequence, and register raw_data_handler
    for input reports. Returns the open pywinusb device; caller must
    call .close() when done."""
    devices = hid.HidDeviceFilter(vendor_id=VENDOR_ID, product_id=PRODUCT_ID).get_devices()
    if not devices:
        print(f"No device found for VID_{VENDOR_ID:04X} & PID_{PRODUCT_ID:04X}.")
        sys.exit(1)

    device = devices[0]
    device.open()
    device.set_raw_data_handler(raw_data_handler)

    out_len = device.hid_caps.output_report_byte_length
    for buf in build_init_sequence(out_len):
        device.send_output_report(buf)
        time.sleep(0.05)

    return device
