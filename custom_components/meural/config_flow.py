"""Config flow for Meural integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN  # pylint:disable=unused-import
from . import pymeural

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({"email": str, "password": str})


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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, refresh_token = await validate_input(self.hass, user_input)

                await self.async_set_unique_id(user_input["email"], raise_on_progress=False)
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
                errors["base"] = "cannot_connect"
            except pymeural.InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
