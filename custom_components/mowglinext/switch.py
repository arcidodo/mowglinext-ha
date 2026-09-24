"""MowgliNext switch entity — show only the active zone on the map camera.

A display preference (not a mower setting): with a large garden split into zones the
whole-garden view makes the planned mowing paths a haze, so this zooms in on the zone
being mowed and leaves the others, and their trail/path, out.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
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
    async_add_entities([MowglinextMapFocusSwitch(hub)])


class MowglinextMapFocusSwitch(MowglinextEntity, SwitchEntity, RestoreEntity):
    """Only the zone being mowed is shown on the map camera."""

    _attr_name = "Map: active zone only"
    _attr_icon = "mdi:crosshairs"
    _attr_entity_category = EntityCategory.CONFIG
    _topic_key = "map_focus_active"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_map_focus_active"

    @property
    def available(self) -> bool:
        # A display preference: usable whether or not the mower is currently online.
        return True

    @property
    def is_on(self) -> bool:
        return self.hub.map_focus_active

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.hub.async_set_map_focus_active(last.state == STATE_ON)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.hub.async_set_map_focus_active(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hub.async_set_map_focus_active(False)
