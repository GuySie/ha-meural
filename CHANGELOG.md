# Changelog

All notable changes to the ha-meural Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.1-beta.3] - 2026-08-27

### Fixed
- Fixed a 500 response when refreshing a completed mobile sign-in: Home Assistant's public flow lookup does not include a result `type`, so the refresh notification now uses only the documented flow identity and step data.
- Use Home Assistant's public event bus method for the reconnect notification, compatible with Home Assistant 2026.8.

## [2.5.1-beta.2] - 2026-08-27

### Fixed
- A Home Assistant tab that missed the one-time external-flow event while the phone changed networks can now be notified again with **Refresh Home Assistant**. Reopening an already-completed mobile sign-in link also sends this notification instead of only showing that the result was previously delivered.
- The browser now validates the expected JSON success status before clearing the temporary sign-in result, so an unexpected empty or non-JSON 2xx response remains retryable.
- A callback containing a mismatched flow ID no longer removes the valid session associated with its unguessable state value.

## [2.5.1-beta.1] - 2026-08-25

### Fixed
- Mobile sign-in no longer requires Home Assistant to remain reachable while the phone uses mobile data. If the callback fails, the result remains only in the open browser tab so the user can reconnect to home Wi-Fi or VPN and press **Send to Home Assistant**.
- Made the callback idempotent so retrying after a lost HTTP response cannot deliver the same sign-in result to the Home Assistant config flow twice.

### Security
- The temporary Cognito result is never displayed, copied to the clipboard, or written to browser storage; it is cleared when the tab closes and the one-time link still expires after 10 minutes.

## [2.5.0] - 2026-08-19

### Fixed
- Fixed a connection leak in `PyMeural.request()`: the cloud API response was never used as a context manager, so the underlying connection was never released back to the pool.
- `expires_in: 0` and a JWT `exp` claim of `0` in a token response are no longer silently replaced with a 1-hour fallback expiry, which could keep an already-expired token in use.
- NETGEAR's CloudFront/WAF block is now also detected when it answers with an HTML block page instead of a JSON error body.
- A WAF block or 5xx/429 response while refreshing a token or submitting a challenge answer is no longer misreported as invalid credentials, so it no longer forces an unnecessary reauth.
- `async_refresh_galleries()` now also treats a WAF/auth block as a recoverable error, matching the regular device-settings poll, instead of raising it unhandled from integration setup, the `synchronize` service, and the media browser.
- Restored a compatibility shim (`async_step_reauth_confirm`) for reauth flows that were already open in the Home Assistant frontend before this update installs.

### Added
- Added exponential backoff (60s, doubling up to a 30-minute cap) before retrying a failed cloud token refresh, keyed per account (`trust_id`, falling back to the config entry ID before one is known) so a sustained WAF block or outage doesn't hammer NETGEAR's auth endpoint. Resets automatically after a successful reauthentication.
- Reauthentication now reuses the config entry's existing `trust_id`, so NETGEAR recognizes the device as already-trusted and typically skips a fresh OTP/MFA challenge.

### Changed
- The account password is no longer persisted in the config entry after login — it was kept only for v2.3.x rollback compatibility, which is now dropped. Existing config entries have any stored password scrubbed automatically on the next Home Assistant restart.
- The local Canvas HTTP client now reuses its pooled keep-alive connection for the first attempt of a safe (retryable) read instead of always requesting a fresh connection.
- The display orientation select entity's optimistic state now falls back to the last value reported by the local coordinator after a bounded timeout (3 poll intervals) if the device never confirms the change, instead of blocking on a fixed 0.5s sleep and forced refresh after every change.
- Extracted the repeated `device_info` property into a shared `MeuralDeviceInfoMixin` (new `entity.py`), used by the light, number, select, sensor, and switch entities.
- Consolidated the repeated "update the cloud setting, then optimistically patch the coordinator's cached device data" logic into `CloudDataUpdateCoordinator.async_apply_device_setting()`, used by the number, select, and switch entities.

## [2.4.2-beta.1] - 2026-08-13

