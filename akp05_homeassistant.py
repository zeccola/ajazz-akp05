"""
Bridge AKP05 button/encoder presses to Home Assistant service calls over
its REST API -- e.g. pressing button 1 toggles light.bedroom_lights.

Setup:
    1. In Home Assistant: profile (bottom-left) -> Security -> Long-Lived
       Access Tokens -> Create Token. Copy it somewhere safe.
    2. Copy ha_config.example.json to ha_config.json and fill in:
         - base_url: your Home Assistant URL (e.g. http://homeassistant.local:8123)
         - token: the access token from step 1
         - bindings: map event IDs (see below) to just an entity_id:
           { "entity_id": "light.bedroom_lights" }
       ha_config.json holds a live credential -- don't commit/share it.

Event IDs you can bind:
    button_1 .. button_10          (fires on press, not release)
    encoder_1_button .. encoder_4_button   (fires on press)
    encoder_1_cw / encoder_1_ccw   (and _2_/_3_/_4_, fire once per twist tick)

Each binding needs only "entity_id" -- the domain (the part before the
dot, e.g. "light") is read straight off it, and the service called is
"toggle" by default, which covers light/switch/fan/etc. If some entity
needs a different service (e.g. a scene, which only has "turn_on"), add
an explicit "service" to override the default:
    { "entity_id": "scene.movie_night", "service": "turn_on" }

Encoders (encoder_N_cw/_ccw) are single-shot per detent, not press/release,
so they suit incremental actions better than "toggle" -- add "data" for
any extra service parameters beyond entity_id, e.g. step the brightness
up/down a bit each tick:
    "encoder_1_cw":  { "entity_id": "light.bedroom_lights", "service": "turn_on", "data": {"brightness_step_pct": 10} }
    "encoder_1_ccw": { "entity_id": "light.bedroom_lights", "service": "turn_on", "data": {"brightness_step_pct": -10} }
or for a media player's volume:
    "encoder_2_cw":  { "entity_id": "media_player.living_room", "service": "volume_up" }
    "encoder_2_ccw": { "entity_id": "media_player.living_room", "service": "volume_down" }

Optional per-binding "icon" (button_N bindings only -- encoders don't have
a clean, isolated image slot the way buttons do): any Material Design
Icons name (the same "mdi:xxx" icons Home Assistant's own UI uses, e.g.
"floor-lamp-outline" -- browse them at https://pictogrammers.com/library/mdi/).
After the service call, this queries the entity's actual current state
from HA (not just assumed from the toggle) and draws that icon on the
button colored green/red/gray for on/off/unknown. Also synced once on
startup so the icon is correct before you touch anything. First use of
any icon downloads and caches the MDI font (~1.3MB, one-time) -- see
akp05_icons.py.

This uses the same full wake-up sequence as akp05_buttons.py, which
means it WILL CLEAR YOUR BUTTON IMAGES on startup (same as every other
listener script here -- see akp05_device.py for why). Icon-bound buttons
get their status icon back immediately after; any other button images
you had will need re-flashing.

Usage:
    python akp05_homeassistant.py [path/to/ha_config.json]   (default: ha_config.json)
"""

import json
import os
import socket
import sys
import time

import requests
import urllib3.util.connection as urllib3_conn

from akp05_device import BUTTON_TO_WIRE_KEY, KEY_IDX, STATE_IDX, connect, encode_image, upload_image
from akp05_icons import build_icon

# Windows quirk: "*.local" hostnames (mDNS) often resolve to a link-local
# IPv6 address, and a urllib3/Windows interaction mangles the scope id
# into the flowinfo field, raising "OverflowError: connect(): flowinfo
# must be 0-1048575". Forcing IPv4-only DNS resolution avoids it, and
# Home Assistant always listens on IPv4 too, so this has no downside.
urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

BUTTON_KEYS = {i: f"button_{i}" for i in range(1, 11)}

ENCODER_PRESS_KEYS = {
    0x37: "encoder_1_button",
    0x35: "encoder_2_button",
    0x33: "encoder_3_button",
    0x36: "encoder_4_button",
}

