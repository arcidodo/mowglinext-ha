"""Tests for the external-rain feedback entities: a binary sensor that is on while the mower is
COUNTING an external verdict, and a diagnostic enum sensor with the exact state."""
import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

BINARY = "binary_sensor.mowgli_external_rain"
STATUS = "sensor.mowgli_external_rain_status"
TOPIC = "mowgli/external_rain_state"


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


def _payload(**over) -> str:
    base = {
        "state": "wet",
        "enabled": True,
        "received": True,
        "wet": True,
        "active": True,
        "onboard_wet": False,
        "age_s": 312,
        "expires_in_s": 6888,
        "max_age_s": 7200,
    }
    base.update(over)
    return json.dumps(base)


async def test_unknown_until_the_mower_publishes_the_topic(hass: HomeAssistant, mqtt_mock) -> None:
    # Older mower software never publishes it: honest "unknown", not a fabricated "off".
    await _setup(hass, mqtt_mock)

    assert hass.states.get(BINARY).state == "unknown"
    assert hass.states.get(STATUS).state == "unknown"


async def test_wet_and_counted_reads_on(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(hass, TOPIC, _payload())
    await hass.async_block_till_done()

    binary = hass.states.get(BINARY)
    assert binary.state == "on"
    assert binary.attributes["state"] == "wet"
    assert binary.attributes["age_s"] == 312
    assert binary.attributes["expires_in_s"] == 6888
    assert binary.attributes["own_sensor_wet"] is False
    status = hass.states.get(STATUS)
    assert status.state == "Wet"
    assert status.attributes["counted"] is True
    assert status.attributes["max_age_s"] == 7200


async def test_an_expired_or_disabled_verdict_reads_off_even_though_it_said_wet(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup(hass, mqtt_mock)

    # The mower no longer counts it: the sensor follows the mower, not what HA last sent.
    async_fire_mqtt_message(
        hass, TOPIC, _payload(state="expired", active=False, expires_in_s=None)
    )
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state == "off"
    assert hass.states.get(STATUS).state == "Expired"

    async_fire_mqtt_message(
        hass, TOPIC, _payload(state="disabled", enabled=False, active=False, expires_in_s=None)
    )
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state == "off"
    assert hass.states.get(STATUS).state == "Disabled"


async def test_dry_and_no_data(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(
        hass, TOPIC, _payload(state="dry", wet=False, active=False, expires_in_s=None)
    )
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state == "off"
    assert hass.states.get(STATUS).state == "Dry"

    async_fire_mqtt_message(
        hass,
        TOPIC,
        _payload(
            state="no_data", received=False, wet=False, active=False, age_s=None, expires_in_s=None
        ),
    )
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state == "off"
    assert hass.states.get(STATUS).state == "No data"


async def test_the_mowers_own_sensor_is_reported_apart(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup(hass, mqtt_mock)

    async_fire_mqtt_message(
        hass,
        TOPIC,
        _payload(state="dry", wet=False, active=False, onboard_wet=True, expires_in_s=None),
    )
    await hass.async_block_till_done()

    # The external source is dry; the mower's own sensor is wet -- the two are not mixed up.
    assert hass.states.get(BINARY).state == "off"
    assert hass.states.get(BINARY).attributes["own_sensor_wet"] is True
