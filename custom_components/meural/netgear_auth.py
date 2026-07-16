"""Netgear Accounts authentication for the Meural cloud API."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

COGNITO_REGION = "eu-west-1"
COGNITO_CLIENT_ID = "487bd4kvb1fnop6mbgk8gu5ibf"
COGNITO_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
MEURAL_OAUTH_CLIENT_ID = "3ui6nklcaqoij8inrkm06gfk4s"
NETGEAR_ACCOUNTS_URL = "https://accounts2.netgear.com"

COGNITO_HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
}


class MeuralAuthError(Exception):
    """Base exception for Meural authentication failures."""


class CannotConnect(MeuralAuthError):
    """The Netgear authentication service could not be reached."""


class InvalidAuth(MeuralAuthError):
    """The Netgear or Meural credentials/session are invalid."""


class InvalidChallenge(InvalidAuth):
    """The supplied authentication challenge response is invalid or expired."""


class AuthenticationBlocked(MeuralAuthError):
    """AWS WAF blocked the Cognito authentication request."""


@dataclass(frozen=True)
class PendingChallenge:
    """A Cognito challenge that needs an interactive response."""

    username: str
    session: str
    name: str
    parameters: dict[str, Any]
    attempt: int
    trust_id: str


class ChallengeRequired(MeuralAuthError):
    """Cognito needs a one-time code or other interactive answer."""

    def __init__(self, challenge: PendingChallenge) -> None:
        super().__init__(f"Cognito challenge {challenge.name} requires a response")
        self.challenge = challenge


@dataclass(frozen=True)
class AuthResult:
    """Meural OAuth tokens returned by Netgear Accounts."""

    access_token: str
    refresh_token: str
    expires_at: float
    trust_id: str
    id_token: str | None = None


class _HttpError(Exception):
    """Internal HTTP error with a parsed response body."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP request failed with status {status}")
        self.status = status
        self.body = body


