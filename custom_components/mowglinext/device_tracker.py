"""MowgliNext device tracker.

Backed by <prefix>/gps (a raw NavSatFix relay with real lat/lon), NOT
<prefix>/position — that topic is in the mower's local odom frame and has
no real-world coordinates, so it cannot back a map entity. See
docs/MQTT_CONTROL.md in the main repo.
"""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
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
    async_add_entities([MowglinextDeviceTracker(hub)])


class MowglinextDeviceTracker(MowglinextEntity, TrackerEntity):
    _attr_name = "Location"
    _topic_key = "gps"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_location"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return (self.hub.data.get("gps") or {}).get("latitude")

    @property
    def longitude(self) -> float | None:
        return (self.hub.data.get("gps") or {}).get("longitude")

    @property
    def extra_state_attributes(self) -> dict:
        gps = self.hub.data.get("gps") or {}
        # status: NavSatStatus.status (-1 NO_FIX, 0 FIX, 1 SBAS_FIX,
        # 2 GBAS_FIX) — does NOT distinguish RTK Fixed from Float.
        return {"altitude": gps.get("altitude"), "fix_status": gps.get("status")}
