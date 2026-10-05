"""Tests for the schedules sensor and the set_schedule/delete_schedule
services (lawn_mower.py's entity services, coordinator.py's publish helpers).
"""
import json

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

SENSOR = "sensor.mowgli_schedules"
ENTITY = "lawn_mower.mowgli"

_ONE_SCHEDULE = {
    "schedules": [
        {
            "id": "1",
            "area": 0,
            "time": "06:00",
            "daysOfWeek": [1, 2, 3, 4, 5],
            "enabled": True,
            "createdAt": "2026-09-20T08:00:00Z",
        },
        {
            "id": "2",
            "area": 1,
            "time": "18:00",
            "daysOfWeek": [6],
            "enabled": False,
            "createdAt": "2026-09-21T08:00:00Z",
        },
    ]
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


def _published(mqtt_mock, topic: str):
    return [call for call in mqtt_mock.async_publish.mock_calls if topic in call.args]


# ===========================================================================
# The schedules sensor
# ===========================================================================


async def test_schedules_sensor_state_is_the_enabled_count(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(hass, "mowgli/schedules", json.dumps(_ONE_SCHEDULE))
    await hass.async_block_till_done()

    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "1"  # one of the two is enabled
    assert state.attributes["total"] == 2
    assert state.attributes["schedules"] == _ONE_SCHEDULE["schedules"]


async def test_schedules_sensor_defaults_to_zero_with_nothing_retained(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "0"
    assert state.attributes["schedules"] == []


async def test_schedules_sensor_is_available_while_the_mower_is_offline(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    async_fire_mqtt_message(hass, "mowgli/available", "offline")
    await hass.async_block_till_done()

    # Same pattern as every other sensor: unavailable when the mower itself is.
    assert hass.states.get(SENSOR).state == "unavailable"


# ===========================================================================
# mowglinext.set_schedule
# ===========================================================================

_AREAS = [{"index": 0, "name": "Voor", "id": 11}, {"index": 2, "name": "Achter", "id": 7}]


async def test_set_schedule_without_an_area_mows_all_areas(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "entity_id": ENTITY,
            "time": "06:00:00",
            "days_of_week": [1, 2, 3, 4, 5],
            "enabled": True,
        },
        blocking=True,
    )

    published = _published(mqtt_mock, "mowgli/schedules/set")
    assert published, mqtt_mock.async_publish.mock_calls
    payload = json.loads(published[-1].args[1])
    assert payload == {
        "areaId": 0,
        "time": "06:00",
        "daysOfWeek": [1, 2, 3, 4, 5],
        "enabled": True,
    }
    assert "id" not in payload, "a create must not send an id field at all"


async def test_set_schedule_resolves_an_area_name_to_its_stable_id(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(hass, "mowgli/areas", json.dumps(_AREAS))
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {"entity_id": ENTITY, "area": "Achter", "time": "17:30:00", "days_of_week": [6]},
        blocking=True,
    )

    payload = json.loads(_published(mqtt_mock, "mowgli/schedules/set")[-1].args[1])
    # The stable id (7), not the positional index (2).
    assert payload["areaId"] == 7
    assert payload["areaName"] == "Achter"


async def test_set_schedule_rejects_an_unknown_area(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(hass, "mowgli/areas", json.dumps(_AREAS))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="Unknown area 'Zij'"):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {"entity_id": ENTITY, "area": "Zij", "time": "06:00:00", "days_of_week": [1]},
            blocking=True,
        )
    assert not _published(mqtt_mock, "mowgli/schedules/set")


async def test_set_schedule_rejects_an_overlap_like_the_mower_would(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(hass, "mowgli/schedules", json.dumps(_ONE_SCHEDULE))
    await hass.async_block_till_done()

    # Schedule "1" is enabled Mon-Fri 06:00; Monday 06:30 is 30 min from it.
    with pytest.raises(ServiceValidationError, match="06:00"):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {"entity_id": ENTITY, "time": "06:30:00", "days_of_week": [1]},
            blocking=True,
        )
    assert not _published(mqtt_mock, "mowgli/schedules/set")

    # Disabled, or updating schedule "1" itself, is never an overlap.
    for extra in ({"enabled": False}, {"id": "1"}):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {"entity_id": ENTITY, "time": "06:30:00", "days_of_week": [1], **extra},
            blocking=True,
        )
    assert len(_published(mqtt_mock, "mowgli/schedules/set")) == 2


async def test_set_schedule_with_an_id_updates_instead_of_creating(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "entity_id": ENTITY,
            "time": "06:00:00",
            "days_of_week": [1],
            "id": "42",
        },
        blocking=True,
    )

    payload = json.loads(_published(mqtt_mock, "mowgli/schedules/set")[-1].args[1])
    assert payload["id"] == "42"


async def test_set_schedule_enabled_defaults_to_true(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {"entity_id": ENTITY, "time": "06:00:00", "days_of_week": [1]},
        blocking=True,
    )

    payload = json.loads(_published(mqtt_mock, "mowgli/schedules/set")[-1].args[1])
    assert payload["enabled"] is True


# ===========================================================================
# mowglinext.delete_schedule
# ===========================================================================


async def test_delete_schedule_publishes_the_bare_id(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    await hass.services.async_call(
        DOMAIN,
        "delete_schedule",
        {"entity_id": ENTITY, "id": "1758901234567890000"},
        blocking=True,
    )

    published = _published(mqtt_mock, "mowgli/schedules/delete")
    assert published, mqtt_mock.async_publish.mock_calls
    assert "1758901234567890000" in published[-1].args