ENCODER_TWIST_KEYS = {
    0xA0: "encoder_1_ccw", 0xA1: "encoder_1_cw",
    0x50: "encoder_2_ccw", 0x51: "encoder_2_cw",
    0x90: "encoder_3_ccw", 0x91: "encoder_3_cw",
    0x70: "encoder_4_ccw", 0x71: "encoder_4_cw",
}

DEFAULT_SERVICE = "toggle"


def classify(key: int, state: int):
    """Returns (event_id, fire_now). Buttons/encoder-presses only fire on
    the DOWN edge (state==1); twists are single-shot, always fire."""
    if key in BUTTON_KEYS:
        return BUTTON_KEYS[key], state == 1
    if key in ENCODER_PRESS_KEYS:
        return ENCODER_PRESS_KEYS[key], state == 1
    if key in ENCODER_TWIST_KEYS:
        return ENCODER_TWIST_KEYS[key], True
    return None, False


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def service_of(binding: dict) -> str:
    return binding.get("service", DEFAULT_SERVICE)


def call_ha_service(base_url: str, token: str, domain: str, service: str, entity_id: str, data: dict | None = None):
    url = f"{base_url.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"entity_id": entity_id, **(data or {})}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=5)
        if resp.status_code >= 400:
            print(f"  HA error {resp.status_code}: {resp.text[:200]}")
        else:
            extra = f", {data}" if data else ""
            print(f"  -> {domain}.{service}({entity_id}{extra}) ok")
    except requests.RequestException as exc:
        print(f"  HA request failed: {exc}")


def get_ha_state(base_url: str, token: str, entity_id: str) -> str | None:
    url = f"{base_url.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        return resp.json()["state"]
    except requests.RequestException as exc:
        print(f"  couldn't fetch state for {entity_id}: {exc}")
        return None


def sync_button_icon(device, base_url: str, token: str, button_num: int, entity_id: str, icon_name: str):
    state = get_ha_state(base_url, token, entity_id)
    if state is None:
        return
    is_on = state == "on"
    try:
        img = build_icon(icon_name, is_on)
    except KeyError as exc:
        print(f"  {exc}")
        return
    jpeg_bytes = encode_image(img, img.size)
    wire_key = BUTTON_TO_WIRE_KEY[button_num]
    upload_image(device, wire_key, jpeg_bytes)
    print(f"  icon updated: {entity_id} is {state}")


def make_handler(config, device_holder):
    base_url = config["base_url"]
    token = config["token"]
    bindings = config["bindings"]

    def raw_data_handler(data):
        if len(data) <= STATE_IDX:
            return
        key = data[KEY_IDX]
        state = data[STATE_IDX]
        if key == 0:
            return

        event_id, fire = classify(key, state)
        if not event_id or not fire:
            return

        binding = bindings.get(event_id)
        ts = time.strftime("%H:%M:%S")
        if binding is None:
            return  # unbound event, ignore quietly

        entity_id = binding["entity_id"]
        domain = domain_of(entity_id)
        service = service_of(binding)
        print(f"[{ts}] {event_id} -> {domain}.{service}({entity_id})")
        call_ha_service(base_url, token, domain, service, entity_id, binding.get("data"))

        icon_name = binding.get("icon")
        if icon_name and event_id.startswith("button_"):
            device = device_holder.get("device")
            if device is not None:
                time.sleep(0.3)  # let HA finish applying the toggle before we re-query state
                button_num = int(event_id.split("_")[1])
                sync_button_icon(device, base_url, token, button_num, entity_id, icon_name)

    return raw_data_handler


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "ha_config.json"
    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Copy ha_config.example.json to ha_config.json and fill it in first.")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    print(f"Loaded {len(config['bindings'])} binding(s) from {config_path}")
    for event_id, b in config["bindings"].items():
        domain = domain_of(b["entity_id"])
        service = service_of(b)
        print(f"  {event_id} -> {domain}.{service}({b['entity_id']})" + (f"  [icon: {b['icon']}]" if b.get("icon") else ""))

    device_holder = {}
    device = connect(make_handler(config, device_holder))
    device_holder["device"] = device

    for event_id, b in config["bindings"].items():
        if b.get("icon") and event_id.startswith("button_"):
            button_num = int(event_id.split("_")[1])
            sync_button_icon(device, config["base_url"], config["token"], button_num, b["entity_id"], b["icon"])

    print("\nListening for button/encoder presses. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
