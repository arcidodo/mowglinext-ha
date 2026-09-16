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
- A `select` entity ("Start area") listing your recorded mow areas by name — pick one to start
  mowing it now, ahead of the normal iteration order. Backed by `<prefix>/areas`/`<prefix>/start_area`
  (**requires mowglinext PR [#638](https://github.com/mowglinext/mowglinext/pull/638)**). Areas have
  no stable ID yet ([mowglinext#637](https://github.com/mowglinext/mowglinext/issues/637)), so this
  always re-resolves the area's index from the freshest list at the moment you pick it — never a
  value cached from when the dropdown was built — and refuses (with a visible error) rather than
  risk starting the wrong area if the name has since disappeared from the list.
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
