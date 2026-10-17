"""Tests for the cover and button entities."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_CLOSE_COVER,
    SERVICE_CLOSE_COVER_TILT,
    SERVICE_OPEN_COVER,
    SERVICE_OPEN_COVER_TILT,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.neosmartblinds.const import DOMAIN

from .conftest import (
    FakeController,
    blind_subentry,
    controller_entry,
    setup_entry,
)

COVER = "cover.blind_one"


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: COVER, **data}, blocking=True
    )


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, secs: float
) -> None:
    freezer.tick(timedelta(seconds=secs))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _position(hass: HomeAssistant) -> int:
    return hass.states.get(COVER).attributes[ATTR_CURRENT_POSITION]


async def test_setup_creates_entities(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """A cover and two favourite buttons are created on a device per blind."""
    entry = controller_entry(fake_controller, [blind_subentry(motor_code="no")])
    await setup_entry(hass, entry)
    state = hass.states.get(COVER)
    assert state is not None
    assert state.attributes["assumed_state"] is True
    features = state.attributes[ATTR_SUPPORTED_FEATURES]
    assert features & CoverEntityFeature.SET_POSITION
    assert features & CoverEntityFeature.OPEN_TILT
    assert not features & CoverEntityFeature.SET_TILT_POSITION
    assert hass.states.get("button.blind_one_favourite_position_1")
    assert hass.states.get("button.blind_one_favourite_position_2")
    ent = er.async_get(hass).async_get(COVER)
    assert ent.unique_id == "neosmartblinds.021.230-04.no.1"
    assert ent.config_subentry_id in entry.subentries


async def test_open_close_stop(
    hass: HomeAssistant, fake_controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """Open/close send up/dn and track travel; stop keeps the estimate."""
    await setup_entry(hass, controller_entry(fake_controller, [blind_subentry()]))
    assert _position(hass) == 50

    await _call(hass, SERVICE_CLOSE_COVER)
    assert fake_controller.received == ["021.230-04-dn!bf"]
    assert hass.states.get(COVER).state == CoverState.CLOSING
    await _advance(hass, freezer, 5)  # 20s close time -> 25% travelled
    assert _position(hass) == 25
    await _call(hass, SERVICE_STOP_COVER)
    assert fake_controller.actions[-1] == "021.230-04-sp"
    assert hass.states.get(COVER).state == CoverState.OPEN
    assert _position(hass) == 25

    await _call(hass, SERVICE_OPEN_COVER)
    assert fake_controller.actions[-1] == "021.230-04-up"
    await _advance(hass, freezer, 16)
    assert hass.states.get(COVER).state == CoverState.OPEN
    assert _position(hass) == 100
    # No stop is needed at the ends
    assert fake_controller.actions[-1] == "021.230-04-up"


async def test_emulated_positioning(
    hass: HomeAssistant, fake_controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """Mode 2 moves with up/down and stops after the calculated time."""
    await setup_entry(
        hass, controller_entry(fake_controller, [blind_subentry(start_position=100)])
    )
    assert _position(hass) == 100

    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 40})
    assert fake_controller.actions == ["021.230-04-dn"]
    await _advance(hass, freezer, 6)  # 60% of 20s = 12s
    assert _position(hass) == 70
    assert hass.states.get(COVER).state == CoverState.CLOSING

    # Retarget further in the same direction: no new command, longer timer
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 20})
    assert fake_controller.actions == ["021.230-04-dn"]
    await _advance(hass, freezer, 6.5)
    assert fake_controller.actions == ["021.230-04-dn"]
    await _advance(hass, freezer, 4)  # 16s total = 80%
    assert fake_controller.actions == ["021.230-04-dn", "021.230-04-sp"]
    assert _position(hass) == 20
    assert hass.states.get(COVER).state == CoverState.OPEN


async def test_emulated_direction_change(
    hass: HomeAssistant, fake_controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """Asking for a position behind the blind stops it and reverses."""
    await setup_entry(
        hass, controller_entry(fake_controller, [blind_subentry(start_position=0)])
    )
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 80})
    await _advance(hass, freezer, 10)  # at 50
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 30})
    assert fake_controller.actions == [
        "021.230-04-up",
        "021.230-04-sp",
        "021.230-04-dn",
    ]
    assert hass.states.get(COVER).state == CoverState.CLOSING
    await _advance(hass, freezer, 4.1)
    assert fake_controller.actions[-1] == "021.230-04-sp"
    assert _position(hass) == 30


async def test_explicit_positioning(
    hass: HomeAssistant, fake_controller: FakeController, freezer: FrozenDateTimeFactory
) -> None:
    """Mode 1 sends the inverted position and never needs a stop."""
    await setup_entry(
        hass,
        controller_entry(
            fake_controller,
            [blind_subentry(motor_code="no", percent_support=1, start_position=100)],
        ),
    )
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 30})
    assert fake_controller.received == ["021.230-04-70!no"]
    await _advance(hass, freezer, 5)
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 10})
    assert fake_controller.received[-1] == "021.230-04-90!no"
    await _advance(hass, freezer, 20)
    assert _position(hass) == 10
    assert "021.230-04-sp!no" not in fake_controller.received
    # The ends use up/down
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 99})
    assert fake_controller.received[-1] == "021.230-04-up!no"


async def test_favourite_only_mode(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Mode 0 keeps the v2 50/51 favourite shortcut and rejects other positions."""
    await setup_entry(
        hass, controller_entry(fake_controller, [blind_subentry(percent_support=0)])
    )
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 51})
    assert fake_controller.actions == ["021.230-04-i2"]
    assert _position(hass) == 51
    await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 50})
    assert fake_controller.actions[-1] == "021.230-04-gp"
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_SET_COVER_POSITION, **{ATTR_POSITION: 30})


