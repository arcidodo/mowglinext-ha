"""MowgliNext select entity — arm a recorded area to start mowing.

Picking an option only arms it (stores the name on the shared hub); it does
NOT publish anything by itself. Press the companion "Start selected area"
button (button.py) to actually start it — that's the point where the name
is resolved to an index, against the freshest <prefix>/areas payload, since
there is no persistent "selected area" state on the mower itself.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity
from .map_render import PALETTES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextAreaSelect(hub), MowglinextMapStyleSelect(hub)])


class MowglinextAreaSelect(MowglinextEntity, SelectEntity):
    """Arms an area on the shared hub for the "Start selected area" button.

    Deliberately does not resolve or publish anything itself: recorded areas
    have no stable id yet (mowglinext#637), so an unrelated area add/edit/
    delete between arming and pressing the button can silently reassign
    every index. The button re-resolves by name at press time instead of
    trusting an index cached here. See docs/MQTT_CONTROL.md in the main repo
    for the full interim-contract caveat.
    """

    _attr_name = "Area to start"
    _attr_icon = "mdi:map-marker-radius"
    _topic_key = "areas"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_start_area"
        # Momentary UI control, not a persisted mower setting: this only
        # ever reflects the last area WE armed, purely so the UI shows
        # something sensible after a selection -- it is not read back from
        # the mower (there is no "currently selected area" concept there).
        self._current_option: str | None = None

    @property
    def options(self) -> list[str]:
        areas = self.hub.data.get("areas") or []
        return [area["name"] for area in areas if "name" in area]

    @property
    def current_option(self) -> str | None:
        if self._current_option in self.options:
            return self._current_option
        return None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            # Fail fast rather than silently arming a name that's already
            # gone -- the button re-validates again at press time too,
            # since the list can still change in the gap between arming and
            # pressing.
            raise HomeAssistantError(
                f"'{option}' is not in the current recorded-area list; it may have been "
                "renamed or removed. Try again after the list refreshes."
            )
        self._current_option = option
        self.hub.pending_area_name = option
        self.async_write_ha_state()


class MowglinextMapStyleSelect(MowglinextEntity, SelectEntity, RestoreEntity):
    """Colour style of the map camera (a dashboard preference, not a mower setting)."""

    _attr_name = "Map style"
    _attr_icon = "mdi:palette"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(PALETTES)
    _topic_key = "map_style"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_map_style"

    @property
    def available(self) -> bool:
        # A display preference: usable whether or not the mower is currently online.
        return True

    @property
    def current_option(self) -> str:
        return self.hub.map_style

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.hub.async_set_map_style(last.state)

    async def async_select_option(self, option: str) -> None:
        if option not in PALETTES:
            raise HomeAssistantError(f"Unknown map style: {option}")
        self.hub.async_set_map_style(option)
