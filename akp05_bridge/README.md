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
   - "Device not found yet, retrying in 5s..." repeating → the container
     can't see the USB device. This used to crash-loop the whole add-on
     on every miss, including a transient one right after a restart
     before USB re-enumerated — fixed to retry in-process instead, but
     if it repeats forever, confirm the AKP05 shows up on the *host*
     itself first (the VM-passthrough case above), then confirm
     `usb`/`udev` are still `true` in `config.yaml`.
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
   - **Icons don't show up after a restart until you clear and retype
     the same MDI name** — this was also a real bug (fixed): the device
     wipes every button's screen on every reconnect (including a plain
     add-on restart), but nothing was re-uploading the remembered icons.
     `connect_device()` now re-renders every button it has a saved icon
     for immediately after connecting.
   - **Unplugging and replugging the USB cable leaves the panel black
     until you manually restart the add-on** — this was also a real bug
     (fixed): a physical disconnect made the read/keepalive threads
     silently end with no recovery attempt, so replugging the cable
     didn't do anything on its own. The add-on now notices (via either
     thread) and reconnects on its own once the device reappears, same
     retry loop the initial connection uses -- watch the **Log** tab for
     "Device disconnected -- waiting for it to come back..." followed by
     "Device reconnected". `akp05/status` also correctly reports
     `offline` for that window, so entities show unavailable rather than
     silently stale.
   - **"No hidraw device found" repeating forever even though the AKP05
     is plugged into the HA host and shows up under Settings → System →
     Hardware** — seen once on real hardware: every control in HA looked
     dead (icons/text/brightness all stuck on old values) while button
     presses still worked. Unplugging the AKP05, waiting 5 seconds,
     plugging it back in, and restarting the add-on fixed it outright.
     The log now says so itself after a minute of retries. Since 0.10.0
     the add-on also connects to MQTT *before* looking for the device,
     so while it's missing `akp05/status` reads `offline` and the
     entities show unavailable instead of looking healthy.
   - **Brightness keeps dropping on its own** — two real bugs, both
     fixed. 0.10.0: the image-upload sequence sends a brightness command
     as part of waking the panel, and it was hard-coded to 50%, so every
     text/icon refresh (e.g. a sensor value updating) reset it. 0.10.1:
     the 10-second keepalive also sends the display-init pair (`DIS` +
     a bare `LIG`, which carries brightness 0) on every tick, so the
     panel was reset every 10 seconds regardless. 0.10.1 tried dropping
     that pair to send only `CONNECT` like the reference libraries do —
     on real hardware that made the panel stop taking image updates
     after the first tick, so 0.10.2 keeps the pair and instead
     re-sends whatever the Brightness entity currently says right after
     it, on every tick. The panel now also defaults to 100% on start.
   - **The whole panel blinks dark every few seconds, and a text/icon
     update goes black for a moment before the new one appears** —
     fixed in 0.10.3. The keepalive and every image upload sent a bare
     `LIG` (which is "backlight 0") before the real brightness, and
     uploads also cleared the target button to black before drawing
     it. Neither is done now; the reference implementations never did
     either before an upload. If your unit turns out to need the old
     sequence (symptom would be updates not showing up), turn on
     `legacy_wake_sequence` in the add-on's Configuration tab and
     restart — no rebuild needed. Please report it if so.
   - **After a while, icon/text changes stop showing on the device
     (buttons still work), and unplugging/replugging the AKP05 fixes
     it** — a real bug, fixed in 0.10.4: an image upload was sent as
     three separate write batches, so the 10-second keepalive (or the
     strip poller) could inject its own packets between the image data
     and the commit. The device then silently accepted every later
     upload and displayed none of them until power-cycled. Uploads are
     now one uninterruptible batch. The add-on also logs every upload
     with its size and duration now, so if anything like this recurs
     the Log tab shows exactly what was sent last.
   - **Every icon and text set fails (buttons still work), and neither
     a restart nor a replug fixes it** — different bug from the one
     above, despite looking the same from the outside; fixed in 0.11.1.
     The MDI and Roboto fonts were downloaded on first use and cached
     inside the image, so an add-on rebuild wiped them and every render
     needed jsdelivr/GitHub to be reachable again. Until they were, all
     icon/text renders raised, their entities stayed unknown (state is
     only echoed after a successful render), and button presses carried
     on working because they never touch the font code — hence a freeze
     that ignores restarts and power-cycles. The fonts now ship baked
     into the image, so nothing is fetched at runtime. Tell-tale in the
     log: `Downloading MDI icon font...` followed by a connection or
     timeout error on `akp05/button_<n>/icon/set`. Two smaller fixes in
     the same release: fonts, `/data` state, and the strip canvas are
     all written to a temp file and renamed now (an interrupted write
     left a corrupt file that broke renders permanently, since nothing
     re-fetches a file that already exists), an unreadable strip cache
     falls back to black instead of failing every strip write from then
     on, and that cache moved to `/data` so a rebuild no longer blanks
     the other three bars on the next Bar write.
   - **Getting a useful log** — the add-on's Log tab only shows the
     last ~100 lines, which a few restarts fill with container
     start/stop noise. Use **Settings → System → Logs**, pick "AKP05
     Bridge" from the dropdown, and **Load full logs** (or the download
     icon) to get the whole thing.
   - **The Log tab is empty or minutes behind** — fixed in 0.10.0
     (Python was buffering its output inside the container).
