"""Config flow for the Ajazz AKP05 integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_API_TOKEN, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_API_TOKEN): str,
    }
)


class InvalidAuth(Exception):
    """Wrong API token."""


async def _async_validate(hass, host: str, port: int, token: str) -> None:
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.get(
        f"http://{host}:{port}/status",
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status == 401:
            raise InvalidAuth
        resp.raise_for_status()


class AkpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Ajazz AKP05 bridge."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _async_validate(
                    self.hass, user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_API_TOKEN]
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - surface any connection failure as cannot_connect
                _LOGGER.exception("Unexpected error validating AKP05 bridge connection")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="AKP05", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)