### Changed
- Merged the current upstream `master` branch, including the official v2.4.1 Home Assistant 2026.8 compatibility update.
- Updated the `boto3` requirement to `>=1.42.97`, matching upstream v2.4.1 and Home Assistant 2026.8 dependency constraints.
- Added upstream's current HACS validation workflow and simplified `hacs.json` metadata.

### Preserved
- Kept the browser-assisted mobile sign-in, current NETGEAR Accounts token flow, additional Canvas entities, local connection retries, and automatic local IP refresh from the previous fork betas.

## [2.4.1] - 2026-08-05

WARNING: Do not update to this version unless you are running Home Assistant 2026.8+

### Fixed
- Support for Home Assistant 2026.8+. The `boto3` requirement is now `>=1.42.97` to match the updated Home Assistant dependency constraints.

## [2.4.0-beta.8] - 2026-07-25

### Fixed
- Updated the local Canvas client when the Meural cloud reports a changed DHCP IP address.
- Removed the dependency on the media-player entity for propagating cloud device updates to local coordinators.
- Replaced empty `DeviceTurnedOff` details with the attempted Canvas IP and underlying connection error.
- Marked local entities unavailable after three consecutive failed updates, using Home Assistant's built-in one-time failure and recovery reporting instead of repeating warning reminders indefinitely.

## [2.4.0-beta.7] - 2026-07-22

### Fixed
- Added one automatic retry for interrupted, read-only Canvas requests.
- Prevented reuse of HTTP connections that the embedded Canvas web server may reset.
- Kept transient local connection failures at debug level and only warn after three consecutive failed updates, with rate-limited reminders during a longer outage.
- Continued serving cached Canvas state during temporary local connection failures.

## [2.4.0-beta.6] - 2026-07-21

### Added
- Added a local **Display Orientation** select for portrait and landscape mode.
- Added **Orientation Match** and **Sleep When Dark** switches for supported Canvases.
- Added **Light Sensitivity** and **Artwork Duration** number entities.
- Added **Image Fit Mode** and **Letterbox Color** select entities.
- Added a **Physical Orientation** sensor based on the Canvas accelerometer.

### Improved
- Preserved all cached local sensor and orientation values during temporary Canvas connection failures.

## [2.4.0-beta.5] - 2026-07-21

### Added
- Added an **Auto Brightness** switch for each Canvas that exposes the Meural `alsEnabled` cloud setting.

### Improved
- Made the backlight light entity's brightness state more reliable by falling back to the Canvas `/remote/get_backlight/` endpoint when the general system response does not contain a backlight value.
- Documented where Home Assistant displays the light entity's brightness slider.

## [2.4.0-beta.4] - 2026-07-21

### Changed
- Simplified setup and reauthentication to two choices: normal sign-in from Home Assistant for connections without an IP block, and browser-assisted mobile sign-in for NETGEAR WAF/IP blocks.

### Removed
- Removed the temporary HTTP CONNECT proxy option and all supporting proxy code, validation, tests, and active documentation.

## [2.4.0-beta.3] - 2026-07-21

### Added
- Added a browser-assisted mobile sign-in option for NETGEAR WAF/IP blocks. Home Assistant provides a one-time link that can be opened on a phone using mobile data, so the Cognito authentication requests originate from the phone instead of the blocked Home Assistant connection.
- The one-time link expires after 10 minutes and accepts a result only once. The NETGEAR email, password, and verification code stay in the phone browser and are sent directly to Cognito; Home Assistant receives only the short-lived Cognito access token needed for the Meural token exchange.
- Kept direct password authentication and the temporary HTTP CONNECT proxy as alternative sign-in methods.

## [2.4.0-beta.2] - 2026-07-21

### Added
- Added an optional temporary HTTP CONNECT proxy for interactive login and OTP challenges. This allows a blocked home IP to complete NETGEAR authentication through a trusted device on another connection without routing all Home Assistant traffic through a VPN.
- The temporary proxy is validated, used only by the active config flow, and never stored in the Home Assistant config entry.

## [2.4.0-beta.1] - 2026-07-15

