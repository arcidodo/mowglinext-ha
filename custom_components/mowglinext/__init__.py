"""The MowgliNext integration.

Bridges a MowgliNext robot mower into Home Assistant over MQTT. See
docs/MQTT_CONTROL.md in the main mowglinext repo for the wire contract this
integration is built against, and README.md here for setup + limitations.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_TOPIC_PREFIX, DOMAIN
from .coordinator import MowglinextHub

PLATFORMS: list[Platform] = [
    Platform.LAWN_MOWER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MowgliNext from a config entry."""
    hub = MowglinextHub(hass, entry, entry.data[CONF_TOPIC_PREFIX])
    await hub.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: MowglinextHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_unload()
    return unload_ok
