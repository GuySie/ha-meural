import logging
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerDevice

from homeassistant.const import (
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_STANDBY,
)

from homeassistant.components.media_player.const import (
    SUPPORT_SELECT_SOURCE,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
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
        "reset_brightness",
        {},
        "async_reset_brightness",
    )

    platform.async_register_entity_service(
        "set_device_orientation",
        {
            vol.Required("orientation"): str
        },
        "async_set_device_orientation",
    )

    platform.async_register_entity_service(
        "load_gallery",
        {
            vol.Required("gallery"): str
        },
        "async_load_gallery",
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

class MeuralEntity(MediaPlayerDevice):
    """Representation of a Meural entity."""

    def __init__(self, meural, device):
        self.meural = meural
        self._meural_device = device
        self._galleries = []

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
        """Include user galleries that may not be synced to the device galleries yet."""
        device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
        user_galleries = await self.meural.get_user_galleries()
        [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
        self._galleries = device_galleries  

    async def async_update(self):
        self._sleep = await self.local_meural.send_get_sleep()

        self._meural_device = await self.meural.get_device(self.meural_device_id)
 
        localdata = await self.local_meural.send_get_gallery_status()
        localitem = int(localdata["current_item"])
        remoteitem = int(self._meural_device["frameStatus"]["currentItem"])
        if localitem != remoteitem:
            _LOGGER.warning("Syncing with Meural API because local item ID %s is not remote item ID %s", localitem, remoteitem)
            await self.meural.sync_device(self.meural_device_id)

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
            return STATE_STANDBY
        elif self._meural_device["imageDuration"] == 0:
            return STATE_PAUSED
        return STATE_PLAYING

    @property
    def source(self):
        """Name of the current input source."""
        sourceid = self._meural_device["currentGallery"]
        inputsource = [g["name"] for g in self._galleries if g["id"] == sourceid]
        if inputsource is None:
            _LOGGER.warning("Source %s not found", sourceid)        
        return inputsource

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return MEURAL_SUPPORT

    @property
    def source_list(self):
        """List of available input sources."""
        return [g["name"] for g in self._galleries]

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        return self._meural_device["currentImage"]
 
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
        await self.local_meural.send_control_backlight(brightness)

    async def async_reset_brightness(self):
        await self.local_meural.send_als_calibrate_off()

    async def async_select_source(self, source):
        """Select input source."""
        source = next((g["id"] for g in self._galleries if g["name"] == source), None)
        if source is None:
            _LOGGER.warning("Source %s not found", source)
        await self.meural.device_load_gallery(self.meural_device_id, source)

    async def async_media_previous_track(self):
        """Send previous track command."""
        await self.local_meural.send_key_left()

    async def async_media_next_track(self):
        """Send next track command."""
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

    async def async_set_device_orientation(self, orientation):
        """Set horizontal or vertical device orientation."""
        if orientation == 'vertical':
            await self.local_meural.send_set_portrait()
        elif orientation == 'horizontal':
            await self.local_meural.send_set_landscape()

    async def async_load_gallery(self, gallery):
        """Change gallery being displayed."""
        gallery = next((g["id"] for g in self._galleries if g["name"] == gallery), None)
        if gallery is None:
            _LOGGER.warning("Source %s not found", gallery)
        await self.meural.device_load_gallery(self.meural_device_id, gallery)
