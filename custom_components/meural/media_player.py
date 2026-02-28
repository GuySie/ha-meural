from __future__ import annotations

from datetime import timedelta
import logging
import asyncio
import random
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.auth.models import RefreshToken
from homeassistant.components import media_source
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, BrowseMedia
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.media_player import MediaClass, MediaType

from homeassistant.const import (
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_OFF,
)

from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature
)

from .const import DOMAIN, SD_CARD_FOLDER_MAX_ID
from .coordinator import CloudDataUpdateCoordinator, LocalDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

MEURAL_SUPPORT = (
    MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meural media player entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    meural = entry_data["meural"]
    cloud_coordinator: CloudDataUpdateCoordinator = entry_data["cloud_coordinator"]

    # Get devices from cloud coordinator data
    devices = list(cloud_coordinator.data["devices"].values())

    # Create entities with local coordinators
    entities = []
    for device in devices:
        _LOGGER.info("Adding Meural device %s", device['alias'])

        # Create local coordinator for this device
        local_coordinator = LocalDataUpdateCoordinator(
            hass,
            device,
            async_get_clientsession(hass),
        )

        # Perform first refresh for local coordinator
        await local_coordinator.async_config_entry_first_refresh()

        entities.append(
            MeuralEntity(
                meural,
                cloud_coordinator,
                local_coordinator,
                device,
            )
        )

    async_add_entities(entities)

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "set_brightness",
        {
            vol.Required("brightness"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=100)
            )
        },
        "async_set_brightness",
    )

    platform.async_register_entity_service(
        "preview_image",
        {
            vol.Required("content_url"): str,
            vol.Required("content_type"): str,
        },
        "async_preview_image",
    )

    platform.async_register_entity_service(
        "reset_brightness",
        {},
        "async_reset_brightness",
    )

    platform.async_register_entity_service(
        "toggle_informationcard",
        {},
        "async_toggle_informationcard",
    )

    platform.async_register_entity_service(
        "set_device_option",
        {
            vol.Optional("orientation"): str,
            vol.Optional("orientationMatch"): bool,
            vol.Optional("alsEnabled"): bool,
            vol.Optional("alsSensitivity"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=100)
            ),
            vol.Optional("goesDark"): bool,
            vol.Optional("imageShuffle"): bool,
            vol.Optional("imageDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("previewDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("overlayDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400)
            ),
            vol.Optional("gestureFeedback"): bool,
            vol.Optional("gestureFeedbackHelp"): bool,
            vol.Optional("gestureFlip"): bool,
            vol.Optional("backgroundColor"): str,
            vol.Optional("fillMode"): str,
            vol.Optional("schedulerEnabled"): bool,
            vol.Optional("galleryRotation"): bool
        },
        "async_set_device_option",
    )

    platform.async_register_entity_service(
        "synchronize",
        {},
        "async_synchronize",
    )

    platform.async_register_entity_service(
        "play_random_playlist",
        {},
        "async_play_random_playlist",
    )

