"""
AKP05 bridge add-on -- MQTT edition.

Talks to the Ajazz AKP05 over raw HID (the only container on a Home
Assistant OS host that can, via this add-on's usb/udev options) and
exposes it to Home Assistant purely through MQTT discovery -- no
separate integration to install anywhere. Requires an MQTT broker
(this add-on declares `mqtt:want` in config.yaml so Supervisor can
auto-inject connection details, with mqtt_host/mqtt_username/
mqtt_password options as a manual fallback -- that auto-injection has
been seen not to work in some setups) and the MQTT integration
configured in Home Assistant itself.

What gets published:
  - homeassistant/light/akp05/brightness/config (retained) -- one light
    entity for panel brightness (buttons + strip together, same as the
    device's own LIG command). Turning it off just sets brightness to 0
    -- it does NOT wipe button/strip images (see akp05/cmd's clear_all
    for that, kept as an explicit action, never a side effect of the
    light).
  - homeassistant/event/akp05/<id>/config (retained) -- one MQTT `event`
    entity per button, encoder button, and encoder twist pair (18
    total). Each is a real entity (Settings -> Devices & Services ->
    MQTT -> Ajazz AKP05), and its presses are usable as automation
    triggers the standard way (Add Trigger -> Entity -> When an event
    occurs). This was tried first as the *only* mechanism for buttons
    (as device_automation triggers, device-only, no entities) -- that
    produced zero visible triggers with no validation error logged
    anywhere, so entities are the one actually confirmed working, via
    the same discovery code path already confirmed for the light.
  - homeassistant/device_automation/akp05/<id>/config (retained) --
    published *in addition* to the event entities above, purely so the
    same presses also show up under Add Trigger -> Device -> Ajazz
    AKP05, for whoever prefers that picker. Redundant with the event
    entities, not required for anything to work.
  - homeassistant/text/akp05/button_<n>_icon/config (retained) -- one
    MQTT `text` entity per button (1-10, the only ones with a screen --
    encoders don't have one). Type any Material Design Icons name
    straight into it in the HA UI (e.g. "floor-lamp-outline") and it
    renders via akp05_icons.build_icon and uploads, same as
    akp05_set_image.py/the akp05/cmd set_icon action. No automation or
    mqtt.publish needed for this one -- it's the direct answer to "let
    me set the mdi from Home Assistant". An unrecognized name just
    doesn't update (see akp05/button_<n>/icon/state below); MQTT text
    entities have no other way to surface an error.
  - homeassistant/text/akp05/button_<n>_text/config (retained) -- a
    second `text` entity per button: pushes an already-formatted string
    (e.g. "21.4°C") straight to the screen via akp05_icons.build_text
    (Roboto -- same font Home Assistant's own frontend uses --
    auto-shrunk to fit). A button shows an icon OR a text value, never
    both; setting one clears the other's remembered state for that
    button.
  - homeassistant/text/akp05/strip_text/config (retained) -- one more
    `text` entity, same idea as button_<n>_text but rendering to the
    full 800x112 touch strip (build_text with the strip's size --
    Roboto, auto-shrunk to fit the width). The direct answer to "let me
    put something on the strip from Home Assistant" without needing the
    base64-image akp05/cmd path. Like a button, the strip shows the
    text OR whatever image was last pushed via set_strip/
    set_strip_chunk, never both -- setting either forgets the other
    (the strip is a single full-write surface, so the add-on remembers
    at most one thing to restore after a reconnect/display_on).
  - homeassistant/text/akp05/strip_url/config (retained) -- the third
    strip mode: set an image URL and the add-on re-fetches and paints
    it every strip_refresh_seconds (add-on option, default 30). Built
    for balloob's Puppet add-on -- design a dashboard view in the
    normal Lovelace editor, then point this at
    http://homeassistant.local:10000/<dashboard>/0?viewport=800x112 for
    a live card on the strip -- but any URL serving an image works.
    Text, URL, and raw images are mutually exclusive; setting any one
    forgets the others.
  - homeassistant/switch/akp05/display/config (retained) -- one MQTT
    `switch` entity, "Display": ON/OFF is exactly what akp05/cmd's
    display_on / display_off actions do (see below), with the current
    state echoed on akp05/display/state so it reads as a real toggle
    on a dashboard, in a script/automation (switch.turn_on/off), or
    via a voice assistant -- no mqtt.publish JSON needed for the most
    common use. Replaced a pair of Display Off / Display On `button`
    entities (0.9.1) -- a toggle with state is the more natural shape.
  - akp05/status -- retained "online"/"offline" (MQTT last-will), used
    as every entity's availability topic.
  - akp05/event/<id> -- NOT retained, JSON {"event_type": "pressed"}
    etc., one topic per event entity above.
  - akp05/event -- NOT retained, plain "<event_type>:<object_id>", feeds
    only the device_automation triggers (which match a raw payload
    string, not a JSON field, hence the separate topic/format).
  - akp05/button_<n>/icon/state, .../text/state, akp05/strip/text/state
    -- retained, echo back whatever was actually set. icon/state only
    updates on a successful render (an unrecognized MDI name is
    silently rejected rather than echoed, the only feedback an MQTT
    text entity can give); text always echoes since there's nothing to
    validate.

What it subscribes to:
  - akp05/power/set, akp05/brightness/set -- the light entity's own
    command topics ("ON"/"OFF" and "0".."100" respectively).
  - akp05/button_<n>/icon/set, .../text/set -- the two text entities'
    command topics above; empty string clears the button.
  - akp05/strip/text/set -- the Strip Text entity's command topic;
    empty string clears the strip to black.
  - akp05/strip/url/set -- the Strip URL entity's command topic; empty
    string leaves URL mode (stops the poller repainting).
  - akp05/display/set -- the Display switch's command topic: "OFF" runs
    display_off, "ON" runs display_on.
  - (removed in 0.10.0: akp05/entity_update and the per-button "Follow
    Entity" text entities. Showing a live sensor value on a button is
    now just an automation calling text.set_value on that button's
    Text entity directly -- see text_monitor_automation_example.yaml at
    the repo root. On every MQTT connect the add-on clears the retained
    discovery configs of removed entities so they disappear from Home
    Assistant on their own; see _stale_retained_topics().)
  - akp05/cmd -- JSON commands for things that don't map to a single
    entity: raw images (there's no MQTT entity type for uploading a
    file from the UI, so this stays automation/script-only), strip
    images, clearing, display_off/display_on, set_text, set_strip_text,
    experimental_sleep. See the add-on's README for the payload shapes;
    call these from automations with the mqtt.publish service.

akp05/cmd's display_off/display_on are a deliberate pair, separate from
the light entity's own on/off: the light is non-destructive brightness
only (see above), while display_off actually blacks the screen (brightness
alone doesn't -- LIG is backlight/PWM only, content stays faintly visible
at 0%, confirmed in akp05_set_brightness.py's docstring) by also wiping
every button/strip image, and display_on is the new way back -- restores
brightness and re-renders everything that was showing (icons and text
values alike), which previously needed a full add-on restart to get back
(connect_device()'s own restore logic, now also reachable on demand).

experimental_sleep is a different, unconfirmed attempt at a *real* power
state rather than dim+wipe -- a "HAN" command found in mirajazz's source
(distinct from LIG), used by its own Device::sleep()/Device::shutdown().
opendeck-akp05 (the AKP05-specific project) does call shutdown() itself,
which is a real signal this is relevant to this device, not just some
other Mirabox model mirajazz also supports -- but nobody has confirmed
on real AKP05 hardware whether it behaves differently from 0% brightness,
or how the panel wakes back up afterward. See Bridge.sleep_display()'s
docstring before relying on this for anything -- worst case, if the panel
stops responding to anything, a physical unplug/replug may be the only
way to recover it.

Startup order: MQTT first, then the device. Until the AKP05 is found,
akp05/status reads "offline" (so its entities show unavailable in Home
Assistant rather than silently stale) and every command is logged and
dropped. If the log shows "No hidraw device found" repeating while the
device is plugged in, power-cycle it (unplug, wait 5s, replug) and
restart the add-on -- seen once on real hardware and that was the fix.

For syncing a button's icon to an entity's on/off state (green/red)
rather than a text value, use an automation triggered on that entity's
state, calling the akp05/cmd set_icon action with the appropriate
"state" -- see icon_sync_automation_example.yaml at the repo root.
"""

