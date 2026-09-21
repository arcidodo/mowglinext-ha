"""Tests for the pure map renderer (no Home Assistant needed)."""
import math
from io import BytesIO

from PIL import Image

from custom_components.mowglinext.map_render import (
    BACKGROUND,
    LAWN_FILL,
    MOWER_FILL,
    Area,
    TrailBuffer,
    is_session_start,
    parse_areas,
    parse_datum,
    render_map,
    render_placeholder,
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
    width, height = image.size
    return any(
        _close(image.getpixel((x, y)), colour, tol=6)
        for x in range(0, width, 4)
        for y in range(0, height, 4)
    )


def test_view_grows_to_keep_the_mower_visible_outside_the_boundary() -> None:
    # A mower 30 m north of a 10 m lawn: both must still be in the picture.
    image = _open(render_map([Area(name="", boundary=SQUARE)], (5.0, 40.0)))
    assert _has_colour(image, MOWER_FILL)
    assert _has_colour(image, LAWN_FILL)
    assert image.size[1] <= 1000


def test_position_only_still_renders() -> None:
    image = _open(render_map([], (2.0, 3.0)))
    assert image.size[0] == 800
