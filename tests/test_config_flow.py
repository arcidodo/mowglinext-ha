"""Tests for the MowgliNext config flow.

Verified against a real pytest-homeassistant-custom-component run in CI
(.github/workflows/test.yml) -- see git history for the two real bugs that
surfaced there: the "mqtt not configured" check originally read
`hass.config.components` (always true once we declare "mqtt" as a
manifest dependency -- Home Assistant sets a declared dependency up
before running our flow, regardless of whether the user ever configured
a broker) instead of checking for an actual mqtt config entry.
"""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN


async def test_abort_when_mqtt_not_configured(hass: HomeAssistant) -> None:
    """No mqtt config entry exists yet -> abort, not a broken form.

    Deliberately does NOT request the `mqtt_mock` fixture -- that fixture
    is what creates a real (mocked) "mqtt" config entry, which is exactly
    the condition this test asserts is absent.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_creates_entry_with_topic_prefix(hass: HomeAssistant, mqtt_mock) -> None:
    """A non-empty prefix creates an entry keyed on that prefix.

    Requesting the `mqtt_mock` fixture is what satisfies the config flow's
    `hass.config_entries.async_entries("mqtt")` check -- it registers a
    real (mocked) mqtt config entry, unlike the plain
    `hass.config.components.add("mqtt")` this test used before that only
    ever satisfied the old, buggy check.
    """
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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOPIC_PREFIX: "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOPIC_PREFIX: "invalid_prefix"}
