# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HA-meural is a Home Assistant custom component that integrates NETGEAR Meural Canvas digital art frames. It provides media player entities with support for controlling artwork display, playlists, brightness, and various Canvas settings through both the Meural cloud API and local device interface.

**Repository**: https://github.com/GuySie/ha-meural

## Validation and Testing

### Validating the Integration

Run Home Assistant's hassfest validation (GitHub Actions will run this automatically on push):
```bash
# This validation runs via GitHub Actions (.github/workflows/hassfest.yaml)
# No local test suite exists
```

### Manual Testing

To test changes, install the integration in a Home Assistant instance:
1. Copy `custom_components/meural` to your Home Assistant's `custom_components` directory
2. Restart Home Assistant
3. Add the Meural integration via UI (*Settings* → *Devices & Services* → *Add Integration*)

## Architecture

### Dual Coordinator Pattern (v2.0.0+)

The integration uses two DataUpdateCoordinators for efficient polling:

**CloudDataUpdateCoordinator** (`coordinator.py:49-226`):
- Polls Meural cloud API every 60 seconds (device settings only), or 3600 seconds (1 hour) when all devices are sleeping
- Gallery data fetched separately via `async_refresh_galleries()` every 30 minutes (`GALLERY_UPDATE_INTERVAL`)
- Gallery refresh triggered synchronously at startup (in `__init__.py` after `async_config_entry_first_refresh()`), as a background task on regular poll when stale, lazily on media browser open, and after `synchronize()` service
- Handles authentication errors and triggers reauth flow
- Shared across all devices for a single account
- Aggregates sleep state across all entities to determine polling interval
- `async_apply_device_setting()`: writes a cloud-backed setting via `PyMeural.update_device()` then optimistically patches the cached device dict and calls `async_set_updated_data()`; used by the number, select, and switch entities' `async_set_native_value`/`async_select_option`/`async_turn_on`/`async_turn_off` instead of each duplicating that update+patch logic

**LocalDataUpdateCoordinator** (`coordinator.py:229-450`):
- Polls local device API every 10 seconds
- Each device has its own local coordinator instance
- Fetches real-time state: sleep status, local galleries, gallery status, gsensor orientation, lux, free space, WiFi signal
- When device is sleeping, skips gallery fetches but still polls `send_get_system()` so sensor data (lux, free space, WiFi signal) continues to update every 10 seconds
- Gracefully handles offline devices without failing the integration
- Preserves last known sleep state on transient connection failures (no flickering)
- Returns cached data when device is unreachable

### Core Components

**PyMeural** (`pymeural.py`):
- Cloud API client for Meural's REST API (https://api.meural.com/v1/)
- Uses the current NETGEAR Accounts flow (Cognito `CUSTOM_AUTH`, OAuth token exchange, and NETGEAR refresh endpoint)
- Handles authentication token lifecycle with callback for persistent storage
- Exponential backoff (module-level `_AUTH_BACKOFF_STATE`, see Authentication Flow above) before retrying a failed token refresh, to avoid hammering NETGEAR's auth endpoint during a sustained WAF block or outage
- All API methods are async and use aiohttp; cloud requests use `async with self.session.request(...)` so the connection is always released back to the pool

**NetgearAuthenticator** (`netgear_auth.py`):
- Handles password and OTP/MFA Cognito challenges interactively through the config flow
- Exchanges the Cognito access token for Meural access, ID, and refresh tokens
- Refreshes Meural access tokens through NETGEAR Accounts without repeating password login
- Reuses a persistent trust identifier and never uses the stored password for background refresh
- Detects a NETGEAR CloudFront/WAF block from either a JSON `ForbiddenException`-style body or an HTML block page (CloudFront's own 403 response has no JSON body)
- A 429 or 5xx from NETGEAR while submitting a challenge answer or refreshing a token raises `CannotConnect`, not a credential/challenge error, so a transient outage doesn't get misreported as a bad code or invalid auth
- Shared lookup tables (`RESPONSE_KEY_MAP`, `PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS`, `WAF_ERROR_KEYWORDS`, `WAF_BLOCK_PAGE_KEYWORDS`, `USER_MIGRATION_KEYWORDS`) are re-exported and embedded verbatim into the browser-side Cognito flow in `mobile_auth.py`, so the standalone mobile sign-in page's challenge/WAF-detection logic can't drift from this module's data (the control flow itself still has to be duplicated in JS since that page runs standalone in a phone's browser)

