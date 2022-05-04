from datetime import timedelta
import logging
import voluptuous as vol

try:
    from homeassistant.components.media_player import MediaPlayerEntity
except ImportError:
    from homeassistant.components.media_player import MediaPlayerDevice as MediaPlayerEntity

from homeassistant.auth.models import RefreshToken
from homeassistant.components import media_source
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, BrowseMedia
from homeassistant.helpers import entity_platform
from homeassistant.helpers.network import get_url

from homeassistant.const import (
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_OFF,
    MAJOR_VERSION,
    MINOR_VERSION,
)

from homeassistant.components.media_player.const import (
    MEDIA_CLASS_DIRECTORY,
    MEDIA_TYPE_IMAGE,
    MEDIA_TYPE_PLAYLIST,
    MediaPlayerEntityFeature,
)

from .const import DOMAIN
from .pymeural import LocalMeural

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
        "preview_image_cloud",
        {
            vol.Required("content_url"): str,
            vol.Required("content_type"): str,
            vol.Optional("name"): str,
            vol.Optional("author"): str,
            vol.Optional("description"): str,
            vol.Optional("medium"): str,
            vol.Optional("year"): str,
        },
        "async_preview_image_cloud",
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
        """Set up default image duration."""
        try:
            _LOGGER.info("Meural device %s: Setup. Getting device information from Meural server", self.name)
            self._meural_device = await self.meural.get_device(self.meural_device_id)
            self._pause_duration = self._meural_device["imageDuration"]
        except:
            _LOGGER.error("Meural device %s: Setup. Error while contacting Meural server, aborting setup", self.name, exc_info=True)
            self._abort = True
            return

        """Set up local galleries."""
        try:
            localgalleries = await self.local_meural.send_get_galleries()
            self._galleries = sorted(localgalleries, key = lambda i: i["name"])
            _LOGGER.info("Meural device %s: Setup. Has %d local galleries on local device" % (self.name, len(self._galleries)))
        except:
            _LOGGER.error("Meural device %s: Setup. Error while contacting local device, aborting setup", self.name, exc_info=True)
            self._abort = True
            return

        """Set up remote galleries."""
        try:
            device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
            _LOGGER.info("Meural device %s: Setup. Getting %d device galleries from Meural server", self.name, len(device_galleries))
            user_galleries = await self.meural.get_user_galleries()
            _LOGGER.info("Meural device %s: Setup. Getting %d user galleries from Meural server", self.name, len(user_galleries))
            [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
            self._remote_galleries = device_galleries
            _LOGGER.info("Meural device %s: Setup. Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))
        except:
            _LOGGER.error("Meural device %s: Setup. Error while contacting Meural server, aborting setup", self.name, exc_info=True)
            self._abort = True
            return

        """Check if current gallery is an SD-card folder (ID 1, 2, 3 or 4) and set up first item to display."""
        self._gallery_status = await self.local_meural.send_get_gallery_status()
        current_gallery = int(self._gallery_status["current_gallery"])
        if current_gallery > 4:
            try:
                self._current_item = await self.meural.get_item(int(self._gallery_status["current_item"]))
            except:
                _LOGGER.warning("Meural device %s: Setup. Error while getting information of currently displayed item from Meural server, resetting item information",  self.name, exc_info=True)
                self._current_item = {}
        else:
            _LOGGER.info("Meural device %s: Setup. Gallery %s is a local SD-card folder, resetting item information", self.name, current_gallery)
            self._current_item = {}

        _LOGGER.info("Meural device %s: Setup has completed",  self.name)

    async def async_update(self):
        if self._abort == True:
            _LOGGER.debug("Meural device %s: Updating. Setup was aborted, device will not be updated", self.name)
            return

        try:
            self._sleep = await self.local_meural.send_get_sleep()
        except:
            _LOGGER.warning("Meural device %s: Updating. Error while contacting local device", self.name, exc_info=True)
            self._sleep = True

        """Only poll the Meural API if the device is not sleeping."""
        if self._sleep == False:
            """Update local galleries."""
            localgalleries = await self.local_meural.send_get_galleries()
            self._galleries = sorted(localgalleries, key = lambda i: i["name"])
            """Save orientation we had before update and poll new remote state."""
            old_orientation = self._meural_device["orientation"]
            self._meural_device = await self.meural.get_device(self.meural_device_id)
            """Save item we had before update and poll new local state."""
            old_item = int(self._gallery_status["current_item"])
            self._gallery_status = await self.local_meural.send_get_gallery_status()
            """Check if current gallery is based on a folder on the SD-card (ID 1, 2, 3 or 4)."""
            current_gallery = int(self._gallery_status["current_gallery"])
            if current_gallery > 4:

                """Check if current item or orientation have changed."""
                new_item = int(self._gallery_status["current_item"])
                new_orientation = self._meural_device["orientation"]
                if old_item != new_item:
                    """Only get item information if current item has changed since last poll."""
                    _LOGGER.info("Meural device %s: Updating. Item changed. Getting information from Meural server for item %s", self.name, new_item)
                    try:
                        self._current_item = await self.meural.get_item(new_item)
                    except:
                        _LOGGER.warning("Meural device %s: Updating. Error while getting information of currently displayed item %s from Meural server, resetting item information", self.name, new_item, exc_info=True)
                        self._current_item = {}
                elif old_orientation != new_orientation:
                    """If orientationMatch is enabled, current item in gallery_status will not reflect item displayed after orientation changes. Force update of gallery_status by reloading gallery."""
                    _LOGGER.info("Meural device %s: Updating. Orientation has changed, reloading gallery to force update of currently displayed item", self.name)
                    await self.local_meural.send_change_gallery(self._gallery_status["current_gallery"])
            else:
                _LOGGER.info("Meural device %s: Updating. Gallery %s is a local SD-card folder, resetting item information", self.name, current_gallery)
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
            "configuration_url": "http://" + self._meural_device["localIp"] + "/remote/",
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
        """Return the content type of current playing media."""
        return MEDIA_TYPE_IMAGE

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

    async def async_select_source(self, source):
        """Select playlist to display."""
        source = next((g["id"] for g in self._galleries if g["name"] == source), None)
        if source is None:
            _LOGGER.warning("Meural device %s: Selecting source. Source %s not found", self.name, source)
        await self.local_meural.send_change_gallery(source)

    async def async_media_previous_track(self):
        """Send previous image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_right()
        else:
            await self.local_meural.send_key_left()

    async def async_media_next_track(self):
        """Send next image command."""
        if self._meural_device["gestureFlip"] == True:
            await self.local_meural.send_key_left()
        else:
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
        use_cloud = kwargs.get("use_cloud", False)
        if use_cloud:
            name = kwargs.get("name")
            author = kwargs.get("author")
            description = kwargs.get("description")
            medium = kwargs.get("medium")
            year = kwargs.get("year")

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
            if use_cloud:
                await self.meural.send_postcard_cloud(self._meural_device, media_id, media_type, name, author, description, medium, year)
            else:
                await self.local_meural.send_postcard(media_id, media_type)

        # Play gallery (playlist or album) by ID.
        elif media_type in ['playlist']:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, playing gallery %s", self.name, media_type, media_id)
            await self.local_meural.send_change_gallery(media_id)

        # "Preview image from URL.
        if media_type in [ 'image/jpg', 'image/png', 'image/jpeg', 'image/gif' ]:
            _LOGGER.info("Meural device %s: Playing media. Media type is %s, previewing image from %s", self.name, media_type, media_id)
            if use_cloud:
                await self.meural.send_postcard_cloud(self._meural_device, media_id, media_type, name, author, description, medium, year)
            else:
                await self.local_meural.send_postcard(media_id, media_type)

        # Play item (artwork) by ID. Play locally if item is in currently displayed gallery. If not, play using Meural server."""
        elif media_type in ['item']:
            if media_id.isdigit():
                currentgallery_id = self._gallery_status["current_gallery"]
                currentitems = await self.local_meural.send_get_items_by_gallery(currentgallery_id)
                in_playlist = next((g["title"] for g in currentitems if g["id"] == media_id), None)
                if in_playlist is None:
                    _LOGGER.info("Meural device %s: Playing media. Item %s is not in current gallery, trying to display via Meural server", self.name, media_id)
                    try:
                        await self.meural.device_load_item(self.meural_device_id, media_id)
                    except:
                        _LOGGER.error("Meural device %s: Playing media. Error while trying to display %s item %s via Meural server", self.name, media_type, media_id, exc_info=True)
                else:
                    _LOGGER.info("Meural device %s: Playing media. Item %s is in current gallery %s, trying to display via local device", self.name, media_id, self._gallery_status["current_gallery_name"])
                    await self.local_meural.send_change_item(media_id)
            else:
                _LOGGER.error("Meural device %s: Playing media. ID %s is not an item", self.name, media_id)

        # This is an unsupported media type.
        else:
            _LOGGER.error("Meural device %s: Playing media. Does not support displaying this %s media with ID %s", self.name, media_type, media_id)

    async def async_preview_image(self, content_url, content_type):
        """Preview image from URL."""
        if content_type in [ 'image/jpg', 'image/png', 'image/jpeg' ]:
            _LOGGER.info("Meural device %s: Previewing image. Media type is %s, previewing image from %s", self.name, content_type, content_url)
            await self.async_play_media(media_type=content_type, media_id=content_url)
        else:
            _LOGGER.error("Meural device %s: Previewing image. Does not support media type %s", self.name, content_type)

    async def async_preview_image_cloud(self, content_url, content_type, name=None, author=None, description=None, medium=None, year=None):
        """Preview image from URL."""
        if content_type in [ 'image/jpg', 'image/png', 'image/jpeg', 'image/gif' ]:
            _LOGGER.info("Meural device %s: Previewing image via meural cloud. Media type is %s, previewing image from %s", self.name, content_type, content_url)
            await self.async_play_media(media_type=content_type, media_id=content_url, use_cloud=True, name=name, author=author, description=description, medium=medium, year=year)
        else:
            _LOGGER.error("Meural device %s: Previewing image. Does not support media type %s", self.name, content_type)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        _LOGGER.debug("Meural device %s: Browsing media. Media_content_type is %s, media_content_id is %s", self.name, media_content_type, media_content_id)
        if media_content_id in (None, "") and media_content_type in (None, ""):
            response = BrowseMedia(
                title="Meural Canvas",
                media_class=MEDIA_CLASS_DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[BrowseMedia(
                    title="Media Source",
                    media_class=MEDIA_CLASS_DIRECTORY,
                    media_content_id="",
                    media_content_type="localmediasource",
                    can_play=False,
                    can_expand=True),
                BrowseMedia(
                    title="Meural Playlists",
                    media_class=MEDIA_CLASS_DIRECTORY,
                    media_content_id="",
                    media_content_type="meuralplaylists",
                    can_play=False,
                    can_expand=True),
                ]
            )
            return response

        elif media_source.is_media_source_id(media_content_id) or media_content_type=="localmediasource":
            kwargs = {}
            if MAJOR_VERSION > 2022 or (MAJOR_VERSION == 2022 and MINOR_VERSION >= 2):
                kwargs['content_filter'] = lambda item: item.media_content_type in ('image/jpg', 'image/png', 'image/jpeg')

            response = await media_source.async_browse_media(self.hass, media_content_id, **kwargs)
            return response

        elif media_content_type=="meuralplaylists":
            response = BrowseMedia(
                title="Meural Playlists",
                media_class=MEDIA_CLASS_DIRECTORY,
                media_content_id="",
                media_content_type="",
                can_play=False,
                can_expand=True,
                children=[])

            device_galleries = await self.meural.get_device_galleries(self.meural_device_id)
            _LOGGER.info("Meural device %s: Browsing media. Getting %d device galleries from Meural server", self.name, len(device_galleries))
            user_galleries = await self.meural.get_user_galleries()
            _LOGGER.info("Meural device %s: Browsing media. Getting %d user galleries from Meural server", self.name, len(user_galleries))
            [device_galleries.append(x) for x in user_galleries if x not in device_galleries]
            self._remote_galleries = device_galleries
            _LOGGER.info("Meural device %s: Browsing media. Has %d unique remote galleries on Meural server" % (self.name, len(self._remote_galleries)))

            for g in self._galleries:

                thumb=next((h["cover"] for h in self._remote_galleries if h["id"] == int(g["id"])), None)
                if thumb == None and (int(g["id"])>4):
                    _LOGGER.debug("Meural device %s: Browsing media. Gallery %s misses thumbnail, getting gallery items", self.name, g["id"])
                    album_items = await self.local_meural.send_get_items_by_gallery(g["id"])
                    _LOGGER.info("Meural device %s: Browsing media. Replacing missing thumbnail of gallery %s with first gallery item image. Getting information from Meural server for item %s", self.name, g["id"], album_items[0]["id"])
                    first_item = await self.meural.get_item(album_items[0]["id"])
                    thumb = first_item["image"]
                _LOGGER.debug("Meural device %s: Browsing media. Thumbnail image for gallery %s is %s", self.name, g["id"], thumb)

                response.children.append(BrowseMedia(
                    title=g["name"],
                    media_class=MEDIA_TYPE_PLAYLIST,
                    media_content_id=g["id"],
                    media_content_type=MEDIA_TYPE_PLAYLIST,
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
