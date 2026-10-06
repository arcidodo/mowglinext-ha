"""MowgliNext buttons.

Besides "Reset emergency" and "Start selected area", one "Mow <area>" button per
recorded area (<prefix>/areas): one press starts mowing that area.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import COMMAND_RESET_EMERGENCY, DOMAIN
from .coordinator import MowglinextHub
from .dynamic import async_track_list_entities, free_entity_id, mower_object_id
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextResetEmergencyButton(hub), MowglinextStartAreaButton(hub)])

    def _area_ids() -> list[str] | None:
        if not hub.has_received("areas"):
            return None
        # Keyed by the area's stable id (0 / absent: not assigned yet, or older mower
        # software that publishes none) -- an index would hand a button to whichever
        # area moves into that slot after an edit.
        return [str(a["id"]) for a in hub.data.get("areas") or [] if _valid_id(a.get("id"))]

    def _create(area_ids: list[str]) -> list[MowglinextMowAreaButton]:
        registry = er.async_get(hass)
        prefix = mower_object_id(hass, hub)
        taken: set[str] = set()
        buttons = []
        for area_id in area_ids:
            button = MowglinextMowAreaButton(hub, int(area_id))
            existing = registry.async_get_entity_id(Platform.BUTTON, DOMAIN, button.unique_id)
            button.entity_id = existing or free_entity_id(
                hass, f"{Platform.BUTTON}.{prefix}_mow_{slugify(button.area_name)}", taken
            )
            taken.add(button.entity_id)
            buttons.append(button)
        return buttons

    async_track_list_entities(
        hass,
        hub,
        async_add_entities,
        platform=Platform.BUTTON,
        topic="areas",
        unique_id_prefix="mow_area_",
        current_keys=_area_ids,
        create=_create,
    )


def _valid_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class MowglinextResetEmergencyButton(MowglinextEntity, ButtonEntity):
    """Publishes COMMAND_RESET_EMERGENCY (254).

    NOTE (docs/MQTT_CONTROL.md, main repo): as of this writing that command
    code is declared in HighLevelControl.srv but is NOT wired to any BT
    guard on the MQTT command channel — the real emergency-reset path is
    the separate /hardware_bridge/emergency_stop ROS service, which
    mqtt_bridge_node does not currently expose. This button is provided for
    forward-compatibility; confirm against your own mqtt_bridge_node build
    before relying on it to actually clear a latched emergency.
    """

    _attr_name = "Reset emergency"
    _attr_icon = "mdi:alert-remove"
    _attr_entity_category = EntityCategory.CONFIG
    _topic_key = "emergency"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_reset_emergency"

    async def async_press(self) -> None:
        await self.hub.async_publish_command(COMMAND_RESET_EMERGENCY)


class MowglinextStartAreaButton(MowglinextEntity, ButtonEntity):
    """Starts mowing whichever area is currently armed on the "Area to
    start" select entity (select.py).

    Re-resolves the armed name to an index from the CURRENT <prefix>/areas
    payload at press time -- never a value cached from when it was armed --
    because indices are not stable (mowglinext#637) and the area list can
    change during the gap between arming and pressing.
    """

    _attr_name = "Start selected area"
    _attr_icon = "mdi:play-circle-outline"
    _topic_key = "areas"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_start_selected_area"

    async def async_press(self) -> None:
        option = self.hub.pending_area_name
        if option is None:
            raise HomeAssistantError("No area armed — pick one in 'Area to start' first.")
        areas = self.hub.data.get("areas") or []
        match = next((area for area in areas if area.get("name") == option), None)
        if match is None:
            # The list moved between arming and pressing (or it's simply
            # gone stale) -- refuse rather than silently starting nothing
            # or, worse, a stale index that now points at a different area.
            raise HomeAssistantError(
                f"'{option}' is not in the current recorded-area list; it may have been "
                "renamed or removed. Pick it again after the list refreshes."
            )
        await self.hub.async_start_area(match["index"])


class MowglinextMowAreaButton(MowglinextEntity, ButtonEntity):
    """Starts mowing one recorded area, identified by its stable id.

    The press resolves that id to the area's CURRENT index from the freshest
    <prefix>/areas -- <prefix>/start_area only takes an index, and an edit to the
    area list can renumber every area -- and refuses if the area is gone.
    """

    _attr_icon = "mdi:play-circle-outline"
    _topic_key = "areas"

    def __init__(self, hub: MowglinextHub, area_id: int) -> None:
        super().__init__(hub)
        self.area_id = area_id
        self._attr_unique_id = f"{hub.device_id}_mow_area_{area_id}"
        # Last known name: keeps the label while the area is (briefly) missing.
        self._last_name = f"Area {area_id}"

    def _area(self) -> dict | None:
        for area in self.hub.data.get("areas") or []:
            if area.get("id") == self.area_id:
                if area.get("name"):
                    self._last_name = area["name"]
                return area
        return None

    @property
    def area_name(self) -> str:
        self._area()
        return self._last_name

    @property
    def name(self) -> str:
        return f"Mow {self.area_name}"

    @property
    def available(self) -> bool:
        return self.hub.available and self._area() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"area_id": self.area_id, "area_name": self.area_name}

    async def async_press(self) -> None:
        area = self._area()
        if area is None or not isinstance(area.get("index"), int):
            raise HomeAssistantError(
                f"'{self._last_name}' is no longer in the recorded-area list; it may have "
                "been removed."
            )
        await self.hub.async_start_area(area["index"])
