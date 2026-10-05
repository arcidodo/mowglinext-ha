"""Tests for the per-schedule switches (switch.py's MowglinextScheduleSwitch):
one switch per <prefix>/schedules entry, created and removed as the list changes,
toggling `enabled` back over <prefix>/schedules/set.
"""
import json

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

_WEEKDAYS = {
    "id": "1790146100949054310",
    "area": 0,
    "time": "17:30",
    "daysOfWeek": [1, 2, 3, 4, 5, 0, 6],
    "enabled": True,
    "createdAt": "2026-09-23T06:48:20.949058255Z",
}
_SATURDAY = {
    "id": "1790146100949054311",
    "area": 1,
    "time": "09:00",
    "daysOfWeek": [6],
    "enabled": False,
    "lastRun": "2026-09-26T09:00:00Z",
}


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


async def _publish_schedules(hass: HomeAssistant, *schedules: dict) -> None:
    async_fire_mqtt_message(
        hass,
        "mowgli/schedules",
        json.dumps({"schedules": list(schedules), "total": len(schedules)}),
    )
    await hass.async_block_till_done()


def _schedule_switches(hass: HomeAssistant) -> list[str]:
    return sorted(
        s.entity_id
        for s in hass.states.async_all("switch")
        if s.attributes.get("schedule_control")
    )


def _published(mqtt_mock, topic: str):
    return [call for call in mqtt_mock.async_publish.mock_calls if topic in call.args]


async def test_one_switch_per_schedule_reflecting_enabled(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass, "mowgli/areas", json.dumps([{"index": 0, "name": "Achter"}])
    )
    await _publish_schedules(hass, _WEEKDAYS, _SATURDAY)

    assert _schedule_switches(hass) == [
        "switch.mowgli_schedule_1",
        "switch.mowgli_schedule_2",
    ]
    first = hass.states.get("switch.mowgli_schedule_1")
    assert first.state == "on"
    # Named by time only: the mower ignores a schedule's "area" and mows every area,
    # so even a known area name ("Achter" for area 0) must not appear.
    assert first.name == "Mowgli Schedule 17:30"
    second = hass.states.get("switch.mowgli_schedule_2")
    assert second.state == "off"
    assert second.name == "Mowgli Schedule 09:00"


async def test_attributes_feed_the_lawn_mower_card_schedules_panel(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass, "mowgli/areas", json.dumps([{"index": 0, "name": "Achter"}])
    )
    await _publish_schedules(hass, _WEEKDAYS, _SATURDAY)

    attrs = hass.states.get("switch.mowgli_schedule_1").attributes
    assert attrs["schedule_control"] is True
    assert attrs["name"] == "Schedule 17:30"
    assert "map_label" not in attrs
    assert attrs["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert attrs["start_times"] == ["17:30"]
    assert attrs["schedule_id"] == "1790146100949054310"

    attrs = hass.states.get("switch.mowgli_schedule_2").attributes
    assert attrs["weekdays"] == ["Sat"]
    assert attrs["last_run"] == "2026-09-26T09:00:00Z"


@pytest.mark.parametrize(("service", "enabled"), [("turn_off", False), ("turn_on", True)])
async def test_toggling_publishes_the_full_schedule_with_enabled_changed(
    hass: HomeAssistant, mqtt_mock, service: str, enabled: bool
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS)

    await hass.services.async_call(
        "switch", service, {"entity_id": "switch.mowgli_schedule_1"}, blocking=True
    )

    published = _published(mqtt_mock, "mowgli/schedules/set")
    assert published, mqtt_mock.async_publish.mock_calls
    assert json.loads(published[-1].args[1]) == {
        "id": "1790146100949054310",
        "area": 0,
        "time": "17:30",
        "daysOfWeek": [1, 2, 3, 4, 5, 0, 6],
        "enabled": enabled,
    }


async def test_state_follows_the_mower_not_the_command(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.mowgli_schedule_1"}, blocking=True
    )
    assert hass.states.get("switch.mowgli_schedule_1").state == "on"

    await _publish_schedules(hass, {**_WEEKDAYS, "enabled": False})
    assert hass.states.get("switch.mowgli_schedule_1").state == "off"


async def test_switches_follow_schedules_being_added_and_removed(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS, _SATURDAY)

    await _publish_schedules(hass, _SATURDAY)
    assert _schedule_switches(hass) == ["switch.mowgli_schedule_2"]
    registry = er.async_get(hass)
    assert registry.async_get("switch.mowgli_schedule_1") is None

    # A new schedule takes the lowest free number; the existing one keeps its id.
    third = {**_WEEKDAYS, "id": "1790146100949054312", "time": "07:00"}
    await _publish_schedules(hass, _SATURDAY, third)
    assert _schedule_switches(hass) == [
        "switch.mowgli_schedule_1",
        "switch.mowgli_schedule_2",
    ]
    assert hass.states.get("switch.mowgli_schedule_1").attributes["start_times"] == ["07:00"]
    assert hass.states.get("switch.mowgli_schedule_2").attributes["weekdays"] == ["Sat"]

    await _publish_schedules(hass)
    assert _schedule_switches(hass) == []


async def test_no_schedule_switches_before_the_list_arrives(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    assert _schedule_switches(hass) == []

    # A schedule switch registered in an earlier run is kept until the mower's list
    # arrives, not pruned on start-up just because nothing was received yet.
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{entry.entry_id}_schedule_{_WEEKDAYS['id']}",
        config_entry=entry,
        suggested_object_id="mowgli_schedule_1",
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("switch.mowgli_schedule_1") is not None

    # Deleted while Home Assistant was not running: removed once the list arrives.
    await _make_available(hass)
    await _publish_schedules(hass, _SATURDAY)
    assert (
        registry.async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_schedule_{_WEEKDAYS['id']}"
        )
        is None
    )
    # ...which frees its number for the schedule that does exist.
    assert _schedule_switches(hass) == ["switch.mowgli_schedule_1"]
    assert hass.states.get("switch.mowgli_schedule_1").attributes["weekdays"] == ["Sat"]


async def test_restart_keeps_the_entity_id(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS, _SATURDAY)
    await _publish_schedules(hass, _SATURDAY)  # schedule_1 freed, schedule_2 stays

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _make_available(hass)
    await _publish_schedules(hass, _SATURDAY)
    assert _schedule_switches(hass) == ["switch.mowgli_schedule_2"]


async def test_switch_unavailable_while_the_mower_is_offline(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS)

    async_fire_mqtt_message(hass, "mowgli/available", "offline")
    await hass.async_block_till_done()
    assert hass.states.get("switch.mowgli_schedule_1").state == "unavailable"


async def test_toggling_a_vanished_schedule_raises(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_schedules(hass, _WEEKDAYS)

    switch = next(
        e
        for e in hass.data["entity_components"]["switch"].entities
        if e.entity_id == "switch.mowgli_schedule_1"
    )
    hub = switch.hub
    hub.data["schedules"] = {"schedules": []}  # gone, before the entity is removed
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_off()