**MeuralDeviceInfoMixin** (`entity.py`):
- Shared `device_info` property (keyed by the device's `productKey`) mixed into the light, number, select, sensor, and switch entity classes, replacing a property that used to be duplicated in each of those files

**LocalMeural** (`pymeural.py`):
- Local device API client for Canvas web server (http://DEVICE-IP/remote/)
- Controls device directly without cloud dependency
- Handles device sleep/wake detection

**MeuralBacklightLight** (`light.py`):
- Light entity controlling the Canvas backlight brightness
- Turning off suspends the Canvas device (equivalent to media player turn off)
- Turning on wakes the Canvas device (equivalent to media player turn on)
- Uses optimistic state updates for on/off; brightness changes apply immediately
- Stays in sync with the media player entity — both reflect the same sleep/wake state

**MeuralSensorEntities** (`sensor.py`):
- **Ambient Light** (`MeuralLuxSensor`): illuminance in lux from local API; enabled by default; useful for automations
- **Free Space** (`MeuralFreeSpaceSensor`): available Canvas storage in MB from local API; diagnostic; disabled by default
- **WiFi Signal** (`MeuralWifiSignalSensor`): WiFi signal strength in dBm from local API; diagnostic; disabled by default
- **Last Seen by Cloud** (`MeuralLastSeenSensor`): timestamp of last cloud contact from cloud API; diagnostic; disabled by default

**MeuralEntity** (`media_player.py`):
- Media player entity implementing standard Home Assistant media player features
- Coordinates between cloud and local data sources
- Registers custom services (set_brightness, preview_image, set_device_option, etc.)
- Detects physical device rotation via gsensor changes when orientationMatch is enabled
- Reloads current gallery after orientation change to force `current_item` update (local API limitation)
- Updates thumbnails immediately after user navigation actions without waiting for next poll cycle
- Uses optimistic state updates for turn on/off, pause/play, and shuffle so the UI reflects changes instantly
- Turn on: optimistically sets sleeping=False; device takes several seconds to wake so no immediate refresh (10s poll confirms)
- Turn off: optimistically sets sleeping=True then confirms with an immediate refresh (device suspends quickly)
- `sw_version` in `device_info` prefers local firmware version from `send_get_system()`, falls back to cloud value
- `_cloud_only_galleries()`: computes galleries present in cloud (`device_galleries` + `user_galleries`) but not yet on the local device; used by `source_list`, `async_select_source`, `async_browse_media`, and `async_play_media`
- Gallery selection tries local device first (via `send_change_gallery`); if not on device, falls back to cloud API (`device_load_gallery`)
- `async_browse_media` gracefully skips inaccessible playlist thumbnail items: when the cloud API fails to fetch a thumbnail (`aiohttp.ClientError`, `asyncio.TimeoutError`, or missing `image` key), a warning is logged and browsing continues without a thumbnail

### Authentication Flow

1. User provides email/password via config flow
2. NetgearAuthenticator starts Cognito `CUSTOM_AUTH` and answers the password challenge
3. If NETGEAR requires OTP/MFA, the config flow asks for the verification code
4. The Cognito token is exchanged through NETGEAR Accounts for Meural OAuth tokens
5. Meural tokens are stored in the config entry; the password is never persisted (used only in-memory for the interactive sign-in, then discarded). `__init__.py` scrubs any password still stored by a pre-upgrade config entry on the next setup.
6. Reauthentication (`async_step_reauth`) reuses the config entry's existing `trust_id`, so NETGEAR typically recognizes the device as already-trusted and skips a fresh OTP/MFA challenge. `async_step_reauth_confirm` remains as a compatibility shim (forwards to `async_step_password_login`) for reauth flows already open in the frontend before an upgrade.
7. Access tokens are refreshed through NETGEAR Accounts. A failed refresh (invalid auth, connection error, or WAF block) is recorded in `pymeural.py`'s module-level `_AUTH_BACKOFF_STATE`, keyed by `trust_id` (or the config entry ID before one is known); further refresh attempts back off exponentially (60s, doubling to a 30-minute cap) until a refresh succeeds or `reset_auth_backoff()` clears it after a fresh interactive login. State is kept at module scope, not on the `PyMeural` instance, so it survives Home Assistant recreating `PyMeural` on every `ConfigEntryNotReady` retry.
8. If the Meural refresh token fails outright (`InvalidAuth`), Home Assistant triggers the reauth flow; a `CannotConnect` or `AuthenticationBlocked` (WAF/rate-limit) failure raises `UpdateFailed` for retry instead, since re-entering credentials can't fix a network or WAF issue

### Local-only mode (v2.6.0+)

An alternative setup path that never contacts NETGEAR's servers — no account, no `PyMeural`/`CloudDataUpdateCoordinator`, no stored credentials. Controls a single Canvas over its local web server only.

- Entry point: `config_flow.py` adds a `local_setup` menu option (and an `async_step_reconfigure` to edit the host/name later). The user supplies the Canvas IP; `pymeural.build_local_device()` probes the local `identify`/`system` endpoints and builds a synthetic cloud-style device dict (defensive field mapping, safe defaults for cloud-only keys). The entry stores `{local_only: True, host, device}`.
- `__init__.py` branches on `entry.data[CONF_LOCAL_ONLY]` (`_async_setup_local_entry`): creates only a `LocalDataUpdateCoordinator`, stores `meural=None`/`cloud_coordinator=None` plus a shared `devices` dict and `local_only` flag, and forwards to `LOCAL_PLATFORMS` (`media_player`, `sensor`, `light`, `select`). `number`/`switch` are skipped (all cloud-backed).
- Platforms read the device list from `entry_data["devices"]` (populated in both modes). `select.py`/`sensor.py` register only their local-capable entities in local-only mode (the orientation select and the 4 local sensors; the cloud setting selects and the Last-Seen sensor are skipped).
- `media_player.py` `MeuralEntity` takes a `local_only` flag: in local-only mode the local coordinator is its primary `CoordinatorEntity`, cloud-only features (`SHUFFLE_SET`/`PAUSE`/`PLAY`) are dropped, cloud service methods early-return with a warning (`_cloud_service_unavailable`), and browse/source/play paths use local galleries only. Artwork metadata is left unset (`_fetch_current_item_if_needed` returns early): the only metadata source is the cloud item API, and `gallery_status.current_item` is a cloud id that the local items endpoint doesn't resolve to a title.

### Data Flow

1. Cloud coordinator fetches device settings from Meural API every 60s; gallery data fetched separately every 30 min
2. Local coordinator polls each device's local interface every 10s for real-time state including gsensor
3. Media player entity subscribes to both coordinators
4. Entity state derived from combination of cloud and local data
5. Update intervals adjust dynamically (slower when all devices sleeping)
6. gsensor changes trigger gallery reload to detect orientationMatch item switches
7. User actions (next/prev track, playlist changes) trigger immediate local coordinator refresh
8. Turn on/off, pause/play use optimistic state updates for instant UI feedback without waiting for next poll

## Key Files

- `__init__.py`: Integration setup, coordinator initialization
- `coordinator.py`: Cloud and local data update coordinators
- `media_player.py`: Media player entity implementation and custom services
- `light.py`: Backlight light entity
- `sensor.py`: Sensor entities (ambient light, free space, WiFi signal, last cloud contact)
- `entity.py`: `MeuralDeviceInfoMixin`, the shared `device_info` property used by the light, number, select, sensor, and switch entities
- `pymeural.py`: API clients for both cloud and local interfaces
- `netgear_auth.py`: NETGEAR Accounts/Cognito authentication (interactive login, OTP/MFA challenges, token refresh, WAF detection)
- `mobile_auth.py`: Browser-assisted mobile sign-in handoff for a blocked home IP
- `config_flow.py`: Configuration flow for UI setup
- `const.py`: Constants (update intervals, domain name)
- `services.yaml`: Custom service definitions
- `manifest.json`: Integration metadata (version, requirements, dependencies)

## Dependencies

- **aiohttp**: Async HTTP client (provided by Home Assistant); the fork's interactive NETGEAR flow uses this, not boto3 — upstream's `manifest.json` still lists `boto3` (its own auth flow depends on it), so merging from upstream will keep reintroducing a `boto3` requirement into this fork's `manifest.json` that nothing here actually imports. Drop it again after each upstream merge.
- Home Assistant 2026.8+ (upstream v2.4.1 dependency baseline)
- Python 3.11+

## Custom Services

Beyond standard media player services, the integration provides:
- `meural.set_brightness`: Set backlight brightness (0-100)
- `meural.reset_brightness`: Enable automatic brightness via ambient light sensor
- `meural.toggle_informationcard`: Toggle museum-style artwork information card
- `meural.synchronize`: Sync Meural server with Canvas
- `meural.preview_image`: Display image from URL temporarily
- `meural.set_device_option`: Configure Canvas options (orientation, shuffle, duration, etc.)
- `meural.play_random_playlist`: Pick a random playlist from all playlists loaded on the Canvas and play it
- `meural.load_playlist`: (Re)load a playlist from the cloud API onto the Canvas by `gallery_id` or `gallery_name`; synchronizes cloud changes not yet on the device

All services are fully documented in `services.yaml`.

## Important Notes

- NETGEAR email, SMS, authenticator, and custom challenges are supported through the config flow
- SD card folders (meural1-4) supported but with limited metadata
- Uses both cloud polling and local device communication
- Local IP discovery happens via cloud API (device must be online to initial setup)
- Preview images use temporary display mechanism with configurable duration
- `source_list` includes cloud-only galleries immediately on startup as gallery data is populated synchronously during integration setup
