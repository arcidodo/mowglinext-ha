"""Constants for the MowgliNext integration.

Command codes and state values mirror `mowgli_interfaces/srv/HighLevelControl.srv`
and `mowgli_interfaces/msg/HighLevelStatus.msg` in the main mowglinext repo — see
`docs/MQTT_CONTROL.md` there for the authoritative, versioned contract this
integration is built against.
"""
from __future__ import annotations

DOMAIN = "mowglinext"

CONF_TOPIC_PREFIX = "topic_prefix"
DEFAULT_TOPIC_PREFIX = "mowgli"

# HighLevelControl.srv command codes. Payloads on "<prefix>/command" are the
# ASCII decimal string of these values (e.g. "1"), NOT a raw byte.
COMMAND_START = 1
COMMAND_HOME = 2
COMMAND_RECORD_AREA = 3
COMMAND_RECORD_FINISH = 5
COMMAND_RECORD_CANCEL = 6
COMMAND_MANUAL_MOW = 7
COMMAND_STOP = 8
# Declared in HighLevelControl.srv but NOT wired to any BT guard on the MQTT
# command channel as of this writing (see docs/MQTT_CONTROL.md) — the real
# reset/clear-maps paths are separate services the bridge does not expose.
# Kept here, and the Reset Emergency button wired to it, for
# forward-compatibility rather than usefulness today.
COMMAND_RESET_EMERGENCY = 254
COMMAND_DELETE_MAPS = 255

# HighLevelStatus.msg state values.
STATE_NULL = 0
STATE_IDLE = 1
STATE_AUTONOMOUS = 2
STATE_RECORDING = 3
STATE_MANUAL_MOWING = 4
