"""Tests for the pure map renderer (no Home Assistant needed)."""
import math
from io import BytesIO

from PIL import Image

import pytest

from custom_components.mowglinext.map_render import (
    BACKGROUND,
    LAWN_FILL,
    MOWER_FILL,
    PALETTES,
    RTK_FIXED_COLOUR,
    RTK_FLOAT_COLOUR,
    RTK_NONE_COLOUR,
    Area,
    Dock,
    TrailBuffer,
    active_area_index,
    is_blade_on,
    is_charging,
    is_session_start,
    parse_areas,
    parse_datum,
    parse_coverage_path,
    parse_dock,
    parse_pose,
    rotate_heading,
    rotate_point,
    split_planned_path,
    render_map,
    render_placeholder,
    rtk_marker_colour,
    to_enu,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def _open(png: bytes) -> Image.Image:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    return Image.open(BytesIO(png)).convert("RGB")


def _close(pixel: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 12) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(pixel, expected))


# --- projection: must match mowgli_interfaces/wgs84_projection.hpp ---------------------------


def test_north_offset_uses_metres_per_degree() -> None:
    east, north = to_enu(52.001, 4.0, 52.0, 4.0)
    assert east == 0.0
    assert math.isclose(north, 111.3194908, rel_tol=1e-9)


def test_east_offset_is_scaled_by_cos_of_datum_latitude() -> None:
    east, north = to_enu(52.0, 4.001, 52.0, 4.0)
    assert north == 0.0
    assert math.isclose(east, 111.3194908 * math.cos(math.radians(52.0)), rel_tol=1e-9)


# --- parsing ---------------------------------------------------------------------------------


def test_parse_areas_keeps_valid_and_drops_malformed() -> None:
    payload = {
        "areas": [
            {"index": 0, "name": "Front", "boundary": [[0, 0], [4, 0], [4, 3]], "obstacles": []},
            {"index": 1, "name": "TooShort", "boundary": [[0, 0], [1, 1]]},
            {"index": 2, "name": "BadPoints", "boundary": [[0, 0], ["x", 1], [2, math.nan]]},
            "not a dict",
            {
                "index": 3,
                "name": "Back",
                "boundary": [[0, 0], [5, 0], [5, 5]],
                "obstacles": [[[1, 1], [2, 1], [2, 2]], [[0, 0], [1, 1]]],
            },
        ]
    }
    areas = parse_areas(payload)
    assert [a.name for a in areas] == ["Front", "Back"]
    assert areas[1].obstacles == [[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]]


def test_parse_areas_tolerates_garbage() -> None:
    assert parse_areas(None) == []
    assert parse_areas([]) == []
    assert parse_areas({"areas": None}) == []


def test_parse_datum() -> None:
    assert parse_datum({"datum_lat": 52.1, "datum_lon": 4.5}) == (52.1, 4.5)
    assert parse_datum({"datum_lat": 52.1}) is None
    assert parse_datum({"datum_lat": "x", "datum_lon": 4.5}) is None
    assert parse_datum(None) is None


def test_zero_datum_means_not_set() -> None:
    # A bridge that was never given the datum publishes 0/0; using it would put a real
    # fix ~6000 km from the lawn.
    assert parse_datum({"datum_lat": 0.0, "datum_lon": 0.0}) is None
    assert parse_datum({"datum_lat": 0.0, "datum_lon": 4.5}) == (0.0, 4.5)


# --- trail -----------------------------------------------------------------------------------


def test_trail_thins_by_distance() -> None:
    trail = TrailBuffer(min_step_m=0.5)
    assert trail.add((0.0, 0.0))
    assert not trail.add((0.2, 0.0))
    assert trail.add((0.6, 0.0))
    assert len(trail) == 2


def test_trail_is_bounded_and_clearable() -> None:
    trail = TrailBuffer(min_step_m=0.0, max_points=3)
    for i in range(10):
        trail.add((float(i), 0.0))
    assert trail.points() == [(7.0, 0.0), (8.0, 0.0), (9.0, 0.0)]
    trail.clear()
    assert len(trail) == 0


def test_session_starts_on_entering_undocking_only() -> None:
    assert is_session_start("IDLE", "UNDOCKING")
    assert is_session_start(None, "UNDOCKING")
    assert not is_session_start("UNDOCKING", "UNDOCKING")
    assert not is_session_start("UNDOCKING", "MOWING")
    assert not is_session_start("IDLE", "IDLE")


# --- rendering -------------------------------------------------------------------------------


def test_nothing_to_draw_gives_a_placeholder() -> None:
    image = _open(render_map([], None))
    assert image.size == (800, 400)


