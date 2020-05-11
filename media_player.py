import logging

from homeassistant.components.media_player import MediaPlayerDevice

from homeassistant.components.media_player import STATE_PLAYING
from homeassistant.components.media_player.const import SUPPORT_SELECT_SOURCE

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    meural = hass.data[DOMAIN][config_entry.entry_id]
    devices = await meural.get_user_devices()
    async_add_entities(MeuralEntity(meural, device) for device in devices)


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
        return SUPPORT_SELECT_SOURCE

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