import base64
import json
import os
import threading
import time
from io import BytesIO

import paho.mqtt.client as mqtt
import requests
from PIL import Image

from akp05_device import (
    BUTTON_IMAGE_SIZE,
    BUTTON_TO_WIRE_KEY,
    KEY_IDX,
    STATE_IDX,
    STRIP_CHUNK_WIDTH,
    STRIP_IMAGE_SIZE,
    STRIP_WIRE_KEY,
    connect,
    crt_command,
    encode_image,
    load_strip_canvas,
    save_strip_canvas,
    send_commands,
    upload_image,
)
from akp05_icons import build_icon, build_text

OPTIONS_PATH = "/data/options.json"
DEVICE_ID = "akp05"

STATUS_TOPIC = f"{DEVICE_ID}/status"
# Plain "event_type:object_id" on one shared topic, used only by the
# device_automation triggers below -- separate from the per-entity JSON
# topics event entities use, since MQTT device triggers match on a raw
# payload string, not a JSON field.
TRIGGER_EVENT_TOPIC = f"{DEVICE_ID}/event"
POWER_SET_TOPIC = f"{DEVICE_ID}/power/set"
POWER_STATE_TOPIC = f"{DEVICE_ID}/power/state"
BRIGHTNESS_SET_TOPIC = f"{DEVICE_ID}/brightness/set"
BRIGHTNESS_STATE_TOPIC = f"{DEVICE_ID}/brightness/state"
CMD_TOPIC = f"{DEVICE_ID}/cmd"


def _icon_set_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/icon/set"


def _icon_state_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/icon/state"


# button -> its set-topic, for on_message's dispatch (only images have a
# writable screen -- encoders don't, so this is buttons 1-10 only)
ICON_SET_TOPICS = {_icon_set_topic(button): button for button in range(1, 11)}


def _text_set_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/text/set"


def _text_state_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/text/state"


TEXT_SET_TOPICS = {_text_set_topic(button): button for button in range(1, 11)}


STRIP_TEXT_SET_TOPIC = f"{DEVICE_ID}/strip/text/set"
STRIP_TEXT_STATE_TOPIC = f"{DEVICE_ID}/strip/text/state"
STRIP_URL_SET_TOPIC = f"{DEVICE_ID}/strip/url/set"
STRIP_URL_STATE_TOPIC = f"{DEVICE_ID}/strip/url/state"

# The Display switch entity: ON/OFF commands in, current state echoed
# back (retained) so the toggle reads correctly after a HA restart.
DISPLAY_SET_TOPIC = f"{DEVICE_ID}/display/set"
DISPLAY_STATE_TOPIC = f"{DEVICE_ID}/display/state"
DISPLAY_OFF_PAYLOAD = "OFF"
DISPLAY_ON_PAYLOAD = "ON"

