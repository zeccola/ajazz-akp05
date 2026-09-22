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
    `Device::keep_alive()` sends a `"CONNECT"` command (7 letters, not
    the usual 3 -- `crt_command` below handles that), and
    opendeck-akp05's `keepalive_task` calls it on a 10-second timer for
    the life of the connection, concurrently with reading input.
    This project's keepalive sends the DIS + bare LIG wake pair first,
    then CONNECT -- and that pair is NOT optional on this hardware:
    0.10.1 tried CONNECT alone (matching a literal reading of mirajazz,
    whose `initialize()` is lazy/first-call-only) and the panel stopped
    taking image updates after the very first tick. Confirmed on real
    hardware, reverted in 0.10.2. The catch is that the bare LIG
    carries brightness 0, so the wake pair on its own was a brightness
    reset every 10 seconds ("brightness keeps going back down"). Fix:
    set `device.brightness_provider` to a zero-arg callable returning
    0-100 and every tick sends LIG with that value right after the
    wake pair -- exactly the DIS / LIG / LIG-brightness prefix
    `upload_image` has always used. `open_device()` below
    does the same, transparently, for every caller -- including the
    one-shot scripts (set_image etc.), not just the long-running ones,
    since e.g. "all buttons" or a strip upload can run long enough to
    risk it too. A per-device write lock (also set up there) keeps the
    keepalive's own writes from interleaving with an in-progress
    multi-packet image upload, which has no per-chunk framing of its own
    to survive that.
  - A physical unplug (not just a protocol-level drop the keepalive
    guards against) makes os.read/os.write on the hidraw fd start
    raising OSError -- this used to just silently end the read and
    keepalive threads with no way for a caller to know or recover, so a
    disconnect needed a full process restart even after replugging.
    connect()/open_device() now take an on_disconnect callback for this
    (see their docstrings) -- the module itself only detects and reports
    it, reconnecting is the caller's job.

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
        try:
            return Image.open(STRIP_CACHE_PATH).convert("RGB")
        except OSError:
            # A truncated/corrupt cache (process killed mid-save) would
            # otherwise raise on every strip write from here on -- every
            # chunk/bar update dead, surviving restarts, with no way back
            # short of deleting the file by hand. Start over instead.
            print(f"Strip cache at {STRIP_CACHE_PATH} is unreadable -- starting from a black strip")
    return Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))


def save_strip_canvas(img):
    # Temp file + rename, so an interrupted save can't leave a partial
    # PNG behind for load_strip_canvas to choke on (see above).
    tmp = f"{STRIP_CACHE_PATH}.part"
    img.save(tmp, format="PNG")
    os.replace(tmp, STRIP_CACHE_PATH)


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

# Escape hatch: True restores the pre-0.10.3 wake prefix on every
# keepalive tick and image upload -- DIS, bare LIG (= brightness 0),
# LIG <pct>, and for uploads CLE <key> + STP before the BAT. That prefix
# made the whole panel blink dark for a moment every 10 seconds and on
# every text/icon update (the bare LIG is a real "backlight off"), and
# the CLE flashed the target button black before its new image. The
# default now skips the bare LIG and the pre-clear -- the reference
# implementations (mirajazz/opendeck-akp05) do neither before a BAT.
# DIS itself is kept: 0.10.1 showed the device stops taking updates
# without the wake pair in the keepalive. The add-on exposes this as
# its `legacy_wake_sequence` option in case a unit turns out to need
# the old form -- flip it and restart, no rebuild.
LEGACY_WAKE = False


def wake_sequence(total_len: int, brightness: int) -> list[list[int]]:
    """DIS + LIG <brightness>: wakes the panel and (re)asserts brightness
    in one go, with no pass through brightness 0. See LEGACY_WAKE."""
    pct = max(0, min(100, int(brightness)))
    commands = [crt_command("DIS", [], total_len)]
    if LEGACY_WAKE:
        commands.append(crt_command("LIG", [0x00, 0x00], total_len))
    commands.append(crt_command("LIG", [0x00, 0x00, pct], total_len))
    return commands


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

    def __init__(self, path: str, on_disconnect=None):
        self._fd = os.open(path, os.O_RDWR)
        self._handler = lambda data: None
        self._thread = None
        self._stop = threading.Event()
        # Called from whichever background thread notices the device is
        # gone -- a physical unplug means os.read/os.write start raising
        # OSError, and this used to just silently end the read/keepalive
        # threads with no way for a caller to know or recover, needing a
        # full process restart even after replugging. May be called more
        # than once (both the read loop and the keepalive loop can each
        # notice); callers are expected to de-duplicate/be idempotent.
        self._on_disconnect = on_disconnect
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
                data = b""
            if not data:
                # Either an OSError (e.g. ENODEV) or a clean EOF (empty
                # read) -- don't bet on which one a real unplugged hidraw
                # node actually produces, treat both as "device is gone".
                # Tested with a pipe: closing the write end gives EOF, not
                # OSError -- without this, that case span this loop at
                # 100% CPU forever instead of ever noticing.
                if self._on_disconnect is not None:
                    try:
                        self._on_disconnect()
                    except Exception:
                        pass
                return
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


