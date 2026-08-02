# Ajazz AKP05 control scripts

Python scripts to read button/encoder input and set LCD images/brightness on
an Ajazz AKP05 (`VID_0300 & PID_3004`, and likely the AKP05E/AKP05CN Pro/AKP05
Pro siblings on the same USB IDs family) over raw HID — no vendor software,
no Rust toolchain required.

Everything here was reverse-engineered from real hardware, cross-checked
against two open-source references for the same device family:
[`mirajazz`](https://github.com/4ndv/mirajazz) (Rust HID library) and
[`opendeck-akp05`](https://github.com/ambiso/opendeck-akp05) (OpenDeck
plugin). Where something is confirmed against real hardware vs. still a
guess is called out in `akp05_device.py`'s module docstring — read that
first if you're modifying the protocol code.

## Setup

```
pip install pywinusb pillow
```

Windows only (`pywinusb` wraps the native Windows HID API). No compiler or
extra drivers needed.

## Scripts

### Reading input

- **`akp05_buttons.py`** — clean, human-readable button/encoder events, no
  raw bytes. This is what you want day to day.
  ```
  python akp05_buttons.py
  ```
  ```
  [21:26:53] Key 1 pressed
  [21:26:54] Key 1 released
  [21:27:45] Encoder 1 turned CCW
  [21:27:47] Encoder 1 button pressed
  ```

- **`akp05_listener.py`** — raw/debug listener. Shows trimmed hex plus a
  best-effort decode for every input report, including anything not yet
  mapped (e.g. touch-strip gestures — not captured yet). Use this to
  investigate new input types.
  ```
  python akp05_listener.py
  ```

### Writing to the LCDs

- **`akp05_set_image.py`** — upload an image to a button (1–10), or to a
  200px-wide quarter of the touch strip (11–14).
  ```
  python akp05_set_image.py 3 icon.png       # button 3
  python akp05_set_image.py all icon.png     # all 10 buttons
  python akp05_set_image.py 5 clear          # blank button 5
  python akp05_set_image.py 12 icon.png      # strip, 2nd quarter (px 200-400)
  ```

- **`akp05_set_strip.py`** — upload an image across the whole strip in one
  shot (auto-resized to 800x112).
  ```
  python akp05_set_strip.py banner.png
  python akp05_set_strip.py clear
  ```

- **`akp05_set_strip_color.py`** — fill the whole strip with a solid color.
  ```
  python akp05_set_strip_color.py "#ff8800"
  python akp05_set_strip_color.py "255,136,0"
  python akp05_set_strip_color.py skyblue
  ```

- **`akp05_set_brightness.py`** — set display brightness (buttons + strip
  together).
  ```
  python akp05_set_brightness.py 75
  ```

### Internals / debugging

- **`akp05_device.py`** — shared module: connection handling, the CRT-framed
  command protocol, confirmed key/wire-key mappings, image upload helpers,
  and the local strip-image cache. Everything else imports from here. Its
  module docstring is the source of truth for the protocol.
- **`akp05_send_native.py`** — send an image to any wire-key at its native
  pixel size, no resizing. Used to calibrate the button/strip dimensions;
  handy again if you ever need to probe a new wire-key or image size.
- **`list_hid.py`** — enumerate the device's HID collection(s) and print
  their capabilities (report lengths, usage page, etc).

## Key facts about the protocol

(Full detail, including exactly how each of these was confirmed, is in
`akp05_device.py`'s docstring.)

- One vendor-defined HID interface (usage page `0xFFA0`, usage `0x01`).
  Output commands are framed `[0x00, 'C','R','T', 0x00,0x00, <3-letter cmd>,
  payload...]`, zero-padded to 1025 bytes. The device sends nothing over USB
  until it gets an init sequence (`DIS`, `LIG`, `LIG`-brightness, `CLE`,
  `STP`).
- **Button-press input** numbering (`akp05_buttons.py`): keys 1–10, plus
  encoder press/twist codes — see the table in `akp05_device.py`.
- **Image upload** ("BAT" command) uses a *different* numbering than
  button-press input, and it's not a fixed offset — it was mapped by
  flashing numbered labels to each wire-key and reading the layout back
  off the device:
  - wire-keys 6–10 → bottom row of buttons (button-press keys 6–10)
  - wire-keys 11–15 → top row of buttons (button-press keys 1–5)
  - wire-key 1 → the touch strip (single target, native size 800x112 —
    the `176x112` spec in opendeck-akp05's own source is wrong for this)
  - wire-key 5 isn't wired to anything and times out on write
  - `akp05_set_image.py`'s `BUTTON_TO_WIRE_KEY` table and strip-chunk logic
    handle all of this translation for you.
- Button/strip images: 112x112 / 800x112 JPEG respectively, rotated 180°,
  no mirroring.
- The strip only accepts a full 800x112 write — there's no way to update
  part of it, or read back what's currently shown. `akp05_set_image.py`'s
  chunk mode (targets 11-14) works around this with a local image cache
  (`.akp05_strip_cache.png`) that's composited and re-uploaded on every
  write; `akp05_set_strip.py` / `akp05_set_strip_color.py` keep that cache
  in sync too.

## Not yet figured out

- The touch strip's own input (touch/swipe gestures) hasn't been captured.
  Run `akp05_listener.py` and touch the strip — it'll show up as
  `(unrecognized)` with raw bytes, which is the starting point for mapping
  it.
- The `"MAI"` command (mentioned in some third-party docs as a dedicated
  touch-strip-image command, distinct from `"BAT"`) was tried and produced
  no visible effect — `"BAT"` to wire-key 1 is what actually works.
