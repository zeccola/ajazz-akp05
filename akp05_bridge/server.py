"""
AKP05 bridge add-on server.

Owns the USB HID connection to the Ajazz AKP05 (the only container on a
Home Assistant OS host that can, via this add-on's usb/udev options) and
exposes it over a local HTTP+WebSocket API for the AKP05 custom
integration (custom_components/akp05, running in Core) to talk to.

Endpoints (all require `Authorization: Bearer <api_token>`, checked
against the add-on's `api_token` option -- set in its Configuration tab):
    GET  /status                     -> {"connected": bool, "brightness": int|None}
    POST /brightness   {"value": 0-100}
    POST /image         {"button": 1-10 | "all", "image_b64": "...", "clear": bool}
    POST /strip         {"image_b64": "...", "clear": bool}          (full 800x112)
    POST /strip_chunk   {"chunk": 11-14, "image_b64": "...", "clear": bool}
    POST /icon          {"button": 1-10, "icon": "mdi:...", "state": "on"|"off"|null}
    POST /clear_all      -> brightness 0 + wipe every button/strip (destructive,
                             kept as an explicit action, never a side effect --
                             see custom_components/akp05/light.py)
    GET  /ws             (WebSocket) -> pushes
                             {"type": "button", "id": 1-10, "action": "pressed"|"released"}
                             {"type": "encoder_button", "id": 1-4, "action": "pressed"|"released"}
                             {"type": "encoder_twist", "id": 1-4, "action": "cw"|"ccw"}

image_b64 is a base64-encoded PNG/JPEG of any size -- resizing/rotation/
JPEG-encoding for the device is handled here via akp05_device.encode_image,
same as every other script in this project.
"""

import asyncio
import base64
import json
import os
from io import BytesIO

from aiohttp import WSMsgType, web
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

PORT = 8000
OPTIONS_PATH = "/data/options.json"

BUTTON_KEYS = set(range(1, 11))
ENCODER_PRESS_KEYS = {0x37: 1, 0x35: 2, 0x33: 3, 0x36: 4}
ENCODER_TWIST_KEYS = {
    0xA0: (1, "ccw"), 0xA1: (1, "cw"),
    0x50: (2, "ccw"), 0x51: (2, "cw"),
    0x90: (3, "ccw"), 0x91: (3, "cw"),
    0x70: (4, "ccw"), 0x71: (4, "cw"),
}


def load_api_token() -> str:
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f).get("api_token", "") or ""
    return os.environ.get("API_TOKEN", "")


API_TOKEN = load_api_token()


class Bridge:
    """Owns the device connection and the set of connected WebSocket
    clients. Device I/O happens on a background thread (akp05_device's
    read loop / blocking writes); events are handed back to the asyncio
    loop via run_coroutine_threadsafe."""

    def __init__(self):
        self.device = None
        self.brightness = None
        self.clients: set[web.WebSocketResponse] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def connect_device(self):
        self.device = connect(self._on_report, full_init=True)
        self.brightness = 50  # matches build_init_sequence's wake-up brightness

    def _on_report(self, data):
        if len(data) <= STATE_IDX:
            return
        key, state = data[KEY_IDX], data[STATE_IDX]
        if key == 0:
            return
        event = self._classify(key, state)
        if event and self.loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(event), self.loop)

    @staticmethod
    def _classify(key: int, state: int):
        if key in BUTTON_KEYS:
            return {"type": "button", "id": key, "action": "pressed" if state == 1 else "released"}
        if key in ENCODER_PRESS_KEYS:
            return {"type": "encoder_button", "id": ENCODER_PRESS_KEYS[key], "action": "pressed" if state == 1 else "released"}
        if key in ENCODER_TWIST_KEYS:
            enc_id, direction = ENCODER_TWIST_KEYS[key]
            return {"type": "encoder_twist", "id": enc_id, "action": direction}
        return None

    async def _broadcast(self, event: dict):
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_json(event)
            except (ConnectionResetError, RuntimeError):
                dead.add(ws)
        self.clients -= dead

    def _out_len(self) -> int:
        return self.device.hid_caps.output_report_byte_length

    def set_brightness(self, value: int):
        value = max(0, min(100, int(value)))
        send_commands(self.device, [crt_command("LIG", [0x00, 0x00, value], self._out_len())])
        self.brightness = value

    def clear_all(self):
        out_len = self._out_len()
        send_commands(self.device, [
            crt_command("LIG", [0x00, 0x00, 0], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], out_len),
            crt_command("CLE", [0x00, 0x00, 0x00, STRIP_WIRE_KEY], out_len),
            crt_command("STP", [], out_len),
        ])
        self.brightness = 0

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