def open_device(raw_data_handler=None, on_disconnect=None):
    """Open the AKP05 and register raw_data_handler for input reports
    (pass None if you're only sending commands). Sends no init commands
    at all -- caller is responsible for that. Returns the open device
    (pywinusb on Windows, LinuxHidDevice on Linux); caller must call
    .close() when done.

    on_disconnect, if given, is called (maybe more than once -- see
    LinuxHidDevice) from a background thread once a physical unplug is
    noticed (Linux: the read loop's own os.read failing; both platforms:
    the keepalive's write failing). It's the caller's job to actually
    reconnect -- this module only detects and reports it, so a one-shot
    script doesn't need to care, but a long-running caller (the add-on)
    can wire this up to retry.

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
        device = LinuxHidDevice(path, on_disconnect=on_disconnect)
        device.set_raw_data_handler(raw_data_handler or (lambda data: None))

    out_len = device.hid_caps.output_report_byte_length
    device._write_lock = threading.Lock()
    device._last_keepalive = time.monotonic()
    _start_keepalive(device, out_len, on_disconnect)
    return device


# Longest any real batch should ever hold the write lock. The biggest
# one is a full 800x112 strip upload: ~11 packets at 0.05s each, well
# under a second. Anything still waiting after this isn't contention,
# it's a write stuck on an unresponsive panel.
WRITE_LOCK_TIMEOUT = 20


class DeviceBusyError(RuntimeError):
    """Raised instead of waiting forever for the write lock.

    Every write used to block on this lock with no timeout, so a single
    write stuck on a wedged panel took down every other writer for good:
    the keepalive stopped, and (in the add-on) the thread handling
    incoming commands stopped with it, which looks from Home Assistant
    like icons, text and the display switch all going dead while button
    presses -- read on a separate thread -- carry on working. Failing the
    one command lets the caller log it and recover instead."""


def _write_all(device, buffers):
    """Caller must hold device._write_lock."""
    for buf in buffers:
        device.send_output_report(buf)
        time.sleep(0.05)


def _acquire_write_lock(device):
    if not device._write_lock.acquire(timeout=WRITE_LOCK_TIMEOUT):
        raise DeviceBusyError(
            f"the device write lock has been held for over {WRITE_LOCK_TIMEOUT}s -- "
            "an earlier write is stuck on the panel"
        )


def send_commands(device, buffers):
    """Holds the device's write lock for the whole batch, not just each
    individual write -- a multi-packet sequence like an image upload's
    raw JPEG chunks has no per-chunk framing, so if the keepalive
    thread's own commands interleaved between chunks (each write on its
    own would still leave that gap), it would corrupt the upload."""
    _acquire_write_lock(device)
    try:
        _write_all(device, buffers)
    finally:
        device._write_lock.release()


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


def upload_image(device, wire_key: int, jpeg_bytes: bytes, brightness: int = 50):
    """Wake (without wiping other keys), upload the image straight over
    whatever this wire-key currently shows, commit. No pass through
    brightness 0 and no pre-clear of the key, so the only visible change
    is old image -> new image (see LEGACY_WAKE for the old form).

    brightness: the wake-up sequence includes a LIG (brightness) command
    -- it sets the panel's brightness as a side effect of every upload.
    Long-running callers that track brightness (the add-on) must pass
    the current value, or every image refresh silently resets the panel
    to this default (that was a real, reported bug: "keeps going back
    to 50%"). One-shot CLI scripts can leave it."""
    out_len = device.hid_caps.output_report_byte_length
    prefix = wake_sequence(out_len, brightness)
    if LEGACY_WAKE:
        prefix += [
            crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len),
            crt_command("STP", [], out_len),
        ]
    # One lock hold for the ENTIRE upload: wake, BAT header + data, STP.
    # These used to be three separate send_commands() calls, so the
    # keepalive thread (every 10s) -- or the add-on's strip poller --
    # could slip its own packets in between the image data and the
    # commit. On real hardware that left the device in a state where it
    # accepted every later upload silently and displayed none of them,
    # until a physical power-cycle. (The old always-clear-first prefix
    # apparently reset that state on the next upload, which is why the
    # bug only surfaced once that clear was dropped for the no-blink
    # sequence.) The reference notes are explicit that uploads must be
    # serialised and allowed to settle before anything else is sent.
    _acquire_write_lock(device)
    try:
        _write_all(device, prefix)
        _write_all(device, build_bat_commands(wire_key, jpeg_bytes, out_len))
        _write_all(device, [crt_command("STP", [], out_len)])
    finally:
        device._write_lock.release()


