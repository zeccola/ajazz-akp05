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
  - homeassistant/text/akp05/button_<n>_follow/config (retained) -- a
    third `text` entity per button: type an entity_id into it (e.g.
    "sensor.bedroom_temperature") to say *which* entity this button
    should display. Doesn't make this add-on watch anything itself --
    see the akp05/entity_update note below for why, and how values
    actually get here.
  - akp05/status -- retained "online"/"offline" (MQTT last-will), used
    as every entity's availability topic.
  - akp05/event/<id> -- NOT retained, JSON {"event_type": "pressed"}
    etc., one topic per event entity above.
  - akp05/event -- NOT retained, plain "<event_type>:<object_id>", feeds
    only the device_automation triggers (which match a raw payload
    string, not a JSON field, hence the separate topic/format).
  - akp05/button_<n>/icon/state, .../text/state, .../follow/state --
    retained, echo back whatever was actually set. icon/state only
    updates on a successful render (an unrecognized MDI name is
    silently rejected rather than echoed, the only feedback an MQTT
    text entity can give); text/follow always echo since there's
    nothing to validate.

What it subscribes to:
  - akp05/power/set, akp05/brightness/set -- the light entity's own
    command topics ("ON"/"OFF" and "0".."100" respectively).
  - akp05/button_<n>/icon/set, .../text/set, .../follow/set -- the three
    text entities' command topics above; empty string clears/unlinks.
  - akp05/entity_update -- JSON {"entity_id": ..., "text": ...}, NOT
    published by this add-on -- fed by a shared automation
    (text_monitor_automation_example.yaml at the repo root), forwarding
    whichever entities you want available. This add-on never watches
    Home Assistant's own state itself: an earlier version tried that
    in-process (a "linked entity" text entity, watching Home Assistant's
    Core API directly via a websocket) and it was pulled back out after
    never being reliably confirmed working end to end. This is the
    MQTT-only replacement -- one shared automation instead of a
    per-add-on Core API connection, using only the MQTT path already
    confirmed solid. On receipt, routes the text to whichever button(s)
    currently have that entity_id set via .../follow/set.
  - akp05/cmd -- JSON commands for things that don't map to a single
    entity: raw images (there's no MQTT entity type for uploading a
    file from the UI, so this stays automation/script-only), strip
    images, clearing, display_off/display_on, set_text. See the add-on's
    README for the payload shapes; call these from automations with the
    mqtt.publish service.