def test_placeholder_size() -> None:
    assert _open(render_placeholder("hello", width=300, height=120)).size == (300, 120)


def test_lawn_is_filled_and_mower_is_north_of_centre() -> None:
    # 10 m square, mower at (5, 8): with the 1.5 m margin the view is 13 m wide, so
    # 61.5 px/m -> lawn centre (5, 5) is at (400, 400) and the mower at about (400, 215).
    area = Area(name="", boundary=SQUARE)
    image = _open(render_map([area], (5.0, 8.0)))
    assert image.size == (800, 800)
    assert _close(image.getpixel((400, 215)), MOWER_FILL)
    assert _close(image.getpixel((400, 523)), LAWN_FILL)  # lawn at (5, 3), clear of marker
    assert _close(image.getpixel((5, 5)), BACKGROUND)  # outside the boundary


def test_obstacle_is_cut_out_of_the_lawn() -> None:
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    image = _open(render_map([Area(name="", boundary=SQUARE, obstacles=[hole])], None))
    assert _close(image.getpixel((400, 400)), BACKGROUND)


def _has_colour(image: Image.Image, colour: tuple[int, int, int]) -> bool:
    # A full scan, not a coarse grid: a dashed line (the planned path) can be thin enough
    # that a stride misses every dash entirely.
    return any(_close(pixel, colour, tol=6) for pixel in image.getdata())


def test_view_grows_to_keep_the_mower_visible_outside_the_boundary() -> None:
    # A mower 30 m north of a 10 m lawn: both must still be in the picture.
    image = _open(render_map([Area(name="", boundary=SQUARE)], (5.0, 40.0)))
    assert _has_colour(image, MOWER_FILL)
    assert _has_colour(image, LAWN_FILL)
    assert image.size[1] <= 1000


def test_position_only_still_renders() -> None:
    image = _open(render_map([], (2.0, 3.0)))
    assert image.size[0] == 800


def test_notice_is_drawn_on_the_image() -> None:
    area = Area(name="", boundary=SQUARE)
    plain = render_map([area], None)
    with_notice = render_map([area], None, notice="Waiting for a GPS fix")
    assert plain != with_notice
    assert _open(with_notice).size == _open(plain).size


# --- reading the other topics --------------------------------------------------------------


def test_blade_is_on_only_above_cutting_speed() -> None:
    assert is_blade_on({"mower_motor_rpm": 3200.0})
    assert not is_blade_on({"mower_motor_rpm": 0.0})
    assert not is_blade_on({"mower_motor_rpm": 120.0})  # spinning up / coasting down
    assert not is_blade_on({"mower_motor_rpm": None})
    assert not is_blade_on({"mower_motor_rpm": True})
    assert not is_blade_on({})
    assert not is_blade_on(None)


def test_marker_colour_follows_rtk_quality() -> None:
    assert rtk_marker_colour({"fix_type": 3, "rtk_mode": 3, "fix_valid": True}) == RTK_FIXED_COLOUR
    assert rtk_marker_colour({"fix_type": 2, "rtk_mode": 2, "fix_valid": True}) == RTK_FLOAT_COLOUR
    assert rtk_marker_colour({"fix_type": 1, "rtk_mode": 1, "fix_valid": True}) == RTK_NONE_COLOUR
    assert rtk_marker_colour({"fix_type": 0, "rtk_mode": 1, "fix_valid": False}) == RTK_NONE_COLOUR


def test_invalid_fix_overrides_a_stale_fixed_type() -> None:
    assert rtk_marker_colour({"fix_type": 3, "rtk_mode": 3, "fix_valid": False}) == RTK_NONE_COLOUR


def test_marker_colour_is_none_without_rtk_data() -> None:
    assert rtk_marker_colour({}) is None
    assert rtk_marker_colour(None) is None
    assert rtk_marker_colour({"quality_percent": 100}) is None


def test_active_area_only_while_working_an_area() -> None:
    assert active_area_index({"state_name": "MOWING", "current_area": 2}) == 2
    assert active_area_index({"state_name": "TRANSIT", "current_area": 0}) == 0
    assert active_area_index({"state_name": "IDLE", "current_area": 2}) is None
    assert active_area_index({"state_name": "MOWING", "current_area": -1}) is None
    assert active_area_index({"state_name": "MOWING"}) is None
    assert active_area_index(None) is None


def test_is_charging() -> None:
    assert is_charging({"is_charging": True})
    assert not is_charging({"is_charging": False})
    assert not is_charging({})
    assert not is_charging(None)


