"""MowgliNext lawn_mower entity — the primary "is it mowing?" entity.

HighLevelStatus has more states than HA's LawnMowerActivity can express (no
native "recording" or "manual mowing" activity, see docs/MQTT_CONTROL.md in
the main repo for the full state table) — the raw state_name/sub_state_name
survive as extra_state_attributes and on the diagnostic "State" sensor
(sensor.py) for anyone who needs the exact substate.
"""
from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COMMAND_HOME,
    COMMAND_START,
    COMMAND_STOP,
    DOMAIN,
    STATE_AUTONOMOUS,
    STATE_IDLE,
    STATE_MANUAL_MOWING,
    STATE_RECORDING,
)
from .coordinator import MowglinextHub
from .entity import MowglinextEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextLawnMower(hub)])


class MowglinextLawnMower(MowglinextEntity, LawnMowerEntity):
    """Maps <prefix>/high_level_status onto HA's lawn_mower domain."""

    _attr_name = None  # use the device name ("Mowgli") as the entity name
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )
    _topic_key = "high_level_status"

    def __init__(self, hub: MowglinextHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.device_id}_lawn_mower"

    @property
    def activity(self) -> LawnMowerActivity | None:
        status = self.hub.data.get("high_level_status") or {}
        if not status:
            return None
        # Emergency overrides everything else, regardless of `state`.
        if status.get("emergency"):
            return LawnMowerActivity.ERROR
        state = status.get("state")
        if state == STATE_IDLE:
            return (
                LawnMowerActivity.DOCKED
                if status.get("is_charging")
                else LawnMowerActivity.PAUSED
            )
        if state in (STATE_AUTONOMOUS, STATE_MANUAL_MOWING):
            # AUTONOMOUS also covers undocking/transit/returning-to-dock —
            # HA has no distinct "returning" activity for a mower.
            return LawnMowerActivity.MOWING
        if state == STATE_RECORDING:
            # No native "recording" activity; PAUSED is the closest
            # non-misleading choice (it is, in fact, not mowing).
            return LawnMowerActivity.PAUSED
        # STATE_NULL, or an absent/unknown value.
        return LawnMowerActivity.ERROR

    @property
    def extra_state_attributes(self) -> dict:
        status = self.hub.data.get("high_level_status") or {}
        return {
            "state_name": status.get("state_name"),
            "sub_state_name": status.get("sub_state_name"),
            "current_area": status.get("current_area"),
            "coverage_percent": status.get("coverage_percent"),
        }

    async def async_start_mowing(self) -> None:
        await self.hub.async_publish_command(COMMAND_START)

    async def async_pause(self) -> None:
        # COMMAND_STOP is the true pause-in-place (holds position, blade
        # off); COMMAND_HOME docks instead — see async_dock below.
        await self.hub.async_publish_command(COMMAND_STOP)

    async def async_dock(self) -> None:
        await self.hub.async_publish_command(COMMAND_HOME)
