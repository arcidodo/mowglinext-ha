"""MowgliNext calendar entity — the mowing schedules, as calendar events.

The "Schedules" sensor (sensor.py) already carries the raw list for automations
and templates; this renders the same data as an actual Home Assistant calendar
(Settings -> ... -> the Calendar dashboard, and any calendar card), which is a
much more readable way to see "when does it mow" than a sensor attribute.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import schedules
from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity

# The schedule itself has no end time (a mow ends whenever the area finishes,
# not on a clock) -- this is purely a nominal block so the event is visible on
# a calendar grid, not a claim about how long mowing actually takes.
_EVENT_DURATION = timedelta(hours=1)

# How far ahead `event` (the entity's own state: "the next occurrence") looks
# before giving up and reporting none scheduled.
_UPCOMING_WINDOW = timedelta(days=14)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextSchedulesCalendar(hub)])


class MowglinextSchedulesCalendar(MowglinextEntity, CalendarEntity):
    """One event per (schedule, occurrence) in the requested window -- there is
    no RRULE expansion to rely on here, since each schedule is a simple
    "these weekdays, this time" rule with no exceptions, so a straightforward
    day-by-day scan is both simpler and easier to get right than building an
    RFC5545 rule for it.
    """

    _attr_name = "Mowing schedule"
    _attr_icon = "mdi:calendar-clock"
    _topic_key = "schedules"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_schedules_calendar"

    def _schedules(self) -> list[dict]:
        return (self.hub.data.get("schedules") or {}).get("schedules") or []

    def _occurrences(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Every (schedule, day) pair whose event overlaps [start, end).

        A schedule's "time" is the mower's own local wall-clock time (it is
        set on the mower with no timezone attached, the same way a cron
        schedule works) -- always build it in Home Assistant's configured
        local timezone, never in whatever timezone the caller's start/end
        happen to carry. `start`/`end` themselves are still compared as-is:
        aware-datetime comparison normalises across timezones correctly, and
        using them to bound which LOCAL calendar dates to scan (rather than
        assuming they are already local-midnight-aligned) keeps this correct
        for a caller in any timezone.
        """
        local_start = dt_util.as_local(start)
        local_end = dt_util.as_local(end)
        events: list[CalendarEvent] = []
        for sched in self._schedules():
            if not sched.get("enabled"):
                continue
            # The area's name, or "Mowing (all areas)" for a schedule that mows them all.
            summary = (
                schedules.area_label(sched, self.hub.data.get("areas") or [])
                or "Mowing (all areas)"
            )
            time_of_day = _parse_time(sched.get("time"))
            days = _parse_days(sched.get("daysOfWeek"))
            if time_of_day is None or not days:
                continue
            day = local_start.date()
            last_day = local_end.date()
            while day <= last_day:
                # daysOfWeek: 0=Sunday..6=Saturday; date.weekday(): 0=Monday..6=Sunday.
                if (day.weekday() + 1) % 7 in days:
                    event_start = datetime.combine(
                        day, time_of_day, tzinfo=dt_util.DEFAULT_TIME_ZONE
                    )
                    event_end = event_start + _EVENT_DURATION
                    if event_end > start and event_start < end:
                        events.append(
                            CalendarEvent(
                                start=event_start,
                                end=event_end,
                                summary=summary,
                                description=f"MowgliNext schedule {sched.get('id')}",
                                uid=f"{sched.get('id')}-{day.isoformat()}",
                            )
                        )
                day += timedelta(days=1)
        events.sort(key=lambda e: e.start)
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = self._occurrences(now, now + _UPCOMING_WINDOW)
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._occurrences(start_date, end_date)


def _parse_time(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def _parse_days(value: object) -> set[int]:
    if not isinstance(value, list):
        return set()
    return {d for d in value if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6}
