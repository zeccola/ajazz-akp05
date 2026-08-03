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
  - The device needs a periodic keepalive or it drops the connection
    after roughly 15 seconds (screen blanks, input reporting stops until
    a button press partially wakes it, but state/images are lost) --
    confirmed by checking both reference implementations, since this
    project's own testing hit exactly that symptom: mirajazz's
    `Device::keep_alive()` re-sends the minimal wake sequence (DIS +
    LIG) plus a `"CONNECT"` command (7 letters, not the usual 3 --
    `crt_command` below handles that), and opendeck-akp05's
    `keepalive_task` calls it on a 10-second timer for the life of the
    connection, concurrently with reading input. `open_device()` below
    does the same, transparently, for every caller -- including the
    one-shot scripts (set_image etc.), not just the long-running ones,
    since e.g. "all buttons" or a strip upload can run long enough to
    risk it too. A per-device write lock (also set up there) keeps the
    keepalive's own writes from interleaving with an in-progress
    multi-packet image upload, which has no per-chunk framing of its own
    to survive that.

Platform support: Windows uses `pywinusb` (wraps the native HID API, which
always wants a leading report-ID byte even though this device has no real
report IDs -- that's the buf[0] every command below leaves as 0x00). Linux
(for running this inside a Home Assistant add-on container) talks to
/dev/hidrawN directly:
  - hidraw has no equivalent leading byte for a no-report-ID device, so
    writes send buf[1:] (dropping that pad byte) and reads come back
    without it too.
  - That shifts KEY_IDX/STATE_IDX by one on Linux (9/10 instead of 10/11)
    -- this is inferred from the hidraw report-ID convention, not yet
    confirmed against real hardware. First thing to check once the device
    is plugged into a Linux box: print raw report bytes and verify button
    presses land where expected before trusting decoded events.
"""

import os
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import pywinusb.hid as hid

VENDOR_ID = 0x0300
PRODUCT_ID = 0x3004

KEY_IDX = 10 if IS_WINDOWS else 9
STATE_IDX = 11 if IS_WINDOWS else 10

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
    """Every command so far has been 3 letters (DIS/LIG/CLE/STP/BAT), but
    the keepalive's "CONNECT" is 7 -- so this places the payload right
    after wherever the command word actually ends, instead of the fixed
    offset 9 a 3-letter-only version would hardcode (which would have
    silently corrupted the buffer length for anything longer, since
    Python list-slice assignment resizes to fit)."""
    buf = [0] * total_len
    buf[1:4] = [ord(c) for c in "CRT"]
    cmd_bytes = [ord(c) for c in command]
    buf[6 : 6 + len(cmd_bytes)] = cmd_bytes
    payload_start = 6 + len(cmd_bytes)
    buf[payload_start : payload_start + len(payload)] = payload
    return buf


def minimal_init_sequence(total_len: int) -> list[list[int]]:
    """The bare minimum to wake the device (mirajazz's `initialize()`).
    Does not touch brightness or existing key images."""
    return [
        crt_command("DIS", [], total_len),
        crt_command("LIG", [0x00, 0x00], total_len),
    ]


KEEPALIVE_INTERVAL = 10  # seconds -- matches opendeck-akp05's keepalive_task


def keep_alive_command(total_len: int) -> list[int]:
    return crt_command("CONNECT", [], total_len)


def build_init_sequence(total_len: int) -> list[list[int]]:
    """Full startup sequence (matches what opendeck-akp05 does on every
    connect). Clears all key images -- don't use this before an image
    upload you don't want immediately wiped."""
    return minimal_init_sequence(total_len) + [
        crt_command("LIG", [0x00, 0x00, 50], total_len),  # set_brightness(50)
        crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], total_len),  # clear all keys
        crt_command("STP", [], total_len),
    ]


class _HidCaps:
    def __init__(self, output_report_byte_length: int):
        self.output_report_byte_length = output_report_byte_length


class LinuxHidDevice:
    """Duck-types the subset of pywinusb's device object the rest of this
    module calls (send_output_report / set_raw_data_handler / close /
    hid_caps.output_report_byte_length), backed by a /dev/hidrawN node.
    Keeping that surface identical means crt_command/build_bat_commands/
    upload_image/connect etc. need no platform-specific branches at all."""

    REPORT_LENGTH = 1024  # matches the Windows packet_size (total_len - 1)

    def __init__(self, path: str):
        self._fd = os.open(path, os.O_RDWR)
        self._handler = lambda data: None
        self._thread = None
        self._stop = threading.Event()
        self.hid_caps = _HidCaps(self.REPORT_LENGTH + 1)

    def set_raw_data_handler(self, handler):
        self._handler = handler or (lambda data: None)
        if self._thread is None:
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                data = os.read(self._fd, self.REPORT_LENGTH)
            except OSError:
                return
            if data:
                self._handler(list(data))

    def send_output_report(self, buf):
        # buf[0] is the Windows-only pad/report-ID byte (see module
        # docstring) -- hidraw wants the report itself, with no prefix.
        os.write(self._fd, bytes(buf[1:]))

    def close(self):
        self._stop.set()
        try:
            os.close(self._fd)
        except OSError:
            pass