### Fixed
- Replaced the legacy direct Cognito token flow with the current NETGEAR Accounts flow: Cognito `CUSTOM_AUTH`, optional OTP/MFA challenge handling, OAuth token exchange, and Meural token refresh through `accounts2.netgear.com`.
- Stopped background password logins after a token failure, preventing repeated OTP messages and AWS WAF retry storms.
- Added a complete Home Assistant reauthentication flow for expired legacy sessions.

### Changed
- Cloud requests now use the Meural v1 API and current web-client headers.
- Background token refresh no longer repeats the password login.
- Removed the `boto3` dependency; Cognito calls now use Home Assistant's shared async HTTP session.

## [2.3.0] - 2026-05-19

### Fixed
- Browsing media no longer fails when a playlist thumbnail item is inaccessible via the Meural cloud API; the error is logged as a warning and browsing continues without a thumbnail.

## [2.2.0] - 2026-03-23

### Added
- **orientationMatch detection restored**: Automatic detection of physical device rotation via gsensor is back. The integration reloads the current gallery when the device rotates with orientationMatch enabled, keeping `current_item` metadata accurate.
- **Ambient light sensor**: New sensor reporting the Canvas ambient light level in lux, sourced from the local device API. The local coordinator now polls `send_get_system()` even when the Canvas is sleeping, so the ambient light (lux) sensor — and other local sensors — continue to update every 10 seconds while the device is off. 
- **Free Space sensor**: New diagnostic sensor reporting available Canvas storage space in megabytes. Disabled by default; enable in Home Assistant's entity settings.
- **WiFi Signal sensor**: New diagnostic sensor reporting Canvas WiFi signal strength in dBm. Disabled by default; enable in Home Assistant's entity settings.
- **Last Seen by Cloud sensor**: New diagnostic sensor reporting the last timestamp the device contacted the Meural cloud, useful for connectivity monitoring. Disabled by default; enable in Home Assistant's entity settings.
- **Backlight light entity**: New light entity for the Canvas backlight, allowing brightness control and on/off. Turning the light off suspends the Canvas; turning it on wakes it.
- **Local firmware version**: The Canvas firmware version shown in Home Assistant is now sourced from the local device API for accuracy.

## [2.1.0] - 2026-03-03

### Changed
- **Reduced local API calls**: Removed `send_get_system()` call from the local polling cycle, reducing local device API calls from 4 to 3 per 10-second poll when the device is awake. The gsensor orientation data is no longer fetched or used.

### Removed
- **orientationMatch detection**: Removed automatic detection of physical device rotation via gsensor. The integration no longer reloads the current gallery when the device rotates with orientationMatch enabled. `current_item` metadata may be stale after a rotation until the gallery naturally advances.

## [2.0.0] - 2026-02-28
- Modernized component to current Home Assistant best practices using Claude Code
- Fixed longstanding bugs using Claude Code
- Implemented longstanding feature requests using Claude Code

### Breaking Changes
- **None** - This release is fully backward compatible with v1.x installations

### Added
- **DataUpdateCoordinator architecture**: Implemented modern coordinator pattern with dual coordinators (CloudDataUpdateCoordinator and LocalDataUpdateCoordinator)
- **Dynamic polling intervals**: Cloud API polling adjusts from 60s when devices are awake to 3600s (1 hour) when all devices are sleeping. Gallery data is now fetched on a 30-minute interval separately from the 60s device settings poll, reducing cloud API load.
- **Refresh token support**: AWS Cognito refresh tokens reduce re-authentication from every 10 minutes to every ~30 days
- **Automatic reauth flow**: Authentication errors now trigger Home Assistant's reauth flow automatically
- **Duplicate auth prevention**: Async lock prevents multiple parallel API calls from triggering duplicate authentication attempts
- **Cloud gallery selection**: Playlists not yet loaded on the Canvas now appear in `source_list` and the media browser under "Meural Playlists"; selecting one loads it onto the device via the Meural cloud API (`device_load_gallery`)
- **`meural.play_random_playlist` service**: New service that picks a random playlist from all playlists currently loaded on the Canvas and plays it; avoids re-selecting the currently playing playlist when multiple playlists are available
- **`meural.load_playlist` service**: New service that (re)loads the chosen playlist from the cloud API. This synchronizes any changes made to the playlist on the cloud API that were not stored on the local device yet.