class NetgearAuthenticator:
    """Authenticate against Cognito and exchange for Meural OAuth tokens."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        trust_id: str | None = None,
    ) -> None:
        self.session = session
        self.trust_id = trust_id or str(uuid.uuid4())

    async def authenticate(self, username: str, password: str) -> AuthResult:
        """Start the current Netgear login flow."""
        try:
            response = await self._initiate_auth(
                "CUSTOM_AUTH",
                {"USERNAME": username},
            )
        except _HttpError as err:
            if not self._is_user_migration_error(err):
                self._raise_auth_error(err)
            _LOGGER.debug(
                "Meural: Account requires Cognito password-auth migration fallback"
            )
            try:
                response = await self._initiate_auth(
                    "USER_PASSWORD_AUTH",
                    {"USERNAME": username, "PASSWORD": password},
                )
            except _HttpError as fallback_err:
                self._raise_auth_error(fallback_err)

        return await self._finish_cognito_auth(
            response,
            username=username,
            attempt=0,
            password=password,
        )

    async def complete_challenge(
        self,
        challenge: PendingChallenge,
        answer: str,
    ) -> AuthResult:
        """Complete a pending Cognito OTP/MFA/custom challenge."""
        if challenge.trust_id != self.trust_id:
            raise InvalidChallenge("Authentication session identity changed")

        try:
            response = await self._respond_to_challenge(challenge, answer)
        except _HttpError as err:
            if self._is_waf_error(err):
                raise AuthenticationBlocked(
                    "Netgear blocked the authentication request"
                ) from err
            raise InvalidChallenge(
                "The verification code is invalid or expired"
            ) from err

        return await self._finish_cognito_auth(
            response,
            username=challenge.username,
            attempt=challenge.attempt,
        )

    async def refresh(self, refresh_token: str) -> AuthResult:
        """Refresh Meural tokens through Netgear Accounts."""
        try:
            response = await self._request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/getAccessToken",
                headers={
                    "Authorization": f"Bearer {refresh_token}",
                    "appkey": MEURAL_OAUTH_CLIENT_ID,
                    "Accept": "application/json",
                },
            )
        except _HttpError as err:
            if self._is_waf_error(err):
                raise AuthenticationBlocked(
                    "Netgear blocked the token refresh request"
                ) from err
            if err.status == 429 or err.status >= 500:
                raise CannotConnect(
                    "Netgear temporarily rejected the token refresh"
                ) from err
            raise InvalidAuth("The Meural session has expired") from err

        return self._parse_meural_tokens(response, refresh_token)

    async def _finish_cognito_auth(
        self,
        response: dict[str, Any],
        *,
        username: str,
        attempt: int,
        password: str | None = None,
    ) -> AuthResult:
        """Answer password challenges and surface interactive challenges."""
        while "AuthenticationResult" not in response:
            attempt += 1
            challenge_name = response.get("ChallengeName")
            challenge_session = response.get("Session")
            parameters = response.get("ChallengeParameters") or {}

            if not challenge_name or not challenge_session:
                raise InvalidAuth(
                    "Cognito returned neither tokens nor a supported challenge"
                )

            pending = PendingChallenge(
                username=username,
                session=challenge_session,
                name=challenge_name,
                parameters=parameters,
                attempt=attempt,
                trust_id=self.trust_id,
            )

            if password and self._password_answers_challenge(pending):
                try:
                    response = await self._respond_to_challenge(pending, password)
                except _HttpError as err:
                    self._raise_auth_error(err)
                continue

            raise ChallengeRequired(pending)

        auth_result = response["AuthenticationResult"]
        cognito_access_token = auth_result.get("AccessToken")
        if not cognito_access_token:
            raise InvalidAuth("Cognito did not return an access token")

        return await self._exchange_cognito_token(cognito_access_token)

    async def _exchange_cognito_token(self, cognito_access_token: str) -> AuthResult:
        """Exchange a Cognito token for Meural access and refresh tokens."""
        authorize_query = urlencode({"client_id": MEURAL_OAUTH_CLIENT_ID})
        try:
            authorize_response = await self._request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/oauth/authorize?{authorize_query}",
                headers={
                    "Authorization": f"Bearer {cognito_access_token}",
                    "Accept": "application/json",
                },
            )
            authorize_body = self._unwrap(authorize_response)
            code = authorize_body.get("code") or authorize_body.get("authorizationCode")
            if not code:
                raise InvalidAuth(
                    "Netgear authorization did not return an authorization code"
                )

            token_query = urlencode({"code": str(code)})
            token_response = await self._request_json(
                "GET",
                f"{NETGEAR_ACCOUNTS_URL}/api/oauth/token?{token_query}",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        except _HttpError as err:
            if self._is_waf_error(err):
                raise AuthenticationBlocked(
                    "Netgear blocked the token exchange request"
                ) from err
            if err.status == 429 or err.status >= 500:
                raise CannotConnect(
                    "Netgear Accounts is temporarily unavailable"
                ) from err
            raise InvalidAuth("Netgear rejected the Meural token exchange") from err

        return self._parse_meural_tokens(token_response)

    async def _initiate_auth(
        self,
        auth_flow: str,
        auth_parameters: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "AuthFlow": auth_flow,
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": auth_parameters,
            "ClientMetadata": self._client_metadata(),
        }
        return await self._request_json(
            "POST",
            COGNITO_URL,
            headers=COGNITO_HEADERS,
            json_data=payload,
        )

    async def _respond_to_challenge(
        self,
        challenge: PendingChallenge,
        answer: str,
    ) -> dict[str, Any]:
        payload = {
            "ChallengeName": challenge.name,
            "ClientId": COGNITO_CLIENT_ID,
            "Session": challenge.session,
            "ChallengeResponses": {
                "USERNAME": challenge.username,
                self._response_key(challenge.name): answer,
            },
            "ClientMetadata": self._client_metadata(),
        }
        return await self._request_json(
            "POST",
            COGNITO_URL,
            headers={
                **COGNITO_HEADERS,
                "X-Amz-Target": (
                    "AWSCognitoIdentityProviderService.RespondToAuthChallenge"
                ),
            },
            json_data=payload,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(15):
                async with self.session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_data,
                ) as response:
                    try:
                        body = await response.json(content_type=None)
                    except ValueError:
                        body = {"message": await response.text()}

                    if 200 <= response.status < 300:
                        if isinstance(body, dict):
                            return body
                        raise CannotConnect(
                            "Netgear returned an unexpected authentication response"
                        )
                    raise _HttpError(response.status, body)
        except _HttpError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect("Could not connect to Netgear Accounts") from err

    def _parse_meural_tokens(
        self,
        response: dict[str, Any],
        existing_refresh_token: str | None = None,
    ) -> AuthResult:
        body = self._unwrap(response)
        access_token = self._pick(body, "access_token", "accessToken", "token")
        refresh_token = self._pick(body, "refresh_token", "refreshToken")
        refresh_token = refresh_token or existing_refresh_token

        if not access_token or not refresh_token:
            raise InvalidAuth("Netgear returned an incomplete Meural session")

        expires_in = self._pick(body, "expires_in", "expiresIn") or 3600
        try:
            fallback_expiry = time.time() + float(expires_in)
        except (TypeError, ValueError):
            fallback_expiry = time.time() + 3600

        return AuthResult(
            access_token=str(access_token),
            refresh_token=str(refresh_token),
            id_token=self._pick(body, "id_token", "idToken"),
            expires_at=self._jwt_expiration(str(access_token)) or fallback_expiry,
            trust_id=self.trust_id,
        )

    def _raise_auth_error(self, err: _HttpError) -> None:
        if self._is_waf_error(err):
            raise AuthenticationBlocked(
                "Netgear blocked the authentication request"
            ) from err
        if err.status == 429 or err.status >= 500:
            raise CannotConnect(
                "Netgear authentication is temporarily unavailable"
            ) from err
        raise InvalidAuth("Netgear rejected the account credentials") from err

    def _client_metadata(self) -> dict[str, str]:
        return {
            "trustID": self.trust_id,
            "sourceEvent": "login",
            "language": "en-US",
            "appType": "meural",
        }

    @staticmethod
    def _password_answers_challenge(challenge: PendingChallenge) -> bool:
        if challenge.name != "CUSTOM_CHALLENGE" or challenge.attempt != 1:
            return False
        description = json.dumps(challenge.parameters).lower()
        return not any(
            keyword in description
            for keyword in ("otp", "verification", "email", "phone", "code")
        )

    @staticmethod
    def _response_key(challenge_name: str) -> str:
        return {
            "EMAIL_MFA": "EMAIL_MFA_CODE",
            "EMAIL_OTP": "EMAIL_OTP_CODE",
            "SMS_MFA": "SMS_MFA_CODE",
            "SMS_OTP": "SMS_OTP_CODE",
            "SOFTWARE_TOKEN_MFA": "SOFTWARE_TOKEN_MFA_CODE",
            "NEW_PASSWORD_REQUIRED": "NEW_PASSWORD",
        }.get(challenge_name, "ANSWER")

    @staticmethod
    def _is_user_migration_error(err: _HttpError) -> bool:
        return "user_not_found" in json.dumps(err.body).lower()

    @staticmethod
    def _is_waf_error(err: _HttpError) -> bool:
        description = json.dumps(err.body).lower()
        return "forbiddenexception" in description or "waf block" in description

    @staticmethod
    def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
        body = response.get("data", response)
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _pick(body: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = body.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _jwt_expiration(token: str) -> float | None:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        try:
            encoded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded))
            return float(payload["exp"]) if payload.get("exp") else None
        except (binascii.Error, KeyError, TypeError, ValueError):
            return None
