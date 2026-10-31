"""Unit tests for transient local Canvas connection failures."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Self

import aiohttp

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "meural"
PACKAGE_NAME = "meural_local_api_tests"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(COMPONENT_PATH)]
sys.modules[PACKAGE_NAME] = package

try:
    from homeassistant.exceptions import HomeAssistantError
except ImportError:
    homeassistant = types.ModuleType("homeassistant")
    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        """Minimal Home Assistant error stub for isolated client tests."""

    exceptions.HomeAssistantError = HomeAssistantError
    homeassistant.exceptions = exceptions
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.exceptions", exceptions)

MODULE_PATH = COMPONENT_PATH / "pymeural.py"
SPEC = importlib.util.spec_from_file_location(f"{PACKAGE_NAME}.pymeural", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
pymeural = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pymeural
SPEC.loader.exec_module(pymeural)


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, body: Any) -> None:
        self.body = body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, content_type=None) -> Any:
        return self.body


class RaisingRequest:
    """Request context manager that fails while connecting."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self) -> None:
        raise self.error

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeSession:
    """Queue request contexts and capture outgoing options."""

    def __init__(self, *outcomes: FakeResponse | RaisingRequest) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"Unexpected request: {method} {url}")
        return self.outcomes.pop(0)


class LocalMeuralRequestTest(unittest.IsolatedAsyncioTestCase):
    """Exercise retry and connection handling for the local Canvas API."""

    def setUp(self) -> None:
        pymeural.LOCAL_RETRY_DELAY = 0

    async def test_safe_read_retries_after_server_disconnect(self) -> None:
        session = FakeSession(
            RaisingRequest(aiohttp.ServerDisconnectedError()),
            FakeResponse({"response": False}),
        )
        client = pymeural.LocalMeural(
            {"localIp": "192.0.2.10", "alias": "Test Canvas"}, session
        )

        sleeping = await client.send_get_sleep()

        self.assertFalse(sleeping)
        self.assertEqual(2, len(session.requests))
        self.assertNotIn("Connection", session.requests[0]["headers"])
        self.assertEqual("close", session.requests[1]["headers"]["Connection"])

    async def test_safe_read_raises_after_second_interruption(self) -> None:
        session = FakeSession(
            RaisingRequest(aiohttp.ServerDisconnectedError()),
            RaisingRequest(aiohttp.ClientOSError(104, "Connection reset by peer")),
        )
        client = pymeural.LocalMeural(
            {"localIp": "192.0.2.10", "alias": "Test Canvas"}, session
        )

        with self.assertRaises(aiohttp.ClientOSError):
            await client.send_get_system()

        self.assertEqual(2, len(session.requests))

    async def test_control_command_is_not_retried(self) -> None:
        session = FakeSession(RaisingRequest(aiohttp.ServerDisconnectedError()))
        client = pymeural.LocalMeural(
            {"localIp": "192.0.2.10", "alias": "Test Canvas"}, session
        )

        with self.assertRaises(aiohttp.ServerDisconnectedError):
            await client.send_key_right()

        self.assertEqual(1, len(session.requests))

    async def test_device_update_uses_new_cloud_ip(self) -> None:
        session = FakeSession(FakeResponse({"response": True}))
        client = pymeural.LocalMeural(
            {"localIp": "192.0.2.10", "alias": "Test Canvas"}, session
        )

        changed = client.update_device(
            {"localIp": "192.0.2.25", "alias": "Test Canvas"}
        )
        sleeping = await client.send_get_sleep()

        self.assertTrue(changed)
        self.assertTrue(sleeping)
        self.assertEqual("192.0.2.25", client.ip)
        self.assertIn("http://192.0.2.25/", session.requests[0]["url"])

    async def test_device_update_reports_unchanged_ip(self) -> None:
        session = FakeSession()
        client = pymeural.LocalMeural(
            {"localIp": "192.0.2.10", "alias": "Test Canvas"}, session
        )

        changed = client.update_device(
            {"localIp": "192.0.2.10", "alias": "Renamed Canvas"}
        )

        self.assertFalse(changed)
        self.assertEqual("Renamed Canvas", client.device["alias"])


class BuildLocalDeviceTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the local-only device probe used by the config/reconfigure flow."""

    def setUp(self) -> None:
        pymeural.LOCAL_RETRY_DELAY = 0

    async def test_maps_identify_and_system_fields(self) -> None:
        session = FakeSession(
            FakeResponse(
                {
                    "response": {
                        "product_key": "PK123",
                        "frame_name": "Living Room",
                        "sku": "Canvas II",
                    }
                }
            ),
            FakeResponse({"response": {"version": "3.1.60"}}),
        )

        device = await pymeural.build_local_device("192.0.2.10", None, session)

        self.assertEqual("PK123", device["productKey"])
        self.assertEqual("PK123", device["id"])
        self.assertEqual("Living Room", device["alias"])
        self.assertEqual("Living Room", device["name"])
        self.assertEqual("Canvas II", device["frameModel"]["name"])
        self.assertEqual("3.1.60", device["version"])
        self.assertEqual("192.0.2.10", device["localIp"])
        self.assertEqual("normal", device["status"])
        self.assertFalse(device["imageShuffle"])
        self.assertFalse(device["gestureFlip"])

    async def test_user_supplied_name_overrides_frame_name(self) -> None:
        session = FakeSession(
            FakeResponse({"response": {"product_key": "PK1", "frame_name": "Default"}}),
            FakeResponse({"response": {"version": "1.0"}}),
        )

        device = await pymeural.build_local_device("192.0.2.10", "Office", session)

        self.assertEqual("Office", device["alias"])

    async def test_missing_fields_fall_back_to_defaults(self) -> None:
        session = FakeSession(
            FakeResponse({"response": {}}),
            FakeResponse({"response": {}}),
        )

        device = await pymeural.build_local_device("192.0.2.33", None, session)

        # No product key -> unique id falls back to the host.
        self.assertEqual("192.0.2.33", device["productKey"])
        self.assertEqual("192.0.2.33", device["alias"])
        self.assertEqual("Meural Canvas", device["frameModel"]["name"])
        self.assertEqual("unknown", device["version"])

    async def test_system_failure_is_tolerated(self) -> None:
        # identify succeeds; the (best-effort) system call fails on both attempts.
        session = FakeSession(
            FakeResponse({"response": {"product_key": "PK9", "version": "2.0"}}),
            RaisingRequest(aiohttp.ServerDisconnectedError()),
            RaisingRequest(aiohttp.ClientOSError(104, "Connection reset by peer")),
        )

        device = await pymeural.build_local_device("192.0.2.5", None, session)

        self.assertEqual("PK9", device["productKey"])
        # Version falls back to the identify value when system is unavailable.
        self.assertEqual("2.0", device["version"])

    async def test_unreachable_device_raises_connection_error(self) -> None:
        session = FakeSession(
            RaisingRequest(aiohttp.ServerDisconnectedError()),
            RaisingRequest(aiohttp.ClientOSError(104, "Connection reset by peer")),
        )

        with self.assertRaises(aiohttp.ClientError):
            await pymeural.build_local_device("192.0.2.99", None, session)


if __name__ == "__main__":
    unittest.main()