ICONS_PATH = "/data/button_icons.json"
TEXTS_PATH = "/data/button_texts.json"
STRIP_TEXT_PATH = "/data/strip_text.json"
STRIP_URL_PATH = "/data/strip_url.json"


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default
    return default


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


BUTTON_KEYS = set(range(1, 11))
ENCODER_PRESS_KEYS = {0x37: 1, 0x35: 2, 0x33: 3, 0x36: 4}
ENCODER_TWIST_KEYS = {
    0xA0: (1, "ccw"), 0xA1: (1, "cw"),
    0x50: (2, "ccw"), 0x51: (2, "cw"),
    0x90: (3, "ccw"), 0x91: (3, "cw"),
    0x70: (4, "ccw"), 0x71: (4, "cw"),
}

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": "Ajazz AKP05",
    "manufacturer": "Ajazz",
    "model": "AKP05",
}


def load_options() -> dict:
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


OPTIONS = load_options()
DISCOVERY_PREFIX = OPTIONS.get("discovery_prefix") or "homeassistant"
# How often the Strip URL poller re-fetches (seconds). Floor of 5 in the
# schema -- every fetch is a full ~1s strip re-upload, so there's no
# point hammering faster, and Puppet itself takes ~10s on a cold render.
STRIP_REFRESH_SECONDS = max(5, int(OPTIONS.get("strip_refresh_seconds") or 30))


