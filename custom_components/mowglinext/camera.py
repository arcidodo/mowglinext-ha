"""MowgliNext map camera.

Renders the recorded mowing areas (<prefix>/area_boundary), the mower's
current position (<prefix>/gps, projected through the same datum the mower
uses) and its trail since the current mow session started, as a PNG. Built as
a camera entity because cards such as custom:lawn-mower-card take a camera for
their map preview.

The mower's position comes from <prefix>/pose (the localizer's fused pose in
the map frame: smooth, with a heading, and no datum needed) while it is being
published, and falls back to <prefix>/gps projected through the datum.

Also drawn: the area being worked is highlighted and the others dimmed
(<prefix>/high_level_status), the stretches driven with the blade running are
drawn as a stripe as wide as the cut (<prefix>/status), and the mower's marker
is coloured by its RTK quality (<prefix>/rtk_status).
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MowglinextHub
from .entity import MowglinextEntity
from .map_render import (
    PALETTES,
    PLAUSIBLE_RADIUS_M,
    RGB,
    Point,
    TrailBuffer,
    TrailSample,
    active_area_index,
    is_blade_on,
    is_charging,
    is_session_start,
    parse_areas,
    parse_datum,
    parse_coverage_path,
    parse_dock,
    parse_pose,
    render_map,
    rtk_marker_colour,
    to_enu,
)

# Attribute writes (not image renders) are rate-limited so a 1 Hz GPS stream
# does not turn into 1 Hz recorder churn. The image itself is rendered on
# request from the latest data.
STATE_WRITE_MIN_INTERVAL_S = 5.0

# render_map()'s own default (800, up to 1000 tall) is sized for a full-page view.
# Home Assistant's entity "more info" dialog shows a still camera's image near its
# native pixel size, so a tall/narrow garden at that size could overflow the dialog
# and force the whole page to scroll. Render smaller by default; an explicit
# width/height request (e.g. a card asking for a thumbnail) still overrides it.
DEFAULT_IMAGE_WIDTH = 640
DEFAULT_IMAGE_MAX_HEIGHT = 640

# A <prefix>/pose older than this is not trusted any more (the localizer stopped
# publishing, e.g. it lost its fix) and the position falls back to the raw GPS fix.
POSE_MAX_AGE_S = 10.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub: MowglinextHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowglinextMapCamera(hub)])


def _notice(payload: Any, position: Point | None) -> str | None:
    """Why the mower is missing from the picture, when it is."""
    if position is not None or not parse_areas(payload):
        return None
    if parse_datum(payload) is None:
        return "Mower position unavailable: the mower reports no map datum (update its software)"
    return "Waiting for a GPS fix"


def _render(
    payload: Any,
    position: Point | None,
    trail: list[TrailSample],
    style: str,
    active_area: int | None,
    marker_colour: RGB | None,
    heading: float | None,
    width: int,
    max_height: int,
    coverage_path_payload: Any,
) -> bytes:
    return render_map(
        parse_areas(payload),
        position,
        trail,
        width=width,
        max_height=max_height,
        notice=_notice(payload, position),
        palette=PALETTES[style],
        active_area=active_area,
        marker_colour=marker_colour,
        heading=heading,
        dock=parse_dock(payload),
        planned_path=parse_coverage_path(coverage_path_payload),
    )


class MowglinextMapCamera(MowglinextEntity, Camera):
    """Map preview: lawn polygons, trail and current mower position."""

    _attr_name = "Map"
    # high_level_status drives session-start detection and the highlighted area
    # (via _handle_hub_update); the other topics are added in async_added_to_hass.
    _topic_key = "high_level_status"
    _unrecorded_attributes = frozenset(
        {"map_updated", "trail_points", "position_x", "position_y", "heading_deg", "blade_on"}
    )

    def __init__(self, hub: MowglinextHub) -> None:
        Camera.__init__(self)
        MowglinextEntity.__init__(self, hub)
        self.content_type = "image/png"
        self._attr_unique_id = f"{hub.device_id}_map"
        self._trail = TrailBuffer()
        self._position: Point | None = None
        self._heading: float | None = None
        self._last_pose_time: float | None = None
        self._previous_state_name: str | None = None
        self._active_area: int | None = None
        self._charging = False
        self._blading = False
        self._marker_colour: RGB | None = None
        self._render_version = 0
        self._rendered_version = -1
        self._written_version = -1
        self._last_write = 0.0
        self._written_available: bool | None = None
        self._png: bytes | None = None
        self._png_size: tuple[int, int] | None = None
        self._extra_unsubs: list[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._extra_unsubs = [
            self.hub.async_add_listener("gps", self._handle_gps),
            self.hub.async_add_listener("pose", self._handle_pose),
            self.hub.async_add_listener("coverage_path", self._handle_coverage_path),
            self.hub.async_add_listener("area_boundary", self._handle_area_boundary),
            self.hub.async_add_listener("status", self._handle_status),
            self.hub.async_add_listener("rtk_status", self._handle_rtk_status),
            self.hub.async_add_listener("map_style", self._handle_map_style),
        ]
        # The broker replays retained data on subscribe, so some may already be here.
        self._read_context()
        self._ingest_position()
        self._render_version += 1

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._extra_unsubs:
            unsub()
        self._extra_unsubs = []
        await super().async_will_remove_from_hass()

    def _read_context(self) -> None:
        """Pick up whatever the hub already holds for the non-position inputs."""
        hls = self.hub.data.get("high_level_status")
        self._active_area = active_area_index(hls)
        self._charging = is_charging(hls)
        self._blading = is_blade_on(self.hub.data.get("status"))
        self._marker_colour = rtk_marker_colour(self.hub.data.get("rtk_status"))

    @callback
    def _handle_hub_update(self) -> None:
        """high_level_status: session starts, the active area, and being on the dock."""
        hls = self.hub.data.get("high_level_status") or {}
        changed = False
        if is_session_start(self._previous_state_name, hls.get("state_name")):
            self._trail.clear()
            changed = True
        self._previous_state_name = hls.get("state_name")
        active = active_area_index(hls)
        if active != self._active_area:
            self._active_area = active
            changed = True
        # GPS jitter while docked would scribble over the map; keep the trail still.
        self._charging = is_charging(hls)
        if changed:
            self._render_version += 1
        self._write_state_if_due()

    @callback
    def _handle_status(self) -> None:
        # Only affects the trail points added from now on, so no re-render.
        self._blading = is_blade_on(self.hub.data.get("status"))

    @callback
    def _handle_rtk_status(self) -> None:
        colour = rtk_marker_colour(self.hub.data.get("rtk_status"))
        if colour != self._marker_colour:
            self._marker_colour = colour
            self._render_version += 1
            self._write_state_if_due()

    @callback
    def _handle_map_style(self) -> None:
        self._render_version += 1
        self._write_state_if_due(force=True)

    @callback
    def _handle_area_boundary(self) -> None:
        # The datum may have just become known, so the last fix can now be placed.
        self._ingest_position()
        self._render_version += 1
        self._write_state_if_due()

    @callback
    def _handle_gps(self) -> None:
        if self._ingest_position():
            self._render_version += 1
        self._write_state_if_due()

    @callback
    def _handle_coverage_path(self) -> None:
        self._render_version += 1
        self._write_state_if_due()

    @callback
    def _handle_pose(self) -> None:
        self._last_pose_time = time.monotonic()
        if self._ingest_position():
            self._render_version += 1
        self._write_state_if_due()

    def _pose_is_fresh(self) -> bool:
        return (
            self._last_pose_time is not None
            and time.monotonic() - self._last_pose_time <= POSE_MAX_AGE_S
        )

    def _current_position(self) -> tuple[Point, float | None] | None:
        """The mower's position (and heading, if known): the fused pose, else the GPS fix."""
        if self._pose_is_fresh() and (pose := parse_pose(self.hub.data.get("pose"))) is not None:
            return pose
        gps = self.hub.data.get("gps") or {}
        datum = parse_datum(self.hub.data.get("area_boundary"))
        lat, lon = gps.get("latitude"), gps.get("longitude")
        status = gps.get("status")
        if (
            datum is None
            or not isinstance(lat, (int, float))
            or not isinstance(lon, (int, float))
            or (isinstance(status, (int, float)) and status < 0)
        ):
            return None
        return to_enu(lat, lon, datum[0], datum[1]), None

    def _ingest_position(self) -> bool:
        """Take the latest position; True if it (or the heading) changed."""
        current = self._current_position()
        if current is None:
            return False
        position, heading = current
        if max(map(abs, position)) > PLAUSIBLE_RADIUS_M:
            return False
        if position == self._position and heading == self._heading:
            return False
        moved = position != self._position
        self._position = position
        self._heading = heading
        if moved and not self._charging:
            self._trail.add(position, self._blading)
        return True

    def _write_state_if_due(self, force: bool = False) -> None:
        # The mower going offline/online must show at once, throttle or not.
        force = force or self.available != self._written_available
        if not force and self._written_version == self._render_version:
            return
        now = time.monotonic()
        if not force and now - self._last_write < STATE_WRITE_MIN_INTERVAL_S:
            return
        self._last_write = now
        self._written_version = self._render_version
        self._written_available = self.available
        self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        # A caller that asks for a specific size (e.g. a thumbnail) overrides our own
        # smaller default; render_map fits the content within it, never distorting it.
        size = (width or DEFAULT_IMAGE_WIDTH, height or DEFAULT_IMAGE_MAX_HEIGHT)
        if (
            self._png is None
            or self._rendered_version != self._render_version
            or self._png_size != size
        ):
            version = self._render_version
            self._png = await self.hass.async_add_executor_job(
                partial(
                    _render,
                    self.hub.data.get("area_boundary"),
                    self._position,
                    self._trail.samples(),
                    self.hub.map_style,
                    self._active_area,
                    self._marker_colour,
                    self._heading,
                    *size,
                    self.hub.data.get("coverage_path"),
                )
            )
            self._rendered_version = version
            self._png_size = size
        return self._png

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "trail_points": len(self._trail),
            "blade_on": self._blading,
            "map_style": self.hub.map_style,
            "map_updated": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if self._position is not None:
            attrs["position_x"] = round(self._position[0], 2)
            attrs["position_y"] = round(self._position[1], 2)
        if self._heading is not None:
            attrs["heading_deg"] = round(math.degrees(self._heading) % 360, 1)
        attrs["position_source"] = "pose" if self._pose_is_fresh() else "gps"
        return attrs
