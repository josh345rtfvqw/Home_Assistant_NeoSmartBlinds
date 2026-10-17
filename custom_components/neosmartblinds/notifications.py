"""Notifications shown in Home Assistant when a controller or command fails.

Each notice has a fixed id, so a repeated failure replaces it rather than adding
another, and it is dismissed once things work again.
"""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


def _disconnected_id(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_disconnected"


def _command_failed_id(entry: ConfigEntry, target: str) -> str:
    return f"{DOMAIN}_{entry.entry_id}_{target}_command_failed"


@callback
def async_notify_disconnected(
    hass: HomeAssistant, entry: ConfigEntry, error: str | None
) -> None:
    """Say that the controller stopped answering."""
    message = (
        f"Home Assistant can't reach **{entry.title}**, so its blinds won't respond. "
        "Check that the controller is powered on and connected to your network, and "
        "that its IP address hasn't changed (use **Reconfigure** on the controller "
        "to update it).\n\n"
        "This notice goes away by itself when the controller answers again."
    )
    if error:
        message += f"\n\nError: `{error}`"
    persistent_notification.async_create(
        hass,
        message,
        title="Neo Smart Controller offline",
        notification_id=_disconnected_id(entry),
    )


@callback
def async_dismiss_disconnected(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the offline notice."""
    persistent_notification.async_dismiss(hass, _disconnected_id(entry))


@callback
def async_notify_command_failed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: str,
    name: str,
    command: str,
    error: str,
) -> None:
    """Say that a command to a blind or room wasn't sent.

    target identifies the blind or room, so each has at most one notice.
    """
    persistent_notification.async_create(
        hass,
        f"The command `{command}` to **{name}** wasn't sent: {error}\n\n"
        "The blind probably didn't move. Try again, or check the controller's "
        "connection.\n\n"
        "This notice goes away the next time a command to it is sent.",
        title="Neo Smart Blinds: command failed",
        notification_id=_command_failed_id(entry, target),
    )


@callback
def async_dismiss_command_failed(
    hass: HomeAssistant, entry: ConfigEntry, target: str
) -> None:
    """Remove the failed-command notice for a blind or room."""
    persistent_notification.async_dismiss(hass, _command_failed_id(entry, target))