def _stale_retained_topics() -> list[str]:
    """Retained topics left behind by entities this add-on no longer
    publishes. An MQTT discovery entity lives on in Home Assistant until
    its retained config is cleared, so on every MQTT connect these get
    an empty retained publish -- HA removes the entity, the broker
    drops the topic, and after that it's a no-op. (0.7.0 removed "Link"
    without doing this, which is why those could still be lingering on
    some installs -- covered here too.)"""
    topics = []
    for button in range(1, 11):
        for removed in ("follow", "link"):
            topics.append(f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/button_{button}_{removed}/config")
            topics.append(f"{DEVICE_ID}/button_{button}/{removed}/state")
    # 0.9.1's Display Off / Display On buttons, replaced by the switch.
    topics.append(f"{DISCOVERY_PREFIX}/button/{DEVICE_ID}/display_off/config")
    topics.append(f"{DISCOVERY_PREFIX}/button/{DEVICE_ID}/display_on/config")
    return topics

# Supervisor is *supposed* to inject these once an MQTT broker is
# available (this add-on declares `mqtt:want` in config.yaml), but that
# auto-provisioning only works with the official Mosquitto broker add-on
# and has been seen not to fire at all in some setups (falling back to
# an anonymous connection Mosquitto then rejects) -- so the add-on's own
# Configuration-tab options always win when set, as a manual escape
# hatch that doesn't depend on that mechanism working.
MQTT_HOST = OPTIONS.get("mqtt_host") or os.environ.get("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(OPTIONS.get("mqtt_port") or os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = OPTIONS.get("mqtt_username") or os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = OPTIONS.get("mqtt_password") or os.environ.get("MQTT_PASSWORD") or None


def _light_discovery_payload() -> dict:
    # The standard MQTT light shape (separate command_topic for on/off,
    # plus brightness_command_topic/brightness_state_topic) rather than
    # the on_command_type: brightness shortcut this used before -- that
    # relies on command_topic being safely omittable, which isn't
    # actually certain, and a schema-validation failure on this payload
    # would silently produce zero entities, which is exactly what was
    # seen. This shape is unambiguously well-supported.
    return {
        "name": "Brightness",
        "unique_id": f"{DEVICE_ID}_brightness",
        "command_topic": POWER_SET_TOPIC,
        "state_topic": POWER_STATE_TOPIC,
        "payload_on": "ON",
        "payload_off": "OFF",
        "brightness_command_topic": BRIGHTNESS_SET_TOPIC,
        "brightness_state_topic": BRIGHTNESS_STATE_TOPIC,
        "brightness_scale": 100,
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _event_entities():
    """(object_id, event_types, device_class) for every button/encoder.
    Each gets its own MQTT `event` entity (a real entity, not just a
    device-only trigger -- MQTT device_automation triggers were tried
    first and silently produced nothing despite valid-looking, error-free
    discovery payloads; event entities go through the same discovery
    code path already confirmed working for the light, so this is the
    higher-confidence mechanism and it means these show up as normal
    entities too, not just as automation triggers."""
    for button in range(1, 11):
        yield f"button_{button}", ["pressed", "released"], "button"
    for encoder in range(1, 5):
        yield f"encoder_{encoder}_button", ["pressed", "released"], "button"
        yield f"encoder_{encoder}", ["cw", "ccw"], None


def _event_topic(object_id: str) -> str:
    return f"{DEVICE_ID}/event/{object_id}"


def _event_discovery_payload(object_id: str, event_types: list, device_class: str | None) -> dict:
    payload = {
        "name": object_id.replace("_", " ").title(),
        "unique_id": f"{DEVICE_ID}_{object_id}",
        "state_topic": _event_topic(object_id),
        "event_types": event_types,
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }
    if device_class:
        payload["device_class"] = device_class
    return payload


def _trigger_discovery_payload(object_id: str, event_type: str) -> dict:
    return {
        "automation_type": "trigger",
        "type": event_type,
        "subtype": object_id,
        "topic": TRIGGER_EVENT_TOPIC,
        "payload": f"{event_type}:{object_id}",
        "device": DEVICE_INFO,
    }


def _icon_discovery_payload(button: int) -> dict:
    # MQTT `text` entity -- typing straight into this in the HA UI is
    # the whole point (no automation/mqtt.publish JSON needed). Renders
    # via akp05_icons.build_icon, same as akp05_set_image.py and the
    # akp05/cmd set_icon action; an unrecognized MDI name just fails
    # quietly here (logged, no state echo -- see on_message) since MQTT
    # text entities have no error-surfacing mechanism of their own.
    return {
        "name": f"Button {button} Icon",
        "unique_id": f"{DEVICE_ID}_button_{button}_icon",
        "command_topic": _icon_set_topic(button),
        "state_topic": _icon_state_topic(button),
        "icon": "mdi:image-edit-outline",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _text_discovery_payload(button: int) -> dict:
    # A second MQTT `text` entity per button: pushes an already-formatted
    # string (e.g. "21.4°C") straight to the screen via akp05_icons.build_text
    # (Roboto, auto-shrunk to fit). For a live sensor value, an automation
    # just calls text.set_value on this entity whenever the sensor
    # changes -- see text_monitor_automation_example.yaml.
    return {
        "name": f"Button {button} Text",
        "unique_id": f"{DEVICE_ID}_button_{button}_text",
        "command_topic": _text_set_topic(button),
        "state_topic": _text_state_topic(button),
        "icon": "mdi:format-text",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _strip_url_discovery_payload() -> dict:
    # The "pretty card on the strip" answer: point this at anything that
    # serves an image over HTTP and the add-on re-fetches it every
    # strip_refresh_seconds and paints it (resized to 800x112). Built
    # with balloob's Puppet add-on in mind -- design a dashboard view in
    # the normal Lovelace editor, then set this to e.g.
    # http://homeassistant.local:10000/<dashboard>/0?viewport=800x112
    # -- but any image URL works (cameras, graphs, whatever).
    return {
        "name": "Strip URL",
        "unique_id": f"{DEVICE_ID}_strip_url",
        "command_topic": STRIP_URL_SET_TOPIC,
        "state_topic": STRIP_URL_STATE_TOPIC,
        "icon": "mdi:link-variant",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _strip_text_discovery_payload() -> dict:
    # Same idea as _text_discovery_payload but for the whole 800x112
    # touch strip -- the one directly-typeable control for it (raw
    # images stay on the akp05/cmd base64 path; MQTT has no entity type
    # for uploading a file from the UI).
    return {
        "name": "Strip Text",
        "unique_id": f"{DEVICE_ID}_strip_text",
        "command_topic": STRIP_TEXT_SET_TOPIC,
        "state_topic": STRIP_TEXT_STATE_TOPIC,
        "icon": "mdi:format-text",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _display_switch_discovery_payload() -> dict:
    # MQTT `switch` entity for the screen -- ON/OFF map to the akp05/cmd
    # display_on/display_off actions, which were only reachable via a
    # mqtt.publish JSON payload before (fine inside an automation,
    # awkward as a dashboard tile or a voice-assistant target). A
    # switch with a state topic is the natural HA shape: one toggle
    # that also reads back correctly after a HA restart.
    return {
        "name": "Display",
        "unique_id": f"{DEVICE_ID}_display",
        "command_topic": DISPLAY_SET_TOPIC,
        "state_topic": DISPLAY_STATE_TOPIC,
        "payload_on": DISPLAY_ON_PAYLOAD,
        "payload_off": DISPLAY_OFF_PAYLOAD,
        "icon": "mdi:monitor",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def publish_discovery(client: mqtt.Client):
    client.publish(
        f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}/brightness/config",
        json.dumps(_light_discovery_payload()),
        retain=True,
    )
    client.publish(
        f"{DISCOVERY_PREFIX}/switch/{DEVICE_ID}/display/config",
        json.dumps(_display_switch_discovery_payload()),
        retain=True,
    )
    for button in range(1, 11):
        client.publish(
            f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/button_{button}_icon/config",
            json.dumps(_icon_discovery_payload(button)),
            retain=True,
        )
        client.publish(
            f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/button_{button}_text/config",
            json.dumps(_text_discovery_payload(button)),
            retain=True,
        )
    client.publish(
        f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/strip_text/config",
        json.dumps(_strip_text_discovery_payload()),
        retain=True,
    )
    client.publish(
        f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/strip_url/config",
        json.dumps(_strip_url_discovery_payload()),
        retain=True,
    )
    for object_id, event_types, device_class in _event_entities():
        client.publish(
            f"{DISCOVERY_PREFIX}/event/{DEVICE_ID}/{object_id}/config",
            json.dumps(_event_discovery_payload(object_id, event_types, device_class)),
            retain=True,
        )
        # Also publish a device-only trigger for the same event -- shows
        # up under Add Trigger -> Device instead of -> Entity. Tried
        # first as the *only* mechanism; it silently produced zero
        # usable triggers with no validation error logged anywhere, so
        # the event entities above are the one actually confirmed
        # working, and this is now just an additional, redundant path
        # kept because it's a nicer picker for some people.
        for event_type in event_types:
            client.publish(
                f"{DISCOVERY_PREFIX}/device_automation/{DEVICE_ID}/{event_type}_{object_id}/config",
                json.dumps(_trigger_discovery_payload(object_id, event_type)),
                retain=True,
            )


class Bridge:
    """Owns the device connection. HID reads happen on a background
    thread (akp05_device's read loop); client.publish() is safe to call
    from there directly -- paho-mqtt's publish() is thread-safe, so no
    extra queue/event-loop bridging is needed the way an asyncio server
    would require."""

    def __init__(self, client: mqtt.Client):
        self.client = client
        self.device = None
        self.brightness = 50
        self._last_nonzero_brightness = 50
        self._reconnect_lock = threading.Lock()
        # Persisted across restarts. A button shows an icon OR a text
        # value, never both -- set_icon/set_text each clear the other's
        # entry for that button, so at most one of these two dicts has
        # any given button in it at a time.
        self.button_icons: dict[int, str] = {int(k): v for k, v in _load_json(ICONS_PATH, {}).items()}
        self.button_texts: dict[int, str] = {int(k): v for k, v in _load_json(TEXTS_PATH, {}).items()}
        # What the Display switch reports: False only after display_off
        # (dimmed to 0 AND wiped -- see clear_all), True again on
        # display_on or a (re)connect's full init.
        self.display_on_state = True
        # What the strip is showing, if it's showing text (empty string
        # otherwise -- same one-or-the-other rule as a button's
        # icon-vs-text, but against set_strip/set_strip_chunk images).
        self.strip_text: str = _load_json(STRIP_TEXT_PATH, "")
        # ...or a periodically-refetched image URL (the "dashboard card
        # on the strip" mode, via e.g. the Puppet add-on). Text, URL,
        # and raw set_strip images are all mutually exclusive: setting
        # any one forgets the others. The poller thread runs for the
        # process's whole life and just idles while strip_url is empty.
        self.strip_url: str = _load_json(STRIP_URL_PATH, "")
        self._strip_url_wake = threading.Event()
        threading.Thread(target=self._strip_url_loop, daemon=True).start()

    def connect_device(self):
        self.device = connect(self._on_report, full_init=True, on_disconnect=self._handle_disconnect)
        # Every keepalive tick (10s) re-sends the current brightness --
        # see akp05_device's keepalive note. Belt-and-braces against
        # anything (ours or the firmware's) drifting it.
        self.device.brightness_provider = lambda: self.brightness
        self.display_on_state = True
        self.publish_state()
        self._restore_button_displays()

    def _handle_disconnect(self):
        """Called from a background thread (akp05_device's read loop or
        keepalive loop -- possibly both, around the same time) once a
        physical unplug is noticed. Used to just end silently with no
        recovery, needing a manual add-on restart even after replugging
        the cable -- this reconnects in-process instead, reusing the
        same "wait for the device to reappear" retry loop startup already
        uses. Guarded by a lock since both detectors can fire together."""
        if not self._reconnect_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._reconnect, daemon=True).start()

    def _reconnect(self):
        try:
            print("Device disconnected -- waiting for it to come back...")
            self.client.publish(STATUS_TOPIC, "offline", retain=True)
            try:
                self.device.close()
            except Exception:
                pass
            _connect_device_with_retry(self)
            print("Device reconnected")
            self.client.publish(STATUS_TOPIC, "online", retain=True)
        finally:
            self._reconnect_lock.release()

    def _restore_button_displays(self):
        """full_init wipes every button's screen (CLE) on every connect
        -- including a plain add-on restart, not just a fresh install --
        but nothing was re-uploading whatever each button is supposed to
        show, so restarting silently blanked them until something set a
        new value (the only thing that actually re-triggers a render).
        Re-render everything we remember, icons and text values alike."""
        for button, icon in list(self.button_icons.items()):
            try:
                self.set_icon(button, icon, None)
            except Exception as exc:  # noqa: BLE001 - one bad icon shouldn't block the rest
                print(f"Couldn't restore icon for button {button} ({icon!r}): {exc}")
        for button, text in list(self.button_texts.items()):
            try:
                self.set_text(button, text)
            except Exception as exc:  # noqa: BLE001 - one bad value shouldn't block the rest
                print(f"Couldn't restore text for button {button} ({text!r}): {exc}")
        if self.strip_text:
            try:
                self.set_strip_text(self.strip_text)
            except Exception as exc:  # noqa: BLE001
                print(f"Couldn't restore strip text ({self.strip_text!r}): {exc}")
        if self.strip_url:
            # Just nudge the poller -- it does the fetch/paint itself.
            self._strip_url_wake.set()

    def _out_len(self) -> int:
        return self.device.hid_caps.output_report_byte_length

    def publish_state(self):
        self.client.publish(BRIGHTNESS_STATE_TOPIC, str(self.brightness), retain=True)
        self.client.publish(POWER_STATE_TOPIC, "ON" if self.brightness > 0 else "OFF", retain=True)
        self.client.publish(
            DISPLAY_STATE_TOPIC,
            DISPLAY_ON_PAYLOAD if self.display_on_state else DISPLAY_OFF_PAYLOAD,
            retain=True,
        )

    def set_brightness(self, value: int):
        value = max(0, min(100, int(value)))
        send_commands(self.device, [crt_command("LIG", [0x00, 0x00, value], self._out_len())])
        self.brightness = value
        if value > 0:
            self._last_nonzero_brightness = value
        self.publish_state()

    def set_power(self, on: bool):
        self.set_brightness(self._last_nonzero_brightness if on else 0)

    def clear_all(self):
        """Dims to 0% AND wipes every button/strip image to black -- the
        actual way to make the screen go black. Brightness alone doesn't
        do it (confirmed in akp05_set_brightness.py's own docstring: LIG
        is backlight/PWM only, content stays faintly visible at 0%), so
        this is what display_off (below) uses under a clearer name for
        that specific use case. Doesn't touch button_icons -- the add-on
        still remembers what was showing, so display_on can restore it."""
        out_len = self._out_len()
        send_commands(self.device, [
            crt_command("LIG", [0x00, 0x00, 0], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, STRIP_WIRE_KEY], out_len),
            crt_command("STP", [], out_len),
        ])
        self.brightness = 0
        self.display_on_state = False
        self.publish_state()

    def display_on(self):
        """Pairs with clear_all/display_off: restores brightness and
        re-renders every button's remembered icon or text value (reuses
        the same restore logic connect_device() already uses after a
        reconnect -- this just triggers it on demand instead)."""
        self.display_on_state = True
        self.set_brightness(self._last_nonzero_brightness)
        self._restore_button_displays()

    def sleep_display(self):
        """EXPERIMENTAL, unconfirmed on real AKP05 hardware -- do not
        wire this into display_off/display_on until it's actually been
        tested. Found in mirajazz's source: a "HAN" command (distinct
        from LIG) that its own Device::sleep()/Device::shutdown() use --
        real signal it's relevant to this device specifically, not just
        some other Mirabox model mirajazz also supports: opendeck-akp05
        (the AKP05-specific project, not the general library) actually
        calls shutdown() somewhere in its own code, which goes through
        this same command. Whether it produces a genuinely different
        result than 0% brightness, and how (or whether) the panel wakes
        back up afterward via a normal command, is unknown -- if it
        doesn't respond to anything after this, a physical unplug/replug
        may be the only way to recover it. Test on real hardware with
        that in mind before relying on it for anything."""
        out_len = self._out_len()
        send_commands(self.device, [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
            crt_command("HAN", [], out_len),
        ])

    def set_button_image(self, button: int, jpeg_bytes: bytes):
        # brightness=: upload_image's wake-up sequence includes a LIG
        # (brightness) command, which used to be hard-coded to 50 -- so
        # every text/icon refresh silently dragged the panel back to
        # 50% within seconds of setting anything else. Pass what the
        # light entity currently says instead.
        upload_image(self.device, BUTTON_TO_WIRE_KEY[button], jpeg_bytes, brightness=self.brightness)

    def clear_button(self, button: int):
        out_len = self._out_len()
        wire_key = BUTTON_TO_WIRE_KEY[button]
        send_commands(self.device, [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
            # Re-assert brightness right after the wake pair, same as
            # upload_image does -- the bare LIG above carries 0.
            crt_command("LIG", [0x00, 0x00, self.brightness], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len),
            crt_command("STP", [], out_len),
        ])

    def set_strip(self, jpeg_bytes: bytes):
        upload_image(self.device, STRIP_WIRE_KEY, jpeg_bytes, brightness=self.brightness)

    def set_strip_chunk(self, chunk: int, patch_img: Image.Image):
        x_offset = (chunk - 11) * STRIP_CHUNK_WIDTH
        canvas = load_strip_canvas()
        canvas.paste(patch_img, (x_offset, 0))
        save_strip_canvas(canvas)
        self.set_strip(encode_image(canvas, STRIP_IMAGE_SIZE))

    def set_strip_text(self, text: str):
        """Renders text across the whole strip (build_text at
        STRIP_IMAGE_SIZE -- Roboto, auto-shrunk to fit the width) and
        remembers it for restore-after-reconnect, same as a button's
        text. Empty text clears the strip to black and forgets."""
        if text:
            img = build_text(text, size=STRIP_IMAGE_SIZE)
        else:
            img = Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))
        # Either way this takes the strip over -- even an empty set means
        # "blank it", which URL mode would repaint over seconds later.
        self.forget_strip_url()
        save_strip_canvas(img)
        self.set_strip(encode_image(img, STRIP_IMAGE_SIZE))
        self.strip_text = text
        _save_json(STRIP_TEXT_PATH, text)

    def forget_strip_text(self):
        """Called when a raw image lands on the strip (set_strip/
        set_strip_chunk cmd actions) -- mirrors how a button's set_icon
        forgets its text: whatever was written last is the one thing
        remembered/restored, never both."""
        if self.strip_text:
            self.strip_text = ""
            _save_json(STRIP_TEXT_PATH, "")
            self.client.publish(STRIP_TEXT_STATE_TOPIC, "", retain=True)

    def forget_strip_url(self):
        """Same idea for URL mode -- stops the poller repainting over
        whatever just replaced it."""
        if self.strip_url:
            self.strip_url = ""
            _save_json(STRIP_URL_PATH, "")
            self.client.publish(STRIP_URL_STATE_TOPIC, "", retain=True)

    def set_strip_url(self, url: str):
        """Enters (or leaves, on empty) URL mode: the poller re-fetches
        the image every STRIP_REFRESH_SECONDS and paints it. The first
        fetch happens immediately (the poller is woken rather than
        waiting out its current sleep). An unreachable URL doesn't
        raise here -- the poller logs each failed fetch and keeps
        retrying on the same schedule, so a Puppet add-on restart heals
        on its own."""
        self.strip_url = url
        _save_json(STRIP_URL_PATH, url)
        if url:
            self.forget_strip_text()
            self._strip_url_wake.set()

    def _strip_url_loop(self):
        while True:
            woken = self._strip_url_wake.wait(timeout=STRIP_REFRESH_SECONDS)
            self._strip_url_wake.clear()
            url = self.strip_url
            if not url or self.device is None:
                continue
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB").resize(STRIP_IMAGE_SIZE, Image.LANCZOS)
            except Exception as exc:  # noqa: BLE001 - a dead URL shouldn't kill the poller
                print(f"Strip URL fetch failed ({url}): {exc}")
                continue
            # Re-check: text/raw-image may have taken over during the
            # fetch (which can take seconds against a cold Puppet) --
            # don't paint a stale card over what the user just set.
            if self.strip_url != url:
                continue
            try:
                save_strip_canvas(img)
                self.set_strip(encode_image(img, STRIP_IMAGE_SIZE))
            except Exception as exc:  # noqa: BLE001 - e.g. device mid-reconnect
                print(f"Strip URL paint failed: {exc}")

    def set_icon(self, button: int, icon: str, state: str | None):
        is_on = {"on": True, "off": False}.get(state)
        img = build_icon(icon, is_on)  # raises KeyError for an unrecognized name -- caller decides how to handle
        self.set_button_image(button, encode_image(img, img.size))
        self.button_icons[button] = icon
        _save_json(ICONS_PATH, self.button_icons)
        if self.button_texts.pop(button, None) is not None:
            _save_json(TEXTS_PATH, self.button_texts)

    def set_text(self, button: int, text: str):
        img = build_text(text)
        self.set_button_image(button, encode_image(img, img.size))
        self.button_texts[button] = text
        _save_json(TEXTS_PATH, self.button_texts)
        if self.button_icons.pop(button, None) is not None:
            _save_json(ICONS_PATH, self.button_icons)

    @staticmethod
    def _classify(key: int, state: int):
        """Returns (object_id, event_type) matching _event_entities()."""
        if key in BUTTON_KEYS:
            return f"button_{key}", ("pressed" if state == 1 else "released")
        if key in ENCODER_PRESS_KEYS:
            return f"encoder_{ENCODER_PRESS_KEYS[key]}_button", ("pressed" if state == 1 else "released")
        if key in ENCODER_TWIST_KEYS:
            enc_id, direction = ENCODER_TWIST_KEYS[key]
            return f"encoder_{enc_id}", direction
        return None, None

    def _on_report(self, data):
        if len(data) <= STATE_IDX:
            return
        key, state = data[KEY_IDX], data[STATE_IDX]
        if key == 0:
            return
        object_id, event_type = self._classify(key, state)
        if object_id:
            # Neither retained -- a stateless press shouldn't replay
            # itself to every future subscriber/on every HA restart.
            self.client.publish(_event_topic(object_id), json.dumps({"event_type": event_type}))
            self.client.publish(TRIGGER_EVENT_TOPIC, f"{event_type}:{object_id}")


bridge_holder: dict = {}


def _decode_image(image_b64: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(image_b64))).convert("RGB")


def _handle_cmd(bridge: Bridge, payload: dict):
    action = payload.get("action")
    if action == "set_brightness":
        bridge.set_brightness(payload["value"])
    elif action in ("clear_all", "display_off"):
        bridge.clear_all()
    elif action == "display_on":
        bridge.display_on()
    elif action == "experimental_sleep":
        bridge.sleep_display()
    elif action == "clear_button":
        bridge.clear_button(int(payload["button"]))
    elif action == "set_icon":
        bridge.set_icon(int(payload["button"]), payload["icon"], payload.get("state"))
    elif action == "set_text":
        bridge.set_text(int(payload["button"]), payload["text"])
    elif action == "set_image":
        img = _decode_image(payload["image_b64"])
        bridge.set_button_image(int(payload["button"]), encode_image(img, BUTTON_IMAGE_SIZE))
    elif action == "set_strip":
        if payload.get("clear"):
            img = Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0))
        else:
            img = _decode_image(payload["image_b64"]).resize(STRIP_IMAGE_SIZE, Image.LANCZOS)
        save_strip_canvas(img)
        bridge.set_strip(encode_image(img, STRIP_IMAGE_SIZE))
        bridge.forget_strip_text()
        bridge.forget_strip_url()
    elif action == "set_strip_chunk":
        chunk = int(payload["chunk"])
        size = (STRIP_CHUNK_WIDTH, STRIP_IMAGE_SIZE[1])
        if payload.get("clear"):
            patch = Image.new("RGB", size, (0, 0, 0))
        else:
            patch = _decode_image(payload["image_b64"]).resize(size, Image.LANCZOS)
        bridge.set_strip_chunk(chunk, patch)
        bridge.forget_strip_text()
        bridge.forget_strip_url()
    elif action == "set_strip_text":
        bridge.set_strip_text(payload["text"])
        bridge.client.publish(STRIP_TEXT_STATE_TOPIC, payload["text"], retain=True)
    elif action == "set_strip_url":
        bridge.set_strip_url(payload["url"])
        bridge.client.publish(STRIP_URL_STATE_TOPIC, payload["url"], retain=True)
    else:
        print(f"Unknown akp05/cmd action: {action!r}")