def test_parse_areas_keeps_the_index() -> None:
    areas = parse_areas(
        {"areas": [{"index": 3, "name": "Back", "boundary": [[0, 0], [1, 0], [1, 1]]}]}
    )
    assert areas[0].index == 3
    no_index = parse_areas({"areas": [{"name": "X", "boundary": [[0, 0], [1, 0], [1, 1]]}]})
    assert no_index[0].index is None


# --- trail with the blade state ------------------------------------------------------------


def test_trail_remembers_the_blade_state_per_point() -> None:
    trail = TrailBuffer(min_step_m=0.5)
    trail.add((0.0, 0.0), blading=False)
    trail.add((1.0, 0.0), blading=True)
    trail.add((2.0, 0.0), blading=True)
    assert trail.samples() == [(0.0, 0.0, False), (1.0, 0.0, True), (2.0, 0.0, True)]
    assert trail.points() == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]


# --- what gets drawn -----------------------------------------------------------------------

BLADE_TRAIL = [(1.0, 5.0, False), (2.0, 5.0, False), (5.0, 5.0, True), (8.0, 5.0, True)]


def test_blade_on_stretches_are_a_stripe_and_the_rest_a_thin_line() -> None:
    palette = PALETTES["classic"]
    image = _open(render_map([Area(name="", boundary=SQUARE)], None, BLADE_TRAIL))
    assert _has_colour(image, palette.mowed)
    assert _has_colour(image, palette.trail)


def test_a_trail_without_blade_flags_has_no_stripe() -> None:
    palette = PALETTES["classic"]
    image = _open(render_map([Area(name="", boundary=SQUARE)], None, [(1.0, 5.0), (8.0, 5.0)]))
    assert _has_colour(image, palette.trail)
    assert not _has_colour(image, palette.mowed)


def test_inactive_areas_are_dimmed() -> None:
    a = Area(name="", boundary=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)], index=0)
    b = Area(name="", boundary=[(14.0, 0.0), (24.0, 0.0), (24.0, 10.0), (14.0, 10.0)], index=1)
    # 24 m wide + margins: 800 px / (24 + 2*1.92) ~ 28.7 px/m; centres at x ~ 5 and 19 m.
    plain = _open(render_map([a, b], None))
    focused = _open(render_map([a, b], None, active_area=0))
    y = plain.size[1] // 2
    left = (int((5 + 1.92) * 28.7), y)
    right = (int((19 + 1.92) * 28.7), y)
    assert _close(plain.getpixel(left), LAWN_FILL) and _close(plain.getpixel(right), LAWN_FILL)
    assert _close(focused.getpixel(left), LAWN_FILL)
    assert not _close(focused.getpixel(right), LAWN_FILL, tol=8)


def test_an_active_area_that_is_not_on_the_map_dims_nothing() -> None:
    a = Area(name="", boundary=SQUARE, index=0)
    assert render_map([a], None, active_area=7) == render_map([a], None)


def test_marker_colour_override_replaces_the_palette_colour() -> None:
    image = _open(render_map([Area(name="", boundary=SQUARE)], (5.0, 8.0), marker_colour=RTK_FIXED_COLOUR))
    assert _has_colour(image, RTK_FIXED_COLOUR)
    assert not _has_colour(image, MOWER_FILL)


@pytest.mark.parametrize("name", list(PALETTES))
def test_every_palette_renders(name: str) -> None:
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    png = render_map(
        [Area(name="tuin", boundary=SQUARE, obstacles=[hole], index=0)],
        (5.0, 8.0),
        BLADE_TRAIL,
        palette=PALETTES[name],
        active_area=0,
        notice="hello",
    )
    assert _open(png).size[0] == 800


def test_the_transparent_palette_has_alpha_and_cut_out_obstacles() -> None:
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    png = render_map(
        [Area(name="", boundary=SQUARE, obstacles=[hole])], None, palette=PALETTES["natural"]
    )
    image = Image.open(BytesIO(png))
    assert image.mode == "RGBA"
    assert image.getpixel((3, 3))[3] == 0  # background outside the lawn
    assert image.getpixel((400, 400))[3] == 0  # the obstacle is a hole
    assert image.getpixel((400, 523))[3] == 255  # lawn


# --- dock, pose, heading ---------------------------------------------------------------------


def test_parse_dock() -> None:
    assert parse_dock({"dock": {"x": 1.5, "y": -2.0, "yaw": 0.5}}) == Dock(1.5, -2.0, 0.5)
    assert parse_dock({"areas": []}) is None  # no dock calibrated: the key is omitted
    assert parse_dock({"dock": {"x": 1.5, "y": "?", "yaw": 0.5}}) is None
    assert parse_dock({"dock": {"x": math.nan, "y": 0, "yaw": 0}}) is None
    assert parse_dock(None) is None


