"""MowgliNext switch entities.

- "Map: active zone only" — show only the active zone on the map camera. A display
  preference (not a mower setting): with a large garden split into zones the
  whole-garden view makes the planned mowing paths a haze, so this zooms in on the zone
  being mowed and leaves the others, and their trail/path, out.
- One switch per mowing schedule (<prefix>/schedules), on = the schedule is enabled.
  Turning one off pauses that schedule without deleting it; the set of switches follows
  the schedule list as schedules are added or removed, from Home Assistant or from the
  mower's own Settings page.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import schedules
from .const import DOMAIN
from .coordinator import MowglinextHub
from .dynamic import async_track_list_entities, mower_object_id
from .entity import MowglinextEntity

# Indexed by daysOfWeek (0=Sunday..6=Saturday); the "weekdays" attribute lists them
# Monday-first, as a week reads on a calendar.
_WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextMapFocusSwitch(hub)])

    def _schedule_ids() -> list[str] | None:
        payload = hub.data.get("schedules") or {}
        if not isinstance(payload.get("schedules"), list):
            return None  # nothing received yet, or an older mower without the topic
        return [str(s["id"]) for s in payload["schedules"] if s.get("id") is not None]

    def _create(schedule_ids: list[str]) -> list[MowglinextScheduleSwitch]:
        registry = er.async_get(hass)
        prefix = mower_object_id(hass, hub)
        taken: set[str] = set()
        switches = []
        for schedule_id in schedule_ids:
            switch = MowglinextScheduleSwitch(hub, schedule_id)
            existing = registry.async_get_entity_id(Platform.SWITCH, DOMAIN, switch.unique_id)
            switch.entity_id = existing or _free_schedule_entity_id(hass, prefix, taken)
            taken.add(switch.entity_id)
            switches.append(switch)
        return switches

    async_track_list_entities(
        hass,
        hub,
        async_add_entities,
        platform=Platform.SWITCH,
        topic="schedules",
        unique_id_prefix="schedule_",
        current_keys=_schedule_ids,
        create=_create,
    )


def _free_schedule_entity_id(hass: HomeAssistant, prefix: str, taken: set[str]) -> str:
    """switch.<mower>_schedule_<n> with the lowest n not in use — the schedule's own id
    is a 19-digit timestamp, unreadable in an entity id."""
    registry = er.async_get(hass)
    n = 1
    while True:
        entity_id = f"{Platform.SWITCH}.{prefix}_schedule_{n}"
        if (
            entity_id not in taken
            and registry.async_get(entity_id) is None
            and hass.states.get(entity_id) is None
        ):
            return entity_id
        n += 1


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


class MowglinextScheduleSwitch(MowglinextEntity, SwitchEntity):
    """One mowing schedule: on = enabled. Not optimistic — the state follows the
    mower's own <prefix>/schedules, which it republishes after every change."""

    _attr_icon = "mdi:calendar-clock"
    _topic_key = "schedules"

    def __init__(self, hub: MowglinextHub, schedule_id: str) -> None:
        super().__init__(hub)
        self.schedule_id = schedule_id
        self._attr_unique_id = f"{hub.device_id}_schedule_{schedule_id}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The name shows the area's CURRENT name, so a rename re-renders it too.
        self.async_on_remove(self.hub.async_add_listener("areas", self.async_write_ha_state))

    def _schedule(self) -> dict | None:
        for sched in self.hub.schedules:
            if str(sched.get("id")) == self.schedule_id:
                return sched
        return None

    def _area_label(self, sched: dict) -> str:
        return (
            schedules.area_label(sched, self.hub.data.get("areas") or [])
            or schedules.ALL_AREAS_LABEL
        )

    @property
    def name(self) -> str:
        sched = self._schedule()
        if sched is None:
            return "Schedule"
        return " ".join(
            part for part in ("Schedule", self._area_label(sched), sched.get("time")) if part
        )

    @property
    def available(self) -> bool:
        return self.hub.available and self._schedule() is not None

    @property
    def is_on(self) -> bool | None:
        sched = self._schedule()
        return bool(sched.get("enabled")) if sched else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sched = self._schedule()
        if sched is None:
            return {"schedule_id": self.schedule_id}
        days = sched.get("daysOfWeek") or []
        valid_days = sorted(
            {d for d in days if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6},
            key=lambda d: (d + 6) % 7,
        )
        label = self._area_label(sched)
        attributes: dict[str, Any] = {
            # Read by the lawn-mower-card to list this switch in its Schedules panel.
            "schedule_control": True,
            "name": " ".join(part for part in (label, sched.get("time")) if part),
            "map_label": label,
            "weekdays": [_WEEKDAY_NAMES[d] for d in valid_days],
            "start_times": [sched["time"]] if sched.get("time") else [],
            "schedule_id": self.schedule_id,
            "area_id": schedules.area_id(sched),
            "time": sched.get("time"),
            "days_of_week": days,
        }
        for source, key in (
            ("lastRun", "last_run"),
            ("lastSkipReason", "last_skip_reason"),
            ("lastSkippedAt", "last_skipped_at"),
        ):
            if sched.get(source):
                attributes[key] = sched[source]
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        sched = self._schedule()
        if sched is None:
            raise HomeAssistantError(f"Schedule {self.schedule_id} no longer exists")
        payload = schedules.toggled(sched, enabled)
        # The mower drops an overlapping enable with only a log line; refuse it here
        # with the reason instead of a switch that silently stays off.
        if other := schedules.find_overlap(payload, self.hub.schedules):
            raise HomeAssistantError(schedules.overlap_message(other))
        await self.hub.async_publish_schedule(payload)