3. In Home Assistant: **Settings → Devices & Services → MQTT** — an
   "Ajazz AKP05" device should appear (MQTT discovery is automatic, no
   "Add Integration" step needed) with a **Brightness** entity, 18 event
   entities (one per button, encoder button, and encoder twist pair),
   2 text entities per button (**Icon** and **Text** — 20 total), 2 text
   entities per strip split (**Bar 1-4 Icon** and **Bar 1-4 Text** — 8
   total), a **Display** on/off switch, plus **Strip Text** and
   **Strip URL** entities for the touch strip as a whole.

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
  `display_off`/`display_on` below) so toggling this in a routine
  automation can't accidentally erase your icons.
- **Turning the screen off and back on** — the **Display** switch
  (`switch.ajazz_akp05_display`). Off dims to 0% *and* wipes every
  button/strip image to actual black — brightness 0 alone leaves the
  content faintly visible on this panel. On restores the previous
  brightness and re-renders everything the add-on remembers (icons,
  text values, strip text/URL). Put it on a dashboard, use
  `switch.turn_on`/`switch.turn_off`/`switch.toggle` from a script or
  automation, or expose it to a voice assistant — it's the entity form
  of the `display_off`/`display_on` commands below, same code path,
  and its state stays correct across a Home Assistant restart.
