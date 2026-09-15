"""MQTT hub shared by every entity of one MowgliNext config entry.

Push-based (`iot_class: local_push`): subscribes once to every documented
topic (see `docs/MQTT_CONTROL.md` in the main mowglinext repo) via Home
Assistant's own `mqtt` integration, and fans updates out to entities via a
small per-topic listener list. There is no polling and this hub never opens
its own broker connection — it rides on whatever broker HA's `mqtt`
integration is already connected to, so multiple mowers share one
connection and reuse HA's own reconnect/backoff handling.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

# Topic suffixes carrying JSON, mapped 1:1 to the key their parsed payload is
# stored under in self.data. "available" is handled separately — it's plain
# text ("online"/"offline"), not JSON.
JSON_TOPICS: tuple[str, ...] = (
    "status",
    "power",
    "emergency",
    "high_level_status",
    "gps",
    "rtk_status",
    "diagnostics",
)


class MowglinextHub:
    """Owns the MQTT subscriptions for one mower and its last-known state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, topic_prefix: str) -> None:
        self.hass = hass
        self.entry = entry
        self.topic_prefix = topic_prefix
        # No <prefix>/available has ever arrived yet: assume unavailable
        # rather than optimistically "online", so entities don't show a
        # stale/wrong state before the first real message.
        self.available = False
        self.data: dict[str, Any] = {name: {} for name in JSON_TOPICS}
        self._listeners: dict[str, list[Callable[[], None]]] = {}
        self._unsubscribes: list[Callable[[], None]] = []

    @property
    def device_id(self) -> str:
        """Stable identifier used to group entities under one HA device.

        Deliberately the config entry id, not the (user-editable) topic
        prefix: renaming the prefix via the options flow must not orphan
        entity history or re-create the device.
        """
        return self.entry.entry_id

    def _topic(self, suffix: str) -> str:
        return f"{self.topic_prefix}/{suffix}"

    async def async_setup(self) -> None:
        """Subscribe to every documented topic."""
        for suffix in JSON_TOPICS:
            self._unsubscribes.append(
                await mqtt.async_subscribe(
                    self.hass, self._topic(suffix), self._make_json_handler(suffix)
                )
            )
        self._unsubscribes.append(
            await mqtt.async_subscribe(self.hass, self._topic("available"), self._handle_available)
        )

    async def async_unload(self) -> None:
        """Undo every subscription made in async_setup.

        Must be called from async_unload_entry — an integration reload that
        skips this leaks a subscription per topic, each still holding a
        reference back into this (about to be discarded) hub.
        """
        for unsub in self._unsubscribes:
            unsub()
        self._unsubscribes.clear()

    def _make_json_handler(self, suffix: str) -> Callable[[mqtt.ReceiveMessage], None]:
        @callback
        def _handle(msg: mqtt.ReceiveMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except (ValueError, TypeError):
                _LOGGER.debug("Ignoring non-JSON payload on %s: %r", msg.topic, msg.payload)
                return
            self.data[suffix] = payload
            self._notify(suffix)

        return _handle

    @callback
    def _handle_available(self, msg: mqtt.ReceiveMessage) -> None:
        self.available = str(msg.payload).strip().lower() == "online"
        # Availability gates every entity, not just one topic's listeners —
        # notify everyone so `available` (an Entity property, not tied to a
        # single topic) gets re-evaluated everywhere.
        for suffix in (*JSON_TOPICS, "available"):
            self._notify(suffix)

    def _notify(self, suffix: str) -> None:
        for listener in self._listeners.get(suffix, []):
            listener()

    @callback
    def async_add_listener(self, suffix: str, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever `suffix`'s payload updates.

        Returns an unsubscribe callable — entities MUST call it from
        `async_will_remove_from_hass`, or a reload/removal leaks a callback
        that keeps firing against a torn-down entity.
        """
        self._listeners.setdefault(suffix, []).append(listener)

        @callback
        def _remove() -> None:
            self._listeners[suffix].remove(listener)

        return _remove

    async def async_publish_command(self, command: int) -> None:
        """Fire-and-forget: publish a HighLevelControl command code.

        Payload MUST be the ASCII decimal string (e.g. "1"), NOT a raw byte
        — see docs/MQTT_CONTROL.md. There is no ack on this channel:
        entities update optimistically and reconcile against the next
        high_level_status payload.
        """
        await mqtt.async_publish(self.hass, self._topic("command"), str(int(command)))