class MeuralEntity(CoordinatorEntity[CloudDataUpdateCoordinator], MediaPlayerEntity):
    """Representation of a Meural entity."""

    def __init__(
        self,
        meural,
        cloud_coordinator: CloudDataUpdateCoordinator,
        local_coordinator: LocalDataUpdateCoordinator,
        device: dict[str, Any],
    ) -> None:
        """Initialize the Meural entity."""
        super().__init__(cloud_coordinator)

        self.meural = meural
        self.cloud_coordinator = cloud_coordinator
        self.local_coordinator = local_coordinator
        self._meural_device = device
        self._current_item: dict[str, Any] = {}
        self._pause_duration = 0
        self._abort = False
        self._last_fetched_item_id: int | None = None
        self._last_gsensor: str | None = None

        # Start listening to local coordinator updates
        self.async_on_remove(
            self.local_coordinator.async_add_listener(self._handle_local_coordinator_update)
        )

        # Unregister local coordinator when entity is removed
        self.async_on_remove(
            lambda: self.cloud_coordinator.unregister_local_coordinator(
                self.meural_device_id
            )
        )

    @property
    def meural_device_id(self) -> str:
        """Return the device ID."""
        return str(self._meural_device["id"])

    @property
    def meural_device_name(self) -> str:
        """Return the device name."""
        return self._meural_device["name"]

    @property
    def local_meural(self):
        """Return the LocalMeural instance from coordinator."""
        return self.local_coordinator.local_meural

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()

        # Register this entity's local coordinator with cloud coordinator
        self.cloud_coordinator.register_local_coordinator(
            self.meural_device_id, self.local_coordinator
        )

        # Get device info from cloud coordinator
        device_id = self.meural_device_id
        if device_id in self.cloud_coordinator.data["devices"]:
            self._meural_device = self.cloud_coordinator.data["devices"][device_id]
            self._pause_duration = self._meural_device.get("imageDuration", 0)
            _LOGGER.info("Meural device %s: Setup completed", self.name)

        # Fetch initial current item if needed
        await self._fetch_current_item_if_needed()

    async def _fetch_current_item_if_needed(self) -> None:
        """Fetch current item information if not an SD-card folder."""
        if not self.local_coordinator.data:
            return

        gallery_status = self.local_coordinator.data.get("gallery_status", {})
        if not gallery_status:
            return

        current_gallery = int(gallery_status.get("current_gallery", 0))
        if current_gallery > SD_CARD_FOLDER_MAX_ID:
            try:
                current_item_id = int(gallery_status.get("current_item", 0))
                # Only fetch if item ID has changed to avoid unnecessary cloud API calls
                if current_item_id and current_item_id != self._last_fetched_item_id:
                    _LOGGER.debug(
                        "Meural device %s: Fetching cloud data for item %s to update thumbnail",
                        self.name,
                        current_item_id,
                    )
                    self._current_item = await self.meural.get_item(current_item_id)
                    self._last_fetched_item_id = current_item_id
                    # Update UI with new thumbnail
                    self.async_write_ha_state()
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as err:
                _LOGGER.warning(
                    "Meural device %s: Error getting current item information: %s",
                    self.name,
                    err,
                )
                self._current_item = {}
                # Reset last fetched ID on error to retry next time
                self._last_fetched_item_id = None
        else:
            _LOGGER.debug(
                "Meural device %s: Gallery %s is a local SD-card folder",
                self.name,
                current_gallery,
            )
            self._current_item = {}
            # Reset last fetched ID when in SD card folder
            self._last_fetched_item_id = None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the cloud coordinator."""
        device_id = self.meural_device_id
        if device_id in self.coordinator.data["devices"]:
            old_device = self._meural_device
            self._meural_device = self.coordinator.data["devices"][device_id]

            # Update local coordinator's device reference
            self.local_coordinator.update_device(self._meural_device)

            # Cloud coordinator now manages interval based on all devices' sleep states

            # Check if orientation changed (requires special handling)
            if old_device.get("orientation") != self._meural_device.get("orientation"):
                _LOGGER.debug(
                    "Meural device %s: Orientation changed from %s to %s",
                    self.name,
                    old_device.get("orientation"),
                    self._meural_device.get("orientation"),
                )

        self.async_write_ha_state()

    def _handle_local_coordinator_update(self) -> None:
        """Handle updated data from the local coordinator."""
        # Notify cloud coordinator that sleep state may have changed
        self.cloud_coordinator.notify_sleep_state_changed()

        if self.local_coordinator.data:
            # Detect physical rotation via gsensor when orientationMatch is enabled.
            # gallery_status.current_item does not update on orientationMatch switches,
            # so we use gsensor changes as the trigger to clear stale item metadata.
            gsensor = self.local_coordinator.data.get("gsensor")
            if (
                gsensor is not None
                and self._last_gsensor is not None
                and gsensor != self._last_gsensor
                and self._meural_device.get("orientationMatch")
            ):
                _LOGGER.debug(
                    "Meural device %s: gsensor changed from %s to %s with orientationMatch enabled, reloading gallery to force item update",
                    self.name,
                    self._last_gsensor,
                    gsensor,
                )
                self._current_item = {}
                self._last_fetched_item_id = None
                self.hass.async_create_task(self._reload_gallery_on_orientation_change())
            else:
                # When local data updates, fetch current item if it changed
                gallery_status = self.local_coordinator.data.get("gallery_status", {})
                if gallery_status:
                    # Schedule fetching current item in the background
                    self.hass.async_create_task(self._fetch_current_item_if_needed())
            if gsensor is not None:
                self._last_gsensor = gsensor

        self.async_write_ha_state()


    @property
    def name(self) -> str:
        """Name of the device."""
        return self._meural_device["alias"]

    @property
    def unique_id(self) -> str:
        """Unique ID of the device."""
        return self._meural_device["productKey"]

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {
                (DOMAIN, self.unique_id)
            },
            "name": self.name,
            "manufacturer": "NETGEAR",
            "model": self._meural_device["frameModel"]["name"],
            "sw_version": self._meural_device["version"],
            "configuration_url": f"http://{self._meural_device['localIp']}/remote/",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if coordinators are working and device is not offline
        return (
            self.coordinator.last_update_success
            and self.local_coordinator.last_update_success
            and self._meural_device.get("status") != "offline"
        )

    @property
    def state(self) -> str:
        """Return the state of the entity."""
        if self.local_coordinator.sleeping:
            return STATE_OFF
        elif self._meural_device.get("imageDuration", 0) == 0:
            return STATE_PAUSED
        return STATE_PLAYING

    @property
    def source(self) -> str | None:
        """Name of the current playlist."""
        if not self.local_coordinator.data:
            return None
        gallery_status = self.local_coordinator.data.get("gallery_status", {})
        return gallery_status.get("current_gallery_name")

    @property
    def supported_features(self) -> int:
        """Flag media player features that are supported."""
        return MEURAL_SUPPORT

    @property
    def source_list(self) -> list[str]:
        """List of available playlists."""
        if not self.local_coordinator.data:
            return []
        galleries = self.local_coordinator.data.get("galleries", [])
        return [g["name"] for g in galleries]

    @property
    def media_content_id(self) -> int | None:
        """Return the content ID of current playing media."""
        if not self.local_coordinator.data:
            return None
        gallery_status = self.local_coordinator.data.get("gallery_status", {})
        current_item = gallery_status.get("current_item")
        return int(current_item) if current_item is not None else None

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        return MediaType.IMAGE

    @property
    def media_summary(self):
        """Return the summary of current playing media."""
        if 'description' in self._current_item:
            return self._current_item["description"]
        else:
            return None

    @property
    def media_title(self):
        """Return the title of current playing media."""
        if 'name' in self._current_item:
            return self._current_item["name"]
        else:
            return None

    @property
    def media_artist(self):
        """Artist of current playing media. Replaced with artist name and the artwork year."""
        if (not self._current_item) is False:
            if self._current_item["artistName"] is not None:
                if self._current_item["year"] is not None:
                    return str(self._current_item["artistName"]) + ", " + str(self._current_item["year"])
                else:
                    return str(self._current_item["artistName"])
            elif self._current_item["author"] is not None:
                if self._current_item["year"] is not None:
                    return str(self._current_item["author"]) + ", " + str(self._current_item["year"])
                else:
                    return str(self._current_item["author"])
            elif self._current_item["year"] is not None:
                return "Unknown, " + str(self._current_item["year"])
            return None
        else:
            return None

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        if 'image' in self._current_item:
            return self._current_item["image"]
        else:
            return None

    @property
    def media_image_remotely_accessible(self) -> bool:
        """If the image url is remotely accessible."""
        return True

    @property
    def shuffle(self):
        """Boolean if shuffling is enabled."""
        return self._meural_device["imageShuffle"]

    async def async_set_device_option(
        self,
        orientation=None,
        orientationMatch=None,
        alsEnabled=None,
        alsSensitivity=None,
        goesDark=None,
        imageShuffle=None,
        imageDuration=None,
        previewDuration=None,
        overlayDuration=None,
        gestureFeedback=None,
        gestureFeedbackHelp=None,
        gestureFlip=None,
        backgroundColor=None,
        fillMode=None,
        schedulerEnabled=None,
        galleryRotation=None):
        """Set the configuration options on the Meural server."""
        params = {}
        if orientation is not None:
            params["orientation"] = orientation
        if orientationMatch is not None:
            params["orientationMatch"] = orientationMatch
        if alsEnabled is not None:
            params["alsEnabled"] = alsEnabled
        if alsSensitivity is not None:
            params["alsSensitivity"] = alsSensitivity
        if goesDark is not None:
            params["goesDark"] = goesDark
        if imageShuffle is not None:
            params["imageShuffle"] = imageShuffle
        if imageDuration is not None:
            params["imageDuration"] = imageDuration
        if previewDuration is not None:
            params["previewDuration"] = previewDuration
        if overlayDuration is not None:
            params["overlayDuration"] = overlayDuration
        if gestureFeedback is not None:
            params["gestureFeedback"] = gestureFeedback
        if gestureFeedbackHelp is not None:
            params["gestureFeedbackHelp"] = gestureFeedbackHelp
        if gestureFlip is not None:
            params["gestureFlip"] = gestureFlip
        if backgroundColor is not None:
            params["backgroundColor"] = backgroundColor
        if fillMode is not None:
            params["fillMode"] = fillMode
        if schedulerEnabled is not None:
            params["schedulerEnabled"] = schedulerEnabled
        if galleryRotation is not None:
            params["galleryRotation"] = galleryRotation
        _LOGGER.info("Meural device %s: Setting options. Setting options on Meural server", self.name)
        await self.meural.update_device(self.meural_device_id, params)

    async def async_set_brightness(self, brightness):
        """Change backlight brightness setting."""
        await self.local_meural.send_control_backlight(brightness)

    async def async_reset_brightness(self):
        """Automatically adjust backlight to room's lighting according to ambient light sensor."""
        await self.local_meural.send_als_calibrate_off()

    async def async_toggle_informationcard(self):
        """Toggle display of the information card."""
        await self.local_meural.send_key_up()

    async def async_synchronize(self):
        """Synchronize device with Meural server."""
        _LOGGER.info("Meural device %s: Synchronizing with Meural server", self.name)
        await self.meural.sync_device(self.meural_device_id)

    async def async_play_random_playlist(self):
        """Pick a random gallery from all available galleries and play it."""
        if not self.local_coordinator.data:
            _LOGGER.warning("Meural device %s: Play random playlist. No local data available", self.name)
            return

        galleries = self.local_coordinator.data.get("galleries", [])
        if not galleries:
            _LOGGER.warning("Meural device %s: Play random playlist. No galleries available", self.name)
            return

        gallery_status = self.local_coordinator.data.get("gallery_status", {})
        current_gallery_id = str(gallery_status.get("current_gallery", ""))

        candidate_galleries = [g for g in galleries if str(g["id"]) != current_gallery_id]
        if not candidate_galleries:
            # Only one gallery available; play it regardless
            candidate_galleries = galleries

        gallery = random.choice(candidate_galleries)
        _LOGGER.info(
            "Meural device %s: Play random playlist. Playing random gallery %s, ID %s",
            self.name,
            gallery["name"],
            gallery["id"],
        )
        await self.local_meural.send_change_gallery(gallery["id"])
        await self._refresh_after_user_action()

    async def _refresh_after_user_action(self) -> None:
        """Refresh coordinator data immediately after user action to update thumbnail."""
        # Give device a moment to process the command
        await asyncio.sleep(0.5)

        # Force immediate refresh for user-initiated actions (bypasses throttling)
        # This will automatically trigger _fetch_current_item_if_needed() via the coordinator update listener
        await self.local_coordinator.async_refresh()

    async def _reload_gallery_on_orientation_change(self) -> None:
        """Reload the current gallery after an orientationMatch rotation to force item update.

        When the device physically rotates with orientationMatch enabled, it switches to an
        orientation-appropriate item internally but gallery_status.current_item never updates
        via the local API until the gallery naturally advances. Reloading the same gallery
        forces the device to report the correct current_item.
        """
        if not self.local_coordinator.data:
            return
        gallery_status = self.local_coordinator.data.get("gallery_status", {})
        current_gallery_id = gallery_status.get("current_gallery")
        if not current_gallery_id or int(current_gallery_id) <= SD_CARD_FOLDER_MAX_ID:
            return
        _LOGGER.debug(
            "Meural device %s: Reloading gallery %s to force current_item update after orientation change",
            self.name,
            current_gallery_id,
        )
        try:
            # Wait for the orientation transition to complete before reloading.
            # The transition animation takes several seconds; tune this if the reload
            # still interrupts the transition or takes too long to respond.
            await asyncio.sleep(5.0)
            await self.local_meural.send_change_gallery(current_gallery_id)
            await asyncio.sleep(1.0)
            await self.local_coordinator.async_refresh()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Meural device %s: Error reloading gallery after orientation change: %s",
                self.name,
                err,
            )

    async def async_select_source(self, source: str) -> None:
        """Select playlist to display."""
        if not self.local_coordinator.data:
            _LOGGER.warning("Meural device %s: Selecting source. No local data available", self.name)
            return

        galleries = self.local_coordinator.data.get("galleries", [])
        playlist = next((g["id"] for g in galleries if g["name"] == source), None)
        if playlist is None:
            _LOGGER.warning("Meural device %s: Selecting source. Source %s not found", self.name, source)
        else:
            _LOGGER.info("Meural device %s: Selecting source. Playing gallery %s, ID %s", self.name, source, playlist)
            await self.local_meural.send_change_gallery(playlist)
            # Refresh immediately to update thumbnail
            await self._refresh_after_user_action()

    async def async_media_previous_track(self) -> None:
        """Send previous image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_right()
        else:
            await self.local_meural.send_key_left()
        # Refresh immediately to update thumbnail
        await self._refresh_after_user_action()

    async def async_media_next_track(self) -> None:
        """Send next image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_left()
        else:
            await self.local_meural.send_key_right()
        # Refresh immediately to update thumbnail
        await self._refresh_after_user_action()

    async def async_turn_on(self):
        """Resume Meural frame display."""
        await self.local_meural.send_key_resume()

    async def async_turn_off(self):
        """Suspend Meural frame display."""
        await self.local_meural.send_key_suspend()

    async def async_media_pause(self):
        """Set duration to 0 (pause), store current duration in pause_duration."""
        self._pause_duration = self._meural_device["imageDuration"]
        _LOGGER.info("Meural device %s: Pausing player. Setting image duration on Meural server to 0", self.name)
        await self.meural.update_device(self.meural_device_id, {"imageDuration": 0})

    async def async_media_play(self):
        """Restore duration from pause_duration (play). Use duration 1800 if no pause_duration was stored."""
        if self._pause_duration != 0:
            _LOGGER.info("Meural device %s: Unpause player. Setting image duration on Meural server to %s", self.name, self._pause_duration)
            await self.meural.update_device(self.meural_device_id, {"imageDuration": self._pause_duration})
        else:
            _LOGGER.info("Meural device %s: Unpause player. Setting image duration on Meural server to 1800", self.name)
            await self.meural.update_device(self.meural_device_id, {"imageDuration": 1800})

    async def async_set_shuffle(self, shuffle):
        """Enable/disable shuffling."""
        _LOGGER.info("Meural device %s: Shuffling player. Setting shuffle on Meural server to %s", self.name, shuffle)
        await self.meural.update_device(self.meural_device_id, {"imageShuffle": shuffle})

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Play media from media_source."""
        if media_source.is_media_source_id(media_id):
            sourced_media = await media_source.async_resolve_media(self.hass, media_id)
            media_type = sourced_media.mime_type
            media_id = sourced_media.url

            # If media ID is a relative URL, we serve it from HA.
            if media_id[0] == "/":
                user = await self.hass.auth.async_get_owner()
                if user.refresh_tokens:
                    refresh_token: RefreshToken = list(user.refresh_tokens.values())[0]

                    # Use kwargs so it works both before and after the change in Home Assistant 2022.2
                    media_id = async_sign_path(
                        hass=self.hass,
                        refresh_token_id=refresh_token.id,
                        path=media_id,
                        expiration=timedelta(minutes=5)
                    )

                # Prepend external URL.
                hass_url = get_url(self.hass, allow_internal=True)
                media_id = f"{hass_url}{media_id}"

            _LOGGER.info("Meural device %s: Playing media. Media type is %s, previewing image from %s", self.name, media_type, media_id)
            await self.local_meural.send_postcard(media_id, media_type)

        # Play gallery (playlist or album) by ID.
        elif media_type in ['playlist']:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, playing gallery %s", self.name, media_type, media_id)
            await self.local_meural.send_change_gallery(media_id)
            # Refresh immediately to update thumbnail
            await self._refresh_after_user_action()

        # "Preview image from URL.
        elif media_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, previewing image from %s", self.name, media_type, media_id)
            await self.local_meural.send_postcard(media_id, media_type)

        # Play item (artwork) by ID. Play locally if item is in currently displayed gallery. If not, play using Meural server."""
        elif media_type in ['item']:
            if media_id.isdigit():
                if not self.local_coordinator.data:
                    _LOGGER.warning("Meural device %s: Playing media. No local data available", self.name)
                    return

                gallery_status = self.local_coordinator.data.get("gallery_status", {})
                currentgallery_id = gallery_status.get("current_gallery")
                if not currentgallery_id:
                    _LOGGER.warning("Meural device %s: Playing media. Current gallery not available", self.name)
                    return

                currentitems = await self.local_meural.send_get_items_by_gallery(currentgallery_id)
                in_playlist = next((g["title"] for g in currentitems if g["id"] == media_id), None)
                if in_playlist is None:
                    _LOGGER.info("Meural device %s: Playing media. Item %s is not in current gallery, trying to display via Meural server", self.name, media_id)
                    try:
                        await self.meural.device_load_item(self.meural_device_id, media_id)
                    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as err:
                        _LOGGER.error("Meural device %s: Playing media. Error while trying to display %s item %s via Meural server: %s", self.name, media_type, media_id, err, exc_info=True)
                        return
                else:
                    current_gallery_name = gallery_status.get("current_gallery_name", "")
                    _LOGGER.info("Meural device %s: Playing media. Item %s is in current gallery %s, trying to display via local device", self.name, media_id, current_gallery_name)
                    await self.local_meural.send_change_item(media_id)
                # Refresh immediately to update thumbnail
                await self._refresh_after_user_action()
            else:
                _LOGGER.error("Meural device %s: Playing media. ID %s is not an item", self.name, media_id)

        # This is an unsupported media type.
        else:
            _LOGGER.error("Meural device %s: Playing media. Does not support displaying this %s media with ID %s", self.name, media_type, media_id)

    async def async_preview_image(self, content_url, content_type):
        """Preview image from URL."""
        if content_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Previewing image. Media type is %s, previewing image from %s", self.name, content_type, content_url)
            await self.local_meural.send_postcard(content_url, content_type)
        else:
            _LOGGER.error("Meural device %s: Previewing image. Does not support media type %s", self.name, content_type)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        _LOGGER.debug("Meural device %s: Browsing media. Media_content_type is %s, media_content_id is %s", self.name, media_content_type, media_content_id)
        if media_content_id in (None, "") and media_content_type in (None, ""):
            response = BrowseMedia(
                title="Meural Canvas",
                media_class=MediaClass.DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[BrowseMedia(
                    title="Media Source",
                    media_class=MediaClass.DIRECTORY,
                    media_content_id="",
                    media_content_type="localmediasource",
                    can_play=False,
                    can_expand=True),
                BrowseMedia(
                    title="Meural Playlists",
                    media_class=MediaClass.DIRECTORY,
                    media_content_id="",
                    media_content_type="meuralplaylists",
                    can_play=False,
                    can_expand=True),
                ]
            )
            return response

        elif media_source.is_media_source_id(media_content_id) or media_content_type=="localmediasource":
            response = await media_source.async_browse_media(
                self.hass,
                media_content_id,
                content_filter=lambda item: item.media_content_type in ('image/jpg', 'image/png', 'image/jpeg')
            )
            return response

        elif media_content_type=="meuralplaylists":
            response = BrowseMedia(
                title="Meural Playlists",
                media_class=MediaClass.DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[])

            # Get galleries from coordinators
            if not self.local_coordinator.data or not self.cloud_coordinator.data:
                _LOGGER.warning("Meural device %s: Browsing media. Coordinator data not available", self.name)
                return response

            local_galleries = self.local_coordinator.data.get("galleries", [])
            device_galleries = self.cloud_coordinator.data.get("device_galleries", {}).get(self.meural_device_id, [])
            user_galleries = self.cloud_coordinator.data.get("user_galleries", [])

            # Combine device and user galleries
            remote_galleries = device_galleries.copy()
            [remote_galleries.append(x) for x in user_galleries if x not in remote_galleries]

            _LOGGER.info("Meural device %s: Browsing media. Has %d local galleries, %d remote galleries", self.name, len(local_galleries), len(remote_galleries))

            for g in local_galleries:
                thumb = next((h["cover"] for h in remote_galleries if h["id"] == int(g["id"])), None)
                if thumb is None and (int(g["id"]) > SD_CARD_FOLDER_MAX_ID):
                    _LOGGER.debug("Meural device %s: Browsing media. Gallery %s misses thumbnail, getting gallery items", self.name, g["id"])
                    album_items = await self.local_meural.send_get_items_by_gallery(g["id"])
                    if album_items:
                        _LOGGER.info("Meural device %s: Browsing media. Replacing missing thumbnail of gallery %s with first gallery item image. Getting information from Meural server for item %s", self.name, g["id"], album_items[0]["id"])
                        first_item = await self.meural.get_item(album_items[0]["id"])
                        thumb = first_item["image"]
                _LOGGER.debug("Meural device %s: Browsing media. Thumbnail image for gallery %s is %s", self.name, g["id"], thumb)

                response.children.append(BrowseMedia(
                    title=g["name"],
                    media_class=MediaType.PLAYLIST,
                    media_content_id=g["id"],
                    media_content_type=MediaType.PLAYLIST,
                    can_play=True,
                    can_expand=False,
                    thumbnail=thumb,
                    )
                )
            return response

        else:
            _LOGGER.error("Meural device %s: Browsing media. Media not found, media_content_type is %s, media_content_id is %s", self.name, media_content_type, media_content_id)
            raise BrowseError(
                f"Media not found: {media_content_type} / {media_content_id}"
            )
