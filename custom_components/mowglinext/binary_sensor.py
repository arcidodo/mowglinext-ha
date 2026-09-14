"""MowgliNext binary sensors."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextEmergencyBinarySensor(hub)])


class MowglinextEmergencyBinarySensor(MowglinextEntity, BinarySensorEntity):
    """Backed by <prefix>/emergency.active_emergency."""

    _attr_name = "Emergency"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _topic_key = "emergency"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_emergency"

    @property
    def is_on(self) -> bool | None:
        payload = self.hub.data.get("emergency")
        if not payload:
            return None
        return bool(payload.get("active_emergency"))

    @property
    def extra_state_attributes(self) -> dict:
        payload = self.hub.data.get("emergency") or {}
        return {
            "reason": payload.get("reason"),
            "latched": payload.get("latched_emergency"),
        }
