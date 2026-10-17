"""Cover entities for NeoSmartBlinds.

The controller never reports position, so position is estimated from the time a
blind takes to open (open_time) and close (close_time), and restored across
restarts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.components.cover import (
    PLATFORM_SCHEMA as COVER_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
)
from homeassistant.core import (
    CoreState,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import NeoConfigEntry
from .blind import NeoBlind
from .const import (
    CONF_BLIND_CODE,
    CONF_CLOSE_TIME,
    CONF_HUB_ID,
    CONF_MOTOR_CODE,
    CONF_PARENT,
    CONF_PERCENT_SUPPORT,
    CONF_PROTOCOL,
    CONF_RAIL,
    CONF_START_POSITION,
    DOMAIN,
    EXPLICIT_POSITIONING,
    LEGACY_POSITIONING,
    SYNC_NONE,
    SYNC_OPEN,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

PROGRESS_INTERVAL = timedelta(seconds=1)

# Old YAML options, kept so existing configurations can be imported.
PLATFORM_SCHEMA = COVER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_BLIND_CODE): cv.string,
        vol.Optional(CONF_CLOSE_TIME, default=20): cv.positive_int,
        vol.Required(CONF_HUB_ID): cv.string,
        vol.Optional(CONF_PROTOCOL, default="http"): cv.string,
        vol.Optional(CONF_PORT, default=8838): cv.port,
        vol.Optional(CONF_RAIL, default=1): cv.positive_int,
        vol.Optional(CONF_PERCENT_SUPPORT, default=0): cv.positive_int,
        vol.Optional(CONF_MOTOR_CODE, default=""): cv.string,
        vol.Optional(CONF_START_POSITION): cv.positive_int,
        vol.Optional(CONF_PARENT): cv.string,
    }
)

_IMPORT_LOCK = f"{DOMAIN}_import_lock"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: Callable[..., None],
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Import a YAML-configured blind into a config entry."""
    data = {k: v for k, v in config.items() if k != "platform"}
    # Each YAML blind is imported separately; run them one at a time so blinds on
    # the same controller end up in the same entry.
    lock = hass.data.setdefault(_IMPORT_LOCK, asyncio.Lock())
    async with lock:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=data
        )
    if result["type"] is FlowResultType.ABORT and result["reason"] not in (
        "already_configured",
        "blind_imported",
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"yaml_import_failed_{result['reason']}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="yaml_import_failed",
            translation_placeholders={
                "name": str(config[CONF_NAME]),
                "reason": str(result["reason"]),
            },
        )
        return
    ir.async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        breaks_in_ha_version=None,
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "NeoSmartBlinds",
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a cover for each blind subentry."""
    for subentry_id, blind in entry.runtime_data.blinds.items():
        async_add_entities([NeoSmartBlindsCover(blind)], config_subentry_id=subentry_id)


@dataclass
class _Move:
    """A movement in progress, used to estimate position."""

    start_position: int
    target: int
    started: datetime
    travel_time: float  # seconds to travel the full height in this direction
    needs_stop: bool
    cancel_timer: Callable[[], None] | None = None

    @property
    def direction(self) -> int:
        return 1 if self.target > self.start_position else -1

    @property
    def duration(self) -> float:
        return abs(self.target - self.start_position) * self.travel_time / 100

    def estimate(self) -> int:
        elapsed = (dt_util.utcnow() - self.started).total_seconds()
        if self.duration <= 0 or elapsed >= self.duration:
            return self.target
        travelled = elapsed * 100 / self.travel_time
        return round(self.start_position + self.direction * travelled)

    def cancel(self) -> None:
        if self.cancel_timer is not None:
            self.cancel_timer()
            self.cancel_timer = None


class NeoSmartBlindsCover(CoverEntity, RestoreEntity):
    """A Neo blind; position is estimated, never reported by the controller."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = CoverDeviceClass.BLIND
    _attr_assumed_state = True
    _attr_should_poll = False

    def __init__(self, blind: NeoBlind) -> None:
        """Initialise the cover."""
        self._blind = blind
        self._attr_unique_id = blind.unique_id
        self._attr_device_info = blind.device_info
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )
        # Without positioning the more-info dialog shows open/stop/close buttons
        # instead of a slider that can't do anything useful.
        if blind.percent_support != LEGACY_POSITIONING:
            features |= CoverEntityFeature.SET_POSITION
        if blind.tilt and blind.commands.micro_up and blind.commands.micro_down:
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
        self._attr_supported_features = features
        self._position = 50
        self._move: _Move | None = None
        self._cancel_progress: Callable[[], None] | None = None
        self._done_job = HassJob(self._async_move_done, cancel_on_shutdown=True)

    # State ------------------------------------------------------------------

    @property
    def current_cover_position(self) -> int | None:
        """Return the (estimated) position, 0 closed and 100 open."""
        if self._blind.percent_support == LEGACY_POSITIONING:
            return None
        return self._estimated_position

    @property
    def _estimated_position(self) -> int:
        """Return the position tracked internally, also in mode 0."""
        if self._move is not None:
            return self._move.estimate()
        return self._position

    @property
    def is_closed(self) -> bool:
        """Return whether the blind is closed."""
        return self._move is None and self._position == 0

    @property
    def is_opening(self) -> bool:
        """Return whether the blind is opening."""
        return self._move is not None and self._move.direction > 0

    @property
    def is_closing(self) -> bool:
        """Return whether the blind is closing."""
        return self._move is not None and self._move.direction < 0

    async def async_added_to_hass(self) -> None:
        """Restore position and join the room group."""
        await super().async_added_to_hass()
        self._move_area_to_device()
        blind = self._blind
        last = await self.async_get_last_state()
        if last is not None and isinstance(
            pos := last.attributes.get(ATTR_CURRENT_POSITION), int
        ):
            self._position = pos
        elif last is not None and last.state == CoverState.CLOSED:
            # Mode 0 has no position attribute; keep open/closed across restarts
            self._position = 0
        elif (
            blind.percent_support != LEGACY_POSITIONING
            and blind.start_position is not None
        ):
            # Nothing saved yet (first start): assume the configured position
            self._position = max(0, min(100, int(blind.start_position)))
        if blind.startup_sync != SYNC_NONE and self.hass.state is not CoreState.running:
            # Only when Home Assistant starts, not when the entry is reloaded
            self.async_on_remove(async_at_started(self.hass, self._async_startup_sync))
        if blind.group is not None:
            self.async_on_remove(blind.group.register(blind.unique_id))
        self.async_on_remove(blind.add_favourite_listener(self._on_favourite))

    async def _async_startup_sync(self, _hass: HomeAssistant) -> None:
        """Drive the blind to a known end so the estimated position is real."""
        _LOGGER.debug(
            "%s re-syncing position: %s", self.entity_id, self._blind.startup_sync
        )
        try:
            if self._blind.startup_sync == SYNC_OPEN:
                await self.async_open_cover()
            else:
                await self.async_close_cover()
        except HomeAssistantError as err:
            _LOGGER.warning("Couldn't re-sync %s at startup: %s", self.entity_id, err)

    def _move_area_to_device(self) -> None:
        """Move an area set on the entity onto the blind's device.

        v2 blinds had no device, so their area was set on the entity. The cover now
        takes its name from the device, and HA flags an unnamed entity with its own
        area, so hand the area to the device (and the favourite buttons with it).
        """
        entity = self.registry_entry
        if entity is None or entity.area_id is None or entity.device_id is None:
            return
        dev_reg = dr.async_get(self.hass)
        if (device := dev_reg.async_get(entity.device_id)) is None:
            return
        if device.area_id is None:
            dev_reg.async_update_device(device.id, area_id=entity.area_id)
        elif device.area_id != entity.area_id:
            # The user picked different areas on purpose; leave them alone.
            return
        er.async_get(self.hass).async_update_entity(entity.entity_id, area_id=None)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel timers."""
        self._cancel_move()

    # Services ---------------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open fully."""
        await self._async_start_move(100, self._blind.async_up, needs_stop=False)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close fully."""
        await self._async_start_move(0, self._blind.async_down, needs_stop=False)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop moving."""
        await self._blind.async_stop()
        self._halt()

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Micro-step up."""
        await self._blind.async_micro_up()

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Micro-step down."""
        await self._blind.async_micro_down()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move to a position."""
        position: int = kwargs[ATTR_POSITION]
        blind = self._blind

        # The controller only accepts 01-99; treat the ends as full open/close.
        if position > 98:
            await self.async_open_cover()
            return
        if position < 2:
            await self.async_close_cover()
            return

        if (move := self._move) is not None:
            current = move.estimate()
            ahead = position >= current if move.direction > 0 else position <= current
            if ahead and position != current:
                await self._async_retarget(move, position)
                return
            # Wrong direction (or already there): stop before moving again
            await self._blind.async_stop()
            self._halt()

        if position == self._position:
            return
        if blind.percent_support == EXPLICIT_POSITIONING:
            await self._async_start_move(
                position, lambda: blind.async_position(position), needs_stop=False
            )
        else:
            send = blind.async_up if position > self._position else blind.async_down
            await self._async_start_move(position, send, needs_stop=True)

    # Movement tracking ------------------------------------------------------

    async def _async_start_move(
        self,
        target: int,
        send: Callable[[], Awaitable[None]],
        *,
        needs_stop: bool,
    ) -> None:
        """Send the command and track the move until it should be complete."""
        start = self._estimated_position
        await send()
        self._cancel_move()
        travel_time = (
            self._blind.open_time if target > start else self._blind.close_time
        )
        move = _Move(start, target, dt_util.utcnow(), travel_time, needs_stop)
        self._move = move
        self._position = target
        _LOGGER.debug(
            "%s moving %s -> %s over %.1fs",
            self.entity_id,
            start,
            target,
            move.duration,
        )
        self._schedule_done(move)
        self._cancel_progress = async_track_time_interval(
            self.hass, self._progress, PROGRESS_INTERVAL, cancel_on_shutdown=True
        )
        self.async_write_ha_state()

    async def _async_retarget(self, move: _Move, position: int) -> None:
        """Change the target of a move already heading the right way."""
        if self._blind.percent_support == EXPLICIT_POSITIONING:
            await self._blind.async_position(position)
            move.needs_stop = False
        else:
            move.needs_stop = True
        move.target = position
        self._position = position
        move.cancel()
        self._schedule_done(move)
        self.async_write_ha_state()

    def _schedule_done(self, move: _Move) -> None:
        remaining = move.duration - (dt_util.utcnow() - move.started).total_seconds()
        move.cancel_timer = async_call_later(
            self.hass, max(remaining, 0), self._done_job
        )

    async def _async_move_done(self, _now: datetime) -> None:
        """The blind should have reached its target."""
        move = self._move
        if move is None:
            return
        move.cancel_timer = None
        self._cancel_move()
        self._position = move.target
        self.async_write_ha_state()
        if move.needs_stop:
            try:
                await self._blind.async_stop()
            except HomeAssistantError as err:
                _LOGGER.warning("%s: could not stop blind: %s", self.entity_id, err)

    @callback
    def _progress(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @callback
    def _halt(self) -> None:
        """Forget the current move, keeping the estimated position."""
        if self._move is not None:
            self._position = self._move.estimate()
        self._cancel_move()
        self.async_write_ha_state()

    @callback
    def _cancel_move(self) -> None:
        if self._move is not None:
            self._move.cancel()
            self._move = None
        if self._cancel_progress is not None:
            self._cancel_progress()
            self._cancel_progress = None

    @callback
    def _on_favourite(self, number: int) -> None:
        """A favourite was selected; the real position is unknown."""
        position = self._estimated_position
        self._cancel_move()
        if position == 0:
            # A favourite is never fully closed
            position = 50
        self._position = position
        self.async_write_ha_state()
