"""Tests for the map camera entity (<prefix>/area_boundary + <prefix>/gps)."""
import json
from io import BytesIO

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from PIL import Image
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.mowglinext import camera as camera_module
from custom_components.mowglinext.const import CONF_TOPIC_PREFIX, DOMAIN

CAMERA = "camera.mowgli_map"
DATUM_LAT, DATUM_LON = 52.0, 4.0
NORTH_1M_DEG = 1.0 / 111319.4908  # one metre north, in degrees of latitude

AREA_BOUNDARY = {
    "datum_lat": DATUM_LAT,
    "datum_lon": DATUM_LON,
    "areas": [
        {
            "index": 0,
            "name": "Front Lawn",
            "boundary": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "obstacles": [],
        }
    ],
}


@pytest.fixture(autouse=True)
def _write_state_every_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 5 s attribute-write throttle would hide updates from a fast test."""
    monkeypatch.setattr(camera_module, "STATE_WRITE_MIN_INTERVAL_S", 0.0)


async def _setup_entry(hass: HomeAssistant, mqtt_mock) -> ConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TOPIC_PREFIX: "mowgli"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fire(hass: HomeAssistant, topic: str, payload: dict | str) -> None:
    async_fire_mqtt_message(
        hass, f"mowgli/{topic}", payload if isinstance(payload, str) else json.dumps(payload)
    )
    await hass.async_block_till_done()


async def _make_available(hass: HomeAssistant, state_name: str = "IDLE") -> None:
    await _fire(hass, "available", "online")
    await _fire(hass, "high_level_status", {"state": 1, "state_name": state_name})


async def _fix(hass: HomeAssistant, metres_north: float, metres_east: float = 0.0) -> None:
    await _fire(
        hass,
        "gps",
        {
            "latitude": DATUM_LAT + metres_north * NORTH_1M_DEG,
            "longitude": DATUM_LON + metres_east * NORTH_1M_DEG / 0.6156614753,  # cos(52 deg)
            "altitude": 1.0,
            "status": 0,
        },
    )


def _png(image_bytes: bytes) -> Image.Image:
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    return Image.open(BytesIO(image_bytes))


async def test_camera_serves_a_png_map(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fire(hass, "area_boundary", AREA_BOUNDARY)
    await _fix(hass, 5.0, 5.0)

    state = hass.states.get(CAMERA)
    assert state is not None
    assert state.attributes["entity_picture"]

    image = await async_get_image(hass, CAMERA)
    assert image.content_type == "image/png"
    assert _png(image.content).size[0] == 800


async def test_position_is_projected_through_the_datum(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fire(hass, "area_boundary", AREA_BOUNDARY)
    await _fix(hass, 5.0, 5.0)

    attrs = hass.states.get(CAMERA).attributes
    assert attrs["position_y"] == pytest.approx(5.0, abs=0.01)
    assert attrs["position_x"] == pytest.approx(5.0, abs=0.01)


async def test_without_a_datum_the_map_is_a_placeholder(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fix(hass, 5.0)  # a fix, but no area_boundary yet -> no datum to place it

    attrs = hass.states.get(CAMERA).attributes
    assert "position_x" not in attrs
    image = await async_get_image(hass, CAMERA)
    assert _png(image.content).size == (800, 400)


async def test_position_appears_once_the_datum_arrives(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fix(hass, 3.0)
    await _fire(hass, "area_boundary", AREA_BOUNDARY)

    assert hass.states.get(CAMERA).attributes["position_y"] == pytest.approx(3.0, abs=0.01)


async def test_no_fix_is_not_added_to_the_trail(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fire(hass, "area_boundary", AREA_BOUNDARY)
    await _fix(hass, 1.0)
    await _fire(hass, "gps", {"latitude": 0.0, "longitude": 0.0, "status": -1})

    attrs = hass.states.get(CAMERA).attributes
    assert attrs["trail_points"] == 1
    assert attrs["position_y"] == pytest.approx(1.0, abs=0.01)


async def test_trail_grows_while_moving_and_resets_on_a_new_session(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass, "IDLE")
    await _fire(hass, "area_boundary", AREA_BOUNDARY)
    for metres in (1.0, 2.0, 3.0, 4.0):
        await _fix(hass, metres)
    assert hass.states.get(CAMERA).attributes["trail_points"] == 4

    # Sitting still adds nothing (thinned by distance).
    await _fix(hass, 4.0)
    assert hass.states.get(CAMERA).attributes["trail_points"] == 4

    # Entering UNDOCKING starts a fresh session -> the trail is cleared.
    await _fire(hass, "high_level_status", {"state": 2, "state_name": "UNDOCKING"})
    assert hass.states.get(CAMERA).attributes["trail_points"] == 0

    # Staying in UNDOCKING / moving on to MOWING does not clear it again.
    await _fix(hass, 5.0)
    await _fire(hass, "high_level_status", {"state": 2, "state_name": "MOWING"})
    assert hass.states.get(CAMERA).attributes["trail_points"] == 1


async def test_unavailable_when_the_mower_is_offline(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    assert hass.states.get(CAMERA).state != "unavailable"

    await _fire(hass, "available", "offline")
    assert hass.states.get(CAMERA).state == "unavailable"


async def test_zero_datum_still_shows_the_lawn_but_no_position(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fire(hass, "area_boundary", {**AREA_BOUNDARY, "datum_lat": 0.0, "datum_lon": 0.0})
    await _fix(hass, 5.0)  # a real fix, projected through datum 0/0 it would be ~6000 km away

    assert "position_x" not in hass.states.get(CAMERA).attributes
    image = await async_get_image(hass, CAMERA)
    # The lawn alone (10 m square -> 800x800), not a continent-sized empty map.
    assert _png(image.content).size == (800, 800)


async def test_an_implausibly_distant_fix_is_not_plotted(hass: HomeAssistant, mqtt_mock) -> None:
    await _setup_entry(hass, mqtt_mock)
    await _make_available(hass)
    await _fire(hass, "area_boundary", AREA_BOUNDARY)
    await _fix(hass, 5.0)
    await _fix(hass, 200_000.0)  # 200 km north of the datum

    attrs = hass.states.get(CAMERA).attributes
    assert attrs["position_y"] == pytest.approx(5.0, abs=0.01)
    assert attrs["trail_points"] == 1
