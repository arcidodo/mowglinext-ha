"""Tests for the mower's own rain sensor and the "Paused for rain" binary sensors."""
import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN, RAIN_STATE_NAMES

SENSOR = "binary_sensor.mowgli_rain_sensor"
PAUSE = "binary_sensor.mowgli_paused_for_rain"


async def _setup(hass: HomeAssistant, mqtt_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, "mowgli/available", "online")
    async_fire_mqtt_message(
        hass, "mowgli/high_level_status", json.dumps({"state": 1, "state_name": "IDLE"})
    )
    await hass.async_block_till_done()


async def test_the_mowers_own_rain_sensor_follows_status(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup(hass, mqtt_mock)
    assert hass.states.get(SENSOR).state == "unknown"  # no <prefix>/status yet

    async_fire_mqtt_message(hass, "mowgli/status", json.dumps({"rain_detected": True}))
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "on"

    async_fire_mqtt_message(hass, "mowgli/status", json.dumps({"rain_detected": False}))
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "off"


async def test_a_status_without_the_field_is_unknown_not_dry(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(hass, "mowgli/status", json.dumps({"emergency": False}))
    await hass.async_block_till_done()

    assert hass.states.get(SENSOR).state == "unknown"


@pytest.mark.parametrize("state_name", sorted(RAIN_STATE_NAMES))
async def test_paused_for_rain_is_on_in_every_rain_state(
    hass: HomeAssistant, mqtt_mock, state_name: str
) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(
        hass, "mowgli/high_level_status", json.dumps({"state": 1, "state_name": state_name})
    )
    await hass.async_block_till_done()

    pause = hass.states.get(PAUSE)
    assert pause.state == "on"
    assert pause.attributes["state_name"] == state_name


@pytest.mark.parametrize("state_name", ["IDLE", "MOWING", "DOCKING", "CHARGING", "UNDOCKING"])
async def test_paused_for_rain_is_off_otherwise(
    hass: HomeAssistant, mqtt_mock, state_name: str
) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(
        hass, "mowgli/high_level_status", json.dumps({"state": 1, "state_name": state_name})
    )
    await hass.async_block_till_done()

    assert hass.states.get(PAUSE).state == "off"


def test_the_rain_state_names_are_the_ones_the_behavior_tree_publishes() -> None:
    # main_tree.xml's RainGuard publishes exactly these; a rename there must reach this list.
    assert RAIN_STATE_NAMES == {
        "RAIN_DETECTED_DOCKING",
        "RAIN_WAITING",
        "RESUMING_AFTER_RAIN",
        "RAIN_DOCK_FAILED",
        "RAIN_TIMEOUT",
    }
