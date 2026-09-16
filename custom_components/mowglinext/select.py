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
