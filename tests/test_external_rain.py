"""Tests for the mowglinext.set_external_rain service (lawn_mower.py's entity
service, coordinator.py's publish helper)."""
import json

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

ENTITY = "lawn_mower.mowgli"
TOPIC = "mowgli/external_rain"


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


def _published(mqtt_mock):
    return [call for call in mqtt_mock.async_publish.mock_calls if TOPIC in call.args]


@pytest.mark.parametrize(("raining", "payload"), [(True, "yes"), (False, "no")])
async def test_set_external_rain_publishes_yes_or_no_retained(
    hass: HomeAssistant, mqtt_mock, raining: bool, payload: str
) -> None:
    await _setup(hass, mqtt_mock)

    await hass.services.async_call(
        DOMAIN, "set_external_rain", {"entity_id": ENTITY, "raining": raining}, blocking=True
    )

    published = _published(mqtt_mock)
    assert published, mqtt_mock.async_publish.mock_calls
    topic, sent, _qos, retain = published[-1].args
    assert (topic, sent) == (TOPIC, payload)
    # State, not a command: retained so the last verdict survives a bridge restart.
    assert retain is True


async def test_set_external_rain_accepts_the_usual_truthy_spellings(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup(hass, mqtt_mock)

    await hass.services.async_call(
        DOMAIN, "set_external_rain", {"entity_id": ENTITY, "raining": "on"}, blocking=True
    )

    assert _published(mqtt_mock)[-1].args[1] == "yes"


async def test_set_external_rain_requires_the_raining_field(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup(hass, mqtt_mock)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "set_external_rain", {"entity_id": ENTITY}, blocking=True
        )
    assert not _published(mqtt_mock)
