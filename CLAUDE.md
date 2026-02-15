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

**CloudDataUpdateCoordinator** (`coordinator.py:26-87`):
- Polls Meural cloud API every 60 seconds (600 seconds when sleeping)
- Fetches device information, device galleries, and user galleries
- Handles authentication errors and triggers reauth flow
- Shared across all devices for a single account

**LocalDataUpdateCoordinator** (`coordinator.py:89-175`):
- Polls local device API every 10 seconds
- Each device has its own local coordinator instance
- Fetches real-time state: sleep status, local galleries, gallery status
- Gracefully handles offline devices without failing the integration
- Returns cached data when device is unreachable

### Core Components

**PyMeural** (`pymeural.py`):
- Cloud API client for Meural's REST API (https://api.meural.com/v0/)
- Uses AWS Cognito (boto3) for authentication with automatic token refresh
- Handles authentication token lifecycle with callback for persistent storage
- All API methods are async and use aiohttp

**LocalMeural** (`pymeural.py`):
- Local device API client for Canvas web server (http://DEVICE-IP/remote/)
- Controls device directly without cloud dependency
- Handles device sleep/wake detection

**MeuralEntity** (`media_player.py`):
- Media player entity implementing standard Home Assistant media player features
- Coordinates between cloud and local data sources
- Registers custom services (set_brightness, preview_image, set_device_option, etc.)

### Authentication Flow

1. User provides email/password via config flow
2. PyMeural authenticates with AWS Cognito, receives access + refresh tokens
3. Tokens stored in config entry via `token_update_callback`
4. Access token automatically refreshed when expired
5. If refresh token fails, triggers Home Assistant reauth flow

### Data Flow

1. Cloud coordinator fetches devices and galleries from Meural API
2. Local coordinator polls each device's local interface for real-time state
3. Media player entity subscribes to both coordinators
4. Entity state derived from combination of cloud and local data
5. Update intervals adjust dynamically (slower when device sleeping)

## Key Files

- `__init__.py`: Integration setup, coordinator initialization
- `coordinator.py`: Cloud and local data update coordinators
- `media_player.py`: Media player entity implementation and custom services
- `pymeural.py`: API clients for both cloud and local interfaces
- `config_flow.py`: Configuration flow for UI setup
- `const.py`: Constants (update intervals, domain name)
- `services.yaml`: Custom service definitions
- `manifest.json`: Integration metadata (version, requirements, dependencies)

## Dependencies

- **boto3==1.38.15**: AWS SDK for Cognito authentication
- **aiohttp**: Async HTTP client (provided by Home Assistant)
- Home Assistant 2024.1.0+ (DataUpdateCoordinator pattern)
- Python 3.11+

## Custom Services

Beyond standard media player services, the integration provides:
- `meural.set_brightness`: Set backlight brightness (0-100)
- `meural.reset_brightness`: Enable automatic brightness via ambient light sensor
- `meural.toggle_informationcard`: Toggle museum-style artwork information card
- `meural.synchronize`: Sync Meural server with Canvas
- `meural.preview_image`: Display image from URL temporarily
- `meural.set_device_option`: Configure Canvas options (orientation, shuffle, duration, etc.)

All services are fully documented in `services.yaml`.

## Important Notes

- No two-factor authentication support (standard login only)
- SD card folders (meural1-4) supported but with limited metadata
- Uses both cloud polling and local device communication
- Local IP discovery happens via cloud API (device must be online to initial setup)
- Preview images use temporary display mechanism with configurable duration
