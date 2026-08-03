# AKP05 Bridge (Home Assistant add-on)

Owns the USB connection to the Ajazz AKP05 on your Home Assistant OS /
Supervised host and exposes it to Home Assistant purely via **MQTT
discovery** — buttons/encoders become native automation triggers,
brightness becomes a light entity, all automatically. This is the only
component you need to install; there's no separate integration to copy
into `/config/custom_components/`.

## Prerequisites

- Home Assistant OS or Supervised (this needs Supervisor — it won't work
  on Home Assistant Container/Core-only installs).
- An MQTT broker add-on installed and running — e.g. the official
  **Mosquitto broker** add-on — with the **MQTT** integration configured
  in Home Assistant (Settings → Devices & Services → MQTT). If you
  already use MQTT for anything else, this is already done.
- The AKP05 physically reachable by the HA host. If HA OS is itself a VM
  (Proxmox, ESXi, etc.), pass the USB device through to the VM first —
  the add-on's USB access starts from what the host OS can already see.

## Installing it

### Option A — add this repo as an add-on repository (recommended)

1. Push this repo to GitHub if you haven't.
2. **Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories**,
   paste `https://github.com/zeccola/ajazz-akp05`, **Add**.
3. Close and reopen the Add-on Store — "AKP05 Bridge" should now appear
   under **Ajazz AKP05**.
4. Click it, then **Install**.

### Option B — copy the folder directly as a local add-on

No GitHub push needed, good for testing changes before committing.

1. Get file access to the HA host: the **Samba share** add-on (mounts
   `\\<host>\addons` from Windows) or **SSH & Terminal** if you'd rather
   `scp`/`rsync`.
2. Copy this whole `akp05_bridge/` folder to
   `/addons/local/akp05_bridge/` on the host (the folder itself must
   contain `config.yaml` directly).
3. **Settings → Add-ons → Add-on Store → ⋮ → Check for updates** — "AKP05
   Bridge" should appear under **Local add-ons**.
4. Click it, then **Install**.

Either way, the first install builds a Docker image on the host, which
takes a few minutes.

## Configure and start

Try it with no configuration first — this add-on declares `mqtt:want` in
`config.yaml`, so Supervisor *should* inject broker host/port/credentials
automatically once an MQTT broker add-on is running, no setup needed.

1. **Info** tab → **Start**. Optionally enable **Start on boot** and
   **Watchdog** so it recovers automatically.
2. **Log** tab — confirm you see the device get found and "Connected to
   MQTT broker". If not:
   - **"MQTT connection rejected"**, or Mosquitto's own log shows
     `received null username or password` / `not authorised` — the
     Supervisor auto-injection didn't provide credentials (this has been
     seen to simply not fire in some setups, or only reliably works with
     the *official* Mosquitto broker add-on specifically). Fix: create a
     dedicated MQTT login and enter it manually —
     1. **Settings → People → Users → Add User** (or, if your Mosquitto
        add-on has its own **Logins** list in its Configuration tab, add
        an entry there instead).
     2. Open this add-on's **Configuration** tab, fill in `mqtt_username`
        / `mqtt_password` with that login (and `mqtt_host`/`mqtt_port`
        too, if the broker isn't at the default `core-mosquitto:1883` —
        e.g. `homeassistant.local` on whatever port you've set MQTT to
        listen on).
     3. Save, **Restart** the add-on.
   - "No hidraw device found" → the container can't see the USB device.
     Confirm the AKP05 shows up on the *host* itself first (the
     VM-passthrough case above), then confirm `usb`/`udev` are still
     `true` in `config.yaml`.
   - Device found, MQTT connected, but a permission error opening
     `/dev/hidrawN` → a udev-rule/permission mismatch inside the
     container. This project hasn't had a chance to confirm the exact
     permissions Supervisor's `udev: true` grants against real AKP05
     hardware yet — if you hit this, it needs a udev rule added to the
     add-on, not a config change on your end.
   - **Screen goes black / device seems to stop responding after ~15
     seconds, entities go unavailable** — this was a real bug (fixed):
     the device drops its connection if it doesn't hear from the host
     periodically. Confirmed against both reference implementations
     (mirajazz, opendeck-akp05), which send a keepalive every 10 seconds
     for exactly this reason — `akp05_device.py`'s `connect()` now does
     the same automatically. If you still see this on a version that
     includes that fix, it's a new bug, not the same one.
3. In Home Assistant: **Settings → Devices & Services → MQTT** — an
   "Ajazz AKP05" device should appear (MQTT discovery is automatic, no
   "Add Integration" step needed) with a **Brightness** entity, 18 event
   entities (one per button, encoder button, and encoder twist pair),
   and 10 **Button N Icon** text entities.

## Using it

