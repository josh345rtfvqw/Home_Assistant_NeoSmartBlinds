"""Favourite position buttons for NeoSmartBlinds."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NeoConfigEntry
from .blind import NeoBlind

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up favourite buttons for each blind."""
    for subentry_id, blind in entry.runtime_data.blinds.items():
        buttons = [NeoFavouriteButton(blind, 1)]
        if blind.supports_favourite_2:
            buttons.append(NeoFavouriteButton(blind, 2))
        async_add_entities(buttons, config_subentry_id=subentry_id)


class NeoFavouriteButton(ButtonEntity):
    """Send a blind to one of its stored favourite positions."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # The controller can't say whether a favourite has been saved on the blind,
    # so leave the buttons for the user to enable once they've set one.
    _attr_entity_registry_enabled_default = False

    def __init__(self, blind: NeoBlind, number: int) -> None:
        """Initialise the button."""
        self._blind = blind
        self._number = number
        self._attr_translation_key = f"favourite_{number}"
        self._attr_unique_id = f"{blind.unique_id}.favourite_{number}"
        self._attr_device_info = blind.device_info

    async def async_press(self) -> None:
        """Go to the favourite position."""
        await self._blind.async_favourite(self._number)