def _keepalive_commands(device, out_len: int) -> list[list[int]]:
    # The wake pair is required here (see module docstring: CONNECT
    # alone kills image updates after the first tick). A long-running
    # caller that tracks brightness (the add-on) gets DIS + LIG <its
    # value> -- no pass through 0, so no blink -- via wake_sequence();
    # one-shot CLI callers keep the plain minimal init.
    provider = getattr(device, "brightness_provider", None)
    if provider is not None:
        commands = wake_sequence(out_len, provider())
    else:
        commands = minimal_init_sequence(out_len)
    commands.append(keep_alive_command(out_len))
    return commands


def keepalive_if_due(device) -> bool:
    """Send a keepalive if one is due; returns whether it went.

    The background thread below has to take the write lock like everyone
    else, so a run of image uploads starves it: each upload holds the
    lock for ~350ms, a queued backlog of them runs for many seconds, and
    this panel drops its connection after roughly 15 without a
    keepalive. Worse, that drop triggers a reconnect whose restore is
    itself a burst of uploads, which can starve the next keepalive in
    turn -- a spiral that shows up as the panel working for a few
    minutes and then dying. A caller working through a queue of uploads
    should call this between them so a backlog can't cost the
    connection."""
    last = getattr(device, "_last_keepalive", 0.0)
    if time.monotonic() - last < KEEPALIVE_INTERVAL:
        return False
    out_len = device.hid_caps.output_report_byte_length
    send_commands(device, _keepalive_commands(device, out_len))
    device._last_keepalive = time.monotonic()
    return True


def _keepalive_loop(device, out_len: int, stop_event: threading.Event, on_disconnect):
    # Checks twice as often as the interval, since keepalive_if_due()
    # skips when a caller (the add-on's upload worker) already sent one.
    while not stop_event.wait(KEEPALIVE_INTERVAL / 2):
        try:
            keepalive_if_due(device)
        except Exception:
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    pass
            return  # device is gone -- the read loop should notice too (Linux), or the caller's on_disconnect handles it


def _start_keepalive(device, out_len: int, on_disconnect=None):
    """Runs for the life of the connection -- without this the device
    drops its own connection after ~15s (see module docstring). Wraps
    device.close() so callers don't need to know this thread exists;
    every existing `device.close()` call site already stops it for free."""
    stop_event = threading.Event()
    thread = threading.Thread(target=_keepalive_loop, args=(device, out_len, stop_event, on_disconnect), daemon=True)
    thread.start()

    original_close = device.close

    def close_and_stop_keepalive():
        stop_event.set()
        original_close()

    device.close = close_and_stop_keepalive


def connect(raw_data_handler=None, full_init: bool = True, on_disconnect=None):
    """open_device() (which already starts the background keepalive --
    see its docstring) plus the init sequence. Set full_init=False to
    send only the minimal wake-up sequence, leaving brightness and
    existing key images untouched -- note this path is unverified on
    real hardware; the full sequence is what's actually been confirmed
    to unlock the device. on_disconnect: see open_device()."""
    device = open_device(raw_data_handler, on_disconnect=on_disconnect)
    out_len = device.hid_caps.output_report_byte_length
    sequence = build_init_sequence(out_len) if full_init else minimal_init_sequence(out_len)
    send_commands(device, sequence)
    return device
