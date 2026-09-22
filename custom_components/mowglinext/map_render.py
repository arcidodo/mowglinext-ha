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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

Point = tuple[float, float]
RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

# Same equirectangular projection the whole mower stack uses
# (mowgli_interfaces/wgs84_projection.hpp): the map frame is ENU metres from
# the datum, X = east, Y = north, no rotation.
EARTH_RADIUS_M = 6378137.0
METERS_PER_DEG = EARTH_RADIUS_M * math.pi / 180.0

# A garden mower does not roam further than this from its map datum; a fix that
# projects beyond it is garbage (or the datum is wrong) and is not plotted.
PLAUSIBLE_RADIUS_M = 5000.0

# Blade motor speed above which the mower counts as cutting. Idle/noise is far
# below; a running blade is in the thousands.
BLADE_RPM_THRESHOLD = 300.0
# Effective cut width used to draw the mowed stripe (the mower's own default).
DEFAULT_TOOL_WIDTH_M = 0.18

# high_level_status states in which `current_area` is the area being worked.
ACTIVE_AREA_STATES = frozenset({"MOWING", "PLANNING", "TRANSIT"})

MINT: RGB = (69, 214, 136)  # gui/web/src/pages/MapStyle.tsx's brand mint, '#45D688'

RTK_FIXED_COLOUR: RGB = (0, 200, 83)
RTK_FLOAT_COLOUR: RGB = (255, 152, 0)
RTK_NONE_COLOUR: RGB = (244, 67, 54)

_SUPERSAMPLE = 2
_SCALE_BAR_STEPS_M = (1, 2, 5, 10, 20, 50, 100, 200)
_DIM_FACTOR = 0.55  # how far an inactive area is mixed towards the background
_DIM_ALPHA = 90  # ... or how transparent it becomes when there is no background


@dataclass(frozen=True)
class Palette:
    """Colours for one map style. `background=None` renders a transparent PNG so
    the map blends into whatever card it is shown on."""

    background: RGB | None
    lawn_fill: RGB
    lawn_outline: RGB
    obstacle_outline: RGB
    mowed: RGB  # the stripe drawn where the blade has been cutting
    trail: RGB  # the thin line where the mower travelled with the blade off
    mower_fill: RGB  # used when the RTK quality is unknown
    mower_outline: RGB
    dock: RGB
    planned_path: RGB
    text: RGB
    notice: RGB


PALETTES: dict[str, Palette] = {
    # The original look: dark slate background.
    "classic": Palette(
        background=(27, 35, 40),
        lawn_fill=(46, 125, 60),
        lawn_outline=(125, 220, 138),
        obstacle_outline=(224, 160, 48),
        mowed=(120, 205, 110),
        trail=(74, 163, 255),
        mower_fill=(255, 82, 82),
        mower_outline=(255, 255, 255),
        dock=(129, 212, 250),
        planned_path=MINT,
        text=(230, 236, 240),
        notice=(224, 160, 48),
    ),
    # Transparent: takes on the colour of the card behind it.
    "natural": Palette(
        background=None,
        lawn_fill=(102, 187, 106),
        lawn_outline=(200, 230, 201),
        obstacle_outline=(161, 110, 60),
        mowed=(46, 125, 50),
        trail=(255, 235, 59),
        mower_fill=(255, 112, 67),
        mower_outline=(255, 255, 255),
        dock=(79, 195, 247),
        planned_path=MINT,
        text=(255, 255, 255),
        notice=(255, 213, 79),
    ),
    "light": Palette(
        background=(240, 244, 240),
        lawn_fill=(129, 199, 132),
        lawn_outline=(46, 125, 50),
        obstacle_outline=(121, 85, 72),
        mowed=(46, 125, 50),
        trail=(25, 118, 210),
        mower_fill=(229, 57, 53),
        mower_outline=(255, 255, 255),
        dock=(2, 119, 189),
        planned_path=MINT,
        text=(33, 43, 36),
        notice=(191, 96, 0),
    ),
    # High contrast for dark dashboards.
    "night": Palette(
        background=(8, 10, 12),
        lawn_fill=(27, 94, 32),
        lawn_outline=(105, 240, 174),
        obstacle_outline=(255, 171, 64),
        mowed=(102, 187, 106),
        trail=(0, 229, 255),
        mower_fill=(255, 145, 0),
        mower_outline=(255, 255, 255),
        dock=(79, 195, 247),
        planned_path=MINT,
        text=(224, 224, 224),
        notice=(255, 171, 64),
    ),
}
DEFAULT_PALETTE = "classic"