def on_connect(client, userdata, flags, reason_code, properties=None):
    if getattr(reason_code, "is_failure", bool(reason_code)):
        print(
            f"MQTT connection rejected: {reason_code}. If this says "
            "unauthorized/not authorised, mqtt_username/mqtt_password "
            "are empty or wrong -- set them in this add-on's "
            "Configuration tab (see the README's Troubleshooting "
            "section for how to create an MQTT login)."
        )
        return
    print(f"Connected to MQTT broker (host={MQTT_HOST}, user={MQTT_USERNAME or '(none)'})")
    client.subscribe(POWER_SET_TOPIC)
    client.subscribe(BRIGHTNESS_SET_TOPIC)
    client.subscribe(CMD_TOPIC)
    for topic in ICON_SET_TOPICS:
        client.subscribe(topic)
    for topic in TEXT_SET_TOPICS:
        client.subscribe(topic)
    client.subscribe(STRIP_TEXT_SET_TOPIC)
    client.subscribe(STRIP_URL_SET_TOPIC)
    client.subscribe(DISPLAY_SET_TOPIC)
    for topic in _stale_retained_topics():
        client.publish(topic, "", retain=True)
    publish_discovery(client)
    bridge = bridge_holder.get("bridge")
    # MQTT comes up before the device is found (see main) -- only claim
    # "online" once the AKP05 is actually open, so its entities show
    # unavailable rather than silently stale while it's missing.
    device_ready = bridge is not None and bridge.device is not None
    client.publish(STATUS_TOPIC, "online" if device_ready else "offline", retain=True)
    if device_ready:
        bridge.publish_state()


