"""Tests for room (channel 15) command aggregation."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import FakeController, blind_subentry, controller_entry, setup_entry

ROOM = "021.230-15"


async def _setup_room(hass: HomeAssistant, controller: FakeController) -> None:
    await setup_entry(
        hass,
        controller_entry(
            controller,
            [
                blind_subentry("Left", "021.230-01", parent_group=ROOM),
                blind_subentry("Middle", "021.230-02", parent_group=ROOM),
                blind_subentry("Right", "021.230-03", parent_group=ROOM),
                blind_subentry("Elsewhere", "021.231-01"),
            ],
        ),
    )


async def _call(hass: HomeAssistant, service: str, entities: list[str]) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: entities}, blocking=True
    )


async def _call_and_wait_window(
    hass: HomeAssistant, *calls: tuple[str, list[str]]
) -> None:
    """Make calls that will wait for the aggregation window, then end the window."""
    task = asyncio.gather(*(_call(hass, service, ents) for service, ents in calls))
    for _ in range(10):
        await asyncio.sleep(0.01)
    assert not task.done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await task


async def test_whole_room_sends_one_command(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The same command to every blind in the room becomes one room command."""
    await _setup_room(hass, fake_controller)
    await _call(
        hass, SERVICE_CLOSE_COVER, ["cover.left", "cover.middle", "cover.right"]
    )
    assert fake_controller.received == [f"{ROOM}-dn!bf"]
    for entity in ("cover.left", "cover.middle", "cover.right"):
        assert hass.states.get(entity).state == "closing"


async def test_separate_calls_within_window(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Separate service calls arriving within 250 ms are aggregated too."""
    await _setup_room(hass, fake_controller)
    await asyncio.gather(
        _call(hass, SERVICE_OPEN_COVER, ["cover.left"]),
        _call(hass, SERVICE_OPEN_COVER, ["cover.middle"]),
        _call(hass, SERVICE_OPEN_COVER, ["cover.right"]),
    )
    assert fake_controller.received == [f"{ROOM}-up!bf"]


async def test_part_of_room_sends_individually(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """When only some blinds get the command, each is sent after the window."""
    await _setup_room(hass, fake_controller)
    await _call_and_wait_window(
        hass, (SERVICE_CLOSE_COVER, ["cover.left", "cover.right"])
    )
    assert sorted(fake_controller.received) == [
        "021.230-01-dn!bf",
        "021.230-03-dn!bf",
    ]


async def test_different_commands_are_not_merged(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """A different action flushes what has been collected."""
    await _setup_room(hass, fake_controller)
    await _call_and_wait_window(
        hass,
        (SERVICE_CLOSE_COVER, ["cover.left", "cover.middle"]),
        (SERVICE_STOP_COVER, ["cover.right"]),
    )
    assert sorted(fake_controller.received) == [
        "021.230-01-dn!bf",
        "021.230-02-dn!bf",
        "021.230-03-sp!bf",
    ]


async def test_blind_without_group_is_immediate(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Blinds without a parent group skip the aggregation window."""
    await _setup_room(hass, fake_controller)
    await _call(hass, SERVICE_OPEN_COVER, ["cover.elsewhere"])
    assert fake_controller.received == ["021.231-01-up!bf"]


async def test_room_failure_reaches_every_blind(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """If the room command fails, every caller sees the error and nothing moves."""
    await _setup_room(hass, fake_controller)
    fake_controller.status = 400
    try:
        await _call(
            hass, SERVICE_CLOSE_COVER, ["cover.left", "cover.middle", "cover.right"]
        )
    except Exception as err:  # noqa: BLE001
        assert "400" in str(err)
    else:
        raise AssertionError("expected an error")
    for entity in ("cover.left", "cover.middle", "cover.right"):
        assert hass.states.get(entity).state == "open"


ROOM_COVER = "cover.room_021_230_15"


async def test_room_cover_sends_one_command(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """A room cover is created for a shared room code and moves every member."""
    await _setup_room(hass, fake_controller)
    state = hass.states.get(ROOM_COVER)
    assert state is not None
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & CoverEntityFeature.SET_POSITION

    await _call(hass, SERVICE_CLOSE_COVER, [ROOM_COVER])
    await hass.async_block_till_done()
    assert fake_controller.actions == [f"{ROOM}-dn"]
    for blind in ("left", "middle", "right"):
        assert hass.states.get(f"cover.{blind}").state == CoverState.CLOSING
    assert hass.states.get("cover.elsewhere").state != CoverState.CLOSING
    assert hass.states.get(ROOM_COVER).state == CoverState.CLOSING


async def test_room_cover_position_is_average(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The room position is the average of its blinds."""
    await setup_entry(
        hass,
        controller_entry(
            fake_controller,
            [
                blind_subentry(
                    "Left", "021.230-01", parent_group=ROOM, start_position=20
                ),
                blind_subentry(
                    "Right", "021.230-02", parent_group=ROOM, start_position=60
                ),
            ],
        ),
    )
    assert hass.states.get(ROOM_COVER).attributes[ATTR_CURRENT_POSITION] == 40


async def test_no_room_cover_without_shared_code(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """One blind with a room code, or a room added as a blind, gets no extra cover."""
    await setup_entry(
        hass,
        controller_entry(
            fake_controller,
            [
                blind_subentry("Left", "021.230-01", parent_group=ROOM),
                blind_subentry("Other", "021.231-01", parent_group="021.231-15"),
                blind_subentry("Other 2", "021.231-02", parent_group="021.231-15"),
                blind_subentry("Other room", "021.231-15"),
            ],
        ),
    )
    assert hass.states.get(ROOM_COVER) is None
    assert hass.states.get("cover.room_021_231_15") is None
    assert hass.states.get("cover.other_room") is not None


async def test_room_cover_without_positioning(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Rooms of favourites-only blinds show buttons, not a slider."""
    await setup_entry(
        hass,
        controller_entry(
            fake_controller,
            [
                blind_subentry(
                    "Left", "021.230-01", parent_group=ROOM, percent_support=0
                ),
                blind_subentry("Right", "021.230-02", parent_group=ROOM),
            ],
        ),
    )
    state = hass.states.get(ROOM_COVER)
    assert (
        not state.attributes[ATTR_SUPPORTED_FEATURES] & CoverEntityFeature.SET_POSITION
    )
    assert ATTR_CURRENT_POSITION not in state.attributes
