"""Tests for the "Start selected area" button (button.py).

The area-picker select (select.py) only arms hub.pending_area_name; this
button is what actually resolves that name to an index -- against the
freshest <prefix>/areas payload, at press time -- and publishes
<prefix>/start_area.
"""
import json

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN


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


async def test_press_without_armed_area_raises(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.mowgli_start_selected_area"},
            blocking=True,
        )


async def test_press_publishes_resolved_index_for_armed_area(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/areas",
        json.dumps([{"index": 0, "name": "Front Lawn"}, {"index": 2, "name": "Back Garden"}]),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.mowgli_area_to_start", "option": "Back Garden"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.mowgli_start_selected_area"},
        blocking=True,
    )

    # "Back Garden" is index 2, not its position (1) in the list -- indices
    # are not guaranteed contiguous/positional (docs/MQTT_CONTROL.md).
    published = [
        call for call in mqtt_mock.async_publish.mock_calls if "mowgli/start_area" in call.args
    ]
    assert published, mqtt_mock.async_publish.mock_calls
    assert "2" in published[-1].args


async def test_press_reresolves_at_press_time_not_arm_time(
    hass: HomeAssistant, mqtt_mock
) -> None:
    # Arm while "Back Garden" is index 2, then let the list get rebuilt
    # (reassigning every index, mowglinext#637) BEFORE pressing. The button
    # must use the index current at press time, not one cached from arming.
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass,
        "mowgli/areas",
        json.dumps([{"index": 0, "name": "Front Lawn"}, {"index": 2, "name": "Back Garden"}]),
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.mowgli_area_to_start", "option": "Back Garden"},
        blocking=True,
    )

    async_fire_mqtt_message(
        hass,
        "mowgli/areas",
        json.dumps([{"index": 0, "name": "Back Garden"}, {"index": 1, "name": "Front Lawn"}]),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.mowgli_start_selected_area"},
        blocking=True,
    )

    published = [
        call for call in mqtt_mock.async_publish.mock_calls if "mowgli/start_area" in call.args
    ]
    assert published, mqtt_mock.async_publish.mock_calls
    assert "0" in published[-1].args


async def test_press_with_armed_area_no_longer_in_list_raises(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass, "mowgli/areas", json.dumps([{"index": 0, "name": "Front Lawn"}])
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.mowgli_area_to_start", "option": "Front Lawn"},
        blocking=True,
    )

    async_fire_mqtt_message(hass, "mowgli/areas", json.dumps([]))
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.mowgli_start_selected_area"},
            blocking=True,
        )
