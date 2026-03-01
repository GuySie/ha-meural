"""The Meural integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from . import pymeural
from .coordinator import CloudDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["media_player"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Meural component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meural from a config entry."""
    if "email" not in entry.data:
        _LOGGER.warning("Authentication changed. Please set up Meural again")
        return False

    def token_update_callback(token: str, refresh_token: str) -> None:
        """Update both access token and refresh token in config entry."""
        _LOGGER.debug("Tokens updated. Saving to config entry.")
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "token": token, "refresh_token": refresh_token}
        )

    # Create PyMeural instance with token refresh callback
    meural = pymeural.PyMeural(
        entry.data["email"],
        entry.data["password"],
        entry.data.get("token"),
        token_update_callback,
        async_get_clientsession(hass),
        refresh_token=entry.data.get("refresh_token"),
    )

    # Create and initialize CloudDataUpdateCoordinator
    cloud_coordinator = CloudDataUpdateCoordinator(hass, meural, entry)

    # Perform first refresh
    await cloud_coordinator.async_config_entry_first_refresh()

    # Populate gallery data synchronously so it is available immediately
    await cloud_coordinator.async_refresh_galleries()

    # Store meural instance and coordinator in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "meural": meural,
        "cloud_coordinator": cloud_coordinator,
    }

    # Forward to platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
