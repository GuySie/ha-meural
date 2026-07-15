from __future__ import annotations

import asyncio
import logging
import json
import time
from typing import Any, Callable, NoReturn

import aiohttp
import async_timeout
import boto3
from botocore.exceptions import ClientError as BotoClientError

from aiohttp.client_exceptions import ClientResponseError

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.meural.com/v0/"

AUTH_CLIENT_NAME = "cognito-idp"
AUTH_CLIENT_REGION = "eu-west-1"
AUTH_CLIENT_CLIENTID = "487bd4kvb1fnop6mbgk8gu5ibf"

# Cognito error codes that mean the credentials themselves were rejected.
# Anything else (WAF blocks, throttling, network errors) is a connectivity
# problem, not proof the username/password is wrong.
COGNITO_INVALID_CREDENTIAL_CODES = {"NotAuthorizedException", "UserNotFoundException"}


def _raise_for_auth_error(err: Exception) -> NoReturn:
    """Classify an auth-request exception as InvalidAuth or CannotConnect."""
    if isinstance(err, BotoClientError):
        code = err.response.get("Error", {}).get("Code", "")
        if code in COGNITO_INVALID_CREDENTIAL_CODES:
            raise InvalidAuth from err
    raise CannotConnect from err

# Backoff before retrying full authentication after a failure, to avoid hammering
# the auth endpoint (e.g. during an upstream WAF/rate-limit block). Doubles on each
# consecutive failure up to the cap, and resets after a success.
AUTH_RETRY_BACKOFF_BASE = 60
AUTH_RETRY_BACKOFF_MAX = 1800


def _auth_backoff_seconds(failure_count: int) -> float:
    """Return the backoff duration for the given number of consecutive auth failures."""
    return min(AUTH_RETRY_BACKOFF_BASE * (2 ** (failure_count - 1)), AUTH_RETRY_BACKOFF_MAX)


# Auth backoff state keyed by account email, kept at module scope (rather than on
# PyMeural instances) so it survives Home Assistant recreating the PyMeural instance
# on every ConfigEntryNotReady setup retry - otherwise a sustained upstream block
# (e.g. WAF) would keep resetting the backoff and get hit on every retry. Resets
# naturally on a full Home Assistant restart.
_AUTH_BACKOFF_STATE: dict[str, dict[str, Any]] = {}


def _get_auth_backoff_state(username: str) -> dict[str, Any]:
    return _AUTH_BACKOFF_STATE.setdefault(
        username, {"last_failure": 0.0, "failure_count": 0, "error_type": CannotConnect}
    )


async def authenticate(
    session: aiohttp.ClientSession, username: str, password: str
) -> tuple[str, str]:
    """Authenticate and return access token and refresh token."""
    _LOGGER.info('Meural: Authenticating with username and password')

    def initiate_auth():
        client = boto3.client(AUTH_CLIENT_NAME, region_name=AUTH_CLIENT_REGION)
        return client.initiate_auth(
            ClientId=AUTH_CLIENT_CLIENTID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )

    try:
        response = await asyncio.to_thread(initiate_auth)
    except Exception as err:
        _LOGGER.warning("Meural: Authentication request failed: %s", err)
        _raise_for_auth_error(err)

    if "AuthenticationResult" in response:
        auth_result = response["AuthenticationResult"]
        access_token = auth_result["AccessToken"]
        refresh_token = auth_result.get("RefreshToken", "")
        _LOGGER.debug("Meural: Authentication successful, tokens received")
        return access_token, refresh_token

    _LOGGER.warning(
        "Meural: Authentication response missing expected result (challenge: %s)",
        response.get("ChallengeName", "unknown"),
    )
    raise InvalidAuth


