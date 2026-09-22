"""MowgliNext number entity — the map camera's display rotation.

Matches the robot GUI's own "Map Rotation" (Mapbox bearing, gui.map.display.bearing):
0 keeps north up. That value lives in the GUI's own local display settings, not on
MQTT, so it can't be read from the mower -- this lets the operator dial in the same
value here if they want the two views to line up visually.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextMapRotationNumber(hub)])


class MowglinextMapRotationNumber(MowglinextEntity, NumberEntity, RestoreEntity):
    """Compass bearing shown at the top of the map camera's image."""

    _attr_name = "Map rotation"
    _attr_icon = "mdi:compass-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = -180
    _attr_native_max_value = 180
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "°"
    _topic_key = "map_rotation_deg"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_map_rotation"

    @property
    def available(self) -> bool:
        # A display preference: usable whether or not the mower is currently online.
        return True

    @property
    def native_value(self) -> float:
        return self.hub.map_rotation_deg

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self.hub.async_set_map_rotation_deg(float(last.state))
            except ValueError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        self.hub.async_set_map_rotation_deg(value)
