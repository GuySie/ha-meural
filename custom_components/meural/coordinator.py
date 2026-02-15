"""DataUpdateCoordinator for Meural integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CLOUD_UPDATE_INTERVAL,
    CLOUD_UPDATE_INTERVAL_SLEEPING,
    LOCAL_UPDATE_INTERVAL,
)
from .pymeural import DeviceTurnedOff, InvalidAuth, LocalMeural, PyMeural

_LOGGER = logging.getLogger(__name__)


class CloudDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Meural cloud API data."""

    def __init__(
        self,
        hass: HomeAssistant,
        meural: PyMeural,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.meural = meural
        self.entry = entry
        self._update_interval = timedelta(seconds=CLOUD_UPDATE_INTERVAL)
        self._local_coordinators: dict[str, Any] = {}

        super().__init__(
            hass,
            _LOGGER,
            name="Meural Cloud",
            update_interval=self._update_interval,
        )

    def set_update_interval(self, sleeping: bool) -> None:
        """Adjust update interval based on device sleep state.

        Deprecated: This method is kept for backward compatibility.
        Use register_local_coordinator() instead - the coordinator now
        automatically manages intervals based on all devices' sleep states.
        """
        if sleeping:
            self.update_interval = timedelta(seconds=CLOUD_UPDATE_INTERVAL_SLEEPING)
        else:
            self.update_interval = timedelta(seconds=CLOUD_UPDATE_INTERVAL)

    def register_local_coordinator(
        self, device_id: str, local_coordinator: "LocalDataUpdateCoordinator"
    ) -> None:
        """Register a local coordinator for sleep state tracking."""
        self._local_coordinators[device_id] = local_coordinator
        self._update_polling_interval()

    def unregister_local_coordinator(self, device_id: str) -> None:
        """Unregister a local coordinator."""
        self._local_coordinators.pop(device_id, None)
        self._update_polling_interval()

    def _update_polling_interval(self) -> None:
        """Update polling interval based on all devices' sleep states."""
        # If any device is awake, use fast interval
        # Only use slow interval if ALL devices are sleeping or no devices registered
        any_awake = any(
            not coord.sleeping for coord in self._local_coordinators.values()
        )

        if any_awake:
            new_interval = timedelta(seconds=CLOUD_UPDATE_INTERVAL)
        else:
            new_interval = timedelta(seconds=CLOUD_UPDATE_INTERVAL_SLEEPING)

        # Only update if interval actually changed to avoid unnecessary refreshes
        if self.update_interval != new_interval:
            awake_count = sum(
                1 for coord in self._local_coordinators.values() if not coord.sleeping
            )
            _LOGGER.debug(
                "Meural Cloud: Adjusting update interval to %s seconds (%d awake devices)",
                new_interval.total_seconds(),
                awake_count,
            )
            self.update_interval = new_interval

    def notify_sleep_state_changed(self) -> None:
        """Called when a local coordinator's sleep state may have changed."""
        self._update_polling_interval()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Meural cloud API."""
        try:
            # Get all devices
            devices = await self.meural.get_user_devices()

            # Get device galleries and user galleries
            device_galleries_by_device: dict[str, list[dict[str, Any]]] = {}
            for device in devices:
                device_id = device["id"]
                device_galleries = await self.meural.get_device_galleries(device_id)
                device_galleries_by_device[str(device_id)] = device_galleries

            user_galleries = await self.meural.get_user_galleries()

            return {
                "devices": {str(device["id"]): device for device in devices},
                "device_galleries": device_galleries_by_device,
                "user_galleries": user_galleries,
            }

        except InvalidAuth as err:
            # Authentication failed - trigger reauth flow
            raise ConfigEntryAuthFailed(
                "Authentication failed. Please reauthenticate."
            ) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Network error - raise UpdateFailed for retry
            raise UpdateFailed(f"Error communicating with Meural cloud API: {err}") from err
        except Exception as err:
            # Unexpected error
            _LOGGER.exception("Unexpected error updating Meural cloud data")
            raise UpdateFailed(f"Unexpected error: {err}") from err


class LocalDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Meural local device data."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: dict[str, Any],
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the coordinator."""
        self.device = device
        self.device_id = str(device["id"])
        self.local_meural = LocalMeural(device, session)
        self._sleeping = True
        self._enabled = True

        super().__init__(
            hass,
            _LOGGER,
            name=f"Meural Local {device['alias']}",
            update_interval=timedelta(seconds=LOCAL_UPDATE_INTERVAL),
        )

    def update_device(self, device: dict[str, Any]) -> None:
        """Update device reference with latest cloud data."""
        self.device = device
        self.local_meural.device = device

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable updates."""
        self._enabled = enabled

    @property
    def sleeping(self) -> bool:
        """Return if device is sleeping."""
        return self._sleeping

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Meural local device API."""
        if not self._enabled:
            return self.data or {}

        try:
            # Get sleep status
            self._sleeping = await self.local_meural.send_get_sleep()

            if self._sleeping:
                # Device is sleeping, return minimal data
                return {
                    "sleeping": True,
                    "galleries": self.data.get("galleries", []) if self.data else [],
                    "gallery_status": self.data.get("gallery_status", {}) if self.data else {},
                }

            # Device is awake, get full data
            galleries = await self.local_meural.send_get_galleries()
            gallery_status = await self.local_meural.send_get_gallery_status()

            return {
                "sleeping": False,
                "galleries": sorted(galleries, key=lambda i: i["name"]),
                "gallery_status": gallery_status,
            }

        except (DeviceTurnedOff, aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Device offline or network error - set sleeping but don't fail
            _LOGGER.debug(
                "Meural device %s: Error contacting local device: %s",
                self.device.get("alias", self.device_id),
                err,
            )
            self._sleeping = True
            # Return last known data or minimal data
            return {
                "sleeping": True,
                "galleries": self.data.get("galleries", []) if self.data else [],
                "gallery_status": self.data.get("gallery_status", {}) if self.data else {},
            }
        except Exception as err:
            # Unexpected error
            _LOGGER.exception(
                "Unexpected error updating Meural local device %s",
                self.device.get("alias", self.device_id),
            )
            # Don't fail integration, just return last known data
            return self.data or {"sleeping": True, "galleries": [], "gallery_status": {}}