# Kept for callers/tests that refer to the default palette's colours.
_DEFAULT = PALETTES[DEFAULT_PALETTE]
BACKGROUND = _DEFAULT.background
LAWN_FILL = _DEFAULT.lawn_fill
LAWN_OUTLINE = _DEFAULT.lawn_outline
OBSTACLE_OUTLINE = _DEFAULT.obstacle_outline
TRAIL = _DEFAULT.trail
MOWER_FILL = _DEFAULT.mower_fill
MOWER_OUTLINE = _DEFAULT.mower_outline
TEXT = _DEFAULT.text


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
    index: int | None = None  # map_server's raw area index, as in high_level_status.current_area


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
        index = raw.get("index")
        areas.append(
            Area(
                name=str(raw.get("name") or ""),
                boundary=boundary,
                obstacles=obstacles,
                index=index if isinstance(index, int) and not isinstance(index, bool) else None,
            )
        )
    return areas


def parse_datum(payload: Any) -> tuple[float, float] | None:
    """(datum_lat, datum_lon) from a <prefix>/area_boundary payload, if usable."""
    if not isinstance(payload, dict):
        return None
    lat, lon = payload.get("datum_lat"), payload.get("datum_lon")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)):
        return None
    if lat == 0 and lon == 0:
        # The mower's "not set" value: a bridge that was never given the datum
        # publishes 0/0, and projecting a real fix through it lands ~6000 km away.
        return None
    return float(lat), float(lon)


@dataclass(frozen=True)
class Dock:
    """The charging dock in the map frame: metres from the datum, yaw in radians (CCW from east)."""

    x: float
    y: float
    yaw: float


def parse_dock(payload: Any) -> Dock | None:
    """The optional "dock" object of a <prefix>/area_boundary payload."""
    dock = payload.get("dock") if isinstance(payload, dict) else None
    if not isinstance(dock, dict):
        return None
    values = [dock.get(k) for k in ("x", "y", "yaw")]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return None
    return Dock(float(values[0]), float(values[1]), float(values[2]))


def parse_pose(payload: Any) -> tuple[Point, float] | None:
    """((x, y), yaw) from a <prefix>/pose payload: the mower's fused pose in the map frame."""
    if not isinstance(payload, dict):
        return None
    values = [payload.get(k) for k in ("x", "y", "yaw")]
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in values
    ):
        return None
    return (float(values[0]), float(values[1])), float(values[2])


# The GUI's own map view (gui/web/src/pages/MapPage.tsx SUBPATH_GAP_M) splits
# <prefix>/coverage_path the same way: it is a raw concatenation of segments, and
# consecutive points can be far apart where the plan jumps between segments that
# are not driven directly across.
PLANNED_PATH_GAP_M = 0.75


def parse_coverage_path(payload: Any) -> list[Point]:
    """Points of a <prefix>/coverage_path payload; malformed entries are dropped."""
    if not isinstance(payload, dict):
        return []
    return _points(payload.get("points"))


def split_planned_path(points: Sequence[Point]) -> list[list[Point]]:
    """`points` cut into runs at any gap wider than PLANNED_PATH_GAP_M."""
    runs: list[list[Point]] = []
    current: list[Point] = []
    for point in points:
        if current and math.hypot(point[0] - current[-1][0], point[1] - current[-1][1]) > PLANNED_PATH_GAP_M:
            runs.append(current)
            current = []
        current.append(point)
    if current:
        runs.append(current)
    return runs


# --- interpreting the other topics ------------------------------------------------------------


def is_blade_on(status_payload: Any) -> bool:
    """True while <prefix>/status reports the blade motor above cutting speed."""
    if not isinstance(status_payload, dict):
        return False
    rpm = status_payload.get("mower_motor_rpm")
    return isinstance(rpm, (int, float)) and not isinstance(rpm, bool) and rpm > BLADE_RPM_THRESHOLD


def rtk_marker_colour(rtk_payload: Any) -> RGB | None:
    """Mower marker colour from <prefix>/rtk_status: fixed green, float orange, else red.

    None when nothing usable has been received, so the palette's own colour applies.
    """
    if not isinstance(rtk_payload, dict) or not ({"fix_type", "rtk_mode"} & rtk_payload.keys()):
        return None
    if rtk_payload.get("fix_valid") is False:
        return RTK_NONE_COLOUR
    if rtk_payload.get("fix_type") == 3 or rtk_payload.get("rtk_mode") == 3:
        return RTK_FIXED_COLOUR
    if rtk_payload.get("fix_type") == 2 or rtk_payload.get("rtk_mode") == 2:
        return RTK_FLOAT_COLOUR
    return RTK_NONE_COLOUR


