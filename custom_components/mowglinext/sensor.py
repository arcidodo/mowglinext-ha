"""MowgliNext sensors — all diagnostic/telemetry values not covered by the
lawn_mower entity's own state.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MowglinextBatterySensor(hub),
            MowglinextCoverageSensor(hub),
            MowglinextGpsQualitySensor(hub),
            MowglinextStateSensor(hub),
        ]
    )


class _HighLevelStatusSensor(MowglinextEntity, SensorEntity):
    """Base for the sensors below: all read a single field from
    <prefix>/high_level_status."""

    _topic_key = "high_level_status"
    _field: str = ""

    @property
    def native_value(self):
        return (self.hub.data.get("high_level_status") or {}).get(self._field)


class MowglinextBatterySensor(_HighLevelStatusSensor):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _field = "battery_percent"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_battery"


class MowglinextCoverageSensor(_HighLevelStatusSensor):
    _attr_name = "Coverage"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:map-check-outline"
    _field = "coverage_percent"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_coverage"


class MowglinextGpsQualitySensor(_HighLevelStatusSensor):
    _attr_name = "GPS quality"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:crosshairs-gps"
    _field = "gps_quality_percent"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_gps_quality"


class MowglinextStateSensor(_HighLevelStatusSensor):
    """Raw state_name — the exact BT substate, not the lossy 4-activity
    mapping the lawn_mower entity is forced to use (e.g. "RECORDING" and
    "DIG_OBSTRUCTION" both collapse to non-mowing activities there)."""

    _attr_name = "State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"
    _field = "state_name"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_state"

    @property
    def extra_state_attributes(self) -> dict:
        status = self.hub.data.get("high_level_status") or {}
        return {"sub_state_name": status.get("sub_state_name")}
