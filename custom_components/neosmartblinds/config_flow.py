"""Config flow for NeoSmartBlinds: a controller entry with one subentry per blind."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import NeoClient, NeoConnectionError
from .blind import blind_unique_id, shared_rooms
from .const import (
    BLIND_CODE_RE,
    CONF_BLIND_CODE,
    CONF_CLOSE_TIME,
    CONF_HUB_ID,
    CONF_MOTOR_CODE,
    CONF_OPEN_TIME,
    CONF_PARENT,
    CONF_PERCENT_SUPPORT,
    CONF_PROTOCOL,
    CONF_RAIL,
    CONF_ROOM_NAMES,
    CONF_START_POSITION,
    CONF_STARTUP_SYNC,
    CONF_TDBU_EXTENDED,
    CONF_TILT,
    DEFAULT_CLOSE_TIME,
    DEFAULT_PORTS,
    DOMAIN,
    EXPLICIT_POSITIONING,
    IMPLICIT_POSITIONING,
    LEGACY_POSITIONING,
    MOTOR_CODES,
    POSITION_MOTOR_CODES,
    PROTOCOL_HTTP,
    PROTOCOL_TCP,
    RAIL_BOTH,
    RAIL_TOP,
    SUBENTRY_BLIND,
    SYNC_CLOSE,
    SYNC_NONE,
    SYNC_OPEN,
    TDBU_EXTENDED_MOTOR_CODES,
)

_LOGGER = logging.getLogger(__name__)

MOTOR_CODE_RE = re.compile(r"^[a-z0-9]{2}$")


def _address_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST): TextSelector(),
            vol.Required(CONF_PROTOCOL, default=PROTOCOL_HTTP): SelectSelector(
                SelectSelectorConfig(
                    options=[PROTOCOL_HTTP, PROTOCOL_TCP],
                    translation_key=CONF_PROTOCOL,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _connection_schema(protocol: str) -> vol.Schema:
    """Port (defaulting to the protocol's port) and controller id."""
    hub_id = vol.Required if protocol == PROTOCOL_HTTP else vol.Optional
    return vol.Schema(
        {
            vol.Required(CONF_PORT, default=DEFAULT_PORTS[protocol]): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
            ),
            hub_id(CONF_HUB_ID): TextSelector(),
        }
    )


def _normalise_controller(user_input: Mapping[str, Any]) -> dict[str, Any]:
    protocol = str(user_input.get(CONF_PROTOCOL, PROTOCOL_HTTP)).strip().lower()
    port = user_input.get(CONF_PORT)
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PROTOCOL: protocol,
        CONF_PORT: int(port) if port else DEFAULT_PORTS.get(protocol, 8838),
        CONF_HUB_ID: str(user_input.get(CONF_HUB_ID) or "").strip(),
    }


class NeoSmartBlindsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Neo Smart Blinds controller."""

    VERSION = 1
    MINOR_VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Blinds are added as subentries of the controller."""
        return {SUBENTRY_BLIND: BlindSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Name the rooms."""
        return RoomNamesOptionsFlow()

    async def _async_validate(self, data: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if data[CONF_PROTOCOL] == PROTOCOL_HTTP and not data[CONF_HUB_ID]:
            errors[CONF_HUB_ID] = "hub_id_required"
            return errors
        client = NeoClient(
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_PROTOCOL],
            data[CONF_HUB_ID],
            async_get_clientsession(self.hass),
        )
        try:
            await client.async_check_connection()
        except NeoConnectionError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error checking controller")
            errors["base"] = "unknown"
        return errors

    _address: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the controller's address and protocol."""
        if user_input is not None:
            self._address = {
                CONF_HOST: str(user_input[CONF_HOST]).strip(),
                CONF_PROTOCOL: user_input[CONF_PROTOCOL],
            }
            self._async_abort_entries_match({CONF_HOST: self._address[CONF_HOST]})
            return await self.async_step_connection()
        return self.async_show_form(step_id="user", data_schema=_address_schema())

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the port, prefilled for the protocol, and the controller id."""
        errors: dict[str, str] = {}
        protocol = self._address[CONF_PROTOCOL]
        if user_input is not None:
            data = _normalise_controller(self._address | user_input)
            await self.async_set_unique_id(data[CONF_HUB_ID] or data[CONF_HOST])
            self._abort_if_unique_id_configured()
            if not (errors := await self._async_validate(data)):
                return self.async_create_entry(
                    title=f"Neo controller ({data[CONF_HOST]})", data=data
                )
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(protocol), user_input
            ),
            errors=errors,
            description_placeholders={CONF_PROTOCOL: protocol.upper()},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the controller's address or protocol."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._address = {
                CONF_HOST: str(user_input[CONF_HOST]).strip(),
                CONF_PROTOCOL: user_input[CONF_PROTOCOL],
            }
            return await self.async_step_reconfigure_connection()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _address_schema(), entry.data
            ),
        )

    async def async_step_reconfigure_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the port and controller id."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        protocol = self._address[CONF_PROTOCOL]
        if user_input is not None:
            data = _normalise_controller(self._address | user_input)
            if not (errors := await self._async_validate(data)):
                return self.async_update_and_abort(entry, data=data)
        current = dict(entry.data)
        if current.get(CONF_PROTOCOL) != protocol:
            # Protocol changed: offer the new protocol's port
            current[CONF_PORT] = DEFAULT_PORTS[protocol]
        return self.async_show_form(
            step_id="reconfigure_connection",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(protocol), user_input or current
            ),
            errors=errors,
            description_placeholders={CONF_PROTOCOL: protocol.upper()},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The controller rejected the hub id."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a corrected hub id."""
        entry = self._get_reauth_entry()
        if user_input is not None:
            return self.async_update_and_abort(
                entry, data_updates={CONF_HUB_ID: user_input[CONF_HUB_ID].strip()}
            )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HUB_ID): TextSelector()}), entry.data
            ),
            description_placeholders={CONF_HOST: entry.data[CONF_HOST]},
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import one blind from the old YAML cover platform."""
        controller = _normalise_controller(import_data)
        if controller[CONF_PROTOCOL] not in DEFAULT_PORTS:
            return self.async_abort(reason="invalid_protocol")
        if not BLIND_CODE_RE.match(str(import_data[CONF_BLIND_CODE])):
            return self.async_abort(reason="invalid_blind_code")
        parent = str(import_data.get(CONF_PARENT) or "").strip()
        if parent and not BLIND_CODE_RE.match(parent):
            return self.async_abort(reason="invalid_parent_group")
        rail = int(import_data.get(CONF_RAIL, RAIL_TOP))
        percent = int(import_data.get(CONF_PERCENT_SUPPORT, LEGACY_POSITIONING))
        if not RAIL_TOP <= rail <= RAIL_BOTH:
            return self.async_abort(reason="invalid_rail")
        if not LEGACY_POSITIONING <= percent <= IMPLICIT_POSITIONING:
            return self.async_abort(reason="invalid_percent_support")

        blind: dict[str, Any] = {
            CONF_BLIND_CODE: import_data[CONF_BLIND_CODE],
            CONF_MOTOR_CODE: str(import_data.get(CONF_MOTOR_CODE) or "").lower(),
            CONF_RAIL: rail,
            CONF_TDBU_EXTENDED: False,
            CONF_PERCENT_SUPPORT: percent,
            CONF_CLOSE_TIME: int(import_data.get(CONF_CLOSE_TIME, DEFAULT_CLOSE_TIME)),
        }
        if import_data.get(CONF_START_POSITION) is not None:
            blind[CONF_START_POSITION] = int(import_data[CONF_START_POSITION])
        if parent:
            blind[CONF_PARENT] = parent
        unique_id = blind_unique_id(blind)
        title = str(import_data[CONF_NAME])

        unique = controller[CONF_HUB_ID] or controller[CONF_HOST]
        for entry in self._async_current_entries(include_ignore=False):
            if (
                entry.unique_id != unique
                and entry.data[CONF_HOST] != controller[CONF_HOST]
            ):
                continue
            if any(sub.unique_id == unique_id for sub in entry.subentries.values()):
                return self.async_abort(reason="already_configured")
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=blind,
                    subentry_type=SUBENTRY_BLIND,
                    title=title,
                    unique_id=unique_id,
                ),
            )
            return self.async_abort(reason="blind_imported")

        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Neo controller ({controller[CONF_HOST]})",
            data=controller,
            subentries=[
                ConfigSubentryData(
                    data=blind,
                    subentry_type=SUBENTRY_BLIND,
                    title=title,
                    unique_id=unique_id,
                )
            ],
        )


def _blind_schema() -> vol.Schema:
    motor_options = [
        SelectOptionDict(value=code, label=f"{code} – {label}")
        for code, label in MOTOR_CODES.items()
    ]
    return vol.Schema(
        {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(CONF_BLIND_CODE): TextSelector(),
            vol.Optional(CONF_MOTOR_CODE): SelectSelector(
                SelectSelectorConfig(
                    options=motor_options,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_RAIL, default=str(RAIL_TOP)): SelectSelector(
                SelectSelectorConfig(
                    options=["1", "2", "3"],
                    translation_key=CONF_RAIL,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_TDBU_EXTENDED, default=False): BooleanSelector(),
            vol.Required(CONF_TILT, default=False): BooleanSelector(),
            vol.Required(
                CONF_PERCENT_SUPPORT, default=str(LEGACY_POSITIONING)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=["0", "1", "2"],
                    translation_key=CONF_PERCENT_SUPPORT,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_CLOSE_TIME, default=DEFAULT_CLOSE_TIME): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=600,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_OPEN_TIME): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=600,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_STARTUP_SYNC, default=SYNC_NONE): SelectSelector(
                SelectSelectorConfig(
                    options=[SYNC_NONE, SYNC_OPEN, SYNC_CLOSE],
                    translation_key=CONF_STARTUP_SYNC,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_START_POSITION): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_PARENT): TextSelector(),
        }
    )


def _validate_blind(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (subentry data, errors) for the blind form."""
    errors: dict[str, str] = {}
    code = str(user_input[CONF_BLIND_CODE]).strip()
    motor = str(user_input.get(CONF_MOTOR_CODE) or "").strip().lower()
    rail = int(user_input[CONF_RAIL])
    extended = bool(user_input.get(CONF_TDBU_EXTENDED, False))
    percent = int(user_input[CONF_PERCENT_SUPPORT])
    parent = str(user_input.get(CONF_PARENT) or "").strip()

    if not BLIND_CODE_RE.match(code):
        errors[CONF_BLIND_CODE] = "invalid_blind_code"
    if motor and not MOTOR_CODE_RE.match(motor):
        errors[CONF_MOTOR_CODE] = "invalid_motor_code"
    if extended and motor not in TDBU_EXTENDED_MOTOR_CODES:
        errors[CONF_TDBU_EXTENDED] = "tdbu_extended_motor"
    if parent and not BLIND_CODE_RE.match(parent):
        errors[CONF_PARENT] = "invalid_blind_code"
    elif parent and parent == code:
        errors[CONF_PARENT] = "parent_is_blind"
    if percent == EXPLICIT_POSITIONING and (
        rail != RAIL_TOP or extended or (motor and motor not in POSITION_MOTOR_CODES)
    ):
        errors[CONF_PERCENT_SUPPORT] = "position_not_supported"

    data: dict[str, Any] = {
        CONF_BLIND_CODE: code,
        CONF_MOTOR_CODE: motor,
        CONF_RAIL: rail,
        CONF_TDBU_EXTENDED: extended,
        CONF_TILT: bool(user_input.get(CONF_TILT, False)),
        CONF_PERCENT_SUPPORT: percent,
        CONF_CLOSE_TIME: int(user_input[CONF_CLOSE_TIME]),
        CONF_STARTUP_SYNC: user_input.get(CONF_STARTUP_SYNC, SYNC_NONE),
    }
    if user_input.get(CONF_OPEN_TIME) is not None:
        data[CONF_OPEN_TIME] = int(user_input[CONF_OPEN_TIME])
    if user_input.get(CONF_START_POSITION) is not None:
        data[CONF_START_POSITION] = int(user_input[CONF_START_POSITION])
    if parent:
        data[CONF_PARENT] = parent
    return data, errors