async def refresh_access_token(
    session: aiohttp.ClientSession, refresh_token: str
) -> str:
    """Refresh access token using refresh token."""
    _LOGGER.info('Meural: Refreshing access token using refresh token')

    def initiate_auth_refresh():
        client = boto3.client(AUTH_CLIENT_NAME, region_name=AUTH_CLIENT_REGION)
        return client.initiate_auth(
            ClientId=AUTH_CLIENT_CLIENTID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )

    try:
        response = await asyncio.to_thread(initiate_auth_refresh)
    except Exception as err:
        _LOGGER.warning("Meural: Failed to refresh token: %s", err)
        _raise_for_auth_error(err)

    if "AuthenticationResult" in response:
        access_token = response["AuthenticationResult"]["AccessToken"]
        _LOGGER.debug("Meural: Access token refreshed successfully")
        return access_token

    _LOGGER.warning(
        "Meural: Refresh response missing expected result (challenge: %s)",
        response.get("ChallengeName", "unknown"),
    )
    raise InvalidAuth

class PyMeural:
    """Client for Meural cloud API."""

    def __init__(
        self,
        username: str,
        password: str,
        token: str | None,
        token_update_callback: Callable[[str, str], None],
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
    ) -> None:
        """Initialize PyMeural client."""
        self.username = username
        self.password = password
        self.session = session
        self.token = token
        self.refresh_token = refresh_token
        self.token_update_callback = token_update_callback
        self._auth_lock = asyncio.Lock()

    async def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        fetched_new_token = self.token is None
        if self.token is None:
            await self.get_new_token()
        url = f"{BASE_URL}{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["params"] = data
            else:
                kwargs["json"] = data
        with async_timeout.timeout(10):
            try:
                resp = await self.session.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Token {self.token}",
                        "x-meural-api-version": "3",
                    },
                    raise_for_status=True,
                    **kwargs,
                )
            except ClientResponseError as err:
                if err.status != 401:
                    _LOGGER.error(
                        "Meural: Sending request to %s failed with status %s: %s",
                        path, err.status, err.message,
                    )
                    raise
                # If a new token was just fetched and it fails again, just raise
                if fetched_new_token:
                    _LOGGER.error(
                        "Meural: Sending request to %s failed. Freshly fetched token was rejected (401): %s",
                        path, err.message,
                    )
                    raise
                _LOGGER.info('Meural: Sending Request failed. Re-Authenticating')
                self.token = None
                return await self.request(method, path, data)
            except Exception as err:
                _LOGGER.error('Meural: Sending Request failed. Raising: %s', err)
                raise
        response = await resp.json()
        return response["data"]

    async def get_new_token(self) -> None:
        """Fetch and store a new authentication token."""
        async with self._auth_lock:
            # Check if another concurrent request already refreshed the token
            if self.token is not None:
                return

            # Back off after a recent failure instead of hammering the auth endpoint
            # on every subsequent request (e.g. while an upstream WAF/rate-limit block
            # is in effect). Backoff doubles with each consecutive failure. Keyed by
            # account in module-level state, since Home Assistant recreates this
            # PyMeural instance on every ConfigEntryNotReady setup retry.
            backoff_state = _get_auth_backoff_state(self.username)
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
                    # rather than being misreported as bad credentials.
                    raise backoff_state["error_type"](
                        f"Skipping authentication attempt, retrying in {remaining:.0f}s"
                    )

            try:
                # Try to refresh using refresh token first
                if self.refresh_token:
                    try:
                        _LOGGER.debug("Meural: Attempting to refresh access token")
                        self.token = await refresh_access_token(self.session, self.refresh_token)
                        # Update only access token, keep existing refresh token
                        self.token_update_callback(self.token, self.refresh_token)
                        return
                    except InvalidAuth:
                        _LOGGER.info("Meural: Refresh token invalid, performing full authentication")
                        # Refresh token is invalid, fall through to full authentication

                # Full authentication with username and password
                _LOGGER.info("Meural: Performing full authentication")
                self.token, self.refresh_token = await authenticate(
                    self.session, self.username, self.password
                )
                self.token_update_callback(self.token, self.refresh_token)
            except (InvalidAuth, CannotConnect) as err:
                backoff_state["last_failure"] = time.monotonic()
                backoff_state["failure_count"] += 1
                backoff_state["error_type"] = type(err)
                _LOGGER.warning(
                    "Meural: Authentication failed (%s: %s), backing off for %.0fs before retrying",
                    type(err).__name__,
                    err.__cause__ or err,
                    _auth_backoff_seconds(backoff_state["failure_count"]),
                )
                raise
            else:
                if backoff_state["failure_count"]:
                    _LOGGER.info(
                        "Meural: Authentication recovered after %d failed attempt(s)",
                        backoff_state["failure_count"],
                    )
                backoff_state["last_failure"] = 0.0
                backoff_state["failure_count"] = 0

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

    async def device_load_gallery(self, device_id: str | int, gallery_id: str | int) -> dict[str, Any]:
        """Load a gallery on a device."""
        return await self.request("post", f"devices/{device_id}/galleries/{gallery_id}")

    async def device_load_item(self, device_id: str | int, item_id: str | int) -> dict[str, Any]:
        """Load an item on a device."""
        return await self.request("post", f"devices/{device_id}/items/{item_id}")

    async def get_device(self, device_id: str | int) -> dict[str, Any]:
        """Get device information."""
        return await self.request("get", f"devices/{device_id}")

    async def get_device_galleries(self, device_id: str | int) -> list[dict[str, Any]]:
        """Get device galleries."""
        return await self.request("get", f"devices/{device_id}/galleries", {"count": 1000})

    async def update_device(self, device_id: str | int, data: dict[str, Any]) -> dict[str, Any]:
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

    async def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"http://{self.ip}/remote/{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["params"] = data
            else:
                kwargs["data"] = data
        try:
            with async_timeout.timeout(10):
                resp = await self.session.request(
                    method,
                    url,
                    raise_for_status=True,
                    **kwargs,
                )
            response = await resp.json(content_type=None)
            return response["response"]
        except aiohttp.client_exceptions.ClientConnectorError:
            raise DeviceTurnedOff

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

    async def send_get_items_by_gallery(self, gallery_id: str | int) -> list[dict[str, Any]]:
        """Get items in a gallery."""
        return await self.request("get", f"get_frame_items_by_gallery_json/{gallery_id}")

    async def send_postcard(self, url: str, content_type: str) -> aiohttp.ClientResponse:
        # photo uploads are done doing a multipart/form-data form
        # with key 'photo' and value being the image data

        # FIXME: meural accepts image/jpeg but not image/jpg
        if content_type == 'image/jpg':
            content_type = 'image/jpeg'

        _LOGGER.info(
            "Meural device %s: Sending postcard. URL is %s",
            self.device['alias'],
            url,
        )
        with async_timeout.timeout(10):
            response = await self.session.get(url)
            image = await response.read()
        _LOGGER.info(
            "Meural device %s: Sending postcard. Downloaded %d bytes of image",
            self.device['alias'],
            len(image),
        )

        data = aiohttp.FormData()
        data.add_field('photo', image, content_type=content_type)
        response = await self.session.post(f"http://{self.ip}/remote/postcard", data=data)
        _LOGGER.info(
            "Meural device %s: Sending postcard. Response: %s",
            self.device['alias'],
            response,
        )
        text = await response.text()

        r = json.loads(text)
        _LOGGER.info(
            "Meural device %s: Sending postcard. Image uploaded, status: %s, response: %s",
            self.device['alias'],
            r['status'],
            r['response'],
        )
        if r['status'] != 'pass':
            _LOGGER.error(
                "Meural device %s: Sending postcard. Could not upload, response: %s",
                self.device['alias'],
                r['response'],
            )

        return response

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

class DeviceTurnedOff(HomeAssistantError):
    """Error to indicate device turned off or not connected to the network."""
