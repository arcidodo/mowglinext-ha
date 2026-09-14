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

from .const import CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN

# `homeassistant.data_entry_flow.FlowResult` was removed in favour of
# `config_entries.ConfigFlowResult` — referencing it via the already-imported
# `config_entries` module (rather than a separate top-level import) means
# this annotation-only reference can't itself raise ImportError on an older
# or newer Home Assistant core; `from __future__ import annotations` above
# also means it's never evaluated at runtime either way.


class MowglinextConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MowgliNext."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        # `"mqtt" in hass.config.components` is true once the mqtt
        # integration has finished setup — a broker being reachable is a
        # separate question the hub surfaces later via <prefix>/available,
        # since a broker that is merely slow to answer shouldn't block setup.
        if "mqtt" not in self.hass.config.components:
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