def _form_values(subentry: ConfigSubentry) -> dict[str, Any]:
    values = dict(subentry.data)
    values[CONF_NAME] = subentry.title
    values[CONF_RAIL] = str(values.get(CONF_RAIL, RAIL_TOP))
    values[CONF_PERCENT_SUPPORT] = str(
        values.get(CONF_PERCENT_SUPPORT, LEGACY_POSITIONING)
    )
    if not values.get(CONF_MOTOR_CODE):
        values.pop(CONF_MOTOR_CODE, None)
    return values


class RoomNamesOptionsFlow(OptionsFlow):
    """Name the room covers made for shared room codes."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a name field for each room."""
        rooms = shared_rooms(self.config_entry.subentries.values())
        if not rooms:
            return self.async_abort(reason="no_rooms")
        if user_input is not None:
            names = {
                code: name
                for code in rooms
                if (name := str(user_input.get(code) or "").strip())
            }
            return self.async_create_entry(data={CONF_ROOM_NAMES: names})

        current: dict[str, str] = self.config_entry.options.get(CONF_ROOM_NAMES, {})
        schema = vol.Schema(
            {
                vol.Optional(
                    code, description={"suggested_value": current.get(code)}
                ): TextSelector()
                for code in rooms
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "rooms": "\n".join(
                    f"- {code}: {', '.join(names)}" for code, names in rooms.items()
                )
            },
        )


