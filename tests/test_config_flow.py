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


async def test_options_flow_defaults_to_empty_host(hass: HomeAssistant, mqtt_mock) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.mowglinext.const import CONF_MOWER_HOST

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MOWER_HOST: "192.168.1.50"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MOWER_HOST] == "192.168.1.50"


async def test_options_flow_strips_whitespace_and_accepts_blank(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.mowglinext.const import CONF_MOWER_HOST

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MOWER_HOST: "  192.168.1.50  "}
    )
    assert entry.options[CONF_MOWER_HOST] == "192.168.1.50"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MOWER_HOST: ""}
    )
    assert entry.options[CONF_MOWER_HOST] == ""


async def test_visit_link_defaults_to_github_without_a_host(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.mowglinext.const import DEFAULT_CONFIGURATION_URL, DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import device_registry as dr
    device_registry = dr.async_get(hass)
    hub = hass.data[DOMAIN][entry.entry_id]
    device = device_registry.async_get_device(identifiers={(DOMAIN, hub.device_id)})
    assert device is not None
    assert device.configuration_url == DEFAULT_CONFIGURATION_URL


async def test_visit_link_points_at_the_mower_after_setting_a_host(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.mowglinext.const import CONF_MOWER_HOST, DOMAIN, MOWER_GUI_PORT

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MOWER_HOST: "192.168.1.50"}
    )
    await hass.async_block_till_done()  # the update listener reloads the entry

    from homeassistant.helpers import device_registry as dr
    device_registry = dr.async_get(hass)
    hub = hass.data[DOMAIN][entry.entry_id]
    device = device_registry.async_get_device(identifiers={(DOMAIN, hub.device_id)})
    assert device is not None
    assert device.configuration_url == f"http://192.168.1.50:{MOWER_GUI_PORT}"


async def test_visit_link_auto_detects_from_the_mower(hass: HomeAssistant, mqtt_mock) -> None:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_mqtt_message,
    )
    from homeassistant.helpers import device_registry as dr

    from custom_components.mowglinext.const import DOMAIN, MOWER_GUI_PORT

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, "mowgli/host", '{"ip": "192.168.12.10"}')
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    hub = hass.data[DOMAIN][entry.entry_id]
    device = device_registry.async_get_device(identifiers={(DOMAIN, hub.device_id)})
    assert device is not None
    assert device.configuration_url == f"http://192.168.12.10:{MOWER_GUI_PORT}"


async def test_manual_host_option_overrides_the_mowers_own(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_mqtt_message,
    )
    from homeassistant.helpers import device_registry as dr

    from custom_components.mowglinext.const import CONF_MOWER_HOST, DOMAIN, MOWER_GUI_PORT

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, "mowgli/host", '{"ip": "192.168.12.10"}')
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MOWER_HOST: "10.0.0.5"}
    )
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    hub = hass.data[DOMAIN][entry.entry_id]
    device = device_registry.async_get_device(identifiers={(DOMAIN, hub.device_id)})
    assert device.configuration_url == f"http://10.0.0.5:{MOWER_GUI_PORT}"
