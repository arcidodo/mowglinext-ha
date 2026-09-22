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
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_MOWER_HOST, DEFAULT_MAP_STYLE
from .map_render import PALETTES

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
    "pose",
    "coverage_path",
    "rtk_status",
    "areas",
    "area_boundary",
    "diagnostics",
)

# <prefix>/areas is a JSON *array* ([{"index":..,"name":..}, ...]), unlike
# every other JSON_TOPICS entry which is an object -- give it a matching
# empty-list default instead of the generic {} below.
_JSON_TOPIC_DEFAULTS: dict[str, Any] = {"areas": []}

# The behavior tree republishes <prefix>/high_level_status at a steady ~1 Hz
# cadence UNCONDITIONALLY (not just on change) as long as the BT and the MQTT
# bridge are both healthy and connected -- see docs/MQTT_CONTROL.md. That
# makes it a reliable heartbeat: if nothing arrives for this long, something
# is actually wrong (a wedged mqtt_bridge_node that never lost its TCP
# connection won't trigger the <prefix>/available LWT, so that topic alone
# can say "online" indefinitely while every other topic silently goes
# stale). STALE_AFTER_S is deliberately generous (~10x the expected 1 Hz
# cadence) to ride out normal jitter/reconnects without flapping.
HEARTBEAT_TOPIC = "high_level_status"
STALE_AFTER_S = 10.0
STALE_CHECK_INTERVAL_S = 5.0


class MowglinextHub:
    """Owns the MQTT subscriptions for one mower and its last-known state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, topic_prefix: str) -> None:
        self.hass = hass
        self.entry = entry
        self.topic_prefix = topic_prefix
        # Optional, set via the integration's Options flow -- see const.py's
        # CONF_MOWER_HOST doc comment for why this isn't asked for at setup.
        self.mower_host = entry.options.get(CONF_MOWER_HOST, "").strip()
        # No <prefix>/available has ever arrived yet: assume unavailable
        # rather than optimistically "online", so entities don't show a
        # stale/wrong state before the first real message.
        self._mqtt_online = False
        # 0.0 (never seen) is always considered stale by _is_fresh() below.
        self._last_heartbeat = 0.0
        self._was_available = False
        self.data: dict[str, Any] = {
            name: _JSON_TOPIC_DEFAULTS.get(name, {}) for name in JSON_TOPICS
        }
        self._listeners: dict[str, list[Callable[[], None]]] = {}
        self._unsubscribes: list[Callable[[], None]] = []
        # UI-only "armed" area name, set by the area-picker select entity and
        # read by the companion "Start selected area" button — never
        # published to MQTT itself, and never persisted across a restart.
        # Deliberately a name, not an index: the index is only ever resolved
        # from the CURRENT <prefix>/areas payload at the moment the button
        # is actually pressed (mowglinext#637 — indices are not stable), not
        # cached from whenever it was picked.
        self.pending_area_name: str | None = None
        # UI preference for the map camera's colours, set by the "Map style"
        # select entity (which restores it across restarts). Held here so the
        # camera can react to it like it reacts to any other update.
        self.map_style: str = DEFAULT_MAP_STYLE
        # Degrees, matching the robot GUI's own "Map Rotation" (Mapbox bearing,
        # gui.map.display.bearing): 0 = north up. Not read from the mower -- that
        # value lives in the GUI's own local display settings, not on MQTT -- so this
        # is a separate preference the operator dials in here to match it if they want.
        self.map_rotation_deg: float = 0.0

    @property
    def available(self) -> bool:
        """True only if the broker says we're online AND data is fresh.

        Both conditions matter: the LWT (`_mqtt_online`) catches a lost TCP
        connection, but a `mqtt_bridge_node` that's merely wedged (stuck
        without actually dropping the socket) would never trigger it —
        that's what the heartbeat freshness check is for. Either one being
        false means "don't trust what's currently in self.data".
        """
        return self._mqtt_online and self._is_heartbeat_fresh()

    def _is_heartbeat_fresh(self) -> bool:
        return (time.monotonic() - self._last_heartbeat) < STALE_AFTER_S

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
        """Subscribe to every documented topic and start the staleness watchdog."""
        for suffix in JSON_TOPICS:
            self._unsubscribes.append(
                await mqtt.async_subscribe(
                    self.hass, self._topic(suffix), self._make_json_handler(suffix)
                )
            )
        self._unsubscribes.append(
            await mqtt.async_subscribe(self.hass, self._topic("available"), self._handle_available)
        )
        self._unsubscribes.append(
            async_track_time_interval(
                self.hass, self._check_staleness, timedelta(seconds=STALE_CHECK_INTERVAL_S)
            )
        )

    async def async_unload(self) -> None:
        """Undo every subscription/timer started in async_setup.

        Must be called from async_unload_entry — an integration reload that
        skips this leaks a subscription per topic (plus the staleness
        timer), each still holding a reference back into this (about to be
        discarded) hub.
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
            if suffix == HEARTBEAT_TOPIC:
                self._last_heartbeat = time.monotonic()
                self._notify_if_availability_changed()
            self._notify(suffix)

        return _handle

    @callback
    def _handle_available(self, msg: mqtt.ReceiveMessage) -> None:
        self._mqtt_online = str(msg.payload).strip().lower() == "online"
        self._notify_if_availability_changed()

    @callback
    def _check_staleness(self, _now) -> None:
        """Periodic fallback: catch a heartbeat that just stopped arriving.

        The JSON handler above already re-checks availability on every
        heartbeat message; this timer is what notices the OTHER direction —
        going stale between messages, when nothing is arriving to trigger
        that check on its own.
        """
        self._notify_if_availability_changed()

    def _notify_if_availability_changed(self) -> None:
        now_available = self.available
        if now_available == self._was_available:
            return
        self._was_available = now_available
        _LOGGER.debug(
            "%s availability changed: %s (mqtt_online=%s, heartbeat_fresh=%s)",
            self.topic_prefix,
            now_available,
            self._mqtt_online,
            self._is_heartbeat_fresh(),
        )
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

    @callback
    def async_set_map_style(self, style: str) -> None:
        """Change the map camera's colour style; unknown or unchanged styles are ignored."""
        if style == self.map_style or style not in PALETTES:
            return
        self.map_style = style
        self._notify("map_style")

    @callback
    def async_set_map_rotation_deg(self, degrees: float) -> None:
        """Change the map camera's display rotation; normalised to [-180, 180)."""
        normalised = ((degrees + 180) % 360 + 360) % 360 - 180
        if normalised == self.map_rotation_deg:
            return
        self.map_rotation_deg = normalised
        self._notify("map_rotation_deg")

    async def async_publish_command(self, command: int) -> None:
        """Fire-and-forget: publish a HighLevelControl command code.

        Payload MUST be the ASCII decimal string (e.g. "1"), NOT a raw byte
        — see docs/MQTT_CONTROL.md. There is no ack on this channel:
        entities update optimistically and reconcile against the next
        high_level_status payload.
        """
        await mqtt.async_publish(self.hass, self._topic("command"), str(int(command)))

    async def async_start_area(self, index: int) -> None:
        """Fire-and-forget: start mowing the recorded area at `index` now.

        `index` is a raw, purely positional index into map_server_node's
        area list -- there is no stable per-area id yet (mowglinext#637).
        Callers MUST resolve it from the freshest `<prefix>/areas` payload
        immediately before calling this, never a cached value from earlier
        in the session: an unrelated area add/edit/delete elsewhere can
        silently reassign every index, not just ones after the change.
        """
        await mqtt.async_publish(self.hass, self._topic("start_area"), str(int(index)))