def _monotonic() -> float:
    """Stopwatch clock for measuring travel times."""
    return time.monotonic()


class BlindSubentryFlow(ConfigSubentryFlow):
    """Add or change a blind."""

    def _duplicate(self, data: dict[str, Any], ignore: str | None = None) -> bool:
        for sub in self._get_entry().subentries.values():
            if sub.subentry_id == ignore or sub.subentry_type != SUBENTRY_BLIND:
                continue
            if (
                sub.data.get(CONF_BLIND_CODE) == data[CONF_BLIND_CODE]
                and int(sub.data.get(CONF_RAIL, RAIL_TOP)) == data[CONF_RAIL]
            ):
                return True
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a blind."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate_blind(user_input)
            if not errors and self._duplicate(data):
                errors["base"] = "already_configured"
            if not errors:
                return self.async_create_entry(
                    title=str(user_input[CONF_NAME]).strip(),
                    data=data,
                    unique_id=blind_unique_id(data),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _blind_schema(), user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose between changing the settings and measuring the travel times."""
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["settings", "calibrate"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a blind. Its unique id is kept so the entity stays the same."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate_blind(user_input)
            if not errors and self._duplicate(data, ignore=subentry.subentry_id):
                errors["base"] = "already_configured"
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=str(user_input[CONF_NAME]).strip(),
                    data=data,
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                _blind_schema(), user_input or _form_values(subentry)
            ),
            errors=errors,
        )

    # Measuring the travel times -------------------------------------------------
    # The blind is driven through its cover entity so Home Assistant keeps track
    # of where it is; the user presses Submit the moment it stops.

    _cover: str
    _started: float
    _close_time: float
    _open_time: float

    async def _async_move(self, service: str) -> dict[str, str]:
        """Start the blind moving; return form errors."""
        try:
            await self.hass.services.async_call(
                COVER_DOMAIN, service, {ATTR_ENTITY_ID: self._cover}, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Couldn't move %s: %s", self._cover, err)
            return {"base": "cannot_send"}
        self._started = _monotonic()
        return {}

    def _calibrate_form(
        self, step_id: str, errors: dict[str, str] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": self._get_reconfigure_subentry().title},
        )

    async def async_step_calibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Explain, then open the blind fully so timing starts from the top."""
        if user_input is None:
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            if entry.state is not ConfigEntryState.LOADED:
                return self.async_abort(reason="not_loaded")
            cover = er.async_get(self.hass).async_get_entity_id(
                COVER_DOMAIN, DOMAIN, subentry.unique_id or ""
            )
            if cover is None or self.hass.states.get(cover) is None:
                return self.async_abort(reason="cover_unavailable")
            self._cover = cover
            return self._calibrate_form("calibrate")
        if errors := await self._async_move("open_cover"):
            return self._calibrate_form("calibrate", errors)
        return self._calibrate_form("calibrate_close")

    async def async_step_calibrate_close(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Once it's fully open, start closing and the stopwatch."""
        if errors := await self._async_move("close_cover"):
            return self._calibrate_form("calibrate_close", errors)
        return self._calibrate_form("calibrate_closed")

    async def async_step_calibrate_closed(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """The user pressed Submit when the blind stopped at the bottom."""
        self._close_time = _monotonic() - self._started
        return self._calibrate_form("calibrate_open")

    async def async_step_calibrate_open(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Start opening and the stopwatch."""
        if errors := await self._async_move("open_cover"):
            return self._calibrate_form("calibrate_open", errors)
        return self._calibrate_form("calibrate_opened")

    async def async_step_calibrate_opened(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """The user pressed Submit when the blind stopped at the top."""
        self._open_time = _monotonic() - self._started
        return await self.async_step_calibrate_save()

    async def async_step_calibrate_save(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the measured times and save them."""
        close_time = max(1, round(self._close_time))
        open_time = max(1, round(self._open_time))
        if user_input is not None:
            subentry = self._get_reconfigure_subentry()
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data={
                    **subentry.data,
                    CONF_CLOSE_TIME: close_time,
                    CONF_OPEN_TIME: open_time,
                },
            )
        return self.async_show_form(
            step_id="calibrate_save",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._get_reconfigure_subentry().title,
                "close_time": str(close_time),
                "open_time": str(open_time),
            },
        )