def test_parse_pose() -> None:
    assert parse_pose({"x": 1.0, "y": 2.0, "yaw": 0.5}) == ((1.0, 2.0), 0.5)
    assert parse_pose({"x": 1.0, "y": 2.0}) is None
    assert parse_pose({"x": True, "y": 2.0, "yaw": 0.5}) is None
    assert parse_pose({"x": math.inf, "y": 2.0, "yaw": 0.5}) is None
    assert parse_pose(None) is None


def test_the_dock_is_drawn() -> None:
    palette = PALETTES["classic"]
    plain = _open(render_map([Area(name="", boundary=SQUARE)], None))
    with_dock = _open(
        render_map([Area(name="", boundary=SQUARE)], None, dock=Dock(9.0, 5.0, math.pi))
    )
    assert not _has_colour(plain, palette.dock)
    assert _has_colour(with_dock, palette.dock)


def test_the_view_grows_to_include_a_dock_outside_the_lawn() -> None:
    palette = PALETTES["classic"]
    image = _open(
        render_map([Area(name="", boundary=SQUARE)], None, dock=Dock(30.0, 5.0, math.pi))
    )
    assert _has_colour(image, palette.dock)
    assert _has_colour(image, palette.lawn_fill)


def test_the_arrow_points_where_the_mower_faces() -> None:
    # 10 m lawn + 1.5 m margin: 61.5 px/m, the lawn centre (5, 5) is at (400, 400).
    def marker_pixel(yaw: float, dx: int, dy: int) -> tuple[int, int, int]:
        image = _open(
            render_map(
                [Area(name="", boundary=SQUARE)], (5.0, 5.0), heading=yaw, marker_colour=(0, 200, 83)
            )
        )
        return image.getpixel((400 + dx, 400 + dy))

    green = (0, 200, 83)
    # 5 px ahead of the centre is inside the arrow; 12 px behind it is outside.
    for yaw, ahead, behind in (
        (math.pi / 2, (0, -5), (0, 12)),  # facing north
        (0.0, (5, 0), (-12, 0)),  # facing east
        (math.pi, (-5, 0), (12, 0)),  # facing west
        (-math.pi / 2, (0, 5), (0, -12)),  # facing south
    ):
        assert _close(marker_pixel(yaw, *ahead), green), yaw
        assert not _close(marker_pixel(yaw, *behind), green), yaw


def test_without_a_heading_the_marker_is_a_dot() -> None:
    image = _open(render_map([Area(name="", boundary=SQUARE)], (5.0, 5.0), marker_colour=(0, 200, 83)))
    green = (0, 200, 83)
    for dx, dy in ((0, -4), (0, 4), (-4, 0), (4, 0)):
        assert _close(image.getpixel((400 + dx, 400 + dy)), green)


# --- planned coverage path ---------------------------------------------------------------------


def test_parse_coverage_path() -> None:
    assert parse_coverage_path({"points": [[1.0, 2.0], [3.0, 4.0]]}) == [(1.0, 2.0), (3.0, 4.0)]
    assert parse_coverage_path({"points": []}) == []
    assert parse_coverage_path(None) == []
    assert parse_coverage_path({}) == []


def test_split_planned_path_cuts_at_a_wide_gap() -> None:
    points = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (5.0, 0.0), (5.5, 0.0)]
    assert split_planned_path(points) == [[(0.0, 0.0), (0.5, 0.0), (1.0, 0.0)], [(5.0, 0.0), (5.5, 0.0)]]


def test_split_planned_path_edge_cases() -> None:
    assert split_planned_path([]) == []
    assert split_planned_path([(1.0, 1.0)]) == [[(1.0, 1.0)]]


def _dense_line(p0: tuple[float, float], p1: tuple[float, float], step_m: float = 0.2) -> list[tuple[float, float]]:
    """A straight line from p0 to p1, sampled densely -- a real plan's points are close
    together (F2C waypoints), so PLANNED_PATH_GAP_M (0.75 m) never splits WITHIN a run."""
    length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    steps = max(2, int(length / step_m) + 1)
    return [
        (p0[0] + (p1[0] - p0[0]) * t / (steps - 1), p0[1] + (p1[1] - p0[1]) * t / (steps - 1))
        for t in range(steps)
    ]


