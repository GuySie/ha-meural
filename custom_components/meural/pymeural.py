from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp.client_exceptions import ClientResponseError
from homeassistant.exceptions import HomeAssistantError

from .netgear_auth import (
    AuthenticationBlocked,
    AuthResult,
    CannotConnect,
    InvalidAuth,
    NetgearAuthenticator,
)

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.meural.com/v1/"
LOCAL_READ_ATTEMPTS = 2
LOCAL_RETRY_DELAY = 0.25

# Backoff before retrying a token refresh after a failure, to avoid hammering the
# auth endpoint (e.g. during an upstream WAF/rate-limit block). Doubles on each
# consecutive failure up to the cap, and resets after a success.
AUTH_RETRY_BACKOFF_BASE = 60
AUTH_RETRY_BACKOFF_MAX = 1800


def _auth_backoff_seconds(failure_count: int) -> float:
    """Return the backoff duration for the given number of consecutive auth failures."""
    return min(AUTH_RETRY_BACKOFF_BASE * (2 ** (failure_count - 1)), AUTH_RETRY_BACKOFF_MAX)


# Auth backoff state keyed by trust_id (or, until one is known, by config entry
# ID), kept at module scope (rather than on PyMeural instances) so it survives
# Home Assistant recreating the PyMeural instance on every ConfigEntryNotReady
# setup retry - otherwise a sustained upstream block (e.g. WAF) would keep
# resetting the backoff and get hit on every retry. Resets naturally on a full
# Home Assistant restart. PyMeural no longer carries a username/password (auth
# is centralized in the config flow), so trust_id - persisted across instance
# recreation via the config entry - is the durable key instead. A config entry
# created before trust_id existed won't have one until its first successful
# refresh, so entry_id (always present) is the fallback key for that window -
# never a single shared bucket, which would let one such account's failures
# throttle every other account that also lacks a trust_id.
_AUTH_BACKOFF_STATE: dict[str, dict[str, Any]] = {}


def _get_auth_backoff_state(key: str) -> dict[str, Any]:
    return _AUTH_BACKOFF_STATE.setdefault(
        key, {"last_failure": 0.0, "failure_count": 0, "error_type": CannotConnect}
    )


def reset_auth_backoff(trust_id: str) -> None:
    """Clear auth backoff state after a successful interactive login.

    A completed config/reauth flow proves authentication works again, so any
    backoff recorded from earlier refresh failures under this trust_id must not
    delay the first token refresh after reauthentication.
    """
    if _AUTH_BACKOFF_STATE.pop(trust_id, None) is not None:
        _LOGGER.debug(
            "Meural: Cleared authentication backoff state after interactive login"
        )


async def authenticate(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    trust_id: str | None = None,
) -> AuthResult:
    """Authenticate through Netgear Accounts and return Meural OAuth tokens."""
    _LOGGER.info("Meural: Starting interactive Netgear authentication")
    authenticator = NetgearAuthenticator(session, trust_id)
    return await authenticator.authenticate(username, password)


async def refresh_access_token(
    session: aiohttp.ClientSession,
    refresh_token: str,
    trust_id: str | None = None,
) -> AuthResult:
    """Refresh Meural OAuth tokens through Netgear Accounts."""
    _LOGGER.info("Meural: Refreshing access token through Netgear Accounts")
    authenticator = NetgearAuthenticator(session, trust_id)
    return await authenticator.refresh(refresh_token)


