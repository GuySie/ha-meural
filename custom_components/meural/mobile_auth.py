"""One-time browser-assisted authentication for NETGEAR Accounts."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import (
    EVENT_DATA_ENTRY_FLOW_PROGRESSED,
    FlowResultType,
    UnknownFlow,
)

from .const import DOMAIN
from .netgear_auth import (
    COGNITO_CLIENT_ID,
    COGNITO_URL,
    PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS,
    RESPONSE_KEY_MAP,
    USER_MIGRATION_KEYWORDS,
    WAF_BLOCK_PAGE_KEYWORDS,
    WAF_ERROR_KEYWORDS,
)

_LOGGER = logging.getLogger(__name__)

AUTH_CALLBACK_PATH = "/api/meural/mobile-auth/{flow_id}"
AUTH_CALLBACK_NAME = "api:meural:mobile-auth"
MOBILE_AUTH_TIMEOUT = 10 * 60

_DATA_SESSIONS = "_mobile_auth_sessions"
_DATA_COMPLETED_SESSIONS = "_mobile_auth_completed_sessions"
_DATA_SESSION_LOCKS = "_mobile_auth_session_locks"
_DATA_VIEW_REGISTERED = "_mobile_auth_view_registered"


@dataclass(frozen=True)
class MobileAuthSession:
    """A pending, short-lived mobile authentication handoff."""

    flow_id: str
    state: str
    origin: str
    expires_at: float
    trust_id: str | None = None


def create_mobile_auth_url(
    hass: HomeAssistant,
    flow_id: str,
    frontend_base: str,
    trust_id: str | None = None,
) -> str:
    """Register a pending handoff and return its one-time browser URL.

    `trust_id`, when supplied, is the config entry's already-trusted device
    identifier (e.g. during reauth) so NETGEAR recognizes this install as
    known instead of treating the browser handoff as a brand-new device.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    sessions: dict[str, MobileAuthSession] = domain_data.setdefault(_DATA_SESSIONS, {})
    completed_sessions: dict[str, MobileAuthSession] = domain_data.setdefault(
        _DATA_COMPLETED_SESSIONS, {}
    )
    session_locks: dict[str, asyncio.Lock] = domain_data.setdefault(
        _DATA_SESSION_LOCKS, {}
    )
    now = time.monotonic()
    for collection in (sessions, completed_sessions):
        for state, session in list(collection.items()):
            if session.expires_at <= now:
                collection.pop(state, None)
                session_locks.pop(state, None)

    if not domain_data.get(_DATA_VIEW_REGISTERED):
        hass.http.register_view(MeuralMobileAuthView())
        domain_data[_DATA_VIEW_REGISTERED] = True

    state = secrets.token_urlsafe(32)
    parsed_base = urlsplit(frontend_base)
    if (
        parsed_base.scheme != "https"
        or not parsed_base.netloc
        or parsed_base.username is not None
        or parsed_base.password is not None
    ):
        raise ValueError("Home Assistant supplied an invalid frontend address")
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    sessions[state] = MobileAuthSession(
        flow_id=flow_id,
        state=state,
        origin=origin,
        expires_at=now + MOBILE_AUTH_TIMEOUT,
        trust_id=trust_id,
    )
    path = AUTH_CALLBACK_PATH.format(flow_id=quote(flow_id, safe=""))
    return f"{origin}{path}?state={quote(state, safe='')}"