def test_the_planned_path_is_drawn() -> None:
    palette = PALETTES["classic"]
    plain = _open(render_map([Area(name="", boundary=SQUARE)], None))
    planned = _open(
        render_map(
            [Area(name="", boundary=SQUARE)], None, planned_path=_dense_line((1.0, 5.0), (8.0, 5.0))
        )
    )
    assert not _has_colour(plain, palette.planned_path)
    assert _has_colour(planned, palette.planned_path)


def test_a_gap_in_the_planned_path_is_not_drawn_as_one_line() -> None:
    # Two short, densely-sampled runs far apart: the corridor between them must stay
    # background, not a single line connecting them (split_planned_path cuts there).
    palette = PALETTES["classic"]
    path = _dense_line((1.0, 5.0), (2.0, 5.0)) + _dense_line((28.0, 5.0), (29.0, 5.0))
    image = _open(
        render_map(
            [Area(name="", boundary=[(0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (0.0, 10.0)])],
            None,
            planned_path=path,
        )
    )
    midpoint_px = image.size[0] // 2
    row = [image.getpixel((x, image.size[1] // 2)) for x in range(midpoint_px - 20, midpoint_px + 20)]
    assert not any(_close(p, palette.planned_path, tol=6) for p in row)


def test_the_view_grows_to_include_the_planned_path() -> None:
    palette = PALETTES["classic"]
    image = _open(
        render_map(
            [Area(name="", boundary=SQUARE)],
            None,
            planned_path=_dense_line((20.0, 5.0), (25.0, 5.0)),
        )
    )
    assert _has_colour(image, palette.planned_path)
    assert _has_colour(image, palette.lawn_fill)


# --- map rotation (matches the GUI's own "Map Rotation" / Mapbox bearing) --------------------


def test_zero_rotation_is_a_no_op() -> None:
    assert rotate_point((3.0, -4.0), 0.0) == (3.0, -4.0)
    assert rotate_heading(1.23, 0.0) == 1.23


def test_north_point_moves_to_the_left_at_bearing_90() -> None:
    # bearing=90 means "facing east": east is at the top of the image, so north
    # (90 degrees left of east) ends up on the left.
    x, y = rotate_point((0.0, 1.0), 90.0)
    assert x == pytest.approx(-1.0, abs=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)


def test_east_point_moves_to_the_top_at_bearing_90() -> None:
    x, y = rotate_point((1.0, 0.0), 90.0)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(1.0, abs=1e-9)


def test_rotation_preserves_distance() -> None:
    p = rotate_point((3.0, 4.0), 37.0)
    assert math.hypot(*p) == pytest.approx(5.0)


def test_heading_rotates_the_same_way_as_position() -> None:
    # A mower facing north (heading = pi/2, CCW from east) at bearing=90 should now
    # face the same direction its rotated position vector points: west.
    heading = rotate_heading(math.pi / 2, 90.0)
    x, y = math.cos(heading), math.sin(heading)
    assert (x, y) == pytest.approx((-1.0, 0.0), abs=1e-9)


def test_rendered_map_rotates_with_the_scene() -> None:
    # A mower due EAST of the lawn's centre renders on the RIGHT at bearing=0 (normal
    # north-up). At bearing=90 (facing east, so east is "up"), that same physical
    # point must render at the TOP instead -- the scene has visibly rotated.
    area = Area(name="", boundary=SQUARE)  # centred on (5, 5)
    east_of_centre = (20.0, 5.0)
    upright = _open(render_map([area], east_of_centre))
    rotated = _open(render_map([area], east_of_centre, rotation_deg=90.0))

    def marker_centroid(image: Image.Image) -> tuple[float, float]:
        xs, ys = [], []
        for x in range(0, image.size[0], 2):
            for y in range(0, image.size[1], 2):
                if _close(image.getpixel((x, y)), MOWER_FILL):
                    xs.append(x)
                    ys.append(y)
        assert xs, "marker not found"
        return sum(xs) / len(xs), sum(ys) / len(ys)

    ux, _uy = marker_centroid(upright)
    _rx, ry = marker_centroid(rotated)
    assert ux > upright.size[0] * 0.7  # right side
    assert ry < rotated.size[1] * 0.3  # top


def test_rotating_a_dock_rotates_its_heading_too() -> None:
    # Sanity check: rotating a dock (position + heading together) actually changes
    # the rendered image, rather than the heading rotation silently doing nothing.
    plain = _open(render_map([Area(name="", boundary=SQUARE)], None, dock=Dock(5.0, 20.0, 0.0)))
    rotated = _open(
        render_map(
            [Area(name="", boundary=SQUARE)], None, dock=Dock(5.0, 20.0, 0.0), rotation_deg=90.0
        )
    )
    assert plain.tobytes() != rotated.tobytes()
