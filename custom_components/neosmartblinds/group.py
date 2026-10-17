"""Room (channel 15) command aggregation.

When every blind in a room is sent the same action within a short window (for
example a scene, a HomeKit room or an automation that moves several blinds), one
room command is transmitted instead of one per blind. This keeps the controller,
which only handles a command every ~500 ms, from falling behind.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .api import BlindCommand, NeoClient
from .const import COMMAND_AGGREGATION_PERIOD

_LOGGER = logging.getLogger(__name__)


@dataclass
class _Batch:
    action: str
    motor_code: str
    # member id -> (individual command, future resolved when it has been handled)
    waiting: dict[str, tuple[BlindCommand, asyncio.Future[None]]] = field(
        default_factory=dict
    )
    cancel_timer: Callable[[], None] | None = None


class NeoGroup:
    """Collects identical commands for the blinds sharing a parent group code."""

    def __init__(self, hass: HomeAssistant, client: NeoClient, code: str) -> None:
        """Initialise the group."""
        self.hass = hass
        self.client = client
        self.code = code
        self.members: set[str] = set()
        self._batch: _Batch | None = None

    @callback
    def register(self, member_id: str) -> Callable[[], None]:
        """Register a blind as a member; returns a callable that unregisters it."""
        self.members.add(member_id)

        @callback
        def unregister() -> None:
            self.members.discard(member_id)
            if (batch := self._batch) is not None and self._is_complete(batch):
                self._flush(batch)

        return unregister

    async def async_send(self, member_id: str, command: BlindCommand) -> None:
        """Send a command for a member, possibly merged into one room command."""
        if member_id not in self.members or len(self.members) < 2:
            await self.client.async_send(command)
            return

        batch = self._batch
        if batch is not None and (
            batch.action != command.action
            or batch.motor_code != command.motor_code
            or member_id in batch.waiting
        ):
            # A different command arrived; send what was collected so far as is.
            _LOGGER.debug(
                "%s, %s interrupts pending group command %s",
                self.code,
                command.action,
                batch.action,
            )
            self._flush(batch)
            batch = None

        if batch is None:
            batch = _Batch(command.action, command.motor_code)
            self._batch = batch
            new_batch = batch

            @callback
            def _window_closed(_now: datetime) -> None:
                self._flush(new_batch)

            batch.cancel_timer = async_call_later(
                self.hass, COMMAND_AGGREGATION_PERIOD, _window_closed
            )

        future: asyncio.Future[None] = self.hass.loop.create_future()
        batch.waiting[member_id] = (command, future)
        if self._is_complete(batch):
            self._flush(batch)
        await future

    def _is_complete(self, batch: _Batch) -> bool:
        return bool(batch.waiting) and self.members <= batch.waiting.keys()

    @callback
    def _flush(self, batch: _Batch) -> None:
        """Stop collecting for this batch and transmit it."""
        if self._batch is not batch:
            return
        self._batch = None
        if batch.cancel_timer is not None:
            batch.cancel_timer()
            batch.cancel_timer = None
        self.hass.async_create_task(
            self._async_transmit(batch), f"neosmartblinds group {self.code}"
        )

    async def _async_transmit(self, batch: _Batch) -> None:
        if len(batch.waiting) > 1 and self._is_complete(batch):
            _LOGGER.debug("%s, sending %s to the whole room", self.code, batch.action)
            room = BlindCommand(self.code, batch.action, batch.motor_code)
            futures = [future for _, future in batch.waiting.values()]
            try:
                await self.client.async_send(room)
            except Exception as err:  # noqa: BLE001 - handed to every waiting blind
                for future in futures:
                    if not future.done():
                        future.set_exception(err)
            else:
                for future in futures:
                    if not future.done():
                        future.set_result(None)
            return

        for command, future in batch.waiting.values():
            try:
                await self.client.async_send(command)
            except Exception as err:  # noqa: BLE001 - handed to the waiting blind
                if not future.done():
                    future.set_exception(err)
            else:
                if not future.done():
                    future.set_result(None)
