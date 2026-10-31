"""Select platform for Meural integration."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOCAL_UPDATE_INTERVAL
from .coordinator import CloudDataUpdateCoordinator, LocalDataUpdateCoordinator
from .entity import MeuralDeviceInfoMixin

_LOGGER = logging.getLogger(__name__)

ORIENTATION_PORTRAIT = "portrait"
ORIENTATION_LANDSCAPE = "landscape"

# How long to trust an unconfirmed optimistic orientation before falling back to
# the last value the local coordinator actually reported. Bounds how long the
# entity can show a stale value if the Canvas accepts the command but never
# applies it, while still giving the device a few poll cycles to catch up.
ORIENTATION_OPTIMISTIC_TIMEOUT = LOCAL_UPDATE_INTERVAL * 3


@dataclass(frozen=True, kw_only=True)
class MeuralSelectDescription:
    """Describe a Meural cloud-backed select setting."""

    key: str
    name: str
    unique_suffix: str
    icon: str
    options: tuple[str, ...]


SELECT_DESCRIPTIONS = (
    MeuralSelectDescription(
        key="fillMode",
        name="Image Fit Mode",
        unique_suffix="image_fit_mode",
        icon="mdi:fit-to-screen-outline",
        options=("contain", "auto crop", "as is", "stretch"),
    ),
    MeuralSelectDescription(
        key="backgroundColor",
        name="Letterbox Color",
        unique_suffix="letterbox_color",
        icon="mdi:palette-outline",
        options=("black", "grey", "white"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meural select entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    local_only: bool = entry_data.get("local_only", False)
    cloud_coordinator: CloudDataUpdateCoordinator | None = entry_data["cloud_coordinator"]
    local_coordinators: dict[str, LocalDataUpdateCoordinator] = entry_data[
        "local_coordinators"
    ]

    entities = []
    for device in entry_data["devices"].values():
        local_coordinator = local_coordinators[str(device["id"])]
        # Display Orientation is driven entirely through the local Canvas API.
        entities.append(MeuralDisplayOrientationSelect(local_coordinator, device))
        # The remaining selects are cloud-backed settings.
        if local_only or cloud_coordinator is None:
            continue
        for description in SELECT_DESCRIPTIONS:
            if description.key not in device:
                _LOGGER.debug(
                    "Meural device %s does not report %s support",
                    device["alias"],
                    description.name,
                )
                continue
            entities.append(
                MeuralSettingSelect(
                    cloud_coordinator,
                    device,
                    description,
                )
            )

    async_add_entities(entities)


class MeuralDisplayOrientationSelect(
    MeuralDeviceInfoMixin, CoordinatorEntity[LocalDataUpdateCoordinator], SelectEntity
):
    """Configured display orientation of a Meural Canvas device."""

    _attr_options = [ORIENTATION_PORTRAIT, ORIENTATION_LANDSCAPE]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:screen-rotation"

    def __init__(
        self,
        coordinator: LocalDataUpdateCoordinator,
        device: dict[str, Any],
    ) -> None:
        """Initialize the orientation select."""
        super().__init__(coordinator)
        self._device = device
        self._attr_name = f"{device['alias']} Display Orientation"
        self._attr_unique_id = f"{device['id']}_display_orientation"
        self._optimistic_option: str | None = None
        self._optimistic_since: float = 0.0

    def _handle_coordinator_update(self) -> None:
        """Clear the optimistic option once a poll confirms it, matching sibling
        entities' pattern of trusting the next scheduled poll rather than a
        blocking refresh. A poll landing before the device applies the change
        leaves the optimistic value in place instead of flashing back to stale
        data. If the device never reports the change (e.g. it silently rejected
        it), fall back to the reported value after a bounded timeout instead of
        showing the unconfirmed value forever.
        """
        if self._optimistic_option is not None and (
            self._reported_option() == self._optimistic_option
            or time.monotonic() - self._optimistic_since > ORIENTATION_OPTIMISTIC_TIMEOUT
        ):
            self._optimistic_option = None
        super()._handle_coordinator_update()

    def _reported_option(self) -> str | None:
        """Return the orientation as last reported by the local coordinator."""
        if not self.coordinator.data:
            return None
        value = str(self.coordinator.data.get("orientation", "")).lower()
        return {
            "portrait": ORIENTATION_PORTRAIT,
            "vertical": ORIENTATION_PORTRAIT,
            "landscape": ORIENTATION_LANDSCAPE,
            "horizontal": ORIENTATION_LANDSCAPE,
        }.get(value)

    @property
    def current_option(self) -> str | None:
        """Return the configured display orientation."""
        if self._optimistic_option is not None:
            return self._optimistic_option
        return self._reported_option()

    async def async_select_option(self, option: str) -> None:
        """Set the display orientation through the local Canvas API."""
        _LOGGER.info(
            "Meural device %s: Setting display orientation to %s",
            self._device["alias"],
            option,
        )
        if option == ORIENTATION_PORTRAIT:
            await self.coordinator.local_meural.send_set_portrait()
        else:
            await self.coordinator.local_meural.send_set_landscape()
        self._optimistic_option = option
        self._optimistic_since = time.monotonic()
        self.async_write_ha_state()


class MeuralSettingSelect(
    MeuralDeviceInfoMixin, CoordinatorEntity[CloudDataUpdateCoordinator], SelectEntity
):
    """Select-style cloud setting for a Meural Canvas device."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: CloudDataUpdateCoordinator,
        device: dict[str, Any],
        description: MeuralSelectDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device = device
        self._device_id = str(device["id"])
        self._description = description
        self._attr_name = f"{device['alias']} {description.name}"
        self._attr_unique_id = f"{device['id']}_{description.unique_suffix}"
        self._attr_icon = description.icon
        self._attr_options = list(description.options)

    @property
    def current_option(self) -> str | None:
        """Return the current cloud setting."""
        if not self.coordinator.data:
            return None
        device = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not device:
            return None
        raw = device.get(self._description.key)
        if raw is None:
            return None
        value = str(raw).strip().lower()
        aliases = {
            "gray": "grey",
            "autocrop": "auto crop",
            "auto_crop": "auto crop",
            "asis": "as is",
            "as_is": "as is",
        }
        value = aliases.get(value, value)
        return value if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Set the cloud-backed option."""
        _LOGGER.info(
            "Meural device %s: Setting %s to %s",
            self._device["alias"],
            self._description.key,
            option,
        )
        await self.coordinator.async_apply_device_setting(
            self._device_id, self._description.key, option
        )