class MeuralMobileAuthView(HomeAssistantView):
    """Serve and receive the one-time browser-assisted login."""

    url = AUTH_CALLBACK_PATH
    name = AUTH_CALLBACK_NAME
    requires_auth = False

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """Serve the self-contained mobile login page."""
        session, completed = self._get_session(request, flow_id)
        if session is None:
            return self._error_page(
                "This mobile sign-in link is invalid or has expired.",
                status=web.HTTPGone.status_code,
            )

        if completed:
            self._notify_frontend_if_waiting(request.app[KEY_HASS], session.flow_id)
            return self._error_page(
                "This sign-in has already been sent. Home Assistant has been "
                "notified again; return to its tab to continue.",
                status=web.HTTPOk.status_code,
            )

        nonce = secrets.token_urlsafe(24)
        return web.Response(
            text=_mobile_login_html(nonce, session.trust_id),
            content_type="text/html",
            charset="utf-8",
            headers=_security_headers(nonce),
        )

    async def post(self, request: web.Request, flow_id: str) -> web.Response:
        """Pass a Cognito result back into the waiting config flow."""
        session, completed = self._get_session(request, flow_id)
        if session is None:
            return web.json_response(
                {"error": "This mobile sign-in link is invalid or has expired."},
                status=web.HTTPGone.status_code,
                headers={"Cache-Control": "no-store"},
            )

        if request.headers.get("Origin", "").rstrip("/") != session.origin:
            _LOGGER.warning(
                "Meural: Mobile sign-in response came from an unexpected origin"
            )
            return web.json_response(
                {"error": "The sign-in response came from an unexpected origin."},
                status=web.HTTPForbidden.status_code,
                headers={"Cache-Control": "no-store"},
            )

        if completed:
            flow_advanced = self._notify_frontend_if_waiting(
                request.app[KEY_HASS], session.flow_id
            )
            return web.json_response(
                {
                    "status": "ok",
                    "already_completed": True,
                    "flow_advanced": flow_advanced,
                },
                headers={"Cache-Control": "no-store"},
            )

        if request.content_type != "application/json" or request.content_length is None:
            return web.json_response(
                {"error": "Invalid sign-in response."},
                status=web.HTTPBadRequest.status_code,
                headers={"Cache-Control": "no-store"},
            )
        if request.content_length > 16_384:
            return web.json_response(
                {"error": "The sign-in response is too large."},
                status=web.HTTPRequestEntityTooLarge.status_code,
                headers={"Cache-Control": "no-store"},
            )

        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"error": "Invalid sign-in response."},
                status=web.HTTPBadRequest.status_code,
                headers={"Cache-Control": "no-store"},
            )

        validated = _validate_callback_data(data)
        if validated is None:
            return web.json_response(
                {"error": "The sign-in response is incomplete."},
                status=web.HTTPBadRequest.status_code,
                headers={"Cache-Control": "no-store"},
            )

        hass: HomeAssistant = request.app[KEY_HASS]
        domain_data = hass.data[DOMAIN]
        locks: dict[str, asyncio.Lock] = domain_data[_DATA_SESSION_LOCKS]
        lock = locks.setdefault(session.state, asyncio.Lock())
        async with lock:
            # A previous request may have completed while this one waited for the
            # lock. Treat the retry as success so a lost HTTP response is harmless.
            current_session, completed = self._get_session(request, flow_id)
            if current_session is None:
                return web.json_response(
                    {"error": "This mobile sign-in link is invalid or has expired."},
                    status=web.HTTPGone.status_code,
                    headers={"Cache-Control": "no-store"},
                )
            if completed:
                flow_advanced = self._notify_frontend_if_waiting(
                    hass, current_session.flow_id
                )
                return web.json_response(
                    {
                        "status": "ok",
                        "already_completed": True,
                        "flow_advanced": flow_advanced,
                    },
                    headers={"Cache-Control": "no-store"},
                )

            try:
                result = await hass.config_entries.flow.async_configure(
                    flow_id=flow_id,
                    user_input=validated,
                )
            except Exception:  # pylint: disable=broad-except
                # Keep the session pending: the browser can retry after restoring
                # its connection to Home Assistant.
                _LOGGER.exception("Meural: Could not resume the mobile sign-in flow")
                return web.json_response(
                    {"error": "Home Assistant could not resume the sign-in flow."},
                    status=web.HTTPBadRequest.status_code,
                    headers={"Cache-Control": "no-store"},
                )

            if result["type"] is not FlowResultType.EXTERNAL_STEP_DONE:
                _LOGGER.debug(
                    "Meural: Mobile sign-in flow was no longer active when the "
                    "browser handoff arrived"
                )
                return web.json_response(
                    {"error": "The Home Assistant sign-in flow is no longer active."},
                    status=web.HTTPBadRequest.status_code,
                    headers={"Cache-Control": "no-store"},
                )

            sessions: dict[str, MobileAuthSession] = domain_data[_DATA_SESSIONS]
            completed_sessions: dict[str, MobileAuthSession] = domain_data[
                _DATA_COMPLETED_SESSIONS
            ]
            sessions.pop(session.state, None)
            completed_sessions[session.state] = session

        locks.pop(session.state, None)
        _LOGGER.debug("Meural: Mobile sign-in handoff delivered to Home Assistant")
        return web.json_response(
            {"status": "ok", "flow_advanced": False},
            headers={"Cache-Control": "no-store"},
        )

    @staticmethod
    def _notify_frontend_if_waiting(hass: HomeAssistant, flow_id: str) -> bool:
        """Wake a reconnected frontend while the external step is still waiting."""
        try:
            result = hass.config_entries.flow.async_get(flow_id)
        except UnknownFlow:
            return True

        # `async_get` deliberately returns only the flow identity and current
        # step, so do not inspect a non-existent result type here. A refresh is
        # only sent after the browser has already delivered this session.
        hass.bus.async_fire(
            EVENT_DATA_ENTRY_FLOW_PROGRESSED,
            {
                "handler": result["handler"],
                "flow_id": flow_id,
                "refresh": True,
            },
        )
        return False

    @staticmethod
    def _get_session(
        request: web.Request,
        flow_id: str,
    ) -> tuple[MobileAuthSession | None, bool]:
        """Return a live session and whether its handoff already completed."""
        hass: HomeAssistant = request.app[KEY_HASS]
        state = request.query.get("state", "")
        domain_data = hass.data.get(DOMAIN, {})
        sessions: dict[str, MobileAuthSession] = domain_data.get(_DATA_SESSIONS, {})
        completed_sessions: dict[str, MobileAuthSession] = domain_data.get(
            _DATA_COMPLETED_SESSIONS, {}
        )
        session = sessions.get(state)
        completed = False
        if session is None:
            session = completed_sessions.get(state)
            completed = session is not None
        if session is None:
            _LOGGER.debug("Meural: Mobile sign-in session is invalid or expired")
            return None, False

        # A request with a guessed/malformed flow ID must never be able to remove
        # the real session belonging to the unguessable state value.
        if not secrets.compare_digest(session.flow_id, flow_id):
            _LOGGER.debug("Meural: Mobile sign-in flow ID does not match its session")
            return None, False

        if session.expires_at <= time.monotonic():
            _LOGGER.debug("Meural: Mobile sign-in session is invalid or expired")
            sessions.pop(state, None)
            completed_sessions.pop(state, None)
            domain_data.get(_DATA_SESSION_LOCKS, {}).pop(state, None)
            return None, False
        return session, completed

    @staticmethod
    def _error_page(message: str, status: int) -> web.Response:
        """Return a minimal no-cache error page without executable content."""
        return web.Response(
            text=(
                "<!doctype html><html lang='en'><meta charset='utf-8'>"
                f"<title>Meural sign-in</title><p>{html.escape(message)}</p></html>"
            ),
            status=status,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )


def _validate_callback_data(data: Any) -> dict[str, str] | None:
    """Validate the small result bundle produced by the trusted login page."""
    if not isinstance(data, dict) or set(data) != {
        "email",
        "cognito_access_token",
        "trust_id",
    }:
        return None

    email = data.get("email")
    access_token = data.get("cognito_access_token")
    trust_id = data.get("trust_id")
    if (
        not isinstance(email, str)
        or not 3 <= len(email.strip()) <= 320
        or "@" not in email
        or not isinstance(access_token, str)
        or not 100 <= len(access_token) <= 8192
        or not isinstance(trust_id, str)
    ):
        return None
    try:
        uuid.UUID(trust_id)
    except ValueError:
        return None
    return {
        "email": email.strip(),
        "cognito_access_token": access_token,
        "trust_id": trust_id,
    }


def _security_headers(nonce: str) -> dict[str, str]:
    """Return strict browser controls for a page that handles a password."""
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            f"connect-src 'self' {COGNITO_URL}; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _mobile_login_html(nonce: str, trust_id: str | None) -> str:
    """Build the standalone page; credentials never leave its browser context."""
    client_id = json.dumps(COGNITO_CLIENT_ID)
    cognito_url = json.dumps(COGNITO_URL)
    existing_trust_id = json.dumps(trust_id)
    # Sourced from netgear_auth.py's constants (see the comment there) so this
    # embedded browser-side copy of the Cognito challenge logic can't drift
    # from the Python implementation's data, even though the control flow
    # around it still has to be duplicated since this must run standalone in
    # a phone's browser rather than calling back into Home Assistant.
    response_key_map = json.dumps(RESPONSE_KEY_MAP)
    password_challenge_exclusion_keywords = json.dumps(
        list(PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS)
    )
    waf_error_keywords = json.dumps(list(WAF_ERROR_KEYWORDS))
    waf_block_page_keywords = json.dumps(list(WAF_BLOCK_PAGE_KEYWORDS))
    user_migration_keywords = json.dumps(list(USER_MIGRATION_KEYWORDS))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Meural mobile sign-in</title>
  <style nonce="{nonce}">
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; padding: 24px; background: #f4f5f7; color: #202124; }}
    main {{ max-width: 440px; margin: 5vh auto; padding: 28px; border-radius: 18px;
            background: #fff; box-shadow: 0 8px 30px #0002; }}
    h1 {{ margin-top: 0; font-size: 1.55rem; }}
    p {{ line-height: 1.45; }}
    label {{ display: block; margin-top: 18px; font-weight: 600; }}
    input {{ width: 100%; box-sizing: border-box; margin-top: 7px; padding: 13px;
             border: 1px solid #8b8b8b; border-radius: 9px; font: inherit; }}
    button {{ width: 100%; margin-top: 22px; padding: 13px; border: 0;
              border-radius: 999px; background: #e36f4f; color: #fff;
              font: inherit; font-weight: 700; }}
    button:disabled {{ opacity: .55; }}
    #code-form, #handoff, #done {{ display: none; }}
    #status {{ min-height: 1.5em; margin-top: 18px; }}
    .hint {{ font-size: .92rem; color: #5f6368; }}
    .error {{ color: #b3261e; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #161719; color: #eee; }}
      main {{ background: #242528; }}
      .hint {{ color: #bbb; }}
    }}
  </style>
</head>
<body>
<main>
  <section id="login">
    <h1>Sign in to Meural</h1>
    <p>Keep this page open, then turn Wi-Fi off before signing in so NETGEAR receives the request from your mobile connection.</p>
    <p class="hint">Your email, password, and verification code are sent directly from this browser to NETGEAR. Home Assistant receives only a temporary sign-in token.</p>
    <form id="login-form">
      <label>Email<input id="email" name="email" type="email" autocomplete="username" maxlength="320" required></label>
      <label>Password<input id="password" name="password" type="password" autocomplete="current-password" required></label>
      <button id="login-button" type="submit">Sign in with NETGEAR</button>
    </form>
    <form id="code-form">
      <label>Verification code<input id="code" name="code" type="text" autocomplete="one-time-code" inputmode="numeric" required></label>
      <button id="code-button" type="submit">Verify</button>
    </form>
    <p id="status" role="status" aria-live="polite"></p>
  </section>
  <section id="handoff">
    <h1>NETGEAR sign-in succeeded</h1>
    <p>The sign-in result now needs to be sent to Home Assistant.</p>
    <p><strong>Reconnect to your home Wi-Fi or VPN, keep this tab open, then press the button below.</strong></p>
    <button id="handoff-button" type="button">Send to Home Assistant</button>
    <p id="handoff-status" role="status" aria-live="polite"></p>
    <p class="hint">The result stays only in this open tab and the link expires after 10 minutes.</p>
  </section>
  <section id="done">
    <h1>Sign-in sent</h1>
    <p id="done-status" role="status" aria-live="polite">Notifying the Home Assistant window…</p>
    <button id="refresh-button" type="button">Refresh Home Assistant</button>
    <p class="hint">If Home Assistant still shows the external website step, make sure its tab is connected and press this button.</p>
  </section>
</main>
<script nonce="{nonce}">
"use strict";
const COGNITO_CLIENT_ID = {client_id};
const COGNITO_URL = {cognito_url};
const EXISTING_TRUST_ID = {existing_trust_id};
const RESPONSE_KEY_MAP = {response_key_map};
const PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS = {password_challenge_exclusion_keywords};
const WAF_ERROR_KEYWORDS = {waf_error_keywords};
const WAF_BLOCK_PAGE_KEYWORDS = {waf_block_page_keywords};
const USER_MIGRATION_KEYWORDS = {user_migration_keywords};
const loginForm = document.getElementById("login-form");
const codeForm = document.getElementById("code-form");
const handoffSection = document.getElementById("handoff");
const loginButton = document.getElementById("login-button");
const codeButton = document.getElementById("code-button");
const handoffButton = document.getElementById("handoff-button");
const refreshButton = document.getElementById("refresh-button");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const codeInput = document.getElementById("code");
const statusNode = document.getElementById("status");
const handoffStatusNode = document.getElementById("handoff-status");
const doneStatusNode = document.getElementById("done-status");
let currentEmail = "";
let currentPassword = "";
let trustId = "";
let pending = null;
let pendingHandoff = null;
let handoffInProgress = false;
let wakeInProgress = false;
let wakeCancelled = false;

function setStatus(message, isError = false) {{
  statusNode.textContent = message;
  statusNode.className = isError ? "error" : "";
}}

function setBusy(busy) {{
  loginButton.disabled = busy;
  codeButton.disabled = busy;
}}

function clearSecrets() {{
  currentPassword = "";
  passwordInput.value = "";
  codeInput.value = "";
  pending = null;
}}

function setHandoffStatus(message, isError = false) {{
  handoffStatusNode.textContent = message;
  handoffStatusNode.className = isError ? "error" : "";
}}

function showHandoff(message, canRetry = true) {{
  document.getElementById("login").style.display = "none";
  handoffSection.style.display = "block";
  setHandoffStatus(message, true);
  handoffButton.disabled = !canRetry;
}}

async function wakeHomeAssistant() {{
  if (wakeInProgress || wakeCancelled) return false;
  wakeInProgress = true;
  refreshButton.disabled = true;
  try {{
    const response = await fetch(window.location.pathname + window.location.search, {{
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {{ "Content-Type": "application/json" }},
      body: "{{}}"
    }});
    let result = {{}};
    try {{ result = await response.json(); }} catch (error) {{ /* No response body. */ }}
    if (!response.ok || result.status !== "ok") {{
      throw new Error(String(result.error || "Home Assistant could not be refreshed."));
    }}
    return result.flow_advanced === true;
  }} finally {{
    wakeInProgress = false;
    refreshButton.disabled = false;
  }}
}}

async function sendHandoff() {{
  if (!pendingHandoff || handoffInProgress) return;
  handoffInProgress = true;
  handoffButton.disabled = true;
  document.getElementById("login").style.display = "none";
  handoffSection.style.display = "block";
  setHandoffStatus("Sending the sign-in result to Home Assistant…");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {{
    const response = await fetch(window.location.pathname + window.location.search, {{
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(pendingHandoff),
      signal: controller.signal
    }});
    let result = {{}};
    try {{ result = await response.json(); }} catch (error) {{ /* No response body. */ }}
    if (!response.ok) {{
      if (response.status === 410) {{
        pendingHandoff = null;
        throw new Error("This sign-in link expired. Return to Home Assistant and start again.");
      }}
      throw new Error(String(result.error || "Home Assistant rejected the sign-in result."));
    }}
    if (result.status !== "ok") {{
      throw new Error("Home Assistant returned an invalid sign-in response.");
    }}
    const flowAdvanced = result.flow_advanced === true;
    pendingHandoff = null;
    clearSecrets();
    handoffSection.style.display = "none";
    document.getElementById("done").style.display = "block";
    doneStatusNode.textContent = flowAdvanced
      ? "Home Assistant continued the setup. You can return to its tab."
      : "Return to Home Assistant. If it still waits, come back and press Refresh Home Assistant.";
  }} catch (error) {{
    if (pendingHandoff) {{
      showHandoff("Reconnect to your home Wi-Fi or VPN, then try sending again.");
    }} else {{
      showHandoff(String(error.message || "The sign-in result could not be sent."), false);
    }}
  }} finally {{
    clearTimeout(timeout);
    handoffInProgress = false;
    if (pendingHandoff) handoffButton.disabled = false;
  }}
}}

function newTrustId() {{
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0"));
  return hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
    hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
    hex.slice(10).join("");
}}

async function cognitoCall(target, payload) {{
  const response = await fetch(COGNITO_URL, {{
    method: "POST",
    mode: "cors",
    credentials: "omit",
    cache: "no-store",
    headers: {{
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService." + target
    }},
    body: JSON.stringify(payload)
  }});
  let body = {{}};
  try {{ body = await response.json(); }} catch (error) {{ /* No response body. */ }}
  if (!response.ok) {{
    const failure = new Error(String(body.message || body.Message || "NETGEAR rejected the sign-in."));
    failure.details = JSON.stringify(body).toLowerCase();
    failure.status = response.status;
    throw failure;
  }}
  return body;
}}

function metadata() {{
  return {{ trustID: trustId, sourceEvent: "login", language: "en-US", appType: "meural" }};
}}

async function initiate() {{
  try {{
    return await cognitoCall("InitiateAuth", {{
      AuthFlow: "CUSTOM_AUTH",
      ClientId: COGNITO_CLIENT_ID,
      AuthParameters: {{ USERNAME: currentEmail }},
      ClientMetadata: metadata()
    }});
  }} catch (error) {{
    const details = String(error.details || "");
    if (!USER_MIGRATION_KEYWORDS.some(keyword => details.includes(keyword))) throw error;
    return cognitoCall("InitiateAuth", {{
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: COGNITO_CLIENT_ID,
      AuthParameters: {{ USERNAME: currentEmail, PASSWORD: currentPassword }},
      ClientMetadata: metadata()
    }});
  }}
}}

function responseKey(name) {{
  return RESPONSE_KEY_MAP[name] || "ANSWER";
}}

function passwordAnswersChallenge(name, parameters, attempt) {{
  if (name !== "CUSTOM_CHALLENGE" || attempt !== 1) return false;
  const description = JSON.stringify(parameters || {{}}).toLowerCase();
  return !PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS.some(keyword => description.includes(keyword));
}}

async function respond(challenge, answer) {{
  return cognitoCall("RespondToAuthChallenge", {{
    ChallengeName: challenge.name,
    ClientId: COGNITO_CLIENT_ID,
    Session: challenge.session,
    ChallengeResponses: {{
      USERNAME: currentEmail,
      [responseKey(challenge.name)]: answer
    }},
    ClientMetadata: metadata()
  }});
}}

async function processResponse(response, attempt = 0) {{
  while (!response.AuthenticationResult) {{
    attempt += 1;
    const name = response.ChallengeName;
    const session = response.Session;
    const parameters = response.ChallengeParameters || {{}};
    if (!name || !session) throw new Error("NETGEAR returned an unsupported sign-in response.");
    const challenge = {{ name, session, parameters, attempt }};
    if (passwordAnswersChallenge(name, parameters, attempt)) {{
      response = await respond(challenge, currentPassword);
      continue;
    }}
    pending = challenge;
    loginForm.style.display = "none";
    codeForm.style.display = "block";
    setBusy(false);
    setStatus("Enter the verification code sent by NETGEAR.");
    codeInput.focus();
    return;
  }}
  const accessToken = response.AuthenticationResult.AccessToken;
  if (typeof accessToken !== "string" || accessToken.length < 100) {{
    throw new Error("NETGEAR did not return a usable sign-in token.");
  }}
  pendingHandoff = {{
    email: currentEmail,
    cognito_access_token: accessToken,
    trust_id: trustId
  }};
  currentPassword = "";
  passwordInput.value = "";
  codeInput.value = "";
  pending = null;
  await sendHandoff();
}}

function isWafError(error) {{
  const details = String(error.details || "");
  if (WAF_ERROR_KEYWORDS.some(keyword => details.includes(keyword))) return true;
  return error.status === 403 && WAF_BLOCK_PAGE_KEYWORDS.some(keyword => details.includes(keyword));
}}

function showFailure(error) {{
  setBusy(false);
  if (isWafError(error)) {{
    setStatus("NETGEAR also blocked this mobile connection. Confirm Wi-Fi is off and retry later.", true);
  }} else {{
    setStatus(String(error.message || "Sign-in failed."), true);
  }}
}}

loginForm.addEventListener("submit", async (event) => {{
  event.preventDefault();
  currentEmail = emailInput.value.trim();
  currentPassword = passwordInput.value;
  trustId = EXISTING_TRUST_ID || newTrustId();
  pending = null;
  setBusy(true);
  setStatus("Contacting NETGEAR…");
  try {{ await processResponse(await initiate()); }} catch (error) {{ showFailure(error); }}
}});

codeForm.addEventListener("submit", async (event) => {{
  event.preventDefault();
  if (!pending) return;
  const challenge = pending;
  pending = null;
  setBusy(true);
  setStatus("Checking the verification code…");
  try {{ await processResponse(await respond(challenge, codeInput.value.trim()), challenge.attempt); }}
  catch (error) {{ pending = challenge; showFailure(error); }}
}});

handoffButton.addEventListener("click", sendHandoff);

refreshButton.addEventListener("click", async () => {{
  doneStatusNode.textContent = "Notifying Home Assistant again…";
  try {{
    const advanced = await wakeHomeAssistant();
    doneStatusNode.textContent = advanced
      ? "Home Assistant continued the setup. You can return to its tab."
      : "Home Assistant was notified. Return to its tab; press again if it still waits.";
  }} catch (error) {{
    doneStatusNode.textContent = String(error.message || "Home Assistant could not be refreshed.");
  }}
}});

window.addEventListener("pagehide", () => {{
  wakeCancelled = true;
  clearSecrets();
  pendingHandoff = null;
  currentEmail = "";
  trustId = "";
}});
</script>
</body>
</html>"""
