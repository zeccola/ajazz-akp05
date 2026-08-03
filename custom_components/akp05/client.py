"""Client for the AKP05 bridge add-on's local HTTP+WebSocket API.

The add-on (akp05_bridge/server.py) is the only thing that touches
the USB device -- this just talks to its API over the LAN/localhost.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_AVAILABILITY, SIGNAL_EVENT

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY = 5
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class AkpConnectionError(Exception):
    """Raised when the bridge add-on can't be reached or rejects the token."""


class AkpClient:
    """Owns the HTTP calls and the persistent WebSocket event stream."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, token: str) -> None:
        self._hass = hass
        self._base_url = f"http://{host}:{port}"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._session = async_get_clientsession(hass)
        self._ws_task: asyncio.Task | None = None
        self._stop = False
        self.available = False

    async def async_check_connection(self) -> dict:
        try:
            async with self._session.get(
                f"{self._base_url}/status", headers=self._headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise AkpConnectionError("Unauthorized -- check the API token")
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise AkpConnectionError(str(exc)) from exc

    def start(self) -> None:
        self._stop = False
        self._ws_task = self._hass.loop.create_task(self._ws_loop())

    async def stop(self) -> None:
        self._stop = True
        if self._ws_task is not None:
            self._ws_task.cancel()

    def _set_available(self, value: bool) -> None:
        if self.available == value:
            return
        self.available = value
        async_dispatcher_send(self._hass, SIGNAL_AVAILABILITY, value)

    async def _ws_loop(self) -> None:
        while not self._stop:
            try:
                async with self._session.ws_connect(
                    f"{self._base_url}/ws", headers=self._headers, heartbeat=30
                ) as ws:
                    self._set_available(True)
                    _LOGGER.debug("Connected to AKP05 bridge websocket")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            async_dispatcher_send(self._hass, SIGNAL_EVENT, msg.json())
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                _LOGGER.debug("AKP05 bridge websocket error: %s", exc)
            self._set_available(False)
            if not self._stop:
                await asyncio.sleep(RECONNECT_DELAY)

    async def _post(self, path: str, json_body: dict) -> None:
        async with self._session.post(
            f"{self._base_url}{path}", json=json_body, headers=self._headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            resp.raise_for_status()

    async def async_set_brightness(self, value: int) -> None:
        await self._post("/brightness", {"value": value})

    async def async_clear_all(self) -> None:
        await self._post("/clear_all", {})

    async def async_set_button_icon(self, button: int, icon: str, state: str | None) -> None:
        await self._post("/icon", {"button": button, "icon": icon, "state": state})

    async def async_set_button_image(self, button: int, image_b64: str) -> None:
        await self._post("/image", {"button": button, "image_b64": image_b64})

    async def async_clear_button(self, button: int) -> None:
        await self._post("/image", {"button": button, "clear": True})