class PyMeural:
    """Client for Meural cloud API."""

    def __init__(
        self,
        token: str | None,
        token_update_callback: Callable[[str, str, float, str], None],
        session: aiohttp.ClientSession,
        entry_id: str,
        refresh_token: str | None = None,
        expires_at: float | None = None,
        trust_id: str | None = None,
    ) -> None:
        """Initialize PyMeural client."""
        self.session = session
        self.token = token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.trust_id = trust_id
        self._entry_id = entry_id
        self.token_update_callback = token_update_callback
        self._auth_lock = asyncio.Lock()

    @property
    def _backoff_key(self) -> str:
        """Return the durable key for this account's auth backoff state.

        Uses trust_id once known; falls back to the config entry ID for
        entries that predate trust_id (before their first successful refresh),
        so those entries don't share a single global backoff bucket.
        """
        return self.trust_id or f"entry:{self._entry_id}"

    async def request(
        self, method: str, path: str, data: dict[str, Any] | None = None, timeout: int = 10
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["params"] = data
            else:
                kwargs["json"] = data
        if self.token and self.expires_at and self.expires_at <= time.time() + 60:
            _LOGGER.debug("Meural: Access token is about to expire, refreshing proactively")
            self.token = None

        for attempt in range(2):
            if self.token is None:
                await self.get_new_token()

            async with asyncio.timeout(timeout):
                try:
                    async with self.session.request(
                        method,
                        url,
                        headers={
                            "Authorization": f"Token {self.token}",
                            "Accept": "application/json",
                            "x-meural-api-version": "4",
                            "x-meural-source-platform": "web",
                        },
                        raise_for_status=True,
                        **kwargs,
                    ) as resp:
                        response = await resp.json(content_type=None)
                    return response.get("data", response)
                except ClientResponseError as err:
                    if err.status != 401:
                        _LOGGER.error("Meural: Cloud request failed: %s", err)
                        raise
                    self.token = None
                    self.expires_at = None
                    if attempt == 1:
                        raise InvalidAuth(
                            "Meural rejected a newly refreshed access token"
                        ) from err
                    _LOGGER.info(
                        "Meural: Cloud request returned 401; refreshing session once"
                    )
                except Exception as err:
                    _LOGGER.error("Meural: Cloud request failed: %s", err)
                    raise

        raise InvalidAuth("Unable to obtain a valid Meural access token")

    async def get_new_token(self) -> None:
        """Fetch and store a new authentication token."""
        async with self._auth_lock:
            # Check if another concurrent request already refreshed the token
            if self.token is not None:
                return

            if not self.refresh_token:
                raise InvalidAuth("No Meural refresh token is available")

            # Back off after a recent failure instead of hammering the auth endpoint
            # on every subsequent request (e.g. while an upstream WAF/rate-limit
            # block is in effect). Backoff doubles with each consecutive failure.
            backoff_state = _get_auth_backoff_state(self._backoff_key)
            if backoff_state["last_failure"]:
                backoff = _auth_backoff_seconds(backoff_state["failure_count"])
                time_since_failure = time.monotonic() - backoff_state["last_failure"]
                if time_since_failure < backoff:
                    remaining = backoff - time_since_failure
                    _LOGGER.debug(
                        "Meural: Backing off authentication for %.0fs after %d consecutive failure(s)",
                        remaining,
                        backoff_state["failure_count"],
                    )
                    # Re-raise the same error type as the failure that caused this
                    # backoff, so a WAF/network block still reports as CannotConnect
                    # or AuthenticationBlocked rather than being misreported as bad
                    # credentials.
                    raise backoff_state["error_type"](
                        f"Skipping authentication attempt, retrying in {remaining:.0f}s"
                    )

            try:
                result = await refresh_access_token(
                    self.session,
                    self.refresh_token,
                    self.trust_id,
                )
            except (InvalidAuth, CannotConnect, AuthenticationBlocked) as err:
                backoff_state["last_failure"] = time.monotonic()
                backoff_state["failure_count"] += 1
                backoff_state["error_type"] = type(err)
                _LOGGER.warning(
                    "Meural: Authentication failed (%s: %s), backing off for %.0fs before retrying",
                    type(err).__name__,
                    err,
                    _auth_backoff_seconds(backoff_state["failure_count"]),
                )
                raise

            if backoff_state["failure_count"]:
                _LOGGER.info(
                    "Meural: Authentication recovered after %d failed attempt(s)",
                    backoff_state["failure_count"],
                )
            backoff_state["last_failure"] = 0.0
            backoff_state["failure_count"] = 0

            self.token = result.access_token
            self.refresh_token = result.refresh_token
            self.expires_at = result.expires_at
            self.trust_id = result.trust_id
            self.token_update_callback(
                result.access_token,
                result.refresh_token,
                result.expires_at,
                result.trust_id,
            )

    async def get_user(self) -> dict[str, Any]:
        """Get user information."""
        return await self.request("get", "user")

    async def get_user_items(self) -> list[dict[str, Any]]:
        """Get user items."""
        return await self.request("get", "user/items", {"count": 1000})

    async def get_user_galleries(self) -> list[dict[str, Any]]:
        """Get user galleries."""
        return await self.request("get", "user/galleries", {"count": 1000})

    async def get_user_devices(self) -> list[dict[str, Any]]:
        """Get user devices."""
        return await self.request("get", "user/devices", {"count": 1000})

    async def get_user_feedback(self) -> dict[str, Any]:
        """Get user feedback."""
        return await self.request("get", "user/feedback")

    async def device_load_gallery(
        self, device_id: str | int, gallery_id: str | int
    ) -> dict[str, Any]:
        """Load a gallery on a device."""
        return await self.request("post", f"devices/{device_id}/galleries/{gallery_id}", timeout=60)

    async def delete_device_gallery(self, device_id: str | int, gallery_id: str | int) -> dict[str, Any]:
        """Remove a gallery from a device."""
        return await self.request("delete", f"devices/{device_id}/galleries/{gallery_id}")

    async def device_load_item(
        self, device_id: str | int, item_id: str | int
    ) -> dict[str, Any]:
        """Load an item on a device."""
        return await self.request("post", f"devices/{device_id}/items/{item_id}")

    async def get_device(self, device_id: str | int) -> dict[str, Any]:
        """Get device information."""
        return await self.request("get", f"devices/{device_id}")

    async def get_device_galleries(self, device_id: str | int) -> list[dict[str, Any]]:
        """Get device galleries."""
        return await self.request(
            "get", f"devices/{device_id}/galleries", {"count": 1000}
        )

    async def update_device(
        self, device_id: str | int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update device settings."""
        return await self.request("put", f"devices/{device_id}", data)

    async def sync_device(self, device_id: str | int) -> dict[str, Any]:
        """Synchronize device with Meural server."""
        return await self.request("post", f"devices/{device_id}/sync")

    async def get_item(self, item_id: str | int) -> dict[str, Any]:
        """Get item information."""
        return await self.request("get", f"items/{item_id}")


class LocalMeural:
    """Client for Meural local device API."""

    def __init__(self, device: dict[str, Any], session: aiohttp.ClientSession) -> None:
        """Initialize LocalMeural client."""
        self.ip: str = device["localIp"]
        self.device = device
        self.session = session

    def update_device(self, device: dict[str, Any]) -> bool:
        """Update cloud device data and return whether its local IP changed."""
        previous_ip = self.ip
        self.device = device
        if new_ip := device.get("localIp"):
            self.ip = str(new_ip)
        return self.ip != previous_ip

    async def request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a request to the Canvas, retrying safe reads once."""
        url = f"http://{self.ip}/remote/{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["params"] = data
            else:
                kwargs["data"] = data

        # The embedded Canvas web server occasionally resets keep-alive
        # connections. Avoid reusing them and retry read-only endpoints once.
        is_safe_read = method.lower() == "get" and path.startswith(
            ("control_check/", "get_", "identify/")
        )
        attempts = LOCAL_READ_ATTEMPTS if is_safe_read else 1

        for attempt in range(attempts):
            # Safe reads get a retry, so their first attempt can cheaply reuse the
            # pooled keep-alive connection: if the Canvas has since reset it, that
            # attempt fails fast and the retry below asks for a fresh connection
            # instead of hitting the same reset one. Writes get only one attempt,
            # so they always request a fresh connection up front rather than risk
            # silently failing against a connection the Canvas has since reset.
            headers = {} if (is_safe_read and attempt == 0) else {"Connection": "close"}
            try:
                async with asyncio.timeout(10):
                    async with self.session.request(
                        method,
                        url,
                        headers=headers,
                        raise_for_status=True,
                        **kwargs,
                    ) as resp:
                        response = await resp.json(content_type=None)
                        return response["response"]
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as err:
                if attempt + 1 < attempts:
                    _LOGGER.debug(
                        "Meural device %s: Local read %s was interrupted; retrying once",
                        self.device.get("alias", self.ip),
                        path,
                    )
                    await asyncio.sleep(LOCAL_RETRY_DELAY)
                    continue
                if isinstance(err, aiohttp.ClientConnectorError):
                    raise DeviceTurnedOff(
                        f"Cannot connect to Canvas at {self.ip}: {err}"
                    ) from err
                raise

        raise RuntimeError("Local Meural request exhausted all attempts")

    async def send_key_right(self) -> dict[str, Any]:
        """Send key right command."""
        return await self.request("get", "control_command/set_key/right/")

    async def send_key_left(self) -> dict[str, Any]:
        """Send key left command."""
        return await self.request("get", "control_command/set_key/left/")

    async def send_key_up(self) -> dict[str, Any]:
        """Send key up command."""
        return await self.request("get", "control_command/set_key/up/")

    async def send_key_down(self) -> dict[str, Any]:
        """Send key down command."""
        return await self.request("get", "control_command/set_key/down/")

    async def send_key_suspend(self) -> dict[str, Any]:
        """Send suspend command."""
        return await self.request("get", "control_command/suspend")

    async def send_key_resume(self) -> dict[str, Any]:
        """Send resume command."""
        return await self.request("get", "control_command/resume")

    async def send_control_backlight(self, brightness: int) -> dict[str, Any]:
        """Set backlight brightness."""
        return await self.request("get", f"control_command/set_backlight/{brightness}/")

    async def send_als_calibrate_off(self) -> dict[str, Any]:
        """Turn off ambient light sensor calibration."""
        return await self.request("get", "control_command/als_calibrate/off/")

    async def send_set_portrait(self) -> dict[str, Any]:
        """Set orientation to portrait."""
        return await self.request("get", "control_command/set_orientation/portrait")

    async def send_set_landscape(self) -> dict[str, Any]:
        """Set orientation to landscape."""
        return await self.request("get", "control_command/set_orientation/landscape")

    async def send_change_gallery(self, gallery_id: str | int) -> dict[str, Any]:
        """Change to a different gallery."""
        return await self.request("get", f"control_command/change_gallery/{gallery_id}")

    async def send_change_item(self, item_id: str | int) -> dict[str, Any]:
        """Change to a different item."""
        return await self.request("get", f"control_command/change_item/{item_id}")

    async def send_get_backlight(self) -> dict[str, Any]:
        """Get backlight status."""
        return await self.request("get", "get_backlight/")

    async def send_get_sleep(self) -> bool:
        """Get sleep status."""
        return await self.request("get", "control_check/sleep/")

    async def send_get_system(self) -> dict[str, Any]:
        """Get system information."""
        return await self.request("get", "control_check/system/")

    async def send_identify(self) -> dict[str, Any]:
        """Identify the device."""
        return await self.request("get", "identify/")

    async def send_get_wifi_connections(self) -> dict[str, Any]:
        """Get WiFi connections."""
        return await self.request("get", "get_wifi_connections_json/")

    async def send_get_galleries(self) -> list[dict[str, Any]]:
        """Get galleries on the device."""
        return await self.request("get", "get_galleries_json/")

    async def send_get_gallery_status(self) -> dict[str, Any]:
        """Get current gallery status."""
        return await self.request("get", "get_gallery_status_json/")

    async def send_get_items_by_gallery(
        self, gallery_id: str | int
    ) -> list[dict[str, Any]]:
        """Get items in a gallery."""
        return await self.request(
            "get", f"get_frame_items_by_gallery_json/{gallery_id}"
        )

    async def send_postcard(
        self, url: str, content_type: str
    ) -> aiohttp.ClientResponse:
        # photo uploads are done doing a multipart/form-data form
        # with key 'photo' and value being the image data

        # FIXME: meural accepts image/jpeg but not image/jpg
        if content_type == "image/jpg":
            content_type = "image/jpeg"

        _LOGGER.info(
            "Meural device %s: Sending postcard. URL is %s",
            self.device["alias"],
            url,
        )
        async with asyncio.timeout(10):
            response = await self.session.get(url)
            image = await response.read()
        _LOGGER.info(
            "Meural device %s: Sending postcard. Downloaded %d bytes of image",
            self.device["alias"],
            len(image),
        )

        data = aiohttp.FormData()
        data.add_field("photo", image, content_type=content_type)
        response = await self.session.post(
            f"http://{self.ip}/remote/postcard", data=data
        )
        _LOGGER.info(
            "Meural device %s: Sending postcard. Response: %s",
            self.device["alias"],
            response,
        )
        text = await response.text()

        r = json.loads(text)
        _LOGGER.info(
            "Meural device %s: Sending postcard. Image uploaded, status: %s, response: %s",
            self.device["alias"],
            r["status"],
            r["response"],
        )
        if r["status"] != "pass":
            _LOGGER.error(
                "Meural device %s: Sending postcard. Could not upload, response: %s",
                self.device["alias"],
                r["response"],
            )

        return response


class DeviceTurnedOff(HomeAssistantError):
    """Error to indicate device turned off or not connected to the network."""
