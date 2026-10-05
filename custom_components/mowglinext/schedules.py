"""Pure helpers for the mower's mowing schedules (<prefix>/schedules).

A schedule mows ONE area or ALL of them: `areaId` is the area's stable id (the
`id` in <prefix>/areas), `0` or absent means all areas (a plain Start), and
`areaName` is a display snapshot taken when the schedule was saved. Older mower
software has no `areaId` at all (only an `area` index its scheduler ignored), so
such a schedule really does mow every area.

The mower refuses an enabled schedule that starts less than 60 minutes from
another enabled one on a shared weekday, and says so only in its log (MQTT has no
error channel). `find_overlap` mirrors that rule so Home Assistant can refuse with
a readable message instead of a toggle that silently does nothing.
"""
from __future__ import annotations

from typing import Any

ALL_AREAS_LABEL = "All areas"

# Same values as the mower's own check (gui/pkg/api/schedules.go).
MIN_SPACING_MINUTES = 60
_MINUTES_PER_WEEK = 7 * 24 * 60

# Written by the mower's scheduler itself; never sent back on schedules/set.
_SERVER_MANAGED = ("createdAt", "lastRun", "lastSkipReason", "lastSkippedAt")


def area_id(sched: dict[str, Any]) -> int:
    """The schedule's stable area id, 0 for all areas."""
    value = sched.get("areaId")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def area_label(sched: dict[str, Any], areas: list[dict[str, Any]]) -> str | None:
    """The name of the area a schedule mows, or None when it mows all of them.

    The area's CURRENT name wins over the schedule's `areaName` snapshot (so a
    rename shows up); the snapshot covers an area that has since been removed.
    """
    target = area_id(sched)
    if not target:
        return None
    for area in areas:
        if area.get("id") == target and area.get("name"):
            return area["name"]
    return sched.get("areaName") or f"Area {target}"


def resolve_area(value: Any, areas: list[dict[str, Any]]) -> tuple[int, str | None]:
    """(areaId, areaName) for a service call's `area`: an area name, or empty /
    "all" / 0 for all areas. Raises ValueError with a readable reason otherwise."""
    if value is None or (isinstance(value, int) and not isinstance(value, bool) and value == 0):
        return 0, None
    name = str(value).strip()
    for area in areas:
        if area.get("name") == name:
            if not area.get("id"):
                raise ValueError(
                    f"Area '{name}' has no stable id yet; per-area schedules need a mower "
                    "release whose <prefix>/areas publishes one"
                )
            return int(area["id"]), name
    if name.casefold() in ("", "0", "all", ALL_AREAS_LABEL.casefold()):
        return 0, None
    known = ", ".join(f"'{a['name']}'" for a in areas if a.get("name")) or "none received yet"
    raise ValueError(f"Unknown area '{name}' (known areas: {known})")


def toggled(sched: dict[str, Any], enabled: bool) -> dict[str, Any]:
    """The schedules/set payload that changes only `enabled`.

    An update replaces the whole record, so every field the mower published —
    including ones this integration does not know about — is sent back as-is.
    """
    payload = {k: v for k, v in sched.items() if k not in _SERVER_MANAGED}
    payload["enabled"] = enabled
    return payload


def find_overlap(
    candidate: dict[str, Any], schedules: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The first OTHER enabled schedule the candidate would overlap, if enabled."""
    if not candidate.get("enabled"):
        return None
    own = _starts(candidate)
    for other in schedules:
        if not other.get("enabled") or (
            candidate.get("id") is not None and str(other.get("id")) == str(candidate.get("id"))
        ):
            continue
        for a in own:
            for b in _starts(other):
                diff = abs(a - b)
                if min(diff, _MINUTES_PER_WEEK - diff) < MIN_SPACING_MINUTES:
                    return other
    return None


def overlap_message(other: dict[str, Any]) -> str:
    return (
        f"Overlaps the enabled schedule starting at {other.get('time')}: enabled schedules "
        f"sharing a weekday must start at least {MIN_SPACING_MINUTES} minutes apart"
    )


def _starts(sched: dict[str, Any]) -> list[int]:
    """Minutes since Sunday 00:00 of every start of the schedule."""
    try:
        hour, minute = str(sched.get("time")).split(":")
        minute_of_day = int(hour) * 60 + int(minute)
    except (ValueError, TypeError):
        return []
    days = sched.get("daysOfWeek") or []
    return [
        d * 24 * 60 + minute_of_day
        for d in days
        if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6
    ]
