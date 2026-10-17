"""One configured blind: its settings and the commands it sends."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .api import BlindCommand, NeoAuthError, NeoClient, NeoError
from .const import (
    CMD_DOWN,
    CMD_DOWN2,
    CMD_DOWN3,
    CMD_DOWN4,
    CMD_FAV,
    CMD_FAV_2,
    CMD_MICRO_DOWN,
    CMD_MICRO_DOWN2,
    CMD_MICRO_UP,
    CMD_MICRO_UP2,
    CMD_STOP,
    CMD_STOP2,
    CMD_UP,
    CMD_UP2,
    CMD_UP3,
    CMD_UP4,
    CONF_BLIND_CODE,
    CONF_CLOSE_TIME,
    CONF_MOTOR_CODE,
    CONF_OPEN_TIME,
    CONF_PARENT,
    CONF_PERCENT_SUPPORT,
    CONF_RAIL,
    CONF_START_POSITION,
    CONF_STARTUP_SYNC,
    CONF_TDBU_EXTENDED,
    CONF_TILT,
    DEFAULT_CLOSE_TIME,
    DOMAIN,
    FAV_2_MOTOR_CODES,
    LEGACY_POSITIONING,
    MOTOR_CODES,
    RAIL_BOTH,
    RAIL_BOTTOM,
    RAIL_TOP,
    SUBENTRY_BLIND,
    SYNC_NONE,
)
from .group import NeoGroup

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RailCommands:
    """Actions used to drive one rail."""

    up: str
    down: str
    stop: str
    micro_up: str | None
    micro_down: str | None


def rail_commands(rail: int, tdbu_extended: bool) -> RailCommands:
    """Return the actions for a rail.

    The standard set covers ordinary blinds (rail 1) and "tb" top-down/bottom-up
    blinds. The extended set is for TDBU blinds with mb, ra, ap or db motors.
    """
    if tdbu_extended:
        if rail == RAIL_BOTTOM:
            return RailCommands(CMD_UP2, CMD_DOWN2, CMD_STOP2, None, None)
        if rail == RAIL_BOTH:
            return RailCommands(CMD_UP3, CMD_DOWN3, CMD_STOP2, None, None)
        return RailCommands(CMD_UP4, CMD_DOWN4, CMD_STOP2, None, None)
    if rail == RAIL_BOTTOM:
        return RailCommands(
            CMD_UP2, CMD_DOWN2, CMD_STOP, CMD_MICRO_UP2, CMD_MICRO_DOWN2
        )
    if rail == RAIL_BOTH:
        return RailCommands(CMD_UP3, CMD_DOWN3, CMD_STOP, None, None)
    return RailCommands(CMD_UP, CMD_DOWN, CMD_STOP, CMD_MICRO_UP, CMD_MICRO_DOWN)


def shared_rooms(subentries: Iterable[ConfigSubentry]) -> dict[str, list[str]]:
    """Return room code -> blind names, for room codes shared by two or more blinds.

    A room code that is itself added as a blind already has its own cover, so it
    gets no room cover.
    """
    blinds = [s for s in subentries if s.subentry_type == SUBENTRY_BLIND]
    codes = {s.data[CONF_BLIND_CODE] for s in blinds}
    rooms: dict[str, list[str]] = {}
    for sub in blinds:
        if (parent := sub.data.get(CONF_PARENT)) and parent not in codes:
            rooms.setdefault(parent, []).append(sub.title)
    return {code: names for code, names in sorted(rooms.items()) if len(names) >= 2}


def blind_unique_id(data: dict[str, Any]) -> str:
    """Return the unique id used by v2 (YAML) entities so the registry carries over."""
    return (
        f"{DOMAIN}.{data[CONF_BLIND_CODE]}."
        f"{data.get(CONF_MOTOR_CODE, '')}.{data.get(CONF_RAIL, RAIL_TOP)}"
    )


class NeoBlind:
    """A blind behind a controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        client: NeoClient,
        group: NeoGroup | None,
    ) -> None:
        """Initialise from a blind subentry."""
        data = subentry.data
        self.hass = hass
        self.entry = entry
        self.subentry_id = subentry.subentry_id
        self.name = subentry.title
        self.code: str = data[CONF_BLIND_CODE]
        self.motor_code: str = data.get(CONF_MOTOR_CODE, "") or ""
        self.rail: int = int(data.get(CONF_RAIL, RAIL_TOP))
        self.tdbu_extended: bool = bool(data.get(CONF_TDBU_EXTENDED, False))
        self.tilt: bool = bool(data.get(CONF_TILT, False))
        self.percent_support: int = int(
            data.get(CONF_PERCENT_SUPPORT, LEGACY_POSITIONING)
        )
        self.close_time: float = float(data.get(CONF_CLOSE_TIME, DEFAULT_CLOSE_TIME))
        # Blinds often rise slower than they drop; without an open time assume
        # both directions take the same time.
        self.open_time: float = float(data.get(CONF_OPEN_TIME) or self.close_time)
        self.startup_sync: str = data.get(CONF_STARTUP_SYNC, SYNC_NONE)
        self.start_position: int | None = data.get(CONF_START_POSITION)
        self.parent: str | None = data.get(CONF_PARENT) or None
        self.unique_id: str = subentry.unique_id or blind_unique_id(dict(data))
        self.commands = rail_commands(self.rail, self.tdbu_extended)
        self._client = client
        self._group = group
        self._favourite_listeners: list[Callable[[int], None]] = []

    @property
    def group(self) -> NeoGroup | None:
        """Return the room group this blind belongs to, if any."""
        return self._group

    @property
    def supports_favourite_2(self) -> bool:
        """Return whether intermediate position 2 is likely to work."""
        return not self.motor_code or self.motor_code in FAV_2_MOTOR_CODES

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device for this blind."""
        rail = f", rail {self.rail}" if self.rail != RAIL_TOP else ""
        model = MOTOR_CODES.get(self.motor_code, "Blind")
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Neo Smart Blinds",
            model=f"{model}{rail}",
            serial_number=self.code,
            via_device=(DOMAIN, self.entry.unique_id or self.entry.entry_id),
        )

    async def async_send(
        self, action: str, *, use_group: bool = True, code: str | None = None
    ) -> None:
        """Send an action, through the room group when one is configured.

        code sends the action to another blind or room code with this blind's
        motor code (used by the room cover), bypassing the group.
        """
        command = BlindCommand(code or self.code, action, self.motor_code)
        try:
            if use_group and code is None and self._group is not None:
                await self._group.async_send(self.unique_id, command)
            else:
                await self._client.async_send(command)
        except NeoAuthError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_hub_id"
            ) from err
        except NeoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": str(command), "error": str(err)},
            ) from err

    async def async_up(self) -> None:
        """Move up."""
        await self.async_send(self.commands.up)

    async def async_down(self) -> None:
        """Move down."""
        await self.async_send(self.commands.down)

    async def async_stop(self) -> None:
        """Stop moving."""
        await self.async_send(self.commands.stop)

    async def async_position(self, position: int) -> None:
        """Move to a position given as HA percent open (Neo uses percent closed)."""
        await self.async_send(f"{100 - position:02d}")

    async def async_micro_up(self) -> None:
        """Micro-step up (used for tilt)."""
        if self.commands.micro_up:
            await self.async_send(self.commands.micro_up, use_group=False)

    async def async_micro_down(self) -> None:
        """Micro-step down (used for tilt)."""
        if self.commands.micro_down:
            await self.async_send(self.commands.micro_down, use_group=False)

    async def async_favourite(self, number: int) -> None:
        """Go to favourite position 1 (gp) or 2 (i2)."""
        await self.async_send(CMD_FAV if number == 1 else CMD_FAV_2, use_group=False)
        for listener in list(self._favourite_listeners):
            listener(number)

    @callback
    def add_favourite_listener(
        self, listener: Callable[[int], None]
    ) -> Callable[[], None]:
        """Call listener(number) after a favourite is selected."""
        self._favourite_listeners.append(listener)

        @callback
        def remove() -> None:
            self._favourite_listeners.remove(listener)

        return remove
