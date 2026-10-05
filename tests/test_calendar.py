"""Tests for the mowing-schedule calendar entity (calendar.py).

The "Schedules" sensor (test_schedules.py) already covers the raw data; these
tests focus on the day-by-day occurrence expansion this entity does on top of
it -- weekday matching, disabled schedules being excluded, and the event title
naming the schedule's area (by its stable areaId) or "all areas".
"""
import json

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

ENTITY = "calendar.mowgli_mowing_schedule"


async def _setup_entry(hass: HomeAssistant, mqtt_mock) -> ConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _make_available(hass: HomeAssistant) -> None:
    async_fire_mqtt_message(hass, "mowgli/available", "online")
    async_fire_mqtt_message(
        hass, "mowgli/high_level_status", json.dumps({"state": 1, "state_name": "IDLE"})
    )
    await hass.async_block_till_done()


def _entity(hass: HomeAssistant):
    for entity in hass.data["entity_components"]["calendar"].entities:
        if entity.entity_id == ENTITY:
            return entity
    raise AssertionError(f"{ENTITY} not found")


async def test_calendar_lists_every_matching_weekday_in_the_window(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/schedules",
        json.dumps(
            {
                "schedules": [
                    {
                        "id": "1",
                        "area": 0,
                        "time": "06:00",
                        "daysOfWeek": [1, 3],  # Monday, Wednesday
                        "enabled": True,
                    }
                ]
            }
        ),
    )
    await hass.async_block_till_done()

    # A Monday through the following Monday, exclusive: Mon, Wed match; the
    # second Monday's 06:00 falls exactly on end's midnight boundary and is
    # correctly excluded (a half-open [start, end) window).
    start = dt_util.as_utc(dt_util.parse_datetime("2026-09-28T00:00:00"))  # a Monday
    end = start + __import__("datetime").timedelta(days=7)

    events = await _entity(hass).async_get_events(hass, start, end)

    assert [e.start.strftime("%a %H:%M") for e in events] == ["Mon 06:00", "Wed 06:00"]


async def test_calendar_excludes_disabled_schedules(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/schedules",
        json.dumps(
            {
                "schedules": [
                    {"id": "1", "area": 0, "time": "06:00", "daysOfWeek": [1], "enabled": False}
                ]
            }
        ),
    )
    await hass.async_block_till_done()

    start = dt_util.as_utc(dt_util.parse_datetime("2026-09-28T00:00:00"))
    end = start + __import__("datetime").timedelta(days=7)
    events = await _entity(hass).async_get_events(hass, start, end)

    assert events == []


async def _summaries(hass: HomeAssistant, schedule: dict) -> list[str]:
    async_fire_mqtt_message(hass, "mowgli/schedules", json.dumps({"schedules": [schedule]}))
    await hass.async_block_till_done()
    start = dt_util.as_utc(dt_util.parse_datetime("2026-09-28T00:00:00"))
    end = start + __import__("datetime").timedelta(days=1)
    return [e.summary for e in await _entity(hass).async_get_events(hass, start, end)]


async def test_calendar_summary_names_the_scheduled_area(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/areas",
        json.dumps([{"index": 0, "name": "Front", "id": 11}, {"index": 2, "name": "Back Garden", "id": 7}]),
    )
    base = {"id": "1", "time": "06:00", "daysOfWeek": [1], "enabled": True}

    # Resolved by the stable id, not by the index.
    assert await _summaries(hass, {**base, "areaId": 7, "areaName": "Old name"}) == ["Back Garden"]
    # An area that no longer exists keeps the schedule's own snapshot.
    assert await _summaries(hass, {**base, "areaId": 99, "areaName": "Gone"}) == ["Gone"]


async def test_calendar_summary_for_all_areas(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    base = {"id": "1", "time": "06:00", "daysOfWeek": [1], "enabled": True}

    assert await _summaries(hass, {**base, "areaId": 0}) == ["Mowing (all areas)"]
    # Older mower software: only an `area` index its scheduler ignored -> all areas.
    assert await _summaries(hass, {**base, "area": 0}) == ["Mowing (all areas)"]


async def test_calendar_event_state_is_the_next_upcoming_occurrence(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/schedules",
        json.dumps(
            {
                "schedules": [
                    {"id": "1", "area": 0, "time": "23:59", "daysOfWeek": [0, 1, 2, 3, 4, 5, 6], "enabled": True}
                ]
            }
        ),
    )
    await hass.async_block_till_done()

    entity = _entity(hass)
    event = entity.event
    assert isinstance(event, CalendarEvent)
    assert event.start >= dt_util.now()


async def test_calendar_reports_no_event_with_nothing_scheduled(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    assert _entity(hass).event is None
