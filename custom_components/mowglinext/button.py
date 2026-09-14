"""MowgliNext buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COMMAND_RESET_EMERGENCY, DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextResetEmergencyButton(hub)])


class MowglinextResetEmergencyButton(MowglinextEntity, ButtonEntity):
    """Publishes COMMAND_RESET_EMERGENCY (254).

    NOTE (docs/MQTT_CONTROL.md, main repo): as of this writing that command
    code is declared in HighLevelControl.srv but is NOT wired to any BT
    guard on the MQTT command channel — the real emergency-reset path is
    the separate /hardware_bridge/emergency_stop ROS service, which
    mqtt_bridge_node does not currently expose. This button is provided for
    forward-compatibility; confirm against your own mqtt_bridge_node build
    before relying on it to actually clear a latched emergency.
    """

    _attr_name = "Reset emergency"
    _attr_icon = "mdi:alert-remove"
    _attr_entity_category = EntityCategory.CONFIG
    _topic_key = "emergency"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_reset_emergency"

    async def async_press(self) -> None:
        await self.hub.async_publish_command(COMMAND_RESET_EMERGENCY)
