"""Switch platform for Meural integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CloudDataUpdateCoordinator
from .entity import MeuralDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MeuralSwitchDescription:
    """Describe a Meural cloud-backed switch."""

    key: str
    name: str
    unique_suffix: str
    icon: str
    entity_category: EntityCategory | None = EntityCategory.CONFIG


SWITCH_DESCRIPTIONS = (
    MeuralSwitchDescription(
        key="alsEnabled",
        name="Auto Brightness",
        unique_suffix="auto_brightness",
        icon="mdi:brightness-auto",
        entity_category=None,
    ),
    MeuralSwitchDescription(
        key="orientationMatch",
        name="Orientation Match",
        unique_suffix="orientation_match",
        icon="mdi:crop-rotate",
    ),
    MeuralSwitchDescription(
        key="goesDark",
        name="Sleep When Dark",
        unique_suffix="sleep_when_dark",
        icon="mdi:theme-light-dark",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meural switch entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    if entry_data.get("local_only"):
        return  # All switch settings are cloud-backed; unavailable in local-only mode.
    cloud_coordinator: CloudDataUpdateCoordinator = entry_data["cloud_coordinator"]

    entities = []
    for device in cloud_coordinator.data["devices"].values():
        for description in SWITCH_DESCRIPTIONS:
            if description.key not in device:
                _LOGGER.debug(
                    "Meural device %s does not report %s support",
                    device["alias"],
                    description.name,
                )
                continue
            _LOGGER.info(
                "Adding Meural %s switch for device %s",
                description.name,
                device["alias"],
            )
            entities.append(
                MeuralSettingSwitch(
                    cloud_coordinator,
                    device,
                    description,
                )
            )

    async_add_entities(entities)


class MeuralSettingSwitch(
    MeuralDeviceInfoMixin, CoordinatorEntity[CloudDataUpdateCoordinator], SwitchEntity
):
    """Boolean cloud setting for a Meural Canvas device."""

    def __init__(
        self,
        coordinator: CloudDataUpdateCoordinator,
        device: dict[str, Any],
        description: MeuralSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device["id"])
        self._description = description
        self._attr_name = f"{device['alias']} {description.name}"
        self._attr_unique_id = f"{device['id']}_{description.unique_suffix}"
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def is_on(self) -> bool | None:
        """Return whether the setting is enabled."""
        if not self.coordinator.data:
            return None
        device = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not device or self._description.key not in device:
            return None
        value = device[self._description.key]
        if isinstance(value, str):
            return value.lower() in {"1", "true", "on", "yes"}
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._async_set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._async_set_value(False)

    async def _async_set_value(self, enabled: bool) -> None:
        """Update the setting and reflect it immediately in Home Assistant."""
        _LOGGER.info(
            "Meural device %s: Setting %s to %s",
            self._device["alias"],
            self._description.key,
            enabled,
        )
        await self.coordinator.async_apply_device_setting(
            self._device_id, self._description.key, enabled
        )
