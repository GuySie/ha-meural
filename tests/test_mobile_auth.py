"""Unit tests for the browser-assisted mobile authentication handoff."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class FlowResultType(Enum):
    """Minimal Home Assistant flow result type used by the view."""

    EXTERNAL_STEP_DONE = "external_done"
    FORM = "form"


class UnknownFlow(Exception):
    """Minimal Home Assistant unknown-flow exception."""


class HomeAssistantView:
    """Minimal Home Assistant HTTP view base class."""


def _load_mobile_auth_module():
    """Load mobile_auth.py with small Home Assistant dependency stubs."""
    package = types.ModuleType("meural_mobile_auth_test")
    package.__path__ = []
    sys.modules[package.__name__] = package

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    http = types.ModuleType("homeassistant.components.http")
    http.KEY_HASS = "hass"
    http.HomeAssistantView = HomeAssistantView
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.EVENT_DATA_ENTRY_FLOW_PROGRESSED = "data_entry_flow_progressed"
    data_entry_flow.FlowResultType = FlowResultType
    data_entry_flow.UnknownFlow = UnknownFlow
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.http": http,
            "homeassistant.core": core,
            "homeassistant.data_entry_flow": data_entry_flow,
        }
    )

    const = types.ModuleType(f"{package.__name__}.const")
    const.DOMAIN = "meural"
    sys.modules[const.__name__] = const
    netgear_auth = types.ModuleType(f"{package.__name__}.netgear_auth")
    netgear_auth.COGNITO_CLIENT_ID = "test-client-id"
    netgear_auth.COGNITO_URL = "https://cognito-idp.example.test/"
    netgear_auth.PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS = ("verification",)
    netgear_auth.RESPONSE_KEY_MAP = {"CUSTOM_CHALLENGE": "ANSWER"}
    netgear_auth.USER_MIGRATION_KEYWORDS = ("usernotfoundexception",)
    netgear_auth.WAF_BLOCK_PAGE_KEYWORDS = ("request blocked",)
    netgear_auth.WAF_ERROR_KEYWORDS = ("waf",)
    sys.modules[netgear_auth.__name__] = netgear_auth

    module_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "meural"
        / "mobile_auth.py"
    )
    module_name = f"{package.__name__}.mobile_auth"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


mobile_auth = _load_mobile_auth_module()


class FakeHttp:
    """Capture Home Assistant HTTP view registration."""

    def __init__(self) -> None:
        self.views = []

    def register_view(self, view) -> None:
        self.views.append(view)


class FakeFlowManager:
    """Return queued outcomes and count handoff attempts."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []
        self.current = None

    async def async_configure(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        # Home Assistant's public async_get() returns only flow identity and
        # current step, not the full FlowResult (including its type).
        self.current = {
            "handler": outcome["handler"],
            "step_id": outcome["step_id"],
        }
        return outcome

    def async_get(self, flow_id: str):
        if self.current is None:
            raise UnknownFlow
        return self.current


class FakeBus:
    """Capture internal Home Assistant flow progress events."""

    def __init__(self) -> None:
        self.events = []

    def async_fire(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class FakeHass:
    """Small Home Assistant object used by the mobile auth view."""

    def __init__(self, *outcomes) -> None:
        self.data = {}
        self.http = FakeHttp()
        self.bus = FakeBus()
        self.config_entries = types.SimpleNamespace(flow=FakeFlowManager(*outcomes))


class FakeRequest:
    """Small aiohttp request used by the mobile auth view."""

    def __init__(self, hass, state: str, payload: dict[str, str]) -> None:
        self.app = {"hass": hass}
        self.query = {"state": state}
        self.headers = {"Origin": "https://ha.example.test"}
        self.content_type = "application/json"
        self._payload = payload
        self.content_length = len(json.dumps(payload).encode())

    async def json(self):
        return self._payload


def _session(hass: FakeHass) -> tuple[str, dict[str, str]]:
    url = mobile_auth.create_mobile_auth_url(
        hass,
        "flow-id",
        "https://ha.example.test/config/integrations",
    )
    state = parse_qs(urlsplit(url).query)["state"][0]
    payload = {
        "email": "person@example.com",
        "cognito_access_token": "t" * 120,
        "trust_id": "2b5e5df6-164d-47b5-b8ee-bf6d77aa5fe0",
    }
    return state, payload


def _external_done() -> dict:
    return {
        "type": FlowResultType.EXTERNAL_STEP_DONE,
        "handler": "meural",
        "step_id": "mobile_finish",
    }


class MobileAuthHandoffTest(unittest.IsolatedAsyncioTestCase):
    """Exercise retry and duplicate delivery behavior."""

    async def test_duplicate_delivery_is_idempotent(self) -> None:
        hass = FakeHass(_external_done())
        state, payload = _session(hass)
        view = mobile_auth.MeuralMobileAuthView()

        first = await view.post(FakeRequest(hass, state, payload), "flow-id")
        second = await view.post(FakeRequest(hass, state, payload), "flow-id")

        self.assertEqual(200, first.status)
        self.assertEqual(200, second.status)
        self.assertTrue(json.loads(second.text)["already_completed"])
        self.assertFalse(json.loads(second.text)["flow_advanced"])
        self.assertEqual(1, len(hass.config_entries.flow.calls))
        self.assertEqual(1, len(hass.bus.events))

    async def test_failed_handoff_can_be_retried(self) -> None:
        hass = FakeHass(
            RuntimeError("temporary failure"),
            _external_done(),
        )
        state, payload = _session(hass)
        view = mobile_auth.MeuralMobileAuthView()

        first = await view.post(FakeRequest(hass, state, payload), "flow-id")
        second = await view.post(FakeRequest(hass, state, payload), "flow-id")

        self.assertEqual(400, first.status)
        self.assertEqual(200, second.status)
        self.assertEqual(2, len(hass.config_entries.flow.calls))

    async def test_completed_get_wakes_reconnected_frontend(self) -> None:
        hass = FakeHass(_external_done())
        state, payload = _session(hass)
        view = mobile_auth.MeuralMobileAuthView()
        request = FakeRequest(hass, state, payload)
        await view.post(request, "flow-id")
        events_before_get = len(hass.bus.events)

        response = await view.get(request, "flow-id")

        self.assertEqual(200, response.status)
        self.assertIn("notified again", response.text)
        self.assertEqual(events_before_get + 1, len(hass.bus.events))

    async def test_refresh_reports_removed_flow_as_advanced(self) -> None:
        hass = FakeHass(_external_done())
        state, payload = _session(hass)
        view = mobile_auth.MeuralMobileAuthView()
        request = FakeRequest(hass, state, payload)
        await view.post(request, "flow-id")
        hass.config_entries.flow.current = None
        events_before_retry = len(hass.bus.events)

        response = await view.post(request, "flow-id")

        self.assertTrue(json.loads(response.text)["flow_advanced"])
        self.assertEqual(events_before_retry, len(hass.bus.events))

    def test_wrong_flow_id_does_not_remove_session(self) -> None:
        hass = FakeHass(_external_done())
        state, payload = _session(hass)
        request = FakeRequest(hass, state, payload)

        wrong, _ = mobile_auth.MeuralMobileAuthView._get_session(
            request, "wrong-flow-id"
        )
        correct, _ = mobile_auth.MeuralMobileAuthView._get_session(
            request, "flow-id"
        )

        self.assertIsNone(wrong)
        self.assertIsNotNone(correct)

    def test_browser_keeps_result_only_in_open_tab(self) -> None:
        page = mobile_auth._mobile_login_html("nonce", None)

        self.assertIn("Reconnect to your home Wi-Fi or VPN", page)
        self.assertIn("Send to Home Assistant", page)
        self.assertIn("Refresh Home Assistant", page)
        self.assertIn("pendingHandoff", page)
        self.assertIn('result.status !== "ok"', page)
        self.assertNotIn("localStorage", page)
        self.assertNotIn("sessionStorage", page)


if __name__ == "__main__":
    unittest.main()