akp05/cmd's display_off/display_on are a deliberate pair, separate from
the light entity's own on/off: the light is non-destructive brightness
only (see above), while display_off actually blacks the screen (brightness
alone doesn't -- LIG is backlight/PWM only, content stays faintly visible
at 0%, confirmed in akp05_set_brightness.py's docstring) by also wiping
every button/strip image, and display_on is the new way back -- restores
brightness and re-renders everything that was showing (icons and text
values alike), which previously needed a full add-on restart to get back
(connect_device()'s own restore logic, now also reachable on demand).

For syncing a button's icon to an entity's on/off state (green/red)
rather than a text value, use an automation triggered on that entity's
state, calling the akp05/cmd set_icon action with the appropriate
"state" -- see icon_sync_automation_example.yaml at the repo root.
"""

import base64
import json
import os
import time
from io import BytesIO

import paho.mqtt.client as mqtt
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


def _follow_set_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/follow/set"


def _follow_state_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/follow/state"


FOLLOW_SET_TOPICS = {_follow_set_topic(button): button for button in range(1, 11)}

# Published by a *shared* automation (text_monitor_automation_example.yaml),
# not by this add-on -- the add-on deliberately doesn't watch entities
# itself (that was tried as "linked entity"/HAWatcher, pulled back out
# for being unreliable to confirm working). One automation forwards
# whichever entities you want available to follow; this add-on just
# routes {"entity_id": ..., "text": ...} to whichever button(s) currently
# follow that entity_id.
ENTITY_UPDATE_TOPIC = f"{DEVICE_ID}/entity_update"

ICONS_PATH = "/data/button_icons.json"
TEXTS_PATH = "/data/button_texts.json"
FOLLOWS_PATH = "/data/button_follows.json"


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
    # A third MQTT `text` entity per button: pushes an already-formatted
    # string (e.g. "21.4°C") straight to the screen via akp05_icons.build_text
    # (Roboto, auto-shrunk to fit) -- useful directly from an automation/
    # script for one-off values, and it's what entity_update (below)
    # renders through for anything a button is following.
    return {
        "name": f"Button {button} Text",
        "unique_id": f"{DEVICE_ID}_button_{button}_text",
        "command_topic": _text_set_topic(button),
        "state_topic": _text_state_topic(button),
        "icon": "mdi:format-text",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _follow_discovery_payload(button: int) -> dict:
    # Configures *which* entity_id this button follows -- just the
    # mapping, typed here once. The actual values come from
    # entity_update, published by a shared automation, not from this
    # add-on watching Home Assistant itself (see module docstring for
    # why). Independent of the Icon/Text entities -- whichever one a
    # button last received a value through is what's currently showing.
    return {
        "name": f"Button {button} Follow Entity",
        "unique_id": f"{DEVICE_ID}_button_{button}_follow",
        "command_topic": _follow_set_topic(button),
        "state_topic": _follow_state_topic(button),
        "icon": "mdi:eye-outline",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def publish_discovery(client: mqtt.Client):
    client.publish(
        f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}/brightness/config",
        json.dumps(_light_discovery_payload()),
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
            f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/button_{button}_follow/config",
            json.dumps(_follow_discovery_payload(button)),
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
        # Persisted across restarts. A button shows an icon OR a text
        # value, never both -- set_icon/set_text each clear the other's
        # entry for that button, so at most one of these two dicts has
        # any given button in it at a time.
        self.button_icons: dict[int, str] = {int(k): v for k, v in _load_json(ICONS_PATH, {}).items()}
        self.button_texts: dict[int, str] = {int(k): v for k, v in _load_json(TEXTS_PATH, {}).items()}
        # Which entity_id (if any) each button follows -- just the
        # mapping; entity_update (fed by a shared automation) is what
        # actually delivers values for these.
        self.button_follows: dict[int, str] = {int(k): v for k, v in _load_json(FOLLOWS_PATH, {}).items()}

    def connect_device(self):
        self.device = connect(self._on_report, full_init=True)
        self.publish_state()
        self._restore_button_displays()

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

    def _out_len(self) -> int:
        return self.device.hid_caps.output_report_byte_length

    def publish_state(self):
        self.client.publish(BRIGHTNESS_STATE_TOPIC, str(self.brightness), retain=True)
        self.client.publish(POWER_STATE_TOPIC, "ON" if self.brightness > 0 else "OFF", retain=True)

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
        self.publish_state()

    def display_on(self):
        """Pairs with clear_all/display_off: restores brightness and
        re-renders every button's remembered icon or text value (reuses
        the same restore logic connect_device() already uses after a
        reconnect -- this just triggers it on demand instead)."""
        self.set_brightness(self._last_nonzero_brightness)
        self._restore_button_displays()

    def set_button_image(self, button: int, jpeg_bytes: bytes):
        upload_image(self.device, BUTTON_TO_WIRE_KEY[button], jpeg_bytes)

    def clear_button(self, button: int):
        out_len = self._out_len()
        wire_key = BUTTON_TO_WIRE_KEY[button]
        send_commands(self.device, [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, wire_key], out_len),
            crt_command("STP", [], out_len),
        ])

    def set_strip(self, jpeg_bytes: bytes):
        upload_image(self.device, STRIP_WIRE_KEY, jpeg_bytes)

    def set_strip_chunk(self, chunk: int, patch_img: Image.Image):
        x_offset = (chunk - 11) * STRIP_CHUNK_WIDTH
        canvas = load_strip_canvas()
        canvas.paste(patch_img, (x_offset, 0))
        save_strip_canvas(canvas)
        self.set_strip(encode_image(canvas, STRIP_IMAGE_SIZE))

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

    def set_follow(self, button: int, entity_id: str):
        if entity_id:
            self.button_follows[button] = entity_id
        else:
            self.button_follows.pop(button, None)
        _save_json(FOLLOWS_PATH, self.button_follows)

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
    elif action == "set_strip_chunk":
        chunk = int(payload["chunk"])
        size = (STRIP_CHUNK_WIDTH, STRIP_IMAGE_SIZE[1])
        if payload.get("clear"):
            patch = Image.new("RGB", size, (0, 0, 0))
        else:
            patch = _decode_image(payload["image_b64"]).resize(size, Image.LANCZOS)
        bridge.set_strip_chunk(chunk, patch)
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
    client.subscribe(ENTITY_UPDATE_TOPIC)
    for topic in ICON_SET_TOPICS:
        client.subscribe(topic)
    for topic in TEXT_SET_TOPICS:
        client.subscribe(topic)
    for topic in FOLLOW_SET_TOPICS:
        client.subscribe(topic)
    publish_discovery(client)
    client.publish(STATUS_TOPIC, "online", retain=True)
    bridge = bridge_holder.get("bridge")
    if bridge is not None:
        bridge.publish_state()


def on_message(client, userdata, msg):
    bridge = bridge_holder["bridge"]
    try:
        if msg.topic == POWER_SET_TOPIC:
            bridge.set_power(msg.payload.decode().strip().upper() == "ON")
        elif msg.topic == BRIGHTNESS_SET_TOPIC:
            bridge.set_brightness(int(msg.payload.decode()))
        elif msg.topic == CMD_TOPIC:
            _handle_cmd(bridge, json.loads(msg.payload.decode()))
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
        elif msg.topic in FOLLOW_SET_TOPICS:
            button = FOLLOW_SET_TOPICS[msg.topic]
            entity_id = msg.payload.decode().strip()
            bridge.set_follow(button, entity_id)
            client.publish(_follow_state_topic(button), entity_id, retain=True)
        elif msg.topic == ENTITY_UPDATE_TOPIC:
            update = json.loads(msg.payload.decode())
            entity_id, text = update.get("entity_id"), update.get("text", "")
            for button, followed in list(bridge.button_follows.items()):
                if followed == entity_id:
                    bridge.set_text(button, text)
                    client.publish(_text_state_topic(button), text, retain=True)
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
    while True:
        try:
            bridge.connect_device()
            return
        except SystemExit:
            print(f"Device not found yet, retrying in {DEVICE_RETRY_DELAY}s...")
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
    _connect_device_with_retry(bridge)

    # connect_async + loop_forever(retry_first_connection=True) instead
    # of a plain connect(): the mqtt:need service dependency should mean
    # Mosquitto is already up, but this survives it winning the startup
    # race anyway instead of crash-looping.
    client.connect_async(MQTT_HOST, MQTT_PORT)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