- **Buttons/encoders — two ways to trigger on them, both published:**
  - Each button/encoder is a real MQTT `event` entity: Add Trigger →
    Entity → **When an event occurs** → pick e.g. "Button 3" → event
    type `pressed` or `released` (encoders' twist entities use
    `cw`/`ccw` instead). This is the mechanism actually confirmed
    working, since it uses the same discovery code path as the light.
  - The same presses are *also* published as device-only
    `device_automation` triggers, so Add Trigger → Device → **Ajazz
    AKP05** works too if you prefer that picker. This was tried first as
    the *only* mechanism and silently produced zero usable triggers with
    no error logged anywhere — kept alongside the entities now since
    it's harmless if it does work for you, but don't rely on it alone.
- **Brightness** — the light entity. Turning it off sets brightness to
  0% only — it does **not** wipe button/strip images, unlike the CLI's
  `akp05_set_brightness.py off`. Deliberately kept separate (see
  `clear_all` below) so toggling this in a routine automation can't
  accidentally erase your icons.
- **Setting a button's icon — directly in the UI, no automation needed**
  — each button has a **Button N Icon** text entity (Settings → Devices
  & Services → MQTT → Ajazz AKP05, or just search for it). Click it,
  type any [Material Design Icons](https://pictogrammers.com/library/mdi/)
  name (e.g. `floor-lamp-outline`), hit enter — it renders and uploads
  immediately, gray (no on/off state tracked this way). Clear a button
  by setting its text to empty. A name that doesn't exist just won't
  take — check the add-on's **Log** tab if a button doesn't update,
  that's the only place an invalid name gets reported.
  For a version that's colored by an entity's actual on/off state (green
  on / red off), automate it instead via `akp05/cmd`'s `set_icon` action
  below, e.g. triggered on that entity's state changing.
- **Raw images/clearing/strip** — no MQTT entity type exists for
  uploading a file from the UI, so these stay plain MQTT commands. Call
  them from an automation with the built-in `mqtt.publish` service,
  topic `akp05/cmd`, JSON payload:
  ```yaml
  # render a Material Design Icon on a button, colored by on/off state
  # (the Button N Icon text entities above call this same code path,
  # just without the state coloring -- use this form when you want that)
  {"action": "set_icon", "button": 3, "icon": "floor-lamp-outline", "state": "on"}

  # raw base64 PNG/JPEG on a button
  {"action": "set_image", "button": 3, "image_b64": "..."}

  # blank one button
  {"action": "clear_button", "button": 3}

  # dim to 0% AND wipe every button/strip image -- destructive,
  # images must be re-uploaded afterward
  {"action": "clear_all"}

  # whole touch strip (800x112, auto-resized) or one of its 200px chunks
  {"action": "set_strip", "image_b64": "..."}
  {"action": "set_strip_chunk", "chunk": 12, "image_b64": "..."}
  ```

## Topic reference

| Topic                       | Direction | Payload                              |
|------------------------------|-----------|---------------------------------------|
| `akp05/status`               | publishes | `online` / `offline` (retained, LWT)  |
| `akp05/event/<id>`           | publishes | `{"event_type": "pressed"}` etc., not retained. `<id>` is `button_1`..`button_10`, `encoder_1_button`..`encoder_4_button`, `encoder_1`..`encoder_4` (twist). Feeds the event entities. |
| `akp05/event`                | publishes | `<event_type>:<id>`, not retained. Feeds only the device_automation triggers (raw payload match, not JSON). |
| `akp05/power/set`            | subscribes| `ON` / `OFF`                          |
| `akp05/power/state`          | publishes | `ON` / `OFF` (retained)               |
| `akp05/brightness/set`       | subscribes| `0`-`100`                             |
| `akp05/brightness/state`     | publishes | `0`-`100` (retained)                  |
| `akp05/button_<n>/icon/set`  | subscribes| MDI icon name, e.g. `floor-lamp-outline`; empty clears the button. `<n>` is `1`-`10`. |
| `akp05/button_<n>/icon/state`| publishes | Echoes the name back, retained, only on a successful render |
| `akp05/cmd`                  | subscribes| JSON, see above                       |

All of this is namespaced under `akp05/` and the MQTT discovery configs
under `<discovery_prefix>/`; nothing else on your broker is touched.

## Updating

If you change `akp05_device.py` or `akp05_icons.py` at the repo root,
run `python akp05_bridge/sync_vendor.py` to copy the changes into this
folder before rebuilding — Docker's build context is this folder only,
so it can't reach the repo root copies directly (see `sync_vendor.py`'s
docstring). Then reinstall/rebuild the add-on (Option A: push + "Check
for updates" + Update; Option B: re-copy the folder + rebuild from the
add-on's page).
