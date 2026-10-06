"""MowgliNext lawn_mower entity — the primary "is it mowing?" entity.

HighLevelStatus has more states than HA's LawnMowerActivity can express (no
native "recording" or "manual mowing" activity, see docs/MQTT_CONTROL.md in
the main repo for the full state table) — the raw state_name/sub_state_name
survive as extra_state_attributes and on the diagnostic "State" sensor
(sensor.py) for anyone who needs the exact substate.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform

from . import schedules
from .const import (
    COMMAND_HOME,
    COMMAND_START,
    COMMAND_STOP,
    DOMAIN,
    STATE_AUTONOMOUS,
    STATE_IDLE,
    STATE_MANUAL_MOWING,
    STATE_RECORDING,
)
from .coordinator import MowglinextHub
from .entity import MowglinextEntity

_DAY_OF_WEEK = vol.All(vol.Coerce(int), vol.Range(min=0, max=6))

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_DELETE_SCHEDULE = "delete_schedule"
SERVICE_START_AREA = "start_area"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextLawnMower(hub)])

    # Entity services (not hass.services.async_register): HA resolves the
    # target entity from the service call for us, same as the built-in
    # start_mowing/pause/dock above -- no manual device_id/entry lookup needed.
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE,
        {
            vol.Required("time"): cv.time,
            vol.Optional("area"): vol.Any(None, cv.string),
            vol.Required("days_of_week"): vol.All(cv.ensure_list, [_DAY_OF_WEEK]),
            vol.Optional("enabled", default=True): cv.boolean,
            vol.Optional("id"): cv.string,
        },
        "async_set_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_START_AREA,
        {vol.Required("area"): cv.string},
        "async_start_area",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_SCHEDULE,
        {vol.Required("id"): cv.string},
        "async_delete_schedule",
    )


class MowglinextLawnMower(MowglinextEntity, LawnMowerEntity):
    """Maps <prefix>/high_level_status onto HA's lawn_mower domain."""

    _attr_name = None  # use the device name ("Mowgli") as the entity name
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )
    _topic_key = "high_level_status"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_lawn_mower"

    @property
    def activity(self) -> LawnMowerActivity | None:
        status = self.hub.data.get("high_level_status") or {}
        if not status:
            return None
        # Emergency overrides everything else, regardless of `state`.
        if status.get("emergency"):
            return LawnMowerActivity.ERROR
        state = status.get("state")
        if state == STATE_IDLE:
            return (
                LawnMowerActivity.DOCKED
                if status.get("is_charging")
                else LawnMowerActivity.PAUSED
            )
        if state in (STATE_AUTONOMOUS, STATE_MANUAL_MOWING):
            # AUTONOMOUS also covers undocking/transit/returning-to-dock —
            # HA has no distinct "returning" activity for a mower.
            return LawnMowerActivity.MOWING
        if state == STATE_RECORDING:
            # No native "recording" activity; PAUSED is the closest
            # non-misleading choice (it is, in fact, not mowing).
            return LawnMowerActivity.PAUSED
        # STATE_NULL, or an absent/unknown value.
        return LawnMowerActivity.ERROR

    @property
    def extra_state_attributes(self) -> dict:
        status = self.hub.data.get("high_level_status") or {}
        return {
            "state_name": status.get("state_name"),
            "sub_state_name": status.get("sub_state_name"),
            "current_area": status.get("current_area"),
            "coverage_percent": status.get("coverage_percent"),
        }

    async def async_start_mowing(self) -> None:
        await self.hub.async_publish_command(COMMAND_START)

    async def async_pause(self) -> None:
        # COMMAND_STOP is the true pause-in-place (holds position, blade
        # off); COMMAND_HOME docks instead — see async_dock below.
        await self.hub.async_publish_command(COMMAND_STOP)

    async def async_dock(self) -> None:
        await self.hub.async_publish_command(COMMAND_HOME)

    async def async_set_schedule(
        self,
        time: object,
        days_of_week: list[int],
        area: str | None = None,
        enabled: bool = True,
        id: str | None = None,  # noqa: A002 - matches the service field name
    ) -> None:
        """mowglinext.set_schedule: create (no id, or an unknown one) or
        update (an existing id) a mowing schedule -- see the "Schedules"
        sensor for the current list and the ids it assigns on create.

        `area` is the area's NAME (resolved to its stable id from the freshest
        <prefix>/areas); omitted, empty or "all" mows every area."""
        try:
            area_id, area_name = schedules.resolve_area(area, self.hub.data.get("areas") or [])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        candidate = {
            "id": id,
            "time": time.strftime("%H:%M"),
            "daysOfWeek": days_of_week,
            "enabled": enabled,
        }
        if other := schedules.find_overlap(candidate, self.hub.schedules):
            raise ServiceValidationError(schedules.overlap_message(other))
        await self.hub.async_set_schedule(
            area_id=area_id,
            area_name=area_name,
            time=candidate["time"],
            days_of_week=days_of_week,
            enabled=enabled,
            schedule_id=id,
        )

    async def async_start_area(self, area: str) -> None:
        """mowglinext.start_area: start mowing one recorded area now, by NAME.

        Resolved to the area's index from the freshest <prefix>/areas at call
        time -- the index is positional and can change with any edit to the
        area list, so it is never taken from the caller or cached."""
        areas = self.hub.data.get("areas") or []
        name = area.strip()
        match = next((a for a in areas if a.get("name") == name), None)
        if match is None or not isinstance(match.get("index"), int):
            known = ", ".join(f"'{a['name']}'" for a in areas if a.get("name"))
            raise ServiceValidationError(
                f"Unknown area '{name}' (known areas: {known or 'none received yet'})"
            )
        await self.hub.async_start_area(match["index"])

    async def async_delete_schedule(self, id: str) -> None:  # noqa: A002
        """mowglinext.delete_schedule."""
        await self.hub.async_delete_schedule(id)
