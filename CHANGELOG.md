# Changelog

All notable changes to the ha-meural Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
