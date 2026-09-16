"""Tests for the lawn_mower entity's activity mapping and command publishing.

Verified against a real pytest-homeassistant-custom-component run in CI
(.github/workflows/test.yml).
"""
import json

from homeassistant.components.lawn_mower import LawnMowerActivity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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


async def test_activity_mapping_and_availability(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)

    # No <prefix>/available yet -> unavailable, regardless of other topics.
    state = hass.states.get("lawn_mower.mowgli")
    assert state is not None
    assert state.state == "unavailable"

    async_fire_mqtt_message(hass, "mowgli/available", "online")
    async_fire_mqtt_message(
        hass,
        "mowgli/high_level_status",
        json.dumps(
            {
                "state": 2,  # AUTONOMOUS
                "state_name": "AUTONOMOUS",
                "sub_state_name": "MOWING",
                "is_charging": False,
                "emergency": False,
                "battery_percent": 80.0,
                "coverage_percent": 12.5,
            }
        ),
    )
    await hass.async_block_till_done()

    state = hass.states.get("lawn_mower.mowgli")
    assert state.state == LawnMowerActivity.MOWING
    assert state.attributes["coverage_percent"] == 12.5

    # IDLE + charging -> DOCKED.
    async_fire_mqtt_message(
        hass,
        "mowgli/high_level_status",
        json.dumps(
            {"state": 1, "is_charging": True, "emergency": False, "state_name": "IDLE"}
        ),
    )
    await hass.async_block_till_done()
    assert hass.states.get("lawn_mower.mowgli").state == LawnMowerActivity.DOCKED

    # emergency overrides everything.
    async_fire_mqtt_message(
        hass,
        "mowgli/high_level_status",
        json.dumps({"state": 2, "emergency": True, "state_name": "AUTONOMOUS"}),
    )
    await hass.async_block_till_done()
    assert hass.states.get("lawn_mower.mowgli").state == LawnMowerActivity.ERROR


async def test_start_mowing_publishes_command(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    async_fire_mqtt_message(hass, "mowgli/available", "online")
    # The coordinator's freshness watchdog (coordinator.py) requires BOTH the
    # LWT ("available") AND a recent high_level_status heartbeat before the
    # entity reports itself available -- Home Assistant does not invoke a
    # service method on an unavailable entity, so without this the
    # start_mowing call below silently never reaches async_start_mowing().
    async_fire_mqtt_message(
        hass, "mowgli/high_level_status", json.dumps({"state": 1, "state_name": "IDLE"})
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "lawn_mower",
        "start_mowing",
        {"entity_id": "lawn_mower.mowgli"},
        blocking=True,
    )

    # mqtt_mock wraps the underlying client; exact call-arg shape has drifted
    # across Home Assistant versions, so check loosely for "published '1' to
    # the command topic" rather than pin an exact positional/keyword form.
    published = [
        call for call in mqtt_mock.async_publish.mock_calls if "mowgli/command" in call.args
    ]
    assert published, mqtt_mock.async_publish.mock_calls
    assert "1" in published[-1].args
