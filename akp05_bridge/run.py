"""
AKP05 bridge add-on -- MQTT edition.

Talks to the Ajazz AKP05 over raw HID (the only container on a Home
Assistant OS host that can, via this add-on's usb/udev options) and
exposes it to Home Assistant purely through MQTT discovery -- no
separate integration to install anywhere. Requires an MQTT broker (this
add-on declares `mqtt:need` in config.yaml, so Supervisor auto-injects
connection details once e.g. the Mosquitto broker add-on is running) and
the MQTT integration configured in Home Assistant itself.

What gets published (retained, so they survive an HA restart without
needing this add-on to republish first):
  - homeassistant/light/akp05/brightness/config -- one light entity for
    panel brightness (buttons + strip together, same as the device's own
    LIG command). Turning it off just sets brightness to 0 -- it does
    NOT wipe button/strip images (see akp05/cmd's clear_all for that,
    kept as an explicit action, never a side effect of the light).
  - homeassistant/device_automation/akp05/<id>/config -- one device
    trigger per button (pressed/released), encoder button
    (pressed/released), and encoder twist (cw/ccw), so they show up
    under Settings -> Automations -> Add Trigger -> Device -> AKP05,
    same as e.g. a Zigbee remote. All share one event topic
    (akp05/event); each trigger's discovery config just matches a
    distinct payload on it.
  - akp05/status -- retained "online"/"offline" (MQTT last-will), used
    as the light's availability topic.

What it subscribes to:
  - akp05/brightness/set -- plain "0".."100" (the light entity's own
    command topic).
  - akp05/cmd -- JSON commands for things that don't map to a single
    entity/trigger: icons, raw images, clearing. See the add-on's
    README for the payload shapes; call these from automations with the
    mqtt.publish service.
"""

import base64
import json
import os
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
from akp05_icons import build_icon

OPTIONS_PATH = "/data/options.json"
DEVICE_ID = "akp05"

STATUS_TOPIC = f"{DEVICE_ID}/status"
EVENT_TOPIC = f"{DEVICE_ID}/event"
BRIGHTNESS_SET_TOPIC = f"{DEVICE_ID}/brightness/set"
BRIGHTNESS_STATE_TOPIC = f"{DEVICE_ID}/brightness/state"
CMD_TOPIC = f"{DEVICE_ID}/cmd"

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
    return {
        "name": "Brightness",
        "unique_id": f"{DEVICE_ID}_brightness",
        "brightness_command_topic": BRIGHTNESS_SET_TOPIC,
        "brightness_state_topic": BRIGHTNESS_STATE_TOPIC,
        "brightness_scale": 100,
        # No separate on/off payload -- HA derives on/off from brightness
        # (0 = off) and sends brightness for both "turn on" and "turn
        # off" clicks, matching LIG's actual single-value protocol.
        "on_command_type": "brightness",
        "availability_topic": STATUS_TOPIC,
        "device": DEVICE_INFO,
    }


def _triggers():
    """(trigger type, subtype) pairs. Payload on EVENT_TOPIC for each is
    "type:subtype" -- keep in sync with _classify() below."""
    for button in range(1, 11):
        yield "pressed", f"button_{button}"
        yield "released", f"button_{button}"
    for encoder in range(1, 5):
        yield "pressed", f"encoder_{encoder}_button"
        yield "released", f"encoder_{encoder}_button"
        yield "cw", f"encoder_{encoder}"
        yield "ccw", f"encoder_{encoder}"


def _trigger_discovery_payload(trigger_type: str, subtype: str) -> dict:
    return {
        "automation_type": "trigger",
        "type": trigger_type,
        "subtype": subtype,
        "topic": EVENT_TOPIC,
        "payload": f"{trigger_type}:{subtype}",
        "device": DEVICE_INFO,
    }


def publish_discovery(client: mqtt.Client):
    client.publish(
        f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}/brightness/config",
        json.dumps(_light_discovery_payload()),
        retain=True,
    )
    for trigger_type, subtype in _triggers():
        object_id = f"{trigger_type}_{subtype}"
        client.publish(
            f"{DISCOVERY_PREFIX}/device_automation/{DEVICE_ID}/{object_id}/config",
            json.dumps(_trigger_discovery_payload(trigger_type, subtype)),
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

    def connect_device(self):
        self.device = connect(self._on_report, full_init=True)
        self.publish_brightness()

    def _out_len(self) -> int:
        return self.device.hid_caps.output_report_byte_length

    def publish_brightness(self):
        self.client.publish(BRIGHTNESS_STATE_TOPIC, str(self.brightness), retain=True)

    def set_brightness(self, value: int):
        value = max(0, min(100, int(value)))
        send_commands(self.device, [crt_command("LIG", [0x00, 0x00, value], self._out_len())])
        self.brightness = value
        self.publish_brightness()

    def clear_all(self):
        out_len = self._out_len()
        send_commands(self.device, [
            crt_command("LIG", [0x00, 0x00, 0], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, STRIP_WIRE_KEY], out_len),
            crt_command("STP", [], out_len),
        ])
        self.brightness = 0
        self.publish_brightness()

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
        img = build_icon(icon, is_on)
        self.set_button_image(button, encode_image(img, img.size))

    @staticmethod
    def _classify(key: int, state: int):
        if key in BUTTON_KEYS:
            return ("pressed" if state == 1 else "released"), f"button_{key}"
        if key in ENCODER_PRESS_KEYS:
            return ("pressed" if state == 1 else "released"), f"encoder_{ENCODER_PRESS_KEYS[key]}_button"
        if key in ENCODER_TWIST_KEYS:
            enc_id, direction = ENCODER_TWIST_KEYS[key]
            return direction, f"encoder_{enc_id}"
        return None, None

    def _on_report(self, data):
        if len(data) <= STATE_IDX:
            return
        key, state = data[KEY_IDX], data[STATE_IDX]
        if key == 0:
            return
        trigger_type, subtype = self._classify(key, state)
        if trigger_type:
            self.client.publish(EVENT_TOPIC, f"{trigger_type}:{subtype}")


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
    client.subscribe(BRIGHTNESS_SET_TOPIC)
    client.subscribe(CMD_TOPIC)
    publish_discovery(client)
    client.publish(STATUS_TOPIC, "online", retain=True)
    bridge = bridge_holder.get("bridge")
    if bridge is not None:
        bridge.publish_brightness()


def on_message(client, userdata, msg):
    bridge = bridge_holder["bridge"]
    try:
        if msg.topic == BRIGHTNESS_SET_TOPIC:
            bridge.set_brightness(int(msg.payload.decode()))
        elif msg.topic == CMD_TOPIC:
            _handle_cmd(bridge, json.loads(msg.payload.decode()))
    except Exception as exc:  # noqa: BLE001 - a bad command shouldn't kill the bridge
        print(f"Error handling message on {msg.topic}: {exc}")


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.will_set(STATUS_TOPIC, "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    bridge = Bridge(client)
    bridge_holder["bridge"] = bridge
    bridge.connect_device()

    # connect_async + loop_forever(retry_first_connection=True) instead
    # of a plain connect(): the mqtt:need service dependency should mean
    # Mosquitto is already up, but this survives it winning the startup
    # race anyway instead of crash-looping.
    client.connect_async(MQTT_HOST, MQTT_PORT)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