def on_message(client, userdata, msg):
    bridge = bridge_holder["bridge"]
    if bridge.device is None:
        print(f"Device not connected -- ignoring message on {msg.topic}")
        return
    try:
        if msg.topic == POWER_SET_TOPIC:
            bridge.set_power(msg.payload.decode().strip().upper() == "ON")
        elif msg.topic == BRIGHTNESS_SET_TOPIC:
            bridge.set_brightness(int(msg.payload.decode()))
        elif msg.topic == CMD_TOPIC:
            _handle_cmd(bridge, json.loads(msg.payload.decode()))
        elif msg.topic == DISPLAY_SET_TOPIC:
            # The Display switch -- same code paths as akp05/cmd's
            # display_off/display_on, just reached by a toggle instead
            # of a JSON payload. State is echoed via publish_state().
            payload = msg.payload.decode().strip().upper()
            if payload == DISPLAY_OFF_PAYLOAD:
                bridge.clear_all()
            elif payload == DISPLAY_ON_PAYLOAD:
                bridge.display_on()
            else:
                print(f"Unknown {DISPLAY_SET_TOPIC} payload: {payload!r}")
        elif msg.topic in ICON_SET_TOPICS:
            button = ICON_SET_TOPICS[msg.topic]
            icon = msg.payload.decode().strip()
            if icon:
                bridge.set_icon(button, icon, None)
            else:
                bridge.clear_button(button)
            # Only echoed back on success -- an unrecognized MDI name
            # raises inside set_icon (caught below), so the text field
            # simply won't update to a name that didn't actually render,
            # which is the only feedback an MQTT text entity can give.
            client.publish(_icon_state_topic(button), icon, retain=True)
        elif msg.topic in TEXT_SET_TOPICS:
            button = TEXT_SET_TOPICS[msg.topic]
            text = msg.payload.decode().strip()
            if text:
                bridge.set_text(button, text)
            else:
                bridge.clear_button(button)
            client.publish(_text_state_topic(button), text, retain=True)
        elif msg.topic == STRIP_TEXT_SET_TOPIC:
            text = msg.payload.decode().strip()
            bridge.set_strip_text(text)
            client.publish(STRIP_TEXT_STATE_TOPIC, text, retain=True)
        elif msg.topic == STRIP_URL_SET_TOPIC:
            url = msg.payload.decode().strip()
            bridge.set_strip_url(url)
            client.publish(STRIP_URL_STATE_TOPIC, url, retain=True)
    except Exception as exc:  # noqa: BLE001 - a bad command shouldn't kill the bridge
        print(f"Error handling message on {msg.topic}: {exc}")