def active_area_index(high_level_status: Any) -> int | None:
    """The area being worked right now, or None (idle, docked, unknown)."""
    if not isinstance(high_level_status, dict):
        return None
    if high_level_status.get("state_name") not in ACTIVE_AREA_STATES:
        return None
    area = high_level_status.get("current_area")
    if isinstance(area, int) and not isinstance(area, bool) and area >= 0:
        return area
    return None


def is_charging(high_level_status: Any) -> bool:
    return isinstance(high_level_status, dict) and high_level_status.get("is_charging") is True


# --- trail ------------------------------------------------------------------------------------

TrailSample = tuple[float, float, bool]  # x, y, blade running while reaching this point


class TrailBuffer:
    """Bounded record of where the mower has been, thinned by distance."""

    def __init__(self, min_step_m: float = 0.15, max_points: int = 4000) -> None:
        self._min_step_m = min_step_m
        self._samples: deque[TrailSample] = deque(maxlen=max_points)

    def add(self, point: Point, blading: bool = False) -> bool:
        """Append `point` unless it is closer than min_step_m to the last one."""
        if self._samples:
            last = self._samples[-1]
            if math.hypot(point[0] - last[0], point[1] - last[1]) < self._min_step_m:
                return False
        self._samples.append((point[0], point[1], blading))
        return True

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    def points(self) -> list[Point]:
        return [(x, y) for x, y, _ in self._samples]

    def samples(self) -> list[TrailSample]:
        return list(self._samples)


def is_session_start(previous_state_name: str | None, state_name: str | None) -> bool:
    """True on the transition into UNDOCKING: a new mow session begins there."""
    return state_name == "UNDOCKING" and previous_state_name != "UNDOCKING"


def _as_sample(item: Sequence[float | bool]) -> TrailSample:
    blading = bool(item[2]) if len(item) > 2 else False
    return float(item[0]), float(item[1]), blading


def _runs(trail: Sequence[TrailSample]) -> Iterator[tuple[bool, list[Point]]]:
    """Consecutive stretches of the trail with the same blade state.

    The state of a segment is that of the point the mower had just reached.
    """
    run: list[Point] = []
    state = False
    for i in range(1, len(trail)):
        blading = trail[i][2]
        if run and blading != state:
            yield state, run
            run = []
        if not run:
            run = [(trail[i - 1][0], trail[i - 1][1])]
            state = blading
        run.append((trail[i][0], trail[i][1]))
    if run:
        yield state, run


# --- drawing ----------------------------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: fixed-size bitmap font only
        return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw, xy: Point, text: str, font: Any, fill: RGBA
) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (xy[0] - (left + right) / 2, xy[1] - (top + bottom) / 2), text, font=font, fill=fill
    )


