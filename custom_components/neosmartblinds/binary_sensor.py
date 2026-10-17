"""Controller connection sensor for NeoSmartBlinds."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import NeoConfigEntry, controller_identifier
from .api import NeoClient, NeoError

PARALLEL_UPDATES = 0

# The controller has no status call, so check now and then that it accepts a
# connection. Commands update the status as well.
PROBE_INTERVAL = timedelta(minutes=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NeoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the connection sensor for the controller."""
    async_add_entities([NeoConnectionSensor(entry)])


class NeoConnectionSensor(BinarySensorEntity):
    """Whether the controller is answering."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: NeoConfigEntry) -> None:
        """Initialise the sensor."""
        self._client: NeoClient = entry.runtime_data.client
        identifier = controller_identifier(entry)
        self._attr_unique_id = f"{identifier[1]}.connection"
        self._attr_device_info = DeviceInfo(identifiers={identifier})

    @property
    def is_on(self) -> bool | None:
        """Return whether the last exchange with the controller worked."""
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        """Follow the client's status and check the controller periodically."""
        self.async_on_remove(self._client.add_listener(self.async_write_ha_state))
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_probe, PROBE_INTERVAL, cancel_on_shutdown=True
            )
        )
        if self._client.connected is None:
            self.hass.async_create_task(self._async_probe(None))

    async def _async_probe(self, _now: datetime | None) -> None:
        try:
            await self._client.async_probe()
        except NeoError:
            pass  # recorded by the client and shown as disconnected
