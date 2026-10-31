"""The Meural integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant.const import CONF_HOST

from .const import CONF_DEVICE, CONF_LOCAL_ONLY, DOMAIN
from . import pymeural
from .coordinator import CloudDataUpdateCoordinator, LocalDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["media_player", "sensor", "light", "switch", "select", "number"]
# Platforms available in local-only mode (cloud-backed number/switch are skipped).
LOCAL_PLATFORMS = ["media_player", "sensor", "light", "select"]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Meural component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meural from a config entry."""
    if entry.data.get(CONF_LOCAL_ONLY):
        return await _async_setup_local_entry(hass, entry)

    if "email" not in entry.data:
        _LOGGER.warning("Meural: Authentication changed. Please set up Meural again")
        return False

    # Earlier builds persisted the account password alongside the tokens.
    # It is never read back (reauth asks interactively), so scrub it.
    if "password" in entry.data:
        _LOGGER.info(
            "Meural: Removing stored account password from config entry; "
            "it is no longer used"
        )
        data = dict(entry.data)
        data.pop("password")
        hass.config_entries.async_update_entry(entry, data=data)

    def token_update_callback(
        token: str,
        refresh_token: str,
        expires_at: float,
        trust_id: str,
    ) -> None:
        """Persist rotated Meural OAuth tokens."""
        _LOGGER.debug("Meural: Tokens updated. Saving to config entry")
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "token": token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "trust_id": trust_id,
            },
        )

    # Create PyMeural instance with token refresh callback
    meural = pymeural.PyMeural(
        entry.data.get("token"),
        token_update_callback,
        async_get_clientsession(hass),
        entry.entry_id,
        refresh_token=entry.data.get("refresh_token"),
        expires_at=entry.data.get("expires_at"),
        trust_id=entry.data.get("trust_id"),
    )

    # Create and initialize CloudDataUpdateCoordinator
    cloud_coordinator = CloudDataUpdateCoordinator(hass, meural, entry)

    # Perform first refresh
    await cloud_coordinator.async_config_entry_first_refresh()

    # Populate gallery data synchronously so it is available immediately
    await cloud_coordinator.async_refresh_galleries()

    # Create and initialize a LocalDataUpdateCoordinator for each device
    devices = list(cloud_coordinator.data["devices"].values())
    local_coordinators = {}
    for device in devices:
        local_coordinator = LocalDataUpdateCoordinator(
            hass,
            device,
            async_get_clientsession(hass),
        )
        await local_coordinator.async_config_entry_first_refresh()
        local_coordinators[str(device["id"])] = local_coordinator

    # Register local coordinators with the cloud coordinator so it can
    # dynamically adjust its polling interval based on device sleep states.
    for device_id, local_coordinator in local_coordinators.items():
        cloud_coordinator.register_local_coordinator(device_id, local_coordinator)
        local_coordinator.cloud_coordinator = cloud_coordinator
    cloud_coordinator.notify_sleep_state_changed()

    # Store meural instance, coordinators, and devices in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "meural": meural,
        "cloud_coordinator": cloud_coordinator,
        "local_coordinators": local_coordinators,
        "devices": cloud_coordinator.data["devices"],
        "local_only": False,
    }

    # Forward to platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_setup_local_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a local-only Meural entry (single Canvas, no NETGEAR cloud)."""
    device = dict(entry.data[CONF_DEVICE])
    # The stored IP is the source of truth and may have been edited via reconfigure.
    device["localIp"] = entry.data[CONF_HOST]
    device_id = str(device["id"])

    local_coordinator = LocalDataUpdateCoordinator(
        hass,
        device,
        async_get_clientsession(hass),
    )
    await local_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "meural": None,
        "cloud_coordinator": None,
        "local_coordinators": {device_id: local_coordinator},
        "devices": {device_id: device},
        "local_only": True,
    }

    await hass.config_entries.async_forward_entry_setups(entry, LOCAL_PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    platforms = LOCAL_PLATFORMS if entry.data.get(CONF_LOCAL_ONLY) else PLATFORMS
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in platforms
            ]
        )
    )
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        cloud_coordinator = entry_data["cloud_coordinator"]
        if cloud_coordinator is not None:
            for device_id in entry_data["local_coordinators"]:
                cloud_coordinator.unregister_local_coordinator(device_id)

    return unload_ok
