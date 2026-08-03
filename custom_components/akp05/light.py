"""Brightness control for the Ajazz AKP05 panel, as a single light entity
representing the whole backlight (buttons + strip together, same as the
device's own LIG command)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import AkpClient
from .const import DOMAIN, MANUFACTURER, MODEL, SIGNAL_AVAILABILITY


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    client: AkpClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AkpBrightnessLight(entry, client)])


class AkpBrightnessLight(LightEntity):
    """Turning this off/on only ever sets brightness to 0%/last-known% --
    NOT the destructive full-image-wipe behavior of the CLI's
    `akp05_set_brightness.py off`. That's intentional: LIG is just a
    backlight/PWM level at the protocol level (see akp05_device.py's
    docstring), not a real power state, so mapping it to a normal light's
    on/off is accurate. The destructive wipe is only ever triggered
    explicitly via the akp05.clear_all service, never as a side effect of
    someone toggling this light off in an automation.
    """

    _attr_has_entity_name = True
    _attr_name = "Brightness"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, client: AkpClient) -> None:
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_brightness"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=entry.title,
        )
        # The bridge sets 50% on connect as part of its wake-up sequence
        # (see akp05_device.build_init_sequence) -- assume that until the
        # user changes it; there's no way to read brightness back from
        # the device itself.
        self._brightness_pct = 50
        self._is_on = True

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_AVAILABILITY, self._handle_availability))

    @callback
    def _handle_availability(self, _available: bool) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return round(self._brightness_pct * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if "brightness" in kwargs:
            self._brightness_pct = round(kwargs["brightness"] * 100 / 255)
        elif self._brightness_pct == 0:
            self._brightness_pct = 100
        await self._client.async_set_brightness(self._brightness_pct)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.async_set_brightness(0)
        self._is_on = False
        self.async_write_ha_state()