def _find_hidraw_path(vendor_id: int, product_id: int) -> str | None:
    base = "/sys/class/hidraw"
    if not os.path.isdir(base):
        return None
    suffix = f"{vendor_id:08X}:{product_id:08X}"
    for name in sorted(os.listdir(base)):
        try:
            with open(os.path.join(base, name, "device", "uevent")) as f:
                content = f.read()
        except OSError:
            continue
        for line in content.splitlines():
            if line.startswith("HID_ID=") and line.split("=", 1)[1].upper().endswith(suffix):
                return os.path.join("/dev", name)
    return None


def open_device(raw_data_handler=None):
    """Open the AKP05 and register raw_data_handler for input reports
    (pass None if you're only sending commands). Sends no init commands
    at all -- caller is responsible for that. Returns the open device
    (pywinusb on Windows, LinuxHidDevice on Linux); caller must call
    .close() when done.

    Every caller gets a background keepalive (see module docstring) and
    a write lock from here -- not just connect() -- since even the
    one-shot scripts (set_image, set_brightness, ...) are long enough in
    the "all buttons"/strip case to risk the same ~15s drop, and it
    costs nothing when they finish well under that."""
    if IS_WINDOWS:
        devices = hid.HidDeviceFilter(vendor_id=VENDOR_ID, product_id=PRODUCT_ID).get_devices()
        if not devices:
            print(f"No device found for VID_{VENDOR_ID:04X} & PID_{PRODUCT_ID:04X}.")
            sys.exit(1)
        device = devices[0]
        device.open()
        device.set_raw_data_handler(raw_data_handler or (lambda data: None))
    else:
        path = _find_hidraw_path(VENDOR_ID, PRODUCT_ID)
        if path is None:
            print(f"No hidraw device found for VID_{VENDOR_ID:04X} & PID_{PRODUCT_ID:04X}.")
            sys.exit(1)
        device = LinuxHidDevice(path)
        device.set_raw_data_handler(raw_data_handler or (lambda data: None))

    out_len = device.hid_caps.output_report_byte_length
    device._write_lock = threading.Lock()
    _start_keepalive(device, out_len)
    return device


def send_commands(device, buffers):
    """Holds the device's write lock for the whole batch, not just each
    individual write -- a multi-packet sequence like an image upload's
    raw JPEG chunks has no per-chunk framing, so if the keepalive
    thread's own commands interleaved between chunks (each write on its
    own would still leave that gap), it would corrupt the upload."""
    with device._write_lock:
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


def _keepalive_loop(device, out_len: int, stop_event: threading.Event):
    while not stop_event.wait(KEEPALIVE_INTERVAL):
        try:
            send_commands(device, minimal_init_sequence(out_len) + [keep_alive_command(out_len)])
        except Exception:
            return  # device is gone -- whatever's reading input reports will notice too


def _start_keepalive(device, out_len: int):
    """Runs for the life of the connection -- without this the device
    drops its own connection after ~15s (see module docstring). Wraps
    device.close() so callers don't need to know this thread exists;
    every existing `device.close()` call site already stops it for free."""
    stop_event = threading.Event()
    thread = threading.Thread(target=_keepalive_loop, args=(device, out_len, stop_event), daemon=True)
    thread.start()

    original_close = device.close

    def close_and_stop_keepalive():
        stop_event.set()
        original_close()

    device.close = close_and_stop_keepalive


def connect(raw_data_handler=None, full_init: bool = True):
    """open_device() (which already starts the background keepalive --
    see its docstring) plus the init sequence. Set full_init=False to
    send only the minimal wake-up sequence, leaving brightness and
    existing key images untouched -- note this path is unverified on
    real hardware; the full sequence is what's actually been confirmed
    to unlock the device."""
    device = open_device(raw_data_handler)
    out_len = device.hid_caps.output_report_byte_length
    sequence = build_init_sequence(out_len) if full_init else minimal_init_sequence(out_len)
    send_commands(device, sequence)
    return device
