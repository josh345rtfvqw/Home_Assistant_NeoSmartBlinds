"""Tests for the controller client."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.neosmartblinds.api import (
    BlindCommand,
    NeoAuthError,
    NeoClient,
    NeoCommandError,
    NeoConnectionError,
)

from .conftest import HUB_ID, FakeController


def _client(hass: HomeAssistant, controller: FakeController, protocol: str, **kw):
    port = controller.http_port if protocol == "http" else controller.tcp_port
    return NeoClient(
        "127.0.0.1", port, protocol, HUB_ID, async_get_clientsession(hass), **kw
    )


def test_command_format() -> None:
    """Commands are ID1.ID2-CHANNEL-ACTION!MOTORCODE."""
    assert str(BlindCommand("021.230-04", "up", "bf")) == "021.230-04-up!bf"
    assert str(BlindCommand("021.230-04", "70")) == "021.230-04-70"


async def test_http_send(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """HTTP sends command, id and a 7 digit hash."""
    client = _client(hass, fake_controller, "http")
    await client.async_send(BlindCommand("021.230-04", "up", "bf"))
    await client.async_send(BlindCommand("021.230-04", "sp", "bf"))
    assert fake_controller.received == ["021.230-04-up!bf", "021.230-04-sp!bf"]
    assert all(len(h) == 7 and h.isdigit() for h in fake_controller.hashes)
    assert fake_controller.hashes[0] != fake_controller.hashes[1]


async def test_http_errors(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """401 is an auth error, 400 a command error, 409 is retried once."""
    client = _client(hass, fake_controller, "http")
    fake_controller.statuses = [401]
    with pytest.raises(NeoAuthError):
        await client.async_send(BlindCommand("021.230-04", "up"))
    fake_controller.statuses = [400]
    with pytest.raises(NeoCommandError):
        await client.async_send(BlindCommand("021.230-04", "zz"))
    fake_controller.statuses = [409]
    await client.async_send(BlindCommand("021.230-04", "up"))
    assert fake_controller.received == ["021.230-04-up"]
    fake_controller.statuses = [409, 409]
    with pytest.raises(NeoCommandError):
        await client.async_send(BlindCommand("021.230-04", "up"))


async def test_tcp_send(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """TCP sends one command per connection."""
    client = _client(hass, fake_controller, "tcp")
    await client.async_send(BlindCommand("021.230-04", "dn", "bf"))
    await client.async_send(BlindCommand("021.230-04", "sp", "bf"))
    assert fake_controller.received == ["021.230-04-dn!bf", "021.230-04-sp!bf"]
    await client.async_check_connection()


async def test_connection_error(hass: HomeAssistant, socket_enabled: None) -> None:
    """An unreachable controller raises NeoConnectionError."""
    for protocol in ("http", "tcp"):
        client = NeoClient(
            "127.0.0.1", 1, protocol, HUB_ID, async_get_clientsession(hass)
        )
        with pytest.raises(NeoConnectionError):
            await client.async_send(BlindCommand("021.230-04", "up"))
    with pytest.raises(NeoConnectionError):
        await client.async_check_connection()


@pytest.mark.parametrize("protocol", ["http", "tcp"])
async def test_commands_are_spaced(
    hass: HomeAssistant, fake_controller: FakeController, protocol: str
) -> None:
    """Concurrent commands are sent one at a time with a gap between them."""
    client = _client(hass, fake_controller, protocol, spacing=0.2)
    await asyncio.gather(
        *(client.async_send(BlindCommand(f"021.230-0{i}", "up")) for i in range(1, 4))
    )
    assert len(fake_controller.received) == 3
    gaps = [
        b - a
        for a, b in zip(fake_controller.times, fake_controller.times[1:], strict=False)
    ]
    assert all(gap >= 0.19 for gap in gaps), gaps
