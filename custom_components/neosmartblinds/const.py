"""Constants for the NeoSmartBlinds integration."""

from __future__ import annotations

import re
from typing import Final

DOMAIN: Final = "neosmartblinds"

# Controller (config entry) keys. CONF_HOST/CONF_PORT come from homeassistant.const.
CONF_HUB_ID: Final = "hub_id"
CONF_PROTOCOL: Final = "protocol"

# Blind (config subentry) keys. Names match the old YAML options.
CONF_BLIND_CODE: Final = "blind_code"
CONF_CLOSE_TIME: Final = "close_time"
CONF_RAIL: Final = "rail"
CONF_TDBU_EXTENDED: Final = "tdbu_extended"
CONF_TILT: Final = "tilt"
CONF_PERCENT_SUPPORT: Final = "percent_support"
CONF_MOTOR_CODE: Final = "motor_code"
CONF_START_POSITION: Final = "start_position"
CONF_OPEN_TIME: Final = "open_time"
CONF_STARTUP_SYNC: Final = "startup_sync"
CONF_PARENT: Final = "parent_group"

# Controller entry options
CONF_ROOM_NAMES: Final = "room_names"  # room code -> name

SUBENTRY_BLIND: Final = "blind"


def controller_title(host: str) -> str:
    """Return the title given to a controller entry."""
    return f"Neo Smart Controller ({host})"


PROTOCOL_HTTP: Final = "http"
PROTOCOL_TCP: Final = "tcp"
DEFAULT_PORTS: Final = {PROTOCOL_HTTP: 8838, PROTOCOL_TCP: 8839}

DEFAULT_CLOSE_TIME: Final = 20

# startup_sync: move the blind to a known end when Home Assistant starts
SYNC_NONE: Final = "none"
SYNC_OPEN: Final = "open"
SYNC_CLOSE: Final = "close"

BLIND_CODE_RE: Final = re.compile(r"^\d{3}\.\d{3}-\d{2}$")
# An action is two letters or digits: up, dn, mu, i1, u4, 01-99, ...
ACTION_RE: Final = re.compile(r"^[a-z0-9]{2}$")

SERVICE_SEND_COMMAND: Final = "send_command"
ATTR_COMMAND: Final = "command"

# percent_support modes
LEGACY_POSITIONING: Final = 0  # favourites only
EXPLICIT_POSITIONING: Final = 1  # send 01-99 to the blind, estimate travel
IMPLICIT_POSITIONING: Final = 2  # timed up/down followed by stop

# Rails for top-down/bottom-up blinds
RAIL_TOP: Final = 1  # top / middle rail (standard command set)
RAIL_BOTTOM: Final = 2  # bottom / lower rail
RAIL_BOTH: Final = 3  # both rails together

# Seconds to wait for sibling blinds before deciding whether to send a room command
COMMAND_AGGREGATION_PERIOD: Final = 0.25
# Minimum gap between two commands to the same controller (docs: >= 500 ms)
COMMAND_SPACING: Final = 0.7
IO_TIMEOUT: Final = 10
GROUP_CHANNEL: Final = "15"

CMD_UP: Final = "up"
CMD_DOWN: Final = "dn"
CMD_STOP: Final = "sp"
CMD_MICRO_UP: Final = "mu"
CMD_MICRO_DOWN: Final = "md"
CMD_FAV: Final = "gp"
CMD_FAV_1: Final = "i1"
CMD_FAV_2: Final = "i2"

# Top-down/bottom-up ("tb" and the standard rail-2 set)
CMD_UP2: Final = "u2"
CMD_DOWN2: Final = "d2"
CMD_MICRO_UP2: Final = "o2"
CMD_MICRO_DOWN2: Final = "c2"
CMD_UP3: Final = "u3"
CMD_DOWN3: Final = "d3"

# Extended TDBU set for mb/ra/ap/db motors of the TDBU type
CMD_UP4: Final = "u4"
CMD_DOWN4: Final = "d4"
CMD_STOP2: Final = "x2"

# Motor codes accepted by the controller, with a description for the UI
MOTOR_CODES: Final = {
    "bf": "Bofu (standard)",
    "vb": "Bofu (vertical)",
    "tb": "Bofu top-down/bottom-up",
    "no": "NEO",
    "rx": "Raex",
    "nc": "Nice F-Code",
    "nr": "Nice 0-Code",
    "k1": "A-OK (standard)",
    "k2": "A-OK v2 (Louvolite)",
    "k3": "A-OK v3 (Eclipse)",
    "dy": "Dooya (unidirectional)",
    "by": "Bofu/Dooya (deprecated)",
    "wt": "Wistar / Alpha",
    "db": "Dooya (bi-directional)",
    "sy": "Somfy RTS",
    "gp": "Gaposa",
    "ra": "Rollease ARC",
    "re": "Rollease EL",
    "ap": "AMP (Turnils)",
    "sf": "Sunfrere",
    "rc": "Ronco",
    "bl": "Alta Bliss",
    "mb": "Coulisse MotionBlinds",
    "jo": "Jiecang",
}
# Motors that accept a 01-99 position action
POSITION_MOTOR_CODES: Final = frozenset(
    {"no", "db", "ra", "rb", "ap", "bl", "mb", "jo"}
)
# Motors that may have the extended top-down/bottom-up command set
TDBU_EXTENDED_MOTOR_CODES: Final = frozenset({"mb", "ra", "ap", "db"})
# Motors documented to support intermediate position 2 (i2)
FAV_2_MOTOR_CODES: Final = frozenset({"no", "rx"})
