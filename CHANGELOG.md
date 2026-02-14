# Changelog

All notable changes to the ha-meural Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-02-14

### Breaking Changes
- **None** - This release is fully backward compatible with v1.x installations

### Added
- **DataUpdateCoordinator architecture**: Implemented modern coordinator pattern with dual coordinators (CloudDataUpdateCoordinator and LocalDataUpdateCoordinator)
- **Dynamic polling intervals**: Cloud API polling adjusts from 30s to 120s when device is sleeping
- **Comprehensive type hints**: Added type annotations throughout the codebase for better maintainability
- **Better error recovery**: Improved exception handling with specific exception types
- **Automatic reauth flow**: Authentication errors now trigger Home Assistant's reauth flow automatically

### Changed
- **Improved efficiency**: LocalMeural instances are now persistent and reused instead of being recreated on every call
- **Modern string formatting**: Updated all string formatting to use f-strings and logging best practices
- **Better coordinator-based state management**: Entities now use coordinator data instead of manual polling

### Deprecated
- Removed `CONFIG_SCHEMA` (no longer needed in modern Home Assistant)
- Removed `CONNECTION_CLASS` attribute (deprecated in Home Assistant)
- Removed version checks for MAJOR_VERSION/MINOR_VERSION (no longer needed)
- Removed try/except import for MediaPlayerDevice/MediaPlayerEntity (modern HA only uses MediaPlayerEntity)

### Fixed
- **Critical safety fix**: Replaced all bare `except:` clauses with specific exception types (aiohttp.ClientError, asyncio.TimeoutError, KeyError) to prevent catching system exits and other critical exceptions
- **Config flow bug**: Fixed config flow error handling where `raise` statement prevented error messages from displaying to users
- **Memory efficiency**: Fixed inefficient LocalMeural instance creation pattern

### Security
- Replaced dangerous bare `except:` clauses that could catch system exits and keyboard interrupts
- Improved exception handling to catch only expected error types

### Technical
- Minimum Home Assistant version: 2024.1.0
- Minimum Python version: 3.11
- Added `from __future__ import annotations` to all modules for better type hint performance
- Full backward compatibility maintained - existing installations upgrade seamlessly

## [1.1.4] - Previous Release

See git history for changes in previous releases.
