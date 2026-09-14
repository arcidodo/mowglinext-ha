"""Tests for the MowgliNext config flow.

NOTE: written against pytest-homeassistant-custom-component's conventions
(the `hass`/`mqtt_mock` fixtures) but not executed in this environment (no
network access to install Home Assistant's test harness here) — run
`pip install -r requirements_test.txt && pytest` before relying on these,
and expect minor fixture-name drift across Home Assistant versions.
"""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN


async def test_abort_when_mqtt_not_configured(hass: HomeAssistant) -> None:
    """No mqtt integration set up yet -> abort, not a broken form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_creates_entry_with_topic_prefix(hass: HomeAssistant, mqtt_mock) -> None:
    """A non-empty prefix creates an entry keyed on that prefix."""
    hass.config.components.add("mqtt")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOPIC_PREFIX: "mowgli"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_TOPIC_PREFIX: "mowgli"}


async def test_rejects_empty_prefix(hass: HomeAssistant, mqtt_mock) -> None:
    hass.config.components.add("mqtt")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOPIC_PREFIX: "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOPIC_PREFIX: "invalid_prefix"}
