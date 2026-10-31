"""Light platform for Meural integration — controls Canvas backlight brightness."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LocalDataUpdateCoordinator
from .entity import MeuralDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meural light entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    local_coordinators: dict[str, LocalDataUpdateCoordinator] = entry_data[
        "local_coordinators"
    ]

    devices = list(entry_data["devices"].values())

    entities = []
    for device in devices:
        _LOGGER.info("Adding Meural backlight light for device %s", device["alias"])
        local_coordinator = local_coordinators[str(device["id"])]
        entities.append(MeuralBacklightLight(local_coordinator, device))

    async_add_entities(entities)


class MeuralBacklightLight(
    MeuralDeviceInfoMixin, CoordinatorEntity[LocalDataUpdateCoordinator], LightEntity
):
    """Backlight brightness control for a Meural Canvas device."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:image"

    def __init__(
        self,
        coordinator: LocalDataUpdateCoordinator,
        device: dict[str, Any],
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device['alias']} Backlight"
        self._attr_unique_id = f"{device['id']}_backlight"
        self._optimistic_brightness: int | None = None

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic brightness once coordinator confirms the new value."""
        self._optimistic_brightness = None
        super()._handle_coordinator_update()

    def _meural_brightness(self) -> int | None:
        """Return current backlight as a Meural value (0-100), or None if unavailable."""
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("backlight")
        try:
            if raw is None:
                return None
            return max(0, min(100, int(raw)))
        except (ValueError, TypeError):
            return None

    @property
    def is_on(self) -> bool:
        """Return true if the device is awake (backlight is on)."""
        return self.coordinator.data is not None and not self.coordinator.sleeping

    @property
    def brightness(self) -> int | None:
        """Return brightness scaled to HA range (0-255)."""
        if self._optimistic_brightness is not None:
            return self._optimistic_brightness
        level = self._meural_brightness()
        if level is None:
            return None
        return math.floor(level * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on / set brightness. Wakes device first if sleeping."""
        if self.coordinator.sleeping:
            _LOGGER.info(
                "Meural device %s: Turning on backlight (waking device)",
                self._device["alias"],
            )
            await self.coordinator.local_meural.send_key_resume()
            self.coordinator.set_sleeping_optimistic(False)
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS)
        if ha_brightness is not None:
            meural_brightness = round(ha_brightness * 100 / 255)
            _LOGGER.info(
                "Meural device %s: Setting backlight to %s%%",
                self._device["alias"],
                meural_brightness,
            )
            await self.coordinator.local_meural.send_control_backlight(
                meural_brightness
            )
            self._optimistic_brightness = ha_brightness
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off backlight by suspending the Canvas device."""
        _LOGGER.info(
            "Meural device %s: Turning off backlight (suspending device)",
            self._device["alias"],
        )
        await self.coordinator.local_meural.send_key_suspend()
        self.coordinator.set_sleeping_optimistic(True)
