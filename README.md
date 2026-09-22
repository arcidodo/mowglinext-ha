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
