"""Pure map rendering for the MowgliNext map camera.

No Home Assistant imports on purpose: everything here is plain geometry plus
Pillow, so it can be unit-tested without the HA test harness.

Inputs come from the mower's MQTT contract (docs/MQTT_CONTROL.md in the main
repo): <prefix>/area_boundary carries the mowing-area polygons as map-frame
metres from a WGS84 datum, and <prefix>/gps carries the mower's real lat/lon.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

Point = tuple[float, float]

# Same equirectangular projection the whole mower stack uses
# (mowgli_interfaces/wgs84_projection.hpp): the map frame is ENU metres from
# the datum, X = east, Y = north, no rotation.
EARTH_RADIUS_M = 6378137.0
METERS_PER_DEG = EARTH_RADIUS_M * math.pi / 180.0

BACKGROUND = (27, 35, 40)
LAWN_FILL = (46, 125, 60)
LAWN_OUTLINE = (125, 220, 138)
OBSTACLE_FILL = (27, 35, 40)
OBSTACLE_OUTLINE = (224, 160, 48)
TRAIL = (74, 163, 255)
MOWER_FILL = (255, 82, 82)
MOWER_OUTLINE = (255, 255, 255)
TEXT = (230, 236, 240)

_SUPERSAMPLE = 2
_SCALE_BAR_STEPS_M = (1, 2, 5, 10, 20, 50, 100, 200)


def to_enu(lat: float, lon: float, datum_lat: float, datum_lon: float) -> Point:
    """Project a WGS84 coordinate to datum-relative (east, north) metres."""
    east = (lon - datum_lon) * math.cos(math.radians(datum_lat)) * METERS_PER_DEG
    north = (lat - datum_lat) * METERS_PER_DEG
    return east, north


@dataclass
class Area:
    name: str
    boundary: list[Point]
    obstacles: list[list[Point]] = field(default_factory=list)


def _points(raw: Any) -> list[Point]:
    """Coerce [[x, y], ...] into points, dropping anything malformed."""
    if not isinstance(raw, list):
        return []
    out: list[Point] = []
    for item in raw:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in item[:2])
        ):
            out.append((float(item[0]), float(item[1])))
    return out


def parse_areas(payload: Any) -> list[Area]:
    """Areas from a <prefix>/area_boundary payload; malformed entries are skipped."""
    if not isinstance(payload, dict):
        return []
    areas: list[Area] = []
    for raw in payload.get("areas") or []:
        if not isinstance(raw, dict):
            continue
        boundary = _points(raw.get("boundary"))
        if len(boundary) < 3:
            continue
        obstacles = [o for o in (_points(o) for o in raw.get("obstacles") or []) if len(o) >= 3]
        areas.append(Area(name=str(raw.get("name") or ""), boundary=boundary, obstacles=obstacles))
    return areas


def parse_datum(payload: Any) -> tuple[float, float] | None:
    """(datum_lat, datum_lon) from a <prefix>/area_boundary payload, if usable."""
    if not isinstance(payload, dict):
        return None
    lat, lon = payload.get("datum_lat"), payload.get("datum_lon")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)):
        return None
    return float(lat), float(lon)


class TrailBuffer:
    """Bounded record of where the mower has been, thinned by distance."""

    def __init__(self, min_step_m: float = 0.15, max_points: int = 4000) -> None:
        self._min_step_m = min_step_m
        self._points: deque[Point] = deque(maxlen=max_points)

    def add(self, point: Point) -> bool:
        """Append `point` unless it is closer than min_step_m to the last one."""
        if self._points:
            last = self._points[-1]
            if math.hypot(point[0] - last[0], point[1] - last[1]) < self._min_step_m:
                return False
        self._points.append(point)
        return True

    def clear(self) -> None:
        self._points.clear()

    def __len__(self) -> int:
        return len(self._points)

    def points(self) -> list[Point]:
        return list(self._points)


def is_session_start(previous_state_name: str | None, state_name: str | None) -> bool:
    """True on the transition into UNDOCKING: a new mow session begins there."""
    return state_name == "UNDOCKING" and previous_state_name != "UNDOCKING"


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: fixed-size bitmap font only
        return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw, xy: Point, text: str, font: Any, fill: tuple[int, int, int]
) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (xy[0] - (left + right) / 2, xy[1] - (top + bottom) / 2), text, font=font, fill=fill
    )


def _encode(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_placeholder(message: str, width: int = 800, height: int = 400) -> bytes:
    image = Image.new("RGB", (width, height), BACKGROUND)
    _draw_centered_text(ImageDraw.Draw(image), (width / 2, height / 2), message, _font(22), TEXT)
    return _encode(image)


def render_map(
    areas: Sequence[Area],
    position: Point | None,
    trail: Sequence[Point] = (),
    *,
    width: int = 800,
    min_height: int = 360,
    max_height: int = 1000,
) -> bytes:
    """Render the lawn(s), the mower's trail and its current position as a PNG.

    North is up. The view fits every area plus the mower and its trail, so the
    mower stays visible even when it is outside the recorded boundary.
    """
    if not areas and position is None:
        return render_placeholder("Waiting for map data")

    every_point: list[Point] = [p for a in areas for p in a.boundary]
    every_point += list(trail)
    if position is not None:
        every_point.append(position)

    xs = [p[0] for p in every_point]
    ys = [p[1] for p in every_point]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    margin_m = max(0.08 * max(span_x, span_y), 1.5)
    min_x, max_x = min_x - margin_m, max_x + margin_m
    min_y, max_y = min_y - margin_m, max_y + margin_m
    span_x, span_y = max_x - min_x, max_y - min_y

    height = int(min(max(width * span_y / span_x, min_height), max_height))
    scale = min(width / span_x, height / span_y)  # px per metre
    offset_x = (width - span_x * scale) / 2
    offset_y = (height - span_y * scale) / 2
    ss = _SUPERSAMPLE

    def px(point: Point) -> Point:
        return (
            ((point[0] - min_x) * scale + offset_x) * ss,
            (height - ((point[1] - min_y) * scale + offset_y)) * ss,
        )

    image = Image.new("RGB", (width * ss, height * ss), BACKGROUND)
    draw = ImageDraw.Draw(image)

    for area in areas:
        outline = [px(p) for p in area.boundary]
        draw.polygon(outline, fill=LAWN_FILL)
        draw.line([*outline, outline[0]], fill=LAWN_OUTLINE, width=2 * ss, joint="curve")
        for obstacle in area.obstacles:
            hole = [px(p) for p in obstacle]
            draw.polygon(hole, fill=OBSTACLE_FILL)
            draw.line([*hole, hole[0]], fill=OBSTACLE_OUTLINE, width=2 * ss, joint="curve")
        if area.name:
            centre = (
                sum(p[0] for p in outline) / len(outline),
                sum(p[1] for p in outline) / len(outline),
            )
            _draw_centered_text(draw, centre, area.name, _font(14 * ss), TEXT)

    if len(trail) >= 2:
        draw.line([px(p) for p in trail], fill=TRAIL, width=3 * ss, joint="curve")

    if position is not None:
        cx, cy = px(position)
        radius = 7 * ss
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=MOWER_FILL,
            outline=MOWER_OUTLINE,
            width=2 * ss,
        )

    _draw_scale_bar(draw, scale, height, ss)

    return _encode(image.resize((width, height), Image.LANCZOS))


def _draw_scale_bar(draw: ImageDraw.ImageDraw, scale: float, height: int, ss: int) -> None:
    """A simple metric scale bar, bottom-left, at most a quarter of the image wide."""
    length_m = 1
    for step in _SCALE_BAR_STEPS_M:
        if step * scale <= 200:
            length_m = step
    x0, y0 = 16 * ss, (height - 16) * ss
    x1 = x0 + length_m * scale * ss
    draw.line([(x0, y0), (x1, y0)], fill=TEXT, width=2 * ss)
    draw.line([(x0, y0 - 4 * ss), (x0, y0 + 4 * ss)], fill=TEXT, width=2 * ss)
    draw.line([(x1, y0 - 4 * ss), (x1, y0 + 4 * ss)], fill=TEXT, width=2 * ss)
    draw.text((x0, y0 - 22 * ss), f"{length_m} m", font=_font(12 * ss), fill=TEXT)
