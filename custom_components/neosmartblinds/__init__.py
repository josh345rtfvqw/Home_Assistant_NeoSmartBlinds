"""The NeoSmartBlinds integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import notifications
from .api import NeoClient
from .blind import NeoBlind
from .const import (
    CONF_HUB_ID,
    CONF_PARENT,
    CONF_PROTOCOL,
    DOMAIN,
    SUBENTRY_BLIND,
    controller_title,
)
from .group import NeoGroup

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.COVER]


@dataclass
class NeoRuntimeData:
    """Objects shared by the blinds of one controller."""

    client: NeoClient
    groups: dict[str, NeoGroup] = field(default_factory=dict)
    blinds: dict[str, NeoBlind] = field(default_factory=dict)


type NeoConfigEntry = ConfigEntry[NeoRuntimeData]


def controller_identifier(entry: ConfigEntry) -> tuple[str, str]:
    """Return the device registry identifier of the controller."""
    return (DOMAIN, entry.unique_id or entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: NeoConfigEntry) -> bool:
    """Set up a Neo Smart Controller from a config entry."""
    client = NeoClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        protocol=entry.data[CONF_PROTOCOL],
        hub_id=entry.data.get(CONF_HUB_ID, ""),
        session=async_get_clientsession(hass),
    )
    runtime = NeoRuntimeData(client)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_BLIND:
            continue
        group = None
        if parent := subentry.data.get(CONF_PARENT):
            group = runtime.groups.setdefault(parent, NeoGroup(hass, client, parent))
        runtime.blinds[subentry.subentry_id] = NeoBlind(
            hass, entry, subentry, client, group
        )
    entry.runtime_data = runtime

    @callback
    def _connection_changed() -> None:
        if client.connected is False:
            notifications.async_notify_disconnected(hass, entry, client.last_error)
        elif client.connected:
            notifications.async_dismiss_disconnected(hass, entry)

    entry.async_on_unload(client.add_listener(_connection_changed))
    # A reload (for example after fixing the address) checks again from scratch
    entry.async_on_unload(lambda: notifications.async_dismiss_disconnected(hass, entry))

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={controller_identifier(entry)},
        manufacturer="Neo Smart Blinds",
        model="Neo Smart Controller",
        name=entry.title,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: NeoConfigEntry) -> bool:
    """Migrate an old config entry."""
    if entry.version > 1:
        # Downgraded from a future version
        return False
    if entry.minor_version < 2:
        # 1.2: favourite buttons are disabled by default, because the controller
        # can't report whether a favourite is saved. Disable the existing ones
        # too; the user enables them once they've set a favourite.
        ent_reg = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if (
                entity.domain == Platform.BUTTON
                and ".favourite_" in entity.unique_id
                and entity.disabled_by is None
            ):
                ent_reg.async_update_entity(
                    entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
                )
        hass.config_entries.async_update_entry(entry, minor_version=2)
        _LOGGER.debug("Migrated %s to version 1.2", entry.title)
    if entry.minor_version < 3:
        # 1.3: controllers are titled "Neo Smart Controller (<host>)". Only rename
        # entries still using the old default title, not ones the user renamed.
        title = entry.title
        if title == f"Neo controller ({entry.data[CONF_HOST]})":
            title = controller_title(entry.data[CONF_HOST])
        hass.config_entries.async_update_entry(entry, title=title, minor_version=3)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NeoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: NeoConfigEntry) -> None:
    """Reload when the controller settings or the set of blinds change."""
    await hass.config_entries.async_reload(entry.entry_id)