def _encode(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _opaque(colour: RGB) -> RGBA:
    return (*colour, 255)


def _canvas(size: tuple[int, int], palette: Palette) -> Image.Image:
    fill = (0, 0, 0, 0) if palette.background is None else _opaque(palette.background)
    return Image.new("RGBA", size, fill)


def _finish(image: Image.Image, palette: Palette) -> bytes:
    """Encode; a map with a background is flattened to RGB, a transparent one keeps alpha."""
    return _encode(image if palette.background is None else image.convert("RGB"))


def _mix(a: RGB, b: RGB, amount: float) -> RGB:
    """`a` moved `amount` (0..1) of the way towards `b`."""
    return (
        round(a[0] + (b[0] - a[0]) * amount),
        round(a[1] + (b[1] - a[1]) * amount),
        round(a[2] + (b[2] - a[2]) * amount),
    )


def _readable_on(colour: RGB) -> RGB:
    """Black or white, whichever reads better on `colour`."""
    luminance = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
    return (0, 0, 0) if luminance > 140 else (255, 255, 255)


def _dimmed(colour: RGB, palette: Palette) -> RGBA:
    """`colour` as it appears in an area that is not being worked."""
    if palette.background is None:
        return (*colour, _DIM_ALPHA)
    return _opaque(_mix(colour, palette.background, _DIM_FACTOR))


def render_placeholder(
    message: str, width: int = 800, height: int = 400, palette: Palette = _DEFAULT
) -> bytes:
    image = _canvas((width, height), palette)
    _draw_centered_text(
        ImageDraw.Draw(image), (width / 2, height / 2), message, _font(22), _opaque(palette.text)
    )
    return _finish(image, palette)


def render_map(
    areas: Sequence[Area],
    position: Point | None,
    trail: Sequence[Sequence[float | bool]] = (),
    *,
    width: int = 800,
    min_height: int = 360,
    max_height: int = 1000,
    notice: str | None = None,
    palette: Palette = _DEFAULT,
    active_area: int | None = None,
    marker_colour: RGB | None = None,
    tool_width_m: float = DEFAULT_TOOL_WIDTH_M,
    heading: float | None = None,
    dock: Dock | None = None,
    planned_path: Sequence[Point] = (),
) -> bytes:
    """Render the lawn(s), the mower's trail and its current position as a PNG.

    North is up. The view fits every area plus the mower and its trail, so the
    mower stays visible even when it is outside the recorded boundary. `trail`
    items are (x, y) or (x, y, blade_on): stretches driven with the blade running
    are drawn as a stripe as wide as the cut, the rest as a thin line. When
    `active_area` names one of the areas, the others are dimmed. `heading` (radians,
    CCW from east) turns the mower's dot into an arrow; `dock` adds the charger.
    `planned_path` is drawn as a thin solid line (matching the GUI's own style), split
    into runs at PLANNED_PATH_GAP_M.
    """
    if not areas and position is None:
        return render_placeholder(
            "Waiting for map data", width=width, height=round(width * 0.5), palette=palette
        )

    samples = [_as_sample(item) for item in trail]
    every_point: list[Point] = [p for a in areas for p in a.boundary]
    every_point += [(x, y) for x, y, _ in samples]
    if position is not None:
        every_point.append(position)
    if dock is not None:
        every_point.append((dock.x, dock.y))
    every_point += list(planned_path)

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

    image = _canvas((width * ss, height * ss), palette)
    draw = ImageDraw.Draw(image)
    # Cutting an obstacle out of the lawn paints it in the background colour, or
    # fully transparent when the map has no background of its own.
    cut_out = (0, 0, 0, 0) if palette.background is None else _opaque(palette.background)

    highlight = active_area is not None and any(a.index == active_area for a in areas)
    for area in areas:
        dim = highlight and area.index != active_area

        def tone(colour: RGB, dim: bool = dim) -> RGBA:
            return _dimmed(colour, palette) if dim else _opaque(colour)

        outline = [px(p) for p in area.boundary]
        draw.polygon(outline, fill=tone(palette.lawn_fill))
        draw.line(
            [*outline, outline[0]], fill=tone(palette.lawn_outline), width=2 * ss, joint="curve"
        )
        for obstacle in area.obstacles:
            hole = [px(p) for p in obstacle]
            draw.polygon(hole, fill=cut_out)
            draw.line(
                [*hole, hole[0]], fill=tone(palette.obstacle_outline), width=2 * ss, joint="curve"
            )
        if area.name:
            centre = (
                sum(p[0] for p in outline) / len(outline),
                sum(p[1] for p in outline) / len(outline),
            )
            _draw_centered_text(draw, centre, area.name, _font(14 * ss), tone(palette.text))

    if dock is not None:
        _draw_dock(draw, px, dock, scale, ss, palette)

    for run in split_planned_path(planned_path):
        if len(run) >= 2:
            _draw_planned_path_line(draw, [px(p) for p in run], palette.planned_path, ss)

    runs = list(_runs(samples))
    stripe_px = max(3.0, tool_width_m * scale) * ss
    for blading, run in runs:
        if not blading:
            continue
        pts = [px(p) for p in run]
        draw.line(pts, fill=_opaque(palette.mowed), width=round(stripe_px), joint="curve")
        radius = stripe_px / 2
        for cx, cy in (pts[0], pts[-1]):  # round the ends of the stripe
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius), fill=_opaque(palette.mowed)
            )
    for blading, run in runs:
        if not blading:
            draw.line(
                [px(p) for p in run], fill=_opaque(palette.trail), width=2 * ss, joint="curve"
            )

    if position is not None:
        _draw_mower(draw, px(position), heading, ss, marker_colour or palette.mower_fill, palette)

    _draw_scale_bar(draw, scale, height, ss, palette)
    if notice:
        _draw_notice(draw, notice, ss, palette)

    return _finish(image.resize((width, height), Image.LANCZOS), palette)


def _draw_planned_path_line(draw: ImageDraw.ImageDraw, points: list[Point], colour: RGB, ss: int) -> None:
    """A thin solid line, matching the robot GUI's own coverage-path style
    (gui/web/src/types/map.ts PathFeature: a plain stroke, no dash pattern). Slightly
    wider than the trail (2*ss): against the lawn's own greens, a thinner mint line
    loses too much of its colour to the final LANCZOS downsize."""
    draw.line(points, fill=_opaque(colour), width=max(3 * ss, 2), joint="curve")


