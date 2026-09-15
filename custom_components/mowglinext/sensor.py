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
            MowglinextRtkStatusSensor(hub),
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


# <prefix>/rtk_status's fix_type_name values (mowgli_interfaces/msg/GnssStatus,
# see docs/MQTT_CONTROL.md) mapped to a friendlier label for the ENUM sensor
# below. "UNKNOWN" (an out-of-range value) is deliberately not listed as an
# option -- native_value falls back to None for it rather than lying with a
# fabricated label.
_RTK_STATUS_LABELS: dict[str, str] = {
    "NO_FIX": "No fix",
    "GPS_FIX": "GPS fix",
    "RTK_FLOAT": "RTK float",
    "RTK_FIXED": "RTK fixed",
    "DEAD_RECKONING": "Dead reckoning",
}


class MowglinextRtkStatusSensor(MowglinextEntity, SensorEntity):
    """The same RTK fix classification the robot's own LED ring and GUI use
    (<prefix>/rtk_status, relayed from mowgli_interfaces/msg/GnssStatus) --
    not a lossy re-derivation, so this can never show something the robot
    itself disagrees with.
    """

    _attr_name = "RTK status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_RTK_STATUS_LABELS.values())
    _attr_icon = "mdi:satellite-variant"
    _topic_key = "rtk_status"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_rtk_status"

    @property
    def native_value(self) -> str | None:
        rtk = self.hub.data.get("rtk_status") or {}
        if not rtk.get("fix_valid"):
            return "No fix"
        return _RTK_STATUS_LABELS.get(rtk.get("fix_type_name"))

    @property
    def extra_state_attributes(self) -> dict:
        rtk = self.hub.data.get("rtk_status") or {}
        return {
            "quality_percent": rtk.get("quality_percent"),
            "rtk_mode_name": rtk.get("rtk_mode_name"),
            "fix_valid": rtk.get("fix_valid"),
        }


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
