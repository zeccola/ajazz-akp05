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
pip install pywinusb pillow requests flask
```

Windows only (`pywinusb` wraps the native Windows HID API). No compiler or
extra drivers needed. `requests`/`flask` are only needed for the Home
Assistant bridge and its web UI.

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
  together). `off` dims to 0% and clears everything to black (destructive —
  wipes images, `on` can't restore them); `on` just restores 100% brightness.
  ```
  python akp05_set_brightness.py 75
  python akp05_set_brightness.py off
  python akp05_set_brightness.py on
  ```

### Home Assistant

These two scripts run on a Windows PC with the device plugged into *it*,
talking to HA over its REST API from the outside. If your HA install is
Home Assistant OS/Supervised and you can plug the AKP05 into that host
directly instead, see
["Running natively on Home Assistant OS"](#running-natively-on-home-assistant-os)
below — no separate always-on PC needed, and the device shows up as a
real HA device with native automation triggers rather than a config file.

- **`akp05_homeassistant.py`** — bridge button/encoder presses to Home
  Assistant service calls over its REST API (e.g. button 1 toggles
  `light.bedroom_lights`).
  Setup:
  1. In Home Assistant: profile → Security → Long-Lived Access Tokens →
     Create Token.
  2. Copy `ha_config.example.json` to `ha_config.json` (git-ignored — holds
     your token, don't commit it) and fill in `base_url`, `token`, and a
     `bindings` map from event IDs to just an entity ID, e.g.
     ```json
     "button_1": { "entity_id": "light.bedroom_lights" }
     ```
     Domain (the part before the dot, e.g. `light`) is read off the entity
     ID automatically; the service called defaults to `toggle` (covers
     light/switch/fan/etc). Add an explicit `"service"` to override it for
     entities that need something else, e.g.
     `{ "entity_id": "scene.movie_night", "service": "turn_on" }`.
     Bindable event IDs: `button_1`..`button_10`, `encoder_1_button`..
     `encoder_4_button`, `encoder_1_cw`/`encoder_1_ccw` (and `_2_`/`_3_`/`_4_`).
     Twists (`_cw`/`_ccw`) fire once per detent, not press/release, so they
     suit *incremental* actions better than the default toggle — add
     `"data"` for extra service parameters beyond `entity_id`, confirmed
     live against a real dimmable light:
     ```json
     "encoder_1_cw":  { "entity_id": "light.bedroom_lights", "service": "turn_on", "data": {"brightness_step_pct": 10} },
     "encoder_1_ccw": { "entity_id": "light.bedroom_lights", "service": "turn_on", "data": {"brightness_step_pct": -10} }
     ```
     or for a media player's volume, no `data` needed:
     ```json
     "encoder_2_cw":  { "entity_id": "media_player.living_room", "service": "volume_up" },
     "encoder_2_ccw": { "entity_id": "media_player.living_room", "service": "volume_down" }
     ```
  3. Run it:
     ```
     python akp05_homeassistant.py
     ```
  Uses the same full wake-up as the listener scripts, so starting it clears
  your button images — flash icons after starting, or just leave it running.

  Optional per-`button_N` binding: `"icon": "floor-lamp-outline"` (or any
  [Material Design Icons](https://pictogrammers.com/library/mdi/) name —
  the same set Home Assistant's own UI uses). After the service call, it
  queries the entity's real state from HA and paints that icon on the
  button, green (on) / red (off) / gray (unknown) — also synced once on
  startup. See `akp05_icons.py`.

- **`akp05_web.py`** — local web UI for editing `ha_config.json`'s bindings
  instead of hand-editing JSON, **and** for starting/stopping
  `akp05_homeassistant.py` itself, so this is the only script you need to
  run directly day to day.
  ```
  python akp05_web.py
  ```
  Open `http://127.0.0.1:5757`. Local-only (binds to 127.0.0.1), no login —
  same trust boundary as editing the file directly. The access-token field
  is always blank on load (never echoes the saved secret back into the
  page); leave it blank on save to keep the current token. Buttons and
  encoders (push + clockwise + counter-clockwise, all 4) are listed as
  separate labeled rows — "Data" accepts a JSON object for extra service
  parameters (see the encoder brightness/volume examples above); invalid
  JSON there is rejected with an error and not saved, rather than silently
  dropped.

  The "Bridge" section runs `akp05_homeassistant.py` as a child process —
  Start/Stop/Restart buttons, a running/stopped status, and a snapshot of
  its recent output (reload the page or press a button to refresh it;
  it's not a live-updating stream). Saving bindings does **not**
  auto-restart the bridge if it's already running, on purpose — hit
  Restart yourself so applying new config is a visible, explicit action.

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
- **`akp05_icons.py`** — renders any [Material Design Icons](https://pictogrammers.com/library/mdi/)
  name (e.g. `floor-lamp-outline`, `fan`, `ceiling-light`) colored by
  on/off/unknown state, using the same icon set Home Assistant's own UI
  uses. First use downloads and caches the MDI font + name lookup
  (~3MB total, one-time, `.mdi_cache/` — git-ignored); after that it's
  fully offline. No fixed icon list to maintain — any name works.

## Running natively on Home Assistant OS

If your HA install is Home Assistant OS (or Supervised) and the AKP05 is
plugged straight into that host, you don't need a separate always-on
Windows PC at all — a single Supervisor add-on exposes it as a real HA
device (buttons/encoders as native automation triggers, brightness as a
light entity) via **MQTT discovery**, instead of the config-file-driven
bridge described above. Requires an MQTT broker (e.g. the official
Mosquitto add-on) — no separate integration to install.

**Why an add-on at all**: Home Assistant OS only grants raw USB/HID
access to add-on containers (via their `usb`/`udev` options) — Core's own
container doesn't get that for arbitrary hardware. `akp05_bridge/` is the
only thing that ever talks to the device (it runs the same protocol code
as everything else here, plus a new Linux hidraw backend added to
`akp05_device.py`), and publishes everything to HA over MQTT rather than
needing a matching custom integration on the Core side.

### Install

Push this repo to GitHub, then add it as an add-on repository:
**Settings → Add-ons → Add-on Store → ⋮ (top-right) → Repositories**,
paste `https://github.com/zeccola/ajazz-akp05`, **Add**, then install
"AKP05 Bridge" and **Start** it — full instructions, including a manual/
no-GitHub-push fallback via Samba/SSH, are in
[`akp05_bridge/README.md`](akp05_bridge/README.md). No configuration
needed: MQTT broker credentials are injected automatically by Supervisor.
An "Ajazz AKP05" device then appears under Settings → Devices & Services
→ MQTT on its own — there's no "Add Integration" step.

### Using it

- **Buttons/encoders** — each is a real MQTT `event` entity, so in an
  automation: Add Trigger → Entity → **When an event occurs** → pick the
  button/encoder → event type (`pressed`/`released`, or `cw`/`ccw` for
  encoder twists).
- **Brightness** — a normal light entity. Turning it off dims to 0%
  *without* touching any images, unlike the CLI's `off` (which also
  wipes everything).
- **Setting a button's icon** — directly in the UI: each button has a
  **Button N Icon** text entity, type any [MDI](https://pictogrammers.com/library/mdi/)
  name into it and it renders and uploads immediately, no automation
  needed.
- **Showing an entity's on/off state on a button** — also directly in
  the UI: each button also has a **Button N Linked Entity** text entity,
  type an entity_id into it (e.g. `light.bedroom_lights`) and that
  button's icon turns green/red to track it live, again no automation
  needed (uses Home Assistant's own Core API, `homeassistant_api: true`
  — auto-configured, nothing to set up).
- **Raw images/strip/clearing** — plain MQTT commands (topic
  `akp05/cmd`), called via the `mqtt.publish` service from an automation
  — see [`akp05_bridge/README.md`](akp05_bridge/README.md#using-it) for
  the exact payloads.

### Troubleshooting

- **No device found, in the add-on log**: a USB passthrough issue — if
  HA OS is itself a VM (Proxmox/ESXi/etc.), the AKP05 needs to be passed
  through to that VM first; the add-on's `usb`/`udev` options only help
  once the host OS can already see the device.
- **Buttons don't register, or the wrong ones fire**: the Linux hidraw
  backend's byte offsets (`KEY_IDX`/`STATE_IDX` in `akp05_device.py`)
  are inferred from the hidraw no-report-ID convention, not yet
  confirmed against real hardware — check the add-on log for raw report
  bytes against what you actually pressed.
- More in [`akp05_bridge/README.md`](akp05_bridge/README.md#configure-and-start).

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
