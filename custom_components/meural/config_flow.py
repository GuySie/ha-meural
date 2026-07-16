"""Config flow for Meural integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from . import netgear_auth

_LOGGER = logging.getLogger(__name__)

CONF_EMAIL = "email"
CONF_VERIFICATION_CODE = "verification_code"

DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
CHALLENGE_SCHEMA = vol.Schema({vol.Required(CONF_VERIFICATION_CODE): str})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meural."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state used while completing an OTP challenge."""
        self._authenticator: netgear_auth.NetgearAuthenticator | None = None
        self._pending_challenge: netgear_auth.PendingChallenge | None = None
        self._email: str | None = None
        self._password: str | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle initial account setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            _LOGGER.debug("Attempting Meural authentication for %s", self._email)
            result = await self._start_authentication(
                self._email,
                user_input[CONF_PASSWORD],
                errors,
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> FlowResult:
        """Start reauthentication for an expired legacy or Meural session."""
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Ask for the password only when an interactive login is required."""
        errors: dict[str, str] = {}
        if user_input is not None and self._email is not None:
            result = await self._start_authentication(
                self._email,
                user_input[CONF_PASSWORD],
                errors,
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": self._email or ""},
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
            try:
                result = await self._authenticator.complete_challenge(
                    self._pending_challenge,
                    user_input[CONF_VERIFICATION_CODE].strip(),
                )
                return await self._finish_authentication(result)
            except netgear_auth.ChallengeRequired as err:
                self._pending_challenge = err.challenge
            except netgear_auth.InvalidChallenge:
                errors["base"] = "invalid_code"
            except netgear_auth.AuthenticationBlocked:
                errors["base"] = "auth_blocked"
            except netgear_auth.CannotConnect:
                errors["base"] = "cannot_connect"
            except netgear_auth.InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception completing Meural challenge")
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
        self._password = password
        self._authenticator = netgear_auth.NetgearAuthenticator(
            async_get_clientsession(self.hass)
        )
        try:
            result = await self._authenticator.authenticate(email, password)
            return await self._finish_authentication(result)
        except netgear_auth.ChallengeRequired as err:
            self._pending_challenge = err.challenge
            return await self.async_step_challenge()
        except netgear_auth.AuthenticationBlocked:
            _LOGGER.warning("Netgear WAF blocked Meural authentication")
            errors["base"] = "auth_blocked"
        except netgear_auth.CannotConnect:
            _LOGGER.warning("Cannot connect to Netgear authentication services")
            errors["base"] = "cannot_connect"
        except netgear_auth.InvalidAuth:
            _LOGGER.warning("Invalid credentials for Meural account %s", email)
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception authenticating Meural account")
            errors["base"] = "unknown"
        return None

    async def _finish_authentication(
        self,
        result: netgear_auth.AuthResult,
    ) -> FlowResult:
        """Create or update the config entry with Meural OAuth tokens."""
        assert self._email is not None
        assert self._password is not None
        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            "token": result.access_token,
            "refresh_token": result.refresh_token,
            "expires_at": result.expires_at,
            "trust_id": result.trust_id,
        }

        await self.async_set_unique_id(self._email, raise_on_progress=False)
        if self.source == config_entries.SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data=data,
            )

        self._abort_if_unique_id_configured()
        _LOGGER.info("Successfully authenticated Meural account %s", self._email)
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
