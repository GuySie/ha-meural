"""Number platform for Meural integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CloudDataUpdateCoordinator
from .entity import MeuralDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MeuralNumberDescription:
    """Describe a Meural cloud-backed number setting."""

    key: str
    name: str
    unique_suffix: str
    icon: str
    minimum: float
    maximum: float
    step: float
    unit: str


NUMBER_DESCRIPTIONS = (
    MeuralNumberDescription(
        key="alsSensitivity",
        name="Light Sensitivity",
        unique_suffix="light_sensitivity",
        icon="mdi:brightness-6",
        minimum=0,
        maximum=100,
        step=1,
        unit=PERCENTAGE,
    ),
    MeuralNumberDescription(
        key="imageDuration",
        name="Artwork Duration",
        unique_suffix="artwork_duration",
        icon="mdi:timer-outline",
        minimum=0,
        maximum=86400,
        step=1,
        unit=UnitOfTime.SECONDS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meural number entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    if entry_data.get("local_only"):
        return  # All number settings are cloud-backed; unavailable in local-only mode.
    cloud_coordinator: CloudDataUpdateCoordinator = entry_data["cloud_coordinator"]

    entities = []
    for device in cloud_coordinator.data["devices"].values():
        for description in NUMBER_DESCRIPTIONS:
            if description.key not in device:
                _LOGGER.debug(
                    "Meural device %s does not report %s support",
                    device["alias"],
                    description.name,
                )
                continue
            entities.append(
                MeuralSettingNumber(
                    cloud_coordinator,
                    device,
                    description,
                )
            )

    async_add_entities(entities)


class MeuralSettingNumber(
    MeuralDeviceInfoMixin, CoordinatorEntity[CloudDataUpdateCoordinator], NumberEntity
):
    """Numeric cloud setting for a Meural Canvas device."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: CloudDataUpdateCoordinator,
        device: dict[str, Any],
        description: MeuralNumberDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device["id"])
        self._description = description
        self._attr_name = f"{device['alias']} {description.name}"
        self._attr_unique_id = f"{device['id']}_{description.unique_suffix}"
        self._attr_icon = description.icon
        self._attr_native_min_value = description.minimum
        self._attr_native_max_value = description.maximum
        self._attr_native_step = description.step
        self._attr_native_unit_of_measurement = description.unit

    @property
    def native_value(self) -> float | None:
        """Return the current setting value."""
        if not self.coordinator.data:
            return None
        device = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not device:
            return None
        raw = device.get(self._description.key)
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the numeric Meural setting."""
        value = round(value)
        _LOGGER.info(
            "Meural device %s: Setting %s to %s",
            self._device["alias"],
            self._description.key,
            value,
        )
        await self.coordinator.async_apply_device_setting(
            self._device_id, self._description.key, value
        )
