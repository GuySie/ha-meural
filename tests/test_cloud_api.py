"""Unit tests for cloud-only Meural feature logic."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "meural"
PACKAGE_NAME = "meural_cloud_api_tests"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(COMPONENT_PATH)]
sys.modules[PACKAGE_NAME] = package


# Minimal HA stubs for the imports used by media_player.py.
def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.exceptions = types.ModuleType("homeassistant.exceptions")
    components = types.ModuleType("homeassistant.components")
    auth = types.ModuleType("homeassistant.auth")
    auth_models = types.ModuleType("homeassistant.auth.models")
    config_entries = types.ModuleType("homeassistant.config_entries")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    entity_platform_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    network_mod = types.ModuleType("homeassistant.helpers.network")
    update_coordinator_mod = types.ModuleType("homeassistant.helpers.update_coordinator")
    http_auth_mod = types.ModuleType("homeassistant.components.http.auth")
    media_source_mod = types.ModuleType("homeassistant.components.media_source")
    media_player_mod = types.ModuleType("homeassistant.components.media_player")
    media_player_const_mod = types.ModuleType("homeassistant.components.media_player.const")
    const_mod = types.ModuleType("homeassistant.const")

    class HomeAssistantError(Exception):
        pass

    class ConfigEntry:
        pass

    class HomeAssistant:
        pass

    class SupportsResponse:
        OPTIONAL = "optional"

    class RefreshToken:
        def __init__(self, id: str | None = None):
            self.id = id

    class MediaPlayerEntity:
        pass

    class BrowseError(Exception):
        pass

    class BrowseMedia:
        def __init__(
            self,
            title: str,
            media_class: str,
            media_content_id: str,
            media_content_type: str,
            can_play: bool,
            can_expand: bool,
            children: list["BrowseMedia"] | None = None,
            thumbnail: str | None = None,
        ) -> None:
            self.title = title
            self.media_class = media_class
            self.media_content_id = media_content_id
            self.media_content_type = media_content_type
            self.can_play = can_play
            self.can_expand = can_expand
            self.children = children or []
            self.thumbnail = thumbnail

    class MediaClass:
        DIRECTORY = "directory"

    class MediaType:
        IMAGE = "image"
        PLAYLIST = "playlist"

    class MediaPlayerEntityFeature(int):
        BROWSE_MEDIA = 1
        SELECT_SOURCE = 2
        NEXT_TRACK = 4
        PAUSE = 8
        PLAY = 16
        PLAY_MEDIA = 32
        PREVIOUS_TRACK = 64
        SHUFFLE_SET = 128
        TURN_OFF = 256
        TURN_ON = 512

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator
            self.hass = None

        def async_on_remove(self, func):
            return func

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.last_update_success = True
            self.data = {}

    class AddEntitiesCallback:
        pass

    class EntityPlatform:
        def __init__(self):
            self.async_register_entity_service = lambda *args, **kwargs: None

    homeassistant.exceptions.HomeAssistantError = HomeAssistantError
    homeassistant.components = components
    homeassistant.auth = auth
    homeassistant.auth.models = auth_models
    homeassistant.config_entries = config_entries
    homeassistant.core = core
    homeassistant.helpers = helpers

    auth_models.RefreshToken = RefreshToken
    config_entries.ConfigEntry = ConfigEntry
    core.HomeAssistant = HomeAssistant
    core.SupportsResponse = SupportsResponse

    media_player_mod.MediaPlayerEntity = MediaPlayerEntity
    media_player_mod.BrowseError = BrowseError
    media_player_mod.BrowseMedia = BrowseMedia
    media_player_mod.MediaClass = MediaClass
    media_player_mod.MediaType = MediaType
    media_player_mod.MediaPlayerEntityFeature = MediaPlayerEntityFeature
    media_player_const_mod.MediaPlayerEntityFeature = MediaPlayerEntityFeature

    const_mod.STATE_PLAYING = "playing"
    const_mod.STATE_PAUSED = "paused"
    const_mod.STATE_OFF = "off"

    http_auth_mod.async_sign_path = lambda *args, **kwargs: "/signed"
    network_mod.get_url = lambda *args, **kwargs: "http://example.com"
    media_source_mod.is_media_source_id = lambda media_id: False
    media_source_mod.async_resolve_media = None
    media_source_mod.async_browse_media = None

    entity_platform_mod.current_platform = types.SimpleNamespace(get=lambda: EntityPlatform())
    entity_platform_mod.AddEntitiesCallback = AddEntitiesCallback
    update_coordinator_mod.CoordinatorEntity = CoordinatorEntity
    update_coordinator_mod.DataUpdateCoordinator = DataUpdateCoordinator

    homeassistant.components.media_player = media_player_mod
    homeassistant.components.media_source = media_source_mod
    homeassistant.components.media_player.const = media_player_const_mod

    helpers.entity_platform = entity_platform_mod
    helpers.network = network_mod
    helpers.update_coordinator = update_coordinator_mod

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.exceptions"] = homeassistant.exceptions
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.http.auth"] = http_auth_mod
    sys.modules["homeassistant.components.media_source"] = media_source_mod
    sys.modules["homeassistant.components.media_player"] = media_player_mod
    sys.modules["homeassistant.components.media_player.const"] = media_player_const_mod
    sys.modules["homeassistant.auth"] = auth
    sys.modules["homeassistant.auth.models"] = auth_models
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform_mod
    sys.modules["homeassistant.helpers.network"] = network_mod
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator_mod
    sys.modules["homeassistant.const"] = const_mod


_install_homeassistant_stubs()

# Stub the cloud/local coordinator and auth exceptions used by the module.
coordinator_mod = types.ModuleType(f"{PACKAGE_NAME}.coordinator")

class CloudDataUpdateCoordinator:
    def __init__(self, *args, **kwargs):
        self.data = {}

class LocalDataUpdateCoordinator:
    def __init__(self, *args, **kwargs):
        self.data = {}
        self.sleeping = False
        self.last_update_success = True

coordinator_mod.CloudDataUpdateCoordinator = CloudDataUpdateCoordinator
coordinator_mod.LocalDataUpdateCoordinator = LocalDataUpdateCoordinator
sys.modules[f"{PACKAGE_NAME}.coordinator"] = coordinator_mod

netgear_auth_mod = types.ModuleType(f"{PACKAGE_NAME}.netgear_auth")

class CannotConnect(Exception):
    pass

class InvalidAuth(Exception):
    pass

class AuthenticationBlocked(Exception):
    pass

netgear_auth_mod.CannotConnect = CannotConnect
netgear_auth_mod.InvalidAuth = InvalidAuth
netgear_auth_mod.AuthenticationBlocked = AuthenticationBlocked
sys.modules[f"{PACKAGE_NAME}.netgear_auth"] = netgear_auth_mod


def load_module(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE_NAME}.{module_name}", COMPONENT_PATH / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


const_module = load_module("const", "const.py")
media_player_module = load_module("media_player", "media_player.py")


class FakeLocalMeural:
    def __init__(self):
        self.calls: list[tuple[str, str | int]] = []

    async def send_change_gallery(self, gallery_id: str | int):
        self.calls.append(("send_change_gallery", gallery_id))


class FakeLocalCoordinator:
    def __init__(self, data: dict[str, object] | None = None):
        self.data = data or {}
        self.sleeping = False
        self.last_update_success = True
        self.async_refresh_calls = 0

    async def async_refresh(self):
        self.async_refresh_calls += 1


class FakeCloudCoordinator:
    def __init__(self, data: dict[str, object] | None = None):
        self.data = data or {}

    def notify_sleep_state_changed(self):
        pass


class FakeMeuralClient:
    def __init__(self):
        self.calls: list[tuple[str, str | int, str | int]] = []

    async def device_load_gallery(self, device_id: str | int, gallery_id: str | int):
        self.calls.append(("device_load_gallery", device_id, gallery_id))

    async def delete_device_gallery(self, device_id: str | int, gallery_id: str | int):
        self.calls.append(("delete_device_gallery", device_id, gallery_id))


class CloudApiFeatureTests(unittest.IsolatedAsyncioTestCase):
    def _make_entity(self, *, cloud_data=None, local_data=None):
        entity = media_player_module.MeuralEntity.__new__(media_player_module.MeuralEntity)
        entity.meural = FakeMeuralClient()
        entity.cloud_coordinator = FakeCloudCoordinator(cloud_data or {})
        entity.local_coordinator = FakeLocalCoordinator(local_data or {})
        entity.local_meural = FakeLocalMeural()
        entity._meural_device = {
            "id": "abc123",
            "alias": "Test Frame",
            "productKey": "product-key",
            "frameModel": {"name": "Canvas"},
            "version": "1.0",
            "localIp": "192.168.1.50",
            "status": "online",
            "imageDuration": 60,
            "imageShuffle": False,
            "gestureFlip": False,
            "orientationMatch": False,
        }
        entity._current_item = {}
        entity._pause_duration = 0
        entity._last_fetched_item_id = None
        entity._last_gsensor = None
        return entity

    async def test_play_random_cloud_playlist_skips_current_gallery_and_uses_cloud_data(self):
        entity = self._make_entity(
            cloud_data={
                "device_galleries": {"abc123": [{"id": 101, "name": "One"}, {"id": 102, "name": "Current"}]},
                "user_galleries": [{"id": 103, "name": "Three"}, {"id": 101, "name": "One"}],
            },
            local_data={
                "gallery_status": {"current_gallery": 102},
            },
        )

        with patch("random.choice", side_effect=lambda candidates: candidates[0]):
            result = await entity.async_play_random_cloud_playlist()

        self.assertEqual({"id": 103, "name": "Three"}, result)
        self.assertEqual(("device_load_gallery", "abc123", 103), entity.meural.calls[0])
        self.assertEqual(("send_change_gallery", 103), entity.local_meural.calls[0])
        self.assertEqual(1, entity.local_coordinator.async_refresh_calls)

    async def test_play_random_cloud_playlist_deduplicates_cloud_galleries_by_id(self):
        entity = self._make_entity(
            cloud_data={
                "device_galleries": {"abc123": [{"id": "101", "name": "One"}]},
                "user_galleries": [{"id": 101, "name": "One"}, {"id": "103", "name": "Three"}],
            },
            local_data={
                "gallery_status": {"current_gallery": 999},
            },
        )

        with patch("random.choice", side_effect=lambda candidates: candidates[0]):
            await entity.async_play_random_cloud_playlist()

        self.assertEqual(("device_load_gallery", "abc123", 103), entity.meural.calls[0])

    async def test_delete_playlist_by_gallery_id_calls_cloud_delete_and_refreshes(self):
        entity = self._make_entity(
            cloud_data={
                "device_galleries": {"abc123": [{"id": 11, "name": "Alpha"}]},
                "user_galleries": [{"id": 22, "name": "Beta"}],
            }
        )

        await entity.async_delete_playlist(gallery_id=11)

        self.assertEqual(("delete_device_gallery", "abc123", 11), entity.meural.calls[0])
        self.assertEqual(1, entity.local_coordinator.async_refresh_calls)

    async def test_delete_playlist_by_gallery_name_resolves_id_and_deletes(self):
        entity = self._make_entity(
            cloud_data={
                "device_galleries": {"abc123": [{"id": 11, "name": "Alpha"}]},
                "user_galleries": [{"id": 22, "name": "Beta"}],
            }
        )

        await entity.async_delete_playlist(gallery_name="Beta")

        self.assertEqual(("delete_device_gallery", "abc123", 22), entity.meural.calls[0])
        self.assertEqual(1, entity.local_coordinator.async_refresh_calls)

    async def test_delete_playlist_requires_gallery_id_or_gallery_name(self):
        entity = self._make_entity()

        await entity.async_delete_playlist()

        self.assertEqual([], entity.meural.calls)
        self.assertEqual(0, entity.local_coordinator.async_refresh_calls)


if __name__ == "__main__":
    unittest.main()
