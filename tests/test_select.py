"""Tests for the area-picker select entity (<prefix>/areas).

Selecting only arms hub.pending_area_name -- it does not publish anything.
See test_button.py for the companion "Start selected area" button that
actually resolves and publishes <prefix>/start_area.
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


async def test_options_track_the_areas_topic(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)

    state = hass.states.get("select.mowgli_area_to_start")
    assert state is not None
    assert state.attributes["options"] == []

    async_fire_mqtt_message(
        hass,
        "mowgli/areas",
        json.dumps([{"index": 0, "name": "Front Lawn"}, {"index": 2, "name": "Back Garden"}]),
    )
    await hass.async_block_till_done()

    state = hass.states.get("select.mowgli_area_to_start")
    assert state.attributes["options"] == ["Front Lawn", "Back Garden"]


async def test_select_option_arms_hub_without_publishing(
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

    # Arming alone must not publish anything -- that's the whole point of
    # splitting "pick" from "start" into a separate button.
    published = [
        call for call in mqtt_mock.async_publish.mock_calls if "mowgli/start_area" in call.args
    ]
    assert not published, mqtt_mock.async_publish.mock_calls

    state = hass.states.get("select.mowgli_area_to_start")
    assert state.state == "Back Garden"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hub = hass.data[DOMAIN][entry.entry_id]
    assert hub.pending_area_name == "Back Garden"


async def test_select_stale_option_raises(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    async_fire_mqtt_message(
        hass, "mowgli/areas", json.dumps([{"index": 0, "name": "Front Lawn"}])
    )
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.mowgli_area_to_start", "option": "Renamed Or Gone"},
            blocking=True,
        )
