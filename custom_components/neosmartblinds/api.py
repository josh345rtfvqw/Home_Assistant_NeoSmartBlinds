"""Async client for the Neo Smart Blinds controller's local HTTP and TCP APIs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import aiohttp

from .const import COMMAND_SPACING, IO_TIMEOUT, PROTOCOL_TCP

_LOGGER = logging.getLogger(__name__)


class NeoError(Exception):
    """Base error talking to the controller."""


class NeoConnectionError(NeoError):
    """The controller could not be reached."""


class NeoAuthError(NeoError):
    """The controller rejected the hub id (HTTP 401)."""


class NeoCommandError(NeoError):
    """The controller rejected the command (HTTP 400/404)."""


@dataclass(frozen=True, slots=True)
class BlindCommand:
    """A single action for one blind code."""

    code: str  # ID1.ID2-CHANNEL
    action: str
    motor_code: str = ""

    def __str__(self) -> str:
        """Return the wire format, ID1.ID2-CHANNEL-ACTION!MOTORCODE."""
        command = f"{self.code}-{self.action}"
        if self.motor_code:
            command += f"!{self.motor_code}"
        return command


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """One exchange with the controller, kept for diagnostics."""

    time: datetime
    command: str  # the command, or "connection check"
    result: str  # "ok" or the error


class NeoClient:
    """Send commands to one controller, one at a time with spacing between them."""

    def __init__(
        self,
        host: str,
        port: int,
        protocol: str,
        hub_id: str,
        session: aiohttp.ClientSession,
        spacing: float | None = None,
    ) -> None:
        """Initialise the client."""
        self.host = host
        self.port = port
        self.protocol = protocol
        self.hub_id = hub_id
        self._session = session
        self._spacing = COMMAND_SPACING if spacing is None else spacing
        self._lock = asyncio.Lock()
        self._last_sent = 0.0
        self._last_hash = 0
        # Whether the controller answered the last exchange (None: not tried yet)
        self.connected: bool | None = None
        self.last_error: str | None = None
        self.history: deque[CommandRecord] = deque(maxlen=20)
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener when the connection status changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _record(self, what: str, err: Exception | None) -> None:
        self.history.append(
            CommandRecord(datetime.now(UTC), what, "ok" if err is None else repr(err))
        )
        # Any answer, even a rejected command, means the controller is reachable
        connected = not isinstance(err, NeoConnectionError)
        if err is not None:
            self.last_error = repr(err)
        if connected != self.connected:
            self.connected = connected
            for listener in list(self._listeners):
                listener()

    async def _async_exchange(
        self, what: str, send: Callable[[], Awaitable[None]]
    ) -> None:
        """Run one exchange when the controller is free, and record the result."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._last_sent + self._spacing - loop.time()
            if wait > 0:
                _LOGGER.debug("%s, delaying for %.3fs", what, wait)
                await asyncio.sleep(wait)
            try:
                await send()
            except NeoError as err:
                self._record(what, err)
                raise
            else:
                self._record(what, None)
            finally:
                self._last_sent = loop.time()

    async def async_send(self, command: BlindCommand) -> None:
        """Send a command, waiting for the controller to be free first."""
        if self.protocol == PROTOCOL_TCP:
            await self._async_exchange(
                str(command), lambda: self._async_send_tcp(command)
            )
        else:
            await self._async_exchange(
                str(command), lambda: self._async_send_http(command)
            )

    async def async_probe(self) -> None:
        """Check the controller is reachable, queued like a command."""
        await self._async_exchange("connection check", self.async_check_connection)

    async def async_check_connection(self) -> None:
        """Open and close a connection to the controller without sending a command."""
        try:
            async with asyncio.timeout(IO_TIMEOUT):
                _, writer = await asyncio.open_connection(self.host, self.port)
                writer.close()
                await writer.wait_closed()
        except (OSError, TimeoutError) as err:
            raise NeoConnectionError(str(err) or repr(err)) from err

    def _next_hash(self) -> str:
        """Return a 7 digit hash that differs from the previous one."""
        value = int(time.time() * 1000) % 10_000_000
        if value == self._last_hash:
            value = (value + 1) % 10_000_000
        self._last_hash = value
        return f"{value:07d}"

    async def _async_send_http(self, command: BlindCommand) -> None:
        url = f"http://{self.host}:{self.port}/neo/v1/transmit"
        for attempt in range(2):
            params = {
                "command": str(command),
                "id": self.hub_id,
                "hash": self._next_hash(),
            }
            _LOGGER.debug("%s, Tx: %s", command.code, params["command"])
            try:
                async with self._session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=IO_TIMEOUT)
                ) as resp:
                    status = resp.status
                    body = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise NeoConnectionError(str(err) or repr(err)) from err
            _LOGGER.debug("%s, Rx: %s %s", command.code, status, body)
            if status == 200:
                return
            if status == 401:
                raise NeoAuthError("Controller rejected hub id")
            if status == 409 and attempt == 0:
                # Hash collided with one the controller has seen; try a fresh one
                continue
            raise NeoCommandError(f"Controller returned HTTP {status} for {command}")

    async def _async_send_tcp(self, command: BlindCommand) -> None:
        payload = f"{command}\r\n".encode()
        _LOGGER.debug("%s, Tx: %s", command.code, command)
        try:
            async with asyncio.timeout(IO_TIMEOUT):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                try:
                    writer.write(payload)
                    await writer.drain()
                    response = await reader.read(256)
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError) as err:
            raise NeoConnectionError(str(err) or repr(err)) from err
        _LOGGER.debug("%s, Rx: %s", command.code, response.decode(errors="replace"))
