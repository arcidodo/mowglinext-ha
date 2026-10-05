"""Unit tests for the pure schedule helpers (schedules.py)."""
import pytest

from custom_components.mowglinext import schedules

_AREAS = [{"index": 0, "name": "Voor", "id": 11}, {"index": 2, "name": "Achter", "id": 7}]


def _sched(time: str, days: list[int], enabled: bool = True, sid: str = "x") -> dict:
    return {"id": sid, "time": time, "daysOfWeek": days, "enabled": enabled}


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        (_sched("06:00", [1]), _sched("06:59", [1], sid="y"), True),
        (_sched("06:00", [1]), _sched("07:00", [1], sid="y"), False),  # exactly 60 min
        (_sched("06:00", [1]), _sched("06:30", [2], sid="y"), False),  # other weekday
        (_sched("23:30", [1]), _sched("00:15", [2], sid="y"), True),  # across midnight
        (_sched("23:30", [6]), _sched("00:15", [0], sid="y"), True),  # Sat -> Sun wrap
        (_sched("06:00", [1]), _sched("06:30", [1], enabled=False, sid="y"), False),
        (_sched("06:00", [1]), _sched("06:30", [1], sid="x"), False),  # itself
    ],
)
def test_find_overlap_mirrors_the_mower_rule(a: dict, b: dict, overlap: bool) -> None:
    assert (schedules.find_overlap(a, [b]) is not None) is overlap


def test_a_disabled_candidate_never_overlaps() -> None:
    assert schedules.find_overlap(_sched("06:00", [1], enabled=False), [_sched("06:00", [1], sid="y")]) is None


@pytest.mark.parametrize("value", [None, "", "  ", "all", "All areas", "0", 0])
def test_resolve_area_all(value) -> None:
    assert schedules.resolve_area(value, _AREAS) == (0, None)


def test_resolve_area_by_name_returns_the_stable_id() -> None:
    assert schedules.resolve_area("Achter", _AREAS) == (7, "Achter")


def test_resolve_area_unknown_lists_the_known_names() -> None:
    with pytest.raises(ValueError, match="'Voor', 'Achter'"):
        schedules.resolve_area("Zij", _AREAS)


def test_resolve_area_without_a_published_id_is_refused() -> None:
    with pytest.raises(ValueError, match="no stable id"):
        schedules.resolve_area("Achter", [{"index": 2, "name": "Achter"}])


def test_toggled_keeps_every_field_except_the_schedulers_own() -> None:
    sched = {
        "id": "1",
        "areaId": 7,
        "areaName": "Achter",
        "time": "17:30",
        "daysOfWeek": [6],
        "enabled": True,
        "createdAt": "2026-09-23T06:48:20Z",
        "lastRun": "2026-09-27T17:30:00Z",
        "futureField": "kept",
    }
    assert schedules.toggled(sched, False) == {
        "id": "1",
        "areaId": 7,
        "areaName": "Achter",
        "time": "17:30",
        "daysOfWeek": [6],
        "enabled": False,
        "futureField": "kept",
    }
