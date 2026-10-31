"""Config flow for Meural integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import http
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import netgear_auth
from .const import CONF_DEVICE, CONF_LOCAL_ONLY, DOMAIN
from .mobile_auth import create_mobile_auth_url
from .pymeural import DeviceTurnedOff, build_local_device, reset_auth_backoff

_LOGGER = logging.getLogger(__name__)

CONF_EMAIL = "email"
CONF_VERIFICATION_CODE = "verification_code"
CONF_COGNITO_ACCESS_TOKEN = "cognito_access_token"
CONF_TRUST_ID = "trust_id"

HEADER_FRONTEND_BASE = "HA-Frontend-Base"

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})
CHALLENGE_SCHEMA = vol.Schema({vol.Required(CONF_VERIFICATION_CODE): str})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meural."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state used while completing an OTP challenge."""
        self._authenticator: netgear_auth.NetgearAuthenticator | None = None
        self._pending_challenge: netgear_auth.PendingChallenge | None = None
        self._email: str | None = None
        self._trust_id: str | None = None
        self._mobile_cognito_access_token: str | None = None
        self._mobile_trust_id: str | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Choose a sign-in method for initial account setup."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["mobile_login", "password_login", "local_setup"],
        )

    async def async_step_local_setup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Set up a single Canvas over the LAN without a NETGEAR account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            name = (user_input.get(CONF_NAME) or "").strip() or None
            device = await self._probe_local_device(host, name, errors)
            if device is not None:
                await self.async_set_unique_id(
                    device["productKey"], raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                _LOGGER.info("Meural: Added local-only Canvas at %s", host)
                return self.async_create_entry(
                    title=device["alias"],
                    data={
                        CONF_LOCAL_ONLY: True,
                        CONF_HOST: host,
                        CONF_DEVICE: device,
                    },
                )

        return self.async_show_form(
            step_id="local_setup",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Edit the host/name of an existing local-only Canvas entry."""
        entry = self._get_reconfigure_entry()
        if not entry.data.get(CONF_LOCAL_ONLY):
            # Account-based entries are reconfigured by reauthenticating, not here.
            return self.async_abort(reason="reconfigure_not_supported")
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            name = (user_input.get(CONF_NAME) or "").strip() or None
            device = await self._probe_local_device(host, name, errors)
            if device is not None:
                # Make sure the reconfigured host still points at the same Canvas.
                await self.async_set_unique_id(
                    device["productKey"], raise_on_progress=False
                )
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                _LOGGER.info("Meural: Reconfigured local-only Canvas to %s", host)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_LOCAL_ONLY: True,
                        CONF_HOST: host,
                        CONF_DEVICE: device,
                    },
                )

        current_device = entry.data.get(CONF_DEVICE, {})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, "")
                    ): str,
                    vol.Optional(
                        CONF_NAME, default=current_device.get("alias", "")
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _probe_local_device(
        self,
        host: str,
        name: str | None,
        errors: dict[str, str],
    ) -> dict[str, Any] | None:
        """Probe a Canvas over the LAN, recording a flow error on failure."""
        try:
            return await build_local_device(
                host, name, async_get_clientsession(self.hass)
            )
        except (DeviceTurnedOff, aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.warning("Meural: Could not reach Canvas at %s", host)
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Meural: Unexpected error probing Canvas at %s", host)
            errors["base"] = "unknown"
        return None

    async def async_step_password_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the normal direct NETGEAR password sign-in."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if CONF_EMAIL in user_input:
                self._email = user_input[CONF_EMAIL].strip()
            if self._email is None:
                return self.async_abort(reason="unknown")
            _LOGGER.debug("Meural: Attempting authentication for %s", self._email)
            result = await self._start_authentication(
                self._email,
                user_input[CONF_PASSWORD],
                errors,
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="password_login",
            data_schema=(
                REAUTH_SCHEMA
                if self.source == config_entries.SOURCE_REAUTH
                else DATA_SCHEMA
            ),
            description_placeholders={"email": self._email or ""},
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> FlowResult:
        """Start reauthentication for an expired legacy or Meural session."""
        self._email = entry_data[CONF_EMAIL]
        # Reuse the entry's trust_id so Netgear recognizes this install as
        # already-trusted, typically skipping a fresh OTP/MFA challenge.
        self._trust_id = entry_data.get("trust_id")
        return await self.async_step_reauth_method()

    async def async_step_reauth_method(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Choose a sign-in method for reauthentication."""
        return self.async_show_menu(
            step_id="reauth_method",
            menu_options=["mobile_login", "password_login"],
            description_placeholders={"email": self._email or ""},
        )

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Keep compatibility with reauth flows opened before an upgrade."""
        return await self.async_step_password_login(user_input)

    async def async_step_mobile_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Authenticate Cognito in a phone browser on a different connection."""
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._mobile_cognito_access_token = user_input[CONF_COGNITO_ACCESS_TOKEN]
            self._mobile_trust_id = user_input[CONF_TRUST_ID]
            return self.async_external_step_done(next_step_id="mobile_finish")

        request = http.current_request.get()
        if request is None or not (
            frontend_base := request.headers.get(HEADER_FRONTEND_BASE)
        ):
            return self.async_abort(reason="external_url_unavailable")

        try:
            auth_url = create_mobile_auth_url(
                self.hass,
                self.flow_id,
                frontend_base,
                trust_id=self._trust_id,
            )
        except ValueError:
            return self.async_abort(reason="external_url_unavailable")
        return self.async_external_step(step_id="mobile_login", url=auth_url)

    async def async_step_mobile_finish(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Exchange the browser's short-lived Cognito token for Meural tokens."""
        if (
            self._email is None
            or self._mobile_cognito_access_token is None
            or self._mobile_trust_id is None
        ):
            return self.async_abort(reason="mobile_login_expired")

        errors: dict[str, str] = {}
        try:
            authenticator = netgear_auth.NetgearAuthenticator(
                async_get_clientsession(self.hass),
                trust_id=self._mobile_trust_id,
            )
            result = await authenticator.exchange_cognito_token(
                self._mobile_cognito_access_token
            )
            self._mobile_cognito_access_token = None
            return await self._finish_authentication(result)
        except netgear_auth.AuthenticationBlocked:
            _LOGGER.warning("Meural: Netgear WAF blocked mobile sign-in token exchange")
            errors["base"] = "auth_blocked"
        except netgear_auth.CannotConnect:
            _LOGGER.warning(
                "Meural: Cannot connect to Netgear authentication services "
                "finishing mobile sign-in"
            )
            errors["base"] = "cannot_connect"
        except netgear_auth.InvalidAuth:
            _LOGGER.warning(
                "Meural: Mobile sign-in token was rejected as invalid or expired"
            )
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Meural: Unexpected exception finishing mobile sign-in")
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="mobile_finish",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_challenge(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle an email, SMS, authenticator, or custom Cognito challenge."""
        if self._authenticator is None or self._pending_challenge is None:
            return self.async_abort(reason="challenge_expired")

        errors: dict[str, str] = {}
        if user_input is not None:
            _LOGGER.debug(
                "Meural: Submitting response to challenge %s",
                self._pending_challenge.name,
            )
            try:
                result = await self._authenticator.complete_challenge(
                    self._pending_challenge,
                    user_input[CONF_VERIFICATION_CODE].strip(),
                )
                return await self._finish_authentication(result)
            except netgear_auth.ChallengeRequired as err:
                _LOGGER.info(
                    "Meural: Netgear requires a further challenge (%s)",
                    err.challenge.name,
                )
                self._pending_challenge = err.challenge
            except netgear_auth.InvalidChallenge:
                _LOGGER.warning(
                    "Meural: Verification code rejected as invalid or expired"
                )
                errors["base"] = "invalid_code"
            except netgear_auth.AuthenticationBlocked:
                _LOGGER.warning(
                    "Meural: Netgear WAF blocked authentication during challenge"
                )
                errors["base"] = "auth_blocked"
            except netgear_auth.CannotConnect:
                _LOGGER.warning("Meural: Cannot connect to Netgear authentication services")
                errors["base"] = "cannot_connect"
            except netgear_auth.InvalidAuth:
                _LOGGER.warning(
                    "Meural: Authentication session became invalid during challenge"
                )
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Meural: Unexpected exception completing authentication challenge"
                )
                errors["base"] = "unknown"

        challenge = self._pending_challenge
        return self.async_show_form(
            step_id="challenge",
            data_schema=CHALLENGE_SCHEMA,
            description_placeholders={
                "challenge": challenge.name,
                "destination": self._challenge_destination(challenge),
            },
            errors=errors,
        )

    async def _start_authentication(
        self,
        email: str,
        password: str,
        errors: dict[str, str],
    ) -> FlowResult | None:
        """Start login and route to a challenge form when Netgear requires it."""
        try:
            self._authenticator = netgear_auth.NetgearAuthenticator(
                async_get_clientsession(self.hass),
                self._trust_id,
            )
            result = await self._authenticator.authenticate(email, password)
            return await self._finish_authentication(result)
        except netgear_auth.ChallengeRequired as err:
            _LOGGER.info(
                "Meural: Netgear requires an interactive challenge (%s)",
                err.challenge.name,
            )
            self._pending_challenge = err.challenge
            return await self.async_step_challenge()
        except netgear_auth.AuthenticationBlocked:
            _LOGGER.warning("Meural: Netgear WAF blocked authentication")
            errors["base"] = "auth_blocked"
        except netgear_auth.CannotConnect:
            _LOGGER.warning("Meural: Cannot connect to Netgear authentication services")
            errors["base"] = "cannot_connect"
        except netgear_auth.InvalidAuth:
            _LOGGER.warning("Meural: Invalid credentials for account %s", email)
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Meural: Unexpected exception authenticating account")
            errors["base"] = "unknown"
        return None

    async def _finish_authentication(
        self,
        result: netgear_auth.AuthResult,
    ) -> FlowResult:
        """Create or update the config entry with Meural OAuth tokens."""
        assert self._email is not None
        data = {
            CONF_EMAIL: self._email,
            "token": result.access_token,
            "refresh_token": result.refresh_token,
            "expires_at": result.expires_at,
            "trust_id": result.trust_id,
        }
        # A completed login proves authentication works again, so any backoff
        # recorded under this trust_id from earlier refresh failures must not
        # delay the first token refresh after (re)authentication.
        reset_auth_backoff(result.trust_id)

        await self.async_set_unique_id(self._email, raise_on_progress=False)
        if self.source == config_entries.SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            _LOGGER.info("Meural: Successfully reauthenticated account %s", self._email)
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data=data,
            )

        self._abort_if_unique_id_configured()
        _LOGGER.info("Meural: Successfully authenticated account %s", self._email)
        return self.async_create_entry(title=self._email, data=data)

    @staticmethod
    def _challenge_destination(challenge: netgear_auth.PendingChallenge) -> str:
        """Return a redacted challenge destination supplied by Cognito."""
        for key in (
            "CODE_DELIVERY_DESTINATION",
            "deliveryDestination",
            "email",
            "phone_number",
        ):
            value = challenge.parameters.get(key)
            if value:
                return str(value)
        return "your Netgear account"
