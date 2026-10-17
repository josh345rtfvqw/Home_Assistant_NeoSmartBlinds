"""Diagnostics for NeoSmartBlinds."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import NeoConfigEntry
from .const import CONF_HUB_ID

TO_REDACT = {CONF_HOST, CONF_HUB_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NeoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a controller and its blinds."""
    client = entry.runtime_data.client
    return {
        "controller": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "blinds": [
            {"title": sub.title, **sub.data} for sub in entry.subentries.values()
        ],
        "rooms": sorted(entry.runtime_data.groups),
        "connection": {
            "connected": client.connected,
            "last_error": client.last_error,
            "recent": [
                {
                    "time": record.time.isoformat(),
                    "command": record.command,
                    "result": record.result,
                }
                for record in client.history
            ],
        },
    }
