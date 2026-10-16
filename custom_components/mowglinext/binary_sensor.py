"""MowgliNext binary sensors."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RAIN_STATE_NAMES
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MowglinextEmergencyBinarySensor(hub),
            MowglinextRainSensorBinarySensor(hub),
            MowglinextRainPauseBinarySensor(hub),
            MowglinextExternalRainBinarySensor(hub),
        ]
    )


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


class MowglinextRainSensorBinarySensor(MowglinextEntity, BinarySensorEntity):
    """The mower's OWN rain sensor (<prefix>/status.rain_detected): on while it is wet. This is
    only the sensor on the mower; an external verdict is the separate "External rain" sensor, and
    "Paused for rain" says whether the mower actually stopped for rain."""

    _attr_name = "Rain sensor"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _topic_key = "status"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_rain_sensor"

    @property
    def is_on(self) -> bool | None:
        payload = self.hub.data.get("status")
        if not payload or "rain_detected" not in payload:
            return None
        return bool(payload.get("rain_detected"))


class MowglinextRainPauseBinarySensor(MowglinextEntity, BinarySensorEntity):
    """On while the mower is stopped, docking, waiting or resuming BECAUSE of rain -- whichever
    source (its own sensor or an external verdict) triggered it. Derived from the behavior
    tree's state name (<prefix>/high_level_status), so it follows what the mower actually does
    rather than any single sensor. Off while idle, charging or mowing normally."""

    _attr_name = "Paused for rain"
    _attr_icon = "mdi:weather-pouring"
    _topic_key = "high_level_status"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_rain_pause"

    @property
    def is_on(self) -> bool | None:
        payload = self.hub.data.get("high_level_status")
        if not payload:
            return None
        return payload.get("state_name") in RAIN_STATE_NAMES

    @property
    def extra_state_attributes(self) -> dict:
        payload = self.hub.data.get("high_level_status") or {}
        return {"state_name": payload.get("state_name")}


class MowglinextExternalRainBinarySensor(MowglinextEntity, BinarySensorEntity):
    """On while the mower is COUNTING an external rain verdict right now
    (<prefix>/external_rain_state.active): a fresh wet verdict, with the feature switched on.

    This is what the mower itself reports, not what Home Assistant last sent -- so a verdict that
    expired, or was switched off in the mower's Rain settings, reads off here even though HA
    still thinks it said "yes". Unknown until the mower has published the topic (older mower
    software never does).
    """

    _attr_name = "External rain"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _topic_key = "external_rain_state"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_external_rain"

    @property
    def is_on(self) -> bool | None:
        payload = self.hub.data.get("external_rain_state")
        if not payload:
            return None
        return bool(payload.get("active"))

    @property
    def extra_state_attributes(self) -> dict:
        payload = self.hub.data.get("external_rain_state") or {}
        return {
            "state": payload.get("state"),
            "last_verdict_wet": payload.get("wet"),
            "enabled": payload.get("enabled"),
            "own_sensor_wet": payload.get("onboard_wet"),
            "age_s": payload.get("age_s"),
            "expires_in_s": payload.get("expires_in_s"),
        }
