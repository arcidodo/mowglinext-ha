"""The MowgliNext integration.

Bridges a MowgliNext robot mower into Home Assistant over MQTT. See
docs/MQTT_CONTROL.md in the main mowglinext repo for the wire contract this
integration is built against, and README.md here for setup + limitations.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import CONF_TOPIC_PREFIX, DOMAIN
from .coordinator import MowglinextHub

PLATFORMS: list[Platform] = [
    Platform.LAWN_MOWER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.CAMERA,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MowgliNext from a config entry."""
    hub = MowglinextHub(hass, entry, entry.data[CONF_TOPIC_PREFIX])
    await hub.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload on an Options flow change (e.g. mower_host) so the new value takes
    # effect everywhere that reads it at setup time, notably the device page's
    # "Visit" link (entity.py's device_info).
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # <prefix>/host can arrive well after the entities (and the device registry
    # entry) are already set up -- MQTT delivery, even of a retained message, is
    # not guaranteed to land before async_forward_entry_setups() returns. A
    # config entry reload only fires for OPTIONS changes, not for hub data, so
    # push the device registry's configuration_url directly whenever it changes,
    # instead of relying solely on entity.py's device_info (which is only read
    # again at (re)registration).
    @callback
    def _update_configuration_url() -> None:
        registry = dr.async_get(hass)
        device = registry.async_get_device(identifiers={(DOMAIN, hub.device_id)})
        if device is not None and device.configuration_url != hub.configuration_url:
            registry.async_update_device(device.id, configuration_url=hub.configuration_url)

    entry.async_on_unload(hub.async_add_listener("host", _update_configuration_url))
    _update_configuration_url()  # covers a retained <prefix>/host that already arrived
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: MowglinextHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_unload()
    return unload_ok
