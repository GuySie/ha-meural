import logging
import voluptuous as vol

try:
    from homeassistant.components.media_player import MediaPlayerEntity
except ImportError:
    from homeassistant.components.media_player import MediaPlayerDevice as MediaPlayerEntity

from homeassistant.components.media_player import BrowseError, BrowseMedia

from homeassistant.const import (
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_OFF,
)

from homeassistant.components.media_player.const import (
    MEDIA_CLASS_DIRECTORY,
    MEDIA_TYPE_MUSIC,
    MEDIA_TYPE_PLAYLIST,
    SUPPORT_BROWSE_MEDIA,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PLAY_MEDIA,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_SHUFFLE_SET,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
)

from homeassistant.helpers import entity_platform

from .const import DOMAIN
from .pymeural import LocalMeural

_LOGGER = logging.getLogger(__name__)

MEURAL_SUPPORT = (
    SUPPORT_BROWSE_MEDIA
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_NEXT_TRACK
    | SUPPORT_PAUSE
    | SUPPORT_PLAY
    | SUPPORT_PLAY_MEDIA
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_SHUFFLE_SET
    | SUPPORT_TURN_OFF
    | SUPPORT_TURN_ON
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    meural = hass.data[DOMAIN][config_entry.entry_id]
    devices = await meural.get_user_devices()
    for device in devices:
        _LOGGER.info("Adding Meural device %s" % (device['alias'], ))
        async_add_entities([MeuralEntity(meural, device), ])

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

class MeuralEntity(MediaPlayerEntity):
    """Representation of a Meural entity."""

    def __init__(self, meural, device):
        self.meural = meural
        self._meural_device = device
        self._galleries = []
        self._remote_galleries = []
        self._gallery_status = []
        self._current_item = {}

        self._pause_duration = 0
        self._sleep = True
        self._abort = False

    @property
    def meural_device_id(self):
        return self._meural_device["id"]

    @property
    def meural_device_name(self):
        return self._meural_device["name"]

    @property
    def local_meural(self):
        return LocalMeural(
            self._meural_device,
            self.hass.helpers.aiohttp_client.async_get_clientsession(),
        )

    async def async_added_to_hass(self):
        """Set up local galleries."""
        try:
            self._galleries = await self.local_meural.send_get_galleries()
            _LOGGER.info("Meural device %s: Has %d local galleries on local device" % (self.name, len(self._galleries)))
        except:
            _LOGGER.error("Meural device %s: Error while contacting local device, aborting setup", self.name)
            self._abort = True
            return

        """Set up remote galleries."""
        device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
        _LOGGER.info("Meural device %s: Getting %d device galleries from Meural server", self.name, len(device_galleries))
        user_galleries = await self.meural.get_user_galleries()
        _LOGGER.info("Meural device %s: Getting %d user galleries from Meural server", self.name, len(user_galleries))
        [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
        self._remote_galleries = device_galleries
        _LOGGER.info("Meural device %s: Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))

        """Check if current gallery is based on a folder on the SD-card (ID 1, 2, 3 or 4) and set up first item to display."""
        self._gallery_status = await self.local_meural.send_get_gallery_status()
        current_gallery = int(self._gallery_status["current_gallery"])
        if current_gallery > 4:
            try:
                self._current_item = await self.meural.get_item(int(self._gallery_status["current_item"]))
            except:
                _LOGGER.warning("Meural device %s: Error while getting information of currently displayed item from Meural server, resetting item information",  self.name)
                self._current_item = {}
        else:
            _LOGGER.info("Meural device %s: Gallery ID %s is a local SD-card folder, resetting item information", self.name, current_gallery)
            self._current_item = {}

        """Set up default image duration."""
        self._meural_device = await self.meural.get_device(self.meural_device_id)
        self._pause_duration = self._meural_device["imageDuration"]
        _LOGGER.info("Meural device %s: Setup has completed",  self.name)

    async def async_update(self):
        if self._abort == True:
            _LOGGER.debug("Meural device %s: Setup was aborted, device will not be updated", self.name)
            return

        try:
            self._sleep = await self.local_meural.send_get_sleep()
        except:
            _LOGGER.warning("Meural device %s: Error while contacting local device", self.name)
            self._sleep = True

        """Only poll the Meural API if the device is not sleeping."""
        if self._sleep == False:
            """Update galleries."""
            self._galleries = await self.local_meural.send_get_galleries()
            """Save orientation and item we had before polling."""
            old_orientation = self._meural_device["orientation"]
            self._meural_device = await self.meural.get_device(self.meural_device_id)
            old_item = int(self._gallery_status["current_item"])
            self._gallery_status = await self.local_meural.send_get_gallery_status()

            """Check if current gallery is based on a folder on the SD-card (ID 1, 2, 3 or 4)."""
            current_gallery = int(self._gallery_status["current_gallery"])
            if current_gallery > 4:

                """Check if current item or orientation have changed."""
                local_item = int(self._gallery_status["current_item"])
                new_orientation = self._meural_device["orientation"]
                if old_item != local_item:
                    """Only get item information if current item has changed since last poll."""
                    _LOGGER.info("Meural device %s: Item changed. Getting information from Meural server for item %s", self.name, local_item)
                    try:
                        self._current_item = await self.meural.get_item(local_item)
                    except:
                        _LOGGER.warning("Meural device %s: Error while getting information of currently displayed item %s from Meural server, resetting item information", self.name, local_item)
                        self._current_item = {}
                elif old_orientation != new_orientation:
                    """If orientationMatch is enabled, current item in gallery_status will not reflect item displayed after orientation changes. Force update of gallery_status by reloading gallery."""
                    _LOGGER.info("Meural device %s: Orientation has changed, reloading gallery to force update of currently displayed item", self.name)
                    await self.local_meural.send_change_gallery(self._gallery_status["current_gallery"])
            else:
                _LOGGER.info("Meural device %s: Gallery ID %s is a local SD-card folder, resetting item information", self.name, current_gallery)
                self._current_item = {}

    @property
    def name(self):
        """Name of the device."""
        return self._meural_device["alias"]

    @property
    def unique_id(self):
        """Unique ID of the device."""
        return self._meural_device["productKey"]

    @property
    def device_info(self):
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.unique_id)
            },
            "name": self.name,
            "manufacturer": "NETGEAR",
            "model": self._meural_device["frameModel"]["name"],
            "sw_version": self._meural_device["version"],
        }

    @property
    def available(self):
        """Device available."""
        return self._meural_device["status"] != "offline"

    @property
    def state(self):
        """Return the state of the entity."""
        if self._sleep == True:
            return STATE_OFF
        elif self._meural_device["imageDuration"] == 0:
            return STATE_PAUSED
        return STATE_PLAYING

    @property
    def source(self):
        """Name of the current playlist."""
        return self._gallery_status["current_gallery_name"]

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return MEURAL_SUPPORT

    @property
    def source_list(self):
        """List of available playlists."""
        return [g["name"] for g in self._galleries]

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        return int(self._gallery_status["current_item"])

    @property
    def media_content_type(self):
        """Return the content type of current playing media. Because Image does not support artist names, use Music as alternative."""
        return MEDIA_TYPE_MUSIC

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
        """Artist of current playing media, normally for music track only. Replaced with artist name and the artwork year."""
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

    async def async_select_source(self, source):
        """Select playlist to display."""
        source = next((g["id"] for g in self._galleries if g["name"] == source), None)
        if source is None:
            _LOGGER.warning("Meural %s: Source %s not found", self.name, source)
        await self.local_meural.send_change_gallery(source)

    async def async_media_previous_track(self):
        """Send previous image command."""
        await self.local_meural.send_key_left()

    async def async_media_next_track(self):
        """Send next image command."""
        await self.local_meural.send_key_right()

    async def async_turn_on(self):
        """Resume Meural frame display."""
        await self.local_meural.send_key_resume()

    async def async_turn_off(self):
        """Suspend Meural frame display."""
        await self.local_meural.send_key_suspend()

    async def async_media_pause(self):
        """Set duration to 0 (pause), store current duration in pause_duration."""
        self._pause_duration = self._meural_device["imageDuration"]
        _LOGGER.info("Meural device %s: Setting image duration on Meural server to 0", self.name)
        await self.meural.update_device(self.meural_device_id, {"imageDuration": 0})

    async def async_media_play(self):
        """Restore duration from pause_duration (play). Use duration 300 if no pause_duration was stored."""
        if self._pause_duration != 0:
            _LOGGER.info("Meural device %s: Setting image duration on Meural server to %s", self.name, self._pause_duration)
            await self.meural.update_device(self.meural_device_id, {"imageDuration": self._pause_duration})
        else:
            _LOGGER.info("Meural device %s: Setting image duration on Meural server to 300", self.name)            
            await self.meural.update_device(self.meural_device_id, {"imageDuration": 300})

    async def async_set_shuffle(self, shuffle):
        """Enable/disable shuffling."""
        await self.meural.update_device(self.meural_device_id, {"imageShuffle": shuffle})

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Display an image. If sending a JPG or PNG uses preview functionality. If sending an item ID loads locally if image is in currently selected playlist, or via Meural API if this is not the case."""
        if media_type in ['playlist']:
            _LOGGER.info("Meural device %s: Media type is %s, playing gallery %s", self.name, media_type, media_id)
            await self.local_meural.send_change_gallery(media_id)
        elif media_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Media type is %s, previewing image from %s", self.name, media_type, media_id)
            await self.local_meural.send_postcard(media_id, media_type)
        elif media_type in ['item']:
            if media_id.isdigit():
                currentgallery_id = self._gallery_status["current_gallery"]
                currentitems = await self.local_meural.send_get_items_by_gallery(currentgallery_id)
                in_playlist = next((g["title"] for g in currentitems if g["id"] == media_id), None)
                if in_playlist is None:
                    _LOGGER.info("Meural device %s: Item %s is not in current gallery, trying to display via Meural server", self.name, media_id)
                    try:
                        await self.meural.device_load_item(self.meural_device_id, media_id)
                    except:
                        _LOGGER.error("Meural device %s: Error while trying to display %s item %s via Meural server", self.name, media_type, media_id)
                else:
                    _LOGGER.info("Meural device %s: Item %s is in current gallery %s, trying to display via local device", self.name, media_id, self._gallery_status["current_gallery_name"])
                    await self.local_meural.send_change_item(media_id)
            else:
                _LOGGER.error("Meural device %s: ID %s is not an item", self.name, media_id)
        else:
            _LOGGER.error("Meural device %s: Does not support displaying this %s media with ID %s", self.name, media_type, media_id)

    async def async_preview_image(self, content_url, content_type):
        if content_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Media type is %s, previewing image from %s", self.name, content_type, content_url)
            await self.local_meural.send_postcard(content_url, content_type)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""

        device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
        _LOGGER.info("Meural device %s: Getting %d device galleries from Meural server", self.name, len(device_galleries))
        user_galleries = await self.meural.get_user_galleries()
        _LOGGER.info("Meural device %s: Getting %d user galleries from Meural server", self.name, len(user_galleries))
        [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
        self._remote_galleries = device_galleries
        _LOGGER.info("Meural device %s: Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))

        if media_content_id not in (None, ""):
            raise BrowseError(
                f"Media not found: {media_content_type} / {media_content_id}"
            )

        return BrowseMedia(
            title="Playlists",
            media_class=MEDIA_CLASS_DIRECTORY,
            media_content_id="",
            media_content_type=MEDIA_TYPE_PLAYLIST,
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    title=g["name"],
                    media_class=MEDIA_TYPE_PLAYLIST,
                    media_content_id=g["id"],
                    media_content_type=MEDIA_TYPE_PLAYLIST,
                    can_play=True,
                    can_expand=False,
                    thumbnail=next((h["cover"] for h in self._remote_galleries if h["id"] == int(g["id"])), None),
                )
                for g in self._galleries
            ],
        )