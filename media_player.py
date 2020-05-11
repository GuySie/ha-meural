import logging
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerDevice

from homeassistant.components.media_player import STATE_PLAYING
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

MEURAL_SUPPORT = SUPPORT_SELECT_SOURCE | SUPPORT_NEXT_TRACK | SUPPORT_PAUSE | SUPPORT_PLAY | SUPPORT_PREVIOUS_TRACK | SUPPORT_SHUFFLE_SET | SUPPORT_TURN_OFF | SUPPORT_TURN_ON


async def async_setup_entry(hass, config_entry, async_add_entities):
    meural = hass.data[DOMAIN][config_entry.entry_id]
    devices = await meural.get_user_devices()
    async_add_entities(MeuralEntity(meural, device) for device in devices)
    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "change_duration",
        {
            vol.Required("time"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=3600)
            )
        },
        "async_change_duration",
    )

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        "set_device_option",
        {
            vol.Optional("time"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=3600)
            ),
            vol.Optional("shuffle"): bool
        },
        "async_set_device_option",
    )

class MeuralEntity(MediaPlayerDevice):
    """Representation of a Meural entity."""

    def __init__(self, meural, device):
        self.meural = meural
        self._meural_device = device
        self._galleries = []

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
        device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
        user_galleries = await self.meural.get_user_galleries()
        self._galleries = user_galleries + device_galleries

    async def async_update(self):
        self._meural_device = await self.meural.get_device(self.meural_device_id)

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
        return STATE_PLAYING

    @property
    def source(self):
        """Name of the current input source."""
        return None

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

    async def async_change_duration(self, time):
        await self.meural.update_device(self.meural_device_id, {"imageDuration": time})

    async def async_set_device_option(self, time=None, shuffle=None):
        params = {}
        if time is not None:
           params["imageDuration"] = time
        if shuffle is not None:
           params["imageShuffle"] = shuffle
        await self.meural.update_device(self.meural_device_id, params)

    async def async_turn_on(self):
        """Resume Meural frame display."""
        await self.local_meural.send_key_resume()

    async def async_turn_off(self):
        """Suspend Meural frame display."""
        await self.local_meural.send_key_suspend()

