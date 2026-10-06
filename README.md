# MowgliNext for Home Assistant

A HACS-installable Home Assistant integration for [MowgliNext](https://github.com/mowglinext/mowglinext),
an open-source autonomous robot mower. Talks to the mower over MQTT — see
[`docs/MQTT_CONTROL.md`](https://github.com/mowglinext/mowglinext/blob/main/docs/MQTT_CONTROL.md)
in the main repo for the full wire contract this integration is built against. That is the
stable, versioned surface; this integration deliberately does **not** talk to the mower's
internal `:4006` REST/WebSocket API (unauthenticated, unversioned, an implementation detail of
the mower's own web UI) or its separate embedded MQTT broker.

## Requirements

- Home Assistant's own **MQTT** integration, already set up and connected to a broker your
  mower's `mqtt_bridge_node` also publishes to. That can be the mower's own bundled broker
  (`mowgli-mqtt`, reachable at `<mower-ip>:1883` on your LAN by default) or a broker of your own
  — either way, point HA's MQTT integration and the mower's `mqtt_host` setting at the *same*
  broker.
- `mqtt_bridge_node` enabled on the mower — off by default. On the mower's own GUI:
  **Settings → MQTT / Home Assistant**.

The brand icon (`custom_components/mowglinext/brand/`) needs Home Assistant **2026.3+** to
display (the [brands proxy API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)
that lets a custom integration serve its own icon locally, instead of requiring a PR to
`home-assistant/brands`). On older Home Assistant versions the integration still works
identically — you just get a generic icon in the integration list instead of the mower icon.

## Installation

### Via HACS (once you've published this repo)
1. HACS → Integrations → ⋮ → Custom repositories → add this repo's URL, category "Integration".
2. Install "MowgliNext", restart Home Assistant.

### Manual (for local testing — no repo or HACS needed)
Copy `custom_components/mowglinext/` into your Home Assistant config directory's
`custom_components/` folder and restart Home Assistant.

## Options

The device page's **Visit** link opens the mower's own web interface (port 4006) automatically,
using `<prefix>/host` — the mower's own LAN IP, which it publishes itself (needs a mower release
with that topic; older mower software publishes nothing there, and the link falls back to this
project's GitHub repo). Settings → Devices & services → MowgliNext → **Configure** lets you
enter a host manually instead, which always wins over the auto-detected one — useful if Home
Assistant needs a different reachable address than the mower's own interface (a different VLAN, a
VPN, port forwarding, ...). Leave it blank to use the automatic value.

## Setup

Settings → Devices & services → Add integration → **MowgliNext** → enter the MQTT topic prefix
(default `mowgli`, matching the mower's own default — only change it if you changed it on the
mower too).

## What you get

- A `lawn_mower` entity — start / pause / dock — mapped from `<prefix>/high_level_status`.
- Diagnostic sensors: battery %, coverage %, GPS quality %, RTK status (No fix / GPS fix / RTK
  float / RTK fixed — the same classification the robot's own LED ring and GUI use, not a
  re-derivation), and the raw BT state name.
- An `binary_sensor` for the emergency latch, and a "Reset emergency" button.
- A `device_tracker` entity backed by `<prefix>/gps` (real lat/lon), so the mower can show up
  on a Home Assistant map — not `<prefix>/position`, which is in the mower's local odom frame.
- A `camera` entity ("Map", `camera.mowgli_map`): a PNG of your recorded mowing areas (and their
  obstacles), the mower's current position and its trail since the current mow session started.
  It is a camera entity because cards such as `custom:lawn-mower-card` take a camera for their map
  preview: pick `camera.mowgli_map` as that card's camera entity. It is built from
  `<prefix>/area_boundary` (polygons, in metres from the mower's datum) and `<prefix>/gps`
  (projected through the same datum with the mower's own equirectangular formula), and needs
  neither a satellite-tile service nor an extra HACS card. The trail restarts whenever a new mow
  session begins (the mower entering `UNDOCKING`). What is drawn:
  - the area being worked (`<prefix>/high_level_status` `current_area`, while MOWING / PLANNING /
    TRANSIT) at full colour and the other areas dimmed;
  - the trail: stretches driven **with the blade running** (`<prefix>/status` `mower_motor_rpm`
    above 300) as a stripe as wide as the cut (18 cm), so you see what has really been mowed; the
    rest as a thin line. No trail is recorded while the mower is on the charger, so GPS jitter at
    the dock does not scribble over the map;
  - the charging dock, when the mower publishes one (`dock` in `<prefix>/area_boundary`);
  - the mower as an arrow pointing where it faces, when the mower publishes its fused pose
    (`<prefix>/pose`, map frame). That pose is also used for the position and the trail, because it
    is smoother than the raw GPS fix and needs no datum; without a fresh pose (older mower
    software, or the localizer not publishing for 10 s) the raw GPS fix is used and the marker is a
    dot;
  - the mower's marker coloured by RTK quality (`<prefix>/rtk_status`): fixed green, float
    orange, anything else red;
  - the **planned** route (headland rings, then serpentine swaths) as a thin mint line —
    matching the mower's own GUI map style — from `<prefix>/coverage_path` (needs a mower
    release that publishes it; mowglinext/mowglinext#726's sibling feature). Drawn under
    the trail, so what has already been mowed stands out on top of the plan.
- A `number` entity ("Map rotation", -180°..180°, configuration category): rotates the whole
  map image, matching the robot GUI's own "Map Rotation" (Mapbox bearing). That value is a
  local display setting on the mower's GUI (`gui.map.display.bearing`), not on MQTT, so it
  can't be read automatically — set the same number here if you want the two views to line
  up. Default 0 keeps north up (unchanged from earlier versions); restored after a restart and
  usable while the mower is offline, same as "Map style".
- A `switch` entity ("Map: active zone only", configuration category): shows only the zone being
  mowed, zoomed in, and leaves the other zones (and the trail, planned path, mower and dock outside
  it) out. Useful for a large garden split into zones, where the whole-garden view turns the
  planned mowing paths into a haze. Off by default (the whole garden, as before); it only takes
  effect while the mower reports which zone it is working on, otherwise everything is shown.
  Restored after a restart and usable while the mower is offline.
- A `select` entity ("Map style", configuration category) with the map's colour style: `natural`
  (default; transparent, so it takes on the colour of the card behind it), `classic`, `light` or
  `night`. It is a dashboard preference: it works while the mower is offline and is restored
  after a restart.
- A `select` entity ("Area to start") listing your recorded mow areas by name, plus a companion
  "Start selected area" button — pick an area, then press the button to start mowing it now, ahead
  of the normal iteration order. Split into two steps so opening the dropdown (or an automation
  reading it) can never accidentally start a mow. Backed by `<prefix>/areas`/`<prefix>/start_area`.
  Areas have no stable ID yet ([mowglinext#637](https://github.com/mowglinext/mowglinext/issues/637)),
  so the button always re-resolves the area's index from the freshest list at the moment it's
  pressed — never a value cached from when it was picked — and refuses (with a visible error) rather
  than risk starting the wrong area if the name has since disappeared from the list.
  The select carries `area_control: true` and `start_entity` (the button's entity id), so
  [lovelace-lawn-mower-card](https://github.com/EvotecIT/lovelace-lawn-mower-card) can offer an
  area menu on its Start button without extra configuration.
- One `button` per recorded area ("Mow Achter", `button.mowgli_mow_achter`, …): one press starts
  mowing that area — put them on any dashboard for a one-tap "mow this area". They are keyed by the
  area's stable `id` from `<prefix>/areas`, so a button keeps pointing at the same area through
  renames (its name follows; the entity id stays) and through edits that renumber the list; each
  press resolves the area's current index for `<prefix>/start_area` from the freshest list, and
  refuses if the area is gone. Buttons appear and disappear with the area list. Needs a mower
  release whose `<prefix>/areas` publishes `id`; on older software there are no per-area buttons
  (use the select + "Start selected area" above). The same is available as a service,
  `mowglinext.start_area` with `area: <name>`, for automations and scripts.
- A "Schedules" sensor and two services, `mowglinext.set_schedule` / `mowglinext.delete_schedule`,
  for the mower's own mowing schedules — these live only in the mower's GUI, with no ROS2
  representation at all, so the mower's own GUI backend (not `mqtt_bridge_node`) publishes and
  accepts them on the same broker. A schedule mows **one area or all of them**: `areaId` is the
  area's stable id (the `id` in `<prefix>/areas`, which survives edits to other areas), `0` means
  all areas, and `areaName` is a label snapshot. The sensor's state is how many schedules are
  enabled, and its `schedules` attribute is the full list (`id`, `areaId`, `areaName`, `time`,
  `daysOfWeek`, `enabled`, and `lastRun`/`lastSkipReason` if the scheduler skipped a due run, e.g.
  for wet soil or a removed area) — read it in a template or automation. `set_schedule` creates a
  schedule (leave `id` empty) or updates one (an existing `id`, found in the sensor's attributes);
  its `area` is the area's **name** (as in the "Area to start" selector), resolved to its stable id
  from the latest `<prefix>/areas` — leave it empty for all areas. `delete_schedule` removes one by
  `id`. The mower refuses two enabled schedules that start less than 60 minutes apart on a shared
  weekday; it only logs that, so `set_schedule` and the switches below check the same rule first and
  refuse with the reason. A schedule created or edited this way is executed by the mower's own
  scheduler exactly like one created in its GUI, and vice versa — one set of schedules, editable
  from either place. **A due, enabled schedule starts the mower unattended**, exactly like
  `lawn_mower.start_mowing`; needs a mower release that publishes `<prefix>/schedules` (older mower
  software: the sensor reads 0 schedules and the services silently do nothing). Per-area schedules
  need a mower release whose scheduler understands `areaId` and whose `<prefix>/areas` publishes
  `id`; on older software every schedule mows all areas, and is shown that way.
- A `calendar` entity ("Mowing schedule") showing the same schedules as actual calendar events —
  open it from the Calendar dashboard, or add it to any calendar card, to see when the mower is
  going to run without reading a sensor attribute. Each enabled schedule is expanded into one event
  per matching weekday within whatever range the calendar view asks for; the block shown (1 hour)
  is only for visibility — the schedule itself has no end time, and the mow actually ends whenever
  the area finishes. The event's title is the scheduled area's current name (or the schedule's own
  `areaName` if that area was removed), or "Mowing (all areas)". The entity's own state is the next
  upcoming occurrence.
- One `switch` per schedule (`switch.mowgli_schedule_1`, `_2`, …, named after its area and start
  time, e.g. "Schedule Achter 17:30" or "Schedule All areas 09:00"): on = the schedule is enabled.
  Turning one off pauses that schedule without deleting it (e.g. to skip tomorrow's mow); turning it
  on again resumes it. The switch sends the schedule back on `<prefix>/schedules/set` exactly as the
  mower published it except for `enabled` — including `areaId`/`areaName`, so a per-area schedule
  stays per-area — and its state follows what the mower publishes back on `<prefix>/schedules` (not
  the command), so a toggle the mower rejected does not show as done. Switches appear and disappear
  as schedules are added or deleted, here or in the mower's GUI; a new schedule takes the lowest
  free number, and an existing switch keeps its entity id. Their attributes (`schedule_control`,
  `name`, `map_label`, `weekdays`, `start_times`) are what
  [lovelace-lawn-mower-card](https://github.com/EvotecIT/lovelace-lawn-mower-card) reads to list them
  in its **Schedules** panel — it finds them on its own for the `lawn_mower.mowgli` entity, no extra
  card configuration needed.
- Availability tracking via `<prefix>/available` (the broker's own Last Will and Testament) **and**
  a freshness watchdog on `<prefix>/high_level_status`: that topic is republished at a steady ~1 Hz
  by the mower's behavior tree, so if no update arrives for 10 seconds the integration marks itself
  unavailable — even if the broker still thinks the connection is up (a `mqtt_bridge_node` that's
  wedged without actually dropping its TCP connection won't trigger the LWT on its own). The goal:
  you should never see a frozen "mowing"/"docked" state that's actually gone stale — either it's
  correct, or the entity honestly shows unavailable.

## Limitations

- `HighLevelStatus` has more states than Home Assistant's `lawn_mower` domain can express (no
  native "recording" or "manual mowing" activity) — the raw `state_name`/`sub_state_name` survive
  as `lawn_mower` attributes and on the diagnostic "State" sensor for anyone who needs the exact
  substate.
- The map needs the mower to publish a real map datum in `<prefix>/area_boundary`. A mower that
  reports datum 0/0 (its "not set" value, e.g. a release from before the launch file passed the
  datum to `mqtt_bridge_node`) still shows the lawn, with a note that the position is unavailable,
  instead of plotting the mower thousands of kilometres away. Fixes further than 5 km from the
  datum are ignored the same way.
- The map camera renders at a smaller default size (640×≤640) than `map_render.render_map()`'s
  own default, so a tall/narrow garden does not overflow Home Assistant's "more info" dialog and
  force the page to scroll. A caller that requests a specific size (a card asking for a
  thumbnail) still gets that size instead.
- The map camera is schematic: no satellite background and no mowed-versus-remaining coverage (only where the
  blade has actually been, since this integration started). The trail is kept in memory only, so it starts empty
  after a Home Assistant restart until the mower moves again.
- Commands are fire-and-forget over MQTT — there is no acknowledgement. The `lawn_mower` entity
  updates once the next `high_level_status` payload arrives (up to `publish_rate`, 1 Hz by
  default, after the command is sent), not instantly.
- `COMMAND_RESET_EMERGENCY` (254) is not currently wired to anything on the mower's MQTT command
  channel as of this writing — see `docs/MQTT_CONTROL.md` in the main repo. The button is
  provided for forward-compatibility, not because it's known to work today.
- No authentication is enforced on the wire by default (matching the mower's own bundled
  broker) — treat the broker like any other device on your trusted LAN.

## Development / testing

```bash
pip install -r requirements_test.txt
pytest
```

The tests in `tests/` are written against `pytest-homeassistant-custom-component`'s conventions
but have not been run in the environment that generated this scaffold (no network access to
install Home Assistant's test harness there) — run them yourself before relying on them, and
expect minor fixture-name adjustments across Home Assistant versions.

## License

GPL-3.0, matching the main [MowgliNext](https://github.com/mowglinext/mowglinext) project.
