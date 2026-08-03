"""
AKP05 bridge add-on -- MQTT edition.

Talks to the Ajazz AKP05 over raw HID (the only container on a Home
Assistant OS host that can, via this add-on's usb/udev options) and
exposes it to Home Assistant mainly through MQTT discovery -- no
separate integration to install anywhere. Requires an MQTT broker
(this add-on declares `mqtt:want` in config.yaml so Supervisor can
auto-inject connection details, with mqtt_host/mqtt_username/
mqtt_password options as a manual fallback -- that auto-injection has
been seen not to work in some setups) and the MQTT integration
configured in Home Assistant itself.

Also reaches Home Assistant's own Core API directly (HAWatcher, below)
-- config.yaml's `homeassistant_api: true` auto-grants a SUPERVISOR_TOKEN
for this, same idea as the MQTT auto-injection. This is only used to
watch whichever entities are currently linked via the "Button N Linked
Entity" text entities and recolor that button's icon on a state change,
so a button can show a real entity's on/off state with no separate
automation -- it's a real expansion of what this add-on can do beyond
USB + MQTT (read access to entity states), worth knowing about even
though it's narrowly used.

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
  - homeassistant/text/akp05/button_<n>_link/config (retained) -- a
    second `text` entity per button: type an entity_id into it (e.g.
    "light.bedroom_lights") and this button's icon (whatever's currently
    set via the icon entity above, DEFAULT_ICON if nothing ever was)
    gets recolored green/red to track that entity's on/off state, live,
    via HAWatcher -- again no automation needed. Independent of the icon
    entity, so typing into both for the same button means the next state
    change just overwrites whatever the icon entity set; pick one.
  - akp05/status -- retained "online"/"offline" (MQTT last-will), used
    as every entity's availability topic.
  - akp05/event/<id> -- NOT retained, JSON {"event_type": "pressed"}
    etc., one topic per event entity above.
  - akp05/event -- NOT retained, plain "<event_type>:<object_id>", feeds
    only the device_automation triggers (which match a raw payload
    string, not a JSON field, hence the separate topic/format).
  - akp05/button_<n>/icon/state -- retained, echoes back the icon name
    that's actually showing, but ONLY on a successful render -- an
    invalid name is silently rejected rather than echoed, so the text
    field just won't change to a value that didn't actually work.
  - akp05/button_<n>/link/state -- retained, echoes back the currently
    linked entity_id (empty if none).

What it subscribes to:
  - akp05/power/set, akp05/brightness/set -- the light entity's own
    command topics ("ON"/"OFF" and "0".."100" respectively).
  - akp05/button_<n>/icon/set -- the icon text entities' command topic;
    empty string clears that button instead.
  - akp05/button_<n>/link/set -- the linked-entity text entities' command
    topic; empty string unlinks.
  - akp05/cmd -- JSON commands for things that don't map to a single
    entity: raw images (there's no MQTT entity type for uploading a
    file from the UI, so this stays automation/script-only), strip
    images, clearing. See the add-on's README for the payload shapes;
    call these from automations with the mqtt.publish service.
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from io import BytesIO

import paho.mqtt.client as mqtt
import websocket
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
from akp05_icons import build_icon

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


def _link_set_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/link/set"


def _link_state_topic(button: int) -> str:
    return f"{DEVICE_ID}/button_{button}/link/state"


LINK_SET_TOPICS = {_link_set_topic(button): button for button in range(1, 11)}

DEFAULT_ICON = "help-circle-outline"  # used if a button is linked before it's ever had an icon set
ICONS_PATH = "/data/button_icons.json"
LINKS_PATH = "/data/button_links.json"


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


def _link_discovery_payload(button: int) -> dict:
    # A second MQTT `text` entity per button, alongside the icon one --
    # type an entity_id into it (e.g. "light.bedroom_lights") and the
    # add-on watches that entity via Home Assistant's own Core API
    # (see HAWatcher) and recolors this button's icon green/red to match
    # its on/off state, with no separate automation needed. Uses
    # whatever icon name is currently set for the button (falls back to
    # DEFAULT_ICON) -- these two entities are independent, so if you
    # also type into the Icon entity later, it'll get overwritten back
    # by the next state change; pick one or the other per button.
    return {
        "name": f"Button {button} Linked Entity",
        "unique_id": f"{DEVICE_ID}_button_{button}_link",
        "command_topic": _link_set_topic(button),
        "state_topic": _link_state_topic(button),
        "icon": "mdi:link-variant",
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
            f"{DISCOVERY_PREFIX}/text/{DEVICE_ID}/button_{button}_link/config",
            json.dumps(_link_discovery_payload(button)),
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
        # Persisted across restarts: last icon name set per button, and
        # which entity (if any) each button's icon is linked to.
        self.button_icons: dict[int, str] = {int(k): v for k, v in _load_json(ICONS_PATH, {}).items()}
        self.button_links: dict[int, str] = {int(k): v for k, v in _load_json(LINKS_PATH, {}).items()}

    def connect_device(self):
        self.device = connect(self._on_report, full_init=True)
        self.publish_state()
        self._restore_button_icons()

    def _restore_button_icons(self):
        """full_init wipes every button's screen (CLE) on every connect
        -- including a plain add-on restart, not just a fresh install --
        but nothing was re-uploading whatever icon each button is
        supposed to show, so restarting silently blanked them until the
        icon entity's value was changed again (which is the only thing
        that actually re-triggers set_icon). Re-render everything we
        remember. Linked buttons render once here (gray, since we don't
        know their entity's state synchronously) and get recolored
        moments later once HAWatcher connects and calls sync_all()."""
        for button, icon in list(self.button_icons.items()):
            try:
                self.set_icon(button, icon, None)
            except Exception as exc:  # noqa: BLE001 - one bad icon shouldn't block the rest
                print(f"Couldn't restore icon for button {button} ({icon!r}): {exc}")

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
        out_len = self._out_len()
        send_commands(self.device, [
            crt_command("LIG", [0x00, 0x00, 0], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, STRIP_WIRE_KEY], out_len),
            crt_command("STP", [], out_len),
        ])
        self.brightness = 0
        self.publish_state()

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

    def set_link(self, button: int, entity_id: str):
        if entity_id:
            self.button_links[button] = entity_id
        else:
            self.button_links.pop(button, None)
        _save_json(LINKS_PATH, self.button_links)

    def refresh_linked_icon(self, button: int, is_on: bool):
        """Called by HAWatcher when a linked entity's state changes --
        recolors using whatever icon is already set for this button
        (DEFAULT_ICON if none ever was)."""
        icon = self.button_icons.get(button, DEFAULT_ICON)
        self.set_icon(button, icon, "on" if is_on else "off")

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


# Auto-injected once config.yaml's homeassistant_api: true is set --
# same auto-provisioning idea as the MQTT broker credentials, no manual
# long-lived access token needed.
HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
# Supervisor's documented proxy for an add-on's access to Core's own
# WebSocket API when homeassistant_api: true is set. Hasn't actually
# been exercised against a live instance yet -- if HAWatcher can't
# connect, check the add-on log for the exact error first.
HA_WS_URL = "ws://supervisor/core/websocket"
HA_REST_BASE = "http://supervisor/core/api"


class HAWatcher:
    """Watches whichever entities are currently linked (Bridge.button_links)
    via Home Assistant's own WebSocket API, so a button's icon can track
    an arbitrary entity's on/off state with no separate automation --
    just typing an entity_id into that button's "Linked Entity" text
    entity. Runs on its own background thread; MQTT (Bridge) and this
    are otherwise independent of each other."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self._next_id = 1
        self._subscribe_id = None

    def start(self):
        if not HA_TOKEN:
            print(
                "No SUPERVISOR_TOKEN -- 'homeassistant_api: true' missing "
                "from config.yaml? Linked-entity icon sync won't work "
                "(icon/set text entities still will)."
            )
            return
        print(f"Starting HA websocket watcher (token present, connecting to {HA_WS_URL})")
        threading.Thread(target=self._run_forever, daemon=True).start()

    def _run_forever(self):
        while True:
            try:
                ws = websocket.WebSocketApp(
                    HA_WS_URL,
                    on_open=lambda _ws: print("HA websocket connection opened"),
                    on_message=self._on_message,
                    on_error=lambda _ws, err: print(f"HA websocket error: {err}"),
                    on_close=lambda _ws, code, msg: print(f"HA websocket closed (code={code}, msg={msg})"),
                )
                ws.run_forever()
            except Exception as exc:  # noqa: BLE001 - keep retrying regardless of why it dropped
                print(f"HA websocket connection failed: {exc}")
            time.sleep(5)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        msg_type = data.get("type")
        if msg_type == "auth_required":
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        elif msg_type == "auth_invalid":
            print(f"HA websocket auth failed: {data.get('message')} -- check SUPERVISOR_TOKEN/homeassistant_api: true")
        elif msg_type == "auth_ok":
            print("HA websocket authenticated -- subscribing to state_changed")
            self._next_id += 1
            self._subscribe_id = self._next_id
            ws.send(json.dumps({"id": self._subscribe_id, "type": "subscribe_events", "event_type": "state_changed"}))
            self.sync_all()
        elif msg_type == "result" and data.get("id") == self._subscribe_id:
            if data.get("success"):
                print("HA websocket subscribed to state_changed events successfully")
            else:
                print(f"HA websocket subscribe_events FAILED: {data.get('error')}")
        elif msg_type == "event":
            event = data.get("event", {})
            if event.get("event_type") == "state_changed":
                event_data = event.get("data", {})
                new_state = event_data.get("new_state") or {}
                entity_id = event_data.get("entity_id")
                if entity_id in self.bridge.button_links.values():
                    print(f"HA state_changed for linked entity {entity_id}: {new_state.get('state')}")
                self._apply(entity_id, new_state.get("state"))

    def _apply(self, entity_id: str, state: str):
        for button, linked in list(self.bridge.button_links.items()):
            if linked == entity_id:
                try:
                    self.bridge.refresh_linked_icon(button, state == "on")
                except Exception as exc:  # noqa: BLE001 - one bad icon shouldn't stop the rest
                    print(f"Couldn't refresh icon for button {button} ({entity_id}): {exc}")

    def sync_one(self, button: int, entity_id: str):
        """Fetches current state right away for a newly-set link, rather
        than waiting for that entity's next change."""
        if not HA_TOKEN:
            print(f"Can't sync {entity_id} for button {button} -- no SUPERVISOR_TOKEN")
            return
        if not entity_id:
            return
        try:
            req = urllib.request.Request(
                f"{HA_REST_BASE}/states/{entity_id}", headers={"Authorization": f"Bearer {HA_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                state = json.loads(resp.read())["state"]
            print(f"Fetched {entity_id} state = {state!r} for button {button}")
            self.bridge.refresh_linked_icon(button, state == "on")
        except urllib.error.HTTPError as exc:
            print(f"Couldn't fetch initial state for {entity_id}: HTTP {exc.code} {exc.reason}")
        except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
            print(f"Couldn't fetch initial state for {entity_id}: {exc}")

    def sync_all(self):
        for button, entity_id in list(self.bridge.button_links.items()):
            self.sync_one(button, entity_id)


bridge_holder: dict = {}


def _decode_image(image_b64: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(image_b64))).convert("RGB")


def _handle_cmd(bridge: Bridge, payload: dict):
    action = payload.get("action")
    if action == "set_brightness":
        bridge.set_brightness(payload["value"])
    elif action == "clear_all":
        bridge.clear_all()
    elif action == "clear_button":
        bridge.clear_button(int(payload["button"]))
    elif action == "set_icon":
        bridge.set_icon(int(payload["button"]), payload["icon"], payload.get("state"))
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
    for topic in ICON_SET_TOPICS:
        client.subscribe(topic)
    for topic in LINK_SET_TOPICS:
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
        elif msg.topic in LINK_SET_TOPICS:
            button = LINK_SET_TOPICS[msg.topic]
            entity_id = msg.payload.decode().strip()
            bridge.set_link(button, entity_id)
            client.publish(_link_state_topic(button), entity_id, retain=True)
            ha_watcher = bridge_holder.get("ha_watcher")
            if entity_id and ha_watcher is not None:
                ha_watcher.sync_one(button, entity_id)
    except Exception as exc:  # noqa: BLE001 - a bad command shouldn't kill the bridge
        print(f"Error handling message on {msg.topic}: {exc}")


DEVICE_RETRY_DELAY = 5  # seconds


def _connect_device_with_retry(bridge: Bridge):
    """akp05_device.open_device() calls sys.exit(1) if the hidraw node
    isn't found yet -- fine for a one-shot CLI script, but for this
    long-running add-on that previously meant the *entire* process
    (MQTT client, HAWatcher, everything) died and had to be restarted by
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

    ha_watcher = HAWatcher(bridge)
    bridge_holder["ha_watcher"] = ha_watcher
    ha_watcher.start()

    # connect_async + loop_forever(retry_first_connection=True) instead
    # of a plain connect(): the mqtt:need service dependency should mean
    # Mosquitto is already up, but this survives it winning the startup
    # race anyway instead of crash-looping.
    client.connect_async(MQTT_HOST, MQTT_PORT)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