def _draw_mower(
    draw: ImageDraw.ImageDraw,
    centre: Point,
    heading: float | None,
    ss: int,
    fill: RGB,
    palette: Palette,
) -> None:
    """The mower: an arrow pointing where it faces, or a dot when the heading is unknown."""
    cx, cy = centre
    if heading is None:
        radius = 8 * ss
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=_opaque(fill),
            outline=_opaque(palette.mower_outline),
            width=2 * ss,
        )
        return
    # Screen y grows downwards, the map's north upwards.
    dx, dy = math.cos(heading), -math.sin(heading)
    nx, ny = -dy, dx  # to the mower's left on screen
    tip = 13 * ss
    back = 8 * ss
    half_width = 9 * ss
    outline = [
        (cx + dx * tip, cy + dy * tip),
        (cx - dx * back + nx * half_width, cy - dy * back + ny * half_width),
        (cx - dx * back * 0.35, cy - dy * back * 0.35),
        (cx - dx * back - nx * half_width, cy - dy * back - ny * half_width),
    ]
    draw.polygon(outline, fill=_opaque(fill))
    draw.line(
        [*outline, outline[0]], fill=_opaque(palette.mower_outline), width=2 * ss, joint="curve"
    )


def _draw_dock(
    draw: ImageDraw.ImageDraw, px: Any, dock: Dock, scale: float, ss: int, palette: Palette
) -> None:
    """The charging dock: a rounded plate with a tick on the side the mower drives in from.

    The dock pose's yaw is the heading the mower has when docked, so the plate
    extends ahead of that point.
    """
    dx, dy = math.cos(dock.yaw), -math.sin(dock.yaw)
    nx, ny = -dy, dx
    length = max(0.55 * scale, 22) * ss
    width = max(0.4 * scale, 16) * ss
    cx, cy = px((dock.x, dock.y))
    # Plate centred 40 % of its length ahead of the docked position.
    ox, oy = cx + dx * length * 0.4, cy + dy * length * 0.4
    corners = [
        (ox + dx * length / 2 + nx * width / 2, oy + dy * length / 2 + ny * width / 2),
        (ox + dx * length / 2 - nx * width / 2, oy + dy * length / 2 - ny * width / 2),
        (ox - dx * length / 2 - nx * width / 2, oy - dy * length / 2 - ny * width / 2),
        (ox - dx * length / 2 + nx * width / 2, oy - dy * length / 2 + ny * width / 2),
    ]
    draw.polygon(corners, fill=_opaque(palette.dock))
    draw.line(
        [*corners, corners[0]], fill=_opaque(palette.mower_outline), width=2 * ss, joint="curve"
    )
    if length / ss >= 44:  # only when the plate is big enough to carry the word
        _draw_centered_text(
            draw, (ox, oy), "DOCK", _font(10 * ss), _opaque(_readable_on(palette.dock))
        )


def _draw_notice(draw: ImageDraw.ImageDraw, text: str, ss: int, palette: Palette) -> None:
    """A one-line status message, top-left, on a dark strip so it stays legible."""
    font = _font(13 * ss)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad = 6 * ss
    x0, y0 = 10 * ss, 10 * ss
    strip = (0, 0, 0, 150) if palette.background is None else _opaque(palette.background)
    draw.rectangle(
        (x0, y0, x0 + (right - left) + 2 * pad, y0 + (bottom - top) + 2 * pad), fill=strip
    )
    draw.text((x0 + pad - left, y0 + pad - top), text, font=font, fill=_opaque(palette.notice))


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw, scale: float, height: int, ss: int, palette: Palette
) -> None:
    """A simple metric scale bar, bottom-left, at most 200 px wide."""
    length_m = 1
    for step in _SCALE_BAR_STEPS_M:
        if step * scale <= 200:
            length_m = step
    colour = _opaque(palette.text)
    x0, y0 = 16 * ss, (height - 16) * ss
    x1 = x0 + length_m * scale * ss
    draw.line([(x0, y0), (x1, y0)], fill=colour, width=2 * ss)
    draw.line([(x0, y0 - 4 * ss), (x0, y0 + 4 * ss)], fill=colour, width=2 * ss)
    draw.line([(x1, y0 - 4 * ss), (x1, y0 + 4 * ss)], fill=colour, width=2 * ss)
    draw.text((x0, y0 - 22 * ss), f"{length_m} m", font=_font(12 * ss), fill=colour)
