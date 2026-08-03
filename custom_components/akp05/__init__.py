"""The Ajazz AKP05 integration.

Talks to the AKP05 Bridge add-on (addon/akp05_bridge/) over its local
HTTP+WebSocket API -- this integration never touches USB/HID itself,
since Home Assistant Core's own container doesn't get that access the
way an add-on does.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_PORT, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .client import AkpClient, AkpConnectionError
from .const import CONF_API_TOKEN, CONF_SUBTYPE, DOMAIN, EVENT_AKP05, MANUFACTURER, MODEL, SIGNAL_EVENT

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT]

SERVICE_SET_BUTTON_ICON = "set_button_icon"
SERVICE_SET_BUTTON_IMAGE = "set_button_image"
SERVICE_CLEAR_BUTTON = "clear_button"
SERVICE_CLEAR_ALL = "clear_all"

ATTR_BUTTON = "button"
ATTR_ICON = "icon"
ATTR_STATE = "state"
ATTR_IMAGE_B64 = "image_b64"

_BUTTON_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=1, max=10))

SET_BUTTON_ICON_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_BUTTON): _BUTTON_SCHEMA,
        vol.Required(ATTR_ICON): cv.string,
        vol.Optional(ATTR_STATE): vol.In(["on", "off"]),
    }
)
SET_BUTTON_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_BUTTON): _BUTTON_SCHEMA,
        vol.Required(ATTR_IMAGE_B64): cv.string,
    }
)
CLEAR_BUTTON_SCHEMA = vol.Schema({vol.Required(ATTR_BUTTON): _BUTTON_SCHEMA})


def _translate_event(event: dict) -> dict | None:
    """Map a bridge WS event to the (type, subtype) vocabulary that
    device_trigger.py's TRIGGERS list expects."""
    kind, event_id, action = event.get("type"), event.get("id"), event.get("action")
    if kind == "button":
        return {CONF_TYPE: action, CONF_SUBTYPE: f"button_{event_id}"}
    if kind == "encoder_button":
        return {CONF_TYPE: action, CONF_SUBTYPE: f"encoder_{event_id}_button"}
    if kind == "encoder_twist":
        return {CONF_TYPE: action, CONF_SUBTYPE: f"encoder_{event_id}"}
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = AkpClient(hass, entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_API_TOKEN])

    try:
        await client.async_check_connection()
    except AkpConnectionError as exc:
        raise ConfigEntryNotReady(f"Could not reach AKP05 bridge: {exc}") from exc

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title,
    )

    def _handle_bridge_event(event: dict) -> None:
        payload = _translate_event(event)
        if payload is not None:
            hass.bus.async_fire(EVENT_AKP05, {CONF_DEVICE_ID: device_entry.id, **payload})

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_EVENT, _handle_bridge_event))

    client.start()
    entry.async_on_unload(client.stop)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_set_button_icon(call: ServiceCall) -> None:
        await client.async_set_button_icon(call.data[ATTR_BUTTON], call.data[ATTR_ICON], call.data.get(ATTR_STATE))

    async def _handle_set_button_image(call: ServiceCall) -> None:
        await client.async_set_button_image(call.data[ATTR_BUTTON], call.data[ATTR_IMAGE_B64])

    async def _handle_clear_button(call: ServiceCall) -> None:
        await client.async_clear_button(call.data[ATTR_BUTTON])

    async def _handle_clear_all(call: ServiceCall) -> None:
        await client.async_clear_all()

    if not hass.services.has_service(DOMAIN, SERVICE_SET_BUTTON_ICON):
        hass.services.async_register(DOMAIN, SERVICE_SET_BUTTON_ICON, _handle_set_button_icon, schema=SET_BUTTON_ICON_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_SET_BUTTON_IMAGE, _handle_set_button_image, schema=SET_BUTTON_IMAGE_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_BUTTON, _handle_clear_button, schema=CLEAR_BUTTON_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_CLEAR_ALL, _handle_clear_all)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            for service in (SERVICE_SET_BUTTON_ICON, SERVICE_SET_BUTTON_IMAGE, SERVICE_CLEAR_BUTTON, SERVICE_CLEAR_ALL):
                hass.services.async_remove(DOMAIN, service)
    return unload_ok