async def test_buttons_and_tilt(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Favourite buttons send gp/i2 and tilt sends micro steps."""
    await setup_entry(hass, controller_entry(fake_controller, [blind_subentry()]))
    # bf motors don't document i2, so only favourite 1 exists
    assert hass.states.get("button.blind_one_favourite_position_2") is None
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.blind_one_favourite_position_1"},
        blocking=True,
    )
    await _call(hass, SERVICE_OPEN_COVER_TILT)
    await _call(hass, SERVICE_CLOSE_COVER_TILT)
    assert fake_controller.actions == [
        "021.230-04-gp",
        "021.230-04-mu",
        "021.230-04-md",
    ]


@pytest.mark.parametrize(
    ("rail", "extended", "motor", "expected"),
    [
        (2, False, "tb", ["u2", "d2", "sp", "o2"]),
        (3, False, "tb", ["u3", "d3", "sp"]),
        (1, True, "mb", ["u4", "d4", "x2"]),
        (2, True, "mb", ["u2", "d2", "x2"]),
    ],
)
async def test_rails(
    hass: HomeAssistant,
    fake_controller: FakeController,
    rail: int,
    extended: bool,
    motor: str,
    expected: list[str],
) -> None:
    """Top-down/bottom-up rails use their own command set."""
    await setup_entry(
        hass,
        controller_entry(
            fake_controller,
            [blind_subentry(rail=rail, tdbu_extended=extended, motor_code=motor)],
        ),
    )
    await _call(hass, SERVICE_OPEN_COVER)
    await _call(hass, SERVICE_CLOSE_COVER)
    await _call(hass, SERVICE_STOP_COVER)
    features = hass.states.get(COVER).attributes[ATTR_SUPPORTED_FEATURES]
    if len(expected) == 4:
        await _call(hass, SERVICE_OPEN_COVER_TILT)
    else:
        assert not features & CoverEntityFeature.OPEN_TILT
    assert [a.rsplit("-", 1)[1] for a in fake_controller.actions] == expected


async def test_restore_position(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """Without start_position the last position is restored."""
    mock_restore_cache(hass, [State(COVER, "open", {ATTR_CURRENT_POSITION: 37})])
    entry = controller_entry(fake_controller, [blind_subentry()])
    # Restore matches on entity_id, so pre-register it the way v2 would have
    er.async_get(hass).async_get_or_create(
        COVER_DOMAIN,
        DOMAIN,
        "neosmartblinds.021.230-04.bf.1",
        suggested_object_id="blind_one",
    )
    await setup_entry(hass, entry)
    assert _position(hass) == 37


async def test_start_position_wins(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """start_position overrides the restored state."""
    mock_restore_cache(hass, [State(COVER, "open", {ATTR_CURRENT_POSITION: 37})])
    await setup_entry(
        hass, controller_entry(fake_controller, [blind_subentry(start_position=80)])
    )
    assert _position(hass) == 80


async def test_bad_hub_id_starts_reauth(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """A 401 raises an error and starts a reauth flow."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    fake_controller.hub_id = "something-else"
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_OPEN_COVER)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    # State didn't change because nothing was sent
    assert hass.states.get(COVER).state == CoverState.OPEN


async def test_tcp_controller(
    hass: HomeAssistant, fake_controller: FakeController
) -> None:
    """The same entities work over TCP."""
    await setup_entry(
        hass, controller_entry(fake_controller, [blind_subentry()], protocol="tcp")
    )
    await _call(hass, SERVICE_OPEN_COVER)
    assert fake_controller.received == ["021.230-04-up!bf"]


async def test_unload(hass: HomeAssistant, fake_controller: FakeController) -> None:
    """Unloading cancels timers and removes entities."""
    entry = controller_entry(fake_controller, [blind_subentry()])
    await setup_entry(hass, entry)
    await _call(hass, SERVICE_CLOSE_COVER)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(COVER).state == "unavailable"