### Changed
- **Improved efficiency**: LocalMeural instances are now persistent and reused instead of being recreated on every call
- **Modern string formatting**: Updated all string formatting to use f-strings and logging best practices
- **Better coordinator-based state management**: Entities now use coordinator data instead of manual polling
- **Pagination support**: Fetch all devices and galleries (up to 1000) instead of only the first 10 items
- **Immediate thumbnail updates**: User navigation actions (next/previous track, playlist changes) now update thumbnails immediately instead of waiting for next polling cycle
- **Optimized thumbnail fetching**: Only fetch artwork metadata from cloud when displayed item actually changes, reducing API calls from every 10s to only when needed
- **Optimistic state updates**: Turn on/off, pause/play, and shuffle now update the media player card instantly without waiting for the next poll cycle
- **Efficient polling**: Cloud coordinator aggregates all devices' sleep states - polls at 60s if any device is awake, 3600s (1 hour) only when all devices are sleeping
- **Comprehensive type hints**: Added type annotations throughout the codebase for better maintainability
- **Enhanced error visibility**: Local coordinator connection failures now log at WARNING level instead of DEBUG, with clear indication of cached data usage
- **Better error recovery**: Improved exception handling with specific exception types

### Deprecated
- Removed `CONFIG_SCHEMA` (no longer needed in modern Home Assistant)
- Removed `CONNECTION_CLASS` attribute (deprecated in Home Assistant)
- Removed version checks for MAJOR_VERSION/MINOR_VERSION (no longer needed)
- Removed try/except import for MediaPlayerDevice/MediaPlayerEntity (modern HA only uses MediaPlayerEntity)

### Fixed
- **Critical safety fix**: Replaced all bare `except:` clauses with specific exception types (aiohttp.ClientError, asyncio.TimeoutError, KeyError) to prevent catching system exits and other critical exceptions
- **Config flow bug**: Fixed config flow error handling where `raise` statement prevented error messages from displaying to users
- **Memory efficiency**: Fixed inefficient LocalMeural instance creation pattern
- **aiohttp parameter error**: Fixed "unexpected keyword argument 'query'" by changing to correct 'params' parameter in both PyMeural and LocalMeural
- **Cloud coordinator race condition**: Fixed issue where multiple devices could cause incorrect polling intervals by having each entity independently set the coordinator interval
- **orientationMatch detection**: Fixed issue where device orientation changes with orientationMatch enabled wouldn't update artwork details in Home Assistant. Uses gsensor data from local system API to detect physical rotation; reloads the current gallery to force `current_item` update since the local API doesn't reflect orientationMatch switches until a gallery reload
- **Sleep state flickering**: Fixed transient connection failures incorrectly flipping device state to sleeping; now preserves last known sleep state on network errors to prevent STATE_PLAYING/STATE_OFF flickering
- **play_media error handling**: Fixed missing early return after cloud API error in the item play handler, preventing subsequent local API call on already-failed operations
- **Log format string**: Fixed malformed warning log message when local device contact fails, resolving "Bad logger message" errors in Home Assistant logs
- **Turn on not showing thumbnail**: After waking a Canvas, the media player card now immediately reflects the ON state; thumbnail loads within the next 10-second local poll once the device has fully woken
- **Turn off staying ON**: Media player card now immediately shows OFF state when turning off, confirmed by a rapid local coordinator refresh
- **Pause/play state delay**: Pausing or resuming now immediately updates the media player card instead of waiting up to 60 seconds for the next cloud poll

### Technical
- Minimum Home Assistant version: 2024.1.0
- Minimum Python version: 3.11
- Added `from __future__ import annotations` to all modules for better type hint performance
- Full backward compatibility maintained - existing installations upgrade seamlessly

## [1.1.4] - Previous Release

See git history for changes in previous releases.
