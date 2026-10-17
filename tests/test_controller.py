"""Tests for room names, the connection sensor and diagnostics."""

from __future__ import annotations

import socket
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_OPEN_COVER, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.neosmartblinds.const import CONF_ROOM_NAMES, DOMAIN
from custom_components.neosmartblinds.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    HUB_ID,
    FakeController,
    blind_subentry,
    controller_entry,
    setup_entry,
)

ROOM = "021.230-15"
CONNECTION = "binary_sensor.neo_controller_127_0_0_1_connection"


def _room_device(hass: HomeAssistant) -> dr.DeviceEntry:
    entity_id = er.async_get(hass).async_get_entity_id(
        COVER_DOMAIN, DOMAIN, f"{DOMAIN}.{ROOM}.room"
    )
    device_id = er.async_get(hass).async_get(entity_id).device_id
    return dr.async_get(hass).async_get(device_id)


def _room_blinds():
    return [
        blind_subentry("Left", "021.230-01", parent_group=ROOM),
        blind_subentry("Right", "021.230-02", parent_group=ROOM),
    ]


async def test_name_rooms(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """Rooms are named from the controller's options."""
    entry = controller_entry(fake_controller, _room_blinds())
    await setup_entry(hass, entry)
    assert hass.states.get("cover.room_021_230_15") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert "Left, Right" in result["description_placeholders"]["rooms"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {ROOM: "  Lounge  "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ROOM_NAMES: {ROOM: "Lounge"}}
    await hass.async_block_till_done()

    assert _room_device(hass).name == "Lounge"

    # Clearing the name goes back to the default
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert entry.options == {CONF_ROOM_NAMES: {}}
    await hass.async_block_till_done()
    assert _room_device(hass).name == f"Room {ROOM}"


async def test_room_names_without_rooms(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The options explain that rooms need a shared room code."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_rooms"


async def test_connection_sensor(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The sensor is on while the controller answers."""
    await setup_entry(hass, controller_entry(fake_controller, [blind_subentry()]))
    assert hass.states.get(CONNECTION).state == STATE_ON


async def test_connection_sensor_offline(
    hass: HomeAssistant,
    fake_controller: FakeController,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The sensor turns off when the controller stops answering, and back on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    entry = controller_entry(fake_controller, [blind_subentry()], port=closed_port)
    await setup_entry(hass, entry)
    assert hass.states.get(CONNECTION).state == STATE_OFF

    # A command that reaches the controller turns it back on
    client = entry.runtime_data.client
    client.port = fake_controller.http_port
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: "cover.blind_one"},
        blocking=True,
    )
    assert hass.states.get(CONNECTION).state == STATE_ON

    # And the periodic check notices when it goes away again
    client.port = closed_port
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    # Interval jobs run as background tasks
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(CONNECTION).state == STATE_OFF


async def test_diagnostics(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Diagnostics hide the address and controller id and list recent commands."""
    entry = controller_entry(fake_controller, _room_blinds())
    await setup_entry(hass, entry)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: "cover.left"}, blocking=True
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["controller"]["host"] == "**REDACTED**"
    assert diag["controller"]["hub_id"] == "**REDACTED**"
    assert HUB_ID not in str(diag)
    assert sorted(b["title"] for b in diag["blinds"]) == ["Left", "Right"]
    assert diag["rooms"] == [ROOM]
    assert diag["connection"]["connected"] is True
    commands = [r["command"] for r in diag["connection"]["recent"]]
    assert "connection check" in commands
    assert "021.230-01-up!bf" in commands
