"""Constants for the Ajazz AKP05 integration."""

DOMAIN = "akp05"

CONF_API_TOKEN = "api_token"
DEFAULT_PORT = 8000

MANUFACTURER = "Ajazz"
MODEL = "AKP05"

# Not a stock homeassistant.const symbol -- defined locally, like most
# integrations' device_trigger.py do, since it's just a dict key that
# needs to match between async_get_triggers/TRIGGER_SCHEMA/fired events.
CONF_SUBTYPE = "subtype"

EVENT_AKP05 = "akp05_event"

# Dispatcher signals, internal to this integration (not the HA event bus).
SIGNAL_EVENT = f"{DOMAIN}_event"
SIGNAL_AVAILABILITY = f"{DOMAIN}_availability"

NUM_BUTTONS = 10
NUM_ENCODERS = 4
