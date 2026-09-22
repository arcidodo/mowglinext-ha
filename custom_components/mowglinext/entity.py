"""Shared entity base for MowgliNext: device grouping + availability + a
single MQTT-driven update signal.
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DEFAULT_CONFIGURATION_URL, DOMAIN, MOWER_GUI_PORT
from .coordinator import MowglinextHub


class MowglinextEntity(Entity):
    """Base for every MowgliNext entity.

    Subclasses set `_topic_key` to whichever of coordinator.JSON_TOPICS (or
    "available") their state derives from, so they only re-render when that
    topic actually updates.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _topic_key: str = "high_level_status"

    def __init__(self, hub: MowglinextHub) -> None:
        self.hub = hub
        self._unsub_update: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        # Computed live (not cached in __init__) so a mower_host added or changed
        # later via the Options flow (Settings -> Devices & services -> MowgliNext ->
        # Configure) takes effect on the entry's reload, without needing every
        # entity re-created.
        host = self.hub.mower_host
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub.device_id)},
            name="Mowgli",
            manufacturer="MowgliNext",
            model="Robot mower",
            configuration_url=f"http://{host}:{MOWER_GUI_PORT}" if host else DEFAULT_CONFIGURATION_URL,
        )

    @property
    def available(self) -> bool:
        return self.hub.available

    async def async_added_to_hass(self) -> None:
        self._unsub_update = self.hub.async_add_listener(self._topic_key, self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update is not None:
            self._unsub_update()
            self._unsub_update = None

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()
