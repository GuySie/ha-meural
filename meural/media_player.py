import logging
import voluptuous as vol

try:
    from homeassistant.components.media_player import MediaPlayerEntity
except ImportError:
    from homeassistant.components.media_player import MediaPlayerDevice as MediaPlayerEntity

from homeassistant.const import (
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_OFF,
)

from homeassistant.components.media_player.const import (
    MEDIA_TYPE_MUSIC,
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
    SUPPORT_SELECT_SOURCE
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
    async_add_entities(MeuralEntity(meural, device) for device in devices)

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
            vol.Required("file"): str,
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
                vol.Range(min=0, max=3600)
            ),
            vol.Optional("overlayDuration"): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=3600)
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

class MeuralEntity(MediaPlayerEntity):
    """Representation of a Meural entity."""

    def __init__(self, meural, device):
        self.meural = meural
        self._meural_device = device
        self._galleries = []
        self._gallery_status = []
        self._current_item = []

        self._pause_duration = 0
        self._sleep = True

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
        """Set up galleries. Include user galleries that may not be synced to the device galleries yet."""
        device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
        user_galleries = await self.meural.get_user_galleries()
        [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
        self._galleries = device_galleries  

        """Set up first item to display."""
        self._gallery_status = await self.local_meural.send_get_gallery_status()
        self._current_item = await self.meural.get_item(int(self._gallery_status["current_item"]))

        """Set up default image duration."""
        self._meural_device = await self.meural.get_device(self.meural_device_id)
        self._pause_duration = self._meural_device["imageDuration"]

    async def async_update(self):
        self._sleep = await self.local_meural.send_get_sleep()

        """Only poll the Meural API if the device is not sleeping."""
        if self._sleep == False:
            """Save orientation and item we had before polling."""
            old_orientation = self._meural_device["orientation"]
            self._meural_device = await self.meural.get_device(self.meural_device_id)
            old_item = int(self._gallery_status["current_item"])
            self._gallery_status = await self.local_meural.send_get_gallery_status()

            """Check if current item or orientation have changed."""
            local_item = int(self._gallery_status["current_item"])
            new_orientation = self._meural_device["orientation"]
            if old_item != local_item:
                """Only get item information if current item has changed since last poll."""
#                _LOGGER.warning("Item changed. Getting item from Meural API for ID %s", local_item)
                self._current_item = await self.meural.get_item(local_item)
            elif old_orientation != new_orientation:
                """If orientationMatch is enabled, current item in gallery_status will not reflect item displayed after orientation changes. Force update of gallery_status by reloading gallery."""
#                _LOGGER.warning("Orientation changed. Force update.")
                await self.local_meural.send_change_gallery(self._gallery_status["current_gallery"])

    @property
    def name(self):
        """Name of the device."""
        return self._meural_device["alias"]

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
        return self._current_item["description"]

    @property
    def media_title(self):
        """Return the title of current playing media."""
        return self._current_item["name"]

    @property
    def media_artist(self):
        """Artist of current playing media, music track only. Replaced with artist name and the artwork year."""        
        if self._current_item["artistName"] is not None:
            if self._current_item["year"] is not None:
                return self._current_item["artistName"] + ", " + self._current_item["year"]
            else:
                return self._current_item["artistName"]
        elif self._current_item["author"] is not None:
            if self._current_item["year"] is not None:
                return self._current_item["author"] + ", " + self._current_item["year"]
            else:
                return self._current_item["author"]
        elif self._current_item["year"] is not None:
            return "Unknown, " + str(self._current_item["year"])
        return ""

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        return self._current_item["image"]
 
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

    async def async_select_source(self, source):
        """Select playlist to display."""
        source = next((g["id"] for g in self._galleries if g["name"] == source), None)
        if source is None:
            _LOGGER.warning("Source %s not found", source)
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
        await self.meural.update_device(self.meural_device_id, {"imageDuration": 0})

    async def async_media_play(self):
        """Restore duration from pause_duration (play). Use duration 300 if no pause_duration was stored."""
        if self._pause_duration != 0:
            await self.meural.update_device(self.meural_device_id, {"imageDuration": self._pause_duration})
        else:
            await self.meural.update_device(self.meural_device_id, {"imageDuration": 300})            

    async def async_set_shuffle(self, shuffle):
        """Enable/disable shuffling."""
        await self.meural.update_device(self.meural_device_id, {"imageShuffle": shuffle})

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Display an image. To use a local call this image has to be in the currently selected playlist, or unexpected behavior can occur. Call Meural API if this is not the case."""
        if media_id.isdigit():
            currentgallery_id = self._gallery_status["current_gallery"]
            currentitems = await self.local_meural.send_get_items_by_gallery(currentgallery_id)
            in_playlist = next((g["title"] for g in currentitems if g["id"] == media_id), None)
            if in_playlist is None:
#                _LOGGER.warning("Item %s is not in current playlist, trying to play via remote API.", media_id)
                await self.meural.device_load_item(self.meural_device_id, media_id)
            else:
#                _LOGGER.warning("Item %s in current playlist %s, loading locally.", media_id, self._gallery_status["current_gallery_name"])
                await self.local_meural.send_change_item(media_id)
        else:
            _LOGGER.warning("Can't play media: %s is not an item ID", media_id)

    async def async_preview_image(self, file):
        test = await self.local_meural.send_postcard(file)
        _LOGGER.warning("Image %s ", test)
