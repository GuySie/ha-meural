"""The Meural integration."""
import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from . import pymeural

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

PLATFORMS = ["media_player"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Meural component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Meural from a config entry."""
    if "email" not in entry.data:
        _LOGGER.warning("Authentication changed. Please set up Meural again")
        return False

    def token_update_callback(token):
        _LOGGER.debug("Token changed. Updating config entry.")
        hass.config_entries.async_update_entry(entry, data={**entry.data, "token": token})

    hass.data[DOMAIN][entry.entry_id] = pymeural.PyMeural(
        entry.data["email"],
        entry.data["password"],
        entry.data["token"],
        token_update_callback,
        hass.helpers.aiohttp_client.async_get_clientsession()
    )

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

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
