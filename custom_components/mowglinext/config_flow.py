"""Config flow for MowgliNext.

One config entry = one mower, identified by its MQTT topic prefix. Broker
host/port/credentials are NOT asked here — they live once in Home
Assistant's own `mqtt` integration, which this integration rides on (see
coordinator.py) rather than opening a second, independent connection.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import CONF_MOWER_HOST, CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN

# `homeassistant.data_entry_flow.FlowResult` was removed in favour of
# `config_entries.ConfigFlowResult` — referencing it via the already-imported
# `config_entries` module (rather than a separate top-level import) means
# this annotation-only reference can't itself raise ImportError on an older
# or newer Home Assistant core; `from __future__ import annotations` above
# also means it's never evaluated at runtime either way.


class MowglinextConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MowgliNext."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MowglinextOptionsFlow:
        return MowglinextOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        # NOT `"mqtt" not in hass.config.components` -- that's true almost
        # immediately regardless of whether the user ever configured a
        # broker, because we declare "mqtt" in manifest.json's
        # `dependencies`: Home Assistant sets a declared dependency up
        # (empty/default config, no config entry) before running our flow
        # at all, precisely so a flow CAN assume the dependency's helpers
        # are importable/usable. That check can never fire once we depend
        # on mqtt, in production or in tests -- confirmed by
        # test_abort_when_mqtt_not_configured failing against a bare `hass`
        # fixture with no mqtt config entry at all.
        #
        # The real question is whether the user has actually gone through
        # Settings -> Add integration -> MQTT and pointed it at a broker,
        # which is exactly what a config entry for the "mqtt" domain
        # existing means. A broker being reachable is a separate question
        # the hub surfaces later via <prefix>/available -- a broker that's
        # merely slow to answer shouldn't block setup here.
        if not self.hass.config_entries.async_entries("mqtt"):
            return self.async_abort(reason="mqtt_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            prefix = user_input[CONF_TOPIC_PREFIX].strip().strip("/")
            if not prefix:
                errors[CONF_TOPIC_PREFIX] = "invalid_prefix"
            else:
                await self.async_set_unique_id(prefix)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Mowgli ({prefix})", data={CONF_TOPIC_PREFIX: prefix}
                )

        schema = vol.Schema({vol.Required(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class MowglinextOptionsFlow(config_entries.OptionsFlow):
    """Settings.Devices & services.MowgliNext.Configure.

    Currently just the mower's own hostname/IP, used to point the device page's
    "Visit" link at its GUI (port 4006) instead of the project's GitHub repo --
    optional, and not asked for at initial setup, since MQTT alone never tells
    us the mower's address.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            host = user_input.get(CONF_MOWER_HOST, "").strip()
            return self.async_create_entry(data={CONF_MOWER_HOST: host})

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MOWER_HOST,
                    default=self.config_entry.options.get(CONF_MOWER_HOST, ""),
                ): str
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