bridge = Bridge()


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if API_TOKEN and request.headers.get("Authorization") != f"Bearer {API_TOKEN}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


def _decode_image(image_b64: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(image_b64))).convert("RGB")


async def handle_status(request: web.Request):
    return web.json_response({"connected": bridge.device is not None, "brightness": bridge.brightness})


async def handle_brightness(request: web.Request):
    body = await request.json()
    bridge.set_brightness(body["value"])
    return web.json_response({"ok": True, "brightness": bridge.brightness})


async def handle_clear_all(request: web.Request):
    bridge.clear_all()
    return web.json_response({"ok": True})


async def handle_image(request: web.Request):
    body = await request.json()
    target = body["button"]
    buttons = list(range(1, 11)) if target == "all" else [int(target)]
    for button in buttons:
        if body.get("clear"):
            bridge.clear_button(button)
        else:
            jpeg_bytes = encode_image(_decode_image(body["image_b64"]), BUTTON_IMAGE_SIZE)
            bridge.set_button_image(button, jpeg_bytes)
    return web.json_response({"ok": True})


async def handle_strip(request: web.Request):
    body = await request.json()
    img = Image.new("RGB", STRIP_IMAGE_SIZE, (0, 0, 0)) if body.get("clear") else _decode_image(body["image_b64"]).resize(STRIP_IMAGE_SIZE, Image.LANCZOS)
    save_strip_canvas(img)
    bridge.set_strip(encode_image(img, STRIP_IMAGE_SIZE))
    return web.json_response({"ok": True})


async def handle_strip_chunk(request: web.Request):
    body = await request.json()
    chunk = int(body["chunk"])
    size = (STRIP_CHUNK_WIDTH, STRIP_IMAGE_SIZE[1])
    patch = Image.new("RGB", size, (0, 0, 0)) if body.get("clear") else _decode_image(body["image_b64"]).resize(size, Image.LANCZOS)
    bridge.set_strip_chunk(chunk, patch)
    return web.json_response({"ok": True})


async def handle_icon(request: web.Request):
    body = await request.json()
    button = int(body["button"])
    is_on = {"on": True, "off": False}.get(body.get("state"))
    try:
        img = build_icon(body["icon"], is_on)
    except KeyError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    bridge.set_button_image(button, encode_image(img, img.size))
    return web.json_response({"ok": True})


async def handle_ws(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bridge.clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        bridge.clients.discard(ws)
    return ws


async def on_startup(app: web.Application):
    bridge.loop = asyncio.get_running_loop()
    await bridge.loop.run_in_executor(None, bridge.connect_device)


def main():
    if not API_TOKEN:
        print("WARNING: no api_token set in this add-on's Configuration tab -- the API is unauthenticated.")

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/status", handle_status)
    app.router.add_post("/brightness", handle_brightness)
    app.router.add_post("/clear_all", handle_clear_all)
    app.router.add_post("/image", handle_image)
    app.router.add_post("/strip", handle_strip)
    app.router.add_post("/strip_chunk", handle_strip_chunk)
    app.router.add_post("/icon", handle_icon)
    app.router.add_get("/ws", handle_ws)
    app.on_startup.append(on_startup)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
