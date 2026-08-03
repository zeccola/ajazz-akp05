"""Device triggers for the Ajazz AKP05 -- buttons/encoders as native
"Device > When..." automation triggers, the way e.g. Zigbee remotes work.

This delegates to the built-in `event` trigger platform rather than
listening on the event bus directly (the same pattern deconz/zha/hue use
for their remotes): __init__.py fires a plain HA bus event (EVENT_AKP05)
with device_id/type/subtype, and async_attach_trigger below just asks the
event platform to filter for a matching one.

NOTE: this file leans on Home Assistant's internal device_automation/
trigger plumbing, which has shifted shape across versions more than the
rest of this integration. If triggers don't show up under a device's
"Add Trigger" in the UI, check Core's log for a device_automation/akp05
load error first. Regardless of whether this file works, the fallback
always available is a plain HA "Event" trigger in an automation, event
type `akp05_event`, matching on event_data {type, subtype} (see README).
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SUBTYPE, DOMAIN, EVENT_AKP05, NUM_BUTTONS, NUM_ENCODERS

# (trigger type, subtype) pairs -- must match _translate_event() in __init__.py
TRIGGERS: list[tuple[str, str]] = []
for _button in range(1, NUM_BUTTONS + 1):
    TRIGGERS.append(("pressed", f"button_{_button}"))
    TRIGGERS.append(("released", f"button_{_button}"))
for _encoder in range(1, NUM_ENCODERS + 1):
    TRIGGERS.append(("pressed", f"encoder_{_encoder}_button"))
    TRIGGERS.append(("released", f"encoder_{_encoder}_button"))
    TRIGGERS.append(("cw", f"encoder_{_encoder}"))
    TRIGGERS.append(("ccw", f"encoder_{_encoder}"))

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In({t for t, _ in TRIGGERS}),
        vol.Required(CONF_SUBTYPE): vol.In({s for _, s in TRIGGERS}),
    }
)


async def async_validate_trigger_config(hass: HomeAssistant, config: ConfigType) -> ConfigType:
    return TRIGGER_SCHEMA(config)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict]:
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None or not any(entry_id in hass.data.get(DOMAIN, {}) for entry_id in device.config_entries):
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger_type,
            CONF_SUBTYPE: subtype,
        }
        for trigger_type, subtype in TRIGGERS
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_AKP05,
            event_trigger.CONF_EVENT_DATA: {
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                CONF_TYPE: config[CONF_TYPE],
                CONF_SUBTYPE: config[CONF_SUBTYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")