DEVICE_RETRY_DELAY = 5  # seconds


def _connect_device_with_retry(bridge: Bridge):
    """akp05_device.open_device() calls sys.exit(1) if the hidraw node
    isn't found yet -- fine for a one-shot CLI script, but for this
    long-running add-on that previously meant the *entire* process
    (MQTT client and everything) died and had to be restarted by
    Supervisor on every transient miss, e.g. the USB device not having
    finished re-enumerating yet right after a restart. Retry in-process
    instead of crash-looping the whole container over it."""
    attempts = 0
    while True:
        try:
            bridge.connect_device()
            bridge.client.publish(STATUS_TOPIC, "online", retain=True)
            return
        except SystemExit:
            print(f"Device not found yet, retrying in {DEVICE_RETRY_DELAY}s...")
        except Exception as exc:  # noqa: BLE001 - e.g. a permission error on the hidraw node
            print(f"Opening the device failed ({exc!r}), retrying in {DEVICE_RETRY_DELAY}s...")
        attempts += 1
        if attempts == 12:
            # Seen on real hardware: plugged in, visible on the host,
            # yet never found here until the device itself was
            # power-cycled. Say so once rather than scrolling forever.
            print(
                "Still no device after a minute. If the AKP05 is plugged into this "
                "host, unplug it, wait 5 seconds, plug it back in, then restart "
                "this add-on -- that has fixed exactly this before."
            )
        time.sleep(DEVICE_RETRY_DELAY)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.will_set(STATUS_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    bridge = Bridge(client)
    bridge_holder["bridge"] = bridge
    # Device search runs off the main thread so MQTT comes up first:
    # while the AKP05 is missing, akp05/status reads "offline" and its
    # entities show unavailable in HA. Previously this blocked here
    # before ever touching MQTT, so a missing device left whatever
    # retained "online" the last run had published sitting there, and
    # HA looked healthy while nothing actually worked.
    threading.Thread(target=_connect_device_with_retry, args=(bridge,), daemon=True).start()

    # connect_async + loop_forever(retry_first_connection=True) instead
    # of a plain connect(): the mqtt:need service dependency should mean
    # Mosquitto is already up, but this survives it winning the startup
    # race anyway instead of crash-looping.
    client.connect_async(MQTT_HOST, MQTT_PORT)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