- **Setting a button's icon — directly in the UI, no automation needed**
  — each button has a **Button N Icon** text entity (Settings → Devices
  & Services → MQTT → Ajazz AKP05, or just search for it). Click it,
  type any [Material Design Icons](https://pictogrammers.com/library/mdi/)
  name (e.g. `floor-lamp-outline`), hit enter — it renders and uploads
  immediately, gray (no on/off state tracked this way). Clear a button
  by setting its text to empty. A name that doesn't exist just won't
  take — check the add-on's **Log** tab if a button doesn't update,
  that's the only place an invalid name gets reported.
- **Showing a live value on a button — "text monitor"** — each button
  also has a **Button N Text** entity: push an already-formatted string
  like `21.4°C` straight to the screen, in [Roboto](https://fonts.google.com/specimen/Roboto)
  — the same font Home Assistant's own frontend uses, auto-shrunk to
  fit. Type into it directly, or for a sensor that should stay in sync,
  a small automation: trigger on the sensor's state, action
  `text.set_value` on `text.ajazz_akp05_button_N_text` — see
  `text_monitor_automation_example.yaml` at the repo root. (The
  per-button "Follow Entity" field and `akp05/entity_update` topic that
  used to do this were removed in 0.10.0; the direct form is simpler.)
  A button shows an icon OR a text value, never both — whichever you
  set most recently wins.
- **Icon or text on one strip split — "Bar 1"-"Bar 4"** — the touch
  strip is split into four 200x112 slices left to right, each with its
  own **Bar N Icon** and **Bar N Text** entities that work exactly like
  a button's — type an MDI name into the Icon entity, or an
  already-formatted string into the Text entity, and just that quarter
  of the strip updates (the rest is left alone; the add-on composites
  the change into the strip's cache and re-uploads the full 800x112
  image, same as `set_strip_chunk` below). A split shows an icon OR a
  text value, never both, and setting either takes the whole strip out
  of Strip Text/Strip URL mode (they all paint over the same surface —
  see below).
- **Text on the touch strip** — the **Strip Text** entity works exactly
  like a button's Text entity but renders across the whole 800x112
  strip: type into it in the UI, or drive it from an automation with
  `text.set_value` on `text.ajazz_akp05_strip_text`. Auto-shrinks to
  fit the width; empty clears the strip to black.
- **A live dashboard card on the touch strip** — design it graphically,
  no image editing: install [balloob's Puppet add-on](https://github.com/balloob/home-assistant-addons)
  (add that repo under Add-on store → ⋮ → Repositories, give Puppet a
  long-lived access token in its configuration), build a dashboard view
  with the normal card editor containing whatever entities/icons you
  want, then set the **Strip URL** entity to e.g.
  `http://homeassistant.local:10000/<your-dashboard>/0?viewport=800x112`.
  The add-on re-fetches and repaints it every `strip_refresh_seconds`
  (add-on option, default 30) — a self-updating card on the strip.
  Puppet also takes `dark`, `theme=...`, and `zoom=...` query
  parameters if the default render doesn't look right at 112px tall.
  Any URL that serves an image works, not just Puppet — camera
  snapshots, grafana panels, whatever. A failed fetch just logs and
  retries on the next cycle, so a Puppet restart heals on its own.
  The strip shows text OR the URL's image OR a raw pushed image
  (`set_strip`/`set_strip_chunk` below) OR whatever the four Bar
  icon/text entities last set — setting any one replaces (and
  un-remembers) the others.
- **Coloring an icon by an entity's on/off state (green/red)** — same
  shared-automation philosophy, different action: trigger on the
  entity's state, call the `akp05/cmd` `set_icon` action below with the
  right `state` — see `icon_sync_automation_example.yaml`.
- **Raw images/clearing/strip** — no MQTT entity type exists for
  uploading a file from the UI, so these stay plain MQTT commands. Call
  them from an automation with the built-in `mqtt.publish` service,
  topic `akp05/cmd`, JSON payload:
  ```yaml
  # render a Material Design Icon on a button, colored by on/off state
  # (the Button N Icon text entities above call this same code path,
  # just without the state coloring -- use this form when you want that)
  {"action": "set_icon", "button": 3, "icon": "floor-lamp-outline", "state": "on"}

  # push a text value (same code path as the Button N Text entity)
  {"action": "set_text", "button": 3, "text": "21.4°C"}

  # same two actions, for one of the strip's four splits (1-4) instead
  # of a button -- same code paths as the Bar N Icon/Text entities
  {"action": "set_bar_icon", "bar": 2, "icon": "thermometer", "state": "on"}
  {"action": "set_bar_text", "bar": 2, "text": "21.4°C"}
  {"action": "clear_bar", "bar": 2}

  # raw base64 PNG/JPEG on a button
  {"action": "set_image", "button": 3, "image_b64": "..."}

  # blank one button
  {"action": "clear_button", "button": 3}

  # turn the whole display off -- dims to 0% AND wipes every button/strip
  # image to actual black (brightness alone doesn't get you there; the
  # panel stays faintly visible at 0%). "clear_all" is the same action
  # under its older name, kept working -- use whichever reads better in
  # your automation. Same thing as turning the Display switch off.
  {"action": "display_off"}

  # ...and back on: restores brightness and re-renders every button's
  # remembered icon (nothing else currently does that in one call --
  # otherwise it needs a full add-on restart to come back). Same thing
  # as turning the Display switch on.
  {"action": "display_on"}

  # whole touch strip (800x112, auto-resized) or one of its 200px chunks
  {"action": "set_strip", "image_b64": "..."}
  {"action": "set_strip_chunk", "chunk": 12, "image_b64": "..."}

  # text across the whole strip (same code path as the Strip Text entity)
  {"action": "set_strip_text", "text": "Hello there"}

  # point the strip at an image URL (same code path as the Strip URL
  # entity -- re-fetched every strip_refresh_seconds until unset)
  {"action": "set_strip_url", "url": "http://homeassistant.local:10000/strip/0?viewport=800x112"}

  # EXPERIMENTAL, unconfirmed on real hardware -- a "HAN" command found
  # in mirajazz's source, distinct from LIG, used by its own sleep()/
  # shutdown() -- possibly an actual power state rather than dim+wipe.
  # Not wired into display_off/display_on until this is confirmed safe:
  # nobody knows yet whether the panel reliably wakes back up afterward.
  # If it stops responding to anything after this, a physical unplug/
  # replug may be the only recovery. Test carefully.
  {"action": "experimental_sleep"}
  ```

  A common use: an automation on `sun.sun`/a schedule/an `input_boolean`
  turning the **Display** switch off at night and on in the morning
  (or publishing the equivalent `display_off`/`display_on` commands).

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
| `akp05/button_<n>/text/set`  | subscribes| Already-formatted string, e.g. `21.4°C`; empty clears the button. |
| `akp05/button_<n>/text/state`| publishes | Echoes the value back, retained |
| `akp05/bar_<n>/icon/set`     | subscribes| MDI icon name for strip split `<n>` (`1`-`4`, left to right); empty clears just that split. |
| `akp05/bar_<n>/icon/state`   | publishes | Echoes the name back, retained, only on a successful render |
| `akp05/bar_<n>/text/set`     | subscribes| Already-formatted string for strip split `<n>`; empty clears just that split. |
| `akp05/bar_<n>/text/state`   | publishes | Echoes the value back, retained |
| `akp05/strip/text/set`       | subscribes| Text for the whole strip; empty clears it to black. |
| `akp05/strip/text/state`     | publishes | Echoes the text back, retained |
| `akp05/strip/url/set`        | subscribes| Image URL to poll onto the strip; empty leaves URL mode. |
| `akp05/strip/url/state`      | publishes | Echoes the URL back, retained |
| `akp05/display/set`          | subscribes| `OFF` / `ON` -- the Display switch's command; same as `display_off`/`display_on` on `akp05/cmd` |
| `akp05/display/state`        | publishes | `ON` / `OFF` (retained) -- what the Display switch shows |
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
