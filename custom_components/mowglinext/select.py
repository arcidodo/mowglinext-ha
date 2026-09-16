"""MowgliNext select entity — pick a recorded area to start mowing now.

Same "select an option, it fires immediately" pattern several vacuum
integrations use for "clean this room now" -- there is no persistent
"selected area" state on the mower itself, this is a momentary control
that always resolves against the freshest <prefix>/areas list.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextAreaSelect(hub)])


class MowglinextAreaSelect(MowglinextEntity, SelectEntity):
    """Publishes <prefix>/start_area for the area picked, by re-resolving
    its index from the CURRENT <prefix>/areas payload at selection time --
    never a value cached from when the option list was built. Recorded
    areas have no stable id yet (mowglinext#637); an unrelated area add/
    edit/delete elsewhere can silently reassign every index, so caching one
    across a session risks starting the wrong area. See docs/MQTT_CONTROL.md
    in the main repo for the full interim-contract caveat.
    """

    _attr_name = "Start area"
    _attr_icon = "mdi:map-marker-radius"
    _topic_key = "areas"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_start_area"
        # Momentary control, not a persisted mower setting: this only ever
        # reflects the last area WE asked to start, purely so the UI shows
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
        areas = self.hub.data.get("areas") or []
        match = next((area for area in areas if area.get("name") == option), None)
        if match is None:
            # The list moved between the user opening the picker and
            # choosing an option (or it's simply gone stale) -- refuse
            # rather than silently starting nothing or, worse, a stale
            # index that now points at a different area.
            raise HomeAssistantError(
                f"'{option}' is not in the current recorded-area list; it may have been "
                "renamed or removed. Try again after the list refreshes."
            )
        await self.hub.async_start_area(match["index"])
        self._current_option = option
        self.async_write_ha_state()
