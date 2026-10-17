"""Fixtures for NeoSmartBlinds tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.neosmartblinds.blind import blind_unique_id
from custom_components.neosmartblinds.const import (
    CONF_BLIND_CODE,
    CONF_CLOSE_TIME,
    CONF_HUB_ID,
    CONF_MOTOR_CODE,
    CONF_PERCENT_SUPPORT,
    CONF_PROTOCOL,
    CONF_RAIL,
    CONF_TDBU_EXTENDED,
    DOMAIN,
    SUBENTRY_BLIND,
)

HUB_ID = "440036000447393032323330"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def no_command_spacing() -> Any:
    """Don't wait between commands unless a test asks for it."""
    with patch("custom_components.neosmartblinds.api.COMMAND_SPACING", 0):
        yield


@dataclass
class FakeController:
    """A local stand-in for a Neo controller, speaking both HTTP and TCP."""

    http_port: int = 0
    tcp_port: int = 0
    hub_id: str = HUB_ID
    status: int | None = None  # force an HTTP status
    statuses: list[int] = field(default_factory=list)  # consumed one per request
    received: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    @property
    def actions(self) -> list[str]:
        """Return just the ID-CHANNEL-ACTION part of each command."""
        return [cmd.split("!")[0] for cmd in self.received]

    def clear(self) -> None:
        """Forget received commands."""
        self.received.clear()
        self.hashes.clear()
        self.times.clear()

    async def handle_http(self, request: web.Request) -> web.Response:
        """Handle /neo/v1/transmit."""
        if self.statuses:
            status = self.statuses.pop(0)
        elif self.status is not None:
            status = self.status
        elif request.query.get("id") != self.hub_id:
            status = 401
        else:
            status = 200
        if status == 200:
            self.received.append(request.query["command"])
            self.hashes.append(request.query["hash"])
            self.times.append(asyncio.get_running_loop().time())
        return web.Response(status=status)

    async def handle_tcp(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Echo the command (without motor code) and close, like the real thing."""
        line = (await reader.readline()).decode().strip()
        if line:
            self.received.append(line)
            self.times.append(asyncio.get_running_loop().time())
            writer.write(f"{line.split('!')[0]}\r\n".encode())
            await writer.drain()
        writer.close()


@pytest.fixture
async def fake_controller(socket_enabled: None) -> AsyncGenerator[FakeController]:
    """Run a fake controller on localhost."""
    controller = FakeController()
    app = web.Application()
    app.router.add_get("/neo/v1/transmit", controller.handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    controller.http_port = runner.addresses[0][1]
    server = await asyncio.start_server(controller.handle_tcp, "127.0.0.1", 0)
    controller.tcp_port = server.sockets[0].getsockname()[1]
    yield controller
    server.close()
    await server.wait_closed()
    await runner.cleanup()


def blind_subentry(
    title: str = "Blind One",
    code: str = "021.230-04",
    **data: Any,
) -> ConfigSubentryData:
    """Build blind subentry data."""
    blind = {
        CONF_BLIND_CODE: code,
        CONF_MOTOR_CODE: "bf",
        CONF_RAIL: 1,
        CONF_TDBU_EXTENDED: False,
        CONF_PERCENT_SUPPORT: 2,
        CONF_CLOSE_TIME: 20,
    } | data
    return ConfigSubentryData(
        data=blind,
        subentry_type=SUBENTRY_BLIND,
        title=title,
        unique_id=blind_unique_id(blind),
    )


def controller_entry(
    controller: FakeController,
    subentries: list[ConfigSubentryData],
    protocol: str = "http",
    minor_version: int = 2,
    port: int | None = None,
) -> MockConfigEntry:
    """Build a controller config entry pointing at the fake controller."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Neo controller (127.0.0.1)",
        unique_id=HUB_ID,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PROTOCOL: protocol,
            CONF_PORT: port
            or (controller.http_port if protocol == "http" else controller.tcp_port),
            CONF_HUB_ID: HUB_ID,
        },
        subentries_data=subentries,
        minor_version=minor_version,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
