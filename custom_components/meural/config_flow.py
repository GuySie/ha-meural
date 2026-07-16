"""Config flow for Meural integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN  # pylint:disable=unused-import
from . import pymeural

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({"email": str, "password": str})
REAUTH_DATA_SCHEMA = vol.Schema({"password": str})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> tuple[str, str]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    Returns access token and refresh token.
    """
    session = async_get_clientsession(hass)
    return await pymeural.authenticate(session, data["email"], data["password"])

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meural."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            _LOGGER.debug("Attempting authentication for %s", user_input["email"])
            try:
                token, refresh_token = await validate_input(self.hass, user_input)

                await self.async_set_unique_id(user_input["email"], raise_on_progress=False)
                _LOGGER.info("Successfully authenticated Meural account %s", user_input["email"])
                return self.async_create_entry(
                    title=user_input["email"],
                    data={
                        "email": user_input["email"],
                        "password": user_input["password"],
                        "token": token,
                        "refresh_token": refresh_token,
                    },
                )
            except pymeural.CannotConnect:
                _LOGGER.warning("Cannot connect to Meural API for %s", user_input["email"])
                errors["base"] = "cannot_connect"
            except pymeural.InvalidAuth:
                _LOGGER.warning("Invalid credentials for Meural account %s", user_input["email"])
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle reauthentication triggered by an authentication failure."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the reauth confirmation form."""
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None
        email = self._reauth_entry.data["email"]

        if user_input is not None:
            _LOGGER.debug("Attempting reauthentication for %s", email)
            try:
                token, refresh_token = await validate_input(
                    self.hass, {"email": email, "password": user_input["password"]}
                )
                _LOGGER.info("Successfully reauthenticated Meural account %s", email)
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        "password": user_input["password"],
                        "token": token,
                        "refresh_token": refresh_token,
                    },
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")
            except pymeural.CannotConnect:
                _LOGGER.warning("Cannot connect to Meural API for %s", email)
                errors["base"] = "cannot_connect"
            except pymeural.InvalidAuth:
                _LOGGER.warning("Invalid credentials for Meural account %s", email)
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_DATA_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )
