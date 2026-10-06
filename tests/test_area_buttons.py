"""Tests for the per-area "Mow <area>" buttons and the mowglinext.start_area service."""
import json

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

_AREAS = [
    {"index": 0, "name": "Voor", "id": 11},
    {"index": 2, "name": "Achter", "id": 7},
]


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


async def _publish_areas(hass: HomeAssistant, areas: list[dict]) -> None:
    async_fire_mqtt_message(hass, "mowgli/areas", json.dumps(areas))
    await hass.async_block_till_done()


def _area_buttons(hass: HomeAssistant) -> list[str]:
    return sorted(
        s.entity_id for s in hass.states.async_all("button") if "area_id" in s.attributes
    )


def _start_area_payloads(mqtt_mock) -> list[str]:
    return [
        call.args[1]
        for call in mqtt_mock.async_publish.mock_calls
        if "mowgli/start_area" in call.args
    ]


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def test_one_button_per_area(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    assert _area_buttons(hass) == ["button.mowgli_mow_achter", "button.mowgli_mow_voor"]
    state = hass.states.get("button.mowgli_mow_achter")
    assert state.name == "Mowgli Mow Achter"
    assert state.attributes["area_id"] == 7
    assert state.attributes["area_name"] == "Achter"


async def test_press_starts_the_area_at_its_current_index(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    await _press(hass, "button.mowgli_mow_achter")
    assert _start_area_payloads(mqtt_mock)[-1] == "2"

    # The area list was rebuilt and Achter moved to index 0: the same button
    # follows it by its stable id, and keeps its entity id.
    await _publish_areas(
        hass, [{"index": 0, "name": "Achter", "id": 7}, {"index": 1, "name": "Voor", "id": 11}]
    )
    await _press(hass, "button.mowgli_mow_achter")
    assert _start_area_payloads(mqtt_mock)[-1] == "0"


async def test_buttons_follow_areas_being_added_renamed_and_removed(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    # Renamed: same entity, new name.
    await _publish_areas(
        hass, [{"index": 0, "name": "Voortuin", "id": 11}, {"index": 2, "name": "Achter", "id": 7}]
    )
    assert hass.states.get("button.mowgli_mow_voor").name == "Mowgli Mow Voortuin"

    # Removed and added.
    await _publish_areas(hass, [{"index": 0, "name": "Zij", "id": 12}])
    assert _area_buttons(hass) == ["button.mowgli_mow_zij"]
    assert er.async_get(hass).async_get("button.mowgli_mow_achter") is None


async def test_no_buttons_without_stable_ids(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    # Older mower software: no id, or 0 (not assigned yet).
    await _publish_areas(
        hass, [{"index": 0, "name": "Voor"}, {"index": 1, "name": "Achter", "id": 0}]
    )

    assert _area_buttons(hass) == []


async def test_buttons_survive_a_restart_until_the_area_list_arrives(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    # Nothing received yet after the reload: the buttons are not pruned.
    assert registry.async_get("button.mowgli_mow_achter") is not None

    await _make_available(hass)
    await _publish_areas(hass, [_AREAS[1]])
    assert _area_buttons(hass) == ["button.mowgli_mow_achter"]
    assert registry.async_get("button.mowgli_mow_voor") is None


async def test_pressing_a_vanished_area_is_refused(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    button = next(
        e
        for e in hass.data["entity_components"]["button"].entities
        if e.entity_id == "button.mowgli_mow_achter"
    )
    button.hub.data["areas"] = []  # gone, before the entity is removed
    with pytest.raises(HomeAssistantError, match="Achter"):
        await button.async_press()
    assert _start_area_payloads(mqtt_mock) == []


async def test_button_unavailable_while_the_mower_is_offline(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    async_fire_mqtt_message(hass, "mowgli/available", "offline")
    await hass.async_block_till_done()
    assert hass.states.get("button.mowgli_mow_achter").state == "unavailable"


async def test_start_area_service_resolves_the_name(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _publish_areas(hass, _AREAS)

    await hass.services.async_call(
        DOMAIN, "start_area", {"entity_id": "lawn_mower.mowgli", "area": "Achter"}, blocking=True
    )
    assert _start_area_payloads(mqtt_mock)[-1] == "2"

    with pytest.raises(ServiceValidationError, match="'Voor', 'Achter'"):
        await hass.services.async_call(
            DOMAIN, "start_area", {"entity_id": "lawn_mower.mowgli", "area": "Zij"}, blocking=True
        )
    assert len(_start_area_payloads(mqtt_mock)) == 1
